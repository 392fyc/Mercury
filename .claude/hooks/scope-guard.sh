#!/usr/bin/env bash
# GATE: block software installation to C drive + adapter-KB coupling.
# Allows normal file writes to C drive (configs, caches, user data).
# Only blocks paths that indicate software installation (Program Files, etc).
# Token cost: ZERO. No external deps.

INPUT=$(cat)
# Extract file_path (jq preferred, sed fallback)
if command -v jq >/dev/null 2>&1; then
  FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
else
  FILE=$(echo "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

[ -z "$FILE" ] && exit 0

# Block software installation to C drive (Program Files, installers, etc)
if echo "$FILE" | grep -qiE '^[Cc]:[/\\](Program Files|Program Files \(x86\)|ProgramData|Windows|opt)[/\\]'; then
  echo "Blocked: CLAUDE.md — install software to D:\\Program Files, not C drive." >&2
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
