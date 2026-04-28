#!/usr/bin/env bash
# scripts/migrate-session-index-to-files.sh — Phase F.B one-shot migration helper.
# Reads <memory-dir>/SESSION_INDEX.md table rows and writes one
# memory/sessions/S<N>(-<lane>)?.md per row.
#
# Implements the per-session file split required by Issue #330 (Phase F.B
# cutover, parent epic #315, Rule 7 v0.1 BREAKING). After migration, the
# regenerate-memory-index.sh `--in-place` cutover replaces canonical
# SESSION_INDEX.md table body and MEMORY.md "Project (Session History)"
# subsection with content sourced from these per-session files.
#
# Idempotent — skips files that already exist (preserves S6/S7/S8-side-multi-lane
# pre-existing previews from F.A soak window). Pass --force to overwrite.
#
# Pipe-corruption handling: rows with embedded `|` in cell content (NR=66/71/73/77
# in current SESSION_INDEX.md, observed in S71 / S3-side-multi-lane /
# S4-side-multi-lane / S6-side-multi-lane code-spans containing `||` and `\|`)
# emit WARN and the migration MERGES the split fields back into the description/
# outcome cells using a heuristic: assume corruption is in the description (col 4)
# or outcome (col 5) and reconstruct by re-joining excess fields. Operators
# inspect the WARN-named files manually before running --in-place to confirm
# reconstruction matches intent. Pre-existing files (already hand-written for
# the corrupt rows) take precedence via the idempotent skip.
#
# Usage:
#   scripts/migrate-session-index-to-files.sh [--memory-dir PATH] [--force] [--dry-run]
#
# Defaults:
#   --memory-dir   ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --force        overwrite existing per-session files (default: skip)
#   --dry-run      print intended writes to stdout, do not touch disk
#
# Exit codes:
#   0  migration completed (some files may have been skipped — see stderr WARN)
#   1  source SESSION_INDEX.md unparseable / required field missing
#   2  invalid args / memory dir missing

set -u

die()  { printf 'migrate-session-index: %s\n' "$1" >&2; exit 2; }
warn() { printf 'migrate-session-index WARN: %s\n' "$1" >&2; }

MEMORY_DIR=""
FORCE=0
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --memory-dir) shift; [ $# -gt 0 ] || die "--memory-dir needs a value"
                  [ -n "$1" ] || die "--memory-dir requires a non-empty path"
                  MEMORY_DIR="$1"; shift ;;
    --force)      FORCE=1; shift ;;
    --dry-run)    DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '2,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
[ -d "$MEMORY_DIR" ] || die "memory dir not found: $MEMORY_DIR"

SESSION_INDEX_FILE="$MEMORY_DIR/SESSION_INDEX.md"
SESSIONS_DIR="$MEMORY_DIR/sessions"
[ -f "$SESSION_INDEX_FILE" ] || die "SESSION_INDEX.md not found: $SESSION_INDEX_FILE"

if [ "$DRY_RUN" = "0" ]; then
  mkdir -p "$SESSIONS_DIR" || die "mkdir failed: $SESSIONS_DIR"
fi

# ---------------------------------------------------------------------------
# Filename derivation from session_id
#
#   S29              -> sessions/S29.md
#   S6-side-multi-lane -> sessions/S6-side-multi-lane.md
#   S35–S46          -> sessions/S35-S46.md (en-dash → ASCII hyphen for portability;
#                       caller can split into 12 stub files manually post-migration)
# ---------------------------------------------------------------------------
derive_filename() {
  local sid="$1"
  # Replace en-dash with ASCII hyphen (en-dash in filenames is portable on
  # NTFS/APFS/ext4 but causes friction in CLI tooling — better to normalize
  # here and let operators see consistent ASCII names).
  printf '%s.md' "$sid" | tr '–' '-'
}

