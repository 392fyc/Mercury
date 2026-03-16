# Mercury Project Rules

## MUST

- **Commit at every checkpoint**: Every major milestone MUST be committed and pushed to the remote repository to maintain clear progress history.
- **Code review before commit**: Each milestone MUST be code-reviewed BEFORE committing, not after. Quality gates are enforced pre-commit.
- **Research from live sources**: All research MUST be based on actual web queries and verified sources, not training data. The agent ecosystem evolves rapidly. This explicitly includes: slash command lists, SDK/API method signatures and parameters, CLI feature inventories, Tauri plugin APIs, and any third-party behavior. Never assume completeness from memory — always verify against official docs or source code.
- **Main Agent is user-configurable**: The Main Agent MUST be user-configurable via UI/config. Any agent (Claude Code, Codex, opencode, Gemini CLI, etc.) can be assigned as Main Agent.
- **Install to D drive**: Install software to `D:\Program Files`, not C drive, when possible.
- **Obsidian CLI for framework management**: Obsidian CLI is enabled by default for project-level task/doc management. Each project gets a `{Project}_KB` vault alongside its source folder. Agents retain their own MCP/mem0/knowledge architecture independently — only Orchestrator/TaskManager uses KB.
- **Agents First**: Inter-agent communication uses JSON/YAML (machine-readable, minimal ambiguity). All agent interactions MUST include agentId, model, and sessionId. Task files persist as JSON in KB for dashboard visualization.
- **Use Chinese for milestone summaries**: Return phase/milestone completion messages in Chinese.
- **Role boundary enforcement**: Every agent MUST operate strictly within its assigned role. Receiving a plan or code snippet does NOT authorize direct execution — work MUST be routed through the SoT task flow. See **Role Boundaries** section below.
- **Plan → TaskBundle**: When an implementation plan is received, Main Agent MUST convert it into TaskBundle(s) and dispatch via `create_task` → `dispatch_task`. Main Agent MUST NOT copy-paste implement.

## DO NOT

- DO NOT hardcode any specific agent as the Main Agent.
- DO NOT make agent adapters depend on Obsidian/KB — agents keep their own MCP/SDK architecture. Only Orchestrator-level code uses KB.
- DO NOT commit without running code review first.
- DO NOT guess or assume SDK/CLI APIs from training data — verify via web search or actual source code. This caused real bugs (e.g., missing `/skills` command intercept because the full command list was assumed from memory instead of queried live).
- DO NOT install software to the C drive when D drive is available.
- DO NOT interfere with agent-level architecture, MCP connections, or mem0 configurations — Mercury is a CLI-to-GUI wrapper, not an API platform.
- DO NOT bypass the SoT task flow. Even when a plan contains ready-to-use code snippets or exact file edits, the responsible role agent must be dispatched — no agent may self-promote to another role's scope.
- DO NOT execute work outside your assigned role. If you identify work that belongs to another role, create/dispatch a task for that role instead of doing it yourself.

## Role Boundaries

Every agent operating in Mercury MUST identify its current role and stay within that role's scope. Violating role boundaries breaks the SoT audit trail, skips review gates, and defeats multi-agent verification.

| Role | CAN do | MUST NOT do |
|------|--------|-------------|
| **main** | Create/decompose tasks, dispatch TaskBundles, Main Review (receipt sanity check), coordinate acceptance flow, communicate with user, summarize sessions | Write implementation code, run tests, modify source files directly, perform acceptance testing |
| **dev** | Read TaskBundle, implement within `allowed_write_scope`, fill `implementation_receipt`, commit code, run tests relevant to the task | Create tasks, dispatch to other agents, perform acceptance, modify files outside `allowed_write_scope`, skip receipt submission |
| **acceptance** | Read AcceptanceBundle (blind — no dev narrative), run acceptance checks, write verdict (pass/partial/fail/blocked) | Read dev conversation/reasoning, modify source code, create new tasks, communicate directly with dev agent |
| **research** | Query external sources, read docs/KB, produce research summaries for requesting agent | Modify source code, create tasks, dispatch to other agents, make architectural decisions |
| **design** | Produce design documents, UI/UX specifications, architecture proposals | Modify source code, dispatch implementation tasks, perform acceptance |

### Self-check protocol

Before executing any action, every agent MUST ask:

1. **What is my current role?** (Check session role assignment)
2. **Does this action fall within my role's CAN-do column?** If no → create/dispatch a task for the correct role instead.
3. **Am I about to write code while assigned as main/acceptance/research/design?** If yes → STOP. Dispatch to a dev agent.

### Issue → Task flow (mandatory — no shortcut permitted)

All work enters the system through one of two paths. Templates live in `Mercury_KB/templates/`.

