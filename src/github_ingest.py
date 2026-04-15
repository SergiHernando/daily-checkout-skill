"""
github_ingest.py — Stage 1: GitHub Activity Ingest
====================================================
Fetches the last N hours of a user's GitHub activity using the `gh` CLI
and normalises it into a structured DailyActivity object ready for
Stage 2 (signal extraction).

Requirements:
    - gh CLI installed and authenticated (`gh auth login`)
    - pip install pydantic

Usage:
    python github_ingest.py                    # authenticated user, last 24 h
    python github_ingest.py --user sgilaber   # explicit user
    python github_ingest.py --hours 48        # wider window
    python github_ingest.py --out activity.json
"""

from __future__ import annotations

import json
import subprocess
import sys
import argparse
from datetime import datetime, timezone, timedelta
from typing import Optional
from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Config defaults (overridden by CLI args)
# ---------------------------------------------------------------------------

DEFAULT_LOOKBACK_H = 24
PER_PAGE = 100   # GitHub max per page


# ---------------------------------------------------------------------------
# Output models  (the Stage 2 contract)
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    COMMIT         = "commit"
    PR_OPENED      = "pr_opened"
    PR_MERGED      = "pr_merged"
    PR_CLOSED      = "pr_closed"
    PR_REVIEWED    = "pr_reviewed"
    ISSUE_OPENED   = "issue_opened"
    ISSUE_CLOSED   = "issue_closed"
    ISSUE_COMMENT  = "issue_comment"
    PUSH           = "push"           # branch push with no extractable commits
    OTHER          = "other"


class RawEvent(BaseModel):
    event_type:   EventType
    repo:         str                         # "owner/repo"
    timestamp:    datetime
    title:        Optional[str]  = None       # commit msg / PR title / issue title
    body_snippet: Optional[str] = None        # first 280 chars of body
    url:          Optional[str]  = None
    metadata:     dict           = Field(default_factory=dict)


class DailyActivity(BaseModel):
    username:      str
    window_start:  datetime
    window_end:    datetime
    events:        list[RawEvent]

    # Pre-computed aggregates consumed by Stage 2
    repos_touched: list[str] = Field(default_factory=list)
    commit_count:  int = 0
    pr_count:      int = 0
    issue_count:   int = 0
    review_count:  int = 0
    comment_count: int = 0

    def summarise(self) -> str:
        return (
            f"{self.username} · {self.window_start:%Y-%m-%d} · "
            f"{len(self.events)} events across {len(self.repos_touched)} repos "
            f"[commits={self.commit_count} prs={self.pr_count} "
            f"issues={self.issue_count} reviews={self.review_count} "
            f"comments={self.comment_count}]"
        )


# ---------------------------------------------------------------------------
# gh CLI wrapper
# ---------------------------------------------------------------------------

class GhCli:
    """
    Thin wrapper around `gh api` that handles pagination and JSON parsing.
    All network I/O goes through the local gh process — no tokens in code.
    """

    @staticmethod
    def _run(args: list[str]) -> list | dict:
        """Run a gh command, return parsed JSON, raise on non-zero exit."""
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"`gh {' '.join(args)}` failed:\n{result.stderr.strip()}"
            )
        return json.loads(result.stdout)

    @classmethod
    def whoami(cls) -> str:
        """Return the login of the authenticated user."""
        data = cls._run(["api", "/user"])
        return data["login"]

    @classmethod
    def user_events(cls, username: str, since: datetime) -> list[dict]:
        """
        Fetch /users/{username}/events with pagination, stopping as soon as
        we reach events older than `since` (events arrive newest-first).
        """
        collected: list[dict] = []
        page = 1

        while True:
            batch: list[dict] = cls._run([
                "api",
                f"/users/{username}/events?per_page={PER_PAGE}&page={page}",
            ])

            if not batch:
                break

            for ev in batch:
                ts = _parse_dt(ev.get("created_at", ""))
                if ts < since:
                    return collected   # oldest event in this batch is before window
                collected.append(ev)

            if len(batch) < PER_PAGE:
                break   # last page

            page += 1

        return collected


# ---------------------------------------------------------------------------
# Normaliser — raw GitHub event dict → list[RawEvent]
# ---------------------------------------------------------------------------

def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _snip(s: str | None, n: int = 280) -> str | None:
    if not s:
        return None
    return s[:n] + ("…" if len(s) > n else "")


