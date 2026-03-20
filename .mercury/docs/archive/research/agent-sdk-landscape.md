# Agent SDK/API 接口概览

> Mercury 项目技术选型参考：各 CLI Agent 的程序化控制接口

---

## 接口汇总矩阵

| Agent | SDK 包名 | 非交互 CLI | Streaming JSON | Session 恢复 | MCP Server 模式 |
|-------|---------|-----------|---------------|-------------|----------------|
| **Claude Code** | `@anthropic-ai/claude-agent-sdk` (TS) / `claude-agent-sdk` (Py) | `claude -p` | `--output-format stream-json` | `--resume <id>` | ❌ |
| **Codex CLI** | `@openai/codex-sdk` (TS) | `codex exec` | `--json` (JSONL) | `resume <id>` | ✅ `codex mcp-server` |
| **opencode** | 无独立 SDK，HTTP Server + ACP | `opencode run` | `--format json` | `--session`, `--continue` | ❌ (自身是 MCP 客户端) |
| **Gemini CLI** | `@google/gemini-cli-sdk` (TS) | `gemini -p` | `--output-format stream-json` | ❌ | ❌ |

---

## Claude Code — Claude Agent SDK

### 核心 API
```typescript
import { query, ClaudeAgentOptions } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Fix the bug in auth.py",
  options: {
    allowedTools: ["Read", "Edit", "Bash"],
    permissionMode: "acceptEdits",
    model: "opus",
  },
})) {
  // AssistantMessage | ToolUseMessage | ResultMessage
}
```

### 关键能力
- Subagent 系统：`agents: { "reviewer": AgentDefinition(...) }`
- Hooks：PreToolUse, PostToolUse, Stop, SessionStart
- Session 持久化 + 恢复
- MCP Server 集成
- 权限控制（acceptEdits, bypassPermissions, canUseTool callback）

---

## Codex CLI — Codex SDK + MCP Server

### SDK API
```typescript
import { Codex } from "@openai/codex-sdk";

const codex = new Codex();
const thread = codex.startThread();
const result = await thread.run("Fix the CI failures");
```

### MCP Server 模式（可被其他 Agent 调用）
```bash
codex mcp-server
# 暴露 "codex" 和 "codex-reply" 两个 MCP tool
```

### 原生多 Agent
```bash
codex exec --agents '[{"role":"explorer"},{"role":"worker"}]'
# 内置 spawn_agents_on_csv, 最大 6 并发, 1 层嵌套
```

---

## opencode — HTTP Server + ACP

### HTTP Server 模式
```bash
opencode serve --port 4096 --hostname localhost
# 其他客户端可 attach
opencode run --attach http://localhost:4096 "task"
```

### ACP (Agent Client Protocol)
```bash
opencode acp
# stdin/stdout nd-JSON 消息传递
```

### 特性
- Client/Server 架构，TUI 只是一个客户端
- `--attach` 允许多客户端连接同一 server
- 避免 MCP server 冷启动延迟

---

## Gemini CLI — Gemini CLI SDK

### SDK API
```typescript
import { GeminiCliAgent } from "@google/gemini-cli-sdk";

const agent = new GeminiCliAgent({ instructions: "..." });
const stream = agent.sendStream("prompt", controller.signal);

for await (const chunk of stream) {
  if (chunk.type === "content") process.stdout.write(chunk.value.text);
}
```

### 特性
- sendStream() 异步可迭代
- AbortController 取消支持
- 2M token 上下文窗口

---

## 通用集成模式

### 推荐优先级
1. **SDK 集成**（最佳）— 类型安全，事件流，session 管理
2. **非交互 CLI + JSON 输出**（次选）— 简单但功能完整
3. **PTY 包装**（降级）— 任何 CLI 工具都能用，但需解析 ANSI

### 进程管理要点
- 每个 Agent 独立进程
- `--no-session-persistence` / `--ephemeral` 避免磁盘竞争
- Claude Code `--worktree` 支持 git 隔离并行
- JSON 事件流用于监控；进程信号用于生命周期管理

---

## 参考项目: Golutra

| 维度 | Golutra 方案 | Mercury 可借鉴/改进 |
|------|------------|-------------------|
| 集成方式 | PTY 包装所有 CLI | SDK 优先，PTY 降级 |
| 技术栈 | Rust (Tauri) + Vue 3 | 待定（研究阶段） |
| 编排 | 顺序派发，@mention 路由 | Orchestrator 模式，Main Agent 编排 |
| 会话管理 | PTY session + wezterm-term | Agent SDK session + 持久化 |
| 消息路由 | Semantic parser 从终端输出提取 | SDK 事件流原生结构化 |
| 许可证 | BSL 1.1（非开源） | 仅参考架构思路 |

---

*编写日期: 2026-03-15 | Main Agent 研究汇总*
