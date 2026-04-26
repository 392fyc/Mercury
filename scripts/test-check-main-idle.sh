#!/usr/bin/env bash
# scripts/test-check-main-idle.sh — smoke tests for check-main-idle.sh
#
# Uses --no-issue-check to avoid live GitHub calls. Builds synthetic
# memory with a fresh / stale `session-handoff.md` and verifies exit code
# semantics + report fields. Branch activity is tested implicitly via the
# real repo (no synthetic git refs).

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-check-main-idle: not inside a git repo" >&2; exit 2; }
SCRIPT="$REPO_ROOT/scripts/check-main-idle.sh"
[ -x "$SCRIPT" ] || { echo "test-check-main-idle: $SCRIPT missing or not executable" >&2; exit 2; }

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
pass() { printf '  PASS: %s\n' "$1"; PASS=$((PASS+1)); }
fail() { printf '  FAIL: %s\n' "$1"; FAIL=$((FAIL+1)); }

assert_exit() {
  local expected=$1 desc=$2; shift 2
  "$@" >/dev/null 2>&1; local actual=$?
  if [ "$actual" = "$expected" ]; then pass "$desc (exit=$actual)"
  else fail "$desc (expected=$expected got=$actual)"; fi
}

assert_contains() {
  local desc=$1 needle=$2 actual=$3
  if printf '%s' "$actual" | grep -q -- "$needle"; then pass "$desc"
  else fail "$desc — needle '$needle' not in: $(printf '%s' "$actual" | head -c200)"; fi
}

# ---- arg validation ----
echo "[arg-validation]"
assert_exit 0 "--help"                       "$SCRIPT" --help
assert_exit 2 "unknown flag"                  "$SCRIPT" --bogus
assert_exit 2 "--hours zero"                  "$SCRIPT" --hours 0 --no-issue-check --memory-dir "$TMP"
assert_exit 2 "--hours non-numeric"           "$SCRIPT" --hours abc --no-issue-check --memory-dir "$TMP"
assert_exit 2 "--format invalid"              "$SCRIPT" --format yaml --no-issue-check --memory-dir "$TMP"
assert_exit 2 "missing memory dir"            "$SCRIPT" --memory-dir "$TMP/nope" --no-issue-check

# ---- fresh handoff → not idle (exit 1) ----
echo
echo "[scenarios]"
MEM_FRESH="$TMP/fresh"; mkdir -p "$MEM_FRESH"
touch "$MEM_FRESH/session-handoff.md"

OUT_FRESH=$("$SCRIPT" --hours 48 --memory-dir "$MEM_FRESH" --no-issue-check --format text 2>&1)
RC=$?
[ "$RC" = "1" ] && pass "fresh handoff → exit 1 (not idle)" \
  || fail "fresh handoff exit=$RC out=$OUT_FRESH"
assert_contains "report has branch_age"  "branch_age"  "$OUT_FRESH"
assert_contains "report has handoff_age" "handoff_age" "$OUT_FRESH"
assert_contains "report has issue_age"   "issue_age"   "$OUT_FRESH"
assert_contains "verdict line present"   "verdict:"    "$OUT_FRESH"

# ---- stale handoff (60d) + main lane has no current branches/Issues
# (--no-issue-check suppresses signal 3; signal 1 only if NO main-lane
# branches exist locally — depends on environment, so we don't assert
# verdict=idle for this case to keep tests deterministic).
MEM_STALE="$TMP/stale"; mkdir -p "$MEM_STALE"
touch "$MEM_STALE/session-handoff.md"
STALE_DATE=$(date -d '60 days ago' '+%Y%m%d%H%M' 2>/dev/null || date -v-60d '+%Y%m%d%H%M' 2>/dev/null)
if [ -n "$STALE_DATE" ]; then
  touch -t "$STALE_DATE" "$MEM_STALE/session-handoff.md" 2>/dev/null \
    && pass "stale fixture mtime set" \
    || fail "could not set stale fixture mtime"
  OUT_STALE=$("$SCRIPT" --hours 48 --memory-dir "$MEM_STALE" --no-issue-check --format json 2>&1) || true
  assert_contains "json verdict field present" '"verdict":"' "$OUT_STALE"
  # Verify the handoff_age field shows a large number (>48h), not "0h".
  if printf '%s' "$OUT_STALE" | grep -qE '"handoff_age_hours":"[0-9]{3,}"|"handoff_age_hours":"inf"'; then
    pass "stale handoff_age reflects 60d-old mtime (>=100h)"
  else
    fail "stale handoff_age unexpectedly small in: $OUT_STALE"
  fi
fi

# ---- json format ----
OUT_JSON=$("$SCRIPT" --hours 48 --memory-dir "$MEM_FRESH" --no-issue-check --format json 2>&1) || true
assert_contains "json has threshold_hours" '"threshold_hours":48' "$OUT_JSON"
assert_contains "json has verdict"          '"verdict":"'         "$OUT_JSON"

# ---- die-on-tool-failure (Argus #327 finding #5 fix) ----
# When gh issue list fails (network/auth), check-main-idle.sh MUST exit 2
# (not silently treat as "no Issue activity" → "idle"). A false "idle"
# verdict directly enables spurious Rule 4.1 emergency escalation PRs.
echo
echo "[die-on-tool-failure]"
mkdir -p "$TMP/bin-fail"
cat > "$TMP/bin-fail/gh" <<'EOF'
#!/usr/bin/env bash
echo "fake gh failure" >&2
exit 1
EOF
chmod +x "$TMP/bin-fail/gh"

OUT_FAIL=$(PATH="$TMP/bin-fail:$PATH" GH_REPO="test-owner/test-repo" \
  "$SCRIPT" --hours 48 --memory-dir "$MEM_FRESH" --format text 2>&1) || RC_FAIL=$?
RC_FAIL=${RC_FAIL:-0}
[ "$RC_FAIL" = "2" ] && pass "gh failure → die exit=2 (no spurious idle verdict)" \
  || fail "gh failure exit=$RC_FAIL out=$OUT_FAIL (expected 2)"

# Verify the die message points to the right escape hatch.
if printf '%s' "$OUT_FAIL" | grep -q -- "--no-issue-check"; then
  pass "die message mentions --no-issue-check escape hatch"
else
  fail "die message missing escape hatch hint: $OUT_FAIL"
fi

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
