#!/usr/bin/env bash
# scripts/lane-cap-check.sh — Mercury active-lane count report.
# Originally the Δ7 HARD-CAP check (v0.1 Delta 7, Issue #314). The
# lane-count cap was removed by Issue #605 (#599 ADR D1: lanes are uncapped).
#
# Counts the number of `Status: active` lanes in LANES.md and reports them.
# By default there is NO cap: the verdict is `uncapped` and the exit code
# is 0. Pass `--max N` to opt in to a threshold of your own; the verdict is
# then `within_cap` / `exceeded` and exit code 1 signals "exceeded" so a
# caller that wants a gate (CI step / pre-commit hook) can use it.
#
# This script is ADVISORY: it never installs hooks or modifies LANES.md.
# It reports; any gating is up to the caller that passes --max. Hard
# mechanical enforcement is intentionally out of scope, since side lanes
# cannot easily install or modify shared hooks.
#
# Usage:
#   scripts/lane-cap-check.sh [--lanes-file PATH] [--memory-dir PATH]
#                             [--max N] [--format text|json]
#
# Defaults:
#   --lanes-file   <memory-dir>/LANES.md
#   --memory-dir   $MERCURY_MEMORY_DIR, else the project's lane home (scripts/lane-paths.sh memory-dir)
#   --max          (none: count-only, no cap)
#   --format       text
#
# Exit codes:
#   0  no --max given (count-only), or count <= max
#   1  --max given and count > max
#   2  invalid args / lanes-file missing / memory dir missing

set -u

LANE_PATHS_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/lane-paths.sh"
# shellcheck source=lib/lane-paths.sh
. "$LANE_PATHS_LIB"  # per-project lane home (#613)

die()  { printf 'lane-cap-check: %s\n' "$1" >&2; exit 2; }
warn() { printf 'lane-cap-check WARN: %s\n' "$1" >&2; }

# Defensive JSON-string escaper for lane names read from LANES.md (lane names
# are validated upstream by lane-claim/lane-close but lane-cap-check accepts
# whatever the file contains — manual edits could include quotes/backslashes
# that break JSON output). Output INCLUDES the surrounding double-quotes.
# Mirrors scripts/lane-sweep.sh json_string().
json_string() {
  local s="$1"
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\t'/\\t}
  printf '"%s"' "$s"
}

MAX=""
FORMAT=text
LANES_FILE=""
MEMORY_DIR=""

while [ $# -gt 0 ]; do
  case "$1" in
    --lanes-file)  shift; [ $# -gt 0 ] || die "--lanes-file needs a value"
                   [ -n "$1" ] || die "--lanes-file requires a non-empty path"
                   LANES_FILE="$1"; shift ;;
    --memory-dir)  shift; [ $# -gt 0 ] || die "--memory-dir needs a value"
                   [ -n "$1" ] || die "--memory-dir requires a non-empty path"
                   MEMORY_DIR="$1"; shift ;;
    --max)         shift; [ $# -gt 0 ] || die "--max needs a value"
                   [ -n "$1" ] || die "--max must be a positive integer: ''"
                   MAX="$1"; shift ;;
    --format)      shift; [ $# -gt 0 ] || die "--format needs a value"
                   FORMAT="$1"; shift ;;
    -h|--help)
      sed -n '2,30p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

# A set --max must be a positive integer without leading zeros, so it is
# emitted verbatim as a valid JSON number and compared as decimal.
if [ -n "$MAX" ]; then
  case "$MAX" in
    [1-9]|[1-9]*[0-9]) case "$MAX" in *[!0-9]*) die "--max must be a positive integer: '$MAX'" ;; esac ;;
    *) die "--max must be a positive integer: '$MAX'" ;;
  esac
fi
case "$FORMAT" in
  text|json) ;;
  *) die "--format must be text or json (got '$FORMAT')" ;;
esac

if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR=$(lane_memory_dir ".") \
    || die "cannot resolve this project's lane memory dir (run inside a checkout, pass --memory-dir, or set MERCURY_MEMORY_DIR)"
fi
[ -d "$MEMORY_DIR" ] || die "memory dir not found: $MEMORY_DIR (set --memory-dir or MERCURY_MEMORY_DIR)"

if [ -z "$LANES_FILE" ]; then LANES_FILE="$MEMORY_DIR/LANES.md"; fi
[ -f "$LANES_FILE" ] || die "LANES.md not found: $LANES_FILE"

