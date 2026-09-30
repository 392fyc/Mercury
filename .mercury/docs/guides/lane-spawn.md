# Lane Spawn Ceremony — `scripts/lane-spawn.sh`

Implements **#323 Phase B B1** (atomic lane creation). Companion to
[`lane-claim.sh`](./lane-claim.md) (Rule 1.1 / Issue #309) and
[`lane-close.sh`](./lane-close.md) (Rule 3.2 / Issue #311).

## Why

v0 had no script for opening a lane — the operator manually filed an Issue,
applied `lane:<name>` label, picked a branch name, wrote a handoff template,
and edited `LANES.md`. Five steps means five ways to drift, and the
handoff/LANES.md edit was easy to forget. Phase B makes the four-step
ceremony atomic from a single command.

## What it does (in order)

| # | Step | Source of truth | Failure semantics |
|---|------|-----------------|-------------------|
| 1 | Validate args (`<lane>`, `<issue>`, optional `--short`/`--slug`/`--harness`) | local | exit 2 if invalid; nothing mutated |
| 2 | Refuse if `<lane>` already in `LANES.md` Active Lanes | `LANES.md` | exit 1; nothing mutated |
| 3 | *(removed)* No lane-count cap since Issue #605 (the former Delta 7 HARD-CAP) | — | — |
| 4 | Refuse if `<short>` already in use by another active lane | `LANES.md` | exit 1; nothing mutated |
| 5 | Claim Issue via `lane-claim.sh` (Rule 1.1 probe-after-write) | GitHub | exit 1 on conflict; later steps skipped |
| 6 | Create branch `lane/<short>/<issue>-<slug>` off `origin/develop` | local git | exit 1 on existing branch / missing origin/develop; later steps skipped |
| 7 | Write per-lane handoff template (refuses to overwrite) | user-memory | exit 1 if file exists; LANES.md NOT touched |
| 8 | Append new section to `LANES.md` (own section per Rule 6) | `LANES.md` | exit 2 on awk failure; manual edit advised |

Steps 5–8 mutate state. Steps 1–4 are pure validation. The split is
intentional so a `--dry-run` can fully exercise validation without external
side-effects.

## First-time setup on a machine

All lane scripts read `LANES.md` from the project's lane home (`MERCURY_MEMORY_DIR`, else
`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded main checkout>/memory`; print it with
`scripts/lane-paths.sh lanes-file`, Issue #613). Every project has its own. On a machine
that has none, they exit with "memory dir not found" or "LANES.md not found". Create a valid registry once
from inside the Mercury checkout (Issue #607):

```bash
scripts/lane-init.sh                      # main lane driven by Claude Code
scripts/lane-init.sh --main-harness codex # or by Codex
scripts/lane-init.sh --dry-run            # preview only
```

It creates the memory dir if needed and writes `## Active Lanes` with the `main`
lane (its `Worktree path` is the main checkout, even when run from a linked lane
worktree) plus `## Closed Lanes`. An existing `LANES.md` is never modified, and a
symlink or non-regular file at that path is refused. Tests: `scripts/test-lane-init.sh`.

## Usage

```bash
scripts/lane-spawn.sh <lane> <issue>
                      [--short SHORT] [--slug SLUG]
                      [--memory-dir PATH] [--lanes-file PATH]
                      [--repo-root PATH] [--repo OWNER/REPO]
                      [--harness claude|codex] [--no-claim] [--no-branch]
                      [--dry-run] [--yes]
```

| Flag / Env var | Effect |
|----------------|--------|
| `<lane>` (positional) | Lane name. `[A-Za-z0-9_-]+`. |
| `<issue>` (positional) | GitHub Issue number to claim. Positive integer. |
| `--short SHORT` | Override short branch prefix (Rule 2.1, ≤8 chars, `[a-z0-9-]+`). Default: lane name lowercased + filtered + truncated. |
| `--slug SLUG` | Override branch slug. Default: derived from Issue title via `gh issue view`. |
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then the project's lane home `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded main checkout>/memory` (`scripts/lane-paths.sh memory-dir`; `D--Mercury-Mercury` for `D:/Mercury/Mercury`; #613). |
| `--lanes-file PATH` | Override LANES.md location (default: `<memory-dir>/LANES.md`). |
| `--repo-root PATH` | Override repo root (default: `git rev-parse --show-toplevel`). |
| `--repo OWNER/REPO` | Pin GitHub repo for `gh` calls. Defaults to `gh repo view` / `GH_REPO`. |
| `--handoff-dir PATH` | Where the handoff template is written. Default: the project's handoff dir (`scripts/lane-paths.sh handoff-dir`: `<kb_dir>/handoff` per the main checkout's `.handoff-config`, else `<main checkout>/.handoff`; #613). Falls back to `<memory-dir>` when the project cannot be resolved. |
| `--harness claude\|codex` | Which CLI drives the lane; written as `- **Harness**:` in the new `LANES.md` section. Default: `claude`. See the [#599 ADR](../research/issue-599-cross-harness-lane-isolation-2026-09.md) D1. |
| `--no-claim` | Skip the `lane-claim.sh` step (useful for offline/manual claim). |
| `--no-branch` | Skip the `git branch` step (useful when branch is created elsewhere). |
| `--dry-run` | Print intended actions and exit before any state-mutating command. **Note**: `gh repo view` and `gh issue view` may still be invoked *before* the dry-run gate to resolve repo target and derive the slug from the Issue title. To stay fully offline, combine with `--no-claim --slug <slug>` (and `--repo OWNER/REPO` if `gh repo view` is unavailable). |
| `--yes` | Skip the interactive confirm prompt. Required in non-interactive contexts. |
| `MERCURY_MEMORY_DIR` (env) | Same as `--memory-dir`. |
| `GH_REPO` (env) | Same as `--repo`. |

Example: open a Codex-driven lane (solo by default; see the [#599 ADR](../research/issue-599-cross-harness-lane-isolation-2026-09.md) for pairing):

```bash
scripts/lane-spawn.sh art 612 --short art --harness codex
```

### Branch naming (Rule 2.1)

Default form: `lane/<short>/<issue>-<slug>`, total length capped at **40
chars**. If the auto-derived slug would overflow, it is truncated and
trailing hyphens stripped. Example:

```text
lane=newlane issue=300 short=newl slug="phase-b-lane-lifecycle-cleanup-pass"
→ lane/newl/300-phase-b-lane-lifecycle  (40 chars after truncation)
```

If the prefix `lane/<short>/<issue>-` itself exceeds 40 chars (e.g. very
long short name + 6-digit issue number), the script exits 1 and asks for a
shorter `--short`.

### Section ownership (Rule 6)

Step 8 only **appends** a new section to `LANES.md`. It never modifies
existing sections — Rule 6 binds the spawn ceremony exactly as it binds
`lane-close.sh`. The append-point is "immediately before the next `## `
header" (typically `## Closed Lanes`); if no such header is found, the
section is appended at end-of-file.

### Handoff overwrite protection

Step 7 refuses to write if `session-handoff-<lane>.md` already exists in
the handoff dir (`--handoff-dir`, else `scripts/lane-paths.sh handoff-dir`)
or as a legacy copy in `<memory-dir>`. This protects in-flight session state from being clobbered
by an accidental re-spawn under the same lane name. To re-spawn, archive
or delete the prior handoff first.

### Exit codes

| Exit | Meaning |
|------|---------|
| `0` | Spawn succeeded, or `--dry-run` path completed. |
| `1` | Validation failed: lane already exists / short collision / Issue not found / handoff exists / aborted at confirm prompt. Local state may be partially mutated when failure occurs **after** step 5 — read the script output to see exact stop point. |
| `2` | Argument error / missing `gh` or `jq` / cannot resolve repo or memory dir / awk LANES.md edit failure. |

## Examples

```bash
# Standard spawn — claim Issue, derive slug from title, branch off origin/develop
scripts/lane-spawn.sh newlane 300

# Pin short name and slug explicitly (bypass Issue title fetch)
scripts/lane-spawn.sh newlane 300 --short newl --slug "phase-b-test"

# Dry-run preview (no FS / git / Issue mutation; gh repo+issue read-only metadata still queried)
scripts/lane-spawn.sh newlane 300 --dry-run

# Fully-offline dry-run (no gh at all — pre-pin --slug and --repo)
scripts/lane-spawn.sh newlane 300 --dry-run --no-claim --slug "phase-b-test" --repo OWNER/REPO

# Offline spawn — branch + handoff + LANES.md only, no Issue claim
scripts/lane-spawn.sh newlane 300 --no-claim --short newl --slug "x"

# Spawn from outside the Mercury checkout (CI)
scripts/lane-spawn.sh newlane 300 \
  --repo-root /workspace/Mercury \
  --memory-dir /workspace/memory \
  --repo 392fyc/Mercury \
  --yes
```

## Tests

```bash
scripts/test-lane-spawn.sh
```

Runs offline (no `gh`, no live LANES.md, no live git) and exercises:

- Argument validation (missing args, invalid lane / issue / flag)
- Dry-run exits 0 + does not mutate LANES.md / handoff file
- Duplicate-lane refusal (`lane` already in Active Lanes)
- Short-name collision refusal (cross-lane uniqueness)
- No lane-count cap: spawning still succeeds when many lanes are already active (#605)
- `--harness`: `codex` recorded (both the mid-file and end-of-file insert paths), default `claude`, invalid or missing value rejected, shown in `--dry-run`
- Happy-path with `--no-claim --no-branch`: handoff written, LANES.md
  appended, other lanes' Status untouched
- Handoff overwrite guard: refuses + LANES.md NOT mutated
- Non-interactive without `--yes` → exit 1
- Auto-derived `--short` from lane name
- Long slug truncated to keep branch ≤40 chars

## Source references

- Issue [#323](https://github.com/392fyc/Mercury/issues/323) — Phase B
  acceptance criteria
- Issue [#309](https://github.com/392fyc/Mercury/issues/309) — Rule 1.1
  probe-after-write (B1 dependency, merged via PR #317)
- Issue [#313](https://github.com/392fyc/Mercury/issues/313),
  [#314](https://github.com/392fyc/Mercury/issues/314) — Rule 2.1 short
  prefix + HARD-CAP=5 (merged via PR #328)
- `feedback_lane_protocol.md` — full protocol (v0 + v0.1 + v0.2)
- [`lane-claim.md`](./lane-claim.md), [`lane-close.md`](./lane-close.md),
  [`lane-sweep.md`](./lane-sweep.md) — sibling ceremonies
