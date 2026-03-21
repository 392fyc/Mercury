# Codex 提升为 Main Agent — 基础设施需求

> 状态: 规划中 | 当前 Main Agent: Claude Code (唯一)

## 前置条件

当前系统在配置层面限制仅 Claude Code 可担任 Main Agent。
提升 Codex 为 Main Agent 需要以下基础设施逐项就绪。

## 需求矩阵

| # | 基础设施 | 说明 | 复杂度 | 状态 |
|---|---------|------|--------|------|
| 1 | **MCP Server 暴露 Mercury RPC** | Mercury orchestrator 暴露 streamable HTTP MCP endpoint，Codex 通过 MCP tools 调用 `dispatch_task`, `create_task` 等。Codex 原生支持 MCP（config.toml 配置） | 中 | 未开始 |
| 2 | **Skills: dispatch workflow** | `.agents/skills/dispatch-task/SKILL.md` 定义 dispatch 流程，让 Codex 知道如何创建+下发任务 | 低 | 未开始 |
| 3 | **Skills: acceptance workflow** | `.agents/skills/acceptance-review/SKILL.md` 定义 acceptance 流程 | 低 | 未开始 |
| 4 | **Approval 桥接** | Codex 的 `on-request` approval 模式与 Mercury approval control plane 对接，路由到 Mercury GUI | 中 | 未开始 |
| 5 | **Sandbox 配置** | Main agent 需要 git 操作 + 网络调用。配置 `workspace-write` + 网络白名单或 `danger-full-access` | 低 | 未开始 |
| 6 | **Orchestrator 配置校验** | 允许在 agent config 中将 Codex 的 roles 包含 `main`，orchestrator 解除 Claude-only 限制 | 低 | 未开始 |

## Codex 能力现状（已验证）

| 能力 | 状态 | 说明 |
|------|------|------|
| 程序化调用 | 支持 | `codex exec` 非交互模式，app-server JSON-RPC |
| AGENTS.md | 支持 | 层级发现，session 启动时注入 |
| Session 持久化 | 支持 | 交互模式可 resume，有用户级历史保存 |
| Skills | 支持 | `.agents/skills/` 目录，SKILL.md 格式，显式/隐式触发 |
| Hooks | 实验性 | SessionStart/Stop, UserPromptSubmit |
| MCP 集成 | 支持 | config.toml 配置 MCP servers，STDIO 或 streamable HTTP |
| Multi-agent | 支持 | spawn_agent/send_input/wait_agent 内置工具 |
| Web search | 支持 | `web_search` 工具 |

## Codex MCP 接入优化方向

当前 Mercury 通过 `CodexAdapter`（app-server transport）与 Codex 通信。
如果 Codex 作为 Main Agent，可考虑反向架构：

```
方案 A (当前): Mercury Orchestrator → CodexAdapter → Codex app-server
方案 B (MCP):  Codex → MCP tools → Mercury Orchestrator (as MCP server)
```

方案 B 中 Codex 主动调用 Mercury RPC，orchestrator 退化为被动服务。
这需要 Mercury orchestrator 实现 MCP server protocol（当前仅有 JSON-RPC HTTP server）。

## Skills 同步扩充计划

Claude Code 和 Codex 的 skill 格式不通用：

| | Claude Code | Codex |
|---|-------------|-------|
| 目录 | `.claude/skills/` | `.agents/skills/` |
| 格式 | `SKILL.md` (markdown) | `SKILL.md` (markdown) |
| 触发 | description 匹配 / slash command | `$skill-name` / description 匹配 |

**统一策略**：在 `.mercury/skills/` 维护通用 skill 定义源，构建时/启动时同步到各 agent 目录。
或在 AGENTS.md/CLAUDE.md 的 Navigation 表中指向 `.mercury/skills/` 作为 skill 参考。

## 依赖关系

```
#1 MCP Server ──→ #2 dispatch skill ──→ #6 配置解锁
                  #3 acceptance skill ──↗
#4 Approval 桥接 ──────────────────────↗
#5 Sandbox 配置 ───────────────────────↗
```

#1 是前置依赖：没有 MCP endpoint，Codex 无法调用 Mercury RPC。
