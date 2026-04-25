#!/usr/bin/env bash
# scripts/test-lane-claim.sh — smoke + race tests for lane-claim.sh
#
# Stubs `gh` via PATH-prepended fakes to avoid real GitHub API calls.
# Verifies: arg validation, --help, --dry-run, happy path, race detection.
#
# Run from repo root (or anywhere — auto-discovers via git rev-parse).
# Exit 0 if all tests pass; non-zero if any test fails.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-claim: not inside a git repo" >&2
  exit 2
}
SCRIPT="$REPO_ROOT/scripts/lane-claim.sh"
[ -x "$SCRIPT" ] || {
  echo "test-lane-claim: $SCRIPT missing or not executable" >&2
  exit 2
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

PASS=0
FAIL=0
pass() { printf '  PASS: %s\n' "$1"; PASS=$((PASS+1)); }
fail() { printf '  FAIL: %s\n' "$1"; FAIL=$((FAIL+1)); }

assert_exit() {
  local expected=$1
  local desc=$2
  shift 2
  "$@" >/dev/null 2>&1
  local actual=$?
  if [ "$actual" = "$expected" ]; then
    pass "$desc (exit=$actual)"
  else
    fail "$desc (expected exit=$expected got=$actual)"
  fi
}

# ---------------------------------------------------------------------------
# Arg validation tests (no gh stub needed — validation runs before any API call)
# ---------------------------------------------------------------------------

echo "[arg-validation]"
assert_exit 0 "--help prints usage"               "$SCRIPT" --help
assert_exit 2 "missing both args"                  "$SCRIPT"
assert_exit 2 "missing issue arg"                  "$SCRIPT" only-lane
assert_exit 2 "lane with space rejected"           "$SCRIPT" "bad lane" 309
assert_exit 2 "lane starting with hyphen rejected" "$SCRIPT" -bad 309
assert_exit 2 "non-numeric issue rejected"         "$SCRIPT" good 0xFF
assert_exit 2 "unknown flag rejected"              "$SCRIPT" --bogus good 309
assert_exit 2 "empty issue rejected"               "$SCRIPT" good ""

# ---------------------------------------------------------------------------
# gh stubs for happy path + race + zero-label edge case
# ---------------------------------------------------------------------------

mkdir -p "$TMP/bin"

cat > "$TMP/bin/gh-happy" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "issue edit")    exit 0 ;;
  "issue view")    echo '{"labels":[{"name":"lane:test"},{"name":"P1"}]}' ; exit 0 ;;
  "api user")      echo 'testuser' ; exit 0 ;;
  *) echo "stub-happy: unhandled $*" >&2 ; exit 99 ;;
esac
EOF
chmod +x "$TMP/bin/gh-happy"

cat > "$TMP/bin/gh-race" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "issue edit")    exit 0 ;;
  "issue view")    echo '{"labels":[{"name":"lane:main"},{"name":"lane:other"}]}' ; exit 0 ;;
  "api user")      echo 'testuser' ; exit 0 ;;
  "issue comment") exit 0 ;;
  *) echo "stub-race: unhandled $*" >&2 ; exit 99 ;;
esac
EOF
chmod +x "$TMP/bin/gh-race"

cat > "$TMP/bin/gh-zero" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "issue edit")    exit 0 ;;
  "issue view")    echo '{"labels":[{"name":"P1"}]}' ; exit 0 ;;
  "api user")      echo 'testuser' ; exit 0 ;;
  *) echo "stub-zero: unhandled $*" >&2 ; exit 99 ;;
esac
EOF
chmod +x "$TMP/bin/gh-zero"

cat > "$TMP/bin/gh-edit-fail" <<'EOF'
#!/usr/bin/env bash
case "$1 $2" in
  "issue edit") echo "fake edit failure" >&2 ; exit 1 ;;
  *) echo "stub-edit-fail: unhandled $*" >&2 ; exit 99 ;;
esac
EOF
chmod +x "$TMP/bin/gh-edit-fail"

stub() {
  ln -sf "$TMP/bin/$1" "$TMP/bin/gh"
}

echo
echo "[scenarios]"

stub gh-happy
PATH="$TMP/bin:$PATH" assert_exit 0 "happy path (1 lane label) → exit 0" \
  "$SCRIPT" --no-assignee test 309

stub gh-race
PATH="$TMP/bin:$PATH" assert_exit 1 "race detected (2 lane labels) → exit 1" \
  "$SCRIPT" --no-assignee main 309

stub gh-zero
PATH="$TMP/bin:$PATH" assert_exit 1 "zero lane labels post-write → exit 1" \
  "$SCRIPT" --no-assignee test 309

stub gh-edit-fail
PATH="$TMP/bin:$PATH" assert_exit 2 "gh edit failure → exit 2" \
  "$SCRIPT" --no-assignee test 309

# --dry-run path: doesn't even call gh — should succeed without stub
PATH="$TMP/bin:$PATH" assert_exit 0 "--dry-run skips API → exit 0" \
  "$SCRIPT" --dry-run --no-assignee test 309

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
