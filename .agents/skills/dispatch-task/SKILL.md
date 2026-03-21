---
name: dispatch-task
description: |
  Use this skill when the user wants Mercury to create and dispatch a TaskBundle, assign a dev agent, split work into sub-tasks, or hand implementation to another agent. Trigger proactively on English and Chinese requests such as "dispatch", "delegate", "assign", "create task", "task bundle", "派发", "下发", "创建任务", "分配", "拆任务", "指派". This skill is for the Main Agent's bundle-based dev-task workflow: verify the target agent with `get_agents`, build `create_task` params, create `feature/TASK-*`, and call `dispatch_task`.
---

# Dispatch Task

## When

- Use for Main Agent orchestration, not direct implementation.
- Use when a user asks to create a task, assign work, dispatch to an agent, or break a request into executable work.
- Use when the next correct step is a Mercury task handoff, even if the user does not say "dispatch".
- This workflow is the current bundle-based path for `dev` execution. Do not promise `create_task` can dispatch `research` or `design` unless the orchestrator is extended.

## Pipeline

1. Read the canonical references you need:
   - `.mercury/docs/guides/sot-workflow.md`
   - `.mercury/docs/guides/git-flow.md`
   - `.mercury/roles/main.yaml`
   - `.mercury/templates/dispatch-prompt.template.md`
2. Gather the `CreateTaskParams` inputs:
   - required: `title`, `context`, `assignedTo`, `priority`, `codeScope`, `readScope.requiredDocs`, `allowedWriteScope`, `definitionOfDone`
   - optional: `phaseId`, `docsMustUpdate`, `docsMustNotTouch`, `readScope.optionalDocs`, `requiredEvidence`, `reviewConfig`, `handoffToAcceptance`, `maxReworks`
   - use live runtime shapes from `packages/orchestrator/src/task-manager.ts` and `packages/core/src/types.ts`
   - use `{Project}_KB/99-templates/task-bundle.template.json` as a reference artifact; resolve `{Project}_KB` per your environment (e.g., `Mercury_KB` for the Mercury project)
3. Verify the target agent with `get_agents`:

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

1. Create the task with `create_task`. Use `sev-0` to `sev-3`, not human-only labels like `high` or `medium`.

```powershell
$params = @{
  title             = "..."
  priority          = "sev-2"
  assignedTo        = "dev-agent-id"
  context           = "..."
  codeScope         = @{ include = @("packages/orchestrator/src"); exclude = @() }
  readScope         = @{ requiredDocs = @(".mercury/docs/guides/sot-workflow.md"); optionalDocs = @() }
  allowedWriteScope = @{ codePaths = @("packages/orchestrator/src"); kbPaths = @() }
  docsMustUpdate    = @()
  docsMustNotTouch  = @("CLAUDE.md", "AGENTS.md", "{Project}_KB/99-templates/")
  definitionOfDone  = @("...", "...")
  requiredEvidence  = @("test output")
}

$body = @{
  jsonrpc = "2.0"
  method  = "create_task"
  params  = $params
  id      = 2
} | ConvertTo-Json -Depth 10

$task = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
$taskId = $task.result.taskId
```

1. Create the feature branch after the `taskId` is known:

```powershell
git switch develop
git pull origin develop
git switch -c "feature/$taskId"
git push -u origin "feature/$taskId"
```

1. Dispatch the task:

```powershell
$body = @{
  jsonrpc = "2.0"
  method  = "dispatch_task"
  params  = @{ taskId = $taskId }
  id      = 3
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

7. Remember two runtime constraints:
   - `create_task` accepts `CreateTaskParams`, not a full persisted TaskBundle
   - the current RPC surface does not expose a general `update_task` method for backfilling `task.branch`; do not invent one
8. If local RPC fails, check whether the orchestrator is running before blaming network access. If the loopback call is blocked only by sandboxing, request escalation.

## Output

- Return a short Chinese milestone message such as: `任务 TASK-xxxx 已派发给 <agent-id>，分支: feature/TASK-xxxx。`
- State the agent id, task id, branch name, and whether acceptance handoff was configured.
- If dispatch cannot proceed, say exactly which required field, agent capability, or RPC call is blocking it.

## Evidence

- Keep the final `taskId`, `assignedTo`, `priority`, `readScope.requiredDocs`, and `definitionOfDone`.
- Preserve the exact RPC methods used: `get_agents`, `create_task`, `dispatch_task`.
- If you had to rely on template-only fields, say so explicitly and name the source file.
