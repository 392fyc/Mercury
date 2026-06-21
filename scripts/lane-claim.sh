#!/usr/bin/env bash
# scripts/lane-claim.sh — Mercury multi-lane Issue claim with probe-after-write.
# Implements Rule 1.1 of feedback_lane_protocol.md (v0.1 Delta 1, Issue #309).
#
# Adds `lane:<lane>` label (+ @me assignee) to <issue-number>, then re-queries
# Issue labels. If post-write count of `lane:*` labels > 1, aborts non-zero
# and posts an Issue comment so the user can resolve the conflict.
#
# GitHub REST API non-atomic: two concurrent claims can both succeed silently.
# This wrapper detects the race post-hoc but immediately after the write —
# closer than v0 first-timestamp-wins (which never detects).
#
# Usage:
#   scripts/lane-claim.sh <lane-name> <issue-number> [--dry-run] [--no-assignee]
#
# Repo target: pinned at script start. Defaults to current cwd's git repo
# (via `gh repo view`); override with GH_REPO=owner/repo for CI / off-cwd use.
# All gh issue calls receive --repo $REPO explicitly to prevent cwd drift mid-run.
#
# Exit codes:
#   0  clean claim (exactly 1 lane:* label after probe)
#   1  conflict detected (>1 lane:* labels) or zero labels post-write
#   2  invalid args / gh|jq not available / API error / cannot resolve repo

set -u

die()  { printf 'lane-claim: %s\n' "$1" >&2; exit 2; }
warn() { printf 'lane-claim WARN: %s\n' "$1" >&2; }

LANE=""
ISSUE=""
DRY_RUN=0
NO_ASSIGNEE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --no-assignee) NO_ASSIGNEE=1; shift ;;
    -h|--help)
      sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --) shift; while [ $# -gt 0 ]; do
          if [ -z "$LANE" ]; then LANE="$1"
          elif [ -z "$ISSUE" ]; then ISSUE="$1"
          else die "too many positional arguments after --: $1"; fi
          shift
        done; break ;;
    -*) die "unknown flag: $1" ;;
    *)
      if [ -z "$LANE" ]; then
        LANE="$1"
      elif [ -z "$ISSUE" ]; then
        ISSUE="$1"
      else
        die "too many positional arguments; got: $1"
      fi
      shift
      ;;
  esac
done

[ -n "$LANE" ]  || die "missing <lane-name> argument (try --help)"
[ -n "$ISSUE" ] || die "missing <issue-number> argument (try --help)"

# Lane name validation: alnum + hyphen + underscore only. Protects label content
# from injection into shell expansions / JSON later in the comment payload.
case "$LANE" in
  -*) die "lane name must not start with hyphen: '$LANE'" ;;
  *[!A-Za-z0-9_-]*) die "lane name must be [A-Za-z0-9_-]: '$LANE'" ;;
esac
case "$ISSUE" in
  ''|*[!0-9]*|0) die "issue must be a positive integer: '$ISSUE'" ;;
esac

LABEL="lane:$LANE"

# Dry-run gate: print intent and exit without any external command (no gh / jq /
# git invocations). Strict offline semantics — useful for CI validation in
# minimal containers that may not even have gh installed yet.
if [ "$DRY_RUN" -eq 1 ]; then
  REPO_PREVIEW="${GH_REPO:-<resolved at runtime via gh repo view>}"
  printf '[dry-run] target repo: %s\n' "$REPO_PREVIEW"
  printf '[dry-run] step 1: gh issue edit %s --repo %s --add-label %s\n' \
    "$ISSUE" "$REPO_PREVIEW" "$LABEL"
  printf '[dry-run] step 2: gh issue view %s --repo %s --json labels (probe + count lane:* labels + ownership-invariant check)\n' \
    "$ISSUE" "$REPO_PREVIEW"
  if [ "$NO_ASSIGNEE" -eq 0 ]; then
    printf '[dry-run] step 3 (only if probe is clean): gh issue edit %s --repo %s --add-assignee @me\n' \
      "$ISSUE" "$REPO_PREVIEW"
  fi
  printf '[dry-run] no gh/jq calls executed in dry-run mode\n'
  exit 0
fi

# Live-mode pre-flight: tools + repo target.
command -v gh >/dev/null 2>&1 || die "gh CLI not installed or not on PATH"
command -v jq >/dev/null 2>&1 || die "jq not installed or not on PATH"

# Pin the target repo at script start. Defense against CI / fork / wrong-cwd
# execution silently writing to the wrong repo. Order: GH_REPO env override →
# `gh repo view` (current cwd's git repo via gh's resolution). All subsequent
# `gh issue *` calls get `--repo $REPO` explicitly so a mid-script cwd change
# or env mutation cannot redirect writes.
REPO="${GH_REPO:-}"
if [ -z "$REPO" ]; then
  # gh repo view can fail for several reasons: not in a git repo, no `origin`
  # remote, gh auth missing, network/API outage. Don't claim "not in a git repo"
  # specifically — the actual stderr is suppressed for cleanliness so the
  # caller cannot distinguish reasons. Suggest GH_REPO as the deterministic
  # override path.
  REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null) \
    || die "cannot determine target repo via GH_REPO or gh repo view (no git repo, no origin remote, gh auth missing, or API/network error)"
