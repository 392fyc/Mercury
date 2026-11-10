# Scenario gate: project settings

The scenario gate is a user-level hook (Mercury #779). It makes two things checkable instead of merely requested:
external claims are looked up and verified, and a project's own rules are read before work that depends on them.
What the hook does, and what it cannot do, is described in Mercury's guide `docs/02-guides/scenario-gate.md`.

A project takes part by keeping one file, `.mercury/gate.json`, at its root. The file belongs to the project: Mercury
ships the template next to this page (`mercury-gate.template.json`) and never writes or updates the project's copy.
Copy the template to `.mercury/gate.json`, commit it, and adjust it to the repository.

| Key | Meaning |
|---|---|
| `required_reading` | Files, relative to the project root, that must be read in full before a file of the project is modified, and before a design is delivered. The record is forgotten at every compaction and at `/clear`, so they are read again then. |
| `design_lanes` | Lane names. A session whose prompt starts with `[LANE=<name>]` for one of them has the design check on for the whole session. |
| `exempt` | Extra glob patterns, relative to the project root, for files that may be modified without the reading. `.tmp/*` and `.handoff/*` are always exempt. |

Choosing the list:

- List what a change must not contradict: the rule table, the decision or principles page, standing preferences. Do
  not list instruction files the host already loads (`AGENTS.md`, `CLAUDE.md`).
- Keep it short. Every listed file is read again after each compaction, by the main session and by every subagent
  that modifies files.
- A file the Read tool cannot return in one call needs `allow_large: true`; a file over the tool's size limit cannot
  be satisfied at all, so split it or list a shorter digest.
- Entries that point outside the project or to a file that does not exist are not enforced; the hook reports them
  once per session.

A project without `.mercury/gate.json` is not gated: the research and design checks still work there, without a
reading requirement.
