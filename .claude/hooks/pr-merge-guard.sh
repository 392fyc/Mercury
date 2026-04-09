#!/usr/bin/env bash
# GATE: block gh pr merge unless automated review (Argus or CodeRabbit) has completed.
# Token cost: ~2 gh API calls when intercepted.
#
# Pattern: same as pre-commit-guard.sh
#   PreToolUse(Bash) → detect "gh pr merge" → check review status → block/allow
#
# Fix history:
#   Session 22 / Argus PR #7 — Bug 1: -R/--repo flag not propagated.
#     The hook parsed PR number from `gh pr merge N -R owner/repo` but then called
#     `gh pr view "$PR_NUMBER"` without --repo, so it queried Mercury's PR #N instead
#     of the target repo's PR #N and incorrectly blocked an APPROVED merge.
#     Fix: parse -R / --repo / --repo=VALUE from the command; propagate to every gh call.
#
#   Session 27 / Issue #189 — Bug 2: grep pattern matched command text inside Write/Edit
#     content. Creating Issue #189 itself triggered the hook because the body text
#     contained the substring "gh pr merge". The old pattern fired on any Bash command
#     whose text contained that substring.
#     Fix: anchor the match to command structure — only fire when a token sequence
#     "gh pr merge" appears at the start of a logical command segment (after optional
#     env-var prefixes, and across &&, ||, ;, | separators).

INPUT=$(cat)
# Extract command (jq preferred, sed fallback)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
fi

# ── Bug 2 fix (part 1): inverse whitelist — first token must be gh ──
# If the outer command's first real token is anything other than `gh`,
# skip entirely. This handles the common false-positive of
# `git commit -m "body gh pr merge"`, `cat <<EOF ... gh pr merge ... EOF`,
# `for k in ...; do ... gh pr merge ...`, etc. where the literal text
# appears inside a quoted/heredoc body, not as a real command.
# Strips leading whitespace + VAR=value env prefixes before checking.
# Realistic chained merges always begin with `gh` (e.g.
# `gh pr checks N && gh pr merge N`), so this is both simpler and
# more accurate than an explicit per-program whitelist.
_stripped=$(printf '%s' "$COMMAND" | sed 's/^[[:space:]]*//; s/^\([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]*\)*//')
_first_prog=$(printf '%s' "$_stripped" | awk '{print $1}')
if [ "$_first_prog" != "gh" ]; then
  exit 0
fi

# ── Bug 2 fix (part 2): anchor match to command structure ────────
# Split the command on shell separators (&&, ||, ;, |) so each logical
# segment is checked independently. Only intercept when a segment's
# first meaningful token sequence is literally: gh  pr  merge.
# Note: text-based splitting does NOT respect quote boundaries, so a
# literal `; gh pr merge` inside a quoted body of a non-whitelisted
# first program would still false-positive. The whitelist above
# covers the realistic cases.
_INTERCEPT=0
_SEGMENTS=$(printf '%s' "$COMMAND" | sed 's/&&/\n/g; s/||/\n/g; s/;/\n/g; s/|/\n/g')
while IFS= read -r _seg; do
  _seg=$(printf '%s' "$_seg" | sed 's/^[[:space:]]*//')
  [ -z "$_seg" ] && continue
  # Strip leading VAR=value env-var prefixes (e.g. GH_TOKEN=x gh pr merge ...)
  _first_cmd=$(printf '%s' "$_seg" | sed 's/^\([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]*\)*//')
  if printf '%s' "$_first_cmd" | grep -qE '^gh[[:space:]]+pr[[:space:]]+merge([[:space:]]|$)'; then
    _INTERCEPT=1
    break
  fi
done <<EOF
$_SEGMENTS
EOF

[ "$_INTERCEPT" -eq 1 ] || exit 0

# ── Bug 1 fix: parse -R / --repo and propagate to all gh calls ───
REPO_FLAG=""
_prev=""
for _tok in $COMMAND; do
  case "$_prev" in
    -R|--repo)
      REPO_FLAG="$_tok"
      break
      ;;
  esac
  case "$_tok" in
    --repo=*)
      REPO_FLAG="${_tok#--repo=}"
      break
      ;;
  esac
  _prev="$_tok"
