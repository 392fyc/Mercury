# Lane project template

Installed by Mercury (pack `lanes`) at `.mercury/packs/lanes/docs/mercury-lane-project-template.md`; do not edit the installed copy.

The lane contract (`mercury-lane-contract.md`) states the rules every project
follows. Projects differ in their repositories, tools and machines, so each
project writes its own lane document, its project layer, that says where and
how those rules are carried out. This template lists the sections that document
has, in this order, with Mercury's default for each.

How to use it:

- Copy the section headings into the project's lane document, in any language.
  Keep the order so readers of different projects find the same thing in the
  same place.
- Under each heading, write the project's concrete answer: a path, a script, a
  label. Where the default fits, write "Mercury default" and stop.
- A project rule that differs from a default takes priority within that
  project. It must still satisfy the contract; for example a project may
  choose its own branch types, but the purpose still comes first.
- Keep machine paths out of the document. Name how a path is resolved
  (an environment variable, then an ignored local configuration file) instead
  of the path itself.
- Content that is not about lanes, such as the project's domain rules, stays
  in the project's other documents.

## 1. Concepts

What a lane, a lead harness and a joining agent mean in this project, and any
project-specific roles.

Default: the contract's "Lanes and agents" section, unchanged.

## 2. Locations

Where lane state lives:

- the memory root and how it is resolved;
- the project's directory within it (its bucket), and that the bucket does not
  depend on where the repository is checked out;
- the registry, each lane's directory, checkpoints, handoffs and inbox;
- the lane work area for drafts, raw data and review notes.

Default: the lane home that `.mercury/packs/lanes/scripts/lane-paths.sh`
resolves. The work area has no default: every project names its location.

## 3. Registry

The registry's file name, the fields of a lane section, the allowed states,
and how a user declaration is quoted.

Default: `LANES.md` with the fields the lane scripts read (see
`guides/lane-naming.md`).

## 4. Session flow

What a session reads at start, how it learns its lane, and what it writes at
the end, including which skills or scripts do each step.

Default: the contract's "Session identity" section and the lane guides.

## 5. Shared-file writes

The script or procedure that performs the version-preconditioned write of the
contract's "State and write locations" section, and how a session records the
hash it read.

Default: the four steps in the contract, done by hand.

## 6. Branches, worktrees and pull requests

- Branch name: Mercury default `<type>/<purpose>[-<lane>]-<agent>`, with the
  purpose first and the lane and agent as suffixes only. A project may change
  the types or the separators but never puts the agent or the lane first.
- The base branch new task branches start from.
- Where task worktrees live and how they are created, published and retired.
- The labels issues and pull requests carry for a lane.

Default: the branch form above, the integration branch from
`.mercury/config.json` (`params.integration_branch`) or else origin's default
branch, and the project's worktree lifecycle.

## 7. Cross-lane messages and coordination

Where inbox entries go, how they are appended, and how overlap between lanes is
raised with the user.

Default: the contract's "Cross-lane work" section; entries are appended to the
receiving lane's `inbox.md`.

## 8. Main checkout

What may legitimately appear as uncommitted in a main checkout (normally
nothing, because lane state and the work area are ignored) and how leftovers
are reported.

Default: the contract's rule that the main checkout is not a development
workspace.

## 9. Controlled entries

The project's script for each raw command it denies (publish, base-branch
sync, worktree creation, shared-memory write), and their dry-run and
explicit-configuration modes.

Default: the contract's "Controlled entries and guards" section.

## 10. Review and tests

How reviews and tests of lane tooling run without touching real lane state.

Default: temporary repositories and the tooling's dry-run modes.
