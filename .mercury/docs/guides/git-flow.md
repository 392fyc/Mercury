<!-- Intentionally in English: branch names, commit formats, and git commands
     are English-native; mixing languages would reduce clarity for all agents. -->

# Git Branching (GitHub Flow)

## Branch Structure

| Branch | Purpose | Merge method |
|--------|---------|-------------|
| `master` | Stable — PR from develop only | Human / Main Agent |
| `develop` | Integration baseline — **PR from task branches only** | Main Agent via the guarded PR flow |
| `feature/TASK-XXX` | Per-task work branch | Implementation and Git delivery owners are named by the task |
| `lane/<lane>/(init|<n>|<n>-<slug>)` | Lane work accepted by the Codex guard | Same review and PR requirements as task branches |

For Codex Git writes, `scripts/codex/guard.ps1` defines the accepted task-branch
forms. Select an accepted branch before staging or committing; do not bypass
the guard to use an unsupported branch name.

## Workflow

1. The coordinating agent selects an isolated task branch from develop and
   identifies the implementation and Git delivery owners. Routine work may be
   completed directly; bounded delegation is optional. Invoke a multi-stage
   pipeline only when the user explicitly calls for it or an established task
   plan requires it.
2. Implement within the assigned scope and run relevant checks. A delegated
   worker stays on its assigned branch and commits or pushes only when assigned.
3. Inspect the final staged diff and complete the review required by the task's
   risk and AGENTS.md. Resolve material findings before committing. In Codex,
   use `scripts/codex/git-safe.ps1 add` for explicit files, then record the reviewed
   staged snapshot with `scripts/codex/guard.ps1 mark-review`, and use
   `scripts/codex/git-safe.ps1 commit/push` for delivery. Changed staged content
   requires renewed review and a fresh mark; the mark alone is not review evidence.
4. The assigned delivery owner opens the Issue-linked PR into `develop`; the
   configured review bot performs its asynchronous PR review.
5. Resolve review findings and complete required checks and approvals before the
   coordinating agent merges through the guarded PR flow.
6. Milestone release: PR `develop` → `master`.

**Direct commits or pushes to develop, main, or master are forbidden.** All code
enters develop through PRs. Never publish merely because implementation finished.

## Branch Protection (GitHub)

Both `develop` and `master` have branch protection rules enabled:

| Rule | develop | master |
|------|---------|--------|
| Require PR before merge | Yes | Yes |
| Required approving reviews | 1 | 1 |
| Dismiss stale reviews | Yes | Yes |

PRs must receive at least one approved review (from the configured review bot or
an authorized human reviewer) before merging, along with all required checks.

## Commit Format

`{type}({task_id}): {summary}`

type: feat / fix / refactor / chore / docs

## Dev Agent Git Permissions

| Allowed | Forbidden |
|---------|-----------|
| Guarded staging of explicit assigned files | `git switch` / `git checkout <branch>` |
| Guarded commit when assigned and reviewed | `git branch -d` / `git reset` / `git stash` |
| Guarded push to the assigned task branch when assigned | `git rebase` / `git merge` |
| `git diff` / `git status` / `git log` | `git add -A` / `git add .` |
| | `git push --force` |
| | Direct writes to master / develop / main |
