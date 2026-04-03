---
name: dispatch-scope-validation
description: Validate TaskBundle scope fields before dispatching — ensure readScope and allowedWriteScope are correctly populated.
category: TOOL_GUIDE
roles:
  - main
origin: IMPORTED
tags:
  - dispatch
  - task
  - scope
  - validation
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Dispatch Scope Validation

## Required Fields Before Dispatch

Before calling `mcp__mercury-orchestrator__dispatch_task`, verify the TaskBundle has:

1. **readScope.requiredDocs** — list of KB paths the agent must read (non-empty for research/design)
2. **allowedWriteScope.repoPaths** — list of repo paths the agent may modify
3. **allowedWriteScope.kbPaths** — list of KB paths the agent may write (required for research/design)
4. **branch** — target git branch (required for dev tasks)
5. **definitionOfDone** — non-empty checklist

## Validation Checklist

```
[ ] taskId is non-empty and unique
[ ] role is one of: dev, research, design
[ ] branch is set (dev tasks)
[ ] readScope.requiredDocs populated (research/design tasks)
[ ] allowedWriteScope.kbPaths populated (research/design tasks)
[ ] definitionOfDone has at least one item
[ ] assignedTo refers to a known agent
```

## Common Mistakes

- Dispatching research tasks without `kbPaths` → agent cannot write findings
- Dispatching dev tasks without `branch` → agent commits to wrong branch
- Empty `definitionOfDone` → acceptance agent has nothing to check
- Missing `requiredDocs` → agent lacks context, may hallucinate
