#!/usr/bin/env bash
# tests/lanes/test-lane-spawn.sh — smoke tests for lane-spawn.sh
#
# Builds synthetic LANES.md + memory dir, exercises argument validation,
# dry-run, --no-claim/--no-branch happy path, duplicate-lane refusal,
# no lane-count cap (#605), --harness, short-name uniqueness, and handoff
# overwrite guard.
# Tests run offline (no gh / no live Issue probe). Exit 0 if all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-spawn: not inside a git repo" >&2; exit 2; }
SCRIPT="$REPO_ROOT/.mercury/packs/lanes/scripts/lane-spawn.sh"
[ -x "$SCRIPT" ] || { echo "test-lane-spawn: $SCRIPT missing or not executable" >&2; exit 2; }

# BSD/macOS mktemp requires explicit template form for portability; GNU
# tolerates either. Honor $TMPDIR for CI sandboxes.
TMP=$(mktemp -d "${TMPDIR:-/tmp}/test-lane-spawn.XXXXXX")
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

- **Short name**: `main`
- **Branch**: `develop`
- **Handoff file**: `session-handoff.md`
- **Status**: `active`

### `existing-lane`

- **Short name**: `existing`
- **Branch**: `feature/100-foo-existing-claude`
- **Handoff file**: `session-handoff-existing-lane.md`
- **Status**: `active`

## Closed Lanes

(none)
EOF
}

write_fixture_lanes_at_cap() {
  local path="$1"
  cat > "$path" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `lane1`
- **Short name**: `lane1`
- **Status**: `active`

### `lane2`
- **Short name**: `lane2`
- **Status**: `active`

### `lane3`
- **Short name**: `lane3`
- **Status**: `active`

### `lane4`
- **Short name**: `lane4`
- **Status**: `active`

### `lane5`
- **Short name**: `lane5`
- **Status**: `active`

## Closed Lanes

(none)
EOF
}

# ---- arg validation ----
echo "[arg-validation]"
assert_exit 0 "--help"                        "$SCRIPT" --help
assert_exit 2 "missing both args"             "$SCRIPT"
assert_exit 2 "missing issue arg"             "$SCRIPT" mylane
assert_exit 2 "lane name with space rejected" "$SCRIPT" "bad lane" 100 --no-claim --no-branch --yes
assert_exit 2 "lane starting with hyphen"     "$SCRIPT" -bad 100 --no-claim --no-branch --yes
assert_exit 2 "issue must be int"             "$SCRIPT" mylane abc --no-claim --no-branch --yes
assert_exit 2 "issue zero rejected"           "$SCRIPT" mylane 0 --no-claim --no-branch --yes
assert_exit 2 "unknown flag"                  "$SCRIPT" --bogus

# ---- dry-run path ----
echo
echo "[dry-run]"
MEM1="$TMP/mem1"; mkdir -p "$MEM1"; write_fixture_lanes "$MEM1/LANES.md"
mkdir -p "$TMP/repo1"

OUT1=$("$SCRIPT" newlane 200 --dry-run --no-claim --no-branch --slug "test-spawn" \
        --memory-dir "$MEM1" --lanes-file "$MEM1/LANES.md" \
        --repo-root "$TMP/repo1" 2>&1)
RC1=$?
[ "$RC1" = "0" ] && pass "dry-run exits 0" || fail "dry-run exit=$RC1: $OUT1"
case "$OUT1" in
  *"[dry-run] lane=newlane issue=200"*) pass "dry-run prints lane+issue" ;;
  *) fail "dry-run output missing lane/issue: $OUT1" ;;
esac
case "$OUT1" in
  *"branch=feature/200-test-spawn-newlane-claude "*) pass "dry-run prints branch" ;;
  *) fail "dry-run output missing branch line: $OUT1" ;;
