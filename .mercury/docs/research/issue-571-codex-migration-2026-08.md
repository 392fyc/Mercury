# Mercury 工作流迁移到 Codex CLI —— 目标清单

> 编制日期：2026-08-14。所有版本号与实测结论都锚定这一天，超过两周请重新核对。
> 这份文档是**目标**不是**步骤**：每条说清「要达成什么状态」和「怎么算达成」，具体怎么做由执行时决定。

---

## 0. 背景与本文档的用法

Claude 订阅到期，需要把 Mercury 的完整工作流切换到 Codex CLI，并尽量保住现有能力——
包括 Mercury 自身、SoT 组、设计库、KB，以及用户级配置。

把这份文档整份贴给新会话作为工作目标。执行时按 G0 → G6 的顺序推进，同层内可并行。
**每完成一条，用「完成判据」那一栏的命令或检查点验证，通过了才算完成。**

---

## 1. 关键事实（做任何判断前先读这一节）

### 1.1 版本锚点

| 项 | 值 |
|---|---|
| 本机 Codex CLI | **0.129.0** |
| 最新稳定版 | **0.147.0**（2026-08-07），落后 18 个小版本 |
| 官方文档站 | `developers.openai.com/codex/*` 已 308 重定向到 **`learn.chatgpt.com/docs/*`** |
| 当前认证 | Azure OpenAI（`model_provider = "azure"`，`env_key = AZURE_OPENAI_API_KEY`，模型 `gpt-5.6-terra`） |

### 1.2 第 0 号阻塞（未解决前，后面全是空转）

`~/.codex/config.toml` 里 Mercury 的信任条目写成了**扩展长度路径形式**：

```toml
[projects.'\\?\D:\Mercury\Mercury']    # ← 错误形式
```

官方 config reference 明文：**"Untrusted projects skip project-scoped `.codex/` layers,
including project-local config, hooks, and rules."**

后果：`.codex/config.toml` 的 developer_instructions、`.codex/hooks.json` 的 8 条 hook、
`.codex/rules/` 的 9 条禁令，**当前三者全部未加载**。SoT 两个仓是普通形式，没这个问题。

已做：备份在 `~/.codex/config.toml.backup-pre-codex-migration-20260814`；
`PokemonAutoChess` 那条已改成普通形式；**Mercury 那条尚未改**。

### 1.3 四个已查实的结论（推翻了两个原设想）

**① Dynamic Workflow 的重建规模 = 约 400–550 行 TypeScript，不是一个大工程。**

分档估算（有效代码行，不含空行注释测试）：

| 档 | 内容 | 行数 |
|---|---|---|
| 档一 · 最小可用 | 并行跑 N 个 agent、结构化结果回收进变量 | 120–170 |
| 档二 · 带护栏 | + 并发池、背压、重试、超时、失败隔离、丢弃工作量日志、子进程清理 | 累计 370–520 |
| 档三 · 全等价 | + 断点续跑缓存、token 预算对象、进度可视化 | 累计 650–900（最小可视化）／1100–1700（真 TUI） |

**结论：档二 + 15–25 行的 per-item 链就够，约 400–550 行。** 依据是把 7 个现役脚本的原语使用全量数过：

- `budget.total / spent() / remaining()` —— **用了 0 次**（脚本里的 "budget" 全是自己算的普通常量）
- worktree 隔离 —— **用了 0 次，而且是刻意的**（`mercury-large-migration.js` 自己写明：runtime 会自动删除隔离 worktree 且合并回写语义未文档化，所以刻意避开）
- 断点续跑缓存 —— 脚本层用了 0 次（那是 runtime 行为）
- 扇出规模都很克制，**没有一个脚本接近 1000 agent 或 4096 项**
- 被重度依赖的只有 `log()`（77 次），全部是「被丢弃的工作量必须显式报出来」这条护栏 —— 在 Codex 上就是 `console.error` + JSONL，几乎零成本

`pipeline` 的「阶段间无屏障」**不需要单独写调度器**：它等价于
`parallelAll(items.map(i => () => s1(i).then(r => s2(r, i))))` 配一个共享信号量，是共享并发池的自然涌现结果。这是本次评估最大的削减项。

**② 「多 agent 并行 + 过程中交互」Codex 能做到约七成，且不需要 WSL。**

| 能力 | 状态 | 证据等级 |
|---|---|---|
| 并行跑多个 agent | **完全满足** | 官方文档正面记载 |
| 父 → 子 过程中追加指令（steer） | **满足** | 官方文档一句模糊描述，精确语义在源码；**本机实测跑通** |
| 子 → 父 主动推消息 | **满足** | 纯源码证据，文档零记载 |
| 同级 ↔ 同级 互发 | v2 源码支持（绝对路径寻址），**文档零记载**；v1 不支持 | 源码 |
| 父主动读子的**中间状态** | **不支持** | —— 这是最实质的缺口 |
| 共享任务列表 / 可订阅消息总线 | **不存在** | 官方 Issue #21027 挂着无人回应 |

⚠️ **工具名（`spawn_agent` / `send_input` / `wait_agent` / `close_agent` / `send_message` / `followup_task`）
一个都不在官方文档正文里**，只在源码和 release notes。这意味着它们是实现细节、随时可变，
**不能写进依赖代码的稳定假设**。

**③ WSL 那条路的前提对 Codex 不成立，不需要它。**

