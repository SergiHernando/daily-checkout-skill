#!/bin/sh
set -e

SKILL_NAME="daily-checkout"
SKILL_URL="https://raw.githubusercontent.com/SergiHernando/daily-checkout-skill/main/.claude/skills/daily-checkout/SKILL.md"
SETTINGS=".claude/settings.json"

# Install skill
mkdir -p ".claude/skills/$SKILL_NAME"
curl -fsSL "$SKILL_URL" -o ".claude/skills/$SKILL_NAME/SKILL.md"
echo "daily-checkout skill installed (.claude/skills/$SKILL_NAME/)"

# Add gh permission to .claude/settings.json
if [ ! -f "$SETTINGS" ]; then
  printf '{\n  "permissions": {\n    "allow": ["Bash(gh *)"]\n  }\n}\n' > "$SETTINGS"
  echo "Created $SETTINGS with gh permission"
elif command -v jq >/dev/null 2>&1; then
  tmp=$(jq 'if ((.permissions.allow // []) | index("Bash(gh *)")) then . else .permissions.allow = ((.permissions.allow // []) + ["Bash(gh *)"]) end' "$SETTINGS")
  printf '%s\n' "$tmp" > "$SETTINGS"
  echo "Updated $SETTINGS with gh permission"
else
  echo "Note: add \"Bash(gh *)\" to $SETTINGS permissions.allow to suppress permission prompts"
fi

echo "Run /daily-checkout in any Claude Code session."
