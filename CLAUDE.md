# Mercury — Claude Code

## Identity

Agent: Claude Code
Your role is injected by the orchestrator at session start via system prompt (`# Role Assignment: {role}`).
If no role assignment is received, refer to the dispatch prompt or handoff packet.
Role definitions: `.mercury/roles/{role}.yaml`

## Task Triage (Main Agent only)

When creating tasks as Main Agent, assess complexity and route accordingly:

| Complexity | Criteria | Routing |
|------------|----------|---------|
| Simple | Single file, clear fix, no research needed | Fastest available dev agent |
| Medium | Multi-file, some ambiguity, may need research | Standard dev agent |
| Complex | Architecture change, cross-package, research-heavy | Most capable dev agent, include research phase |

Fill `modelRecommendation` in TaskBundle when applicable. Orchestrator will use this for future auto-routing.

## Navigation

Read these docs on demand when you need the corresponding information:

| Topic | Path |
|-------|------|
| Role definitions & boundaries | `.mercury/roles/{role}.yaml` |
| SoT task workflow | `.mercury/docs/guides/sot-workflow.md` |
| Git branching rules | `.mercury/docs/guides/git-flow.md` |
| KB directory structure | `.mercury/docs/guides/kb-structure.md` |
| Project architecture | `.mercury/docs/guides/architecture.md` |
| Dispatch prompt templates | `.mercury/templates/` |
| Bundle templates | `Mercury_KB/99-templates/` |

## MUST

- **Commit at every checkpoint**: every milestone must be committed and pushed.
- **Code review before commit**: every milestone must be code-reviewed before committing.
- **Web search before SDK/API code**: before writing ANY code that imports an external SDK, references an API signature, or claims a package version, you MUST use WebSearch/WebFetch to verify against the vendor's official documentation. GitHub source code alone is NOT sufficient. If verification is not possible, mark claims as UNVERIFIED.
- **Agents First**: inter-agent communication uses JSON/YAML. All interactions must include agentId, model, sessionId.
- **Chinese for milestones**: return milestone completion messages in Chinese.
- **Role boundary enforcement**: operate strictly within your assigned role.
- **PR to develop**: all code merges into develop must go through a PR. Direct push to develop is forbidden.
- **Install to D drive**: install software to `D:\Program Files`, not C drive.

## DO NOT

- Do not hardcode any specific agent as Main Agent.
- Do not make adapters depend on Obsidian/KB — agents keep their own MCP/SDK architecture.
- Do not commit without code review.
- Do not guess SDK/CLI APIs from training data.
- Do not install software to C drive.
- Do not bypass the SoT task flow.
- Do not execute work outside your assigned role.