esac
# dry-run must NOT mutate LANES.md
LANES_AFTER=$(cat "$MEM1/LANES.md")
case "$LANES_AFTER" in
  *"### \`newlane\`"*) fail "dry-run mutated LANES.md (newlane present)" ;;
  *) pass "dry-run did not mutate LANES.md" ;;
esac
[ ! -f "$MEM1/session-handoff-newlane.md" ] \
  && pass "dry-run did not write handoff" \
  || fail "dry-run wrote handoff file"

# ---- branch naming (#703) and integration branch resolution ----
echo
echo "[branch-naming]"
MEMB="$TMP/memb"; mkdir -p "$MEMB"; write_fixture_lanes "$MEMB/LANES.md"
mkdir -p "$TMP/repob"
# Long purpose: no 40-char cap any more; lane then agent as suffixes.
OUTB=$("$SCRIPT" newlane 300 --short newl --harness codex --type fix \
        --slug "a-very-long-purpose-that-used-to-be-truncated-at-forty-chars" \
        --dry-run --no-claim --no-branch --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" \
        --repo-root "$TMP/repob" 2>&1)
case "$OUTB" in
  *"branch=fix/300-a-very-long-purpose-that-used-to-be-truncated-at-forty-chars-newl-codex "*) pass "type prefix, no length cap, lane then agent suffix" ;;
  *) fail "unexpected branch line: $OUTB" ;;
esac
assert_exit 2 "invalid --type rejected" "$SCRIPT" newlane 300 --type lane --slug x --dry-run --no-claim --no-branch --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" --repo-root "$TMP/repob"
assert_exit 2 "--type claude rejected"  "$SCRIPT" newlane 300 --type claude --slug x --dry-run --no-claim --no-branch --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" --repo-root "$TMP/repob"
# The branch must belong to THIS lane: a slug ending in another lane's name is refused.
MEMO="$TMP/memo"; mkdir -p "$MEMO"
printf '# Lanes\n\n## Active Lanes\n\n### `side-multi-lane`\n\n- **Short name**: `side-mlane`\n- **Status**: `active`\n' > "$MEMO/LANES.md"
OUTO=$("$SCRIPT" mlane 302 --short mlane --slug "x-side" --dry-run --no-claim --no-branch \
        --memory-dir "$MEMO" --lanes-file "$MEMO/LANES.md" --repo-root "$TMP/repob" 2>&1); RCO=$?
case "$RCO:$OUTO" in
  2:*"belong to lane name 'side-mlane'"*) pass "slug that makes the branch another lane's is refused (exit 2)" ;;
  *) fail "expected exit 2 for ambiguous owner: $RCO $OUTO" ;;
esac
OUTO=$("$SCRIPT" mlane 302 --short mlane --slug "x-other" --dry-run --no-claim --no-branch \
        --memory-dir "$MEMO" --lanes-file "$MEMO/LANES.md" --repo-root "$TMP/repob" 2>&1); RCO=$?
[ "$RCO" = "0" ] && pass "unambiguous slug accepted" || fail "unambiguous slug refused: $RCO $OUTO"
# Base branch: no env / config / origin/HEAD -> error (never guessed).
OUTB=$(env -u MERCURY_BASE_BRANCH "$SCRIPT" newlane 301 --short newl --slug x --dry-run --no-claim \
        --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" --repo-root "$TMP/repob" 2>&1); RCB=$?
case "$RCB:$OUTB" in
  2:*"cannot resolve the integration branch"*) pass "unresolvable integration branch is an error" ;;
  *) fail "expected exit 2 + integration-branch error: $RCB $OUTB" ;;
esac
# 1. env var
OUTB=$(MERCURY_BASE_BRANCH=trunk "$SCRIPT" newlane 301 --short newl --slug x --dry-run --no-claim \
        --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" --repo-root "$TMP/repob" 2>&1)
case "$OUTB" in
  *"git branch feature/301-x-newl-claude origin/trunk"*) pass "base: MERCURY_BASE_BRANCH" ;;
  *) fail "base from env: $OUTB" ;;
