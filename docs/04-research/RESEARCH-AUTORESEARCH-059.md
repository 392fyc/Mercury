# AutoResearch 长时间高精度研究能力集成调研

| 字段 | 值 |
|------|-----|
| 任务 ID | TASK-RESEARCH-059 |
| 关联 Issue | #59 |
| 执行者 | Research Agent (Session 15) |
| 日期 | 2026-03-25 |
| 状态 | 调研完成，等待人类审阅 |

---

## 1. 项目概述

### 1.1 什么是 AutoResearch

AutoResearch 是 Andrej Karpathy 于 2026 年 3 月发布的开源项目，核心理念是将 AI Agent 置入一个**自主实验循环**（autonomous experiment loop）中：Agent 修改代码 → 运行实验 → 评估指标 → 保留/回滚 → 重复。原始项目专注于单 GPU 上的 LLM 训练优化，但社区已将此模式泛化至任意可量化指标的领域。

- **GitHub**: https://github.com/karpathy/autoresearch
- **许可证**: MIT License（企业友好）
- **Star 数**: 发布 48 小时内突破 8,000（截至 2026 年 3 月）
- **语言/运行时**: Python 3.10+，依赖 PyTorch + uv 包管理器

### 1.2 核心指标

Karpathy 的实际运行结果：700 次实验、20 次真正改进、11% 速度提升（已优化代码基础上）。

