#!/bin/bash
# DISABLED: never registered in settings.json. Redundant with CLAUDE.md MUST rules
# and Codex developer_instructions. Token cost ~100/prompt for duplicate enforcement.
# INJECT: Append mandatory web-research rules to every user prompt before Claude processes it.
# Event: UserPromptSubmit — fires before Claude starts reasoning.
# Output on stdout is added to Claude's context alongside the user prompt.
# Token cost: ~100 tokens injected per prompt. No external deps.

INPUT=$(cat)

# Extract the user's prompt text
# Prefer jq for robust JSON parsing; fall back to sed for minimal environments
if command -v jq >/dev/null 2>&1; then
  PROMPT=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null)
else
  PROMPT=$(echo "$INPUT" | sed -n 's/.*"prompt"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

# ── Detect if prompt likely involves SDK/API/CLI/external tool work ──
NEEDS_RESEARCH=""

# Check for SDK/package mentions
echo "$PROMPT" | grep -qiE 'sdk|api|cli|codex|claude.code|openai|anthropic|tauri|obsidian|npm|package' && NEEDS_RESEARCH="1"

# Check for integration/research/implementation keywords
echo "$PROMPT" | grep -qiE 'integrate|implement|install|upgrade|migrate|version|resume|session|mcp' && NEEDS_RESEARCH="1"

# If no research-sensitive keywords, stay silent (no context injection)
[ -z "$NEEDS_RESEARCH" ] && exit 0

# ── Inject mandatory context ──
cat <<'CONTEXT'
<user-prompt-submit-hook>
MANDATORY RESEARCH PROTOCOL (injected by hook, applies to ALL agents):
- Before writing code or documentation that references external SDK/API/CLI behavior:
  1. Use WebSearch to verify against OFFICIAL vendor documentation FIRST
  2. Cross-check npm/PyPI registry for package versions and publish status
  3. GitHub source code alone is NOT sufficient — repos may show dev versions
- Include source URLs when recording technical facts in handoffs, tasks, or KB
- If a claim cannot be web-verified, mark it explicitly as UNVERIFIED
- This rule applies regardless of your role (main, dev, research, acceptance)
</user-prompt-submit-hook>
CONTEXT

exit 0
