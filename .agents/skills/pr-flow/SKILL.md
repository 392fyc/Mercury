---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, poll for CodeRabbit review, respond to ALL threads (inline + outside-diff), fix issues, resolve threads, re-review, and merge after approval. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments", "标准PR流程". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`. It replaces the manual C4-C7 steps in the Mercury workflow.
---

# PR Flow

## Overview

This skill automates the complete PR lifecycle with non-blocking polling and comprehensive review thread handling. It supports both single-PR and multi-PR workflows.

Codex adaptation:
- use `scripts/codex/git-safe.ps1` for `add`, `commit`, and `push`
- use `gh api graphql` for thread resolution
- if the current Codex tool surface does not expose `CronCreate` or `CronDelete`, keep the same 10-minute cadence via an external scheduler, host automation, or explicit follow-up invocations with persisted state files
- do not use `git stash`, `git switch`, or `git checkout` inside guarded Codex task sessions; for multi-PR splits use separate worktrees

## Prerequisites

- `gh` CLI v2.x+ (authenticated)
- `git` with push access to the PR branch
- `jq` for JSON parsing
- current branch must not be `develop`, `main`, or `master`
- follow `.mercury/docs/guides/git-flow.md` as the authoritative branching policy

## Pipeline

### Phase 1: Create PR(s)

#### Single-PR Mode (default)

```powershell
$branch = git branch --show-current
powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 push -u origin $branch
gh pr create --base develop --title "<type>(<scope>): description (#issue)" --body @"
## Summary
- bullet points

## Test plan
- [ ] test items

Generated with Codex
"@
```

#### Multi-PR Mode

When changes should be split by category:

1. Create separate worktrees branching off `origin/develop`:
   - `git worktree add ../Mercury-split-A -b feature/TASK-123-part-A origin/develop`
   - `git worktree add ../Mercury-split-B -b feature/TASK-123-part-B origin/develop`
2. Restore only the intended files into each worktree via direct file copy or `git restore --source <branch> -- <path>`.
3. Stage, commit, and push each worktree branch. While located inside the target worktree directory, invoke `git-safe.ps1` using a path that resolves to the main repo root (relative or absolute):
   - `Set-Location ../Mercury-split-A`
   - `powershell -ExecutionPolicy Bypass -File ../Mercury/scripts/codex/git-safe.ps1 add <path>`
   - `powershell -ExecutionPolicy Bypass -File ../Mercury/scripts/codex/git-safe.ps1 commit -Message "<message>"`
   - `powershell -ExecutionPolicy Bypass -File ../Mercury/scripts/codex/git-safe.ps1 push origin <branch>`
4. Create one PR per worktree branch (specify `--head` explicitly):
   - `gh pr create --base develop --head feature/TASK-123-part-A --title "feat(scope): part A"`
   - `gh pr create --base develop --head feature/TASK-123-part-B --title "feat(scope): part B"`
5. Track all PR numbers for parallel monitoring.

Do not rely on `git stash` in guarded Codex sessions.

### Phase 2: Poll for CodeRabbit Review (Non-Blocking)

Do not use long blocking `sleep` loops for 10-minute review polling.

Preferred model:
- if the host exposes recurring jobs, schedule a 10-minute recurring check
- otherwise, persist state files and have the host, wrapper, or operator reinvoke the same review-check prompt every 10 minutes

State files:

```text
.pr-flow-check-count-<PR_NUMBER>
.pr-flow-iteration-<PR_NUMBER>
.pr-flow-multi.txt
```

Each check should:
1. fetch review status with `gh pr view <N> --json reviews,reviewDecision`
2. fetch inline comments with `gh api repos/{owner}/{repo}/pulls/<N>/comments`
3. detect new activity
4. increment or clear the consecutive-no-activity counter
5. after 3 consecutive quiet checks, trigger `@coderabbitai review` once if it has not already been requested

### Phase 3: Respond to ALL Review Threads

Iteration cap: default `MAX_ITERATIONS=5`.

Critical rule: every CodeRabbit comment must receive a response, even if you disagree.

This includes:
- inline comments
- outside-diff comments embedded in review bodies
- review body suggestions

#### For each inline comment

