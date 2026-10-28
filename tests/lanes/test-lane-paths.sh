#!/usr/bin/env bash
# tests/lanes/test-lane-paths.sh — offline tests for the per-project lane home
# resolver (.mercury/packs/lanes/scripts/lib/lane-paths.sh + .mercury/packs/lanes/scripts/lane-paths.sh; Issue #613).
# Builds throwaway git projects; never touches the real ~/.claude. Exit 0 if
# all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-paths: not inside a git repo" >&2; exit 2; }
CLI="$REPO_ROOT/.mercury/packs/lanes/scripts/lane-paths.sh"
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
   "$(bash -c ". '$REPO_ROOT/.mercury/packs/lanes/scripts/lib/lane-paths.sh'; lane_encode_project_dir 'D:/Mercury/Mercury'")" "D--Mercury-Mercury"
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
(cd "$AW" && bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-init.sh" >/dev/null 2>&1)
[ -f "$MA/LANES.md" ] && pass "lane-init (run in a lane worktree) writes the project's LANES.md" || fail "lane-init wrote elsewhere"
OUT=$(bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-spawn.sh" talent 7 --short talent --slug x --no-claim --no-branch --yes --repo-root "$AW" 2>&1); RC=$?
eq "lane-spawn exit 0 with default paths" "$RC" "0"
[ -f "$KB/handoff/session-handoff-talent.md" ] && pass "lane-spawn writes the handoff template to the unified handoff dir" \
  || fail "handoff not in unified dir: $OUT"
[ ! -e "$MA/session-handoff-talent.md" ] && pass "no handoff written to the memory dir" || fail "handoff also in memory dir"
case "$(cat "$MA/LANES.md")" in *'### `talent`'*) pass "lane-spawn appended to the project's LANES.md" ;; *) fail "LANES.md not updated" ;; esac
[ ! -e "$MB/LANES.md" ] && pass "other project's registry untouched" || fail "project B registry touched"
OUT=$(bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-sweep.sh" --repo-root "$AW" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"lane":"talent","branch_age_days":'*'"handoff_age_days":"0"'*) pass "lane-sweep reads the unified handoff (age 0)" ;;
  *) fail "lane-sweep handoff age: $OUT" ;;
esac
touch "$KB/handoff/session-handoff.md"
OUT=$(bash "$REPO_ROOT/.mercury/packs/lanes/scripts/check-main-idle.sh" --repo-root "$AW" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"handoff_age_hours":"0"'*) pass "check-main-idle reads the unified main handoff" ;;
  *) fail "check-main-idle handoff age: $OUT" ;;
esac
rm -f "$KB/handoff/session-handoff.md"; touch "$MA/session-handoff.md"
OUT=$(bash "$REPO_ROOT/.mercury/packs/lanes/scripts/check-main-idle.sh" --repo-root "$AW" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"handoff_age_hours":"0"'*) pass "legacy memory-dir handoff still read as fallback" ;;
  *) fail "legacy fallback: $OUT" ;;
esac

touch "$KB/handoff/session-handoff.md"; FIX="$TMP/fixture-mem"; mkdir -p "$FIX"; cp "$MA/LANES.md" "$FIX/LANES.md"
OUT=$(bash "$REPO_ROOT/.mercury/packs/lanes/scripts/check-main-idle.sh" --repo-root "$AW" --memory-dir "$FIX" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"handoff_age_hours":"0"'*) fail "explicit --memory-dir still mixed in the project's handoff: $OUT" ;;
  *) pass "explicit --memory-dir does not read the project's handoff dir" ;;
esac
touch "$MA/session-handoff-legacy.md"
bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-spawn.sh" legacy 8 --short legacy --slug x --no-claim --no-branch --yes --repo-root "$AW" >/dev/null 2>&1
eq "lane-spawn refuses a lane whose legacy memory-dir handoff exists" "$?" "1"

