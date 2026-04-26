#!/usr/bin/env bash
# scripts/test-lane-status.sh — smoke test for lane-status.sh (Issue #322)
# Stubs `gh` and `git` network calls to avoid hitting real GitHub.
# Asserts output JSON is valid and contains required top-level keys.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LANE_STATUS="$SCRIPT_DIR/lane-status.sh"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1 — $2"; FAIL=$(( FAIL + 1 )); }

# ---------------------------------------------------------------------------
# Setup: temp repo root + stub bin directory prepended to PATH
# ---------------------------------------------------------------------------
REPO_ROOT="/tmp/mercury-lane-status-test-$$"
export MERCURY_TEST_REPO_ROOT="$REPO_ROOT"
mkdir -p "$REPO_ROOT/.git"
mkdir -p "$REPO_ROOT/.mercury/state"

STUB_BIN="/tmp/mercury-lane-status-stubs-$$"
mkdir -p "$STUB_BIN"

cleanup() {
  rm -rf "$REPO_ROOT" "$STUB_BIN"
}
trap cleanup EXIT

# Resolve the real git binary BEFORE prepending STUB_BIN to PATH so stubs can
# delegate without infinite recursion. Walk PATH and skip STUB_BIN entries.
REAL_GIT=""
for _p in $(echo "$PATH" | tr ':' '\n'); do
  _g="$_p/git"
  if [ -x "$_g" ] && [ "$_p" != "$STUB_BIN" ]; then
    REAL_GIT="$_g"
    break
  fi
