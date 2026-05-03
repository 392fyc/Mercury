# Lane Naming + Capacity — `feedback_lane_protocol.md` Rule 2 & HARD-CAP

Implements the **Rule 2 short branch prefix** delta (v0.1 Delta 6, Issue
[#313](https://github.com/392fyc/Mercury/issues/313)) and the **HARD-CAP at 5
active lanes** delta (v0.1 Delta 7, Issue
[#314](https://github.com/392fyc/Mercury/issues/314)).

## Why two deltas in one guide

Both shape the lane registry: Δ6 controls how branches are named, Δ7 controls
how many lanes can exist concurrently. Operators reason about "what lane do I
open / what do I name its branch" in one mental motion; one combined guide is
shorter than two cross-referenced ones.

## Δ6 — Short branch prefix (`lane/<short>/<N>-<slug>`)

### What changed

- **OLD prefix** (still valid for backward-compat): `feature/lane-<lane>/TASK-<N>-*` — 45-65 chars
- **NEW prefix** (preferred for new work): `lane/<short>/<N>-<slug>` — ≤40 chars

Example: `feature/lane-side-multi-lane/TASK-313-314-phase-c` (51 chars) →
`lane/side-mlane/313-phase-c` (27 chars).

### Why short matters

- Community soft cap is ~50 chars (Graphite naming guide); LeanTaaS hard cap is
  28 chars. 65-char branches push past both, breaking IDE autocomplete + URL
  pasting + `gh pr` shell expansion in some terminals.
- Mercury empirically observed in S3-S5 that the legacy 51-char prefix already
  truncates in `git branch` listing on narrow terminals and forces line wraps
  in PR titles.

### Short-name convention

Each lane declares a `Short name` field in its own `LANES.md` section. The
short name MUST be:

- ≤ 8 characters (giving the rest of the branch ≥ 27 chars for `<N>-<slug>`)
- Match `[a-z0-9-]+` (lowercase + digits + hyphen only)
- Globally unique across all active + closed lanes (avoid colliding with a
  closed lane's archived branches)

Default mapping for current Mercury lanes:

| Lane name | Short name | Rationale |
|-----------|-----------|-----------|
| `main` | `main` | Already short; canonical default lane |
| `side-multi-lane` | `side-mlane` | Compress "multi-lane" → "mlane" (8 chars exact) |

For new lanes: pick a short name at lane open time and write it in the lane's
`LANES.md` section before any branch is created. If two operators independently
pick the same short name, lane-claim semantics + manual review apply (no
mechanical enforcement; collision is rare).

### Backward compatibility

- All existing `feature/lane-<lane>/...` branches and `feature/TASK-<N>-*`
  legacy main-lane branches REMAIN valid until their containing lane closes.
- New work on existing lanes MAY continue using the legacy prefix to avoid
  mid-lane branch-naming churn.
- New lanes opened after Δ6 SHOULD use the short prefix.

### Script support (current state)

`scripts/lane-sweep.sh` and `scripts/check-main-idle.sh` currently glob
`refs/heads/feature/lane-<lane>/*` for branch-activity probing. These scripts
will be extended in v0.2 to also glob `refs/heads/lane/<short>/*` once one
real lane uses the new prefix end-to-end (deferred to avoid speculative code).

If a lane ONLY has new-prefix branches and no legacy `feature/lane-*`
branches, the sweep's branch-activity signal will report "inf" until v0.2 ref
glob extension lands. This degrades gracefully — handoff mtime + Issue
activity remain valid signals, and the AND-gate verdict still requires three
stale signals before flagging stale.

## Δ7 — HARD-CAP at 5 active lanes

### Cap value

`LANES.md` MUST NOT exceed **5 active lanes** simultaneously. The cap is
declared in `feedback_lane_protocol.md` and enforced advisorily by
`scripts/lane-cap-check.sh`.

### Why 5

Three converging research bases:

- **Miller's Law** ([Laws of UX](https://lawsofux.com/millers-law/)): human
  short-term memory holds 7±2 items reliably. Lanes consume operator working
  memory (which lane is on what task, what's the latest handoff, what's
  blocked); the 7±2 lower bound (5) is a defensible ceiling.
- **Google multi-agent research**
  ([towards a science of scaling agent systems](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)):
  3-5 agents optimal; 20+ catastrophic; 39-70% reasoning performance drop at
  scale. Lanes are coordination units, not agents, but the same coordination
  cost curve applies.
- **Personal Kanban WIP limits**
  ([Atlassian Kanban WIP](https://www.atlassian.com/agile/kanban/wip-limits)):
  3-5 max parallel activities is the conventional sweet spot for sustained
  throughput vs context-switch cost.

### Resolution when cap is hit

If you want to open lane #6:

1. **Close an existing lane first.** Use `scripts/lane-close.sh <lane>` to
   flip Status to `closed` + prune `.tmp/lane-<lane>/`.
2. **OR** open a GitHub Issue with the `protocol-violation` label requesting
   a temporary cap raise. The Issue body MUST justify the raise (specific
   work that requires more parallelism, expected duration, plan to return
   below cap). User arbitrates.

### Advisory enforcement

```bash
scripts/lane-cap-check.sh [--lanes-file PATH] [--memory-dir PATH]
                          [--max N] [--format text|json]
```

| Flag | Effect |
|------|--------|
| `--max N` | Override the cap (default 5). |
| `--format text\|json` | Output format. |
| `--lanes-file PATH` | Override LANES.md location. |
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`. |
| `MERCURY_MEMORY_DIR` (env) | Same effect as `--memory-dir`. |

Exit `0` if count ≤ max, exit `1` if exceeded, exit `2` on argument or
environment errors. Verdict is reported as text or JSON; the script never
mutates LANES.md.

The script is **advisory** — running it before opening a new lane is a
discipline, not an automated gate. Pre-commit hook enforcement was considered
and rejected: side lanes cannot easily install or modify shared hooks, and
the cap is a sociotechnical limit (operator + maintainer review) rather than
a mechanical one.

### Sample text output

```
lane-cap-check: 2 active lane(s), cap=5 → within_cap
  active: main,side-multi-lane
```

When exceeded:

```
lane-cap-check: 6 active lane(s), cap=5 → exceeded
  active: main,side-mlane,side-foo,side-bar,side-baz,side-qux
  resolution: close an existing lane OR open Issue with `protocol-violation` label requesting cap raise (per feedback_lane_protocol.md HARD-CAP §)
```

### `protocol-violation` GitHub label

Defined in the Mercury repo at #314 implementation time. Color `#B60205`
(GitHub red), description: "Multi-lane protocol violation (e.g. >5 active
lanes, Rule X breach) requiring user arbitration".

Operators opening cap-raise Issues use this label so the user can triage all
protocol violations from one filter.

## Lane workspace isolation via git worktree (v0.1 Delta 9, Issue [#342](https://github.com/392fyc/Mercury/issues/342))

### Why lane-level isolation matters

Sessions launched by Claude Code store per-project state under
`~/.claude/projects/<project>/` (documented at
<https://code.claude.com/docs/en/claude-directory>). The `<project>`
subdirectory name is derived from the cwd of the `claude` invocation; the
exact slash-to-dash encoding (e.g. `D:\Mercury\Mercury` → `D--Mercury-Mercury`)
is empirically observed in this repo and not a documented contract — verify
the encoding for your platform before relying on a specific path:

```bash
# Before opening a new lane worktree at <new-path>, check what dirname
# Claude Code derives by listing existing project state dirs:
ls ~/.claude/projects/ | head -20

# After running `claude` once in the new worktree (any prompt is fine,
# even just a no-op exit), list again to confirm the dirname for the
# new cwd:
ls ~/.claude/projects/ | grep -i <expected-substring>
```

When two lanes share the same cwd (e.g. both run from `D:/Mercury/Mercury`),
they share the same project state dir, the same `MEMORY.md`, and the same
`session-handoff[-<lane>].md` set — and any SessionStart/SessionEnd hooks that
resolve project state from cwd read from the shared state.

S13-side-multi-lane (2026-04-28) hit this empirically: a `main` lane
auto-handoff invocation spawned a new `claude` session in the shared cwd,
SessionStart hook lane-blindly loaded the **side** lane's handoff (latest
mtime), and the new session executed side-lane work content under the wrong
lane identity. Work content was protocol-compliant on its own merits, but
the routing was wrong — the lane that owned the work had no record of it
running. See Issue [#342](https://github.com/392fyc/Mercury/issues/342) for
the full forensic post-mortem.

The fix: give each lane a dedicated git worktree at a distinct path so the
cwd-encoded project state directory is also distinct.

> **Path notation in this §**: examples use `<repo-root>` as a placeholder
> for the parent directory operators put their Mercury checkouts under
> (e.g. Mercury team's documented value is `D:/Mercury` per
> [`CLAUDE.md`](../../CLAUDE.md) §MUST "Install software to `D:\Program Files`,
> not C drive"; on Unix this might be `~/repos`). Concrete `D:/Mercury/...`
> paths shown later are the team's actual values; substitute your own
> `<repo-root>` when reading. The lane protocol does not impose `D:/Mercury`
> on other operators — only that each lane's `Worktree path` field in
> `LANES.md` records whatever concrete path that operator chose.

### Distinguished from task-level worktrees

This § is about **lane-level** isolation (one worktree per active lane,
long-lived). It is NOT the same scope as the **task-level** worktree spec in
[`.mercury/docs/guides/worktree-workflow.md`](worktree-workflow.md), which
covers `.worktrees/{taskId}` worktrees created by the `dev-pipeline` skill
for a single dev task and removed at PR merge.

| Scope | Path | Lifecycle | Purpose |
|-------|------|-----------|---------|
| **Lane-level** (this §) | `<repo-root>/Mercury-<short>` | Per active lane; long-lived; removed at lane close | Isolate per-cwd Claude Code project state (MEMORY / handoff / transcripts) |
| **Task-level** ([`worktree-workflow.md`](worktree-workflow.md)) | `<repo-root>/Mercury/.worktrees/{taskId}` | Per dev task; ephemeral; removed at PR merge | Isolate concurrent dev-pipeline branch checkouts |

A side lane MAY use both: live in `<repo-root>/Mercury-<short>` for its
session-state isolation, AND have `dev-pipeline` create
`.worktrees/{taskId}` inside that lane worktree for parallel dev tasks.

### Worktree path convention

| Lane | Worktree path | Notes |
|------|---------------|-------|
| `main` | `<repo-root>/Mercury` (Mercury team value: `D:/Mercury/Mercury`) | Backward-compat default; the original repo checkout |
| `<side>` | `<repo-root>/Mercury-<short>` (Mercury team value: `D:/Mercury/Mercury-<short>`) | `<short>` = the lane's `Short name` field (≤8 char, per Δ6); the operator's own `<repo-root>` choice — record concrete value in own LANES.md `Worktree path` field per Rule 5.1 |

Example: `side-multi-lane` (short name `side-mlane`) →
`<repo-root>/Mercury-side-mlane` (Mercury team value:
`D:/Mercury/Mercury-side-mlane`).

The path lives outside the main checkout so that auto-handoff invocations
launched from the lane's session can `cd` to a path that resolves to a
distinct `~/.claude/projects/<encoded-cwd>/` dir (Mercury team value:
`~/.claude/projects/D--Mercury-Mercury-<short>/`).

### Setup at lane open

After opening a new lane (LANES.md section added, short name declared):

> **Safety**: `<short>` MUST already pass Rule 2.1 validation (lowercase
> + digits + hyphen only, ≤8 chars, globally unique — see §Δ6 above).
> The Rule 2.1 character class `[a-z0-9-]+` excludes path-traversal
> sequences (`..`, `/`, `\`), shell metacharacters (`;`, `|`, `$`, backtick,
> spaces), and quote characters — making string interpolation into the
> commands below safe at the protocol level. Verify the `Short name` field
> in your lane's LANES.md section matches `^[a-z0-9-]{1,8}$` before
> proceeding.

```bash
# Prerequisite: ensure origin/develop is current to avoid basing the new
# worktree on a stale local branch (matches scripts/lane-spawn.sh convention)
git fetch origin

# Recommended: create a fresh init branch off origin/develop (avoids checkout collision)
git worktree add -b lane/<short>/init <repo-root>/Mercury-<short> origin/develop
# Mercury team example:
# git worktree add -b lane/side-mlane/init D:/Mercury/Mercury-side-mlane origin/develop
```

If you instead try `git worktree add <repo-root>/Mercury-<short> develop` and
`develop` is already checked out in the main `<repo-root>/Mercury` repo,
git refuses with "fatal: 'develop' is already used by worktree at ..." per
[git-worktree(1)](https://git-scm.com/docs/git-worktree) (a branch can only
be checked out by one worktree at a time). The `-b lane/<short>/init` form
above sidesteps the collision by creating a new branch from `origin/develop`. The
lane's actual work branches (Rule 2.1 short-prefix `lane/<short>/<N>-<slug>`)
are created later inside the worktree; the `init` branch can be deleted
once a real work branch exists.

### Operational expectation

Side-lane sessions launch with the worktree as cwd:

```bash
cd <repo-root>/Mercury-<short>
claude  # SessionStart hook now reads from ~/.claude/projects/<encoded-cwd>/
# Mercury team example:
# cd D:/Mercury/Mercury-side-mlane && claude
# → ~/.claude/projects/D--Mercury-Mercury-side-mlane/
```

This makes the per-cwd project state dir distinct from the main lane's,
which gives each lane its own (paths shown with `<encoded-cwd>` =
your platform's encoding of `<repo-root>/Mercury-<short>`):

- `~/.claude/projects/<encoded-cwd>/memory/MEMORY.md`
- `~/.claude/projects/<encoded-cwd>/memory/SESSION_INDEX.md`
- `~/.claude/projects/<encoded-cwd>/memory/sessions/`
- `~/.claude/projects/<encoded-cwd>/memory/session-handoff[-<lane>].md`
- session transcripts under `~/.claude/projects/<encoded-cwd>/` (Claude Code
  provides the exact `transcript_path` via the hook payload — see
  [hooks reference](https://code.claude.com/docs/en/hooks); do not assume a
  specific filename pattern)

Hooks that resolve project state from cwd (e.g. Mercury's user-level
SessionStart loader) start routing correctly without code change: SessionStart
input includes a `cwd` field
([documented](https://code.claude.com/docs/en/hooks)), and the hook can
resolve the corresponding `~/.claude/projects/<project>/` dir from it. Hooks
that hard-code a project path or use other resolution strategies are
unaffected by this isolation; verify your specific hook before relying on
auto-routing.

### Cleanup at lane close

`scripts/lane-close.sh` does **not** currently remove the lane worktree (it
only flips Status to `closed` + prunes `.tmp/lane-<lane>/` per Rule 3.2).
Operators run worktree cleanup manually as part of the close ceremony:

```bash
# Sanity-check the path resolves to the expected worktree before proceeding,
# especially before the --force variant:
git worktree list | grep -F "<repo-root>/Mercury-<short>"

git worktree remove <repo-root>/Mercury-<short>
# OR (if uncommitted state exists and is intentionally being discarded)
git worktree remove --force <repo-root>/Mercury-<short>

# Mercury team example:
# git worktree list | grep -F "D:/Mercury/Mercury-side-mlane"
# git worktree remove D:/Mercury/Mercury-side-mlane
```

`git worktree remove` operates on registered worktrees only (verifiable via
`git worktree list`) — it cannot remove arbitrary filesystem paths even with
`--force`, so the operation is bounded to the worktree set this repo knows
about. Combined with the Rule 2.1 `<short>` validation above, the command
shape is safe for protocol-conforming inputs.

The `~/.claude/projects/<encoded-cwd>/` user-memory dir is preserved as
audit trail; operators may archive it manually if no longer needed.

### Backward compatibility

- `main` lane keeps `<repo-root>/Mercury` as its worktree path (Mercury
  team value: `D:/Mercury/Mercury`) — no migration needed for existing
  main-lane workflows.
- Side lanes that opened before Δ9 ran in the shared cwd; this is a known
  routing-bleed risk per Issue #342. Migration is per-lane operator decision
  at next lane-active session.

### Cross-references

- `feedback_lane_protocol.md` Rule 5.1 (sub-rule of Rule 5: Per-lane state
  separation) formalizes the worktree path convention as protocol
- `LANES.md` Governance §Lane workspace isolation declares each lane MUST
  state its `Worktree path` field
- `.mercury/docs/guides/worktree-workflow.md` covers the orthogonal
  task-level worktree scope

## Tests

```bash
scripts/test-lane-cap-check.sh
```

32 cases covering arg validation, within-cap, boundary (count == max),
exceeded, custom max, closed-lane exclusion, JSON output validity (including
quote/backslash hostile lane names), parser robustness (orphan-no-status
WARN, zombie-in-Closed-section exclusion), and empty Active Lanes section.
Tests do NOT touch real GitHub or LANES.md — synthetic fixtures only.

## Source references

- Issue [#313](https://github.com/392fyc/Mercury/issues/313) — Δ6 acceptance criteria
- Issue [#314](https://github.com/392fyc/Mercury/issues/314) — Δ7 acceptance criteria
- [v0.1 Delta companion §Δ6](../lane-protocol-v0.1-deltas.md#delta-6--rule-2-shorter-branch-prefix-p3)
- [v0.1 Delta companion §Δ7](../lane-protocol-v0.1-deltas.md#delta-7--hard-cap-at-5-active-lanes-doc-only)
- [Limiting Git Branch Names to 28 Characters (LeanTaaS)](https://medium.com/leantaas-engineering/why-are-we-limiting-git-branch-name-length-to-28-characters-c49cb5f4ff9a)
- [Best practices for naming Git branches (Graphite)](https://graphite.com/guides/git-branch-naming-conventions)
- [Miller's Law (Laws of UX)](https://lawsofux.com/millers-law/)
- [Towards a science of scaling agent systems (Google research)](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/)
- [Working with WIP limits for Kanban (Atlassian)](https://www.atlassian.com/agile/kanban/wip-limits)
- Issue [#342](https://github.com/392fyc/Mercury/issues/342) — Δ9 lane-level worktree isolation acceptance criteria + S13 routing-bleed forensic record
- [Claude Code .claude directory reference](https://code.claude.com/docs/en/claude-directory) — per-project `~/.claude/projects/<project>/` state dir
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — SessionStart `cwd` JSON field
- [Windows Terminal command line arguments](https://learn.microsoft.com/en-us/windows/terminal/command-line-arguments) — `new-tab -d <directory>` per-tab starting directory
- [git-worktree(1)](https://git-scm.com/docs/git-worktree) — multiple working trees from a single repo
