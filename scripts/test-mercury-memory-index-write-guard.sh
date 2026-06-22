#!/usr/bin/env bash
# scripts/test-mercury-memory-index-write-guard.sh — Phase F.C lock-in (Issue #331).
#
# Synthetic-stdin tests for scripts/hooks/mercury-memory-index-write-guard.py.
# No real ~/.claude/ touched; per-test temp memory dir + MERCURY_MEMORY_DIR env
# override drives the resolution path.
#
# Exit 0 = all assertions pass; 1 = any assertion fail; 2 = test harness error.

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$ROOT/scripts/hooks/mercury-memory-index-write-guard.py"
PY="${PYTHON_BIN:-python}"

# On MSYS2/Cygwin, env vars whose name matches *_DIR or *_PATH are
# auto-converted from POSIX (/tmp/...) to Windows (C:/Users/.../Temp/...) at
# the fork-exec boundary into a Windows-native python.exe. The stdin JSON,
# however, is not transformed. The hook normalizes via Path.resolve(), so
# production paths (always Windows-form) stay consistent — but tests need a
# common form on both sides. Use cygpath -m where available to emit a
# mixed-form path (C:/Users/...) that survives both Bash and Python paths.
canon_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -m "$1"
  else
    printf '%s' "$1"
  fi
}

# Portable mktemp wrapper (Argus iter-2 finding): bare `mktemp -d` is GNU-only;
# BSD/macOS requires an explicit template or `-t prefix`. Use a template
# rooted at $TMPDIR (or /tmp) with a stable prefix.
portable_mktemp_d() {
  local prefix="${1:-mercury-fc-test}"
  local base="${TMPDIR:-/tmp}"
  mktemp -d "$base/$prefix.XXXXXX"
}

[ -f "$HOOK" ] || { printf 'test harness: hook script missing: %s\n' "$HOOK" >&2; exit 2; }
command -v "$PY" >/dev/null 2>&1 || { printf 'test harness: python missing\n' >&2; exit 2; }

PASS=0
FAIL=0
ASSERT=0

assert_eq() {
  local name="$1" want="$2" got="$3"
  ASSERT=$((ASSERT + 1))
  if [ "$want" = "$got" ]; then
    PASS=$((PASS + 1))
    return 0
  fi
  FAIL=$((FAIL + 1))
  printf 'FAIL %s\n  want=%s\n  got =%s\n' "$name" "$want" "$got" >&2
  return 1
}

assert_contains() {
  local name="$1" needle="$2" hay="$3"
  ASSERT=$((ASSERT + 1))
  case "$hay" in
    *"$needle"*) PASS=$((PASS + 1)); return 0 ;;
    *) FAIL=$((FAIL + 1)); printf 'FAIL %s\n  needle=%s\n  hay   =%s\n' "$name" "$needle" "$hay" >&2; return 1 ;;
  esac
}

run_hook() {
  local stdin_payload="$1"
  printf '%s' "$stdin_payload" | "$PY" "$HOOK"
}

