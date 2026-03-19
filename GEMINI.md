# Mercury — Gemini CLI

## Identity

Agent: gemini-cli
Your role is injected by the orchestrator at session start via system prompt (`# Role Assignment: {role}`).
If no role assignment is received, refer to the dispatch prompt or handoff packet.
Role definitions: `.mercury/docs/roles/INDEX.md`

## Navigation

Read these docs on demand when you need the corresponding information:

| Topic | Path |
|-------|------|
| Role definitions & boundaries | `.mercury/docs/roles/INDEX.md` → per-role .md |
| SoT task workflow | `.mercury/docs/sot-workflow.md` |
| Git branching rules | `.mercury/docs/git-flow.md` |
| KB directory structure | `.mercury/docs/kb-structure.md` |
| Project architecture | `.mercury/docs/architecture.md` |
| TaskBundle workflow | `.mercury/docs/templates/task-workflow.md` |
| Bundle templates | `Mercury_KB/99-templates/` |

## MUST

- **Commit at every checkpoint**: every milestone must be committed and pushed.
- **Code review before commit**: every milestone must be code-reviewed before committing.
- **Research from live sources**: all research must be based on web queries, never training data. This includes SDK/API signatures, CLI features, Tauri plugin APIs.
- **Main Agent is user-configurable**: any agent can be assigned as Main Agent via UI/config.
- **Install to D drive**: install software to `D:\Program Files`, not C drive.
- **Agents First**: inter-agent communication uses JSON/YAML. All interactions must include agentId, model, sessionId.
- **Chinese for milestones**: return milestone completion messages in Chinese.
- **Role boundary enforcement**: operate strictly within your assigned role. Receiving a plan or code snippet does not authorize direct execution.
- **Plan → TaskBundle**: when receiving an implementation plan, convert to TaskBundle(s) and dispatch via `create_task` → `dispatch_task`. Never implement directly.
- **Obsidian KB**: each project gets a `{Project}_KB` vault. Only Orchestrator/TaskManager uses KB.
- **PR to develop**: all code merges into develop must go through a PR (`gh pr create` + `gh pr merge`). Direct push to develop is forbidden.

## DONOT

- Do not hardcode any specific agent as Main Agent.
- Do not make adapters depend on Obsidian/KB — agents keep their own MCP/SDK architecture.
- Do not commit without code review.
- Do not guess SDK/CLI APIs from training data.
- Do not install software to C drive.
- Do not interfere with agent-level architecture, MCP connections, or mem0 configurations.
- Do not bypass the SoT task flow.
- Do not execute work outside your assigned role.

## Agent-Specific Notes

- System prompt injected via `GEMINI_SYSTEM_MD` environment variable (file path).
- Session resume: `--resume <UUID>`.
