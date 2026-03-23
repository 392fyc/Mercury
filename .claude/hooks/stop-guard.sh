#!/usr/bin/env bash
# GATE: block stop if staged uncommitted changes exist.
# Token cost: ZERO. No external deps.

cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

STAGED=$(git diff --cached --name-only 2>/dev/null)

if [ -n "$STAGED" ]; then
  echo '{"decision":"block","reason":"Staged uncommitted changes detected. Commit before stopping (CLAUDE.md: commit at every checkpoint)."}'
  exit 0
fi

# Clean up session-scoped state flags for this session
STATE_DIR="$(dirname "$0")/state"
SESSION_ID="${PPID:-$$}"
rm -f "$STATE_DIR/session-init-${SESSION_ID}" 2>/dev/null
# Also clean up any stale session-init flags older than 24h (crashed sessions)
find "$STATE_DIR" -name "session-init-*" -mmin +1440 -delete 2>/dev/null

exit 0
