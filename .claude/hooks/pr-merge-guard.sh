#!/bin/bash
# GATE: block gh pr merge unless CodeRabbit review has completed.
# Token cost: ~1 gh API call when intercepted.
#
# Pattern: same as pre-commit-guard.sh
#   PreToolUse(Bash) → detect "gh pr merge" → check CodeRabbit status → block/allow

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | grep -oP '"command"\s*:\s*"\K[^"]*')

# Only intercept gh pr merge commands
echo "$COMMAND" | grep -qE 'gh\s+pr\s+merge' || exit 0

# Extract PR number from command (e.g., "gh pr merge 15 --merge")
PR_NUMBER=$(echo "$COMMAND" | grep -oP 'gh\s+pr\s+merge\s+\K\d+')

if [ -z "$PR_NUMBER" ]; then
  echo "BLOCKED: could not extract PR number from merge command." >&2
  exit 2
fi

# Allow manual bypass via state flag (e.g., human-approved merge)
STATE_DIR="$(dirname "$0")/state"
FLAG="$STATE_DIR/pr-merge-approved-${PR_NUMBER}"
if [ -f "$FLAG" ]; then
  rm -f "$FLAG" 2>/dev/null
  exit 0
fi

# Check CodeRabbit status via gh pr checks
CR_STATUS=$(gh pr checks "$PR_NUMBER" 2>/dev/null | grep -i "CodeRabbit" | awk '{print $2}')

if [ "$CR_STATUS" = "pass" ]; then
  exit 0
fi

if [ "$CR_STATUS" = "pending" ]; then
  cat >&2 <<MSG
BLOCKED: CodeRabbit review still in progress for PR #${PR_NUMBER}.
Wait for review to complete before merging.
To check: gh pr checks ${PR_NUMBER}
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
  exit 2
fi

if [ -z "$CR_STATUS" ]; then
  cat >&2 <<MSG
BLOCKED: No CodeRabbit check found for PR #${PR_NUMBER}.
Ensure CodeRabbit is configured and has started reviewing.
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
  exit 2
fi

# CR_STATUS is something unexpected (fail, etc.)
cat >&2 <<MSG
BLOCKED: CodeRabbit status is "${CR_STATUS}" for PR #${PR_NUMBER}.
Review must pass before agent can merge.
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
exit 2
