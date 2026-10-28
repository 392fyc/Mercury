# Phase A Install Guide — Cross-Lane Aggregator

Issue #322 / #320 · Phase A · `.mercury/packs/lanes/scripts/lane-status.sh`

> The first half of Phase A, the `statusline-mercury.sh` status bar script with its 5h quota marker, was
> removed from the repository (#675). This guide covers the remaining half, the lane status aggregator.

---

## Prerequisites

- `jq` installed and on PATH
- `gh` CLI authenticated (`gh auth status`)
- Claude Code CLI installed
- Mercury repo cloned, working tree clean

---

## Install lane-status.sh

No symlink needed — runs from repo root via cron or manually.

### Manual run

```bash
bash .mercury/packs/lanes/scripts/lane-status.sh --print
# Compact summary table printed to stdout.
# .mercury/state/lane-status.json written.
```

Inspect output:

```bash
jq '.' .mercury/state/lane-status.json
```

Each lane also carries a `registry` object read from the project's
`LANES.md` (Issue #616). It always has the keys `short`, `harness`
(lower-cased; default `claude`), `session`, `peers` (list), `status`,
`branch`, `worktree`, `handoff` and `inbox`; a field missing from
`LANES.md` is `null`. Each value is the first backticked group of the
field's bullet, so notes such as `(per Rule 5.1)` or a trailing comment
are ignored. Registered lanes without a `lane:*` label are listed too;
labelled lanes that are not registered get `registry: null`.
Branches ending with the lane's short name (`<type>/<purpose>-<short>[-<agent>]`,
Rule 2.1, longest registered name wins) count; without a registered short name the
lane name is used, as `lane-assertion.sh` does. The registry is `--lanes-file PATH`,
else `MERCURY_LANES_FILE`, else the project's lane home
(`.mercury/packs/lanes/scripts/lane-paths.sh lanes-file`). A missing registry only warns and
leaves `registry: null`.

### Cron registration (via Claude Code `CronCreate` tool)

**Do NOT register from the script** — registration is user-driven via Claude Code:

```text
CronCreate:
  cron: "*/5 * * * *"
  durable: true
  prompt: |
    Run .mercury/packs/lanes/scripts/lane-status.sh from the Mercury repo root ($REPO_ROOT).
    If lane-status.json shows any is_stale: true lanes, append a line to
    .mercury/state/stale-lanes.log with ISO timestamp and lane name.
```

> `durable: true` is **mandatory** — without it the cron silently disappears after session restart,
> and the lane-status checks stop after a restart.

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

`lane-status.sh` installs nothing outside the repository. To undo it, remove the `CronCreate`
registration (use `CronDelete` inside Claude Code) and delete the generated state file:

```bash
rm -f .mercury/state/lane-status.json .mercury/state/stale-lanes.log
```

---

## Acceptance Verification Matrix

| # | Criterion (Issue #320) | Verification command |
|---|-------------------------------|----------------------|
| 1 | `lane-status.json` updates with `last_checked_at` ISO timestamp | `bash .mercury/packs/lanes/scripts/lane-status.sh && jq .last_checked_at .mercury/state/lane-status.json` |
| 2 | durable cron registered (survives session restart) | After `CronCreate durable:true`, restart Claude Code session, then verify cron still listed |

---

## Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MERCURY_LANE_STALE_MIN` | `15` | Minutes after which a lane is considered stale |
| `MERCURY_LANES_FILE` | (project lane home) | `LANES.md` that `lane-status.sh` reads; `--lanes-file` wins over it (#616) |
| `CLAUDE_PROJECT_DIR` | (injected by Claude Code) | Repo root override for `lane-status.sh` |
| `MERCURY_TEST_REPO_ROOT` | (unset) | Test isolation override for `lane-status.sh` |
