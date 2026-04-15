"""
pisto.py — Daily checkout runner
=================================
Wires all four stages into a single command.

Usage:
    python pisto.py                          # authenticated gh user, last 24 h
    python pisto.py --user sgilaber         # explicit GitHub username
    python pisto.py --hours 48              # wider window
    python pisto.py --channel slack         # single channel output
    python pisto.py --out checkout.json     # save full JSON
    python pisto.py --save-stages           # also dump intermediate stage files

Requirements:
    pip install anthropic pydantic
    gh auth login
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from github_ingest    import ingest
from signal_extract   import extract
from intent_infer     import infer
from narrative_render import render, DailyCheckout


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Pisto — GitHub activity → daily narrative checkout"
    )
    p.add_argument("--user",        default="",    help="GitHub username (default: authenticated user)")
    p.add_argument("--hours",       default=24,    type=int,   help="Lookback window in hours (default: 24, ignored when --date is set)")
    p.add_argument("--date",        default="",    help="Analyse a specific UTC day, e.g. 2026-04-14 (overrides --hours)")
    p.add_argument("--channel",     default="",    help="Render a single channel: slack | linkedin | x")
    p.add_argument("--out",         default="",    help="Write final JSON to file")
    p.add_argument("--save-stages", action="store_true", help="Save intermediate stage JSON files")
    p.add_argument("--model",       default="claude-sonnet-4-6", help="Claude model to use")
    return p.parse_args()


def run(
    username: str = "",
    hours: int = 24,
    date: str = "",
    channel: str = "",
    model: str = "claude-sonnet-4-6",
) -> DailyCheckout:

    print("▶ Stage 1 — fetching GitHub activity…",   file=sys.stderr)
    activity = ingest(username=username, lookback_hours=hours, date=date or None)
    print(f"  {activity.summarise()}",               file=sys.stderr)

    print("▶ Stage 2 — extracting signals…",         file=sys.stderr)
    bundle = extract(activity)
    print(f"  {bundle.summarise()}",                 file=sys.stderr)

    print("▶ Stage 3 — inferring intent…",           file=sys.stderr)
    frame = infer(bundle, model=model)
    print(f"  {frame.summarise()}",                  file=sys.stderr)

    print("▶ Stage 4 — rendering narratives…",       file=sys.stderr)
    channels = [channel] if channel else None
    checkout = render(frame, channels=channels, model=model)

    return checkout


if __name__ == "__main__":
    args     = _parse_args()
    checkout = run(
        username=args.user,
        hours=args.hours,
        date=args.date,
        channel=args.channel,
        model=args.model,
    )

    print(checkout.pretty_print())

    if args.out:
        Path(args.out).write_text(checkout.model_dump_json(indent=2))
        print(f"\nSaved to {args.out}", file=sys.stderr)

    if args.save_stages:
        # Re-run stages individually to capture intermediates
        from github_ingest  import ingest   as _ingest
        from signal_extract import extract  as _extract
        from intent_infer   import infer    as _infer

        activity = _ingest(username=args.user, lookback_hours=args.hours, date=args.date or None)
        bundle   = _extract(activity)
        frame    = _infer(bundle, model=args.model)

        Path("activity.json").write_text(activity.model_dump_json(indent=2))
        Path("signals.json").write_text(bundle.model_dump_json(indent=2))
        Path("intent.json").write_text(frame.model_dump_json(indent=2))
        print("Stage files saved: activity.json · signals.json · intent.json", file=sys.stderr)
