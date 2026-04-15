"""
signal_extract.py — Stage 2: Signal Extraction
===============================================
Reads a DailyActivity (Stage 1 output) and produces a scored SignalBundle
ready for Stage 3 (intent inference).

No LLM here — this is pure deterministic analysis. The goal is to compress
raw events into a structured signal object that tells Stage 3 *what kind of
day it was* without asking it to parse JSON event lists.

Usage:
    python signal_extract.py < activity.json
    python signal_extract.py --in activity.json
    python signal_extract.py --in activity.json --out signals.json

    # Full pipeline so far:
    python github_ingest.py | python signal_extract.py
"""

from __future__ import annotations

import json
import sys
import argparse
import re
from collections import Counter
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# Re-use Stage 1 types (same file tree assumed; adjust import if packaging)
from github_ingest import DailyActivity, EventType, RawEvent


# ---------------------------------------------------------------------------
# Output models  (the Stage 3 contract)
# ---------------------------------------------------------------------------

class WorkMode(str, Enum):
    """Broad characterisation of how the day was spent."""
    FOCUSED     = "focused"      # deep work on one repo / theme
    SCATTERED   = "scattered"    # many repos, no dominant thread
    COLLABORATIVE = "collaborative"  # reviews, comments dominate
    MAINTENANCE = "maintenance"  # fixes, chores, dependency bumps
    EXPLORATORY = "exploratory"  # new repos, lots of issue activity
    MIXED       = "mixed"


class CommitTheme(str, Enum):
    FIX      = "fix"
    FEAT     = "feat"
    REFACTOR = "refactor"
    TEST     = "test"
    CHORE    = "chore"
    DOCS     = "docs"
    OTHER    = "other"


class ThemeBreakdown(BaseModel):
    theme:   CommitTheme
    count:   int
    share:   float   # 0–1


class RepoSignal(BaseModel):
    repo:          str
    event_count:   int
    commit_count:  int
    pr_count:      int
    issue_count:   int
    dominant_theme: Optional[CommitTheme] = None
    keywords:      list[str] = Field(default_factory=list)  # top nouns from commit msgs


class SignalBundle(BaseModel):
    """
    The compressed, scored representation of a day's activity.
    Fed verbatim (as JSON) into the Stage 3 intent-inference prompt.
    """
    username:     str
    date:         str          # YYYY-MM-DD
    window_hours: int

    # ── Volume ──────────────────────────────────────────────────────────────
    total_events:  int
    commit_count:  int
    pr_count:      int
    issue_count:   int
    review_count:  int
    comment_count: int

    # ── Focus ───────────────────────────────────────────────────────────────
    repos_touched:   list[str]
    repo_count:      int
    focus_score:     float     # 0–1; 1 = all events on one repo
    primary_repo:    Optional[str] = None

    # ── Commit semantics ────────────────────────────────────────────────────
    theme_breakdown:  list[ThemeBreakdown] = Field(default_factory=list)
    dominant_theme:   Optional[CommitTheme] = None
    debt_ratio:       float   # share of fix+chore commits; high = maintenance day
    keywords:         list[str] = Field(default_factory=list)  # top ~10 nouns

    # ── Momentum ────────────────────────────────────────────────────────────
    pr_momentum:      Optional[str] = None   # "opening" | "closing" | "reviewing" | "balanced"
    hour_spread:      list[int]     = Field(default_factory=list)  # events per hour 0–23
    burst_hours:      list[int]     = Field(default_factory=list)  # hours with activity spike

    # ── Collaboration ───────────────────────────────────────────────────────
    collaboration_index: float   # 0–1; share of events that are social (reviews, comments)

    # ── Per-repo detail ─────────────────────────────────────────────────────
    repo_signals: list[RepoSignal] = Field(default_factory=list)

    # ── Mode (coarse label for prompt) ──────────────────────────────────────
    work_mode: WorkMode = WorkMode.MIXED

    def summarise(self) -> str:
        return (
            f"{self.username} · {self.date} · mode={self.work_mode.value} · "
            f"focus={self.focus_score:.2f} · debt={self.debt_ratio:.2f} · "
            f"collab={self.collaboration_index:.2f} · "
            f"keywords=[{', '.join(self.keywords[:5])}]"
        )


# ---------------------------------------------------------------------------
# Commit message classifier
# ---------------------------------------------------------------------------

# Conventional-commit prefixes + common shorthand
_THEME_PATTERNS: list[tuple[CommitTheme, re.Pattern]] = [
    (CommitTheme.FIX,      re.compile(r"^(fix|bug|hotfix|patch|revert|closes?|resolve)", re.I)),
    (CommitTheme.FEAT,     re.compile(r"^(feat|feature|add|new|implement|introduce)", re.I)),
    (CommitTheme.REFACTOR, re.compile(r"^(refactor|rename|move|extract|restructure|reorgani[sz]e|clean)", re.I)),
    (CommitTheme.TEST,     re.compile(r"^(test|spec|coverage|assert)", re.I)),
    (CommitTheme.CHORE,    re.compile(r"^(chore|bump|deps?|upgrade|update|ci|cd|build|release|version|changelog)", re.I)),
    (CommitTheme.DOCS,     re.compile(r"^(docs?|readme|comment|documentation|typo)", re.I)),
]

