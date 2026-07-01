#!/usr/bin/env bash
# scripts/regenerate-memory-index.sh — Mercury memory-index regeneration.
# Implements Phase F.A (Issue #329, additive — landed) and Phase F.B (Issue #330,
# in-place cutover) of feedback_lane_protocol.md Rule 7 REPLACE (v0.1 Delta 5,
# parent epic #315).
#
# Reads a Mercury user-memory directory and emits a regenerated index document
# combining (a) the SESSION_INDEX.md table region and (b) the
# "Project (Session History)" bullets region of MEMORY.md.
#
# Modes:
#   - default (F.A additive):  writes <memory-dir>/INDEX.generated.md;
#                              canonical MEMORY.md / SESSION_INDEX.md untouched.
#   - --in-place (F.B cutover): rewrites canonical SESSION_INDEX.md table body
#                              AND MEMORY.md "Project (Session History)"
#                              subsection, idempotent via HTML-comment markers.
#                              BREAKING per Rule 7 v0 → v0.1 promotion. Phase F.D
#                              (Issue #514): the MEMORY.md region is a fixed
#                              pointer to SESSION_INDEX.md, NOT per-session
#                              bullets — the inlined history overflowed the
#                              ~24KB Claude Code context-load window. SESSION_INDEX.md
#                              + per-session sessions/*.md stay authoritative.
#
# Source precedence (per session row, both modes):
#   1. <memory-dir>/sessions/S<N>(-<lane>)?.md frontmatter — when present,
#      authoritative
#   2. <memory-dir>/SESSION_INDEX.md existing table row — fallback
#
# Markdown table cells emitted in either mode escape literal `|` to `\|` so
# pre-existing pipe-corruption rows (S71/S3-side-multi-lane/S4-side-multi-lane/
# S6-side-multi-lane carry `||` in code-spans) cleanly survive regenerate.
#
# Out of scope (per Issue #329/#330 acceptance criteria):
#   - feedback_*.md / project_*.md (non-session) / reference_*.md MEMORY.md rows
#   - mem0 / claude-handoff session_chain integration (orthogonal #252)
#
# Companion (Phase F.C, Issue #331): scripts/hooks/mercury-memory-index-write-guard.py
# (PreToolUse) + scripts/hooks/mercury-memory-index-validator.py (SessionEnd) provide
# mechanical enforcement of canonical-file edits via the regenerate flow. This script
# exports MERCURY_INDEX_REGENERATE=1 (defense-in-depth) so script-driven tool-use
# chains can be distinguished from direct Claude Edit/Write calls.
#
# Usage:
#   scripts/regenerate-memory-index.sh [--memory-dir PATH] [--output PATH]
#                                      [--format text|diff] [--in-place]
#
# Defaults:
#   --memory-dir   ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --output       <memory-dir>/INDEX.generated.md  (use - to write to stdout;
#                  ignored when --in-place is set)
#   --format       text  (diff = compare fresh regenerate against existing INDEX.generated.md
#                         snapshot for drift detection; does NOT compare against canonical
#                         MEMORY.md / SESSION_INDEX.md; mutually exclusive with --in-place)
#   --in-place     mutate canonical SESSION_INDEX.md + MEMORY.md (F.B cutover);
#                  inserts/replaces between HTML-comment markers, idempotent.
#
# Exit codes:
#   0  clean regenerate (output written; in diff mode: no drift vs prior INDEX.generated.md;
#      in --in-place mode: canonical files mutated successfully)
#   1  parse error in source file (per-session frontmatter malformed / unsupported
#      block scalar in frontmatter) OR diff mode detected drift vs prior
#      <memory-dir>/INDEX.generated.md snapshot
#   2  invalid args / memory dir missing / SESSION_INDEX.md or MEMORY.md missing /
#      output write failure (disk full / permission denied / parent dir missing) /
#      --in-place + --output | --format diff combined (mutually exclusive) /
#      --in-place canonical file (SESSION_INDEX.md or MEMORY.md) is a symlink, OR
#      its realpath resolves outside the --memory-dir realpath (Issue #516
#      symlink-hijack / realpath-ownership guard)

