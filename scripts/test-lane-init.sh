#!/usr/bin/env bash
# scripts/test-lane-init.sh — offline tests for scripts/lane-init.sh (Issue #607).
# Builds throwaway memory dirs; never touches the real LANES.md. Verifies the
# generated registry is accepted by the other lane scripts. Exit 0 if all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-init: not inside a git repo" >&2; exit 2; }
SCRIPT="$REPO_ROOT/scripts/lane-init.sh"
[ -x "$SCRIPT" ] || { echo "test-lane-init: $SCRIPT missing or not executable" >&2; exit 2; }

TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-lane-init.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

PASS=0; FAIL=0
pass() { printf '  PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf '  FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }
assert_exit() {
  local expected="$1" desc="$2"; shift 2
  "$@" >/dev/null 2>&1; local actual=$?
  if [ "$actual" = "$expected" ]; then pass "$desc (exit=$actual)"
  else fail "$desc (expected=$expected got=$actual)"; fi
}

echo "[arg-validation]"
assert_exit 0 "--help" "$SCRIPT" --help
assert_exit 2 "unknown flag rejected" "$SCRIPT" --bogus
assert_exit 2 "invalid --main-harness rejected" "$SCRIPT" --memory-dir "$TMP/x" --main-harness gemini
assert_exit 2 "--main-harness without value rejected" "$SCRIPT" --memory-dir "$TMP/x" --main-harness
assert_exit 2 "empty --memory-dir rejected" "$SCRIPT" --memory-dir ""
assert_exit 2 "backtick in --main-worktree rejected" "$SCRIPT" --memory-dir "$TMP/x" --main-worktree '/a`b'
assert_exit 2 "relative --main-worktree rejected" "$SCRIPT" --memory-dir "$TMP/x" --main-worktree .
assert_exit 2 "newline in --main-worktree rejected" "$SCRIPT" --memory-dir "$TMP/x" --main-worktree "$(printf '/a\nb')"
: > "$TMP/notadir"
assert_exit 2 "memory dir that is a file rejected" "$SCRIPT" --memory-dir "$TMP/notadir" --main-worktree /abs
assert_exit 2 "memory dir that is a file rejected in --dry-run too" "$SCRIPT" --memory-dir "$TMP/notadir" --main-worktree /abs --dry-run
[ ! -e "$TMP/x/LANES.md" ] && pass "rejected runs wrote nothing" || fail "rejected run created LANES.md"

echo
echo "[dry-run]"
OUT=$("$SCRIPT" --memory-dir "$TMP/dry" --main-worktree "$TMP/wt" --dry-run 2>&1); RC=$?
[ "$RC" = "0" ] && pass "--dry-run exit 0" || fail "--dry-run exit=$RC"
case "$OUT" in *"## Active Lanes"*) pass "--dry-run prints the skeleton" ;; *) fail "--dry-run output: $OUT" ;; esac
[ ! -e "$TMP/dry" ] && pass "--dry-run creates nothing" || fail "--dry-run created files"

echo
echo "[create]"
MEM="$TMP/fresh/memory"; WT="$TMP/wt"; mkdir -p "$WT"
OUT=$("$SCRIPT" --memory-dir "$MEM" --main-worktree "$WT" --main-harness codex 2>&1); RC=$?
[ "$RC" = "0" ] && pass "create exit 0 (memory dir created)" || fail "create exit=$RC out=$OUT"
L="$MEM/LANES.md"
[ -f "$L" ] && pass "LANES.md written" || fail "LANES.md missing"
C=$(cat "$L" 2>/dev/null)
for needle in '## Active Lanes' '### `main` (default lane)' '- **Harness**: `codex`' \
              "- **Worktree path**: \`$WT\`" '- **Status**: `active`' '## Closed Lanes'; do
  case "$C" in *"$needle"*) pass "contains: $needle" ;; *) fail "missing: $needle" ;; esac
done
OUTD=$("$SCRIPT" --memory-dir "$TMP/def" --main-worktree "$WT" 2>&1)
case "$(cat "$TMP/def/LANES.md" 2>/dev/null)" in
  *'- **Harness**: `claude`'*) pass "default main harness is claude" ;;
  *) fail "default harness not claude: $OUTD" ;;
esac

echo
echo "[idempotent]"
printf '\n<!-- sentinel -->\n' >> "$L"
BEFORE=$(cat "$L")
OUT=$("$SCRIPT" --memory-dir "$MEM" --main-worktree "$WT" --main-harness claude 2>&1); RC=$?
[ "$RC" = "0" ] && pass "re-run exit 0" || fail "re-run exit=$RC"
[ "$(cat "$L")" = "$BEFORE" ] && pass "existing LANES.md left unchanged" || fail "existing LANES.md was modified"
case "$OUT" in *"already exists"*) pass "re-run says it already exists" ;; *) fail "re-run output: $OUT" ;; esac

echo
echo "[accepted-by-other-lane-scripts]"
MEM2="$TMP/accept"
"$SCRIPT" --memory-dir "$MEM2" --main-worktree "$WT" >/dev/null 2>&1
OUT=$(BOOTSTRAP_PROMPT='[LANE=main] hi' bash "$REPO_ROOT/scripts/lane-assertion.sh" \
        --memory-dir "$MEM2" --cwd "$WT" --branch develop 2>&1); RC=$?
