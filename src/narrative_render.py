"""
narrative_render.py — Stage 4: Narrative Render
================================================
Reads an IntentFrame (Stage 3 output) and produces channel-aware
narratives for Slack, LinkedIn, and X.

Each channel gets the same intent context but a distinct persona prompt.
The LLM never sees raw events — only the structured intent frame.

Requirements:
    pip install anthropic pydantic

Usage:
    python narrative_render.py < intent.json
    python narrative_render.py --in intent.json
    python narrative_render.py --in intent.json --out checkout.json
    python narrative_render.py --in intent.json --channel slack

    # Full Pista pipeline:
    python github_ingest.py \\
      | python signal_extract.py \\
      | python intent_infer.py \\
      | python narrative_render.py
"""

from __future__ import annotations

import json
import sys
import argparse
from typing import Optional

import anthropic
from pydantic import BaseModel

from intent_infer import IntentFrame


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

class ChannelPost(BaseModel):
    channel:    str
    text:       str
    char_count: int
    word_count: int


class DailyCheckout(BaseModel):
    username:  str
    date:      str
    intent:    str          # primary_intent value, for reference
    arc:       str          # protagonist_arc, for reference
    slack:     ChannelPost
    linkedin:  ChannelPost
    x:         ChannelPost

    def pretty_print(self) -> str:
        divider = "─" * 60
        return (
            f"\n{divider}\n"
            f"PISTA DAILY CHECKOUT · {self.username} · {self.date}\n"
            f"intent: {self.intent}  |  arc: {self.arc}\n"
            f"{divider}\n\n"
            f"── SLACK ({self.slack.word_count} words) ──\n"
            f"{self.slack.text}\n\n"
            f"── LINKEDIN ({self.linkedin.word_count} words) ──\n"
            f"{self.linkedin.text}\n\n"
            f"── X ({self.x.char_count} chars) ──\n"
            f"{self.x.text}\n"
            f"{divider}\n"
        )


# ---------------------------------------------------------------------------
# Channel persona definitions
# ---------------------------------------------------------------------------

_SHARED_RULES = """
Rules that apply to ALL channels:
- Tell a story with a point of view — never list what was done.
- The arc is the spine; build from it, don't restate it verbatim.
- Use anchor_concepts and named_artifacts naturally — don't force them all in.
- Never use phrases like "today I worked on" or "I spent time".
- Never use the word "delve". Never use the phrase "it's worth noting".
- Write in first person, past tense for what happened, present tense for insight.
- Respond with ONLY the post text. No labels, no quotes, no preamble.
"""

_CHANNEL_PERSONAS: dict[str, dict] = {
    "slack": {
        "label": "Slack (internal team)",
        "length": "80–130 words",
        "tone": "candid and direct — you're talking to people who understand the codebase",
        "structure": "one paragraph, no hashtags, no emoji unless it adds meaning, "
                     "end with what's next or what's still open",
        "persona": "a senior engineer giving an honest end-of-day standup to their team",
    },
    "linkedin": {
        "label": "LinkedIn",
        "length": "150–220 words",
        "tone": "reflective and specific — you're talking to peers and the broader tech community",
        "structure": "two or three short paragraphs. First paragraph: the insight or tension. "
                     "Second: what the work revealed. Third (optional): a broader takeaway or "
                     "open question. End with 2–4 relevant hashtags.",
        "persona": "a CTO building in public — thoughtful, not self-promotional",
    },
    "x": {
        "label": "X / Twitter",
        "length": "≤280 characters",
        "tone": "punchy — one sharp observation that earns a retweet",
        "structure": "one or two sentences maximum. The first sentence is the hook. "
                     "Optional second sentence adds tension or twist. No hashtags unless "
                     "they add meaning and fit the character count.",
        "persona": "an engineer who notices things others miss",
    },
}


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are Pista, an AI that turns the traces of an engineer's day into \
narrative. You have already inferred the intent and arc behind the day. \
Now your job is to write the actual posts — each shaped for its channel, \
each telling a story with a point of view.

