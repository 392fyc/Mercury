# Mercury 研究综合 — 多 Agent 协作系统全景

> 综合 6 路并行研究的最终输出。所有结论基于实际网络查询获取的一手资料。
> 研究日期: 2026-03-16

---

## 研究覆盖范围

| 研究路线 | 覆盖内容 | 输出文件 |
|----------|---------|---------|
| GitHub 仓库 | 9 个多 Agent 协作仓库 (OpenHands, Claude Code, Golutra, GSD, Superpowers, Mem0, OpenSpec, Cline, Plandex) | `multi-agent-orchestration-repos.md` |
| Anthropic 技术文章 | 5 篇工程博客 (Effective Agents, Multi-Agent Research, Agent SDK, Context Engineering, Harness) | `anthropic-agent-research.md` |
| OpenAI 技术文章 | 4 篇 (Deep Research, Agents SDK, Practical Guide, AgentKit) + Codex 多 Agent | `openai-multi-agent-research.md` |
| Harness Engineering | 5 篇核心文章 + 6 篇补充 (OpenAI, Philipp Schmid, LangChain, swyx, Anthropic Evals, Cursor, Langfuse, Inngest, Martin Fowler) | `harness-engineering-research.md` |
| Prompt 模式 | Claude Cookbook 全部 Agent prompts, Claude Code 系统 prompt 架构 (~110 条件片段), Agent SDK 模式 | `claude-cookbook-prompts-research.md` |
| 框架与协议 | 8 个框架/协议 (CrewAI, AutoGen, LangGraph, Mastra, Agency Swarm, smolagents, A2A, MCP) + Memory 论文, Cursor, Langfuse | `agent-orchestration-frameworks.md` |

---

## 一、架构决策矩阵

### 1.1 核心架构模式：事件驱动 Orchestrator-Workers

所有研究源收敛到同一结论：

| 维度 | 推荐模式 | 来源 |
|------|---------|------|
| **消息总线** | Event-sourcing（不可变事件 + append-only log） | OpenHands (69k★), Anthropic |
| **编排模式** | Orchestrator-Workers（Lead 分解 + Workers 并行执行） | Anthropic 生产系统 (90.2% 提升), OpenAI Deep Research |
| **Agent 集成** | SDK 优先, PTY 降级 | Claude Agent SDK, Golutra |
| **桌面框架** | Tauri 2 (Rust + Vue) | Golutra 验证可行 |
| **通信协议** | MCP (agent-tool) + A2A (agent-agent) | Linux Foundation 标准 |

### 1.2 Mercury 分层架构

```
┌─────────────────────────────────────────────────┐
│                Mercury GUI (Tauri 2 + Vue)       │
│  ┌───────────┐ ┌────────────┐ ┌───────────────┐ │
│  │ Agent     │ │ Task       │ │ Review &      │ │
│  │ Panels    │ │ Orchestr.  │ │ Diff Viewer   │ │
│  └───────────┘ └────────────┘ └───────────────┘ │
├─────────────────────────────────────────────────┤
│              Event Bus (append-only log)         │
│  Action → Execution → Observation (typed events) │
├─────────────────────────────────────────────────┤
│              Agent Adapters                      │
│  ┌──────┐ ┌──────┐ ┌────────┐ ┌──────┐         │
│  │Claude│ │Codex │ │opencode│ │Gemini│ ...      │
│  │ SDK  │ │ SDK  │ │  HTTP  │ │ SDK  │ PTY     │
│  └──────┘ └──────┘ └────────┘ └──────┘         │
├─────────────────────────────────────────────────┤
│              Infrastructure                      │
│  ┌────────┐ ┌─────────┐ ┌──────┐ ┌───────────┐ │
│  │Memory  │ │Context  │ │Eval  │ │Observ.    │ │
│  │3-Layer │ │Compactor│ │Engine│ │OTEL Traces│ │
│  └────────┘ └─────────┘ └──────┘ └───────────┘ │
└─────────────────────────────────────────────────┘
```

---

## 二、七大核心系统设计

### 2.1 Event Bus（消息总线）

**模式**: OpenHands 事件溯源 + Claude Code Hooks

```typescript
// 事件类型体系
type MercuryEvent =
  | AgentAction      // Agent 发起操作
  | AgentObservation // Agent 接收结果
  | OrchestratorCommand // 编排器指令
  | TaskStateChange  // 任务状态流转
  | HumanIntervention // 人工介入

// 核心特性
- 不可变事件, append-only
- 确定性重放 (debugging + 会话恢复)
- 24+ 生命周期 hook 点 (PreToolUse, PostToolUse, SubagentStart/Stop, etc.)
- HTTP hooks 支持实时事件推送到 Mercury server
```

