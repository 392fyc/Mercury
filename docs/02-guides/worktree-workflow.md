<!-- Intentionally in English: branch names, git commands, and file paths
     are English-native; mixing languages reduces clarity for all agents. -->

# Worktree Workflow Specification

## Purpose

Enable parallel multi-task development by isolating each dev task in its own
git worktree. This removes branch-collision risk when multiple agents work
simultaneously and formalises the constraint in
[`git-flow.md`](git-flow.md)
(dev agents must never switch branches) by giving each agent a dedicated,
branch-locked working directory.

See also: [git-flow.md](git-flow.md) for branch naming
rules and merge procedures.

---

## Roles and Responsibilities

| Actor | Responsibility |
|-------|----------------|
| Main Agent | Creates worktree + branch before dispatch (only under the repo root, `.worktrees/{taskId}`); injects `worktreePath` into TaskBundle; after the PR is merged and verified, removes the worktree + local branch without asking (see [Cleanup after merge](#cleanup-after-merge-and-abandoned-work)) |
| Dev Agent | Uses `cd <worktreePath>` before any file operation; never switches branches or creates/modifies worktrees |
| Reaper | `scripts/worktree-reaper.sh` — removes worktrees whose work is already merged and whose tree is clean; only lists unmerged, PR-less, aged worktrees for the user to confirm (never deletes them) |

---

## Worktree Lifecycle

```text
Main: git worktree add <path> -b <branch>
  --> TaskBundle.worktreePath = <path>
  --> TaskBundle.branch = <branch>
        |
        v
Dispatch -> Dev Agent: cd <worktreePath>, work exclusively inside it
        |
        v
Dev: commit + push (from within worktreePath, never switches branch)
        |
        v
Main: review -> PR -> review bot -> merge
        |
        v
Main: verify merged + clean (see Cleanup after merge)
        |
        +-- merged, clean ---> git worktree remove <path> (no --force)
        |                      + git branch -d (or -D with merged-PR evidence)
        |                      + git fetch --prune origin (remote branch is removed by GitHub)
        |
        +-- dirty / cannot verify merge ---> keep everything, report to the user
        |
        +-- abandoned without merge ------> list for the user; delete only after the user confirms
```

---

## Naming Conventions

| Artifact | Pattern | Example |
|----------|---------|---------|
| Worktree path | `.worktrees/{taskId}` | `.worktrees/247-worktree-per-task` |
| Branch | `<type>/<purpose>[-<lane>]-<agent>` per git-flow.md | `feature/247-worktree-per-task-claude` |

The worktree root (`.worktrees/`) is listed in `.gitignore` as `/.worktrees/` —
worktrees are transient and must not be committed.

---

## Task Bundle Field

When Main hands a task to a dev agent, it passes the worktree path as a
`worktreePath` field of the task description before dispatching:

```json
{
  "taskId": "247-worktree-per-task",
  "worktreePath": "/absolute/path/to/repo/.worktrees/247-worktree-per-task",
  "...": "other fields"
}
```

The dev agent receives `worktreePath` in its prompt and runs `cd <worktreePath>`
before every file operation. Dev never creates or removes worktrees.

---

## Dispatch Integration

Worktree management follows these steps when Main dispatches a dev agent:

| Step | Action |
|-------|--------|
| **Dispatch dev** | Main runs `git worktree add "${REPO_ROOT}/.worktrees/${TASK_ID}" -b "${TASK_BRANCH}"` then injects the absolute path into `TaskBundle.worktreePath` before dispatch |
| **Dev prompt** | Prompt explicitly instructs dev to `cd <worktreePath>` before any file operation |
| **Forbidden list** | Dev agents are forbidden from "Creating or modifying git worktrees (Main's responsibility)" |
| **Cleanup** | After the PR is merged: `bash scripts/cleanup-worktree-branch.sh "${TASK_BRANCH}" master` (safe mode; no `--force`). On blocked/escalation exits the work is not merged, so nothing is deleted: report the branch to the user (see abandoned work below) |

---

## Dispatch Protocol (Main Agent)

Before dispatching a task, Main MUST:

1. **Create the branch and worktree:**

   ```bash
   REPO_ROOT=$(git rev-parse --show-toplevel)
   WORKTREE_PATH="${REPO_ROOT}/.worktrees/${TASK_ID}"
   git worktree add "${WORKTREE_PATH}" -b "${TASK_BRANCH}"
   ```

2. **Inject into TaskBundle:**

   ```json
   { "worktreePath": "/absolute/path/.worktrees/{taskId}" }
   ```

3. **Instruct dev in dispatch prompt:**
   > Working directory: `<worktreePath>`. Use `cd <worktreePath>` before any file operation.

4. **After PR merge**, clean up (details below):

   ```bash
   bash scripts/cleanup-worktree-branch.sh "${TASK_BRANCH}" master
   ```

Task worktrees live only at `${REPO_ROOT}/.worktrees/${TASK_ID}`. Do not create
any worktree outside the repository (for example a sibling directory such as
`Mercury-<short>`): the reaper, the cleanup script and the `.gitignore` entry
all assume the in-repo location. Long-lived lane worktrees also live inside the
repository, at `${REPO_ROOT}/.worktrees/lanes/<short>`
([`lane-naming.md`](../../packs/lanes/docs/guides/lane-naming.md)); the reaper and the cleanup script's
auto-discovery skip `.worktrees/lanes/`, and a lane is closed only through the
lane-close procedure.

---

## Cleanup after merge and abandoned work

Decision (Issue #663): worktrees, local branches and remote branches must not
pile up after the work is finished.

**After the PR is merged: delete without asking, but only after verifying.**
All of these must hold:

1. The branch is **proven merged** in one of two ways:
   - A merged PR exists and the branch tip is equal to, or an ancestor of, that
     PR's head commit (`gh pr list --state merged --head <branch> --json headRefOid`
     plus `git merge-base --is-ancestor <tip> <headRefOid>`). This also covers
     squash and rebase merges, where the branch commits never appear in the base
     history. Commits added after the PR head are not covered, so they are never lost.
   - The branch has at least one commit of its own (evidence: its tip differs from the
     commit recorded by the oldest entry of its reflog, i.e. where it was created; no
     reflog means no evidence) **and** all of it is already in the base
     branch (`git merge-base --is-ancestor <tip> origin/master`).

   Being an ancestor of the base branch alone proves nothing: a branch that was
   just cut from `master` and has no commit of its own is also an ancestor of
   `master`, and deleting it would destroy a task that has only just started.
   A branch with no own commit and no merged PR is therefore never removed
   automatically; it is only listed (`CONFIRM`).
2. The path is a registered worktree root (`git worktree list --porcelain`) that checks
   out exactly that branch, and it is provably clean: `git status --porcelain --ignored`
   shows no staged, unstaged or untracked files **and no ignored files** other than
   regenerable caches (`__pycache__/`, `.pytest_cache/`, `*.pyc`). Plain
   `git worktree remove` also deletes ignored files (`.handoff/`, `.tmp/`, a local
   `.env`), which `git status` alone does not show. If `git status` fails (broken
   `.git` link, damaged index) the state is unknown and counts as not clean.
3. The branch is not `main`, `master`, `develop` or origin's default branch
   (`git symbolic-ref refs/remotes/origin/HEAD`). Both scripts refuse these before
   any local delete. Nothing on the GitHub side stops a deletion of these
   branches in the Mercury-family repositories (`protected: false`), so this
   check is the only guard; never rely on remote branch protection.

Then run `scripts/cleanup-worktree-branch.sh <branch> master`. The script
re-checks 1 and 3 itself and exits without deleting anything if it cannot
verify the merge (exit 1) or the branch is protected (exit 2). It removes the
worktree with a plain `git worktree remove` (never `--force`, except for the verified submodule case below), deletes the local
branch with `git branch -d` (or `-D` with the merged-PR evidence printed), and
reads the remote back with `git fetch --prune origin`. The remote branch is
deleted by GitHub's "automatically delete head branches" setting; if
`origin/<branch>` still exists after the fetch, the script warns and the
remote branch is handled like abandoned work below.

**Worktree with checked-out submodules: the one exception to "never `--force`"
(Issue #670).** Once a submodule was checked out inside a task worktree, git refuses
a plain `git worktree remove` ("working trees containing submodules cannot be moved
or removed"), because the submodule repositories live under `<git-dir>/modules` of
that worktree. Both scripts therefore use a single `git worktree remove --force` for
such a worktree, and only when **all** of these hold:

1. The worktree passes every check above (registered root, merge proven, no staged,
   unstaged or untracked content, no ignored files other than regenerable caches).
2. Every **checked-out** submodule, recursively, is verified. "Checked out" means
   `<submodule path>/.git` exists; the `git submodule status` prefix alone is not trusted,
   because a submodule shown as `-` can still hold files. For each one:
   - the status prefix is a space, i.e. the checked-out commit equals the commit the
     superproject records (`+` differs, `U` is a conflict, and `-` on a checked-out
     submodule means "checked out but not active", which cannot be verified and blocks);
   - the path is its own repository root (`git rev-parse --show-toplevel`), so the check
     cannot silently run against the superproject;
   - `git -C <submodule> status --porcelain --ignored=matching` shows nothing except
     regenerable caches;
   - the same applies to the submodules inside it. A submodule that is not checked out
     (no `.git`) is skipped.
3. Then exactly one `git worktree remove --force` runs (no retry, no `rm -rf`), followed
   by the usual branch delete and remote read-back.

`git submodule deinit` is deliberately **not** run. `submodule init` and `deinit` write
`submodule.<name>.url` into the config shared by all worktrees
(`$GIT_COMMON_DIR/config`), so a deinit in a task worktree also unregisters the
submodules of the main checkout (verified with git 2.43: afterwards the main checkout's
`git submodule status` shows `-`). A plain force removal deletes only the task worktree
directory and its own `<common-dir>/worktrees/<name>/` data; the main checkout's url,
status and files stay intact.

Limits: only the checked-out commit is compared with the recorded one. Commits that
exist only on other local branches inside a submodule are **not** examined and are lost
with the force removal; push such work from the submodule before cleaning up. A worktree
that has `<git-dir>/modules` but no checked-out submodule also takes this force path
(that leftover repository data is removed with it). The `--force` rm-rf fallback of
`cleanup-worktree-branch.sh` refuses any worktree whose index holds a gitlink with a
checked-out `.git` below it.

If any condition fails, the worktree and the branch are kept: `cleanup-worktree-branch.sh`
prints the reason and exits 1, and the reaper prints a `DIRTY` line (the superproject
already sees the change) or a `SUBMODULE :` line (only the submodule check sees it).
`--dry-run` prints only the `remove --force` command. A worktree without a checked-out
submodule and without `<git-dir>/modules` keeps the plain, never-forced removal. The
decision "which worktrees are forced" lives in the scripts, not in agent judgement: do
not run `--force` by hand for a worktree whose check failed.

**Dirty worktree or merge cannot be verified: delete nothing.** Report the
path, the branch and what is uncommitted or unverified. `--force` does not
override this: `cleanup-worktree-branch.sh --force` exists only to retry on
Windows file locks and still skips a dirty or unverifiable worktree. Its `rm -rf`
fallback runs only for a registered, verified-clean worktree root whose plain remove
failed, and never under `.worktrees/lanes`. An explicit `--worktree-path` must be such
a root and must check out the given branch, otherwise the script exits 1 and deletes
nothing.

**Abandoned without merging: list first, delete only after the user confirms.**
Report branch, tip commit, number of commits not in the base branch, remote
branch and PR state. After the user confirms, remove them in this order:

```bash
git worktree remove "${WORKTREE_PATH}"        # add --force only if the user also confirms discarding uncommitted content
git branch -D "${TASK_BRANCH}"
# Remote: read the branch back first and compare with the tip commit you listed for the user
gh api "repos/<owner>/<repo>/git/ref/heads/${TASK_BRANCH}" --jq .object.sha
# Only if that SHA equals the confirmed tip commit (otherwise stop; someone pushed since):
gh api -X DELETE "repos/<owner>/<repo>/git/refs/heads/${TASK_BRANCH}"   # not a bare git push
git fetch --prune origin
```

Never delete `main`, `master`, `develop` or the default branch this way.

---

## Reaper

`scripts/worktree-reaper.sh` sweeps `.worktrees/` for leftovers of finished or
interrupted work. It only ever deletes **merged and clean** worktrees.

```bash
# Preview (safe, default)
bash scripts/worktree-reaper.sh --dry-run

# Remove merged + clean worktrees and their local branches
bash scripts/worktree-reaper.sh --prune
```

The script classifies each worktree:

| Output | Meaning | Action |
|--------|---------|--------|
| `KEEP` | Branch has an open PR (any age), is protected (`main`, `master`, `develop`, origin's default branch), or the worktree is unmerged and newer than `WORKTREE_AGE_DAYS` (default 7) | none |
| `DIRTY` | Merged, but the worktree has uncommitted content or ignored files other than regenerable caches (paths are listed) | kept; clean it up by hand |
| `SUBMODULE :` | Merged and clean, but a checked-out submodule is not at its recorded commit, is not active, holds uncommitted or non-cache ignored content, or cannot be verified (Issue #670) | kept; fix the submodule by hand |
| `WOULD REAP` / `REAPED` | Proven merged (tip contained in a merged PR head, or the branch has its own commits and all of them are in `origin/<default branch>`) and clean | `--prune`: plain `git worktree remove` + `git branch -d` (`-D` with merged-PR evidence); a worktree with verified-clean checked-out submodules is removed with one `git worktree remove --force`, no deinit (Issue #670). Not limited by age |
| `CONFIRM` | Not proven merged (unmerged work, or no own commit and no merged PR), no open PR, older than the threshold | **never deleted in any mode**; prints branch, tip commit and unmerged commit count for the user to decide |

Details:
- Uses `gh pr list --state open` for active PR branches; `--prune` refuses to run if `gh` cannot list them.
- A branch that is merely an ancestor of the base, with no commit of its own and no merged PR (for example a worktree created a moment ago), is never treated as merged, at any age.
- The `.worktrees/lanes/` directory is skipped (`SKIP : lanes ...`): lane worktrees are long-lived and closed by the lane-close procedure.
- Directories under `.worktrees/` that are not worktree roots (or whose `.git` link is broken) are skipped (`SKIP`).
- Remote branches are not touched; GitHub's auto-delete-head-branch handles them.
- Supports `WORKTREE_ROOT`, `WORKTREE_AGE_DAYS` and `WORKTREE_BASE_REF` env var overrides.

Run `scripts/worktree-reaper.sh --dry-run` at session start to inspect
any worktrees left behind by interrupted pipeline runs.

Tests: `bash tests/test_worktree_reaper.sh` (TAP output; exit code = number of failures) and
`bash tests/test_cleanup_worktree_branch.sh` for the cleanup script.

---

## Merge Strategy

Each worktree produces an independent PR (`feature/{taskId}-<agent>` into `master`).
PRs are merged in dependency order when dependencies exist. Conflicts are
resolved by Main Agent via rebase before merge; after rebasing a pushed branch,
`--force-with-lease` is used to update the PR branch (direct force-push without
lease is prohibited).

```text
master --> feature/TASK-a1b2c3d4-add-auth-claude     (independent)         -> PR -> merge
        --> feature/TASK-e5f6g7h8-add-logging-codex(independent)         -> PR -> merge
        --> feature/TASK-i9j0k1l2-refactor-auth-claude(dependsOn TASK-a1b2c3d4) -> wait -> PR -> merge
```

---

## Relationship to Existing Constraints

| Existing Rule | How Worktree Workflow Satisfies It |
|---------------|-------------------------------------|
| Dev agents must never switch branches ([`git-flow.md`](git-flow.md)) | Worktree is branch-locked at creation; the same branch cannot be checked out in multiple worktrees simultaneously |
| All code enters master through PRs ([`git-flow.md`](git-flow.md)) | Each worktree branch becomes an independent PR |
| Main creates branches, Dev does not ([`git-flow.md`](git-flow.md)) | Main creates worktree+branch pre-dispatch; Dev only commits |

---

## Decided and open questions

Decided (Issue #663):

1. Every worktree lives inside the repository, never outside it (no sibling
   directories such as `Mercury-<short>`): task worktrees at `.worktrees/{taskId}`,
   lane worktrees at `.worktrees/lanes/<short>`.

Open (deferred):

2. For tasks that share a dependency, should the dependent task inherit the
   parent's worktree or get a fresh one from master? Fresh worktree is safer.
3. How does the acceptance agent reference files — via worktree path or repo
   root? Acceptance likely needs the repo root for blind review.
