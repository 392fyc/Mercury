---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, poll for CodeRabbit review, respond to ALL threads (inline + outside-diff), fix issues, resolve threads, re-review, and merge after approval. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments", "标准PR流程". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`. It replaces the manual C4-C7 steps in the Mercury workflow.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, WebSearch, WebFetch, Agent, TodoWrite, CronCreate, CronDelete
---

# PR Flow

## Overview

This skill automates the complete PR lifecycle with **non-blocking polling** and **comprehensive review thread handling**. It supports both single-PR and multi-PR (split by task category) workflows.

## Prerequisites

- `gh` CLI v2.x+ (authenticated)
- `git` with push access to the PR branch
- Current branch must not be `develop` or `main`

## Pipeline

### Phase 1: Create PR(s)

#### Single-PR Mode (default)
When all changes belong to one logical task:

```bash
BRANCH=$(git branch --show-current)
# Ensure pushed
git push -u origin "$BRANCH"
# Create PR with required metadata (Mercury hook requires --assignee and --label)
gh pr create --base develop \
  --title "<type>(<scope>): description (#issue)" \
  --body "$(cat <<'BODY'
## Summary
- bullet points

## Test plan
- [ ] test items

Generated with Claude Code
BODY
)" \
  --assignee 392fyc \
  --label "<bug|enhancement|refactor>"
```

#### Multi-PR Mode
When changes should be split by task category (e.g., feature + bugfix + UI):

1. Stash all changes: `git stash`
2. For each category:
   - Create branch from develop: `git checkout -b <branch-name> develop`
   - Selectively restore only this category's files: `git checkout stash@{0} -- <file1> <file2> ...`
   - Commit, push, create PR
3. After all branches created: `git stash drop`
4. Track all PR numbers for parallel review monitoring

### Phase 2: Poll for CodeRabbit Review (Non-Blocking)

**DO NOT** use blocking `sleep` loops. Use `CronCreate` for periodic checks:

```yaml
CronCreate:
  cron: "*/10 * * * *"
  prompt: <check-review-prompt>
  recurring: true
```

The check prompt should:
1. Fetch review status for all tracked PRs via `gh pr view <N> --json reviews`
2. Check for new inline comments via `gh api repos/{owner}/{repo}/pulls/<N>/comments`
3. Track consecutive checks via file-based counter (cron jobs are stateless across invocations):

   ```bash
   COUNT_FILE=".pr-flow-check-count-${PR_NUMBER}"
   COUNT=0; [ -f "$COUNT_FILE" ] && COUNT=$(<"$COUNT_FILE")
   if [ "$HAS_NEW_ACTIVITY" = "false" ]; then
     COUNT=$((COUNT + 1)); echo "$COUNT" > "$COUNT_FILE"
   else
     rm -f "$COUNT_FILE"  # reset on activity
   fi
   ```

4. After **3 consecutive checks with no CodeRabbit activity**, proactively trigger (with dedup):

   ```bash
   if [ "$COUNT" -ge 3 ]; then
     # Only trigger if no existing @coderabbitai review comment
     if ! gh api repos/{owner}/{repo}/issues/${PR_NUMBER}/comments --jq '.[].body' | grep -Fq "@coderabbitai review"; then
       gh pr comment "$PR_NUMBER" --body "@coderabbitai review"
     fi
     rm -f "$COUNT_FILE"
   fi
   ```

5. When reviews arrive:
   - **Single-PR mode**: cancel the cron job, clean up counter files, proceed to Phase 3
   - **Multi-PR mode**: only cancel the global cron when ALL tracked PRs have reviews or are merged/closed; process each PR's reviews independently via Phase 3

### Phase 3: Respond to ALL Review Threads

**Iteration cap**: Track review-fix iterations (Phase 2→5 cycles). Default `MAX_ITERATIONS=5`. If reached:
- Log a warning: "Max review iterations reached, requesting human intervention"
- Post a PR comment notifying the user
- Stop automatic rework and wait for human guidance

**CRITICAL RULE**: Every CodeRabbit comment MUST receive a response, even if you disagree.
This includes:
- **Inline comments** (accessible via `gh api repos/{owner}/{repo}/pulls/<N>/comments`)
- **Outside-diff comments** (embedded in review body, not inline — address in PR comment)
- **Review body suggestions** (often contain actionable architecture feedback)

#### For each inline comment:
1. Read the full comment body
2. Assess: is the issue valid?
   - **Valid**: Fix the code, reply with commit SHA and what changed
   - **Disagree**: Reply explaining why the current approach is correct
3. Reply via `gh api repos/{owner}/{repo}/pulls/comments/<ID>/replies -f body="..."`

#### For outside-diff comments:
1. These appear in the review body, not as inline threads
2. **IMPORTANT**: When posting PR comments (non-direct thread replies), always include `@coderabbitai` mention so CodeRabbit can detect and track the response
3. Address them in a PR comment summarizing all fixes:

   ```bash
   gh pr comment <PR_NUMBER> --body "@coderabbitai
   ## Addressed CodeRabbit review
   ### Inline comments (N/N resolved):
   1. **Issue** — fixed in <sha>
   ### Outside-diff comments (N/N resolved):
   1. **Issue** (lines X-Y) — fixed in <sha>"
   ```