# Stop-words to exclude from keyword extraction
_STOP = {
    "the","a","an","and","or","but","in","on","at","to","for","of","with",
    "from","is","are","was","were","be","been","has","have","had","not","no",
    "this","that","it","its","by","as","up","do","use","add","fix","update",
    "remove","change","revert","minor","misc","wip","merge","pull","branch",
    "commit","pr","issue","via","into","out","get","set","let","may","should",
    "will","can","also","more","some","all","any","my","our","your","their",
    "when","if","then","else","so","too","just","now","new","old","make",
    "run","check","handle","support","allow","prevent","ensure","move","bump",
}


def _classify_commit(msg: str) -> CommitTheme:
    if not msg:
        return CommitTheme.OTHER
    # Strip conventional-commit scope  e.g. "feat(auth): ..."
    clean = re.sub(r"\(.+?\):\s*", " ", msg).strip()
    for theme, pat in _THEME_PATTERNS:
        if pat.match(clean):
            return theme
    return CommitTheme.OTHER


def _extract_keywords(messages: list[str], top_n: int = 10) -> list[str]:
    """
    Pull the most frequent meaningful tokens from commit messages.
    Very lightweight — no NLP dependency needed.
    """
    tokens: list[str] = []
    for msg in messages:
        # Remove conventional-commit prefix and scope
        clean = re.sub(r"^[a-z]+(\(.+?\))?:\s*", "", msg, flags=re.I)
        # Tokenise on word boundaries, lowercase, filter
        words = re.findall(r"\b[a-z][a-z0-9_-]{2,}\b", clean.lower())
        tokens.extend(w for w in words if w not in _STOP)
    counts = Counter(tokens)
    return [w for w, _ in counts.most_common(top_n)]


# ---------------------------------------------------------------------------
# Per-repo aggregation
# ---------------------------------------------------------------------------

def _repo_signal(repo: str, events: list[RawEvent]) -> RepoSignal:
    commits = [e for e in events if e.event_type == EventType.COMMIT]
    prs     = [e for e in events if e.event_type in (
                   EventType.PR_OPENED, EventType.PR_MERGED, EventType.PR_CLOSED)]
    issues  = [e for e in events if e.event_type in (
                   EventType.ISSUE_OPENED, EventType.ISSUE_CLOSED)]

    msgs    = [e.title or "" for e in commits]
    themes  = [_classify_commit(m) for m in msgs]
    dominant = Counter(themes).most_common(1)[0][0] if themes else None
    keywords = _extract_keywords(msgs, top_n=5)

    return RepoSignal(
        repo=repo,
        event_count=len(events),
        commit_count=len(commits),
        pr_count=len(prs),
        issue_count=len(issues),
        dominant_theme=dominant,
        keywords=keywords,
    )


# ---------------------------------------------------------------------------
# Focus score
# ---------------------------------------------------------------------------

def _focus_score(events: list[RawEvent]) -> tuple[float, Optional[str]]:
    """
    Herfindahl-style concentration: sum of squared repo shares.
    1.0 = all events on one repo; approaches 0 as events spread evenly.
    """
    if not events:
        return 0.0, None
    counts = Counter(e.repo for e in events)
    total  = len(events)
    hhi    = sum((c / total) ** 2 for c in counts.values())
    primary = counts.most_common(1)[0][0]
    return round(hhi, 3), primary


# ---------------------------------------------------------------------------
# Hour spread & burst detection
# ---------------------------------------------------------------------------

def _hour_analysis(events: list[RawEvent]) -> tuple[list[int], list[int]]:
    spread = [0] * 24
    for e in events:
        spread[e.timestamp.hour] += 1
    if not any(spread):
        return spread, []
    avg   = sum(spread) / 24
    stdev = (sum((x - avg) ** 2 for x in spread) / 24) ** 0.5
    threshold = avg + stdev
    bursts = [h for h, c in enumerate(spread) if c > threshold and c > 0]
    return spread, bursts


# ---------------------------------------------------------------------------
# PR momentum
# ---------------------------------------------------------------------------

def _pr_momentum(events: list[RawEvent]) -> Optional[str]:
    opened   = sum(1 for e in events if e.event_type == EventType.PR_OPENED)
    closed   = sum(1 for e in events if e.event_type in (EventType.PR_MERGED, EventType.PR_CLOSED))
    reviewed = sum(1 for e in events if e.event_type == EventType.PR_REVIEWED)
    total    = opened + closed + reviewed
    if total == 0:
        return None
    if reviewed / total > 0.5:
        return "reviewing"
    if opened > closed * 2:
        return "opening"
    if closed > opened * 2:
        return "closing"
    return "balanced"


