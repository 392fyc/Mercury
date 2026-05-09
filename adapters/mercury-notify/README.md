# mercury-notify

Thin HTTP client for hook scripts to send notifications through the channel router.

## Role

Forwards `notify(severity, title, body)` calls to `mercury-channel-router` via HTTP POST.
Does not hold Telegram credentials or spawn processes. If the router is not running, fails silently.

**Caller scope (per Issue #316)**: emit only **user-actionable events** — events where the user has a meaningful action to take (decide next step, approve permission, acknowledge milestone). Internal agent telemetry (loop-detector stalls, hook failures, autocompact, heartbeat) is **anti-pattern** — see `adapters/mercury-channel-router/README.md` "Acceptable Callers".

## Usage

```js
const { notify } = require('./adapters/mercury-notify/notify.cjs');
// Example: dev-pipeline announcing pipeline completion (user decides next step)
await notify('info', 'Dev pipeline complete: 369-notify-wire', 'verdict=pass | files=4 | branch=feat/369-notify-wire');
```

For shell callers, prefer the `scripts/notify-event.sh` wrapper:

```bash
bash scripts/notify-event.sh info "Dev pipeline complete: 369" "verdict=pass | files=4 | branch=feat/369-notify-wire"
```

## Startup

No startup needed. `require` directly from any skill/hook emitting a user-actionable event. The router must be running separately (spawned automatically by `mercury-channel-client` when a Claude Code session starts).

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
