#!/bin/bash
# GATE: block `gh pr create` unless --assignee and --label are present.
# Ensures all PRs follow Mercury standard flow with proper metadata.
#
# Pattern: PreToolUse(Bash) → detect "gh pr create" → check flags → block/allow
#
# NOTE: We match against raw INPUT (not parsed COMMAND) to avoid JSON escaping
# issues with sed/grep. The flags --assignee/--label won't appear in title/body
# content, so raw matching is safe and robust.

INPUT=$(cat)

# Only intercept gh pr create commands
echo "$INPUT" | grep -qE 'gh\s+pr\s+create' || exit 0

MISSING=""

# Check --assignee or --add-assignee (match word boundary to avoid substring false positives)
echo "$INPUT" | grep -qE '(--assignee|--add-assignee)' || MISSING="${MISSING}  - --assignee (required: who owns this PR)\n"

# Check --label or --add-label
echo "$INPUT" | grep -qE '(--label|--add-label)' || MISSING="${MISSING}  - --label (required: bug/enhancement/etc.)\n"

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
