#!/usr/bin/env bash
# Smoke test for scripts/notify-event.sh — verifies fail-safe contract:
#   T1 usage error          → exit 1, stderr usage line
#   T2 disabled             → exit 0, JSON ok:true skipped:true
#   T3 no router            → exit 0, JSON ok:false (no_token | transport)
#   T4 adapter gone         → exit 0, JSON ok:false error:adapter_missing
#   T5 node missing         → exit 0, JSON ok:false error:node_missing
#   T6 synchronous require  → exit 0, JSON ok:false error:invoke:* (broken adapter)
#
# Runs without spawning a real router; uses unreachable port for T3.
# Run: bash scripts/notify-event.test.sh
set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
NOTIFY="$SCRIPT_DIR/notify-event.sh"
PASS=0
FAIL=0

assert() {
  local name=$1 cond=$2
  if [ "$cond" = "true" ]; then
    echo "  PASS $name"; PASS=$((PASS+1))
  else
    echo "  FAIL $name"; FAIL=$((FAIL+1))
  fi
}

echo "T1 usage error (0 args)"
out=$(bash "$NOTIFY" 2>&1 >/dev/null) ; rc=$?
assert "exit-code-1"  "$([ "$rc" -eq 1 ] && echo true || echo false)"
assert "stderr-usage" "$([[ "$out" == *"usage: notify-event.sh"* ]] && echo true || echo false)"

echo "T2 MERCURY_NOTIFY_DISABLED=1"
out=$(MERCURY_NOTIFY_DISABLED=1 bash "$NOTIFY" info "smoke-t2" "body") ; rc=$?
assert "exit-code-0"   "$([ "$rc" -eq 0 ] && echo true || echo false)"
assert "json-skipped"  "$([[ "$out" == *'"skipped":true'* ]] && echo true || echo false)"
assert "json-ok-true"  "$([[ "$out" == *'"ok":true'* ]] && echo true || echo false)"

echo "T3 unreachable router (port=1)"
# Port 1 is reserved/unbound — guarantees ECONNREFUSED.
# Result depends on token presence: no token → no_token; token present → transport.
out=$(MERCURY_ROUTER_PORT=1 bash "$NOTIFY" info "smoke-t3" "body") ; rc=$?
assert "exit-code-0"      "$([ "$rc" -eq 0 ] && echo true || echo false)"
assert "json-ok-false"    "$([[ "$out" == *'"ok":false'* ]] && echo true || echo false)"
assert "json-no-token-or-transport" "$([[ "$out" == *'"error":"no_token"'* || "$out" == *'"error":"transport"'* ]] && echo true || echo false)"

echo "T4 adapter missing (REPO_ROOT detection fallback to bogus)"
# Run from a non-git tmp dir AND with a dirname that points to non-existent ../adapters
TMPD=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/notify-test-$$")
mkdir -p "$TMPD/scripts"
cp "$NOTIFY" "$TMPD/scripts/notify-event.sh"
chmod +x "$TMPD/scripts/notify-event.sh"
out=$(cd "$TMPD" && bash "$TMPD/scripts/notify-event.sh" info "smoke-t4" "body") ; rc=$?
assert "exit-code-0"          "$([ "$rc" -eq 0 ] && echo true || echo false)"
assert "json-adapter-missing" "$([[ "$out" == *'"error":"adapter_missing"'* ]] && echo true || echo false)"
rm -rf "$TMPD"

echo "T6 synchronous require() failure (broken adapter)"
# Synthesize a notify.cjs that throws at top-level so the inline node -e
# require(NOTIFY_MOD) call raises synchronously. The wrapper's outer try/catch
# must convert this to ok:false + invoke:* JSON without exiting non-zero —
# regression coverage for the iter-1 Medium finding.
TMPD6=$(mktemp -d 2>/dev/null || echo "${TMPDIR:-/tmp}/notify-test-t6-$$")
mkdir -p "$TMPD6/scripts" "$TMPD6/adapters/mercury-notify"
cp "$NOTIFY" "$TMPD6/scripts/notify-event.sh"
chmod +x "$TMPD6/scripts/notify-event.sh"
printf "throw new Error('synthetic_require_fail');\n" > "$TMPD6/adapters/mercury-notify/notify.cjs"
out=$(cd "$TMPD6" && bash "$TMPD6/scripts/notify-event.sh" info "smoke-t6" "body" 2>/dev/null) ; rc=$?
assert "exit-code-0"             "$([ "$rc" -eq 0 ] && echo true || echo false)"
assert "json-ok-false"           "$([[ "$out" == *'"ok":false'* ]] && echo true || echo false)"
assert "json-invoke-error"       "$([[ "$out" == *'"error":"invoke:'* ]] && echo true || echo false)"
assert "json-synthetic-message"  "$([[ "$out" == *'synthetic_require_fail'* ]] && echo true || echo false)"
rm -rf "$TMPD6"

echo "T5 node binary missing (PATH=/nonexistent + absolute bash)"
# Hardened PATH guarantees `command -v node` fails. We invoke bash via $BASH
# (absolute path of the running interpreter) so the parent's `bash` lookup
# does not itself fail under PATH=/nonexistent. The script must still exit 0
# and emit the node_missing JSON line — preserving the fail-safe contract.
# Note: the script was refactored to use POSIX `${0%/*}` instead of `dirname`
# so it works even when `dirname` is unreachable on a hardened PATH.
out=$(PATH=/nonexistent "${BASH:-bash}" "$NOTIFY" info "smoke-t5" "body") ; rc=$?
assert "exit-code-0"        "$([ "$rc" -eq 0 ] && echo true || echo false)"
assert "json-node-missing"  "$([[ "$out" == *'"error":"node_missing"'* ]] && echo true || echo false)"

echo
echo "Result: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
