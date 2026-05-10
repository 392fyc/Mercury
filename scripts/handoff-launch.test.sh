#!/usr/bin/env bash
# handoff-launch.test.sh — Smoke tests for handoff-launch.sh
#
# Uses --dry-run mode exclusively: never spawns wt/tmux.
# Exit 0 if all tests pass, 1 if any fail.
#
# Mercury Issue #377

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCH_SCRIPT="$SCRIPT_DIR/handoff-launch.sh"

PASS_COUNT=0
FAIL_COUNT=0
FAILURES=()

# ── Fixtures ──────────────────────────────────────────────────────────────
TMPDIR_FIXTURE="$(mktemp -d)"
FAKE_WORKTREE="$TMPDIR_FIXTURE/worktree"
FAKE_HANDOFF="$TMPDIR_FIXTURE/session-handoff-test.md"
mkdir -p "$FAKE_WORKTREE"
echo "# Fake handoff doc" > "$FAKE_HANDOFF"

# Clean up on exit
trap 'rm -rf "$TMPDIR_FIXTURE"' EXIT

# ── Helpers ───────────────────────────────────────────────────────────────
assert_exit() {
  local test_name="$1"
  local expected_exit="$2"
  shift 2
  local output
  output=$("$@" 2>&1)
  local actual_exit=$?
  if [ "$actual_exit" -eq "$expected_exit" ]; then
    echo "PASS: $test_name"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "FAIL: $test_name — expected exit $expected_exit, got $actual_exit"
    echo "      output: $output"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILURES+=("$test_name")
  fi
}

assert_output_contains() {
  local test_name="$1"
  local needle="$2"
  shift 2
  local output
  output=$("$@" 2>&1)
  if echo "$output" | grep -qF -- "$needle"; then
    echo "PASS: $test_name"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "FAIL: $test_name — expected output to contain: $needle"
    echo "      actual output: $output"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILURES+=("$test_name")
  fi
}

assert_exit_and_contains() {
  local test_name="$1"
  local expected_exit="$2"
  local needle="$3"
  shift 3
  local output
  output=$("$@" 2>&1)
  local actual_exit=$?
  local exit_ok=1
  local content_ok=1
  if [ "$actual_exit" -ne "$expected_exit" ]; then
    exit_ok=0
  fi
  if ! echo "$output" | grep -qF -- "$needle"; then
    content_ok=0
  fi
  if [ "$exit_ok" -eq 1 ] && [ "$content_ok" -eq 1 ]; then
    echo "PASS: $test_name"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "FAIL: $test_name"
    if [ "$exit_ok" -eq 0 ]; then
      echo "      expected exit $expected_exit, got $actual_exit"
    fi
    if [ "$content_ok" -eq 0 ]; then
      echo "      expected output to contain: $needle"
    fi
    echo "      actual output: $output"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILURES+=("$test_name")
  fi
}

# ── Tests ─────────────────────────────────────────────────────────────────

# 1. --help exits 0 and outputs usage
assert_exit_and_contains \
  "--help exits 0 with usage" \
  0 "Usage:" \
  bash "$LAUNCH_SCRIPT" --help

# 2. Missing --lane exits 2
assert_exit_and_contains \
  "missing --lane exits 2" \
  2 "--lane is required" \
  bash "$LAUNCH_SCRIPT" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF"

# 3. Missing --worktree exits 2
assert_exit_and_contains \
  "missing --worktree exits 2" \
  2 "--worktree is required" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --handoff-doc "$FAKE_HANDOFF"

# 4. Missing --handoff-doc exits 2
assert_exit_and_contains \
  "missing --handoff-doc exits 2" \
  2 "--handoff-doc is required" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "$FAKE_WORKTREE"

# 5. Invalid lane name exits 2
assert_exit_and_contains \
  "invalid lane name exits 2" \
  2 "invalid lane name" \
  bash "$LAUNCH_SCRIPT" \
    --lane "Bad-Lane!" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF"

# 6. Nonexistent worktree dir exits 2
assert_exit_and_contains \
  "nonexistent worktree exits 2" \
  2 "not exist or is not a directory" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "/nonexistent/path/worktree" \
    --handoff-doc "$FAKE_HANDOFF"

