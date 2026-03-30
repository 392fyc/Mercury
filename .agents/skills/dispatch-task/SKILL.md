---
name: dispatch-task
description: |
  Guide the Main Agent through Mercury's complete task dispatch workflow: gather requirements from the user, construct a TaskBundle, create a feature branch, persist via orchestrator RPC, and dispatch to a worker agent (dev, research, or design). Use this skill whenever the user asks to create a task, delegate work, assign an agent, decompose a request into sub-tasks, or says "dispatch", "下发", "派发", "创建任务", "分配", "assign", "delegate", "安排", "任务分解". Even if the user does not use those exact words, use this skill whenever work needs to be handed off to another agent.
---

# Task Dispatch Workflow

You are the Main Agent orchestrating Mercury's SoT flow. This skill walks you through creating and dispatching a TaskBundle to a worker agent.

The full SoT lifecycle is documented in `.mercury/docs/guides/sot-workflow.md`. This skill focuses on the dispatch phase.

> Codex adaptation: in guarded Codex repos, avoid `git switch` and `git checkout` inside the active dev session. Prefer creating the feature branch in a fresh worktree from `origin/develop`, then dispatch the worker onto that worktree.

## Step 1: Gather Requirements

Before constructing the TaskBundle, confirm these with the user. Do not guess if unclear.

| Field | What to ask | Required |
|-------|-------------|----------|
| **title** | One-line summary of what needs to be done | Yes |
| **description** | Detailed context: root cause, expected behavior, constraints | Yes |
| **assignedTo** | Target agent ID (check available agents via RPC `get_agents`) | Yes |
| **target role** | `dev` \| `research` \| `design` | Yes |
| **priority** | `sev-0` \| `sev-1` \| `sev-2` \| `sev-3` | Yes |
| **codeScope.include** | Files or directories the worker should read | Yes |
| **codeScope.exclude** | Files or directories to skip | No |
| **allowedWriteScope.codePaths** | Paths the worker may modify | Yes |
| **docsMustNotTouch** | Paths the worker must never modify | No |
| **readScope.requiredDocs** | Documents the worker must read before starting | Yes |
| **definitionOfDone** | Objectively verifiable completion criteria | Yes |
| **requiredEvidence** | What proof the worker must provide | No |
| **complexity** | `simple` \| `medium` \| `complex` | No |

## Step 2: Construct the CreateTaskParams

Build the JSON payload for `create_task`. The orchestrator generates `taskId`, `status`, and `createdAt` automatically. You only provide the `CreateTaskParams` fields.

```json
{
  "title": "<one-line summary>",
  "priority": "<sev-0|sev-1|sev-2|sev-3>",
  "assignedTo": "<agent-id>",
  "context": "<detailed description>",
  "codeScope": {
    "include": ["<paths to read>"],
    "exclude": ["<paths to skip>"]
  },
  "readScope": {
    "requiredDocs": ["<doc paths injected into dispatch prompt>"]
  },
  "allowedWriteScope": {
    "codePaths": ["<paths the worker may modify>"]
  },
  "docsMustNotTouch": ["CLAUDE.md", "AGENTS.md", "99-templates/"],
  "definitionOfDone": ["<criterion 1>", "<criterion 2>"],
  "requiredEvidence": ["<test output, runtime proof, screenshots>"]
}
```

Optional fields include `phaseId`, `branch`, `docsMustUpdate`, `reviewConfig`, `handoffToAcceptance`, and `maxReworks`.

The full template with all fields is at `99-templates/task-bundle.template.json` in the KB vault.

## Step 3: Persist via RPC

Send the `CreateTaskParams` to the orchestrator. The response contains the generated `taskId`:

```powershell
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$body = @{
  jsonrpc = "2.0"
  method  = "create_task"
  params  = <CreateTaskParams hashtable>
  id      = 1
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

Save the returned `taskId`. You need it for the branch and dispatch.

## Step 4: Create Feature Branch

After getting the `taskId`, create the working branch from `develop`.

Preferred Codex-safe pattern:

```powershell
git worktree add -b "feature/$taskId" "D:\Mercury\worktrees\$taskId" origin/develop
```

Fallback pattern, only when current guard rules permit branch switching in the session:

```powershell
git switch develop
git pull origin develop
git switch -c "feature/$taskId"
git push -u origin "feature/$taskId"
```

Branch naming follows `.mercury/docs/guides/git-flow.md`: `feature/{taskId}` from `develop`.

## Step 5: Dispatch

Trigger the dispatch to start the worker session:

```powershell
$body = @{
  jsonrpc = "2.0"
  method  = "dispatch_task"
  params  = @{ taskId = "<TASK-id>" }
  id      = 2
} | ConvertTo-Json -Depth 10

Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$port" -ContentType "application/json" -Body $body
```

The orchestrator will:
1. load the TaskBundle and build KB context from `readScope.requiredDocs`
2. build a role-scoped system prompt from `.mercury/roles/<role>.yaml`
3. build a dispatch prompt from `.mercury/templates/dispatch-prompt.template.md`
4. start a new agent session and send the prompt

## Step 6: Confirm and Report

After dispatch succeeds:

1. Report to the user in Chinese: `任务 TASK-xxx 已派发给 <agent-id>，角色: <role>，分支: feature/TASK-xxx`
2. The worker agent is now autonomous. Wait for its implementation receipt.
3. When the receipt arrives, proceed to Main Review.

## Common Mistakes to Avoid

- skipping branch creation: the worker needs a dedicated branch
- empty `definitionOfDone`: acceptance review depends on objectively testable criteria
- overly broad `allowedWriteScope`
- missing `readScope.requiredDocs`
- forgetting `docsMustNotTouch` defaults
