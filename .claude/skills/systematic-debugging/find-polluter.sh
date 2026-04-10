#!/usr/bin/env bash
# Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
# Source: https://github.com/obra/superpowers/blob/917e5f5/skills/systematic-debugging/find-polluter.sh
# SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d
# Date: 2026-04-10
# Issue: #209

# find-polluter.sh - Find which test creates unwanted files/state
#
# Usage: ./find-polluter.sh <file-or-dir-to-watch> <test-glob-pattern> [test-command]
# Example: ./find-polluter.sh '.git' 'src/**/*.test.ts'
# Example: ./find-polluter.sh '.git' 'src/**/*.test.ts' 'npx jest --silent'
#
# The test command receives the test file path as its last argument.
# Default: npx vitest run (override via 3rd arg or TEST_RUNNER env var)
#
# Runs tests one-by-one, checks after each whether the watched
# file/directory appeared. Stops at the first polluter.

set -e

POLLUTION_CHECK="$1"
TEST_PATTERN="$2"
TEST_CMD="${3:-${TEST_RUNNER:-npx vitest run}}"

if [ -z "$POLLUTION_CHECK" ] || [ -z "$TEST_PATTERN" ]; then
  echo "Usage: $0 <file-or-dir-to-watch> <test-glob-pattern> [test-command]"
  echo "Example: $0 '.git' 'src/**/*.test.ts'"
  echo "Example: $0 '.git' 'src/**/*.test.ts' 'npx jest --silent'"
  echo ""
  echo "Override test runner via 3rd argument or TEST_RUNNER env var."
  exit 1
fi

# Clean up before starting
rm -rf "$POLLUTION_CHECK" 2>/dev/null || true

# Find all matching test files
TEST_FILES=$(find . -path "./$TEST_PATTERN" -type f 2>/dev/null | sort)

if [ -z "$TEST_FILES" ]; then
  echo "No test files matched pattern: $TEST_PATTERN"
  exit 1
fi

TOTAL=$(printf '%s\n' "$TEST_FILES" | wc -l | tr -d ' ')
CURRENT=0
FOUND=false

echo "Checking $TOTAL test files for pollution of '$POLLUTION_CHECK'..."
echo ""

for TEST_FILE in $TEST_FILES; do
  CURRENT=$((CURRENT + 1))

  # Clean before each test
  rm -rf "$POLLUTION_CHECK" 2>/dev/null || true

  echo "[$CURRENT/$TOTAL] Running: $TEST_FILE"
  $TEST_CMD "$TEST_FILE" > /dev/null 2>&1 || true

  if [ -e "$POLLUTION_CHECK" ]; then
    echo ""
    echo "====================================="
    echo "FOUND POLLUTER! Test: $TEST_FILE"
    echo "Created: $POLLUTION_CHECK"
    echo "====================================="
    echo ""
    echo "To investigate:"
    echo "  1. Run: $TEST_CMD $TEST_FILE"
    echo "  2. Check for git init, fs.mkdirSync, or similar calls"
    echo "  3. Look for missing cleanup in afterEach/afterAll"
    FOUND=true
    break
  fi
done

if [ "$FOUND" = false ]; then
  echo ""
  echo "No polluting test found. '$POLLUTION_CHECK' was not created by any individual test."
  echo "It may be caused by test interaction (running multiple tests together)."
fi
