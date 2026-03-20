# KB 结构

Obsidian vault 命名约定: `{Project}_KB`（如 `Mercury_KB`）。路径由项目配置决定，不硬编码。

| 路径 | 内容 | 写权限 |
|------|------|--------|
| `00-index/` | 导航、仪表盘、handoff指南、决策索引 | Main only |
| `01-registry/` | 全局索引、阶段注册表、策略 | Main only |
| `01-research/` | 研究文档与分析 | Research agent / Main |
| `02-context/` | 当前session账本、checkpoint、计划 | Main only |
| `03-decisions/` | 架构决策记录 (ADR) | Main only |
| `10-tasks/` | TaskBundle JSON | Main 创建, Dev 填 receipt |
| `11-issues/` | IssueBundle JSON | 任意角色创建, Main triage |
| `12-acceptances/` | AcceptanceBundle JSON | Main 创建, Acceptance 填结果 |
| `13-handoff/` | Handoff packets, session context | Main + 发起方 |
| `99-templates/` | Bundle 模板（只读参考） | Main only |
| `archive/` | 已归档制品（过期handoff、旧研究） | Main only |

KB 路径通过 orchestrator 启动配置注入，各 Agent 指令文件中引用相对路径 `{Project}_KB/{子目录}/`。

## 目录编号约定

- `00-09`: 元数据层（导航、注册表、研究、上下文、决策）— `01-registry` 和 `01-research` 共享前缀是有意设计，二者均属「只读参考数据」类别
- `10-19`: 工作制品层（tasks、issues、acceptances、handoffs）— 由 orchestrator 读写
- `99`: 模板层（只读参考）
- `archive/`: 已过期制品归档
