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
  sdk-adapters/       # Agent CLI wrappers
  core/               # Shared TypeScript types
```

## Agent 指令文件

每个 Agent CLI 对应一个根目录指令文件（`CLAUDE.md` / `AGENTS.md` / `OPENCODE.md` / `GEMINI.md`），
遵循统一模板。**Main Agent 由用户在 UI/config 中动态指定**，任何 Agent 均可担任 Main 角色。

角色在运行时通过 orchestrator system prompt 注入，指令文件中不包含硬编码角色映射。
