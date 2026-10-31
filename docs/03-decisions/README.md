# Decisions

The decision register: one row per decision in force, newest first. A new decision adds its row here; when it
overturns an earlier one, that row and its record are deleted in the same pull request. The rules that follow from
these decisions are in [01-rules](../01-rules/README.md). Records of retired systems were deleted in #736 and remain
in Git history.

| Document | Status | Content |
|---|---|---|
| [DEC-2026-08-14-codex-primary-harness.md](DEC-2026-08-14-codex-primary-harness.md) | current | Codex CLI becomes the primary harness: personal-subscription auth, three enforcement layers, no Git writes under the sandbox |

Some decisions are recorded as ADR-style research reports in [04-research](../04-research/README.md), for
example the context strategy (2026-05) and settings permissions versus hooks (2026-06).
