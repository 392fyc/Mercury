#!/usr/bin/env bash
# scripts/lane-sweep.sh — Mercury multi-lane stale-lane detection (report only).
# Implements Rule 3.1 of feedback_lane_protocol.md (v0.1 Delta 2, Issue #310).
#
# A lane is "stale" if ALL THREE criteria are simultaneously older than the
# threshold (default 14 days):
#   1. Branch activity   — newest committerdate on `feature/lane-<lane>/*` refs
#                          (main lane also checks legacy `feature/TASK-*`)
#   2. Handoff activity  — mtime of the lane's `session-handoff[-<lane>].md`
#                          file in the user-memory directory
#   3. Issue activity    — newest `updatedAt` of any Issue carrying the
#                          `lane:<lane>` label (state=all)
#
# Per Rule 6 (LANES.md section ownership) this script REPORTS ONLY — it never
# mutates `LANES.md`. The owning lane is responsible for any status flip.
#
# Usage:
#   scripts/lane-sweep.sh [--lanes-file PATH] [--memory-dir PATH]
#                         [--days N] [--repo OWNER/REPO] [--repo-root PATH]
#                         [--format text|json] [--no-issue-check]
#
# Defaults:
#   --lanes-file   <memory-dir>/LANES.md
#   --memory-dir   ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --days         14
#   --repo         resolved at runtime via `gh repo view` (or GH_REPO env)
#   --repo-root    `git rev-parse --show-toplevel` from cwd; pin explicitly
#                  when running outside the Mercury checkout
#   --format       text
#
# Exit codes:
#   0  success — report emitted (independent of stale/fresh verdicts)
#   2  invalid args / missing required tools / cannot resolve repo or memory dir

set -u

die()  { printf 'lane-sweep: %s\n' "$1" >&2; exit 2; }
warn() { printf 'lane-sweep WARN: %s\n' "$1" >&2; }

DAYS=14
FORMAT=text
LANES_FILE=""
MEMORY_DIR=""
REPO=""
REPO_ROOT=""
NO_ISSUE_CHECK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --days)         shift; [ $# -gt 0 ] || die "--days needs a value"; DAYS="$1"; shift ;;
    --format)       shift; [ $# -gt 0 ] || die "--format needs a value"; FORMAT="$1"; shift ;;
    --lanes-file)   shift; [ $# -gt 0 ] || die "--lanes-file needs a value"; LANES_FILE="$1"; shift ;;
    --memory-dir)   shift; [ $# -gt 0 ] || die "--memory-dir needs a value"; MEMORY_DIR="$1"; shift ;;
    --repo)         shift; [ $# -gt 0 ] || die "--repo needs a value"; REPO="$1"; shift ;;
    --repo-root)    shift; [ $# -gt 0 ] || die "--repo-root needs a value"; REPO_ROOT="$1"; shift ;;
    --no-issue-check) NO_ISSUE_CHECK=1; shift ;;
    -h|--help)
      sed -n '2,35p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

# Repo-root resolution for branch-activity probe. Without an explicit pin,
# `git for-each-ref` would silently return empty in non-git contexts, masking
# operator error (e.g. running the sweep from the wrong directory) as
# branch_age=inf. Pinning a verified repo root makes the failure mode obvious.
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) \
    || die "cannot resolve repo root (cwd is not inside a git checkout — pass --repo-root explicitly)"
fi
[ -d "$REPO_ROOT/.git" ] || [ -f "$REPO_ROOT/.git" ] \
  || die "--repo-root is not a git checkout: $REPO_ROOT"

case "$DAYS" in
  ''|*[!0-9]*|0) die "--days must be a positive integer: '$DAYS'" ;;
esac
case "$FORMAT" in
  text|json) ;;
  *) die "--format must be text or json (got '$FORMAT')" ;;
esac

# Memory dir resolution. Caller may pass --memory-dir to point at a synthetic
# directory (used by the test harness); otherwise honor MERCURY_MEMORY_DIR env
# override; otherwise fall back to the canonical Claude Code path.
if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
[ -d "$MEMORY_DIR" ] || die "memory dir not found: $MEMORY_DIR (set --memory-dir or MERCURY_MEMORY_DIR)"

if [ -z "$LANES_FILE" ]; then
  LANES_FILE="$MEMORY_DIR/LANES.md"
fi
[ -f "$LANES_FILE" ] || die "LANES.md not found: $LANES_FILE"

