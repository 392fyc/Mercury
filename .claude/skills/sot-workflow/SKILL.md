---
name: sot-workflow
description: |
  Reference guide for Mercury's SoT (Ship of Theseus) task orchestration lifecycle. Provides the complete task state machine, role responsibilities, transition rules, and RPC method reference. Consult this skill whenever you need to understand the task flow, figure out what step comes next, check which RPC method to call, look up role boundaries, or understand why a task is in a particular state. Triggers on: "SoT", "任务流程", "workflow", "task lifecycle", "状态机", "what's next", "下一步", "流程", "state machine". This is background knowledge that helps you make correct orchestration decisions — load it whenever task management is involved.
user-invocable: false
---

# SoT Task Orchestration Reference

This skill provides quick reference to Mercury's task lifecycle. For the canonical source, read `.mercury/docs/guides/sot-workflow.md`.

## Task State Machine

```text
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
| Create & Dispatch | Main | Decompose request, build TaskBundle, create branch, dispatch to worker |
| Implement | Dev | Code within `allowedWriteScope`, fill `implementationReceipt`, commit + push |
| Main Review | Main | Check receipt completeness (branch, summary, changedFiles, evidence present) |
| Acceptance | Acceptance | Blind review: code + tests + runtime only, no dev narrative |
| Close / Rework | Main | Merge PR on pass, or build rework prompt on fail |

## RPC Methods Reference

| Method | Purpose | Key Params |
|--------|---------|------------|
| `create_task` | Create a TaskBundle | `CreateTaskParams` (see `/dispatch-task`) |
| `dispatch_task` | Dispatch task to worker | `taskId` |
| `record_receipt` | Dev submits implementation receipt | `taskId`, `receipt` |
| `main_review_result` | Main approves/rejects receipt | `taskId`, `decision`, `reason?`, `acceptorId?` |
| `create_acceptance` | Start blind acceptance review | `taskId`, `acceptorId` |
| `record_acceptance_result` | Record acceptance verdict | `acceptanceId`, `results: {verdict, findings, recommendations}` |
| `list_tasks` | Query tasks | `status?`, `assignedTo?` |
| `get_task` | Get single task | `taskId` |

All methods use JSON-RPC 2.0 over HTTP POST to `http://127.0.0.1:${MERCURY_RPC_PORT:-7654}`.

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
| `assignedTo` | Worker agent ID |
| `codeScope` | Files to include/exclude from context |
| `readScope.requiredDocs` | Docs injected into dispatch prompt |
| `allowedWriteScope.codePaths` | Paths the worker may modify |
| `docsMustNotTouch` | Forbidden modification targets |
| `definitionOfDone` | Objectively verifiable completion criteria |

Full template: `{Project}_KB/99-templates/task-bundle.template.json` (resolve `{Project}_KB` per environment)

## Related Skills

- `/dispatch-task` — Execute the dispatch workflow
- `/acceptance-review` — Execute the acceptance workflow
- `/web-research` — Verify external dependencies before coding
