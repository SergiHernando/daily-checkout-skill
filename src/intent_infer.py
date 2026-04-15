"""
intent_infer.py — Stage 3: Intent Inference
============================================
Reads a SignalBundle (Stage 2 output) and uses Claude to infer the
*intent* behind the day — what the engineer was trying to accomplish —
producing a structured IntentFrame consumed by Stage 4 (narrative render).

This is the only stage that calls an LLM. Everything before it is
deterministic; everything after it is template/prompt work.

Requirements:
    pip install anthropic pydantic

Usage:
    python intent_infer.py < signals.json
    python intent_infer.py --in signals.json
    python intent_infer.py --in signals.json --out intent.json

    # Full pipeline so far:
    python github_ingest.py | python signal_extract.py | python intent_infer.py
"""

from __future__ import annotations

import json
import sys
import argparse
from enum import Enum
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from signal_extract import SignalBundle


# ---------------------------------------------------------------------------
# Output models  (the Stage 4 contract)
# ---------------------------------------------------------------------------

class IntentCluster(str, Enum):
    """
    A fixed vocabulary of intent labels. Kept small and mutually exclusive
    so Stage 4 can map each one to a distinct narrative voice.
    """
    HARDENING_BOUNDARY   = "hardening_boundary"    # tightening security, validation, contracts
    SHIPPING_CAPABILITY  = "shipping_capability"   # new feature, visible user-facing progress
    PAYING_DOWN_DEBT     = "paying_down_debt"      # refactors, chores, cleanup sprints
    EXPLORING_UNKNOWN    = "exploring_unknown"     # spikes, new repos, issue-heavy days
    ENABLING_TEAM        = "enabling_team"         # reviews, comments, unblocking others
    STABILISING_PROD     = "stabilising_prod"      # hotfixes, incidents, rollbacks
    LAYING_FOUNDATIONS   = "laying_foundations"    # infra, pipelines, scaffolding
    CONSOLIDATING_GAINS  = "consolidating_gains"   # tests, docs, integration work post-feature


class EmotionalRegister(str, Enum):
    URGENT       = "urgent"       # incidents, hotfixes, fast cadence
    EXPLORATORY  = "exploratory"  # spikes, new territory
    METHODICAL   = "methodical"   # steady refactors, tests, docs
    GENERATIVE   = "generative"   # shipping, building, momentum
    COLLABORATIVE = "collaborative"


class IntentFrame(BaseModel):
    """
    The structured intent extracted from a day's signal bundle.
    Fed as context into Stage 4's narrative prompt — NOT as content to echo.
    """
    username:           str
    date:               str

    # Core intent
    primary_intent:     IntentCluster
    secondary_intent:   Optional[IntentCluster] = None
    confidence:         float                       # 0–1, LLM self-reported

    # Narrative levers
    emotional_register: EmotionalRegister
    protagonist_arc:    str   # 1-sentence framing of the day's story arc
    tension:            Optional[str] = None  # what problem/constraint drove the work
    resolution:         Optional[str] = None  # what was resolved or advanced

    # Vocabulary for Stage 4 to use (nouns, not verbs — stage 4 picks the voice)
    anchor_concepts:    list[str] = Field(default_factory=list)  # 3–5 key concepts
    named_artifacts:    list[str] = Field(default_factory=list)  # repos, PRs, issues worth naming

    # Raw LLM reasoning (kept for debugging / audit)
    reasoning:          str = ""

    def summarise(self) -> str:
        return (
            f"{self.username} · {self.date} · "
            f"intent={self.primary_intent.value} · "
            f"register={self.emotional_register.value} · "
            f"arc=\"{self.protagonist_arc[:60]}...\""
        )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are Pista, an AI that reads the traces of an engineer's day and \
infers the underlying intent — not what they did, but what they were \
trying to accomplish.

You receive a structured signal bundle extracted from a day's GitHub \
activity. Your job is to reason about it and return a structured JSON \
object describing the intent, narrative arc, and emotional register of \
the day.

Rules:
- Focus on direction and purpose, not on listing events.
- The protagonist_arc must read as the opening line of a story, not a \
  summary of tasks. It should answer: "What was this engineer in the \
  middle of today?"
- tension and resolution are optional — only populate them if the \
  signals clearly support it.
- anchor_concepts should be the 3–5 domain concepts that matter, \
  derived from keywords and repo context.
- named_artifacts should only include repos, PRs or issues worth \
  mentioning by name in a story (i.e. meaningful, not noise).
- confidence reflects how clearly the signals point to the intent.
- Respond ONLY with valid JSON matching the schema. No preamble, no \
  markdown fences.
