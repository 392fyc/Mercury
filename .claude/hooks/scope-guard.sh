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
  # Sed fallback for the Claude Code file_path shape. Caveat: the regex
  # `[^"]*` does not handle JSON-escaped quote characters and assumes the
  # JSON value is on a single line. To avoid silently passing a partially-
  # extracted path past the C-drive block-list, fail CLOSED whenever the
  # `"file_path":` key is present but the regex extraction fails — the
  # input is then either pretty-printed or contains escapes the sed
  # fallback cannot interpret reliably (Argus iter-3 finding).
  FILE=$(printf '%s' "$INPUT" | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
  [ -n "$FILE" ] && PATHS="$FILE"

  if [ -z "$FILE" ] && printf '%s' "$INPUT" | grep -qE '"file_path"[[:space:]]*:'; then
    echo "Blocked: scope-guard.sh sed fallback could not extract file_path (multi-line JSON or escaped quotes); install jq for full coverage. Failing closed." >&2
    exit 2
  fi

  # Codex apply_patch detection without jq: look for two independent
  # signatures in the raw JSON. (1) a `"command":` key, and (2) any V4A
  # directive marker anywhere in the body. Splitting the check makes the
  # detection robust against JSON formatting variants (pretty-printed
  # multi-line input would break a single-line regex that requires the
  # key and the directive on the same line — Argus iter 2 finding).
  # When both signatures are present and jq is absent, fail CLOSED rather
  # than silently bypass the scope gate.
  if printf '%s' "$INPUT" | grep -qE '"command"[[:space:]]*:' \
     && printf '%s' "$INPUT" | grep -qE '\*\*\* (Begin Patch|Update File:|Add File:|Delete File:|Move to:)'; then
    echo "Blocked: scope-guard.sh requires \`jq\` for Codex apply_patch shape (V4A patch heredoc). Install jq to enable scope checks; failing closed to avoid silent bypass." >&2
    exit 2
  fi
fi

[ -z "$PATHS" ] && exit 0

# Iterate each path; any single violation → exit 2.
while IFS= read -r FILE; do
  [ -z "$FILE" ] && continue

  # Reject path-traversal segments outright. V4A apply_patch and Claude Code
  # Edit/Write should never need ".." in tool_input; if a payload contains
  # one, the input is either malformed or attempting to relativize past the
  # blocked-prefix check below. Building a complete shell-side path
  # normalizer is far harder than rejecting outright.
  if printf '%s' "$FILE" | grep -qE '(^|[/\\])\.\.(/|\\|$)'; then
    echo "Blocked: scope-guard.sh refuses path-traversal segment '..' (path: $FILE)" >&2
    exit 2
  fi

  # Block software installation to C drive — regular path AND UNC long-path
  # forms. `\\?\C:\Program Files\...` (verbatim) and `\\.\C:\Program Files\...`
  # (device namespace) both normalize to C:\Program Files\... when the file
  # system call runs, so both must be caught. Forward / backward slashes are
  # both accepted by Win32, so the regex covers both.
  if printf '%s' "$FILE" | grep -qiE '^(\\\\[?.]\\)?[Cc]:[/\\](Program Files|Program Files \(x86\)|ProgramData|Windows|opt)[/\\]'; then
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
