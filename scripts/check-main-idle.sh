#!/usr/bin/env bash
# scripts/check-main-idle.sh — detect main-lane idleness for Rule 4.1
# emergency spec-change escalation (v0.1 Delta 4, Issue #312).
#
# Evaluates THREE main-lane activity signals against the threshold
# (default 48h) and returns "idle" only when ALL THREE are simultaneously
# older than the threshold (logical AND, not maximum):
#   1. Newest commit on `feature/lane-main/*` or legacy `feature/TASK-*` refs
#   2. mtime of `<memory-dir>/session-handoff.md`
#   3. Newest `updatedAt` of any Issue carrying the `lane:main` label
#
# AND-gate prevents a single missing-data signal from producing a false
# "idle" verdict; all three must converge before the precondition is met.
# This script is a HELPER — it answers "is the threshold met?", not "should we
# escalate?". Escalation is always opt-in and arbitrated by the user per
# Rule 4.1.
#
# Usage:
#   scripts/check-main-idle.sh [--hours N] [--memory-dir PATH]
#                              [--repo OWNER/REPO] [--repo-root PATH]
#                              [--no-issue-check] [--format text|json]
#
# Defaults:
#   --hours        48
#   --memory-dir   ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --repo         resolved at runtime via `gh repo view` (or GH_REPO env)
#   --repo-root    `git rev-parse --show-toplevel` from cwd; pin explicitly
#                  when running outside the Mercury checkout
#   --format       text
#
# Exit codes:
#   0  main is idle past the threshold (escalation precondition met)
#   1  main is NOT idle (recent activity within window) — also returned for
#      report-only success cases
#   2  invalid args / missing required tools / cannot resolve memory or repo

set -u

die() { printf 'check-main-idle: %s\n' "$1" >&2; exit 2; }

HOURS=48
FORMAT=text
MEMORY_DIR=""
REPO=""
REPO_ROOT=""
NO_ISSUE_CHECK=0

while [ $# -gt 0 ]; do
  case "$1" in
    --hours)        shift; [ $# -gt 0 ] || die "--hours needs a value"; HOURS="$1"; shift ;;
    --memory-dir)   shift; [ $# -gt 0 ] || die "--memory-dir needs a value"; MEMORY_DIR="$1"; shift ;;
    --repo)         shift; [ $# -gt 0 ] || die "--repo needs a value"; REPO="$1"; shift ;;
    --repo-root)    shift; [ $# -gt 0 ] || die "--repo-root needs a value"; REPO_ROOT="$1"; shift ;;
    --format)       shift; [ $# -gt 0 ] || die "--format needs a value"; FORMAT="$1"; shift ;;
    --no-issue-check) NO_ISSUE_CHECK=1; shift ;;
    -h|--help)
      sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

# Repo-root for branch-activity probe. Same rationale as lane-sweep.sh —
# without an explicit pin, `git for-each-ref` silently returns empty in
# non-git contexts and masks operator error as branch_age=inf.
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) \
    || die "cannot resolve repo root (cwd is not inside a git checkout — pass --repo-root explicitly)"
fi
[ -d "$REPO_ROOT/.git" ] || [ -f "$REPO_ROOT/.git" ] \
  || die "--repo-root is not a git checkout: $REPO_ROOT"

case "$HOURS" in
  ''|*[!0-9]*|0) die "--hours must be a positive integer: '$HOURS'" ;;
esac
case "$FORMAT" in
  text|json) ;;
  *) die "--format must be text or json (got '$FORMAT')" ;;
esac

if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
[ -d "$MEMORY_DIR" ] || die "memory dir not found: $MEMORY_DIR"

if [ "$NO_ISSUE_CHECK" -eq 0 ]; then
  command -v gh >/dev/null 2>&1 || die "gh CLI required (or pass --no-issue-check)"
  command -v jq >/dev/null 2>&1 || die "jq required (or pass --no-issue-check)"
  if [ -z "$REPO" ]; then REPO="${GH_REPO:-}"; fi
  if [ -z "$REPO" ]; then
    REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null) \
      || die "cannot determine target repo (pass --repo or set GH_REPO)"
  fi
