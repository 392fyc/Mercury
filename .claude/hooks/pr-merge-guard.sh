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
#     Fix: parse -R / --repo / --repo=VALUE and propagate to every gh call.
#
#   Session 27 / Issue #189 — Bug 2: grep pattern matched command text inside Write/Edit
#     content. Creating Issue #189 itself triggered the hook because the body text
#     contained the substring "gh pr merge". First attempt used an inverse whitelist
#     (first token must be `gh`) + naive sed-based segment splitting; Argus flagged
#     the whitelist as a bypass vector (`echo ok && gh pr merge N` would skip) and
#     the repo parsing as cross-segment bleed.
#
#   Session 27 / PR #210 iteration 2 — proper fix:
#     - Quote-aware awk segment splitter: respects single/double quote state, so
#       `git commit -m "...gh pr merge..."` and `echo '...;gh pr merge...'` both
#       produce a single segment whose first token is git/echo, which does not
#       match `^gh pr merge` → no false positive.
#     - Inverse whitelist removed: `echo ok && gh pr merge N` now correctly splits
#       into two segments, the second begins with `gh pr merge`, intercept fires.
#     - -R/--repo parsing is scoped to the matched merge segment only, not the
#       whole command — prevents `gh pr view -R A && gh pr merge 5 -R B` from
#       picking up repo A when validating the B merge.

INPUT=$(cat)
# Extract command (jq preferred, sed fallback)
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
fi

