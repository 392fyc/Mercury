---
name: sot-workflow
description: |
  Use this skill when the user is asking how Mercury's SoT lifecycle works, what status comes next, which role owns a phase, or how dispatch, Main Review, acceptance, rework, and close fit together. Trigger proactively on English and Chinese requests such as "SoT", "workflow", "task lifecycle", "state machine", "what happens after main_review", "任务流程", "状态机", "下一步是什么", "谁负责验收", "派发之后怎么走". This is a reference skill: it explains lifecycle rules, role boundaries, core RPC names, and the canonical guides to cite.
---

# SoT Workflow

## When

- Use when a user asks about Mercury task states, next steps, role boundaries, or orchestration rules.
- Use when you need to explain why a task is in a given status or what should happen after dispatch, Main Review, or acceptance.
- Use as background knowledge before using `dispatch-task` or `acceptance-review`.
- This skill explains the lifecycle; it does not execute the workflow by itself.

## Pipeline

1. Read the canonical references you need:
   - `.mercury/docs/guides/sot-workflow.md`
   - `.mercury/docs/guides/git-flow.md`
   - `.mercury/docs/guides/kb-structure.md`
   - `.mercury/roles/main.yaml`
   - `.mercury/roles/dev.yaml`
   - `.mercury/roles/acceptance.yaml`
2. Map the task to one of the entry paths:
   - bug path: `Issue -> Task -> Dispatch`
   - planned feature path: `Task -> Dispatch`
3. Use the core state machine:

```text
drafted -> dispatched -> in_progress -> implementation_done -> main_review -> acceptance -> verified -> closed
blocked -> in_progress | failed
main_review -> in_progress
acceptance -> in_progress
```

4. Use the role boundaries:
   - Main: create, dispatch, review coordination, acceptance coordination, merge decisions
   - Dev: implement inside allowed scope, commit, provide receipt
   - Acceptance: blind review from code and runtime only
5. When RPC names matter, use the current ones:
   - `get_agents`
   - `create_task`
   - `dispatch_task`
   - `record_receipt`
   - `main_review_result`
   - `create_acceptance`
   - `record_acceptance_result`
6. For task shape questions, prefer runtime sources over older templates:
   - `packages/orchestrator/src/task-manager.ts`
   - `packages/core/src/types.ts`
   - `{Project}_KB/99-templates/task-bundle.template.json` as reference only

## Output

- Explain the current state, owning role, and next expected transition.
- If the user asks "what next", answer in the form `current status -> owner -> next action`.
- Point to the execution skill when appropriate:
   - `dispatch-task` for dispatch
   - `acceptance-review` for acceptance
   - `web-research` for external dependency verification

## Evidence

- Cite the exact guide or role file used for the answer.
- If a rule depends on runtime code rather than prose docs, cite `task-manager.ts` or `types.ts`.
- If you rely on the KB template, say that it is a reference artifact rather than the final source of truth.
