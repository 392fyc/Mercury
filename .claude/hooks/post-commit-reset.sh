#!/bin/bash
# RESET: clear review flag after successful git commit.
# Token cost: ZERO. No external deps.

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | grep -oP '"command"\s*:\s*"\K[^"]*')

echo "$COMMAND" | grep -qE 'git\s+commit' || exit 0

STATE_DIR="$(dirname "$0")/state"
rm -f "$STATE_DIR/review-passed" 2>/dev/null
git rev-parse HEAD 2>/dev/null > "$STATE_DIR/last-commit-sha" 2>/dev/null

exit 0
