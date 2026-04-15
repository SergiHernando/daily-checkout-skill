#!/bin/sh
set -e

SKILL_NAME="daily-checkout"
SKILL_URL="https://raw.githubusercontent.com/SergiHernando/daily-checkout-skill/main/.claude/skills/daily-checkout/SKILL.md"

# Prefer global install; fall back to project-level if ~/.claude is not writable
if [ -w "$HOME/.claude" ] || mkdir -p "$HOME/.claude/skills" 2>/dev/null; then
  DEST="$HOME/.claude/skills/$SKILL_NAME"
  SCOPE="global"
else
  DEST=".claude/skills/$SKILL_NAME"
  SCOPE="project"
fi

mkdir -p "$DEST"
curl -fsSL "$SKILL_URL" -o "$DEST/SKILL.md"

echo "daily-checkout skill installed ($SCOPE: $DEST)"
echo "Run /daily-checkout in any Claude Code session."