esac
# 2. config.json (needs python), env var wins over it; 3. origin/HEAD
REPOC="$TMP/repoc"; mkdir -p "$REPOC/.mercury"
git init -q "$REPOC" 2>/dev/null
printf '{"params":{"integration_branch":"fromcfg"}}\n' > "$REPOC/.mercury/config.json"
if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
  OUTB=$(env -u MERCURY_BASE_BRANCH "$SCRIPT" newlane 301 --short newl --slug x --dry-run --no-claim \
          --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" --repo-root "$REPOC" 2>&1)
  case "$OUTB" in
    *"origin/fromcfg"*) pass "base: params.integration_branch in .mercury/config.json" ;;
    *) fail "base from config: $OUTB" ;;
  esac
  OUTB=$(MERCURY_BASE_BRANCH=envwins "$SCRIPT" newlane 301 --short newl --slug x --dry-run --no-claim \
          --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" --repo-root "$REPOC" 2>&1)
  case "$OUTB" in
    *"origin/envwins"*) pass "base: env var beats config.json" ;;
    *) fail "env should beat config: $OUTB" ;;
  esac
else
  pass "base: config.json cases skipped (no python)"
fi
rm -f "$REPOC/.mercury/config.json"
git -C "$REPOC" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/mainline 2>/dev/null
OUTB=$(env -u MERCURY_BASE_BRANCH "$SCRIPT" newlane 301 --short newl --slug x --dry-run --no-claim \
        --memory-dir "$MEMB" --lanes-file "$MEMB/LANES.md" --repo-root "$REPOC" 2>&1)
case "$OUTB" in
  *"origin/mainline"*) pass "base: origin/HEAD" ;;
  *) fail "base from origin/HEAD: $OUTB" ;;
esac

# ---- duplicate-lane refusal ----
echo
echo "[duplicate-lane]"
MEM2="$TMP/mem2"; mkdir -p "$MEM2"; write_fixture_lanes "$MEM2/LANES.md"
mkdir -p "$TMP/repo2"
OUT2=$("$SCRIPT" existing-lane 201 --no-claim --no-branch --yes --slug "x" \
        --memory-dir "$MEM2" --lanes-file "$MEM2/LANES.md" \
        --repo-root "$TMP/repo2" 2>&1)
RC2=$?
[ "$RC2" = "1" ] && pass "existing-lane rejected (exit=1)" \
  || fail "existing-lane should refuse: exit=$RC2 out=$OUT2"

# ---- short-name uniqueness ----
echo
echo "[short-name-uniqueness]"
MEM3="$TMP/mem3"; mkdir -p "$MEM3"; write_fixture_lanes "$MEM3/LANES.md"
mkdir -p "$TMP/repo3"
# Try a new lane name BUT reuse existing-lane's short ("existing").
OUT3=$("$SCRIPT" newname 202 --short existing --slug "x" --no-claim --no-branch --yes \
        --memory-dir "$MEM3" --lanes-file "$MEM3/LANES.md" \
        --repo-root "$TMP/repo3" 2>&1)
RC3=$?
[ "$RC3" = "1" ] && pass "duplicate short name rejected (exit=1)" \
  || fail "duplicate short should refuse: exit=$RC3 out=$OUT3"

# ---- no lane-count cap (#605; Delta 7 HARD-CAP removed) ----
echo
echo "[no-cap]"
MEM4="$TMP/mem4"; mkdir -p "$MEM4"; write_fixture_lanes_at_cap "$MEM4/LANES.md"
mkdir -p "$TMP/repo4"
OUT4=$("$SCRIPT" lane6 203 --short l6 --slug "x" --no-claim --no-branch --yes \
        --memory-dir "$MEM4" --lanes-file "$MEM4/LANES.md" \
        --repo-root "$TMP/repo4" 2>&1)