Claude Code 的跨会话消息确实依赖 `CLAUDE_CODE_MESSAGING_SOCKET`（unix domain socket），
官方明文「Claude Code doesn't offer cross-session messaging on native Windows」——**这条属实**。
但两处要修正：原生 Windows 上缺的精确来说是「跨**会话**」，**同一会话内的 subagent 与 teammate 消息照常可用**；
而且就算搬进 WSL，WSL 里的会话与 Windows 宿主上的会话**互相看不见**，必须全部搬进同一个发行版。

**Codex 完全不同**：它的多进程协作走 **JSON-RPC（`codex app-server`）+ 内置 multi-agent 工具**，
两条在原生 Windows 上都能跑。官方 Windows sandbox 页现在**默认推荐原生 Windows**，
WSL 降级成「需要 Linux-native 工具链时才选」的兜底（网上大量「WSL2 才是生产推荐」的说法是旧信息）。

本机 0.129.0 原生 Windows 三项实测均通过：app-server 能起 WebSocket 监听（健康端点 200）；
`turn/steer` 真的在半途改变了运行中 agent 的行为；一个 app-server 进程 + 两个 WebSocket 客户端 + 三个 thread 真并行、互不干扰。

**④ superpowers 不是 harness，与原设想不符。**

要找的是 `obra/superpowers`（271,672 star，MIT，active，最后 push 2026-08-13）。
它是**一套跑在别人 harness 上的 skill 包 + 开发方法论**——14 个 markdown 技能，
在 Codex 侧只用 Codex 原生的 skills 机制交付，**不带 hook、不带 MCP、不带任何运行时**。
它的 `.codex-plugin/plugin.json` 里 `"hooks": {}` 是**故意**声明为空的，
用来抑制 Codex 的 hooks 自动发现——因为「Codex surfaces skills natively and runs no session-start hook」。

所以它**补不上**多 agent 编排、agent 间通信、定时任务、statusline 任何一个缺口。
它有价值，但价值在方法论内容（brainstorming / TDD / subagent-driven-development /
using-git-worktrees / verification-before-completion 等 14 个技能），不在 harness 能力。

⚠️ 官方插件市场里的版本是 **5.1.3（2026-06-09）**，上游已经 **6.3.0（2026-08-12）**，
**落后约 2 个月 / 2 个 minor**。绕过办法：用仓库根下自带的 `.agents/plugins/marketplace.json`
声明的 `superpowers-dev` 市场（本机 0.129.0 实测通过）。

---

## 2. 已定决策（不需要再讨论）

1. **Codex CLI / desktop 改用个人 ChatGPT 订阅**，不再走 AOAI key。其他依赖该 key 的组件暂不修改。
2. **`dual-verify` 的第二路** = 自发两个 subagent 做盲审对比（不同 reasoning effort），不依赖外部 harness。
3. **7 个 workflow** 按现役使用情况做细节处理并重建，不是整体冻结。
4. **OMC 不迁**（围绕 Claude Code 原语建的，逐个重建投入产出比极低）。superpowers 作为**方法论 skill 包**引入，
   但不要指望它承担 harness 职责（见 1.3 ④）。
5. **不走 WSL**（见 1.3 ③）。

---

## 3. 目标清单

### G0 · 解封锁层 —— 不达成则后面全部空转

| # | 目标 | 完成判据 |
|---|---|---|
| **G0-1** | Codex CLI / desktop 走个人 ChatGPT 订阅 | `codex login status` 显示 ChatGPT 账号；配置里不再有 `model_provider = "azure"`；`model` 改成订阅侧可用的模型名 |
| **G0-2** | Mercury 仓被 Codex 视为 trusted，`.codex/` 整层真正加载 | `codex debug prompt-input \| grep -c "dual-verify"` ≥ 1（**当前为 0**） |
| **G0-3** | CLI 版本对齐 | `codex --version` 显示 0.147.0 |

**G0-1 的做法**：`[model_providers.azure]` 整块**保留**（不被 `model_provider` 引用就不生效，回滚只需把那一行加回来）；
`AZURE_OPENAI_API_KEY` 环境变量与其他依赖它的组件**一律不动**。
认证切换命令：`codex logout` → `codex login`（浏览器流程）。
config.toml 里控制认证的键是 **`forced_login_method = "chatgpt" | "api"`**
（网上流传的 `preferred_auth_method` 官方文档里没有，是第三方说法）。

**G0-2 的做法**：把 `[projects.'\\?\D:\Mercury\Mercury']` 改成 `[projects.'D:\Mercury\Mercury']`；
补上缺失的 `D:\ShipOfTheseus\SoT-fyc-space` 与活跃 worktree `D:\Mercury\Mercury-sidebug-q`；
`D:\Mercury\Mercury-side-bug` 指向的目录已不存在。

**回滚**：`mv ~/.codex/config.toml.backup-pre-codex-migration-20260814 ~/.codex/config.toml`

---

### G1 · 指令层

| # | 目标 | 完成判据 |
|---|---|---|
| **G1-1** | `AGENTS.md` 成为唯一指令真源，承载 CLAUDE.md 的全部 MUST / DO NOT / Navigation / cherry-pick 协议 | 字节数 < 28000；在文件末尾放哨兵字符串，`codex debug prompt-input` 能搜到它 |
| **G1-2** | 「说人话」的表达规则有落点 | 长会话后期抽查规则遵守度 |

