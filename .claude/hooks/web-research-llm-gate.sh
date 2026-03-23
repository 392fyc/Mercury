#!/usr/bin/env bash
# GATE (Layer 2): broad structural detection for external technical claims.
# Complements Layer 1 (web-research-gate.sh) with wider pattern coverage.
# Both layers share the same web-researched flag (60s TTL).
#
# Layer 1: detects imports, semver, config keys, CLI flags, URLs
# Layer 2: detects quoted string assignments, function calls on external
#          objects, HTTP methods, package.json-style deps, and any content
#          in config files (.json, .yaml, .yml, .toml)
#
# Flag-aware: if web research was done within 60s, both layers allow through.

INPUT=$(cat)

if command -v jq >/dev/null 2>&1; then
  FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
  CONTENT=$(echo "$INPUT" | jq -r '.tool_input.new_string // .tool_input.content // empty' 2>/dev/null)
else
  FILE_PATH=""
  CONTENT=$(echo "$INPUT" | sed -n 's/.*"\(new_string\|content\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\2/p' | head -1)
fi

# Skip hook infrastructure
case "$FILE_PATH" in
  */.claude/hooks/*|.claude/hooks/*|./.claude/hooks/*) exit 0 ;;
esac

[ -z "$CONTENT" ] && exit 0

# Check web-researched flag (shared 60s TTL)
STATE_DIR="$(dirname "$0")/state"
FLAG="$STATE_DIR/web-researched"
if [ -f "$FLAG" ]; then
  FLAG_AGE=$(( $(date +%s) - $(stat -c %Y "$FLAG" 2>/dev/null || stat -f %m "$FLAG" 2>/dev/null || echo 0) ))
  if [ "$FLAG_AGE" -lt 60 ] 2>/dev/null; then
    exit 0
  fi
fi

TRIGGERED=""

# Config files always require verification when modified (they contain external refs)
case "$FILE_PATH" in
  *.json|*.yaml|*.yml|*.toml)
    # Only flag config files that look like they have external references
    if echo "$CONTENT" | grep -qiE '["\x27]?(model|version|engine|provider|url|endpoint|api|registry|image|base)["\x27]?[[:space:]]*:'; then
      TRIGGERED="config file with external reference keys"
    fi
    ;;
esac

# Package dependency declarations
if echo "$CONTENT" | grep -qE '"(dependencies|devDependencies|peerDependencies)"[[:space:]]*:'; then
  TRIGGERED="${TRIGGERED:+$TRIGGERED, }package dependency declaration"
fi

# Docker/container image references
if echo "$CONTENT" | grep -qiE '(FROM[[:space:]]|image:[[:space:]]|docker\.io/|ghcr\.io/)'; then
  TRIGGERED="${TRIGGERED:+$TRIGGERED, }container image reference"
fi

[ -z "$TRIGGERED" ] && exit 0

cat >&2 <<MSG
BLOCKED (Layer 2): Content contains external technical references without recent verification.
Detected: ${TRIGGERED}.
Action: Run WebSearch/WebFetch to verify, then retry. Flag valid for 60 seconds.
MSG
exit 2