**Path A: Bug / Problem discovered → Issue first**
```
Any role: create Issue → Main: triage → Main: create Task (linked to Issue) → dispatch
```

**Path B: Planned feature / phase work → Task directly**
```
Main: create Task → dispatch
```

**判断标准**: 如果触发原因是"发现了问题"（bug、crash、行为不符合预期），走 Path A。如果触发原因是"计划新增功能"，走 Path B。Issue 和 Task 是两个独立实体——Issue 记录"发生了什么"，Task 记录"要做什么"。跳过 Issue 直接建 Task 会丢失问题追溯链。

#### Issue 登记（Path A 必经）

| Step | Actor | Action | Template | Output |
|------|-------|--------|----------|--------|
| 0a. Report | Any role | 发现问题，填写 Issue | `issue-bundle.template.json` | `issues/ISSUE-{date}-{nnn}.json` |
| 0b. Triage | Main | 评估优先级，决定是否关联 Task | — | Update Issue status, set `linkedTaskIds` |

#### Task 执行流程

| Step | Actor | Action | Template | Output |
|------|-------|--------|----------|--------|
| 1. Create task | Main | Fill task bundle, save to KB, set `linkedIssueIds` if from Issue | `task-bundle.template.json` | `tasks/TASK-{phase}-{nnn}.json` |
| 2. Dispatch | Main | Compose short natural-language prompt, send to dev agent | `dispatch-prompt.template.md` | (agent session) |
| 3. Implement | Dev | Read TaskBundle from KB, implement within scope, fill `implementationReceipt` | (receipt fields in task bundle) | Update `tasks/TASK-{phase}-{nnn}.json` |
| 4. Main Review | Main | Sanity check on receipt — scope violations, evidence completeness | — | Update `mainReview` in task bundle |
| 5. Create acceptance | Main | Fill acceptance bundle, dispatch to acceptance agent | `acceptance-bundle.template.json` | `acceptances/ACC-{phase}-{nnn}.json` |
| 6. Blind review | Acceptance | Read AcceptanceBundle only (no dev narrative), run checks, write verdict | (result fields in acceptance bundle) | Update `acceptances/ACC-{phase}-{nnn}.json` |
| 7. Close or rework | Main | Close task (+ linked Issue) or trigger rework cycle | — | Update task bundle + issue status |

#### Supplementary templates (milestone boundaries / context overflow)

| Template | When to use | Output |
|----------|-------------|--------|
| `handoff-packet.template.json` | Session transfer between agents or context boundaries | `handoff/HANDOFF-{nnn}.json` |
| `session-context.template.json` | Milestone snapshot for recovery | `handoff/SESSION-{phase}-{desc}.json` |
| `dispatch-prompt.template.md` | Main composes relay prompt to dev/acceptance/research | (agent session — not persisted to KB) |

No shortcut is permitted. A plan with complete code ≠ permission to implement directly.

## Agent Instruction Files

Each agent CLI has its own instruction file in the project root. These files define role identity, scope boundaries, task workflow, git rules, and escalation protocol per agent.

| File | Agent | Default Role | Reads by |
|------|-------|-------------|----------|
| `CLAUDE.md` | Claude Code | main | Claude Code CLI (auto-loaded) |
| `AGENTS.md` | Codex CLI | dev | Codex CLI (auto-loaded as `AGENTS.md`) |
| `OPENCODE.md` | opencode | dev | opencode (auto-loaded) |
| `GEMINI.md` | Gemini CLI | dev | Gemini CLI (via `GEMINI_SYSTEM_MD` env var) |

**Maintenance rule**: When updating role boundaries, task workflow, or KB structure in CLAUDE.md, the corresponding sections in ALL agent instruction files MUST be updated in the same commit to stay consistent.

## Architecture

Mercury is a **CLI-to-GUI wrapper** for multi-agent collaboration:
- Tauri 2 (Rust) + Vue 3 frontend
- Node.js sidecar orchestrator (JSON-RPC 2.0 over stdio)
- SDK adapters wrap existing CLIs (Claude Code, Codex, opencode, Gemini CLI)
- SoT (Ship of Theseus) task orchestration pattern
- Flow: Vue → Tauri Rust → Node.js Orchestrator → SDK Adapters → Agent CLIs

## KB Structure

Obsidian vault: `D:\Mercury\Mercury_KB\`

| Path | Content | Write Access |
|------|---------|-------------|
| `tasks/` | TaskBundle JSON files | Main creates, Dev fills receipt |
| `acceptances/` | AcceptanceBundle JSON files | Main creates, Acceptance fills result |
| `issues/` | IssueBundle JSON files | Any role creates, Main triages |
| `handoff/` | Handoff packets, session context | Main and originating agent |
| `templates/` | Bundle templates (read-only reference) | Main only |
