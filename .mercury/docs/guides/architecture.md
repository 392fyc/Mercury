# Mercury 架构

Mercury 是一套轻量的多代理协作 harness：它配置 Claude Code 与 Codex 两种运行时，而不是自带桌面应用或编排服务。早期的 Tauri/Vue 桌面应用、Node.js orchestrator 与 `packages/` 工作区已在 #666 删除，可通过 Git 历史追溯。

## 现行组成

```text
.claude/        Claude Code 的子代理定义、技能与权限规则
.codex/         Codex 的配置、代理与命令规则
.agents/skills/ 跨运行时共用的技能
.mercury/       文档、状态清单与下游同步模板（templates/codex-project/）
adapters/       外部项目的薄适配层（目前只有 playwright-mcp）
scripts/        维护脚本、lane 工具与 Codex 受控 Git 脚本
```

## 角色定义

Claude Code 的角色定义在 `.claude/agents/*.md`，Codex 的在 `.codex/agents/*.toml`；其中 `main.toml` 描述主会话本身，不是子代理。当前主会话就是 Mercury 主代理。

## Agent 指令文件

`AGENTS.md` 是共享项目规则，`CLAUDE.md` 通过 `@AGENTS.md` 导入并补充 Claude Code 适配。其他运行时的指令文件（`GEMINI.md`、`OPENCODE.md`）已在 #666 删除。
