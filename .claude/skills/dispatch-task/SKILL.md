---
name: dispatch-task
description: |
  Guide the Main Agent through Mercury's complete task dispatch workflow: gather requirements from the user, construct a TaskBundle, create a feature branch, persist via orchestrator RPC, and dispatch to a worker agent (dev/research/design). Use this skill whenever the user asks to create a task, delegate work, assign an agent, decompose a request into sub-tasks, or says "dispatch", "下发", "派发", "创建任务", "分配", "assign", "delegate", "安排", "任务分解". Even if the user doesn't use these exact words, use this skill whenever work needs to be handed off to another agent — it ensures the SoT workflow is followed correctly and nothing gets skipped.
disable-model-invocation: true
allowed-tools: Bash, Read, Write, Grep, Glob, WebSearch, WebFetch
---

# Task Dispatch Workflow

You are the Main Agent orchestrating Mercury's SoT (Ship of Theseus) task flow.
This skill walks you through creating and dispatching a TaskBundle to a worker agent.

The full SoT lifecycle is documented in `.mercury/docs/guides/sot-workflow.md` — read it if you need the complete picture. This skill focuses on the dispatch phase.

## Step 1: Gather Requirements

Before constructing the TaskBundle, confirm these with the user. Do not guess — ask if unclear.

| Field | What to ask | Required |
|-------|-------------|----------|
| **title** | One-line summary of what needs to be done | Yes |
| **description** | Detailed context: root cause, expected behavior, constraints | Yes |
| **assignedTo** | Target agent ID (check available agents via RPC `get_agents`) | Yes |
| **target role** | `dev` \| `research` \| `design` — determines system prompt and permissions | Yes |
| **priority** | `sev-0` \| `sev-1` \| `sev-2` \| `sev-3` | Yes |
| **codeScope.include** | Files/directories the worker should read | Yes |
| **codeScope.exclude** | Files/directories to skip | No |
| **allowedWriteScope.codePaths** | Paths the worker may modify | Yes |
| **docsMustNotTouch** | Paths the worker must never modify | No (defaults apply) |
| **readScope.requiredDocs** | Documents the worker must read before starting | Yes |
| **definitionOfDone** | Objectively verifiable completion criteria | Yes |
| **requiredEvidence** | What proof the worker must provide (test output, screenshots, etc.) | No |
| **complexity** | `simple` \| `medium` \| `complex` — affects model recommendation | No |

## Step 2: Construct the CreateTaskParams

Build the JSON payload for `create_task`. The orchestrator generates `taskId`, `status`, and `createdAt` automatically — you only need to provide the `CreateTaskParams` fields.

```json
{
  "title": "<one-line summary>",
  "priority": "<sev-0|sev-1|sev-2|sev-3>",
  "assignedTo": "<agent-id>",
  "context": "<detailed description — root cause, expected behavior, constraints>",
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
  "docsMustNotTouch": ["CLAUDE.md", "AGENTS.md", "Mercury_KB/99-templates/"],
  "definitionOfDone": ["<objectively verifiable criterion 1>", "<criterion 2>"],
  "requiredEvidence": ["<test output, runtime proof, screenshots>"]
}
```

Optional fields (include when needed): `phaseId`, `branch`, `docsMustUpdate`, `reviewConfig`, `handoffToAcceptance`, `maxReworks`.

The full template with all fields is at `Mercury_KB/99-templates/task-bundle.template.json` — read it for the complete schema including resilience and contextInjection.

## Step 3: Persist via RPC

Send the CreateTaskParams to the orchestrator. The response contains the generated `taskId`:

```bash
curl -s -X POST http://127.0.0.1:${MERCURY_RPC_PORT:-7654} \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"create_task","params":<CreateTaskParams JSON>,"id":1}'
```

Save the returned `taskId` — you need it for the branch name and dispatch.

## Step 4: Create Feature Branch

After getting the taskId, create the working branch from `develop`:

```bash
git checkout develop && git pull origin develop
git checkout -b feature/<taskId> develop
git push -u origin feature/<taskId>
```

Branch naming follows `.mercury/docs/guides/git-flow.md`: `feature/{taskId}` from develop.

## Step 5: Dispatch

Trigger the dispatch to start the worker session:

```bash
curl -s -X POST http://127.0.0.1:${MERCURY_RPC_PORT:-7654} \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"dispatch_task","params":{"taskId":"<TASK-id>"},"id":2}'
```

The orchestrator will:
1. Load the TaskBundle and build KB context from `readScope.requiredDocs`
2. Build a role-scoped system prompt from `.mercury/roles/<role>.yaml`
3. Build a dispatch prompt from `.mercury/templates/dispatch-prompt.template.md`
4. Start a new agent session and send the prompt

## Step 6: Confirm and Report

After dispatch succeeds:

1. Report to the user in Chinese (milestone rule): "任务 TASK-xxx 已派发给 <agent-id>，角色: <role>，分支: feature/TASK-xxx"
2. The worker agent is now autonomous — wait for its implementation receipt
3. When the receipt arrives, proceed to Main Review (read `.mercury/docs/guides/sot-workflow.md` for next steps)

## Common Mistakes to Avoid

- **Skipping branch creation**: The worker needs a dedicated branch. Never dispatch to `develop` or `master` directly.
- **Empty definitionOfDone**: Without verifiable criteria, acceptance review cannot function. Each criterion should be objectively testable.
- **Overly broad allowedWriteScope**: Scope the worker tightly. `["packages/"]` is too broad — prefer `["packages/orchestrator/src/"]`.
- **Missing readScope docs**: If the worker needs context docs to understand the task, list them. The orchestrator injects these into the dispatch prompt.
- **Forgetting docsMustNotTouch defaults**: Always include `CLAUDE.md`, `AGENTS.md`, and `Mercury_KB/99-templates/` unless the task specifically requires modifying them.
