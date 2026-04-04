#!/usr/bin/env bash
# GATE: block `gh pr create` unless --assignee and --label are present.
# Ensures all PRs follow Mercury standard flow with proper metadata.
#
# Pattern: PreToolUse(Bash) → detect "gh pr create" → check flags → block/allow
#
# NOTE: We match against raw INPUT (not parsed COMMAND) to avoid JSON escaping
# issues with sed/grep. The flags --assignee/--label won't appear in title/body
# content, so raw matching is safe and robust.

INPUT=$(cat)

# In bypass/unattended mode, skip metadata enforcement — orchestrator provides metadata
if command -v jq >/dev/null 2>&1; then
  PERM_MODE=$(echo "$INPUT" | jq -r '.permission_mode // "default"' 2>/dev/null)
else
  PERM_MODE=$(echo "$INPUT" | sed -n 's/.*"permission_mode"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi
if [ "$PERM_MODE" = "bypassPermissions" ] || [ "$PERM_MODE" = "dontAsk" ]; then
  exit 0
fi

# Only intercept gh pr create commands
echo "$INPUT" | grep -qE 'gh[[:space:]]+pr[[:space:]]+create' || exit 0

MISSING=""

# Check --assignee or --add-assignee
echo "$INPUT" | grep -qE '(--assignee|--add-assignee)' || MISSING="${MISSING}  - --assignee (required: who owns this PR)\n"

# Check --label or --add-label
echo "$INPUT" | grep -qE '(--label|--add-label)' || MISSING="${MISSING}  - --label (required: bug/enhancement/etc.)\n"

# Extract --base value (supports --base develop, --base=develop, --base "develop")
BASE_VAL=$(echo "$INPUT" | grep -oE '\-\-base[[:space:]=]+[^[:space:]"]+' | sed 's/.*[[:space:]=]//' | head -1)
# Also check --base="value" form
[ -z "$BASE_VAL" ] && BASE_VAL=$(echo "$INPUT" | grep -oE '\-\-base[[:space:]=]+"[^"]+"' | sed 's/.*["]//' | sed 's/"//' | head -1)

if [ -z "$BASE_VAL" ]; then
  MISSING="${MISSING}  - --base develop (required: all PRs must target develop)\n"
elif [ "$BASE_VAL" != "develop" ]; then
  MISSING="${MISSING}  - --base must be 'develop', got '${BASE_VAL}' (Mercury git-flow rule)\n"
fi

if [ -n "$MISSING" ]; then
  cat >&2 <<MSG
BLOCKED: PR creation missing required metadata (Mercury standard flow).
Missing flags:
$(printf '%b' "$MISSING")
All PRs must include assignee, label, and --base develop.
Example: gh pr create --title "..." --body "..." --base develop --assignee 392fyc --label enhancement
MSG
  exit 2
fi

exit 0