done
[ -z "$REAL_GIT" ] && { echo "FAIL: real git not found on PATH" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Stub `gh` — emits canned responses
# ---------------------------------------------------------------------------
cat > "$STUB_BIN/gh" << 'GHSTUB'
#!/bin/bash
# Stub gh for lane-status.sh tests

# gh label list --limit 100 --json name
if [[ "$*" == *"label list"* ]]; then
  echo '[{"name":"lane:main"},{"name":"lane:side-multi-lane"}]'
  exit 0
fi

# gh issue list --label "lane:main" ...
if [[ "$*" == *"lane:main"* ]]; then
  echo '[{"number":301,"title":"Main lane test issue","labels":[{"name":"lane:main"}],"updatedAt":"2026-04-26T10:00:00Z"}]'
  exit 0
fi

# gh issue list --label "lane:side-multi-lane" ...
if [[ "$*" == *"lane:side-multi-lane"* ]]; then
  echo '[{"number":309,"title":"Side lane test issue","labels":[{"name":"lane:side-multi-lane"}],"updatedAt":"2026-04-26T10:05:00Z"}]'
  exit 0
fi

# Default: empty list
echo '[]'
exit 0
GHSTUB
chmod +x "$STUB_BIN/gh"

# Stub `git` — intercepts ls-remote and log; delegates other git commands to REAL_GIT.
printf '%s\n' '#!/bin/bash' \
  'if [[ "$*" == *"ls-remote"* ]]; then' \
  "  printf '%s\t%s\n' 'abc123def456' 'refs/heads/feature/lane-main/TASK-001'" \
  "  printf '%s\t%s\n' '789xyz000111' 'refs/heads/feature/lane-side-multi-lane/TASK-309'" \
  '  exit 0; fi' \
  'if [[ "$*" == *"log"* ]]; then' \
  '  echo "2026-04-26T10:00:00Z"; exit 0; fi' \
  "exec '$REAL_GIT' \"\$@\"" > "$STUB_BIN/git"
chmod +x "$STUB_BIN/git"

# Prepend stub bin to PATH
export PATH="$STUB_BIN:$PATH"

# ---------------------------------------------------------------------------
# Test 1 — valid JSON output with required top-level keys
# ---------------------------------------------------------------------------
OUTPUT_FILE="$REPO_ROOT/.mercury/state/lane-status.json"

set +e
bash "$LANE_STATUS" 2>/dev/null
EXIT_CODE=$?
set -e

if [ "$EXIT_CODE" -ne 0 ]; then
  fail "T1 script exited non-zero" "exit code: $EXIT_CODE"
else
  pass "T1 script exits 0"
fi

if [ ! -f "$OUTPUT_FILE" ]; then
  fail "T1 output file exists" "lane-status.json not created"
else
  pass "T1 output file created"
fi

# Validate JSON structure
if jq -e 'has("last_checked_at") and has("lanes") and has("stale_threshold_minutes")' \
       "$OUTPUT_FILE" > /dev/null 2>&1; then
  pass "T1 JSON has required top-level keys"
else
  fail "T1 JSON has required top-level keys" "$(cat "$OUTPUT_FILE" 2>/dev/null | head -3)"
fi

# Validate lanes is an array
if jq -e '.lanes | type == "array"' "$OUTPUT_FILE" > /dev/null 2>&1; then
  pass "T1 lanes is an array"
else
  fail "T1 lanes is an array" "$(jq '.lanes' "$OUTPUT_FILE" 2>/dev/null)"
fi

# Validate last_checked_at is a non-empty string
LAST_CHECKED=$(jq -r '.last_checked_at' "$OUTPUT_FILE" 2>/dev/null)
if [ -n "$LAST_CHECKED" ] && [ "$LAST_CHECKED" != "null" ]; then
  pass "T1 last_checked_at is set: $LAST_CHECKED"
else
  fail "T1 last_checked_at is set" "got: $LAST_CHECKED"
fi

# Validate stale_threshold_minutes is numeric
STALE_MIN=$(jq -r '.stale_threshold_minutes' "$OUTPUT_FILE" 2>/dev/null)
if [[ "$STALE_MIN" =~ ^[0-9]+$ ]]; then
  pass "T1 stale_threshold_minutes is numeric: $STALE_MIN"
else
  fail "T1 stale_threshold_minutes is numeric" "got: $STALE_MIN"
fi

# ---------------------------------------------------------------------------
# Test 2 — each lane entry has required fields
# ---------------------------------------------------------------------------
LANE_COUNT=$(jq '.lanes | length' "$OUTPUT_FILE" 2>/dev/null)
if [ "$LANE_COUNT" -ge 1 ]; then
  pass "T2 at least one lane in output (got $LANE_COUNT)"
else
  fail "T2 at least one lane in output" "got $LANE_COUNT"
fi

if jq -e '.lanes[] | has("name") and has("issues") and has("branches") and has("is_stale")' \
       "$OUTPUT_FILE" > /dev/null 2>&1; then
  pass "T2 each lane has name/issues/branches/is_stale"
else
  fail "T2 each lane has name/issues/branches/is_stale" \
       "$(jq '.lanes[0]' "$OUTPUT_FILE" 2>/dev/null)"
fi

# ---------------------------------------------------------------------------
# Test 3 — --print flag produces human-readable stdout
# ---------------------------------------------------------------------------
PRINT_OUT=$(bash "$LANE_STATUS" --print 2>/dev/null)
if echo "$PRINT_OUT" | grep -q "Lane Status"; then
  pass "T3 --print produces summary header"
else
  fail "T3 --print produces summary header" "stdout: $PRINT_OUT"
fi

# T_DATE — staleness / date-parsing test (validates M1 fix).
# Stubs git log to return a controlled timestamp; asserts is_stale accordingly.
# REAL_GIT was resolved at test-script start (above) so subtest stubs reuse it.

# T_DATE.1: branch committed 30 seconds ago → is_stale: false
# Issue updatedAt is set far in the future so it never triggers stale alone;
# branch timestamp is the controlling signal.
FRESH_TS="$(date -u -d '30 seconds ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || \
            date -u -v-30S '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || \
            date -u '+%Y-%m-%dT%H:%M:%SZ')"

STUB_DATE1="/tmp/mercury-lane-status-stubs-date1-$$"
mkdir -p "$STUB_DATE1"

# Write gh stub: single lane:main, issue updated far in future (never stale)
printf '%s\n' '#!/bin/bash' \
  'if [[ "$*" == *"label list"* ]]; then' \
  '  echo '"'"'[{"name":"lane:main"}]'"'"'; exit 0; fi' \
  'if [[ "$*" == *"issue list"* ]]; then' \
  '  echo '"'"'[{"number":1,"title":"Fresh issue","labels":[{"name":"lane:main"}],"updatedAt":"2099-01-01T00:00:00Z"}]'"'"'; exit 0; fi' \
  'echo '"'"'[]'"'"'; exit 0' > "$STUB_DATE1/gh"
chmod +x "$STUB_DATE1/gh"

# Write git stub: returns FRESH_TS for log; real git for everything else
printf '%s\n' '#!/bin/bash' \
  'if [[ "$*" == *"ls-remote"* ]]; then' \
  "  printf '%s\t%s\n' 'aaabbbccc111' 'refs/heads/feature/lane-main/TASK-DATE'; exit 0; fi" \
  'if [[ "$*" == *"log"* ]]; then' \
  "  echo '$FRESH_TS'; exit 0; fi" \
  "exec '$REAL_GIT' \"\$@\"" > "$STUB_DATE1/git"
chmod +x "$STUB_DATE1/git"

export PATH="$STUB_DATE1:$(echo "$PATH" | sed "s|${STUB_BIN}:||g")"

rm -f "$REPO_ROOT/.mercury/state/lane-status.json"
set +e
bash "$LANE_STATUS" 2>/dev/null
EXIT_D1=$?
set -e

if [ "$EXIT_D1" -ne 0 ]; then
  fail "T_DATE.1 script exits 0 (fresh branch)" "exit code: $EXIT_D1"
elif [ ! -f "$REPO_ROOT/.mercury/state/lane-status.json" ]; then
  fail "T_DATE.1 output file created" "missing lane-status.json"
else
  # Use index 0 — T_DATE stubs emit exactly one lane
  D1_STALE=$(jq -r '.lanes[0].is_stale' "$REPO_ROOT/.mercury/state/lane-status.json" 2>/dev/null || echo "missing")
  D1_TS=$(jq -r '.lanes[0].branches[0].last_commit_at // ""' "$REPO_ROOT/.mercury/state/lane-status.json" 2>/dev/null || echo "")
  if [ "$D1_STALE" = "false" ]; then
    pass "T_DATE.1 is_stale=false for branch committed 30s ago"
  else
    fail "T_DATE.1 is_stale=false for branch committed 30s ago" "got is_stale=$D1_STALE ts=$D1_TS"
  fi
  if [ -n "$D1_TS" ] && [ "$D1_TS" != "null" ]; then
    pass "T_DATE.1 last_commit_at is non-empty: $D1_TS"
  else
    fail "T_DATE.1 last_commit_at is non-empty" "got: $D1_TS (full json: $(cat "$REPO_ROOT/.mercury/state/lane-status.json"))"
  fi
fi

rm -rf "$STUB_DATE1"

# T_DATE.2: branch committed 1 hour ago → is_stale: true
# Both issue and branch timestamps are stale so both signals agree.
STALE_TS="$(date -u -d '1 hour ago' '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || \
            date -u -v-1H '+%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || \
            echo '2000-01-01T00:00:00Z')"

STUB_DATE2="/tmp/mercury-lane-status-stubs-date2-$$"
mkdir -p "$STUB_DATE2"

printf '%s\n' '#!/bin/bash' \
  'if [[ "$*" == *"label list"* ]]; then' \
  '  echo '"'"'[{"name":"lane:main"}]'"'"'; exit 0; fi' \
  'if [[ "$*" == *"issue list"* ]]; then' \
  "  echo '[{\"number\":1,\"title\":\"Old\",\"labels\":[{\"name\":\"lane:main\"}],\"updatedAt\":\"$STALE_TS\"}]'; exit 0; fi" \
  'echo '"'"'[]'"'"'; exit 0' > "$STUB_DATE2/gh"
chmod +x "$STUB_DATE2/gh"

printf '%s\n' '#!/bin/bash' \
  'if [[ "$*" == *"ls-remote"* ]]; then' \
  "  printf '%s\t%s\n' 'aaabbbccc222' 'refs/heads/feature/lane-main/TASK-DATE'; exit 0; fi" \
  'if [[ "$*" == *"log"* ]]; then' \
  "  echo '$STALE_TS'; exit 0; fi" \
  "exec '$REAL_GIT' \"\$@\"" > "$STUB_DATE2/git"
chmod +x "$STUB_DATE2/git"

export PATH="$STUB_DATE2:$(echo "$PATH" | sed "s|${STUB_DATE1}:||g")"

rm -f "$REPO_ROOT/.mercury/state/lane-status.json"
set +e
bash "$LANE_STATUS" 2>/dev/null
EXIT_D2=$?
set -e

if [ "$EXIT_D2" -ne 0 ]; then
  fail "T_DATE.2 script exits 0 (stale branch)" "exit code: $EXIT_D2"
elif [ ! -f "$REPO_ROOT/.mercury/state/lane-status.json" ]; then
  fail "T_DATE.2 output file created" "missing lane-status.json"
else
  D2_STALE=$(jq -r '.lanes[0].is_stale' "$REPO_ROOT/.mercury/state/lane-status.json" 2>/dev/null || echo "missing")
  if [ "$D2_STALE" = "true" ]; then
    pass "T_DATE.2 is_stale=true for branch committed 1h ago"
  else
    fail "T_DATE.2 is_stale=true for branch committed 1h ago" "got is_stale=$D2_STALE"
  fi
fi

rm -rf "$STUB_DATE2"

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed (total $(( PASS + FAIL )))"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
