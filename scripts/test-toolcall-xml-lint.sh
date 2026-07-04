#!/usr/bin/env bash
# scripts/test-toolcall-xml-lint.sh — tests for toolcall-xml-lint.sh (Issue #527).
#
# Every tool-call marker used below is ASSEMBLED FROM VARIABLES ($lt/$gt hold the angle
# brackets; the antml: prefix is split by a printf %s) so THIS test's own source contains no
# intact marker — the same self-poisoning discipline as the linter it exercises. The linter is
# run against BOTH scripts (see "source is marker-free" cases) to prove that invariant holds.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINT="$SCRIPT_DIR/toolcall-xml-lint.sh"
SELF_TEST="$SCRIPT_DIR/test-toolcall-xml-lint.sh"

PASS=0
FAIL=0
pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1"; FAIL=$(( FAIL + 1 )); }

assert_exit() {
  local label="$1" want="$2"; shift 2
  local got=0
  "$@" >/dev/null 2>&1 || got=$?
  if [[ "$got" -eq "$want" ]]; then pass "$label (exit $got)"; else fail "$label — want exit $want, got $got"; fi
}
assert_not_contains() {
  local label="$1" hay="$2" needle="$3"
  if ! printf '%s' "$hay" | grep -qF "$needle"; then pass "$label"; else fail "$label — unexpectedly found the needle"; fi
}

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

lt='<'; gt='>'   # angle brackets kept out of literal source

# --- fixtures -------------------------------------------------------------------------------
clean="$tmpdir/clean.md"
printf 'Just normal prose about invoking a function and its parameters.\nNo markers here.\n' > "$clean"

# dirty fixture exercises all three bare tag kinds (invoke / parameter / function_calls),
# each assembled at runtime from $lt/$gt so this source stays marker-free.
dirty="$tmpdir/dirty.md"
{
  printf 'intro line\n'
  printf '%sinvoke name="Bash"%s\n'      "$lt" "$gt"
  printf '%sparameter name="command"%s\n' "$lt" "$gt"
  printf '%sfunction_calls%s\n'          "$lt" "$gt"
} > "$dirty"

# namespaced-prefix variant; the "antml:" prefix is split by %s so it is not spelled intact here
dirty_prefix="$tmpdir/dirty_prefix.md"
printf 'leading text an%sml:invoke trailing text\n' 't' > "$dirty_prefix"

# a fixture with ONLY a bare parameter tag — guards the coverage hole Codex flagged
dirty_param="$tmpdir/dirty_param.md"
printf 'a line then %sparameter name="x"%s and more\n' "$lt" "$gt" > "$dirty_param"

# --- tests ----------------------------------------------------------------------------------
assert_exit "clean fixture passes"                      0 bash "$LINT" "$clean"
assert_exit "dirty fixture (invoke+parameter+function_calls) fails" 1 bash "$LINT" "$dirty"
assert_exit "dirty fixture (antml: prefix) fails"       1 bash "$LINT" "$dirty_prefix"
assert_exit "bare parameter tag alone is caught"        1 bash "$LINT" "$dirty_param"
assert_exit "real repo Claude-context is clean (baseline)" 0 bash "$LINT"

# fail-closed: an explicit but non-existent path must not silently pass — exit 2, not 0
assert_exit "non-existent path arg fails closed"        2 bash "$LINT" "$tmpdir/does-not-exist.md"

# the linter's AND the test's own source must be marker-free (real self-poisoning proof now
# that the linter no longer hard-skips them)
assert_exit "linter source is marker-free"              0 bash "$LINT" "$LINT"
assert_exit "test source is marker-free"                0 bash "$LINT" "$SELF_TEST"

# verbose output must MASK the brackets — no intact marker echoed back
verbose_out="$(TOOLCALL_LINT_VERBOSE=1 bash "$LINT" "$dirty" 2>&1 || true)"
assert_not_contains "verbose output masks the invoke bracket"        "$verbose_out" "${lt}invoke"
assert_not_contains "verbose output masks the function_calls tag"    "$verbose_out" "${lt}function_calls${gt}"

echo "----------------------------------------"
echo "toolcall-xml-lint tests: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]] || exit 1
exit 0