set -u

# Phase F.C lock-in (Issue #331): stamp environment so PreToolUse write-guard
# can identify script-driven runs and short-circuit allow. Defense-in-depth —
# canonical writes happen via shell I/O which does not fire Edit/Write hooks,
# but the stamp covers any edge path where a child process invokes Edit/Write
# (e.g. SDK-driven sub-agent calling regenerate from inside a tool-use chain).
export MERCURY_INDEX_REGENERATE=1

die()  { printf 'regenerate-memory-index: %s\n' "$1" >&2; exit 2; }
warn() { printf 'regenerate-memory-index WARN: %s\n' "$1" >&2; }

MEMORY_DIR=""
OUTPUT=""
FORMAT=text
IN_PLACE=0

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
    --in-place)   IN_PLACE=1; shift ;;
    -h|--help)
      # Print full Usage + Exit-codes block (must keep this end line in sync if header grows).
      sed -n '2,68p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

case "$FORMAT" in
  text|diff) ;;
  *) die "--format must be text or diff (got '$FORMAT')" ;;
esac

# --in-place is mutually exclusive with --output and --format diff. Combining either
# silently would leave operators uncertain about which file the cutover actually
# touched.
if [ "$IN_PLACE" = "1" ]; then
  [ -z "$OUTPUT" ]      || die "--in-place is mutually exclusive with --output"
  [ "$FORMAT" = "text" ] || die "--in-place is mutually exclusive with --format diff"
