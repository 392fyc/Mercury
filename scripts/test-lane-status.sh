#!/bin/bash
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

# ---------------------------------------------------------------------------
# Stub `git` — intercepts ls-remote and log; passes other git commands through
# ---------------------------------------------------------------------------
cat > "$STUB_BIN/git" << 'GITSTUB'
#!/bin/bash
# Stub git for lane-status.sh tests

if [[ "$*" == *"ls-remote"*"feature/lane-*"* ]]; then
  echo "abc123def456	refs/heads/feature/lane-main/TASK-001"
  echo "789xyz000111	refs/heads/feature/lane-side-multi-lane/TASK-309"
  exit 0
fi

if [[ "$*" == *"log"*"-1"*"--format=%cI"* ]]; then
  echo "2026-04-26T10:00:00+00:00"
  exit 0
fi

# Pass through all other git commands to the real git
exec "$(which git)" "$@"
GITSTUB
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

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed (total $(( PASS + FAIL )))"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
