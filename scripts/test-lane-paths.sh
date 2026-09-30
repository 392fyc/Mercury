#!/usr/bin/env bash
# scripts/test-lane-paths.sh — offline tests for the per-project lane home
# resolver (scripts/lib/lane-paths.sh + scripts/lane-paths.sh; Issue #613).
# Builds throwaway git projects; never touches the real ~/.claude. Exit 0 if
# all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-paths: not inside a git repo" >&2; exit 2; }
CLI="$REPO_ROOT/scripts/lane-paths.sh"
[ -x "$CLI" ] || { echo "test-lane-paths: $CLI missing or not executable" >&2; exit 2; }

TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-lane-paths.XXXXXX")
trap 'rm -rf "$TMP"' EXIT
export CLAUDE_CONFIG_DIR="$TMP/cfg"
unset MERCURY_MEMORY_DIR

PASS=0; FAIL=0
pass() { printf '  PASS: %s\n' "$1"; PASS=$((PASS + 1)); }
fail() { printf '  FAIL: %s\n' "$1"; FAIL=$((FAIL + 1)); }
eq() { if [ "$2" = "$3" ]; then pass "$1"; else fail "$1 (got '$2', want '$3')"; fi; }

mkproj() {  # mkproj <dir> — git repo with one commit on develop
  git init -q "$1" 2>/dev/null
  git -C "$1" checkout -q -b develop 2>/dev/null
  git -C "$1" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
}
enc() { printf '%s' "$1" | LC_ALL=C sed 's/[^A-Za-z0-9]/-/g'; }

A="$TMP/work/Proj-A"; B="$TMP/work/Proj.B"
mkproj "$A"; mkproj "$B"
git -C "$A" worktree add -q -b lane/art/init "$TMP/work/Proj-A-art" >/dev/null 2>&1
AW="$TMP/work/Proj-A-art"

echo "[memory-dir: one lane home per project]"
MA=$("$CLI" memory-dir --repo-root "$A"); MB=$("$CLI" memory-dir --repo-root "$B")
eq "project A memory dir" "$MA" "$CLAUDE_CONFIG_DIR/projects/$(enc "$A")/memory"
eq "project B memory dir" "$MB" "$CLAUDE_CONFIG_DIR/projects/$(enc "$B")/memory"
[ "$MA" != "$MB" ] && pass "distinct projects get distinct registries" || fail "projects collide"
eq "linked lane worktree resolves to its project's main lane home" "$("$CLI" memory-dir --repo-root "$AW")" "$MA"
eq "cwd default (run inside the lane worktree)" "$(cd "$AW" && "$CLI" memory-dir)" "$MA"
eq "lanes-file" "$("$CLI" lanes-file --repo-root "$AW")" "$MA/LANES.md"
eq "main-worktree from a lane worktree" "$("$CLI" main-worktree --repo-root "$AW")" "$A"
eq "encoding matches Claude Code (D:/Mercury/Mercury)" \
   "$(bash -c ". '$REPO_ROOT/scripts/lib/lane-paths.sh'; lane_encode_project_dir 'D:/Mercury/Mercury'")" "D--Mercury-Mercury"
eq "MERCURY_MEMORY_DIR global override" "$(MERCURY_MEMORY_DIR=/x/y "$CLI" memory-dir --repo-root "$A")" "/x/y"

echo
echo "[handoff-dir: unified per project]"
eq "no .handoff-config -> <main>/.handoff" "$("$CLI" handoff-dir --repo-root "$AW")" "$A/.handoff"
KB="$TMP/My KB"; mkdir -p "$KB"
printf 'kb_dir = "%s"  \n' "$KB" > "$A/.handoff-config"
eq "main checkout .handoff-config kb_dir (quotes, spaces)" "$("$CLI" handoff-dir --repo-root "$AW")" "$KB/handoff"
printf 'kb_dir=%s\n' "$TMP/elsewhere" > "$AW/.handoff-config"; mkdir -p "$TMP/elsewhere"
eq "a lane worktree's own .handoff-config is ignored (main wins)" "$("$CLI" handoff-dir --repo-root "$AW")" "$KB/handoff"
printf 'kb_dir=%s\n' "$TMP/missing-kb" > "$B/.handoff-config"
eq "kb_dir that does not exist falls back to <main>/.handoff" "$("$CLI" handoff-dir --repo-root "$B")" "$B/.handoff"
eq "handoff-file main" "$("$CLI" handoff-file --repo-root "$AW")" "$KB/handoff/session-handoff.md"
eq "handoff-file lane" "$("$CLI" handoff-file --lane art --repo-root "$AW")" "$KB/handoff/session-handoff-art.md"

