# mercury-channel-router

Long-running Telegram bot + IPC server. One instance per machine.

## Role

- Holds the single Telegram bot polling connection (one connection per bot token).
- Exposes a localhost HTTP IPC server for `mercury-channel-client` and `mercury-notify` to communicate with.
- Routes inbound Telegram messages to the correct Claude Code session.
- Handles commands: `/status`, `/list`, `/cancel`, `/continue`, `/help`.
- Enforces sender allowlist and session limit (default 5 — see `MERCURY_ROUTER_MAX_SESS` override below; lifted from 3 in Phase C #324 to match `feedback_lane_protocol.md` HARD-CAP).

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
| `MERCURY_NOTIFY_DISABLED` | No | Truthy → disables Telegram polling entirely (IPC still works). Accepted truthy values (case-insensitive, whitespace trimmed): `1`, `true`, `yes`, `on`. Any other value (incl. `0`, `false`, `no`, `off`, empty, unset) leaves Telegram enabled. See Issue #298. |
| `MERCURY_ROUTER_MAX_SESS` | No | Override the in-router session cap (default 5; Phase C #324). Non-finite or non-positive values fall back to the default. |

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

## Acceptable Callers (notify endpoint)

The `/notify` IPC endpoint is for **user-actionable events only** — events where the user on Telegram has a meaningful action to take (decide next step, approve permission, acknowledge milestone).

**✅ Acceptable**:
- Dev Pipeline task complete → user decides next step (continue / cancel / handoff)
- Handoff session-switch → user is aware new tab spawned, can switch focus
- Permission relay → user approves/denies tool call
- Critical security event → token leak attempt, unusual sender

**❌ Anti-patterns (never wire to /notify)**:
- `loop-detector` stall events → agent self-consumes (writeStallReport file is the agent-internal channel) — see Issue #316
- Hook script failures → agent self-recovers
- Autocompact / heartbeat / periodic state events
- Any event where the user has no actionable response on Telegram

Rationale: confusing internal agent telemetry with user-facing notifications floods the channel and trains the user to ignore it. Reserve Telegram for events that genuinely require human attention.

## Verdict Replies (permission requests)

When the router relays a `permission-request` to Telegram, the message shows a **prefixed request id** in the form `<6char-session-prefix>-<5char-request-id>` (e.g. `a1b2c3-defgh`). Verdict replies MUST quote that full prefixed id:

```
yes a1b2c3-defgh
no  a1b2c3-defgh
```

Bare unprefixed verdicts (`yes defgh`) are rejected with a usage hint — the prefix lets the router resolve the verdict back to the correct session when multiple lanes are concurrent. See Issue #304 nit 5.

## Notes

- Bun is optional — Node 20+ required (per repo `package.json` engines + Phase 5 ADR §9.1).
- Lock file at `~/.mercury/router.lock` prevents duplicate instances. Telegram polling does not start until the lock is held (#304 nit 4) — a second router process exits via `EADDRINUSE` before it can race the first one on Telegram's getUpdates queue.
- Requires `grammy` (installed via pnpm at project root). Issue #302 swapped from `node-telegram-bot-api@0.67.0` to drop the deprecated `request@2.88.2` transitive dependency stack.
- Long Telegram messages are truncated via `lib/truncate.cjs` to keep within the 4096-UTF-16-code-unit cap while preserving surrogate pairs (#300).
- `MERCURY_NOTIFY_DISABLED` env flag is parsed via `lib/env.cjs` strict truthy helper to match the `=== '1'` convention used elsewhere in Mercury (`mercury-loop-detector`, `mercury-test-gate`); see Issue #298.
- Magic numbers (shutdown grace, lock retries, tgSend retry budget) are named constants near the top of `router.cjs` per Issue #304 nit 1.
