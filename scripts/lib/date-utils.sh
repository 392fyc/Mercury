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

# normalize_iso: strip a +HH:MM / -HH:MM offset suffix to a literal "Z" so that BSD
# `date -j -f "%Y-%m-%dT%H:%M:%SZ"` can parse it. The offset is DROPPED, not converted —
# this is lossy and only safe when the BSD fallback path is reached. GNU `date -d` and
# `gdate -d` parse offsets natively, so they MUST receive the original timestamp (not
# the normalized form) to avoid silent UTC drift on inputs like "2026-04-26T12:00:00+08:00".
normalize_iso() {
  local ts="$1"
  if [[ "$ts" == *Z ]]; then
    echo "$ts"
    return
  fi
  echo "$ts" | sed 's/[+-][0-9][0-9]:[0-9][0-9]$/Z/'
}

# parse_epoch: convert ISO 8601 timestamp to Unix epoch integer.
# Strategy (in order):
#   1. TZ=UTC date -d   (GNU/Linux, parses offset natively — pass raw $ts)
#   2. gdate -d         (GNU coreutils on macOS via brew, parses offset natively — raw $ts)
#   3. BSD date -j -f   (macOS built-in, requires Z-form — uses normalize_iso, drops offset)
#   4. Fallback: 0
# Mercury sources (git --date=format-local Z, gh API Z) all emit Z-form, so the BSD path's
# offset-drop is a no-op in practice. This ordering preserves correctness if a non-Z source
# is ever introduced.
parse_epoch() {
  local ts="$1"
  [ -z "$ts" ] && echo 0 && return

  local epoch
  epoch="$(TZ=UTC date -d "$ts" +%s 2>/dev/null)" && echo "$epoch" && return

  if command -v gdate > /dev/null 2>&1; then
    epoch="$(TZ=UTC gdate -d "$ts" +%s 2>/dev/null)" && echo "$epoch" && return
  fi

  local norm
  norm="$(normalize_iso "$ts")"
  epoch="$(date -j -f "%Y-%m-%dT%H:%M:%SZ" "$norm" +%s 2>/dev/null)" && echo "$epoch" && return

  echo 0
}
