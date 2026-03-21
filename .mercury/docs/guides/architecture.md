# Mercury 架构

用于多代理协作的命令行到图形界面包装器。

## 技术栈

- **Frontend**: Vue 3 (Tauri 2 desktop app)
- **Shell**: Rust (Tauri)
- **Orchestrator**: Node.js sidecar (JSON-RPC 2.0 over stdio)
- **SDK Adapters**: 包装 Agent CLI (Claude Code, Codex, opencode, Gemini CLI)
- **Task Flow**: SoT (Ship of Theseus) orchestration pattern

## 数据流

```text
Vue Frontend → Tauri Rust → Node.js Orchestrator → SDK Adapters → Agent CLIs
```

## 目录结构

```text
packages/
  gui/                # Tauri 2 desktop app
    src/              # Vue 3 frontend
    src-tauri/        # Rust shell
  orchestrator/       # Node.js sidecar (JSON-RPC 2.0)
    src/
      role-loader.ts      # 运行时从 YAML 加载角色定义
      role-prompt-builder.ts  # 生成 role-scoped system prompt
      task-manager.ts     # TaskBundle 状态机 + 基于模板的 prompt 构建
  sdk-adapters/       # Agent CLI wrappers
  core/               # Shared TypeScript types (RoleCard interface, AgentRole)
```

## 角色定义

角色以 YAML 文件定义于 `.mercury/roles/{role}.yaml`，运行时由 `role-loader.ts` 加载。
每个 YAML 包含结构化元数据（canExecuteCode, canDelegateToRoles, inputBoundary, outputBoundary）
和 instructions 文本块。5 个角色: main, dev, acceptance, research, design。

## Dispatch 模板

Dispatch prompt 模板位于 `.mercury/templates/`，由 task-manager.ts 在运行时读取并填充占位符。
模板包含执行协议和歧义升级规则。

## Agent 指令文件

每个 Agent CLI 对应一个根目录指令文件（`CLAUDE.md` / `AGENTS.md` / `OPENCODE.md` / `GEMINI.md`），
遵循统一精简模板（Identity + Navigation + MUST/DO NOT）。
**当前运营约束（非永久性架构限制）**：仅 Claude Code 配置为 Main Agent。
这是阶段性限制，非硬编码——系统架构本身支持任意 agent 担任 main 角色。
将其他 agent（如 Codex）提升为 Main Agent 需要额外基础设施搭建（见 `.mercury/docs/codex-main-agent-roadmap.md`）。

角色在运行时通过 orchestrator system prompt 注入，指令文件中不包含角色定义（defer to YAML）。
