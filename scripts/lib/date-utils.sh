#!/bin/bash
# scripts/lib/date-utils.sh — portable date parsing helpers for Mercury scripts.
# Source this file; do not execute directly.
#
# Provides:
#   parse_epoch <iso_timestamp>  — converts ISO 8601 string to Unix epoch (seconds).
#                                   Handles both Linux `date -d` and BSD `date -j`.
#                                   Falls back to 0 on parse failure.
#   normalize_iso <iso_timestamp> — strips +HH:MM / -HH:MM timezone suffix to Z-form
#                                   so BSD date -j can parse it.

# normalize_iso: convert "2026-04-26T12:34:56+00:00" → "2026-04-26T12:34:56Z"
# Works for any +HH:MM or -HH:MM offset by stripping the offset (treating as UTC
# for staleness purposes — sub-hour TZ differences are irrelevant for a 15-min gate).
normalize_iso() {
  local ts="$1"
  # Already Z-suffixed: return as-is
  if [[ "$ts" == *Z ]]; then
    echo "$ts"
    return
  fi
  # Strip trailing +HH:MM or -HH:MM (including +00:00 and -00:00 forms)
  echo "$ts" | sed 's/[+-][0-9][0-9]:[0-9][0-9]$/Z/'
}

# parse_epoch: convert ISO 8601 timestamp to Unix epoch integer.
# Strategy (in order):
#   1. TZ=UTC date -d   (GNU/Linux)
#   2. gdate -d         (GNU coreutils on macOS via brew)
#   3. BSD date -j -f   (macOS built-in, requires Z-normalized input)
#   4. Fallback: 0
parse_epoch() {
  local ts="$1"
  [ -z "$ts" ] && echo 0 && return

  local norm
  norm="$(normalize_iso "$ts")"

  # Try GNU date -d (Linux)
  local epoch
  epoch="$(TZ=UTC date -d "$norm" +%s 2>/dev/null)" && echo "$epoch" && return

  # Try gdate (GNU coreutils on macOS via brew)
  if command -v gdate > /dev/null 2>&1; then
    epoch="$(TZ=UTC gdate -d "$norm" +%s 2>/dev/null)" && echo "$epoch" && return
  fi

  # Try BSD date -j (macOS built-in); requires Z-normalized input
  epoch="$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$norm" +%s 2>/dev/null)" && echo "$epoch" && return

  # All parsers failed — return 0
  echo 0
}
