#!/usr/bin/env bash
# scripts/regenerate-memory-index.sh — Mercury memory-index regeneration (Phase F.A).
# Implements Phase F.A of feedback_lane_protocol.md Rule 7 REPLACE (v0.1 Delta 5,
# Issue #329 / parent epic #315).
#
# Reads a Mercury user-memory directory and emits a regenerated index document
# combining (a) the SESSION_INDEX.md table region and (b) the
# "Project (Session History)" bullets region of MEMORY.md.
#
# Phase F.A is **non-breaking**: by default the regenerated index is written to
# a separate file (default <memory-dir>/INDEX.generated.md) so canonical
# MEMORY.md / SESSION_INDEX.md stay byte-identical during soak. Operators
# inspect the generated file via `diff` against expected output across multiple
# sessions to validate determinism before Phase F.B cutover (Issue #330).
#
# Source precedence (per session row):
#   1. <memory-dir>/sessions/S<N>(-<lane>)?.md frontmatter — when present,
#      authoritative
#   2. <memory-dir>/SESSION_INDEX.md existing table row — fallback
#
# This precedence lets Phase F.A operate immediately on TODAY's state (no
# per-session files yet exist) and gracefully accept per-session files as they
# appear during F.B cutover.
#
# Out of scope (per Issue #329 acceptance criteria):
#   - feedback_*.md / project_*.md (non-session) / reference_*.md MEMORY.md rows
#   - mem0 / claude-handoff session_chain integration (orthogonal #252)
#
# Usage:
#   scripts/regenerate-memory-index.sh [--memory-dir PATH] [--output PATH]
#                                      [--format text|diff]
#
# Defaults:
#   --memory-dir   ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --output       <memory-dir>/INDEX.generated.md  (use - to write to stdout)
#   --format       text  (diff = compare fresh regenerate against existing INDEX.generated.md
#                         snapshot for soak drift detection; does NOT compare against canonical
#                         MEMORY.md / SESSION_INDEX.md — those are read-only inputs in F.A)
#
# Exit codes:
#   0  clean regenerate (output written; in diff mode: no drift vs prior INDEX.generated.md)
#   1  parse error in source file (per-session frontmatter malformed / unsupported
#      block scalar in frontmatter) OR diff mode detected drift vs prior
#      <memory-dir>/INDEX.generated.md snapshot
#   2  invalid args / memory dir missing / SESSION_INDEX.md or MEMORY.md missing /
#      output write failure (disk full / permission denied / parent dir missing)

set -u

die()  { printf 'regenerate-memory-index: %s\n' "$1" >&2; exit 2; }
warn() { printf 'regenerate-memory-index WARN: %s\n' "$1" >&2; }

MEMORY_DIR=""
OUTPUT=""
FORMAT=text

while [ $# -gt 0 ]; do
  case "$1" in
    --memory-dir) shift; [ $# -gt 0 ] || die "--memory-dir needs a value"
                  [ -n "$1" ] || die "--memory-dir requires a non-empty path"
                  MEMORY_DIR="$1"; shift ;;
    --output)     shift; [ $# -gt 0 ] || die "--output needs a value"
                  [ -n "$1" ] || die "--output requires a non-empty path"
                  OUTPUT="$1"; shift ;;
    --format)     shift; [ $# -gt 0 ] || die "--format needs a value"
                  FORMAT="$1"; shift ;;
    -h|--help)
      # Print full Usage + Exit-codes block (must keep this end line in sync if header grows).
      sed -n '2,46p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

case "$FORMAT" in
  text|diff) ;;
  *) die "--format must be text or diff (got '$FORMAT')" ;;
esac

if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
[ -d "$MEMORY_DIR" ] || die "memory dir not found: $MEMORY_DIR (set --memory-dir or MERCURY_MEMORY_DIR)"

# Sanitize MEMORY_DIR for embedding into generated frontmatter — strip CR/LF/NUL so
# a hostile or accidentally-injected path can't insert spurious YAML keys via newline.
# Operators see a single-line `generated_from:` value regardless of source string content.
SAFE_MEMORY_DIR=$(printf '%s' "$MEMORY_DIR" | tr -d '\r\n\0')