fi
case "$REPO" in
  */*) ;;
  *) die "invalid repo format '$REPO' (expected owner/repo)" ;;
esac

# Step 1: write LABEL only — assignee deferred until the probe verifies a clean
# claim (Copilot iter 2 #317: a losing concurrent claimant should not modify
# assignees on an Issue another lane already owns).
if ! gh issue edit "$ISSUE" --repo "$REPO" --add-label "$LABEL" >/dev/null; then
  die "gh issue edit #$ISSUE failed (add-label)"
fi

# Step 2: probe — re-query labels and count lane:* prefix
LABELS_JSON=$(gh issue view "$ISSUE" --repo "$REPO" --json labels 2>/dev/null) \
  || die "gh issue view #$ISSUE failed (post-write probe)"

LANE_LABELS=$(printf '%s' "$LABELS_JSON" \
  | jq -r '.labels[].name | select(startswith("lane:"))' 2>/dev/null) \
  || die "jq parse failed on gh issue view output"

if [ -z "$LANE_LABELS" ]; then
  LANE_COUNT=0
else
  LANE_COUNT=$(printf '%s\n' "$LANE_LABELS" | grep -c '^lane:')
fi

if [ "$LANE_COUNT" -gt 1 ]; then
  printf 'lane-claim CONFLICT: issue #%s has %d lane:* labels: %s\n' \
    "$ISSUE" "$LANE_COUNT" "$(printf '%s' "$LANE_LABELS" | tr '\n' ' ')" >&2

  ACTOR=$(gh api user --jq .login 2>/dev/null || echo unknown)
  # Strip backticks from label names before embedding in the markdown bullet
  # list. Lane names produced by this script are alnum + hyphen + underscore
  # (validated above), but other tools could create lane:* labels containing
  # backticks; embedding raw would break out of inline-code formatting.
  LABEL_LIST=$(printf '%s' "$LANE_LABELS" | tr -d '`' | sed 's/^/- `/' | sed 's/$/`/')
  COMMENT_BODY=$(cat <<EOF
:rotating_light: Lane claim conflict detected by \`scripts/lane-claim.sh\` (Rule 1.1 probe-after-write).

Post-write label set contains $LANE_COUNT \`lane:*\` labels:

$LABEL_LIST

This invocation attempted to add \`$LABEL\` as \`@$ACTOR\` (ordering of past claims is not knowable from this side — multiple lane labels are simply present after the probe).

GitHub REST API non-atomic — concurrent claims both succeeded silently. Manual arbitration required:

1. Decide which lane owns Issue #$ISSUE
2. Other lane(s): \`gh issue edit $ISSUE --repo $REPO --remove-label lane:<other>\`
3. Loser lanes close their session and fall back to non-conflicting work

Source: [Mercury lane-claim.md (Rule 1.1 in-repo guide)](https://github.com/$REPO/blob/HEAD/.mercury/docs/guides/lane-claim.md).
EOF
)
  # Conflict comment is a side-effect. Detection + non-zero exit are the hard
  # requirements; the comment is best-effort. Retry once on transient gh/API
  # failure; surface a loud warn if both attempts fail so the operator knows
  # the conflict needs manual posting (the conflict itself is still signaled
  # via stderr CONFLICT line above + exit 1 below).
  COMMENT_OK=0
  for attempt in 1 2; do
    if gh issue comment "$ISSUE" --repo "$REPO" --body "$COMMENT_BODY" >/dev/null 2>&1; then
      COMMENT_OK=1
      break
    fi
    [ "$attempt" -lt 2 ] && sleep 1
  done
  if [ "$COMMENT_OK" -eq 0 ]; then
    warn "FAILED to post conflict comment after 2 attempts — conflict is still signaled via the CONFLICT line above + exit 1; post the comment manually so the other lane sees the conflict"
  fi
  exit 1
fi

if [ "$LANE_COUNT" -eq 0 ]; then
  warn "no lane:* labels detected post-write — the edit may have silently failed"
  exit 1
fi

# Final invariant: the single lane:* label MUST be the one we tried to claim.
# A mismatch here means the edit silently failed AND a different lane already
# owns the Issue — exit 1 (treat as ownership conflict, not a clean claim).
# grep -F (literal) + -x (full-line) avoids regex surprises in $LABEL.
if ! printf '%s\n' "$LANE_LABELS" | grep -qxF "$LABEL"; then
  EXISTING=$(printf '%s' "$LANE_LABELS" | tr '\n' ' ' | sed 's/[[:space:]]*$//')
  warn "single lane:* label is '$EXISTING', not requested '$LABEL' — claim did not take effect (existing owner)"
  exit 1
fi

# Step 3: probe verified clean ownership — now safe to set assignee.
# Failure here is non-fatal; the label claim is already recorded.
if [ "$NO_ASSIGNEE" -eq 0 ]; then
  if ! gh issue edit "$ISSUE" --repo "$REPO" --add-assignee "@me" >/dev/null 2>&1; then
    warn "label claimed but --add-assignee @me failed — non-fatal, set assignee manually if needed"
  fi
fi

printf 'lane-claim: issue #%s claimed by %s (probe verified single lane label)\n' \
  "$ISSUE" "$LABEL"
exit 0