fi

NOW=$(date +%s)
THRESHOLD_SECS=$((HOURS * 3600))

# 1. Branch activity — main lane ref glob.
# Pinned via `git -C "$REPO_ROOT"` so cwd doesn't silently hijack the result.
branch_ts=$(git -C "$REPO_ROOT" for-each-ref --sort=-committerdate --format='%(committerdate:unix)' \
  'refs/heads/feature/lane-main/*' \
  'refs/remotes/*/feature/lane-main/*' \
  'refs/heads/feature/TASK-*' \
  'refs/remotes/*/feature/TASK-*' \
  2>/dev/null | head -n1)

# 2. Handoff mtime — main file is unsuffixed.
handoff_file="$MEMORY_DIR/session-handoff.md"
if [ -f "$handoff_file" ]; then
  handoff_ts=$(stat -c %Y "$handoff_file" 2>/dev/null || stat -f %m "$handoff_file" 2>/dev/null || echo "")
else
  handoff_ts=""
fi

# 3. Issue activity — `lane:main` label.
# Distinguish "no Issues with this label" (legitimate empty) from "probe
# failed" (gh/jq/date error). Failure must NOT silently produce a "main is
# idle" verdict — that would directly trigger spurious Rule 4.1 emergency
# escalation PRs (importance 8/10 per Argus #327 review). Die on probe
# failure; only an empty Issue list is treated as a missing-data signal.
issue_ts=""
if [ "$NO_ISSUE_CHECK" -eq 0 ]; then
  out=$(gh issue list --repo "$REPO" --label "lane:main" --state all --limit 200 --json updatedAt 2>/dev/null) \
    || die "gh issue list failed (Issue activity probe — refusing to verdict 'idle' on tool failure; pass --no-issue-check to bypass)"
  iso=$(printf '%s' "$out" | jq -r 'if length == 0 then "" else (max_by(.updatedAt).updatedAt) end' 2>/dev/null) \
    || die "jq parse failed on gh output (Issue activity probe — refusing to verdict on parse error)"
  if [ -n "$iso" ]; then
    issue_ts=$(date -d "$iso" +%s 2>/dev/null || date -j -f '%Y-%m-%dT%H:%M:%SZ' "$iso" +%s) \
      || die "date parse failed for ISO timestamp '$iso' (refusing to verdict on parse error)"
  fi
fi

age_hours() {
  local ts="$1"
  if [ -z "$ts" ]; then echo "inf"; else echo $(( (NOW - ts) / 3600 )); fi
}

is_idle_signal() {
  local ts="$1"
  if [ -z "$ts" ]; then return 0; fi
  [ $((NOW - ts)) -gt "$THRESHOLD_SECS" ]
}

bage=$(age_hours "$branch_ts")
hage=$(age_hours "$handoff_ts")
iage=$(age_hours "$issue_ts")

verdict=active
if is_idle_signal "$branch_ts" && is_idle_signal "$handoff_ts" && is_idle_signal "$issue_ts"; then
  verdict=idle
fi

if [ "$FORMAT" = "json" ]; then
  printf '{"threshold_hours":%d,"branch_age_hours":"%s","handoff_age_hours":"%s","issue_age_hours":"%s","verdict":"%s"}\n' \
    "$HOURS" "$bage" "$hage" "$iage" "$verdict"
else
  printf 'main lane idleness check (threshold %dh)\n' "$HOURS"
  printf '  branch_age:  %sh\n' "$bage"
  printf '  handoff_age: %sh\n' "$hage"
  printf '  issue_age:   %sh\n' "$iage"
  printf '  verdict:     %s\n' "$verdict"
fi

[ "$verdict" = "idle" ] && exit 0 || exit 1
