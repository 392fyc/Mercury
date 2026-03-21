#!/bin/bash
# DISABLED: removed from settings.json in c6e7360 (over-restrictive).
# Pair: requires post-web-research-flag.sh on PostToolUse(WebSearch|WebFetch).
# GATE: block Write/Edit containing technical claims unless web research was done recently.
# Scope: SDK imports, version numbers, API signatures, CLI flags, npm packages, URLs as evidence.
# Applies to ALL agents via .claude/settings.json PreToolUse(Edit|Write).
# Token cost: ZERO. No external deps.

INPUT=$(cat)

# Extract content being written (new_string for Edit, content for Write)
# Prefer jq for robust JSON parsing; fall back to sed for minimal environments
if command -v jq >/dev/null 2>&1; then
  CONTENT=$(echo "$INPUT" | jq -r '.tool_input.new_string // .tool_input.content // empty' 2>/dev/null)
else
  CONTENT=$(echo "$INPUT" | sed -n 's/.*"\(new_string\|content\)"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\2/p' | head -1)
fi

[ -z "$CONTENT" ] && exit 0

# ── Pattern 1: SDK package imports ──
HAS_SDK_IMPORT=""
if echo "$CONTENT" | grep -qE '@anthropic-ai|@openai/codex|@google/gemini|claude-code|codex-sdk'; then
  HAS_SDK_IMPORT="1"
fi

# ── Pattern 2: Version claims (e.g., "v0.115.0", "version 2.1.76", "gpt-5.4") ──
HAS_VERSION=""
if echo "$CONTENT" | grep -qE 'v[0-9]+\.[0-9]+\.[0-9]+|version[[:space:]]+[0-9]+\.[0-9]|npm[[:space:]]+install|published.*[0-9]+\.[0-9]'; then
  HAS_VERSION="1"
fi

# ── Pattern 3: API method signatures in technical docs (JSON/markdown with SDK calls) ──
HAS_API_SIG=""
if echo "$CONTENT" | grep -qE 'query\(\{|startThread\(|resumeThread\(|codex-reply|mcp-server|unstable_v[0-9]'; then
  HAS_API_SIG="1"
fi

# If no sensitive patterns found, allow through
[ -z "$HAS_SDK_IMPORT" ] && [ -z "$HAS_VERSION" ] && [ -z "$HAS_API_SIG" ] && exit 0

# ── Check if web research was done recently (within last 3 minutes) ──
STATE_DIR="$(dirname "$0")/state"
FLAG="$STATE_DIR/web-researched"

if [ -f "$FLAG" ]; then
  # stat -c %Y for Linux/Windows, stat -f %m for Darwin; fallback to 0 (treat as expired)
  FLAG_AGE=$(( $(date +%s) - $(stat -c %Y "$FLAG" 2>/dev/null || stat -f %m "$FLAG" 2>/dev/null || echo 0) ))

  if [ "$FLAG_AGE" -lt 180 ] 2>/dev/null; then
    exit 0
  fi
fi

# ── Build specific blocking message ──
REASON=""
[ -n "$HAS_SDK_IMPORT" ] && REASON="SDK package import detected"
[ -n "$HAS_VERSION" ] && REASON="${REASON:+$REASON, }version/package claim detected"
[ -n "$HAS_API_SIG" ] && REASON="${REASON:+$REASON, }API signature detected"

cat >&2 <<MSG
BLOCKED: Writing content with technical claims that require web verification.
Reason: ${REASON}.
Rule: "DO NOT guess SDK/CLI APIs from training data — verify via web search or official docs."
Action: Use WebSearch or WebFetch to verify, then retry. Flag expires after 3 minutes.
MSG
exit 2
