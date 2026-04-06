# Codex 提升为 Main Agent — 基础设施需求

> 状态: 规划中 | 当前 Main Agent: Claude Code (唯一)
> 更新: 2026-03-21

## 前置条件

当前系统在配置层面限制仅 Claude Code 可担任 Main Agent。
提升 Codex 为 Main Agent 需要以下基础设施逐项就绪。

## 需求矩阵

| # | 基础设施 | 说明 | 复杂度 | 状态 |
|---|---------|------|--------|------|
| 1 | **MCP Server 暴露 Mercury RPC** | Mercury orchestrator 暴露 streamable HTTP MCP endpoint，Codex 通过 MCP tools 调用 `dispatch_task`, `create_task` 等 | 中 | 未开始 |
| 2 | **Skills: dispatch workflow** | `.agents/skills/dispatch-task/SKILL.md` 定义 dispatch 流程 | 低 | ✅ 已完成 |
| 3 | **Skills: acceptance workflow** | `.agents/skills/acceptance-review/SKILL.md` 定义 acceptance 流程 | 低 | ✅ 已完成 |
| 4 | **Skills: auto-verify** | `.agents/skills/auto-verify/SKILL.md` 提交前质量门控 | 低 | ✅ 已完成 |
| 5 | **Skills: web-research** | `.agents/skills/web-research/SKILL.md` 联网查证协议 | 低 | ✅ 已完成 |
| 6 | **Skills: sot-workflow** | `.agents/skills/sot-workflow/SKILL.md` SoT 状态机参考 | 低 | ✅ 已完成 |
| 7 | **Approval 桥接** | Codex `on-request` approval 模式与 Mercury approval control plane 对接 | 中 | 未开始 |
| 8 | **Sandbox 配置** | Main agent 需要 `workspace-write` + 网络白名单 | 低 | 未开始 |
| 9 | **Orchestrator 配置校验** | 允许 Codex roles 包含 `main`，解除 Claude-only 限制 | 低 | 未开始 |

## Codex 能力现状（已验证 2026-03）

