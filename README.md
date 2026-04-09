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

  After installing, verify the CLI is on your `PATH`:

  ```bash
  claude --version     # Claude Code
  opencode --version   # opencode
  ```

  Mercury auto-detects installed CLIs at startup. If none are found, the GUI shows a setup prompt.

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

**Config loading order:** `mercury.config.example.json` is loaded first as a template, then merged with `mercury.config.json` (project) or `~/.mercury/config.json` (home). If neither exists, the template (or built-in defaults) is used and written to `mercury.config.json` automatically.

**Validation:** If the config file is missing or contains invalid JSON, Mercury falls back to built-in defaults and logs a warning in the GUI console. Required fields (`id`, `displayName`, `cli`, `roles`, `integration`, `capabilities`, `restrictions`, `maxConcurrentSessions`) are validated at load time — agents with missing fields are skipped with a warning. Config changes require an app restart; hot-reload is not currently supported.

## Example Files

Several configuration files are shipped as `.example` templates. Copy and customize before use:

| Template | Target | Purpose |
|---|---|---|
| `mercury.config.example.json` | `mercury.config.json` | Agent definitions (see [Configuration](#configuration)) |
| `.pr_agent.toml.example` | `.pr_agent.toml` | PR review bot configuration (Argus / Qodo Merge) |
| `CLAUDE.local.md.example` | `CLAUDE.local.md` | Claude Code local instructions (caveman concise mode) |

`mercury.config.json`, `.pr_agent.toml`, and `CLAUDE.local.md` are gitignored — changes stay local.

### Caveman Mode (CLAUDE.local.md)

Enables persistent concise output style via [caveman](https://github.com/JuliusBrussee/caveman) — drops filler ~30-40%, preserves all technical content.

```bash
# Via skill (recommended) — takes effect on next session restart
/caveman-on          # enable lite mode (default)
/caveman-on full     # enable full mode
/caveman-off         # disable

# Or manually
cp CLAUDE.local.md.example CLAUDE.local.md
```

### PR Review Bot (.pr_agent.toml)

```bash
cp .pr_agent.toml.example .pr_agent.toml
# Edit .pr_agent.toml with your review bot instructions
```

## License

MIT
