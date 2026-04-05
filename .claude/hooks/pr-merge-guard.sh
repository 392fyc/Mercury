#!/usr/bin/env bash
# GATE: block gh pr merge unless automated review (Argus or CodeRabbit) has completed.
# Token cost: ~2 gh API calls when intercepted.
#
# Pattern: same as pre-commit-guard.sh
#   PreToolUse(Bash) → detect "gh pr merge" → check review status → block/allow

INPUT=$(cat)
# Extract command (jq preferred, sed fallback)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
fi

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
_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
STATE_DIR="$_PROJECT/.mercury/state"
if ! mkdir -p "$STATE_DIR"; then
  echo "BLOCKED: cannot create state dir: $STATE_DIR" >&2
  exit 2
fi
FLAG="$STATE_DIR/pr-merge-approved-${PR_NUMBER}"
if [ -f "$FLAG" ]; then
  rm -f "$FLAG" 2>/dev/null
  exit 0
fi

# ── Primary gate: reviewDecision ──────────────────────────────────
# This covers approvals from any source: Argus, CodeRabbit, or human.
REVIEW_DECISION=$(gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision // "REVIEW_REQUIRED"' 2>/dev/null | tr '[:lower:]' '[:upper:]')

if [ "$REVIEW_DECISION" = "APPROVED" ]; then
  exit 0
fi

# ── Secondary gate: has any review bot posted a review? ──────────
# Check for reviews from argus-review[bot] or coderabbitai[bot].
# Argus posts GitHub Review objects (not CI checks).
REPO=$(gh pr view "$PR_NUMBER" --json baseRepository --jq '.baseRepository.nameWithOwner' 2>/dev/null)
if [ -z "$REPO" ]; then
  REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null)
fi

BOT_REVIEW_STATE=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}/reviews" \
  --jq '[.[] | select(
    .user.login == "argus-review[bot]" or
    .user.login == "coderabbitai[bot]" or
    .user.login == "coderabbitai"
  )] | last | .state // empty' 2>/dev/null)

# Also check legacy CodeRabbit CI check (transition period)
CR_CI_STATUS=$(gh pr checks "$PR_NUMBER" --json name,state -q '.[] | select(.name | test("CodeRabbit";"i")) | .state' 2>/dev/null | head -n1 | tr '[:upper:]' '[:lower:]')

# Allow if CodeRabbit CI passed
if [ "$CR_CI_STATUS" = "success" ]; then
  exit 0
fi

# Allow only if bot's latest review is APPROVED (COMMENTED alone is not sufficient)
if [ "$BOT_REVIEW_STATE" = "APPROVED" ]; then
  echo "NOTE: Review bot approved (reviewDecision: ${REVIEW_DECISION}) — allowing merge." >&2
  exit 0
fi

# ── Block: no review activity or review in progress ──────────────
if [ -z "$BOT_REVIEW_STATE" ] && [ -z "$CR_CI_STATUS" ]; then
  cat >&2 <<MSG
BLOCKED: No automated review found for PR #${PR_NUMBER}.
Trigger a review with /review or wait for auto-trigger.
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
  exit 2
fi

if [ "$REVIEW_DECISION" = "CHANGES_REQUESTED" ]; then
  cat >&2 <<MSG
BLOCKED: Changes requested on PR #${PR_NUMBER}.
Address review feedback before merging.
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
  exit 2
fi

# Fallback: review in progress or unexpected state
cat >&2 <<MSG
BLOCKED: Review not yet complete for PR #${PR_NUMBER} (reviewDecision: ${REVIEW_DECISION}, botReview: ${BOT_REVIEW_STATE:-none}, CI: ${CR_CI_STATUS:-none}).
Wait for review to complete before merging.
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
exit 2
