# Mercury 执行规划

> 状态: **生效中** | 制定日期: 2026-04-06
> 依据: DIRECTION.md (项目方向定义)
> 本文档定义从当前状态到新架构的执行路径。

---

## 自举路径概览

Mercury 的开发本身就是一个自举过程：每个 Phase 完成后解锁新的开发能力，供后续 Phase 使用。

在 Phase 0-3 期间，我们本质上是**一个 Claude Code CLI 挂载在 Mercury 目录下工作**。
能利用的只有 Claude Code 原生能力：agents/*.md、skills、hooks、sub-agents。
直到核心模块完成后，Mercury 才能从内部驱动自身的开发。

```
Phase 0: 清理 + 搭建
  可用: Claude Code CLI 原生功能
  解锁: agent.md 定义、清洁的目录结构

Phase 1: Dev Pipeline + pr-flow
  可用: agent.md (sub-agent 链)、现有 skills/hooks
  解锁: Main→Dev→Acceptance 开发流水线、PR 全自动化

Phase 2: Quality Gate
  可用: Phase 1 的 dev pipeline + 外部项目挂载
  解锁: agent 防早退、机械化完成标准

Phase 3: Memory Layer (可与 Phase 2 并行)
  可用: Phase 1 的 dev pipeline、NAS 基础设施
  解锁: 跨 session/跨项目长期记忆

Phase 4: Session Continuity
  可用: Phase 1-3 全部能力
  解锁: agent 跨 session 自动接力 ← 里程碑: Mercury 可长时间自主工作

Phase 5: Notify Hub
  可用: Phase 1-4 全部能力
  解锁: 远程通知 + 确认 ← 里程碑: 人类可离开键盘

Phase 6: GUI
  可用: 全部模块
  解锁: 多 session 可视化管理 ← 里程碑: Mercury 完整体验
```

---

## 会话模式（Session Modes）

不同类型的工作应使用不同的 agent 配置。以下是可复用的会话模式模板：

### Mode A: 需求分析 / 方向决策

**适用场景**: 项目方向讨论、需求梳理、架构决策（如方向重定义会话）
**agent 配置**: 产品经理视角 + 技术架构师视角（由 main agent 模拟双角色）
**关键规则**: 所有关键节点必须向用户提问，不允许 agent 自行猜测
**技术研究**: 框架设计师可随时调用 /autoresearch 对重要技术问题进行研究
**产出**: 决策文档、方向文档、执行规划

### Mode B: 标准开发

**适用场景**: 功能开发、bug 修复、代码变更
**agent 配置**: Main → Dev (sub-agent) → Acceptance (sub-agent) 线性链
**关键规则**: Issue-first、dev pipeline skill 触发、质量门禁、PR 流程
**前置条件**: Phase 1 完成后可用
**产出**: 代码变更 + PR

### Mode C: 技术调研

**适用场景**: 外部项目评估、技术方案选型、可行性验证
**agent 配置**: Main + Research sub-agent（深度调研）
**关键规则**: /autoresearch 协议、web 验证强制、多源交叉验证
**产出**: 研究报告 + 决策建议

### Mode D: 外部项目挂载

**适用场景**: 评估、引入、适配外部项目
**agent 配置**: Main (评估) + Dev sub-agent (编写适配层) + Acceptance sub-agent (验证集成)
**关键规则**: 评估五标准（社区活跃度、接口稳定性、可剥离性、维护者信誉、替代成本）
**前置条件**: Phase 0 完成后可用
**产出**: git submodule + adapter + UPSTREAM.md

### Mode E: 文档 / 方法论编写

**适用场景**: PHILOSOPHY.md、视频/文章素材、用户指南
**agent 配置**: Main 单 agent，注重叙事结构和可读性
**关键规则**: 面向外部读者（非自用笔记），需要清晰的"为什么"叙事
**产出**: 可发布的文档

---

## Phase 0: 基础整理

**目标**: 清理旧架构遗留，为新方向铺路。无新功能开发。

**可用开发模式**: Claude Code CLI 原生功能（无 agent.md，无 dev pipeline）
**推荐会话模式**: 直接由 main agent 执行，无需 sub-agent 链

### 0-1. 归档旧组件
- 在 repo 根目录创建 archive/ 目录
- 将 packages/orchestrator/、packages/gui/、packages/sdk-adapters/ 移入 archive/
- 更新 .gitignore 和 workspace 配置
- 保留 packages/core/ 待评估

### 0-2. Issue 清理
- 关闭不再适用的 Issues (#158, #72, #57, #54, #50, #49)，附注"方向重定义，详见 DIRECTION.md"
- 保留 #63 (Agent 群体记忆 → Memory Layer) 和 #61 (CLI 自动化 → Session Continuity)
- 更新保留的 Issues 标签，标注归属模块
- 重新定义 #101 的 description

### 0-3. Role YAML 转换
- 将 .mercury/roles/*.yaml 转换为 .claude/agents/*.md 格式
- 保留 YAML 在 archive/ 作为参考
- 验证 sub-agent 定义可被 Claude Code 正确加载

### 0-4. CLAUDE.md 更新 (已在 Session 20 部分完成)
- 验证 Navigation 表格指向新文档
- 确保 MUST/DO NOT 反映新方向

### 0-5. 目录结构搭建
```
modules/          # git submodules (外部项目挂载，空目录 + .gitkeep)
adapters/         # 适配层
  README.md       # 适配层规范说明
```

**产出**: 干净的代码库 + agent.md 定义 + 关闭的 Issues
**人类干预点**: Issue 关闭前确认列表
**Phase 0 完成后解锁**: agent.md sub-agent 定义可用，后续 Phase 可使用 Mode B/C/D

---

## Phase 1: Dev Pipeline + pr-flow

**目标**: 让 Mercury 可以用来做一次真实的开发任务（而非开发 Mercury 自身）。

**可用开发模式**: agent.md (Phase 0 产出) + 现有 skills/hooks
**推荐会话模式**: Mode B (标准开发) 的首次验证 — 用 agent.md 定义的 sub-agent 来开发 Phase 1 自身
**注意**: 这是 dev pipeline 的第一次实战测试，预期会有调整

### 1-1. Dev Pipeline Preset
- 完成 .claude/agents/dev.md、acceptance.md 的 sub-agent 定义（Phase 0 已创建基础版）
- 创建 dev-pipeline skill: 一键触发 Main → Dev → Acceptance 链
- 编写 dispatch 模板（复用 .mercury/templates/ 现有资产）
- 在 SoT 游戏项目或其他真实项目上验证

### 1-2. pr-flow 增强
- 当前 pr-flow 已有基础，增强为全流程：
  - 创建 PR（含 Issue 引用）
  - 轮询 review bot (Argus)
  - 读取所有 review 线程
  - 自动修复 + 回复
  - 等待 approval → merge
- 确保可独立于 Mercury 其他模块使用

### 1-3. 方法论文档
- 为 Dev Pipeline 编写 PHILOSOPHY.md
- 为 pr-flow 编写 PHILOSOPHY.md
- 这是未来视频/文章的第一批素材

**产出**: 可在真实项目中使用的 dev pipeline + PR 自动化
**人类干预点**: 首次在真实项目上试用时确认效果
**验收标准**: 在非 Mercury 项目上完成一次完整的 Issue → Dev → Acceptance → PR → Merge 流程
**Phase 1 完成后解锁**: Mode B 标准开发流程完整可用

---

## Phase 2: Quality Gate 挂载

**目标**: 挂载外部项目，实现 agent 自检和防早退。

**可用开发模式**: Mode B (dev pipeline) + Mode C (技术调研) + Mode D (外部项目挂载)
**推荐会话模式**:
- Session A (Mode C): /autoresearch 深度评估四个候选项目
- Session B (Mode D): 挂载选定项目 + 编写 adapter
- Session C (Mode B): 在 dev pipeline 中集成 quality gate

### 2-1. 外部项目评估 + 选择
- 对 GSD、Superpowers、OMC、OpenSpace 四个项目做深度评估
- 使用 /autoresearch 协议进行技术调研
- 确定首批挂载对象（可能不需要全部四个）
- 评估维度: 与 Mercury 的集成难度、skill/hook 接口暴露方式、license 兼容

### 2-2. 首批 Submodule 挂载
- git submodule add 选定项目
- 编写适配层 (adapters/{project}/)
- 验证适配层不超过 200 行

### 2-3. Stop Hook 实现 ✅ 已实现 (Issue #206, 2026-04-08)

**实现路径**: Path β — 独立 Mercury adapter，与 OMC 层叠运行（Layer model）。
**适配层**: `adapters/mercury-test-gate/` (152 LOC, Node.js CJS)
**机制**: `SubagentStop` 事件触发 → 解析 test command（convention file 优先，fallback 自动检测）→ 执行 → exit code 非零则 emit `{"decision":"block"}` → dev agent 无法退出。
**验收标准**: Dev sub-agent 不能在 test 未通过时 stop（机械化，harness-level，bypass-proof up to 3 re-entries）。
**集成测试**: 由用户在 merge 后执行真实 dev pipeline run，结果记录于 Phase 2 completion ADR（DEC-4）。

- ~~基于挂载项目的机制，实现 agent stop 拦截~~ — 已完成，见 `adapters/mercury-test-gate/hook.cjs`
- ~~定义 completion checklist 格式（机械化标准）~~ — test exit code = 0 即为通过标准
- ~~在 Dev Pipeline 中集成~~ — 已通过 `.claude/settings.json` SubagentStop 注册

**产出**: 可拦截 agent 早退的质量门禁
**人类干预点**: 外部项目选择决策；首次 stop hook 误拦截时调整阈值
**验收标准**: Dev sub-agent 不能在 test 未通过时 stop
**Phase 2 完成后解锁**: Mode B 的质量保障层

---

## Phase 3: Memory Layer (NAS KB)

**目标**: 建立跨项目的长期记忆系统。

**可用开发模式**: Mode B (dev pipeline, Phase 1 产出) + Mode C (技术调研)
**推荐会话模式**:
- Session A (Mode A): KB 架构设计（需要用户确认结构）
- Session B (Mode B): MCP 接入 + skill 实现
**可与 Phase 2 并行执行**（两者共同依赖 Phase 1，但互不依赖）

### 3-1. NAS KB 架构设计
- 在 NAS 上创建 Obsidian vault 结构
- 定义 wiki 编译规则（raw → compiled）
- 设计 index 和 summary 自动维护机制

### 3-2. MCP 接入
- 评估 Obsidian MCP server 方案（或 fork/自建轻量替代）
- 配置 .mcp.json 连接 NAS vault
- 验证 agent 可读写 KB

### 3-3. 周期维护 Agent (#92)
- 实现 KB health check skill
- LLM lint: 不一致检测、缺失数据补充、过时内容标记
- 配置定期执行（Scheduled Tasks 或 Cron）

**产出**: NAS 上的可用 KB + 周期维护
**人类干预点**: KB 结构设计确认；NAS 权限配置
**验收标准**: agent 在项目 A 写入的知识，可在项目 B 中被查询到
**Phase 3 完成后解锁**: 跨 session/跨项目知识持久化；Phase 4 的 handoff 存储层

---

## Phase 4: Session Continuity

**目标**: 让 agent 可以跨 session 持续工作。

**可用开发模式**: Mode B + Quality Gate (Phase 2) + Memory Layer (Phase 3)
**推荐会话模式**:
- Session A (Mode C): 三种技术方案的实测对比
- Session B-C (Mode B): 原型实现 + 卡死检测
**注意**: 这是技术难度最高的模块，预计需要更多迭代

### 4-1. Session Continuity 基础 ✅ (S47-S48)
- ✅ ADR 完成: Option D (Hybrid) 方案选定 (PR #239)
- ✅ Agent SDK session resume/fork 能力已验证
- ✅ M0: PreCompact → auto-memory checkpoint (`flush.py`)
- ✅ M2: `session_chain` SQLite 表 + SessionEnd 自动记录
- ✅ M1: `/handoff` 全局 skill + `handoff-orchestrator.py` (Agent SDK 续接)
- 实现位置: AgentKB PR#5, Mercury #238
- 研究报告: `.mercury/docs/research/phase4-1-*`, `.research/reports/RESEARCH-OpenClaw-*`

### 4-2. Worktree-per-task + session_chain 增强
- 基于 4-1 的 session_chain 表扩展
- 强制 worktree-per-task 隔离
- 评估 OMC `project-session-manager` skill 集成

### 4-3. Compact-prevention 模式
- 接近 context 上限时主动 `/handoff`
- PreCompact/PostCompact hooks 重定位

### 4-4. 卡死检测 ✅ (S37, #226)
- ✅ sliding window 循环检测已实现 (PR #229, #231)
- 多级超时（soft → idle → hard）待实现
- 卡死后自动生成诊断报告 + 通知用户

**产出**: agent 可以自动跨 session 继续工作
**人类干预点**: 技术方案选择；首次 session 接力时确认状态传递完整性
**验收标准**: agent 在 context 耗尽后自动启动新 session 并继续之前的任务
**Phase 4 完成后解锁**: agent 可长时间自主工作 ← 核心里程碑

---

## Phase 5: Notify Hub

**目标**: 统一通知出口，支持远程确认。

**可用开发模式**: 全部 Phase 1-4 能力 + Session Continuity
**推荐会话模式**: Mode B（标准开发），agent 可以跨 session 持续开发此模块

### 5-1. 通知接口定义
- 定义标准通知格式（事件类型、severity、内容）
- 设计确认/拒绝回调机制

### 5-2. 首个通知渠道
- 优先实现 Telegram 或 LINE bot（Issue #91）
- 或利用 Claude Code Channels 原生机制
- agent commit/PR/异常时自动发通知
- 用户可回复确认或修改方向

### 5-3. 与其他模块集成
- Session Continuity: session 切换时通知
- Quality Gate: stop 被拦截时通知
- Dev Pipeline: 任务完成时通知

**产出**: 统一通知层 + 至少一个 IM 渠道
**人类干预点**: 通知渠道选择；通知频率调优（避免过度打扰）
**验收标准**: agent 在关键节点自动通知用户，用户可远程确认
**Phase 5 完成后解锁**: 人类可离开键盘，远程确认

---

## Phase 6: GUI（按需启动）

**前置条件**: Phase 1-5 核心模块稳定运行后评估是否需要。

**可用开发模式**: Mercury 完整能力栈
**推荐会话模式**: Mode A (需求分析) 确定 GUI 范围 → Mode B (标准开发) 实现

### 可能的方向
- 基于现有 Tauri + Vue 代码重构（从 archive/ 中取出）
- 或基于第三方 Claude Code GUI 二次开发
- 仅做: session 列表、状态总览、快速介入

**不在当前规划范围内，未来按需启动。**

---

## 执行节奏

| Phase | 预计 sessions | 依赖 | 推荐会话模式 |
|---|---|---|---|
| Phase 0 | 1 | 无 | 直接执行 |
| Phase 1 | 2-3 | Phase 0 | Mode B 首次验证 |
| Phase 2 | 2-3 | Phase 1 | Mode C → Mode D → Mode B |
| Phase 3 | 2-3 | Phase 1（使用 dev pipeline） | Mode A → Mode B |
| Phase 4 | 3-4 | Phase 2 + Phase 3 | Mode C → Mode B |
| Phase 5 | 2 | Phase 4 | Mode B |
| Phase 6 | TBD | Phase 1-5 | Mode A → Mode B |

### 每个 Phase 的标准流程
1. 创建 GitHub Issue 描述 Phase 目标
2. 选择会话模式
3. 技术调研（如需要，Mode C）
4. 实现（Mode B 或 Mode D）
5. 在真实项目上验证
6. 编写方法论文档（Mode E）
7. PR → develop

---

## 成功标准

Mercury 新架构成功的判断标准：

1. **可以用 Mercury 开发非 Mercury 项目** — 解决 bootstrap 悖论
2. **人类只在关键节点介入** — agent 自主完成 80%+ 的开发流程
3. **单个模块可独立安装使用** — 在新项目中 5 分钟内可引入任意 Mercury skill
4. **外部项目更新不影响 Mercury 本体** — 只需更新 adapter
5. **方法论文档可独立阅读** — 不看代码也能理解 Mercury 的理念
