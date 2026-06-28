---
title: Mercury 做减法 — 环境/记忆/KB 瘦身审计(决策就绪报告）
issue: 517
lane: side-bug
date: 2026-06-27
status: 审计快照 2026-06-27（只读）→ GATE 已通过 2026-06-28（用户批准 3 项）→ 已执行（批 A MEM-8/ORC-1 + git pull；批 B kb-lint 归档/文档对齐 PR #519；mem0 退役 #518）；DIRECTION.md/#384 ADR 标注交 main
method: /effort ultracode Workflow `mercury-slim-audit`（5 域 fan-out + 逐候选对抗式验证；37 agent / 2.74M token / 22 min）
---

# Mercury 做减法瘦身审计 — 决策就绪报告（#517）

> **GATE 提醒（审计时点）**：本报告正文是 2026-06-27 审计快照，是**提案**而非执行记录——审计本身只读（在 `develop` 主 checkout，零 commit）。
>
> **执行后续（2026-06-28，GATE 已通过）**：用户批准 3 项后已执行——批 A（MEM-8/ORC-1 + `git pull` 同步）+ 批 B（kb-lint 归档 + Mercury_KB/mem0 文档对齐，PR #519）+ mem0 退役（#518，计划任务待用户提权）。执行记录见 #517/#518/#519 评论。下文决策表为审计时点判断,保留作历史。

## 0. 一句话结论（先看这个）

**Mercury 的环境其实已经相当精简，不是「长期做加法堆出一堆肥肉」**。两次最大的减法（#512 Telegram 子系统全剥 + #514 MEMORY.md 111KB→23KB）已经吃掉了高杠杆收益。对抗式验证把 48 个候选里的 **17 个翻转**（几乎全偏保守），最终：

| 裁决 | 数量 | 含义 |
|---|---|---|
| **GO-CUT** | 10 | 可剥离，但其中 3 个其实是「上游已删、本地 stale」=git pull，1 个 user-level；真正「side-bug 开 PR 删」的干净项只有 **3 个**（约 30KB + 1 死 skill + 1 gitignored 残留） |
| **DEFER** | 14 | 需先满足前置条件 / 属 main 或 user 治理 / 待 soak |
| **KEEP** | 24 | 验证为活跃或不可逆损失 > 收益 |

**真正的省 context 杠杆几乎为零**：每会话 ~75KB 的 auto-load 地板里，能砍的大头要么已被 #514 处理（MEMORY.md），要么属 main（CLAUDE.md carve-out）/ user-level（全局 CLAUDE.md 翻译表），side-bug 在 repo 层能独立拿下的 context 节省 ≈ 0。**减法的真实价值在「磁盘卫生 + 死配置清理 + 把 2 个隐藏问题摆上台面让用户决策」，而非 context 提效。**

## 1. 三个最重要的纠正（对抗式验证救回的误判）

这是本次审计最有价值的部分 —— 我会话开始时凭直觉认的「线索」被对抗验证逐一推翻：

### 纠正 ① 「#512 死代码」根本不是死代码，是本地 checkout 落后 3 commit
- 我开局发现 `adapters/mercury-channel-client` + `mercury-channel-router` 仍在、`.mcp.json` 仍注册 `mercury-telegram`，判为「#512 漏删的高置信死代码」。
- **实测推翻**：#512(commit `9c063c2`/PR#513)**早已合并进 `origin/develop`**（`git merge-base --is-ancestor 9c063c2 origin/develop`=YES；`origin/develop` 版 `.mcp.json` 无 `mercury-telegram`，`adapters/` 仅剩 4 个活适配器）。本地工作树 `develop` HEAD=`105163f`(#507)**落后 `origin/develop`=`4cedfaf`(#515) 共 3 commit**。
- **正解 = `git pull`（快进同步），绝不开删除 PR**——开删除 PR 会与已合并的 #513 冲突。
- 涉及候选：`context/CTX-1`、`周边基建/INF-1`、`周边基建/INF-2`。

### 纠正 ② 记忆层「磁盘垃圾」并非无依赖，且 memory 目录非 git-tracked（删=不可逆）
- 我以为 `INDEX.generated.md`(117KB) + `.pre-cutover.bak` + 56 孤儿是无依赖磁盘垃圾。
- **实测推翻**：
  - `INDEX.generated.md` 是 F.C validator hook 的 `--format diff` 漂移基线（`regenerate-memory-index.sh:697`）——hook 当前未注册（休眠），但删基线会让它将来注册时首跑就误判 drift。
  - 2 个 `.pre-cutover.bak` 是 `regenerate-memory-index.sh:641-647` + 运维指南 Channel 2 回滚通道**文档化的活回滚物**（永不覆写设计意图）。
  - 56 个 `project_sessionNN_state.md` 孤儿是各 session 的**唯一全量详档**（131 行 vs `sessions/SNN.md` 24 行 stub），其中 9 个仍被 `SESSION_INDEX.md` 活指针直接指向。
  - `memory/` 目录**非 git-tracked**（`git rev-parse` → fatal）→ 删即永失，无回溯。
- **结果翻转**：`记忆层/MEM-1`、`MEM-2` GO-CUT→DEFER；`MEM-4` DEFER→KEEP。正解是「先退役回滚通道 / 先 merge 详档进 stub」再删，而非盲删。

### 纠正 ③ mem0 是「空仓黑洞」——从未存过一条记忆，却每会话空跑
- **实测**：Qdrant `points` 表 = **0 行**，`history.db` = **0 行**，皆 12288 字节空 sqlite 默认页，自 `2026-04-18` 初始化后从未写入。**根因**：`OPENAI_API_KEY` UNSET → `mem0_bridge.py` fail-safe 静默 no-op。
- mem0 在本机**从未沉淀任何语义记忆**，召回功能 `recall()` 全仓零调用方 —— 但它每会话仍空跑 3 个 hook + 维护 370 LOC，外加一个**今天刚成功跑过、明天还要跑的每日 NAS 镜像计划任务** `ClaudeMem0NasSync`。
- #384 ADR「keep + monitor」当时**没有这份空仓证据**。
- **判定 DEFER + 升级给用户**：这不是机械 cut（有活耦合：`flush.py` ingest / NAS-sync 任务 / CLAUDE.md 验证 runbook），而是一个**需用户决策**：要么接上 API key 兑现 #384 ADR 假设的召回价值，要么正式退役整栈。涉及 `记忆层/MEM-10`、`KB/KB-4`。

**附带文档纠正**：`KB/KB-5` —— **Mercury_KB 是活 KB**（8.1M / 223 文件 / `handoff/` 今天还在写），但 `CLAUDE.md` L59 + `DIRECTION.md` L119 写「已归档/已废」=STALE。→ KEEP KB，**改文档**（非 cut）。

## 2. GO-CUT 决策表（10 项 —— 真正可剥离）

> 列：候选 | 实测开销 | 可逆 | 谁能动 | 动作 / 前置条件

| ID（域） | 物 | 开销 | safe | 可逆 | 谁/动作 |
|---|---|---|---|---|---|
| **记忆层/MEM-8** | 已 ARCHIVED 的 `session-handoff-side-multi-lane.md` | 12.7KB（非 auto-load） | ✅True | moderate | **side-bug 可执行**。删文件 + 同步移除 `MEMORY.md:47` index bullet（避免悬挂指针）。lane 已显式关闭，内容已沉淀进 `feedback_lane_protocol.md` v1 + `sessions/S*-side-multi-lane.md` |
| **编排面/ORC-1** | `.claude/workflows/.omc/` 失效 OMC runtime 残留（含过期重复 ecc-scan.js 副本） | ~16.5KB（gitignored，不进 context） | ✅True | trivial | **side-bug 可执行**。纯本地 housekeeping，零 repo / git 影响。删前加一道「无 OMC session 正占用此 cwd」检查 |
| **KB/KB-2 == 编排面/ORC-3** | `kb-lint` skill（`.claude/skills/kb-lint/`）—— 硬依赖 unset 的 `$AGENTKB_DIR`，一启动即 abort | skill 描述进每会话注册表 1 条目（~178B） | ✅True | trivial | **side-bug 可执行**。**archive**（移 `archive/skills/`）非硬删，保可逆；同批改 `README.md` skill 表 + onboarding bullet。与 AgentKB 收尾同批 |
| **KB/KB-6** | obsidian MCP 注册（settings.json allow + server） | 末次用 2026-02-27（~4 月 dormant），Mercury_KB 现走直接 FS 访问 | ✅True | easy | **user-level**（~/.claude 治理，非 Mercury PR）。剥离零功能风险；同改 3 处陈旧 doc 引用 |
| **context/CTX-1** | 本地 `.mcp.json` 仍注册死服务 `mercury-telegram` | 每会话尝试 spawn 4332B node `channel.cjs` | ✅True | trivial | **= git pull**（见纠正①）。上游已删，本地 stale。**勿开删除 PR** |
| **周边基建/INF-1** | #512 残留 channel/router/notify 适配器（3615 LOC） | 纯磁盘 | ✅True | trivial | **= git pull**（上游已删，本地落后 3 commit）。**勿开删除 PR**（会与 #513 冲突） |
| **周边基建/INF-2** | #512 残留脚本 `notify-event*.sh`+`lane-auto-report*.sh`（705 LOC） | 纯磁盘 | ✅True | trivial | **= git pull**（同 INF-1） |
| **context/INF-1**（异名同域碰撞） | `session-start.py` 的 knowledge-index 注入死半段（`~/.claude/knowledge/` 已不存在） | 每会话 read 落 except 发 fallback 串 | ✅True | easy | **user-level hook**。外科式只删 line 26/28/59-63，**保留 daily-log 半段**（误删整 hook=切断活注入）。低杠杆 |
| **周边基建/INF-3** | `scripts/migrate-session-index-to-files.sh`(285 LOC) 一次性 migration | 纯磁盘，0 runtime caller | ⚠False | trivial | **belongs-to-main 边缘**（lane-protocol memory infra）。剥离须同步删 `memory-index-regenerate.md` L180-189 文档引用（活指南前置步骤2） |

> **GO-CUT 现实**：10 项里，3 项（CTX-1/INF-1/INF-2）是「git pull 同步」非删除；1 项（KB-6）user-level；1 项（context/INF-1）user-level hook；1 项（INF-3）belongs-to-main 边缘。**纯 side-bug 在 repo/记忆层能独立干净拿下的只有 3 项：`MEM-8` + `ORC-1` + `KB-2/ORC-3`（kb-lint）**——合计约 30KB 磁盘 + 1 死 skill + 1 gitignored 残留。

## 3. DEFER 决策表（14 项 —— 有前置条件 / 属 main 或 user 治理 / 待 soak）

| ID（域） | 物 | 为何 DEFER（前置条件） | 谁 |
|---|---|---|---|
| 记忆层/MEM-1 | `INDEX.generated.md`(117KB) | 先退役 F.C validator diff-基线通道，否则 hook 注册后首跑误判 drift | side-bug（待前置） |
| 记忆层/MEM-2 | 2×`.pre-cutover.bak`(52KB) | 先在脚本+指南正式退役 F.B Channel 2 回滚通道；memory 非 git 不可逆 | side-bug（待前置） |
| 记忆层/MEM-3 | 今日 3 个 MEMORY.md 压缩备份(221KB) | `project_memory_md_compaction_fd:16`「确认无误后可删」soak 未过（今日刚建）；1-2 周后转 GO-CUT | side-bug（待 soak） |
| 记忆层/MEM-5 | LANES.md stale per-session 段(88KB) | `#496` LANES 自动维护由 **main** 认领；Rule 6 禁本 lane 改他 lane 段 | belongs-to-main |
| 记忆层/MEM-10 + KB/KB-4 | **mem0 空仓栈**(370 LOC + Qdrant) | **需用户决策**：接 API key 兑现召回 / 退役整栈（含 NAS-sync 任务 + flush.py ingest）。新空仓证据回填 #384 ADR | user-level + 升级 |
| KB/KB-1 | AgentKB 在 CLAUDE.md 的 archival-pending 行 + 4 处引用 | 可折 L58 表行为墓碑，但须 carve-out 保留 L66/L69/L80（`AGENTKB_MEM0_DISABLED` 是**活** kill-switch 文档） | side-bug（需精界定） |
| KB/KB-3 | `scripts/mem0_migrate.py`(153 LOC) 一次性迁移 | `mem0_unit_test.py:16` import 它（含 6 个 frontmatter 用例）；须同改测试。与 KB-1/KB-2 同批 | side-bug（待 co-edit） |
| context/MEM-1 | CLAUDE.md §Cherry-pick + Carve-out（占全文 43.7%） | **belongs-to-main**（shared-spec）。建议 main 起 Issue 迁 Category A/B 进 guide 留薄指针；但 mercury-gui 是活消费者，迁移须保 DISAGREE-cite 可用性 | belongs-to-main |
| context/CTX-2 | user-level `claude_ai_*` MCP（8 deferred 工具） | Mercury 0 消费者但属用户全局通用工具；无项目级开关；剥离波及用户邮件/日历工作流 | user-level |
| context/MEM-4 | OMC `.omc/project-memory.json`(14KB) 双 memory 层 | 与 MEMORY.md 冗余度未实测；删文件徒劳（hook 每会话重建）。需另起 de-dup 审计 | user-level（待审计） |
| 周边基建/INF-5 | 未安装的 `mercury-memory-index-{validator,write-guard}.py`(14KB) | 捆绑 INF-4 退役决策；当前 inert 反而对（防误 deny 必要手改）；#516 path-hardening OPEN | belongs-to-main |
| 编排面/ORC-4 | `gh-project-flow` skill | 自声明「BOOTSTRAP-ONLY: Phase 3 退役」条件已 fired（Project #3 看板已陈旧，最大 #248 vs repo #517）；但 dev-pipeline 仍引用。先核实 Project #3 是否事实死 | 偏 belongs-to-main |
| KB/KB-7 | user-level memory `reference_obsidian_kb.md`（stale 描述 AgentKB 为活） | 属记忆域跨界发现；一行注解级修订（标 AgentKB archival-pending） | user-level memory |

## 4. KEEP 要点（24 项 —— 验证为活跃 / 不可删）

审计的价值也包括**确认这些该留**。核心 KEEP：

- **记忆层**：`MEMORY.md`(唯一 auto-load，已 #514 压缩到 23KB，再砍边际≈0)、`SESSION_INDEX.md`(F.D 权威历史，非 auto-load 是正确设计)、`MEM-9` health handoff(lane 未显式关闭，用户隐私 lane，须用户先关才能退 handoff)、`MEM-11` main 归档。
- **context**：全局 CLAUDE.md 翻译表(`MEM-2`，用户亲定「全局问题」执行机制，不可迁)、全局 MEMORY.md(`MEM-3`，已 #514 优化)、CLAUDE.local.md(caveman，用户选)、AGENTS.md(codex 唯一入口)、`mercury-orchestrator` MCP(`ORC-1`，生产编排核心，未运行≠死，可考虑 lazy 注册=main 决策)。
- **周边基建**：`INF-4` regen 引擎(LIVE 文件唯一维护工具，validator/write-guard 子层可独立退役但属 main)、`cost_tracker`(`INF-6`，#361 双消费活跃)、`mercury-gui`(`INF-7`，Phase 6 交付物，停滞 33 天但非废=main 战略)、loop-detector / test-gate / gpt-image-2 / playwright(活适配器)。
- **编排面**：dev-pipeline 6 agent 核心链(CLAUDE.md MUST)、3 个通用 Workflow 模板(`ORC-6`，#478 P0 投资，本审计正用 audit 模式)、`ecc-practice-scan`(已触发有产出)、`talent-validate`(SoT 5 真跑，belongs-to-main，**警示：untracked 未 commit 有丢失风险**)、`game-*` 三件套(`ORC-2`，SoT game-dev 链，belongs-to-main)、`multi-source-research`(`ORC-5`，#478 deliberate 投资，probation-KEEP)。

## 5. 额外 toil 发现（非 cut，建议各立 follow-up Issue）

1. **regen 机制 dormant 根因**：`sessions/S112.md:3` 的超长单行 YAML `description`（含反引号+冒号+特殊字符）疑似炸 parser → `regenerate-memory-index.sh --in-place` 对真实数据 exit 1 → 整套 F.D 自动维护退化为手改，validator SessionEnd 钩子长期 warn。**修复 = 净化 S112.md(及同类) frontmatter**，是独立 infra-fix（belongs-to-main memory infra），能让 F.D 自动化复活。
2. **mem0 空仓**：`OPENAI_API_KEY` unset → 2+ 月从未存记忆（见纠正③）。需用户决策接 key / 退役（user-level 治理，类 #259 记录命令清单+diff+验证）。
3. **talent-validate.js untracked**：`.claude/workflows/talent-validate.js` 是 SoT 5 真跑的活模板但**未 commit**（working-tree-only），有丢失风险 → 提醒 main lane commit。

## 6. Post-GATE 执行批次建议（每批小、可回滚、各自 Issue→分支→PR→/dual-verify，从 side-bug worktree）

> 仅供用户拍板后参考；**未经逐条批准不执行**。

- **批 A（side-bug，最低风险，多为非-repo）**：`MEM-8`（删 side-multi-lane handoff + MEMORY.md bullet，memory 非 repo）+ `ORC-1`（删 `.omc/` 残留，gitignored）。基本不需 PR（不在 Mercury repo / gitignored），属本地 housekeeping。
- **批 B（side-bug，一个 repo PR：AgentKB 收尾）**：`KB-2/ORC-3`（archive kb-lint skill + README 表）+ `KB-1`（CLAUDE.md L58 折墓碑，carve-out 保留 L66/L69/L80 活 kill-switch 文档）+ `KB-3`（删 mem0_migrate.py + 同改 mem0_unit_test.py）。一个内聚 PR。
- **同步动作（用户在 main worktree 执行）**：`git pull` 同步本地 develop→origin/develop（消除 CTX-1/INF-1/INF-2 stale 残留）。注意：拉入 #508/#514 等 3 commit，FF 干净无丢失。
- **用户决策（不是机械 cut）**：mem0 空仓（接 key / 退役）；Mercury_KB 文档对齐（`KB-5` side-bug 改 CLAUDE.md L59 + 请 main 改 DIRECTION.md L119）；CLAUDE.md carve-out 瘦身（main 起 Issue）；gh-project-flow sunset（先核实 Project #3 死活）。
- **follow-up Issue**：S112.md frontmatter 净化（复活 regen）；mem0 空仓量化（user-level 治理）。
- **待 soak**：`MEM-3` 今日 3 备份（1-2 周后 GO-CUT）。

## 7. 方法论与可信度

- Workflow `mercury-slim-audit`：5 域并行候选发现（每域实测开销 + 品质影响 + 可逆性 + 依赖 grep）→ 逐**非-KEEP** 候选派对抗式 skeptic（指令「尽力反驳剥离判断、找现存消费者、默认 removal_safe=false」）。
- 对抗式验证**触发 17 处翻转**（GO-CUT→DEFER/KEEP 11 处 + DEFER→GO-CUT/KEEP 6 处），证明纯直觉减法会误删/漏判——GATE + 对抗验证是必要的。
- 已声明的未深挖项（禁静默截断）：49 个 .sh 中 ~9000 LOC 的 lane-*/codex-*/intel-*/push-guard 生产脚本族默认 KEEP 未逐一验活；OMC plugin ~55 个 deferred 工具名精确 token 成本未逐条量化（user-level）；project-memory.json↔MEMORY.md 内容级重复度未 diff；AgentKB 本体目录内部未审计（超 Mercury repo 范围）。
- 跨域 ID 碰撞已用域名命名空间消歧（context 域复用了 MEM-*/INF-*/ORC-* 前缀）。
- 外部事实（mem0 空仓 / OPENAI_API_KEY unset / Qdrant 0 行 / #512 已合并 origin/develop / obsidian MCP 末用日期）均为 agent 实测命令输出，非训练数据推断。
