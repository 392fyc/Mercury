#!/usr/bin/env bash
# GATE: git commit blocked unless code review flag is set.
# Token cost: ZERO. No external deps (no jq).

# Read stdin (hook JSON input)
INPUT=$(cat)

# Debug logging: opt-in via GUARD_DEBUG=1 to avoid persisting sensitive payloads.
STATE_DIR="$(dirname "$0")/state"
if ! mkdir -p "$STATE_DIR"; then
  echo "WARNING: cannot create state dir: $STATE_DIR" >&2
fi
LOG_FILE="$STATE_DIR/pre-commit-guard-debug.log"
if [ "${GUARD_DEBUG:-0}" = "1" ]; then
  # Truncate debug log if > 100KB to prevent unbounded growth
  if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 102400 ]; then
    tail -100 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
  fi
  echo "[$(date -Iseconds)] INPUT=$INPUT" >> "$LOG_FILE"
fi

# Extract command (jq preferred, sed fallback — no grep -oP for portability)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

if [ "${GUARD_DEBUG:-0}" = "1" ]; then
  echo "[$(date -Iseconds)] COMMAND=$COMMAND" >> "$LOG_FILE"
fi

# Only intercept git commit commands.
# Use printf '%s' to handle multi-line/heredoc commands that contain embedded
# newlines. `echo` can truncate on some shells; printf '%s' preserves the string.
printf '%s' "$COMMAND" | grep -qE 'git\s+commit' || exit 0

# Allow --amend (fixing previous commit, review already done).
# Match --amend as a standalone argument token (preceded by whitespace),
# not inside a quoted message string like -m "...--amend..."
# We check for whitespace-delimited --amend to avoid false positives in heredocs.
if printf '%s' "$COMMAND" | grep -qE '(^|\s)--amend(\s|$)'; then
  exit 0
fi

FLAG="$STATE_DIR/review-passed"

if [ -f "$FLAG" ]; then
  exit 0
fi

cat >&2 <<'MSG'
BLOCKED: Code review required before commit (CLAUDE.md MUST rule).
Run /dual-verify (preferred) or /code-review before committing.
To bypass: touch .claude/hooks/state/review-passed
MSG
exit 2