done
if [ -n "$REPO_FLAG" ]; then
  _REPO_ARG="--repo $REPO_FLAG"
else
  _REPO_ARG=""
fi

# Extract PR selector — first non-flag token after `gh pr merge`
# Strip `gh pr merge` prefix, then find first token not starting with -
MERGE_ARGS=$(echo "$COMMAND" | sed -n 's/.*gh[[:space:]][[:space:]]*pr[[:space:]][[:space:]]*merge[[:space:]][[:space:]]*//p')
PR_SELECTOR=""
_skip_next=0
for token in $MERGE_ARGS; do
  if [ "$_skip_next" -eq 1 ]; then
    _skip_next=0
    continue
  fi
  case "$token" in
    -R|--repo)
      # Next token is the repo value, skip it
      _skip_next=1
      continue
      ;;
    -*) continue ;;
    *) PR_SELECTOR="$token"; break ;;
  esac
done
PR_NUMBER=""

case "$PR_SELECTOR" in
  ''|--*)
    # No selector or flag-only; fallback to current branch PR
    # shellcheck disable=SC2086
    PR_NUMBER=$(gh pr view $_REPO_ARG --json number -q '.number' 2>/dev/null)
    ;;
  *[!0-9]*)
    # URL or branch name selector — resolve via gh
    # shellcheck disable=SC2086
    PR_NUMBER=$(gh pr view "$PR_SELECTOR" $_REPO_ARG --json number -q '.number' 2>/dev/null)
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
# shellcheck disable=SC2086
REVIEW_DECISION=$(gh pr view "$PR_NUMBER" $_REPO_ARG --json reviewDecision --jq '.reviewDecision // "REVIEW_REQUIRED"' 2>/dev/null | tr '[:lower:]' '[:upper:]')

if [ "$REVIEW_DECISION" = "APPROVED" ]; then
  exit 0
fi

# ── Secondary gate: has any review bot posted a review? ──────────
# Check for reviews from argus-review[bot] or coderabbitai[bot].
# Argus posts GitHub Review objects (not CI checks).
if [ -n "$REPO_FLAG" ]; then
  REPO="$REPO_FLAG"
else
  # shellcheck disable=SC2086
  REPO=$(gh pr view "$PR_NUMBER" $_REPO_ARG --json baseRepository --jq '.baseRepository.nameWithOwner' 2>/dev/null)
  if [ -z "$REPO" ]; then
    REPO=$(gh repo view --json nameWithOwner --jq '.nameWithOwner' 2>/dev/null)
  fi
fi

BOT_REVIEW_STATE=$(gh api "repos/${REPO}/pulls/${PR_NUMBER}/reviews" \
  --jq '[.[] | select(
    .user.login == "argus-review[bot]" or
    .user.login == "coderabbitai[bot]" or
    .user.login == "coderabbitai"
  )] | last | .state // empty' 2>/dev/null)

# Also check legacy CodeRabbit CI check (transition period)
# shellcheck disable=SC2086
CR_CI_STATUS=$(gh pr checks "$PR_NUMBER" $_REPO_ARG --json name,state -q '.[] | select(.name | test("CodeRabbit";"i")) | .state' 2>/dev/null | head -n1 | tr '[:upper:]' '[:lower:]')

# ── Block CHANGES_REQUESTED before any bot/CI allow gates ─────────
# This prevents stale bot approvals or legacy CI success from bypassing
# a newer CHANGES_REQUESTED review decision.
if [ "$REVIEW_DECISION" = "CHANGES_REQUESTED" ]; then
  cat >&2 <<MSG
BLOCKED: Changes requested on PR #${PR_NUMBER}.
Address review feedback before merging.
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
  exit 2
fi

# Allow if CodeRabbit CI passed (and not CHANGES_REQUESTED — checked above)
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

# Fallback: review in progress or unexpected state
cat >&2 <<MSG
BLOCKED: Review not yet complete for PR #${PR_NUMBER} (reviewDecision: ${REVIEW_DECISION}, botReview: ${BOT_REVIEW_STATE:-none}, CI: ${CR_CI_STATUS:-none}).
Wait for review to complete before merging.
To bypass (human-approved): touch ${STATE_DIR}/pr-merge-approved-${PR_NUMBER}
MSG
exit 2
