# mercury-channel-client

MCP channel server — one instance per Claude Code session, bridging the session to `mercury-channel-router`.

> **PREREQUISITE**: Claude Code MUST be launched from the **project root directory** for `.mcp.json` relative paths to resolve correctly. Launching from a subdirectory will fail to load this adapter.

## Role

- Spawned by Claude Code via `.mcp.json` when the session starts with `--dangerously-load-development-channels`.
- Detects if the router is running; spawns it if not.
- Registers the current session with the router (branch name, project path, pid).
- Opens an SSE stream to receive inbound Telegram messages and forwards them as `notifications/claude/channel`.
- Exposes a `reply` MCP tool so Claude can send messages back to Telegram.
- Deregisters on session exit.

## Startup

Claude Code loads this automatically via `.mcp.json` at the project root. No manual start needed.

Launch Claude Code with channels enabled:

```bash
claude --dangerously-load-development-channels server:mercury-telegram
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MERCURY_ROUTER_PORT` | No | IPC port (default: 8788) |
| `CLAUDE_SESSION_ID` | Auto | Set by Claude Code; used as session identity |
| `CLAUDE_PROJECT_DIR` | Auto | Set by Claude Code; used for label derivation |

## User Setup

1. Ensure `.mcp.json` exists at the project root (already committed).
2. Add bot token and allowed user IDs to `~/.claude/settings.json`:

```json
{
  "env": {
    "MERCURY_TELEGRAM_BOT_TOKEN": "your-bot-token",
    "MERCURY_TELEGRAM_ALLOWED_USER_IDS": "your-telegram-user-id"
  }
}
```

3. Launch with:

```bash
claude --dangerously-load-development-channels server:mercury-telegram
```

## MCP Tool: reply

```json
{
  "name": "reply",
  "description": "Send a reply to Telegram via the channel router.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "chat_id": { "type": "number" },
      "text":    { "type": "string" }
    },
    "required": ["chat_id", "text"]
  }
}
```

## Notes

- Bun is optional — Node 18+ works fine.
- Requires `@modelcontextprotocol/sdk` (installed via pnpm at project root).
- Session limit is 3; if the router returns 429, the client logs a warning and Claude Code continues without Telegram.
