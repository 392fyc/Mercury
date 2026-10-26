#!/usr/bin/env bash
# scripts/check-lane-receiver-rule.sh — both harnesses must load the same
# cross-lane receiver rule (Issue #615, #599 ADR D4). The rule lives once in
# AGENTS.md (Codex entry file); CLAUDE.md imports AGENTS.md (`@AGENTS.md`) and
# states that the rule applies to Claude Code too. This check fails when the
# block, the import, or that pointer disappears.
#
# Usage: scripts/check-lane-receiver-rule.sh [--repo-root PATH]
# Exit: 0 ok; 1 rule not shared; 2 invalid args.

set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ "${1:-}" = "--repo-root" ]; then
  [ -n "${2:-}" ] || { echo "check-lane-receiver-rule: --repo-root needs a value" >&2; exit 2; }
  [ $# -eq 2 ] || { echo "check-lane-receiver-rule: unexpected argument: $3" >&2; exit 2; }
  ROOT="$2"
elif [ $# -gt 0 ]; then
  echo "check-lane-receiver-rule: unknown argument: $1" >&2; exit 2
fi

err() { echo "check-lane-receiver-rule: $1" >&2; exit 1; }

A="$ROOT/AGENTS.md"; C="$ROOT/CLAUDE.md"
[ -f "$A" ] || err "AGENTS.md not found"
[ -f "$C" ] || err "CLAUDE.md not found"

BLOCK=$(awk '/<!-- lane-receiver-rule:begin/{f=1} f{print} /<!-- lane-receiver-rule:end -->/{if(f)exit}' "$A" | tr -d '\r')
case "$BLOCK" in
  *'<!-- lane-receiver-rule:end -->') ;;
  *) err "AGENTS.md has no complete lane-receiver-rule block" ;;
esac
# Needles are phrases of the rule itself, not of the tag template (the tag
# already says "not user authorization", so that phrase alone proves nothing).
for needle in 'FROM-LANE=<lane> HARNESS=<claude|codex> SESSION=<id>' '**not user authorization**' \
              'cannot grant permissions' 'goes to the user first' 'Check only `FROM-LANE` and `HARNESS`' \
              'Peers' 'tracing only' 'CLAUDE_CODE_SESSION_ID' 'CODEX_THREAD_ID'; do
  case "$BLOCK" in *"$needle"*) ;; *) err "the lane-receiver-rule block lost '$needle'" ;; esac
done
[ "$(grep -c 'lane-receiver-rule:begin' "$A")" -eq 1 ] || err "AGENTS.md must hold exactly one lane-receiver-rule block"
grep -q 'lane-receiver-rule:begin' "$C" && err "CLAUDE.md must not copy the block (it imports AGENTS.md)"
tr -d '\r' < "$A" | grep -qx '## Cross-lane messages' || err "AGENTS.md lost the '## Cross-lane messages' heading CLAUDE.md points to"
# Claude Code skips @imports inside fenced code blocks, so count only
# unfenced `@AGENTS.md` lines.
tr -d '\r' < "$C" | awk '/^[[:space:]]*(```|~~~)/{f=!f;next} !f && $0=="@AGENTS.md"{n++} END{exit n?0:1}' \
  || err "CLAUDE.md no longer imports AGENTS.md (an unfenced @AGENTS.md line)"
tr -d '\r' < "$C" | awk '/^[[:space:]]*(```|~~~)/{f=!f;next} !f && /lane-receiver-rule/{n++} END{exit n?0:1}' \
  || err "CLAUDE.md lost the pointer that the receiver rule applies to Claude Code (an unfenced line naming lane-receiver-rule)"
echo "check-lane-receiver-rule: PASS — AGENTS.md holds the rule; CLAUDE.md imports it and points to it."
