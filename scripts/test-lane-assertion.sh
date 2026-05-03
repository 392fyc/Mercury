#!/usr/bin/env bash
# Tests for scripts/lane-assertion.sh — synthetic LANES.md fixtures + env
# overrides drive the script through every exit code branch. No real
# user-memory state required; tests are reproducible across machines.
#
# Run: bash scripts/test-lane-assertion.sh

set -u  # not -e: each scenario inspects rc explicitly

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ASSERTION="$SCRIPT_DIR/lane-assertion.sh"
[[ -x "$ASSERTION" ]] || { printf 'lane-assertion.sh not executable: %s\n' "$ASSERTION" >&2; exit 1; }

PASS=0
FAIL=0
declare -a FAILURES=()

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    PASS=$((PASS + 1))
    printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    FAILURES+=("$label: expected [$expected], got [$actual]")
    printf '  FAIL %s -- expected [%s], got [%s]\n' "$label" "$expected" "$actual"
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    PASS=$((PASS + 1))
    printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    FAILURES+=("$label: missing [$needle]")
    printf '  FAIL %s -- missing [%s]\n' "$label" "$needle"
  fi
}

# Fixture work dir.
WORK_DIR="$(mktemp -d -t lane-assertion-test.XXXXXX)" || { printf 'mktemp failed\n' >&2; exit 1; }
trap '[[ -n "${WORK_DIR:-}" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"' EXIT

MEMDIR="$WORK_DIR/memory"
mkdir -p "$MEMDIR"

# ── Synthetic LANES.md fixture matching real Mercury structure ──
cat > "$MEMDIR/LANES.md" <<'LANES_EOF'
---
name: LANES
description: test fixture
type: project
---
# Mercury Lanes Registry

## Active Lanes

### `main`

