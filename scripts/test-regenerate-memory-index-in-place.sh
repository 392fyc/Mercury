#!/usr/bin/env bash
# scripts/test-regenerate-memory-index-in-place.sh — Test harness for --in-place
# mode of scripts/regenerate-memory-index.sh (Issue #330, Phase F.B cutover).
#
# Synthetic memory dir fixtures + assertion helpers. Tests do NOT touch real
# user-memory layer.
#
# Usage:
#   scripts/test-regenerate-memory-index-in-place.sh [--verbose]
#
# Exit codes:
#   0  all tests pass
#   1  one or more tests fail

set -u

VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { printf 'test: must run inside a git repo\n' >&2; exit 1; }
SCRIPT="$REPO_ROOT/scripts/regenerate-memory-index.sh"
[ -x "$SCRIPT" ] || { printf 'test: %s not executable\n' "$SCRIPT" >&2; exit 1; }

# Portable helpers (Argus iter 2 fix 可移植性): GNU and BSD sed differ in -i
# semantics; sha256sum is GNU-only (macOS/BSD use shasum -a 256). Use these
# helpers instead of direct invocations so the test suite runs cleanly on
# Linux + macOS + Windows MSYS2 alike.
sed_inplace() {
  # Usage: sed_inplace <expression> <file>
  local expr="$1"; local file="$2"
  local tmp; tmp=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX") || return 1
  sed "$expr" "$file" > "$tmp" && mv "$tmp" "$file"
}
sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    printf 'test: neither sha256sum nor shasum available\n' >&2
    return 1
  fi
}

PASS=0
FAIL=0
CASES=0

TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/regen-memidx-test.XXXXXX") || { printf 'test: mktemp failed\n' >&2; exit 1; }
trap 'rm -rf "$TEST_ROOT"' EXIT

mk_memdir() {
  local name="$1"
  local dir="$TEST_ROOT/$name"
  mkdir -p "$dir/sessions"
  printf '%s' "$dir"
}

write_session_index() {
  local mdir="$1"; shift
  cat > "$mdir/SESSION_INDEX.md" <<EOF
---
name: SESSION_INDEX
description: test fixture
type: project
---
# Mercury Session Index

| Session | 日期 | 任务主题 | 关键产出 | originSessionId |
|---------|------|----------|----------|-----------------|
$@

> Footer text after the table — must survive splice unchanged.
EOF
}

write_memory_md() {
  local mdir="$1"; shift
  cat > "$mdir/MEMORY.md" <<EOF
# Memory Index

## User
- preserved-user-bullet

## Project (Active)
- preserved-active-bullet

## Project (Session History)
$@

## Reference
- preserved-reference-bullet
EOF
}