**来源**: OpenHands event stream, Claude Code 24+ hooks, AutoGen pub-sub

### 2.2 Orchestration Engine（编排引擎）

**模式**: Wave-based 并行调度 + 查询类型分类

```
Phase 1: 任务分析
  └── 查询分类: depth-first / breadth-first / straightforward (Anthropic cookbook)

Phase 2: 任务分解
  └── Wave 分组: 独立任务并行, 依赖任务串行 (GSD)

Phase 3: Agent 分配
  └── 能力矩阵匹配 + 模型路由 (Cursor router pattern)

Phase 4: 并行执行
  └── 每个 Agent 独立 worktree + 隔离上下文 (Superpowers)

Phase 5: 结果综合
  └── 仅压缩摘要回传 orchestrator (1k-2k tokens ← 数万 tokens)

Phase 6: 验证
  └── 双阶段: spec 合规 + 代码质量 (Superpowers)
  └── 盲验收: Acceptance Agent ≠ Implementation Agent (SoT)
```

**Scaling Rules (Anthropic)**:
| 复杂度 | Sub-Agent 数 | Tool 调用 |
|--------|-------------|----------|
| 简单 | 1 | 3-10 |
| 标准 | 2-3 | 10-20 |
| 中等 | 3-5 | 20-50 |
| 高 | 5-10 (max 20) | 50+ |

**来源**: Anthropic multi-agent research (90% 时间节省), GSD wave scheduling, Cursor model routing

### 2.3 Memory System（记忆系统）

**模式**: 三层记忆 + 动态上下文发现

| 层级 | 功能 | 实现 | 来源 |
|------|------|------|------|
| **Factual** | 知识存储 | Obsidian KB + RAG + MCP | Mem0, Memory 论文 |
| **Experiential** | 从运行中学习 | 轨迹日志 + 成功/失败模式 | Memory 论文, Cursor |
| **Working** | 活跃上下文 | 动态上下文发现 + 压缩 | Cursor (-46.9% tokens), Anthropic |

**Context Management Strategies**:
1. **Lazy Loading**: 只加载名称/摘要, Agent 按需获取完整内容 (Cursor, -46.9% tokens)
2. **Compaction**: 接近上下文限制时自动摘要, 保留架构决策和未解决问题 (Anthropic)
3. **Filesystem as Bus**: 文件系统作为 Agent 间的通用接口 (Cursor)
4. **Persistent Notes**: Agent 主动写笔记到上下文窗口外 (Anthropic Pokemon 案例)
5. **Sub-agent Isolation**: 每个 Agent 独立上下文窗口, 仅返回压缩摘要 (Anthropic)

**来源**: Mem0 (49k★), arXiv Memory 论文 (47 authors), Cursor dynamic context discovery, Anthropic context engineering

### 2.4 Harness System（工具链/脚手架）

**模式**: Middleware 架构 + 自验证循环

```
Mercury Harness Middleware Stack:
├── LocalContextMiddleware    — 启动时映射目录和工具 (LangChain)
├── LoopDetectionMiddleware   — Doom loop 检测 (LangChain)
├── PreCompletionMiddleware   — 完成前强制验证 (LangChain)
├── SecurityAnalyzer          — 操作风险评级 LOW/MEDIUM/HIGH (OpenHands)
├── ContextCompactor          — 上下文压缩管理 (Anthropic/Cursor)
└── TraceCollector            — OTEL 轨迹收集 (Langfuse)
```

**Reasoning Sandwich** (LangChain):
- xhigh reasoning → 规划阶段
- high reasoning → 执行阶段
- xhigh reasoning → 验证阶段

**关键洞察**: "Harness IS the product" — 竞争壁垒不在模型, 在基础设施 (Philipp Schmid, Cursor $50B)

**但要 Design for Deletion**: 模型持续吸收 harness 能力, 每个组件应可替换 (swyx, Bitter Lesson)

**来源**: LangChain harness engineering (+13.7% TerminalBench, 0 模型改动), OpenHands SecurityAnalyzer, Inngest Utah Architecture

### 2.5 Observability（可观测性）

**模式**: OpenTelemetry + 三层评估

```
Tracing 层级:
Session (多轮对话)
  └── Trace (单次请求)
       └── Observation (单步操作, 可嵌套)
            ├── LLM Call
            ├── Tool Execution
            └── Sub-agent Delegation
```

