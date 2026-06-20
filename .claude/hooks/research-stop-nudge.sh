#!/usr/bin/env bash
# research-stop-nudge.sh — opt-in SubagentStop nudge for research agents
# Fires when a research subagent stops. Default: silent (non-blocking).
# Set MERCURY_RESEARCH_STOP_NUDGE=1 to inject a source/[UNVERIFIED] reminder
# into additionalContext so main sees the Mercury research protocol note.
# Always exits 0 (non-blocking); stdin parse failures are tolerated silently.
# Payload validation: only emits when hook_event_name=="SubagentStop" AND
# agent_type=="research". Other event/agent combinations exit silently.
# Field matching uses node JSON.parse (node is already a hook runtime dep) to
# avoid false positives from literal field strings embedded in other values.

set -euo pipefail

# Read stdin (SubagentStop payload); ignore parse errors — always non-blocking.
input="$(cat 2>/dev/null || true)"

if [ "${MERCURY_RESEARCH_STOP_NUDGE:-}" != "1" ]; then
  exit 0
fi

# Validate payload fields via true JSON parsing (node).
# Malformed JSON or missing node → "0" → silent exit 0 (graceful degradation).
matches="$(printf '%s' "$input" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const j=JSON.parse(s);process.stdout.write(j&&j.hook_event_name==="SubagentStop"&&j.agent_type==="research"?"1":"0")}catch(e){process.stdout.write("0")}})' 2>/dev/null || echo 0)"
[ "$matches" = "1" ] || exit 0

# Emit additionalContext nudge to stdout for Claude to see.
printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SubagentStop","additionalContext":"research subagent finished — before using its findings, ensure SDK/API/version claims carry source URLs or are tagged [UNVERIFIED] (Mercury research protocol)."}}'
exit 0