- **Handoff file**: `session-handoff.md`
- **Latest session**: S82
- **Worktree path** (per Rule 5.1, Issue #342): `/var/test/main-lane`
- **Branch prefix**: `feature/lane-main/TASK-<N>-*` or `lane/main/<N>-<slug>`
- **Status**: `active`

### `side-multi-lane`

- **Handoff file**: `session-handoff-side-multi-lane.md`
- **Latest session**: S16-side-multi-lane
- **Worktree path** (per Rule 5.1, Issue #342 — landed S15 2026-05-03): `/var/test/side-mlane` ✅ materialized at S16
- **Short name**: `side-mlane` (8 char)
- **Branch prefix**: `lane/side-mlane/<N>-<slug>` (Rule 2.1)
- **Status**: `active`

### `nopath`

- **Handoff file**: `session-handoff-nopath.md`
- **Status**: `active`
LANES_EOF

run_assert() {
  # $1 label  $2 expected_rc  $3+ args / env (passed via env-prefix)
  local label="$1" want_rc="$2"
  shift 2
  printf '\n[scenario] %s\n' "$label"
  local out rc
  out=$(env "$@" bash "$ASSERTION" --memory-dir "$MEMDIR" 2>&1)
  rc=$?
  assert_eq "  exit code" "$want_rc" "$rc"
  printf '%s\n' "$out" >> "$WORK_DIR/last-output.log"
  printf '%s' "$out"
  return $rc
}

# ───────────────────────────────────────────────────────────────
# Happy paths — main + side lane each on develop and on a valid branch
# ───────────────────────────────────────────────────────────────

printf '\n=== Happy paths ===\n'

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "main lane on develop — rc=0" "0" "$rc"
assert_contains "main lane on develop — verdict aligned" "aligned" "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch 'feature/lane-main/TASK-100-foo' 2>&1)
rc=$?
assert_eq "main lane legacy branch — rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch 'feature/TASK-200-bar' 2>&1)
rc=$?
assert_eq "main lane legacy TASK- branch — rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch 'lane/main/345-handoff' 2>&1)
rc=$?
assert_eq "main lane Rule 2.1 short prefix — rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch 'lane/side-mlane/init' 2>&1)
rc=$?
assert_eq "side lane scaffold init branch — rc=0" "0" "$rc"
assert_contains "side lane verdict line includes lane name" "side-multi-lane" "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch 'lane/side-mlane/342-foo' 2>&1)
rc=$?
assert_eq "side lane Rule 2.1 work branch — rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch 'feature/lane-side-multi-lane/TASK-310-foo' 2>&1)
rc=$?
assert_eq "side lane legacy long-prefix branch — rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# Marker missing / malformed → exit 1
# ───────────────────────────────────────────────────────────────

printf '\n=== Marker errors (exit 1) ===\n'

err=$(env -u MERCURY_LANE_MARKER -u BOOTSTRAP_PROMPT bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop </dev/null 2>&1)
rc=$?
assert_eq "no marker anywhere — rc=1" "1" "$rc"
assert_contains "no marker — message guides recovery" "no parseable" "$err"

err=$(env MERCURY_LANE_MARKER='LANE=main' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "marker missing brackets — rc=1" "1" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "marker empty name — rc=1" "1" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=Side_Multi]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "marker has uppercase/underscore (Rule 2.1 violation) — rc=1" "1" "$rc"

# ───────────────────────────────────────────────────────────────
# cwd mismatch → exit 2 (the routing-bleed risk #342)
# ───────────────────────────────────────────────────────────────

printf '\n=== cwd mismatch (exit 2) ===\n'

err=$(env MERCURY_LANE_MARKER='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch 'lane/side-mlane/init' 2>&1)
rc=$?
assert_eq "side lane marker but cwd is main checkout — rc=2" "2" "$rc"
assert_contains "cwd mismatch — guidance points at cd worktree" "cd " "$err"
assert_contains "cwd mismatch — references worktree path" "side-mlane" "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch develop 2>&1)
rc=$?
assert_eq "main lane marker but cwd is side worktree — rc=2" "2" "$rc"

# ───────────────────────────────────────────────────────────────
# Branch prefix mismatch → exit 3
# ───────────────────────────────────────────────────────────────

printf '\n=== Branch mismatch (exit 3) ===\n'

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch 'lane/side-mlane/init' 2>&1)
rc=$?
assert_eq "main lane on side branch — rc=3" "3" "$rc"
assert_contains "branch mismatch — explains expected" "expects" "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch 'feature/something-unrelated' 2>&1)
rc=$?
assert_eq "side lane on unrelated branch — rc=3" "3" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch 'lane/wrong-short/init' 2>&1)
rc=$?
assert_eq "side lane wrong short-name in branch — rc=3" "3" "$rc"

# ───────────────────────────────────────────────────────────────
# Worktree path missing in LANES.md → exit 4
# ───────────────────────────────────────────────────────────────

printf '\n=== Worktree path missing (exit 4) ===\n'

err=$(env MERCURY_LANE_MARKER='[LANE=nopath]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/nopath' --branch develop 2>&1)
rc=$?
assert_eq "lane has no Worktree path field — rc=4" "4" "$rc"
assert_contains "missing field — points at LANES.md" "LANES.md" "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=ghostlane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/ghost' --branch develop 2>&1)
rc=$?
assert_eq "lane name not present in LANES.md at all — rc=4" "4" "$rc"

# ───────────────────────────────────────────────────────────────
# Soft-disable — bypasses every check, exits 0
# ───────────────────────────────────────────────────────────────

printf '\n=== Soft-disable env (exit 0 always) ===\n'

err=$(env MERCURY_LANE_ASSERT_DISABLED=1 bash "$ASSERTION" \
  --memory-dir "$MEMDIR" 2>&1)
rc=$?
assert_eq "soft-disabled, no marker — rc=0" "0" "$rc"
assert_contains "soft-disable announcement on stdout" "soft-disabled" "$err"

err=$(env MERCURY_LANE_ASSERT_DISABLED=1 MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch 'feature/wrong' 2>&1)
rc=$?
assert_eq "soft-disabled with deliberately misaligned state — rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# Argument errors → exit 5
# ───────────────────────────────────────────────────────────────

printf '\n=== Argument errors (exit 5) ===\n'

err=$(bash "$ASSERTION" --memory-dir 2>&1)
rc=$?
assert_eq "--memory-dir without value — rc=5" "5" "$rc"

err=$(bash "$ASSERTION" --memory-dir '' 2>&1)
rc=$?
assert_eq "--memory-dir with empty value — rc=5" "5" "$rc"

err=$(bash "$ASSERTION" --memory-dir "$WORK_DIR/does-not-exist" 2>&1)
rc=$?
assert_eq "--memory-dir non-existent — rc=5" "5" "$rc"

err=$(bash "$ASSERTION" --memory-dir "$MEMDIR" --format weird 2>&1)
rc=$?
assert_eq "--format unknown value — rc=5" "5" "$rc"

err=$(bash "$ASSERTION" --memory-dir "$MEMDIR" --unknown-flag 2>&1)
rc=$?
assert_eq "unknown flag — rc=5" "5" "$rc"

# ───────────────────────────────────────────────────────────────
# Marker source priority: CLI > env > stdin
# ───────────────────────────────────────────────────────────────

printf '\n=== Marker source priority ===\n'

err=$(env MERCURY_LANE_MARKER='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --marker '[LANE=main]' --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "--marker overrides MERCURY_LANE_MARKER — rc=0 (main wins)" "0" "$rc"

err=$(env -u MERCURY_LANE_MARKER -u BOOTSTRAP_PROMPT bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop \
  <<< '[LANE=main] continue from session handoff' 2>&1)
rc=$?
assert_eq "stdin marker fallback — rc=0" "0" "$rc"

err=$(env BOOTSTRAP_PROMPT='[LANE=side-multi-lane] continue from handoff' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch 'lane/side-mlane/init' 2>&1)
rc=$?
assert_eq "BOOTSTRAP_PROMPT env marker — rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# Codex iter1 High #1: backtick-quoted Worktree paths must preserve spaces.
# `/Users/Alice Smith/repos/Mercury-side` MUST be returned whole, not
# truncated at the first whitespace. Same for paths with parens like
# `C:/Program Files (x86)/Mercury`.
# ───────────────────────────────────────────────────────────────

printf '\n=== Worktree paths with spaces / parens (Codex H#1 regression) ===\n'

SPACE_MEM="$WORK_DIR/space-mem"
mkdir -p "$SPACE_MEM"
cat > "$SPACE_MEM/LANES.md" <<'SPACE_EOF'
---
type: project
---
### `with-space`

- **Worktree path**: `/Users/Alice Smith/repos/Mercury-with-space`

### `with-paren`

- **Worktree path**: `C:/Program Files (x86)/Mercury-with-paren`

### `unquoted-legacy`

- **Worktree path**: /tmp/unquoted-path
SPACE_EOF

err=$(env MERCURY_LANE_MARKER='[LANE=with-space]' bash "$ASSERTION" \
  --memory-dir "$SPACE_MEM" --cwd '/Users/Alice Smith/repos/Mercury-with-space' --branch develop 2>&1)
rc=$?
assert_eq "spaces in path preserved -> rc=0" "0" "$rc"
assert_contains "verdict shows full path" 'Alice Smith' "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=with-space]' bash "$ASSERTION" \
  --memory-dir "$SPACE_MEM" --cwd '/Users/Alice' --branch develop 2>&1)
rc=$?
assert_eq "truncated cwd no longer falsely passes -> rc=2" "2" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=with-paren]' bash "$ASSERTION" \
  --memory-dir "$SPACE_MEM" --cwd 'C:/Program Files (x86)/Mercury-with-paren' --branch develop 2>&1)
rc=$?
assert_eq "parens in path preserved (Program Files (x86)) -> rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=unquoted-legacy]' bash "$ASSERTION" \
  --memory-dir "$SPACE_MEM" --cwd '/tmp/unquoted-path' --branch develop 2>&1)
rc=$?
assert_eq "unquoted legacy path still works -> rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# Codex iter1 High #2: duplicate Worktree path bullets MUST exit 6,
# not silently first-wins.
# ───────────────────────────────────────────────────────────────

printf '\n=== Duplicate Worktree path bullets (Codex H#2 regression) ===\n'

DUP_MEM="$WORK_DIR/dup-mem"
mkdir -p "$DUP_MEM"
cat > "$DUP_MEM/LANES.md" <<'DUP_EOF'
---
type: project
---
### `dup`

- **Worktree path**: `/old/stale/path`
- **Worktree path**: `/new/correct/path`

### `single`

- **Worktree path**: `/just/one`
DUP_EOF

err=$(env MERCURY_LANE_MARKER='[LANE=dup]' bash "$ASSERTION" \
  --memory-dir "$DUP_MEM" --cwd '/new/correct/path' --branch develop 2>&1)
rc=$?
assert_eq "duplicate Worktree path -> rc=6" "6" "$rc"
assert_contains "duplicate message includes count" "Worktree path bullets" "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=single]' bash "$ASSERTION" \
  --memory-dir "$DUP_MEM" --cwd '/just/one' --branch develop 2>&1)
rc=$?
assert_eq "single bullet still works -> rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# Codex iter1 Medium #1: encode_path normalization — trailing slash
# in Worktree path field or cwd MUST NOT cause false-block.
# ───────────────────────────────────────────────────────────────

printf '\n=== encode_path trailing-slash normalization (Codex M#1 regression) ===\n'

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane/' --branch develop 2>&1)
rc=$?
assert_eq "trailing slash on cwd does not falsely block -> rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane\\' --branch develop 2>&1)
rc=$?
assert_eq "trailing backslash on cwd does not falsely block -> rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane///' --branch develop 2>&1)
rc=$?
assert_eq "multiple trailing slashes normalized -> rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# Codex iter1 Low #1: malformed earlier marker source MUST NOT block
# fallback to later sources. Documented contract: "first parseable wins".
# ───────────────────────────────────────────────────────────────

printf '\n=== Marker source fall-through on malformed earlier source (Codex L#1) ===\n'

err=$(env MERCURY_LANE_MARKER='garbage' BOOTSTRAP_PROMPT='[LANE=main] continue' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "MERCURY_LANE_MARKER garbage falls through to BOOTSTRAP_PROMPT -> rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=main] ' BOOTSTRAP_PROMPT='[LANE=side-multi-lane]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "trailing-space MERCURY_LANE_MARKER still parses (awk word-splits) -> rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=Bad_Name]' BOOTSTRAP_PROMPT='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "Bad_Name (uppercase+underscore) falls through to valid BOOTSTRAP_PROMPT -> rc=0" "0" "$rc"

err=$(env BOOTSTRAP_PROMPT='[LANE=Bad_Name]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop \
  <<< '[LANE=main] continue' 2>&1)
rc=$?
assert_eq "malformed BOOTSTRAP_PROMPT falls through to stdin -> rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# Codex iter2 Medium #1: backtick-quoted Worktree path must trim
# trailing whitespace inside the quoted region (invisible-typo defense).
# ───────────────────────────────────────────────────────────────

printf '\n=== Backtick-quoted trailing whitespace trim (Codex iter2 M#1) ===\n'

TRIM_MEM="$WORK_DIR/trim-mem"
mkdir -p "$TRIM_MEM"
cat > "$TRIM_MEM/LANES.md" <<'TRIM_EOF'
---
type: project
---
### `with-trail`

- **Worktree path**: `/tmp/trail-target   `
TRIM_EOF

err=$(env MERCURY_LANE_MARKER='[LANE=with-trail]' bash "$ASSERTION" \
  --memory-dir "$TRIM_MEM" --cwd '/tmp/trail-target' --branch develop 2>&1)
rc=$?
assert_eq "trailing whitespace inside backticks trimmed -> rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=with-trail]' bash "$ASSERTION" \
  --memory-dir "$TRIM_MEM" --cwd '/tmp/trail-target   ' --branch develop --format json 2>&1)
rc=$?
# After trim, expected_enc has no trailing dashes; cwd encode keeps trailing
# slash-dashes after tr conversion of spaces? No — encode_path strips trailing
# `/` and `\` only, NOT spaces; spaces don't appear in raw paths typically.
# But cwd with literal trailing spaces (encoded form differs) → rc=2.
assert_eq "cwd with trailing spaces still distinct from trimmed path -> rc=2" "2" "$rc"

# ───────────────────────────────────────────────────────────────
# Codex iter2 Low #1: [LANE=...] marker MUST be the FIRST whitespace-
# delimited token. Mid-prompt markers must NOT pass.
# ───────────────────────────────────────────────────────────────

printf '\n=== Marker first-token-only enforcement (Codex iter2 L#1) ===\n'

err=$(env MERCURY_LANE_MARKER='preamble [LANE=main] continue' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "marker not first token -> rc=1" "1" "$rc"

err=$(env BOOTSTRAP_PROMPT='garbage [LANE=main] continue' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "BOOTSTRAP_PROMPT mid-marker rejected -> rc=1" "1" "$rc"

err=$(env -u MERCURY_LANE_MARKER -u BOOTSTRAP_PROMPT bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop \
  <<< 'preamble [LANE=main] continue' 2>&1)
rc=$?
assert_eq "stdin mid-marker rejected -> rc=1" "1" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "marker as first token (canonical) still passes -> rc=0" "0" "$rc"

err=$(env BOOTSTRAP_PROMPT='[LANE=main] continue from session handoff' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "marker as first token of multi-word BOOTSTRAP_PROMPT -> rc=0" "0" "$rc"

# Codex iter3 Low #1: leading blank lines must be skipped — the contract
# is "first whitespace-delimited token", not "first line's first token".
err=$(env BOOTSTRAP_PROMPT=$'\n\n[LANE=main] continue' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "leading blank lines + marker on line 3 -> rc=0" "0" "$rc"

err=$(env -u MERCURY_LANE_MARKER -u BOOTSTRAP_PROMPT bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop \
  <<< $'   \n[LANE=main] continue' 2>&1)
rc=$?
assert_eq "stdin with whitespace-only first line + marker line 2 -> rc=0" "0" "$rc"

err=$(env BOOTSTRAP_PROMPT=$'\n\npreamble [LANE=main] continue' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop 2>&1)
rc=$?
assert_eq "blank lines + mid-token marker still rejected -> rc=1" "1" "$rc"

# ───────────────────────────────────────────────────────────────
# Code-fence edge case: a `### \`<name>\`` heading inside ``` fence
# must NOT match. Otherwise documentation examples leak into parsing.
# ───────────────────────────────────────────────────────────────

printf '\n=== Code-fence isolation ===\n'

FENCE_MEM="$WORK_DIR/fence-mem"
mkdir -p "$FENCE_MEM"
cat > "$FENCE_MEM/LANES.md" <<'FENCE_EOF'
---
type: project
---
# Fixture
### `real-lane`

- **Worktree path**: `/real/path`

```
### `fake-lane`
- **Worktree path**: `/should/not/match`
```

### `another-real`

- **Worktree path**: `/another/real`
FENCE_EOF

err=$(env MERCURY_LANE_MARKER='[LANE=fake-lane]' bash "$ASSERTION" \
  --memory-dir "$FENCE_MEM" --cwd '/should/not/match' --branch develop 2>&1)
rc=$?
assert_eq "fake-lane inside fence -> rc=4 (Worktree path missing)" "4" "$rc"
assert_contains "fake-lane fence isolation message" "no Worktree path" "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=real-lane]' bash "$ASSERTION" \
  --memory-dir "$FENCE_MEM" --cwd '/real/path' --branch develop 2>&1)
rc=$?
assert_eq "real-lane outside fence -> rc=0" "0" "$rc"

err=$(env MERCURY_LANE_MARKER='[LANE=another-real]' bash "$ASSERTION" \
  --memory-dir "$FENCE_MEM" --cwd '/another/real' --branch develop 2>&1)
rc=$?
assert_eq "another-real after fence close -> rc=0" "0" "$rc"

# ───────────────────────────────────────────────────────────────
# JSON format
# ───────────────────────────────────────────────────────────────

printf '\n=== JSON format ===\n'

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/main-lane' --branch develop --format json 2>&1)
rc=$?
assert_eq "json happy path — rc=0" "0" "$rc"
assert_contains "json output starts with {" '{"verdict"' "$err"
assert_contains "json output includes lane" '"lane":"main"' "$err"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/var/test/side-mlane' --branch develop --format json 2>&1)
rc=$?
assert_eq "json cwd mismatch — rc=2" "2" "$rc"
assert_contains "json mismatch verdict" '"cwd_mismatch"' "$err"

# Argus iter1 Minor JSON-escape regression: Windows backslash path in --cwd
# must produce VALID JSON (backslash escaped, parseable by jq).
err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd 'C:\Windows\Path\With\Backslashes' --branch develop --format json 2>&1)
rc=$?
assert_eq "json with Windows backslash path — rc=2" "2" "$rc"
assert_contains "json escapes backslashes" 'C:\\Windows' "$err"
parsed=$(printf '%s' "$err" | jq -r '.actual_cwd' 2>/dev/null)
assert_eq "jq round-trips backslash-escaped JSON" 'C:\Windows\Path\With\Backslashes' "$parsed"

err=$(env MERCURY_LANE_MARKER='[LANE=main]' bash "$ASSERTION" \
  --memory-dir "$MEMDIR" --cwd '/path/with/"quote' --branch develop --format json 2>&1)
rc=$?
assert_eq "json with literal double-quote in cwd — rc=2" "2" "$rc"
assert_contains "json escapes double-quote" '\"' "$err"
parsed=$(printf '%s' "$err" | jq -r '.actual_cwd' 2>/dev/null)
assert_eq "jq round-trips quote-escaped JSON" '/path/with/"quote' "$parsed"

# ───────────────────────────────────────────────────────────────
# Summary
# ───────────────────────────────────────────────────────────────

printf '\n=== Summary ===\n'
printf 'pass: %d\n' "$PASS"
printf 'fail: %d\n' "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  printf '\nFailures:\n'
  for f in "${FAILURES[@]}"; do
    printf '  - %s\n' "$f"
  done
  exit 1
fi
exit 0
