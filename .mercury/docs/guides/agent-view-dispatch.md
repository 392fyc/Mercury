# Agent view dispatch convention (Mercury 多 lane + agent view UI 层)

> **Status**: in effect. Path B PRIMARY per ADR
> [`agent-view-multi-lane-adaptation-2026-05.md`](../research/agent-view-multi-lane-adaptation-2026-05.md)
> (Closes [#386](https://github.com/392fyc/Mercury/issues/386), PR
> [#387](https://github.com/392fyc/Mercury/pull/387)). Phase 6 empirical
> refinements in
> [`agent-view-phase6-empirical-2026-05.md`](../research/agent-view-phase6-empirical-2026-05.md)
> (Closes [#391](https://github.com/392fyc/Mercury/issues/391), PR
> [#393](https://github.com/392fyc/Mercury/pull/393)). This guide is the
> operator-facing reference; the ADRs remain the authoritative evidence
> base. Closes [#388](https://github.com/392fyc/Mercury/issues/388)
> (#386-A).

## Purpose

[Agent view](https://code.claude.com/docs/en/agent-view) lets Claude Code
operators dispatch and monitor background sessions (`claude --bg ...`,
`/bg`, `@<agent>` mentions, `claude --bg --agent <name>`) from a single
interactive terminal. Mercury runs a **multi-lane** model
(`feedback_lane_protocol.md` v1 + `LANES.md` registry — both canonical
at `<canonical>/`, the user-memory dir, NOT files in this repo; see
[`lane-naming.md`](lane-naming.md) §"Operational expectation" for the
`<canonical>` resolution) where each lane has its own long-lived worktree
and per-cwd session state.

The two concepts are **orthogonal**: lane = long-lived scope (跨周/跨月);
session = short-lived task (1 lifecycle, supervisor 1h idle kill). This
guide documents the **dispatch convention** that lets the two coexist
without code change to Mercury's lane protocol — agent view operates
purely as an in-flight UI layer on top of the existing per-cwd worktree
isolation.

**Path B (per ADR) in one sentence**: Mercury's existing multi-lane
protocol — LANES.md registry, lane-naming.md worktree convention, per-cwd
session isolation — stays unchanged; agent view sits on top as an
in-flight UI layer and is fully disabled by one env var (§7). All
references below to "Path B" mean this posture.

If you are evaluating whether Mercury should adopt agent view at all,
read the ADRs first; if you are an operator already convinced and need
the day-to-day usage, this guide is the reference.

## TL;DR

- **Always `cd` to the lane's worktree before dispatching a bg session.**
  Mercury's lane isolation is per-cwd; bg sessions inherit the launch
  cwd. Dispatching from the wrong cwd routes session transcripts under
  the wrong lane's `~/.claude/projects/<encoded-cwd>/` dir.
- **Monitor with `claude agents` (TUI) or `claude agents --cwd <path>`**.
  `claude agents` in non-interactive shells reports unavailable per S99
  empirical — it is a TUI subcommand.
- **bg sessions fire `Stop`, not `SessionEnd`.** Any Mercury hook
  registered only under `SessionEnd` is **skipped** for bg sessions —
  most notably the [#361](https://github.com/392fyc/Mercury/issues/361)
  `cost_tracker.write_session_summary` is NOT invoked, so bg session API
  spend is currently invisible to `MERCURY_SESSION_COST_CEILING_USD`.
  Tracked as [#392](https://github.com/392fyc/Mercury/issues/392)
  (awaiting user A/B/C arbitration).
- **File-editing bg workload triggers HYBRID auto-isolation** — platform
  rejects the first `Edit` with a `tool_use_error`, agent reads the
  error and calls `EnterWorktree`, edits land on
  `worktree-<name>` branch inside
  `<lane-cwd>/.claude/worktrees/<name>/`. **Main lane files are not
  touched.** `ExitWorktree(action:"keep")` leaves the dir + branch on
  disk; cleanup is an operator step, not automatic GC.
- **Kill switch**: `CLAUDE_CODE_DISABLE_AGENT_VIEW=1` env or
  `disableAgentView` setting — agent view becomes a no-op without
  touching any Mercury config.

## 1. Bare bg dispatch convention

The canonical dispatch pattern is "`cd` first, then `claude --bg`":

```bash
# main lane:
cd D:/Mercury/Mercury
claude --bg "<prompt>"

# side-bug lane:
cd D:/Mercury/Mercury-side-bug
claude --bg "<prompt>"

# side-sot (cross-repo):
cd D:/ShipOfTheseus/Ship_of_Theseus
claude --bg "<prompt>"
```

Why explicit `cd` matters:

- Mercury's per-cwd session isolation relies on Claude Code encoding the
  launch cwd into `~/.claude/projects/<encoded-cwd>/` (e.g.
  `D--Mercury-Mercury` vs `D--Mercury-Mercury-side-bug`). See
  [`lane-naming.md`](lane-naming.md) §"Operational expectation" for the
  encoding rules and the canonical-vs-per-cwd split.
- bg sessions inherit the dispatcher's cwd verbatim (Phase 2 empirical
  in S98 ADR §Phase 2; `state.json.cwd` preserved exactly).
- Dispatching from `D:/Mercury/Mercury` while *intending* side-bug work
  routes the session transcript jsonl + per-cwd hook scope under the
  **main** lane's encoded dir — the work content is fine, but lane
  attribution is wrong. Mercury's user-level memory layer (handoffs,
  per-session files) lives at the canonical resolution per
  [`lane-naming.md`](lane-naming.md) §"Operational expectation" and is
  not affected; only the transcripts + cwd-derived hook scope drift.

### Workload types verified

| Workload | Mechanism | Verified |
|----------|-----------|----------|
| Read-only (`echo`, log inspection, `git log`, etc.) | bare dispatch — `isolation:none`, cwd preserved | S98 Phase 2 empirical |
| File-editing (`Edit` / `Write` tool invoked) | HYBRID auto-isolation kicks in — see §4 | S99 Phase 6 empirical |
| `--bg --agent <subagent-name>` (template-selected bg session) | bare dispatch — same as bare bg; `--agent` is template selection NOT nested dispatch, so SubagentStop does NOT fire (by design, see §3) | S99 Phase 6 empirical |
| nested-subagent dispatch inside a bg session | UNVERIFIED — defer until Mercury's subagent dispatch model extends to bg | — |
| Long-running session that triggers `PreCompact` | UNVERIFIED — defer until a real multi-hour bg workload is observed | — |

### Hosted hosting model recap (from docs)

Agent view background sessions are hosted by a per-user supervisor
process. The supervisor enforces a **1h idle timeout** by default; sleep
/ shutdown kills all session processes (state.json persists on disk but
the worker is gone — `claude respawn --all` re-attaches). Pin via
`Ctrl+T` in the TUI to override the idle timeout for a session. None of
this is Mercury infrastructure; Mercury simply documents it so operators
know **bg sessions are not durable across reboot the way lanes are**.

## 2. Monitoring active bg sessions

```bash
claude agents                                    # all lanes (TUI)
claude agents --cwd D:/Mercury/Mercury           # main lane only
claude agents --cwd D:/Mercury/Mercury-side-bug  # side-bug lane only
```

**`claude agents` is a TUI subcommand.** S99 empirical (Phase 6 probe
session): running `claude agents` from a non-interactive Claude Code
session returns `'claude agents' is not available in this environment`.
Use it from a real interactive terminal, not from inside another bg
dispatch.

For a per-lane programmatic group-by-cwd view, Mercury may eventually
ship `scripts/lane-status.sh` ([#389](https://github.com/392fyc/Mercury/issues/389),
P3 OPTIONAL — deferred until operator demand crosses ~1/week, per ADR
§Recommendation). Until that exists, the manual fallback is reading
`~/.claude/jobs/*/state.json` + `~/.claude/daemon/roster.json` directly.
Both file paths are schema-undocumented; Mercury does not commit to
their stability.

## 3. Hook compatibility — `Stop` ≠ `SessionEnd`

Phase 6 empirical (S99 ADR §#4):

| Hook event | Fires in bg session? | Mercury hooks affected |
|------------|----------------------|------------------------|
| `SessionStart` | ✅ | mem0 SessionStart, agentkb session-start, OMC SessionStart |
| `UserPromptSubmit` | ✅ | `.claude/hooks/user-prompt-submit.sh` |
| `PreToolUse` | ✅ | loop-detector, push-guard |
| `PostToolUse` | ✅ | mem0 PostToolUse, OMC post-tool-use |
| `PostToolUseFailure` | ✅ | OMC post-tool-use-failure |
| `Stop` | ✅ | settings.json line 120 `Stop: []` empty — Mercury registers **0** hooks here; only OMC plugins fire |
| **`SessionEnd`** | **❌** | **cost_tracker.write_session_summary, mem0 flush — all SKIPPED for bg** |
| `SubagentStop` | ❌ | does not fire for `--bg --agent X` (template selection ≠ nested dispatch) |
| `PreCompact` | ⚠️ UNVERIFIED | short Phase 6 sessions did not trigger compaction |

**Operator implication**: anything Mercury wires only under
`SessionEnd` is invisible to the bg-session code path. The concrete
Mercury-side fallout is `cost_tracker.write_session_summary` (#361, see
§6) + `mem0_hooks` flush — both registered under the SessionEnd matcher
at `~/.claude/settings.json` line 58 and both skipped for bg. If you
wire any future SessionEnd-only hook, factor in the bg-skip explicitly.

## 4. Auto-worktree behavior — HYBRID enforce-then-comply

This is the most surprising mechanism in Phase 6 (S99 ADR §#1). The
[agent view docs](https://code.claude.com/docs/en/agent-view) say:

> "Before editing files, Claude moves the session into an isolated git
> worktree under `.claude/worktrees/`, so parallel sessions can read the
> same checkout but each writes to its own."

The actual mechanism is **neither pure supervisor magic nor pure agent
autonomy** — it is a two-sided contract:

1. **Platform side** — the first `Edit` against a shared checkout path
   returns a `<tool_use_error>` with literal text:

    ```
    This background session hasn't isolated its changes yet. Call
    EnterWorktree first so edits land in a worktree instead of the shared
    checkout, then retry this edit using the worktree path.
    ```

2. **Agent side** — the bg agent reads the error, `ToolSearch`-loads the
   `EnterWorktree` deferred tool, calls
   `EnterWorktree(name="<auto-name>")`, retries the edit against the
   worktree path. On task completion the agent calls `ExitWorktree(...)`.

The mid-flight `state.json` exposes:

```jsonc
{
  "cwd": "D:\\Mercury\\Mercury",            // top-level cwd unchanged
  "worktreePath": "D:\\Mercury\\Mercury\\.claude\\worktrees\\<name>",
  "worktreeBranch": "worktree-<name>"
}
```

Top-level `cwd` does **not** move (it remains the lane worktree). Only
`worktreePath` + `worktreeBranch` populate while inside the auto-iso
window. `ExitWorktree(action:"keep")` nullifies the fields but the
directory + branch are preserved on disk; `action:"discard"` removes
them. Cleanup is an explicit operator step, not automatic GC.

### What this means for Mercury operators

- File-editing bg workload does NOT pollute the main lane working tree.
  Edits land on `worktree-<auto-name>` inside the lane worktree's
  `.claude/worktrees/<auto-name>/` subdirectory.
- Mercury PreToolUse / PostToolUse hooks fire on `EnterWorktree` /
  `ExitWorktree` themselves — policy hooks could intercept (none ship
  today).
- Commits made inside the auto-worktree land on `worktree-<auto-name>`
  branch. Reconciliation back to `develop` or a lane branch is an
  **operator decision**: cherry-pick, merge, or discard. (Phase 6 probe
  did not commit; commit-fate is inferred from the branch-separation
  mechanics, not empirical — future probe 4 will close the gap.)
- The auto-worktree branch HEAD is selected by an Anthropic-internal
  mechanism that did not match the dispatcher's current branch HEAD in
  S99 empirical (the work branch HEAD was `8ffb17a` but the
  `worktree-state` event recorded `originalHeadCommit: e892920`). Both
  SHAs are in the develop ancestry — not a random commit — but the
  supervisor's selection mechanism between them is undocumented (S99
  ADR §#1 sub-finding). **Mitigation**: if a bg task depends on the
  latest develop state, the dispatch prompt should explicitly cite
  file paths + commit SHA so the agent can self-check, rather than
  assuming the worktree HEAD == lane HEAD.

### Cleanup recipe (operator-driven)

```bash
# Inspect first
git worktree list | grep ".claude/worktrees/<name>"
git -C .claude/worktrees/<name> log --oneline -5

# Discard the isolated work
git worktree remove .claude/worktrees/<name>      # refuses if uncommitted state
git worktree remove --force .claude/worktrees/<name>  # discards uncommitted
git branch -D worktree-<name>
```

**Destructive — read before running.** The `--force` variant discards
uncommitted work permanently; `git branch -D` deletes the branch ref
without merge-safety check. The `<name>` placeholder is operator-supplied
(NOT auto-substituted by the shell) — copy-pasting verbatim deletes a
literal directory named `<name>`. Before running:

1. `git worktree list` and grep for the exact path you intend to remove.
2. `git -C .claude/worktrees/<name> status` to confirm there is no
   committed-but-unpushed work you want to keep.
3. Replace `<name>` with the real worktree name from step 1.

The Rule 2.1 `[a-z0-9-]+` short-name validation does NOT apply to
auto-worktree names (they are Anthropic-generated); audit visually.

## 5. `.claude/worktrees/` namespace coexistence

Mercury has a separate, manual `.claude/worktrees/` use-case predating
agent view: `dev-pipeline` and other workflows create
`.claude/worktrees/<task-id>/` for per-task isolation (see
[`worktree-workflow.md`](worktree-workflow.md)). Agent view's auto-iso
shares the same parent directory.

| Source | Name pattern | Lifecycle | Branch |
|--------|--------------|-----------|--------|
| Mercury manual (dev-pipeline) | task IDs (e.g. `session-22-phase1`, `skill-deep-research`) | created per dev task, removed at PR merge | task-specific |
| Agent view auto-iso | Anthropic-generated names from session/prompt context | created at first Edit attempt, optionally preserved by `ExitWorktree(action:"keep")` | `worktree-<auto-name>` |

**Before deleting any `.claude/worktrees/<name>/`**: verify which source
created it. The pragmatic heuristics:

- If the branch is `worktree-<name>` AND the name has no clear task-ID
  shape → agent view auto-iso.
- If `git -C .claude/worktrees/<name> log --oneline -1` shows commits on
  a `lane/<short>/<N>-*` or `feature/lane-*` branch → Mercury manual.
- When in doubt, check `~/.claude/jobs/*/state.json` for any
  `worktreePath` field referencing the dir; if a session is still alive
  and the worktree is its iso target, removing it will surprise the bg
  agent.

As of 2026-05-15 the main lane checkout's `.claude/worktrees/` carries
two historical entries (`session-22-phase1`, `skill-deep-research`)
from pre-agent-view experiments — they are unrelated to agent view
auto-iso and cleanup is a separate housekeeping item (no Issue filed —
low signal). Verify current state via `git worktree list` before
assuming the same entries are still present.

## 6. Cost-tracker integration gap — bg session API spend invisible

Mercury [#361](https://github.com/392fyc/Mercury/issues/361) registers
`cost_tracker.write_session_summary` under the `SessionEnd` hook matcher
in `~/.claude/settings.json` (the user-level layer, per CLAUDE.md
§"Related Repositories"). Because bg sessions fire `Stop` not
`SessionEnd` (§3), `write_session_summary` is **never invoked for bg**,
and per-session jsonl files are never written under
`~/.claude/scripts/cost-tracker/`. Consequence:

- bg session API costs are NOT counted against
  `MERCURY_SESSION_COST_CEILING_USD`.
- The statusline green/yellow/red ceiling thresholds (70% / 89% / 90%)
  reflect interactive-session spend only.
- A workflow that dispatches many bg sessions can silently exceed the
  perceived daily ceiling.

[#392](https://github.com/392fyc/Mercury/issues/392) tracks the gap with
three fix options for user arbitration:

| Option | Approach | Trade-off |
|--------|----------|-----------|
| A | Register `cost_tracker.write_session_summary` under the `Stop` matcher in `~/.claude/settings.json` (parallel to SessionEnd) | Minimal change; needs to verify the Stop hook payload exposes the fields `write_session_summary` reads from the SessionEnd payload (audit the session-end.py invocation before mirroring) |
| B | Periodic background sweep — `cost_tracker.py` scans `~/.claude/jobs/*/state.json` for `linkScanPath` jsonl + parses usage events | Robust against Stop hook payload divergence; invasive (new daemon-ish behavior, new failure modes) |
| C | Document the gap, add a statusline marker for "+N bg sessions outside ceiling tracking" | Zero implementation cost; ceiling protection effectively halved |

Until #392 lands, operators should treat bg session spend as untracked
and budget separately. Mercury's `mem0_hooks` + `cost_tracker` storage
are both `sessionId`-keyed and cwd-independent (verified in S99 ADR §#2)
— there is no routing-bleed risk per se, only an invocation gap.

## 7. Disable channel

Two equivalent kill switches, both Anthropic-provided (Mercury did not
build these):

- **Env**: `CLAUDE_CODE_DISABLE_AGENT_VIEW=1` (one-shot at launch, or
  exported)
- **Setting**: `disableAgentView` in `~/.claude/settings.json` (or
  project `.claude/settings.json`)

When either is active, agent view becomes a no-op:

- The `claude agents` TUI subcommand reports unavailable.
- `claude --bg` dispatches still work (they predate agent view), but
  the in-flight UI overlay does not appear.
- Mercury's lane protocol continues to function unchanged.

This is what makes Path B (from the ADR) detachable per Mercury CLAUDE.md
§MUST "modular design": the entire agent view layer can be removed by
flipping one flag, with zero residual Mercury-side cleanup. If your
workflow does not need bg dispatch at all, disable it and the
multi-lane protocol remains intact.

## Appendix — quick reference

### Dispatch from each Mercury lane (current 4-lane registry)

```bash
# main lane
cd D:/Mercury/Mercury && claude --bg "<prompt>"

# side-bug lane
cd D:/Mercury/Mercury-side-bug && claude --bg "<prompt>"

# side-sot lane (cross-repo, host Godot game repo)
cd D:/ShipOfTheseus/Ship_of_Theseus && claude --bg "<prompt>"

# health lane (conversation-only, no worktree — see LANES.md §`health`)
# Bg dispatch from this lane is unusual; if it ever happens, also set:
#   MERCURY_MEM0_DISABLED=1   # privacy boundary per LANES.md §`health`
# to keep PHI-adjacent content out of the mem0 Qdrant store.
```

Lane worktree paths come from each lane's `Worktree path` field in
`LANES.md` (canonical user-memory file, not in this repo) per Rule 5.1
(see [`lane-naming.md`](lane-naming.md) §"Worktree path convention"
and §"Cross-repo lane variant"). Operators on other machines substitute
their own `<repo-root>` per CLAUDE.md §MUST install-to-D-drive
(Windows team policy; non-Windows operators use their convention).

### What Mercury does NOT promise

- Stability of `~/.claude/jobs/*/state.json` or
  `~/.claude/daemon/roster.json` schemas — these are
  Anthropic-internal, observed empirically by Phase 2 + Phase 6, may
  change without notice.
- That `--bg` flag itself is GA — CLI v2.1.142 `--help` does not list
  it; `claude --bg` works empirically. If Anthropic removes or renames
  it, this guide will need to follow suit.
- That auto-worktree branch HEAD selection is documented — S99 observed
  a discrepancy between the dispatcher's HEAD and the recorded
  `originalHeadCommit`; mitigation is dispatch-prompt-side, not
  Mercury-side.

### Cross-links

- ADR: [`agent-view-multi-lane-adaptation-2026-05.md`](../research/agent-view-multi-lane-adaptation-2026-05.md)
  (Path A/B/C eval — Path B PRIMARY)
- ADR: [`agent-view-phase6-empirical-2026-05.md`](../research/agent-view-phase6-empirical-2026-05.md)
  (file-editing bg + hook lifecycle + cost-tracker gap empirical)
- [`lane-naming.md`](lane-naming.md) — lane short name + Worktree path
  convention + Δ10/Δ11 handoff/lane-assertion contracts
- [`worktree-workflow.md`](worktree-workflow.md) — task-level worktree
  scope (orthogonal to agent view auto-iso, shares
  `.claude/worktrees/` parent)
- [Anthropic: Manage multiple agents with agent view](https://code.claude.com/docs/en/agent-view)
- [Anthropic: Hooks reference](https://code.claude.com/docs/en/hooks)
- [Anthropic: Subagents](https://code.claude.com/docs/en/sub-agents)