**硬约束**：`AGENTS.md` 有 **32 KiB 上限且超限静默截断**（`project_doc_max_bytes` 默认值）。
现状 `CLAUDE.md` 19,174 字节 + `AGENTS.md` 3,307 字节，合并后约占预算 58–67%，**有余量**。
末尾哨兵字符串是**唯一能证明没被截断**的办法，建议做成 CI 检查，超 30000 直接 fail。

**G1-2 的落点**：Codex 的 `personality` 只有 `pragmatic` / `friendly` / `none` 三个枚举值，装不下规则正文。
改投 `developer_instructions`（会话级注入，比 AGENTS.md 更接近系统提示层）+ AGENTS.md 顶部双写。

---

### G2 · 硬门层

| # | 目标 | 完成判据 |
|---|---|---|
| **G2-1** | 8 条 hook 在**真实会话**里触发 | 会话里跑 `git push origin develop` 被 push-guard 拦下；改文件后 scope-guard 有日志 |
| **G2-2** | `.codex/rules/` 9 条禁令自动发现并生效 | `codex execpolicy check -- git push origin HEAD`（**不带 `--rules`**）返回 `forbidden` |
| **G2-3** | 权限模型对等 | `permissions.allow` 白名单改写成「沙箱档位 + `writable_roots` + `approval_policy` + rules」三件套 |

**Windows 调用路径**：官方已提供 `command_windows` / `commandWindows` 字段
（TOML 两种写法都接受），Mercury 的 hooks.json **完全没用它**——这正是悬了三个月的 Windows 调用问题的答案。
顺带：`managed_dir` 在 Windows 上叫 `windows_managed_dir`。

**两条会永久失效，需要重新设计而非搬运**：`post-web-research-flag.sh` 挂的 matcher 是 `WebSearch|WebFetch`，
而官方明文 hosted tool **不走本地 hook 路径**；`web-research-gate.sh` 的时间戳依赖随之作废。

**hook 事件覆盖**：Codex 有 11 个（SessionStart / SessionEnd / UserPromptSubmit / PreToolUse / PostToolUse /
PermissionRequest / PreCompact / PostCompact / SubagentStart / SubagentStop / Stop），
Claude Code 有 30 个。**只有 `type: "command"` 一种 handler 生效**（`prompt` 和 `agent` 会被解析但跳过）。
好消息：`mercury-test-gate` 现在可以迁了，因为 Codex 已有 `SubagentStop`。

---

### G3 · 能力层

| # | 目标 | 完成判据 |
|---|---|---|
| **G3-1** | 12 个项目 skill + SoT 仓 5 个迁到 `.agents/skills/` | `/skills` 逐一列得出；`$dual-verify` 能显式载入；说「提交前做审查」能被隐式选中 |
| **G3-2** | 9 个 subagent 定义迁成 `.codex/agents/*.toml` | `/agent` 能列出；派真任务能 spawn 独立 thread；acceptance 的只读沙箱确实拦住写操作 |
| **G3-3** | MCP 配置并入 config.toml | `/mcp` 看到 obsidian + godot + mercury 都活着 |
| **G3-4** | superpowers 作为方法论 skill 包接入 | `/skills` 里能看到那 14 个；版本不低于上游 6.3.0 |

**要接受的降级**：subagent 的 `tools` 白名单**没有对应物**（改用 `sandbox_mode` + rules 表达）；
skill frontmatter 官方只规定 `name` + `description` 两个必需字段，
`allowed-tools` / `model` / `effort` / `user-invocable` 全部降级成正文里的散文约束。

**skill 发现路径**（官方 6 层）：`$CWD/.agents/skills` → `$CWD/../.agents/skills` → `$REPO_ROOT/.agents/skills`
→ `$HOME/.agents/skills` → `/etc/codex/skills` → 内置。
⚠️ **`~/.codex/skills` 不在这 6 条里**，本机该目录存在的内容按遗留路径对待。

**`.codex/prompts/` 那 14 个文件要处理掉**：项目级 custom prompts 官方不支持、Codex 根本不扫描，
而且 custom prompts 整个机制**官方已标废弃**（原文：「Custom prompts are deprecated. Use skills…」）。
要么升格成 skill，要么删。

**skill 上下文预算**：初始清单占「at most 2% of the model's context window, or 8,000 characters」，
迁完后查 `/status` 的 context usage 是否异常。

---

### G4 · 流程层

| # | 目标 | 完成判据 |
|---|---|---|
| **G4-1** | dev-pipeline / pr-flow / autoresearch 三条主流程能跑通 | 各跑一次真实任务到底 |
| **G4-2** | `dual-verify` 用两个自发 subagent 盲审对比 | 跑一次真实合并门，两路结论能独立产出并对比 |
| **G4-3** | Argus PR 轮询有承载 | Windows 计划任务每 3–5 分钟调 `codex exec --json`，且有轮询上限（沿用「最多 3 次」）与失败退避 |

**G4-3 的原因**：Codex CLI **无法创建或管理定时任务**（官方明文），CronCreate 那条链没有对应物。
ChatGPT web 的定时任务跑在云上、碰不到本地仓库，不适用。
代价：从「会话内即时反馈闭环」变成「脚本发现 review 完成 → 通知 → 人起会话继续」。

---

### G5 · 跨仓与协作层