ln -s "$TMP/outside-target.md" "$KB/handoff/session-handoff-dangle.md"
bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-spawn.sh" dangle 9 --short dangle --slug x --no-claim --no-branch --yes --repo-root "$AW" >/dev/null 2>&1
eq "lane-spawn refuses a dangling symlink at the handoff path" "$?" "1"
[ ! -e "$TMP/outside-target.md" ] && pass "nothing written through the dangling symlink" || fail "wrote through symlink"

git -C "$A" update-ref refs/remotes/origin/master HEAD  # lets step 2 create a branch (#703: base = resolved integration branch)
export MERCURY_BASE_BRANCH=master
RO="$TMP/ro-parent"; mkdir -p "$RO"; chmod 555 "$RO"
if [ ! -w "$RO" ]; then  # root ignores permissions; skip there
  OUT=$(bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-spawn.sh" rotest 10 --short rotest --slug x --no-claim --yes \
        --handoff-dir "$RO/handoff" --repo-root "$AW" 2>&1); RC=$?
  eq "unwritable handoff dir -> spawn fails" "$RC" "1"
  git -C "$AW" rev-parse --verify --quiet refs/heads/feature/10-x-rotest-claude >/dev/null \
    && fail "branch created before the handoff dir check" || pass "no branch created when the handoff dir is unusable"
else
  HD_FILE="$TMP/hd-file"; : > "$HD_FILE"
  OUT=$(bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-spawn.sh" rotest 10 --short rotest --slug x --no-claim --yes \
        --handoff-dir "$HD_FILE" --repo-root "$AW" 2>&1); RC=$?
  eq "handoff dir path is a file -> spawn fails" "$RC" "1"
  git -C "$AW" rev-parse --verify --quiet refs/heads/feature/10-x-rotest-claude >/dev/null \
    && fail "branch created before the handoff dir check" || pass "no branch created when the handoff dir is unusable"
fi
chmod 755 "$RO"
unset MERCURY_BASE_BRANCH

echo
echo "[lane-init --main-worktree picks that project's lane home]"
MC="$CLAUDE_CONFIG_DIR/projects/$(enc "$TMP/work/Proj-C")/memory"
(cd "$TMP/plain" && bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-init.sh" --main-worktree "$TMP/work/Proj-C" >/dev/null 2>&1)
eq "outside a checkout with --main-worktree -> exit 0" "$?" "0"
[ -f "$MC/LANES.md" ] && pass "writes the lane home of --main-worktree" || fail "no LANES.md at $MC"
rm -rf "$MC"
(cd "$A" && bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-init.sh" --main-worktree "$TMP/work/Proj-C" >/dev/null 2>&1)
[ -f "$MC/LANES.md" ] && pass "run in project A with --main-worktree C writes C's lane home, not A's" || fail "lane-init used cwd project"
eq "non-ASCII path encodes bytewise regardless of locale" \
   "$(LC_ALL=C.UTF-8 bash -c ". '$REPO_ROOT/.mercury/packs/lanes/scripts/lib/lane-paths.sh'; lane_encode_project_dir \"/\$(printf '\\351')x\"")" "--x"

echo
echo "[lossy encoding: colliding projects get distinct registries]"
AD="$TMP/work/Proj.A"; mkproj "$AD"
MAD=$("$CLI" memory-dir --repo-root "$AD")
[ "$MAD" != "$MA" ] && pass "Proj.A does not reuse Proj-A's registry" || fail "Proj.A collides with Proj-A ($MAD)"
case "$MAD" in "$CLAUDE_CONFIG_DIR/projects/$(enc "$AD")-lh"*/memory) pass "collision falls back to a hashed sibling dir" ;; *) fail "unexpected dir $MAD" ;; esac
(cd "$AD" && bash "$REPO_ROOT/.mercury/packs/lanes/scripts/lane-init.sh" >/dev/null 2>&1)
[ -f "$MAD/LANES.md" ] && pass "lane-init in Proj.A writes its own registry" || fail "lane-init did not write $MAD/LANES.md"
eq "Proj-A still resolves the plain dir" "$("$CLI" memory-dir --repo-root "$A")" "$MA"
eq "Proj.A resolves the same hashed dir again" "$("$CLI" memory-dir --repo-root "$AD")" "$MAD"
# The lane-handoff skill's inline Step 5 fallback (projects without these scripts)
# must resolve the same registry as the resolver, collisions included.
awk '/^# Cross-repo lanes \(host repo/{f=1} f{print} /^fi$/ && f{exit}' \
  "$REPO_ROOT/.mercury/packs/lanes/skills/lane-handoff/SKILL.md" > "$TMP/step5.sh"
printf 'printf %%s "$LANES_FILE"\n' >> "$TMP/step5.sh"
eq "skill Step 5 fallback = resolver (collision case)" "$(cd "$AD" && bash "$TMP/step5.sh")" "$MAD/LANES.md"
eq "skill Step 5 fallback = resolver (plain case)" "$(cd "$AW" && bash "$TMP/step5.sh")" "$MA/LANES.md"
LEG="$TMP/legacy-cfg"; mkdir -p "$LEG/projects/-x-Proj/memory"
printf '## Active Lanes\n\n### `main`\n\n- **Worktree path**: `\\x\\Proj\\`\n' > "$LEG/projects/-x-Proj/memory/LANES.md"
eq "same checkout written with backslashes is not a collision" \
   "$(CLAUDE_CONFIG_DIR="$LEG" bash -c ". '$REPO_ROOT/.mercury/packs/lanes/scripts/lib/lane-paths.sh'; lane_memory_dir_for_main /x/Proj")" "$LEG/projects/-x-Proj/memory"
printf '## Active Lanes\n\n### `main`\n\n- **Worktree path**: `/d/Mercury/Mercury`\n' > "$LEG/projects/-x-Proj/memory/LANES.md"
mkdir -p "$LEG/projects/D--Mercury-Mercury/memory"; cp "$LEG/projects/-x-Proj/memory/LANES.md" "$LEG/projects/D--Mercury-Mercury/memory/"
eq "MSYS-form record of D:/Mercury/Mercury is the same checkout" \
   "$(CLAUDE_CONFIG_DIR="$LEG" bash -c ". '$REPO_ROOT/.mercury/packs/lanes/scripts/lib/lane-paths.sh'; lane_memory_dir_for_main D:/Mercury/Mercury")" "$LEG/projects/D--Mercury-Mercury/memory"

echo
echo "[project binding: .mercury/memory/lane-home (#644)]"
P="$TMP/work/Proj-P"; mkproj "$P"
git -C "$P" worktree add -q -b lane/p/init "$TMP/work/Proj-P-lane" >/dev/null 2>&1
PW="$TMP/work/Proj-P-lane"
mkdir -p "$P/.mercury/memory/projects/D--Legacy-Id"
printf 'projects/D--Legacy-Id\n' > "$P/.mercury/memory/lane-home"
MP=$("$CLI" main-worktree --repo-root "$P")  # host path form (C:/... on Git for Windows)
eq "binding selects the bound bucket" "$("$CLI" memory-dir --repo-root "$P")" "$MP/.mercury/memory/projects/D--Legacy-Id"
eq "binding is read from the main checkout by a linked worktree" \
   "$("$CLI" lanes-file --repo-root "$PW")" "$MP/.mercury/memory/projects/D--Legacy-Id/LANES.md"
printf 'projects/D--Legacy-Id/\r\n' > "$P/.mercury/memory/lane-home"
eq "trailing slash and CRLF are tolerated" "$("$CLI" memory-dir --repo-root "$P")" "$MP/.mercury/memory/projects/D--Legacy-Id"
eq "MERCURY_MEMORY_DIR still wins over the binding" \
   "$(MERCURY_MEMORY_DIR=/x/y "$CLI" memory-dir --repo-root "$P")" "/x/y"
for bad in '../escape' '/abs/path' 'C:/abs' 'projects\D--Legacy-Id' 'projects/missing' '' '.' './projects/D--Legacy-Id'; do
  printf '%s\n' "$bad" > "$P/.mercury/memory/lane-home"
  if out=$("$CLI" memory-dir --repo-root "$P" 2>/dev/null); then
    fail "invalid binding '$bad' must not resolve (got '$out')"
  else
    pass "invalid binding '$bad' is an error, not a silent fallback"
  fi
done
rm -f "$P/.mercury/memory/lane-home"
eq "no binding file keeps the Claude Code project dir" \
   "$("$CLI" memory-dir --repo-root "$P")" "$CLAUDE_CONFIG_DIR/projects/$(enc "$MP")/memory"

# ---- #703: branch naming + integration branch helpers in the lib ----
echo
echo "[branch-naming-lib]"
LIB="$REPO_ROOT/.mercury/packs/lanes/scripts/lib/lane-paths.sh"
# shellcheck source=/dev/null
. "$LIB"
ok_fmt() { lane_branch_format_ok "$1" && echo yes || echo no; }
eq "format: feature/621-guard-test-guard-codex"  "$(ok_fmt feature/621-guard-test-guard-codex)" yes
eq "format: fix/TASK-12-claude"                  "$(ok_fmt fix/TASK-12-claude)" yes
eq "format: dots and underscores in purpose"     "$(ok_fmt chore/a.b_c-1)" yes
eq "format: very long purpose (no cap)"          "$(ok_fmt feature/$(printf 'a%.0s' $(seq 1 120)))" yes
eq "format: unknown type rejected"               "$(ok_fmt wip/foo)" no
eq "format: lane/ prefix rejected"               "$(ok_fmt lane/art/12-x)" no
eq "format: codex/ prefix rejected"              "$(ok_fmt codex/foo)" no
eq "format: claude/ prefix rejected"             "$(ok_fmt claude/foo)" no
eq "format: mercury/ namespace not generated"    "$(ok_fmt mercury/update)" no
eq "format: slash inside purpose rejected"       "$(ok_fmt feature/a/b)" no
eq "format: purpose must start alnum"            "$(ok_fmt feature/-a)" no
eq "format: empty purpose rejected"              "$(ok_fmt feature/)" no
eq "format: space rejected"                      "$(ok_fmt 'feature/a b')" no
eq "owner: lane then agent suffix"   "$(lane_branch_owner feature/621-guard-test-guard-codex guard art)" guard
eq "owner: lane only"                "$(lane_branch_owner fix/1-x-art art guard)" art
eq "owner: longest wins"             "$(lane_branch_owner feature/7-x-side-mlane-claude mlane side-mlane)" side-mlane
eq "owner: shorter name when only it matches" "$(lane_branch_owner feature/7-x-mlane mlane side-mlane)" mlane
eq "owner: art is not artist"        "$(lane_branch_owner feature/7-x-artist-codex art)" ""
eq "owner: no registered suffix"     "$(lane_branch_owner fix/TASK-12-claude guard art)" ""
eq "owner: name must follow a hyphen" "$(lane_branch_owner feature/xguard guard)" ""

REGF="$TMP/LANES-naming.md"
cat > "$REGF" <<'EOF'
## Active Lanes

### `main`

- **Status**: `active`

### `side-multi-lane`

- **Short name**: `side-mlane`

### `guard`

- **Short name** (note): `guard`
EOF
eq "registered names: short when present, else lane name" \
   "$(lane_registered_names "$REGF" | tr '\n' ' ')" "main side-mlane guard "
eq "short name lookup"          "$(lane_short_name "$REGF" side-multi-lane)" side-mlane
eq "short name falls back to lane name" "$(lane_short_name "$REGF" main)" main

# Integration branch: env > .mercury/config.json > origin/HEAD > error
IB="$TMP/integ"; mkproj "$IB"; mkdir -p "$IB/.mercury"
rc_none=0; (unset MERCURY_BASE_BRANCH; lane_integration_branch "$IB" >/dev/null 2>&1) || rc_none=$?
eq "integration branch: nothing resolves -> error" "$rc_none" "1"
git -C "$IB" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/mainline 2>/dev/null
eq "integration branch: origin/HEAD" "$(unset MERCURY_BASE_BRANCH; lane_integration_branch "$IB")" mainline
if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
  printf '{"params":{"integration_branch":"fromcfg"}}\n' > "$IB/.mercury/config.json"
  eq "integration branch: config.json beats origin/HEAD" "$(unset MERCURY_BASE_BRANCH; lane_integration_branch "$IB")" fromcfg
  printf '{"params":{}}\n' > "$IB/.mercury/config.json"
  eq "integration branch: config without the key falls through" "$(unset MERCURY_BASE_BRANCH; lane_integration_branch "$IB")" mainline
  printf '{"params":{"integration_branch":"fromcfg"}}\n' > "$IB/.mercury/config.json"
else
  pass "integration branch: config.json cases skipped (no python)"
fi
eq "integration branch: env var beats everything" "$(MERCURY_BASE_BRANCH=fromenv lane_integration_branch "$IB")" fromenv

# lane_branch_last_ts / check-main-idle over a throwaway repo
BR="$TMP/branches"; mkproj "$BR"
commit_on() {  # commit_on <branch> <iso date>
  git -C "$BR" checkout -q -b "$1" develop 2>/dev/null || git -C "$BR" checkout -q "$1"
  GIT_COMMITTER_DATE="$2" GIT_AUTHOR_DATE="$2" git -C "$BR" -c user.email=t@t -c user.name=t commit -q --allow-empty -m "c $1"
  git -C "$BR" checkout -q develop
}
commit_on "feature/1-old-guard-codex" "2020-01-01T00:00:00Z"
commit_on "fix/2-new-guard"           "2020-06-01T00:00:00Z"
commit_on "feature/3-art-work-art-claude" "2021-01-01T00:00:00Z"
commit_on "lane/guard/4-legacy"       "2024-06-01T00:00:00Z"  # pre-#703 forms, newest: must not count
commit_on "feature/lane-guard/TASK-8-x" "2024-06-01T00:00:00Z"
commit_on "feature/5-unowned-claude"  "2023-01-01T00:00:00Z"
commit_on "feature/TASK-12-x-guard-codex" "2024-01-01T00:00:00Z"
ts() { date -u -d "$1" +%s 2>/dev/null || date -u -j -f '%Y-%m-%dT%H:%M:%SZ' "$1" +%s; }
eq "last_ts guard: newest suffix branch; pre-#703 lane/guard/, feature/lane-guard/ not counted" "$(lane_branch_last_ts "$BR" guard "$REGF")" "$(ts 2024-01-01T00:00:00Z)"
eq "last_ts main: new-format branches no lane owns (feature/TASK-* of guard not counted, #703 M1)" "$(lane_branch_last_ts "$BR" main "$REGF")" "$(ts 2023-01-01T00:00:00Z)"
eq "last_ts art (unregistered): its own suffix only"   "$(lane_branch_last_ts "$BR" art "$REGF")" "$(ts 2021-01-01T00:00:00Z)"
eq "last_ts: lane without branches -> empty"           "$(lane_branch_last_ts "$BR" nobody "$REGF")" ""
# remote-tracking refs count too
commit_on "tmp-remote-src" "2025-01-01T00:00:00Z"
git -C "$BR" update-ref refs/remotes/origin/feature/9-remote-guard-claude "$(git -C "$BR" rev-parse tmp-remote-src)"
git -C "$BR" branch -q -D tmp-remote-src
eq "last_ts: remote-tracking branch counted for its lane" "$(lane_branch_last_ts "$BR" guard "$REGF")" "$(ts 2025-01-01T00:00:00Z)"

CMI="$REPO_ROOT/.mercury/packs/lanes/scripts/check-main-idle.sh"
MEMI="$TMP/memidle"; mkdir -p "$MEMI"; cp "$REGF" "$MEMI/LANES.md"
OUT=$(bash "$CMI" --repo-root "$BR" --memory-dir "$MEMI" --no-issue-check --format json 2>&1)
case "$OUT" in
  *'"branch_age_hours":"inf"'*) fail "check-main-idle: new-format main branch not seen: $OUT" ;;
  *'"branch_age_hours":"'*) pass "check-main-idle sees main's new-format branches (feature/5-unowned-claude)" ;;
  *) fail "check-main-idle output: $OUT" ;;
esac

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