# ---------------------------------------------------------------------------
# Lane derivation from session_id
#
#   S29                -> main
#   S6-side-multi-lane -> side-multi-lane
#   S12-team-a         -> team-a   (any hyphen-separated suffix, not just side-*)
#   S35-S46            -> S46      (range row — caller must validate; treated as
#                                   sentinel suffix, not a real lane)
# ---------------------------------------------------------------------------
derive_lane() {
  local sid="$1"
  case "$sid" in
    S[0-9]*-*)
      # Strip "S<digits>-" prefix to get lane name. Matches ANY hyphen-separated
      # suffix (side-* / team-* / experiment-* / etc) — the original `*-side-*`
      # pattern incorrectly classified non-side lanes as `main`.
      printf '%s' "$sid" | sed 's/^S[0-9]\{1,\}-//'
      ;;
    *)
      printf 'main'
      ;;
  esac
}

# ---------------------------------------------------------------------------
# YAML scalar escaping
#
# Single-line scalar wrapped in double quotes. Internal `"` → `'` (Mercury
# cell content rarely uses raw English double quotes; replacement preserves
# readability). Internal `\` → `\\`. Newlines / CR stripped (cells are
# single-line in source markdown table).
# ---------------------------------------------------------------------------
yaml_escape() {
  printf '%s' "$1" \
    | tr -d '\r\n' \
    | sed 's/\\/\\\\/g; s/"/'\''/g'
}

# ---------------------------------------------------------------------------
# Counters + WARN capture
#
# `parse_session_index_rows` emits row TSV on stdout and WARN messages on
# stderr. To both surface WARNs to the operator AND count them post-hoc, route
# stderr through a tmp file (avoids the subshell-scope-loss bug where a tee+
# grep -c chain inside `< <(...)` process substitution loses WARN counts).
# ---------------------------------------------------------------------------
WRITTEN=0
SKIPPED=0
STDERR_LOG=$(mktemp) || die "mktemp failed (stderr log)"
trap 'rm -f "$STDERR_LOG"' EXIT

# ---------------------------------------------------------------------------
# Main pass: parse table rows, write per-session files
#
# Awk parses the table; each row emits TSV `sid<TAB>date<TAB>theme<TAB>outcome<TAB>origin`
# with corruption-recovery for rows where field count exceeds 7 (rejoin extras
# back into the cell where they belong via best-effort heuristic: extra fields
# are joined into the THEME column since most observed corruption is in the
# theme/outcome boundary from `|` in code-spans).
# ---------------------------------------------------------------------------
parse_session_index_rows() {
  awk '
    BEGIN { FS = "|"; in_table = 0 }
    /^\| Session/  { in_table = 1; next }
    /^\|---/       { next }
    /^\|/ && in_table {
      n = split($0, f, "|")
      if (n < 6) next
      # Standard 7-field row (n==7): leading + 5 cells + trailing
      if (n == 7) {
        sid = f[2]; dat = f[3]; thm = f[4]; out = f[5]; org = f[6]
      } else {
        # Corruption recovery: extras join into theme cell. Empirically the
        # corrupt rows have `||` or `\|` inside code-span text that landed in
        # the theme cell, so rejoining f[4..n-3] preserves the original cell
        # boundaries (sid=f[2], date=f[3], outcome=f[n-2], origin=f[n-1],
        # theme = join of f[4]..f[n-3]).
        if (n < 8) next  # too few extras to safely reconstruct
        sid = f[2]; dat = f[3]; org = f[n-1]; out = f[n-2]
        thm = f[4]
        for (i = 5; i <= n - 3; i++) thm = thm "|" f[i]
        # WARN to stderr so caller flags the row for manual review
        printf "WARN: row at NR=%d has %d fields, reconstructed theme via best-effort merge: sid=%s\n", NR, n, sid > "/dev/stderr"
      }
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", sid)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", dat)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", thm)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", out)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", org)
      if (sid == "" || sid == "Session") next
      printf "%s\t%s\t%s\t%s\t%s\n", sid, dat, thm, out, org
    }
    /^[^|]/ && in_table { in_table = 0 }
  ' "$1"
}

