#!/usr/bin/env bash
# scripts/lane-cap-check.sh — Mercury multi-lane capacity advisory.
# Implements Rule 7 HARD-CAP of feedback_lane_protocol.md (v0.1 Delta 7,
# Issue #314).
#
# Counts the number of `Status: active` lanes in LANES.md. The protocol
# caps active lanes at 5 (Miller's 7±2 working memory + Google multi-agent
# 3-5 optimal + Personal Kanban WIP limits 3-5). Exceeding the cap requires
# either closing an existing lane OR opening an Issue with the
# `protocol-violation` label requesting a cap raise.
#
# This script is ADVISORY — it reports the count + verdict and uses exit
# code 1 to signal "cap exceeded" so callers can opt-in to gating (CI step
# / pre-commit hook). The script itself never installs hooks or modifies
# LANES.md. Hard mechanical enforcement is intentionally out of scope: the
# cap is sociotechnical (operator + maintainer review), and side lanes
# cannot easily install or modify shared hooks.
#
# Usage:
#   scripts/lane-cap-check.sh [--lanes-file PATH] [--memory-dir PATH]
#                             [--max N] [--format text|json]
#
# Defaults:
#   --lanes-file   <memory-dir>/LANES.md
#   --memory-dir   ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --max          5
#   --format       text
#
# Exit codes:
#   0  count <= max (within cap)
#   1  count >  max (cap exceeded — requires close-existing OR
#      protocol-violation Issue per Rule 7)
#   2  invalid args / lanes-file missing / memory dir missing

set -u

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

MAX=5
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
                   MAX="$1"; shift ;;
    --format)      shift; [ $# -gt 0 ] || die "--format needs a value"
                   FORMAT="$1"; shift ;;
    -h|--help)
      sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

case "$MAX" in
  ''|*[!0-9]*|0) die "--max must be a positive integer: '$MAX'" ;;
esac
case "$FORMAT" in
  text|json) ;;
  *) die "--format must be text or json (got '$FORMAT')" ;;
esac

if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
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

if [ "$COUNT" -le "$MAX" ]; then
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
  printf '{"max":%d,"active_count":%d,"verdict":"%s","lanes":[%s]}\n' \
    "$MAX" "$COUNT" "$VERDICT" "$JSON_LANES"
else
  printf 'lane-cap-check: %d active lane(s), cap=%d → %s\n' "$COUNT" "$MAX" "$VERDICT"
  if [ -n "$ACTIVE_LANES" ]; then
    printf '  active: %s\n' "$(printf '%s' "$ACTIVE_LANES" | tr '\n' ',' | sed 's/,$//')"
  fi
  if [ "$VERDICT" = "exceeded" ]; then
    printf '  resolution: close an existing lane OR open Issue with `protocol-violation` label requesting cap raise (per feedback_lane_protocol.md HARD-CAP §)\n'
  fi
fi

[ "$VERDICT" = "within_cap" ] && exit 0 || exit 1
