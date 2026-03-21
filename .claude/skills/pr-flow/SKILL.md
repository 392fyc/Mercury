---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, wait for CI checks, read CodeRabbit and reviewer comments, dispatch fixes if needed, and merge. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments". This skill should be invoked whenever a task reaches the PR stage (after implementation_done + main_review). It replaces the manual C4-C7 steps in the Mercury workflow.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob
---

# PR Flow

Automates the PR lifecycle that follows Mercury's main_review approval. This replaces manual steps C4-C7 in the ideal workflow, reducing human intervention while preserving all safety gates.

## When

- After a task reaches `main_review` and the main agent approves for acceptance.
- When the user explicitly asks to create, check, or merge a PR.
- After dev work is committed and pushed on a feature branch.

## Pipeline

### Step 1: Create PR

```bash
git push -u origin <branch-name>

gh pr create \
  --base develop \
  --title "<taskId>: <short summary>" \
  --body "## Summary
<bullet points from implementationReceipt.summary>

## Task
- TaskId: <taskId>
- Branch: <branch>
- DoD items completed: <count>

## Test plan
- [ ] TypeScript compilation passes
- [ ] Scope validation passes
- [ ] CodeRabbit review addressed"
```

Target branch is always `develop` per Mercury git-flow rules.

### Step 2: Poll CI Checks

Wait for CI checks to complete. Poll with backoff:

```bash
gh pr checks <pr-number> --watch --fail-fast
```

If checks fail:
- Read the failing check output
- If fixable in scope, fix and push
- If not fixable, report to user

### Step 3: Wait for CodeRabbit Review

Mercury rules require waiting for CodeRabbit before merge. Poll for review:

```bash
gh pr reviews <pr-number>
gh api repos/{owner}/{repo}/pulls/<pr-number>/comments
```

Parse CodeRabbit comments for actionable feedback. Categories:
- **Critical**: Must fix before merge
- **Suggestion**: Fix if in scope, otherwise note as tech debt
- **Nitpick**: Optional, apply if trivial

### Step 4: Address Feedback

For each critical or actionable comment:
1. Apply the fix
2. Commit with message: `fix(PR-feedback): <what was fixed>`
3. Push to the same branch
4. Reply to the comment thread if using `gh api`

### Step 5: Merge

Only merge when:
- All CI checks pass
- CodeRabbit review is complete (not just started)
- No unresolved critical comments

```bash
gh pr merge <pr-number> --squash --delete-branch
```

Ask user for confirmation before executing merge.

## Output

```text
## PR Flow Results
PR: #<number> (<url>)
CI Checks: PASS | FAIL
CodeRabbit: reviewed | pending | N comments
Feedback: <N> critical, <N> suggestions addressed
Status: merged | waiting | blocked
```

## Evidence

```text
pr-flow: PR #<number> merged to develop (CI: pass, CodeRabbit: <N> comments addressed)
```

## Safety Rules

- Never merge without CodeRabbit review completing (Mercury rule)
- Never force-push to the PR branch
- Never merge to main/master directly
- Always ask user confirmation before merge
- If PR has merge conflicts, report and let user decide resolution strategy
