# Phase A Install Guide — 5h Observability + Cross-Lane Aggregator

Issue #322 / #320 · Phase A · `scripts/statusline-mercury.sh` + `scripts/lane-status.sh`

---

## Prerequisites

- `jq` installed and on PATH
- `gh` CLI authenticated (`gh auth status`)
- Claude Code CLI installed
- Mercury repo cloned, working tree clean

---

## A1 — Install statusline-mercury.sh

### Step 1 — Symlink into Claude config dir (idempotent)

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
ln -sf "$REPO_ROOT/scripts/statusline-mercury.sh" "$CLAUDE_DIR/statusline-mercury.sh"
```

Verify the symlink:

```bash
ls -la "$CLAUDE_DIR/statusline-mercury.sh"
# → should point to scripts/statusline-mercury.sh inside the repo
```

### Step 2 — Merge settings.json snippet

> **WARNING — back up first** (per Mercury user-level change governance in CLAUDE.md):

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
cp "$CLAUDE_DIR/settings.json" "$CLAUDE_DIR/settings.json.backup-pre-322"
```

Add the following `statusLine` block to `$CLAUDE_DIR/settings.json`. Merge manually into the
existing JSON object — do **not** overwrite the whole file.

```json
{
  "statusLine": {
    "type": "command",
    "command": "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/statusline-mercury.sh",
    "refreshInterval": 60
  }
}
```

Validate JSON after merging (compute the path in shell so bash `${VAR:-default}`
expansion happens BEFORE Python sees it — Python's `os.path.expandvars` does NOT
understand `${VAR:-default}` syntax):

```bash
SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
python -c "import json, sys; json.load(open(sys.argv[1]))" "$SETTINGS" \
  && echo "JSON valid"
```

### Step 3 — Verify

After restarting Claude Code, the status bar should display:

```
5h: 42% | 7d: 18% | ctx: 12% | Opus 4.7
```

Manual smoke test (no Claude Code session needed):

```bash
# Use a far-future resets_at so the marker stores a realistic epoch (not 0).
echo '{"rate_limits":{"five_hour":{"used_percentage":96,"resets_at":9999999999},"seven_day":{"used_percentage":20}},"model":{"display_name":"Opus 4.7"},"context_window":{"used_percentage":30}}' \
  | bash scripts/statusline-mercury.sh
# → red-colored "5h: 96% | 7d: 20% | ctx: 30% | Opus 4.7"
# → .mercury/state/auto-run-paused file created
cat .mercury/state/auto-run-paused
# → 9999999999  (resets_at echoed from input JSON)
rm -f .mercury/state/auto-run-paused
```

---

## A2 — Install lane-status.sh

No symlink needed — runs from repo root via cron or manually.

### Manual run

```bash
bash scripts/lane-status.sh --print
# Compact summary table printed to stdout.
# .mercury/state/lane-status.json written.
```

Inspect output:

```bash
jq '.' .mercury/state/lane-status.json
```

### Cron registration (via Claude Code `CronCreate` tool)

**Do NOT register from the script** — registration is user-driven via Claude Code:

```text
CronCreate:
  cron: "*/5 * * * *"
  durable: true
  prompt: |
    Run scripts/lane-status.sh from the Mercury repo root ($REPO_ROOT).
    If lane-status.json shows any is_stale: true lanes, append a line to
    .mercury/state/stale-lanes.log with ISO timestamp and lane name.
```

> `durable: true` is **mandatory** — without it the cron silently disappears after session restart,
> opening quota-tracking gaps (per Issue #320 acceptance criterion 5).

Verify registration survived a session restart by listing crons inside Claude Code
itself — the GitHub API does not expose Claude Code's local cron registry:

> Inside the new Claude Code session, ask the agent to run `CronList` (built-in
> tool) and confirm the lane-status entry appears. If it is missing, the
> previous registration was not durable; re-register with `durable: true`.

A separate way to indirectly observe the cron is firing: `lane-status.json`'s
`last_checked_at` timestamp should be within the configured cron interval:

```bash
STATE_FILE="${MERCURY_TEST_REPO_ROOT:-$(git rev-parse --show-toplevel)}/.mercury/state/lane-status.json"
jq -r '.last_checked_at' "$STATE_FILE"
```

---

## Rollback

```bash
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
# Remove symlink
rm -f "$CLAUDE_DIR/statusline-mercury.sh"
# Restore settings.json
mv "$CLAUDE_DIR/settings.json.backup-pre-322" "$CLAUDE_DIR/settings.json"
# Remove any pause marker
rm -f .mercury/state/auto-run-paused
```

---

## Acceptance Verification Matrix

| # | Criterion (Issue #322 + #320) | Verification command |
|---|-------------------------------|----------------------|
| 1 | statusline shows 5h%, 7d%, ctx%, model | `echo '{"rate_limits":{"five_hour":{"used_percentage":50},"seven_day":{"used_percentage":10}},"model":{"display_name":"Test"},"context_window":{"used_percentage":5}}' \| bash scripts/statusline-mercury.sh` → output contains `5h: 50%` |
| 2 | marker created when `used_percentage >= 95`, deleted when window resets | `echo '{"rate_limits":{"five_hour":{"used_percentage":96,"resets_at":9999999999},"seven_day":{}}}' \| bash scripts/statusline-mercury.sh && ls -la .mercury/state/auto-run-paused` |
| 3 | corrupted marker (non-numeric) self-heals without crash | `echo "bad" > .mercury/state/auto-run-paused && echo '{"rate_limits":{"five_hour":{"used_percentage":50}}}' \| bash scripts/statusline-mercury.sh 2>&1 \| grep corrupted` |
| 4 | `lane-status.json` updates with `last_checked_at` ISO timestamp | `bash scripts/lane-status.sh && jq .last_checked_at .mercury/state/lane-status.json` |
| 5 | durable cron registered (survives session restart) | After `CronCreate durable:true`, restart Claude Code session, then verify cron still listed |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MERCURY_PAUSE_THRESHOLD` | `95` | 5h usage % at which auto-run pauses |
| `MERCURY_WARN_THRESHOLD` | `85` | 5h usage % at which display turns yellow |
| `MERCURY_LANE_STALE_MIN` | `15` | Minutes after which a lane is considered stale |
| `CLAUDE_PROJECT_DIR` | (injected by Claude Code) | Repo root override for statusline |
| `MERCURY_TEST_REPO_ROOT` | (unset) | Test isolation override for both scripts |
