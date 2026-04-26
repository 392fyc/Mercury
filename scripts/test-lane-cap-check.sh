#!/usr/bin/env bash
# scripts/test-lane-cap-check.sh — smoke tests for lane-cap-check.sh
#
# Builds synthetic LANES.md fixtures with varying active-lane counts and
# verifies the advisory verdict + count + exit codes. Exit 0 if all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-cap-check: not inside a git repo" >&2; exit 2; }
SCRIPT="$REPO_ROOT/scripts/lane-cap-check.sh"
[ -x "$SCRIPT" ] || { echo "test-lane-cap-check: $SCRIPT missing or not executable" >&2; exit 2; }

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

assert_contains() {
  local desc=$1 needle=$2 actual=$3
  if printf '%s' "$actual" | grep -q -- "$needle"; then pass "$desc"
  else fail "$desc — needle '$needle' not in: $(printf '%s' "$actual" | head -c200)"; fi
}

write_fixture_lanes() {
  local path="$1" count="$2"
  {
    cat <<'EOF'
# Mercury Lanes Registry

## Active Lanes

EOF
    for i in $(seq 1 "$count"); do
      printf '### `lane-%d`\n\n- **Status**: `active`\n\n' "$i"
    done
    # Also include one closed lane to verify it's NOT counted
    cat <<'EOF'
### `lane-closed-x`

- **Status**: `closed`

## Closed Lanes

(none)
EOF
  } > "$path"
}

# ---- arg validation ----
echo "[arg-validation]"
assert_exit 0 "--help"                     "$SCRIPT" --help
assert_exit 2 "unknown flag rejected"      "$SCRIPT" --bogus
assert_exit 2 "--max zero rejected"        "$SCRIPT" --max 0 --memory-dir "$TMP"
assert_exit 2 "--max non-numeric rejected" "$SCRIPT" --max abc --memory-dir "$TMP"
assert_exit 2 "--format invalid rejected"  "$SCRIPT" --format yaml --memory-dir "$TMP"
assert_exit 2 "missing memory dir rejected" "$SCRIPT" --memory-dir "$TMP/nope"
assert_exit 2 "missing lanes-file rejected" "$SCRIPT" --lanes-file "$TMP/nope.md" --memory-dir "$TMP"

# ---- within-cap scenarios ----
MEM="$TMP/mem"; mkdir -p "$MEM"
echo
echo "[within-cap]"
write_fixture_lanes "$MEM/LANES.md" 3
OUT_3=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
RC_3=$?
[ "$RC_3" = "0" ] && pass "3 active / max 5 → exit 0" || fail "3/5 exit=$RC_3 out=$OUT_3"
assert_contains "verdict within_cap" "within_cap" "$OUT_3"
assert_contains "count 3 reported"   "3 active"   "$OUT_3"

# Edge: exactly at cap
write_fixture_lanes "$MEM/LANES.md" 5
OUT_5=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
RC_5=$?
[ "$RC_5" = "0" ] && pass "5 active / max 5 → exit 0 (boundary)" || fail "5/5 exit=$RC_5"

# ---- exceeded scenarios ----
echo
echo "[exceeded]"
write_fixture_lanes "$MEM/LANES.md" 6
OUT_6=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
RC_6=$?
[ "$RC_6" = "1" ] && pass "6 active / max 5 → exit 1" || fail "6/5 exit=$RC_6"
assert_contains "verdict exceeded"   "exceeded"     "$OUT_6"
assert_contains "resolution hint"    "protocol-violation" "$OUT_6"

# Custom max
write_fixture_lanes "$MEM/LANES.md" 4
OUT_4_3=$("$SCRIPT" --memory-dir "$MEM" --max 3 2>&1)
RC_4_3=$?
[ "$RC_4_3" = "1" ] && pass "4 active / max 3 → exit 1 (custom max)" || fail "4/3 exit=$RC_4_3"

# ---- closed lanes excluded from count ----
echo
echo "[exclusion]"
# Fixture above already includes one `closed` lane; verify count = active-only
write_fixture_lanes "$MEM/LANES.md" 2
OUT_EXCL=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
assert_contains "closed lane excluded from count" "2 active" "$OUT_EXCL"

# ---- json format ----
echo
echo "[json]"
write_fixture_lanes "$MEM/LANES.md" 3
OUT_JSON=$("$SCRIPT" --memory-dir "$MEM" --max 5 --format json 2>&1)
RC_JSON=$?
[ "$RC_JSON" = "0" ] && pass "json within_cap exit 0" || fail "json exit=$RC_JSON"
assert_contains "json has max"          '"max":5'                "$OUT_JSON"
assert_contains "json has active_count" '"active_count":3'       "$OUT_JSON"
assert_contains "json has verdict"      '"verdict":"within_cap"' "$OUT_JSON"
assert_contains "json has lanes array"   '"lanes":\["lane-1","lane-2","lane-3"\]' "$OUT_JSON"

# Validate JSON parseability
if command -v python >/dev/null 2>&1; then
  if printf '%s' "$OUT_JSON" | python -c 'import sys,json; json.load(sys.stdin)' 2>/dev/null; then
    pass "json output is parseable"
  else
    fail "json output is INVALID: $OUT_JSON"
  fi