if [ -z "$OUTPUT" ]; then OUTPUT="$MEMORY_DIR/INDEX.generated.md"; fi

SESSIONS_DIR="$MEMORY_DIR/sessions"
SESSION_INDEX_FILE="$MEMORY_DIR/SESSION_INDEX.md"
MEMORY_FILE="$MEMORY_DIR/MEMORY.md"

[ -f "$SESSION_INDEX_FILE" ] || die "SESSION_INDEX.md not found: $SESSION_INDEX_FILE"
[ -f "$MEMORY_FILE" ]        || die "MEMORY.md not found: $MEMORY_FILE"

# ---------------------------------------------------------------------------
# parse_existing_session_index <file>
#
# Emits TSV rows: session_id<TAB>date<TAB>theme<TAB>outcome<TAB>origin
# from existing SESSION_INDEX.md table. Skips header + separator lines.
# Pipe characters embedded in cell content (observed in current SESSION_INDEX.md
# at S72/S73 rows) are detected via field-count > 7 → emits WARN to stderr but
# still continues with positional split. The WARN preserves operator visibility
# into pre-existing corruption that F.B cutover should repair (escape `|` when
# writing per-session files). Hard-fail was considered but rejected for F.A:
# blocking on existing data integrity issues would make F.A non-startable on
# real Mercury memory dirs (S72/S73 already have embedded `|`).
# ---------------------------------------------------------------------------
parse_existing_session_index() {
  awk '
    BEGIN { FS = "|"; in_table = 0 }
    /^\| Session/    { in_table = 1; next }
    /^\|---/         { next }
    /^\|/ && in_table {
      # Trim leading/trailing pipe + each field whitespace
      n = split($0, f, "|")
      # Canonical 5-column row: f[1]=empty (leading |), f[2..6]=session/date/theme/outcome/origin, f[7]=empty (trailing |)
      if (n < 6) next
      # WARN if cell count > 7 — likely a literal "|" in cell content corrupting split.
      # F.A handles this by emitting a warn so operators see the corruption during soak; F.B
      # cutover should escape `|` properly when writing per-session files.
      if (n > 7) {
        print "regenerate-memory-index WARN: SESSION_INDEX.md row at NR=" NR " has " n " pipe-separated fields (likely embedded `|` in cell — output may drift from canonical)" > "/dev/stderr"
      }
      sid = f[2]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", sid)
      dat = f[3]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", dat)
      thm = f[4]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", thm)
      out = f[5]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", out)
      org = f[6]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", org)
      if (sid == "" || sid == "Session") next
      printf "%s\t%s\t%s\t%s\t%s\n", sid, dat, thm, out, org
    }
    /^[^|]/ && in_table { in_table = 0 }
  ' "$1"
}

# ---------------------------------------------------------------------------
# emit_memory_session_history <file>
#
# Emits the "Project (Session History)" subsection of MEMORY.md verbatim
# (between "## Project (Session History)" and the next "## " heading).
# Empty lines, link bullets with original separator (em dash OR ASCII hyphen)
# and original spacing, plain bullets, indented blockquotes, and any other
# content shape are preserved byte-for-byte. F.A is non-breaking — no
# normalization applied. F.B cutover (Issue #330) will introduce per-session-
# file driven synthesis at that boundary if needed.
# ---------------------------------------------------------------------------
emit_memory_session_history() {
  awk '
    BEGIN { in_section = 0 }
    /^## Project \(Session History\)/ { in_section = 1; next }
    /^## / && in_section { in_section = 0; next }
    in_section { print }
  ' "$1"
}