**三层评估** (Langfuse + Anthropic):
| 层级 | 评估什么 | 方法 |
|------|---------|------|
| Black-box | 输入→最终输出 | Code graders, LLM-as-judge |
| Glass-box | 完整轨迹 | 轨迹约束验证, 工具调用序列 |
| White-box | 单步操作 | 细粒度步骤分析 |

**关键指标**:
- **pass@k**: 至少一次成功的概率 (开发阶段)
- **pass^k**: 全部成功的概率 (生产可靠性)
- 成本追踪 / 延迟追踪 / 错误率 (per trace)

**来源**: Langfuse, Anthropic Evals, OpenAI tracing, Cursor

### 2.6 Agent Communication Protocol（Agent 通信协议）

**双协议栈**:

| 协议 | 用途 | 标准 |
|------|------|------|
| **MCP** | Agent → Tool 通信 | JSON-RPC 2.0, Anthropic/Linux Foundation |
| **A2A** | Agent → Agent 通信 | JSON-RPC over HTTP/SSE, Google/Linux Foundation |

**A2A 任务生命周期**:
```
submitted → working → input-required → completed / failed
```

**Agent Card** (能力声明):
```json
{
  "name": "claude-code",
  "version": "2.1.76",
  "endpoint": "sdk://localhost:...",
  "capabilities": ["code", "review", "research"],
  "modalities": ["text", "image"],
  "auth": { "type": "api_key" }
}
```

**来源**: MCP spec (2025-11-25), A2A protocol (150+ org), Anthropic + Google

### 2.7 Prompt Engineering Architecture（Prompt 工程架构）

**模式**: 模块化条件组装 (~110 片段) + 角色分离

**Orchestrator vs Worker Prompt 设计**:

| 方面 | Orchestrator | Worker |
|------|-------------|--------|
| 焦点 | 策略/委派/综合 | 执行/研究/特定任务 |
| 工具 | 主要: sub-agent 调度 | 领域特定 (search, read, etc.) |
| 上下文 | 完整任务 + 综合结果 | 原始任务 + 具体子任务指令 |
| 输出 | 最终综合报告 | 原始发现 + 来源归属 |
| 预算 | 控制总体资源分配 | 每任务 tool call 预算 |
| 规则 | 从不做一线研究; 从不委派最终综合 | 从不产生 sub-sub-agent; 尽快完成 |

**关键 Prompt 技术**:
1. XML 结构化通信 (orchestrator ↔ worker)
2. Markdown+YAML frontmatter Agent 定义文件
3. 查询类型分类 (depth-first / breadth-first / straightforward)
4. OODA loop 方法论 (Observe → Orient → Decide → Act)
5. Budget 校准 (简单: <5 tool calls, 复杂: 10-15)
6. Anti-pattern 显式声明 ("NEVER delegate final report writing")
7. 贝叶斯推理指令 ("update priors based on new information")
8. 示例 > 规则 ("examples are worth a thousand words")

**来源**: Anthropic Cookbook (3 prompts), Claude Code system prompts (110+ fragments, Piebald-AI), Agent SDK docs

---

## 三、从研究到 Mercury 的映射

### 3.1 P0 必须实现（消除 36 次人工中转）

| 能力 | 设计决策 | 参考来源 |
|------|---------|---------|
| SDK 驱动 Sub Agent | Claude Agent SDK `query()` + Codex SDK `startThread()` + opencode HTTP serve | Claude Code SDK, Codex docs |
| 事件流通信 | 不可变事件 append-only log, typed events | OpenHands, Anthropic |
| 统一 GUI | Tauri 2 + Vue 3 + Agent 面板 | Golutra (已验证) |
| Session 管理 | SDK session ID + resume + fork | Claude Code SDK, OpenAI Sessions |

### 3.2 P1 重要

| 能力 | 设计决策 | 参考来源 |
|------|---------|---------|
| Wave-based 调度 | 独立任务并行, 依赖串行 | GSD |
| Git worktree 隔离 | 每 Agent 独立 worktree | Superpowers, Claude Code |
| 三层记忆 | Factual + Experiential + Working | Memory 论文, Mem0 |
| 动态上下文发现 | Lazy loading, filesystem as bus | Cursor (-46.9%) |
| Task Bundle | 结构化任务输入 (SoT 已验证) | SoT 管理模式 |

### 3.3 P2 增强