1. Read the full comment body
2. Assess whether the issue is valid
3. If valid, fix the code and reply with commit SHA and what changed
4. If you disagree, reply with the reasoning
5. Reply via `gh api repos/{owner}/{repo}/pulls/<PR_NUMBER>/comments/<ID>/replies -f body="..."`

#### For outside-diff comments

These appear in the review body, not as inline threads.

Always address them in a PR comment and include `@coderabbitai`:

```powershell
gh pr comment <PR_NUMBER> --body "@coderabbitai
## Addressed CodeRabbit review
### Inline comments (N/N resolved):
1. **Issue** - fixed in <sha>
### Outside-diff comments (N/N resolved):
1. **Issue** - fixed in <sha>"
```

### Phase 4: Fix Issues and Push

1. Read the relevant code before editing
2. Edit files to address valid feedback
3. Build to verify
4. Run milestone code review before committing
5. Stage explicit files through:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 add <path> [more paths...]
```

1. Mark review complete:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/codex/guard.ps1 mark-review
```

1. Commit through the wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 commit -Message "fix(PR-feedback): address comment <ID>"
```

1. Push through the wrapper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 push origin <branch>
```

### Phase 5: Resolve Threads

Resolve threads only after the fixes are pushed.

Use GraphQL to resolve review threads:

```powershell
$threads = @()
$cursor = $null
do {
  $afterClause = if ($cursor) { ", after: `"$cursor`"" } else { "" }
  $query = @"
query {
  repository(owner: "<OWNER>", name: "<NAME>") {
    pullRequest(number: <N>) {
      reviewThreads(first: 100$afterClause) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved path }
      }
    }
  }
}
"@
  $resp = gh api graphql -f query="$query" | ConvertFrom-Json
  $page = $resp.data.repository.pullRequest.reviewThreads
  $threads += $page.nodes
  $cursor = if ($page.pageInfo.hasNextPage) { $page.pageInfo.endCursor } else { $null }
} while ($cursor)
```

Then call:

```powershell
gh api graphql -f query='mutation { resolveReviewThread(input: {threadId: "<THREAD_ID>"}) { thread { id isResolved } } }'
```

Even threads you disagree with must be commented on and resolved. For out-of-scope suggestions, acknowledge that they are out of scope, reply, and resolve.

### Phase 6: Wait for Re-Review

Increment the iteration counter, then return to Phase 2.

If `MAX_ITERATIONS` is reached:
- post a PR comment requesting human intervention
- stop automatic rework
- wait for guidance

### Phase 7: Merge

Pre-merge checks:

1. CI status passes
2. `reviewDecision == APPROVED`
3. unresolved review thread count is zero

Run the reusable guard first; it aborts on any failed check, calls
`gh pr view --json reviewDecision,statusCheckRollup`, and paginates
`reviewThreads(first: 100, after: "<cursor>")` until `hasNextPage == false`
before allowing the merge:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/codex/guard.ps1 pre-merge -PullRequestNumber <PR_NUMBER>
```

Then merge:

```powershell
gh pr merge <PR_NUMBER> --squash --delete-branch
```

### Phase 8: Update Issues and Tasks

After merge:
- close related issues if the PR closes them
- update Mercury task state if the orchestrator is available
- clean up `.pr-flow-iteration-*`, `.pr-flow-check-count-*`, and `.pr-flow-multi.txt`

## Review Response Protocol

| Situation | Action |
|---|---|
| Valid inline issue | Fix code, reply with SHA, resolve thread |
| Valid outside-diff issue | Fix code, post PR comment |
| Disagree with suggestion | Reply explaining reasoning, resolve thread |
| Nitpick or style suggestion | Fix if trivial, otherwise explain and resolve |
| Out-of-scope suggestion | Acknowledge, explain it is out of scope, resolve |

Rules:
- even threads you disagree with must be commented on and resolved
- when posting non-direct PR comments, include `@coderabbitai`
- outside-diff comments still count as required responses

## Output

```text
PR: #<number> (<url>)
CodeRabbit: approved | changes_requested | pending
Feedback: <N> inline, <N> outside-diff, <N> addressed, iteration=<N>
Merge: merged | waiting | blocked
Task: <taskId> -> done | pending
```
