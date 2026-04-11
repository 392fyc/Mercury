# Mercury — Claude Code

## Identity

Agent: Claude Code
Role definitions: `.claude/agents/{role}.md` (Phase 0 已完成迁移)
Archived: `.mercury/roles/*.yaml` → `archive/roles/`

## Navigation

Read these docs on demand when you need the corresponding information:

| Topic | Path |
|-------|------|
| **Project direction (最高准则)** | `.mercury/docs/DIRECTION.md` |
| **Execution plan** | `.mercury/docs/EXECUTION-PLAN.md` |
| Agent definitions | `.claude/agents/*.md` |
| Role definitions (archived) | `archive/roles/*.yaml` |
| Git branching rules | `.mercury/docs/guides/git-flow.md` |
| GitHub Issues workflow | `.mercury/docs/guides/issue-workflow.md` |
| SoT task workflow (legacy, for reference) | `.mercury/docs/guides/sot-workflow.md` |
| KB directory structure | `.mercury/docs/guides/kb-structure.md` |
| Dispatch prompt templates | `.mercury/templates/` |
| Architecture research (PR #162) | `.mercury/docs/research/issue-158-architecture-evaluation.md` |

## Related Repositories

Mercury 的部分功能跨仓库运作。以下仓库通过 `$AGENTKB_DIR` 环境变量关联，不作为 submodule 挂载。

| Repo | Location (env var) | Purpose | 关系 |
|------|-------------------|---------|------|
| **AgentKB** | `$AGENTKB_DIR` | Memory Layer — 存储层、编译层、周期维护 | hooks 注册在全局 settings.json，脚本在 AgentKB |
| **Mercury_KB** | *(archived, 无 env var)* | 项目专属 KB (Obsidian vault, archived) | 已归档，被 AgentKB 取代 |

**跨仓库开发注意事项：**
- `dev-pipeline` 等 skill 假设单仓库工作，跨仓库任务需直接实现
- AgentKB 的 hooks/scripts 变更不走 Mercury PR 流程（独立仓库）
- AgentKB hooks 已迁移到全局 `~/.claude/settings.json`（非项目级），命令中使用 `$AGENTKB_DIR` 引用路径
- 新环境验证: 运行 `cat ~/.claude/settings.json | grep AGENTKB` 确认全局 hooks 已注册

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

## Cherry-pick protocol

When cherry-picking any file from an external project into Mercury, the SAME commit must include:

1. **Manifest entry**: add to `.mercury/state/upstream-manifest.json` — fields: `path`, `scope` (`"project"` for repo files, `"user"` for `~/.claude/` global files), `upstream_repo`, `upstream_path`, `upstream_sha_at_import` (verify via `gh api repos/{owner}/{repo}/commits/{sha}`), `upstream_license`, `import_pr`, `import_date`, `import_rationale`, `last_drift_check` (null).
2. **SKILL.md frontmatter**: add `upstream_source`, `upstream_sha`, `upstream_license`, `cherry_picked_in`, `cherry_picked_at` fields.
3. **Script header**: add 5-line comment block after shebang — `UPSTREAM`, `SOURCE`, `SHA`, `DATE`, `ISSUE`.
4. **Config/template files** (e.g. `*.example`, CLAUDE snippets): add `# Based on <upstream> (LICENSE) SHA: <sha>` attribution comment at top of file.
5. **License gate**: only cherry-pick MIT, Apache-2.0, or other permissive licenses. Record in manifest.
6. **SHA verification**: `upstream_sha_at_import` must be verified via `gh api` before committing. Never record from memory. Mark `UNKNOWN_VERIFY_MANUALLY` only if API is unreachable; list in PR body.

Drift monitoring: run `bash scripts/upstream-drift-check.sh` periodically to detect upstream changes.
