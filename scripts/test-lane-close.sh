#!/usr/bin/env bash
# scripts/test-lane-close.sh — smoke tests for lane-close.sh
#
# Builds synthetic LANES.md + .tmp/lane-X/ and exercises validation, status
# rewrite (only target lane's section), tmp-dir removal, safety guards, and
# dry-run path. Exit 0 if all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-close: not inside a git repo" >&2; exit 2; }
SCRIPT="$REPO_ROOT/scripts/lane-close.sh"
[ -x "$SCRIPT" ] || { echo "test-lane-close: $SCRIPT missing or not executable" >&2; exit 2; }

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

write_fixture_lanes() {
  local path="$1"
  cat > "$path" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `main` (default lane)

- **Handoff file**: `session-handoff.md`
- **Status**: `active`

### `side-target`

- **Handoff file**: `session-handoff-side-target.md`
- **Status**: `active`

### `side-other`

- **Handoff file**: `session-handoff-side-other.md`
- **Status**: `active`

### `side-already-closed`

- **Handoff file**: `session-handoff-side-already-closed.md`
- **Status**: `closed`

## Closed Lanes

(none)
EOF
}

# ---- arg validation ----
echo "[arg-validation]"
assert_exit 0 "--help"                              "$SCRIPT" --help
assert_exit 2 "missing lane arg"                     "$SCRIPT" --yes --memory-dir "$TMP"
assert_exit 2 "lane name with space rejected"        "$SCRIPT" "bad lane" --yes --memory-dir "$TMP"
assert_exit 2 "lane name starting with hyphen rejected" "$SCRIPT" -bad --yes --memory-dir "$TMP"
assert_exit 2 "missing lanes-file rejected"          "$SCRIPT" target --yes --memory-dir "$TMP/nope"

# ---- happy path ----
echo
echo "[happy-path]"
MEM1="$TMP/case1"; mkdir -p "$MEM1"; write_fixture_lanes "$MEM1/LANES.md"
TMPDIR1="$TMP/case1-repo/.tmp/lane-side-target"; mkdir -p "$TMPDIR1"
echo "scratch" > "$TMPDIR1/note.txt"
mkdir -p "$TMP/case1-repo"

OUT=$("$SCRIPT" side-target --yes --force-cross-lane \
        --lanes-file "$MEM1/LANES.md" --memory-dir "$MEM1" \
        --repo-root "$TMP/case1-repo" --tmp-dir "$TMPDIR1" 2>&1)
RC=$?
[ "$RC" = "0" ] && pass "happy path exits 0" || fail "happy path exit=$RC: $OUT"

# Verify side-target Status flipped to closed; OTHER lanes untouched.
TARGET_STATUS=$(awk '/^### `side-target`/{s=1; next} /^### / && s {exit} s && /^- \*\*Status\*\*:/ {print; exit}' "$MEM1/LANES.md")
case "$TARGET_STATUS" in
  *closed*) pass "side-target Status flipped to closed" ;;
  *) fail "side-target Status not flipped: '$TARGET_STATUS'" ;;
esac

OTHER_STATUS=$(awk '/^### `side-other`/{s=1; next} /^### / && s {exit} s && /^- \*\*Status\*\*:/ {print; exit}' "$MEM1/LANES.md")
case "$OTHER_STATUS" in
  *active*) pass "side-other Status untouched (still active)" ;;
  *) fail "side-other Status accidentally modified: '$OTHER_STATUS'" ;;
esac

MAIN_STATUS=$(awk '/^### `main`/{s=1; next} /^### / && s {exit} s && /^- \*\*Status\*\*:/ {print; exit}' "$MEM1/LANES.md")
case "$MAIN_STATUS" in
  *active*) pass "main Status untouched (still active)" ;;
  *) fail "main Status accidentally modified: '$MAIN_STATUS'" ;;
esac

# Verify tmp dir removed.
[ ! -d "$TMPDIR1" ] && pass "tmp dir removed" || fail "tmp dir still exists: $TMPDIR1"

# ---- already-closed lane rejected ----
echo
echo "[validation]"
MEM2="$TMP/case2"; mkdir -p "$MEM2"; write_fixture_lanes "$MEM2/LANES.md"
mkdir -p "$TMP/case2-repo"
OUT2=$("$SCRIPT" side-already-closed --yes --force-cross-lane \
        --lanes-file "$MEM2/LANES.md" --memory-dir "$MEM2" \
        --repo-root "$TMP/case2-repo" 2>&1)