# ---------------------------------------------------------------------------
# parse_per_session_file <file>
#
# Reads a memory/sessions/S<N>(-<lane>)?.md file. Returns TSV row:
# session_id<TAB>date<TAB>theme<TAB>outcome<TAB>origin
# OR exits 1 with WARN if frontmatter is malformed.
#
# Required frontmatter fields: session_id, date, description, outcome
# Optional: origin_session_id (defaults to "—" when missing or empty)
# ---------------------------------------------------------------------------
parse_per_session_file() {
  local file="$1"
  awk -v file="$file" '
    BEGIN { in_fm = 0; sid = ""; dat = ""; thm = ""; out = ""; org = "—"; ok = 0 }
    NR == 1 && /^---[[:space:]]*$/ { in_fm = 1; next }
    in_fm && /^---[[:space:]]*$/ { in_fm = 0; ok = 1; exit }
    in_fm {
      # Reject YAML block scalars (| or >) for required fields — parser only supports
      # single-line key: value scalars per Phase F.A spec. F.B cutover will write
      # frontmatter via this contract; soak window catches violations early.
      if (match($0, /^[[:space:]]*(session_id|date|description|outcome|origin_session_id):[[:space:]]*[|>][[:space:]]*$/)) {
        print "regenerate-memory-index WARN: unsupported YAML block scalar (| or >) in " file " — frontmatter must use single-line scalars" > "/dev/stderr"
        exit 1
      }
      if (match($0, /^[[:space:]]*session_id:[[:space:]]*/)) {
        sid = substr($0, RSTART + RLENGTH)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", sid); gsub(/^["\047]|["\047]$/, "", sid)
      } else if (match($0, /^[[:space:]]*date:[[:space:]]*/)) {
        dat = substr($0, RSTART + RLENGTH)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", dat); gsub(/^["\047]|["\047]$/, "", dat)
      } else if (match($0, /^[[:space:]]*description:[[:space:]]*/)) {
        thm = substr($0, RSTART + RLENGTH)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", thm); gsub(/^["\047]|["\047]$/, "", thm)
      } else if (match($0, /^[[:space:]]*outcome:[[:space:]]*/)) {
        out = substr($0, RSTART + RLENGTH)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", out); gsub(/^["\047]|["\047]$/, "", out)
      } else if (match($0, /^[[:space:]]*origin_session_id:[[:space:]]*/)) {
        v = substr($0, RSTART + RLENGTH)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", v); gsub(/^["\047]|["\047]$/, "", v)
        if (v != "") org = v
      }
    }
    END {
      if (!ok)             { print "regenerate-memory-index WARN: frontmatter not closed in " file > "/dev/stderr"; exit 1 }
      if (sid == "")       { print "regenerate-memory-index WARN: session_id missing in " file > "/dev/stderr"; exit 1 }
      if (dat == "")       { print "regenerate-memory-index WARN: date missing in " file > "/dev/stderr"; exit 1 }
      if (thm == "")       { print "regenerate-memory-index WARN: description missing in " file > "/dev/stderr"; exit 1 }
      if (out == "")       { print "regenerate-memory-index WARN: outcome missing in " file > "/dev/stderr"; exit 1 }
      printf "%s\t%s\t%s\t%s\t%s\n", sid, dat, thm, out, org
    }
  ' "$file"
}

# ---------------------------------------------------------------------------
# Build merged session list
#
# Strategy:
#   1. If sessions/ dir exists: walk *.md, parse frontmatter → emit row
#      Track parsed session_ids in a set (passed via tmpfile)
#   2. Parse SESSION_INDEX.md → emit rows for session_ids NOT in set above
#   3. Sort by session_sort_key
#
# All rows go through one TMPFILE then sorted + emitted.
# ---------------------------------------------------------------------------

TMPFILE=$(mktemp) || die "mktemp failed"
TMPSEEN=$(mktemp) || die "mktemp failed"
trap 'rm -f "$TMPFILE" "$TMPSEEN"' EXIT

