#!/bin/bash
# GATE: git commit blocked unless code review flag is set.
# Token cost: ZERO. No external deps (no jq).

# Read stdin (hook JSON input)
INPUT=$(cat)

# Extract command field via grep — avoids jq dependency
COMMAND=$(echo "$INPUT" | grep -oP '"command"\s*:\s*"\K[^"]*')

# Only intercept git commit commands
echo "$COMMAND" | grep -qE 'git\s+commit' || exit 0

# Allow --amend (fixing previous commit, review already done)
echo "$COMMAND" | grep -qE '\-\-amend' && exit 0

STATE_DIR="$(dirname "$0")/state"
FLAG="$STATE_DIR/review-passed"

if [ -f "$FLAG" ]; then
  exit 0
fi

cat >&2 <<'MSG'
BLOCKED: Code review required before commit (CLAUDE.md MUST rule).
Run /code-review or perform a diff-based review first.
To bypass: touch .claude/hooks/state/review-passed
MSG
exit 2
