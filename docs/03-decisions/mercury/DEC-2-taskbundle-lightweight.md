# DEC-2: TaskBundle Lightweight Dispatch

**Status**: Accepted
**Date**: 2026-03-21
**Task**: TASK-WF-001 / W6

## Context

TaskBundle has 25+ fields. When dispatched to sub-agents, the full bundle
consumes ~800-1200 tokens of context. Sub-agents only need a subset to execute.

## Current State

`buildDevPrompt()` already extracts `bundleMeta` (12 fields) from the full bundle:

| Included in dispatch | Excluded (orchestrator-only) |
|---|---|
| taskId | title (embedded in prompt header) |
| assignee | status (always "dispatched" at this point) |
| priority | createdAt, closedAt, failedAt |
| branch | phaseId |
| codeScope | implementationReceipt |
| readScope | mainReview |
| allowedWriteScope | handoffToAcceptance |
| docsMustUpdate | reworkHistory (injected separately via buildReworkPrompt) |
| docsMustNotTouch | linkedIssueIds |
| definitionOfDone | originatorSessionId |
| requiredEvidence | reviewConfig |
| reworkCount, maxReworks | |

## Analysis

### Already lightweight

The dispatch prompt is already well-optimized. `bundleMeta` contains only
execution-relevant fields. Orchestrator-only lifecycle fields are excluded.

### Further reduction candidates

| Field | Tokens | Can omit? | Risk |
|---|---|---|---|
| `readScope.optionalDocs[]` | ~50-100 | Yes, when empty | Dev may miss context |
| `requiredEvidence[]` | ~30-60 | No | Dev won't know what to prove |
| `reworkCount` + `maxReworks` | ~10 | Yes, on first attempt | Minimal savings |
| `docsMustUpdate[]` when empty | ~5 | Yes | Minimal savings |

### Recommendation

1. **No structural change needed** — current `bundleMeta` extraction is already right-sized
2. **Minor optimization**: skip empty arrays in JSON serialization (~50 tokens saved)
3. **Future**: if bundles grow, use `buildReferencePrompt()` which emits file pointers instead of inline JSON

## Decision

Keep current `bundleMeta` approach. Add conditional omission of empty arrays
and empty scope objects in serialization to save ~50 tokens per dispatch.
No schema change required.

**Related**: `.mercury/docs/guides/sot-workflow.md` (dispatch lifecycle),
`packages/orchestrator/src/task-manager.ts` (`buildDevPrompt`).