echo
echo "[errors]"
mkdir -p "$TMP/plain"
"$CLI" memory-dir --repo-root "$TMP/plain" >/dev/null 2>&1; eq "not a checkout -> exit 2" "$?" "2"
"$CLI" bogus --repo-root "$A" >/dev/null 2>&1; eq "unknown <what> -> exit 2" "$?" "2"
"$CLI" handoff-file --lane 'a b' --repo-root "$A" >/dev/null 2>&1; eq "bad lane name -> exit 2" "$?" "2"

echo
echo "[lane scripts use the project lane home]"
mkdir -p "$MA"
(cd "$AW" && bash "$REPO_ROOT/scripts/lane-init.sh" >/dev/null 2>&1)
[ -f "$MA/LANES.md" ] && pass "lane-init (run in a lane worktree) writes the project's LANES.md" || fail "lane-init wrote elsewhere"
OUT=$(bash "$REPO_ROOT/scripts/lane-spawn.sh" talent 7 --short talent --slug x --no-claim --no-branch --yes --repo-root "$AW" 2>&1); RC=$?
eq "lane-spawn exit 0 with default paths" "$RC" "0"
[ -f "$KB/handoff/session-handoff-talent.md" ] && pass "lane-spawn writes the handoff template to the unified handoff dir" \
  || fail "handoff not in unified dir: $OUT"
[ ! -e "$MA/session-handoff-talent.md" ] && pass "no handoff written to the memory dir" || fail "handoff also in memory dir"
case "$(cat "$MA/LANES.md")" in *'### `talent`'*) pass "lane-spawn appended to the project's LANES.md" ;; *) fail "LANES.md not updated" ;; esac
[ ! -e "$MB/LANES.md" ] && pass "other project's registry untouched" || fail "project B registry touched"
OUT=$(bash "$REPO_ROOT/scripts/lane-sweep.sh" --repo-root "$AW" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"lane":"talent","branch_age_days":'*'"handoff_age_days":"0"'*) pass "lane-sweep reads the unified handoff (age 0)" ;;
  *) fail "lane-sweep handoff age: $OUT" ;;
esac
touch "$KB/handoff/session-handoff.md"
OUT=$(bash "$REPO_ROOT/scripts/check-main-idle.sh" --repo-root "$AW" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"handoff_age_hours":"0"'*) pass "check-main-idle reads the unified main handoff" ;;
  *) fail "check-main-idle handoff age: $OUT" ;;
esac
rm -f "$KB/handoff/session-handoff.md"; touch "$MA/session-handoff.md"
OUT=$(bash "$REPO_ROOT/scripts/check-main-idle.sh" --repo-root "$AW" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"handoff_age_hours":"0"'*) pass "legacy memory-dir handoff still read as fallback" ;;
  *) fail "legacy fallback: $OUT" ;;
esac

touch "$KB/handoff/session-handoff.md"; FIX="$TMP/fixture-mem"; mkdir -p "$FIX"; cp "$MA/LANES.md" "$FIX/LANES.md"
OUT=$(bash "$REPO_ROOT/scripts/check-main-idle.sh" --repo-root "$AW" --memory-dir "$FIX" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"handoff_age_hours":"0"'*) fail "explicit --memory-dir still mixed in the project's handoff: $OUT" ;;
  *) pass "explicit --memory-dir does not read the project's handoff dir" ;;
esac
touch "$MA/session-handoff-legacy.md"
bash "$REPO_ROOT/scripts/lane-spawn.sh" legacy 8 --short legacy --slug x --no-claim --no-branch --yes --repo-root "$AW" >/dev/null 2>&1
eq "lane-spawn refuses a lane whose legacy memory-dir handoff exists" "$?" "1"