RC4=$?
[ "$RC4" = "0" ] && pass "spawn succeeds with 5 active lanes already registered (no cap)" \
  || fail "spawn should not be capped: exit=$RC4 out=$OUT4"
case "$(cat "$MEM4/LANES.md")" in
  *"### \`lane6\`"*) pass "sixth lane section appended" ;;
  *) fail "sixth lane section missing after uncapped spawn" ;;
esac
case "$OUT4" in
  *HARD-CAP*) fail "output still mentions HARD-CAP: $OUT4" ;;
  *) pass "no HARD-CAP message" ;;
esac

# ---- --harness (#605 / #599 ADR D1) ----
echo
echo "[harness]"
MEMH="$TMP/memh"; mkdir -p "$MEMH" "$TMP/repoh"; write_fixture_lanes "$MEMH/LANES.md"
OUTH=$("$SCRIPT" artlane 206 --short art --slug "x" --harness codex --no-claim --no-branch --yes \
        --memory-dir "$MEMH" --lanes-file "$MEMH/LANES.md" --repo-root "$TMP/repoh" 2>&1)
RCH=$?
[ "$RCH" = "0" ] && pass "--harness codex spawn exit 0" || fail "--harness codex exit=$RCH out=$OUTH"
HSEC=$(awk '/^### `artlane`/{s=1; next} /^### / && s {exit} s' "$MEMH/LANES.md")
case "$HSEC" in
  *"- **Harness**: \`codex\`"*) pass "Harness: codex recorded in the new lane section" ;;
  *) fail "Harness field missing/wrong in artlane section: $HSEC" ;;
esac
OUTD=$("$SCRIPT" deflane 207 --short defl --slug "x" --no-claim --no-branch --yes \
        --memory-dir "$MEMH" --lanes-file "$MEMH/LANES.md" --repo-root "$TMP/repoh" 2>&1)
DSEC=$(awk '/^### `deflane`/{s=1; next} /^### / && s {exit} s' "$MEMH/LANES.md")
case "$DSEC" in
  *"- **Harness**: \`claude\`"*) pass "default harness recorded as claude" ;;
  *) fail "default Harness not claude: $DSEC ($OUTD)" ;;
esac
OUTB=$("$SCRIPT" badlane 208 --short badl --slug "x" --harness gemini --no-claim --no-branch --yes \
        --memory-dir "$MEMH" --lanes-file "$MEMH/LANES.md" --repo-root "$TMP/repoh" 2>&1)
RCB=$?
[ "$RCB" = "2" ] && pass "invalid --harness rejected (exit=2)" || fail "invalid --harness exit=$RCB out=$OUTB"
case "$OUTB" in
  *"--harness must be claude or codex"*) pass "invalid --harness error names the allowed values" ;;
  *) fail "invalid --harness error message unexpected: $OUTB" ;;
esac
OUTM=$("$SCRIPT" misslane 210 --short miss --slug "x" --no-claim --no-branch --yes \
        --memory-dir "$MEMH" --lanes-file "$MEMH/LANES.md" --repo-root "$TMP/repoh" --harness 2>&1)
RCM=$?
case "$RCM:$OUTM" in
  "2:"*"--harness needs a value"*) pass "--harness without value rejected" ;;
  *) fail "--harness without value: exit=$RCM out=$OUTM" ;;
esac
# EOF insert branch: Active Lanes is the last "## " section of LANES.md.
MEME="$TMP/meme"; mkdir -p "$MEME" "$TMP/repoe"
printf '# Lanes\n\n## Active Lanes\n\n### `solo`\n\n- **Short name**: `solo`\n- **Status**: `active`\n' > "$MEME/LANES.md"
OUTE=$("$SCRIPT" eoflane 211 --short eofl --slug "x" --harness codex --no-claim --no-branch --yes \
        --memory-dir "$MEME" --lanes-file "$MEME/LANES.md" --repo-root "$TMP/repoe" 2>&1)
