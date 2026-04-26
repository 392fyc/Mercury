# Lane Stale Sweep — `scripts/lane-sweep.sh`

Implements **Rule 3.1 14-day stale lane sweep** of the multi-lane protocol
(v0.1 Delta 2, Issue [#310](https://github.com/392fyc/Mercury/issues/310)).

## Why

Lanes can quietly stop progressing — owner moved on, scope was descoped, or
the work blocked on an external dependency that never resolved. v0 had no
mechanism to detect this; orphaned lanes accumulate in `LANES.md` and confuse
new sessions about which work is live.

Claude Code 2.1.76 added native stale worktree detection (7+ day threshold)
after the 222-workspace + 8-agent same-file-write incidents. Mercury Rule 3.1
gives us an analogous signal at the lane layer.

## What "stale" means

A lane is **stale** if and only if **all three** of the following are true at
the same time (default threshold: 14 days):

| Signal | Source | Stale criterion |
|--------|--------|-----------------|
| Branch activity | `git for-each-ref` on `feature/lane-<lane>/*` (and legacy `feature/TASK-*` for main) | newest committerdate > 14d ago, or no matching refs |
| Handoff activity | mtime of `<memory-dir>/session-handoff[-<lane>].md` | mtime > 14d ago, or file missing |
| Issue activity | `gh issue list --label "lane:<lane>" --state all` newest `updatedAt` | > 14d ago, or no matching Issues |

Each signal is independently classified `fresh` or `stale`. Lane verdict is
`stale` only when **all three signals are stale simultaneously**.

The 14d threshold is a default. Use `--days 30` for a more conservative sweep,
or `--days 7` to mirror Claude Code's worktree threshold.

## Usage

```bash
scripts/lane-sweep.sh [--lanes-file PATH] [--memory-dir PATH]
                      [--days N] [--repo OWNER/REPO] [--repo-root PATH]
                      [--format text|json] [--no-issue-check]
```

| Flag / Env var | Effect |
|----------------|--------|
| `--days N` | Override the staleness threshold (default 14). |
| `--format text\|json` | Output format (default `text` — table). |
| `--lanes-file PATH` | Override LANES.md location. Defaults to `<memory-dir>/LANES.md`. |
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`. |
| `--repo OWNER/REPO` | Pin the GitHub repo for Issue activity probe. Defaults to `gh repo view` resolution. |
| `--repo-root PATH` | Pin the local checkout for `git for-each-ref` branch-activity probing. Defaults to `git rev-parse --show-toplevel` from cwd; pass explicitly when invoking the script outside the Mercury checkout (CI, cron from a different cwd). |
| `--no-issue-check` | Skip the GitHub Issue probe (offline mode / fast tests). |
| `MERCURY_MEMORY_DIR` (env) | Same effect as `--memory-dir`. |
| `GH_REPO` (env) | Same effect as `--repo`. |

### Report-only — Rule 6 compliance

The script **never mutates `LANES.md`**. Status flips remain the responsibility
of the owning lane (Rule 6 ownership). The script's contract is:

> Here are the lanes that meet the stale criteria. The owning lane should
> decide whether to flip its own section to `closed`.

This matches the `feedback_lane_protocol.md` constraint that "only the owning
lane edits its own section". Auto-edit would create a backdoor for one lane
to modify another lane's metadata.

### Exit codes

| Exit | Meaning |
|------|---------|
| `0` | Report emitted successfully, regardless of whether any lanes were stale. |
| `2` | Invalid args, missing `gh`/`jq`, cannot resolve memory dir or repo target. |

The exit code does NOT signal "any lane is stale" — that would conflate
report success with editorial verdict. To act on the verdict, parse the
output (`--format json` is recommended for machine consumption).

## Examples

```bash
# Default: 14-day threshold, text table, live Issue probe
scripts/lane-sweep.sh

# Conservative 30-day threshold, JSON for piping
scripts/lane-sweep.sh --days 30 --format json | jq '.lanes[] | select(.verdict == "stale")'

# Offline run — no GitHub probe, useful in CI without gh auth
scripts/lane-sweep.sh --no-issue-check

# Custom memory dir for testing
scripts/lane-sweep.sh --memory-dir ./test-fixtures/memory --days 7 --no-issue-check
```

### Sample text output

```
LANE                   BRANCH_AGE   HANDOFF_AGE   ISSUE_AGE   VERDICT
main                   0d           0d            0d          fresh
side-multi-lane        0d           0d            0d          fresh
side-experiment        45d          38d           52d         stale
```

## Recommended cadence

- **Manual**: run before opening a new lane to confirm capacity (Rule 7 cap-5).
- **Cron**: monthly is sufficient for v0.1 — the threshold is 14d, the cap is 5
  lanes, and the worst-case cost of a missed sweep is operator confusion (not
  data loss).

A cron registration is **not** committed by default. Operators who want
unattended sweeping can install a scheduled job manually:

```bash
# Linux: crontab -e (1st of every month, 9am UTC)
0 9 1 * * cd "$HOME/Mercury" && bash scripts/lane-sweep.sh --format json > /var/log/mercury-lane-sweep.json
```

```powershell
# Windows: register a Scheduled Task. Substitute <REPO_ROOT> with your local
# Mercury checkout (the script must be invoked via bash on Windows).
schtasks /Create /SC MONTHLY /MO 1 /D 1 /TN "MercuryLaneSweep" /TR "bash <REPO_ROOT>\scripts\lane-sweep.sh"
```

The cron is intentionally not pre-baked because lane operators self-host on
heterogeneous environments (Mercury runs on Windows + Linux + macOS). A repo
script that picks a host-specific scheduler would violate the Mercury
"don't hardcode deployment-specific tools" feedback rule.

## Tests

```bash
scripts/test-lane-sweep.sh
```

Builds a synthetic memory dir + LANES.md fixture and exercises:

- Argument validation (missing values, invalid `--days`, invalid `--format`)
- Active-lane parsing (3 active + 1 closed → only active appear)
- Fresh handoff → `fresh` verdict (one signal defeats the all-three rule)
- Stale handoff (60d-old mtime) → `stale` verdict (all signals satisfied)
- JSON format produces parseable output with required fields
- Empty Active Lanes section → exit 0 with WARN

Tests run offline (use `--no-issue-check`); safe in CI.

## Source references

- Issue [#310](https://github.com/392fyc/Mercury/issues/310) — acceptance criteria
- [v0.1 Delta companion](../lane-protocol-v0.1-deltas.md#delta-2--rule-31-stale-lane-sweep-p1) — full rationale
- [DOCS Worktree cleanup recovery (claude-code#34282)](https://github.com/anthropics/claude-code/issues/34282)
- [Stale worktrees never cleaned up (claude-code#26725)](https://github.com/anthropics/claude-code/issues/26725)
