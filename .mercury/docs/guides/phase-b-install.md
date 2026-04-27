# Phase B Install Guide — Lane Lifecycle Scripts

Issue [#323](https://github.com/392fyc/Mercury/issues/323) · Phase B ·
`scripts/lane-spawn.sh` + `scripts/lane-close.sh` (`--close-issue`) +
`scripts/lane-sweep.sh` (cron)

This guide is the operator's quick-ref for the three lane-lifecycle
scripts. Companion to [`phase-a-install.md`](./phase-a-install.md)
(observability + status aggregator).

## Prerequisites

- `bash` ≥ 4.0 (Git Bash on Windows is fine)
- `jq` and `gh` on PATH
- `gh auth status` clean
- Mercury repo cloned, `origin/develop` reachable

## Module overview

| ID | Script | Purpose | Default exit |
|----|--------|---------|--------------|
| B1 | `scripts/lane-spawn.sh`  | Atomic lane creation: claim Issue + branch + handoff + LANES.md row | 0 = spawned |
| B2 | `scripts/lane-close.sh`  | Atomic lane teardown: Status flip + tmp prune (+ optional Issue close) | 0 = closed |
| B3 | `scripts/lane-sweep.sh`  | Stale-lane detection (REPORT-ONLY) | 0 = report emitted |

Detailed per-script docs live in
[`lane-spawn.md`](./lane-spawn.md), [`lane-close.md`](./lane-close.md),
and [`lane-sweep.md`](./lane-sweep.md).

## B1 — Spawning a new lane

```bash
# Standard spawn (default short = first 8 chars of lane, slug from Issue title)
scripts/lane-spawn.sh <lane-name> <issue-number>

# With explicit short + slug
scripts/lane-spawn.sh side-feature-x 350 --short feat-x --slug "feature-x"

# Dry-run
scripts/lane-spawn.sh side-feature-x 350 --dry-run
```

After spawn:

- `lane:<name>` label applied to the Issue (assignee NOT auto-set —
  `lane-spawn.sh` passes `--no-assignee` to `lane-claim.sh` per Issue
  #317 Copilot iter 2 contention-avoidance; assign manually if you want
  GitHub UI ownership)
- Branch `lane/<short>/<issue>-<slug>` created (off `origin/develop`)
- Handoff template at `<memory-dir>/session-handoff-<lane>.md`
- New section appended to `LANES.md` (own section per Rule 6)

Switch to the branch and start work:

```bash
git switch lane/<short>/<issue>-<slug>
```

## B2 — Closing a lane

Default close (no GitHub Issue mutation):

```bash
scripts/lane-close.sh <lane-name> --yes
```

Close with associated Issue closure (#323 spec):

```bash
scripts/lane-close.sh <lane-name> --yes \
  --close-issue --issue 350 \
  --rationale "Phase B B1 + B2 enhancement landed via PR #XYZ. Closing per #323 acceptance."
```

Behavior:

- Local Status flip + tmp prune always happen first (atomic, rolled back
  only on safety guard failure).
- `--close-issue` is best-effort: it posts a closure rationale comment then
  `gh issue close --reason completed`. Failure here emits `WARN` but does
  **NOT** flip the exit code — local state is already authoritative.
- `--rationale` defaults to a generic line if omitted.

## B3 — Detecting stale lanes (cron)

`lane-sweep.sh` is **report-only**: it never mutates `LANES.md`. This is
intentional per Rule 6 (LANES.md section ownership) — auto-flipping
another lane's section would be a backdoor. Stale verdicts surface in the
report; the owning lane decides whether to flip its own section to
`closed` (using `lane-close.sh`).

> **Note on #323 spec wording:** the original Issue body says "Auto-flip
> status to `stale` in `LANES.md`". Rule 3.1 (v0.1 Delta 2 / #310, merged
> via PR #327) supersedes that wording with a REPORT-ONLY contract to
> respect Rule 6 red line. This Phase B install path follows the merged
> Rule 3.1 implementation, not the original #323 wording.

### Manual run

```bash
# Default 14-day threshold, text table
scripts/lane-sweep.sh

# JSON for piping
scripts/lane-sweep.sh --format json | jq '.lanes[] | select(.verdict == "stale")'
```

### Cron registration (via Claude Code `CronCreate`)

Per [`phase-a-install.md`](./phase-a-install.md) §A2 cron pattern:

```text
CronCreate:
  cron: "0 9 1 * *"          # 09:00 UTC, 1st of every month
  durable: true               # MANDATORY — survives session restart
  prompt: |
    Run scripts/lane-sweep.sh --format json from the Mercury repo root.
    Append any verdict=stale lanes (one line per lane, ISO timestamp prefix)
    to .mercury/state/stale-lanes.log. Do NOT modify LANES.md.
```

> `durable: true` is **mandatory**. Without it the cron silently disappears
> after session restart, opening detection gaps.

Verify the cron survived restart by listing crons inside Claude Code:

```text
CronList → confirm the lane-sweep entry appears.
```

`stale-lanes.log` is the operator's signal — open the lane's GitHub Issue
or the lane's handoff for context, then run `lane-close.sh <lane> --yes`
when ready to retire.

## Verification matrix

| # | Module | Verification command | Expected |
|---|--------|----------------------|----------|
| 1 | B1 | `bash scripts/test-lane-spawn.sh` | `31 pass / 0 fail` |
| 2 | B2 | `bash scripts/test-lane-close.sh` | `42 pass / 0 fail` (includes `--close-issue` cases) |
| 3 | B3 | `bash scripts/test-lane-sweep.sh` | all green |
| 4 | B1 dry-run | `scripts/lane-spawn.sh demo 999 --dry-run --no-claim --slug x` | exit 0, prints intent, no FS mutation |
| 5 | B2 `--close-issue` dry-run | `scripts/lane-close.sh foo --dry-run --close-issue --issue 1` | exit 0, `would gh issue comment` printed |
| 6 | B3 `--no-issue-check` | `scripts/lane-sweep.sh --no-issue-check --memory-dir <fixture>` | exit 0, table or JSON |
| 7 | Cron durability | After `CronCreate durable: true`, restart Claude Code, `CronList` | sweep cron still listed |

## Rollback

The Phase B scripts are independent of Phase A. To roll back Phase B,
prefer reverting the merge commit so history is portable rather than
relying on `HEAD~1` (which depends on local commit ordering and would
not point at "pre-Phase-B" once subsequent merges land):

```bash
# Identify the Phase B merge commit on the develop branch
PHASE_B_MERGE=$(git log develop --merges --grep='Phase B' --format='%H' | head -n 1)

# Create a revert commit (preferred — preserves history, works on any branch state)
git revert -m 1 "$PHASE_B_MERGE"

# Drop the B3 sweep cron (inside Claude Code)
# CronDelete <id>
```

If `git revert` is not viable (e.g. the merge has already been built upon),
fall back to manually deleting the Phase B files and reverting the
`lane-close.sh` `--close-issue` block — `git log -p -- scripts/lane-close.sh`
shows which lines belong to Phase B vs the prior PR #327 baseline.

Operators who installed Phase A can keep it — Phase A and Phase B touch
different surfaces (statusline + lane-status.json vs lane lifecycle).

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `MERCURY_MEMORY_DIR` | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory` | Override memory dir for all three scripts |
| `GH_REPO` | (resolved via `gh repo view`) | Pin GitHub repo for `gh` calls |
| `MERCURY_LANE_STALE_MIN` | `15` | (Phase A) staleness gate for `lane-status.sh` — unrelated to B3's `--days` |

## Source references

- Issue [#323](https://github.com/392fyc/Mercury/issues/323) — Phase B spec
- Issue [#310](https://github.com/392fyc/Mercury/issues/310) — Rule 3.1
  REPORT-ONLY contract (resolves #323 wording conflict)
- Issue [#311](https://github.com/392fyc/Mercury/issues/311) — Rule 3.2
  lane-close
- Issue [#314](https://github.com/392fyc/Mercury/issues/314) — HARD-CAP=5
- PR [#321](https://github.com/392fyc/Mercury/pull/321) — research §Dim
  1.3 missing modules
- [`phase-a-install.md`](./phase-a-install.md) — sibling install guide for
  observability + lane status aggregator
