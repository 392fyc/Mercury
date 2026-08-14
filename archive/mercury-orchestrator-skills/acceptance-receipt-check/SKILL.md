---
name: acceptance-receipt-check
description: Acceptance agent must verify ImplementationReceipt fields and evidence before rendering verdict.
category: TOOL_GUIDE
roles:
  - acceptance
  - main
origin: IMPORTED
tags:
  - acceptance
  - receipt
  - verification
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Acceptance Receipt Validation

## Receipt Fields to Verify

Before calling `mcp__mercury-orchestrator__record_acceptance_result`, confirm these ImplementationReceipt fields:

| Field | Check |
|-------|-------|
| `implementer` | Non-empty agent ID |
| `branch` | Matches task bundle `branch` field |
| `summary` | Non-empty description of what was done |
| `changedFiles` | Non-empty list matching actual diff |
| `evidence` | Matches `requiredEvidence` items from task bundle |
| `docsUpdated` | Covers `docsMustUpdate` list if non-empty |
| `completedAt` | Valid ISO8601 timestamp |

## DoD Verification

For each item in `definitionOfDone`:
1. Locate evidence in the receipt or git diff
2. If not found: verdict = `fail`, include the missing item in findings

## Verdict Rules

- `pass`: ALL DoD items satisfied, ALL required evidence present
- `partial`: Most DoD items satisfied but minor gaps; include specific gaps in findings
- `fail`: One or more critical DoD items missing; always include remediation steps
- `blocked`: External blocker (infra, permissions) prevents completion

## Record After Verdict

After rendering verdict, call `mcp__mercury-orchestrator__record_receipt` to persist:
- The final verdict
- Specific findings (what failed or was noted)
- Recommendations (what the dev agent should fix in rework)
