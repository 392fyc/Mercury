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
| **Lane-level** (this §) | `<repo-root>/Mercury-<short>` | Per active lane; long-lived; removed at lane close | Isolate per-cwd Claude Code session transcripts (jsonl); user-memory dir (MEMORY.md / SESSION_INDEX.md / sessions/ / handoffs) is canonical by design — see §Operational expectation |
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

This makes the per-cwd project state dir distinct from the main lane's, but
**not all per-project state is encoded-cwd-routed**. Mercury's per-cwd
isolation applies to Claude Code core's session transcript storage; the
user-memory layer (MEMORY.md / SESSION_INDEX.md / per-session files /
handoffs) is intentionally canonical for cross-lane memory-index visibility.
Concretely (illustrative path values — actual values depend on the
operator's platform encoding + env vars; trust the script + hook resolution
at runtime, not the literals shown):

- `<encoded-cwd>` = your platform's encoding of `<repo-root>/Mercury-<short>`
  (Mercury team example: `D--Mercury-Mercury-side-mlane` for
  `D:/Mercury/Mercury-side-mlane`)
- `<canonical>` = whatever `MERCURY_MEMORY_DIR` resolves to at runtime, with
  the script default falling back to
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory`
  (Mercury team example: `~/.claude/projects/D--Mercury-Mercury/memory` —
  encoded for the repo's main checkout path)

**Per-cwd (Claude Code core, automatic):** session transcripts under
`~/.claude/projects/<encoded-cwd>/` (Claude Code provides the exact
`transcript_path` via the hook payload — see
[hooks reference](https://code.claude.com/docs/en/hooks); do not assume a
specific filename pattern or layout — read it from the payload).

**Canonical (Mercury user-memory, intentional cross-lane visibility):**

- `<canonical>/MEMORY.md` — shared memory index (per-session bullets
  derived from per-session files via
  `scripts/regenerate-memory-index.sh --in-place`; Rule 6 boundary still
  binds — each lane only edits its own per-session files, never another
  lane's bullets)
- `<canonical>/SESSION_INDEX.md` — shared session table (same derivation)
- `<canonical>/sessions/S<N>.md` (main lane) +
  `<canonical>/sessions/S<N>-<lane>.md` (side lanes) — per-session
  frontmatter + body; lane-suffixed files visibly partition ownership
  while sharing one directory
- `<canonical>/session-handoff.md` (main lane) +
  `<canonical>/session-handoff-<lane>.md` (side lanes) — per-lane
  handoff files in the same dir
- The canonical path is anchored by two converging mechanisms:
  `scripts/regenerate-memory-index.sh` reads the `MERCURY_MEMORY_DIR`
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
  `bash scripts/regenerate-memory-index.sh --help`.

**Why canonical (not per-cwd) for user-memory**: Rule 6 (LANES.md is the
single registry) and Rule 7 (per-session files; canonical MEMORY.md /
SESSION_INDEX.md auto-regenerated) both assume one shared memory index that
every lane can read. Per-cwd routing of memory would fragment the index — a
side lane wouldn't see main lane's session history, and `regenerate-memory-index.sh`
output would diverge per worktree. The S13-side-multi-lane routing-bleed
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

### Δ10 — `/handoff:auto` worktree integration (Issue [#345](https://github.com/392fyc/Mercury/issues/345))

The `handoff` skill (`.claude/skills/handoff/SKILL.md` Step 5 Auto mode) now
auto-resolves the active lane's `Worktree path` from `LANES.md` and uses it
as the new tab's cwd:

1. Lane name is derived from the handoff filename pattern:
   - `session-handoff.md` → `main`
   - `session-handoff-<lane>.md` → `<lane>`
2. `LANES.md` is read for that lane's `Worktree path` bullet (this §
   Worktree path convention table). awk extraction is bounded by the
   `### \`<lane-name>\`` heading scope so cross-lane bleed is impossible.
3. The path is substituted into the spawn command:
   - Windows: `wt -w 0 nt --title "Handoff: <lane>" -d "<worktree>" -- claude -- "$SHORT_PROMPT"`
   - tmux:    `tmux new-window -n handoff -c "<worktree>" "claude -- '$SHORT_PROMPT'"`
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
[`scripts/lane-assertion.sh`](../../../scripts/lane-assertion.sh) looks up
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
[`scripts/lane-assertion.sh`](../../../scripts/lane-assertion.sh) before any
work. The script compares:

| Source | Expected | Mismatch exit |
|--------|----------|---------------|
| `[LANE=<name>]` marker (CLI / `MERCURY_LANE_MARKER` / `BOOTSTRAP_PROMPT` / stdin) | parsable lane name (`[a-z0-9-]+`) | `1` |
| Encoded cwd vs encoded `Worktree path` from `LANES.md` | byte-equal after `: \\ /` → `-` slash-encoding | `2` |
| `git branch --show-current` | matches lane's branch-prefix convention (Rule 2.1 `lane/<short>/*` or legacy `feature/lane-<lane>/TASK-*`). For **side lanes** only `develop` is tolerated as pre-checkout; `master`/`main` are NOT — accepting them would mean a misrouted session on those branches passes the assertion. **Main lane** tolerates all three (`develop`/`master`/`main`) since they are legitimate main-lane operating branches. | `3` |
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
SessionStart hook per `feedback_lane_protocol.md` Rule 5.1 §F.C governance
pattern (analogous to Issue #259 deployment for mem0). The deferred-hook
choice is intentional: a SessionStart-time assertion that runs by default
needs a track record of low false-positive rate before it becomes
unconditional infrastructure.

### Cross-references

- `feedback_lane_protocol.md` Rule 5.1 (sub-rule of Rule 5: Per-lane state
  separation) formalizes the worktree path convention as protocol; §5.1.1
  + §5.1.2 cover the Δ10/Δ11 contracts
- `LANES.md` Governance §Lane workspace isolation declares each lane MUST
  state its `Worktree path` field
- `.mercury/docs/guides/worktree-workflow.md` covers the orthogonal
  task-level worktree scope
- `.claude/skills/handoff/SKILL.md` Step 5 Auto mode for the spawn-side
  contract
- `scripts/lane-assertion.sh` + `scripts/test-lane-assertion.sh` for the
  consumer-side check (test suite covers happy paths for both lanes,
  every exit-code branch including `worktree_path_duplicate`, marker
  source priority + first-non-blank-line semantics, JSON format with
  backslash/quote escaping, soft-disable, code-fence isolation, paths
  with spaces/parens, and side-lane branch tightening — see the suite
  for the current assertion count)

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
- Issue [#345](https://github.com/392fyc/Mercury/issues/345) — Δ10/Δ11 `/handoff:auto` worktree integration + Path C agent lane-assertion acceptance criteria
- [Claude Code .claude directory reference](https://code.claude.com/docs/en/claude-directory) — per-project `~/.claude/projects/<project>/` state dir
- [Claude Code hooks reference](https://code.claude.com/docs/en/hooks) — SessionStart `cwd` JSON field
- [Windows Terminal command line arguments](https://learn.microsoft.com/en-us/windows/terminal/command-line-arguments) — `new-tab -d <directory>` per-tab starting directory
- [git-worktree(1)](https://git-scm.com/docs/git-worktree) — multiple working trees from a single repo
- `feedback_handoff_short_prompt_only.md` — S3-side-multi-lane SHORT_PROMPT lesson (Δ10/Δ11 SHORT_PROMPT design rests on this rule)