| 能力 | 状态 | 说明 | 来源 |
|------|------|------|------|
| 程序化调用 | ✅ | `codex exec` 非交互模式，`codex app-server` JSON-RPC | [OpenAI Codex CLI Reference](https://developers.openai.com/codex/cli/reference) |
| AGENTS.md | ✅ | 层级发现，session 启动时注入 | [OpenAI Codex CLI](https://developers.openai.com/codex/cli) |
| Session 持久化 | ✅ | 交互模式可 resume，有用户级历史保存 | [OpenAI Codex CLI](https://developers.openai.com/codex/cli) |
| Skills | ✅ | `.agents/skills/` 目录，SKILL.md 格式，`$skill-name` 或 description 触发 | [OpenAI Codex CLI](https://developers.openai.com/codex/cli) |
| Hooks | 🔶 | SessionStart/Stop, UserPromptSubmit（实验性） | [OpenAI Codex CLI](https://developers.openai.com/codex/cli) |
| MCP Client | ✅ | `codex mcp add` + `config.toml`，STDIO 或 streamable HTTP | [OpenAI Codex MCP Docs](https://developers.openai.com/codex/mcp) |
| MCP Server | ✅ | `codex mcp-server` 暴露 `codex()` + `codex-reply(threadId)` | [OpenAI Codex MCP Docs](https://developers.openai.com/codex/mcp) |
| Subagents | ✅ | 内置 `default`/`worker`/`explorer`，自定义 TOML agents | [OpenAI Codex Subagents](https://developers.openai.com/codex/subagents) |
| Web search | ✅ | `web_search` 内置工具 | [OpenAI Codex CLI](https://developers.openai.com/codex/cli) |

## Adapter 迁移方案：App-Server → MCP

### 当前架构 (方案 A)

```
Mercury GUI → Orchestrator → CodexAdapter → codex app-server (stdio JSON-RPC)
                                              ↕ (私有协议)
                                            Codex CLI
```

- `CodexAdapter` 通过 `CodexAppServerTransport` 管理 stdio 管道
- 私有 JSON-RPC 协议，需维护完整 type 定义 (`codex-app-server-types.ts`)
- Approval 通过自定义 server request 处理
- 与 Codex 版本强耦合

### 目标架构 (方案 B — Codex as MCP Server)

```
Mercury GUI → Orchestrator → CodexMCPAdapter → codex mcp-server (stdio MCP)
                                                ↕ (标准 MCP 协议)
                                              Codex CLI
```

优势：
- 标准 MCP 协议，不需要维护私有 type 定义
- `codex()` = 新 session (dispatch)，`codex-reply(threadId)` = rework/follow-up
- JSON-RPC notifications 提供结构化事件流
- ThreadManager 内置 session 管理，threadId 即 sessionId
- 可复用官方 MCP TypeScript SDK（npm: `@modelcontextprotocol/sdk` v1.x 稳定），减少自定义代码

迁移步骤：

| # | 步骤 | 说明 | 影响文件 |
|---|------|------|---------|
| 1 | 创建 `CodexMCPAdapter` | 实现 `AgentAdapter` 接口，通过 MCP client 连接 `codex mcp-server` | 新文件 |
| 2 | 映射 MCP tools 到 Adapter 方法 | `codex()` 映射到 `send()`, `codex-reply()` 映射到带 threadId 的 `send()` | 新文件 |
| 3 | 映射 MCP notifications 到事件流 | JSON-RPC notifications 转换为 `AgentStreamingEvent` | 新文件 |
| 4 | Approval 桥接 | Codex MCP server 的 approval 机制对接 Mercury approval control plane | 需研究 |
| 5 | 更新 orchestrator agent 注册 | `integration: "mcp"` 替代 `integration: "pty"` | agent config |
| 6 | 保留旧 adapter 为 fallback | `CodexAdapter` 不删除，通过 config 切换 | 无 |

### 远期架构 (方案 C — Mercury as MCP Server, Codex as Main)

```
Codex CLI (Main Agent)
  ↕ MCP tools
Mercury Orchestrator (MCP Server)
  ↕ JSON-RPC HTTP
Mercury GUI (Dashboard)
```

此方案中：
- Codex 通过 `codex mcp add mercury --url http://localhost:7654/mcp` 注册 Mercury
- Mercury 暴露 MCP tools: `create_task`, `dispatch_task`, `get_task`, `record_receipt` 等
- Codex 主动调用 Mercury RPC，orchestrator 退化为被动服务
- Codex 的 skills 指导它何时调用哪个 MCP tool

前置依赖：Mercury orchestrator 需实现 MCP server protocol（当前仅 JSON-RPC HTTP）

## Skills 同步状态

| Skill | `.claude/skills/` | `.agents/skills/` | 功能对齐 |
|-------|-------------------|-------------------|---------|
| dispatch-task | ✅ | ✅ | ✅ |
| acceptance-review | ✅ | ✅ | ✅ |
| web-research | ✅ | ✅ | ✅ |
| sot-workflow | ✅ | ✅ | ✅ |
| auto-verify | ✅ | ✅ | ✅ |

同步策略：手动对齐。未来可在 `.mercury/skills/` 维护通用源，构建时同步。

## 依赖关系

```
方案 B (Codex as Dev via MCP):
  CodexMCPAdapter ──→ 测试 ──→ config 切换

方案 C (Codex as Main):
  #1 Mercury MCP Server ──→ Skills ready ──→ #9 配置解锁
  #7 Approval 桥接 ──────────────────────────↗
  #8 Sandbox 配置 ───────────────────────────↗
```

方案 B 可独立推进，不阻塞方案 C。
方案 C 的 #1 (Mercury MCP Server) 是关键前置依赖。
