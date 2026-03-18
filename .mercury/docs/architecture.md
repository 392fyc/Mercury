# Mercury 架构

CLI-to-GUI wrapper for multi-agent collaboration.

## 技术栈

- **Frontend**: Vue 3 (Tauri 2 desktop app)
- **Shell**: Rust (Tauri)
- **Orchestrator**: Node.js sidecar (JSON-RPC 2.0 over stdio)
- **SDK Adapters**: 包装 Agent CLI (Claude Code, Codex, opencode, Gemini CLI)
- **Task Flow**: SoT (Ship of Theseus) orchestration pattern

## 数据流

```
Vue Frontend → Tauri Rust → Node.js Orchestrator → SDK Adapters → Agent CLIs
```

## 目录结构

```
packages/
  gui/                # Tauri 2 desktop app
    src/              # Vue 3 frontend
    src-tauri/        # Rust shell
  orchestrator/       # Node.js sidecar (JSON-RPC 2.0)
  sdk-adapters/       # Agent CLI wrappers
  core/               # Shared TypeScript types
```

## Agent 指令文件

| 文件 | Agent | 默认角色 |
|------|-------|---------|
| `CLAUDE.md` | Claude Code | main |
| `AGENTS.md` | Codex CLI | dev |
| `OPENCODE.md` | opencode | dev |
| `GEMINI.md` | Gemini CLI | dev |
