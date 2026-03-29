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
   - Restore relevant files: `git stash pop` or selective `git checkout stash -- <files>`
   - Commit, push, create PR
3. Track all PR numbers for parallel review monitoring

### Phase 2: Poll for CodeRabbit Review (Non-Blocking)

**DO NOT** use blocking `sleep` loops. Use `CronCreate` for periodic checks:

```
CronCreate:
  cron: "*/10 * * * *"
  prompt: <check-review-prompt>
  recurring: true
```

The check prompt should:
1. Fetch review status for all tracked PRs via `gh pr view <N> --json reviews`
2. Check for new inline comments via `gh api repos/{owner}/{repo}/pulls/<N>/comments`
3. Track consecutive checks with no new activity
4. After **3 consecutive checks with no CodeRabbit activity**, proactively trigger:
   ```bash
   gh pr comment <PR_NUMBER> --body "@coderabbitai review"
   ```
5. When reviews arrive, cancel the cron job and proceed to Phase 3

### Phase 3: Respond to ALL Review Threads

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
2. Address them in a PR comment summarizing all fixes:
   ```bash
   gh pr comment <PR_NUMBER> --body "## Addressed CodeRabbit review
   ### Inline comments (N/N resolved):
   1. **Issue** — fixed in <sha>
   ### Outside-diff comments (N/N resolved):
   1. **Issue** (lines X-Y) — fixed in <sha>"
   ```

### Phase 4: Fix Issues and Push

1. **Read** the relevant code sections before editing
2. **Edit** files to address valid feedback
3. **Build** to verify: `pnpm build` (or project-specific build command)
4. **Commit** with descriptive message referencing what was fixed
5. **Push**: `git push`

### Phase 5: Resolve Threads

**IMPORTANT**: Resolve threads AFTER pushing fixes, not before.

#### Inline threads — resolve via GraphQL:

The `gh` CLI does not have a native command for resolving review threads.
Use `gh api graphql` with the `resolveReviewThread` mutation:

```bash
# Get all unresolved thread IDs
gh api graphql -f query='
query {
  repository(owner: "<OWNER>", name: "<NAME>") {
    pullRequest(number: <N>) {
      reviewThreads(first: 100) {
        nodes { id isResolved path }
      }
    }
  }
}'

# Resolve each thread using the GraphQL node ID (PRRT_...)
gh api graphql -F threadId="<THREAD_ID>" -f query='
mutation($threadId: ID!) {
  resolveReviewThread(input: { threadId: $threadId }) {
    thread { id isResolved }
  }
}'
```

#### Verify all threads resolved:
```bash
# Count remaining unresolved threads
gh api graphql -f query='...' --jq '[.data...nodes[] | select(.isResolved==false)] | length'
```

### Phase 6: Wait for Re-Review

Return to **Phase 2** polling. Repeat the Phase 2-5 cycle until CodeRabbit approves.

Typical convergence: 1-2 iterations for most PRs.

### Phase 7: Merge

#### Pre-merge gate checks:
```bash
# 1. CI status
gh pr checks "$PR_NUMBER"

# 2. Review approval — must be "APPROVED"
gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision'

# 3. No unresolved critical/major threads (use GraphQL)
```

#### Merge:
```bash
MERGE_STRATEGY=${MERGE_STRATEGY:-squash}
gh pr merge "$PR_NUMBER" "--$MERGE_STRATEGY" --delete-branch
```

### Phase 8: Update Issues and Tasks

After merge, update related GitHub issues and Mercury task state:
- Close related issues if PR body contains "Closes #N"
- Update Mercury task status via orchestrator RPC if available

## Multi-PR Coordination

When running multiple PRs in parallel:
1. Create all PRs first (Phase 1)
2. Set ONE cron job that checks ALL PRs simultaneously
3. Process reviews independently per PR
4. Merge in dependency order (foundation PRs first)
5. Cancel cron job after all PRs are merged

## Review Response Protocol

| Situation | Action |
|---|---|
| Valid inline issue | Fix code, reply with SHA, resolve thread |
| Valid outside-diff issue | Fix code, post PR comment (no thread to resolve) |
| Disagree with suggestion | Reply explaining reasoning, resolve thread |
| Nitpick/style suggestion | Evaluate: fix if trivial, explain if opinionated, resolve |
| Out-of-scope suggestion | Acknowledge, explain it is out of scope, resolve thread |

**Rule**: Even threads you disagree with MUST be commented on and resolved.
CodeRabbit will not re-approve while unresolved threads remain.

## Output

```text
PR: #<number> (<url>)
CodeRabbit: approved | changes_requested | pending
Feedback: <N> inline, <N> outside-diff, <N> addressed, iteration=<N>
Merge: merged | waiting | blocked
Task: <taskId> -> done | pending
```