# ---------------------------------------------------------------------------
# Work mode classifier
# ---------------------------------------------------------------------------

def _work_mode(
    focus: float,
    debt_ratio: float,
    collab_index: float,
    dominant_theme: Optional[CommitTheme],
    repo_count: int,
    commit_count: int,
    issue_count: int,
) -> WorkMode:
    if collab_index > 0.5:
        return WorkMode.COLLABORATIVE
    if focus > 0.75 and commit_count > 0:
        if debt_ratio > 0.5:
            return WorkMode.MAINTENANCE
        if dominant_theme == CommitTheme.REFACTOR:
            return WorkMode.MAINTENANCE
        return WorkMode.FOCUSED
    if repo_count >= 4 and focus < 0.4:
        return WorkMode.SCATTERED
    if issue_count > commit_count and focus < 0.6:
        return WorkMode.EXPLORATORY
    if commit_count == 0 and issue_count > 0:
        return WorkMode.EXPLORATORY
    return WorkMode.MIXED


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract(activity: DailyActivity) -> SignalBundle:
    events = activity.events

    # ── Commit semantics ────────────────────────────────────────────────────
    commit_events = [e for e in events if e.event_type == EventType.COMMIT]
    commit_msgs   = [e.title or "" for e in commit_events]
    themes        = [_classify_commit(m) for m in commit_msgs]
    theme_counter = Counter(themes)
    total_commits = len(themes)

    theme_breakdown = [
        ThemeBreakdown(
            theme=t,
            count=c,
            share=round(c / total_commits, 3) if total_commits else 0.0,
        )
        for t, c in theme_counter.most_common()
    ]
    dominant_theme = theme_counter.most_common(1)[0][0] if theme_counter else None
    debt_ratio = round(
        (theme_counter.get(CommitTheme.FIX, 0) + theme_counter.get(CommitTheme.CHORE, 0))
        / max(total_commits, 1),
        3,
    )
    keywords = _extract_keywords(commit_msgs)

    # ── Focus ───────────────────────────────────────────────────────────────
    focus_score, primary_repo = _focus_score(events)

    # ── Per-repo signals ────────────────────────────────────────────────────
    by_repo: dict[str, list[RawEvent]] = {}
    for e in events:
        by_repo.setdefault(e.repo, []).append(e)
    repo_signals = [_repo_signal(repo, evs) for repo, evs in by_repo.items()]
    repo_signals.sort(key=lambda r: r.event_count, reverse=True)

    # ── Collaboration ───────────────────────────────────────────────────────
    social = sum(
        1 for e in events
        if e.event_type in (EventType.PR_REVIEWED, EventType.ISSUE_COMMENT)
    )
    collab_index = round(social / max(len(events), 1), 3)

    # ── Temporal ────────────────────────────────────────────────────────────
    hour_spread, burst_hours = _hour_analysis(events)
    pr_momentum = _pr_momentum(events)

    # ── Work mode ───────────────────────────────────────────────────────────
    work_mode = _work_mode(
        focus=focus_score,
        debt_ratio=debt_ratio,
        collab_index=collab_index,
        dominant_theme=dominant_theme,
        repo_count=len(by_repo),
        commit_count=total_commits,
        issue_count=activity.issue_count,
    )

    window_hours = max(
        1,
        round((activity.window_end - activity.window_start).total_seconds() / 3600),
    )

    return SignalBundle(
        username=activity.username,
        date=activity.window_start.strftime("%Y-%m-%d"),
        window_hours=window_hours,
        total_events=len(events),
        commit_count=activity.commit_count,
        pr_count=activity.pr_count,
        issue_count=activity.issue_count,
        review_count=activity.review_count,
        comment_count=activity.comment_count,
        repos_touched=activity.repos_touched,
        repo_count=len(by_repo),
        focus_score=focus_score,
        primary_repo=primary_repo,
        theme_breakdown=theme_breakdown,
        dominant_theme=dominant_theme,
        debt_ratio=debt_ratio,
        keywords=keywords,
        pr_momentum=pr_momentum,
        hour_spread=hour_spread,
        burst_hours=burst_hours,
        collaboration_index=collab_index,
        repo_signals=repo_signals,
        work_mode=work_mode,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pista — Stage 2: signal extraction")
    p.add_argument("--in",  dest="input",  default="", help="Path to Stage 1 JSON (default: stdin)")
    p.add_argument("--out", dest="output", default="", help="Write signals JSON to file (default: stdout)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    raw = open(args.input).read() if args.input else sys.stdin.read()
    activity = DailyActivity.model_validate_json(raw)

    bundle = extract(activity)

    print(bundle.summarise(), file=sys.stderr)

    payload = bundle.model_dump_json(indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(payload)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(payload)
