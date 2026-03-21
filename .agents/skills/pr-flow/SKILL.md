---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, wait for CI checks, read CodeRabbit and reviewer comments, dispatch fixes if needed, and merge. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments". This skill should be invoked whenever a task reaches the PR stage (after implementation_done + main_review).
---

# PR Flow

## When

- After a task reaches `main_review` and the main agent approves.
- When the user asks to create, check, or merge a PR.
- After dev work is committed and pushed on a feature branch.

## Pipeline

1. Push branch and create PR targeting `develop`:

```bash
git push -u origin <branch-name>

gh pr create \
  --base develop \
  --title "<taskId>: <short summary>" \
  --body "## Summary
<bullets from receipt>

## Task
- TaskId: <taskId>"
```

2. Poll CI checks:

```bash
gh pr checks <pr-number> --watch --fail-fast
```

If checks fail, read output, fix if in scope, push again.

3. Wait for CodeRabbit review (Mercury rule: never merge before review **approves**):

```bash
# Poll review status (max 15 min, check every 60s)
for i in $(seq 1 15); do
  STATUS=$(gh pr reviews <pr-number> --json state --jq '.[].state' | tail -1)
  [ "$STATUS" = "APPROVED" ] && break
  sleep 60
done
```

- **Timeout**: 15 minutes max polling. If CodeRabbit is unresponsive, notify user and wait for manual decision.
- **Completion criteria**: `gh pr reviews` shows at least one `APPROVED` state and no pending `CHANGES_REQUESTED`.
- **Service unavailable**: If CodeRabbit status check remains `PENDING` beyond timeout, escalate to user.

4. Address feedback:
   - Critical comments: must fix before merge
   - Suggestions: fix if in scope, otherwise note as tech debt
   - Commit fixes as `fix(PR-feedback): <what>`

5. Merge (ask user confirmation first):

```bash
gh pr merge <pr-number> --squash --delete-branch
```

## Output

```text
## PR Flow Results
PR: #<number> (<url>)
CI Checks: PASS | FAIL
CodeRabbit: approved | pending | N comments
Feedback: <N> critical, <N> suggestions addressed
Status: merged | waiting | blocked
```

## Evidence

```text
pr-flow: PR #<number> merged to develop (CI: pass, CodeRabbit: <N> comments addressed)
```