RC2=$?
[ "$RC2" = "1" ] && pass "already-closed lane rejected (exit=1)" || fail "already-closed exit=$RC2: $OUT2"

# Lane not in LANES.md → exit 1
MEM3="$TMP/case3"; mkdir -p "$MEM3"; write_fixture_lanes "$MEM3/LANES.md"
mkdir -p "$TMP/case3-repo"
assert_exit 1 "unknown lane rejected" \
  "$SCRIPT" no-such-lane --yes --force-cross-lane \
    --lanes-file "$MEM3/LANES.md" --memory-dir "$MEM3" \
    --repo-root "$TMP/case3-repo"

# ---- safety guard: refuse if .uncommitted file present ----
echo
echo "[safety-guards]"
MEM4="$TMP/case4"; mkdir -p "$MEM4"; write_fixture_lanes "$MEM4/LANES.md"
TMPDIR4="$TMP/case4-repo/.tmp/lane-side-target"; mkdir -p "$TMPDIR4"
echo "danger" > "$TMPDIR4/work.uncommitted"
mkdir -p "$TMP/case4-repo"
OUT4=$("$SCRIPT" side-target --yes --force-cross-lane \
        --lanes-file "$MEM4/LANES.md" --memory-dir "$MEM4" \
        --repo-root "$TMP/case4-repo" --tmp-dir "$TMPDIR4" 2>&1)
RC4=$?
[ "$RC4" = "1" ] && pass "uncommitted file blocks close (exit=1)" \
  || fail "uncommitted file did not block: exit=$RC4 out=$OUT4"
# Status MUST still be active in this case (no partial state).
TARGET_STATUS4=$(awk '/^### `side-target`/{s=1; next} /^### / && s {exit} s && /^- \*\*Status\*\*:/ {print; exit}' "$MEM4/LANES.md")
case "$TARGET_STATUS4" in
  *active*) pass "side-target Status preserved (no partial flip on safety abort)" ;;
  *) fail "side-target Status partially flipped: '$TARGET_STATUS4'" ;;
esac

# Refuse if .git directory present (nested checkout indicator).
MEM5="$TMP/case5"; mkdir -p "$MEM5"; write_fixture_lanes "$MEM5/LANES.md"
TMPDIR5="$TMP/case5-repo/.tmp/lane-side-target"; mkdir -p "$TMPDIR5/.git"
mkdir -p "$TMP/case5-repo"
assert_exit 1 ".git artifact blocks close" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM5/LANES.md" --memory-dir "$MEM5" \
    --repo-root "$TMP/case5-repo" --tmp-dir "$TMPDIR5"

# ---- dry-run path ----
echo
echo "[dry-run]"
MEM6="$TMP/case6"; mkdir -p "$MEM6"; write_fixture_lanes "$MEM6/LANES.md"
mkdir -p "$TMP/case6-repo"
OUT6=$("$SCRIPT" side-target --dry-run --force-cross-lane \
        --lanes-file "$MEM6/LANES.md" --memory-dir "$MEM6" \
        --repo-root "$TMP/case6-repo" 2>&1)
RC6=$?
[ "$RC6" = "0" ] && pass "dry-run exits 0" || fail "dry-run exit=$RC6: $OUT6"
TARGET6=$(awk '/^### `side-target`/{s=1; next} /^### / && s {exit} s && /^- \*\*Status\*\*:/ {print; exit}' "$MEM6/LANES.md")
case "$TARGET6" in
  *active*) pass "dry-run did not mutate LANES.md" ;;
  *) fail "dry-run mutated LANES.md: '$TARGET6'" ;;
esac

# ---- non-interactive without --yes refuses ----
echo
echo "[interactive-guard]"
MEM7="$TMP/case7"; mkdir -p "$MEM7"; write_fixture_lanes "$MEM7/LANES.md"
mkdir -p "$TMP/case7-repo"
# stdin redirected from /dev/null → not a tty; without --yes script must abort 1.
"$SCRIPT" side-target --force-cross-lane \
  --lanes-file "$MEM7/LANES.md" --memory-dir "$MEM7" \
  --repo-root "$TMP/case7-repo" </dev/null >/dev/null 2>&1
RC7=$?
[ "$RC7" = "1" ] && pass "non-interactive without --yes refuses" \
  || fail "non-interactive exit=$RC7 (expected 1)"