# Repo resolution for Issue activity probe. Skip if --no-issue-check set
# (useful for offline runs / fast tests). Mirrors lane-claim.sh resolution.
if [ "$NO_ISSUE_CHECK" -eq 0 ]; then
  command -v gh >/dev/null 2>&1 || die "gh CLI required for Issue activity probe (or pass --no-issue-check)"
  command -v jq >/dev/null 2>&1 || die "jq required for Issue activity probe (or pass --no-issue-check)"
  if [ -z "$REPO" ]; then
    REPO="${GH_REPO:-}"
  fi
  if [ -z "$REPO" ]; then
    REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null) \
      || die "cannot determine target repo (pass --repo or set GH_REPO)"
  fi
  case "$REPO" in
    */*) ;;
    *) die "invalid repo format '$REPO' (expected owner/repo)" ;;
  esac
fi

NOW=$(date +%s)
THRESHOLD_SECS=$((DAYS * 86400))

# Parse active lane names from LANES.md. Active Lanes section is delimited by
# the "## Active Lanes" header and the next "## " header (Closed / Governance /
# Rollback). Inside, lane name appears as: ### `<name>` (default lane)?
parse_active_lanes() {
  awk '
    /^## Active Lanes/ { in_active = 1; next }
    /^## / && in_active { in_active = 0 }
    in_active && /^### `[^`]+`/ {
      match($0, /`[^`]+`/)
      name = substr($0, RSTART + 1, RLENGTH - 2)
      print name
    }
  ' "$1"
}

# Newest committerdate (unix ts) across refs matching the lane's branch glob.
# main lane also includes legacy `feature/TASK-*`. Empty output = no refs.
# Pinned via `git -C "$REPO_ROOT"` so cwd doesn't silently hijack the result.
branch_last_ts() {
  local lane="$1"
  local patterns=(
    "refs/heads/feature/lane-${lane}/*"
    "refs/remotes/*/feature/lane-${lane}/*"
  )
  if [ "$lane" = "main" ]; then
    patterns+=(
      "refs/heads/feature/TASK-*"
      "refs/remotes/*/feature/TASK-*"
    )
  fi
  git -C "$REPO_ROOT" for-each-ref --sort=-committerdate --format='%(committerdate:unix)' \
    "${patterns[@]}" 2>/dev/null | head -n1
}

# mtime of session-handoff[-<lane>].md. main lane file has no suffix.
handoff_last_ts() {
  local lane="$1"
  local file
  if [ "$lane" = "main" ]; then
    file="$MEMORY_DIR/session-handoff.md"
  else
    file="$MEMORY_DIR/session-handoff-${lane}.md"
  fi
  if [ -f "$file" ]; then
    # Portable mtime: try GNU stat then BSD stat.
    stat -c %Y "$file" 2>/dev/null || stat -f %m "$file" 2>/dev/null || echo ""
  else
    echo ""
  fi
}

# Newest Issue updatedAt for label lane:<lane>. Empty if no matching Issues
# or --no-issue-check set.
issue_last_ts() {
  local lane="$1"
  if [ "$NO_ISSUE_CHECK" -eq 1 ]; then
    echo ""
    return
  fi
  local out iso
  out=$(gh issue list --repo "$REPO" --label "lane:${lane}" --state all \
        --limit 200 --json updatedAt 2>/dev/null) || { echo ""; return; }
  iso=$(printf '%s' "$out" \
    | jq -r 'if length == 0 then "" else (max_by(.updatedAt).updatedAt) end' 2>/dev/null)
  if [ -n "$iso" ]; then
    date -d "$iso" +%s 2>/dev/null \
      || date -j -f '%Y-%m-%dT%H:%M:%SZ' "$iso" +%s 2>/dev/null \
      || echo ""
  else
    echo ""
  fi
}

# Defensive JSON-string escaper for fields read from LANES.md (lane names
# have no validation upstream — a manually-tampered LANES.md could include
# quote/backslash/newline that break JSON output). Output INCLUDES the
# surrounding double-quotes. Handles the common metacharacters; control
# bytes outside \n/\t fall through unescaped, which is acceptable for the
# defensive use case (Argus #327 finding).
json_string() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

# Age in days from a unix ts (empty → "inf"). Stable formatter for both
# text rows and JSON age fields.
age_days() {
  local ts="$1"
  if [ -z "$ts" ]; then
    echo "inf"
  else
    echo $(( (NOW - ts) / 86400 ))
  fi
}

is_stale_criterion() {
  local ts="$1"
  if [ -z "$ts" ]; then return 0; fi
  local age=$(( NOW - ts ))
  [ "$age" -gt "$THRESHOLD_SECS" ]
}

LANES=$(parse_active_lanes "$LANES_FILE")
if [ -z "$LANES" ]; then
  warn "no active lanes parsed from $LANES_FILE — empty report"
fi

if [ "$FORMAT" = "json" ]; then
  printf '{"threshold_days":%d,"generated_at":%d,"lanes":[' "$DAYS" "$NOW"
  first=1
fi

if [ "$FORMAT" = "text" ]; then
  printf '%-22s %-12s %-13s %-11s %s\n' "LANE" "BRANCH_AGE" "HANDOFF_AGE" "ISSUE_AGE" "VERDICT"
fi

while IFS= read -r lane; do
  [ -z "$lane" ] && continue
  bts=$(branch_last_ts "$lane")
  hts=$(handoff_last_ts "$lane")
  its=$(issue_last_ts "$lane")

  bage=$(age_days "$bts")
  hage=$(age_days "$hts")
  iage=$(age_days "$its")

  verdict=fresh
  if is_stale_criterion "$bts" && is_stale_criterion "$hts" && is_stale_criterion "$its"; then
    verdict=stale
  fi

  if [ "$FORMAT" = "text" ]; then
    printf '%-22s %-12s %-13s %-11s %s\n' "$lane" "${bage}d" "${hage}d" "${iage}d" "$verdict"
  else
    [ "$first" -eq 1 ] || printf ','
    first=0
    # Escape lane name (only field that comes from LANES.md text, may contain
    # arbitrary chars). Other fields are program-generated and constrained
    # to digits + "inf" + "fresh"/"stale".
    LANE_J=$(json_string "$lane")
    printf '{"lane":%s,"branch_age_days":"%s","handoff_age_days":"%s","issue_age_days":"%s","verdict":"%s"}' \
      "$LANE_J" "$bage" "$hage" "$iage" "$verdict"
  fi
done <<EOF
$LANES
EOF

if [ "$FORMAT" = "json" ]; then
  printf ']}\n'
fi

exit 0
