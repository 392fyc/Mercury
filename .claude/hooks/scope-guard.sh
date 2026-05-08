#!/usr/bin/env bash
# GATE: block software installation to C drive + adapter-KB coupling.
# Allows normal file writes to C drive (configs, caches, user data).
# Only blocks paths that indicate software installation (Program Files, etc).
# Token cost: ZERO. jq is REQUIRED for Codex apply_patch shape — without jq,
# the hook fails closed when a Codex command shape is detected (rather than
# silently bypassing scope checks).
#
# Tool-input shape compatibility:
#   - Claude Code Edit/Write: tool_input.file_path (single path)
#   - Codex apply_patch: tool_input.command (V4A patch heredoc with
#     "*** (Update|Add|Delete) File: <path>" markers and rename targets
#     marked by "*** Move to: <path>" — multiple paths possible)
#   Refs: https://developers.openai.com/api/docs/guides/tools-apply-patch
#         https://github.com/openai/codex/issues/6358 (Move-to corner case)
# Both shapes are extracted; gate runs on the union of paths so a patch
# that updates an allowed file but Move-to's it onto a blocked path is
# correctly blocked.

INPUT=$(cat)
PATHS=""

# Extract apply_patch paths from the command body. Recognises the V4A
# directives Update/Add/Delete File AND the rename target Move to.
extract_apply_patch_paths() {
  local body="$1"
  printf '%s' "$body" \
    | grep -E '^\*\*\* (Update File: |Add File: |Delete File: |Move to: )' \
    | sed -E 's/^\*\*\* (Update File: |Add File: |Delete File: |Move to: )//'
}

if command -v jq >/dev/null 2>&1; then
  # 1. Claude Code shape — single explicit path.
  FILE=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
  [ -n "$FILE" ] && PATHS="$FILE"

  # 2. Codex apply_patch shape — paths embedded in the patch heredoc.
  CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
  if [ -n "$CMD" ]; then
    APPLY_PATCH_FILES=$(extract_apply_patch_paths "$CMD")
    if [ -n "$APPLY_PATCH_FILES" ]; then
      if [ -n "$PATHS" ]; then
        PATHS="$PATHS"$'\n'"$APPLY_PATCH_FILES"
      else
        PATHS="$APPLY_PATCH_FILES"
      fi
    fi
  fi
else
  # Sed fallback for the Claude Code file_path shape — robust because
  # file_path is a single quoted string at the JSON top level.
  FILE=$(printf '%s' "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$FILE" ] && PATHS="$FILE"

  # Codex apply_patch detection without jq: look for the V4A directive
  # signature in the raw JSON-encoded command body. Multi-line JSON-string
  # parsing without a JSON-aware tool is too brittle to extract reliable
  # paths, so when we detect Codex shape and lack jq, fail CLOSED rather
  # than silently bypass the scope gate (Codex Issue #357 iter 2 finding).
  if printf '%s' "$INPUT" \
    | grep -qE '"command"[[:space:]]*:[[:space:]]*"[^"]*\*\*\* (Begin Patch|Update File:|Add File:|Delete File:|Move to:)'; then
    echo "Blocked: scope-guard.sh requires \`jq\` for Codex apply_patch shape (V4A patch heredoc). Install jq to enable scope checks; failing closed to avoid silent bypass." >&2
    exit 2
  fi
fi

[ -z "$PATHS" ] && exit 0

# Iterate each path; any single violation → exit 2.
while IFS= read -r FILE; do
  [ -z "$FILE" ] && continue

  # Block software installation to C drive.
  if printf '%s' "$FILE" | grep -qiE '^[Cc]:[/\\](Program Files|Program Files \(x86\)|ProgramData|Windows|opt)[/\\]'; then
    echo "Blocked: CLAUDE.md — install software to D:\\Program Files, not C drive (path: $FILE)" >&2
    exit 2
  fi

  # Block adapter files from importing KB dependencies.
  if printf '%s' "$FILE" | grep -qi 'sdk-adapters'; then
    if printf '%s' "$INPUT" | grep -qiE '(obsidian|knowledge-service|KnowledgeService)'; then
      echo "Blocked: CLAUDE.md — agent adapters must NOT depend on Obsidian/KB (path: $FILE)" >&2
      exit 2
    fi
  fi
done <<< "$PATHS"

exit 0
