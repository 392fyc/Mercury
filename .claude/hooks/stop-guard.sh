#!/usr/bin/env bash
# GATE: block stop if staged uncommitted changes exist.
# Token cost: ZERO. No external deps.

# Compute project root once — used for cd and STATE_DIR
_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$_PROJECT" 2>/dev/null || exit 0

# Read stdin for permission mode (Stop hooks also receive JSON input)
STOP_INPUT=$(cat 2>/dev/null || true)
source "$(dirname "$0")/lib/permission-mode.sh"
PERM_MODE=$(get_permission_mode "$STOP_INPUT")

STAGED=$(git diff --cached --name-only 2>/dev/null)

if [ -n "$STAGED" ]; then
  if is_bypass_mode "$PERM_MODE"; then
    # In bypass/unattended mode, warn but don't block — fall through to cleanup
    echo "WARNING: Stopping with staged uncommitted changes (bypass mode)." >&2
  else
    echo '{"decision":"block","reason":"Staged uncommitted changes detected. Commit before stopping (CLAUDE.md: commit at every checkpoint)."}'
    exit 0
  fi
fi

# Clean up session-scoped state flags for this session
STATE_DIR="$_PROJECT/.mercury/state"
SESSION_ID="${PPID:-$$}"
rm -f "$STATE_DIR/session-init-${SESSION_ID}" 2>/dev/null
# Also clean up any stale session-init flags older than 24h (crashed sessions)
find "$STATE_DIR" -name "session-init-*" -mmin +1440 -delete 2>/dev/null

exit 0
