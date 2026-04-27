# Phase C Install Guide — Auto-report + Director Command Surface

Issue #324 · Phase C · `scripts/lane-auto-report.sh` + `adapters/mercury-channel-router/router.cjs` enhancements

Subsumes #308 (`/dir`, `/model`, `/permission-mode` cmds) and #318 (lane-aware routing reframe).

---

## Prerequisites

- Phase A installed (`scripts/lane-status.sh` writes `.mercury/state/lane-status.json`)
- Phase 5 MVP installed (`mercury-channel-router` running with Telegram bot token)
- `jq` and `curl` on PATH
- Claude Code CLI (for `CronCreate`)

---

## C1 — Install lane-auto-report cron

`scripts/lane-auto-report.sh` diffs the latest `lane-status.json` against the previous
snapshot and POSTs transition notifications to the channel router's `/notify` endpoint.
Director receives unprompted Telegram updates without polling.

### Step 1 — Verify the script runs locally (dry-run)

```bash
cd /path/to/Mercury

# Ensure lane-status.json exists (run Phase A aggregator first)
bash scripts/lane-status.sh

# First invocation seeds baseline; expect "no diff on first run"
MERCURY_LANE_REPORT_QUIET=1 bash scripts/lane-auto-report.sh
# → [lane-auto-report] seeded baseline; no diff on first run

# Second invocation diffs (no real changes yet → 0 emits)
MERCURY_LANE_REPORT_QUIET=1 bash scripts/lane-auto-report.sh
# → [lane-auto-report] emitted 0 notifications
```

### Step 2 — Run the test suite

```bash
bash scripts/test-lane-auto-report.sh
# → 15/15 PASS (covers diff transitions, atomic promote, lock contention,
#               POST-failure retry semantics)
```

### Step 3 — Register cron via Claude Code `CronCreate`

Use `durable: true` so the schedule survives session restarts (per
`feedback_cron_safety.md`). Off-`:00` minute jitter (`2-59/5`) avoids fleet
collision per `feedback_cron_interval_argus.md`.

In a Claude Code session, run:

```
CronCreate({
  schedule: "2-59/5 * * * *",
  durable: true,
  prompt: "Run `bash scripts/lane-status.sh && bash scripts/lane-auto-report.sh` from the Mercury repo root and report the emit count."
})
```

The first cron tick re-seeds the baseline; subsequent ticks emit on real
transitions only (lane add/remove, `is_stale` flip, issue-set change, branch
commit jump, new branch on existing lane).

### Step 4 — Suppress notifications for noisy windows

Set `MERCURY_LANE_REPORT_QUIET=1` in the cron environment (or shell profile)
when running release-week soak — diffs still update the previous snapshot but
nothing posts to Telegram.

---

## C2 — Verify router lane-aware enhancements

The router enhancements are in-place edits to `adapters/mercury-channel-router/router.cjs`.
No install ceremony — restart any running router instance to pick them up.

### Step 1 — Restart the router (if running)

If the router is currently running, the lock file pins it to the old code. Stop it:

```bash
LOCK_FILE="$HOME/.mercury/router.lock"
if [ -f "$LOCK_FILE" ]; then
  PID=$(cat "$LOCK_FILE")
  kill "$PID" 2>/dev/null || true
fi
```

Restart by opening a Claude Code session in any Mercury-rooted repo that loads
the channel client MCP server (the client auto-spawns the router).

### Step 2 — Smoke checklist

From a Telegram chat with the bot (must be in `MERCURY_TELEGRAM_ALLOWED_USER_IDS`):

| Command | Expected response |
|---|---|
| `/help` | Lists `/status /list /lanes /help`, plus `/cancel`, `/continue`, `/dir`, `/model`, `/permission-mode` with usage hints |
| `/lanes` | Per-lane structured rows: `[label] active branch:feature/... sse:N` |
| `/dir @<label> /tmp/test` | `Sent /dir /tmp/test to [<label>]` (target session sees `<channel cmd="dir" path="/tmp/test">dir requested by user: path=/tmp/test</channel>`) |
| `/model @<label> sonnet` | `Sent /model sonnet to [<label>]` (target session sees `<channel cmd="model" model="sonnet">…</channel>`) |
| `/permission-mode @<label> strict` | `Sent /permission-mode strict to [<label>]` (target session sees `<channel cmd="permission-mode" mode="strict">…</channel>`) |
| `/dir` (no args) | `Usage: /dir @<label> <path>` |
| `/dir @nope foo` | `No session matching @nope` |

### Step 3 — Verify MAX_SESS bump (default 5)

The change is non-interactive — observable only when ≥4 simultaneous sessions register.

```bash
# Inspect the live router /sessions endpoint (use the IPC token from ~/.mercury/router.token)
TOKEN=$(cat "$HOME/.mercury/router.token")
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8788/sessions | jq 'length'
```

To override default 5 (e.g., for cap stress tests):

```bash
MERCURY_ROUTER_MAX_SESS=2 node adapters/mercury-channel-router/router.cjs
```

---

## Rollback

C1 (script): delete cron via `CronDelete`. The script is additive — leaving it
in place with the cron disabled has no runtime impact.

C2 (router edits): `git revert` of the Phase C commit on `develop` plus a
router restart. The `MAX_SESS` env variable is read at startup, so rolling back
the binary plus restart is sufficient.

---

## Known limitations

- **Adapter LOC**: `router.cjs` grew from 220 to ~270 LOC, exceeding Mercury's
  200-LOC adapter cap. Cleanup tracked under #303 (M6 split).
- **Router unit tests**: not added in Phase C — full test harness is M6 scope.
  Smoke checklist above is the Phase C verification surface.
- **`/dir` etc. enforcement**: the router relays the command event to the lane
  inbox and the channel client (`mercury-channel-client`) forwards `path`,
  `model`, and `mode` payloads as XML attributes on the `<channel cmd="...">`
  notification. The actual cwd / model / permission-mode change still happens
  inside the lane session — Claude Code reads the notification and either acts
  on it directly or surfaces it to the user. The router is responsible for
  *delivery*, not *enforcement*.