# ── Quote-aware segment splitter ─────────────────────────────────
# Splits $COMMAND on shell separators (;, |, ||, &&) while respecting
# single-quoted and double-quoted regions. Inside single quotes no
# interpretation occurs until the closing '. Inside double quotes,
# `\"` is an escaped quote and does NOT close the string; any other
# character passes through. Separators inside either quote type are
# emitted into the buffer verbatim. This is a minimal shell parser —
# it does not handle here-docs or backtick subshells, but those are
# irrelevant for the merge-detection use case.
#
# SQ is passed in as a variable because awk scripts are wrapped in
# bash single quotes, so a literal single quote character is not
# representable inline.
_SEGMENTS=$(printf '%s\n' "$COMMAND" | awk -v SQ="'" '
BEGIN { in_sq=0; in_dq=0; buf=""; esc=0 }
{
  line = $0
  n = length(line)
  for (i = 1; i <= n; i++) {
    c = substr(line, i, 1)
    if (esc) { buf = buf c; esc = 0; continue }
    if (in_sq) {
      if (c == SQ) in_sq = 0
      buf = buf c
      continue
    }
    if (in_dq) {
      if (c == "\\") { buf = buf c; esc = 1; continue }
      if (c == "\"") in_dq = 0
      buf = buf c
      continue
    }
    if (c == SQ) { in_sq = 1; buf = buf c; continue }
    if (c == "\"") { in_dq = 1; buf = buf c; continue }
    if (c == ";") { print buf; buf = ""; continue }
    if (c == "|") {
      nc = (i < n) ? substr(line, i+1, 1) : ""
      if (nc == "|") { print buf; buf = ""; i++ }
      else { print buf; buf = "" }
      continue
    }
    if (c == "&") {
      nc = (i < n) ? substr(line, i+1, 1) : ""
      if (nc == "&") { print buf; buf = ""; i++; continue }
      print buf; buf = ""; continue
    }
    buf = buf c
  }
  # preserve line breaks inside quoted regions, separate lines outside
  buf = buf " "
}
END { if (buf != "") print buf }
')

# ── Walk segments and find a real `gh pr merge` invocation ──────
_INTERCEPT=0
MATCHED_SEG=""
while IFS= read -r _seg; do
  _seg=$(printf '%s' "$_seg" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')
  [ -z "$_seg" ] && continue
  # Iteratively normalize the leading portion of the segment, stripping
  # any combination of:
  #   - VAR=value env-var prefixes (e.g. GH_TOKEN=x gh ...)
  #   - command wrappers (env, command, exec, builtin, nohup, time)
  #   - short/long flags that those wrappers may accept (-i, -u, -p, --)
  # The loop runs until the string stops changing (bounded to 5 passes).
  # This closes the `env ... gh pr merge` and `command gh pr merge`
  # bypass vectors flagged on PR #210.
  _first_cmd="$_seg"
  for _i in 1 2 3 4 5; do
    _prev_cmd="$_first_cmd"
    _first_cmd=$(printf '%s' "$_first_cmd" | sed 's/^[[:space:]]*//')
    _first_cmd=$(printf '%s' "$_first_cmd" | sed 's/^\([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]*\)*//')
    _first_cmd=$(printf '%s' "$_first_cmd" | sed -E 's/^(env|command|exec|builtin|nohup|time)[[:space:]]+//')
    _first_cmd=$(printf '%s' "$_first_cmd" | sed -E 's/^(--|-[A-Za-z][A-Za-z0-9-]*)([[:space:]]+|$)//')
    [ "$_first_cmd" = "$_prev_cmd" ] && break
  done
  # Token-walker state machine: match `gh [global-opts] pr [sub-opts] merge [merge-args]`.
  # This allows global gh options like `-R owner/name` to appear between `gh`
  # and the `pr` subcommand (addresses PR #210 Argus finding `gh -R X pr merge` bypass).
  # Uses bash array word-splitting on $_first_cmd — adequate for the command prefix
  # since global options and subcommand names are never quoted in realistic usage.
  # Quoted values in the MERGE tail (e.g. `-t "my title"`) are handled separately by
  # merge-arg extraction below.
  set -f  # disable glob expansion during the split
  # shellcheck disable=SC2206
  _tokens=( $_first_cmd )
  set +f
  _global_repo=""
  _pr_idx=-1
  _merge_idx=-1
  if [ "${_tokens[0]:-}" = "gh" ]; then
    _i=1
    _ntok=${#_tokens[@]}
    # Scan gh-global options until we hit `pr` or a non-option non-subcommand token
    while [ "$_i" -lt "$_ntok" ]; do
      _tok="${_tokens[$_i]}"
      case "$_tok" in
        -R|--repo)
          _i=$((_i + 1))
          _global_repo="${_tokens[$_i]:-}"
          ;;
        --repo=*)
          _global_repo="${_tok#--repo=}"
          ;;
        --help|--version|-h)
          ;;
        -*)
          # Unknown global option — assume no value (best-effort).
          ;;
        pr)
          _pr_idx=$_i
          _i=$((_i + 1))
          break
          ;;
        *)
          _i=$_ntok  # non-pr subcommand — not a merge
          break
          ;;
      esac
      _i=$((_i + 1))
    done
    # If we found `pr`, scan for `merge` (skipping any sub-opts)
    if [ "$_pr_idx" -ge 0 ]; then
      while [ "$_i" -lt "$_ntok" ]; do
        _tok="${_tokens[$_i]}"
        case "$_tok" in
          merge)
            _merge_idx=$_i
            break
            ;;
          -*)
            ;;  # sub-subcommand-level option, skip
          *)
            _i=$_ntok  # non-merge, abort
            break
            ;;
        esac
        _i=$((_i + 1))
      done
    fi
  fi
  if [ "$_merge_idx" -ge 0 ]; then
    _INTERCEPT=1
    MATCHED_SEG="$_first_cmd"
    GLOBAL_REPO_FLAG="$_global_repo"
    # Capture the merge-tail: everything after the `merge` token, as a string.
    # This is a raw slice from the original _first_cmd string, NOT the tokens
    # array, so quoted values inside the tail are preserved verbatim.
    MERGE_TAIL=$(printf '%s' "$_first_cmd" | sed -E 's/^.*[[:space:]]merge([[:space:]]+|$)//')
    break
  fi
done <<EOF
$_SEGMENTS
EOF

[ "$_INTERCEPT" -eq 1 ] || exit 0