> 来源: [Fortune — 'The Karpathy Loop'](https://fortune.com/2026/03/17/andrej-karpathy-loop-autonomous-ai-agents-future/)

---

## 2. 核心架构分析

### 2.1 三文件架构

| 文件 | 角色 | 可修改 |
|------|------|--------|
| `prepare.py` | 数据准备、tokenizer、评估函数（不可变基础设施） | ❌ |
| `train.py` | 模型架构、优化器、训练循环（Agent 唯一修改目标） | ✅ |
| `program.md` | Agent 指令/策略（人类编写的"研究组织代码"） | 人类修改 |

> 来源: [GitHub README](https://github.com/karpathy/autoresearch)

### 2.2 实验循环机制

```
┌─────────────────────────────────────────────────┐
│  1. 读取 program.md + results.tsv + git history │
│  2. 形成假设，决定下一步修改                      │
│  3. 修改 train.py（一次原子性变更）               │
│  4. Git commit（保存快照，支持回滚）              │
│  5. 运行训练（固定 5 分钟时间预算）               │
│  6. 解析 val_bpb 指标                            │
│  7. 指标改善 → 保留 commit                       │
│     指标下降 → git reset 回滚                     │
│  8. 记录到 results.tsv → 回到步骤 1              │
└─────────────────────────────────────────────────┘
```

**关键设计原则**：
- **固定时间预算**：每次实验 300 秒（~12 实验/小时，~100 实验/过夜）
- **单一指标优化**：val_bpb（越低越好），消除主观判断
- **Git 即记忆**：完整的实验历史通过 git log 保存
- **永不停止**：`program.md` 明确指令 "do NOT pause to ask the human"

### 2.3 依赖清单

| 依赖 | 版本 | 用途 |
|------|------|------|
| torch | ==2.9.1 (pinned) | 训练框架 |
| numpy | >=2.2.6 | 数值计算 |
| matplotlib | >=3.10.8 | 可视化 |
| pandas | >=2.3.3 | 数据处理 |
| tiktoken | >=0.11.0 | Tokenizer |
| rustbpe | >=0.1.0 | BPE tokenizer |
| kernels | >=0.11.7 | GPU kernels |
| requests | >=2.32.0 | HTTP |
| pyarrow | >=21.0.0 | 数据格式 |

> 来源: [pyproject.toml](https://github.com/karpathy/autoresearch/blob/master/pyproject.toml)

---

## 3. 社区泛化：从 ML 到通用自主循环

原始 autoresearch 专注于 ML 训练优化，但社区已将核心模式提取为通用 Agent 技能：

### 3.1 Claude Code 技能变体

#### (a) uditgoenka/autoresearch — 通用自主优化引擎

- **架构**: Claude Code plugin（SKILL.md + 8 个子命令）
- **8 阶段循环**: Review → Select → Modify → Commit → Verify → Evaluate → Auto-revert → Log
- **泛化能力**: 适用于任何有可量化指标的领域（测试覆盖率、bundle 大小、延迟、安全漏洞数等）
- **子命令**: `/autoresearch:plan`、`:security`、`:ship`、`:debug`、`:fix`、`:scenario`、`:predict`、`:learn`
- **结果追踪**: TSV 格式（iteration | commit | metric | delta | status | description）

> 来源: https://github.com/uditgoenka/autoresearch

#### (b) drivelineresearch/autoresearch-claude-code — 纯技能实现

- **架构**: Plugin（非 MCP server），使用 JSONL 状态文件
- **差异化**: 通过 UserPromptSubmit hook 注入上下文；`METRIC name=number` 协议解耦指标
- **实际案例**: 棒球投球速度预测 R² 从 0.44 提升到 0.78（+78%），22 次自主实验

> 来源: https://github.com/drivelineresearch/autoresearch-claude-code

#### (c) Sibyl Research System — 多 Agent 全流程研究系统

- **规模**: 20+ 专门化 Agent，19 阶段流水线
- **能力**: 文献综述 → 假设辩论 → GPU 实验 → 论文写作 → 同行评审
- **自进化**: 外循环从历史教训中学习，自动更新 Agent 提示词
- **原生 Claude Code**: 12 个 slash commands，MCP server 集成

> 来源: https://github.com/Sibyl-Research-Team/AutoResearch-SibylSystem

### 3.2 关键泛化抽象

从社区项目中提取的核心模式：

| 抽象 | 描述 |
|------|------|
| **Metric Protocol** | `METRIC name=number` 输出协议，解耦验证逻辑 |
| **Scope Boundary** | 明确划定 Agent 可修改的文件范围 |
| **Git-as-Memory** | 用 git commit/revert 替代外部状态存储 |
| **Guard Rails** | `Guard: <command>` 约束必须通过才能保留变更 |
| **Results Log** | TSV/JSONL 持久化实验历史 |
| **program.md / GOAL.md** | 人类策略通过 Markdown 文件传达 |

> 来源: [awesome-autoresearch](https://github.com/alvinunreal/awesome-autoresearch)

---

## 4. 长会话机制分析

### 4.1 上下文管理

AutoResearch 的上下文管理策略**不是**传统的上下文窗口管理，而是**外化状态**：

| 机制 | 实现方式 |
|------|----------|
| **短期上下文** | Agent 每次迭代读取 program.md + results.tsv + git log |
| **长期记忆** | Git commit history（完整实验演化路径） |
| **结果追踪** | results.tsv（结构化指标历史） |
| **崩溃恢复** | Git 快照 → 任何时刻可从最后 commit 恢复 |

**关键洞察**：AutoResearch 不解决 LLM 上下文窗口限制——它通过外化状态到文件系统来**绕过**这个问题。每次循环迭代本质上是一次独立的 Agent 调用，读取外部状态后决策。

### 4.2 分段策略

- **无显式分段**：循环每次迭代是独立的"微会话"
- **状态传递**：通过 results.tsv + git diff 传递历史上下文
- **无检查点机制**：依赖 git 作为隐式检查点

### 4.3 已知局限

- Agent 可靠性因 LLM 而异（Karpathy 报告 "Codex doesn't work because it ignores instruction to never stop"）
- 单次实验结果存在噪声（同一变更可能无法复现）
- 日志注入风险：训练代码输出可能被 Agent 误读为指令

> 来源: [Kingy AI 分析](https://kingy.ai/ai/autoresearch-karpathys-minimal-agent-loop-for-autonomous-llm-experimentation/)

---

## 5. Mercury 集成候选方案

### 5.1 Mercury 现状

| 组件 | 当前能力 |
|------|----------|
| Research Agent 角色 | 只读：Web 搜索、文档阅读、KB 读取 → 产出摘要报告 |
| 执行架构 | Orchestrator → SDK Adapters → Agent CLIs |
| 任务管理 | SoT (Ship of Theseus) TaskBundle 状态机 |
| 数据流 | Vue Frontend → Tauri Rust → Node.js Orchestrator → Agent CLIs |

### 5.2 候选方案对比

#### 方案 A: Claude Code Skill 集成（推荐）

**思路**: 将 autoresearch 模式包装为 Mercury 的 Claude Code Skill，在现有 Agent 会话中注入自主循环能力。

**实现路径**:
1. 创建 `.claude/skills/autoresearch/SKILL.md`，定义循环协议
2. 定义 `/autoresearch` slash command 供 Research Agent 使用
3. 使用 `UserPromptSubmit` hook 在每次迭代中注入上下文
4. 结果写入 KB `04-research/` 目录

**架构**:
```
Mercury Orchestrator
  └─ Claude Code CLI (Research Agent session)
       └─ /autoresearch skill
            ├─ SKILL.md (循环协议 + 策略)
            ├─ results.jsonl (迭代历史)
            └─ Git branch (实验快照)
```

| 优势 | 劣势 |
|------|------|
| 零额外基础设施 | 受限于 Claude Code 单会话能力 |
| 复用现有 SDK Adapter | 指标协议需自定义 |
| 与 SoT 流程自然衔接 | 不支持多 Agent 并行实验 |
| MIT 许可无合规风险 | 上下文窗口仍是硬限制 |
| 社区已有成熟实现可参考 | 需 `--dangerously-skip-permissions` |

**估计工作量**: 1-2 个 Session

---

#### 方案 B: 独立 Research Loop Service

**思路**: 构建一个独立的 Node.js 服务，实现 autoresearch 循环逻辑，通过 JSON-RPC 与 Mercury Orchestrator 通信。

**实现路径**:
1. 新增 `packages/research-loop/` 模块
2. 实现循环控制器（迭代管理、指标解析、回滚逻辑）
3. 通过 JSON-RPC 与 Orchestrator 集成
4. 提供 GUI dashboard 展示实验进度
5. 使用 Agent CLI 作为执行后端

**架构**:
```
Mercury Orchestrator
  ├─ Research Loop Service (JSON-RPC)
  │    ├─ Loop Controller (迭代管理)
  │    ├─ Metric Parser (指标解析)
  │    ├─ Git Manager (版本控制)
  │    └─ Results Store (JSONL)
  └─ SDK Adapter → Agent CLI (执行修改)
```

| 优势 | 劣势 |
|------|------|
| 完全控制循环逻辑 | 开发量大（3-5 Sessions） |
| 可支持多 Agent 并行 | 需要新增 Orchestrator 接口 |
| 原生 GUI 集成 | 增加系统复杂度 |
| 不受单 Agent 上下文限制 | 需要维护额外服务 |

**估计工作量**: 3-5 个 Session

---

#### 方案 C: MCP Server 包装

**思路**: 将 autoresearch 循环逻辑实现为 MCP Server，任何 MCP-compatible Agent 都可调用。

**实现路径**:
1. 实现 `autoresearch-mcp-server`（TypeScript）
2. 暴露 tools: `start_loop`, `get_status`, `get_results`, `stop_loop`
3. 内部管理 git 操作、指标解析、结果追踪
4. 在 Mercury 中注册为 MCP server

**架构**:
```
Any MCP-compatible Agent
  └─ autoresearch-mcp-server
       ├─ tools: start_loop / get_status / get_results / stop_loop
       ├─ Git Manager
       ├─ Metric Parser
       └─ Results Store
```

| 优势 | 劣势 |
|------|------|
| Agent 无关——任何 MCP client 可用 | MCP 协议通信开销 |
| 可跨 Agent CLI 复用 | 循环控制权不在 Agent 手中 |
| 天然适合 Mercury 的 adapter 架构 | 需要管理 MCP server 生命周期 |
| 独立部署和测试 | 与 Git 操作有竞争条件风险 |

**估计工作量**: 2-3 个 Session

---

### 5.3 方案对比总表

| 维度 | 方案 A: Skill | 方案 B: 独立服务 | 方案 C: MCP Server |
|------|-------------|-----------------|-------------------|
| 开发成本 | 低 (1-2 Sessions) | 高 (3-5 Sessions) | 中 (2-3 Sessions) |
| 基础设施变更 | 无 | 新增 package | 新增 MCP server |
| 多 Agent 支持 | ❌ | ✅ | ✅ |
| GUI 集成 | 需额外工作 | 原生 | 需额外工作 |
| SoT 流程适配 | 自然衔接 | 需定义新状态 | 需定义新状态 |
| 灵活性 | 中 | 高 | 高 |
| 维护负担 | 低 | 高 | 中 |
| 社区参考 | 成熟 | 无直接参考 | 部分参考 |

---

## 6. 推荐方案：方案 A（Claude Code Skill 集成）

### 6.1 推荐理由

1. **最低成本验证**: 1-2 个 Session 即可完成，快速验证价值
2. **成熟的社区实现**: uditgoenka/autoresearch 和 drivelineresearch/autoresearch-claude-code 已证明此路径可行
3. **零基础设施变更**: 不需要修改 Orchestrator 或新增 package
4. **与 SoT 自然衔接**: 研究循环的结果可直接写入 KB，符合现有工作流
5. **渐进式扩展**: 如果 Skill 方案验证成功，可以后续升级为方案 B 或 C

### 6.2 初步架构设计

```
.claude/
  skills/
    mercury-research-loop/
      SKILL.md           # 循环协议定义
      commands/
        research-loop.md # /research-loop slash command
      references/
        metric-protocol.md
        results-format.md
```

**SKILL.md 核心内容**:
- 循环协议：Review → Hypothesize → Execute → Evaluate → Keep/Discard → Log
- 指标协议：通过 Web 搜索结果的结构化解析提取事实性指标
- 状态外化：results.jsonl + KB 文件
- Guard Rails：所有技术声明必须附来源 URL
- 终止条件：达到研究目标 / 迭代次数上限 / 人类中断

**与现有 Skill 的关系**:
- 复用 `web-research` skill 的验证协议
- 输出格式兼容 `04-research/` KB 结构
- 通过 SoT TaskBundle 触发和追踪

### 6.3 Research Agent 工作流变化

**当前**:
```
Dispatch → 单次 Web 搜索 → 摘要报告 → 提交审阅
```

**集成后**:
```
Dispatch → /research-loop 启动
  → 迭代 1: 初始搜索，建立 baseline 理解
  → 迭代 2: 深入子问题 A，验证关键声明
  → 迭代 3: 深入子问题 B，交叉验证
  → ...
  → 迭代 N: 综合分析，生成结构化报告
  → results.jsonl 记录每次迭代的发现
  → 提交最终报告 → 人类审阅
```

---

## 7. 风险评估与技术约束

### 7.1 风险清单

| 风险 | 等级 | 缓解策略 |
|------|------|----------|
| **上下文窗口耗尽** | 高 | 每次迭代外化状态到文件；使用 results.jsonl 压缩历史 |
| **研究指标主观性** | 高 | 定义机械化可验证的指标（如：声明数、已验证声明比例、引用数） |
| **Agent 偏离循环** | 中 | SKILL.md 中强制 "NEVER STOP" 指令；Guard 约束 |
| **日志/状态注入** | 中 | 不解析外部训练日志（研究场景不涉及代码执行） |
| **许可证冲突** | 低 | MIT License，完全兼容 |
| **语言/运行时约束** | 低 | 原始项目为 Python，但 Skill 方案不依赖 Python 运行时 |
| **`--dangerously-skip-permissions`** | 中 | 研究循环不涉及代码修改，风险有限；可通过 hook 约束 |
| **Token 成本** | 中 | 每次迭代消耗 token；设置迭代上限（如 20 次）|

### 7.2 研究领域特有挑战

原始 autoresearch 的指标（val_bpb）是**客观、机械可测量的**。将此模式应用于**文献调研/技术分析**时，需要定义等效的可量化指标：

| 候选指标 | 可行性 | 说明 |
|----------|--------|------|
| 已回答研究问题数 / 总问题数 | ✅ 高 | 来自 TaskBundle 的 primaryQuestions |
| 引用来源数 | ✅ 高 | 机械可计数 |
| 已验证声明比例 | ✅ 中 | 需定义"已验证"标准 |
| 报告覆盖度评分 | ⚠️ 中 | 需人类或 LLM 评估 |

### 7.3 技术约束清单

1. **Claude Code 会话限制**: 长时间运行可能触发 API 限流或超时
2. **Web 搜索速率限制**: WebSearch 工具有调用频率限制
3. **KB 写入**: 研究循环中间结果需写入 KB，可能产生大量文件
4. **Git 操作**: 研究任务不修改代码，git-as-memory 模式需适配为 KB 文件版本控制
5. **Windows 环境**: 部分 shell 命令需适配 Windows（已在 Mercury 中解决）

---

## 8. 相关生态系统

### 8.1 值得关注的项目

| 项目 | 特点 | 与 Mercury 的潜在协同 |
|------|------|----------------------|
| [awesome-autoresearch](https://github.com/alvinunreal/awesome-autoresearch) | 生态全景目录 | 持续跟踪生态发展 |
| [Orchestra-Research/AI-Research-SKILLs](https://github.com/Orchestra-Research/AI-research-SKILLs) | 双循环研究技能库 | 可作为 Skill 参考 |
| [greyhaven-ai/autocontext](https://github.com/greyhaven-ai/autocontext) | 持久知识保留 + 分阶段验证门 | 与 SoT 的 gate 机制相似 |
| [jmilinovich/goal-md](https://github.com/jmilinovich/goal-md) | GOAL.md 模式 | 与 TaskBundle 概念对齐 |
| [WecoAI/aideml](https://github.com/WecoAI/aideml) | 树搜索优化 | 可作为高级搜索策略 |

### 8.2 学术参考

| 项目 | 会议 | 相关性 |
|------|------|--------|
| ADAS (ShengranHu) | ICLR 2025 | 元 Agent 设计 |
| SICA (MaximeRobeyns) | ICLR 2025 Workshop | Agent 自我改进 |
| GEPA (gepa-ai) | ICLR 2026 Oral | 自然语言反思进化 |
| AI-Researcher (HKUDS) | NeurIPS 2025 | 全流程研究自动化 |

---

## 9. 结论与下一步

### 9.1 核心发现

1. **AutoResearch 的核心价值不在于其 ML 训练代码**，而在于其**自主迭代循环模式**——这一模式已被社区证明可泛化至任意可量化领域
2. **Claude Code Skill 是最成熟的集成路径**——社区已有多个可直接参考的实现
3. **研究领域的最大挑战是指标定义**——需要将主观的"研究质量"转化为可机械测量的代理指标
4. **Mercury 的 SoT + KB 架构天然支持外化状态模式**——TaskBundle 对应 GOAL.md，KB 对应 results log

### 9.2 建议下一步

1. **人类审阅本报告**，确认方案 A 方向
2. 如获批准，创建实施 TaskBundle（TASK-DEV-xxx）
3. 参考 uditgoenka/autoresearch 实现 Mercury 研究循环 Skill
4. 以当前 Session（Issue #59 调研）为第一个 dogfooding 案例回顾验证

---

## 附录：来源 URL 索引

| # | 来源 | URL |
|---|------|-----|
| 1 | AutoResearch GitHub | https://github.com/karpathy/autoresearch |
| 2 | program.md | https://github.com/karpathy/autoresearch/blob/master/program.md |
| 3 | pyproject.toml | https://github.com/karpathy/autoresearch/blob/master/pyproject.toml |
| 4 | Fortune 报道 | https://fortune.com/2026/03/17/andrej-karpathy-loop-autonomous-ai-agents-future/ |
| 5 | Kingy AI 分析 | https://kingy.ai/ai/autoresearch-karpathys-minimal-agent-loop-for-autonomous-llm-experimentation/ |
| 6 | DataCamp 教程 | https://www.datacamp.com/tutorial/guide-to-autoresearch |
| 7 | uditgoenka/autoresearch (Skill) | https://github.com/uditgoenka/autoresearch |
| 8 | drivelineresearch/autoresearch-claude-code | https://github.com/drivelineresearch/autoresearch-claude-code |
| 9 | Sibyl Research System | https://github.com/Sibyl-Research-Team/AutoResearch-SibylSystem |
| 10 | awesome-autoresearch 目录 | https://github.com/alvinunreal/awesome-autoresearch |
| 11 | The New Stack 报道 | https://thenewstack.io/karpathy-autonomous-experiment-loop/ |
| 12 | VentureBeat 报道 | https://venturebeat.com/technology/andrej-karpathys-new-open-source-autoresearch-lets-you-run-hundreds-of-ai |
| 13 | SkyPilot 博客 | https://blog.skypilot.co/scaling-autoresearch/ |
| 14 | Orchestra Research SKILLs | https://github.com/Orchestra-Research/AI-research-SKILLs |
| 15 | DeepWiki 入门指南 | https://deepwiki.com/karpathy/autoresearch/2-getting-started |
