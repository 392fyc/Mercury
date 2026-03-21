---
name: acceptance-review
description: |
  Use this skill when a Mercury task has passed Main Review and needs blind acceptance, or when the user asks to verify completed work, run acceptance, or do a blind review. Trigger proactively on English and Chinese requests such as "acceptance", "blind review", "verify completed task", "验收", "盲审", "验收任务", "做验收", "复核完成任务". This skill helps the Main Agent choose an acceptance agent with `get_agents`, call `create_acceptance`, and record the verdict correctly with `record_acceptance_result`.
---

# Acceptance Review

## When

- Use after Main Review has approved a task for blind acceptance.
- Use when a user asks to verify completed implementation or run Mercury acceptance.
- Do not use this skill as a substitute for Main Review; it starts after the receipt is ready.
- The intended workflow is `implementation_done -> main_review -> acceptance`, even though the lower-level code is slightly more permissive.

## Pipeline

1. Read the canonical references you need:
   - `.mercury/docs/guides/sot-workflow.md`
   - `.mercury/docs/guides/git-flow.md`
   - `.mercury/roles/acceptance.yaml`
   - `.mercury/templates/acceptance-prompt.template.md`
2. Confirm the handoff is valid:
   - task is effectively ready for acceptance, normally from `main_review`
   - implementation receipt exists
   - `definitionOfDone` is concrete enough to evaluate
3. Find an acceptance agent with `get_agents`:

```powershell
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$body = @{
  jsonrpc = "2.0"
  method  = "get_agents"
  params  = @{}
  id      = 1
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

1. Create the acceptance flow:

```powershell
$body = @{
  jsonrpc = "2.0"
  method  = "create_acceptance"
  params  = @{
    taskId     = "TASK-1234abcd"
    acceptorId = "acceptance-agent-id"
  }
  id      = 2
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

1. Expect the blind review to use only code, runtime output, and allowed bundle fields. The acceptance agent must not rely on `summary`, `evidence`, or `residualRisks`.
1. Wait for a verdict shaped like:

```json
{
  "verdict": "pass | partial | fail | blocked",
  "findings": ["..."],
  "recommendations": ["..."]
}
```

1. Record the verdict with the nested `results` object:

```powershell
$body = @{
  jsonrpc = "2.0"
  method  = "record_acceptance_result"
  params  = @{
    acceptanceId = "ACC-1234abcd"
    results      = @{
      verdict         = "pass"
      findings        = @()
      recommendations = @()
    }
  }
  id      = 3
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

1. Interpret the result precisely:
   - `pass`: task state auto-moves to `verified` then `closed`; Main still handles the PR merge into `develop`
   - `partial` or `fail`: rework is auto-triggered and the response tells you whether a new dev session is needed
   - `blocked`: task moves to `blocked`; resolve the blocker before retrying
1. If RPC fails, verify the orchestrator process first. If loopback is sandbox-blocked, request escalation.

## Output

- Return a short Chinese milestone message such as: `任务 TASK-xxxx 已进入验收，acceptanceId=ACC-xxxx。`
- After the verdict, state the verdict, the top findings, and whether rework or a new session was triggered.
- If acceptance cannot start, say whether the blocker is status, missing receipt data, missing acceptance agent, or RPC failure.

## Evidence

- Keep the `acceptanceId`, `taskId`, `acceptorId`, `verdict`, and top findings.
- Preserve the exact RPC methods used: `get_agents`, `create_acceptance`, `record_acceptance_result`.
- If the blind-review boundary was challenged, cite `.mercury/roles/acceptance.yaml` and `.mercury/templates/acceptance-prompt.template.md`.
