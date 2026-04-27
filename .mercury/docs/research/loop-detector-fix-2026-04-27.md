# Loop-Detector False-Positive Fix — Research & Decision

**Issue**: [#325](https://github.com/392fyc/Mercury/issues/325) — `feat(loop-detector): broaden progress signal — Bash/Agent should not count toward no_progress`
**Lane**: `lane:main` (cross-lane impact, claimed via coordination Issue)
**Session**: S80 main lane, 2026-04-27
**Branch**: `feat/loop-detector-progress-signal`

## Problem

`adapters/mercury-loop-detector/hook.cjs:108` (in `update()` defined at line 96) only resets `np_count` on `Write/Edit/NotebookEdit/MultiEdit/Read/Glob/Grep`. `Bash`, `Agent`, `Task*`, `Skill`, `ToolSearch` accumulate `np_count` even when they represent legitimate work. Verification phases (test runs, git status, gh polling, smoke tests) trip the default `no_progress_threshold=5` after a handful of legitimate Bash calls.

**S79 main lane empirical hit**: ≥5× false positives during PR #335 (Phase C) Argus review polling, CronCreate/Delete operations, parallel research Read fan-out.
**S4-side-multi-lane historical hit**: 3× consecutive false positives during a single verification phase.

## Design space (per #325 body)

| Option | Approach | Risk | Cost |
|--------|----------|------|------|
| **A** | Add `PROGRESS_TOOLS` set (Bash/Agent/Task*/Skill/ToolSearch) — these reset `np_count` | Low — still catches WebSearch/WebFetch loops | ~5 LOC + tests |
| B | Raise `no_progress_threshold` 5 → 12-15 | Medium — masks real stalls | ~1 LOC config |
| C | Rewrite as idle timer on `SessionIdle`/`Stop` hook | High — semantic refactor | Days |

## Decision: **Option A**

**Rationale**:
1. Surgical — single counter behaviour change, no config break, no semantics change for other 3 signals (`duplicate_call`, `same_error`, `read_write_ratio` unchanged).
2. Preserves true-stall detection: `WebSearch`/`WebFetch`/`mcp__*` loops without artifacts still accumulate `np_count`. Detector remains useful.
3. Issue body's recommended path. Mirror's the existing pattern of explicit `WRITE_TOOLS`/`READ_TOOLS` sets — easy to extend later.
4. B alone shifts the threshold but never solves the categorical mis-classification (Bash burst still trips 12 calls eventually).
5. C would be correct-er but needs a Stop-hook redesign — out of scope for a P1 hotfix.

## Implementation plan

### `hook.cjs` (post-fix line numbers)

1. `PROGRESS_TOOLS` set declared at lines 80-81 (after the `READ_TOOLS` line 74 + 5-line comment block at 75-79):
   ```js
   const PROGRESS_TOOLS = new Set(['Bash', 'Agent', 'Skill', 'ToolSearch',
     'Task', 'TaskCreate', 'TaskUpdate', 'TaskList', 'TaskGet', 'TaskOutput', 'TaskStop']);
   ```
2. `is_progress` flag computed in `main()` and passed to `update()` (line 96 signature):
   ```js
   const is_progress = PROGRESS_TOOLS.has(tool_name);
   ```
3. `np_count` reset condition at line 108:
   ```js
   if (is_write || is_read || is_progress || errored) { state.np_count = 0; } else { state.np_count++; }
   ```

**Rationale for PROGRESS_TOOLS membership**:
- `Bash` — git, gh, tests, scripts (the dominant false-positive source).
- `Agent` — sub-agent dispatch is high-cost legitimate work.
- `Task` + `TaskCreate`/`Update`/`List`/`Get`/`Output`/`Stop` — task tracking, by definition progress.
- `Skill` — invoking skills is a deliberate workflow step.
- `ToolSearch` — schema fetching for deferred tools is necessary setup.

**Tools intentionally NOT in PROGRESS_TOOLS** (still increment `np_count`):
- `WebSearch`, `WebFetch` — research loops without writes are the true-stall pattern detector should keep catching.
- `mcp__*` — MCP tool calls are heterogeneous; default to "must produce write" semantics.
- `EnterPlanMode`/`ExitPlanMode`/`EnterWorktree`/`ExitWorktree` — mode toggles are bookkeeping, not work.
- `Monitor`, `ScheduleWakeup`, `Cron*`, `RemoteTrigger`, `PushNotification`, `AskUserQuestion` — orchestration / interaction, not progress.

### Tests (`hook.test.cjs`)

Add new `describe` block: `update() progress signal classification`. Tests:
1. `Bash with non-error response resets np_count` — pre-seed `np_count: 4`, fire Bash success → expect `np_count: 0`.
2. `Agent call resets np_count` — pre-seed `np_count: 4`, fire Agent success → expect `np_count: 0`.
3. `Task* tools all reset np_count` — parametric across `Task`, `TaskCreate`, `TaskUpdate`, `TaskList`, `TaskGet`, `TaskOutput`, `TaskStop`.
4. `Skill resets np_count` — same.
5. `ToolSearch resets np_count` — same.
6. `WebSearch still increments np_count` — pre-seed `np_count: 4`, fire WebSearch → expect `np_count: 5` (still trips threshold).
7. `WebFetch still increments np_count` — same shape.
8. `mcp__foo still increments np_count` — sample MCP call → still increments.
9. **End-to-end regression**: 5 consecutive Bash calls with successful responses do NOT trigger block (was previously the false-positive pattern).
10. **End-to-end stall preservation**: 5 consecutive WebSearch calls DO trigger block (true-stall pattern).

To support unit tests of the `update()` helper, either:
- (a) Export `update` from `hook.cjs` (small surface change), OR
- (b) Mirror `update` inline in `hook.test.cjs` (matches existing `detectStall` mirror pattern).

**Choosing (a)** — exporting is cleaner and avoids the drift risk of a parallel mirror. Add `if (require.main === module) main(); else module.exports = { update, detectStall, PROGRESS_TOOLS };` at the bottom. Existing tests already use child-process spawn for hook.cjs invocation, so dual-mode export is consistent.

### Config doc (`.mercury/config/loop-detector.json`)

No schema change — `no_progress_threshold` keeps default `5`. Behaviour change documented in the loop-detector adapter README/install doc (if any) and in the Issue body / PR description.

### LOC budget (post-fix actuals)

- `hook.cjs`: 175 → 186 LOC. Net +11 (PROGRESS_TOOLS set + 5-line comment block + threaded `is_progress` flag + dual-mode export wrapper).
- `hook.test.cjs`: +~190 lines for 13 unit tests + 2 ETE tests.
- Adapter cap from PR #335 follow-up (#336): 186 LOC well under the 200-cap target the side lane is enforcing.

## Acceptance criteria

1. All 10 new tests pass.
2. All 137 regression tests still pass.
3. Empirical: in S80 own session, no false-positive `no_progress` triggered after the fix lands and the user-level loop-detector hook reloads with the patched code (will become apparent in S81+).
4. `hook.cjs` LOC ≤ 200.
5. Cross-platform — tests run on the same Windows/MINGW + Unix matrix the existing suite covers.

## Out of scope (this PR)

- Option B threshold tuning — addressable later if Option A still trips edges.
- Option C SessionIdle redesign — defer until Anthropic exposes SessionIdle hook event (currently only PostToolUse + Stop).
- `mcp__*` whitelisting — requires per-server analysis; revisit if MCP tool surfaces become dominant work pattern.
- Telemetry / per-tool false-positive rate — would help future tuning but adds state-file schema changes.

## References

- Issue #325 body — full design space + recommended patch sketch
- `adapters/mercury-loop-detector/hook.cjs:73-108` — `WRITE_TOOLS`/`READ_TOOLS`/`PROGRESS_TOOLS` declarations (lines 73-81) + `update()` function (lines 96-109) with the line-108 reset condition
- `adapters/mercury-loop-detector/hook.test.cjs:55-93` — existing detector regression coverage
- `memory/feedback_lane_protocol.md` — Rule 8 cross-lane coordination (this Issue is `lane:main` per coordination)
- S79 handoff doc — Loop-detector false-positive pattern documented in compact-loss section
