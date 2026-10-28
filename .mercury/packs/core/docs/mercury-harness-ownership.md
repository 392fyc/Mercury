# Mercury harness file ownership

Installed by Mercury (pack `core`) at `.mercury/packs/core/docs/mercury-harness-ownership.md`; do not edit the
installed copy.

Mercury (`tools/mercury.py`) installs packs into a project. A project therefore has two areas that the tool writes, and
everything else.

## The Mercury area

In the project, Mercury is the sole source of truth for the following paths. They are generated or copied from a pack
and recorded in `.mercury/lock.json` with a hash:

- `.mercury/packs/` (every installed pack file, including these contracts)
- `.mercury/lock.json`
- `.claude/skills/mercury-*/` and `.agents/skills/mercury-*/` (thin skill entries)
- `.claude/agents/mercury-*.md` and `.codex/agents/mercury-*.toml` (the roles, generated from the pack's `roles/`)
- `.codex/rules/mercury-*.rules` (Git safety rules)
- `.github/workflows/mercury-*.yml` (the update and verify workflows that the `deploy` pack renders, when selected)
- the section of `AGENTS.md` and of `CLAUDE.md` between the `MERCURY:BEGIN` and `MERCURY:END` markers

The project must not edit these paths by hand or initiate changes to their contents. A hand edit stops the next
`apply` and is never overwritten silently. A change to a managed path originates in a Mercury Issue and pull request, is made in the Mercury source (the pack
under `packs/`, or the tool), and then reaches the project by running `python tools/mercury.py apply --target <project>`
from a Mercury checkout.

## The project area

The project owns its own roles and skills and declares them in `.mercury/local.json`; Mercury updates never change them.
The tool only generates, checks and retires their host files:

- the project owns and edits `.mercury/local.json`, the role sources `.mercury/local/roles/<role>.md`, and each skill's
  full directory (the `path` the manifest names);
- the tool generates `.claude/agents/<role>.md`, `.codex/agents/<role>.toml` and the thin skill entries
  `.claude/skills/<skill>/SKILL.md` and `.agents/skills/<skill>/SKILL.md` (none where the skill directory itself is the
  entry location), and records them and their sources in the `local` section of `.mercury/lock.json`.

Create, update and retire through `python tools/mercury.py apply` (add an entry and its source; change the source; remove
the entry). Do not edit a generated file: a hand edit stops the next `apply`. Retiring deletes only generated files that
the lock's `local` section recorded, never a source. Taking over an existing project file at a generated path for the
first time needs the explicit `--adopt-local` flag. A Mercury update pull request never changes the manifest, a source
or the set of generated files; it may only carry generated files regenerated from unchanged sources when a Mercury
release renders them differently, and anything else in the area is refused. Names in this area never start with `mercury-`.

## Everything else

The project owns everything else: its code, domain skills, domain rules, configuration, workflows other than `mercury-*.yml`, and the part of
`AGENTS.md` and `CLAUDE.md` outside the markers (which Mercury never modifies). `.mercury/config.json` is the
project's own file: it selects the packs, declares linked repositories (`linked_repos`: purpose, how to locate, how to
use, rules), and sets parameters (`params`). Linked repositories are listed in the managed section of `AGENTS.md`;
anything their rules do not allow needs its own authorized scope.

Read-only roles (reviewer, acceptance, design, research) must not write. On Codex the sandbox enforces this; on
Claude Code the role carries a `PreToolUse` guard that blocks write commands. A project can name more write-type
scripts for the guard in `params.guard_write_scripts` (blocked outright) and `params.guard_write_actions` (blocked only
for the listed `-Action` values, and whenever the action cannot be read).