fi

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
# Emits TSV rows: session_id<TAB>date<TAB>theme<TAB>outcome<TAB>origin<TAB>file_path
# from existing SESSION_INDEX.md table. Skips header + separator lines.
# `file_path` is empty for SESSION_INDEX-sourced rows (no per-session file backing).
# Pipe characters embedded in cell content (observed in current SESSION_INDEX.md
# at S71/S3-side-multi-lane/S4-side-multi-lane/S6-side-multi-lane rows from
# code-span text such as `||` and `\|`) are detected via field-count > 7 → emits
# WARN to stderr but still continues with positional split. The WARN preserves
# operator visibility into pre-existing corruption; F.B cutover (--in-place)
# escapes `|` → `\|` on emit so the rewritten table renders cleanly even when
# the source had embedded pipes. Hard-fail was considered but rejected: blocking
# on existing data integrity would make F.A non-startable and F.B unrunnable
# until upstream cleanup, while WARN-then-continue lets F.B itself be the fix.
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
      # F.B emit_*_only() escapes `|` → `\|` so rewritten table is clean even with corrupt input.
      if (n > 7) {
        print "regenerate-memory-index WARN: SESSION_INDEX.md row at NR=" NR " has " n " pipe-separated fields (likely embedded `|` in cell — F.B --in-place will emit-escape but per-session migration recommended)" > "/dev/stderr"
      }
      sid = f[2]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", sid)
      dat = f[3]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", dat)
      thm = f[4]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", thm)
      out = f[5]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", out)
      org = f[6]; gsub(/^[[:space:]]+|[[:space:]]+$/, "", org)
      if (sid == "" || sid == "Session") next
      # 6th field (file_path) intentionally empty — SESSION_INDEX rows have no
      # backing per-session file unless one materializes in pass 1.
      printf "%s\t%s\t%s\t%s\t%s\t\n", sid, dat, thm, out, org
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
# parse_per_session_file <file> <file_path>
#
# Reads a memory/sessions/S<N>(-<lane>)?.md file. Returns TSV row:
# session_id<TAB>date<TAB>theme<TAB>outcome<TAB>origin<TAB>file_path
# OR exits 1 with WARN if frontmatter is malformed.
#
# `file_path` is the relative path embedded in the bullet's markdown link
# (e.g. "sessions/S6-side-multi-lane.md"). Caller is responsible for stable
# slug — script does not derive it from session_id since lane suffixes,
# range rows, and special cases need operator-controlled naming.
#
# Required frontmatter fields: session_id, date, description, outcome
# Optional: origin_session_id (defaults to "—" when missing or empty)
# ---------------------------------------------------------------------------
parse_per_session_file() {
  local file="$1"
  local file_path="$2"
  awk -v file="$file" -v fpath="$file_path" '
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
      printf "%s\t%s\t%s\t%s\t%s\t%s\n", sid, dat, thm, out, org, fpath
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

TMPFILE=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed"
TMPSEEN=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed"
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
    # Tightened pattern (H2 dual-verify fix + Argus iter1 unbounded-digit fix):
    # lane suffix MUST start with a lowercase letter and contain only
    # `[a-z0-9-]`. Session number is unbounded (originally capped at 3 digits;
    # Argus correctly noted S1000+ would silently skip — fixed via bash regex
    # since case-glob can't express "1+ digits" cleanly).
    if ! [[ "$ps_base" =~ ^S[0-9]+(-[a-z][a-z0-9-]*)?\.md$ ]]; then
      warn "skip non-canonical session filename: $ps_base (expected S<N>.md or S<N>-<lane>.md, lane=[a-z][a-z0-9-]*)"
      continue
    fi
    # File path embedded in bullet markdown links is relative to MEMORY.md
    # (which lives one level up from sessions/), so prefix with "sessions/".
    file_path="sessions/$ps_base"
    if ! row=$(parse_per_session_file "$ps_file" "$file_path"); then
      printf 'regenerate-memory-index: per-session file parse failed: %s (see WARN above)\n' "$ps_file" >&2
      exit 1
    fi
    sid_field=${row%%$'\t'*}
    # H2 dual-verify fix + Argus iter1 unbounded-digit fix: validate session_id
    # matches canonical S<N> or S<N>-<lane> form AND matches filename basename.
    # Catches frontmatter drift (e.g. `session_id: arbitrary string` or
    # filename/sid mismatch from rename). Bash regex (not case-glob) so session
    # number is unbounded.
    if ! [[ "$sid_field" =~ ^S[0-9]+(-[a-z][a-z0-9-]*)?$ ]]; then
      printf 'regenerate-memory-index: invalid session_id %q in %s (expected S<N> or S<N>-<lane>)\n' "$sid_field" "$ps_file" >&2
      exit 1
    fi
    expected_basename="${sid_field}.md"
    if [ "$expected_basename" != "$ps_base" ]; then
      printf 'regenerate-memory-index: session_id/filename mismatch in %s: frontmatter session_id=%s expects filename %s\n' \
        "$ps_file" "$sid_field" "$expected_basename" >&2
      exit 1
    fi
    printf '%s\n' "$sid_field" >> "$TMPSEEN"
    printf '%s\n' "$row" >> "$TMPFILE"
  done
fi

# Pass 2: SESSION_INDEX.md fallback (only sessions not already covered).
# Per-pass dedup catches accidental copy-paste duplicates within SESSION_INDEX.md itself
# (each sid only emitted once across both passes).
parse_existing_session_index "$SESSION_INDEX_FILE" | while IFS=$'\t' read -r sid dat thm out org fpath; do
  if grep -Fxq -- "$sid" "$TMPSEEN" 2>/dev/null; then continue; fi
  printf '%s\n' "$sid" >> "$TMPSEEN"
  # 6th field intentionally empty for SESSION_INDEX-sourced rows.
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$sid" "$dat" "$thm" "$out" "$org" "$fpath"
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

  # Emit sorted rows. Escape `|` → `\|` in description/outcome cells so
  # pre-existing pipe-corruption rows (S71/S3-side-multi-lane/S4-side-multi-lane/
  # S6-side-multi-lane carry literal `||` in code-spans) emit a clean markdown
  # table. The 6th tab-separated field (file_path) is consumed only by the
  # MEMORY history bullets emitter, not by this table-row emitter.
  #
  # md_escape uses split+join (not gsub) because gsub replacement-string backslash
  # interpretation in awk is implementation-defined when chained from a shell
  # heredoc — split+join produces deterministic `\|` (1 backslash, 1 pipe) per
  # match across gawk/bwk/mawk regardless of POSIX/non-POSIX mode.
  if [ -n "$SORTED" ]; then
    printf '%s\n' "$SORTED" | awk -F'\t' '
      function md_escape(s,    n, parts, i, result) {
        n = split(s, parts, "|")
        result = parts[1]
        for (i = 2; i <= n; i++) result = result "\\|" parts[i]
        return result
      }
      { printf "| %s | %s | %s | %s | %s |\n", $1, $2, md_escape($3), md_escape($4), $5 }
    '
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

# ---------------------------------------------------------------------------
# Phase F.B (--in-place) helpers — Issue #330
# ---------------------------------------------------------------------------
#
# F.B cutover replaces canonical SESSION_INDEX.md table body and MEMORY.md
# "Project (Session History)" subsection with regenerated content, idempotent
# via HTML-comment markers. Markers are inserted on first --in-place run and
# bound the regenerated region on subsequent runs. Pre-existing rows outside
# the marker region (header / preamble / footer / sibling subsections) are
# preserved verbatim — splice is line-anchored and never reflows surrounding
# content.
#
# Marker shape:
#   <!-- BEGIN: scripts/regenerate-memory-index.sh --in-place ... -->
#   ...regenerated content...
#   <!-- END: scripts/regenerate-memory-index.sh --in-place -->
#
# First-run insertion points:
#   - SESSION_INDEX.md: immediately after the table separator line (`|---|...`)
#   - MEMORY.md: immediately after the `## Project (Session History)` heading
#
# Subsequent runs: replace existing marker-bounded content. Idempotent —
# running twice produces byte-identical output.
# ---------------------------------------------------------------------------

# Markers — kept short to fit cleanly in markdown source. The `regenerate-memory-index.sh`
# token in BEGIN doubles as a search anchor for operators auditing canonical files.
IN_PLACE_BEGIN_MARKER='<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place (Issue #330 Phase F.B). Regenerated content — DO NOT edit between markers. -->'
IN_PLACE_END_MARKER='<!-- END: scripts/regenerate-memory-index.sh --in-place -->'

# emit_session_index_rows_only — emits sorted markdown table rows ONLY (no
# header, no separator, no wrapping front-matter). Used by --in-place to write
# the table body that gets spliced between the existing header/separator and
# the marker region. md_escape uses split+join (see emit_index for rationale).
emit_session_index_rows_only() {
  if [ -n "$SORTED" ]; then
    printf '%s\n' "$SORTED" | awk -F'\t' '
      function md_escape(s,    n, parts, i, result) {
        n = split(s, parts, "|")
        result = parts[1]
        for (i = 2; i <= n; i++) result = result "\\|" parts[i]
        return result
      }
      { printf "| %s | %s | %s | %s | %s |\n", $1, $2, md_escape($3), md_escape($4), $5 }
    '
  fi
}

# emit_memory_history_pointer — emits a FIXED pointer block for the MEMORY.md
# "Project (Session History)" marker region (Phase F.D, Issue #514). MEMORY.md
# no longer inlines per-session bullets: the full per-session history was held
# redundantly in MEMORY.md AND SESSION_INDEX.md, and at 100+ sessions it
# overflowed the ~24KB Claude Code context-load window so the back half of
# MEMORY.md was silently truncated at session start. SESSION_INDEX.md (the
# superset table) + the per-session sessions/S<N>(-<lane>)?.md bodies remain the
# authoritative, non-truncated home. Output is constant text (no session-derived
# content) → byte-identical across runs, preserving --in-place idempotency.
emit_memory_history_pointer() {
  cat <<'POINTER_EOF'
> 完整逐-session 历史已迁出 MEMORY.md 以保持其在 Claude Code 加载窗口内可读(Issue #514, Phase F.D)。
> 权威来源:[SESSION_INDEX.md](SESSION_INDEX.md)(本脚本 `--in-place` 维护的逐-session 表格)+ 各 `sessions/S<N>(-<lane>)?.md` 详档。
POINTER_EOF
}

# splice_session_index_in_place <source_file> <generated_rows_file>
# Mutates <source_file> in place: replaces marker-bounded region (subsequent
# runs) OR inserts marker block right after the table separator (first run).
# Atomic: writes to tmp file then mv. Operators recover via either:
#   (a) git revert if the canonical file is in the user-memory layer's git tracking
#   (b) cp from <memory-dir>/SESSION_INDEX.md.pre-cutover.bak (created on first
#       --in-place run; see backup logic below; NOTE (#517): original
#       F.B cutover snapshot was retired/deleted — .pre-cutover.bak created by
#       this script is a generic pre-mutation backup, not a cutover rollback)
splice_session_index_in_place() {
  local source_file="$1"
  local rows_file="$2"
  local tmp
  tmp=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed (splice_session_index_in_place)"

  # Pre-detect marker presence to choose splice strategy. Awk single-pass cannot
  # reliably distinguish first-run (no markers, insert after table separator)
  # from subsequent-run (markers exist, replace between them) because the
  # SESSION_INDEX.md table header is encountered BEFORE the marker line on
  # subsequent runs — pre-detection sidesteps the rule-ordering ambiguity.
  if grep -q '^<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place' "$source_file"; then
    # Subsequent run: marker-bounded replacement. Header/separator and any
    # other surrounding content are preserved verbatim by the catch-all `print`.
    awk -v gen="$rows_file" -v begin_m="$IN_PLACE_BEGIN_MARKER" -v end_m="$IN_PLACE_END_MARKER" '
      index($0, "<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place") == 1 {
        print begin_m
        while ((getline line < gen) > 0) print line
        close(gen)
        in_marker = 1
        next
      }
      in_marker && index($0, "<!-- END: scripts/regenerate-memory-index.sh --in-place") == 1 {
        print end_m
        in_marker = 0
        next
      }
      in_marker { next }
      { print }
    ' "$source_file" > "$tmp" || { rm -f "$tmp"; die "awk splice (subsequent) failed for $source_file"; }
  else
    # First run: no markers yet — locate table header + separator, insert
    # marker block immediately after separator, skip existing legacy rows.
    awk -v gen="$rows_file" -v begin_m="$IN_PLACE_BEGIN_MARKER" -v end_m="$IN_PLACE_END_MARKER" '
      BEGIN { state = "before" }
      state == "before" && /^\| Session \|/ { print; state = "header_seen"; next }
      state == "header_seen" && /^\|---/ {
        print
        print begin_m
        while ((getline line < gen) > 0) print line
        close(gen)
        print end_m
        state = "in_legacy_table"
        next
      }
      # Skip ALL content in the legacy table region: rows (^|), blank lines
      # between rows (preserve canonical SESSION_INDEX.md may have grown via
      # append-with-blank-separator pattern, e.g. S3-S70 contiguous + S<later>
      # rows separated by blank lines). Only headings (#) or blockquotes (>)
      # signal the end of the legacy table region.
      state == "in_legacy_table" && /^\|/ { next }
      state == "in_legacy_table" && /^[[:space:]]*$/ { next }
      state == "in_legacy_table" { state = "after" }
      { print }
    ' "$source_file" > "$tmp" || { rm -f "$tmp"; die "awk splice (first) failed for $source_file"; }
  fi
  mv "$tmp" "$source_file" || die "mv failed for $source_file"
}

# splice_memory_history_in_place <source_file> <generated_bullets_file>
# Mirror of splice_session_index_in_place for MEMORY.md "Project (Session
# History)" subsection. First run inserts markers immediately after the
# `## Project (Session History)` heading; subsequent runs replace marker
# content. The next `## ` heading bounds the legacy region on first run.
splice_memory_history_in_place() {
  local source_file="$1"
  local bullets_file="$2"
  local tmp
  tmp=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed (splice_memory_history_in_place)"

  # Pre-detect marker presence; same rationale as splice_session_index_in_place.
  if grep -q '^<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place' "$source_file"; then
    # Subsequent run: marker-bounded replacement.
    awk -v gen="$bullets_file" -v begin_m="$IN_PLACE_BEGIN_MARKER" -v end_m="$IN_PLACE_END_MARKER" '
      index($0, "<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place") == 1 {
        print begin_m
        while ((getline line < gen) > 0) print line
        close(gen)
        in_marker = 1
        next
      }
      in_marker && index($0, "<!-- END: scripts/regenerate-memory-index.sh --in-place") == 1 {
        print end_m
        in_marker = 0
        next
      }
      in_marker { next }
      { print }
    ' "$source_file" > "$tmp" || { rm -f "$tmp"; die "awk splice (subsequent) failed for $source_file"; }
  else
    # First run: insert markers immediately after `## Project (Session History)`
    # heading; skip legacy bullets until next `## ` heading (or EOF).
    awk -v gen="$bullets_file" -v begin_m="$IN_PLACE_BEGIN_MARKER" -v end_m="$IN_PLACE_END_MARKER" '
      BEGIN { state = "before" }
      state == "before" && /^## Project \(Session History\)/ {
        print
        print begin_m
        while ((getline line < gen) > 0) print line
        close(gen)
        print end_m
        state = "in_legacy_history"
        next
      }
      state == "in_legacy_history" && /^## / { state = "after" }
      state == "in_legacy_history" { next }
      { print }
    ' "$source_file" > "$tmp" || { rm -f "$tmp"; die "awk splice (first) failed for $source_file"; }
  fi
  mv "$tmp" "$source_file" || die "mv failed for $source_file"
}

# ---------------------------------------------------------------------------
# Main: --in-place mode short-circuit (Phase F.B)
# ---------------------------------------------------------------------------
if [ "$IN_PLACE" = "1" ]; then
  ROWS_TMP=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX")    || die "mktemp failed (rows tmp)"
  POINTER_TMP=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed (pointer tmp)"
  trap 'rm -f "$TMPFILE" "$TMPSEEN" "$ROWS_TMP" "$POINTER_TMP"' EXIT

  # Realpath ownership + symlink guard (Issue #516 hardening, defense-in-depth).
  # Runs before the backup loop (which does `cp`) and before the splice functions
  # (which do `mv`), so a hijacked canonical path never reaches either. Mirrors the
  # existing symlink posture already applied to sessions/ (Pass 1, `[ -L ]` skip
  # above) and the SAFE_MEMORY_DIR frontmatter sanitization — same threat family
  # (a canonical file replaced by a symlink, or an operator-supplied --memory-dir
  # that resolves outside its own tree), applied to the two --in-place mutation
  # targets. Not relying on `realpath`/`readlink -f` — neither is guaranteed on
  # macOS/BSD without coreutils, and this script targets git-bash/Linux/macOS
  # alike; `cd ... && pwd -P` is POSIX-portable and resolves symlinks in the
  # directory chain.
  MEM_DIR_REAL=$(cd "$MEMORY_DIR" && pwd -P) || die "failed to resolve realpath of memory dir: $MEMORY_DIR"
  for canonical in "$SESSION_INDEX_FILE" "$MEMORY_FILE"; do
    # Primary guard: refuse outright if the canonical path itself is a symlink.
    # This is the direct hit for the symlink-hijack scenario — cp/mv would
    # otherwise follow the link and mutate whatever it points to.
    if [ -L "$canonical" ]; then
      die "refuse to mutate symlinked canonical file: $canonical (Issue #516 symlink-hijack guard)"
    fi
    # Secondary guard: confirm the (non-symlink) canonical path's realpath still
    # lives under the memory dir's realpath. Catches a --memory-dir argument
    # that resolves outside its own tree via a symlinked ancestor directory.
    canonical_parent_real=$(cd "$(dirname "$canonical")" && pwd -P) || die "failed to resolve realpath of parent dir for: $canonical"
    canonical_real="$canonical_parent_real/$(basename "$canonical")"
    case "$canonical_real" in
      "$MEM_DIR_REAL"/*) ;;
      *) die "refuse to mutate canonical file outside memory dir: $canonical resolves to $canonical_real (expected under $MEM_DIR_REAL) (Issue #516 realpath-ownership guard)" ;;
    esac
  done

  # First-run safety: backup canonical files before mutation. Operators recover via
  # `cp <file>.pre-cutover.bak <file>` if cutover output drifts from expectation.
  # Skip backup if file already exists (subsequent runs are idempotent — backup
  # would overwrite the original pre-cutover snapshot with already-mutated content).
  # NOTE (#517): original F.B cutover .pre-cutover.bak snapshots were
  # retired/deleted; this block creates a generic pre-mutation backup (current state
  # at invocation time), NOT a cutover rollback snapshot.
  for canonical in "$SESSION_INDEX_FILE" "$MEMORY_FILE"; do
    bak="${canonical}.pre-cutover.bak"
    if [ ! -f "$bak" ]; then
      cp "$canonical" "$bak" || die "backup failed: $canonical → $bak"
      printf 'regenerate-memory-index: backup created: %s\n' "$bak" >&2
    fi
  done

  emit_session_index_rows_only > "$ROWS_TMP"    || die "emit session index rows failed"
  emit_memory_history_pointer  > "$POINTER_TMP" || die "emit memory pointer failed"

  splice_session_index_in_place "$SESSION_INDEX_FILE" "$ROWS_TMP"     || die "splice session index failed: $SESSION_INDEX_FILE"
  splice_memory_history_in_place "$MEMORY_FILE"        "$POINTER_TMP" || die "splice memory history failed: $MEMORY_FILE"

  # H1 dual-verify fix + Argus iter1 BEGIN/END symmetry fix: post-splice marker
  # verification. Three failure modes guarded:
  #   (a) BEGIN marker missing → source lacked the anchor (`| Session |` /
  #       `## Project (Session History)`); awk state machine no-op'd silently
  #   (b) END marker missing OR not exactly one → splice broken mid-write OR
  #       previous run left a partial state behind; re-running could produce
  #       structure damage compounding
  #   (c) BEGIN/END count mismatch (should be exactly one of each) → marker
  #       duplication from a prior buggy run; operator MUST restore from .bak
  #       before re-attempting cutover
  # All cases die loudly with concrete remediation so operators don't believe
  # a broken cutover landed cleanly. No auto-rollback (operator initiates per
  # documented Channel 2; auto-restore could mask repeated failures).
  for canonical in "$SESSION_INDEX_FILE" "$MEMORY_FILE"; do
    begin_count=$(grep -cF -- "$IN_PLACE_BEGIN_MARKER" "$canonical" 2>/dev/null || true)
    begin_count=${begin_count:-0}
    end_count=$(grep -cF -- "$IN_PLACE_END_MARKER" "$canonical" 2>/dev/null || true)
    end_count=${end_count:-0}
    if [ "$begin_count" -ne 1 ] || [ "$end_count" -ne 1 ]; then
      die "splice verification failed: $canonical has $begin_count BEGIN + $end_count END markers (expected 1+1). Likely missing anchor (table header / heading) OR prior partial-write state. Restore from $canonical.pre-cutover.bak (the existing generic pre-mutation backup; the original F.B cutover snapshot was retired #517) before re-attempting."
    fi
  done

  NCOUNT=0
  [ -n "$SORTED" ] && NCOUNT=$(printf '%s\n' "$SORTED" | awk 'NF{n++} END{print n+0}')
  printf 'regenerate-memory-index: in-place cutover complete (%d sessions written to canonical SESSION_INDEX.md + MEMORY.md)\n' "$NCOUNT"
  exit 0
fi

if [ "$FORMAT" = "diff" ]; then
  # Diff mode: regenerate to a tmp file then compare against the EXISTING
  # `<memory-dir>/INDEX.generated.md` snapshot from a prior text-mode run. This
  # detects drift in the regenerated index across consecutive runs (the F.A
  # soak signal). It does NOT compare against canonical MEMORY.md /
  # SESSION_INDEX.md — those remain read-only inputs in Phase F.A. The
  # `generated_at` field changes every run by design and is stripped before
  # compare so operators get a structural drift signal independent of timestamp.
  DIFF_TMP=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed"
  DIFF_STRIPPED_NEW=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed"
  DIFF_STRIPPED_OLD=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || die "mktemp failed"
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
