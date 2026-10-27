# Lane Naming + Capacity — lane protocol Rule 2 & HARD-CAP

Defines how lane branches are named (Rule 2 / Rule 2.1, rewritten by Issue
[#703](https://github.com/392fyc/Mercury/issues/703); first introduced as v0.1
Delta 6, Issue [#313](https://github.com/392fyc/Mercury/issues/313)). It also
records the former **HARD-CAP** delta (v0.1 Delta 7, Issue
[#314](https://github.com/392fyc/Mercury/issues/314)), which was **removed**
by Issue [#605](https://github.com/392fyc/Mercury/issues/605): lanes are no
longer capped (see §Δ7).

## Why two deltas in one guide

Both shape the lane registry: Δ6 controls how branches are named, Δ7 used to
control how many lanes could exist concurrently (removed in #605). Operators reason about "what lane do I
open / what do I name its branch" in one mental motion; one combined guide is
shorter than two cross-referenced ones.

## Δ6 — Branch naming: purpose first, lane and agent as suffixes

### The format

```
<type>/<purpose>[-<lane>][-<agent>]
```

| Part | Rule | Example |
|------|------|---------|
| `<type>` | one of `feature`, `feat`, `fix`, `hotfix`, `docs`, `chore`, `refactor`, `test`, `perf`, `ci`, `build` | `feature` |
| `<purpose>` | letters, digits, `.`, `_`, `-`; starts with a letter or digit; **no `/`**; no length limit | `621-guard-test` |
| `-<lane>` | optional suffix: a registered lane short name (the `Short name` in `LANES.md`, else the lane name) | `-guard` |
| `-<agent>` | `-claude` or `-codex`, always the last suffix. A branch an agent creates MUST carry it | `-codex` |

Examples: `feature/621-guard-test-guard-codex` (lane `guard`, created by
Codex) and `fix/TASK-12-claude` (no lane, created by Claude).

### Why purpose first

The branch name is read by people far more often than by scripts (`git branch`,
PR lists, `gh pr list`). With the old prefixes (`lane/side-mlane/...`,
`codex/...`) every branch of a lane started with the same few words, so the
part that tells branches apart, what the branch is for, was pushed to the end
and cut off by narrow terminals. Putting the purpose first and the lane and
agent last keeps the distinguishing part visible. The old 40-character cap
existed only to compensate for long prefixes and is gone: there is no length
limit.

### Forbidden prefixes

New branches never start with `codex/`, `claude/` or `lane/` (they name the
tool or the lane instead of the purpose). The `mercury/` namespace is reserved
for the update bot; the lane scripts never generate it.

### Which lane owns a branch

Remove a trailing `-claude` or `-codex`, then look at the end of what is left:
the branch belongs to the registered lane whose name it ends with
(`-<lane>`). If several registered names match, **the longest wins**. Example
with lanes `mlane` and `side-mlane`: `feature/7-x-side-mlane-codex` ends with
both `-mlane` and `-side-mlane`, so it belongs to `side-mlane`, not `mlane`.
A branch with no registered lane suffix belongs to no side lane; the `main`
lane may use such branches (its name needs no suffix).

`lane-assertion.sh` applies exactly this rule (see §Δ11 below), so the lane a
branch name points to must match the lane the session declares.

### Short-name convention

Each lane declares a `Short name` field in its own `LANES.md` section. The
short name MUST be:

- ≤ 8 characters (it is a branch suffix; keep it readable)
- Match `[a-z0-9-]+` (lowercase + digits + hyphen only)
- Globally unique across all active + closed lanes (avoid colliding with a
  closed lane's archived branches)
- Not a trailing part of another lane's name unless the longest-match rule
  above gives the intended owner (`mlane` vs `side-mlane`)

Default mapping for current Mercury lanes:

| Lane name | Short name | Rationale |
|-----------|-----------|-----------|
| `main` | `main` | Already short; canonical default lane |
| `side-multi-lane` | `side-mlane` | Compress "multi-lane" → "mlane" (8 chars exact) |

For new lanes: pick a short name at lane open time and write it in the lane's
`LANES.md` section before any branch is created. If two operators independently
pick the same short name, lane-claim semantics + manual review apply (no
mechanical enforcement; collision is rare).

### Creating branches

`lane-spawn.sh <lane> <issue> [--type TYPE] [--slug SLUG]` builds
`<type>/<issue>-<slug>-<short>-<harness>` (type defaults to `feature`, harness
is the lane's `--harness`) off the integration branch (next section).

### Integration branch (the base)

Mercury uses a single trunk: task branches start from, and pull requests merge
into, the **integration branch**; `develop` is frozen and no longer used.
Scripts never hard-code the name; they resolve it in this order and stop with
an error when nothing resolves (they do not guess):

1. environment variable `MERCURY_BASE_BRANCH`
2. `params.integration_branch` in the repository's `.mercury/config.json`
   (skipped when no `python` is available). Mercury itself sets this to
   `master`, so in this repository step 2 decides unless the environment
   variable overrides it.
3. the branch `origin/HEAD` points to (`git symbolic-ref --short refs/remotes/origin/HEAD`, without `origin/`)

`scripts/worktree-reaper.sh` uses the same order (after `WORKTREE_BASE_REF`)
and, as a fourth step, reads the remote's real HEAD with
`git ls-remote --symref origin HEAD` when `origin/HEAD` is missing locally.

### Backward compatibility (old formats)

- Existing branches are **not renamed**. The old formats `lane/<short>/...`
  and `feature/lane-<lane>/TASK-*` stay valid until their work is finished. The main lane's old
  `feature/TASK-<N>-*` needs no special case: it is an ordinary new-format
  name, owned by the lane whose suffix it ends with, else by `main`.
- Read-only status and scan scripts (`lane-status.sh`, `lane-sweep.sh`,
  `check-main-idle.sh`) recognize both the old and the new format.
- Assertion-style checks (`lane-assertion.sh`, the `lane-close.sh` ownership
  warning) keep accepting the old formats so that in-progress work is not
  interrupted. This is a compatibility allowance, not a recommendation.
- New branches MUST use the new format; nothing creates `lane/<short>/...`
  branches any more.

### Script support (current state)

`lane-sweep.sh` and `check-main-idle.sh` read every **local and remote-tracking**
branch (`git for-each-ref refs/heads refs/remotes`); `lane-status.sh` reads the
**remote** branches only (`git ls-remote origin 'refs/heads/*'`). All three
match a lane's branches by suffix (new format) plus the old prefixes. Because
the new format has no common prefix, they list every branch and match it
against the registered lane names, where a lane's name is its Short name, else
its lane name. `lane-status.sh` works out each branch's owner once, outside the
lane loop, so the cost grows with branches + lanes, not their product. The
shared logic is in `.mercury/packs/lanes/scripts/lib/lane-paths.sh`
(`lane_branch_owner`, `lane_branch_owner_set`, `lane_branch_last_ts`,
`lane_integration_branch`).

## Δ7 — HARD-CAP at 5 active lanes

> **Superseded (2026-09, Issue [#605](https://github.com/392fyc/Mercury/issues/605)).** Lanes are no longer capped (#599 ADR (Mercury_KB: `04-research/mercury/issue-599-cross-harness-lane-isolation-2026-09.md`) D1). `lane-spawn.sh` no longer refuses at the cap, and `lane-cap-check.sh` only reports the count unless you pass `--max N`. This section is kept as the historical record. Current `lane-cap-check.sh` usage: with no flags it prints the active count with verdict `uncapped` and exits 0; `--max N` (a positive integer) opts in to a threshold of your own, with verdict `within_cap` / `exceeded` and exit 1 when exceeded.

### Cap value

Historical (not in force since #605): `LANES.md` was not allowed to exceed **5 active lanes** simultaneously. The cap was
declared in the lane protocol (now indexed in [lane-protocol.md](lane-protocol.md)) and enforced advisorily by
`.mercury/packs/lanes/scripts/lane-cap-check.sh`.

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

1. **Close an existing lane first.** Use `.mercury/packs/lanes/scripts/lane-close.sh <lane>` to
   flip Status to `closed` + prune `.tmp/lane-<lane>/`.
2. **OR** open a GitHub Issue with the `protocol-violation` label requesting
   a temporary cap raise. The Issue body MUST justify the raise (specific
   work that requires more parallelism, expected duration, plan to return
   below cap). User arbitrates.

### Advisory enforcement

```bash
.mercury/packs/lanes/scripts/lane-cap-check.sh [--lanes-file PATH] [--memory-dir PATH]
                          [--max N] [--format text|json]
```

| Flag | Effect |
|------|--------|
| `--max N` | Override the cap (default 5). |
| `--format text\|json` | Output format. |
| `--lanes-file PATH` | Override LANES.md location. |
| `--memory-dir PATH` | Override memory dir. Defaults to `MERCURY_MEMORY_DIR` env, then the project binding `.mercury/memory/lane-home` in the main checkout (#644), then the project's lane home `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded main checkout>/memory` (`.mercury/packs/lanes/scripts/lane-paths.sh memory-dir`; `D--Mercury-Mercury` for `D:/Mercury/Mercury`; #613). |
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

(Historical output, printed before #605 removed the cap.)

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

> **Path notation in this §**: `<repo-root>` is the root of the Mercury main
> checkout (the directory that holds `.git` and `.worktrees/`). Since Issue #663
> every worktree, lane-level included, lives inside the project at
> `<repo-root>/.worktrees/`; nothing is created in a sibling directory. Each
> lane's `Worktree path` field in `LANES.md` records the concrete path.

### Distinguished from task-level worktrees

This § is about **lane-level** isolation (one worktree per active lane,
long-lived). It is NOT the same scope as the **task-level** worktree spec in
`.mercury/docs/guides/worktree-workflow.md`, which
covers `.worktrees/{taskId}` worktrees created for a
single dev task and removed after the PR merge is verified.

| Scope | Path | Lifecycle | Purpose |
|-------|------|-----------|---------|
| **Lane-level** (this §) | `<repo-root>/.worktrees/lanes/<short>` | Per active lane; long-lived; removed at lane close | Isolate per-cwd Claude Code session transcripts (jsonl); user-memory dir (MEMORY.md / SESSION_INDEX.md / sessions/ / handoffs) is canonical by design — see §Operational expectation |
| **Task-level** (`.mercury/docs/guides/worktree-workflow.md`) | `<repo-root>/.worktrees/{taskId}` | Per dev task; ephemeral; removed after PR merge is verified | Isolate concurrent dev-task branch checkouts |

Both kinds live under the repository's own `.worktrees/` (Issue #663). The
`lanes/` subdirectory is reserved for lane-level worktrees: task-level cleanup
(`scripts/worktree-reaper.sh`, `scripts/cleanup-worktree-branch.sh` auto-discovery)
skips it, because a lane is long-lived and is closed by the lane-close
procedure below, never by task cleanup.

A side lane MAY use both: live in `<repo-root>/.worktrees/lanes/<short>` for its
session-state isolation, AND create
`.worktrees/{taskId}` inside that lane worktree for parallel dev tasks.

### Worktree path convention

| Lane | Worktree path | Notes |
|------|---------------|-------|
| `main` | `<repo-root>` | Backward-compat default; the original repo checkout |
| `<side>` | `<repo-root>/.worktrees/lanes/<short>` | `<short>` = the lane's `Short name` field (≤8 char, per Δ6); record the concrete value in own LANES.md `Worktree path` field per Rule 5.1 |

Example: `side-multi-lane` (short name `side-mlane`) →
`<repo-root>/.worktrees/lanes/side-mlane`.

The lane worktree is a different directory from the main checkout, so
auto-handoff invocations launched from the lane's session can `cd` to a path
that resolves to a distinct `~/.claude/projects/<encoded-cwd>/` dir (the
encoding of the full lane worktree path, which differs from the main
checkout's).

### Cross-repo lane variant (Rule 5.1 extension, Issue [#374](https://github.com/392fyc/Mercury/issues/374))

The default mapping above assumes the lane's worktree is inside the Mercury
checkout (`.worktrees/lanes/<short>`). A **cross-repo lane** points its `Worktree path`
field at a sibling git repository (a different `.git` root, possibly with
its own remote / branching convention) and uses Mercury core agents +
skills + memory mechanics in service of work that lives in that sibling repo.

This is an **extension** of Rule 5.1, not a strict superset: Rule 5.1's
`.worktrees/lanes/<short>` SHOULD-recommendation does not apply, and the
procedural §"Setup at lane open" / §"Cleanup at lane close" Mercury
`git worktree add/remove` flows below also do **not** apply (the host
repo manages its own checkout lifecycle). Only the requirement to declare
a `Worktree path` field per Rule 5.1 (lane workspace isolation) carries
over unchanged; ownership / per-section editing of LANES.md remains
governed by Rule 6.

- The worktree path does NOT need to match `<repo-root>/.worktrees/lanes/<short>` —
  that convention is the default for lanes working on the Mercury checkout, not
  a hard constraint: the host repository's own checkout keeps its own location. The lane MUST still declare a
  stable `Worktree path` field in its own LANES.md section per Rule 5.1
  (Worktree path declaration) — and only the owning lane edits its own
  LANES.md section per Rule 6.
- The lane's branch convention follows the host repo's git workflow, not
  Mercury's. The Rule 2.1 suffix format (`<type>/<purpose>-<short>[-<agent>]`)
  remains recommended for in-host work-branches that the operator wants Mercury
  lane-assertion (Δ11) to validate. If host-repo conventions conflict
  AND the operator accepts the trade-off, `MERCURY_LANE_ASSERT_DISABLED=1`
  is the operator's escape valve — but it skips **all** assertion checks
  (marker, cwd-encoded vs Worktree path, branch convention; see the
  soft-disable section in §"Δ11 — Path C" below), not just branch
  convention. **Use it only as a one-shot command prefix** (e.g.
  `MERCURY_LANE_ASSERT_DISABLED=1 claude ...`) and
  `unset MERCURY_LANE_ASSERT_DISABLED` immediately after; do NOT persist
  in shell profile / `.bashrc` / CI defaults — the assertion bypass is
  meant for exceptional moments, not steady state. Prefer naming
  branches per Rule 2.1 where compatible.
- The lane MUST still claim Mercury Issues via the `lane:<name>` label
  (where `<name>` matches the full lane name in LANES.md, e.g.
  `lane:side-bug` — see `.mercury/packs/lanes/docs/guides/lane-claim.md`)
  for any Mercury-side work (e.g. cross-repo coordination tickets);
  in-host work tracking lives in the host repo's own ledger (KB, Issues,
  etc.) per host convention and is OPAQUE to Mercury LANES.md.
- Mercury asset dependency (user-scope agents, skills, hooks) is what
  makes the cross-repo pattern usable — the lane SHOULD declare a
  `Mercury asset dependency` field in its own LANES.md section listing
  the user-scope assets it relies on (e.g. `~/.claude/agents/{main,dev,...}`).
  This is a documentation-only convention (no schema/tooling enforcement
  in `.mercury/packs/lanes/scripts/lane-spawn.sh` or fixtures); the field exists so future
  user-scope asset migrations have a manual audit anchor.
- **Lane home (#613)**: lane scripts resolve the registry from the main
  checkout of the repo they run in, so from the host repo they would look
  for the host's own lane home. Start cross-repo lane sessions with
  `MERCURY_MEMORY_DIR` set to the owning project's lane home, as a one-shot
  prefix (`MERCURY_MEMORY_DIR="$(bash <mercury>/.mercury/packs/lanes/scripts/lane-paths.sh
  memory-dir --repo-root <mercury>)" claude ...`), never in a shared
  settings env block; the handoff skill's Step 5 honours the same variable
  when the host repo has no `.mercury/packs/lanes/scripts/lane-paths.sh`.

First dogfood: the first cross-repo lane (worktree at `<host-repo-root>/<host-repo>`,
opened 2026-05-10 per [#374](https://github.com/392fyc/Mercury/issues/374)).
Its LANES.md section lives in user-memory at
`<canonical>/LANES.md` (where `<canonical>` resolves per the §"Operational
expectation" rules above — not in the Mercury repo), so the dogfood
reference cannot be cross-checked against repo content from this branch
alone. To verify the companion edit at audit time:

- Read that lane's section in the user-memory LANES.md directly (path
  resolves via `MERCURY_MEMORY_DIR` or its default — see §"Operational
  expectation" above);
- Issue [#374](https://github.com/392fyc/Mercury/issues/374) body links the
  full lane section content as a comment for repo-side audit anchor;
- This guide change and the user-memory companion edit MUST land in the
  same Mercury session (atomicity is operator-enforced, not tooling-enforced).

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
# Prerequisite: ensure origin/<integration-branch> is current to avoid basing the new
# worktree on a stale local branch (matches .mercury/packs/lanes/scripts/lane-spawn.sh convention).
# <integration-branch>: $MERCURY_BASE_BRANCH, else params.integration_branch in
# .mercury/config.json, else the branch origin/HEAD points to (see §Δ6).
git fetch origin

# Recommended: create a fresh init branch off origin/<integration-branch> (avoids checkout collision)
git worktree add -b chore/init-<short>-<agent> <repo-root>/.worktrees/lanes/<short> origin/<integration-branch>
# Example (integration branch master, lane side-mlane driven by Claude):
# git worktree add -b chore/init-side-mlane-claude <repo-root>/.worktrees/lanes/side-mlane origin/master
```

If you instead try `git worktree add <repo-root>/.worktrees/lanes/<short> <integration-branch>` and
the integration branch is already checked out in the main `<repo-root>` checkout,
git refuses with "fatal: '<integration-branch>' is already used by worktree at ..." per
[git-worktree(1)](https://git-scm.com/docs/git-worktree) (a branch can only
be checked out by one worktree at a time). The `-b chore/init-<short>-<agent>` form
above sidesteps the collision by creating a new branch from `origin/<integration-branch>`. The
lane's actual work branches (Rule 2.1 suffix format `<type>/<purpose>-<short>-<agent>`)
are created later inside the worktree; the `init` branch can be deleted
once a real work branch exists. (The `init` name follows the same suffix rule so
`lane-assertion.sh` accepts it; the old `lane/<short>/init` is still accepted as a legacy name.)

### Operational expectation

Side-lane sessions launch with the worktree as cwd:

```bash
cd <repo-root>/.worktrees/lanes/<short>
claude  # SessionStart hook now reads from ~/.claude/projects/<encoded-cwd>/
# Example:
# cd <repo-root>/.worktrees/lanes/side-mlane && claude
# → ~/.claude/projects/<encoded <repo-root>/.worktrees/lanes/side-mlane>/
```

This makes the per-cwd project state dir distinct from the main lane's, but
**not all per-project state is encoded-cwd-routed**. Mercury's per-cwd
isolation applies to Claude Code core's session transcript storage; the
user-memory layer (MEMORY.md / SESSION_INDEX.md / per-session files /
handoffs) is intentionally canonical for cross-lane memory-index visibility.
Concretely (illustrative path values — actual values depend on the
operator's platform encoding + env vars; trust the script + hook resolution
at runtime, not the literals shown):

- `<encoded-cwd>` = your platform's encoding of the full lane worktree path
  `<repo-root>/.worktrees/lanes/<short>` (every non-alphanumeric character becomes `-`)
- `<canonical>` = the project's lane home, `.mercury/packs/lanes/scripts/lane-paths.sh memory-dir`:
  `MERCURY_MEMORY_DIR` if set, else
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded main checkout>/memory`
  (example: `~/.claude/projects/<encoded main checkout>/memory` —
  encoded for the repo's main checkout path; each project gets its own, #613)

**Per-cwd (Claude Code core, automatic):** session transcripts under
`~/.claude/projects/<encoded-cwd>/` (Claude Code provides the exact
`transcript_path` via the hook payload — see
[hooks reference](https://code.claude.com/docs/en/hooks); do not assume a
specific filename pattern or layout — read it from the payload).

**Canonical (Mercury user-memory, intentional cross-lane visibility):**

- `<canonical>/MEMORY.md` — shared memory index (per-session bullets
  derived from per-session files; Mercury no longer ships a generator for it,
  because `scripts/regenerate-memory-index.sh` was removed in #675; keeping it
  current is outside this repository; Rule 6 boundary still
  binds — each lane only edits its own per-session files, never another
  lane's bullets)
- `<canonical>/SESSION_INDEX.md` — shared session table (same upkeep)
- `<canonical>/sessions/S<N>.md` (main lane) +
  `<canonical>/sessions/S<N>-<lane>.md` (side lanes) — per-session
  frontmatter + body; lane-suffixed files visibly partition ownership
  while sharing one directory
- `<handoff-dir>/session-handoff.md` (main lane) +
  `<handoff-dir>/session-handoff-<lane>.md` (side lanes) — per-lane
  handoff files, kept in the project's handoff dir
  (`.mercury/packs/lanes/scripts/lane-paths.sh handoff-dir`, #613), not in `<canonical>`
- The canonical path is anchored by two converging mechanisms:
  `.mercury/packs/lanes/scripts/lane-paths.sh memory-dir` reads the `MERCURY_MEMORY_DIR`
  env var explicitly (default fallback shown in `<canonical>` resolution
  above), and Claude Code core's auto-memory layer + user-level
  SessionStart hooks resolve the same project-anchored dir at runtime.
  These are different code paths converging on the same canonical
  location — if either changes, audit both before relying on routing
  invariance. Override with `MERCURY_MEMORY_DIR` if a deployment
  genuinely needs per-cwd memory routing (not the recommended posture
  for Mercury's lane model). The literal example shown above is for one
  specific Mercury checkout; do not hard-code it elsewhere — derive it
  at runtime from the env var or from
  `bash .mercury/packs/lanes/scripts/lane-paths.sh memory-dir`.

**Why canonical (not per-cwd) for user-memory**: Rule 6 (LANES.md is the
single registry) and Rule 7 (per-session files; canonical MEMORY.md /
SESSION_INDEX.md derived from them, see `lane-protocol.md`) both assume one shared memory index that
every lane can read. Per-cwd routing of memory would fragment the index — a
side lane wouldn't see main lane's session history, and each worktree's
index would diverge. The S13-side-multi-lane routing-bleed
incident addressed by Rule 5.1 was specifically about handoff-file selection
under SessionStart hook latest-mtime ambiguity in shared cwd, not about
memory-index routing. Worktree-per-lane (Rule 5.1) disambiguates the cwd
identity so SessionStart-driven handoff selection now operates within an
unambiguous lane scope, while lane-suffix filenames
(`session-handoff[-<lane>].md`) provide the partitioning within the shared
canonical dir. Operators relying on auto-handoff to read the correct
lane-suffixed file SHOULD verify this empirically the first time a new
worktree starts a session (S17-side-multi-lane Path A end-to-end validation
2026-05-03 confirms jsonl per-cwd isolation; handoff-file selection is the
next observation point if regression suspected).

Hooks that resolve project state from cwd (e.g. Mercury's user-level
SessionStart loader) start routing correctly for the per-cwd component
without code change: SessionStart input includes a `cwd` field
([documented](https://code.claude.com/docs/en/hooks)). Hooks that read
user-memory files (MEMORY.md / SESSION_INDEX.md / handoffs) continue
resolving from the canonical path described above; this is the design, not
a regression. Hooks that hard-code a project path or use other resolution
strategies are unaffected by this isolation; verify your specific hook
before relying on auto-routing.

### Cleanup at lane close

`.mercury/packs/lanes/scripts/lane-close.sh` does **not** currently remove the lane worktree (it
only flips Status to `closed` + prunes `.tmp/lane-<lane>/` per Rule 3.2).
Operators run worktree cleanup manually as part of the close ceremony:

```bash
# Sanity-check the path resolves to the expected worktree before proceeding.
git worktree list | grep -F "<repo-root>/.worktrees/lanes/<short>"

# Verify the lane branch is merged (ancestor of origin/<integration-branch>, or contained in a
# merged PR head) and the worktree is clean (git status --porcelain is empty).
git -C <repo-root>/.worktrees/lanes/<short> status --porcelain

git worktree remove <repo-root>/.worktrees/lanes/<short>      # never --force

# Example:
# git worktree list | grep -F ".worktrees/lanes/side-mlane"
# git worktree remove <repo-root>/.worktrees/lanes/side-mlane
```

If you use `scripts/cleanup-worktree-branch.sh` for the lane branch, pass the
verified lane worktree explicitly with `--worktree-path`; its auto-discovery
skips anything under `.worktrees/lanes/` by design.

If the worktree has uncommitted content or the merge cannot be verified, do
not remove anything; report the path and branch. A lane abandoned without
merging is listed for the user and removed only after the user confirms (see
`.mercury/docs/guides/worktree-workflow.md` for the confirmed-deletion
steps, including the SHA read-back before the remote branch is deleted via
`gh api -X DELETE`; `main`, `master`, `develop` (frozen) and the default branch are never deleted).

`git worktree remove` operates on registered worktrees only (verifiable via
`git worktree list`) — it cannot remove arbitrary filesystem paths, so the
operation is bounded to the worktree set this repo knows about. Combined with
the Rule 2.1 `<short>` validation above, the command shape is safe for
protocol-conforming inputs.

The `~/.claude/projects/<encoded-cwd>/` user-memory dir is preserved as
audit trail; operators may archive it manually if no longer needed.

### Backward compatibility

- `main` lane keeps the main checkout `<repo-root>` as its worktree path — no migration needed for existing
  main-lane workflows.
- Side lanes that opened before Δ9 ran in the shared cwd; this is a known
  routing-bleed risk per Issue #342. Migration is per-lane operator decision
  at next lane-active session.

### Δ10 — `/handoff:auto` worktree integration (Issue [#345](https://github.com/392fyc/Mercury/issues/345))

The `handoff` skill (Codex: `.mercury/packs/lanes/skills/lane-handoff/SKILL.md`; Claude: user-level `handoff`; Step 5 Auto mode) now
auto-resolves the active lane's `Worktree path` from `LANES.md` and uses it
as the new tab's cwd:

1. Lane name is derived from the handoff filename pattern:
   - `session-handoff.md` → `main`
   - `session-handoff-<lane>.md` → `<lane>`
2. `LANES.md` is read for that lane's `Worktree path` bullet (this §
   Worktree path convention table). awk extraction is bounded by the
   `### \`<lane-name>\`` heading scope so cross-lane bleed is impossible.
3. The path is passed to the canonical launcher (Mercury Issue #377; do not
   freeform-construct wt/tmux commands):
   ```bash
   REPO_ROOT="$(git rev-parse --show-toplevel)"
   bash "$REPO_ROOT/.mercury/packs/lanes/scripts/handoff-launch.sh" \
     --lane <lane> --worktree <worktree> --handoff-doc <doc>
   ```
   The launcher handles platform detection (Windows → wt, macOS/Linux → tmux)
   and constructs SHORT_PROMPT with `[LANE=<name>]` marker preserved.
4. If the `Worktree path` field is missing → spawn aborts with explicit
   `Rule 5.1` guidance pointing at `LANES.md`.

This closes the manual-`<cwd>`-substitution failure mode where an operator
or agent invoking `/handoff:auto` from the main checkout (`D:/Mercury/Mercury`)
forgot to override `<cwd>` before spawning a side-lane session — reproducing
the share-cwd routing-bleed scenario that Δ9 was designed to prevent.

### Δ11 — Path C: `[LANE=<name>]` marker + `lane-assertion.sh` (Issue [#345](https://github.com/392fyc/Mercury/issues/345))

The auto-mode SHORT_PROMPT now starts with a `[LANE=<name>]` marker as its
first whitespace-delimited token. **`<name>` is the lane's full section
name** (the heading text under `## Active Lanes` in `LANES.md`), NOT the
short name from the §Δ6 short-prefix convention. The handoff skill
derives `<name>` from the handoff filename pattern (`session-handoff.md`
→ `main`; `session-handoff-side-multi-lane.md` → `side-multi-lane`), and
[`.mercury/packs/lanes/scripts/lane-assertion.sh`](../../scripts/lane-assertion.sh) looks up
the matching lane section by exact heading equality. Operators executing
the assertion manually MUST use the lane section name; using the short
name (e.g. `side-mlane`) instead would yield exit 4 (Worktree path
missing) because no lane section heading matches.

```
[LANE=side-multi-lane] Continue from session handoff. Read <HANDOFF_PATH> as your first action.
```

For the `main` lane the section name and short name happen to be
identical, so `[LANE=main]` works regardless. The distinction matters
only for side lanes whose section name and short name differ
(e.g. `side-multi-lane` vs `side-mlane`). Fixes Argus iter2 Minor
"文档一致性" (PR #346).

The new session validates three-way lane alignment via
[`.mercury/packs/lanes/scripts/lane-assertion.sh`](../../scripts/lane-assertion.sh) before any
work. The script compares:

| Source | Expected | Mismatch exit |
|--------|----------|---------------|
| `[LANE=<name>]` marker (CLI / `MERCURY_LANE_MARKER` / `BOOTSTRAP_PROMPT` / stdin) | parsable lane name (`[a-z0-9-]+`) | `1` |
| Encoded cwd vs encoded `Worktree path` from `LANES.md` | byte-equal after `: \\ /` → `-` slash-encoding | `2` |
| `git branch --show-current` | matches lane's branch convention (Rule 2.1: `<type>/<purpose>-<short>[-<agent>]`, longest registered lane suffix wins; legacy compatibility: `lane/<short>/*` or `feature/lane-<lane>/TASK-*`). For **side lanes** no protected branch is tolerated (`develop` is frozen; `master`/`main` are NOT either) — accepting one would mean a misrouted session on those branches passes the assertion. **Main lane** tolerates all three (`develop`/`master`/`main`) since they are legitimate main-lane operating branches, and accepts any new-format branch that no other registered lane owns. | `3` |
| `Worktree path` field present in lane's `LANES.md` section | non-empty | `4` |

Marker source priority: `--marker` CLI → `MERCURY_LANE_MARKER` env →
`BOOTSTRAP_PROMPT` env → stdin. The first source that yields a parseable
`[LANE=<name>]` token wins; subsequent sources are not consulted.

The marker character class is intentionally `[a-z0-9-]+` (matches Rule 2.1
short-name validation): no path-traversal sequences, shell metacharacters,
or quote characters can leak into the assertion logic via a crafted lane
name. The lane name is also bounded by Rule 6 (only the owning lane edits
its own `LANES.md` section), so injection from outside the protocol is
prevented at the source.

#### Soft-disable

`MERCURY_LANE_ASSERT_DISABLED=1` skips all checks and exits 0 — break-glass
for legitimate scenarios (recovery sessions, intentional cross-lane debug,
single-lane operators who don't want the discipline). The skip writes one
line to stdout so it is auditable in transcripts. Soft-disable is
session-scoped only; it does not persist.

#### Hook integration (forward-looking)

This Issue scope keeps assertion as a manual / agent-as-first-action step
to validate the contract in production. If proven stable across ≥3 sessions
of real auto-handoff usage, follow-up work may wire it into a user-level
SessionStart hook, following the user-level hook governance pattern of Rule 7
Phase F.C (analogous to Issue #259 deployment for mem0). The deferred-hook
choice is intentional: a SessionStart-time assertion that runs by default
needs a track record of low false-positive rate before it becomes
unconditional infrastructure.

### Cross-references

- [lane-protocol.md](lane-protocol.md) Rule 5.1 (sub-rule of Rule 5: Per-lane state
  separation) lists this section as the definition of the worktree path
  convention and of the Δ10/Δ11 contracts
- Each lane MUST state its `Worktree path` field in its own `LANES.md`
  section (`.mercury/packs/lanes/scripts/lane-init.sh` and `.mercury/packs/lanes/scripts/lane-spawn.sh` write it)
- `.mercury/docs/guides/worktree-workflow.md` covers the orthogonal
  task-level worktree scope
- `.mercury/packs/lanes/docs/guides/agent-view-dispatch.md` covers Path B dispatch
  convention for Claude Code's agent view UI layer on top of Mercury
  per-cwd lane isolation (bare bg dispatch, monitoring, hook lifecycle,
  auto-iso HYBRID mechanism, cost-tracker gap, kill switch)
- `.mercury/packs/lanes/skills/lane-handoff/SKILL.md` Step 5 Auto mode for the spawn-side
  contract
- Mercury_KB: `04-research/mercury/issue-599-cross-harness-lane-isolation-2026-09.md`
  (ADR, Issue #599) presets how Codex lanes and Claude Code lanes stay
  isolated, read each other, and exchange messages when they collaborate
- `.mercury/packs/lanes/scripts/lane-assertion.sh` + `tests/lanes/test-lane-assertion.sh` for the
  consumer-side check (test suite covers happy paths for both lanes,
  every exit-code branch including `worktree_path_duplicate`, marker
  source priority + first-non-blank-line semantics, JSON format with
  backslash/quote escaping, soft-disable, code-fence isolation, paths
  with spaces/parens, and side-lane branch tightening — see the suite
  for the current assertion count)

## Tests

```bash
tests/lanes/test-lane-cap-check.sh
```

Cases covering arg validation, the default no-cap mode (`uncapped`, exit 0,
JSON `"max":null`), within-cap, boundary (count == max),
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
- Issue [#345](https://github.com/392fyc/Mercury/issues/345) — Δ10/Δ11 `/handoff:auto` worktree integration + Path C agent lane-assertion acceptance criteria
- [Claude Code .claude directory reference](https://code.claude.com/docs/en/claude-directory) — per-project `~/.claude/projects/<project>/` state dir
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — SessionStart `cwd` JSON field
- [Windows Terminal command line arguments](https://learn.microsoft.com/en-us/windows/terminal/command-line-arguments) — `new-tab -d <directory>` per-tab starting directory
- [git-worktree(1)](https://git-scm.com/docs/git-worktree) — multiple working trees from a single repo
- `feedback_handoff_short_prompt_only.md` — S3-side-multi-lane SHORT_PROMPT lesson (Δ10/Δ11 SHORT_PROMPT design rests on this rule)
