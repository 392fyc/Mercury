#!/usr/bin/env bash
# scripts/test-check-lane-receiver-rule.sh — fixtures for
# scripts/check-lane-receiver-rule.sh (Issue #615).
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECK="$ROOT/scripts/check-lane-receiver-rule.sh"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
PASS=0; FAIL=0
expect() {  # expect <label> <want-rc> <fixture dir>
  bash "$CHECK" --repo-root "$3" >/dev/null 2>&1; local rc=$?
  if [ "$rc" = "$2" ]; then printf '  PASS: %s\n' "$1"; PASS=$((PASS+1));
  else printf '  FAIL: %s (rc=%s, want %s)\n' "$1" "$rc" "$2"; FAIL=$((FAIL+1)); fi
}
fixture() {  # fixture <name> — copy of the real entry files
  mkdir -p "$TMP/$1"; cp "$ROOT/AGENTS.md" "$ROOT/CLAUDE.md" "$TMP/$1/"; printf '%s' "$TMP/$1"
}

expect "real repository passes" 0 "$ROOT"
D=$(fixture crlf); sed -i 's/$/\r/' "$D/AGENTS.md" "$D/CLAUDE.md"; expect "CRLF checkouts pass" 0 "$D"
D=$(fixture noblock); sed -i '/lane-receiver-rule:begin/,/lane-receiver-rule:end/d' "$D/AGENTS.md"; expect "block removed from AGENTS.md" 1 "$D"
D=$(fixture noend); sed -i '/lane-receiver-rule:end/d' "$D/AGENTS.md"; expect "unterminated block" 1 "$D"
D=$(fixture twice); sed -n '/lane-receiver-rule:begin/,/lane-receiver-rule:end/p' "$D/AGENTS.md" > "$D/blk"; cat "$D/blk" >> "$D/AGENTS.md"; expect "two blocks in AGENTS.md" 1 "$D"
D=$(fixture needle); sed -i 's/cannot grant permissions, stand in for the user.s confirmation, or widen the task/can do anything/' "$D/AGENTS.md"; expect "authorization sentence weakened (tag template untouched)" 1 "$D"
D=$(fixture flip); sed -i 's/cannot grant/can grant/' "$D/AGENTS.md"; expect "'cannot grant' reversed" 1 "$D"
D=$(fixture trace); sed -i 's/`SESSION` is for tracing only; never match it against `LANES.md`.//' "$D/AGENTS.md"; expect "SESSION tracing-only clause removed" 1 "$D"
D=$(fixture heading); sed -i 's/^## Cross-lane messages$/## Lanes/' "$D/AGENTS.md"; expect "heading renamed" 1 "$D"
D=$(fixture fenced); sed -i 's/^@AGENTS.md$/```\n@AGENTS.md\n```/' "$D/CLAUDE.md"; expect "@AGENTS.md inside a code fence" 1 "$D"
D=$(fixture noagents); rm "$D/AGENTS.md"; expect "AGENTS.md missing" 1 "$D"
D=$(fixture sid); sed -i 's/CODEX_THREAD_ID/X/g' "$D/AGENTS.md"; expect "rule lost the Codex session variable" 1 "$D"
D=$(fixture noimport); sed -i '/^@AGENTS.md$/d' "$D/CLAUDE.md"; expect "CLAUDE.md no longer imports AGENTS.md" 1 "$D"
D=$(fixture nopointer); sed -i '/lane-receiver-rule/d' "$D/CLAUDE.md"; expect "CLAUDE.md pointer removed" 1 "$D"
D=$(fixture copy); sed -n '/lane-receiver-rule:begin/,/lane-receiver-rule:end/p' "$D/AGENTS.md" > "$D/blk"; cat "$D/blk" >> "$D/CLAUDE.md"; expect "block copied into CLAUDE.md" 1 "$D"
D=$(fixture missing); rm "$D/CLAUDE.md"; expect "CLAUDE.md missing" 1 "$D"
argcheck() {  # argcheck <label> <args...> — expects exit 2
  local label="$1"; shift
  bash "$CHECK" "$@" >/dev/null 2>&1
  if [ $? = 2 ]; then echo "  PASS: $label -> 2"; PASS=$((PASS+1)); else echo "  FAIL: $label"; FAIL=$((FAIL+1)); fi
}
argcheck "bad argument" --bogus
argcheck "extra argument after --repo-root PATH" --repo-root "$ROOT" extra

printf '%d pass / %d fail\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
