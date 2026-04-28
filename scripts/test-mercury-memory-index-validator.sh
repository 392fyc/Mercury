#!/usr/bin/env bash
# scripts/test-mercury-memory-index-validator.sh — Phase F.C lock-in (Issue #331).
#
# Synthetic-stdin tests for scripts/hooks/mercury-memory-index-validator.py.
# Validator is observability-only (SessionEnd cannot block); exit code is
# always 0; stderr surfaces drift warnings only when regenerate --format diff
# returns non-zero.
#
# Exit 0 = all assertions pass; 1 = any assertion fail; 2 = test harness error.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT/scripts/hooks/mercury-memory-index-validator.py"
PY="${PYTHON_BIN:-python}"

[ -f "$HOOK" ] || { printf 'test harness: hook script missing: %s\n' "$HOOK" >&2; exit 2; }
command -v "$PY" >/dev/null 2>&1 || { printf 'test harness: python missing\n' >&2; exit 2; }

PASS=0
FAIL=0
ASSERT=0

assert_eq() {
  local name="$1" want="$2" got="$3"
  ASSERT=$((ASSERT + 1))
  if [ "$want" = "$got" ]; then
    PASS=$((PASS + 1))
    return 0
  fi
  FAIL=$((FAIL + 1))
  printf 'FAIL %s\n  want=%s\n  got =%s\n' "$name" "$want" "$got" >&2
  return 1
}

assert_contains() {
  local name="$1" needle="$2" hay="$3"
  ASSERT=$((ASSERT + 1))
  case "$hay" in
    *"$needle"*) PASS=$((PASS + 1)); return 0 ;;
    *) FAIL=$((FAIL + 1)); printf 'FAIL %s\n  needle=%s\n  hay   =%s\n' "$name" "$needle" "$hay" >&2; return 1 ;;
  esac
}

# Portable mktemp wrapper (Argus iter-2 finding): bare `mktemp -d` is GNU-only;
# BSD/macOS requires an explicit template.
portable_mktemp_d() {
  local prefix="${1:-mercury-fc-test}"
  local base="${TMPDIR:-/tmp}"
  mktemp -d "$base/$prefix.XXXXXX"
}

# Stub repo where regenerate --format diff exits 0 (no drift)
make_stub_repo_clean() {
  local dir; dir="$(portable_mktemp_d val-clean)"
  mkdir -p "$dir/scripts"
  cat > "$dir/scripts/regenerate-memory-index.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
  chmod +x "$dir/scripts/regenerate-memory-index.sh"
  printf '%s' "$dir"
}

# Stub repo where regenerate --format diff exits 1 (drift)
make_stub_repo_drift() {
  local dir; dir="$(portable_mktemp_d val-drift)"
  mkdir -p "$dir/scripts"
  cat > "$dir/scripts/regenerate-memory-index.sh" <<'EOF'
#!/usr/bin/env bash
echo "drift line A on stdout"
echo "drift line B on stderr" >&2
exit 1
EOF
  chmod +x "$dir/scripts/regenerate-memory-index.sh"
  printf '%s' "$dir"
}

# Stub repo where the script behaves differently per first arg:
#   --format diff → exit 1 (drift)
#   --in-place    → exit 0 (autofix succeeds)
make_stub_repo_drift_then_autofix_ok() {
  local dir; dir="$(portable_mktemp_d val-fix-ok)"
  mkdir -p "$dir/scripts"
  cat > "$dir/scripts/regenerate-memory-index.sh" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  --format) [ "${2:-}" = "diff" ] && { echo "drift on stderr" >&2; exit 1; } || exit 2 ;;
  --in-place) echo "in-place autofix complete"; exit 0 ;;
  *) exit 2 ;;
esac
EOF
  chmod +x "$dir/scripts/regenerate-memory-index.sh"
  printf '%s' "$dir"
}

# Stub repo where:
#   --format diff → exit 1 (drift)
#   --in-place    → exit 1 (autofix itself fails)
make_stub_repo_drift_then_autofix_fail() {
  local dir; dir="$(portable_mktemp_d val-fix-bad)"
  mkdir -p "$dir/scripts"
  cat > "$dir/scripts/regenerate-memory-index.sh" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  --format) [ "${2:-}" = "diff" ] && exit 1 || exit 2 ;;
  --in-place) echo "autofix failed: write error" >&2; exit 1 ;;
  *) exit 2 ;;
esac
EOF
  chmod +x "$dir/scripts/regenerate-memory-index.sh"
  printf '%s' "$dir"
}

