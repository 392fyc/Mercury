# mercury-channel-router

Long-running Telegram bot + IPC server. One instance per machine.

## Role

- Holds the single Telegram bot polling connection (one connection per bot token).
- Exposes a localhost HTTP IPC server for `mercury-channel-client` and `mercury-notify` to communicate with.
- Routes inbound Telegram messages to the correct Claude Code session.
- Handles commands: `/status`, `/list`, `/cancel`, `/continue`, `/help`.
- Enforces sender allowlist and session limit (max 3).

## Startup

Do not start manually. `mercury-channel-client` spawns the router automatically on first Claude Code session start. The router exits 30 seconds after all sessions deregister.

To start manually for testing:

```bash
MERCURY_TELEGRAM_BOT_TOKEN=<token> node adapters/mercury-channel-router/router.cjs
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MERCURY_TELEGRAM_BOT_TOKEN` | Yes (for Telegram) | BotFather token |
| `MERCURY_TELEGRAM_ALLOWED_USER_IDS` | **REQUIRED for inbound** | Comma-separated Telegram user IDs (sender allowlist). Empty = all inbound messages dropped (fail-closed). |
| `MERCURY_TELEGRAM_CHAT_ID` | No | Default chat_id for `/notify` when no session has chatted yet |
| `MERCURY_ROUTER_PORT` | No | IPC port (default: 8788) |
| `MERCURY_NOTIFY_DISABLED` | No | Disables Telegram polling entirely; IPC still works |

## User Setup

Add to `~/.claude/settings.json` env block:

```json
{
  "env": {
    "MERCURY_TELEGRAM_BOT_TOKEN": "your-bot-token",
    "MERCURY_TELEGRAM_ALLOWED_USER_IDS": "123456789",
    "MERCURY_TELEGRAM_CHAT_ID": "123456789"
  }
}
```

## Launching Claude Code with Channels

```bash
claude --dangerously-load-development-channels server:mercury-telegram
```

Or set `CLAUDE_HANDOFF_AUTO_LAUNCH_FLAGS` in your environment to propagate this flag through `claude-handoff` auto-spawned sessions.

## IPC Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/register` | Register a session |
| DELETE | `/register/:id` | Deregister a session |
| POST | `/take-ownership/:id` | Mark session as active |
| POST | `/notify` | Send outbound Telegram message |
| POST | `/reply` | Claude reply forwarded to Telegram |
| GET | `/sessions` | List registered sessions |
| GET | `/inbox/:id` | SSE stream of inbound events for a session |

## Notes

- Bun is optional — Node 18+ works fine.
- Lock file at `~/.mercury/router.lock` prevents duplicate instances.
- Requires `node-telegram-bot-api` (installed via pnpm at project root).
