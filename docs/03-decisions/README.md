# Decisions

The decision register: one row per decision in force, newest first. A new decision adds its row here; when it
overturns an earlier one, that row and its record are deleted in the same pull request. The rules that follow from
these decisions are in [01-rules](../01-rules/README.md). Records of retired systems were deleted in #736 and remain
in Git history.

| Document | Status | Content |
|---|---|---|
| [DEC-2026-11-12-downstream-feedback.md](DEC-2026-11-12-downstream-feedback.md) | current | Agents in projects that install Mercury report harness problems as Mercury Issues themselves; a standing authorization limited to opening and commenting on such Issues; Mercury's main agent works from them. |
| [DEC-2026-11-10-pr-procedure-argus.md](DEC-2026-11-10-pr-procedure-argus.md) | current | Argus reviews only on request; the main agent merges its own PR once every gate holds (one Argus review of the final head, no unresolved thread, agents never resolve threads); protected default and integration branches in all five repositories. |
| [DEC-2026-11-05-harness-monitor.md](DEC-2026-11-05-harness-monitor.md) | current | `tools/harness_monitor/` is the named exception to M1: a run-once report on whether agents use what the harness configures |
| [DEC-2026-08-14-codex-primary-harness.md](DEC-2026-08-14-codex-primary-harness.md) | current | Codex CLI becomes the primary harness: personal-subscription auth, three enforcement layers, no Git writes under the sandbox |

Some decisions are recorded as ADR-style research reports in [04-research](../04-research/README.md), for
example the context strategy (2026-05) and settings permissions versus hooks (2026-06).
