#!/bin/bash
# scripts/test-statusline-mercury.sh — smoke tests for statusline-mercury.sh (Issue #322)
# Tests: display output, pause trigger, FLOOR-not-round, boundary 95.0,
#        corrupted marker self-heal, two-source resume, partial resume guard.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATUSLINE="$SCRIPT_DIR/statusline-mercury.sh"

PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Test harness helpers
# ---------------------------------------------------------------------------
pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1"; FAIL=$(( FAIL + 1 )); }

assert_contains() {
  local label="$1" output="$2" needle="$3"
  if echo "$output" | grep -qF "$needle"; then
    pass "$label"
  else
    fail "$label — expected to find '$needle' in: $output"
  fi
}

assert_not_contains() {
  local label="$1" output="$2" needle="$3"
  if ! echo "$output" | grep -qF "$needle"; then
    pass "$label"
  else
    fail "$label — expected NOT to find '$needle' in: $output"
  fi
}

assert_file_exists() {
  local label="$1" path="$2"
  if [ -f "$path" ]; then
    pass "$label"
  else
    fail "$label — file not found: $path"
  fi
}

assert_file_not_exists() {
  local label="$1" path="$2"
  if [ ! -f "$path" ]; then
    pass "$label"
  else
    fail "$label — file unexpectedly exists: $path"
  fi
}

assert_file_content() {
  local label="$1" path="$2" expected="$3"
  local actual
  actual=$(cat "$path" 2>/dev/null || echo "")
  if [ "$actual" = "$expected" ]; then
    pass "$label"
  else
    fail "$label — expected '$expected', got '$actual'"
  fi
}

# ---------------------------------------------------------------------------
# Setup: temp repo root (shared across tests, reset per test)
# ---------------------------------------------------------------------------
CLAUDE_PROJECT_DIR="/tmp/mercury-statusline-test-$$"
export CLAUDE_PROJECT_DIR
# statusline-mercury.sh checks $CLAUDE_PROJECT_DIR/.git to validate repo root
mkdir -p "$CLAUDE_PROJECT_DIR/.git"
mkdir -p "$CLAUDE_PROJECT_DIR/.mercury/state"
MARKER="$CLAUDE_PROJECT_DIR/.mercury/state/auto-run-paused"

cleanup() { rm -rf "$CLAUDE_PROJECT_DIR"; }
trap cleanup EXIT

reset_marker() { rm -f "$MARKER"; }

BASE_JSON_TEMPLATE='{"rate_limits":{"five_hour":{"used_percentage":PCT,"resets_at":9999999999},"seven_day":{"used_percentage":18.3}},"model":{"display_name":"Opus 4.7"},"context_window":{"used_percentage":12}}'

make_json() {
  local pct="$1"
  echo "${BASE_JSON_TEMPLATE//PCT/$pct}"
}

# ---------------------------------------------------------------------------
# Test 1 — display only (42% usage, no pause)
# ---------------------------------------------------------------------------
reset_marker
OUTPUT=$(make_json 42.0 | bash "$STATUSLINE" 2>/dev/null)
assert_contains "T1 stdout contains '5h: 42%'" "$OUTPUT" "5h: 42%"
assert_file_not_exists "T1 no marker created" "$MARKER"

# ---------------------------------------------------------------------------
# Test 2 — pause trigger (96.5% usage)
# ---------------------------------------------------------------------------
reset_marker
make_json 96.5 | bash "$STATUSLINE" 2>/dev/null
assert_file_exists "T2 marker created at 96.5%" "$MARKER"
assert_file_content "T2 marker contains resets_at" "$MARKER" "9999999999"

# ---------------------------------------------------------------------------
# Test 3 — FLOOR-not-round: 94.6 floors to 94, must NOT trigger pause
# ---------------------------------------------------------------------------
reset_marker
make_json 94.6 | bash "$STATUSLINE" 2>/dev/null
assert_file_not_exists "T3 no marker at 94.6% (floors to 94)" "$MARKER"

# ---------------------------------------------------------------------------
# Test 4 — boundary 95.0: must trigger pause
# ---------------------------------------------------------------------------
reset_marker
make_json 95.0 | bash "$STATUSLINE" 2>/dev/null
assert_file_exists "T4 marker created at exactly 95.0%" "$MARKER"

# ---------------------------------------------------------------------------
# Test 5 — corrupted marker self-heal
# Pre-create marker with non-numeric content; feed 50% usage.
# Expect: marker deleted AND stderr contains corruption message.
# ---------------------------------------------------------------------------
reset_marker
echo "not-a-number" > "$MARKER"
STDERR_OUT=$(make_json 50.0 | bash "$STATUSLINE" 2>&1 >/dev/null)
assert_file_not_exists "T5 corrupted marker deleted" "$MARKER"
assert_contains "T5 stderr mentions corruption" "$STDERR_OUT" "corrupted"

# ---------------------------------------------------------------------------
# Test 6 — two-source resume: marker has expired timestamp (0), usage 50%
# Both signals (stored_reset=0 <= now, pct < threshold) → marker removed.
# ---------------------------------------------------------------------------
reset_marker
echo "0" > "$MARKER"
make_json 50.0 | bash "$STATUSLINE" 2>/dev/null
assert_file_not_exists "T6 expired marker removed when usage low" "$MARKER"

# ---------------------------------------------------------------------------
# Test 7 — partial resume: marker has expired timestamp (0), usage still 96%
# Only one signal clears (timestamp expired) but usage still high → marker stays.
# ---------------------------------------------------------------------------
reset_marker
echo "0" > "$MARKER"
make_json 96.0 | bash "$STATUSLINE" 2>/dev/null
assert_file_exists "T7 marker preserved when usage still high despite expired ts" "$MARKER"

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
echo ""
echo "Results: $PASS passed, $FAIL failed (total $(( PASS + FAIL )))"
if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