{shared_rules}
""".format(shared_rules=_SHARED_RULES)


def _build_channel_prompt(frame: IntentFrame, channel: str) -> str:
    persona  = _CHANNEL_PERSONAS[channel]
    concepts = ", ".join(frame.anchor_concepts) if frame.anchor_concepts else "none identified"
    artifacts = ", ".join(frame.named_artifacts) if frame.named_artifacts else "none identified"

    tension_line    = f"tension: {frame.tension}" if frame.tension else ""
    resolution_line = f"resolution: {frame.resolution}" if frame.resolution else ""
    secondary_line  = f"secondary_intent: {frame.secondary_intent.value}" if frame.secondary_intent else ""

    context_block = "\n".join(filter(None, [
        f"primary_intent: {frame.primary_intent.value}",
        secondary_line,
        f"emotional_register: {frame.emotional_register.value}",
        f"protagonist_arc: {frame.protagonist_arc}",
        tension_line,
        resolution_line,
        f"anchor_concepts: {concepts}",
        f"named_artifacts: {artifacts}",
    ]))

    return f"""
Write a {persona['label']} post for this engineer's day.

INTENT CONTEXT
{context_block}

CHANNEL CONSTRAINTS
- Persona: {persona['persona']}
- Tone: {persona['tone']}
- Length: {persona['length']}
- Structure: {persona['structure']}
""".strip()


# ---------------------------------------------------------------------------
# LLM calls  (one per channel, run sequentially to keep it simple)
# ---------------------------------------------------------------------------

def _render_channel(
    client: anthropic.Anthropic,
    frame: IntentFrame,
    channel: str,
    model: str,
    temperature: float,
) -> ChannelPost:
    message = client.messages.create(
        model=model,
        max_tokens=512,
        temperature=temperature,
        system=_SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": _build_channel_prompt(frame, channel)},
        ],
    )

    text = message.content[0].text.strip()

    return ChannelPost(
        channel=channel,
        text=text,
        char_count=len(text),
        word_count=len(text.split()),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(
    frame: IntentFrame,
    channels: Optional[list[str]] = None,
    model: str = "claude-sonnet-4-6",
    temperature: float = 0.7,   # higher than stage 3 — we want expressive variation
) -> DailyCheckout:
    """
    Render narrative posts for each channel from an IntentFrame.
    temperature=0.7 allows the narrative voice to breathe.
    """
    if channels is None:
        channels = ["slack", "linkedin", "x"]

    unknown = [c for c in channels if c not in _CHANNEL_PERSONAS]
    if unknown:
        raise ValueError(f"Unknown channels: {unknown}. Valid: {list(_CHANNEL_PERSONAS)}")

    client = anthropic.Anthropic()

    posts: dict[str, ChannelPost] = {}
    for channel in ["slack", "linkedin", "x"]:   # always render all three internally
        posts[channel] = _render_channel(client, frame, channel, model, temperature)

    return DailyCheckout(
        username=frame.username,
        date=frame.date,
        intent=frame.primary_intent.value,
        arc=frame.protagonist_arc,
        slack=posts["slack"],
        linkedin=posts["linkedin"],
        x=posts["x"],
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pista — Stage 4: narrative render")
    p.add_argument("--in",          dest="input",       default="",                    help="Path to Stage 3 JSON (default: stdin)")
    p.add_argument("--out",         dest="output",      default="",                    help="Write checkout JSON to file (default: stdout)")
    p.add_argument("--channel",     dest="channel",     default="",                    help="Render a single channel: slack | linkedin | x")
    p.add_argument("--model",       dest="model",       default="claude-sonnet-4-6",   help="Claude model to use")
    p.add_argument("--temperature", dest="temperature", default=0.7, type=float,       help="Sampling temperature (default: 0.7)")
    p.add_argument("--pretty",      dest="pretty",      action="store_true",           help="Print formatted output to stderr")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    raw     = open(args.input).read() if args.input else sys.stdin.read()
    frame   = IntentFrame.model_validate_json(raw)
    channels = [args.channel] if args.channel else None

    checkout = render(frame, channels=channels, model=args.model, temperature=args.temperature)

    if args.pretty or not args.output:
        print(checkout.pretty_print(), file=sys.stderr)

    payload = checkout.model_dump_json(indent=2)

    if args.output:
        with open(args.output, "w") as f:
            f.write(payload)
        print(f"Written to {args.output}", file=sys.stderr)
    else:
        print(payload)
