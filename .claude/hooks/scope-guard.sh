#!/bin/bash
# GATE: block writes to C drive and adapter-KB coupling.
# Token cost: ZERO. No external deps.

INPUT=$(cat)
FILE=$(echo "$INPUT" | grep -oP '"file_path"\s*:\s*"\K[^"]*' | head -1)

[ -z "$FILE" ] && exit 0

# Block C drive writes
if echo "$FILE" | grep -qiE '^[Cc]:[/\\]'; then
  echo "Blocked: CLAUDE.md — do not write to C drive when D drive is available." >&2
  exit 2
fi

# Block adapter files from importing KB dependencies
if echo "$FILE" | grep -qi 'sdk-adapters'; then
  if echo "$INPUT" | grep -qiE '(obsidian|knowledge-service|KnowledgeService)'; then
    echo "Blocked: CLAUDE.md — agent adapters must NOT depend on Obsidian/KB." >&2
    exit 2
  fi
fi

exit 0
