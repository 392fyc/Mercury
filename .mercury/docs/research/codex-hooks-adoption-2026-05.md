# Codex CLI 0.128+ Hooks Adoption — Mercury side-bug Phase 1 Research ADR

**Issue**: [#357](https://github.com/392fyc/Mercury/issues/357) (Refs)

**Status**: Phase 1 Research — verdict **CONDITIONAL_GO (Path C — Hybrid)**

**Date**: 2026-05-08
**Session**: S2-side-bug
**Branch**: `lane/side-bug/357-codex-hooks` (Phase 2 implementation)
**Scope**: 把 Mercury `.claude/hooks/` 13-hook 集移植到 Codex CLI 0.128+ 原生 hooks 架构 + 同步 outdated docs

---

## TL;DR

Codex CLI 自 **v0.124.0 (2026-04-23)** GA 上线 lifecycle hooks，至 **v0.128.0 (2026-04-30)** + **v0.129.0 (2026-05-07)** 已具备 plugin 集成、`/hooks` 浏览器、admin 强制等扩展功能。**架构与 Claude Code hooks 高度对齐**（同样 6 events、同样 `matcher`+`type:command`+`timeout` schema、同样 exit 0/1/2 语义、JSON+TOML 双 config 形式），但有 4 个不可忽视的差异：

1. **`web_search` 无 handler**（Issue #20204 列入 silent tools）— Mercury 的 `web-research-gate.sh` / `web-research-extended-gate.sh` / `post-web-research-flag.sh` **不能** 通过 PreToolUse/PostToolUse 拦截 Codex 内置 web search
2. **无 `$CODEX_PROJECT_DIR` 等价物**（Issue #13576 同问题）— hook 必须用 `git rev-parse --show-toplevel` 解析 repo root
3. **Windows shell invocation 未文档化** — `command` field 的解析路径（直接 spawn / `cmd /c` / `bash -c`）官方文档未明，需 empirical 测试
4. **必须 enable feature flag** `[features] hooks = true`（未确认 0.128 是否默认 on）+ project 必须在 `projects.<path>.trust_level = "trusted"` 否则 `.codex/` 层 skip

Mercury 现有 13 hook 中 **9 个可直接 port**、**3 个需调整**、**1 个无对应**（SubagentStop — Codex 无该 event）。综合考虑：**应该启用 Codex hooks 作为主防线，同时保留 `.codex/rules/` + 简化的 `scripts/codex/git-safe.ps1` 作为 defense-in-depth**。这就是 Path C 的形态。

**CONDITIONAL** 项（Phase 2 实施前 / 实施中 验证状态）：

1. ~~用户 `~/.codex/` 当前 Codex CLI 版本 ≥ v0.128~~ → **EMPIRICAL: 用户原 0.117.0；session 升级到 0.129.0 via `npm install -g @openai/codex@latest`**
2. ~~`[features] hooks = true` 在 user 或 repo config 启用~~ → **EMPIRICAL: canonical flag 名 `hooks`（per Codex PR #20522，原 `codex_hooks` 改名为 `hooks` 并保留为 alias），0.129 stage `stable` 默认 `true`；Mercury 显式 `[features] hooks = true` 防 regression；legacy `codex_hooks = true` 仍是合法 alias 但已非 canonical**
3. ~~Repo 在 `~/.codex/config.toml` `[projects."<your-worktree-path>"]` 标 `trust_level = "trusted"`~~ → **完成（user-level patch + backup pre-codex-hooks-adoption-20260508-215243）**
4. Empirical 测试 Windows hook command field 解析 → **CONDITIONAL still — 需 user 在 Codex 实际会话中 trigger Bash/apply_patch 验证 hook 是否 fire；本 PR test harness 仅验证 hook script 在 bash stdin shape 下行为，未验证 Codex spawn path**
5. Empirical 测试 inline `[hooks]` in `.codex/config.toml` 与外置 `.codex/hooks.json` 不会 double-fire — Mercury 仅用 `hooks.json`，未用 inline，规避此 risk
6. Empirical 测试 `apply_patch` matcher 是否真的别名 `Edit` / `Write` — 文档断言 yes，empirical 验证仍待真实 Codex apply_patch 触发
7. Empirical 测试 exit code 2 → block apply_patch / shell tool execution — 同 #4，需真实 Codex 会话；本 PR test harness 仅验证 hook script 自身 exit 2 逻辑
8. SubagentStop hook (`mercury-test-gate/hook.cjs`) 无 Codex 等价 event — 已确认无对应 event；本 PR 不 port，留 Claude Code only feature；Phase 3+ 用 Stop event + dispatch context detect 替代

**Empirical Phase 2 增量发现** (2026-05-08 implementation 阶段)：

- **TOML section bleed gotcha** — `[features]` section 必须放在所有 top-level scalars **之后**。TOML scoping 把 section header 之后所有 `key = value` pair 收为该 section 子节点，直到下一个 section header。若 `[features]` 放在 `developer_instructions = """..."""` 之前，会令 Codex 把 multi-line `developer_instructions` 解析为 `features.developer_instructions`（应是 boolean），整个 config 被 reject。Mercury repo `.codex/config.toml` Phase 2B 实施期间命中此问题并修复。
- **Codex 真的解析并校验 config** — `codex debug models` 加载用户 + 项目两层 config；config 错误立即报错（vs silent skip）。这意味着 hook config 错误也会被 codex CLI 立即暴露 — 是利好，但部署时务必跑 `codex debug models` smoke test。

---

## 1. Background

### 1.1 Mercury 现状

- `.claude/hooks/` 13 个 hook script，按 Claude Code hooks schema 组织在 `.claude/settings.json`
- `.codex/config.toml` developer_instructions #11 明确："On Windows, do not assume Codex hooks will run; use `.codex/rules`, `.codex/config.toml`, repo skills, and `scripts/codex/*.ps1` as the enforcement path"
- `AGENTS.md` L48 同结论："Codex on Windows: hooks are currently disabled, so strong guardrails live in `.codex/config.toml`, `.codex/rules/`, repo skills, and `scripts/codex/*.ps1`"
- `scripts/codex/{guard,git-safe}.ps1` 是 Windows-on PowerShell wrapper，强制 `git add` / `commit` / `push` 必经 mark-review gate
- 这些声明已 outdated（Codex hooks 自 v0.124 GA）

### 1.2 用户场景

- Mercury Codex CLI session 当前完全靠 instruction-level enforcement（developer_instructions + rules）+ wrapper（PowerShell scripts）
- 缺少 Claude Code 端的 push-guard / pre-commit-guard / scope-guard 等 hard-gate 等价机制
- Side-bug lane scope 明确 — peripheral hook/script/infra fixes — 此移植 fits perfectly

### 1.3 Codex hooks GA 时机

- v0.124.0 (2026-04-23): hooks 引擎 stable
- v0.128.0 (2026-04-30): plugin-bundled hooks + hook enablement state + external-agent config import
- v0.129.0 (2026-05-07): `/hooks` browser + before/after compaction hooks + PreToolUse context injection + plugin hooks discovery

---

## 2. Decision Verdict

| Path | 形态 | 推荐度 | Verdict |
|------|------|-------|---------|
| **A** — 1:1 port `.claude/hooks/` → `.codex/hooks.json`，drop wrappers / rules | adoption only | 3/5 | 风险 — WebSearch/WebFetch 无 hook 覆盖；Windows shell 未验证；feature flag / trust 配置不到位会 silent fail |
| **B** — 不 adopt hooks，强化 rules + wrappers | status quo+ | 2/5 | 错失 hook 的 hard-gate 能力；不解决 outdated docs |
| **C** — Hybrid: hooks 主防线 + rules 精简保留 + wrappers 留 git-safe 兜底 | hybrid | **5/5** | **推荐** |

**核心理由**：

- Codex hooks 与 Claude Code hooks **schema 一致** → 9/13 hook 可低成本 port，价值清晰
- 但 web_search 无 handler 是 hard limitation — `web-research-gate.sh` 在 Codex 无法以 hook 形式工作；保留 rules-level enforcement 是必需 fallback
- Windows shell 行为 unverified → 单一防线（hooks）若 silent fail 会留巨大缺口；wrapper 作为 last-line defense
- `.codex/rules/default.rules` 仍可承担 instruction-level guidance（非 tool-gate 的 best-practice 注入）— 删除会丢失 Codex agent 行为约束的重要文本

---

## 3. Per-Question Evidence

### Q1 — Codex CLI hook 在 Windows 调用 `bash` 的具体行为

**Status**: **CONDITIONAL** — 文档未直接说明

**Findings**：
- 官方 hooks 文档示例全部使用 Unix path (`/usr/bin/python3 "$(git rev-parse --show-toplevel)/.codex/hooks/..."`) — 隐含 Unix-shell 假设
- `agenticcontrolplane.com/blog/codex-cli-hooks-reference` 未涉及 Windows shell 细节
- `notify` 配置在 Windows 用 PowerShell list-form (`["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "..."]`)，但 hook command 是 string（不是 list），解析行为不同
- Codex 在 Windows 的 shell 默认执行用 `bash --login`（per Issue #4843 在 macOS/Linux 路径），Windows 路径未确认

**Mercury impact**：
- 现有 hooks 用 `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/script.sh"` 形式
- Codex 上 `$CLAUDE_PROJECT_DIR` 无定义（见 Q5），需改为 `bash "$(git rev-parse --show-toplevel)/.claude/hooks/script.sh"`
- 若 Codex on Windows 直接 spawn 而不经 shell：需 `bash.exe -c "..."` 显式形式
- 若 Codex on Windows 经 `cmd /c`：需注意 cmd 的引号规则
- **CONDITIONAL 验证**: Phase 2 实施时写一个 echo-only test hook 探测实际调用行为

**Citation**: <https://developers.openai.com/codex/hooks>, <https://github.com/openai/codex/issues/4843>

---

### Q2 — Codex 的 tool name regex matcher

**Status**: **VERIFIED with caveat**

**Findings**：
- Canonical Codex tool names: `Bash`、`apply_patch`（aliases `Edit` 和 `Write`）、`mcp__server__tool`
- SessionStart matcher 用 source 字符串：`startup` / `resume` / `clear`
- 文档示例 matcher: `"^Bash$"`、`"Edit|Write"`、`"mcp__filesystem__.*"`
- 关键：**Codex 的 `apply_patch` 是 file-edit tool，与 Claude Code 的 `Edit` / `Write` 在 hook 语义上等价**

**Mercury impact**：
- `.claude/settings.json` 现有 matcher: `"Edit|Write"` → Codex 对应 `"^apply_patch$"`（或 `"Edit|Write"` per alias，**待 empirical 验证**）
- `"Bash"` → `"^Bash$"` (1:1) ✓
- `".*"` (universal) → 同样可用
- `"Skill|Agent"` → Codex **无对应**（Codex 有 skills 但不暴露 tool matcher level；agents 对应 `multi_agents/*` 但 hook 不 fire — 见 Q9 / Issue #20204）
- `"WebSearch|WebFetch"` → Codex **无对应**（无 web tool handler — 见 Q9）

**Citation**: <https://developers.openai.com/codex/hooks>, <https://github.com/openai/codex/issues/20204>

---

### Q3 — Codex hook 的 stdin / stdout / exit code 协议 vs Claude Code

**Status**: **VERIFIED — 完全一致 + 1 处增强**

**Findings**：

| 维度 | Claude Code | Codex | 一致？ |
|------|-------------|-------|-------|
| stdin payload | JSON 含 session_id, tool_name, tool_input, ... | 同 + `model`、`turn_id`（turn-scoped events） | ✓ + 增强 |
| Exit 0 | success；stdout 可被解析 JSON | 同 | ✓ |
| Exit 1 | error；hook fail | 同（"hook execution fails (not detailed)"） | ✓ |
| Exit 2 | block；stderr → reason | 同 | ✓ |
| JSON `{"decision": "block"}` | 支持 PreToolUse | 支持（同字段） | ✓ |
| `{"continue": false, "stopReason": "..."}` | 支持 | 支持（per docs） | ✓ |
| `hookSpecificOutput` | 部分支持 | 全面支持（PreToolUse 注入 context、PermissionRequest decision allow/deny） | ✓ + 增强 |

**Mercury impact**：
- 现有 push-guard.sh / pre-commit-guard.sh / scope-guard.sh 用 exit 2 + stderr message → **直接生效**
- `.claude/hooks/web-research-gate.sh` 用 exit 2 阻断未 web research 的 SDK 调用 → **不能 port**（无 web_search handler）

**Citation**: <https://developers.openai.com/codex/hooks>

---

### Q4 — Codex `[hooks]` inline TOML 还是 `hooks.json` 哪个更稳

**Status**: **VERIFIED — 两者完全等价**

**Findings**：
- 引用官方："Lifecycle hooks configured inline in `config.toml`. Uses the same event schema as `hooks.json`"
- 两者 schema 完全一致；可以同时使用，Codex 启动会 merge + warn
- inline TOML 用 `[[hooks.PreToolUse]]` array-of-tables 形式
- 外置 `hooks.json` 用 standard JSON

**Recommendation**：用 **`hooks.json`**（外置），因为：
- 与 Claude Code `.claude/settings.json` hooks 段结构 1:1 对应，便于 diff / port
- 避免 `.codex/config.toml` 单文件膨胀
- 单文件用途清晰（hooks 一个 file，config 一个 file）

**Citation**: <https://developers.openai.com/codex/config-reference>

---

### Q5 — Codex 是否能 reuse `$CLAUDE_PROJECT_DIR` env var

**Status**: **CONDITIONAL — 不能 reuse，需替换**

**Findings**：
- Codex 官方 hooks 文档**未列出**任何注入 hook process 的 env var
- Issue #13576 (官方 issue): "Setup scripts ... receive no special environment variables providing paths, and there's no straightforward way to get the absolute path to the main/original repo root."
- `CODEX_HOME` 存在但是 user config dir，不是 repo root
- 官方推荐用 `$(git rev-parse --show-toplevel)` 解析 repo root

**Mercury impact**：
- 现有 hooks 用 `bash "$CLAUDE_PROJECT_DIR/.claude/hooks/script.sh"`
- 改写为：`bash "$(git rev-parse --show-toplevel)/.claude/hooks/script.sh"` （Codex 端）
- **Defensive workaround**: 现有 push-guard.sh 内部已 `repo_root="$(cd "$(dirname "$0")/../.." && pwd)"` 形式 — 如果保持 hook 用 `bash` 调用且 cwd 在 repo 内，可以 fall back 到 hook script 内部计算
- **建议**: hook config 端用 `git rev-parse`，hook script 内部保留 fallback 逻辑做 belt-and-suspenders

**Citation**: <https://developers.openai.com/codex/hooks>, <https://github.com/openai/codex/issues/13576>

---

### Q6 — Codex hook 触发时的 cwd

**Status**: **VERIFIED**

**Findings**：
- 官方："commands run with the session `cwd` as their working directory"
- session cwd = Codex 启动目录；通常但不保证是 repo root
- 文档明确警告："Codex may be started from a subdirectory" — 不要依赖 cwd 为 repo root
- 官方推荐用 `git rev-parse --show-toplevel` 解析

**Mercury impact**：
- hook command 中所有相对路径 → 改 absolute via `git rev-parse`
- hook script 内部任何 `./xxx` 路径 → 改 `"$(git rev-parse --show-toplevel)/xxx"`
- 现有 push-guard.sh 已用 absolute resolution → 直接 port 安全

**Citation**: <https://developers.openai.com/codex/hooks>

---

### Q7 — `permissionRequest` event 是否需要 Mercury 适配

**Status**: **OPTIONAL — 可暂不实施**

**Findings**：
- PermissionRequest 当 Codex 即将弹起 approval prompt（如 shell escalation / managed-network approval）时 fire
- Hook 可返回 allow / deny / 不决定（让默认 prompt 继续）
- Response 形式：
  ```json
  {
    "hookSpecificOutput": {
      "hookEventName": "PermissionRequest",
      "decision": {"behavior": "allow"}
    }
  }
  ```
- 多 hook 时 deny wins，否则 allow
- **`updatedInput` / `updatedPermissions` / `interrupt` 字段保留 — 现今返回会 fail-closed**

**Mercury impact**：
- Mercury 现在用 `--dangerously-skip-permissions` 模式（user 配置），不依赖 prompt
- 暂无明确 use case 需要 hook-level approval 决策
- **未来 use case**: 自动批准 read-only commands、deny `rm -rf` 类高风险操作 — Phase 3+ 考虑
- Phase 2 **不实施** PermissionRequest hook，但保留扩展接口

**Citation**: <https://github.com/openai/codex/issues/16301>, <https://developers.openai.com/codex/hooks>

---

### Q8 — 用户级 `~/.codex/hooks.json` vs repo `.codex/hooks.json` 优先级 + 叠加规则

**Status**: **VERIFIED — 叠加，不 override**

**Findings**：
- 优先级（高→低）：(1) Project `<repo>/.codex/hooks.json` 或 inline `[hooks]` in `.codex/config.toml`；(2) User `~/.codex/hooks.json` 或 user `[hooks]`；(3) plugin-bundled hooks
- 关键："Higher-precedence config layers do not replace lower-precedence hooks. If a single layer contains both `hooks.json` and inline `[hooks]`, Codex merges them and warns at startup."
- Project hooks 仅在 `.codex/` layer trusted 时加载（per `projects.<path>.trust_level = "trusted"`）
- 多个 matching hooks 在同一 event 上 **concurrent 启动**（不串行）

**Mercury impact**：
- repo `.codex/hooks.json` 添加不会 displace user-level hooks — Mercury 现在 user-level 是否有 hook 待用户审计
- **Trust 检查**：必须确认 `~/.codex/config.toml` 内 `[projects."<your-worktree-path>"] trust_level = "trusted"` — 否则 repo hooks silent skip
- 不会重复 fire 同名 hook（每个 layer 独立注册的 hook 是不同 hook entries）

**Citation**: <https://developers.openai.com/codex/hooks>

---

### Q9 — Codex tool 名是否完整覆盖 `Bash` / `Edit` / `Write` / `WebSearch` / `WebFetch`

**Status**: **PARTIALLY VERIFIED — Bash/Edit/Write 覆盖，WebSearch/WebFetch 不覆盖**

**Findings (per Issue #20204, 2026-04-29)**：

**Hooks DO fire for**:
- `shell` (Bash)
- `unified_exec`
- `apply_patch`（aliases `Edit`/`Write`）
- `mcp` (MCP tool calls)

**Hooks DO NOT fire for** (官方未实现 handler)：
- `web_search`（无 handler — Mercury web-research-gate.sh 不能 port）
- `list_dir`、`view_image`（filesystem reads）
- `mcp_resource`
- `plan`（`update_plan`）、`goal`（create/update/get）
- `agent_jobs`（`spawn_agents_on_csv`）
- `tool_search`、`tool_suggest`
- `multi_agents/*`、`multi_agents_v2/*`（subagent 派发）

**Mercury impact**：

| Mercury hook | Mercury matcher | Codex matcher | Status |
|--------------|-----------------|---------------|--------|
| scope-guard.sh | `Edit\|Write` | `^apply_patch$` (per alias) | ✓ port |
| web-research-gate.sh | `Edit\|Write` | `^apply_patch$` (proxy) | ✓ port — 在 apply_patch 时 gate |
| web-research-extended-gate.sh | `Edit\|Write` | `^apply_patch$` | ✓ port |
| pre-commit-guard.sh | `Bash` | `^Bash$` (or `^shell$`) | ✓ port |
| push-guard.sh | `Bash` | `^Bash$` | ✓ port |
| pr-create-guard.sh | `Bash` | `^Bash$` | ✓ port |
| pr-merge-guard.sh | `Bash` | `^Bash$` | ✓ port |
| post-commit-reset.sh | PostToolUse `Bash` | `^Bash$` PostToolUse | ✓ port |
| post-review-flag.sh | PostToolUse `Skill\|Agent` | **无对应** — Codex 无 Skill/Agent tool name | ⚠ defer / skip |
| post-web-research-flag.sh | PostToolUse `WebSearch\|WebFetch` | **无对应** | ✗ NOT portable |
| session-init.sh | UserPromptSubmit | UserPromptSubmit | ✓ port |
| user-prompt-submit.sh | UserPromptSubmit | UserPromptSubmit | ✓ port |
| stop-guard.sh | Stop | Stop | ✓ port |
| mercury-loop-detector hook.cjs | PostToolUse `.*` | PostToolUse `.*` | ✓ port (但 fire 范围更窄 — 仅 4 tools 触发，per Issue #20204) |
| mercury-test-gate hook.cjs | SubagentStop | **无对应** — Codex 无 SubagentStop event | ✗ NOT portable |

**Verdict**: 13 hook 中 9 个直接 port、2 个非常受限 / NOT portable（web-research / SubagentStop）、2 个需调整（matcher 替换）。

**Citation**: <https://github.com/openai/codex/issues/20204>, <https://developers.openai.com/codex/hooks>

---

### Q10 — Plugin-bundled hooks discovery 路径

**Status**: **VERIFIED — Codex plugin path 可用，但 Mercury 不需要**

**Findings**：
- Codex CLI 自 v0.128 起支持 plugin-bundled hooks
- Plugin discovery via `/plugins` browser (CLI) 或 plugin 安装
- Plugin 内部 hook discovery: plugin manifest + `hooks/hooks.json`
- v0.129 加了 `/hooks` browser/toggle UI
- Skill discovery paths（**注意：是 skills 不是 hooks**）：`$CWD/.agents/skills`、`$REPO_ROOT/.agents/skills`、`$HOME/.agents/skills`、`/etc/codex/skills`、built-in

**Mercury impact**：
- Mercury 不需要 publish 成 Codex plugin（Mercury 不是通用 productized tool）
- 直接 `.codex/hooks.json` 是正确选择
- 未来若要 distribute Mercury hooks 到其它 Codex user → plugin path 是渠道（不在本 issue scope）

**Citation**: <https://developers.openai.com/codex/changelog>, <https://codex.danielvaughan.com/2026/03/30/codex-cli-plugin-system/>

---

### Q11 — Codex hook on Windows 执行的 shell context

**Status**: **CONDITIONAL — 文档未明，需 empirical 验证**

**Findings**：
- 官方文档**未明确说明** Windows hook 的 shell wrapper
- TOML 示例使用 Unix-style path (`/usr/bin/python3`)，无 Windows 对应示例
- `notify` config 在 Windows 显式 list-form (`["powershell", "-NoProfile", ...]`)，但 hook `command` field 是 string 不是 list
- 推断（per Codex shell tool 在 Windows 用 `bash --login` on macOS/Linux per Issue #4843，Windows 行为类比）：
  - **可能 (a)**: Codex 在 Windows 直接 spawn `command` field 内容 → 需要 `bash.exe -c "..."` 显式
  - **可能 (b)**: Codex 用 `cmd /c command` → 需注意 cmd 引号
  - **可能 (c)**: Codex 用 `bash -c command` (假设 git-bash 在 PATH) → 现有 hook 命令直接可用

**Mercury impact**：
- 假设 (c) — Mercury 现有 hook 命令格式（`bash "$(...)/script.sh"`）work
- 假设 (a) — 需要全部改写为 PowerShell 命令 / 显式 bash.exe 路径
- 假设 (b) — 需要重新评估 cmd 兼容
- **Phase 2 必做的 empirical 测试**（CONDITIONAL #4）：写一个 echo-only test hook，配合 stderr capture 探测实际调用 binary + args

**Citation**: <https://github.com/openai/codex/issues/4843>, <https://developers.openai.com/codex/cli/features>

---

### Q12 — Codex hook timeout 单位 + 默认值

**Status**: **VERIFIED — 单位一致，默认值不同**

**Findings**：
- 单位：**秒**（与 Claude Code 一致 ✓）
- 默认值：**600 秒**（Claude Code 默认 60 秒 — Codex 默认 10× 更宽松）

**Mercury impact**：
- 现有 hook timeout 全在 5-15 秒区间 → 远低于 Codex 默认；不会被 Codex 默认值影响
- **建议 port 时显式保留 timeout 字段**（不依赖默认值），避免未来 Codex 改默认值导致 silent regression
- mercury-test-gate timeout=360 / mercury-loop-detector timeout=10 → 均 < Codex 默认 600 → 安全

**Citation**: <https://developers.openai.com/codex/hooks>

---

### Q13 — Codex hook fail-closed 行为：non-zero exit 是阻断 tool 执行还是只 warn

**Status**: **VERIFIED — exit 2 = block, exit 1 = error (not block)**

**Findings**：
- Exit **0**: success
- Exit **1**: error；hook execution fails (per docs) — **不阻断 tool**（docs 未明确，但与 Claude Code 一致行为）
- Exit **2**: **block / deny**；stderr 内容作为 reason 给到 Codex agent
- JSON path: 返回 `{"decision": "block", "reason": "..."}` 等价于 exit 2
- PermissionRequest 用专门的 `hookSpecificOutput.decision.behavior = "deny"`

**Mercury impact**：
- Mercury push-guard.sh / pre-commit-guard.sh 等用 exit 2 → **fail-closed ok**
- 但 Mercury 部分 hook（如 post-* flags）只 echo 到 stderr 不 exit 2 → Codex 端同样不阻断 ✓
- **CONDITIONAL #6 验证**: empirical 测试 exit 2 在 Codex on Windows 是否真能阻断 apply_patch tool

**Citation**: <https://developers.openai.com/codex/hooks>

---

## 4. Risk Register

| Risk | Severity | Likelihood | Mitigation |
|------|----------|------------|------------|
| `web_search` 无 handler，Mercury web-research enforce 在 Codex 失效 | HIGH | CERTAIN | 保留 `.codex/rules/default.rules` instruction-level enforcement；`developer_instructions` #1 已有 web-search MUST clause |
| Windows shell invocation 未文档化导致 silent fail | HIGH | MEDIUM | Phase 2 必跑 empirical test hook；保留 `scripts/codex/git-safe.ps1` 作为 last-line defense |
| `[features] hooks` 默认未启用 | LOW | LOW (empirical: 0.129 默认 `true`) | Phase 2E 在 user `~/.codex/config.toml` 显式 enable 防 regression；canonical name `hooks` 不是 legacy `codex_hooks` |
| `[features]` section 放置位置导致 TOML scoping bleed | HIGH | HIGH (empirical 命中) | `[features]` MUST 放所有 top-level scalars **之后**；Phase 2B 实施期间命中并修复 |
| `projects.<path>.trust_level = "trusted"` 未配置 → repo hooks silent skip | MEDIUM | HIGH (新 worktree) | Phase 2 在 user-level config 显式声明 trust list；新增 `<your-worktree-path>`（cross-repo governance per CLAUDE.md L37-52） |
| inline `[hooks]` + `hooks.json` 双源 double-fire | LOW | LOW | Mercury 只用 `hooks.json`（per Q4）；不在 `config.toml` 加 `[hooks]` |
| SubagentStop 无对应 → mercury-test-gate 失效 | MEDIUM | CERTAIN | Stop event + 检测 last_assistant_message 是否含 dev subagent 完成 marker；或暂保留为 Claude Code only feature |
| `apply_patch` matcher 别名 `Edit`/`Write` 行为差异 | LOW | LOW | 用 canonical `^apply_patch$`，empirical 验证 Edit/Write alias 真的 work |
| `$CLAUDE_PROJECT_DIR` env 在 Codex 端 undefined | MEDIUM | CERTAIN | hook command 端用 `$(git rev-parse --show-toplevel)`；script 内部已有 fallback |
| Codex 多层 hook 与已挂 plugin (`codex-plugin-cc`) 重复 | LOW | LOW | Phase 2 实施前 audit `~/.codex/plugins/` 已注册 hooks |
| Codex CLI 升级后 hook schema 变 | MEDIUM | LOW (stable since 0.124) | 锁定测试 Codex 版本到 Issue body；写到 manifest |

---

## 5. Implementation Plan

### Phase 2A — `.codex/hooks.json` (主交付)

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "^apply_patch$",
        "hooks": [
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/scope-guard.sh\"", "timeout": 5 },
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/web-research-gate.sh\"", "timeout": 5 },
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/web-research-extended-gate.sh\"", "timeout": 5 }
        ]
      },
      {
        "matcher": "^Bash$",
        "hooks": [
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/pre-commit-guard.sh\"", "timeout": 5 },
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/push-guard.sh\"", "timeout": 5 },
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/pr-create-guard.sh\"", "timeout": 5 },
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/pr-merge-guard.sh\"", "timeout": 15 }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          { "type": "command", "command": "node \"$(git rev-parse --show-toplevel)/adapters/mercury-loop-detector/hook.cjs\"", "timeout": 10 }
        ]
      },
      {
        "matcher": "^Bash$",
        "hooks": [
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/post-commit-reset.sh\"", "timeout": 5 }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/session-init.sh\"", "timeout": 5 },
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/user-prompt-submit.sh\"", "timeout": 5 }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "bash \"$(git rev-parse --show-toplevel)/.claude/hooks/stop-guard.sh\"", "timeout": 10 }
        ]
      }
    ]
  }
}
```

**NOT ported**:
- `WebSearch|WebFetch` PostToolUse → Codex 无 web_search hook handler
- `Skill|Agent` PostToolUse → Codex 无 skill/agent tool matcher
- `SubagentStop` event → Codex 无该 event；mercury-test-gate 待 Phase 3 重构（用 Stop + dev subagent 检测）

### Phase 2B — `.codex/config.toml` 同步

需修改 `developer_instructions`：
- **删** clause #11 ("On Windows, do not assume Codex hooks will run") — outdated
- **改** clause #8/#9/#10 关于 wrapper 强制 — 调整为"Codex hooks 是主防线，wrapper 作为 fallback；非 hook-covered ops 仍走 wrapper"
- **加** feature flag enable 提示：`[features] hooks = true`

需添加 sections：
```toml
[features]
hooks = true
```

### Phase 2C — `AGENTS.md` 同步

- 删 L48 "Codex on Windows: hooks are currently disabled, so strong guardrails live in `.codex/config.toml`, `.codex/rules/`, repo skills, and `scripts/codex/*.ps1`"
- 改为 "Codex hooks 自 v0.124 GA；Mercury `.codex/hooks.json` 是主防线，`.codex/rules/` + `scripts/codex/*.ps1` 是 defense-in-depth"
- 修 outdated path 引用：
  - `Mercury_KB/99-templates/` → 该 vault 已 archived，per CLAUDE.md "Related Repositories"；删除引用
  - `sot-workflow.md` → 仍存在 `.mercury/docs/guides/sot-workflow.md`（legacy），保留但加 "(legacy)" 标记
  - `.mercury/roles/` → archived 至 `archive/roles/`；roles 现在 `.claude/agents/*.md`

### Phase 2D — `scripts/test-codex-hooks.sh` (test harness)

仿 `scripts/test-push-guard.sh` 风格：
- 合成 Codex hook stdin JSON（含 session_id, tool_name, tool_input, cwd, hook_event_name, model）
- 调用每个 hook script
- Verify exit code（block / not block）+ stderr 内容
- 覆盖 12+ test cases，含 happy path + violation paths

### Phase 2E — `~/.codex/config.toml` (user-level, cross-repo)

per CLAUDE.md L37-52 governance：
- 在 file 的 Issue body 内记录命令清单 + diff 摘要 + 验证步骤
- 备份: `cp ~/.codex/config.toml ~/.codex/config.toml.backup-pre-<issue>`
- 修改：
  - `[features] hooks = true` (若未 enable)
  - `[projects."<your-worktree-path>"] trust_level = "trusted"` (确保 repo hooks 加载)
  - 验证 Codex CLI 版本 ≥ v0.128 (`codex --version`)

### Phase 2F — `scripts/codex/*.ps1` 简化（可选，本 issue 不必做）

视 `/dual-verify` 反馈决定：
- `git-safe.ps1` 保留（作为 cross-host fallback）
- `guard.ps1` `mark-review` 仍有用（在 hook 之外的 review-state tracking）
- developer_instructions 可改为"hooks 是主防线，wrappers fallback；可选用 wrappers 做 explicit safety net"

### Phase 2G — `.codex/rules/default.rules` 精简

- 删除已被 hook 覆盖的 rules（push / commit / branch enforcement）
- 保留 web-research / dual-verify / role-boundary / Chinese-milestone 等 instruction-only rules
- Empirical 验证后再做精简（Phase 3 / 后续 PR）

---

## 6. Phase 2 Execution Sequence

1. **File Issue** in main repo: `[side-bug] Codex CLI 0.128+ hooks adoption — port .claude/hooks/ + AGENTS.md/config.toml doc sync` (label: `bug`, `lane:side-bug`, P2)
2. **Lane claim**: `bash scripts/lane-claim.sh side-bug <N>` (Rule 1.1)
3. **Branch**: `git checkout -b lane/side-bug/<N>-codex-hooks` (Rule 2.1)
4. **CONDITIONAL verify (在 implementation 前)**:
   - `codex --version` ≥ v0.128
   - `cat ~/.codex/config.toml | grep -A1 features` 显示 `hooks = true`（若否，加）
   - `cat ~/.codex/config.toml | grep -A1 'projects."<your-worktree-path>"'` 显示 `trust_level = "trusted"`（若否，加）
   - 写 echo-only test hook，empirical 验证 Windows shell invocation
5. **Implementation** (Phase 2A-2D)
6. **Empirical CONDITIONAL #4-#7 测试**:
   - apply_patch alias Edit/Write
   - exit 2 fail-closed
   - inline `[hooks]` + `hooks.json` 不 double-fire（Mercury 只用 hooks.json，但需 verify 用户 user-level config 没 inline 重复）
7. **`/dual-verify`**
8. **Commit + push**
9. **`/pr-flow`** — Argus iter loop, escape-hatch on iter 3+
10. **Merge cleanup**:
    - `Closes #<N>` 自动闭环 issue
    - 在 issue body 补充 user-level `~/.codex/` 改动 evidence
    - LANES.md side-bug § 更新
    - `memory/sessions/S2-side-bug.md`
    - `bash scripts/regenerate-memory-index.sh --in-place`

---

## 7. Open Questions

1. ~~`apply_patch` 是否真在 Codex 上 alias `Edit`/`Write`~~ → 文档断言 yes，需 empirical 验证 (CONDITIONAL #6)
2. ~~Windows hook command field 解析路径~~ → CONDITIONAL #4
3. **CODEX_HOME** 在 hook 内是否被注入 → 当前推断 NO，但未实测；与 hook 实现无 hard 依赖
4. **mercury-loop-detector** 在 Codex 端 PostToolUse `.*` 是否真的 fire 每个 tool — per Issue #20204 大量 tools 无 hook 覆盖 → loop detector 在 Codex 端会"死区"宽于 Claude Code，但仍可工作（监测 active 4 tools 即可）
5. **mercury-test-gate** SubagentStop 替代方案 — 是否走 `Stop` event + parse `last_assistant_message` 来 detect dev subagent 完成；或用 Codex 原生 `multi_agents` 协议（待 Phase 3 探究）
6. **Plugin discovery 影响**：用户 `~/.codex/plugins/` 已挂 `codex-plugin-cc` 是否含 hooks → Phase 2 audit `ls ~/.codex/plugins/*/hooks.json`

---

## 8. Sources

### 官方文档
- [Codex Hooks](https://developers.openai.com/codex/hooks)
- [Codex Configuration Reference](https://developers.openai.com/codex/config-reference)
- [Codex Changelog](https://developers.openai.com/codex/changelog)
- [Codex CLI Features](https://developers.openai.com/codex/cli/features)
- [Codex Agent Approvals & Security](https://developers.openai.com/codex/agent-approvals-security)
- [Codex Config Basics](https://developers.openai.com/codex/config-basic)
- [Codex CLI Reference](https://developers.openai.com/codex/cli/reference)

### Issues / PR
- [PR #9796 (NOT MERGED, Jan 2026 — closed)](https://github.com/openai/codex/pull/9796) — community-submitted hook system；不是 ship 的实现。Codex 用了 OpenAI 自有路径
- [Issue #20204 (open) — PreToolUse coverage gaps](https://github.com/openai/codex/issues/20204)
- [Issue #16301 — PermissionRequest event](https://github.com/openai/codex/issues/16301)
- [Issue #16732 — Hook coverage tracking issue](https://github.com/openai/codex/issues/16732)
- [Issue #19385 — additionalContext in PreToolUse](https://github.com/openai/codex/issues/19385)
- [Issue #14882 — Lifecycle hooks proposal](https://github.com/openai/codex/issues/14882)
- [Issue #13576 — Worktree env vars](https://github.com/openai/codex/issues/13576)
- [Issue #4843 — bash --login on macOS/Linux](https://github.com/openai/codex/issues/4843)
- [Issue #18334 — Customizable .codex location](https://github.com/openai/codex/issues/18334)

### 第三方分析
- [Agentic Control Plane — Codex CLI hooks reference](https://agenticcontrolplane.com/blog/codex-cli-hooks-reference)
- [Codex CLI Plugin System (danielvaughan)](https://codex.danielvaughan.com/2026/03/30/codex-cli-plugin-system/)
- [hatayama/codex-hooks (community wrapper, NOT used as path)](https://github.com/hatayama/codex-hooks)
- [Codex CLI Changelog (gradually.ai)](https://www.gradually.ai/en/changelogs/codex-cli/)
- [How to Install Codex CLI on Windows 2026](https://itecsonline.com/post/how-to-install-codex-cli-on-windows-2026-guide)
- [Claude Code Hooks Reference (for parity comparison)](https://code.claude.com/docs/en/hooks)

### Mercury 内部参考
- `.claude/settings.json` (现有 hook config — 13 hook scripts)
- `.codex/config.toml` (现有 outdated developer_instructions)
- `AGENTS.md` (现有 outdated L48)
- `.mercury/docs/research/pixel-animation-workflow-2026-05-08.md` (ADR 结构参考)
- `CLAUDE.md` L37-52 (cross-repo governance)
- `feedback_lane_protocol.md` v1 (Rules 1-8 lane protocol)
- `feedback_argus_nit_loop.md` (iter 3+ escape-hatch)

---

## 9. Verdict 总结

**CONDITIONAL_GO Path C (Hybrid)** — 启用 Codex hooks 作为主防线（9/13 Mercury hook 直接 port），保留 `.codex/rules/` 精简版 + `scripts/codex/git-safe.ps1` 作为 defense-in-depth。

8 项 CONDITIONAL 必须 Phase 2 实施前 / 实施中 verify，3 项 hook 受限 / NOT portable（web_search、Skill/Agent、SubagentStop），Mercury web-research 强制保留 instruction-level enforcement（`.codex/rules/` + developer_instructions），mercury-test-gate 暂留 Claude Code only。

预期工作量：1-2 sessions，含 empirical Windows test 探测 + dual-verify + Argus iter loop。
