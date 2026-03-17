#!/bin/bash
# Mercury Task Dispatch via HTTP RPC
# Usage: bash .mercury/dispatch.sh <taskId>
# Calls orchestrator's dispatch_task RPC through localhost HTTP endpoint

TASK_ID="$1"
PORT="${MERCURY_RPC_PORT:-7654}"
ENDPOINT="http://127.0.0.1:${PORT}"

if [ -z "$TASK_ID" ]; then
  echo "Usage: dispatch.sh <taskId>" >&2
  exit 1
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$ENDPOINT" \
  -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"method\":\"dispatch_task\",\"params\":{\"taskId\":\"${TASK_ID}\"},\"id\":1}" \
  2>&1)

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | head -1)

if [ "$HTTP_CODE" = "200" ]; then
  echo "Dispatched: ${TASK_ID} → orchestrator → app-server"
  echo "$BODY"
else
  echo "Dispatch failed (HTTP ${HTTP_CODE}): ${BODY}" >&2
  exit 1
fi
