# Multi-Lane Protocol — in-repo rule index

Issue [#618](https://github.com/392fyc/Mercury/issues/618). This page is the **authority for which lane rules are
in force** and where each is defined and enforced. The rules used to live in
the user-memory file `feedback_lane_protocol.md`. That file was lost in the
#579 machine migration and was never in the repo, so every rule now points
to repo sources instead.

## Scope

- **Lane home.** There is one lane home per project, resolved from the project's
  main checkout: `scripts/lane-paths.sh lanes-file` / `handoff-dir`
  ([#613](https://github.com/392fyc/Mercury/issues/613)). Create it with
  `scripts/lane-init.sh`.
- **Lane kinds.** A lane is either solo (no `Peers`) or paired with other lanes.
  Each lane is run by one harness (`claude` or `codex`). Cross-harness rules
  are in the [#599 ADR](../research/issue-599-cross-harness-lane-isolation-2026-09.md).
- **No cap.** Lanes are uncapped ([#605](https://github.com/392fyc/Mercury/issues/605)).
- **Messages from other lanes** follow the receiver rule in `AGENTS.md`,
  under "Cross-lane messages" ([#615](https://github.com/392fyc/Mercury/issues/615)).
- **Downstream lane contract.** Other repositories receive
  `.mercury/templates/codex-project/project/mercury-lane-contract.md`
  ([#629](https://github.com/392fyc/Mercury/issues/629)): user-declared lanes,
  per-agent handoffs, version preconditions for shared files, the cross-lane
  receiver rule, controlled entries, and enforced write guards. Its lane model
  extends Mercury's: the `Harness` of a lane is its **lead harness**, which
  holds formal judgment, and the other harness may join for bounded work such
  as review without taking over. Mercury's own lanes record only the lead
  harness so far and pair lanes as peers
  ([#599 ADR](../research/issue-599-cross-harness-lane-isolation-2026-09.md)).
  How far Mercury itself implements the other requirements is tracked in the
  rules table below. Each downstream project maps the contract to its own
  memory location and scripts.

## Rules

The protocol has eight rules. Version 1 was accepted on 2026-05-03 (#347,
epic #315). The fullest in-repo restatement of all eight is
[articles/001-mercury-harness-overview.md](../../../articles/001-mercury-harness-overview.md)
§"8 条规则全景". The guides and scripts below are the working definitions.

| Rule | In force | Defined / enforced in |
|------|----------|-----------------------|
| 1 — claim an Issue with a `lane:<name>` label before working on it | yes | [lane-claim.md](lane-claim.md), `scripts/lane-claim.sh` |
| 1.1 — probe-after-write: re-query after labelling, stop on a second `lane:*` label | yes | [lane-claim.md](lane-claim.md), `scripts/lane-claim.sh` |
| 2 — per-lane branch namespace; legacy `feature/lane-<lane>/TASK-<N>-*` still accepted | yes | [lane-naming.md](lane-naming.md), `scripts/lane-assertion.sh` (`scripts/codex/guard.ps1` accepts only the short form) |
| 2.1 — short prefix `lane/<short>/<N>-<slug>`: branch ≤40 chars, short name ≤8 chars and unique | yes | [lane-naming.md](lane-naming.md) §Δ6, `scripts/lane-spawn.sh`, `scripts/lane-assertion.sh`, `scripts/codex/guard.ps1` |
| 3 — tmp isolation: each lane keeps scratch files in `.tmp/lane-<lane>/` | yes | [lane-close.md](lane-close.md), `scripts/lane-close.sh` |
| 3.1 — 14-day stale lane sweep (branch, handoff and Issue signals; report-only) | yes | [lane-sweep.md](lane-sweep.md), `scripts/lane-sweep.sh` |
| 3.2 — `.tmp/lane-<lane>/` pruned when the lane closes | yes | [lane-close.md](lane-close.md), `scripts/lane-close.sh` |
| 4 — only the main lane edits `DIRECTION.md` / `EXECUTION-PLAN.md`; other lanes open an Issue | yes | [lane-emergency-escalation.md](lane-emergency-escalation.md) |
| 4.1 — emergency escalation when main is idle >48h | yes | [lane-emergency-escalation.md](lane-emergency-escalation.md), `scripts/check-main-idle.sh` |
| 5 — per-lane state: each lane has its own `session-handoff[-<lane>].md` and session files | yes | [lane-naming.md](lane-naming.md) §Operational expectation, `scripts/lane-paths.sh handoff-file --lane <lane>` |
| 5.1 — one worktree per lane, declared as `Worktree path` (Δ9); `/handoff:auto` launch (Δ10); `[LANE=<name>]` marker check (Δ11); cross-repo variant (#374) | yes | [lane-naming.md](lane-naming.md) §worktree isolation, `scripts/lane-assertion.sh`, `scripts/handoff-launch.sh`, `.agents/skills/handoff/SKILL.md` Step 5 |
| 6 — `LANES.md` is the single lane registry; each lane edits only its own section, and only the owning lane closes itself | yes | [lane-close.md](lane-close.md), [lane-sweep.md](lane-sweep.md), `scripts/lane-spawn.sh`, `scripts/lane-close.sh` |
| 7 — per-session files `sessions/S<N>(-<lane>).md`; `MEMORY.md` / `SESSION_INDEX.md` regions regenerated from them | yes | [memory-index-regenerate.md](memory-index-regenerate.md), `scripts/regenerate-memory-index.sh` |
| Δ7 — hard cap on active lanes | **no**, removed by #605 | [lane-naming.md](lane-naming.md) §Δ7 (historical) |
| 8 — lane lifecycle autonomy: each lane owns the full lifecycle of its claimed Issues as a parallel chain, not a sub-agent of another lane; the only limits are Rule 4 and explicit cross-lane coordination Issues (earlier form: the side-lane "autonomous chain") | yes | [agent-view-multi-lane-adaptation-2026-05.md](../research/agent-view-multi-lane-adaptation-2026-05.md), [agent-team-orchestration-feasibility-2026-04-26.md](../research/agent-team-orchestration-feasibility-2026-04-26.md); no guide yet |
| Cross-harness lanes (Harness / Session / Peers, provenance tag) | yes | [#599 ADR](../research/issue-599-cross-harness-lane-isolation-2026-09.md), `AGENTS.md` "Cross-lane messages" |

The deltas proposed after the first protocol cut, with their evidence, are in
[lane-protocol-v0.1-deltas.md](../lane-protocol-v0.1-deltas.md). The design
research is in
[multi-lane-protocol-2026-04-25.md](../research/multi-lane-protocol-2026-04-25.md).

## Changing a rule

Change this index in the same PR as the guide or script that implements the
change. Do not recreate a user-memory protocol file: user memory is
machine-local and does not travel with the repo.

## History

- 2026-04: the first protocol cut (Rules 1–7) was written in user-memory
  `feedback_lane_protocol.md`; later sub-rules and Rule 8 were added there.
- 2026-05-03: version 1 (eight rules) accepted (#347, epic #315).
- 2026-09: the file was lost in the #579 machine migration, and this index
  became the authority (#618).
