# Scenario gate: project settings

The scenario gate is a user-level hook (Mercury #779). It makes two things checkable instead of merely requested:
external claims are looked up and verified, and a project's own rules are read before work that depends on them.
What the hook does, and what it cannot do, is described in Mercury's guide `docs/02-guides/scenario-gate.md`.

A project takes part by keeping one file, `.mercury/gate.json`, at its root. The file belongs to the project: Mercury
ships the template next to this page (`mercury-gate.template.json`) and never writes or updates the project's copy.
Copy the template to `.mercury/gate.json`, commit it, and adjust it to the repository.

| Key | Meaning |
|---|---|
| `required_reading` | Files, relative to the project root, that must be read in full before any file of the project is modified, and before a design is delivered. The record is forgotten at every compaction and at `/clear`, so they are read again then. |
| `scoped_reading` | A list of groups `{"paths": [...], "read": [...]}`. Before a file whose path matches one of a group's `paths` patterns is modified, that group's `read` files must have been read as well. A file can match several groups; all of them apply. |
| `design_lanes` | Lane names. A session whose prompt starts with `[LANE=<name>]` for one of them has the design check on for the whole session. |
| `exempt` | Extra glob patterns, relative to the project root, for files that may be modified without the reading. `.tmp/*`, `.handoff/*` and `.mercury/memory/*` are always exempt. |

Patterns in `paths` and `exempt` are matched against the path relative to the project root, and `*` also matches
across `/`: `src/*` covers every file below `src/`. These are not gitignore patterns: `**` means the same as `*`, so
`src/**/*.py` needs at least one folder between `src/` and the file and misses `src/x.py`; write `src/*.py` for all of
them. An absolute pattern never matches. A pattern that matches nothing leaves those paths ungated without a notice,
so try a new group once.

Example: a rule page for everyone, one more page for the battle code, and the design rules only for those who change
the design documents.

```json
{
  "required_reading": ["docs/rules/core.md"],
  "scoped_reading": [
    {"paths": ["src/battle/*"], "read": ["docs/rules/battle.md"]},
    {"paths": ["docs/design/*"], "read": ["docs/rules/design-rules.md"]}
  ],
  "design_lanes": [],
  "exempt": ["assets/*"]
}
```

Choosing the lists:

- List what a change must not contradict: the rule table, the decision or principles page, standing preferences. Do
  not list instruction files the host already loads (`AGENTS.md`, `CLAUDE.md`).
- Keep the general list short. Every file in it is read again after each compaction, by the main session and by
  every subagent that modifies files, whatever they change. Put a page that binds only one kind of change (the rules
  of one subsystem, the discipline of one lane) into a group for the paths that change touches. A project where
  several lanes work on different parts needs this; so does a rule document split into a core page and topic pages.
- A group can only name files of this project. Reading that has to come from elsewhere (another repository, a
  service) cannot be enforced here: list the page that tells the agent how to fetch it.
- The design check has no path to go by, so it names the general list and every group, and the agent reads the
  groups that bear on the output.
- A file the Read tool cannot return in one call needs `allow_large: true`; a file over the tool's size limit cannot
  be satisfied at all, so split it or list a shorter digest.
- Entries that point outside the project or to a file that does not exist are not enforced; the hook reports them
  once per session.

A project without `.mercury/gate.json` is not gated: the research and design checks still work there, without a
reading requirement.
