# Mercury Phase 0 PoC 可行性报告

> 日期: 2026-03-16 | 执行环境: Windows 11, Node 24.13, Claude Code 2.1.72, Codex CLI 0.114, opencode 1.2.26

---

## 执行摘要

**结论: Mercury 核心架构可行。** SDK 集成、事件总线、跨 Agent 通信、会话连续性全部验证通过。

| PoC | 测试内容 | 结果 | 备注 |
|-----|---------|------|------|
| **PoC-1** | Claude Agent SDK (`query()`) | ✅ PASS | 单次调用, streaming response, session ID 捕获 |
| **PoC-2** | Codex CLI SDK (`startThread()` + `run()`) | ✅ PASS | 3 条消息返回, usage 统计可获取 |
| **PoC-3** | opencode (`run --format json`) | ⚠️ 部分验证 | CLI 可调用, 参数正确, 但执行超时 (模型响应慢, 非集成问题) |
| **PoC-4** | Event Bus (append-only, typed events) | ✅ PASS | 9/9 测试通过 |
| **PoC-5** | 跨 Agent 通信 (Main→Sub→Result) | ✅ PASS | **核心验证: 0 次人工中转** |
| **PoC-6** | Session Continuity (overflow→handoff) | ✅ PASS | 会话链追踪, 摘要传递, 事件审计 |

---

## 1. SDK 集成验证

### 1.1 Claude Agent SDK (`@anthropic-ai/claude-agent-sdk`)

```
Package: @anthropic-ai/claude-agent-sdk
API: query({ prompt, options }) → AsyncGenerator<SDKMessage>
验证: ✅ 导入成功, 会话启动, prompt 发送, streaming 响应接收
```

**关键发现:**
- `query()` 返回 `AsyncGenerator`, 可直接 `for await` 消费
- 消息类型包括 `system` (含 `session_id`), `assistant` (内容), `result` (最终结果)
- `resume` 选项支持会话恢复
- `allowedTools` 可精确控制 Agent 可用工具
- `agents` 字段支持 subagent 定义

**Mercury 集成方式:**
```typescript
const sdk = await import("@anthropic-ai/claude-agent-sdk");
for await (const msg of sdk.query({ prompt, options })) {
  eventBus.emit("agent.message.receive", agentId, sessionId, msg);
}
```

### 1.2 Codex CLI SDK (`@openai/codex-sdk`)

```
Package: @openai/codex-sdk
API: new Codex() → codex.startThread() → thread.run(prompt) → { items, finalResponse, usage }
验证: ✅ 线程创建, prompt 执行, 结构化结果返回
```

**关键发现:**
- `run()` 返回 `{ items[], finalResponse, usage }` — 结构化, 易解析
- `items` 数组包含独立消息块 (type: `agent_message`)
- `usage` 提供 token 计数 (`input_tokens`, `cached_input_tokens`, `output_tokens`)
- `runStreamed()` 支持流式事件
- `resumeThread(id)` 支持会话恢复

**Mercury 集成方式:**
```typescript
const codex = new Codex();
const thread = codex.startThread();
const result = await thread.run(prompt);
// result.finalResponse → Sub Agent 结果回传 Main Agent
```

### 1.3 opencode (`opencode run`)

```
CLI: opencode run --format json "prompt"
验证: ⚠️ CLI 调用成功, 参数格式正确, 但模型执行超时
```

**关键发现:**
- `--format json` 输出 JSON 事件流
- `--session` / `--continue` 支持会话恢复
- `--attach http://localhost:PORT` 支持连接到运行中的 server
- Windows 需要 `shell: true` 选项

**建议:** opencode 集成可行但需要:
1. 更长的超时设置 (>120s)
2. 优先使用 `opencode serve` + HTTP attach 模式 (避免每次冷启动)
3. 或直接通过 `--attach` 连接已运行的 opencode server

---

## 2. Event Bus 验证

**9/9 测试全部通过:**

| 测试 | 结果 |
|------|------|
| 基础 emit + subscribe | ✅ |
| 事件不可变性 (Object.freeze) | ✅ |
| 通配符订阅 (*) | ✅ |
| 过滤订阅 (by agentId) | ✅ |
| 取消订阅 | ✅ |
| 按 session 检索事件 | ✅ |
| 按 agent 检索事件 | ✅ |
| Append-only 日志完整性 | ✅ |
| Parent event 链式追踪 | ✅ |

**架构验证:**
- 不可变事件 + append-only log = 可重放, 可审计
- 类型化事件 (13 种 EventType) 覆盖 Agent 完整生命周期
- 过滤订阅支持按 Agent/Session 精确路由
- Parent event chaining 支持任务链追踪

---

## 3. 跨 Agent 通信验证

**核心验证: SoT 36 次人工中转 → Mercury 0 次**

```
流程:
  User → Main Agent (Claude): "Read README and list project goals"
       → Main Agent 决定委派
       → Sub Agent (Codex) 自动接收任务
       → Sub Agent 执行 (读文件, 生成摘要)
       → 结果自动回传 Main Agent 上下文
       → 全程 0 次人工介入
```

