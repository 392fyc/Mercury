# Issue Triage Policy

> **History.** This policy describes the retired orchestrator workflow (IssueBundle, `global-index.json`). Current work follows GitHub Issues as described in `AGENTS.md`.

## Purpose

Defines how newly discovered issues enter, interrupt, or stay outside the active task flow.

## Intake Rule

- Every newly discovered non-trivial issue must be recorded as an IssueBundle before it changes the active queue.
- Discovery sources: human, Main Agent, Dev Agent, Acceptance Agent, or automation.
- No direct reprioritization from chat alone — use the Issue → Triage → Task flow.

## Priority Definitions

| Severity | Meaning | Required Action |
|----------|---------|-----------------|
| `Sev-0` | Actively blocks progress or invalidates current acceptance | Interrupt now |
| `Sev-1` | Does not fully block but must be resolved in current phase | Resolve in current phase |
| `Sev-2` | Important but may be scheduled into milestone planning | Schedule to milestone |
| `Sev-3` | Known issue or weak signal | Archive as known issue |

## Decision Rules

| Decision | When to Use |
|----------|-------------|
| `interrupt_now` | Sev-0 blocker, broken source of truth, invalid runtime environment |
| `attach_to_current_task` | Issue is small and sits fully inside current task scope |
| `schedule_to_milestone` | Issue is valid but should be handled in normal queue |
| `archive_as_known_issue` | Issue is recorded but intentionally deferred |

## Authority

| Action | Owner |
|--------|-------|
| Create IssueBundle | Any role |
| Triage and prioritize | Main Agent |
| Change active queue | Main Agent |
| Approve Sev-0 interruption | Human or Main Agent |
| Close Issue | Main Agent |

## Issue Closure Sync

When an issue status changes to `resolved`, Main Agent must update:

1. **IssueBundle** — set `resolution` in the JSON file
2. **Global index** — update `status` in `issueIndex`, remove from phase `activeIssueIds`

## Phase Attachment Rule

- All issues enter `01-registry/global-index.json` first
- An issue is added to a phase's `activeIssueIds` only after triage explicitly attaches it
- Sev-2 and Sev-3 issues may remain global only