# Iterate rows
while IFS=$'\t' read -r sid dat thm out org; do
  [ -z "$sid" ] && continue

  # SECURITY: path-traversal guard — validate sid against canonical whitelist
  # BEFORE constructing $filepath. SESSION_INDEX.md is a trusted local file but
  # this defense-in-depth blocks any input mutation (e.g. operator paste error
  # introducing `../etc/passwd`) from writing outside SESSIONS_DIR.
  # Whitelist: S<N>(-<lane>)? where N is 1+ digits and lane = [a-z][a-z0-9-]*.
  # Range rows like `S35–S46` (en-dash) are intentionally REJECTED by this
  # whitelist (the lane portion `S46` starts uppercase). This is correct
  # behavior: range rows are a legacy SESSION_INDEX shape that does NOT fit
  # the per-session-files model — operators must manually split them into
  # individual stubs (e.g. S35.md ... S46.md) before --in-place cutover.
  # Skipped range rows surface as `1 skipped` in the final report so the
  # operator notices and acts.
  # Argus iter 2 fix (规则不一致): align lowercase-lane constraint with the
  # regenerate-memory-index.sh canonical pattern. Original `[A-Za-z]` allowed
  # uppercase suffixes, which migrate would write to disk but `--in-place`
  # would later REJECT, producing a "migrated but not effective" hidden gap.
  # Lower-case-only matches Mercury Rule 2.1 short-name convention.
  if ! [[ "$sid" =~ ^S[0-9]+(-[a-z][a-z0-9-]*)?$ ]]; then
    warn "path-traversal guard: rejecting non-canonical session_id $sid (expected S<N> or S<N>-<lane> with lane=[a-z][a-z0-9-]* — must match regenerate-memory-index.sh)"
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  filename=$(derive_filename "$sid")
  filepath="$SESSIONS_DIR/$filename"
  lane=$(derive_lane "$sid")

  if [ -f "$filepath" ] && [ "$FORCE" = "0" ]; then
    SKIPPED=$((SKIPPED + 1))
    continue
  fi

  # YAML-escape cells
  desc_yaml=$(yaml_escape "$thm")
  outcome_yaml=$(yaml_escape "$out")
  date_yaml=$(yaml_escape "$dat")
  origin_yaml=$(yaml_escape "$org")

  # Default origin if empty / em-dash
  if [ -z "$origin_yaml" ] || [ "$origin_yaml" = "—" ]; then
    origin_yaml="—"
  fi

  # Compose file content
  content="---
name: session_${sid//-/_}
description: \"${desc_yaml}\"
type: session
session_id: ${sid}
lane: ${lane}
date: ${date_yaml}
origin_session_id: ${origin_yaml}
outcome: \"${outcome_yaml}\"
---

# Session ${sid}

## Theme

${thm}

## Outcome

${out}

## Cross-references

- Migrated from canonical \`SESSION_INDEX.md\` row by \`scripts/migrate-session-index-to-files.sh\` (Issue #330 Phase F.B cutover).
"

  if [ "$DRY_RUN" = "1" ]; then
    printf -- '--- DRY-RUN would write %s ---\n%s\n' "$filepath" "$content"
  else
    printf '%s' "$content" > "$filepath" || die "write failed: $filepath"
    WRITTEN=$((WRITTEN + 1))
  fi
done < <(parse_session_index_rows "$SESSION_INDEX_FILE" 2> "$STDERR_LOG")

# Surface awk WARN messages to operator + count post-hoc.
WARNS=0
if [ -s "$STDERR_LOG" ]; then
  cat "$STDERR_LOG" >&2
  WARNS=$(grep -c '^WARN:' "$STDERR_LOG" 2>/dev/null || true)
  WARNS=${WARNS:-0}
fi

printf 'migrate-session-index: %d written, %d skipped (existing), %d corruption WARN\n' \
  "$WRITTEN" "$SKIPPED" "$WARNS" >&2

exit 0