[ "$RC" = "0" ] && pass "lane-assertion accepts main lane on develop" || fail "lane-assertion exit=$RC out=$OUT"
OUT=$(bash "$REPO_ROOT/scripts/lane-cap-check.sh" --memory-dir "$MEM2" --format json 2>&1); RC=$?
case "$RC:$OUT" in "0:"*'"active_count":1'*) pass "lane-cap-check counts the main lane" ;; *) fail "lane-cap-check: rc=$RC $OUT" ;; esac
mkdir -p "$TMP/repo"
OUT=$(bash "$REPO_ROOT/scripts/lane-spawn.sh" art 612 --short art --slug x --harness codex \
        --no-claim --no-branch --dry-run --memory-dir "$MEM2" --repo-root "$TMP/repo" 2>&1); RC=$?
[ "$RC" = "0" ] && pass "lane-spawn --dry-run accepts the registry" || fail "lane-spawn dry-run rc=$RC out=$OUT"
OUT=$(bash "$REPO_ROOT/scripts/lane-spawn.sh" art 612 --short art --slug x --harness codex \
        --no-claim --no-branch --yes --memory-dir "$MEM2" --repo-root "$TMP/repo" 2>&1); RC=$?
case "$RC:$(cat "$MEM2/LANES.md")" in
  "0:"*'### `art`'*) pass "lane-spawn appends a lane to the generated registry" ;;
  *) fail "lane-spawn real run rc=$RC out=$OUT" ;;
esac

echo
echo "[refuse-non-regular]"
MS="$TMP/sym"; mkdir -p "$MS"
ln -s "$MS/elsewhere.md" "$MS/LANES.md"
assert_exit 2 "dangling LANES.md symlink refused" "$SCRIPT" --memory-dir "$MS" --main-worktree "$WT"
[ ! -e "$MS/elsewhere.md" ] && pass "symlink target not created" || fail "wrote through dangling symlink"
MS2="$TMP/sym2"; mkdir -p "$MS2"; printf 'real\n' > "$MS2/real.md"; ln -s "$MS2/real.md" "$MS2/LANES.md"
assert_exit 2 "symlink to an existing file refused" "$SCRIPT" --memory-dir "$MS2" --main-worktree "$WT"
[ "$(cat "$MS2/real.md")" = "real" ] && pass "symlink target content untouched" || fail "symlink target modified"
MD="$TMP/dirl"; mkdir -p "$MD/LANES.md"
assert_exit 2 "LANES.md that is a directory refused" "$SCRIPT" --memory-dir "$MD" --main-worktree "$WT"

echo
echo "[paths]"
MP="$TMP/paths"; ODD='/tmp/My Dir/$HOME/(x86)'
OUT=$("$SCRIPT" --memory-dir "$MP" --main-worktree "$ODD" 2>&1); RC=$?
[ "$RC" = "0" ] && pass "odd absolute path accepted" || fail "odd path rc=$RC out=$OUT"
case "$(cat "$MP/LANES.md" 2>/dev/null)" in
  *"- **Worktree path**: \`$ODD\`"*) pass "odd path stored verbatim (spaces, \$, parens)" ;;
  *) fail "odd path mangled" ;;
esac
case "$OUT" in *"WARN"*"does not exist"*) pass "missing worktree path warns" ;; *) fail "no warning for missing path: $OUT" ;; esac
MW="$TMP/win"
OUT=$("$SCRIPT" --memory-dir "$MW" --main-worktree 'D:\Mercury\Mercury' 2>&1); RC=$?
case "$RC:$(cat "$MW/LANES.md" 2>/dev/null)" in
  "0:"*'- **Worktree path**: `D:\Mercury\Mercury`'*) pass "Windows backslash path stored verbatim" ;;
  *) fail "Windows path rc=$RC out=$OUT" ;;
esac

echo
echo "[default-main-worktree]"
if command -v git >/dev/null 2>&1; then
  R="$TMP/r"; git init -q -b develop "$R" 2>/dev/null || { git init -q "$R"; git -C "$R" checkout -q -b develop; }
  git -C "$R" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  git -C "$R" worktree add -q -b lane/x/init "$TMP/r-lane" >/dev/null 2>&1
  MAINP=$(git -C "$R" worktree list --porcelain | sed -n '1s/^worktree //p')
  (cd "$TMP/r-lane" && "$SCRIPT" --memory-dir "$TMP/dmw" >/dev/null 2>&1)
  case "$(cat "$TMP/dmw/LANES.md" 2>/dev/null)" in
    *"- **Worktree path**: \`$MAINP\`"*) pass "run from a linked worktree records the MAIN checkout" ;;
    *) fail "default main worktree wrong: $(grep 'Worktree path' "$TMP/dmw/LANES.md" 2>/dev/null)" ;;
  esac
  (cd "$TMP" && MERCURY_MEMORY_DIR="$TMP/envmem" "$SCRIPT" --main-worktree "$WT" >/dev/null 2>&1)
  [ -f "$TMP/envmem/LANES.md" ] && pass "MERCURY_MEMORY_DIR honored as default" || fail "MERCURY_MEMORY_DIR ignored"
fi

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