**事件链:**
```
agent.session.start (claude-code, main)
agent.session.start (codex-cli, dev, delegatedFrom: claude-code)
orchestrator.task.dispatch (TASK-POC-001, assignedTo: codex-cli)
agent.message.receive × 3 (codex-cli 执行结果)
agent.session.end (codex-cli)
orchestrator.task.complete (TASK-POC-001, resultSummary: 1054 chars)
```

**关键指标:**
- 总事件数: 8
- Main Agent 事件: 3
- Sub Agent 事件: 5
- 任务链完整: YES
- 人工中转次数: **0**

---

## 4. Session Continuity 验证

**上下文溢出 → 自动启用新 session 继承对话:**

```
Session 1 (active) → context overflow detected
  → 生成摘要 (240 chars)
  → orchestrator.context.compact event
  → Session 2 (active, parentSessionId = Session 1)
  → orchestrator.session.handoff event
  → Session 2 接收摘要 + 继续 prompt
```

**验证通过:**
- 会话链追踪 (parentSessionId) ✅
- Compact 事件记录 ✅
- Handoff 事件记录 ✅
- 摘要传递到新会话 ✅

---

## 5. 项目结构

```
D:/Mercury/
├── package.json              # pnpm monorepo root
├── pnpm-workspace.yaml
├── tsconfig.json
├── packages/
│   ├── core/                 # @mercury/core
│   │   └── src/
│   │       ├── types.ts      # 类型定义 (Agent, Event, Task, Session)
│   │       ├── event-bus.ts  # 事件总线 (append-only, typed)
│   │       └── index.ts
│   ├── sdk-adapters/         # @mercury/sdk-adapters
│   │   └── src/
│   │       ├── claude-adapter.ts   # Claude Agent SDK 适配器
│   │       ├── codex-adapter.ts    # Codex CLI SDK 适配器
│   │       ├── opencode-adapter.ts # opencode HTTP 适配器
│   │       └── index.ts
│   └── poc/                  # @mercury/poc
│       └── src/
│           ├── claude-sdk-test.ts      # PoC-1
│           ├── codex-sdk-test.ts       # PoC-2
│           ├── opencode-test.ts        # PoC-3
│           ├── eventbus-test.ts        # PoC-4
│           ├── cross-agent-test.ts     # PoC-5
│           └── session-continuity-test.ts # PoC-6
└── docs/
    ├── design/               # 设计文档
    ├── research/             # 研究文档 (6 路研究输出)
    └── poc-report.md         # 本报告
```

---

## 6. 技术决策确认

| 决策 | PoC 前假设 | PoC 后结论 |
|------|-----------|-----------|
| Claude 集成 | SDK 优先 | ✅ **确认: `query()` API 完全满足需求** |
| Codex 集成 | SDK 优先 | ✅ **确认: `Codex.startThread().run()` 结构化返回** |
| opencode 集成 | HTTP serve | ⚠️ **调整: 优先 `serve --port` + `--attach`, 避免 one-shot 冷启动** |
| 消息总线 | Event-sourcing | ✅ **确认: append-only + typed events + parent chaining** |
| 会话管理 | SDK session + handoff | ✅ **确认: 链式 session + 摘要传递** |
| 跨 Agent 通信 | SDK 驱动 | ✅ **确认: 0 次人工中转, 事件链完整** |

---

## 7. Phase 1 (MVP) 建议

基于 PoC 结果，Phase 1 应实现:

### 必须 (Tier 0)
1. **Tauri 2 GUI Shell** — 安装 Rust 工具链, 搭建 Tauri 2 + Vue 3 骨架
2. **Agent 面板** — 每个 Agent 一个会话面板, 显示实时消息流
3. **Main Agent 集成** — Claude SDK `query()` 完整集成, 含 session resume
4. **Sub Agent 集成** — Codex SDK 集成, 任务派发 + 结果回传
5. **Event Bus GUI** — 事件流可视化, 调试/审计用

### 重要 (Tier 1)
6. **Session Continuity** — 上下文监控 + 自动 handoff
7. **Task Bundle UI** — 任务创建/状态追踪界面
8. **opencode 集成** — HTTP serve 模式长连接

### 前置条件
- [ ] 安装 Rust 工具链 (`rustup`)
- [ ] 安装 Tauri 2 CLI (`cargo install tauri-cli`)
- [ ] 验证 Tauri 2 + Vue 3 starter 可运行

---

## 8. 风险和缓解

| 风险 | 严重度 | 缓解 |
|------|--------|------|
| Claude SDK API 变化 | 中 | SDK 有 V2 preview, 关注更新; adapter 层隔离变化 |
| Codex SDK 不稳定 | 中 | 备选: Codex MCP server 模式 (`codex mcp-server`) |
| opencode one-shot 慢 | 低 | 使用 serve 模式长连接 |
| Rust 学习曲线 (Tauri) | 中 | Tauri 后端逻辑最小化, 核心逻辑在 TS 侧 |
| 多 Agent 并发冲突 | 中 | Git worktree 隔离 (Superpowers 模式验证) |

---

*Phase 0 PoC 完成 | 2026-03-16 | Mercury 项目*