def normalise(raw: dict) -> list[RawEvent]:
    """
    One GitHub event → one or more RawEvents.
    A PushEvent with N commits becomes N COMMIT entries so Stage 2 sees
    individual messages, not push wrappers.
    """
    etype   = raw.get("type", "")
    repo    = raw.get("repo", {}).get("name", "unknown")
    ts      = _parse_dt(raw.get("created_at", datetime.now(timezone.utc).isoformat()))
    payload = raw.get("payload", {})
    out: list[RawEvent] = []

    # ── Push → individual commits ──────────────────────────────────────────
    if etype == "PushEvent":
        commits = payload.get("commits", [])
        for c in commits:
            out.append(RawEvent(
                event_type=EventType.COMMIT,
                repo=repo,
                timestamp=ts,
                title=_snip(c.get("message", ""), 120),
                url=f"https://github.com/{repo}/commit/{c.get('sha', '')}",
                metadata={"sha": c.get("sha", "")[:8]},
            ))
        if not commits:
            out.append(RawEvent(
                event_type=EventType.PUSH,
                repo=repo,
                timestamp=ts,
                title=f"Push to {payload.get('ref', '')}",
            ))

    # ── Pull requests ──────────────────────────────────────────────────────
    elif etype == "PullRequestEvent":
        pr     = payload.get("pull_request", {})
        action = payload.get("action", "")
        emap   = {
            "opened":            EventType.PR_OPENED,
            "ready_for_review":  EventType.PR_OPENED,
            "closed":            EventType.PR_MERGED if pr.get("merged") else EventType.PR_CLOSED,
        }
        out.append(RawEvent(
            event_type=emap.get(action, EventType.OTHER),
            repo=repo,
            timestamp=ts,
            title=_snip(pr.get("title")),
            body_snippet=_snip(pr.get("body")),
            url=pr.get("html_url"),
            metadata={"action": action, "number": pr.get("number")},
        ))

    # ── PR reviews ─────────────────────────────────────────────────────────
    elif etype == "PullRequestReviewEvent":
        review = payload.get("review", {})
        pr     = payload.get("pull_request", {})
        out.append(RawEvent(
            event_type=EventType.PR_REVIEWED,
            repo=repo,
            timestamp=ts,
            title=f"Reviewed: {_snip(pr.get('title', ''), 80)}",
            body_snippet=_snip(review.get("body")),
            url=review.get("html_url"),
            metadata={"state": review.get("state", "")},
        ))

    # ── Issues ─────────────────────────────────────────────────────────────
    elif etype == "IssuesEvent":
        issue  = payload.get("issue", {})
        action = payload.get("action", "")
        emap   = {
            "opened": EventType.ISSUE_OPENED,
            "closed": EventType.ISSUE_CLOSED,
        }
        out.append(RawEvent(
            event_type=emap.get(action, EventType.OTHER),
            repo=repo,
            timestamp=ts,
            title=_snip(issue.get("title")),
            body_snippet=_snip(issue.get("body")),
            url=issue.get("html_url"),
            metadata={"action": action, "number": issue.get("number")},
        ))

    # ── Issue / PR comments ────────────────────────────────────────────────
    elif etype == "IssueCommentEvent":
        comment = payload.get("comment", {})
        issue   = payload.get("issue", {})
        out.append(RawEvent(
            event_type=EventType.ISSUE_COMMENT,
            repo=repo,
            timestamp=ts,
            title=f"Comment on: {_snip(issue.get('title', ''), 80)}",
            body_snippet=_snip(comment.get("body")),
            url=comment.get("html_url"),
        ))

    # ── Catch-all ──────────────────────────────────────────────────────────
    else:
        out.append(RawEvent(
            event_type=EventType.OTHER,
            repo=repo,
            timestamp=ts,
            title=etype,
            metadata={"raw_type": etype},
        ))

    return out


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

def _aggregate(
    username: str,
    events: list[RawEvent],
    window_start: datetime,
    window_end: datetime,
) -> DailyActivity:
    repos = sorted({e.repo for e in events})
    counts: dict[str, int] = {k: 0 for k in ("commit", "pr", "issue", "review", "comment")}

    for e in events:
        match e.event_type:
            case EventType.COMMIT:
                counts["commit"] += 1
            case EventType.PR_OPENED | EventType.PR_MERGED | EventType.PR_CLOSED:
                counts["pr"] += 1
            case EventType.ISSUE_OPENED | EventType.ISSUE_CLOSED:
                counts["issue"] += 1
            case EventType.PR_REVIEWED:
                counts["review"] += 1
            case EventType.ISSUE_COMMENT:
                counts["comment"] += 1

    return DailyActivity(
        username=username,
        window_start=window_start,
        window_end=window_end,
        events=events,
        repos_touched=repos,
        commit_count=counts["commit"],
        pr_count=counts["pr"],
        issue_count=counts["issue"],
        review_count=counts["review"],
        comment_count=counts["comment"],
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ingest(username: str = "", lookback_hours: int = DEFAULT_LOOKBACK_H) -> DailyActivity:
    """
    Fetch and normalise GitHub activity for `username` over the last
    `lookback_hours`. If `username` is empty, resolves the authenticated user.
    """
    now          = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=lookback_hours)

    resolved   = username or GhCli.whoami()
    raw_events = GhCli.user_events(resolved, window_start)

    normalised = [ev for r in raw_events for ev in normalise(r)]
    normalised = [e for e in normalised if e.timestamp >= window_start]   # strict window
    normalised.sort(key=lambda e: e.timestamp)                            # chronological

    return _aggregate(resolved, normalised, window_start, now)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Stage 1 — GitHub activity ingest")
    p.add_argument("--user",  default="", help="GitHub username (default: authenticated user)")
    p.add_argument("--hours", type=int, default=DEFAULT_LOOKBACK_H, help="Lookback window in hours")
    p.add_argument("--out",   default="", help="Write JSON to this file instead of stdout")
    return p.parse_args()


if __name__ == "__main__":
    args     = _parse_args()
    activity = ingest(username=args.user, lookback_hours=args.hours)

    # Summary + sample always go to stderr so they don't pollute the JSON pipe
    print(activity.summarise(), file=sys.stderr)
    print(file=sys.stderr)
    for ev in activity.events[:5]:
        print(
            f"  [{ev.timestamp:%H:%M}] {ev.event_type.value:<14} {ev.repo}  →  {ev.title or ''}",
            file=sys.stderr,
        )
    if len(activity.events) > 5:
        print(f"  … and {len(activity.events) - 5} more events", file=sys.stderr)

    payload = activity.model_dump_json(indent=2)

    if args.out:
        with open(args.out, "w") as f:
            f.write(payload)
        print(f"\nWritten to {args.out}", file=sys.stderr)
    else:
        print(payload)