"""

_SCHEMA_HINT = {
    "username": "string",
    "date": "YYYY-MM-DD",
    "primary_intent": "one of: hardening_boundary | shipping_capability | paying_down_debt | exploring_unknown | enabling_team | stabilising_prod | laying_foundations | consolidating_gains",
    "secondary_intent": "same enum or null",
    "confidence": "float 0.0–1.0",
    "emotional_register": "one of: urgent | exploratory | methodical | generative | collaborative",
    "protagonist_arc": "1 sentence, story framing",
    "tension": "string or null — the constraint or problem that drove the work",
    "resolution": "string or null — what was resolved or meaningfully advanced",
    "anchor_concepts": ["3–5 domain concept strings"],
    "named_artifacts": ["repo names, PR titles, or issue summaries worth naming"],
    "reasoning": "2–3 sentences explaining your reasoning",
}


def _build_user_prompt(bundle: SignalBundle) -> str:
    """
    Serialize the signal bundle into a compact prompt-friendly form.
    We don't dump raw event JSON — Stage 3 gets the compressed signals only.
    """
    theme_summary = ", ".join(
        f"{t.theme.value}={t.count}({t.share:.0%})"
        for t in bundle.theme_breakdown
    )
    repo_summary = "\n".join(
        f"  - {r.repo}: {r.event_count} events, "
        f"commits={r.commit_count}, "
        f"dominant_theme={r.dominant_theme.value if r.dominant_theme else 'none'}, "
        f"keywords=[{', '.join(r.keywords[:4])}]"
        for r in bundle.repo_signals[:5]   # top 5 repos only
    )

    return f"""
Signal bundle for {bundle.username} on {bundle.date}:

VOLUME
  total_events={bundle.total_events}
  commits={bundle.commit_count}  prs={bundle.pr_count}  issues={bundle.issue_count}
  reviews={bundle.review_count}  comments={bundle.comment_count}

FOCUS
  repos_touched={bundle.repo_count} ({', '.join(bundle.repos_touched[:6])})
  focus_score={bundle.focus_score:.2f}  (1.0=single repo, 0=fully scattered)
  primary_repo={bundle.primary_repo or 'none'}

COMMIT SEMANTICS
  theme_breakdown: {theme_summary or 'no commits'}
  dominant_theme={bundle.dominant_theme.value if bundle.dominant_theme else 'none'}
  debt_ratio={bundle.debt_ratio:.2f}  (share of fix+chore commits)
  keywords=[{', '.join(bundle.keywords[:10])}]

MOMENTUM
  pr_momentum={bundle.pr_momentum or 'none'}
  burst_hours={bundle.burst_hours}
  work_mode={bundle.work_mode.value}

COLLABORATION
  collaboration_index={bundle.collaboration_index:.2f}  (share of social events)

PER-REPO BREAKDOWN
{repo_summary or '  (no repo detail)'}

---
Return JSON matching this schema exactly:
{json.dumps(_SCHEMA_HINT, indent=2)}
""".strip()


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_claude(bundle: SignalBundle, model: str, temperature: float) -> dict:
    client = anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env

    message = client.messages.create(
        model=model,
        max_tokens=1024,
        temperature=temperature,
        system=_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": _build_user_prompt(bundle)},
        ],
    )

    raw = message.content[0].text.strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    return json.loads(raw)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def infer(
    bundle: SignalBundle,
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.3,
) -> IntentFrame:
    """
    Infer intent from a SignalBundle. Returns a validated IntentFrame.
    temperature=0.3 gives reproducible-enough results while allowing
    some expressive variation in the protagonist_arc.
    """
    raw = _call_claude(bundle, model=model, temperature=temperature)

    # Ensure username/date are always set from the bundle (don't trust LLM)
    raw["username"] = bundle.username
    raw["date"]     = bundle.date

    return IntentFrame.model_validate(raw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pista — Stage 3: intent inference")
    p.add_argument("--in",          dest="input",       default="",                    help="Path to Stage 2 JSON (default: stdin)")
    p.add_argument("--out",         dest="output",      default="",                    help="Write intent JSON to file (default: stdout)")
    p.add_argument("--model",       dest="model",       default="claude-sonnet-4-6",   help="Claude model to use")
    p.add_argument("--temperature", dest="temperature", default=0.3, type=float,       help="Sampling temperature (default: 0.3)")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    raw     = open(args.input).read() if args.input else sys.stdin.read()
    bundle  = SignalBundle.model_validate_json(raw)
    frame   = infer(bundle, model=args.model, temperature=args.temperature)

    print(frame.summarise(), file=sys.stderr)
    print(f"reasoning: {frame.reasoning}", file=sys.stderr)

    payload = frame.model_dump_json(indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(payload)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(payload)