# 7. Nonexistent handoff-doc exits 2
assert_exit_and_contains \
  "nonexistent handoff-doc exits 2" \
  2 "not exist or is not a file" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "/nonexistent/session-handoff.md"

# 8. Valid args + --dry-run exits 0 and prints [LANE=...] SHORT_PROMPT
assert_exit_and_contains \
  "dry-run exits 0 with [LANE=test]" \
  0 "[LANE=test]" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF" \
    --dry-run

# 9. Verify SHORT_PROMPT canonical format in dry-run
assert_exit_and_contains \
  "dry-run SHORT_PROMPT contains 'Continue from session handoff'" \
  0 "Continue from session handoff" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF" \
    --dry-run

# 10. Valid args + --dry-run + custom --title-prefix exits 0 and uses custom prefix
assert_exit_and_contains \
  "dry-run with custom title-prefix uses custom prefix" \
  0 "custom" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF" \
    --title-prefix "custom" \
    --dry-run

# 11. Lane name with uppercase letters (invalid) exits 2
assert_exit_and_contains \
  "lane name with uppercase exits 2" \
  2 "invalid lane name" \
  bash "$LAUNCH_SCRIPT" \
    --lane "SideLane" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF"

# 12. Lane name starting with hyphen (invalid) exits 2
assert_exit_and_contains \
  "lane name starting with hyphen exits 2" \
  2 "invalid lane name" \
  bash "$LAUNCH_SCRIPT" \
    --lane "-badlane" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF"

# 13. Lane name with semicolon exits 2 (caught at name validation before metachar check)
assert_exit \
  "lane name with semicolon exits 2" \
  2 \
  bash "$LAUNCH_SCRIPT" \
    --lane "bad;lane" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF"

# 14. Dry-run output includes WORKTREE path
assert_exit_and_contains \
  "dry-run output includes worktree path" \
  0 "$FAKE_WORKTREE" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF" \
    --dry-run

# 15. Dry-run output includes handoff-doc path in SHORT_PROMPT
assert_exit_and_contains \
  "dry-run SHORT_PROMPT includes handoff-doc path" \
  0 "$FAKE_HANDOFF" \
  bash "$LAUNCH_SCRIPT" \
    --lane "test" \
    --worktree "$FAKE_WORKTREE" \
    --handoff-doc "$FAKE_HANDOFF" \
    --dry-run

# ── Metachar guard tests (via --handoff-doc path injection) ───────────────
# The lane-name regex rejects hostile chars in --lane before they reach the
# metachar grep. To exercise the metachar guard directly we need a path
# whose resolved SHORT_PROMPT contains the forbidden character. The guard
# checks SHORT_PROMPT which embeds the --handoff-doc path literally. So we
# pass a real file whose PATH itself contains a metachar.
# NTFS forbids: \ / : * ? " < > |  — so | is NOT creatable on Windows.
# NTFS allows:  & ; ( ) are fine on NTFS but rejected by some shells.
# Strategy: try to create fixture files; skip individual sub-test if the
# filesystem rejects the name (|| true pattern).

# 16. handoff-doc path containing '&' — metachar guard rejects if creatable
FAKE_HANDOFF_AMP="$TMPDIR_FIXTURE/bad&name.md"
(echo "fake" > "$FAKE_HANDOFF_AMP") 2>/dev/null || true
if [ -f "$FAKE_HANDOFF_AMP" ]; then
  assert_exit_and_contains \
    "metachar in handoff-doc '&' rejected by metachar guard (exit 2)" \
    2 "forbidden metacharacters" \
    bash "$LAUNCH_SCRIPT" \
      --lane "test" \
      --worktree "$FAKE_WORKTREE" \
      --handoff-doc "$FAKE_HANDOFF_AMP" \
      --dry-run
else
  echo "SKIP: test 16 — filesystem rejected '&' in filename (NTFS/platform)"
fi

# 17. handoff-doc path containing backtick — metachar guard rejects if creatable
FAKE_HANDOFF_BT="$TMPDIR_FIXTURE/bad\`name.md"
(echo "fake" > "$FAKE_HANDOFF_BT") 2>/dev/null || true
if [ -f "$FAKE_HANDOFF_BT" ]; then
  assert_exit_and_contains \
    "metachar in handoff-doc backtick rejected by metachar guard (exit 2)" \
    2 "forbidden metacharacters" \
    bash "$LAUNCH_SCRIPT" \
      --lane "test" \
      --worktree "$FAKE_WORKTREE" \
      --handoff-doc "$FAKE_HANDOFF_BT" \
      --dry-run
