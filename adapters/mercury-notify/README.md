# mercury-notify

Thin HTTP client for hook scripts to send notifications through the channel router.

## Role

Forwards `notify(severity, title, body)` calls to `mercury-channel-router` via HTTP POST.
Does not hold Telegram credentials or spawn processes. If the router is not running, fails silently.

## Usage

```js
const { notify } = require('./adapters/mercury-notify/notify.cjs');
await notify('error', 'Mercury stall: no_progress', 'details here');
```

## Startup

No startup needed. `require` directly from any hook or script. The router must be running separately (spawned automatically by `mercury-channel-client` when a Claude Code session starts).

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `MERCURY_ROUTER_PORT` | No | IPC port (default: 8788) |
| `MERCURY_NOTIFY_DISABLED` | No | Set to any value to skip all notifications silently |

## Setup

Add to `~/.claude/settings.json` env block:

```json
{
  "env": {
    "MERCURY_TELEGRAM_BOT_TOKEN": "your-bot-token",
    "MERCURY_TELEGRAM_ALLOWED_USER_IDS": "123456789"
  }
}
```

## Error Handling

Never throws. Returns `{ ok: false, error: "transport" }` if router is unreachable.
Logs to stderr only.

## Notes

- Bun is optional — Node 18+ works fine.
- This module has no dependencies beyond Node built-ins.
