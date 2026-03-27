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

  | Product | CLI | Install |
  |---------|-----|---------|
  | Claude Code | `claude` | `npm i -g @anthropic-ai/claude-code` |
  | Codex CLI | `codex` | `npm i -g @openai/codex` |
  | opencode | `opencode` | See [opencode.ai/download](https://opencode.ai/download) |
  | Gemini CLI | `gemini` | `npm i -g @google/gemini-cli` |

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

Define your agents in `mercury.config.json` at the project root. See `mercury.config.example.json` for a full example.

**Required fields per agent:**

| Field | Type | Description |
|-------|------|-------------|
| `id` | `string` | Unique agent identifier |
| `displayName` | `string` | Display name in GUI |
| `cli` | `string` | CLI executable name |
| `roles` | `AgentRole[]` | Assigned roles (see below) |
| `integration` | `string` | `"sdk"` \| `"mcp"` \| `"http"` \| `"pty"` \| `"rpc"` |
| `capabilities` | `string[]` | e.g. `["code", "review", "orchestration"]` |
| `restrictions` | `string[]` | Scope restrictions |
| `maxConcurrentSessions` | `number` | Max parallel sessions |

**Optional:** `model` (e.g. `"claude-opus-4-6"`, `"o3"`)

**Valid roles:** `"main"` | `"dev"` | `"acceptance"` | `"critic"` | `"research"` | `"design"`

```json
{
  "agents": [
    {
      "id": "claude-code",
      "displayName": "Claude Code",
      "cli": "claude",
      "model": "claude-opus-4-6",
      "roles": ["main", "design"],
      "integration": "sdk",
      "capabilities": ["code", "review", "orchestration"],
      "restrictions": [],
      "maxConcurrentSessions": 3
    }
  ]
}
```

If `mercury.config.json` is missing, the orchestrator falls back to `mercury.config.example.json`. Validation errors are logged to the sidecar console.

## License

MIT
