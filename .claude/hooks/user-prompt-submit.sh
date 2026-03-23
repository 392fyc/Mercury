#!/usr/bin/env bash
# INJECT: Append mandatory web-research protocol to EVERY user prompt.
# Event: UserPromptSubmit — fires before Claude starts reasoning.
# Always injects — no keyword gating. Unconditional injection ensures
# the research protocol is always present regardless of prompt content.
# Token cost: ~100 tokens per prompt. Acceptable for reliability.

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