# ── Parse -R / --repo from the MERGE tail only, fall back to global ──
# Merge-local -R takes precedence over any global -R captured before the
# `pr` subcommand. This handles `gh -R A pr merge 5 -R B` → repo = B.
REPO_FLAG=""
_prev=""
# shellcheck disable=SC2086
for _tok in $MERGE_TAIL; do
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
# Fall back to the global -R parsed during segment detection
[ -z "$REPO_FLAG" ] && REPO_FLAG="${GLOBAL_REPO_FLAG:-}"
# Strip surrounding quotes defensively (real repo names never need them).
REPO_FLAG="${REPO_FLAG%\"}"; REPO_FLAG="${REPO_FLAG#\"}"
REPO_FLAG="${REPO_FLAG%\'}"; REPO_FLAG="${REPO_FLAG#\'}"
# Build _REPO_ARG as a bash array for safe expansion at call sites.
# Replaces the previous `--repo $REPO_FLAG` string concat which word-split
# unsafely if the value ever contained whitespace or metacharacters.
if [ -n "$REPO_FLAG" ]; then
  _REPO_ARG=(--repo "$REPO_FLAG")
else
  _REPO_ARG=()
fi

# ── Extract PR selector from the MERGE tail ─────────────────────
# MERGE_TAIL contains just the tokens after `merge`. Walk them with a
# value-taking-flag skip list so the parser does not misidentify an option
# value (e.g. `-t "my title"`) as the PR selector.
# Value-taking options for `gh pr merge`:
#   -R / --repo           (also global — already handled above, but keep here for local scope)
#   -b / --body STRING
#   -B / --body-file FILE
#   -t / --subject STRING
#   -A / --author-email STRING
#   --match-head-commit SHA
PR_SELECTOR=""
_skip_next=0
# shellcheck disable=SC2086
for token in $MERGE_TAIL; do
  if [ "$_skip_next" -eq 1 ]; then
    _skip_next=0
    continue
  fi
  case "$token" in
    -R|--repo|-b|--body|-B|--body-file|-t|--subject|-A|--author-email|--match-head-commit)
      _skip_next=1
      continue
      ;;
    --repo=*|--body=*|--body-file=*|--subject=*|--author-email=*|--match-head-commit=*)
      continue
      ;;
    -*) continue ;;
    *) PR_SELECTOR="$token"; break ;;
  esac
done
# Defensive quote strip on the selector itself (real PR numbers are plain
# integers or URLs, but a `gh pr merge "5"` form could carry quotes).
PR_SELECTOR="${PR_SELECTOR%\"}"; PR_SELECTOR="${PR_SELECTOR#\"}"
PR_SELECTOR="${PR_SELECTOR%\'}"; PR_SELECTOR="${PR_SELECTOR#\'}"
PR_NUMBER=""

case "$PR_SELECTOR" in
  ''|--*)
    # No selector or flag-only; fallback to current branch PR
    PR_NUMBER=$(gh pr view "${_REPO_ARG[@]}" --json number -q '.number' 2>/dev/null)
    ;;
  *[!0-9]*)
    # URL or branch name selector — resolve via gh
    PR_NUMBER=$(gh pr view "$PR_SELECTOR" "${_REPO_ARG[@]}" --json number -q '.number' 2>/dev/null)
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
REVIEW_DECISION=$(gh pr view "$PR_NUMBER" "${_REPO_ARG[@]}" --json reviewDecision --jq '.reviewDecision // "REVIEW_REQUIRED"' 2>/dev/null | tr '[:lower:]' '[:upper:]')

if [ "$REVIEW_DECISION" = "APPROVED" ]; then
  exit 0
fi

# ── Secondary gate: has any review bot posted a review? ──────────
# Check for reviews from argus-review[bot] or coderabbitai[bot].
# Argus posts GitHub Review objects (not CI checks).
if [ -n "$REPO_FLAG" ]; then
  REPO="$REPO_FLAG"
else
  REPO=$(gh pr view "$PR_NUMBER" "${_REPO_ARG[@]}" --json baseRepository --jq '.baseRepository.nameWithOwner' 2>/dev/null)
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
CR_CI_STATUS=$(gh pr checks "$PR_NUMBER" "${_REPO_ARG[@]}" --json name,state -q '.[] | select(.name | test("CodeRabbit";"i")) | .state' 2>/dev/null | head -n1 | tr '[:upper:]' '[:lower:]')

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
