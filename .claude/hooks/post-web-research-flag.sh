#!/bin/bash
# FLAG: mark web research as done after WebSearch/WebFetch completes.
# Records timestamp and query for audit trail.

STATE_DIR="$(dirname "$0")/state"
mkdir -p "$STATE_DIR"

INPUT=$(cat)
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "done")

# Try to extract query/URL from tool input for audit
if command -v jq >/dev/null 2>&1; then
  QUERY=$(echo "$INPUT" | jq -r '.tool_input.query // .tool_input.url // empty' 2>/dev/null)
else
  # Fallback: try to extract query or url with sed (best effort)
  QUERY=$(echo "$INPUT" | sed -n 's/.*"query"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  [ -z "$QUERY" ] && QUERY=$(echo "$INPUT" | sed -n 's/.*"url"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

echo "${TIMESTAMP} ${QUERY}" > "$STATE_DIR/web-researched"
exit 0