RCE=$?
ESEC=$(awk '/^### `eoflane`/{s=1; next} /^### / && s {exit} s' "$MEME/LANES.md")
case "$RCE:$ESEC" in
  "0:"*"- **Harness**: \`codex\`"*) pass "EOF-branch insert records Harness" ;;
  *) fail "EOF-branch insert missing Harness: exit=$RCE sec=$ESEC out=$OUTE" ;;
esac
case "$(cat "$MEMH/LANES.md")" in
  *"### \`badlane\`"*) fail "invalid --harness still mutated LANES.md" ;;
  *) pass "LANES.md untouched on invalid --harness" ;;
esac
OUTDR=$("$SCRIPT" drylane 209 --short dryl --slug "x" --harness codex --no-claim --no-branch --dry-run \
        --memory-dir "$MEMH" --lanes-file "$MEMH/LANES.md" --repo-root "$TMP/repoh" 2>&1)
case "$OUTDR" in
  *"harness=codex"*) pass "--dry-run shows harness" ;;
  *) fail "--dry-run missing harness: $OUTDR" ;;
esac

# ---- happy path with --no-claim --no-branch --yes ----
echo
echo "[happy-path-offline]"
MEM5="$TMP/mem5"; mkdir -p "$MEM5"; write_fixture_lanes "$MEM5/LANES.md"
mkdir -p "$TMP/repo5"
OUT5=$("$SCRIPT" newlane 204 --short newl --slug "phase-b-test" \
        --no-claim --no-branch --yes \
        --memory-dir "$MEM5" --lanes-file "$MEM5/LANES.md" \
        --repo-root "$TMP/repo5" 2>&1)
RC5=$?
[ "$RC5" = "0" ] && pass "happy path exit 0" || fail "happy path exit=$RC5: $OUT5"
[ -f "$MEM5/session-handoff-newlane.md" ] \
  && pass "handoff file created" \
  || fail "handoff file missing: $MEM5/session-handoff-newlane.md"
case "$(cat "$MEM5/LANES.md")" in
  *"### \`newlane\`"*) pass "newlane section appended to LANES.md" ;;
  *) fail "newlane section NOT in LANES.md after spawn" ;;
esac
case "$(cat "$MEM5/LANES.md")" in
  *"**Branch**: \`feature/204-phase-b-test-newl-claude\`"*) pass "branch field recorded" ;;
  *) fail "branch field missing in LANES.md" ;;
esac
case "$(cat "$MEM5/LANES.md")" in
  *"**Status**: \`active\`"*) pass "status active recorded" ;;
  *) fail "status active missing" ;;
esac
# Other lanes' Status untouched.
EXISTING_STATUS=$(awk '/^### `existing-lane`/{s=1; next} /^### / && s {exit} s && /^- \*\*Status\*\*:/ {print; exit}' "$MEM5/LANES.md")
case "$EXISTING_STATUS" in
  *active*) pass "existing-lane Status untouched" ;;
  *) fail "existing-lane Status modified: '$EXISTING_STATUS'" ;;
esac

# ---- handoff overwrite refusal ----
echo
echo "[handoff-overwrite]"
MEM6="$TMP/mem6"; mkdir -p "$MEM6"; write_fixture_lanes "$MEM6/LANES.md"
mkdir -p "$TMP/repo6"
echo "preexisting content" > "$MEM6/session-handoff-newlane.md"
OUT6=$("$SCRIPT" newlane 205 --short newl --slug "x" --no-claim --no-branch --yes \
        --memory-dir "$MEM6" --lanes-file "$MEM6/LANES.md" \
        --repo-root "$TMP/repo6" 2>&1)
RC6=$?
[ "$RC6" = "1" ] && pass "handoff overwrite refused (exit=1)" \
  || fail "handoff overwrite should refuse: exit=$RC6 out=$OUT6"