write_session_file() {
  # $1 = memory dir, $2 = filename (e.g. S1.md), $3 = description, $4 = outcome
  # $5 = lane (default main), $6 = date (default 2026-01-01), $7 = sid (default derived from filename)
  local mdir="$1" fname="$2" desc="$3" out="$4"
  local lane="${5:-main}" date="${6:-2026-01-01}"
  local sid="${7:-${fname%.md}}"
  cat > "$mdir/sessions/$fname" <<EOF
---
name: session_${sid//-/_}
description: "${desc}"
type: session
session_id: ${sid}
lane: ${lane}
date: ${date}
origin_session_id: —
outcome: "${out}"
---

# Session ${sid}
EOF
}

run_case() {
  local name="$1"; shift
  CASES=$((CASES + 1))
  local rc tmpout tmperr
  tmpout=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX"); tmperr=$(mktemp "${TMPDIR:-/tmp}/regen-memidx.XXXXXX")
  if "$@" >"$tmpout" 2>"$tmperr"; then rc=0; else rc=$?; fi
  LAST_OUT=$(cat "$tmpout"); LAST_ERR=$(cat "$tmperr"); LAST_RC=$rc
  rm -f "$tmpout" "$tmperr"
  if [ "$VERBOSE" -eq 1 ]; then
    printf 'CASE %s: rc=%d\n' "$name" "$rc"
    [ -n "$LAST_OUT" ] && printf '  stdout: %s\n' "$LAST_OUT" | head -3
    [ -n "$LAST_ERR" ] && printf '  stderr: %s\n' "$LAST_ERR" | head -3
  fi
}

assert_rc() {
  local name="$1"; local expected="$2"
  if [ "$LAST_RC" -eq "$expected" ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL: %s (expected rc=%d, got %d)\n' "$name" "$expected" "$LAST_RC" >&2
    [ -n "$LAST_ERR" ] && printf '  stderr: %s\n' "$LAST_ERR" >&2 | head -5
  fi
}

assert_file_contains() {
  # Argus iter 4 fix: only run_case increments CASES; asserts increment only
  # PASS/FAIL. Previous version double-counted CASES (one logical case showed
  # 2 in the summary).
  local name="$1"; local file="$2"; local needle="$3"
  if grep -qF -- "$needle" "$file"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL: %s (file %s missing: %s)\n' "$name" "$file" "$needle" >&2
  fi
}

assert_file_NOT_contains() {
  local name="$1"; local file="$2"; local needle="$3"
  if grep -qF -- "$needle" "$file"; then
    FAIL=$((FAIL + 1))
    printf 'FAIL: %s (file %s unexpectedly contains: %s)\n' "$name" "$file" "$needle" >&2
  else
    PASS=$((PASS + 1))
  fi
}

assert_err_contains() {
  local name="$1"; local needle="$2"
  if printf '%s' "$LAST_ERR" | grep -qF -- "$needle"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL: %s (stderr missing: %s)\n' "$name" "$needle" >&2
  fi
}

# Frozen timestamp for byte-identical idempotency
export MERCURY_REGEN_TIMESTAMP="2026-04-26T00:00:00Z"

# --- Test 1: --in-place rejects --output ---
M1=$(mk_memdir "in-place-rejects-output")
write_session_index "$M1" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M1" '- placeholder'
run_case "in_place_with_output" bash "$SCRIPT" --memory-dir "$M1" --in-place --output /tmp/somefile
assert_rc "in_place_with_output_rc" 2
assert_err_contains "in_place_with_output_msg" "mutually exclusive with --output"

# --- Test 2: --in-place rejects --format diff ---
M2=$(mk_memdir "in-place-rejects-diff")
write_session_index "$M2" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M2" '- placeholder'
run_case "in_place_with_diff" bash "$SCRIPT" --memory-dir "$M2" --in-place --format diff
assert_rc "in_place_with_diff_rc" 2
assert_err_contains "in_place_with_diff_msg" "mutually exclusive with --format diff"

# --- Test 3: --in-place first run inserts BEGIN/END markers in SESSION_INDEX.md ---
M3=$(mk_memdir "first-run-markers-session-index")
write_session_index "$M3" '| S1 | 2026-01-01 | original-theme | original-outcome | — |'
write_memory_md "$M3" '- [project_session1_state.md](project_session1_state.md) — S1 original'
write_session_file "$M3" "S1.md" "new-theme" "new-outcome"
run_case "first_run_session_index" bash "$SCRIPT" --memory-dir "$M3" --in-place
assert_rc "first_run_session_index_rc" 0
assert_file_contains "first_run_session_index_begin_marker" "$M3/SESSION_INDEX.md" "BEGIN: scripts/regenerate-memory-index.sh --in-place"
assert_file_contains "first_run_session_index_end_marker"   "$M3/SESSION_INDEX.md" "END: scripts/regenerate-memory-index.sh --in-place"
# Per-session file content overrides SESSION_INDEX original
assert_file_contains "first_run_session_index_new_theme"    "$M3/SESSION_INDEX.md" "new-theme"
assert_file_NOT_contains "first_run_session_index_old_theme" "$M3/SESSION_INDEX.md" "original-theme"

# --- Test 4: --in-place first run inserts markers in MEMORY.md ---
assert_file_contains "first_run_memory_begin_marker" "$M3/MEMORY.md" "BEGIN: scripts/regenerate-memory-index.sh --in-place"
assert_file_contains "first_run_memory_end_marker"   "$M3/MEMORY.md" "END: scripts/regenerate-memory-index.sh --in-place"
# Bullet uses per-session file path
assert_file_contains "first_run_memory_bullet"       "$M3/MEMORY.md" "[sessions/S1.md](sessions/S1.md) — new-theme"
# Original content gone
assert_file_NOT_contains "first_run_memory_old_bullet" "$M3/MEMORY.md" "S1 original"

# --- Test 5: --in-place preserves SESSION_INDEX header + footer + table header rows ---
assert_file_contains "first_run_preserves_table_header" "$M3/SESSION_INDEX.md" "| Session | 日期 | 任务主题 | 关键产出 | originSessionId |"
assert_file_contains "first_run_preserves_separator"    "$M3/SESSION_INDEX.md" "|---------|------|----------|----------|-----------------|"
assert_file_contains "first_run_preserves_footer"       "$M3/SESSION_INDEX.md" "Footer text after the table"
assert_file_contains "first_run_preserves_md_title"     "$M3/SESSION_INDEX.md" "# Mercury Session Index"

# --- Test 6: --in-place preserves MEMORY.md sibling subsections ---
assert_file_contains "first_run_preserves_user_section"      "$M3/MEMORY.md" "- preserved-user-bullet"
assert_file_contains "first_run_preserves_active_section"    "$M3/MEMORY.md" "- preserved-active-bullet"
assert_file_contains "first_run_preserves_reference_section" "$M3/MEMORY.md" "- preserved-reference-bullet"
assert_file_contains "first_run_preserves_history_heading"   "$M3/MEMORY.md" "## Project (Session History)"

# --- Test 7: --in-place creates pre-cutover backup files ---
assert_file_contains "backup_session_index_exists" "$M3/SESSION_INDEX.md.pre-cutover.bak" "original-theme"
assert_file_contains "backup_memory_exists"        "$M3/MEMORY.md.pre-cutover.bak"        "S1 original"

# --- Test 8: --in-place idempotent (run twice, byte-identical) ---
M8=$(mk_memdir "idempotent")
write_session_index "$M8" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M8" '- placeholder'
write_session_file "$M8" "S1.md" "theme1" "outcome1"
bash "$SCRIPT" --memory-dir "$M8" --in-place >/dev/null 2>&1
SHA1_INDEX=$(sha256_of "$M8/SESSION_INDEX.md")
SHA1_MEMORY=$(sha256_of "$M8/MEMORY.md")
bash "$SCRIPT" --memory-dir "$M8" --in-place >/dev/null 2>&1
SHA2_INDEX=$(sha256_of "$M8/SESSION_INDEX.md")
SHA2_MEMORY=$(sha256_of "$M8/MEMORY.md")
CASES=$((CASES + 1))
if [ "$SHA1_INDEX" = "$SHA2_INDEX" ] && [ "$SHA1_MEMORY" = "$SHA2_MEMORY" ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  printf 'FAIL: idempotent (run 2 differs from run 1)\n' >&2
fi

# --- Test 9: subsequent --in-place runs replace marker content ---
M9=$(mk_memdir "subsequent-run-replace")
write_session_index "$M9" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M9" '- placeholder'
write_session_file "$M9" "S1.md" "first-theme" "first-out"
bash "$SCRIPT" --memory-dir "$M9" --in-place >/dev/null 2>&1
# Verify first run content
assert_file_contains "subsequent_first_theme_present" "$M9/SESSION_INDEX.md" "first-theme"
# Update per-session file with new content
write_session_file "$M9" "S1.md" "second-theme" "second-out"
# Run again — should replace, not append
bash "$SCRIPT" --memory-dir "$M9" --in-place >/dev/null 2>&1
assert_file_contains "subsequent_second_theme_present" "$M9/SESSION_INDEX.md" "second-theme"
assert_file_NOT_contains "subsequent_first_theme_gone" "$M9/SESSION_INDEX.md" "first-theme"
# Verify NO marker duplication
BEGIN_COUNT=$(grep -c 'BEGIN: scripts/regenerate-memory-index.sh --in-place' "$M9/SESSION_INDEX.md")
CASES=$((CASES + 1))
if [ "$BEGIN_COUNT" -eq 1 ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  printf 'FAIL: subsequent_no_marker_dup — expected 1 BEGIN marker, got %d\n' "$BEGIN_COUNT" >&2
fi

# --- Test 10: bullet falls back to plain S<id> form when no per-session file ---
M10=$(mk_memdir "bullet-fallback")
write_session_index "$M10" '| S1 | 2026-01-01 | from-index-only | from-index-out | — |'
write_memory_md "$M10" '- placeholder'
# No per-session file written
bash "$SCRIPT" --memory-dir "$M10" --in-place >/dev/null 2>&1
assert_file_contains "bullet_plain_form" "$M10/MEMORY.md" "- S1 — from-index-only"
assert_file_NOT_contains "bullet_no_link" "$M10/MEMORY.md" "[sessions/S1.md]"

# --- Test 11: pipe escape in description and outcome cells ---
M11=$(mk_memdir "pipe-escape")
write_session_index "$M11" '| S1 | 2026-01-01 | placeholder | placeholder | — |'
write_memory_md "$M11" '- placeholder'
write_session_file "$M11" "S1.md" "theme with | pipe | embedded" "outcome with || double"
bash "$SCRIPT" --memory-dir "$M11" --in-place >/dev/null 2>&1
# Single pipe escaped in table row (description column)
assert_file_contains "pipe_escape_single" "$M11/SESSION_INDEX.md" 'theme with \| pipe \| embedded'
# Double pipe escaped to \|\|
assert_file_contains "pipe_escape_double" "$M11/SESSION_INDEX.md" 'outcome with \|\| double'

# --- Test 12: descending sort in MEMORY.md bullets ---
M12=$(mk_memdir "memory-descending")
write_session_index "$M12" '| S1 | 2026-01-01 | t1 | o1 | — |
| S2 | 2026-01-02 | t2 | o2 | — |
| S3 | 2026-01-03 | t3 | o3 | — |'
write_memory_md "$M12" '- placeholder'
write_session_file "$M12" "S1.md" "theme-1" "out-1"
write_session_file "$M12" "S2.md" "theme-2" "out-2"
write_session_file "$M12" "S3.md" "theme-3" "out-3"
bash "$SCRIPT" --memory-dir "$M12" --in-place >/dev/null 2>&1
# Extract bullets in order, expect S3 before S2 before S1 (descending)
ORDER=$(awk '
  /^<!-- BEGIN: scripts\/regenerate-memory-index.sh --in-place/ { in_marker = 1; next }
  /^<!-- END: scripts\/regenerate-memory-index.sh --in-place/ { in_marker = 0; next }
  in_marker && /^- \[sessions\/S/ {
    match($0, /sessions\/S[0-9]+\.md/)
    if (RLENGTH > 0) printf "%s ", substr($0, RSTART+9, RLENGTH-12)
  }
' "$M12/MEMORY.md")
CASES=$((CASES + 1))
if [ "$ORDER" = "S3 S2 S1 " ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  printf 'FAIL: memory_descending_order — expected "S3 S2 S1 ", got "%s"\n' "$ORDER" >&2
fi

# --- Test 13: ascending sort in SESSION_INDEX.md table preserved ---
ORDER=$(awk -F'|' '
  /^<!-- BEGIN: scripts\/regenerate-memory-index.sh --in-place/ { in_marker = 1; next }
  /^<!-- END: scripts\/regenerate-memory-index.sh --in-place/ { in_marker = 0; next }
  in_marker && /^\| S[0-9]/ {
    sid = $2; gsub(/^[[:space:]]+|[[:space:]]+$/, "", sid)
    printf "%s ", sid
  }
' "$M12/SESSION_INDEX.md")
CASES=$((CASES + 1))
if [ "$ORDER" = "S1 S2 S3 " ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  printf 'FAIL: session_index_ascending_order — expected "S1 S2 S3 ", got "%s"\n' "$ORDER" >&2
fi

# --- Test 14: per-session file with embedded backticks/quotes preserved ---
M14=$(mk_memdir "hostile-content")
write_session_index "$M14" '| S1 | 2026-01-01 | placeholder | placeholder | — |'
write_memory_md "$M14" '- placeholder'
write_session_file "$M14" "S1.md" "desc with backticks code-span" "outcome with quotes preserved"
bash "$SCRIPT" --memory-dir "$M14" --in-place >/dev/null 2>&1
assert_file_contains "hostile_desc_kept" "$M14/SESSION_INDEX.md" 'desc with backticks code-span'

# --- Test 15: backup not overwritten on second --in-place run ---
M15=$(mk_memdir "backup-once")
write_session_index "$M15" '| S1 | 2026-01-01 | first-state | first-out | — |'
write_memory_md "$M15" '- first-bullet'
write_session_file "$M15" "S1.md" "new-theme" "new-out"
bash "$SCRIPT" --memory-dir "$M15" --in-place >/dev/null 2>&1
# First run created backup with first-state
assert_file_contains "backup_initial_state" "$M15/SESSION_INDEX.md.pre-cutover.bak" "first-state"
# Run again — backup should NOT be overwritten with already-mutated content
bash "$SCRIPT" --memory-dir "$M15" --in-place >/dev/null 2>&1
assert_file_contains "backup_still_initial_state"  "$M15/SESSION_INDEX.md.pre-cutover.bak" "first-state"
assert_file_NOT_contains "backup_no_mutated_state" "$M15/SESSION_INDEX.md.pre-cutover.bak" "BEGIN: scripts/regenerate-memory-index.sh"

# --- Test 16: H1 — silent no-op rejection when SESSION_INDEX lacks table header ---
M16=$(mk_memdir "no-table-header")
cat > "$M16/SESSION_INDEX.md" <<'EOF'
# Mercury Session Index
(no table here at all)
EOF
write_memory_md "$M16" '- placeholder'
write_session_file "$M16" "S1.md" "theme1" "out1"
run_case "no_table_header_rejected" bash "$SCRIPT" --memory-dir "$M16" --in-place
assert_rc "no_table_header_rejected_rc" 2
assert_err_contains "no_table_header_msg" "splice verification failed"

# --- Test 17: H1 — silent no-op rejection when MEMORY lacks history heading ---
M17=$(mk_memdir "no-history-heading")
write_session_index "$M17" '| S1 | 2026-01-01 | t | o | — |'
cat > "$M17/MEMORY.md" <<'EOF'
# Memory Index

## Reference
- placeholder
EOF
write_session_file "$M17" "S1.md" "theme1" "out1"
run_case "no_history_heading_rejected" bash "$SCRIPT" --memory-dir "$M17" --in-place
assert_rc "no_history_heading_rejected_rc" 2
assert_err_contains "no_history_heading_msg" "splice verification failed"

# --- Test 18: H2 — frontmatter session_id mismatching filename rejected ---
M18=$(mk_memdir "sid-filename-mismatch")
write_session_index "$M18" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M18" '- placeholder'
mkdir -p "$M18/sessions"
cat > "$M18/sessions/S1.md" <<'EOF'
---
name: session_S99
description: mismatched-sid
type: session
session_id: S99
lane: main
date: 2026-01-01
origin_session_id: —
outcome: ok
---
# Body
EOF
run_case "sid_filename_mismatch_rejected" bash "$SCRIPT" --memory-dir "$M18" --in-place
assert_rc "sid_filename_mismatch_rc" 1
assert_err_contains "sid_filename_mismatch_msg" "session_id/filename mismatch"

# --- Test 19: H2 — invalid session_id format rejected ---
M19=$(mk_memdir "invalid-sid-format")
write_session_index "$M19" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M19" '- placeholder'
mkdir -p "$M19/sessions"
# Filename matches `S99-bogus.md` but session_id frontmatter says arbitrary string
cat > "$M19/sessions/S99-bogus.md" <<'EOF'
---
name: session_evil
description: bad
type: session
session_id: arbitrary string
lane: main
date: 2026-01-01
origin_session_id: —
outcome: ok
---
EOF
run_case "invalid_sid_format_rejected" bash "$SCRIPT" --memory-dir "$M19" --in-place
assert_rc "invalid_sid_format_rc" 1
assert_err_contains "invalid_sid_format_msg" "invalid session_id"

# --- Test 20: H2 — non-canonical filename pattern skipped (not an error) ---
M20=$(mk_memdir "non-canonical-filename")
write_session_index "$M20" '| S1 | 2026-01-01 | from-index | from-index-out | — |'
write_memory_md "$M20" '- placeholder'
mkdir -p "$M20/sessions"
# `S99extra.md` — no hyphen separator before any suffix
cat > "$M20/sessions/S99extra.md" <<'EOF'
---
name: bogus
session_id: S99
date: 2026-01-01
description: should-be-skipped
outcome: ok
---
EOF
# `S5-Upper.md` — uppercase in lane suffix
cat > "$M20/sessions/S5-Upper.md" <<'EOF'
---
name: bogus2
session_id: S5
date: 2026-01-01
description: also-skipped
outcome: ok
---
EOF
run_case "non_canonical_filename_skipped" bash "$SCRIPT" --memory-dir "$M20" --in-place
assert_rc "non_canonical_filename_rc" 0
assert_err_contains "non_canonical_filename_warn1" "skip non-canonical session filename: S99extra.md"
assert_err_contains "non_canonical_filename_warn2" "skip non-canonical session filename: S5-Upper.md"
# SESSION_INDEX row falls through; from-index value present
assert_file_contains "non_canonical_fallback_to_index" "$M20/SESSION_INDEX.md" "from-index"

# --- Test 21: Argus iter1 fix — unbounded session number digits (S1234+) ---
M21=$(mk_memdir "unbounded-digits")
write_session_index "$M21" '| S1234 | 2026-01-01 | t | o | — |'
write_memory_md "$M21" '- placeholder'
write_session_file "$M21" "S1234.md" "large-num-theme" "large-num-out" "main" "2026-01-01" "S1234"
run_case "unbounded_digits" bash "$SCRIPT" --memory-dir "$M21" --in-place
assert_rc "unbounded_digits_rc" 0
assert_file_contains "unbounded_digits_row" "$M21/SESSION_INDEX.md" "| S1234 | 2026-01-01 | large-num-theme |"

# --- Test 22: Argus iter1 fix — BEGIN/END marker asymmetry detection ---
M22=$(mk_memdir "asymmetric-markers")
write_session_index "$M22" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M22" '- placeholder'
write_session_file "$M22" "S1.md" "theme1" "out1"
# First valid run to populate markers
bash "$SCRIPT" --memory-dir "$M22" --in-place >/dev/null 2>&1
# Manually corrupt: delete END marker line (portable — sed -i differs GNU vs BSD)
sed_inplace '/^<!-- END: scripts\/regenerate-memory-index.sh --in-place/d' "$M22/SESSION_INDEX.md"
# Re-run should detect asymmetry and fail
run_case "asymmetric_markers_detected" bash "$SCRIPT" --memory-dir "$M22" --in-place
# Note: subsequent-run path replaces marker content; if BEGIN exists but END is missing,
# the subsequent-run awk reaches EOF with state="in_marker" and never closes. Output then
# has BEGIN-with-content but no END — verification step catches it.
assert_rc "asymmetric_markers_rc" 2
assert_err_contains "asymmetric_markers_msg" "splice verification failed"

# --- Final report ---
printf '\n=== regenerate-memory-index --in-place test summary ===\n'
printf 'cases: %d  assertions: %d  fail: %d\n' "$CASES" "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
