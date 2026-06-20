#!/usr/bin/env bash
# research-stop-nudge.sh — opt-in SubagentStop nudge for research agents
# Fires when a research subagent stops. Default: silent (non-blocking).
# Set MERCURY_RESEARCH_STOP_NUDGE=1 to inject a source/[UNVERIFIED] reminder
# into additionalContext so main sees the Mercury research protocol note.
# Always exits 0 (non-blocking); stdin parse failures are tolerated silently.
# Payload validation: only emits when hook_event_name=="SubagentStop" AND
# agent_type=="research". Other event/agent combinations exit silently.

set -euo pipefail

# Read stdin (SubagentStop payload); ignore parse errors — always non-blocking.
input="$(cat 2>/dev/null || true)"

if [ "${MERCURY_RESEARCH_STOP_NUDGE:-}" != "1" ]; then
  exit 0
fi

# Validate payload fields using grep/sed (no jq dependency).
# Match "hook_event_name": "SubagentStop" (allows optional whitespace around colon).
event_ok=false
if printf '%s' "$input" | grep -qE '"hook_event_name"\s*:\s*"SubagentStop"'; then
  event_ok=true
fi

# Match "agent_type": "research"
agent_ok=false
if printf '%s' "$input" | grep -qE '"agent_type"\s*:\s*"research"'; then
  agent_ok=true
fi

if [ "$event_ok" != "true" ] || [ "$agent_ok" != "true" ]; then
  exit 0
fi

# Emit additionalContext nudge to stdout for Claude to see.
printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SubagentStop","additionalContext":"research subagent finished — before using its findings, ensure SDK/API/version claims carry source URLs or are tagged [UNVERIFIED] (Mercury research protocol)."}}'
exit 0
