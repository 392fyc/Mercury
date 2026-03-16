#!/bin/bash
# GATE: block Write/Edit containing SDK imports unless web research was done this session.
# Token cost: ZERO. No external deps.

INPUT=$(cat)

# Extract content being written (new_string for Edit, content for Write)
# Use grep to pull value — handles both fields
CONTENT=$(echo "$INPUT" | grep -oP '"(?:new_string|content)"\s*:\s*"\K[^"]*' | head -1)

[ -z "$CONTENT" ] && exit 0

# Check for SDK package imports
if echo "$CONTENT" | grep -qE '(@anthropic-ai|@openai/codex|@google/gemini|claude-code)'; then
  STATE_DIR="$(dirname "$0")/state"
  FLAG="$STATE_DIR/web-researched"

  if [ -f "$FLAG" ]; then
    exit 0
  fi

  cat >&2 <<'MSG'
BLOCKED: Writing code with SDK package imports.
CLAUDE.md: "DO NOT guess SDK/CLI APIs from training data — verify via web search or actual source code."
Use WebSearch or WebFetch to verify the API first, then retry.
MSG
  exit 2
fi

exit 0
