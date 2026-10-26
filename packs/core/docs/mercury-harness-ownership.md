# Mercury harness file ownership

Installed by Mercury (pack `core`) at `.mercury/packs/core/docs/mercury-harness-ownership.md`; do not edit the
installed copy.

Mercury (`tools/mercury.py`) installs packs into a project. In the project, Mercury is the sole source of truth
for the following paths. They are generated or copied from a pack and recorded in `.mercury/lock.json` with a hash:

- `.mercury/packs/` (every installed pack file, including these contracts)
- `.mercury/lock.json`
- `.claude/skills/mercury-*/` and `.agents/skills/mercury-*/` (thin skill entries)
- `.claude/agents/mercury-*.md` and `.codex/agents/mercury-*.toml` (the roles, generated from the pack's `roles/`)
- `.codex/rules/mercury-*.rules` (Git safety rules)
- the section of `AGENTS.md` and of `CLAUDE.md` between the `MERCURY:BEGIN` and `MERCURY:END` markers

The project must not edit these paths by hand or initiate changes to their contents. A hand edit stops the next
`apply` and is never overwritten silently. A change to a managed path originates in a Mercury Issue and pull request, is made in the Mercury source (the pack
under `packs/`, or the tool), and then reaches the project by running `python tools/mercury.py apply --target <project>`
from a Mercury checkout.

The project owns everything else: its code, domain skills, domain rules, configuration, workflows, and the part of
`AGENTS.md` and `CLAUDE.md` outside the markers (which Mercury never modifies). `.mercury/config.json` is the
project's own file: it selects the packs, declares linked repositories (`linked_repos`), and sets parameters
(`params`). Linked repositories are listed in the managed section of `AGENTS.md`; writing to one needs its own
authorized scope.

Read-only roles (reviewer, acceptance, design, research) must not write. On Codex the sandbox enforces this; on
Claude Code the role carries a `PreToolUse` guard that blocks write commands. A project can name more write-type
scripts for the guard in `params.guard_write_scripts`.
