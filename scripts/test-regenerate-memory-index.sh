#!/usr/bin/env bash
# scripts/test-regenerate-memory-index.sh — Test harness for
# scripts/regenerate-memory-index.sh (Issue #329, Phase F.A).
#
# Synthetic memory dir fixtures + assertion helpers. Tests do NOT touch real
# user-memory layer.
#
# Usage:
#   scripts/test-regenerate-memory-index.sh [--verbose]
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

PASS=0
FAIL=0
CASES=0

# Workdir per test, under unique temp root
TEST_ROOT=$(mktemp -d -t mercury-regen-test-XXXXXX) || { printf 'test: mktemp failed\n' >&2; exit 1; }
trap 'rm -rf "$TEST_ROOT"' EXIT

mk_memdir() {
  local name="$1"
  local dir="$TEST_ROOT/$name"
  mkdir -p "$dir"
  printf '%s' "$dir"
}

write_session_index() {
  # $1 = memory dir, $2 = body content (table rows after header)
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
EOF
}

write_memory_md() {
  # $1 = memory dir, $2 = "Project (Session History)" body (bullets only, no header)
  local mdir="$1"; shift
  cat > "$mdir/MEMORY.md" <<EOF
# Memory Index

## User
- placeholder

## Project (Active)
- placeholder

## Project (Session History)
$@

## Reference
- placeholder
EOF
}

