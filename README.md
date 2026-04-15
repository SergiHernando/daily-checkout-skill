# daily-checkout-skill

A [Claude Code](https://claude.ai/code) skill that turns your GitHub activity into a daily narrative checkout — ready-to-post copy for Slack, LinkedIn, and X.

## What it does

Run `/daily-checkout` at the end of your day. The skill fetches your GitHub activity, infers the intent behind your commits and PRs (not just what you did, but *what you were trying to accomplish*), and writes channel-appropriate posts in your voice.

**Slack** — candid end-of-day standup for your team  
**LinkedIn** — reflective, specific, building-in-public tone  
**X** — one punchy observation that earns a retweet

## Requirements

- [Claude Code](https://claude.ai/code)
- [`gh` CLI](https://cli.github.com), authenticated (`gh auth login`)

That's it. No Python, no API keys, no setup.

## Install

From the root of any project:

```bash
curl -fsSL https://raw.githubusercontent.com/SergiHernando/daily-checkout-skill/main/scripts/install.sh | sh
```

Installs to `.claude/skills/daily-checkout/` in the current directory.

## Usage

```
/daily-checkout                          # last 24 hours, all channels
/daily-checkout --date 2026-04-14        # specific day
/daily-checkout --hours 48               # wider window
/daily-checkout --channel linkedin       # single channel
/daily-checkout --user sgilaber          # another GitHub user
```

## How it works

The skill uses `gh` CLI shell injection to fetch your raw GitHub events, then Claude reasons through three stages natively:

1. **Signal extraction** — identifies work mode, commit themes, focus, collaboration
2. **Intent inference** — infers what you were trying to accomplish, not just what you did
3. **Narrative rendering** — writes posts shaped for each channel's voice and audience

No external API calls. The intelligence runs entirely within your Claude Code session.

## License

MIT
