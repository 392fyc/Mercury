---
name: sot-workflow
description: |
  Use this skill when the user is asking how Mercury's SoT task lifecycle works, what status comes next, which role owns a phase, or how dispatch, Main Review, acceptance, rework, and close fit together. Trigger on English and Chinese requests such as "SoT", "workflow", "task lifecycle", "state machine", "what happens after main_review", "任务流程", "状态机", "下一步是什么", "谁负责验收", "派发之后怎么走". This is a knowledge skill: it explains the lifecycle, role boundaries, state transitions, and where to find the canonical guides and templates.
---

# SoT Task Orchestration Reference

This skill provides quick reference to Mercury's task lifecycle. For the canonical source, read `.mercury/docs/guides/sot-workflow.md`.

Useful companion files:
- `.mercury/docs/guides/git-flow.md`
- `.mercury/docs/guides/kb-structure.md`
- `.mercury/roles/main.yaml`
- `.mercury/roles/dev.yaml`
- `.mercury/roles/acceptance.yaml`

## Task State Machine

```
drafted → dispatched → in_progress → implementation_done → main_review → acceptance → verified → closed
                            ↑                                    |              |
                            └────────────── rework ──────────────┘              |
                            ↑                                                   |
                            └──────────────── rework ───────────────────────────┘
                       blocked → in_progress (after unblock)
                       failed (terminal)
```

## Entry Paths

| Path | Trigger | Flow |
|------|---------|------|
| **Bug → Issue → Task** | Bug discovered, crash, unexpected behavior | Create Issue → Main triage → Create Task (link Issue) → Dispatch |
| **Planned Feature → Task** | New functionality needed | Main creates Task → Dispatch |

Issues and Tasks are independent entities. An Issue records "what happened"; a Task records "what to do about it."

## Role Responsibilities per Phase

| Phase | Who | Does What |
|-------|-----|-----------|
| Create & Dispatch | Main | Decompose request, build `create_task` params / TaskBundle context, create branch, dispatch to worker |
| Implement | Dev | Code within `allowedWriteScope`, fill `implementationReceipt`, commit + push |
| Main Review | Main | Check receipt completeness (branch, summary, changedFiles, evidence present) |
| Acceptance | Acceptance | Blind review: code + tests + runtime only, no dev narrative |
| Close / Rework | Main | Merge PR on pass, or build rework prompt on fail |

## Key Rules

- **Dev agents never switch branches** — Main creates the branch, Dev works on it
- **Direct push to develop is forbidden** — all code enters through PRs
- **Acceptance is blind** — the acceptance agent cannot see the dev's reasoning or self-assessment
- **Rework has limits** — `maxReworks` (typically 2) bounds retry cycles before escalation
- **Chinese for milestones** — completion messages use Chinese

## TaskBundle Quick Reference

Essential fields the Main Agent must populate:

| Field | Purpose |
|-------|---------|
| `title` | One-line task summary |
| `priority` | `sev-0` \| `sev-1` \| `sev-2` \| `sev-3` |
| `assignedTo` | Worker agent ID string for `create_task` |
| `codeScope` | Files to include/exclude from context |
| `readScope.requiredDocs` | Docs injected into the dispatch prompt |
| `allowedWriteScope.codePaths` | Source paths the worker may modify |
| `allowedWriteScope.kbPaths` | KB paths the worker may modify |
| `docsMustNotTouch` | Forbidden modification targets |
| `definitionOfDone` | Objectively verifiable completion criteria |

Reference sources:
- Live RPC params: `packages/orchestrator/src/task-manager.ts`
- Live task types: `packages/core/src/types.ts`
- KB template reference: `D:/Mercury/Mercury_KB/99-templates/task-bundle.template.json`

The KB template is a reference artifact, not the final authority for raw RPC parameter names. If the template and TypeScript differ, follow the TypeScript.

## Core RPC Names

These are the main orchestrator methods mentioned across the workflow:
- `get_agents`
- `create_task`
- `dispatch_task`
- `record_receipt`
- `main_review_result`
- `create_acceptance`
- `record_acceptance_result`

## Related Skills

- `dispatch-task` — Execute the dispatch workflow
- `acceptance-review` — Execute the acceptance workflow
- `web-research` — Verify external dependencies before coding
