# Mercury 项目方向定义

> 状态: **生效中** | 制定日期: 2026-04-06 | 决策者: 392fyc
> 本文档是 Mercury 所有开发工作的最高准则。任何与本文档冲突的历史文档以本文档为准。
> 取代: `architecture-evolution-plan.md` (旧路线图)

---

## 一、项目定位

### Mercury 是什么

Mercury 是一套**让 AI Agent 能持续、自主、高质量地工作的 harness 框架**。

它不是 Agent 的"管理器"（Claude Code 原生做得更好），而是解决 Claude Code 原生**做不到**的问题：
- Session 耗尽后的自动延续
- 跨 session、跨项目的长期记忆
- 长时间自主工作时的质量保障
- 关键节点的人类最小干预通知

### Mercury 不是什么

- 不是 orchestrator — 不再自建编排层管理 agent "做什么"
- 不是 Agent CLI wrapper — 不再包装 Claude Code / Codex 的 CLI（注: Session Continuity 模块可能使用 CLI 监控作为实现手段，但 Mercury 本身不是 wrapper 产品）
- 不是桌面应用 — GUI 仅为多 session 并行管理面板，不是核心
- 不是封闭系统 — 每个模块可独立拆卸，可嵌入任何项目

### 最终目标

1. **实用工具**: 为个人全周期软件开发（含 SoT 游戏项目）提供最大 AI 辅助效率
2. **可分享框架**: 开源分发，方法论文档 + 可安装工具集，供其他开发者使用
3. **轻量可维护**: 本体尽可能薄，重功能通过外部项目挂载，维护成本可控

---

## 二、架构原则

### P1: 轻量本体 + 外部挂载

Mercury 本体只做外部项目做不到的事。能挂载的绝不自研。

```
Mercury 本体 (自研，最小化)
├── session-continuity/    # 外部项目无此能力
├── memory-layer/          # 外部项目无此能力
├── notify-hub/            # 外部项目无此能力
├── adapters/              # 外部项目接口转换（唯一耦合点）
└── dev-pipeline/          # 预设开发组编排

外部挂载 (git submodule，独立版本管理，挂载到 modules/ 目录)
├── modules/gsd/           # 质量门禁 + 卡死检测
├── modules/superpowers/   # 自检清单 + review 机制
├── modules/ohmyclaudecode/# Stop hook 拦截
├── modules/openspace/     # 技能自进化
└── modules/...            # 未来可追加
```

### P2: 适配层吸收差异

外部项目更新时，Mercury 本体不改。差异由 `adapters/` 吸收。
每个 adapter 是薄转换层，不包含业务逻辑。

### P3: 模块可拆卸

任何 skill、hook、agent 定义都可以从 Mercury 中取出，独立用于其他项目。
模块间无隐式依赖。如果 A 需要 B，必须在 A 的文档中显式声明。

### P4: 方法论先于工具

Mercury 的核心价值不在代码里，在方法论里。
每个模块都有配套的"为什么这样做"文档，这是开源分享和视频内容的素材来源。

### P5: 向上兼容

架构设计必须确保：模型能力变强 → 模块自然受益（而非失效）。
避免任何"假设模型做不到 X"的设计决策。

---

## 三、核心模块定义

### 模块 1: Session Continuity（session 连续性）

**解决的问题**: Claude Code session context 耗尽后需要人类手动 resume/continue，无法长时间自主工作。

**职责**:
- 监控 session context 使用量
- 接近耗尽时自动生成 handoff 状态（当前任务进度、关键上下文、下一步计划）
- 自动启动新 session 并注入 handoff 状态
- 检测 session 卡死（同一操作循环、超时）并升级处理

**技术方向** (需调研确定):
- Agent SDK: ClaudeSDKClient 支持 session resume/fork，自动 context compaction，可通过 max_budget_usd 控制花费
- PostCompact hook: 在 context 压缩后触发，可获取 compact_summary，用于保存关键状态
- CLI wrapper: 监控 claude -p 进程，检测完成/超时后启动新 session 并传递 handoff
- Dispatch + Channels: 利用原生异步任务机制实现链式执行

