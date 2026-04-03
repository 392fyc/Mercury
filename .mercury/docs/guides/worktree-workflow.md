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
| Main Agent | Creates worktree + branch before dispatch; injects `worktreePath` into TaskBundle; merges/deletes after PR merge |
| Dev Agent | Works exclusively inside `worktreePath`; never switches branches or leaves the worktree |
| Orchestrator | (Future) automates worktree creation via `prepareBundleTaskExecution`; detects orphaned worktrees |

---

## Worktree Lifecycle

```text
Main: git worktree add <path> -b <branch>
  --> TaskBundle.worktreePath = <path>
  --> TaskBundle.branch = <branch>
        |
        v
Dispatch -> Dev Agent works inside worktreePath
        |
        v
Dev: commit + push (from within worktreePath, never switches branch)
        |
        v
Main: review -> PR -> CodeRabbit -> merge
        |
        v
Main: git worktree remove <path> + git branch -d <branch>
```

---

## Naming Conventions

| Artifact | Pattern | Example |
|----------|---------|---------|
| Worktree path | `.worktrees/{taskId}` | `.worktrees/TASK-a1b2c3d4` |
| Branch | `feature/{taskId}-{slug}` (existing rule; `taskId` includes `TASK-` prefix) | `feature/TASK-a1b2c3d4-add-auth` |

The worktree root (`.worktrees/`) belongs in `.gitignore` — worktrees are
transient and must not be committed.

---

## TaskBundle Schema Extension (Proposal)

> **Note:** `worktreePath` and `dependsOn` are proposed fields not yet present
> on the `TaskBundle` interface in `packages/core/src/types.ts`. They require a
> future implementation step that adds the fields to the interface and enforces
> the `worktreePath`+`branch` co-set invariant via Zod schema or a runtime guard.

Add two optional fields to `TaskBundle`:

```typescript
interface TaskBundle {
  // ... existing fields ...

  /** Absolute path to the git worktree for this task.
   *  Set by Main Agent before dispatch; Dev Agent works exclusively here.
   *  Undefined for tasks that pre-date worktree workflow or single-task sessions.
   *  Design constraint: worktreePath and branch must always be set together. */
  worktreePath?: string;

  /** Task IDs this task depends on. The scheduler must complete dependsOn tasks
   *  before dispatching this one. Parallel-safe tasks leave this unset or empty. */
  dependsOn?: string[];
}
```

---

## Dispatch Protocol (Main Agent)

Before dispatching a task that will run in parallel with other active tasks,
Main Agent MUST:

1. **Create the branch and worktree:**

   ```bash
   git worktree add .worktrees/{taskId} -b feature/{taskId}-{slug}
   ```

2. **Inject into TaskBundle** (via `update_task` RPC or at task creation):

   ```json
   { "worktreePath": "/absolute/path/.worktrees/{taskId}",
     "branch": "feature/{taskId}-{slug}" }
   ```

3. **Emphasise in dispatch prompt** (to be handled by `buildDevPrompt` when
   `task.worktreePath` is set — see Prompt Integration section below):
   > You are working inside the isolated worktree at `<worktreePath>`.
   > Do not navigate outside this directory. Do not switch branches.

4. **After PR merge**, clean up:

   ```bash
   git worktree remove .worktrees/{taskId}
   git branch -d feature/{taskId}-{slug}
   ```

---

## Prompt Integration

`buildDevPrompt` (in `packages/orchestrator/src/task-manager.ts`) will, when
implemented, prepend a worktree constraint block when `task.worktreePath` is
set. When implemented, this block will replace the existing Dev Agent Git
Permissions table for worktree-enabled tasks, making the isolation constraint
explicit rather than implicit. This is a planned integration and is not yet
implemented.

Proposed block:

```markdown
## Worktree Isolation
You are working in an isolated git worktree.
Working directory: {worktreePath}
Branch: {branch}

CONSTRAINTS:
- All git operations must execute within {worktreePath}
- Do NOT run git checkout, git switch, or git branch -d
- Do NOT navigate to the main working directory or other worktrees
- Commits and pushes go to branch {branch} only
```

---

## Dependency Management

```text
dependsOn: []          -> fully independent -> dispatch immediately, own worktree
dependsOn: [taskId]    -> wait for taskId to reach status=completed before dispatch
```