setup_tmp_memory_dir() {
  local dir
  dir="$(canon_path "$(portable_mktemp_d wg-mem)")"
  mkdir -p "$dir"
  # NOTE: ASCII-only fixture content. Production canonical files contain UTF-8
  # em dashes (U+2014), but the MSYS2-bash heredoc + Windows-native python.exe
  # round-trip mangles multi-byte sequences inside test stdin/file pairs. The
  # write-guard logic itself is byte-agnostic (Path.read_text utf-8 + substring
  # check), so an ASCII fixture exercises the same code paths without harness
  # noise.
  cat > "$dir/MEMORY.md" <<'EOF'
# Memory Index

## Project (Session History)

<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place (Issue #330 Phase F.B). Regenerated content -->
- [sessions/S99.md](sessions/S99.md) - Auto-generated row body
<!-- END: scripts/regenerate-memory-index.sh --in-place -->

## Reference
- some-reference-row
EOF

  cat > "$dir/SESSION_INDEX.md" <<'EOF'
# Session Index

| Session | Date | Topic | Output | originSessionId |
|---------|------|-------|--------|-----------------|
<!-- BEGIN: scripts/regenerate-memory-index.sh --in-place (Issue #330 Phase F.B). Regenerated content -->
| S99 | 2026-04-28 | foo | bar | - |
<!-- END: scripts/regenerate-memory-index.sh --in-place -->

(trailing notes here are NOT in marker region)
EOF

  printf '%s' "$dir"
}

setup_tmp_memory_dir_no_markers() {
  local dir
  dir="$(canon_path "$(portable_mktemp_d wg-nomark)")"
  mkdir -p "$dir"
  printf '# Memory Index\n\nno markers here yet\n' > "$dir/MEMORY.md"
  printf '%s' "$dir"
}

# Array-based temp dir tracking (Argus iter-2 finding): space-separated string
# fails on temp paths containing spaces (common in Windows %TMP% under
# `C:\Users\<First Last>\AppData\Local\Temp`). Bash array iteration with proper
# quoting is robust.
TMP_MEM_DIR_LIST=()
cleanup() {
  local d
  for d in "${TMP_MEM_DIR_LIST[@]}"; do
    [ -n "$d" ] && rm -rf "$d" 2>/dev/null || true
  done
}
trap cleanup EXIT

# ----- Test 1: empty stdin → exit 0 silent -----
out="$(run_hook "")"
assert_eq "T1.empty-stdin.empty-output" "" "$out"

# ----- Test 2: malformed JSON → exit 0 silent -----
out="$(run_hook "{not json")"
assert_eq "T2.malformed-json.empty-output" "" "$out"

# ----- Test 3: non-Edit/Write tool → exit 0 silent -----
out="$(run_hook '{"tool_name":"Bash","tool_input":{"command":"ls"}}')"
assert_eq "T3.bash-tool.empty-output" "" "$out"

out="$(run_hook '{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}')"
assert_eq "T3.read-tool.empty-output" "" "$out"

# ----- Test 4: Edit on non-canonical path → exit 0 silent -----
out="$(run_hook '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/random.md","old_string":"a","new_string":"b"}}')"
assert_eq "T4.edit-non-canonical.empty-output" "" "$out"

# ----- Test 5: Write on canonical MEMORY.md → deny -----
mem_dir="$(setup_tmp_memory_dir)"
TMP_MEM_DIR_LIST+=("$mem_dir")
target="$mem_dir/MEMORY.md"
payload="$(printf '{"tool_name":"Write","tool_input":{"file_path":"%s","content":"x"}}' "$target")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_contains "T5.write-canonical-MEMORY.deny" '"permissionDecision": "deny"' "$out"
assert_contains "T5.write-canonical-MEMORY.reason" "MEMORY.md" "$out"

# ----- Test 6: Write on canonical SESSION_INDEX.md → deny -----
target="$mem_dir/SESSION_INDEX.md"
payload="$(printf '{"tool_name":"Write","tool_input":{"file_path":"%s","content":"x"}}' "$target")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_contains "T6.write-canonical-SESSION_INDEX.deny" '"permissionDecision": "deny"' "$out"
assert_contains "T6.write-canonical-SESSION_INDEX.reason" "SESSION_INDEX.md" "$out"

# ----- Test 7: Edit canonical MEMORY.md old_string IN marker region → deny -----
target="$mem_dir/MEMORY.md"
old_in_region='- [sessions/S99.md](sessions/S99.md) - Auto-generated row body'
payload="$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s","old_string":"%s","new_string":"hacked"}}' "$target" "$old_in_region")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_contains "T7.edit-marker-region.deny" '"permissionDecision": "deny"' "$out"

# ----- Test 8: Edit canonical MEMORY.md old_string OUTSIDE marker region → allow -----
old_outside='some-reference-row'
payload="$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s","old_string":"%s","new_string":"some-other-row"}}' "$target" "$old_outside")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_eq "T8.edit-outside-marker.allow.empty-output" "" "$out"

# ----- Test 9: MERCURY_INDEX_GUARD_DISABLED=1 → bypass even on Write canonical -----
target="$mem_dir/MEMORY.md"
payload="$(printf '{"tool_name":"Write","tool_input":{"file_path":"%s","content":"x"}}' "$target")"
out="$(printf '%s' "$payload" | env MERCURY_MEMORY_DIR="$mem_dir" MERCURY_INDEX_GUARD_DISABLED=1 "$PY" "$HOOK")"
assert_eq "T9.guard-disabled.empty-output" "" "$out"

# ----- Test 10: MERCURY_INDEX_REGENERATE=1 → bypass even on Write canonical -----
out="$(printf '%s' "$payload" | env MERCURY_MEMORY_DIR="$mem_dir" MERCURY_INDEX_REGENERATE=1 "$PY" "$HOOK")"
assert_eq "T10.regenerate-stamp.empty-output" "" "$out"

# ----- Test 11: Edit canonical file with NO markers (pre-cutover or post-rollback) → ALLOW -----
# Fail-open posture: no markers present means no managed region to protect.
# Operators can run --in-place to restore markers; until then, edits are not
# blocked. Note Write to canonical (T5/T6) is still unconditionally denied —
# the discriminator is tool_name (Edit) + region presence.
mem_dir2="$(setup_tmp_memory_dir_no_markers)"
TMP_MEM_DIR_LIST+=("$mem_dir2")
target2="$mem_dir2/MEMORY.md"
payload="$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s","old_string":"no markers here yet","new_string":"hacked"}}' "$target2")"
out="$(MERCURY_MEMORY_DIR="$mem_dir2" run_hook "$payload")"
assert_eq "T11.edit-no-markers.allow" "" "$out"

