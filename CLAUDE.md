# Mercury — Claude Code

## Identity

Agent: Claude Code
Role definitions: `.mercury/roles/{role}.yaml` (current, Phase 0 后归档)
Target: `.claude/agents/{role}.md` (Phase 0 创建后成为唯一主路径)
Migration rule: Phase 0 完成前读 `.mercury/roles/`; 完成后只读 `.claude/agents/`，旧 YAML 移入 `archive/`

## Navigation

Read these docs on demand when you need the corresponding information:

| Topic | Path |
|-------|------|
| **Project direction (最高准则)** | `.mercury/docs/DIRECTION.md` |
| **Execution plan** | `.mercury/docs/EXECUTION-PLAN.md` |
| Role definitions (current) | `.mercury/roles/{role}.yaml` |
| Agent definitions (Phase 0 创建，目录尚不存在) | `.claude/agents/*.md` |
| Git branching rules | `.mercury/docs/guides/git-flow.md` |
| GitHub Issues workflow | `.mercury/docs/guides/issue-workflow.md` |
| SoT task workflow (legacy, for reference) | `.mercury/docs/guides/sot-workflow.md` |
| KB directory structure | `.mercury/docs/guides/kb-structure.md` |
| Dispatch prompt templates | `.mercury/templates/` |
| Architecture research (PR #162) | `.mercury/docs/research/issue-158-architecture-evaluation.md` |

## MUST

- **Direction first**: all development decisions must align with `.mercury/docs/DIRECTION.md`. When in doubt, consult the direction document.
- **Issue-first workflow**: every task must have a GitHub Issue before work begins. PRs must reference the Issue (`Closes #N` / `Fixes #N` / `Resolves #N` / `Refs #N`). Agent progress updates go on the Issue as comments.
- **Commit at every checkpoint**: every milestone must be committed and pushed.
- **Dual-verify before commit**: every milestone must pass `/dual-verify` (parallel Claude Code deep-review + Codex code-audit) before committing. Do not use `/auto-verify` alone as the pre-commit gate.
- **Web search before SDK/API code**: before writing ANY code that imports an external SDK, references an API signature, or claims a package version, you MUST use WebSearch/WebFetch to verify against the vendor's official documentation. GitHub source code alone is NOT sufficient. If verification is not possible, mark claims as UNVERIFIED.
- **Chinese for milestones**: return milestone completion messages in Chinese.
- **PR to develop**: all code merges into develop must go through a PR. Direct push to develop is forbidden.
- **Install to D drive**: install software to `D:\Program Files`, not C drive.
- **Modular design**: every new feature must be independently detachable. If it cannot be used outside Mercury, the coupling is too deep.
- **No self-research**: if an external project can solve the problem, mount it via submodule rather than reimplementing.

## DO NOT

- Do not build custom orchestrator layers — use Claude Code native sub-agents and skills.
- Do not guess SDK/CLI APIs from training data.
- Do not install software to C drive.
- Do not commit without running `/dual-verify`.
- Do not create PRs without an associated GitHub Issue.
- Do not build features that assume the model is weak — design for upward compatibility.
- Do not create adapters exceeding 200 lines — rethink the mounting approach if this happens.