# Stub repo where regenerate --format diff exits 2 (script error, NOT drift)
make_stub_repo_script_error() {
  local dir; dir="$(portable_mktemp_d val-err)"
  mkdir -p "$dir/scripts"
  cat > "$dir/scripts/regenerate-memory-index.sh" <<'EOF'
#!/usr/bin/env bash
echo "missing memory dir or args" >&2
exit 2
EOF
  chmod +x "$dir/scripts/regenerate-memory-index.sh"
  printf '%s' "$dir"
}

# Array-based temp dir tracking (Argus iter-2 finding): space-separated string
# fails on temp paths containing spaces.
TMP_LIST=()
cleanup() {
  local d
  for d in "${TMP_LIST[@]}"; do
    [ -n "$d" ] && rm -rf "$d" 2>/dev/null || true
  done
}
trap cleanup EXIT

# ----- Test 1: clean repo (regenerate exit 0) → no warning, exit 0 -----
repo_clean="$(make_stub_repo_clean)"
TMP_LIST+=("$repo_clean")
out="$(printf '{"session_id":"abc","hook_event_name":"SessionEnd"}' | \
  env MERCURY_REPO_ROOT="$repo_clean" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T1.clean.exit-code" "0" "$ec"
assert_eq "T1.clean.no-warning" "" "$out"

# ----- Test 2: drift repo (regenerate exit 1) → warning to stderr, exit 0 -----
repo_drift="$(make_stub_repo_drift)"
TMP_LIST+=("$repo_drift")
combined="$(printf '{"session_id":"sess-2","hook_event_name":"SessionEnd"}' | \
  env MERCURY_REPO_ROOT="$repo_drift" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T2.drift.exit-code" "0" "$ec"
assert_contains "T2.drift.warning-prefix" "drift detected at session end" "$combined"
assert_contains "T2.drift.session-id-echoed" "sess-2" "$combined"
assert_contains "T2.drift.regen-exit-noted" "regen exit=1" "$combined"
assert_contains "T2.drift.fix-hint" "regenerate-memory-index.sh --in-place" "$combined"

# ----- Test 3: MERCURY_INDEX_VALIDATOR_DISABLED=1 → no warning even on drift -----
combined="$(printf '{"session_id":"sess-3","hook_event_name":"SessionEnd"}' | \
  env MERCURY_REPO_ROOT="$repo_drift" MERCURY_INDEX_VALIDATOR_DISABLED=1 "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T3.disabled.exit-code" "0" "$ec"
assert_eq "T3.disabled.no-output" "" "$combined"

# ----- Test 4: malformed stdin JSON → still runs, no crash -----
combined="$(printf '{not json' | env MERCURY_REPO_ROOT="$repo_clean" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T4.bad-json.exit-code" "0" "$ec"
assert_eq "T4.bad-json.no-output" "" "$combined"

# ----- Test 5: empty stdin → exit 0 -----
combined="$(printf '' | env MERCURY_REPO_ROOT="$repo_clean" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T5.empty-stdin.exit-code" "0" "$ec"

# ----- Test 6: missing repo root (env unset + default repo not found) → exit 0 silent -----
nonexist="$(portable_mktemp_d val-nonexist)"; rm -rf "$nonexist"
combined="$(printf '{"session_id":"x"}' | env MERCURY_REPO_ROOT="$nonexist" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T6.missing-repo.exit-code" "0" "$ec"
assert_eq "T6.missing-repo.no-output" "" "$combined"

# ----- Test 7: drift warning includes stdout/stderr tail -----
combined="$(printf '{"session_id":"sess-7"}' | \
  env MERCURY_REPO_ROOT="$repo_drift" "$PY" "$HOOK" 2>&1)"
assert_contains "T7.tail.stderr-content" "drift line B on stderr" "$combined"
assert_contains "T7.tail.stdout-content" "drift line A on stdout" "$combined"

# ----- Test 8: payload missing session_id → unknown placeholder -----
combined="$(printf '{}' | env MERCURY_REPO_ROOT="$repo_drift" "$PY" "$HOOK" 2>&1)"
assert_contains "T8.no-session_id.placeholder" "session=unknown" "$combined"

# ----- Test 9: payload top-level not a dict → unknown placeholder, no crash -----
combined="$(printf '"not-a-dict"' | env MERCURY_REPO_ROOT="$repo_drift" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T9.not-dict.exit-code" "0" "$ec"
assert_contains "T9.not-dict.placeholder" "session=unknown" "$combined"

# ----- Test 10: bash executable missing on PATH → FileNotFoundError surfaced -----
# Restrict PATH to ONLY the directory containing $PY itself (so the test can
# still launch python.exe) but stripped of any bash/git-bash/MSYS2/WSL paths.
# Without this finding being surfaced, drift check would silently fail (per
# Argus iter 1 finding 3).
py_resolved="$(command -v "$PY" 2>/dev/null || echo "")"
if [ -n "$py_resolved" ]; then
  py_dir="$(dirname "$py_resolved")"
  combined="$(printf '{"session_id":"sess-10"}' | \
    env -i MERCURY_REPO_ROOT="$repo_clean" PATH="$py_dir" SYSTEMROOT="${SYSTEMROOT:-}" "$PY" "$HOOK" 2>&1)"
  ec=$?
  assert_eq "T10.no-bash.exit-code" "0" "$ec"
  assert_contains "T10.no-bash.warning" "bash" "$combined"
  assert_contains "T10.no-bash.disable-hint" "MERCURY_INDEX_VALIDATOR_DISABLED" "$combined"
