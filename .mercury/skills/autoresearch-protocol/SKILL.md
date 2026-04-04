---
name: autoresearch-protocol
description: Multi-round deep research protocol — activate with /autoresearch, verify all claims via web, output structured JSON summary.
category: WORKFLOW
roles:
  - research
origin: IMPORTED
tags:
  - research
  - autoresearch
  - protocol
  - web-verify
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Autoresearch Protocol

## Activation

Type `/autoresearch` at the start of your response when the task has `researchScope: "deep"` or when the task requires 3+ research questions, cross-source verification, or architectural decision analysis.

## Protocol Steps

### Step 1 — Context Budget Check

Before starting, check your remaining context window.
- If below 20,000 tokens: skip research, output JSON summary with what you know
- If above 20,000 tokens: proceed with full protocol

### Step 2 — Question Decomposition

Break the research goal into 3-7 specific questions. For each question:
- State what claim you are verifying
- State the authoritative source you will check

### Step 3 — Multi-Round Research

For each question:
1. `WebSearch` with current year in query
2. `WebFetch` on the primary source URL
3. Cross-check against a second source
4. Record: claim, source URL, confidence (high/medium/low/unverified)

### Step 4 — Synthesis

- Group findings by theme
- Flag contradictions between sources
- Mark claims that could not be verified as `UNVERIFIED`

### Step 5 — Output JSON Summary (ALWAYS LAST)

Output the structured summary as your FINAL message before writing to KB.

```json
{
  "researcher": "<agent-id>",
  "summary": "<one paragraph>",
  "findings": ["<finding 1>", "<finding 2>"],
  "recommendations": ["<recommendation 1>"],
  "verification_score": 4.2,
  "sources": ["<url1>", "<url2>"]
}
```

## Quality Gate

`verification_score` is 0-5 scale:
- 5.0: All claims web-verified against official docs
- 4.0-4.9: Most claims verified, minor gaps noted
- 3.0-3.9: Partial verification, some claims marked UNVERIFIED
- Below 3.0: Too many unverified claims — flag for additional research round
