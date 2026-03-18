# KB 结构

Obsidian vault: `D:\Mercury\Mercury_KB\`

| 路径 | 内容 | 写权限 |
|------|------|--------|
| `10-tasks/` | TaskBundle JSON | Main 创建, Dev 填 receipt |
| `11-issues/` | IssueBundle JSON | 任意角色创建, Main triage |
| `12-acceptances/` | AcceptanceBundle JSON | Main 创建, Acceptance 填结果 |
| `13-handoff/` | Handoff packets, session context | Main + 发起方 |
| `99-templates/` | Bundle 模板（只读参考） | Main only |

路径通过 `mercury.config.json` 的 `obsidian.kbPaths` 配置。
