<!-- Intentionally in English: branch names, commit formats, and git commands
     are English-native; mixing languages would reduce clarity for all agents. -->

# Git Branching (GitHub Flow)

## Branch Structure

| Branch | Purpose | Merge method |
|--------|---------|-------------|
| `master` | Stable — PR from develop only | Human / Main Agent |
| `develop` | Integration baseline — **PR from feature only** | Main Agent via `gh pr merge` |
| `feature/TASK-XXX` | Per-task work branch | Dev works here; Main creates, opens PR, and merges |
| `fix/issue-N-slug` | Issue-linked bugfix | Same workflow as feature branches |
| `feat/issue-N-slug` | Issue-linked feature | Same workflow as feature branches |

## Workflow

1. Main Agent creates `feature/TASK-XXX` from develop, checks it out, dispatches to Dev
2. Dev Agent works on that branch, commits + pushes. **Never switches branches.**
3. Main Review PASS → Main Agent opens PR: `feature/TASK-XXX` → `develop`
4. Review bot auto-reviews the PR (async, non-blocking)
5. Main Agent merges PR via `gh pr merge`
6. Milestone release: PR `develop` → `master`

**Direct push to develop is forbidden.** All code enters develop through PRs.

## Branch Protection (GitHub)

Both `develop` and `master` have branch protection rules enabled:

| Rule | develop | master |
|------|---------|--------|
| Require PR before merge | Yes | Yes |
| Required approving reviews | 1 | 1 |
| Dismiss stale reviews | Yes | Yes |

PRs must receive at least one approved review (from the configured review bot or an authorized human reviewer) before merging.

## Commit Format

`{type}({task_id}): {summary}`

type: feat / fix / refactor / chore / docs

## Dev Agent Git Permissions

| Allowed | Forbidden |
|---------|-----------|
| `git add` (scope-restricted files) | `git switch` / `git checkout <branch>` |
| `git commit` | `git branch -d` / `git reset` / `git stash` |
| `git push origin <branch>` | `git rebase` / `git merge` |
| `git diff` / `git status` / `git log` | `git add -A` / `git add .` |
| | `git push --force` |
| | Operate on master / develop |
