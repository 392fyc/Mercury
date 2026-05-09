#!/usr/bin/env bash
# Mercury notify-event — CLI wrapper around adapters/mercury-notify/notify.cjs
#
# Usage: notify-event.sh <severity> <title> <body>
#   severity: info | warn | error
#   title:    short event headline (≤60 chars recommended)
#   body:     details (≤500 chars recommended)
#
# Returns 0 on POST success or graceful skip (router unreachable / token missing
# / MERCURY_NOTIFY_DISABLED set). Returns 1 only on usage error (wrong arg count).
# This is a Mercury-internal helper; portable forks can no-op delete it.
#
# Stdout: a single JSON line `{ok: bool, error?: string, skipped?: bool}` mirroring
# the underlying notify() return value.
set -euo pipefail

if [ $# -ne 3 ]; then
  echo "usage: notify-event.sh <severity> <title> <body>" >&2
  exit 1
fi

SEVERITY=$1
TITLE=$2
BODY=$3

# Whitelist severity at the system boundary — invalid values are usage errors,
# not silent passthroughs to Telegram. Aligns with router contract (severity is
# rendered as `<SEVERITY>:` prefix in the outbound message).
case "$SEVERITY" in
  info|warn|error) ;;
  *)
    echo "usage: notify-event.sh <severity> <title> <body>  # severity: info|warn|error" >&2
    exit 1
    ;;
esac

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [ -z "$REPO_ROOT" ]; then
  # POSIX parameter expansion — avoids `dirname` external dep so the script
  # still works under hardened PATH (e.g. T5 smoke uses PATH=/nonexistent).
  SELF_DIR="${0%/*}"
  [ "$SELF_DIR" = "$0" ] && SELF_DIR="."
  REPO_ROOT=$(cd "$SELF_DIR/.." 2>/dev/null && pwd 2>/dev/null || echo "")
fi

NOTIFY_MOD="$REPO_ROOT/adapters/mercury-notify/notify.cjs"
if [ ! -f "$NOTIFY_MOD" ]; then
  printf '{"ok":false,"error":"adapter_missing"}\n'
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  printf '{"ok":false,"error":"node_missing"}\n'
  exit 0
fi

# Inline node -e wraps `require()` + the notify call in a try/catch so synchronous
# faults (missing module, broken export, syntax error introduced into notify.cjs)
# are caught before they can exit Node non-zero. We capture stdout and emit a
# uniform fallback JSON line if the captured output is empty — covering the
# pathological case where Node is signal-killed (SIGKILL / segfault / OS-level)
# before any in-process catch handler can run. This preserves the "always emit
# exactly one JSON line" contract even under runtime failure modes that bypass
# the JS-level try/catch.
OUT="$(node -e "var m,n;try{m=require(process.argv[1]);n=m&&m.notify;if(typeof n!=='function')throw new Error('notify_export_missing');n(process.argv[2],process.argv[3],process.argv[4]).then(function(r){process.stdout.write(JSON.stringify(r)+'\n')}).catch(function(e){process.stdout.write(JSON.stringify({ok:false,error:'invoke:'+(e&&e.message||String(e))})+'\n')})}catch(e){process.stdout.write(JSON.stringify({ok:false,error:'invoke:'+(e&&e.message||String(e))})+'\n')}" "$NOTIFY_MOD" "$SEVERITY" "$TITLE" "$BODY" 2>/dev/null || true)"
if [ -n "$OUT" ]; then
  printf '%s\n' "$OUT"
else
  printf '{"ok":false,"error":"invoke:node_runtime_failure"}\n'
fi
