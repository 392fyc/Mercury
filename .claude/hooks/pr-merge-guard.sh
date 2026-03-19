#!/bin/bash
# GATE: block gh pr merge unless CodeRabbit review has completed.
# Token cost: ~1 gh API call when intercepted.
#
# Pattern: same as pre-commit-guard.sh
#   PreToolUse(Bash) → detect "gh pr merge" → check CodeRabbit status → block/allow

INPUT=$(cat)
COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

# Only intercept gh pr merge commands
echo "$COMMAND" | grep -qE 'gh[[:space:]]+pr[[:space:]]+merge' || exit 0

# Extract PR selector — first non-flag token after `gh pr merge`
# Strip `gh pr merge` prefix, then find first token not starting with -
MERGE_ARGS=$(echo "$COMMAND" | sed -n 's/.*gh[[:space:]][[:space:]]*pr[[:space:]][[:space:]]*merge[[:space:]][[:space:]]*//p')
PR_SELECTOR=""
for token in $MERGE_ARGS; do
  case "$token" in
    -*) continue ;;
    *) PR_SELECTOR="$token"; break ;;
  esac
done
PR_NUMBER=""

case "$PR_SELECTOR" in
  ''|--*)
    # No selector or flag-only; fallback to current branch PR
    PR_NUMBER=$(gh pr view --json number -q '.number' 2>/dev/null)
    ;;
  *[!0-9]*)
    # URL or branch name selector — resolve via gh
    PR_NUMBER=$(gh pr view "$PR_SELECTOR" --json number -q '.number' 2>/dev/null)
    ;;
  *)
    # Pure numeric
    PR_NUMBER="$PR_SELECTOR"
    ;;
esac

if [ -z "$PR_NUMBER" ]; then
  echo "BLOCKED: could not determine PR number from command or current branch." >&2
  exit 2
fi

# Allow manual bypass via state flag (e.g., human-approved merge)
STATE_DIR="$(dirname "$0")/state"
if ! mkdir -p "$STATE_DIR"; then
  echo "BLOCKED: cannot create state dir: $STATE_DIR" >&2
  exit 2
fi
FLAG="$STATE_DIR/pr-merge-approved-${PR_NUMBER}"
if [ -f "$FLAG" ]; then
  rm -f "$FLAG" 2>/dev/null
  exit 0
fi

# Check CodeRabbit status via gh pr checks
CR_STATUS=$(gh pr checks "$PR_NUMBER" --json name,state -q '.[] | select(.name | test("CodeRabbit";"i")) | .state' 2>/dev/null | head -n1 | tr '[:upper:]' '[:lower:]')

if [ "$CR_STATUS" = "success" ]; then
  exit 0
fi

case "$CR_STATUS" in
  pending|queued|in_progress)
    cat >&2 <<MSG
BLOCKED: CodeRabbit review still in progress for PR #${PR_NUMBER}.
Wait for review to complete before merging.
To check: gh pr checks ${PR_NUMBER}
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
    exit 2
    ;;
esac

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