### Phase 4: Fix Issues and Push

1. **Read** the relevant code sections before editing
2. **Edit** files to address valid feedback
3. **Build** to verify: `pnpm build` (or project-specific build command)
4. **Milestone code review** — review the diff before committing (per team rule: every milestone must be code-reviewed before commit)
5. **Commit** with descriptive message referencing what was fixed
6. **Push**: `git push`

### Phase 5: Resolve Threads

**IMPORTANT**: Resolve threads AFTER pushing fixes, not before.

#### Inline threads — resolve via GraphQL:

The `gh` CLI does not have a native command for resolving review threads.
Use `gh api graphql` with the `resolveReviewThread` mutation.

**Note**: `reviewThreads(first: 100)` is paginated. For PRs with >100 threads, use cursor-based pagination (`after: endCursor`) to collect all nodes. Most PRs have <100 threads, but the loop pattern is shown below.

```bash
# Get all unresolved thread IDs (with pagination for >100 threads)
CURSOR=""
ALL_THREADS="[]"
while true; do
  AFTER_ARG=""
  [ -n "$CURSOR" ] && AFTER_ARG=", after: \"$CURSOR\""
  RESULT=$(gh api graphql -f query="
  query {
    repository(owner: \"<OWNER>\", name: \"<NAME>\") {
      pullRequest(number: <N>) {
        reviewThreads(first: 100${AFTER_ARG}) {
          pageInfo { hasNextPage endCursor }
          nodes { id isResolved path }
        }
      }
    }
  }")
  NODES=$(echo "$RESULT" | jq '.data.repository.pullRequest.reviewThreads.nodes')
  ALL_THREADS=$(echo "$ALL_THREADS $NODES" | jq -s '.[0] + .[1]')
  HAS_NEXT=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
  [ "$HAS_NEXT" != "true" ] && break
  CURSOR=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
done

# Filter unresolved
UNRESOLVED=$(echo "$ALL_THREADS" | jq '[.[] | select(.isResolved==false)]')

# Resolve each thread using the GraphQL node ID (PRRT_...)
for THREAD_ID in $(echo "$UNRESOLVED" | jq -r '.[].id'); do
  gh api graphql -f query="mutation { resolveReviewThread(input: {threadId: \"$THREAD_ID\"}) { thread { id isResolved } } }"
done
```

#### Verify all threads resolved:

**Important**: After `resolveReviewThread` mutations, `ALL_THREADS` is stale. Re-run the pagination query to get fresh `isResolved` values:

```bash
# Re-query threads after resolution (same CURSOR-based loop as above)
CURSOR=""
ALL_THREADS="[]"
while true; do
  AFTER_ARG=""
  [ -n "$CURSOR" ] && AFTER_ARG=", after: \"$CURSOR\""
  RESULT=$(gh api graphql -f query="
  query {
    repository(owner: \"<OWNER>\", name: \"<NAME>\") {
      pullRequest(number: <N>) {
        reviewThreads(first: 100${AFTER_ARG}) {
          pageInfo { hasNextPage endCursor }
          nodes { id isResolved path }
        }
      }
    }
  }")
  NODES=$(echo "$RESULT" | jq '.data.repository.pullRequest.reviewThreads.nodes')
  ALL_THREADS=$(echo "$ALL_THREADS $NODES" | jq -s '.[0] + .[1]')
  HAS_NEXT=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
  [ "$HAS_NEXT" != "true" ] && break
  CURSOR=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
done

UNRESOLVED_COUNT=$(echo "$ALL_THREADS" | jq '[.[] | select(.isResolved==false)] | length')
if [ "$UNRESOLVED_COUNT" -gt 0 ]; then
  echo "WARNING: $UNRESOLVED_COUNT unresolved threads remain"
fi
```

### Phase 6: Wait for Re-Review

Increment iteration counter before looping back:

```bash
MAX_ITERATIONS=${MAX_ITERATIONS:-5}
ITER_FILE=".pr-flow-iteration-${PR_NUMBER}"
ITER=0; [ -f "$ITER_FILE" ] && ITER=$(<"$ITER_FILE")
ITER=$((ITER + 1)); echo "$ITER" > "$ITER_FILE"

if [ "$ITER" -ge "$MAX_ITERATIONS" ]; then
  echo "Max review iterations ($MAX_ITERATIONS) reached. Requesting human intervention."
  # Post PR comment and wait for user guidance
  # Optionally file remaining issues as follow-up tasks
fi
```

Return to **Phase 2** polling. Repeat the Phase 2-5 cycle until CodeRabbit approves.

Typical convergence: 1-2 iterations for most PRs. Clean up `$ITER_FILE` after merge.

### Phase 7: Merge

#### Pre-merge gate checks:

