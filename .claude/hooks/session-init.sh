#!/usr/bin/env bash
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
# Read vault config via jq (primary) or node (fallback)
if command -v jq &>/dev/null && [ -f "$CLAUDE_PROJECT_DIR/mercury.config.json" ]; then
  VAULT_NAME=$(jq -r '.obsidian.vaultName // "Mercury_KB"' "$CLAUDE_PROJECT_DIR/mercury.config.json" 2>/dev/null)
  KB_VAULT_PATH=$(jq -r '.obsidian.vaultPath // empty' "$CLAUDE_PROJECT_DIR/mercury.config.json" 2>/dev/null)
else
  VAULT_NAME=$(node -e "try{const c=require('$CLAUDE_PROJECT_DIR/mercury.config.json');console.log(c.obsidian.vaultName||'Mercury_KB')}catch(e){console.log('Mercury_KB')}" 2>/dev/null)
  KB_VAULT_PATH=$(node -e "try{console.log(require('$CLAUDE_PROJECT_DIR/mercury.config.json').obsidian.vaultPath)}catch(e){}" 2>/dev/null)
fi
# Detect active tasks via Obsidian CLI (preferred) or config-resolved path (fallback)
# Task ID formats: TASK-NAME-NNN (manual) or TASK-hexhexhex (shortId)
ACTIVE_TASKS=""
if command -v obsidian &>/dev/null; then
  ACTIVE_TASKS=$(obsidian vault="$VAULT_NAME" search query="in_progress" 2>/dev/null \
    | grep -oE 'TASK-[A-Za-z0-9-]+' | head -5 | sort -u)
fi
# Fallback: filesystem scan when CLI unavailable or returned no results
if [ -z "$ACTIVE_TASKS" ] && [ -n "$KB_VAULT_PATH" ] && [ -d "$KB_VAULT_PATH/10-tasks" ]; then
  ACTIVE_TASKS=$(find "$KB_VAULT_PATH/10-tasks" -name "*.json" -not -name "*.receipt.json" \
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