# Original handoff content preserved.
case "$(cat "$MEM6/session-handoff-newlane.md")" in
  "preexisting content") pass "preexisting handoff content preserved" ;;
  *) fail "preexisting handoff modified" ;;
esac
# LANES.md NOT mutated on this failure path.
case "$(cat "$MEM6/LANES.md")" in
  *"### \`newlane\`"*) fail "LANES.md mutated despite handoff guard" ;;
  *) pass "LANES.md untouched on handoff guard failure" ;;
esac

# ---- non-interactive without --yes refused ----
echo
echo "[interactive-guard]"
MEM7="$TMP/mem7"; mkdir -p "$MEM7"; write_fixture_lanes "$MEM7/LANES.md"
mkdir -p "$TMP/repo7"
"$SCRIPT" newlane 206 --no-claim --no-branch --short newl --slug "x" \
  --memory-dir "$MEM7" --lanes-file "$MEM7/LANES.md" \
  --repo-root "$TMP/repo7" </dev/null >/dev/null 2>&1
RC7=$?
[ "$RC7" = "1" ] && pass "non-interactive without --yes refuses" \
  || fail "non-interactive exit=$RC7 (expected 1)"

# ---- --short auto-derive ----
echo
echo "[short-derive]"
MEM8="$TMP/mem8"; mkdir -p "$MEM8"; write_fixture_lanes "$MEM8/LANES.md"
mkdir -p "$TMP/repo8"
# Auto-derived short from "AutoLane" → "autolane" → cut to 8 chars = "autolane"
OUT8=$("$SCRIPT" AutoLane 207 --slug "x" --no-claim --no-branch --yes \
        --memory-dir "$MEM8" --lanes-file "$MEM8/LANES.md" \
        --repo-root "$TMP/repo8" 2>&1)
RC8=$?
[ "$RC8" = "0" ] && pass "auto-derive happy path exit 0" \
  || fail "auto-derive failed: exit=$RC8 out=$OUT8"
case "$(cat "$MEM8/LANES.md")" in
  *"**Short name**: \`autolane\`"*) pass "auto-derived short = autolane" ;;
  *) fail "auto-derived short missing/wrong in LANES.md" ;;
esac

# ---- --lanes-file path traversal defense (Argus iter 2 importance:2/10) ----
# Spec: --lanes-file resolving outside MEMORY_DIR must be refused before any
# external mutation. Default --lanes-file = $MEMORY_DIR/LANES.md trivially OK.
echo
echo "[lanes-file-path-traversal]"
MEM_PT="$TMP/case-pt"; mkdir -p "$MEM_PT"; write_fixture_lanes "$MEM_PT/LANES.md"
mkdir -p "$TMP/case-pt-repo"
# Create a side LANES.md OUTSIDE memory-dir
SIDE_LANES="$TMP/elsewhere-lanes.md"
write_fixture_lanes "$SIDE_LANES"

OUT_PT=$("$SCRIPT" newlane 400 --short newl --slug "x" \
        --no-claim --no-branch --yes \
        --memory-dir "$MEM_PT" --lanes-file "$SIDE_LANES" \
        --repo-root "$TMP/case-pt-repo" 2>&1)
RC_PT=$?
[ "$RC_PT" = "2" ] && pass "--lanes-file outside --memory-dir refused (exit=2)" \
  || fail "--lanes-file path traversal not refused: exit=$RC_PT out=$OUT_PT"

# Default --lanes-file (under --memory-dir) accepted via prior happy-path tests.

# ---- malformed LANES.md (Codex audit Medium) ----
# Spec: spawn must REFUSE before any external mutation when the registry is
# missing '## Active Lanes' — otherwise step 8 awk would silently no-op
# leaving the issue claim / branch / handoff with no registry row.
echo
echo "[malformed-lanes-md]"
MEM_BAD1="$TMP/case-bad1"; mkdir -p "$MEM_BAD1"
# Empty LANES.md (no Active Lanes header at all)
printf '# Mercury Lanes Registry\n\n(empty)\n' > "$MEM_BAD1/LANES.md"
mkdir -p "$TMP/case-bad1-repo"

