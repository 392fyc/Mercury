#!/usr/bin/env bash
# GATE: block stop if staged uncommitted changes exist.
# Token cost: ZERO. No external deps.

cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

# Read stdin for permission mode (Stop hooks also receive JSON input)
STOP_INPUT=$(cat 2>/dev/null || true)

STAGED=$(git diff --cached --name-only 2>/dev/null)

if [ -n "$STAGED" ]; then
  # In bypass/unattended mode, warn but don't block — orchestrator manages checkpoints
  PERM_MODE=""
  if [ -n "$STOP_INPUT" ]; then
    if command -v jq >/dev/null 2>&1; then
      PERM_MODE=$(echo "$STOP_INPUT" | jq -r '.permission_mode // "default"' 2>/dev/null)
    else
      PERM_MODE=$(echo "$STOP_INPUT" | sed -n 's/.*"permission_mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
    fi
  fi
  if [ "$PERM_MODE" = "bypassPermissions" ] || [ "$PERM_MODE" = "dontAsk" ]; then
    echo "WARNING: Stopping with staged uncommitted changes (bypass mode)." >&2
    exit 0
  fi
  echo '{"decision":"block","reason":"Staged uncommitted changes detected. Commit before stopping (CLAUDE.md: commit at every checkpoint)."}'
  exit 0
fi

# Clean up session-scoped state flags for this session
_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
STATE_DIR="$_PROJECT/.mercury/state"
SESSION_ID="${PPID:-$$}"
rm -f "$STATE_DIR/session-init-${SESSION_ID}" 2>/dev/null
# Also clean up any stale session-init flags older than 24h (crashed sessions)
find "$STATE_DIR" -name "session-init-*" -mmin +1440 -delete 2>/dev/null

exit 0