else
  echo "SKIP: test 17 — filesystem rejected backtick in filename (NTFS/platform)"
fi

# ── Argv-capture stub test ─────────────────────────────────────────────────
# Verify the launcher invokes wt (Windows) or tmux (Unix) with > 1 separate
# argv tokens — the regression surface of Mercury Issue #377 (single-string
# ShellExecute path). We stub the native executable so no real spawn happens.

STUB_DIR="$TMPDIR_FIXTURE/stub-bin"
ARGV_LOG="$TMPDIR_FIXTURE/wt-argv.log"
mkdir -p "$STUB_DIR"

# Detect which stub to install based on current platform
CURRENT_PLATFORM="$(uname -s 2>/dev/null || echo "unknown")"
case "$CURRENT_PLATFORM" in
  MINGW*|MSYS*|CYGWIN*)
    STUB_EXEC="wt"
    ;;
  Darwin|Linux)
    STUB_EXEC="tmux"
    ;;
  *)
    STUB_EXEC=""
    ;;
esac

if [ -n "$STUB_EXEC" ]; then
  ARGV_LOG_PATH_FOR_STUB="$ARGV_LOG"
  cat > "$STUB_DIR/$STUB_EXEC" <<STUB
#!/usr/bin/env bash
echo "argc=\$#" > "\$ARGV_LOG_PATH"
for i in "\$@"; do
  echo "argv: \$i" >> "\$ARGV_LOG_PATH"
done
exit 0
STUB
  chmod +x "$STUB_DIR/$STUB_EXEC"

  # Run launcher with PATH prepended so the stub intercepts the call.
  # Do NOT pass --dry-run here — we want the spawn path to execute.
  stub_output=$(ARGV_LOG_PATH="$ARGV_LOG_PATH_FOR_STUB" \
    PATH="$STUB_DIR:$PATH" \
    bash "$LAUNCH_SCRIPT" \
      --lane "test" \
      --worktree "$FAKE_WORKTREE" \
      --handoff-doc "$FAKE_HANDOFF" 2>&1)
  stub_exit=$?

  # 18. Launcher exits 0 when stub wt/tmux succeeds
  if [ "$stub_exit" -eq 0 ]; then
    echo "PASS: stub-argv — launcher exits 0 with stub $STUB_EXEC"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "FAIL: stub-argv — launcher exited $stub_exit (expected 0); output: $stub_output"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILURES+=("stub-argv launcher exit 0")
  fi

  # 19. Stub captured > 1 argv tokens (separate-token, not single-string form)
  if [ -f "$ARGV_LOG" ]; then
    captured_argc=$(grep -E '^argc=' "$ARGV_LOG" | cut -d= -f2)
    if [ -n "$captured_argc" ] && [ "$captured_argc" -gt 1 ]; then
      echo "PASS: stub-argv — $STUB_EXEC invoked with $captured_argc separate argv tokens (not single string)"
      PASS_COUNT=$((PASS_COUNT + 1))
    else
      echo "FAIL: stub-argv — $STUB_EXEC argc=$captured_argc (expected > 1); log: $(cat "$ARGV_LOG" 2>/dev/null)"
      FAIL_COUNT=$((FAIL_COUNT + 1))
      FAILURES+=("stub-argv argc > 1")
    fi
  else
    echo "FAIL: stub-argv — argv log not created (stub did not run or ARGV_LOG_PATH not honoured)"
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAILURES+=("stub-argv log created")
  fi
else
  echo "SKIP: tests 18-19 — unsupported platform for stub-argv test"
fi

# ── Summary ───────────────────────────────────────────────────────────────
TOTAL=$((PASS_COUNT + FAIL_COUNT))
echo ""
echo "$PASS_COUNT/$TOTAL tests passed"

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo "Failed tests:"
  for f in "${FAILURES[@]}"; do
    echo "  - $f"
  done
  exit 1
fi

exit 0