OUT_BAD1=$("$SCRIPT" newlane 300 --short newl --slug "x" \
        --no-claim --no-branch --yes \
        --memory-dir "$MEM_BAD1" --lanes-file "$MEM_BAD1/LANES.md" \
        --repo-root "$TMP/case-bad1-repo" 2>&1)
RC_BAD1=$?
[ "$RC_BAD1" = "1" ] && pass "missing '## Active Lanes' header refused (exit=1)" \
  || fail "missing-header should refuse: exit=$RC_BAD1 out=$OUT_BAD1"
case "$OUT_BAD1" in
  *"Active Lanes"*) pass "error message names the missing header" ;;
  *) fail "error message missing 'Active Lanes': $OUT_BAD1" ;;
esac
# Confirm no FS mutation: LANES.md unchanged, no handoff written.
LANES_BAD1_AFTER=$(cat "$MEM_BAD1/LANES.md")
case "$LANES_BAD1_AFTER" in
  *"### \`newlane\`"*) fail "LANES.md mutated despite refusal" ;;
  *) pass "LANES.md untouched on missing-header refusal" ;;
esac
[ ! -f "$MEM_BAD1/session-handoff-newlane.md" ] \
  && pass "handoff NOT written on missing-header refusal" \
  || fail "handoff written despite refusal"

# Truly empty LANES.md
MEM_BAD2="$TMP/case-bad2"; mkdir -p "$MEM_BAD2"
: > "$MEM_BAD2/LANES.md"
mkdir -p "$TMP/case-bad2-repo"

OUT_BAD2=$("$SCRIPT" newlane 301 --short newl --slug "x" \
        --no-claim --no-branch --yes \
        --memory-dir "$MEM_BAD2" --lanes-file "$MEM_BAD2/LANES.md" \
        --repo-root "$TMP/case-bad2-repo" 2>&1)
RC_BAD2=$?
[ "$RC_BAD2" = "1" ] && pass "empty LANES.md refused (exit=1)" \
  || fail "empty LANES.md should refuse: exit=$RC_BAD2 out=$OUT_BAD2"
[ ! -f "$MEM_BAD2/session-handoff-newlane.md" ] \
  && pass "handoff NOT written on empty-LANES.md refusal" \
  || fail "handoff written despite empty-LANES.md refusal"

# ---- branch length cap (Rule 2.1 ≤40 chars) ----
echo
echo "[branch-no-cap]"
MEM9="$TMP/mem9"; mkdir -p "$MEM9"; write_fixture_lanes "$MEM9/LANES.md"
mkdir -p "$TMP/repo9"
OUT9=$("$SCRIPT" longLane 208 --short ll --slug "very-very-very-long-task-slug-overflow" \
        --no-claim --no-branch --yes \
        --memory-dir "$MEM9" --lanes-file "$MEM9/LANES.md" \
        --repo-root "$TMP/repo9" 2>&1)
RC9=$?
[ "$RC9" = "0" ] && pass "long slug accepted (exit 0)" \
  || fail "long slug failed: exit=$RC9 out=$OUT9"
# Pull the recorded branch: the old 40-char cap is gone (#703), the slug stays whole.
BR=$(awk '/^### `longLane`/{s=1} s && /^- \*\*Branch\*\*: `[^`]+`/{match($0,/`[^`]+`/); print substr($0,RSTART+1,RLENGTH-2); exit}' "$MEM9/LANES.md")
[ "$BR" = "feature/208-very-very-very-long-task-slug-overflow-ll-claude" ]   && pass "branch ${#BR} chars kept whole (no cap)"   || fail "branch not kept whole: '$BR'"

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
