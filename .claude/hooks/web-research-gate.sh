#!/usr/bin/env bash
# GATE (Layer 1): block Write/Edit if content contains external technical references
# and no recent web research was performed.
#
# Detection: structural patterns only — NO hardcoded package/model/API names.
# Layer 2 (web-research-extended-gate.sh) provides wider structural detection.
#
# Flag TTL: 60 seconds (tightened from 180s after Session 11 incident)

INPUT=$(cat)

# In bypass/unattended mode, skip this gate — avoids 60s TTL stalls
source "$(dirname "$0")/lib/permission-mode.sh"
PERM_MODE=$(get_permission_mode "$INPUT")
if is_bypass_mode "$PERM_MODE"; then
  exit 0
fi

if command -v jq >/dev/null 2>&1; then
  CONTENT=$(echo "$INPUT" | jq -r '.tool_input.new_string // .tool_input.content // empty' 2>/dev/null)
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
else
  CONTENT=$(echo "$INPUT" | sed -n 's/.*"\(new_string\|content\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\2/p' | head -1)
  FILE_PATH=""
fi

[ -z "$CONTENT" ] && exit 0

# Skip hook scripts themselves to avoid self-triggering on regex descriptions
case "$FILE_PATH" in
  */.claude/hooks/*) exit 0 ;;
esac

TRIGGERED=""

# Structural: import/require of any external package
if echo "$CONTENT" | grep -qE '^[[:space:]]*(import[[:space:]]|from[[:space:]]|require\()'; then
  TRIGGERED="external import/require"
fi

# Structural: semantic version pattern (x.y.z)
if echo "$CONTENT" | grep -qE '[0-9]+\.[0-9]+\.[0-9]+'; then
  TRIGGERED="${TRIGGERED:+$TRIGGERED, }version identifier"
fi

# Structural: config keys referencing external services (JSON quoted + YAML unquoted)
if echo "$CONTENT" | grep -qiE '["\x27]?(model|engine|provider|baseURL|apiKey|endpoint|runtime)["\x27]?[[:space:]]*:'; then
  TRIGGERED="${TRIGGERED:+$TRIGGERED, }external config key"
fi

# Structural: CLI flags referencing external values
if echo "$CONTENT" | grep -qE '\-\-(model|engine|provider|version|registry|from)[[:space:]=]'; then
  TRIGGERED="${TRIGGERED:+$TRIGGERED, }CLI flag with external ref"
fi

# Structural: external URLs
if echo "$CONTENT" | grep -qE 'https?://[^[:space:]"]+'; then
  TRIGGERED="${TRIGGERED:+$TRIGGERED, }external URL"
fi

[ -z "$TRIGGERED" ] && exit 0

# Check if web research was done within last 60 seconds
_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
STATE_DIR="$_PROJECT/.mercury/state"
FLAG="$STATE_DIR/web-researched"

if [ -f "$FLAG" ]; then
  FLAG_MTIME=$(stat -c %Y "$FLAG" 2>/dev/null || stat -f %m "$FLAG" 2>/dev/null)
  if [ -n "$FLAG_MTIME" ] && [ "$FLAG_MTIME" -gt 0 ] 2>/dev/null; then
    FLAG_AGE=$(( $(date +%s) - FLAG_MTIME ))
    if [ "$FLAG_AGE" -ge 0 ] && [ "$FLAG_AGE" -lt 60 ] 2>/dev/null; then
      exit 0
    fi
  fi
fi

cat >&2 <<MSG
BLOCKED: Content contains external technical references without recent web verification.
Detected: ${TRIGGERED}.
Action: Run WebSearch/WebFetch to verify, then retry. Flag valid for 60 seconds.
MSG
exit 2