# Pass 1: per-session files (authoritative override).
# Parse error → exit 1 (per acceptance criterion: "parse error in source file").
# `[ -f ]` guard handles bash's no-glob-match literal-string fallback (avoids needing nullglob).
if [ -d "$SESSIONS_DIR" ]; then
  for ps_file in "$SESSIONS_DIR"/*.md; do
    [ -f "$ps_file" ] || continue
    # Skip symlinks — prevent path traversal (a symlink in sessions/ pointing outside
    # the memory dir would let an attacker exfiltrate or include arbitrary content).
    if [ -L "$ps_file" ]; then
      warn "skip symlink in sessions dir: $ps_file"
      continue
    fi
    # Restrict to S<N>.md or S<N>-<lane>.md naming. Drafts, READMEs, or accidentally
    # placed files in sessions/ are skipped with WARN rather than failing the whole run.
    ps_base=$(basename "$ps_file")
    case "$ps_base" in
      S[0-9]*.md|S[0-9]*-*.md) ;;
      *) warn "skip non-session markdown file: $ps_base"; continue ;;
    esac
    if ! row=$(parse_per_session_file "$ps_file"); then
      printf 'regenerate-memory-index: per-session file parse failed: %s (see WARN above)\n' "$ps_file" >&2
      exit 1
    fi
    sid_field=${row%%$'\t'*}
    printf '%s\n' "$sid_field" >> "$TMPSEEN"
    printf '%s\n' "$row" >> "$TMPFILE"
  done
fi

# Pass 2: SESSION_INDEX.md fallback (only sessions not already covered).
# Per-pass dedup catches accidental copy-paste duplicates within SESSION_INDEX.md itself
# (each sid only emitted once across both passes).
parse_existing_session_index "$SESSION_INDEX_FILE" | while IFS=$'\t' read -r sid dat thm out org; do
  if grep -Fxq -- "$sid" "$TMPSEEN" 2>/dev/null; then continue; fi
  printf '%s\n' "$sid" >> "$TMPSEEN"
  printf '%s\t%s\t%s\t%s\t%s\n' "$sid" "$dat" "$thm" "$out" "$org"
done >> "$TMPFILE"

# Sort by numeric prefix + lane name (stable for ties).
# session_id forms supported:
#   S<N>                     -> num = N, lane = "main"
#   S<N>-<lane>              -> num = N, lane = "<lane>"
#   S<N>-S<M>  / S<N>–S<M>   -> range row (en-dash or ASCII dash); num = N (lower bound), lane = "main"
# Unparseable IDs sort last (num = 999999) preserving source order via input position.
# POSIX-style 2-arg match() + RSTART/RLENGTH (no gawk array-capture extension).
SORTED=$(awk -F'\t' '
  function key(sid,    num, rest, lane) {
    if (match(sid, /^S[0-9]+/)) {
      num = substr(sid, RSTART + 1, RLENGTH - 1) + 0
      rest = substr(sid, RSTART + RLENGTH)
      # Strip range continuation (en-dash or ASCII dash followed by S<digits>)
      sub(/^[-–][Ss]?[0-9]+/, "", rest)
      # Strip leading "-" of lane suffix
      sub(/^-/, "", rest)
      lane = (rest == "") ? "main" : rest
      return sprintf("%06d\t%s", num, lane)
    }
    return sprintf("999999\t%s", sid)
  }
  { printf "%s\t%s\n", key($1), $0 }
' "$TMPFILE" | sort -t$'\t' -k1,1n -k2,2 | cut -f3-)

# ---------------------------------------------------------------------------
# Emit output
# ---------------------------------------------------------------------------

GENERATED_AT="${MERCURY_REGEN_TIMESTAMP:-$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null)}"
[ -n "$GENERATED_AT" ] || die "date command failed and MERCURY_REGEN_TIMESTAMP not set"

emit_index() {
  cat <<EOF
---
name: INDEX.generated
description: Auto-generated by scripts/regenerate-memory-index.sh — Phase F.A additive (Issue #329). DO NOT EDIT MANUALLY.
type: generated
generated_at: $GENERATED_AT
generated_from: $SAFE_MEMORY_DIR
---

# Memory Index — Auto-Generated

> Phase F.A additive output. Canonical \`MEMORY.md\` and \`SESSION_INDEX.md\` are
> NOT modified. Operators inspect drift via \`diff\` against canonical files
> across multiple sessions before Phase F.B cutover (Issue #330).

## SESSION_INDEX_GENERATED

> Replaces the table in \`SESSION_INDEX.md\` upon Phase F.B cutover.

| Session | 日期 | 任务主题 | 关键产出 | originSessionId |
|---------|------|----------|----------|-----------------|
EOF

  # Emit sorted rows
  if [ -n "$SORTED" ]; then
    printf '%s\n' "$SORTED" | awk -F'\t' '{ printf "| %s | %s | %s | %s | %s |\n", $1, $2, $3, $4, $5 }'
  fi

  cat <<EOF

## MEMORY_PROJECT_SESSION_HISTORY_GENERATED

> Replaces the "Project (Session History)" subsection of \`MEMORY.md\` upon
> Phase F.B cutover. During Phase F.A, this section is preserved verbatim from
> existing \`MEMORY.md\` (per-session file synthesis is a F.B-time concern).

EOF

  # For F.A: emit the existing MEMORY.md "Project (Session History)" section
  # byte-for-byte — empty lines preserved, original bullet separator (em dash
  # or ASCII hyphen) preserved, original spacing preserved. Non-breaking by
  # construction.
  emit_memory_session_history "$MEMORY_FILE"
}

if [ "$FORMAT" = "diff" ]; then
  # Diff mode: regenerate to a tmp file then compare against the EXISTING
  # `<memory-dir>/INDEX.generated.md` snapshot from a prior text-mode run. This
  # detects drift in the regenerated index across consecutive runs (the F.A
  # soak signal). It does NOT compare against canonical MEMORY.md /
  # SESSION_INDEX.md — those remain read-only inputs in Phase F.A. The
  # `generated_at` field changes every run by design and is stripped before
  # compare so operators get a structural drift signal independent of timestamp.
  DIFF_TMP=$(mktemp) || die "mktemp failed"
  DIFF_STRIPPED_NEW=$(mktemp) || die "mktemp failed"
  DIFF_STRIPPED_OLD=$(mktemp) || die "mktemp failed"
  trap 'rm -f "$TMPFILE" "$TMPSEEN" "$DIFF_TMP" "$DIFF_STRIPPED_NEW" "$DIFF_STRIPPED_OLD"' EXIT
  emit_index > "$DIFF_TMP"
  EXISTING_GEN="$MEMORY_DIR/INDEX.generated.md"
  if [ -f "$EXISTING_GEN" ]; then
    grep -v '^generated_at:' "$EXISTING_GEN" > "$DIFF_STRIPPED_OLD" || true
    grep -v '^generated_at:' "$DIFF_TMP"     > "$DIFF_STRIPPED_NEW" || true
    if diff -u "$DIFF_STRIPPED_OLD" "$DIFF_STRIPPED_NEW" >/dev/null; then
      printf 'regenerate-memory-index: no drift (INDEX.generated.md structurally byte-identical to fresh regenerate, ignoring generated_at)\n'
      exit 0
    else
      printf 'regenerate-memory-index: DRIFT detected vs existing INDEX.generated.md (structural — generated_at ignored)\n' >&2
      diff -u "$DIFF_STRIPPED_OLD" "$DIFF_STRIPPED_NEW" >&2 || true
      exit 1
    fi
  else
    printf 'regenerate-memory-index: no existing INDEX.generated.md to diff against (run without --format diff first)\n' >&2
    exit 1
  fi
fi

if [ "$OUTPUT" = "-" ]; then
  emit_index
else
  # Check redirect failure (disk full / permission denied / parent dir missing).
  # Without this, "wrote N sessions" would print even when the redirect failed —
  # operators would believe the soak diff is valid against a stale or absent file.
  if ! emit_index > "$OUTPUT"; then
    die "failed to write output: $OUTPUT (disk full / permission denied / parent dir missing)"
  fi
  # Count sessions via awk (avoids `grep -c .` pipefail hazard + grep returning 1 on empty input).
  NCOUNT=0
  [ -n "$SORTED" ] && NCOUNT=$(printf '%s\n' "$SORTED" | awk 'NF{n++} END{print n+0}')
  printf 'regenerate-memory-index: wrote %s (%d sessions)\n' "$OUTPUT" "$NCOUNT"
fi

exit 0
