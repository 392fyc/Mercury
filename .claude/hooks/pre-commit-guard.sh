#!/usr/bin/env bash
# GATE: git commit blocked unless code review flag is set.
# Token cost: ZERO. No external deps (no jq).

# Read stdin (hook JSON input)
INPUT=$(cat)

# Debug: log raw input for diagnosing guard bypass
STATE_DIR="$(dirname "$0")/state"
mkdir -p "$STATE_DIR"
# Truncate debug log if > 100KB to prevent unbounded growth
LOG_FILE="$STATE_DIR/pre-commit-guard-debug.log"
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 102400 ]; then
  tail -100 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
echo "[$(date -Iseconds)] INPUT=$INPUT" >> "$LOG_FILE"

# Extract command (jq preferred, sed fallback — no grep -oP for portability)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

# Debug: log extracted command
echo "[$(date -Iseconds)] COMMAND=$COMMAND" >> "$LOG_FILE"

# Only intercept git commit commands.
# Use printf '%s' to handle multi-line/heredoc commands that contain embedded
# newlines. `echo` can truncate on some shells; printf '%s' preserves the string.
printf '%s' "$COMMAND" | grep -qE 'git\s+commit' || exit 0

# Allow --amend (fixing previous commit, review already done)
printf '%s' "$COMMAND" | grep -qE '\-\-amend' && exit 0

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
