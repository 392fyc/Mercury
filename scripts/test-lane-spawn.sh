#!/usr/bin/env bash
# scripts/test-lane-spawn.sh — smoke tests for lane-spawn.sh
#
# Builds synthetic LANES.md + memory dir, exercises argument validation,
# dry-run, --no-claim/--no-branch happy path, duplicate-lane refusal,
# HARD-CAP=5 detection, short-name uniqueness, and handoff overwrite guard.
# Tests run offline (no gh / no live Issue probe). Exit 0 if all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-spawn: not inside a git repo" >&2; exit 2; }
SCRIPT="$REPO_ROOT/scripts/lane-spawn.sh"
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
- **Branch**: `lane/existing/100-foo`
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
  *"branch=lane/newlane/200-test-spawn"*) pass "dry-run prints branch" ;;
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

# ---- HARD-CAP=5 detection ----
echo
echo "[hard-cap]"
MEM4="$TMP/mem4"; mkdir -p "$MEM4"; write_fixture_lanes_at_cap "$MEM4/LANES.md"
mkdir -p "$TMP/repo4"
OUT4=$("$SCRIPT" lane6 203 --short l6 --slug "x" --no-claim --no-branch --yes \
        --memory-dir "$MEM4" --lanes-file "$MEM4/LANES.md" \
        --repo-root "$TMP/repo4" 2>&1)
RC4=$?
[ "$RC4" = "1" ] && pass "HARD-CAP=5 enforced (exit=1)" \
  || fail "cap check failed: exit=$RC4 out=$OUT4"
case "$OUT4" in
  *HARD-CAP*) pass "cap-check error message mentions HARD-CAP" ;;
  *) fail "cap-check error message missing 'HARD-CAP': $OUT4" ;;
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
  *"**Branch**: \`lane/newl/204-phase-b-test\`"*) pass "branch field recorded" ;;
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
echo "[branch-cap]"
MEM9="$TMP/mem9"; mkdir -p "$MEM9"; write_fixture_lanes "$MEM9/LANES.md"
mkdir -p "$TMP/repo9"
OUT9=$("$SCRIPT" longLane 208 --short ll --slug "very-very-very-long-task-slug-overflow" \
        --no-claim --no-branch --yes \
        --memory-dir "$MEM9" --lanes-file "$MEM9/LANES.md" \
        --repo-root "$TMP/repo9" 2>&1)
RC9=$?
[ "$RC9" = "0" ] && pass "long slug truncated (exit 0)" \
  || fail "long slug failed: exit=$RC9 out=$OUT9"
# Pull the recorded branch and check ≤40 chars.
BR=$(awk '/^### `longLane`/{s=1} s && /^- \*\*Branch\*\*: `[^`]+`/{match($0,/`[^`]+`/); print substr($0,RSTART+1,RLENGTH-2); exit}' "$MEM9/LANES.md")
[ -n "$BR" ] && [ "${#BR}" -le 40 ] \
  && pass "branch length ${#BR} ≤40 (cap honored)" \
  || fail "branch length ${#BR}: '$BR' (cap violated)"

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
