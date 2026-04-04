#!/usr/bin/env bash
# RESET: clear review flag after successful git commit.
# Token cost: ZERO. No external deps.

INPUT=$(cat)
# Extract command (jq preferred, sed fallback — no grep -oP for portability)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

echo "$COMMAND" | grep -qE 'git\s+commit' || exit 0

_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
STATE_DIR="$_PROJECT/.mercury/state"
rm -f "$STATE_DIR/review-passed" 2>/dev/null
git rev-parse HEAD 2>/dev/null > "$STATE_DIR/last-commit-sha" 2>/dev/null

exit 0
