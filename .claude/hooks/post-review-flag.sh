#!/usr/bin/env bash
# FLAG: mark review as completed after code-review skill or review Agent runs.
# Token cost: ZERO. No external deps.

INPUT=$(cat)

# Detect review-related tool usage (single-agent review or dual-verify)
if echo "$INPUT" | grep -qi '"review"\|"dual-verify"\|"dual_verify"'; then
  STATE_DIR="$(dirname "$0")/state"
  mkdir -p "$STATE_DIR"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$STATE_DIR/review-passed" 2>/dev/null || echo "flagged" > "$STATE_DIR/review-passed"
fi

exit 0
