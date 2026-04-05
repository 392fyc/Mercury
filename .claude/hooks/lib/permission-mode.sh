#!/usr/bin/env bash
# Shared helper: parse permission_mode from hook input JSON.
# Usage: source "$(dirname "$0")/lib/permission-mode.sh"
#        PERM_MODE=$(get_permission_mode "$INPUT")
#        if is_bypass_mode "$PERM_MODE"; then exit 0; fi

get_permission_mode() {
  local input="$1"
  if command -v jq >/dev/null 2>&1; then
    echo "$input" | jq -r '.permission_mode // "default"' 2>/dev/null
  else
    echo "$input" | sed -n 's/.*"permission_mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1
  fi
}

is_bypass_mode() {
  local mode="$1"
  [ "$mode" = "bypassPermissions" ] || [ "$mode" = "dontAsk" ]
}