run_case() {
  local name="$1"; shift
  CASES=$((CASES + 1))
  local out err rc tmpout tmperr
  tmpout=$(mktemp); tmperr=$(mktemp)
  if "$@" >"$tmpout" 2>"$tmperr"; then rc=0; else rc=$?; fi
  out=$(cat "$tmpout"); err=$(cat "$tmperr")
  rm -f "$tmpout" "$tmperr"
  # Stash for assertions in caller
  LAST_OUT="$out"; LAST_ERR="$err"; LAST_RC=$rc
  if [ "$VERBOSE" -eq 1 ]; then
    printf 'CASE %s: rc=%d\n' "$name" "$rc"
    [ -n "$out" ] && printf '  stdout: %s\n' "$out" | head -3
    [ -n "$err" ] && printf '  stderr: %s\n' "$err" | head -3
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

assert_out_contains() {
  local name="$1"; local needle="$2"
  if printf '%s' "$LAST_OUT" | grep -qF -- "$needle"; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL: %s (stdout missing: %s)\n' "$name" "$needle" >&2
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

assert_out_NOT_contains() {
  local name="$1"; local needle="$2"
  if printf '%s' "$LAST_OUT" | grep -qF -- "$needle"; then
    FAIL=$((FAIL + 1))
    printf 'FAIL: %s (stdout unexpectedly contains: %s)\n' "$name" "$needle" >&2
  else
    PASS=$((PASS + 1))
  fi
}

# Frozen timestamp env for byte-identical idempotency tests
export MERCURY_REGEN_TIMESTAMP="2026-04-26T00:00:00Z"

# --- Test 1: --help exits 0 + emits usage ---
run_case "help_flag_exits_zero" bash "$SCRIPT" --help
assert_rc "help_flag_exits_zero" 0
assert_out_contains "help_flag_emits_usage" "Usage:"

# --- Test 2: unknown flag rejected ---
run_case "unknown_flag" bash "$SCRIPT" --bogus
assert_rc "unknown_flag" 2

# --- Test 3: --memory-dir empty rejected ---
run_case "empty_memory_dir_arg" bash "$SCRIPT" --memory-dir ""
assert_rc "empty_memory_dir_arg" 2

# --- Test 4: --memory-dir nonexistent rejected ---
run_case "missing_memory_dir" bash "$SCRIPT" --memory-dir "$TEST_ROOT/nonexistent"
assert_rc "missing_memory_dir" 2
assert_err_contains "missing_memory_dir_msg" "memory dir not found"

# --- Test 5: --format invalid rejected ---
M5=$(mk_memdir "fmt-test")
write_session_index "$M5" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M5" '- placeholder'
run_case "format_invalid" bash "$SCRIPT" --memory-dir "$M5" --format xml
assert_rc "format_invalid" 2

# --- Test 6: --output empty rejected ---
run_case "empty_output_arg" bash "$SCRIPT" --memory-dir "$M5" --output ""
assert_rc "empty_output_arg" 2

# --- Test 7: missing SESSION_INDEX.md detected ---
M7=$(mk_memdir "no-index")
write_memory_md "$M7" '- placeholder'
run_case "missing_session_index" bash "$SCRIPT" --memory-dir "$M7"
assert_rc "missing_session_index" 2
assert_err_contains "missing_session_index_msg" "SESSION_INDEX.md not found"

# --- Test 8: missing MEMORY.md detected ---
M8=$(mk_memdir "no-memory")
write_session_index "$M8" '| S1 | 2026-01-01 | t | o | — |'
run_case "missing_memory" bash "$SCRIPT" --memory-dir "$M8"
assert_rc "missing_memory" 2
assert_err_contains "missing_memory_msg" "MEMORY.md not found"

# --- Test 9: single session round-trip ---
M9=$(mk_memdir "single-session")
write_session_index "$M9" '| S1 | 2026-01-01 | sample theme | sample outcome | — |'
write_memory_md "$M9" '- [project_session1_state.md](project_session1_state.md) — S1: hello'
run_case "single_session_stdout" bash "$SCRIPT" --memory-dir "$M9" --output -
assert_rc "single_session_stdout" 0
assert_out_contains "single_session_row" "| S1 | 2026-01-01 | sample theme | sample outcome | — |"
assert_out_contains "single_session_bullet" "- [project_session1_state.md](project_session1_state.md) — S1: hello"

# --- Test 10: multiple sessions sorted ascending ---
M10=$(mk_memdir "multi-sessions")
write_session_index "$M10" '| S5 | 2026-01-05 | t5 | o5 | — |
| S1 | 2026-01-01 | t1 | o1 | — |
| S10 | 2026-01-10 | t10 | o10 | — |
| S2 | 2026-01-02 | t2 | o2 | — |'
write_memory_md "$M10" '- placeholder'
run_case "multi_sorted" bash "$SCRIPT" --memory-dir "$M10" --output -
assert_rc "multi_sorted" 0
# Verify order: extract session column, expect S1 S2 S5 S10
ORDER=$(printf '%s\n' "$LAST_OUT" | awk -F'|' '/^\| S[0-9]/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); printf "%s ", $2 }')
if [ "$ORDER" = "S1 S2 S5 S10 " ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  printf 'FAIL: multi_sorted_order — expected "S1 S2 S5 S10 ", got "%s"\n' "$ORDER" >&2
fi

# --- Test 11: lane suffix variants — main first within same number ---
M11=$(mk_memdir "lane-suffixes")
write_session_index "$M11" '| S2-side-multi-lane | 2026-01-02 | side-t2 | side-o2 | — |
| S1 | 2026-01-01 | main-t1 | main-o1 | — |
| S1-side-foo | 2026-01-01 | foo-t1 | foo-o1 | — |
| S2 | 2026-01-02 | main-t2 | main-o2 | — |'
write_memory_md "$M11" '- placeholder'
run_case "lane_suffix_order" bash "$SCRIPT" --memory-dir "$M11" --output -
assert_rc "lane_suffix_order" 0
ORDER=$(printf '%s\n' "$LAST_OUT" | awk -F'|' '/^\| S[0-9]/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); printf "%s ", $2 }')
if [ "$ORDER" = "S1 S1-side-foo S2 S2-side-multi-lane " ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  printf 'FAIL: lane_suffix_order — expected "S1 S1-side-foo S2 S2-side-multi-lane ", got "%s"\n' "$ORDER" >&2
fi

# --- Test 12: en-dash range row (S35–S46) sorted by lower bound ---
M12=$(mk_memdir "range-row")
write_session_index "$M12" '| S50 | 2026-01-50 | t50 | o50 | — |
| S35–S46 | 2026-01-35 | range | bulk | — |
| S30 | 2026-01-30 | t30 | o30 | — |'
write_memory_md "$M12" '- placeholder'
run_case "range_row" bash "$SCRIPT" --memory-dir "$M12" --output -
assert_rc "range_row" 0
ORDER=$(printf '%s\n' "$LAST_OUT" | awk -F'|' '/^\| S[0-9]/ { gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2); printf "%s ", $2 }')
if [ "$ORDER" = "S30 S35–S46 S50 " ]; then
  PASS=$((PASS + 1))
else
  FAIL=$((FAIL + 1))
  printf 'FAIL: range_row_order — expected "S30 S35–S46 S50 ", got "%s"\n' "$ORDER" >&2
fi

# --- Test 13: per-session file overrides SESSION_INDEX row ---
M13=$(mk_memdir "per-session-override")
write_session_index "$M13" '| S1 | 2026-01-01 | OLD-theme | OLD-outcome | OLD-origin |'
write_memory_md "$M13" '- placeholder'
mkdir -p "$M13/sessions"
cat > "$M13/sessions/S1.md" <<'EOF'
---
name: session_S1_main
description: NEW-theme
type: session
session_id: S1
lane: main
date: 2026-02-02
origin_session_id: NEW-origin
outcome: NEW-outcome
---

# Session S1 — body
EOF
run_case "per_session_override" bash "$SCRIPT" --memory-dir "$M13" --output -
assert_rc "per_session_override" 0
assert_out_contains "override_new_theme" "NEW-theme"
assert_out_contains "override_new_outcome" "NEW-outcome"
assert_out_contains "override_new_origin" "NEW-origin"
assert_out_contains "override_new_date" "2026-02-02"
assert_out_NOT_contains "override_old_theme_gone" "OLD-theme"

# --- Test 14: per-session file with malformed frontmatter (missing close) ---
M14=$(mk_memdir "malformed-frontmatter")
write_session_index "$M14" '| S2 | 2026-01-02 | fallback | fallback | — |'
write_memory_md "$M14" '- placeholder'
mkdir -p "$M14/sessions"
cat > "$M14/sessions/S2.md" <<'EOF'
---
name: session_S2
description: missing-close
type: session
session_id: S2
date: 2026-02-02
outcome: x

# Body but no frontmatter close
EOF
run_case "malformed_frontmatter" bash "$SCRIPT" --memory-dir "$M14" --output -
assert_rc "malformed_frontmatter" 1
assert_err_contains "malformed_frontmatter_msg" "frontmatter not closed"

# --- Test 15: per-session file missing required field (description) ---
M15=$(mk_memdir "missing-required")
write_session_index "$M15" '| S3 | 2026-01-03 | fb | fb | — |'
write_memory_md "$M15" '- placeholder'
mkdir -p "$M15/sessions"
cat > "$M15/sessions/S3.md" <<'EOF'
---
name: session_S3
type: session
session_id: S3
date: 2026-01-03
outcome: x
---

# Body
EOF
run_case "missing_description" bash "$SCRIPT" --memory-dir "$M15" --output -
assert_rc "missing_description" 1
assert_err_contains "missing_description_msg" "description missing"

# --- Test 16: idempotency — 2 runs with frozen timestamp produce byte-identical output ---
M16=$(mk_memdir "idempotent")
write_session_index "$M16" '| S1 | 2026-01-01 | t1 | o1 | — |
| S2 | 2026-01-02 | t2 | o2 | — |'
write_memory_md "$M16" '- [project_session1_state.md](project_session1_state.md) — S1
- [project_session2_state.md](project_session2_state.md) — S2'
RUN1=$(bash "$SCRIPT" --memory-dir "$M16" --output -)
RUN2=$(bash "$SCRIPT" --memory-dir "$M16" --output -)
if [ "$RUN1" = "$RUN2" ]; then
  CASES=$((CASES + 1)); PASS=$((PASS + 1))
else
  CASES=$((CASES + 1)); FAIL=$((FAIL + 1))
  printf 'FAIL: idempotency — runs differ\n' >&2
  diff <(printf '%s' "$RUN1") <(printf '%s' "$RUN2") >&2 | head -20
fi

# --- Test 17: --output to custom path writes file ---
M17=$(mk_memdir "custom-output")
write_session_index "$M17" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M17" '- placeholder'
OUTPATH="$TEST_ROOT/custom-output/myfile.md"
run_case "custom_output_path" bash "$SCRIPT" --memory-dir "$M17" --output "$OUTPATH"
assert_rc "custom_output_path" 0
if [ -f "$OUTPATH" ]; then
  CASES=$((CASES + 1)); PASS=$((PASS + 1))
else
  CASES=$((CASES + 1)); FAIL=$((FAIL + 1))
  printf 'FAIL: custom_output_file_exists — %s missing\n' "$OUTPATH" >&2
fi

# --- Test 18: default output path is <memory-dir>/INDEX.generated.md ---
M18=$(mk_memdir "default-output")
write_session_index "$M18" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M18" '- placeholder'
run_case "default_output_path" bash "$SCRIPT" --memory-dir "$M18"
assert_rc "default_output_path" 0
if [ -f "$M18/INDEX.generated.md" ]; then
  CASES=$((CASES + 1)); PASS=$((PASS + 1))
else
  CASES=$((CASES + 1)); FAIL=$((FAIL + 1))
  printf 'FAIL: default_output_file_exists — %s/INDEX.generated.md missing\n' "$M18" >&2
fi

# --- Test 19: MEMORY.md plain bullet (no link) preserved ---
M19=$(mk_memdir "plain-bullet")
write_session_index "$M19" '| S62 | 2026-04-19 | sprint | x | — |'
write_memory_md "$M19" '- S62 状态见 [session-handoff.md](session-handoff.md) — PR#271/#275 merged
- [project_session1_state.md](project_session1_state.md) — S1 link form'
run_case "plain_bullet" bash "$SCRIPT" --memory-dir "$M19" --output -
assert_rc "plain_bullet" 0
# Plain row preserved verbatim (with leading "- " re-added)
assert_out_contains "plain_bullet_preserved" "- S62 状态见 [session-handoff.md](session-handoff.md) — PR#271/#275 merged"
assert_out_contains "link_bullet_preserved" "- [project_session1_state.md](project_session1_state.md) — S1 link form"

# --- Test 20: MERCURY_MEMORY_DIR env resolves default ---
M20=$(mk_memdir "env-resolve")
write_session_index "$M20" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M20" '- placeholder'
run_case "env_memory_dir" env MERCURY_MEMORY_DIR="$M20" bash "$SCRIPT" --output -
assert_rc "env_memory_dir" 0
assert_out_contains "env_memory_dir_used" "| S1 |"

# --- Test 21: --output - writes to stdout (no file created) ---
M21=$(mk_memdir "stdout-output")
write_session_index "$M21" '| S1 | 2026-01-01 | stdout-test | o | — |'
write_memory_md "$M21" '- placeholder'
run_case "stdout_output" bash "$SCRIPT" --memory-dir "$M21" --output -
assert_rc "stdout_output" 0
assert_out_contains "stdout_has_content" "stdout-test"
if [ ! -f "$M21/INDEX.generated.md" ]; then
  CASES=$((CASES + 1)); PASS=$((PASS + 1))
else
  CASES=$((CASES + 1)); FAIL=$((FAIL + 1))
  printf 'FAIL: stdout_no_file_created — INDEX.generated.md unexpectedly created\n' >&2
fi

# --- Test 22: diff mode — no existing INDEX.generated.md → exit 1 ---
M22=$(mk_memdir "diff-no-existing")
write_session_index "$M22" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M22" '- placeholder'
run_case "diff_no_existing" bash "$SCRIPT" --memory-dir "$M22" --format diff
assert_rc "diff_no_existing" 1
assert_err_contains "diff_no_existing_msg" "no existing INDEX.generated.md"

# --- Test 23: diff mode — no drift (existing matches fresh) → exit 0 ---
M23=$(mk_memdir "diff-no-drift")
write_session_index "$M23" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M23" '- placeholder'
bash "$SCRIPT" --memory-dir "$M23" >/dev/null 2>&1  # generate
run_case "diff_no_drift" bash "$SCRIPT" --memory-dir "$M23" --format diff
assert_rc "diff_no_drift" 0
assert_out_contains "diff_no_drift_msg" "no drift"

# --- Test 24: diff mode — drift detected (SESSION_INDEX changed since generate) → exit 1 ---
M24=$(mk_memdir "diff-drift")
write_session_index "$M24" '| S1 | 2026-01-01 | original | o | — |'
write_memory_md "$M24" '- placeholder'
bash "$SCRIPT" --memory-dir "$M24" >/dev/null 2>&1  # snapshot generate
write_session_index "$M24" '| S1 | 2026-01-01 | CHANGED | o | — |'  # mutate source
run_case "diff_drift" bash "$SCRIPT" --memory-dir "$M24" --format diff
assert_rc "diff_drift" 1
assert_err_contains "diff_drift_msg" "DRIFT detected"

# --- Test 25: hostile session id with backticks/special chars in description preserved ---
M25=$(mk_memdir "hostile-desc")
write_session_index "$M25" '| S99 | 2026-12-31 | desc with `backticks` and "quotes" | outcome with $vars | — |'
write_memory_md "$M25" '- placeholder'
run_case "hostile_desc" bash "$SCRIPT" --memory-dir "$M25" --output -
assert_rc "hostile_desc" 0
assert_out_contains "hostile_desc_preserved" 'desc with `backticks` and "quotes"'

# --- Test 26: empty SESSION_INDEX (header only, no rows) → empty table emitted ---
M26=$(mk_memdir "empty-rows")
cat > "$M26/SESSION_INDEX.md" <<'EOF'
# Mercury Session Index
| Session | 日期 | 任务主题 | 关键产出 | originSessionId |
|---------|------|----------|----------|-----------------|
EOF
write_memory_md "$M26" '- placeholder'
run_case "empty_rows" bash "$SCRIPT" --memory-dir "$M26" --output -
assert_rc "empty_rows" 0
assert_out_contains "empty_rows_header" "| Session | 日期 | 任务主题 | 关键产出 | originSessionId |"

# --- Test 27: per-session file with origin_session_id "—" preserved ---
M27=$(mk_memdir "origin-em-dash")
write_session_index "$M27" '| S1 | 2026-01-01 | fb | fb | — |'
write_memory_md "$M27" '- placeholder'
mkdir -p "$M27/sessions"
cat > "$M27/sessions/S1.md" <<'EOF'
---
name: session_S1
description: with-em-dash-origin
type: session
session_id: S1
date: 2026-01-01
outcome: ok
origin_session_id: —
---

# Body
EOF
run_case "origin_em_dash" bash "$SCRIPT" --memory-dir "$M27" --output -
assert_rc "origin_em_dash" 0
assert_out_contains "origin_em_dash_preserved" "| S1 | 2026-01-01 | with-em-dash-origin | ok | — |"

# --- Test 28: pipe character in description column triggers WARN + row still emitted ---
M28=$(mk_memdir "pipe-in-cell")
write_session_index "$M28" '| S1 | 2026-01-01 | desc with | embedded pipe | outcome | — |'
write_memory_md "$M28" '- placeholder'
run_case "pipe_in_cell_warn" bash "$SCRIPT" --memory-dir "$M28" --output -
assert_rc "pipe_in_cell_warn" 0
assert_err_contains "pipe_in_cell_warn_msg" "pipe-separated fields"

# --- Test 29: duplicate session_id in SESSION_INDEX deduped (Pass 2 dedup) ---
M29=$(mk_memdir "duplicate-rows")
write_session_index "$M29" '| S1 | 2026-01-01 | first | first-out | — |
| S1 | 2026-01-01 | second | second-out | — |
| S2 | 2026-01-02 | s2 | s2-out | — |'
write_memory_md "$M29" '- placeholder'
run_case "duplicate_rows" bash "$SCRIPT" --memory-dir "$M29" --output -
assert_rc "duplicate_rows" 0
# Count S1 rows in output — should be exactly 1
S1_COUNT=$(printf '%s\n' "$LAST_OUT" | awk -F'|' '/^\| S1 /{n++} END{print n+0}')
if [ "$S1_COUNT" -eq 1 ]; then
  CASES=$((CASES + 1)); PASS=$((PASS + 1))
else
  CASES=$((CASES + 1)); FAIL=$((FAIL + 1))
  printf 'FAIL: duplicate_rows_dedup — expected 1 S1 row, got %d\n' "$S1_COUNT" >&2
fi
# First occurrence wins (verify second-out NOT in output)
assert_out_contains "duplicate_rows_first_kept" "first-out"
assert_out_NOT_contains "duplicate_rows_second_dropped" "second-out"

# --- Test 30: diff mode without frozen timestamp (operator scenario) — generated_at stripped before compare ---
M30=$(mk_memdir "diff-unfrozen-timestamp")
write_session_index "$M30" '| S1 | 2026-01-01 | t | o | — |'
write_memory_md "$M30" '- placeholder'
# Generate baseline with one timestamp
MERCURY_REGEN_TIMESTAMP="2026-04-26T00:00:00Z" bash "$SCRIPT" --memory-dir "$M30" >/dev/null 2>&1
# Diff with a different timestamp — structural content unchanged → should be no drift
run_case "diff_unfrozen_timestamp" env MERCURY_REGEN_TIMESTAMP="2030-01-01T12:34:56Z" bash "$SCRIPT" --memory-dir "$M30" --format diff
assert_rc "diff_unfrozen_timestamp" 0
assert_out_contains "diff_unfrozen_timestamp_msg" "no drift"

# --- Final report ---
printf '\n=== regenerate-memory-index test summary ===\n'
printf 'cases: %d  assertions: %d  fail: %d\n' "$CASES" "$PASS" "$FAIL"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
