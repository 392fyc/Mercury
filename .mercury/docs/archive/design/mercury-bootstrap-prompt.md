# Mercury 项目启动 Prompt

> 用于 Mercury 项目专属 Agent 的首次会话初始化

---

## 项目定义

```
[Agent: Mercury Dev Agent | Model: 待定]
[Task Type: Research + Development]
Topic: Mercury — Multi-Agent GUI Orchestrator
Priority: Sev-1
```

### 项目一句话描述

Mercury 是一个桌面 GUI 应用，允许人类操作者通过一个 Main Agent 界面同时管理和编排多个 AI Agent（Claude Code, Codex CLI, opencode, Gemini CLI 等），消除手动复制粘贴 prompt/结果的中转成本。

---

## 背景

### 为什么需要 Mercury

在 Ship of Theseus 项目中，我们使用 Claude Code 作为 Main Agent，Codex CLI / opencode / AntiGravity 作为 Sub Agents 的多 Agent 协作模式。该模式**已验证有效**（参见 `docs/design/existing-workflow-analysis.md`），但存在一个核心瓶颈：

**所有 Agent 间通信需要人工中转**（复制粘贴 prompt + 等待 + 复制结果）。

一次 3 Agent × 3 轮迭代的任务产生 36 次人工中转操作，每次 1-5 分钟。Mercury 的目标是将这个数字降到 0（Main Agent 直接驱动 Sub Agent session）。

### 已有参考

| 参考 | 类型 | 路径 |
|------|------|------|
| 现有管理模式分析 | 设计文档 | `docs/design/existing-workflow-analysis.md` |
| Agent SDK 接口概览 | 技术调研 | `docs/research/agent-sdk-landscape.md` |
| Golutra | 竞品参考 | `github.com/golutra/golutra` |
| Anthropic 多Agent架构 | 架构参考 | `anthropic.com/research/building-effective-agents` |
| OpenAI Agents SDK | 架构参考 | `openai.github.io/openai-agents-python/` |

---

## 核心需求

### Tier 0 — 必须实现

1. **统一 Agent 会话面板**
   - 在同一 GUI 中同时显示多个 Agent 的会话窗口
   - 每个 Agent 会话独立，可并行运行
   - 会话历史持久化，支持恢复

2. **Main Agent 驱动 Sub Agent**
   - Main Agent 可以通过 SDK/API 直接开启 Sub Agent 的新 session
   - Sub Agent 的输出自动回传 Main Agent 上下文
   - 无需人工复制粘贴

3. **CLI 原生体验**
   - 每个 Agent 窗口支持直接输入命令行命令
   - 支持 / 命令（如 `/commit`, `/review-pr`）
   - 支持 Agent 各自的 SDK 特有功能

4. **图片直接传输**
   - 在会话窗口中直接粘贴/拖入图片
   - 图片作为消息内容发送给 Agent（不是文件路径引用）
   - Agent 返回的图片/截图直接内联显示

### Tier 1 — 重要

5. **任务编排视图**
   - 可视化当前所有 Agent 的任务状态
   - 支持从 Main Agent 派发任务到指定 Sub Agent
   - 任务状态自动追踪

6. **上下文共享**
   - 跨 Agent 共享文件引用、设计裁定、约定
   - 共享上下文不占用单个 Agent 的上下文窗口

7. **多模型支持**
   - 每个 Agent 可配置不同的模型
   - 支持运行时切换模型

### Tier 2 — 增强

8. **模板和工作流**
   - 预定义任务模板（Task Bundle 格式）
   - 可复用的多 Agent 工作流

9. **通知和监控**
   - Agent 完成任务时通知
   - 异常/阻塞检测

---

## 技术方向（待研究确认）

### 集成策略

```
Mercury GUI
├── Claude Code ← Claude Agent SDK (TS/Py)
├── Codex CLI   ← Codex SDK (TS) 或 MCP Server 模式
├── opencode    ← HTTP Server (opencode serve) 或 ACP (stdin/stdout nd-JSON)
├── Gemini CLI  ← Gemini CLI SDK (TS) 或 PTY 降级
└── 未来 Agent  ← 通过 PTY/SDK 适配器扩展
```

### 优先使用 SDK，PTY 作为降级
- 所有 4 个主要 Agent 都提供了 SDK 或非交互 JSON 接口
- SDK 提供类型安全、事件流、session 管理
- PTY 作为通用降级方案，用于不支持 SDK 的 Agent

### 技术栈候选（需进一步研究）
- **Tauri 2 (Rust + Web前端)** — Golutra 方案，桌面原生 + Web UI
- **Electron + React/Vue** — 更成熟的生态，开发速度快
- **纯 Web (本地 server)** — 最灵活，跨平台
- 研究阶段不锁定技术栈

---

## 第一阶段目标

### Phase 0: 技术验证 (PoC)

**目标**: 验证 SDK 集成可行性

1. 用 TypeScript 实现最小化 demo：
   - 通过 Claude Agent SDK 开启一个 Claude Code session
   - 发送 prompt，接收 streaming response
   - 显示在简单 web UI 中

2. 同样验证 Codex SDK 和 opencode serve

3. 验证一个 "Main Agent" 能否通过 SDK 驱动另一个 Agent 完成任务

**交付物**: PoC 代码 + 可行性报告

### Phase 1: MVP

基于 PoC 结果选定技术栈，实现 Tier 0 需求。

---

## Agent 研究任务清单

以下研究任务可派发给 Mercury 项目的 Dev Agent：

### R1: Golutra 深度分析
- 克隆 golutra 仓库，分析完整架构
- 重点研究：PTY 管理、消息语义解析、编排调度逻辑
- 输出：架构分析文档

### R2: SDK PoC 实现
- 分别用 Claude Agent SDK、Codex SDK、opencode serve 实现最小 demo
- 测试 session 创建、prompt 发送、response 接收、session 恢复
- 输出：PoC 代码 + 对比报告

### R3: GUI 框架评估
- 对比 Tauri 2 vs Electron vs 纯 Web 方案
- 考虑：终端仿真、进程管理、图片处理、跨平台
- 输出：技术选型推荐

### R4: 多 Agent 编排 PoC
- 实现 Main Agent 通过 SDK 驱动 Sub Agent 的 PoC
- 测试：prompt 派发 → 执行 → 结果回传 → Main Agent 消费
- 输出：编排流程验证

---

## 项目约束

1. **独立于游戏开发** — Mercury 是工具项目，不依赖 Ship of Theseus 代码
2. **借鉴 KB 管理模式** — 参考但不复制 SoT 的 Registry/Bundle/Session 模式
3. **项目路径**: `D:\Mercury\`
4. **语言**: 设计文档中文，代码英文

---

*编写日期: 2026-03-15 | Main Agent (Claude Code) | Mercury 项目启动*