```bash
# 1. CI status
gh pr checks "$PR_NUMBER"

# 2. Review approval — must be "APPROVED"
gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision'

# 3. No unresolved threads (any unresolved thread blocks merge)
# Re-run the Phase 5 CURSOR-based pagination to freshly populate ALL_THREADS
CURSOR=""
ALL_THREADS="[]"
while true; do
  AFTER_ARG=""
  [ -n "$CURSOR" ] && AFTER_ARG=", after: \"$CURSOR\""
  RESULT=$(gh api graphql -f query="
  query {
    repository(owner: \"<OWNER>\", name: \"<NAME>\") {
      pullRequest(number: <N>) {
        reviewThreads(first: 100${AFTER_ARG}) {
          pageInfo { hasNextPage endCursor }
          nodes { id isResolved }
        }
      }
    }
  }")
  NODES=$(echo "$RESULT" | jq '.data.repository.pullRequest.reviewThreads.nodes')
  ALL_THREADS=$(echo "$ALL_THREADS $NODES" | jq -s '.[0] + .[1]')
  HAS_NEXT=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
  [ "$HAS_NEXT" != "true" ] && break
  CURSOR=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
done

UNRESOLVED_COUNT=$(echo "$ALL_THREADS" | jq '[.[] | select(.isResolved==false)] | length')
if [ "$UNRESOLVED_COUNT" -gt 0 ]; then
  echo "Blocked: $UNRESOLVED_COUNT unresolved review threads remain"
  exit 1
fi
```

#### Merge:

```bash
MERGE_STRATEGY=${MERGE_STRATEGY:-squash}
gh pr merge "$PR_NUMBER" "--$MERGE_STRATEGY" --delete-branch
```

### Phase 8: Update Issues and Tasks

After merge, update related GitHub issues and Mercury task state:
- Close related issues if PR body contains "Closes #N"
- Signal task completion to Mercury orchestrator if available (actual state transition is Main Agent's responsibility):

  ```bash
  # Extract taskId from branch name convention
  TASK_ID=$(git branch --show-current | grep -oE 'TASK-[A-Z][A-Z0-9-]+-[0-9]+' || echo "")

  # Check if Mercury RPC is available
  if [ -n "$TASK_ID" ] && [ -f .mercury/dispatch.sh ]; then
    bash .mercury/dispatch.sh "$TASK_ID" || echo "RPC call failed; Main Agent will handle manually"
  fi
  ```

- Clean up iteration/counter files: `rm -f .pr-flow-iteration-* .pr-flow-check-count-*`

- **Clean up worktrees and local branches** after merge (worktree first, then branch — per Mercury worktree-workflow protocol):

  ```bash
  BRANCH=$(gh pr view "$PR_NUMBER" --json headRefName --jq '.headRefName')

  # Remove worktree first (branch can't be deleted while checked out in a worktree)
  WORKTREE=$(git worktree list --porcelain | awk -v b="refs/heads/$BRANCH" '
    /^worktree /{wt=substr($0,10)}
    /^branch / && substr($0,8)==b {print wt; exit}
  ')
  if [ -n "$WORKTREE" ]; then
    git worktree remove --force "$WORKTREE" 2>/dev/null || true
  fi

  # Delete local branch after worktree removal (remote already deleted by --delete-branch)
  git branch -d "$BRANCH" 2>/dev/null || true
  ```

## Multi-PR Coordination

When running multiple PRs in parallel:

1. Create all PRs first (Phase 1), persist PR numbers:

   ```bash
   echo "$PR1 $PR2 $PR3" > .pr-flow-multi.txt
   ```

2. Set ONE cron job that checks ALL PRs simultaneously — the cron prompt reads `.pr-flow-multi.txt`
3. Process reviews independently per PR (each gets its own counter file)
4. Merge in dependency order (foundation PRs first). Define order as an ordered list:

   ```bash
   # Merge in order: types → orchestrator → GUI
   for PR in $PR_FOUNDATION $PR_FEATURE $PR_UI; do
     gh pr merge "$PR" --squash --delete-branch
   done
   ```

5. After all PRs merged: `CronDelete`, then `rm -f .pr-flow-multi.txt .pr-flow-check-count-*`

## Review Response Protocol

| Situation | Action |
|---|---|
| Valid inline issue | Fix code, reply with SHA, resolve thread |
| Valid outside-diff issue | Fix code, post PR comment (no thread to resolve) |
| Disagree with suggestion | Reply explaining reasoning, resolve thread |
| Nitpick/style suggestion | Evaluate: fix if trivial, explain if opinionated, resolve |
| Out-of-scope suggestion | Acknowledge, explain it is out of scope, resolve thread |

**Rules**:
- Even threads you disagree with MUST be commented on and resolved. CodeRabbit will not re-approve while unresolved threads remain.
- When posting non-direct replies (PR comments instead of thread replies), always include `@coderabbitai` so CodeRabbit can detect the response.

## Output

```text
PR: #<number> (<url>)
CodeRabbit: approved | changes_requested | pending
Feedback: <N> inline, <N> outside-diff, <N> addressed, iteration=<N>
Merge: merged | waiting | blocked
Task: <taskId> -> done | pending
```
