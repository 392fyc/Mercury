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

Mercury 的部分功能跨仓库运作。以下表格记录外部仓库与 Mercury 的关系。

| Repo | Location | Purpose | 关系 |
|------|----------|---------|------|
| **Memory layer (user-level)** | `~/.claude/hooks/` + `~/.claude/scripts/` | mem0 adapter + bridge + flush + session-start/end hooks | 运行时独立于任何 git 仓库；mem0 Qdrant 数据在 `~/.claude/scripts/mem0-state/` |
| **claude-handoff** | 插件仓库 <https://github.com/392fyc/claude-handoff> | Session handoff / 续接 + `session_chain` SQLite | 作为本地插件挂载在 `~/.claude/settings.json` marketplace |
| **AgentKB (archival-pending)** | `$AGENTKB_DIR` | 旧 Memory 层（Karpathy-style KB），Mercury #252 后被 mem0 取代 | 待归档；salvage 审计见 `.mercury/docs/research/agentkb-fork-salvage-audit-2026-04-17.md` |
| **Mercury_KB** | *(archived)* | 项目专属 Obsidian vault (archived) | 已归档，早于 AgentKB 被取代 |

**跨仓库开发注意事项：**
- `dev-pipeline` 等 skill 假设单仓库工作，跨仓库任务需直接实现
- 用户级 hooks / scripts 变更不走 Mercury PR 流程。相关路径里 `~/.claude` 等价于 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`；命令示例可任选一种书写，env 形式在多账户 / CI 下更可移植
- 新环境验证: 运行 `ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/"` 看到 `pre-compact.py`/`session-end.py`/`flush.py`/`mem0_hooks.py`/`mem0_bridge.py` 即为 #259 后状态；`grep AGENTKB "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"` 应返回 0 行
- 安装依赖: `cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" && uv sync` 建立 `.venv/` 并装 mem0ai + qdrant-client
- 回滚通道: `MERCURY_MEM0_DISABLED=1` / `AGENTKB_MEM0_DISABLED=1` / `uv remove mem0ai` 任一即可 no-op mem0 写入路径

**用户级变更治理（避免"仓库外漂移"）：**
- **变更记录位置**: 每次修改 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/hooks/`、`.../scripts/`、`.../settings.json` 时，在 Mercury 内开对应 Issue（类似 #259），在 Issue 下记录"命令清单 + 最终 diff 摘要 + 验证步骤"。Issue 关闭即成为该用户级变更的权威记录
- **验证清单（必须全部通过）**:
  1. `settings.json` JSON 合法（`python -c "import json; json.load(open('settings.json'))"`）
  2. 每个涉及的 hook 脚本在合成 stdin 下 exit 0（见 #259 PR body 的验证示例）
  3. 相关单测或 smoke test 通过（如 `mem0_bridge_test.py` 7/7）
  4. 一次真实 hook 触发观察无回归
- **回滚步骤**: 所有用户级变更前先 `CC="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"; cp "$CC/settings.json" "$CC/settings.json.backup-pre-<issue>"`；发现回归时 `mv` 回去即可；mem0 层额外可通过 env var 软关
- **环境依赖审计**: 定期跑 `grep -rE "AGENTKB_DIR|\$AGENTKB" "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/"` 确认未遗漏旧路径引用

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
