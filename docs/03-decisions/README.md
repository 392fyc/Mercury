# Decisions

The decision register: one row per decision record, newest first. A new decision adds its row here and, when it
replaces an earlier one, marks that row `superseded` with a link to the new record. The rules that follow from
current decisions are in [01-rules](../01-rules/README.md); Mercury's numbered DEC series is in
[mercury/](mercury/README.md).

| Document | Status | Content |
|---|---|---|
| [DEC-2026-08-14-codex-primary-harness.md](DEC-2026-08-14-codex-primary-harness.md) | current | Codex CLI becomes the primary harness: personal-subscription auth, three enforcement layers, no Git writes under the sandbox |
| [DEC-2026-03-17-session-role-history-approval-v2.md](DEC-2026-03-17-session-role-history-approval-v2.md) | history | Session invariants, frozen prompt, history and approval of the retired orchestrator |
| [DESIGN-084-transport-adapter.md](DESIGN-084-transport-adapter.md) | history | Transport adapter of the retired orchestrator |

Some decisions are recorded as ADR-style research reports in [04-research](../04-research/README.md), for
example the context strategy (2026-05) and settings permissions versus hooks (2026-06).