**自研理由**: 外部项目均未解决此问题，这是 Claude Code 生态的共同痛点。

---

### 模块 2: Memory Layer（长期记忆层）

**解决的问题**: Claude Code 内置 memory 容量有限、不可结构化查询、不跨项目。

**职责**:
- NAS 上的 Obsidian vault 作为中心化知识库
- 遵循 Karpathy 模式: raw data → LLM compile → structured wiki → LLM Q&A → incrementally enhance
- 跨项目共享: 不绑定 Mercury，任何项目均可接入
- LLM 自维护: agent 负责写入和维护 wiki，人类只读/查询
- 周期健康检查: LLM lint 检测不一致、缺失、过时内容

**技术方向**:
- MCP server 提供读写接口（需评估当前可用的 Obsidian MCP server 方案）
- NAS SSH 直接访问底层文件
- 周期维护 Agent (Issue #92) 作为 health check skill

**自研理由**: 知识库结构、领域模板、编译流程是 Mercury 特有的。底层存储通过 MCP 生态解决。

---

### 模块 3: Notify Hub（通知层）

**解决的问题**: agent 长时间自主工作时，人类无法得知关键节点，也无法远程确认/干预。

**职责**:
- 在关键节点（commit、PR、需要决策、异常）通知用户
- 支持远程确认（用户通过 IM 回复即可）
- 统一通知出口: 不关心底层是 Telegram、LINE 还是 Channel

**技术方向**:
- Claude Code Channels (原生双向通信)
- IM Bot Bridge (Issue #91)
- Argus review bot (PR 审查通知)

**自研理由**: 通知路由和统一出口是 Mercury 特有需求。底层 IM 集成可用社区方案。

---

### 模块 4: Quality Gate（质量门禁）

**解决的问题**: agent 早期退出、自我确认、循环卡死等导致成果物质量不可靠。

**职责**:
- Stop hook 拦截: agent 尝试 stop 时强制检查 completion checklist
- 机械化退出条件: test pass / lint clean / file exists 等客观标准，不允许主观判断"完成"
- 卡死检测: sliding window 检测循环 + 多级超时
- Acceptance sub-agent 独立验证: 做和验分离

**技术方向**: 主要通过外部项目挂载实现
- GSD (gsd-build/gsd-2): deriveState 状态机 + sliding window 卡死检测
- Superpowers (obra/superpowers): inline checklist + TDD red-green-refactor（已进入 Anthropic marketplace）
- OMC (Yeachan-Heo/oh-my-claudecode): Ralph mode stop hook 拦截
- OpenSpace (HKUDS/OpenSpace): 技能自进化 + proof-gates

**Mercury 自研部分**: adapters 将外部机制统一为 Mercury 的 hook/skill 接口。

---

### 模块 5: Dev Pipeline Preset（开发作业工作组）

**解决的问题**: 每次开发任务都需要手动配置角色分工和执行流程。

**职责**:
- 预设开发工作组: Main → Dev sub-agent → Acceptance sub-agent 线性链
- 使用 Claude Code 原生 sub-agent 机制（非独立 session，节省 token）
- 单任务内线性执行
- 多任务通过多 session 并行

**技术方向**:
- .claude/agents/ sub-agent 定义 (从 Mercury role YAML 转换)
- Dispatch prompt 模板 (.mercury/templates/)
- Session 并行由 GUI 或 CLI 管理

**自研理由**: 角色定义、dispatch 模板、流水线编排是 Mercury 方法论的核心体现。

---

### 模块 6: Detachable Skills（可拆卸技能模块）

**解决的问题**: 通用开发能力增强需要可复用、可分发。

**当前 skills（按优先级）**:
1. **pr-flow** — PR 全流程自动化（创建、审查、修复、merge）— 最高优先
2. **autoresearch** — 自主研究协议（多轮搜索 + 验证门禁）
3. **dual-verify** — 双重验证门禁（Claude Code + Codex 并行审查）
4. **web-research** — Web 研究强制协议
5. **periodic-maintenance** (#92) — 周期性自治维护 Agent
6. **ext-tracker** (#157) — 外部信息变更自动追踪

**每个 skill 必须**:
- 独立可用（不依赖其他 Mercury 模块）
- 有配套方法论文档
- 可作为 Claude Code skill 独立分发

---

### GUI（多 session 并行管理面板）

**定位**: 非核心模块。为多 session 并行开发提供可视化管理。

**职责**:
- Session 列表 + 状态总览
- 快速介入 / 查看结果
- 启动/停止 session

**不做**:
- 不观测 sub-agent 内部行为
- 不作为 agent 的控制面板
- 不包含业务逻辑

**技术方向**: 暂缓开发。优先用 CLI + IM bot 覆盖通知需求，GUI 在核心模块稳定后按需启动。

---

## 四、外部项目挂载策略

### 挂载方式: Git Submodule

每个外部项目作为 git submodule 挂载到 modules/ 目录下。

### 版本管理

- 每个 submodule pin 到稳定 commit 或 tag
- 不自动追踪 upstream 最新
- 升级时在独立分支测试，确认适配层兼容后合并

### 适配层规范

```
adapters/
  {project-name}/
    README.md           # 说明: 挂载了什么、为什么、适配了什么
    adapter.ts 或 .py   # 接口转换代码
    UPSTREAM.md         # 上游版本记录、已知不兼容项
```

适配层只做接口转换，不包含业务逻辑。如果适配层超过 200 行，说明耦合过深，需要重新评估挂载方式。

### 候选挂载项目评估标准

在决定是否挂载一个外部项目前，必须评估:
1. **社区活跃度**: stars、commit 频率、issue 响应
2. **接口稳定性**: 是否有明确的 API 或 skill 接口
3. **可剥离性**: 能否只取需要的部分，而非全量引入
4. **维护者信誉**: 是否有持续维护的迹象
5. **替代成本**: 自研 vs 挂载的开发量对比

---

## 五、现有代码处理

归档目标目录: `archive/` (repo 根目录下)

### 归档（不再投入开发，移入 archive/）

| 组件 | 理由 |
|---|---|
| packages/orchestrator/ | 被 Session Continuity + Dev Pipeline 替代 |
| packages/gui/ | 暂缓，未来按需重启（Phase 6 可能从 archive/ 取出复用） |
| packages/sdk-adapters/ | 被原生方案替代 |
| packages/poc/ | 早期概念验证，已完成使命 |
| .mercury/docs/codex-main-agent-roadmap.md | 方向已变 |
| .mercury/docs/architecture-evolution-plan.md | 被本文档取代 |

### 核心保留 + 增强

| 组件 | 处理 |
|---|---|
| .claude/skills/ | 核心保留，pr-flow 优先增强 |
| .claude/hooks/ | 保留，迁移为可移植配置格式 |
| .mercury/templates/ | 核心保留，dispatch 模板是核心资产 |
| packages/core/ | 评估后部分复用类型定义 |

### 转换

| 组件 | 目标格式 |
|---|---|
| .mercury/roles/*.yaml | 转换为 .claude/agents/*.md (Claude Code 原生 sub-agent 定义) |

---

## 六、Open Issues 处理

### 保留（符合新方向）

| Issue | 归属模块 | 说明 |
|---|---|---|
| #158 架构方向评估 | — | 本文档即为其产出，Phase 0 中关闭 |
| #92 周期维护 Agent | Memory Layer | 作为 KB health check skill |
| #157 外部信息追踪 | Skills | 作为独立 skill |
| #91 IM Bot Bridge | Notify Hub | 通知层实现方式 |
| #86 PR Monitor | Skills | 合并入 pr-flow skill |
| #107 Codex 追踪 | Quality Gate | 评估是否可通过挂载项目解决 |

### 重新定义

| Issue | 处理 |
|---|---|
| #101 harness roadmap | 按本文档方向重写 |
| #155 多团队并行 | 降级: Agent Teams 是原生能力，Mercury 仅做 session 并行管理 |
| #154 Web 自动化 | 评估: 是否作为独立 skill 或通过 MCP 浏览器工具解决 |
| #141 OpenSpace 技能引擎 | 转化: 作为 submodule 挂载而非自研 |

### 关闭（不再适用）

| Issue | 理由 |
|---|---|
| #72 Conference Mode | 方向已变，不再自建多 agent 实时协作 |
| #63 Agent 群体记忆 | 被 Memory Layer (NAS KB) 替代 |
| #61 CLI-Anything | 范围过大，不符合轻量本体原则 |
| #57 Dashboard 分析 | GUI 暂缓 |
| #54 EventLog 加强 | orchestrator 归档 |
| #50 autoSelectAgent 优化 | orchestrator 归档 |
| #49 TaskManager 断言 | orchestrator 归档 |

---

## 七、开发准则

### 决策前必须

1. **这个功能是 Mercury 本体该做的吗？** — 如果外部项目能做，不自研
2. **这个模块能独立拆出来用吗？** — 如果不能，说明耦合过深
3. **模型变强后这个设计还成立吗？** — 如果不成立，说明假设了模型的弱点

### 开发流程（继承并简化）

1. **Issue-first**: 所有工作必须有 GitHub Issue
2. **Sub-agent 链**: Main → Dev (sub-agent) → Acceptance (sub-agent)
3. **质量门禁**: 借助挂载项目的机制，不自研门禁逻辑
4. **PR to develop**: 所有代码通过 PR 合入 develop
5. **中文里程碑**: 里程碑完成消息使用中文

### 文档要求

每个模块必须包含:
- README.md: 是什么、为什么、怎么用
- PHILOSOPHY.md: 方法论解释（视频和文章素材）
- CHANGELOG.md: 变更记录

### 废弃的旧规则（从 CLAUDE.md 中移除）

以下旧 CLAUDE.md 规则已被本文档的新方向取代:
- "Agents First: inter-agent communication uses JSON/YAML with agentId, model, sessionId" — 不再需要自定义 agent 通信协议，使用 Claude Code 原生 sub-agent 机制
- "Role boundary enforcement: operate strictly within your assigned role" — 角色通过 sub-agent 定义天然隔离，不再需要显式规则
- "Do not bypass the SoT task flow" — SoT 10 级状态机归档，保留角色分工和模板化 dispatch 的核心理念
- "Do not hardcode any specific agent as Main Agent" — 不再有多 agent CLI 架构，Main 就是当前 Claude Code session
- "Do not make adapters depend on Obsidian/KB" — adapters 概念已重新定义为外部项目接口转换层

---

## 八、参考资源

所有外部引用已于 2026-04-06 验证可用。

- Karpathy LLM Knowledge Bases (2026-04-02) — Memory Layer 设计理念
- GSD (gsd-build/gsd-2) — Quality Gate: 状态机 + 卡死检测，活跃维护
- Superpowers (obra/superpowers) — Quality Gate: 自检清单 + TDD，已进入 Anthropic marketplace，活跃维护
- Oh My Claude Code (Yeachan-Heo/oh-my-claudecode) — Quality Gate: Stop hook 拦截，活跃维护
- OpenSpace (HKUDS/OpenSpace) — Quality Gate: 技能自进化，活跃维护
- Claude Agent SDK — Session Continuity 技术方向，官方文档
- Claude Code Hooks (PostCompact) — Session Continuity 技术方向，官方文档
- PR #162 研究报告 — Claude Code 原生能力评估，已合并
- codex-plugin-cc (openai/codex-plugin-cc) — Codex 集成方案，活跃维护
