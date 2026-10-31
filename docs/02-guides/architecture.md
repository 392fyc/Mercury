# Mercury 架构

Mercury 是一套轻量的多代理协作 harness：它配置 Claude Code 与 Codex 两种运行时，而不是自带桌面应用或编排服务。早期的 Tauri/Vue 桌面应用、Node.js orchestrator 与 `packages/` 工作区已在 #666 删除，可通过 Git 历史追溯。

## 现行组成

```text
.claude/        Claude Code 的子代理定义、技能入口（mercury- 前缀，由 tools/mercury.py 生成）与权限规则
.codex/         Codex 的配置、代理与命令规则
.agents/skills/ Codex 的技能入口（mercury- 前缀的薄入口，由 tools/mercury.py 生成）
.mercury/       文档、状态清单，以及装载结果：config.json（选用的 pack）、packs/（pack 的装载副本）、lock.json
packs/          可装载内容（技能、脚本、指南）的源；pack.json 声明技能，build/ 放生成器；playwright-mcp 在 mercury.json 中停用
mercury.json    总控：哪些 pack 存在、哪些启用（core 必选）
sources/        外部项目原样（pinned git submodule）
tools/          mercury.py：check / apply --target 装载 pack
scripts/        Mercury 专用维护脚本与 Codex 受控 Git 脚本
```

## 角色定义

Claude Code 的角色定义在 `.claude/agents/mercury-*.md`，Codex 的在 `.codex/agents/mercury-*.toml`；二者由 `tools/mercury.py` 从 `packs/core/roles/` 单一来源生成，不手工修改。`.codex/agents/main.toml` 归项目所有，描述主会话本身，不是子代理。当前主会话就是 Mercury 主代理。

## Agent 指令文件

`AGENTS.md` 是共享项目规则，`CLAUDE.md` 通过 `@AGENTS.md` 导入并补充 Claude Code 适配。其他运行时的指令文件（`GEMINI.md`、`OPENCODE.md`）已在 #666 删除。
