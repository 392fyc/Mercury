#!/bin/bash
# POST-WRITE HOOK: Auto-dispatch TaskBundle via Mercury orchestrator HTTP RPC
# Triggers when a file is written to Mercury_KB/10-tasks/ with status "dispatched"
# Token cost: ZERO. No LLM calls.

INPUT=$(cat)
FILE=$(echo "$INPUT" | grep -oP '"file_path"\s*:\s*"\K[^"]*' | head -1)

# Only trigger for task bundle files in KB
echo "$FILE" | grep -qiE '(Mercury_KB|mercury_kb)[/\\]10-tasks[/\\]TASK-.*\.json' || exit 0

# Extract content and check for dispatched status
CONTENT=$(echo "$INPUT" | grep -oP '"content"\s*:\s*"\K(.*)(?=")' | head -1 2>/dev/null)
if [ -z "$CONTENT" ]; then
  # Try reading the file directly
  [ -f "$FILE" ] && CONTENT=$(cat "$FILE")
fi

# Check if status is "dispatched"
echo "$CONTENT" | grep -q '"status".*"dispatched"' || exit 0

# Extract taskId
TASK_ID=$(echo "$CONTENT" | grep -oP '"taskId"\s*:\s*"\K[^"]*' | head -1)
[ -z "$TASK_ID" ] && exit 0

# Dispatch via Mercury HTTP RPC
PORT="${MERCURY_RPC_PORT:-7654}"
curl -s -X POST "http://127.0.0.1:${PORT}" \
  -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"method\":\"dispatch_task\",\"params\":{\"taskId\":\"${TASK_ID}\"},\"id\":1}" \
  >/dev/null 2>&1 &

echo "Auto-dispatch triggered: ${TASK_ID}" >&2
exit 0
