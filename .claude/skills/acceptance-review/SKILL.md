---
name: acceptance-review
description: |
  Guide the Main Agent through Mercury's acceptance review workflow: perform Main Review on an implementation receipt, then create an AcceptanceBundle and dispatch a blind review to the acceptance agent, then process the verdict (pass/fail/rework). Use this skill when a dev agent has completed work and submitted a receipt, when the user says "验收", "acceptance", "blind review", "盲审", "review task", "审核任务", "检查完成情况", or asks to verify, review, or accept completed work. Also use after receiving an implementation receipt from a worker agent.
disable-model-invocation: true
allowed-tools: Bash, Read, Grep, Glob
---

# Acceptance Review Workflow

You are the Main Agent coordinating acceptance testing. This skill covers the post-Main-Review phase: creating an AcceptanceBundle and dispatching it for blind review.

For the full SoT lifecycle, read `.mercury/docs/guides/sot-workflow.md`.

## Step 0: Main Review (prerequisite)

Before acceptance can begin, you must perform Main Review on the dev agent's implementation receipt. This is a completeness check — not a code review.

Verify the receipt contains: `branch`, `summary`, `changedFiles`, `evidence`. Then record your decision:

```bash
curl -s -X POST http://127.0.0.1:${MERCURY_RPC_PORT:-7654} \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"main_review_result","params":{"taskId":"<TASK-id>","decision":"approve","reason":"Receipt complete","acceptorId":"<acceptance-agent-id>"},"id":1}'
```

The `decision` field accepts `"approve"` or `"reject"`. If approved with an `acceptorId`, the orchestrator automatically triggers the acceptance flow. If you want to trigger it manually, omit `acceptorId` and proceed to Step 1.

If rejected, provide a `reason` and the task returns to `in_progress` for rework.

## Step 1: Create Acceptance Flow (if not auto-triggered)

Trigger the acceptance flow via orchestrator RPC. The orchestrator handles AcceptanceBundle creation, blind prompt construction, and session dispatch automatically.

```bash
curl -s -X POST http://127.0.0.1:${MERCURY_RPC_PORT:-7654} \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"create_acceptance","params":{"taskId":"<TASK-id>","acceptorId":"<acceptance-agent-id>"},"id":1}'
```

The orchestrator will:
1. Create an AcceptanceBundle with `blindInputPolicy` (allowed/forbidden fields)
2. Build a blind-review system prompt from `.claude/agents/acceptance.md`
3. Build an acceptance dispatch prompt from `.mercury/templates/acceptance-prompt.template.md`
4. Strip dev narrative (summary, evidence, residualRisks) from the receipt — the acceptance agent only sees `changedFiles`, `branch`, and `docsUpdated`
5. Start an acceptance session and send the prompt

## Step 2: Wait for Verdict

The acceptance agent will independently:
- Read the code changes on the branch
- Run tests and inspect runtime output
- Evaluate against the definitionOfDone
- Return a structured verdict

The verdict format:
```json
{
  "verdict": "pass | partial | fail | blocked",
  "findings": ["<specific finding 1>", "..."],
  "recommendations": ["<recommendation 1>", "..."]
}
```

## Step 3: Process the Verdict

Record the acceptance result. Note: the verdict, findings, and recommendations are nested inside a `results` object:

```bash
curl -s -X POST http://127.0.0.1:${MERCURY_RPC_PORT:-7654} \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"record_acceptance_result","params":{"acceptanceId":"<ACC-id>","results":{"verdict":"<pass|partial|fail|blocked>","findings":["..."],"recommendations":["..."]}},"id":1}'
```

Then act based on the verdict:

| Verdict | Action |
|---------|--------|
| **pass** | Transition task to `verified` → merge PR → close task. Report: "任务 TASK-xxx 验收通过，已合并。" |
| **partial** | Review findings, decide whether to accept or request rework |
| **fail** | Create a rework prompt and re-dispatch to the dev agent (check `reworkCount < maxReworks`) |
| **blocked** | Investigate the blocker, resolve environment/access issues, retry |

## Blind Review Principle

The acceptance agent operates under strict information isolation. It must never see:
- Dev agent's conversation history or reasoning
- Implementation receipt fields: `summary`, `evidence`, `residualRisks`
- Any dev narrative outside the TaskBundle

This ensures the review is based solely on code quality and test results, not the developer's self-assessment. The orchestrator enforces this automatically through `buildAcceptanceRolePrompt()`.

## Rework Flow

If acceptance fails and `reworkCount < maxReworks`:

1. The orchestrator builds a rework prompt containing the failure findings
2. Re-dispatches to the same dev agent on the same branch
3. Increments `reworkCount`
4. After rework completion, repeat the acceptance cycle

If `reworkCount >= maxReworks`, escalate to the user — the task may need scope revision or manual intervention.
