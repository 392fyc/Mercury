#!/usr/bin/env bash
# scripts/test-lane-sweep.sh — smoke tests for lane-sweep.sh
#
# Builds synthetic memory dir + LANES.md + handoff files; uses --no-issue-check
# to skip live GitHub calls; verifies arg validation, parsing, age computation,
# and stale verdicts. Exit 0 if all pass.

set -u

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "test-lane-sweep: not inside a git repo" >&2; exit 2; }
SCRIPT="$REPO_ROOT/scripts/lane-sweep.sh"
[ -x "$SCRIPT" ] || { echo "test-lane-sweep: $SCRIPT missing or not executable" >&2; exit 2; }

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

assert_not_contains() {
  local desc=$1 needle=$2 actual=$3
  if printf '%s' "$actual" | grep -q -- "$needle"; then
    fail "$desc — unexpected '$needle' in: $(printf '%s' "$actual" | head -c200)"
  else pass "$desc"; fi
}

# ---- arg validation (no synthetic memory needed) ----
echo "[arg-validation]"
assert_exit 0 "--help"                      "$SCRIPT" --help
assert_exit 2 "unknown flag rejected"       "$SCRIPT" --bogus
assert_exit 2 "--days zero rejected"        "$SCRIPT" --days 0 --no-issue-check
assert_exit 2 "--days non-numeric rejected" "$SCRIPT" --days abc --no-issue-check
assert_exit 2 "--format invalid rejected"   "$SCRIPT" --format xml --no-issue-check
assert_exit 2 "missing memory dir rejected" "$SCRIPT" --memory-dir "$TMP/nope" --no-issue-check

# ---- synthetic memory + LANES.md fixture ----
MEM="$TMP/memory"
mkdir -p "$MEM"
cat > "$MEM/LANES.md" <<'EOF'
---
name: LANES
---
# Mercury Lanes Registry

## Active Lanes

### `main` (default lane)

- **Status**: `active`

### `side-fresh`

- **Status**: `active`

### `side-stale`

- **Status**: `active`

## Closed Lanes

### `side-archived`

- **Status**: `closed`

## Governance

something
EOF

# Fresh handoff (now), stale handoff (60d old), no handoff for one lane.
touch "$MEM/session-handoff.md"
touch "$MEM/session-handoff-side-fresh.md"
# Stale: set mtime 60 days ago via touch -t (portable on GNU; BSD uses different syntax).
STALE_DATE=$(date -d '60 days ago' '+%Y%m%d%H%M' 2>/dev/null || date -v-60d '+%Y%m%d%H%M' 2>/dev/null)
if [ -n "$STALE_DATE" ]; then
  touch -t "$STALE_DATE" "$MEM/session-handoff-side-stale.md" 2>/dev/null \
    || { echo "test-lane-sweep: cannot set 60d-old mtime — skipping stale verdict assertion" >&2; STALE_DATE=""; }
else
  echo "test-lane-sweep: cannot compute 60d-ago date — stale verdict assertion will be skipped" >&2
fi

echo
echo "[scenarios]"

OUT=$("$SCRIPT" --memory-dir "$MEM" --days 14 --no-issue-check --format text 2>&1)
RC=$?
[ "$RC" = "0" ] && pass "default-text run exits 0" || fail "default-text run exit=$RC: $OUT"

assert_contains "header row present"        "VERDICT"      "$OUT"
assert_contains "main lane row present"     "main"         "$OUT"
assert_contains "side-fresh lane present"   "side-fresh"   "$OUT"
assert_contains "side-stale lane present"   "side-stale"   "$OUT"
assert_not_contains "closed lane excluded"  "side-archived" "$OUT"

# Fresh handoff → fresh verdict (issue-check off → infinite, but branch missing
# → infinite too; only handoff_age fresh defeats stale). Three-of-three rule:
# stale needs ALL THREE old. With issue=inf + branch=inf + handoff=fresh → not stale.
assert_contains "side-fresh verdict=fresh"  "side-fresh.*fresh"  "$OUT"

if [ -n "$STALE_DATE" ]; then
  assert_contains "side-stale verdict=stale" "side-stale.*stale" "$OUT"
fi

OUT_JSON=$("$SCRIPT" --memory-dir "$MEM" --days 14 --no-issue-check --format json 2>&1)
assert_contains "json output has lanes array"   '"lanes":\['          "$OUT_JSON"
assert_contains "json output has verdict field" '"verdict":"'         "$OUT_JSON"
assert_contains "json output has main entry"    '"lane":"main"'       "$OUT_JSON"

# Empty Active Lanes section → empty body but exit 0 with WARN.
cat > "$TMP/empty-lanes.md" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

## Closed Lanes
EOF
OUT_EMPTY=$("$SCRIPT" --lanes-file "$TMP/empty-lanes.md" --memory-dir "$MEM" --no-issue-check 2>&1)
RC_EMPTY=$?
[ "$RC_EMPTY" = "0" ] && pass "empty active section exits 0" || fail "empty active section exit=$RC_EMPTY"
assert_contains "empty active section warns" "no active lanes" "$OUT_EMPTY"

# ---- JSON escape (Argus #327 finding #6 fix) ----
echo
echo "[json-escape]"
JSON_FIX_LANES="$TMP/json-fix-lanes.md"
cat > "$JSON_FIX_LANES" <<'EOF'
# Mercury Lanes Registry

## Active Lanes

### `weird"name`

- **Status**: `active`

### `back\slash`

- **Status**: `active`

## Closed Lanes
EOF

OUT_ESC=$("$SCRIPT" --lanes-file "$JSON_FIX_LANES" --memory-dir "$MEM" \
          --no-issue-check --format json 2>&1)
RC_ESC=$?
[ "$RC_ESC" = "0" ] && pass "weird-lane-name run exits 0" \
  || fail "weird-lane-name exit=$RC_ESC out=$OUT_ESC"

# JSON validity check via python (best-available cross-platform parser).
if command -v python >/dev/null 2>&1; then
  if printf '%s' "$OUT_ESC" | python -c 'import sys,json; json.load(sys.stdin)' 2>/dev/null; then
    pass "JSON output with quote/backslash in lane name is parseable"
  else
    fail "JSON output is INVALID: $OUT_ESC"
  fi
elif command -v python3 >/dev/null 2>&1; then
  if printf '%s' "$OUT_ESC" | python3 -c 'import sys,json; json.load(sys.stdin)' 2>/dev/null; then
    pass "JSON output with quote/backslash in lane name is parseable (python3)"
  else
    fail "JSON output is INVALID: $OUT_ESC"
  fi
else
  echo "  SKIP: python not available — cannot validate JSON" >&2
fi

echo
printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" = "0" ]
