#!/bin/bash
# FLAG: mark web research as done after WebSearch/WebFetch completes.
# Token cost: ZERO. No external deps.

STATE_DIR="$(dirname "$0")/state"
mkdir -p "$STATE_DIR"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo done)" > "$STATE_DIR/web-researched"
exit 0
