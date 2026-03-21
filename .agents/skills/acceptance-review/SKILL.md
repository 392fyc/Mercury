---
name: acceptance-review
description: |
  Use this skill when a Mercury task has passed Main Review and needs blind acceptance, or when the user asks for acceptance review, blind review, verifying a completed task, or what happens after `main_review`. Trigger on English and Chinese requests such as "acceptance", "blind review", "verify completed task", "验收", "盲审", "验收这个任务", "做验收", "复核完成任务". This skill helps the Main Agent choose an acceptance agent with `get_agents`, call `create_acceptance`, and record the verdict correctly with `record_acceptance_result`. Invoke explicitly with $acceptance-review.
---

# Acceptance Review Workflow

You are the Main Agent coordinating blind acceptance. This skill covers the post-Main-Review phase only.

Read these files when you need the canonical rules:
- `.mercury/docs/guides/sot-workflow.md`
- `.mercury/docs/guides/git-flow.md`
- `.mercury/roles/acceptance.yaml`
- `.mercury/templates/acceptance-prompt.template.md`

## Prerequisites

Before starting acceptance, verify:

1. **Normal workflow status is `main_review`**: the intended path is implementation receipt -> Main Review -> acceptance.
2. **Main Review passed**: the receipt is complete enough to hand off.
3. **An acceptance agent is available**: check via RPC `get_agents` and select an agent that includes the `acceptance` role.

Technical note:
- `TaskManager.createAcceptance()` can technically accept `implementation_done`, `main_review`, or `acceptance`.
- The orchestrated SoT path should still treat acceptance as a post-Main-Review step. Do not skip Main Review just because the lower-level method allows it.

## Step 1: Find an Acceptance Agent

```powershell
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$body = @{
  jsonrpc = "2.0"
  method = "get_agents"
  params = @{}
  id = 1
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

Pick an agent whose configured roles include `acceptance`.

## Step 2: Create the Acceptance Flow

Trigger the acceptance flow via orchestrator RPC. The orchestrator handles AcceptanceBundle creation, blind prompt construction, and session dispatch automatically.

```powershell
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$body = @{
  jsonrpc = "2.0"
  method = "create_acceptance"
  params = @{
    taskId = "TASK-1234abcd"
    acceptorId = "acceptance-agent-id"
  }
  id = 2
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

The orchestrator will:
1. Create an AcceptanceBundle with `blindInputPolicy` (allowed/forbidden fields)
2. Build a blind-review system prompt from `.mercury/roles/acceptance.yaml`
3. Build an acceptance dispatch prompt from `.mercury/templates/acceptance-prompt.template.md`
4. Strip dev narrative (summary, evidence, residualRisks) from the receipt — the acceptance agent only sees `changedFiles`, `branch`, and `docsUpdated`
5. Start an acceptance session and send the prompt

## Step 3: Wait for the Verdict

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

## Step 4: Record the Verdict

`record_acceptance_result` expects the verdict under a nested `results` object.

```powershell
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$body = @{
  jsonrpc = "2.0"
  method = "record_acceptance_result"
  params = @{
    acceptanceId = "ACC-1234abcd"
    results = @{
      verdict = "pass"
      findings = @()
      recommendations = @()
    }
  }
  id = 3
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

Then act based on the verdict:

| Verdict | Action |
|---------|--------|
| **pass** | The RPC auto-transitions the task to `verified` then `closed`. Main still handles the PR merge into `develop` per `.mercury/docs/guides/git-flow.md`. |
| **partial** | The RPC auto-triggers rework and returns whether a new dev session is needed. |
| **fail** | The RPC auto-triggers rework and returns whether a new dev session is needed. |
| **blocked** | The task moves to `blocked`; Main must resolve the blocker before retrying acceptance. |

Read the response carefully:
- `record_acceptance_result` returns whether rework was triggered.
- If `newSession` is `true`, the existing dev session budget has been exhausted and Main needs a fresh dispatch path.

## Blind Review Principle

The acceptance agent operates under strict information isolation. It must never see:
- Dev agent's conversation history or reasoning
- Implementation receipt fields: `summary`, `evidence`, `residualRisks`
- Any dev narrative outside the TaskBundle

This ensures the review is based on code, tests, runtime output, and allowed bundle fields only. The orchestrator enforces this through `.mercury/roles/acceptance.yaml`, `buildAcceptancePrompt()`, and `buildAcceptanceRolePrompt()`.

## Rework Flow

If acceptance fails and `reworkCount < maxReworks`:

1. The orchestrator records the verdict on the AcceptanceBundle.
2. `partial` and `fail` both trigger `triggerRework(...)`.
3. The task returns to `in_progress`.
4. The orchestrator either reuses the current dev session or signals that a new session is required.
5. After rework completion and Main Review approval, repeat the acceptance cycle.

If `reworkCount >= maxReworks`, escalate to the user — the task may need scope revision or manual intervention.

## Codex Notes

- In this Windows/Codex workspace, prefer `Invoke-RestMethod` or `curl.exe`; do not use POSIX parameter expansion like `${MERCURY_RPC_PORT:-7654}`.
- If `127.0.0.1:7654` is unreachable, verify the orchestrator process first. Local loopback failure is not the same as internet access failure.
- If the call only fails because of sandbox isolation, request escalation instead of assuming the RPC method is wrong.

## Common Mistakes to Avoid

- **Using `list_agents`**: the current RPC name is `get_agents`.
- **Passing flat verdict fields**: `record_acceptance_result` requires `params.results.verdict`, `params.results.findings`, and `params.results.recommendations`.
- **Assuming `pass` merges code**: the RPC closes task state, but PR merge remains a Main Agent responsibility.
- **Starting acceptance before Main Review**: the low-level API is more permissive than the intended workflow.
