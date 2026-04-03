---
name: issue-before-task
description: Always create a GitHub Issue before creating a Task for any bug or feature — never skip the Issue step.
category: WORKFLOW
roles:
  - main
origin: IMPORTED
tags:
  - issue
  - workflow
  - bug
  - triage
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Issue-Before-Task Workflow

## The Rule

For ANY bug or feature request: **create a GitHub Issue first**, then create a Task linked to that Issue.

Never create a Task without a corresponding Issue.

## Flow

```
Bug/Feature Identified
  → mcp__mercury-orchestrator__create_issue (type: bug|feature|improvement)
  → Issue triage (assign priority, label)
  → mcp__mercury-orchestrator__create_task (linked to issue)
  → Dispatch
```

## create_issue Fields

```json
{
  "title": "Short description",
  "type": "bug|feature|improvement|question",
  "priority": "P0|P1|P2|P3",
  "source": { "reporterType": "main", "reporterId": "<agentId>" },
  "description": {
    "summary": "One paragraph",
    "details": "Reproduction steps or context",
    "evidence": ["<file:line>", "<error message>"]
  },
  "linkedTaskIds": []
}
```

## Why This Matters

Issues provide:
1. Tracking — GitHub Issues are visible to the team
2. Context — future agents can read the issue for background
3. Triage — priority is set before work starts, not during
4. Auditability — links task completion back to the reported problem

Skipping the Issue step creates invisible work and makes it impossible to trace why a change was made.
