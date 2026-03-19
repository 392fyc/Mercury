# KB 结构

Obsidian vault 命名约定: `{Project}_KB`（如 `Mercury_KB`）。路径由项目配置决定，不硬编码。

| 路径 | 内容 | 写权限 |
|------|------|--------|
| `10-tasks/` | TaskBundle JSON | Main 创建, Dev 填 receipt |
| `11-issues/` | IssueBundle JSON | 任意角色创建, Main triage |
| `12-acceptances/` | AcceptanceBundle JSON | Main 创建, Acceptance 填结果 |
| `13-handoff/` | Handoff packets, session context | Main + 发起方 |
| `99-templates/` | Bundle 模板（只读参考） | Main only |

KB 路径通过 orchestrator 启动配置注入，各 Agent 指令文件中引用相对路径 `Mercury_KB/{子目录}/`。
