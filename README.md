# Mercury — Multi-Agent GUI Orchestrator

> One human + one Main Agent + N Sub Agents = automated multi-agent collaboration

## What is Mercury?

Mercury is a desktop GUI application that enables a human operator to manage multiple AI coding agents (Claude Code, Codex CLI, opencode, Gemini CLI, etc.) through a unified interface. The Main Agent can directly open, drive, and monitor Sub Agent sessions — eliminating manual copy-paste relay that plagues current multi-agent workflows.

## Status

**Phase 1 MVP** — Core architecture implemented, Tauri GUI shell running

## Architecture

```
Vue Frontend ←invoke/events→ Tauri Rust ←JSON-RPC→ Node.js Orchestrator → SDK Adapters
```

## Project Structure

```
mercury/
├── package.json                        # pnpm monorepo root
├── mercury.config.json                 # User-defined agent configuration
├── packages/
│   ├── core/                           # @mercury/core
│   │   └── src/
│   │       ├── types.ts                # AgentConfig, MercuryEvent, SessionInfo, etc.
│   │       └── event-bus.ts            # Append-only typed event bus
│   ├── sdk-adapters/                   # @mercury/sdk-adapters
│   │   └── src/
│   │       ├── claude-adapter.ts       # Claude Agent SDK adapter
│   │       ├── codex-adapter.ts        # Codex CLI SDK adapter
│   │       └── opencode-adapter.ts     # opencode HTTP adapter
│   ├── orchestrator/                   # @mercury/orchestrator
│   │   └── src/
│   │       ├── rpc-transport.ts        # JSON-RPC 2.0 over stdio
│   │       ├── agent-registry.ts       # Agent config → adapter mapping
│   │       ├── orchestrator.ts         # Session, prompt, dispatch management
│   │       └── index.ts                # Sidecar entry point
│   ├── gui/                            # @mercury/gui (Tauri 2 + Vue 3)
│   │   ├── src/
│   │   │   ├── components/             # AgentPanel, EventLog, TitleBar
│   │   │   ├── stores/                 # Reactive state (agents, messages, events)
│   │   │   └── lib/tauri-bridge.ts     # Typed Tauri IPC wrappers
│   │   └── src-tauri/                  # Rust backend
│   │       └── src/
│   │           ├── sidecar.rs          # Node.js orchestrator lifecycle
│   │           ├── commands.rs         # Tauri command handlers
│   │           └── lib.rs              # App setup + plugin registration
│   └── poc/                            # @mercury/poc (Phase 0 verification)
│       └── src/                        # 6 PoC tests (SDK, EventBus, cross-agent)
└── docs/
    ├── design/                         # Architecture & workflow analysis
    ├── research/                       # 40+ source research synthesis
    └── poc-report.md                   # Phase 0 feasibility report
```

## Key Features

- **User-configurable agents** — Main Agent role is not hardcoded; any agent can be primary
- **Zero-handoff orchestration** — Main Agent dispatches to Sub Agents programmatically
- **Event-sourced audit trail** — Append-only immutable event log for all interactions
- **Session continuity** — Automatic handoff on context overflow with summary inheritance
- **SDK-first integration** — Claude Agent SDK, Codex SDK, opencode HTTP

## Configuration

Edit `mercury.config.json` to define your agents:

```json
{
  "agents": [
    { "id": "claude-code", "displayName": "Claude Code", "cli": "claude", "role": "main", ... },
    { "id": "codex-cli", "displayName": "Codex CLI", "cli": "codex", "role": "dev", ... }
  ]
}
```

## Getting Started

### Prerequisites

- [Node.js](https://nodejs.org/) (v18+)
- [pnpm](https://pnpm.io/) (v9+)
- [Rust](https://rustup.rs/) (latest stable)
- At least one supported AI CLI tool installed (e.g. `claude`, `codex`, `opencode`)

### Build & Run

```bash
# Install dependencies
pnpm install

# Run in development mode
cd packages/gui && pnpm tauri dev

# Build for production
cd packages/gui && pnpm tauri build
```

## License

MIT