# Parse Active Lanes section + count `Status: active` markers within it.
# Uses the same Active-Lanes detection logic as scripts/lane-sweep.sh
# (### `<name>` headings between "## Active Lanes" and the next "## " header).
# A lane is counted only if its section contains a Status line resolving to
# `active` (case-sensitive, matching the protocol's literal value).
# awk emits two stream types:
#   ACTIVE <lane>          — lane has Status: active
#   ORPHAN <lane>          — lane heading appeared but next heading came
#                            without a Status line (malformed LANES.md)
# Both are reported; ORPHAN counts as a parsing-warning that the operator
# should fix in LANES.md, but does NOT contribute to the active count.
PARSE_OUTPUT=$(awk '
  function flush(   was_orphan) {
    if (current_lane == "") return
    # ORPHAN only when no Status line at all was seen for the current lane.
    # A non-active Status (e.g. `closed` / `paused`) is well-formed — silent skip.
    if (had_status == 0) print "ORPHAN", current_lane
    current_lane = ""; had_status = 0
  }
  BEGIN { in_active = 0; current_lane = ""; had_status = 0 }
  /^## Active Lanes/ { in_active = 1; flush(); next }
  /^## / && in_active { flush(); in_active = 0 }
  in_active && /^### `[^`]+`/ {
    flush()
    match($0, /`[^`]+`/)
    current_lane = substr($0, RSTART + 1, RLENGTH - 2)
    next
  }
  in_active && current_lane != "" && /^- \*\*Status\*\*:/ {
    had_status = 1
    # `active` MUST be the full Status TOKEN — i.e. followed by either
    # end-of-line OR a non-identifier char (whitespace, punctuation).
    # Without this guard, `active-foo` / `active123` / `active-ish` would
    # false-match. The trailing-token requirement permits well-formed
    # annotations like "active — Phase B complete; ..." which Mercury
    # convention uses to attach short notes to the Status line.
    if ($0 ~ /^- \*\*Status\*\*: `?active`?([^A-Za-z0-9_-]|$)/) { print "ACTIVE", current_lane }
    # closed / paused / other non-active values: tracked but not counted
  }
  END { flush() }
' "$LANES_FILE") || die "awk parse failed for LANES.md (refusing to verdict on parse error): $LANES_FILE"

ACTIVE_LANES=$(printf '%s' "$PARSE_OUTPUT" | awk '/^ACTIVE / { sub(/^ACTIVE /, ""); print }') \
  || die "awk filter (ACTIVE) failed"
ORPHAN_LANES=$(printf '%s' "$PARSE_OUTPUT" | awk '/^ORPHAN / { sub(/^ORPHAN /, ""); print }') \
  || die "awk filter (ORPHAN) failed"

if [ -n "$ORPHAN_LANES" ]; then
  while IFS= read -r orphan; do
    [ -n "$orphan" ] && warn "lane '$orphan' has heading but no Status line — not counted (fix LANES.md)"
  done <<EOF
$ORPHAN_LANES
EOF
fi

COUNT=0
if [ -n "$ACTIVE_LANES" ]; then
  COUNT=$(printf '%s\n' "$ACTIVE_LANES" | grep -c .)
fi

if [ -z "$MAX" ]; then
  VERDICT="uncapped"
elif [ "$COUNT" -le "$MAX" ]; then
  VERDICT="within_cap"
else
  VERDICT="exceeded"
fi

if [ "$FORMAT" = "json" ]; then
  # Build a JSON array of escaped lane names — defensive against quote/
  # backslash characters in manually-tampered LANES.md (lane-cap-check
  # reads raw file content; lane-claim/lane-close validate upstream but
  # this script is the defensive escape layer for hostile inputs).
  JSON_LANES=""
  if [ -n "$ACTIVE_LANES" ]; then
    first=1
    while IFS= read -r ln; do
      [ -z "$ln" ] && continue
      if [ "$first" -eq 1 ]; then first=0; else JSON_LANES="${JSON_LANES},"; fi
      JSON_LANES="${JSON_LANES}$(json_string "$ln")"
    done <<EOF
$ACTIVE_LANES
EOF
  fi
  printf '{"max":%s,"active_count":%d,"verdict":"%s","lanes":[%s]}\n' \
    "${MAX:-null}" "$COUNT" "$VERDICT" "$JSON_LANES"
else
  if [ -z "$MAX" ]; then
    printf 'lane-cap-check: %d active lane(s), no cap → %s\n' "$COUNT" "$VERDICT"
  else
    printf 'lane-cap-check: %d active lane(s), cap=%d → %s\n' "$COUNT" "$MAX" "$VERDICT"
  fi
  if [ -n "$ACTIVE_LANES" ]; then
    printf '  active: %s\n' "$(printf '%s' "$ACTIVE_LANES" | tr '\n' ',' | sed 's/,$//')"
  fi
  if [ "$VERDICT" = "exceeded" ]; then
    printf '  note: over the --max %d threshold you passed (no built-in cap since #605)\n' "$MAX"
  fi
fi

[ "$VERDICT" = "exceeded" ] && exit 1 || exit 0
