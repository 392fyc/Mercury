---
name: dispatch-task
description: |
  Use this skill when the user wants Mercury to create and dispatch a TaskBundle through the orchestrator: create a task, split work, assign a dev agent, send work to another agent, or ask what information is needed before dispatch. Trigger on English and Chinese requests such as "dispatch", "delegate", "assign agent", "create task", "task bundle", "派发", "下发", "创建任务", "分配", "拆任务", "指派". This skill covers the Main Agent's bundle-based dev-task workflow: verify the target agent with `get_agents`, build `create_task` params, create the feature branch, and call `dispatch_task`. Invoke explicitly with $dispatch-task.
---

# Task Dispatch Workflow

You are the Main Agent for Mercury's SoT workflow.
Use this skill for the bundle-based implementation flow that dispatches work to a `dev` agent through the orchestrator.

Read these files when you need the canonical rules:
- `.mercury/docs/guides/sot-workflow.md`
- `.mercury/docs/guides/git-flow.md`
- `.mercury/roles/main.yaml`
- `.mercury/templates/dispatch-prompt.template.md`

This skill focuses on the dispatch phase only. Use `sot-workflow` if the user is asking about the whole lifecycle.

## Scope Boundaries

- This workflow is for Main Agent coordination, not direct implementation.
- The current bundle-aware RPC path dispatches to the `dev` role. Do not promise that `create_task` can target `research` or `design` unless the orchestrator code is extended.
- For runtime RPC shapes, trust `packages/orchestrator/src/task-manager.ts` and `packages/core/src/types.ts` over older KB templates.

## Step 1: Gather Requirements

Before constructing the RPC payload, confirm these fields. Ask when unclear; do not invent scope.

| Field | What to ask | Required |
|-------|-------------|----------|
| **title** | One-line summary of what needs to be done | Yes |
| **context** | Detailed context: root cause, expected behavior, constraints | Yes |
| **assignedTo** | Target dev agent ID. Check available agents via RPC `get_agents` and confirm the agent has the `dev` role | Yes |
| **priority** | `sev-0` \| `sev-1` \| `sev-2` \| `sev-3` | Yes |
| **phaseId** | Optional phase or workstream label if the team is using one | No |
| **codeScope.include** | Files/directories the worker should read | Yes |
| **codeScope.exclude** | Files/directories to skip | No |
| **allowedWriteScope.codePaths** | Paths the worker may modify | Yes |
| **allowedWriteScope.kbPaths** | KB paths the worker may modify | No |
| **readScope.requiredDocs** | Documents the worker must read before starting | Yes |
| **readScope.optionalDocs** | Helpful but non-mandatory docs | No |
| **docsMustUpdate** | Docs the worker must update as part of completion | No |
| **docsMustNotTouch** | Paths the worker must never modify | No |
| **definitionOfDone** | Objectively verifiable completion criteria | Yes |
| **requiredEvidence** | What proof the worker must provide (test output, screenshots, etc.) | No |
| **reviewConfig** | Optional review tuning, such as `diffBaseRef` or pre-check commands | No |
| **handoffToAcceptance** | Optional blind-review policy override and acceptance focus | No |
| **maxReworks** | Maximum Main-triggered rework attempts | No |

## Step 2: Check Available Agents

Use `get_agents`, not `list_agents`.

PowerShell/Codex-friendly example:

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

If you prefer curl in PowerShell, use `curl.exe`, not `curl`, because `curl` is often an alias for `Invoke-WebRequest`.

## Step 3: Build `create_task` Params

`create_task` accepts `CreateTaskParams`, not a full persisted TaskBundle. The orchestrator generates `taskId`, `status`, timestamps, `reworkCount`, and the structured `assignee` block automatically.

```json
{
  "title": "<from step 1>",
  "phaseId": "<optional>",
  "priority": "<sev-0|sev-1|sev-2|sev-3>",
  "assignedTo": "<agent-id>",
  "branch": "<optional; see branch note below>",
  "context": "<detailed description>",
  "codeScope": {
    "include": ["<paths>"],
    "exclude": ["<paths>"]
  },
  "readScope": {
    "requiredDocs": ["<doc paths>"],
    "optionalDocs": ["<doc paths>"]
  },
  "allowedWriteScope": {
    "codePaths": ["<paths>"],
    "kbPaths": ["<kb paths>"]
  },
  "docsMustUpdate": ["<paths>"],
  "docsMustNotTouch": ["CLAUDE.md", "AGENTS.md", "{Project}_KB/99-templates/"],
  "definitionOfDone": ["<criterion 1>", "<criterion 2>"],
  "requiredEvidence": ["<evidence type>"],
  "reviewConfig": {
    "diffBaseRef": "develop...HEAD"
  },
  "handoffToAcceptance": {
    "blindInputPolicy": {
      "allowed": ["Task Bundle input fields", "Codebase", "Runtime environment"],
      "forbidden": ["Dev-agent reasoning outside bundle"]
    },
    "acceptanceFocus": ["<blind acceptance check>"]
  },
  "maxReworks": 2
}
```