ln -s "$TMP/outside-target.md" "$KB/handoff/session-handoff-dangle.md"
bash "$REPO_ROOT/scripts/lane-spawn.sh" dangle 9 --short dangle --slug x --no-claim --no-branch --yes --repo-root "$AW" >/dev/null 2>&1
eq "lane-spawn refuses a dangling symlink at the handoff path" "$?" "1"
[ ! -e "$TMP/outside-target.md" ] && pass "nothing written through the dangling symlink" || fail "wrote through symlink"

echo
echo "[lane-init --main-worktree picks that project's lane home]"
MC="$CLAUDE_CONFIG_DIR/projects/$(enc "$TMP/work/Proj-C")/memory"
(cd "$TMP/plain" && bash "$REPO_ROOT/scripts/lane-init.sh" --main-worktree "$TMP/work/Proj-C" >/dev/null 2>&1)
eq "outside a checkout with --main-worktree -> exit 0" "$?" "0"
[ -f "$MC/LANES.md" ] && pass "writes the lane home of --main-worktree" || fail "no LANES.md at $MC"
rm -rf "$MC"
(cd "$A" && bash "$REPO_ROOT/scripts/lane-init.sh" --main-worktree "$TMP/work/Proj-C" >/dev/null 2>&1)
[ -f "$MC/LANES.md" ] && pass "run in project A with --main-worktree C writes C's lane home, not A's" || fail "lane-init used cwd project"
eq "non-ASCII path encodes bytewise regardless of locale" \
   "$(LC_ALL=C.UTF-8 bash -c ". '$REPO_ROOT/scripts/lib/lane-paths.sh'; lane_encode_project_dir \"/\$(printf '\\351')x\"")" "--x"

echo
echo "[lossy encoding: colliding projects get distinct registries]"
AD="$TMP/work/Proj.A"; mkproj "$AD"
MAD=$("$CLI" memory-dir --repo-root "$AD")
[ "$MAD" != "$MA" ] && pass "Proj.A does not reuse Proj-A's registry" || fail "Proj.A collides with Proj-A ($MAD)"
case "$MAD" in "$CLAUDE_CONFIG_DIR/projects/$(enc "$AD")-lh"*/memory) pass "collision falls back to a hashed sibling dir" ;; *) fail "unexpected dir $MAD" ;; esac
(cd "$AD" && bash "$REPO_ROOT/scripts/lane-init.sh" >/dev/null 2>&1)
[ -f "$MAD/LANES.md" ] && pass "lane-init in Proj.A writes its own registry" || fail "lane-init did not write $MAD/LANES.md"
eq "Proj-A still resolves the plain dir" "$("$CLI" memory-dir --repo-root "$A")" "$MA"
eq "Proj.A resolves the same hashed dir again" "$("$CLI" memory-dir --repo-root "$AD")" "$MAD"
LEG="$TMP/legacy-cfg"; mkdir -p "$LEG/projects/-x-Proj/memory"
printf '## Active Lanes\n\n### `main`\n\n- **Worktree path**: `\\x\\Proj\\`\n' > "$LEG/projects/-x-Proj/memory/LANES.md"
eq "same checkout written with backslashes is not a collision" \
   "$(CLAUDE_CONFIG_DIR="$LEG" bash -c ". '$REPO_ROOT/scripts/lib/lane-paths.sh'; lane_memory_dir_for_main /x/Proj")" "$LEG/projects/-x-Proj/memory"
printf '## Active Lanes\n\n### `main`\n\n- **Worktree path**: `/d/Mercury/Mercury`\n' > "$LEG/projects/-x-Proj/memory/LANES.md"
mkdir -p "$LEG/projects/D--Mercury-Mercury/memory"; cp "$LEG/projects/-x-Proj/memory/LANES.md" "$LEG/projects/D--Mercury-Mercury/memory/"
eq "MSYS-form record of D:/Mercury/Mercury is the same checkout" \
   "$(CLAUDE_CONFIG_DIR="$LEG" bash -c ". '$REPO_ROOT/scripts/lib/lane-paths.sh'; lane_memory_dir_for_main D:/Mercury/Mercury")" "$LEG/projects/D--Mercury-Mercury/memory"

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
