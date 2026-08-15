# Mercury — Codex CLI

## Identity

Agent: codex-cli
Your role is injected by the orchestrator at session start via system prompt (`# Role Assignment: {role}`).
If no role assignment is received, refer to the dispatch prompt or handoff packet.
Codex 原生运行入口：`.codex/agents/{role}.toml`；`.claude/agents/{role}.md` 保留为角色定义源与参考；legacy YAML archived at `archive/roles/{role}.yaml`

## Navigation

Read these docs on demand when you need the corresponding information:

| Topic | Path |
|-------|------|
| **Project direction (最高准则)** | `.mercury/docs/DIRECTION.md` |
| **Execution plan** | `.mercury/docs/EXECUTION-PLAN.md` |
| Role definitions & boundaries | `.codex/agents/{role}.toml`（Codex 原生运行入口）；`.claude/agents/{role}.md`（源与参考）；`archive/roles/{role}.yaml`（archived YAML） |
| Active project memory（本机专用，不纳入公开仓库） | `.mercury/memory/README.md`（存在时） |
| Git branching rules | `.mercury/docs/guides/git-flow.md` |
| GitHub Issues workflow | `.mercury/docs/guides/issue-workflow.md` |
| SoT task workflow (legacy) | `.mercury/docs/guides/sot-workflow.md` |
| KB directory structure | `.mercury/docs/guides/kb-structure.md` |
| Project architecture | `.mercury/docs/guides/architecture.md` |
| Codex hooks ADR | `.mercury/docs/research/codex-hooks-adoption-2026-05.md` |
| Codex hook config | `.codex/hooks.json` |
| Dispatch prompt templates | `.mercury/templates/` |
| Cherry-pick carve-out 细则 | `.mercury/docs/guides/cherry-pick-carve-out.md` |
| **Codex 迁移总台账 (G0-G6)** | [#571](https://github.com/392fyc/Mercury/issues/571) |
| **Codex 迁移主档**(目标 + 实测修正；与正文冲突以附录为准) | `.mercury/docs/research/issue-571-codex-migration-2026-08.md` |

需要历史上下文且本机存在 `.mercury/memory/README.md` 时，先按其 index 按需读取；该目录是本机专用记忆，不纳入公开仓库，保护归档和聊天记录也不是活跃记忆。

## Related Repositories

Mercury 的部分功能跨仓库运作。以下表格记录外部仓库与 Mercury 的关系。

| Repo | Location | Purpose | 关系 |
|------|----------|---------|------|
| **Memory layer (user-level)** | `~/.claude/hooks/` + `~/.claude/scripts/` | flush + session-start/end hooks + cost tracker | 运行时独立于任何 git 仓库 |
| **claude-handoff** | 插件仓库 <https://github.com/392fyc/claude-handoff> | Session handoff / 续接 + `session_chain` SQLite | 本地插件 |
| **Mercury_KB** | Obsidian vault（路径见 `.handoff-config` 的 `kb_dir`） | 项目专属 KB；handoff 文档落点 | **active** — 经直接文件系统访问 |
| **superpowers**（用户级插件） | `~/.codex/plugins/cache/superpowers-dev/superpowers/<版本>/` | 方法论 skill 包（14 个），其中 `systematic-debugging` 与 `verification-before-completion` 是 **Codex 侧唯一来源** | **必需依赖，非可选** — 见下方声明 |

### ⚠️ Codex 侧对 superpowers 的硬依赖（换机器必读）

2026-08-14（Issue [#571](https://github.com/392fyc/Mercury/issues/571) / G3-4）删除了
`.agents/skills/systematic-debugging/` 与 `.agents/skills/verification-before-completion/`
两个旧镜像，因为 superpowers 插件提供同名且更新的 6.3.0，两份并存会让模型在
**未文档化的优先级**下乱选。

**代价是这两个质量 skill 在 Codex 侧变成了机器级依赖**：插件装在用户目录、不在仓库里。
换一台机器或换一个维护者，如果没装插件，它们会**静默从 skill 发现列表消失** ——
不报错、不提示，只是不再被触发。（此风险由 dual-verify 盲审指出，不是事后补记。）

**新环境必须执行**：

```
codex plugin marketplace add obra/superpowers
codex plugin add superpowers@superpowers-dev
```

**自检**（判据是它进没进模型可见范围，不是磁盘上有没有文件 —— 后者曾把人骗过一次）：

```
codex debug prompt-input | grep -o 'superpowers/[^/]*/skills/[a-z-]*/SKILL\.md' | sort -u | wc -l
```

**应返回 14**。返回 0 = 插件未生效，那两个 skill 此刻不可用。

⚠️ 不要用 `grep -c` —— 所有 skill 声明挤在同一个 JSON 行里，`grep -c` 数的是**行数**（会返回 2），
不是 skill 数量。必须 `grep -o` 逐个抽出来再去重计数。

`.claude/skills/` 下的两份**未删**，Claude Code 侧不受影响。

**跨仓库开发注意事项：**
- `dev-pipeline` 等流程假设单仓库工作，跨仓库任务需直接实现
- 用户级 hooks / scripts 变更不走 Mercury PR 流程，但**必须在 Mercury 内开对应 Issue 记录**「命令清单 + 最终 diff 摘要 + 验证步骤」，并在改动前备份、留回滚步骤
- 相关路径里 `~/.claude` 等价于 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`

## MUST

- **Direction first**: all development decisions must align with `.mercury/docs/DIRECTION.md`. When in doubt, consult the direction document.
- **Issue-first workflow**: every task must have a GitHub Issue before work begins. PRs must reference the Issue (`Closes #N` / `Fixes #N` / `Resolves #N` / `Refs #N`). Agent progress updates go on the Issue as comments.
- **Commit at every checkpoint**: every milestone must be committed and pushed.
- **Dual-verify before commit**: every milestone must pass dual-verify before committing. **On Codex as the primary harness, the two lanes are two independently spawned subagents doing blind comparative review at different `model_reasoning_effort` levels** — self-review within a single agent context does not count. (The legacy definition "parallel Claude Code deep-review + Codex code-audit" assumed Claude Code was the host; it no longer applies once Codex is the only harness.)
- **Web search before SDK/API code**: before writing ANY code that imports an external SDK, references an API signature, or claims a package version, you MUST use `web_search` to verify against the vendor's official documentation. GitHub source code alone is NOT sufficient. If web_search is unavailable, mark claims as UNVERIFIED.
- **Chinese for all user-facing responses (normal, clear)**: reply to the user in clear, normal Simplified Chinese for everything — milestones included, not only completion messages. Use plain, complete sentences; avoid cryptic jargon and internet slang (English shorthand or Chinese alike). Keeping English proper nouns / commands / technical terms is fine — the test is reader comprehension, not absence of English. Code, commit messages, and PR bodies keep their own conventions (English where established).
- **PR to develop**: all code merges into develop must go through a PR. Direct push to develop is forbidden.
- **Install to D drive**: install software to `D:\Program Files`, not C drive. (Windows team-specific policy; skip on non-Windows environments.)
- **Modular design**: every new feature must be independently detachable. If it cannot be used outside Mercury, the coupling is too deep.
- **External-project adapters under `adapters/<vendor-name>/` MUST stay under 200 lines.** If an external integration needs more than that, rethink the mounting approach. This rule does NOT apply to Mercury-internal tooling under `scripts/` — internal tooling implements the protocol that lives in this repo and isn't trying to mount anything external, so it has no LOC cap. It also does NOT apply to `mercury-gui/` — the Phase 6 GUI is Mercury-internal tooling (a Tauri 2 desktop shell, not an external-project adapter). Aligns with `.mercury/docs/DIRECTION.md` §"适配层规范" and §8-2. If a reviewer flags a `scripts/` or `mercury-gui/` file for adapter size, cite this carve-out and disagree.
- **No self-research**: if an external project can solve the problem, mount it rather than reimplementing. Mounting follows the three parallel modes in `.mercury/docs/DIRECTION.md` §四 — git submodule (the default), `uvx git+<SHA>` runtime-only, or a version-pinned npm MCP server. The latter two are runtime-only exceptions and additionally require a license gate, a `.mercury/state/upstream-manifest.json` entry plus `adapters/<vendor>/UPSTREAM.md`, and drift monitoring; mode 3 must pin an exact npm version (floating `@latest` is forbidden).

## DO NOT

- Do not build custom orchestrator layers where a native mechanism exists.
- Do not guess SDK/CLI APIs from training data.
- Do not install software to C drive. (Windows only; skip on non-Windows environments.)
- Do not commit without running dual-verify.
- Do not create PRs without an associated GitHub Issue.
- Do not build features that assume the model is weak — design for upward compatibility.
- Do not create **external-project adapters** under `adapters/<vendor-name>/` exceeding 200 lines — rethink the mounting approach if this happens. (Mercury-internal tooling under `scripts/` and `mercury-gui/` is exempt — see the MUST bullet above.)
- Do not write literal tool-call XML markers (bare invoke / function_calls / parameter tags) into agent-context files (`AGENTS.md`, `CLAUDE.md`, `.claude/**`, `.codex/**`, `.mercury/docs/**`, memory, handoffs) — one literal marker can seed a self-poisoning leak that hangs a turn with no output ([#527](https://github.com/392fyc/Mercury/issues/527)). Escape them (`&lt;` / `&gt;`, or split the keyword); `scripts/toolcall-xml-lint.sh` keeps the count at zero.

## Cherry-pick protocol

When cherry-picking any file from an external project into Mercury, the SAME commit must include:

> See the carve-out guide before applying rules 1-6 to files produced by `pnpm dlx shadcn@latest add`, `pnpm create tauri-app`, or `pnpm create vite`.

1. **Manifest entry**: add to `.mercury/state/upstream-manifest.json` — fields: `path`, `scope` (`"project"` for repo files, `"user"` for global files), `upstream_repo`, `upstream_path`, `upstream_sha_at_import` (verify via `gh api repos/{owner}/{repo}/commits/{sha}`), `upstream_license`, `import_pr`, `import_date`, `import_rationale`, `last_drift_check` (null).
2. **Skill frontmatter**: add `upstream_source`, `upstream_sha`, `upstream_license`, `cherry_picked_in`, `cherry_picked_at` fields.
3. **Script header**: add 5-line comment block after shebang — `UPSTREAM`, `SOURCE`, `SHA`, `DATE`, `ISSUE`.
4. **Config/template files**: add `# Based on <upstream> (LICENSE) SHA: <sha>` attribution comment at top of file.
5. **License gate**: only cherry-pick MIT, Apache-2.0, or other permissive licenses. Record in manifest.
6. **SHA verification**: `upstream_sha_at_import` must be verified via `gh api` before committing. Never record from memory.

Drift monitoring: run `bash scripts/upstream-drift-check.sh` periodically.

**Carve-out for CLI-generated scaffolding**: files *produced* by a CLI generator (rather than lifted from an upstream commit) are exempt from rules 1-6. Full rules, the local-path guard, and the authority chain live in `.mercury/docs/guides/cherry-pick-carve-out.md`. Generators not listed there are treated as regular cherry-picks.

## Orchestration on Codex

Mercury 原本用 Claude Code 的 Dynamic Workflow（脚本确定性编排数十个 subagent）承担 repo 级审计、大规模迁移、多源交叉核查等任务。**Codex 没有等价机制**：

- `enable_fanout` 在 CLI 0.147.0 上已标 **removed**，原生确定性扇出不存在。
- Codex 的 subagent 由**模型自主决定**何时 spawn，不是脚本指定拓扑；`.codex/agents/*.toml` 只服务于模型自主 spawn，`codex exec` 拿不到。
- 确定性扇出只能由外层 Node 脚本起 N 个 `codex exec` 进程，或用 Codex SDK 编排。

替代编排层的设计与进度见 [#571](https://github.com/392fyc/Mercury/issues/571) 的 G6。在它落地之前，需要大规模并行的任务**手工切分**，不要假装有 workflow 可用。

## Agent-Specific Notes

- **项目信任是前提**：`~/.codex/config.toml` 的 `[projects.'<path>']` 条目必须用**普通路径形式**（`D:\Mercury\Mercury`），不能是扩展长度形式（`\\?\D:\...`）。后者会让 Codex 视该项目为 untrusted，从而**跳过整个项目级 `.codex/` 层**——project config、hooks、rules 全部不加载，且没有任何报错。自检：`codex debug prompt-input | grep -c dual-verify` 应 ≥ 1。
- **hook 在只读沙箱下不要写文件**：实测在 `--sandbox read-only` 下，一旦 hook 尝试写日志，整个工具调用会挂起直到超时（同一条命令从 9 秒变成 200 秒超时，日志文件停留在 0 字节）。调试 hook 时用 `GUARD_DEBUG=1` 需配合可写沙箱档位。
- **Windows 上工具调用走 pwsh**：实测 Codex 用 `pwsh.exe -Command '...'` 执行，不是 bash。hook 命令若需 Windows 专用形态，用官方的 `command_windows` / `commandWindows` 字段（TOML 两种写法都接受）。同理 `managed_dir` 在 Windows 上叫 `windows_managed_dir`。
- **防护实际是三层，顺序是「沙箱 → rules → 指令层」，前面的先生效。**
  （原先写的是「四层」并把 hook 算作最后一层，实测后已证伪 —— 见下条。）2026-08-14 实测三例：往 `C:\Program Files` 写被**沙箱**拦（`patch rejected: writing outside of the project`）；推受保护分支被**指令层**拦（Codex 引用 developer_instructions 第 6 条自行拒绝执行）；`git commit` 被 **`.codex/rules/`** 拦（原样回显了 rules 里的 justification：「Use `powershell -File scripts/codex/git-safe.ps1 commit …` so review and branch guards run first」）。
  三次都没走到 hook。**2026-08-14 补测：实际是三层，hook 那层根本不存在。**
  原先以为「只要不被前三层接走，hook 就会生效」，实测推翻了：`apply_patch` 这类
  **不被 rules 覆盖**的操作，hook 同样不触发。方法见下条。
- **`PreToolUse` 的 `apply_patch` hook 实测不触发；其余事件未测，一并按不可依赖处理**
  （Issue [#571](https://github.com/392fyc/Mercury/issues/571) / G2-1）。
  `.codex/hooks.json` 注册了 10 条命令（PreToolUse 5 / PostToolUse 2 / UserPromptSubmit 2 / Stop 1），
  `codex features list` 显示 `hooks` 为 `stable`/`true`，
  但 CLI 0.147.0 实测：真实会话里真实发生的 `apply_patch` **没有调用它的 `PreToolUse` hook**。
  **实测只覆盖了这一条路径** —— `UserPromptSubmit` / `Stop` / `PostToolUse` 三类**没有测过**，
  不能据此断言它们也不触发。这里要求「按不可依赖处理」是**保守取向**（未验证的护栏不该当作存在），
  不是已证明它们全都失效。
  **测量方法**（前四种都被判无效，别重走）：给 `scope-guard.sh` 顶部插一行无条件日志 ——
  探针文件确实被创建（证明 patch 真的发生了）、手动调用对照确实写出了日志
  （证明插桩与路径都对），而那次会话没有产生任何日志行。
  **根因 UNVERIFIED** —— Codex hooks 的官方文档页 404，无法核实是路径、schema 还是事件名的问题。
  **Codex 上真正的防线是 `.codex/rules/`**，同一次会话确认：`git push origin HEAD` 在
  router 层 `declined in 0ms` 并原样回显 justification。
- **`.codex/rules/` 的运行时自动发现是有效的**（上面第三例即证据）。但 **`codex execpolicy check` 这个子命令不做自动发现**，必须显式 `--rules <PATH>` —— 它是独立检查工具，别拿它的行为去推断运行时。
- **`codex debug prompt-input` 不含 tool 定义**，不能拿它当「某个工具不存在」的证据。
- **hosted tool 完全没有技术拦截**：`web_search` 这类工具不触发 PreToolUse/PostToolUse，
  **`.codex/rules/` 同样管不到它** —— rules 匹配的是命令前缀，够不着一个托管工具。
  所以 web-research 的强制**只剩 `developer_instructions` 的自律**，没有任何东西会拦你。
  （早先这条写的是「靠 developer_instructions 与 `.codex/rules/` 的指令层」，
  把 rules 也算了进去，与 `.codex/config.toml` 第 11 条相矛盾 —— 那会让人误以为有规则层兜底。）
- **`workspace-write` 下 `.git` / `.agents` / `.codex` 只读；`danger-full-access` 下可写**
  （2026-08-14 实测更正，此前这条写的是「始终只读」「不是配置能解的」，**两句都是错的**）。
  实测：`workspace-write` 下 `git commit` 报 `Unable to create '.git/index.lock': Permission denied`；
  换 `danger-full-access` 后同一操作成功。干净测试仓复现，非本仓配置问题。
- **提高档位不等于裸奔 —— `.codex/rules/` 仍然生效。** 实测 `danger-full-access` 下
  `git push origin HEAD` 依旧被 router 层 `declined in 0ms` 并回显 justification。
  原因是两者机制不同：**沙箱管文件系统访问，rules 管哪些命令能被发出**，
  rules 工作在命令执行之前，不受档位影响。
- **因此 Codex 能走完整开发链**：`danger-full-access` + `.codex/rules/` 拦截直接 git 命令
  + 走 `scripts/codex/git-safe.ps1`（内含受保护分支检查）。
  实证：commit `0a6c08c` 就是 Codex 自己完成暂存与提交产生的。
  日常仍用 `workspace-write`；**只在确实需要 git 写时才临时升档**。
- Codex sandbox may block network access — git push failures are expected, Main Agent handles push.
- **主次与这里原先写的相反**：`.codex/rules/` 才是 Codex 上唯一实测生效的强制层，
  `scripts/codex/*.ps1` 是第二道；hook **不是**主层，因为它根本不触发（见上条 G2-1 实测）。
  **`[features] hooks` 已于 2026-08-14 由 `true` 改为 `false`**（项目级与用户级都改，用户要求）。
  理由：hook 一条都不触发时留着 `true` 是「看起来武装、实际不响」的不确定状态，
  万一某条路径下它又响了就会跑出没人预期的脚本 —— 不作用就该关闭，而不是静默。
  `.codex/hooks.json` 与 `.claude/hooks/` 下的脚本**都保留未删**（后者仍是 Claude Code 的现役强制层，
  照常工作），上游修好后把该标志改回 `true` 即可恢复接线，无需重新配置。

<!-- MERCURY_AGENTS_MD_TAIL_SENTINEL — 末尾哨兵：本文件有 32 KiB 硬上限且超限静默截断。改动后跑 `codex debug prompt-input | grep -c MERCURY_AGENTS_MD_TAIL_SENTINEL`，返回 0 说明文件已被截断。 -->