# ----- Test 11b: Edit canonical file is unreadable / missing → ALLOW (fail-open) -----
mem_dir3="$(canon_path "$(portable_mktemp_d wg-missing)")"
TMP_MEM_DIR_LIST+=("$mem_dir3")
ghost="$mem_dir3/MEMORY.md"
payload="$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s","old_string":"x","new_string":"y"}}' "$ghost")"
out="$(MERCURY_MEMORY_DIR="$mem_dir3" run_hook "$payload")"
assert_eq "T11b.edit-missing-file.allow" "" "$out"

# ----- Test 12: missing tool_input.file_path → exit 0 silent -----
out="$(run_hook '{"tool_name":"Edit","tool_input":{}}')"
assert_eq "T12.edit-no-file_path.empty-output" "" "$out"

# ----- Test 13: tool_input not a dict → exit 0 silent -----
out="$(run_hook '{"tool_name":"Edit","tool_input":"not-a-dict"}')"
assert_eq "T13.edit-bad-tool_input.empty-output" "" "$out"

# ----- Test 14: payload top-level not dict → exit 0 silent -----
out="$(run_hook '["not-a-dict"]')"
assert_eq "T14.payload-not-dict.empty-output" "" "$out"

# ----- Test 15: Edit basename matches but parent dir is unrelated → exit 0 silent -----
unrelated_dir="$(canon_path "$(portable_mktemp_d wg-unrelated)")"
TMP_MEM_DIR_LIST+=("$unrelated_dir")
unrelated_file="$unrelated_dir/MEMORY.md"
printf 'unrelated\n' > "$unrelated_file"
payload="$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s","old_string":"unrelated","new_string":"x"}}' "$unrelated_file")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_eq "T15.basename-match-wrong-dir.allow" "" "$out"

# ----- Test 16: trailing-slash + redundant '.' segments in path normalize correctly -----
# Exercises Path.resolve() canonicalization independently from T5/T6 (which
# already cover the mixed-form path case via canon_path()).
target="$mem_dir/./MEMORY.md"
payload="$(printf '{"tool_name":"Write","tool_input":{"file_path":"%s","content":"x"}}' "$target")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_contains "T16.dot-segment-path.deny" '"permissionDecision": "deny"' "$out"

# ----- Test 17: case-insensitive basename bypass (Argus iter-2 CRITICAL) -----
# On Windows (default-case-insensitive NTFS) `memory.md` resolves to the same
# file as `MEMORY.md`. A case-sensitive guard would let `memory.md` through.
# Hook now lowercases basename before matching CANONICAL_BASENAMES_LOWER.
target="$mem_dir/memory.md"
payload="$(printf '{"tool_name":"Write","tool_input":{"file_path":"%s","content":"x"}}' "$target")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_contains "T17.lowercase-bypass.deny" '"permissionDecision": "deny"' "$out"

target="$mem_dir/Session_Index.MD"
payload="$(printf '{"tool_name":"Write","tool_input":{"file_path":"%s","content":"x"}}' "$target")"
out="$(MERCURY_MEMORY_DIR="$mem_dir" run_hook "$payload")"
assert_contains "T17.mixed-case-bypass.deny" '"permissionDecision": "deny"' "$out"

# ----- Test 18: invalid UTF-8 in canonical Edit → fail-open allow (no raise) -----
# Per Argus iter-2: Path.read_text(encoding="utf-8") can raise UnicodeDecodeError
# on corrupt bytes. Hook now uses errors="replace" + catches UnicodeError so
# SessionEnd "never raises" contract holds.
mem_dir4="$(canon_path "$(portable_mktemp_d wg-utf8)")"
TMP_MEM_DIR_LIST+=("$mem_dir4")
target="$mem_dir4/MEMORY.md"
printf '\xff\xfe\xfd invalid utf-8 bytes here\n' > "$target"
payload="$(printf '{"tool_name":"Edit","tool_input":{"file_path":"%s","old_string":"x","new_string":"y"}}' "$target")"
out="$(MERCURY_MEMORY_DIR="$mem_dir4" run_hook "$payload")"
assert_eq "T18.bad-utf8.allow" "" "$out"

# ----- Summary -----
printf '\n----\n%d cases / %d assertions / %d fail\n' \
  "$((PASS + FAIL))" "$ASSERT" "$FAIL" >&2

[ "$FAIL" -eq 0 ] || exit 1
exit 0