fi

# ---- defensive parser: orphan lane (heading without Status) ----
# Verifies OMC #327 review MAJOR #1 — lane heading not followed by Status
# emits WARN to stderr and is NOT counted (fixes overwrite-attribution bug).
echo
echo "[parser-orphan]"
cat > "$MEM/LANES.md" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `lane-orphan`

- **Handoff**: missing-status.md

### `lane-real`

- **Status**: `active`

## Closed Lanes
EOF
OUT_O=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
RC_O=$?
[ "$RC_O" = "0" ] && pass "orphan + 1 active → exit 0" || fail "orphan exit=$RC_O out=$OUT_O"
assert_contains "orphan WARN emitted"     "lane-orphan"            "$OUT_O"
assert_contains "WARN suggests fix"        "no Status line"         "$OUT_O"
assert_contains "real lane still counted"  "1 active"               "$OUT_O"

# ---- defensive parser: zombie heading in Closed Lanes section is NOT counted ----
# Verifies OMC #327 review MINOR #3 — section-boundary logic correctly
# stops counting at "## Closed Lanes" anchor.
echo
echo "[parser-zombie]"
cat > "$MEM/LANES.md" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `lane-real`

- **Status**: `active`

## Closed Lanes

### `lane-zombie`

- **Status**: `active`

## Governance
EOF
OUT_Z=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
assert_contains "zombie in Closed not counted" "1 active" "$OUT_Z"
if printf '%s' "$OUT_Z" | grep -q "lane-zombie"; then
  fail "zombie lane in Closed Lanes section was incorrectly counted"
else
  pass "zombie lane in Closed Lanes section correctly excluded"
fi

# ---- defensive: JSON escape for hostile lane names ----
# Verifies OMC #327 review MAJOR #2 — quote/backslash in lane name
# produce valid JSON via json_string() helper (mirrors lane-sweep.sh pattern).
echo
echo "[json-escape]"
cat > "$MEM/LANES.md" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `weird"name`

- **Status**: `active`

### `back\slash`

- **Status**: `active`

## Closed Lanes
EOF
OUT_J=$("$SCRIPT" --memory-dir "$MEM" --max 5 --format json 2>&1)
RC_J=$?
[ "$RC_J" = "0" ] && pass "json escape exit 0" || fail "json escape exit=$RC_J out=$OUT_J"
if command -v python >/dev/null 2>&1; then
  if printf '%s' "$OUT_J" | python -c 'import sys,json; d=json.load(sys.stdin); assert d["active_count"]==2 and len(d["lanes"])==2' 2>/dev/null; then
    pass "json with quote/backslash lane names is valid + parses correctly"
  else
    fail "json output INVALID under hostile lane names: $OUT_J"
  fi
fi

# ---- defensive: regex anchored exact match for `active` ----
# Verifies Argus #328 review iter 1 — `Status: active-foo` / `active123`
# / `active-ish` MUST NOT be counted as active (only literal `active`).
echo
echo "[regex-anchor]"
cat > "$MEM/LANES.md" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `lane-genuine`

- **Status**: `active`

### `lane-prefix-attack`

- **Status**: `active-foo`

### `lane-suffix-attack`

- **Status**: `activeless`

### `lane-numeric-attack`

- **Status**: active123

## Closed Lanes
EOF
OUT_R=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
RC_R=$?
[ "$RC_R" = "0" ] && pass "regex-anchor exit 0" || fail "regex-anchor exit=$RC_R"
assert_contains "only genuine lane counted (1 active)" "1 active" "$OUT_R"
if printf '%s' "$OUT_R" | grep -qE "lane-prefix-attack|lane-suffix-attack|lane-numeric-attack"; then
  fail "non-active prefix/suffix/numeric lane was incorrectly counted"
else
  pass "non-active prefix/suffix/numeric lanes correctly excluded"
fi

# Mercury convention: trailing annotation after Status value (separated by
# whitespace + dash/punctuation) IS allowed and counts as active. Without
# this, real LANES.md entries like `Status: \`active\` — Phase B complete`
# would false-negative (regression caught during S5 smoke test).
cat > "$MEM/LANES.md" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `lane-with-annotation`

- **Status**: `active` — Phase B complete; Phase C in progress

### `lane-clean`

- **Status**: `active`

## Closed Lanes
EOF
OUT_W=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
assert_contains "annotated active still counted (Mercury convention)" "2 active" "$OUT_W"
assert_contains "annotated lane name in active list" "lane-with-annotation" "$OUT_W"

# ---- empty Active Lanes section → 0 count → within cap ----
echo
echo "[empty]"
cat > "$MEM/LANES.md" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

## Closed Lanes
EOF
OUT_E=$("$SCRIPT" --memory-dir "$MEM" --max 5 2>&1)
RC_E=$?
[ "$RC_E" = "0" ] && pass "0 active → exit 0" || fail "empty exit=$RC_E"
assert_contains "0 active reported" "0 active" "$OUT_E"

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
