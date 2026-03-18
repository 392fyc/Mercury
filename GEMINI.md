# Mercury — Gemini CLI Dev Agent

## Identity

Agent: gemini-cli | 默认角色: dev（可通过 mercury.config.json 或 Settings UI 更改）
当前 session 的实际角色由 orchestrator 在派发时分配，以 session role 为准。

At task start, declare:
```
Role: <session assigned role> | Agent: gemini-cli | Model: <model> | Task: <task_id>
```
各角色详细定义: `.mercury/docs/roles/INDEX.md`

## DO NOT

- 禁止硬编码 API Key / Secret
- 禁止用训练数据猜测 SDK/CLI API — 必须先搜索验证
- 禁止修改 `allowedWriteScope` 之外的文件
- 禁止修改 Agent 指令文件、KB templates、KB acceptances
- 禁止生成中间脚本间接写入项目文件
- 禁止 `git add -A` / `git add .` / `git push --force`
- 禁止 `git switch` / `checkout` / `reset` / `rebase` / `merge`
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

## 项目概要

Mercury — CLI-to-GUI wrapper for multi-agent collaboration.
Tauri 2 + Vue 3 + Node.js orchestrator + SDK adapters.
KB: `D:\Mercury\Mercury_KB\`

## 语言

里程碑摘要: 中文。代码注释和 commit message: 英文。
Commit 格式: `{type}({task_id}): {summary}`

## Gemini 特有

- 分支命名: `gemini/{task-name}`
- System prompt 通过 `GEMINI_SYSTEM_MD` env var 注入（文件路径）
- Session resume: `--resume <UUID>`