| 能力 | 设计决策 | 参考来源 |
|------|---------|---------|
| OTEL Observability | 嵌套 traces + 三层评估 | Langfuse, Anthropic |
| Harness Middleware | Loop detection, pre-completion check, context compaction | LangChain |
| Model Routing | 按任务复杂度选模型 | Cursor, OpenAI |
| Eval Engine | pass@k + pass^k + code/model/human graders | Anthropic Evals |
| Prompt 版本管理 | 版本化、模板化的 structured prompts | Harrison Chase |

---

## 四、技术栈推荐

| 层 | 选择 | 理由 |
|----|------|------|
| Desktop | Tauri 2 (Rust backend) | Golutra 验证, 原生性能, 进程管理 |
| Frontend | Vue 3 + TypeScript | Golutra 验证, 生态完整 |
| Agent 集成 | SDK 优先 (TS/Py), PTY 降级 | 类型安全 + 事件流 |
| 消息总线 | 自建 Event Store (Rust) | 轻量, 不可变, 确定性重放 |
| 记忆层 | Mem0 (via MCP) + Obsidian KB | 三层记忆, 已有 MCP 集成 |
| 可观测性 | OpenTelemetry + Langfuse (可选) | 行业标准, 开源 |
| 通信协议 | MCP (agent-tool) + 自定义 A2A-like | 已有广泛支持 |
| 构建工具 | pnpm monorepo | Golutra 验证, 现代标准 |

---

## 五、关键洞察总结

### 5.1 所有来源的共识

1. **Start Simple** — 单 Agent 起步, 复杂度按需增加 (Anthropic, OpenAI, 所有框架)
2. **Context > Prompt** — 上下文工程 > prompt 工程 > 模型选择 (Anthropic, Cursor, 所有人)
3. **Harness IS the Product** — 竞争壁垒在基础设施 (Cursor $50B, OpenAI Codex team)
4. **Design for Deletion** — 模型持续吸收 harness 功能, 组件应可替换 (swyx Bitter Lesson)
5. **Sub-agent Isolation** — 每个 Agent 独立上下文, 仅返回压缩摘要 (所有多 Agent 系统)
6. **Eval-Driven Development** — 先定义能力, 再实现 (Anthropic, LangChain)
7. **Review > Generation** — 瓶颈从实现转向审查 (Harrison Chase)

### 5.2 有争议的观点

| 观点 | 支持方 | 反对方 | Mercury 立场 |
|------|--------|--------|-------------|
| Harness 重要性 | LangChain (+13.7%), Cursor, Inngest | Anthropic ("all sauce in model"), Noam Brown | **Both matter**; 聚焦模型无法做的: 持久化, 状态管理, 编排 |
| PTY vs SDK | Golutra (PTY 通用) | Claude Code (SDK 优先) | **SDK 优先, PTY 降级** — SDK 更稳定但需要各 Agent 支持 |
| Framework vs 自建 | CrewAI, LangGraph, AutoGen | Anthropic (start with APIs) | **Infrastructure 自建, 模式参考** — Mercury 需求独特 |
| 代码为动作 vs JSON 工具调用 | smolagents (代码更可组合) | 大多数框架 (JSON tool calls) | **JSON 工具调用** — 与所有 Agent SDK 兼容 |

### 5.3 Mercury 的独特定位

| vs 现有方案 | Mercury 差异 |
|-------------|-------------|
| vs Golutra | SDK 优先 (非 PTY), Orchestrator 模式 (非简单包装) |
| vs OpenHands | 桌面 GUI (非 Docker/K8s), 多异构 Agent (非单 Agent) |
| vs GSD | 跨 Agent 协作 (非 Claude-only), GUI (非 CLI) |
| vs CrewAI/LangGraph | 集成真实 CLI Agents (非 API-only agents) |
| vs Cline | 独立桌面应用 (非 VS Code 插件), 多 Agent (非单 Agent) |

---

## 六、下一步：Phase 0 PoC 路线

基于研究结论，Phase 0 应验证:

1. **SDK 集成 PoC**: 用 Claude Agent SDK `query()` 开启 session, 发送 prompt, 接收 streaming response
2. **事件总线 PoC**: 实现最小事件 store + typed event 发射/接收
3. **Tauri Shell PoC**: Tauri 2 + Vue 3 最小 GUI, 内嵌一个 Agent 面板
4. **跨 Agent 通信 PoC**: Main Agent (Claude SDK) 驱动 Sub Agent (Codex MCP server), 结果自动回传

成功标准: Main Agent 通过 Mercury 发出任务 → Sub Agent 自动接收执行 → 结果自动回传 → **0 次人工中转**

---

*研究完成日期: 2026-03-16 | 6 路并行研究 | 40+ 一手来源*
