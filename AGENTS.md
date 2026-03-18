# Mercury — Codex CLI Dev Agent

## Identity

Agent: codex-cli
你的角色由 orchestrator 在 session 开始时通过 system prompt 注入（`# Role Assignment: {role}`）。
如果没有收到角色分配，参考 dispatch prompt 或 handoff packet 中的角色声明。

At task start, declare:
```
Role: <session assigned role> | Agent: codex-cli | Model: <model> | Task: <task_id>
```
各角色详细定义: `.mercury/docs/roles/INDEX.md`

## DO NOT

- 禁止硬编码 API Key / Secret
- 禁止用训练数据猜测 SDK/CLI API — 必须先搜索验证
- 禁止修改 `allowedWriteScope` 之外的文件
- 禁止修改 Agent 指令文件、KB templates、KB acceptances
- 禁止生成中间脚本间接写入项目文件
- 禁止 `git add -A` / `git add .` / `git push --force`
- 禁止 `git switch` / `checkout` / `reset` / `rebase` / `merge` — 分支管理是 Main Agent 的职责
- 禁止直接操作 master / develop
- 禁止完成后自行拾取新任务

## 导航索引

| 需要了解 | 文档路径 |
|---------|---------|
| 你的角色详细定义 | `.mercury/docs/roles/dev.md` |
| TaskBundle 完整工作流 | `.mercury/docs/templates/task-workflow.md` |
| Git 规范和权限 | `.mercury/docs/git-flow.md` |
| 项目架构 | `.mercury/docs/architecture.md` |
| KB 目录结构 | `.mercury/docs/kb-structure.md` |
| Issue 报告 | `.mercury/docs/sot-workflow.md` |

## 项目概要

Mercury — CLI-to-GUI wrapper for multi-agent collaboration.
Tauri 2 + Vue 3 + Node.js orchestrator + SDK adapters.
KB: `D:\Mercury\Mercury_KB\`

## 语言

里程碑摘要: 中文。代码注释和 commit message: 英文。
Commit 格式: `{type}({task_id}): {summary}`

## Codex 特有

- 分支命名: `codex/{task-name}`
- Cargo.lock 等生成文件因依赖变更改动时一并提交
- 分支状态异常 → 停止工作，上报 Main Agent
