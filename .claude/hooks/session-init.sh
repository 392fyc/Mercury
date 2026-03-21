#!/bin/bash
# SESSION INIT: Generate .claude/current-session.md on first prompt of a session.
# Event: UserPromptSubmit — fires before Claude starts reasoning.
# Idempotent: writes only once per session (uses PID-based flag for session scope).
# Token cost: ZERO (output goes to file, not stdout).

STATE_DIR="$(dirname "$0")/state"
mkdir -p "$STATE_DIR" 2>/dev/null

# Session-scoped guard: use parent PID as session identifier.
# Each Claude Code session has a unique parent process; concurrent sessions
# get independent flags. Stale flags from crashed sessions are harmless
# (unique PIDs) and cleaned up by stop-guard.sh on normal exit.
SESSION_ID="${PPID:-$$}"
FLAG="$STATE_DIR/session-init-${SESSION_ID}"
[ -f "$FLAG" ] && exit 0

cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

# Gather session context
BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
LAST_COMMIT=$(git log -1 --oneline 2>/dev/null || echo "unknown")
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
GIT_STATUS=$(git status --porcelain 2>/dev/null | head -20)
ACTIVE_TASKS=""
if [ -d "Mercury_KB/10-tasks" ]; then
  ACTIVE_TASKS=$(find Mercury_KB/10-tasks -name "*.json" -not -name "*.receipt.json" \
    -exec grep -lE '"status":[[:space:]]*"(in_progress|dispatched|implementation_done)"' {} \; 2>/dev/null \
    | head -5 | while read -r f; do basename "$f" .json; done)
fi

# Write current-session.md (ensure directory exists)
mkdir -p "$CLAUDE_PROJECT_DIR/.claude" 2>/dev/null
SESSION_FILE="$CLAUDE_PROJECT_DIR/.claude/current-session.md"
cat > "$SESSION_FILE" <<EOF
# Current Session

> Auto-generated at session start. Do not edit manually.

| Field | Value |
|-------|-------|
| Started | $TIMESTAMP |
| Branch | \`$BRANCH\` |
| Last Commit | \`$LAST_COMMIT\` |

## Git Status

\`\`\`
${GIT_STATUS:-clean}
\`\`\`

## Active Tasks

${ACTIVE_TASKS:-_none detected_}
EOF

# Mark as done for this session
touch "$FLAG"

exit 0