| # | 目标 | 完成判据 |
|---|---|---|
| **G5-1** | SoT 引擎仓、设计库、KB 三处都能在 Codex 下正常作业 | 各起一次会话完成一个真实改动 |
| **G5-2** | 跨组协作有替代形态 | 双方会话开始时读、结束时写 `docs/cross-lane-inbox.md`，一轮真实往返验证 |
| **G5-3** | 用户级记忆层三个 hook 迁移 | 起一次会话再退出，记忆层有新产物 |

**G5-2 的选型**：Codex 的内置 multi-agent 工具（`spawn_agent` / `send_input` / `wait_agent` 等）
虽然能做父子间的过程中交互，但**工具名不在官方文档、随时可变**，且**父读不到子的中间状态**、
**没有共享任务列表**。所以跨组协作**不要**押在它上面，用文件式收件箱
（`D:\ShipOfTheseus\SoT-fyc-space\docs\cross-lane-inbox.md`，37 KB，本来就是干这个的）。
代价是实时性（秒级 → 下一轮会话）与自动空闲检测；换来的是全部持久化、可审计。

**G5-3 能迁的三个**：SessionStart / SessionEnd / PreCompact，Codex 都有同名事件。

---

### G6 · 编排层

| # | 目标 | 完成判据 |
|---|---|---|
| **G6-1** | 一个约 400–550 行的编排层（档二 + per-item 链） | 能并行跑 N 个 agent、结构化结果回收进变量、有并发池与重试与丢弃日志 |
| **G6-2** | 7 个 workflow 各自有归宿 | 每个明确落在「已重建 / 已降级 / 已冻结」之一，并跑通一次 |
| **G6-3** | 重建结果与旧结果对拍 | 用同一个输入分别跑 Claude 版与 Codex 版，比对结论差异 —— 这是唯一能证明「重建没有降质」的验证 |

**技术选型要点**：

- SDK 走的是 `codex exec --experimental-json`（**源码可见、`--help` 不列的隐藏参数**），必须钉死 SDK 版本并在升级时跑回归。
- `TurnOptions` **只有两个字段**：`outputSchema` 和 `signal`。超时很便宜（`AbortSignal.timeout(ms)` 原生支持）。
- `finalResponse` 返回的是**字符串**，SDK 不做 `JSON.parse` 也不做 schema 校验 —— **每次返回都必须自己校验 + 一次修复重试**，
  因为现役脚本里所有 `(r && r.findings) || []` 这类写法都建立在「返回值一定是合法对象」的假设上。
- 错误处理只有 `new Error(message)`，**无错误码无类型**，重试策略只能字符串匹配 —— 必须配「未识别错误一律不重试 + 打日志」的兜底。
- 每次调用重付基础上下文：实测一句琐碎 prompt 就吃 **13,917 input tokens**，12 路扇出就是约 17 万 token 的固定底。
  要压这块，`codex exec-server`（常驻）或 `codex mcp-server` 是方向，但两者都标 EXPERIMENTAL。

**两项不体现在代码行数里的额外成本**：

1. **`agentType` 的三处使用**（plan-review 的 `design`、staleness 的 `research`、talent-validate 的 `design`/`critic`）
   在 Codex 上没有对应机制——`.codex/agents/*.toml` 只服务于模型自主 spawn，`codex exec` 拿不到。
   解法是把 `.claude/agents/*.md` 的角色指令拼进 prompt。这是**提示词工程量**（每角色约 30 行文本），不是代码量，但要重新调试角色行为。
2. **三个研究类脚本重度依赖 WebSearch/WebFetch**。Codex 侧对应 `-c web_search='"live"'`（本机接受，真实搜索质量未实测），
   且 `web_search_request` / `web_search_cached` 在本机 features 列表里已标 deprecated、`search_tool` 已 removed——
   这块接口正在换代，是额外的验证与维护点。

**多少行都买不到的三样**（要提前接受）：

1. **会话内集成**。Claude 的 workflow 跑在会话里，进度在 task panel、结果直接落回会话上下文、启动前有审批卡。
   Codex 上写的是**外部 Node 脚本**，没有宿主会话可挂。这是架构位置问题，不是代码量问题。
2. **确定性的原生 subagent 调度**。`multi_agent` 虽 stable，但只能靠自然语言让模型去 delegate，
   `enable_fanout` 仍是 under development。确定性扇出只能靠外层起 N 个 `codex exec` 进程。
3. **权限模型的 runtime 兜底**。「一个 agent 独占一个文件、绝不并发改同一文件」这条约定在 Codex 上同样成立，
   但没有 runtime 帮忙兜底，全靠自己的调度器保证。

---

## 4. 待定事项

1. **7 个 workflow 各归哪一档**（重建 / 降级 / 冻结）。建议：`talent-validate` 与三个研究类优先重建，
   `large-migration` 因为 worktree 隔离本来就没用、按 per-file ownership 重建即可，其余按实际使用频率定。
2. **是否保留一档最低价 Claude 订阅**作为过渡期的第二路审查与 workflow 兜底。
3. **订阅侧的模型名**：切到 ChatGPT 订阅后 `model` 该填什么，需要按当时可用模型确定。

---

## 5. 已知陷阱

- **`workspace-write` 沙箱下 `.git`、`.agents`、`.codex` 三个目录始终只读**（官方沙箱文档）。
  也就是说 **Codex 改不了自己的 skill / hook / agent 定义**。整个迁移过程凡涉及写这三个目录，
  必须在 Codex 之外操作。这条贯穿 G1 到 G3。
