#!/usr/bin/env bash
# FLAG: mark review as completed after code-review skill or review Agent runs.
# Token cost: ZERO. No external deps.

INPUT=$(cat)

# Detect review-related tool usage (single-agent review or dual-verify)
if echo "$INPUT" | grep -qi '"review"\|"dual-verify"\|"dual_verify"'; then
  _PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
  STATE_DIR="$_PROJECT/.mercury/state"
  mkdir -p "$STATE_DIR" 2>/dev/null
  date -u +%Y-%m-%dT%H:%M:%SZ > "$STATE_DIR/review-passed" 2>/dev/null || echo "flagged" > "$STATE_DIR/review-passed"
fi

exit 0
