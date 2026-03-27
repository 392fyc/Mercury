# Mercury

Mercury is a desktop application for multi-agent orchestration. It lets a human operator manage multiple AI coding agents — Claude Code (`claude`), Codex CLI (`codex`), opencode (`opencode`), Gemini CLI (`gemini`), etc. — through a single GUI, where a Main Agent can programmatically dispatch tasks to Sub Agents without manual copy-paste relay.

Built with Tauri 2 (Rust) + Vue 3 frontend and a Node.js orchestrator sidecar.

## Project Structure

```text
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

### All Platforms

- [Node.js](https://nodejs.org/) >= 20
- [pnpm](https://pnpm.io/) >= 9
- [Rust](https://rustup.rs/) (stable toolchain)
- At least one supported AI CLI installed:

  | Product | CLI executable |
  |---------|---------------|
  | Claude Code | `claude` |
  | Codex CLI | `codex` |
  | opencode | `opencode` |
  | Gemini CLI | `gemini` |

### Windows

- [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/) — install with "Desktop development with C++" workload
- WebView2 (pre-installed on Windows 10 1803+ and Windows 11)

### Linux (Debian/Ubuntu)

```bash
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

### macOS

- Xcode Command Line Tools: `xcode-select --install`

## Setup & Run

```bash
# Install dependencies
pnpm install

# Development mode (from repo root — runs @mercury/gui via workspace filter)
pnpm dev

# Or from the gui package directly
cd packages/gui && pnpm tauri dev

# Production build (from repo root)
pnpm build
```

## Configuration

Define your agents in `mercury.config.json` at the project root. Required fields: `id`, `displayName`, `cli`. Optional: `role` (`"main"` | `"dev"` | `"review"`, default `"dev"`).

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
