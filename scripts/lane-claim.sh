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
# Exit codes:
#   0  clean claim (exactly 1 lane:* label after probe)
#   1  conflict detected (>1 lane:* labels) or zero labels post-write
#   2  invalid args / gh|jq not available / API error

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
      sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --) shift; break ;;
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
  ''|*[!0-9]*) die "issue must be a positive integer: '$ISSUE'" ;;
esac

command -v gh >/dev/null 2>&1 || die "gh CLI not installed or not on PATH"
command -v jq >/dev/null 2>&1 || die "jq not installed or not on PATH"

LABEL="lane:$LANE"

EDIT_ARGS=(--add-label "$LABEL")
if [ "$NO_ASSIGNEE" -eq 0 ]; then
  EDIT_ARGS+=(--add-assignee "@me")
fi

# Step 1: write — add the lane label (+ @me assignee unless suppressed)
if [ "$DRY_RUN" -eq 1 ]; then
  printf '[dry-run] gh issue edit %s %s\n' "$ISSUE" "${EDIT_ARGS[*]}"
  printf '[dry-run] probe + verdict skipped\n'
  exit 0
fi

if ! gh issue edit "$ISSUE" "${EDIT_ARGS[@]}" >/dev/null; then
  die "gh issue edit #$ISSUE failed"
fi

# Step 2: probe — re-query labels and count lane:* prefix
LABELS_JSON=$(gh issue view "$ISSUE" --json labels 2>/dev/null) \
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
  LABEL_LIST=$(printf '%s' "$LANE_LABELS" | sed 's/^/- `/' | sed 's/$/`/')
  COMMENT_BODY=$(cat <<EOF
:rotating_light: Lane claim conflict detected by \`scripts/lane-claim.sh\` (Rule 1.1 probe-after-write).

Post-write label set contains $LANE_COUNT \`lane:*\` labels:

$LABEL_LIST

Latest claim: \`$LABEL\` by \`@$ACTOR\`.

GitHub REST API non-atomic — concurrent claims both succeeded silently. Manual arbitration required:

1. Decide which lane owns Issue #$ISSUE
2. Other lane(s): \`gh issue edit $ISSUE --remove-label lane:<other>\`
3. Loser lanes close their session and fall back to non-conflicting work

Source: [Mercury feedback_lane_protocol.md Rule 1.1](https://github.com/392fyc/Mercury/blob/develop/.mercury/docs/guides/lane-claim.md).
EOF
)
  if ! gh issue comment "$ISSUE" --body "$COMMENT_BODY" >/dev/null 2>&1; then
    warn "failed to post conflict comment — please post manually"
  fi
  exit 1
fi

if [ "$LANE_COUNT" -eq 0 ]; then
  warn "no lane:* labels detected post-write — the edit may have silently failed"
  exit 1
fi

printf 'lane-claim: issue #%s claimed by %s (probe verified single lane label)\n' \
  "$ISSUE" "$LABEL"
exit 0
