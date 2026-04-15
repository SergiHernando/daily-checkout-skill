#!/bin/sh
set -e

SKILL_NAME="daily-checkout"
SKILL_URL="https://raw.githubusercontent.com/SergiHernando/daily-checkout-skill/main/.claude/skills/daily-checkout/SKILL.md"
DEST="$HOME/.claude/skills/$SKILL_NAME"

mkdir -p "$DEST"
curl -fsSL "$SKILL_URL" -o "$DEST/SKILL.md"

echo "Installed to $DEST/SKILL.md"
echo "Run /daily-checkout in any Claude Code session."
