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

See also: [`.mercury/docs/guides/git-flow.md`](git-flow.md) for branch naming
rules and merge procedures.

---

## Roles and Responsibilities

| Actor | Responsibility |
|-------|----------------|
| Main Agent | Creates worktree + branch before dispatch; injects `worktreePath` into TaskBundle; removes worktree + branch after PR merge |
| Dev Agent | Uses `cd <worktreePath>` before any file operation; never switches branches or creates/modifies worktrees |
| Reaper | `scripts/worktree-reaper.sh` — detects and removes orphaned worktrees (no open PR, older than 7 days) |

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
Main: git worktree remove --force <path> + git branch -d <branch>
```

---

## Naming Conventions

| Artifact | Pattern | Example |
|----------|---------|---------|
| Worktree path | `.worktrees/{taskId}` | `.worktrees/247-worktree-per-task` |
| Branch | `feat/{taskId}` or `feat/{taskId}-{slug}` per git-flow.md | `feat/247-worktree-per-task` |

The worktree root (`.worktrees/`) is listed in `.gitignore` as `/.worktrees/` —
worktrees are transient and must not be committed.

---

## TaskBundle Schema Extension

`worktreePath` is a first-class field in the TaskBundle. Main fills it in
Phase 2 of the dev-pipeline skill before dispatching to the dev agent:

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

## Dev-Pipeline Integration

Worktree management is embedded in the
[`dev-pipeline` skill](.../../.claude/skills/dev-pipeline/SKILL.md):

| Phase | Action |
|-------|--------|
| **Phase 2 — Dispatch Dev** | Main runs `git worktree add "${REPO_ROOT}/.worktrees/${TASK_ID}" -b "${TASK_BRANCH}"` then injects the absolute path into `TaskBundle.worktreePath` before dispatch |
| **Phase 2 — Dev prompt** | Prompt explicitly instructs dev to `cd <worktreePath>` before any file operation |
| **Phase 2 — Forbidden list** | Dev agents are forbidden from "Creating or modifying git worktrees (Main's responsibility)" |
| **Phase 5 — Cleanup** | On every terminal exit path (pass, blocked, escalation): `git worktree remove --force "${WORKTREE_PATH}" && git branch -d "${TASK_BRANCH}"` |
| **Phase 6 — Step 5.1** | After PR merge confirmed, same cleanup block runs as final action |

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

4. **After PR merge**, clean up:

   ```bash
   git worktree remove --force "${WORKTREE_PATH}"
   git branch -d "${TASK_BRANCH}"
   ```

---

## Orphan Reaper

`scripts/worktree-reaper.sh` detects and removes worktrees that have no open
PR and are older than `WORKTREE_AGE_DAYS` days (default: 7).

```bash
# Preview what would be reaped (safe, default)
bash scripts/worktree-reaper.sh --dry-run

# Actually delete orphaned worktrees
bash scripts/worktree-reaper.sh --prune
```

The script:
- Uses `gh pr list --state open` to identify active PR branches
- Keeps any worktree whose branch has an open PR, regardless of age
- Keeps any worktree newer than `WORKTREE_AGE_DAYS` days
- On `--prune`: runs `git worktree remove --force` + `git branch -d`
- Supports `WORKTREE_ROOT` and `WORKTREE_AGE_DAYS` env var overrides

Run via `scripts/worktree-reaper.sh --dry-run` at session start to inspect
any worktrees left behind by interrupted pipeline runs.

Tests: `bash tests/test_worktree_reaper.sh` (exits 0, TAP output).

---

## Merge Strategy

Each worktree produces an independent PR (`feat/{taskId}` into `develop`).
PRs are merged in dependency order when dependencies exist. Conflicts are
resolved by Main Agent via rebase before merge; after rebasing a pushed branch,
`--force-with-lease` is used to update the PR branch (direct force-push without
lease is prohibited).

```text
develop --> feat/TASK-a1b2c3d4-add-auth     (independent)         -> PR -> merge
        --> feat/TASK-e5f6g7h8-add-logging  (independent)         -> PR -> merge
        --> feat/TASK-i9j0k1l2-refactor-auth (dependsOn TASK-a1b2c3d4) -> wait -> PR -> merge
```

---

## Relationship to Existing Constraints

| Existing Rule | How Worktree Workflow Satisfies It |
|---------------|-------------------------------------|
| Dev agents must never switch branches ([`git-flow.md`](git-flow.md)) | Worktree is branch-locked at creation; the same branch cannot be checked out in multiple worktrees simultaneously |
| All code enters develop through PRs ([`git-flow.md`](git-flow.md)) | Each worktree branch becomes an independent PR |
| Main creates branches, Dev does not ([`git-flow.md`](git-flow.md)) | Main creates worktree+branch pre-dispatch; Dev only commits |

---

## Open Questions (deferred)

1. Should `.worktrees/` be nested inside the repo root or alongside it (sibling
   directory)? Current implementation uses repo-root nesting (`.worktrees/{taskId}`).
2. For tasks that share a dependency, should the dependent task inherit the
   parent's worktree or get a fresh one from develop? Fresh worktree is safer.
3. How does the acceptance agent reference files — via worktree path or repo
   root? Acceptance likely needs the repo root for blind review.
