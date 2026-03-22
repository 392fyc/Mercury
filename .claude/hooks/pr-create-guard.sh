#!/bin/bash
# GATE: block `gh pr create` unless --assignee and --label are present.
# Ensures all PRs follow Mercury standard flow with proper metadata.
#
# Pattern: PreToolUse(Bash) → detect "gh pr create" → check flags → block/allow

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only intercept gh pr create commands
echo "$COMMAND" | grep -qE 'gh[[:space:]]+pr[[:space:]]+create' || exit 0

MISSING=""

# Check --assignee or --add-assignee
echo "$COMMAND" | grep -qE '\-\-assignee|\-\-add-assignee' || MISSING="${MISSING}  - --assignee (required: who owns this PR)\n"

# Check --label or --add-label
echo "$COMMAND" | grep -qE '\-\-label|\-\-add-label' || MISSING="${MISSING}  - --label (required: bug/enhancement/etc.)\n"

if [ -n "$MISSING" ]; then
  cat >&2 <<MSG
BLOCKED: PR creation missing required metadata (Mercury standard flow).
Missing flags:
$(echo -e "$MISSING")
All PRs must include assignee and label. Use pr-flow skill or add flags manually.
Example: gh pr create --title "..." --body "..." --assignee 392fyc --label enhancement
MSG
  exit 2
fi

exit 0