The scheduler (future orchestrator enhancement) checks `dependsOn` before each
dispatch wave. Tasks with unresolved dependencies are held in `drafted` status
until prerequisites complete.

For the current session (spec-only), dependency enforcement remains manual:
Main Agent reviews `dependsOn` fields before dispatching.

---

## Merge Strategy

Each worktree produces an independent PR (`feature/{taskId}-{slug}` into
`develop`). PRs are merged in dependency order when dependencies exist.
Conflicts are resolved by Main Agent via rebase before merge; after rebasing a
pushed branch, `--force-with-lease` is used to update the PR branch (direct
force-push without lease is prohibited).

```text
develop --> feature/TASK-a1b2c3d4-add-auth     (independent)         -> PR -> merge
        --> feature/TASK-e5f6g7h8-add-logging  (independent)         -> PR -> merge
        --> feature/TASK-i9j0k1l2-refactor-auth (dependsOn a1b2c3d4) -> wait -> PR -> merge
```

---

## Orphan Detection

A worktree is orphaned when its associated task has reached a terminal state
(`verified`, `closed`, or `failed`) but the worktree directory still exists,
and no non-terminal task currently references that worktree path.
Detection rule:

```bash
# Terminal TaskStatus values: verified, closed, failed
# Non-terminal TaskStatus values: drafted, dispatched, in_progress,
#   implementation_done, main_review, acceptance, blocked

git worktree list | grep ".worktrees/" | while read wt_path _rest; do
  taskId=$(basename "$wt_path")

  # 1. Get task status via get_task RPC
  status=$(orchestrator-rpc '{"method":"get_task","params":{"taskId":"'"$taskId"'"}}' \
    | jq -r '.status // "unknown"')

  # 2. Check if terminal
  if [[ "$status" =~ ^(verified|closed|failed)$ ]]; then

    # 3. Verify no non-terminal task still references this worktree path
    active_refs=$(orchestrator-rpc '{"method":"list_tasks","params":{}}' \
      | jq -r '.tasks[]
          | select(
              (.status | IN("drafted","dispatched","in_progress",
                            "implementation_done","main_review","acceptance","blocked"))
              and .worktreePath == "'"$wt_path"'"
            )
          | .taskId')

    if [[ -z "$active_refs" ]]; then
      echo "ORPHAN: $wt_path (task $taskId is $status, no active references)"
      # request manual confirmation before removal — may contain uncommitted work
    fi
  fi
done
```

Main Agent runs orphan detection at session start. Removal requires explicit
confirmation — worktrees may contain uncommitted work.

---

## Future Orchestrator Interface

The following orchestrator methods are proposed for a future implementation
phase (not part of this spec release):

| Method | Description |
|--------|-------------|
| `createWorktree(taskId, branchSlug)` | git worktree add + updates TaskBundle |
| `removeWorktree(taskId)` | git worktree remove + branch delete |
| `listOrphanedWorktrees()` | Cross-references worktree list with task statuses |
| `prepareParallelWave(taskIds[])` | Creates worktrees for all tasks in a wave batch |

`dispatch_task` RPC response will include `worktreePath` when a worktree is
active for the task.

---

## Relationship to Existing Constraints

| Existing Rule | How Worktree Workflow Satisfies It |
|---------------|-------------------------------------|
| Dev agents must never switch branches ([`git-flow.md`](git-flow.md)) | Worktree is branch-locked at creation; the same branch cannot be checked out in multiple worktrees simultaneously, ensuring work isolation |
| All code enters develop through PRs ([`git-flow.md`](git-flow.md)) | Each worktree branch becomes an independent PR |
| Main creates branches, Dev does not ([`git-flow.md`](git-flow.md)) | Main creates worktree+branch pre-dispatch; Dev only commits |

---

## Open Questions (deferred to implementation phase)

1. Should `.worktrees/` be nested inside the repo root or alongside it (sibling
   directory)? Sibling avoids any risk of accidentally committing worktree state.
   Current spec uses repo-root nesting (`.worktrees/{taskId}`).
2. For tasks that share a dependency, should the dependent task inherit the
   parent's worktree or get a fresh one from develop? Fresh worktree is safer
   and keeps each task's change set isolated for review.
3. How does the acceptance agent reference files — via worktree path or repo
   root? Acceptance likely needs the repo root for blind review, since the
   worktree is task-specific and may diverge from the base.
