# Mercury

Mercury is a desktop application for multi-agent orchestration. It lets a human operator manage multiple AI coding agents (Claude Code, Codex CLI, opencode, Gemini CLI, etc.) through a single GUI, where a Main Agent can programmatically dispatch tasks to Sub Agents without manual copy-paste relay.

Built with Tauri 2 (Rust) + Vue 3 frontend and a Node.js orchestrator sidecar.

## Project Structure

```
packages/
├── core/               # @mercury/core — shared types, event bus, utility functions
├── sdk-adapters/       # @mercury/sdk-adapters — adapter implementations per AI SDK
│   └── src/
│       ├── claude-adapter.ts
│       ├── codex-adapter.ts
│       └── opencode-adapter.ts
├── orchestrator/       # @mercury/orchestrator — session/prompt/dispatch management
│   └── src/
│       ├── orchestrator.ts
│       ├── rpc-transport.ts
│       └── agent-registry.ts
└── gui/                # @mercury/gui — Tauri 2 + Vue 3 desktop app
    ├── src/            # Vue frontend (components, stores, tauri-bridge)
    └── src-tauri/      # Rust backend (sidecar lifecycle, IPC commands)
```

## Prerequisites

- [Node.js](https://nodejs.org/) >= 20
- [pnpm](https://pnpm.io/) >= 9
- [Rust](https://rustup.rs/) (stable toolchain)
- At least one supported AI CLI installed (`claude`, `codex`, `opencode`, etc.)

## Setup & Run

```bash
# Install dependencies
pnpm install

# Development mode
cd packages/gui && pnpm tauri dev

# Production build
cd packages/gui && pnpm tauri build
```

## Configuration

Define your agents in `mercury.config.json` at the project root:

```json
{
  "agents": [
    { "id": "claude-code", "displayName": "Claude Code", "cli": "claude", "role": "main" },
    { "id": "codex-cli", "displayName": "Codex CLI", "cli": "codex", "role": "dev" }
  ]
}
```

## License

MIT
