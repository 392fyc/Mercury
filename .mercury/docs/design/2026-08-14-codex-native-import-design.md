# Codex 原生 Mercury＋SoT 领域完整导入设计

- 日期：2026-08-14
- 状态：设计已获用户批准；双盲审 PASS，待用户复核
- 总台账：[#571](https://github.com/392fyc/Mercury/issues/571)
- 目标宿主：Codex Desktop / Codex CLI 0.147.0

## 1. 目标与非目标

本迁移把 Claude Code 长期维护的 Mercury＋Ship of Theseus（SoT）工作环境完整导入
Codex，并把运行时收敛为 Codex 官方原生能力。Mercury 的职责是提供稳定工作流、质量门禁、
可复核交接和产物一致性，不承担自建通用编排平台。

### 目标

1. 使用 Codex Desktop 官方 Import 导入 Mercury＋SoT 领域的配置、记忆、聊天、项目技能和代理。
2. 用 Codex 原生 skills、subagents、hooks、rules、tasks/threads 和 MCP 复现有效工作流。
3. 保留 Issue-first、dual-verify、官方文档核验、单写边界和 SoT 真源链。
4. 恢复 Godot 与 Obsidian MCP，并为 KB 保留受控本地文件系统 fallback。
5. 在原生能力验收后删除 Dynamic Workflow、外部 orchestrator、Mercury GUI 和其他已弃用控制层。
6. 在导入前把 C 盘可用空间提高到至少 20 GiB（`20 * 2^30` bytes）。

### 非目标

- 不恢复或继续天赋优化；相关 validator 仅冻结留档。
- 不把 OMC、Claude HUD、tmux worker 或 Claude-hosted Codex wrapper 作为最终依赖。
- 不用新的外层 Node/SDK orchestrator 替代 Claude Dynamic Workflow。
- 不删除 SoT 设计数据、KB 内容、聊天历史或仍未完成官方导入的 Claude 源数据。

## 2. 已验证事实与对 Claude 交接的修正

1. Codex 当前 `external_agent_config_imports` 记录为 0，官方导入尚未开始。
2. Codex 0.147.0 支持项目 skills、原生 subagents、MCP、rules 和 hooks。
3. Claude 交接中“Codex hooks 官方文档 404、hooks 不存在”的结论已过时；当前官方文档明确支持
   用户级和项目级 hooks，并要求对非托管 hook 的精确哈希进行信任审核。
4. 本会话已验证 root→agent、agent→agent peer message、follow-up 唤醒和结果聚合。
5. Godot MCP 已完成 `project/get_info` 只读端到端调用；直接依赖固定为
   `@satelliteoflove/godot-mcp@2.15.0`。
6. Obsidian Local REST API with MCP 正在监听：
   - `http://127.0.0.1:27123/mcp/` 未认证返回 401；
   - `https://127.0.0.1:27124/mcp/` 未认证返回 401。
7. Dynamic Workflow 目录含 7 个 JS workflow 和 1 份 README。近期高调用量主要来自本次迁移
   准备和测试。用户裁决：只有在每个 workflow 的有效契约都被官方原生方案替换或被明确冻结归档后，
   才删除旧机制并启用原生协议。
8. Mercury GUI 当前无进程、安装项、快捷方式、计划任务、CI caller 或活跃脚本 caller。
   Issue #427 仍是开放的可选 v2 backlog，不构成历史弃用裁决；删除前须在 #571 记录新裁决。
9. 四根当前均有既存未提交状态：Mercury 5 项、Godot 1 项、设计库 1 项、KB 6 项。它们属于
   用户或其他 lane；迁移不得覆盖、暂存、提交或借 Git 回滚这些内容。
10. 待导入聊天中存在凭据形态内容。任何仍有效的旧 key 必须在 Import 前失效；不能先把有效凭据
    复制进 Codex 再轮换。

## 3. 权威根目录与导入资产

### 3.1 四个领域根目录

| 根目录 | 职责 | 导入方式 |
|---|---|---|
| `D:\Mercury\Mercury` | harness、质量门、工作流治理 | 官方 Import＋原生化对账 |
| `D:\ShipOfTheseus\Ship_of_Theseus` | Godot 游戏实现 | 官方 Import＋项目技能对账 |
| `D:\ShipOfTheseus\SoT-fyc-space` | 设计库软件与生产数据接口 | 通过领域 instructions/skills 接入；无独立 Claude 项目资产 |
| `D:\ShipOfTheseus\ShipOfTheseus-KB` | Obsidian 知识库 | Obsidian MCP＋受控本地文件系统；无独立 Claude 项目资产 |

不能用一条扁平的“设计库 > KB > 代码库”替代字段级权威。当前生效的单写矩阵是：

| 范围 | 允许写者 | 禁止/派生规则 |
|---|---|---|
| Mercury 仓 | Mercury lane | 其他 lane 只能 handoff |
| 设计库 `app/`、`tests/`、`scripts/` | Mercury lane | SoT lane 不直接改软件实现 |
| 设计库生产活数据 | 用户＋SoT lane | Mercury 只读并提出建议 |
| 设计库 `snapshots/` | 活数据写方运行导出器 | 任何 lane 均不得手写 snapshot |
| Godot 仓全域 | SoT main lane | Mercury 只能 handoff |
| KB | SoT main lane＋用户 | Mercury 只能 handoff；`.obsidian/` 不作迁移写目标 |
| 跨 lane 文档 | 发起方 | 收件箱追加、commit、push 后才算送达 |

2026-08-14 用户最新裁决是 `power`、`cd`、`range` 全部归设计库权威；证据位于
`D:\ShipOfTheseus\SoT-fyc-space\docs\cross-lane-inbox.md`。canonical
`mercury-sot-lane-management.md` 仍混有相反旧说法，因此在 SoT lane 按收件箱更新该文档之前，
不得依据这些字段执行任何迁移写入。越界写只能通过 handoff，或由用户给出路径、期限和范围都明确的
临时授权。

### 3.1.1 Dirty-tree 保护门

任何 Import 后改写或删除前，为四根生成不可变 preflight manifest：root、branch、HEAD、
`git status --porcelain=v1`、tracked diff、untracked 路径及内容哈希。当前基线为：

| 根 | Branch | HEAD | Dirty entries |
|---|---|---|---:|
| Mercury | `feature/TASK-571` | `b5bb644d9b28e4abae5b2649c2defd668399edf7` | 5 |
| Godot | `develop` | `f54ad5a4b81759661805929d326006c3a7131102` | 1 |
| 设计库 | `master` | `e2c6241122add9063ee47a976994f69c8678e932` | 1 |
| KB | `master` | `162057cbf6512e83f600fa052e339bcb1d10df93` | 6 |

未提交内容要单独复制到 D 盘可恢复备份并冻结并发写入；任何计划删除路径与 dirty path 相交时立即停止。
迁移提交只能显式暂存本阶段生成文件。

### 3.2 官方 Import 采集清单

最近 30 天共有 68 个 Claude 主聊天：

- `~/.claude` cwd：45 个，其中 44 个首条请求命中 Mercury、SoT、Godot、KB、Codex、MCP
  或天赋领域关键词；剩余 1 个在 Import UI 中人工复核。
- Mercury cwd：14 个，全部导入。
- SoT Godot cwd：9 个，全部导入。

不得把 `~/.claude` 下的 45 个聊天整体视为无关，也不能把“67 个”当作无需秘密治理的硬导入数。
68 个聊天必须全部进入机器可读 manifest，记录 session ID、源 cwd、首条请求哈希、领域分类、
凭据扫描状态、最终去向和 Import 结果。最终去向只能是：

- `import`：领域相关，且其中曾出现的凭据已经失效；
- `exclude-secret`：凭据无法在 Import 前可靠失效，改作受控加密归档；
- `exclude-domain`：人工复核确认不属于 Mercury＋SoT，并记录理由。

Import 前先在服务端轮换所有已暴露凭据并证明旧 key 返回 401。新 key 通过本机安全输入写入环境变量，
不得粘贴到聊天、命令行、manifest 或日志。只有完成这一步后，才允许导入含旧 key 的历史聊天。

凭据扫描必须覆盖全部导入与备份输入，而不只覆盖聊天：settings、MCP/static headers、hooks、commands、
skills、agents、memories、dirty/untracked 文件、配置备份和 attachments。manifest 只记录“命中类型、
源资产 ID、轮换/脱敏/加密归档/排除裁决”，不得记录 secret 值或可逆摘要。D 盘备份使用仅当前用户
可读 ACL；含不可脱敏敏感内容的归档还必须加密。

发现的其他资产：

| 来源 | Skills | Agents | Commands | Workflows | Memories |
|---|---:|---:|---:|---:|---:|
| 用户级 `~/.claude` | 45 | 10 | 1 | 0 | 0 |
| Mercury | 12 | 9 | 2 | 8 个文件 | 295 个 Markdown＋3 个辅助文件 |
| SoT Godot | 5 | 0 | 0 | 0 | 57 |

Mercury 的 3 个辅助文件（JSON、Python、PYC）不是 memory 文档，必须单独裁决而不能计为成功导入。
旧 Mercury/SoT 项目目录另有 26 个领域相关 memory 文件，作为 archive 导入；
`TradingAgents` 的 3 个 memory 不属于本领域，单独留档而不进入活跃上下文。

全部 378 个 memory 文档＋3 个辅助资产（298＋57＋26，共 381 项）都必须在 asset manifest 中有源路径、类型、哈希、
目标和裁决；完成判据按 manifest 逐项核对，不按目录总数猜测。

### 3.3 采集层与启用层

官方 Import 负责完整采集，启用层只保留通过原生化验收的能力：

- 导入相关聊天、记忆、instructions、settings、项目 skills、agents、hooks、MCP 和 commands。
- 自动同步初始保持关闭，避免 Claude 侧废弃物反复写回 Codex。
- 执行锁定为 Codex Desktop Import；CLI 最近聊天上限为 50，不能静默回退到 CLI。
- OMC、Claude HUD、Claude Agent SDK、tmux team、Claude-hosted Codex wrapper 可以作为来源记录，
  但不得进入最终启用层。
- 现有 Superpowers 6.3.0 保持为方法论唯一来源；同名重复 skill 删除或禁用。

官方文档：

- Import：<https://learn.chatgpt.com/docs/import>
- Subagents：<https://learn.chatgpt.com/docs/agent-configuration/subagents>
- Hooks：<https://learn.chatgpt.com/docs/hooks>
- MCP：<https://learn.chatgpt.com/docs/extend/mcp>

## 4. Codex 原生目标架构

### 4.1 指令与技能

- `AGENTS.md` 是仓库指令真源；删除“角色由外部 orchestrator 注入”等过时假设。
- `.agents/skills` 是 Mercury 项目技能真源，`.claude/skills` 在迁移验收后退役。
- SoT 五个领域技能保留，但改为实际 Codex tool contract。
- `handoff`、`dual-verify`、`pr-flow` 是近期有直接使用证据的核心流程，优先迁移。
- `dev-pipeline` 和 subagent-driven development 重写为 `spawn_agent`、消息传递和 plan/goal 语义。
- 天赋 validator 冻结，不触发、不继续设计、不在此迁移中删除其唯一历史实现。

### 4.2 多代理与即时消息

原生协议承担：

- 独立 subagent 并行；
- root↔agent 与 agent↔agent 消息；
- follow-up 任务唤醒；
- 主线程结果聚合；
- 不同 reasoning effort 的盲审。

`dual-verify` 必须使用两个独立 agent，`fork_turns: "none"`，并显式指定不同
`reasoning_effort`。单一上下文自审不计入门禁。

当前能力基线锁定为 Codex 0.147.0。每次 Codex 升级后必须重跑 spawn、peer message、follow-up、
handoff、dual-verify 和 hooks smoke；失败时停止任何后续删除或启用步骤。

### 4.3 Dynamic Workflow 与外部 orchestrator

删除门是“每个功能契约已由官方原生机制通过固定夹具与失败路径验收”，不是“通用并行探针通过”或
“文件看起来旧”。逐 workflow 裁决如下：

| Workflow | 裁决 | 必须保留/复现的契约 | 删除前证据 |
|---|---|---|---|
| `mercury-codebase-audit` | REPLACE | security/correctness/resource 扇出、结构化 finding、逐条对抗复核、cap 与溢出日志、失败隔离 | 固定缺陷 repo 黄金对拍；worker 失败与超时夹具 |
| `mercury-multi-source-research` | REPLACE | 多角度官方来源检索、逐 claim 独立交叉核查、引用、`UNVERIFIED` | 固定研究题的 claim/citation 对拍；无网与单源冲突夹具 |
| `mercury-adversarial-plan-review` | REPLACE | N 份独立计划、judge panel、评分、综合优胜＋亚军亮点、只产计划 | 固定 spec 盲审对拍；单 reviewer 失败夹具 |
| `mercury-large-migration` | REPLACE | per-file ownership、并行改造、逐文件验证、循环至收敛、未处理工作量日志、不自动提交 | 小型多文件 fixture；冲突、失败、未收敛和中断恢复夹具 |
| `mercury-staleness-audit` | REPLACE/ON-DEMAND | manifest 发现、web 核查、对抗复检、六类分类、只产报告 | 固定旧 pin fixture；无网时 `UNVERIFIED` |
| `mercury-ecc-practice-scan` | ARCHIVE/ON-DEMAND | recon、交叉核查、三类映射、只产报告 | 完整 archive manifest；未来启用前按原契约重验 |
| `talent-validate` | FREEZE/ARCHIVE | 唯一 JS 实现、README、usage、依赖、输入输出样例、L1–L4 语义与哈希 | 只读 archive 完整性校验；本迁移不执行天赋任务 |

每项状态只能是 `REPLACED-PASS` 或 `FROZEN-ARCHIVE-PASS`。任何未决项都会阻止删除。尤其
`talent-validate.js` 不得随目录整体删除，必须先迁至明确的只读 archive 并保留哈希与恢复说明。

所有 `REPLACE` workflow 共享以下强制资源不变量，不能只在 codebase audit 上实现：

- 显式并发上限，且不得超过当前 Codex session/platform 限额；
- 显式总 agent/turn 预算、每阶段 cap、单调用 timeout 和全局最大轮次；
- cancel 后停止派生新任务并清理临时状态；worker 失败相互隔离且不被吞掉；
- 所有截断、去重后丢弃、预算耗尽和未处理工作量都必须报告数量与范围，禁止静默少审；
- 未收敛必须返回失败或带剩余清单的非通过状态，不能无限循环或伪装完成；
- 固定夹具必须覆盖越界输入、预算耗尽、超时、取消、worker 失败和未收敛恢复。

删除前生成不可变 allowlist manifest，逐项记录 tracked/untracked 路径、替代入口、caller、哈希、
恢复 commit/tag 和用户裁决。先迁移 caller、docs、CI 和 skill discovery，再原子删除 allowlist；
删除后运行负向 caller scan、全部 CI、skill discovery 和四根 smoke test。只有此后才删除：

- 6 个已替换/归档的活跃 Dynamic Workflow JS 入口及 README 中的活跃触发说明；
- `packages/codex-orchestrator`；
- allowlist 中的旧 Claude launcher、tmux worker、Claude-hosted Codex review wrapper；
- 已证明仅服务这些入口的 gate、文档和 CI 路由。

`.claude/hooks` 不属于泛化删除范围；其 handler 必须先完成下节逐项映射。保留的工作流应是可读
skill/agent 定义和小型确定性校验脚本，不再存在通用编排层。

### 4.4 Hooks 与 rules

保留 `.codex/rules` 作为独立命令防线。解析用户级和 Mercury Claude 配置得到的实际事件必须全部
有去向：

| Claude 事件 | Codex 去向 | 验收重点 |
|---|---|---|
| `SessionStart` | `SessionStart` | startup/resume/compact 上下文注入 |
| `SessionEnd` | `SessionEnd` | 3 秒内原子保存 transcript 快照并写 queue envelope；主线程限定 |
| `PreCompact` | `PreCompact`＋`PostCompact` | compact 前保存、后续恢复注入 |
| `UserPromptSubmit` | 同名事件 | 附加上下文与阻断返回结构 |
| `PreToolUse` | 同名事件 | Bash、`apply_patch`、MCP 覆盖和 deny |
| `PostToolUse` | 同名事件 | 成功输出与 Bash 非零退出 |
| `PostToolUseFailure` | `PostToolUse` 适配 | 官方无同名事件；从 `tool_response`/非零退出识别，覆盖不到的工具失败明确降级 |
| `SubagentStop` | 同名事件 | JSON 输出、继续 subagent、重复防护 |
| `Stop` | 同名事件 | continuation、重复防护 |

迁移步骤：导入 handler 清单并记录文件哈希；先禁用；按官方 payload 改写；在 `/hooks` 审核并信任
精确哈希；对每个 handler 测成功、阻断、超时、非零退出和只读沙箱。hook 未通过完整事件/handler
矩阵前不得作为强制门，也不得用 `features.hooks = true` 伪装可用。Codex 升级后必须重跑该矩阵。

`SessionEnd` 官方上限为 3 秒且会取消未完成后台 hook，因此不得把可能失效的 `transcript_path`
当作唯一数据。hook 在主线程内打开 transcript，将不超过 64 MiB 的完整字节流写入同卷临时文件，
同步落盘并原子 rename 为不可变快照，同时计算 SHA-256；随后原子写入包含 session ID、原路径、
快照路径、byte size、SHA-256、cwd、时间和待处理动作的 durable queue envelope。源文件超过上限、
读取/落盘/哈希/rename 任一步失败或 2.5 秒软截止将触发非零退出、醒目错误日志和未完成标记；
对应旧 handler 在该失败夹具通过且真实 transcript 尺寸分布验证前不得退役。hook 内不得运行 Git、
LLM 提取或寄希望于后台进程。队列只能由下一次 `SessionStart` 或明确登记的 Codex 官方原生 task
消费；禁止新增 Windows Task Scheduler、daemon 或外部 worker。消费者先校验 size/hash，成功后
幂等标记；快照仅在结果持久化并通过验证后回收。Claude
`PostToolUseFailure` 没有同名官方事件；实现阶段必须列出 `PostToolUse` 无法观察的具体工具失败类型。
若任何现役关键失败不可观察，旧 handler 不得退役，除非用户明确接受该项能力降级。

快照和 queue 固定在解析后的 `<CODEX_HOME>\state\session-end-queue`，拒绝 reparse point、网络盘和
云同步目录。实现时以 ACL 机械验证仅当前用户与 `SYSTEM` 可读写，并设置 1 GiB 总容量门。成功消费的
快照在 24 小时内删除；孤儿或失败项进入同目录受限 quarantine，自动保留期最多 30 天，期满后阻断
继续处理并请求用户裁决，绝不静默丢弃。每次 `SessionStart` 必须扫描“快照已 rename、envelope 尚未
写入”窗口产生的孤儿，并根据快照旁车 size/hash 恢复 envelope 或发出醒目错误。

### 4.5 长会话上下文

用户级配置加入：

```toml
model_auto_compact_token_limit_scope = "body_after_prefix"
```

不手动声明 `model_context_window`，不覆盖模型默认压缩阈值。主任务只接收代理聚合结论，
避免把原始日志全部注入上下文。

## 5. MCP 与 KB

### 5.1 Godot

- 保持 direct package pin `@satelliteoflove/godot-mcp@2.15.0`。
- 重新填充被清理的 npm cache 后，再跑 `initialize`、`tools/list` 和 `project/get_info`。
- 传递依赖仍有 semver 浮动风险，记录为残余供应链风险。

### 5.2 Obsidian

目标配置只引用环境变量名：

```toml
[mcp_servers.obsidian]
url = "http://127.0.0.1:27123/mcp/"
bearer_token_env_var = "OBSIDIAN_API_KEY"
required = false
default_tools_approval_mode = "writes"
```

- 任何曾出现在聊天、日志或备份中的 key 都视为已暴露；Import 前先在 Obsidian 侧轮换，并证明
  所有旧 key 返回 401。聊天中的 key 不得被用作恢复凭据。
- 新 key 只通过本机安全输入写入用户环境变量，不进入聊天、命令行、TOML、仓库、日志、备份、
  manifest 或设计文档。
- 重启 Codex 以刷新父进程环境。
- 验收使用专用 probe 目录和 UUID 文件名：先断言路径不存在，以 create-only 写入固定内容并保存
  内容哈希，readback 后在删除前再次核对路径和哈希；`finally` 只清理本次创建对象，最后确认 404/
  不存在且 KB 相对基线无额外 diff。
- 完整链路：`initialize` → `tools/list` → search/read → create-only probe → hash readback → guarded delete。
- MCP 不可用时，SoT KB skills 使用 UTF-8、可复核 patch、git diff 和状态检查的本地文件 fallback。
- 修复缺失的 `03-AI-Context/Active-Context/current-session.md` 入口。

## 6. C 盘导入门禁与清理策略

### 6.1 已完成

- 初始可用空间：12.726 GiB。
- `disk-space-cleaner` Safe 阶段释放：0.051 GiB。
- 9 个精确可重建缓存/旧版本移动至
  `D:\Codex-Cleanup-Quarantine\2026-08-14`，隔离内容约 2.513 GiB。
- 当前运行时观测可用空间：约 15.20 GiB；该数字会随系统活动变化，不作为静态验收值。

隔离项包括 Playwright 浏览器缓存、uv/pip cache、Cargo registry、两个旧 VS Code extension
版本、一份 `.old` Claude 可执行文件和 NVIDIA `ota-artifacts`。隔离而非删除，便于恢复。
`D:\Codex-Cleanup-Quarantine\2026-08-14\manifest.json` 记录原绝对路径、目标、bytes、文件数、
mtime、可复算 metadata inventory hash 和恢复策略；`verify_manifest.py` 是规范 verifier，
`SHA256SUMS` 固定 manifest 与 verifier 自身哈希。目录 inventory hash 不证明文件内容完整性；这些目录
均为可重建缓存，回滚判据是 inventory 可复算或应用重新生成。`uv\cache` 已被运行时重新创建，
不得覆盖恢复。NVIDIA Logs 因 ACL 拒绝而保持原位，空目标不计入隔离成果。

### 6.2 硬门

官方 Import 启动瞬间的实时 `Get-PSDrive C` 可用空间必须不少于 20 GiB，清理目标为 22 GiB 以保留
导入安全余量。不得通过删除以下内容达标：

- Claude 聊天、projects、memory 或 Import 尚未验收的插件源；
- Codex 当前 runtime、state、sessions 或日志数据库；
- pagefile、`C:\Windows\Installer`、用户文档、游戏存档；
- 无法证明可重建的 AppData。

### 6.3 待用户确认的应用级候选

| 候选 | 估算空间 | 风险/条件 |
|---|---:|---|
| Visual Studio Build Tools＋Windows SDK | 约 5.1 GiB | 若除拟弃用、尚待正式裁决的 Mercury GUI/Tauri 外无本机 C++ 构建需求，可卸载 |
| Claude 当前 binary/share＋Claude plugin cache | 约 2.3 GiB | 仅在官方 Import 与回滚归档验收后清理 |
| OMC 全局 npm 包 | 属于 0.83 GiB npm 全局树 | 纯 Codex 最终态必须卸载；先确认无其他入口依赖 |
| `opencode-ai` | 同上 | 不属于 Mercury 必需项，但可能是用户独立工具，需确认 |
| Antigravity/Copilot/Gemini 本地数据 | 约 0.93 GiB | 独立工具数据，需确认是否仍使用 |
| VS Code Claude/旧 ChatGPT extensions | 约 0.93 GiB | 若 VS Code 不再承担 AI 工作流可卸载 |
| QQ 拼音 users 数据 | 3.23 GB | 今日仍在写入，疑似用户词库；默认保留 |
| Rust toolchain | 约 1.33 GiB | 若无 GUI 之外的 Rust 项目可卸载 |

Windows 组件存储分析和 DISM 需要管理员终端；不自动关闭休眠、不修改 pagefile。

确定顺序为：

1. 已完成可恢复 cache 隔离，并用 manifest 校验。
2. 用户确认领域外依赖、#571 记录 GUI 裁决并明确接受“失败时需重装工具链”后，才可卸载 Rust
   toolchain＋Visual Studio Build Tools/Windows SDK。卸载前导出 `rustup show`、installed
   toolchains/targets/components，并用 Visual Studio Installer 官方 `export` 保存 `.vsconfig`、
   instance version、install path 和 bootstrapper/reinstall 说明到 D 盘 cleanup manifest。四根中 Rust
   仅由 Mercury GUI 及其 archive 使用，`cargo install --list` 当前为空。若不接受重装风险，改选
   不影响 GUI 回滚的空间候选。官方依据：
   <https://learn.microsoft.com/visualstudio/install/import-export-installation-configurations>。
3. 若仍不足 22 GiB，再由用户裁决 Antigravity/Copilot/Gemini、VS Code AI extensions、
   `opencode-ai`；Cursor 当前正在运行，不列入删除候选。
4. 官方 Import 与回滚演练通过后，卸载 OMC、Claude binary/share 和 Claude plugin cache。
5. 每阶段重新测量实际 free bytes，不以估算空间宣布达标；每个移动项先更新 quarantine manifest。

## 7. Mercury GUI 与 CI 收敛

### 7.1 GUI

原生 harness 验收后：

1. 在 #571 记录正式弃用裁决和恢复路径。
2. 冻结恢复 tag/commit，生成精确 tracked/untracked 删除 manifest。
3. 关闭或重定向 #427，并先修改 DIRECTION、README、AGENTS、caller、gate、docs 和 CI。
4. 运行负向 caller scan 与 CI，证明没有活跃入口依赖 GUI。
5. 删除 allowlist 内的 tracked GUI 源；Git 历史/tag 保留恢复能力。

`mercury-gui/src-tauri/target` 是独立的可重建本地产物，可在绝对路径和 D 盘目标验证后单独清理，
预计释放 D 盘约 10.5 GiB；它不需要等待源代码弃用裁决，也不能与 tracked source 删除混成一步。

### 7.2 CI

- 保留 `auto-verify.yml`。
- 保留 `upstream-drift.yml`。
- `skill-drift.yml` 从 `.claude/skills` 改为 `.agents/skills` 和 Codex plugin manifest。
- 依赖 Anthropic 的 `external-intel.yml` 退役或改成当前 Codex 情报需求，不能继续假装智能 judge 已运行。
- 原生 dual review 通过后退役 `codex-sync-audit.sh`。

## 8. 顺序、回滚与验收

### 8.1 执行顺序

1. 为四根生成 dirty-tree preflight manifest 和未提交内容备份，冻结并发写入。
2. 在 #571 记录已批准架构；由 SoT lane 先修正字段权威 canonical 文档。
3. 对 68 个聊天、378 个 memory 文档和 3 个辅助资产生成 asset manifest；扫描全部资产与备份凭据。
4. 在服务端轮换所有暴露 key并证明旧 key 失效；新 key 只通过本机安全输入保存。
5. C 盘实时可用空间达到硬门 20 GiB、目标 22 GiB；D 盘保存配置、清单和哈希备份。
6. 用 SQLite backup API 获取一致的 Codex state 快照，或在 Codex 完全退出后连同 WAL/SHM 复制；
   在隔离副本上完成可读取恢复演练。
7. Codex Desktop 官方 Import，自动同步关闭；记录 import batch ID 及逐项 successes/failures。
8. 对账聊天、记忆、instructions、skills、agents、hooks、MCP 和 commands。
9. 重写原生 skills/agents/handoff/dual-verify 和逐 workflow 原生入口。
10. 恢复完整 hooks 事件矩阵、Godot MCP、Obsidian MCP 和 KB session 入口；所有写由对应 lane 执行。
11. 运行四根全链路验收、dirty-tree 差分核对和升级后 smoke。
12. 先更新 Issues、AGENTS、方向文档、caller 和 CI；归档 Talent 唯一实现和冻结 workflow。
13. 按不可变 allowlist 删除 Dynamic Workflow 活跃入口、外部 orchestrator、GUI 和旧 Claude 控制层。
14. 运行负向 caller scan、全量 CI、skill discovery、四根 smoke 和回滚演练。

### 8.2 回滚

- Import 前备份 `~/.codex/config.toml`、hooks、rules、plugins、skills、agents、sessions 索引和 asset
  manifest。状态数据库必须使用 SQLite backup API，或在 Codex 完全退出后连同 WAL/SHM 做一致性快照；
  schema/计数不是备份。
- 回滚分三层并分别验收：repo/config 文件可逐 commit/备份恢复；本地 Codex state、sessions、
  rollouts、attachments、memories 和 vendor-import 文件可由一致快照恢复；ChatGPT 云端导入对象
  先做 UI 能力 preflight，只有界面明确支持的对象才承诺逐项人工删除。官方没有公开 batch undo，
  或某类对象没有删除入口时，第三层明确标为不可逆并在 Import 前取得用户接受，不能用本地 SQLite
  恢复代表云端已撤销。
- 记录 Import batch ID、逐项 successes/failures 和所有新建路径；在隔离副本上演练前两层恢复后才继续。
- 不删除 Claude 源，直到导入验收和回滚演练完成。
- C 盘清理优先移至 D 盘隔离；每项按 manifest 恢复到“原路径不存在”的位置并复核哈希。
- 每个仓库里程碑只提交本阶段文件，保留可独立 revert 的 commit。

### 8.3 完成判据

- 68 个聊天、378 个 memory 文档和 3 个辅助资产全部在 manifest 中有最终去向；Import batch 无未裁决失败。
- Mercury＋SoT instructions、skills 和 agents 都有“导入/原生替换/冻结/删除”裁决。
- Superpowers 14 个 skills 在模型可见范围中且无同名冲突。
- 原生并行、peer message、follow-up、handoff 和不同 effort dual-verify 全部通过。
- 7 个 workflow 全部达到 `REPLACED-PASS` 或 `FROZEN-ARCHIVE-PASS`，含固定夹具和失败路径证据。
- Hooks 九类源事件及每个 handler 的成功/失败矩阵通过；rules 独立有效。
- Godot MCP 与 Obsidian MCP 端到端通过，KB 文件 fallback 通过。
- 四根 dirty-tree 与 preflight 一致，既存用户改动未被覆盖或混入迁移提交。
- 设计库 API 与 snapshot 一致，SoT 单写矩阵有效，字段权威 canonical 文档无互斥表述。
- Dynamic Workflow、`packages/codex-orchestrator`、Claude/tmux wrapper 和 Mercury GUI 不再属于活跃树。
- 活跃 harness 不依赖 Claude binary、OMC、Anthropic key 或外部 orchestrator。
- C 盘 free bytes `>= 20 * 2^30`，目标 `>= 22 * 2^30`；所有清理项有释放量与回滚记录。
- CI 通过，#571、#427、#478、#496 状态与最终架构一致。

## 9. 设计裁决

本设计选择“领域完整导入”：官方 Import 完整采集 Mercury＋SoT 相关历史，最终启用层只保留
Codex 官方原生能力。完整性由资产对账证明，纯净度由最终依赖与活跃入口证明；两者不得互相替代。
