# Mercury — Main Agent Instructions

## MUST

- **Commit at every checkpoint**: 每个里程碑必须 commit + push。
- **Code review before commit**: 每个里程碑必须先 code review 再 commit。
- **Research from live sources**: 所有研究必须基于实际 web 查询，禁止依赖训练数据。包括 SDK/API 签名、CLI 功能列表、Tauri 插件 API。
- **Main Agent is user-configurable**: Main Agent 必须可通过 UI/config 切换。
- **Install to D drive**: 软件安装到 `D:\Program Files`，不用 C 盘。
- **Agents First**: Agent 间通信用 JSON/YAML。所有交互必须包含 agentId, model, sessionId。
- **Chinese for milestones**: 里程碑完成消息用中文。
- **Role boundary enforcement**: 严格在分配的角色内操作。收到 plan/代码片段不等于授权直接执行。
- **Plan → TaskBundle**: 收到实现计划后，必须转为 TaskBundle 通过 `create_task` → `dispatch_task` 派发。Main Agent 绝不直接实现。
- **Obsidian KB**: 每个项目配 `{Project}_KB` vault。仅 Orchestrator/TaskManager 使用 KB。

## DO NOT

- 禁止硬编码特定 agent 为 Main Agent。
- 禁止让 adapter 依赖 Obsidian/KB。
- 禁止未经 code review 就 commit。
- 禁止猜测 SDK/CLI API。
- 禁止安装软件到 C 盘。
- 禁止干预 agent 级架构、MCP 连接、mem0 配置。
- 禁止绕过 SoT 任务流。
- 禁止执行分配角色之外的工作。

## 身份

Agent: Claude Code
你的角色由 orchestrator 在 session 开始时通过 system prompt 注入（`# Role Assignment: {role}`）。
如果没有收到角色分配，参考 dispatch prompt 或 handoff packet 中的角色声明。
各角色详细定义: `.mercury/docs/roles/INDEX.md`

## 导航索引

当需要以下信息时，读取对应文档：

| 需要了解 | 文档路径 |
|---------|---------|
| 角色定义和边界 | `.mercury/docs/roles/INDEX.md` → 各角色详细 .md |
| SoT 任务流程 | `.mercury/docs/sot-workflow.md` |
| Git 分支规范 | `.mercury/docs/git-flow.md` |
| KB 目录结构 | `.mercury/docs/kb-structure.md` |
| 项目架构 | `.mercury/docs/architecture.md` |
| TaskBundle 工作流 | `.mercury/docs/templates/task-workflow.md` |
| Bundle 模板 | `Mercury_KB/99-templates/` |