# ---- tmp-dir safety gate (Argus #327 finding #2 fix) ----
echo
echo "[tmp-dir-safety]"
MEM8="$TMP/case8"; mkdir -p "$MEM8"; write_fixture_lanes "$MEM8/LANES.md"
mkdir -p "$TMP/case8-repo"

# Empty / root paths
assert_exit 2 "--tmp-dir empty refused" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
    --repo-root "$TMP/case8-repo" --tmp-dir ""
assert_exit 2 "--tmp-dir / refused" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
    --repo-root "$TMP/case8-repo" --tmp-dir "/"

# Repo root itself
assert_exit 2 "--tmp-dir == repo-root refused" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
    --repo-root "$TMP/case8-repo" --tmp-dir "$TMP/case8-repo"

# Outside .tmp/ subtree (e.g., HOME-like path under repo)
mkdir -p "$TMP/case8-repo/some-other-dir"
assert_exit 2 "--tmp-dir outside .tmp/ subtree refused" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
    --repo-root "$TMP/case8-repo" --tmp-dir "$TMP/case8-repo/some-other-dir"

# Outside repo entirely
assert_exit 2 "--tmp-dir outside repo refused" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
    --repo-root "$TMP/case8-repo" --tmp-dir "$TMP/elsewhere/lane-x"

# Traversal segments (Argus #327 iter 4 Copilot fix — `..` patterns)
mkdir -p "$TMP/case8-repo/.tmp"
assert_exit 2 "--tmp-dir with trailing /.. refused" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
    --repo-root "$TMP/case8-repo" --tmp-dir "$TMP/case8-repo/.tmp/lane-x/.."
# Note: `..` and `../*` patterns under .tmp/ are typically resolved by realpath
# to a path outside .tmp/ and caught by the "outside subtree" branch instead;
# if realpath is unavailable, the literal `..`-segment guard catches them.
# Both paths exit 2 — the literal-segment guard is the belt-and-braces fallback.

# Lane-name consistency (Argus #327 iter 4/5 finding #3 fix) — refuse
# --tmp-dir whose leaf doesn't match `lane-<LANE>`. Without this, closing
# lane=foo with --tmp-dir=.tmp/lane-bar would silently delete bar's data.
mkdir -p "$TMP/case8-repo/.tmp/lane-other"
assert_exit 2 "--tmp-dir for OTHER lane refused (leaf doesn't match)" \
  "$SCRIPT" side-target --yes --force-cross-lane \
    --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
    --repo-root "$TMP/case8-repo" --tmp-dir "$TMP/case8-repo/.tmp/lane-other"
# Subdirectory under matching lane name accepted.
mkdir -p "$TMP/case8-repo/.tmp/lane-side-target/sub"
OUT_SUB=$("$SCRIPT" side-target --yes --force-cross-lane \
        --lanes-file "$MEM8/LANES.md" --memory-dir "$MEM8" \
        --repo-root "$TMP/case8-repo" --tmp-dir "$TMP/case8-repo/.tmp/lane-side-target/sub" 2>&1)
RC_SUB=$?
[ "$RC_SUB" = "0" ] && pass "--tmp-dir under lane-<LANE>/sub accepted" \
  || fail "--tmp-dir under matching lane subdir rejected: exit=$RC_SUB out=$OUT_SUB"

# Valid path under .tmp/ accepted (regression — prior happy-path covers this
# but explicit assertion documents the safe shape). Use a fresh fixture +
# repo root because the previous safety sub-tests share state and any
# accidental Status flip would poison this assertion.
MEM8B="$TMP/case8b"; mkdir -p "$MEM8B"; write_fixture_lanes "$MEM8B/LANES.md"
mkdir -p "$TMP/case8b-repo/.tmp/lane-side-target"
OUT_OK=$("$SCRIPT" side-target --yes --force-cross-lane \
        --lanes-file "$MEM8B/LANES.md" --memory-dir "$MEM8B" \
        --repo-root "$TMP/case8b-repo" --tmp-dir "$TMP/case8b-repo/.tmp/lane-side-target" 2>&1)
RC_OK=$?
[ "$RC_OK" = "0" ] && pass "valid --tmp-dir under .tmp/ accepted" \
  || fail "valid --tmp-dir rejected: exit=$RC_OK out=$OUT_OK"

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