else
  printf 'SKIP T10.no-bash: cannot resolve %s on PATH\n' "$PY" >&2
fi

# ----- Test 12: MERCURY_INDEX_AUTOFIX=1 + drift → auto --in-place + confirmation -----
# Per Issue #331 Hook 2 spec: when drift detected (exit 1) AND autofix env set,
# validator runs --in-place to refresh canonical, suppresses standard drift
# warning, emits confirmation message instead.
repo_fix_ok="$(make_stub_repo_drift_then_autofix_ok)"
TMP_LIST+=("$repo_fix_ok")
combined="$(printf '{"session_id":"sess-12"}' | \
  env MERCURY_REPO_ROOT="$repo_fix_ok" MERCURY_INDEX_AUTOFIX=1 "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T12.autofix-ok.exit-code" "0" "$ec"
assert_contains "T12.autofix-ok.confirmation" "auto-fix via --in-place succeeded" "$combined"
case "$combined" in
  *"drift detected at session end (session=sess-12, regen exit=1)"*)
    FAIL=$((FAIL + 1)); ASSERT=$((ASSERT + 1))
    printf 'FAIL T12.autofix-ok.suppresses-warning\n  unwanted standard drift warning present\n' >&2 ;;
  *) PASS=$((PASS + 1)); ASSERT=$((ASSERT + 1)) ;;
esac

# ----- Test 13: MERCURY_INDEX_AUTOFIX=1 but autofix itself fails → fallback warning -----
repo_fix_bad="$(make_stub_repo_drift_then_autofix_fail)"
TMP_LIST+=("$repo_fix_bad")
combined="$(printf '{"session_id":"sess-13"}' | \
  env MERCURY_REPO_ROOT="$repo_fix_bad" MERCURY_INDEX_AUTOFIX=1 "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T13.autofix-fail.exit-code" "0" "$ec"
assert_contains "T13.autofix-fail.attempt-noted" "auto-fix exited 1" "$combined"
assert_contains "T13.autofix-fail.fallback-drift" "drift detected" "$combined"

# ----- Test 14: MERCURY_INDEX_AUTOFIX unset → standard drift warning, no autofix attempt -----
combined="$(printf '{"session_id":"sess-14"}' | \
  env MERCURY_REPO_ROOT="$repo_fix_ok" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T14.autofix-unset.exit-code" "0" "$ec"
assert_contains "T14.autofix-unset.standard-drift" "drift detected" "$combined"
case "$combined" in
  *"auto-fix via --in-place succeeded"*)
    FAIL=$((FAIL + 1)); ASSERT=$((ASSERT + 1))
    printf 'FAIL T14.autofix-unset.no-fix-attempted\n  unwanted autofix message present\n' >&2 ;;
  *) PASS=$((PASS + 1)); ASSERT=$((ASSERT + 1)) ;;
esac

# ----- Test 11: regenerate exit 2 (script error, NOT drift) → distinct warning -----
# Per Argus iter-2 medium finding: any non-zero exit was treated as drift.
# Hook now distinguishes exit 1 (drift) from other non-zero (validation
# failed) so operators do not chase phantom drift on script errors.
repo_err="$(make_stub_repo_script_error)"
TMP_LIST+=("$repo_err")
combined="$(printf '{"session_id":"sess-11"}' | env MERCURY_REPO_ROOT="$repo_err" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T11.script-error.exit-code" "0" "$ec"
assert_contains "T11.script-error.kind" "regenerate validation failed" "$combined"
assert_contains "T11.script-error.exit-noted" "exit 2" "$combined"
# Must NOT call it "drift" (semantic distinction enforced)
case "$combined" in
  *"drift detected at session end"*) FAIL=$((FAIL + 1)); ASSERT=$((ASSERT + 1)); printf 'FAIL T11.script-error.not-drift\n  unwanted="drift detected at session end" present in: %s\n' "$combined" >&2;;
  *) PASS=$((PASS + 1)); ASSERT=$((ASSERT + 1));;
esac

# ----- Summary -----
printf '\n----\n%d cases / %d assertions / %d fail\n' \
  "$((PASS + FAIL))" "$ASSERT" "$FAIL" >&2

[ "$FAIL" -eq 0 ] || exit 1
exit 0
