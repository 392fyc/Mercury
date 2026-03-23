#!/usr/bin/env bash
# GATE: git commit blocked unless code review flag is set.
# Token cost: ZERO. No external deps (no jq).

# Read stdin (hook JSON input)
INPUT=$(cat)

# Extract command (jq preferred, sed fallback — no grep -oP for portability)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

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
