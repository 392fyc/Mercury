# Mercury docs

`docs/` is Mercury's knowledge base and an Obsidian vault. It follows the kb pack conventions
(`.mercury/packs/kb/docs/kb-conventions.md`): this page is the entry, every directory has a README index with a
status for each document, and history sits in `90-archive/`. Check it with
`python -B .mercury/packs/kb/scripts/kb_check.py docs`.

## Start here

1. **Current rules**: [01-rules](01-rules/README.md). How Mercury works now, each rule with its source decision.
2. **Current progress**: [00-progress](00-progress/README.md). The open tracking Issues and what comes next.
3. **Decisions**: [03-decisions](03-decisions/README.md). One row per decision, with what it supersedes.

Then, for a task: the working guides in [02-guides](02-guides/README.md) (Git flow, worktrees, Issues, upstream
imports) and the research behind the rules in [04-research](04-research/README.md).

## Directories

| Directory | What it holds |
|---|---|
| [00-progress](00-progress/README.md) | Current progress: open tracking Issues and next steps |
| [01-rules](01-rules/README.md) | Current rules and principles |
| [02-guides](02-guides/README.md) | Working guides for Git, worktrees, Issues, imports and the KB itself |
| [03-decisions](03-decisions/README.md) | Decision register and decision records |
| [04-research](04-research/README.md) | Research reports, design notes, incidents and articles |
| [90-archive](90-archive/README.md) | History: retired plans, policies and early research. Not a basis for work |

## Working with it

- Change `docs/` in the task worktree together with the change it documents, and deliver both in one pull request.
  The pull request has a `KB:` line naming what it updated, or `KB: no impact` and why.
- When a change alters a rule, a decision or progress, update the current document and mark what it replaces in
  the same pull request: set the old row to `superseded` with its replacement, or move retired material to
  `90-archive/`.
- `docs/` is public: no credentials, machine paths, private project details or personal information.
- It replaced the private `Mercury_KB` vault in October 2026 (#718); some older notes mention `Mercury_KB/...`
  paths or folders that were not published.