Reference material:
- `packages/orchestrator/src/task-manager.ts` defines `CreateTaskParams`
- `packages/core/src/types.ts` defines the live `TaskBundle`
- `D:/Mercury/Mercury_KB/99-templates/task-bundle.template.json` is a broader template reference

The KB template is useful for concepts like `handoffToAcceptance`, `contextInjection`, and `resilience`, but parts of it are legacy. For raw RPC calls, prefer the TypeScript interfaces above.

## Step 4: Persist the Task

Create the task first so the orchestrator returns the generated `taskId`:

```powershell
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$params = @{
  title = "..."
  priority = "sev-2"
  assignedTo = "dev-agent-id"
  context = "..."
  codeScope = @{ include = @("packages/orchestrator/src"); exclude = @() }
  readScope = @{ requiredDocs = @(".mercury/docs/guides/sot-workflow.md"); optionalDocs = @() }
  allowedWriteScope = @{ codePaths = @("packages/orchestrator/src"); kbPaths = @() }
  docsMustUpdate = @()
  docsMustNotTouch = @("CLAUDE.md", "AGENTS.md", "{Project}_KB/99-templates/")
  definitionOfDone = @("...", "...")
  requiredEvidence = @("test output")
}

$body = @{
  jsonrpc = "2.0"
  method = "create_task"
  params = $params
  id = 2
} | ConvertTo-Json -Depth 10

$task = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
$task.result.taskId
```

The response is a JSON-RPC envelope. The actual task object is under `result`.

## Step 5: Create the Feature Branch

After `create_task` returns `TASK-...`, create the branch from `develop`:

```powershell
git switch develop
git pull origin develop
git switch -c feature/TASK-<id>
git push -u origin feature/TASK-<id>
```

Branch naming follows `.mercury/docs/guides/git-flow.md`: `feature/TASK-<id>`.

Branch note:
- The current RPC surface does not expose a dedicated `update_task` method for setting `task.branch` after creation.
- Do not invent one.
- If branch metadata is critical to your flow, use the KB-backed workflow that persists the full bundle, or accept that the raw RPC path may dispatch with an empty `branch` field in the prompt.

## Step 6: Dispatch

Trigger the dispatch to start the worker session:

```powershell
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$body = @{
  jsonrpc = "2.0"
  method = "dispatch_task"
  params = @{ taskId = "TASK-1234abcd" }
  id = 3
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

The orchestrator will:
1. Load the TaskBundle and build KB context from `readScope.requiredDocs`
2. Build a dev role prompt using `.mercury/roles/dev.yaml`
3. Build a dispatch prompt from `.mercury/templates/dispatch-prompt.template.md`
4. Start a new agent session and send the prompt

## Step 7: Confirm and Report

After dispatch succeeds:

1. Report to the user in Chinese (milestone rule): "任务 TASK-xxx 已派发给 <agent-id>，角色: <role>，分支: feature/TASK-xxx"
2. The worker agent is now autonomous — wait for its implementation receipt
3. When the receipt arrives, proceed to Main Review (read `.mercury/docs/guides/sot-workflow.md` for next steps)

## Codex Notes

- In this Windows workspace, prefer `Invoke-RestMethod` or `curl.exe`; avoid POSIX-only syntax like `${MERCURY_RPC_PORT:-7654}`.
- If `127.0.0.1:7654` fails, first verify that the orchestrator is actually running. A connection failure does not mean the internet is blocked.
- If the loopback call works outside the sandbox but not inside it, request escalation instead of declaring the RPC dead.
- Keep the payload machine-readable. Do not embed comments inside JSON strings.

## Common Mistakes to Avoid

- **Using `list_agents`**: The current RPC method is `get_agents`.
- **Using human-friendly priorities**: The live API expects `sev-0` to `sev-3`, not `critical/high/medium/low`.
- **Treating `create_task` params as a full TaskBundle**: `create_task` takes `CreateTaskParams`; the orchestrator fills generated fields for you.
- **Assuming arbitrary target roles**: The bundle-aware dispatch path is implemented for `dev` sessions.
- **Skipping branch creation**: The worker needs a dedicated branch. Never dispatch to `develop` or `master` directly.
- **Empty definitionOfDone**: Without verifiable criteria, acceptance review cannot function. Each criterion should be objectively testable.
- **Overly broad allowedWriteScope**: Scope the worker tightly. `["packages/"]` is too broad — prefer `["packages/orchestrator/src/"]`.
- **Missing readScope docs**: If the worker needs context docs to understand the task, list them. The orchestrator injects these into the dispatch prompt.
- **Hardcoding `Mercury_KB` as a repo-relative path**: In docs and handoffs, use `{Project}_KB/99-templates/` as the canonical KB reference.
