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

## Rules

"Reconstructed" means the original wording survives only in the lost
user-memory file. Those rows restate the rule from how the repo's guides and
scripts describe it.

| Rule | In force | Defined / enforced in |
|------|----------|-----------------------|
| 1 — Issue claim by `lane:<name>` label | yes | [lane-claim.md](lane-claim.md), `scripts/lane-claim.sh` |
| 1.1 — probe-after-write claim verification | yes | [lane-claim.md](lane-claim.md), `scripts/lane-claim.sh` |
| 2 — per-lane branch namespace (legacy `feature/lane-<lane>/TASK-*`) | yes, legacy form still accepted | [lane-naming.md](lane-naming.md) |
| 2.1 — short branch prefix `lane/<short>/<N>-<slug>` (short name ≤8 chars, unique) | yes | [lane-naming.md](lane-naming.md) §Δ6, `scripts/lane-spawn.sh`, `scripts/codex/guard.ps1` |
| 3 — a lane is closed by its owning lane (reconstructed) | yes | [lane-close.md](lane-close.md) |
| 3.1 — 14-day stale lane sweep | yes | [lane-sweep.md](lane-sweep.md), `scripts/lane-sweep.sh` |
| 3.2 — `.tmp/lane-<lane>/` prune on close | yes | [lane-close.md](lane-close.md), `scripts/lane-close.sh` |
| 4 — main lane alone edits `DIRECTION.md` / `EXECUTION-PLAN.md` | yes | [lane-emergency-escalation.md](lane-emergency-escalation.md) |
| 4.1 — emergency escalation when main is idle >48h | yes | [lane-emergency-escalation.md](lane-emergency-escalation.md), `scripts/check-main-idle.sh` |
| 5 — per-lane state separation (reconstructed) | yes | [lane-naming.md](lane-naming.md) §worktree isolation |
| 5.1 — one worktree per lane, declared as `Worktree path` (Δ9); `[LANE=<name>]` marker check (Δ10/Δ11) | yes | [lane-naming.md](lane-naming.md), `scripts/lane-assertion.sh` |
| 6 — each lane edits only its own `LANES.md` section | yes | [lane-close.md](lane-close.md), `scripts/lane-spawn.sh` |
| 7 — per-session memory files plus a generated index (replaced the append-only shared index) | yes | [memory-index-regenerate.md](memory-index-regenerate.md), `scripts/regenerate-memory-index.sh` |
| Δ7 — hard cap on active lanes | **no**, removed by #605 | [lane-naming.md](lane-naming.md) §Δ7 (historical) |
| 8 — autonomous side-lane chain: a side lane advances its own claimed Issues without per-step user approval, within its authorization (reconstructed) | yes; no guide yet | described only in research: [agent-team-orchestration-feasibility-2026-04-26.md](../research/agent-team-orchestration-feasibility-2026-04-26.md) |
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
- 2026-09: the file was lost in the #579 machine migration, and this index
  became the authority (#618).
