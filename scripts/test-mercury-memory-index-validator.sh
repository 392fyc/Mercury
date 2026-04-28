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

assert_not_contains() {
  local name="$1" needle="$2" hay="$3"
  ASSERT=$((ASSERT + 1))
  case "$hay" in
    *"$needle"*) FAIL=$((FAIL + 1)); printf 'FAIL %s\n  unwanted=%s\n  hay     =%s\n' "$name" "$needle" "$hay" >&2; return 1 ;;
    *) PASS=$((PASS + 1)); return 0 ;;
  esac
}

# Stub repo where regenerate --format diff exits 0 (no drift)
make_stub_repo_clean() {
  local dir; dir="$(mktemp -d)"
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
  local dir; dir="$(mktemp -d)"
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

TMP_LIST=""
cleanup() { [ -n "${TMP_LIST:-}" ] && for d in $TMP_LIST; do rm -rf "$d" 2>/dev/null || true; done; }
trap cleanup EXIT

run_hook_capture_all() {
  local repo="$1" stdin_payload="$2" extra_env="${3:-}"
  if [ -n "$extra_env" ]; then
    out_combined="$(printf '%s' "$stdin_payload" | env MERCURY_REPO_ROOT="$repo" "$extra_env" "$PY" "$HOOK" 2>&1)"
  else
    out_combined="$(printf '%s' "$stdin_payload" | env MERCURY_REPO_ROOT="$repo" "$PY" "$HOOK" 2>&1)"
  fi
  printf '%s' "$out_combined"
}

# ----- Test 1: clean repo (regenerate exit 0) → no warning, exit 0 -----
repo_clean="$(make_stub_repo_clean)"
TMP_LIST="$TMP_LIST $repo_clean"
out="$(printf '{"session_id":"abc","hook_event_name":"SessionEnd"}' | \
  env MERCURY_REPO_ROOT="$repo_clean" "$PY" "$HOOK" 2>&1)"
ec=$?
assert_eq "T1.clean.exit-code" "0" "$ec"
assert_eq "T1.clean.no-warning" "" "$out"

# ----- Test 2: drift repo (regenerate exit 1) → warning to stderr, exit 0 -----
repo_drift="$(make_stub_repo_drift)"
TMP_LIST="$TMP_LIST $repo_drift"
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
nonexist="$(mktemp -d)"; rm -rf "$nonexist"
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

# ----- Summary -----
printf '\n----\n%d cases / %d assertions / %d fail\n' \
  "$((PASS + FAIL))" "$ASSERT" "$FAIL" >&2

[ "$FAIL" -eq 0 ] || exit 1
exit 0
