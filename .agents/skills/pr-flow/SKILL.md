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

```powershell
git push -u origin <branch-name>

gh pr create `
  --base develop `
  --title "<taskId>: <short summary>" `
  --body "## Summary`n<bullets from receipt>`n`n## Task`n- TaskId: <taskId>"
```

2. Poll CI checks:

```powershell
gh pr checks <pr-number> --watch --fail-fast
```

If checks fail, read output, fix if in scope, push again.

3. Wait for CodeRabbit review (Mercury rule: never merge before review completes):

```powershell
gh pr reviews <pr-number>
gh api repos/{owner}/{repo}/pulls/<pr-number>/comments
```

4. Address feedback:
   - Critical comments: must fix before merge
   - Suggestions: fix if in scope, otherwise note as tech debt
   - Commit fixes as `fix(PR-feedback): <what>`

5. Merge (ask user confirmation first):

```powershell
gh pr merge <pr-number> --squash --delete-branch
```

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