- **`codex debug prompt-input` 不含 tool 定义**，不能拿它当「某个工具不存在」的证据。
- **多客户端连同一个 app-server**：分页 thread 是单写者语义，同一 thread 被两个进程持有会返回 JSON-RPC `-32600`；
  请求队列满返回 `-32001`，客户端要自己做指数退避。
- **额度模型**：ChatGPT 订阅模式下 CLI / web / IDE / cloud **共用同一份额度**，5 小时窗口。
  API key 模式不受该窗口约束、纯按 token 计费。官方 subagents 文档警告
  「subagent workflows consume more tokens than comparable single-agent runs」。
- **`~/.codex/config.toml` 属于用户级配置**，按 Mercury 治理规则：改动前备份、开对应 Issue 记录
  「命令清单 + diff 摘要 + 验证步骤」、留回滚步骤。

---

## 6. 来源

官方文档（全部经 2026-08-14 抓取核对）：
[认证](https://learn.chatgpt.com/docs/auth) ·
[配置参考](https://learn.chatgpt.com/docs/config-file/config-reference) ·
[hooks](https://learn.chatgpt.com/docs/hooks) ·
[skills](https://learn.chatgpt.com/docs/build-skills) ·
[subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) ·
[rules](https://learn.chatgpt.com/docs/agent-configuration/rules) ·
[app-server](https://learn.chatgpt.com/docs/app-server) ·
[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk) ·
[非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode) ·
[Windows sandbox](https://learn.chatgpt.com/docs/windows/windows-sandbox) ·
[custom prompts（已废弃）](https://learn.chatgpt.com/docs/custom-prompts) ·
[Claude Code 跨会话消息](https://code.claude.com/docs/en/cross-session-messaging)

源码级证据（**非文档，随时可变**）：`github.com/openai/codex` tag `rust-v0.147.0` 的
`codex-rs/core/src/tools/router.rs`、`multi_agents_spec.rs`、`sdk/typescript/src/turnOptions.ts`、
`codex-rs/app-server/README.md`；`github.com/obra/superpowers`。

---

# 附录：2026-08-14 执行后的修正（实测推翻了正文的多处内容）

> 正文写于调研阶段。下面这些是**动手做之后**被实证推翻或补充的，与正文冲突时**以本附录为准**。
> 执行台账见 [#571](https://github.com/392fyc/Mercury/issues/571)，代码见 PR #572。

## 一、硬陷阱要改得更严重：Codex 做不了**任何** git 写操作

正文写「沙箱下 `.git` / `.agents` / `.codex` 始终只读 —— Codex 改不了自己的 skill / hook / agent 定义」。

**实测更严重**：让 Codex 完整走一遍 dev-pipeline，它改完代码、`node --check` 通过、自己派了两名独立审查代理、不碰无关文件，然后卡在提交：

```
沙箱拒绝创建 .git/index.lock（.git 在当前环境为只读）
```

走 `scripts/codex/git-safe.ps1` 一样被挡 —— **拦它的不是 rules，是沙箱**。

**所以迁移后的工作模式是固定的、不是可选的**：Codex 负责实现与审查，**提交必须在 Codex 之外完成**。这不是配置能解的。

## 二、判据修正（累计 6 处）

| # | 正文写的 | 实测 |
|---|---|---|
| 1 | G2-1「**8 条** hook」 | 实际注册 **10 条**命令（PreToolUse 5、PostToolUse 2、UserPromptSubmit 2、Stop 1）。清单必须从 `hooks.json` 解析生成，不能预设数量 |
| 2 | G2-2 判据用 `codex execpolicy check`（不带 `--rules`） | 该子命令在 0.129.0 与 0.147.0 上**都强制要求 `--rules`**，不做自动发现。运行时的自动发现**是有效的**，证据是 Codex 拦 `git commit` 时原样回显了 rules 里的 justification |
| 3 | G1-1 提到 `project_doc_fallback_filenames` 可让 Codex 读 CLAUDE.md | **实测无效**。它是「AGENTS.md 不存在时的备选」不是「追加读取」，双写不可避免 |
| 4 | G3-3「/mcp 三个都活」 | 三个应为 obsidian / godot / **playwright**。`mercury-orchestrator` 是死配置 —— 实现早随 #173/#174 归档，端口 7654 无监听，留着只会每次调用刷三次错误 |
| 5 | G3「`.codex/prompts/` 14 个文件删或升格成 skill」 | 它们**不是 custom prompts**，是历史工作产物（#439 审计提示、#440 回复草稿、Argus 触发文本、S130 记录），从未入库，该路径也不被扫描 |
| 6 | G4-1「三条主流程各跑通一次」 | dev-pipeline 与 pr-flow 在 Codex 上**只能跑到审查为止**，提交环节受一节所述沙箱限制。这不是「没验证」，是验证出了结构性边界 |

## 三、新发现的限制

- **hook 的实际作用面被挤压**。防护顺序是**沙箱 → rules → 指令层 → hook**，前面的先生效。三次实测都没走到 hook（写 C 盘被沙箱拦、推保护分支被指令层拦、`git commit` 被 rules 拦）。**`.codex/rules/` 是比 hook 更靠前、更可靠的防线**。
- **G2-1 的触发验证在当前权限下无解**。七种方式全试过：四条常规路径各自堵死（拦截型被前置层接走、loop-detector 因 `enabled:false` 触发即退出、post-commit-reset 的触发条件恰好被拦、UserPromptSubmit/Stop 产物无法归因），三种进程采集路径也不通（CIM 订阅带 `-Action` 非交互不执行、不带 `-Action` 拉不到、后台轮询过重拖垮被观测调用）。下一步需 ETW 内核事件采集，要额外权限。探针脚本已提交（`scripts/codex/hook-probe.ps1`），**它的自检机制两次都正确判了 INVALID 而不是报出看似结论的 NO-EVIDENCE**。
- **`read-only` 沙箱下 agent 联不上网**。`network_access = true` 只配在 `[sandbox_workspace_write]` 下，检索类 agent 用 read-only 会静默拿不到网络。已在 `runAgent` 加警告（不自动提升档位 —— 那会静默扩大权限）。
- **TOML 的 section 会静默吞掉后续顶层标量**。在 `.codex/config.toml` 中部插一个 `[section]`，会把它之后的 `developer_instructions` re-parent 进去，**而解析完全合法不报错**。改完必须验证顶层键，光验证「解析通过」查不出来。

## 四、编排层的实战结论

`packages/codex-orchestrator/` 共 **309 行有效代码**（估算档二为 370–520），四个重建样例全部跑通：`codebase-audit`、`plan-review`、`dual-verify`、`multi-source-research`。

**它们反过来查出了本项目自己的问题**，这比「跑通了」更能说明没降质：

- `codebase-audit` 审编排层自己 → 6 个真实缺陷，全部核实成立并修复（其中一条直接推翻了我写的注释：「按需拉取」其实先物化了整个迭代器）
- `plan-review` 分析 hook 验证难题 → 查出「8 条 hook」这个错误数字，并给出进程追踪这条我没想到的路径
- `dual-verify` 第一次运行就判了自己 NEEDS-CHANGES → 报出的 4 个 high 里 3 个是它自身的真 bug
- `multi-source-research` → 三条论断全标 UNVERIFIED 并给出具体卡点，**没有编造答案**

**重建时必须带上的四条**（都是踩出来的）：schema 要过 `normalizeSchema()`（平台强制 `additionalProperties: false` 且 `required` 要列全）；返回值必须自己 parse 加校验（`finalResponse` 是字符串）；未识别错误一律不重试（SDK 抛裸 Error 无码）；每次调用重付约 1.4 万 input token。

---

# 附录二：2026-08-14 后半程的实测（G0-1 等待期间）

> 这一批产生于一个特殊处境：为切换到个人订阅而执行 `codex logout` 之后，
> 认证只能由用户在浏览器完成，于是有一段较长的等待期。
> 期间发现**一批 Codex 子命令根本不需要登录**，因此把能脱离 G0-1 验证的目标都验了。
> 与正文及附录一冲突时**以本附录为准**。

## 一、哪些命令不需要登录（这决定了等待期能做什么）

`codex debug prompt-input`、`codex execpolicy check`、`codex sandbox`、
`codex plugin`（含 `marketplace`）、`codex mcp list`、`codex doctor` 全部是纯本地命令。

这条本身就是个有用的结论：**指令层、规则内容、沙箱行为、skill 与插件的加载状况，
都可以在没有任何认证的情况下验证**。只有真正需要模型推理的目标才被登录卡住。

## 二、一条必须更正的先前结论：superpowers 从未真正被加载

先前记录「superpowers 已安装、14 个 skill、版本 5.1.3」，**依据是看到磁盘上有文件**。

真正的判据是它有没有进入模型可见范围。`codex debug prompt-input | grep -ci superpower`
当时返回 **0**。

根因：安装只写下了插件缓存，**市场源从未注册进配置**。`~/.codex/config.toml` 里有
`[plugins."superpowers@openai-api-curated"] enabled = true`，但 `codex plugin marketplace list`
中根本没有 `openai-api-curated` —— 那条声明指向一个不存在的市场，因而完全惰性。

修复：`codex plugin marketplace add obra/superpowers`（市场名落为 `superpowers-dev`）
→ `codex plugin add superpowers@superpowers-dev` → 版本 **6.3.0**（官方 curated 那份是落后的 5.1.3）
→ 删除指向不存在市场的残留声明。

复验（同一判据）：**14 个 skill 全部出现**，prompt 从 35,905 涨到 41,020 字节。

**教训**：「装好了」的判据必须是**它在目标系统里生效**，不是「文件在磁盘上」。
这是「表面正确、实质错误」的典型 —— 文件确实存在，声明确实写了，但两者没有接上。

**遗留待裁决**：三个 skill 名现在各出现两次 —— `subagent-driven-development`、
`systematic-debugging`、`verification-before-completion`。一份是 Mercury 早先 cherry-pick 的
旧版（SHA `917e5f5`，受 `upstream-manifest.json` 跟踪），一份来自插件 6.3.0。
**同名并存时哪个优先没有文档规定**。未擅自删除受 manifest 跟踪的文件。

## 三、沙箱行为的直接证据（此前多为推断）

| 档位 | 操作 | 结果 |
|---|---|---|
| `workspace-write` | 写仓内 `.mercury/state/` | 成功 |
| `workspace-write` | 写 `C:\Users\<user>\` | 拒绝，文件未创建 |
| `workspace-write` | 写 `C:\Program Files\` | 拒绝，文件未创建 |
| `workspace-write` | 写 `.codex/` | 拒绝，文件未创建 |
| `workspace-write` | 写 `.agents/` | 拒绝，文件未创建 |
| `workspace-write` | 写 `.git/` | 拒绝，文件未创建 |
| `read-only` | 写仓内 | 拒绝，文件未创建 |
| `read-only` | 读 `AGENTS.md` | 成功 |

**硬陷阱由此从推断变成实证**。这条是整个迁移计划的承重假设 ——
它决定了 skill / hook / agent 定义必须在 Codex 之外修改。

**附带一个会坑人的发现**：`codex sandbox` 这个独立子命令**不读取项目级 `.codex/config.toml`**
（其 `-c` 帮助文本明说是覆盖 `~/.codex/config.toml`），不显式给档位时默认落在只读。
第一次测仓内写入被拒，差点误判成项目配置没生效。**调试沙箱行为必须显式
`-c sandbox_mode=...`，否则测的根本不是项目配置。**

## 四、Codex 原生 multi-agent 的调用契约（读自运行时提示）

这段直接影响 G4-2 能不能做对。内容来自 `codex debug prompt-input` 读到的实际系统提示，
不是从公开文档推断的。

**协作工具**：`spawn_agent`、`followup_task`、`send_message`、`wait_agent`、
`interrupt_agent`、`list_agents`。它们**必须作为直接工具调用发起**，
**不能从 `functions.exec` 内部调用** —— 被有意排除在 `exec` 的 `tools.*` 命名空间之外。

**并发 4 槽且把自己算在内** —— 最多同时跑 3 个子 agent。

**最容易踩的一条**：full-history fork（`fork_turns` 省略或 `"all"`）
**继承父级的 model 与 reasoning effort，且不接受覆盖**。要设 `model` 或 `reasoning_effort`，
必须同时把 `fork_turns` 设为 `"none"` 或一个正整数字符串。

后果很实在：「spawn 两个全历史 fork、分别给不同 effort」这种直觉写法会**静默失效** ——
两路以完全相同的档位跑，比对出来的「两份结论」其实是同一配置的两次采样，
**dual-verify 这个门就成了装饰品**。

巧的是 `fork_turns: "none"` 同时也是盲审的前提：全历史 fork 会把主 agent 的推理
一并带给审查者，那它审的就不是「改动本身」而是「主 agent 如何为改动辩护」。
两个要求指向同一设置，不冲突。

**共享文件系统**：所有 agent 共用同一工作目录，写入对彼此立即可见。
所以盲审的隔离**只存在于上下文层，不存在于文件层** —— 两个审查者必须都是只读的。

**`<multi_agent_mode>` 默认禁止主动 spawn**，除非用户、`AGENTS.md` 或 **skill 指令**明确要求。
这意味着不能指望模型自己想到要开两路，必须由 skill 显式触发。

**这也更正了附录一之前的一处措辞**：先前写「这些工具名只存在于源码与 release notes」，
不准确 —— 它们连同完整行为契约都写在运行时提示里。
「不在官方 subagents 文档正文里」这半仍然成立。
顾虑因此从「没有任何记载」退回到「记载不是公开契约、随版本改动且无迁移公告」
（`send_input` 已经拆成 `send_message` + `followup_task` 一次）。
**一次性调用照着运行时提示写没问题；长期跨组协作的承载物押在它上面则不行。**
跨组仍用文件式收件箱，结论不变。

## 五、判据修正（续附录一，第 7–9 条）

| # | 原判据 | 实测 |
|---|---|---|
| 7 | G3-2「**9 个** subagent → `.codex/agents/*.toml`」 | 应为 **8 个**。差的那个 `main` 是顶层 agent 的角色说明文档，其 frontmatter 明写 "NOT meant to be spawned as a sub-agent"，本就不该作为 subagent 迁移。属计数口径问题，不是迁移缺口 |
| 8 | G3-3「/mcp 三个都活」 | playwright 这一路**在任何 harness 上都起不来**：它依赖的 `MERCURY_PLAYWRIGHT_STORAGE_STATE` 在进程/用户/机器三层全未设置，适配器 `expandEnv()` 按设计 fail-closed。**非迁移回归** —— `.mcp.json` 里是逐字相同的参数。要可用需用户把该变量指向真实的 storage-state 文件（含浏览器登录态，用户私有路径） |
| 9 | 附录一第 2 条称「运行时的自动发现**是有效的**」 | 该观察**发生在移除 azure 路由之前，尚未复测**。规则内容本身已用显式 `--rules` 验证正确（`git push origin HEAD` → forbidden 并回显 justification；`git status` → 无匹配）。但运行时自动发现的复测需要 codex 能真实调用，即需先完成 G0-1。在此之前不应当作已确认 |

## 六、一个被实测逼出来的真 bug（G4-3）

补 Windows 计划任务时，把 `scripts/codex/pr-watch.ps1` 的失败路径逼出来了 ——
**它一直是死代码**，而它正是 G4-3 点名的两条安全约定之一。

**缺陷**：`gh` 查询失败时只创建输出文件、不写内容；`Get-Content -Raw` 对**空文件**
返回 `$null` 而非空字符串；`.Trim()` 打在 `$null` 上抛
`You cannot call a method on a null-valued expression`；叠加
`$ErrorActionPreference = 'Stop'`，脚本在抵达底部失败分支**之前**就终止。

**后果**：网络抖动的真实表现是崩溃而非退避，且状态文件完全不落盘。
复现：连查 3 次不存在的 PR，三次全崩，状态文件一次都没生成。

**修复**（commit `5ed2b81`）：`Read-TextFile` helper 把「不存在」与「空文件」
统一收敛成 `''`；同时加固状态文件加载 —— 截断的 JSON 会让 `ConvertFrom-Json`
返回 `$null`、随后 `$state.done` 同样空引用崩溃，现改为退回全新状态并告警。

**四层验证**：脚本逻辑（上限停在 3 次标记 `exhausted`；退避 60/120/240 秒，
`polls` 保持 0 表示失败不吃配额）→ 损坏恢复（空文件与截断 JSON 均能恢复）
→ 计划任务动作（`LastTaskResult: 0`，状态文件由任务本身写出）
→ 触发器自主起火（`LastRunTime` = `StartBoundary`，`NextRunTime` +4 分钟）。

**教训与附录一「编排层实战结论」互为对照**：同一个「失败路径是不是死代码」的问题，
`pr-watch` 的答案是「是」，编排层的答案是「否」—— 后者在登出导致的 401 下表现完全正确
（判为不可重试只试一次、返回 null 不抛出、汇总照出、丢弃工作量按 #385 护栏记账）。
**差别在于编排层的失败路径被真实走过，而 pr-watch 的从未被走过。**

## 七、G4-3 与目标原文的一处偏离（待裁决）

目标写「计划任务每 3-5 分钟调 `codex exec --json`」，实现是**直接调 `gh`**。

理由：纯状态查询让模型跑一遍固定吃约 1.4 万 input token，而它做的只是读一个 JSON 字段。
轮询上限 3 次与失败退避两条约定均保留。这是取舍不是疏漏，可推翻。

## 八、G5-3 记忆层：修好了一半，另一半是新发现的缺口

### 已修：三个 hook 在 Codex 下会静默什么都不记

用 Codex 形态的合成 stdin 跑记忆层三个 hook，三个都 exit 0。**但 exit 0 不等于干了活。**
查副作用发现 SessionEnd 没有产出任何 flush 文件，日志给出真因：

```
SessionEnd fired: session=g53-probe-end source=unknown
SKIP: empty context
```

两处已知差异的结论一好一坏：

- **差异 1（Codex 给 `reason`、脚本读 `source`）无害** —— `source=unknown`，
  `.get("source", "unknown")` 的默认值兜住了，不崩。
- **差异 2 是真的** —— 不是「轮次太少」，是**解析出零条消息**。

**用真实数据核实**（不是只信自己手写的夹具）：扫 6 个真实 rollout 后确认，
Codex 每行只有 `timestamp` / `type` / `payload` 三个顶层 key，
`message` / `role` / `content` 在顶层出现 **0 次** —— 内容嵌在 `payload.role` /
`payload.content` 里。而解析器读的是 `entry["message"]["role"]` 或退化到
`entry["role"]`，两者都不存在。

**还有第二层陷阱**：content 块的 type 是 **`input_text`**（OpenAI Responses 形态），
不是 Claude 的 `"text"`。只修嵌套层级、不改块类型判断的话，仍然读到空。

**修复**：两个文件各加一支 Codex 分支（`session-end.py` 与 `pre-compact.py`
**各有一份逐字相同的 `extract_conversation_context` 拷贝** —— 改一个必须改另一个，
只改一处会留下一半的静默失败；长期应抽成共用模块）。

只取 `type == "response_item"`：`event_msg/user_message` 是同一轮用户输入的另一种表示，
两个都收会重复计数。

**三项判据验证**（两个文件各跑一遍，全过）：
| 判据 | 结果 |
|---|---|
| A. Codex 路径能读出内容（5 个真实 rollout） | 全部有内容（改动前为零） |
| B. Claude 现役路径未回归（3 轮、内容齐全、summary 行忽略） | 通过 |
| C. 不重复计数（同一轮的两种表示只记一次） | 通过 |

端到端复验：用真实 rollout 跑完整 SessionEnd →
`Flushing session g53-real-end: 11624 chars` → `Result: FLUSH_OK`，
产出 13,593 字节的 flush 文件。改动前是 0 字符、`SKIP: empty context`。

备份：`~/.claude/hooks/session-end.py.backup-pre-571-g53`、
`~/.claude/hooks/pre-compact.py.backup-pre-571-g53`。

### 未修：提取环节硬依赖 Claude CLI（新缺口）

日志里的 `Using claude CLI: C:\Users\392fy\.local\bin\claude.exe` 暴露了另一半问题。

`~/.claude/scripts/flush.py` 的 `_find_claude_exe()` 找不到 claude 可执行文件就直接
`return "FLUSH_ERROR: claude executable not found"`，**全文对 codex 的引用数为 0**。

所以记忆层现在的状态是：

| 环节 | 状态 |
|---|---|
| hook 注册与触发 | 可用 |
| 解析 Codex transcript | **已修，可用** |
| 提取与写入记忆 | **依赖 Claude CLI —— 订阅失效即断** |

**这正是本次迁移的起因所在**（Claude 订阅到期），所以这不是理论风险。
要让记忆层在纯 Codex 环境下可用，`flush.py` 需要一条 `codex exec` 的替代提取路径。
该改动需要 G0-1 完成后才能验证，且属用户级脚本的实质变更，**未擅自动手**。
