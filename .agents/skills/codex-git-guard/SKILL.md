---
name: codex-git-guard
description: |
  Use this skill when the user asks to commit, push, create a branch, open a PR, merge, or otherwise mutate git state from Codex. Trigger proactively on English and Chinese requests such as "commit", "push", "branch", "PR", "merge", "提交", "推送", "分支", "建分支", "提PR", "合并". This skill enforces Mercury's protected-branch workflow for Codex, including the Windows limitation that Codex hooks are unavailable and guardrails must run through repo instructions, skills, and scripts.
---

# Codex Git Guard

## When

- Use before any `git commit`, `git push`, `gh pr create`, `gh pr merge`, or branch-changing request in Codex sessions.
- Use even if the user only asked for code changes when the task is likely to end in a commit.
- Do not use for read-only git commands such as `git status`, `git diff`, or `git log`.

## Pipeline

1. Check the current branch first:

```powershell
git rev-parse --abbrev-ref HEAD
```

2. If the branch is `develop`, `master`, or `main`, stop. Do not commit or push there.
3. If the branch does not match `feature/TASK-*`, stop and move the work to a task branch before mutating git state.
4. When recovering from an accidental protected-branch local commit:
   - create a fresh worktree or branch from `origin/develop`
   - cherry-pick or re-commit the work there
   - if the repository still uses Claude-specific hooks or a `.claude/` directory, prefer leaving them untouched unless the user explicitly asks to modify them
5. Stage files through the safe wrapper:
   - run `powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 add <path> [more paths...]`
   - never use raw `git add .`, `git add -A`, or `git add --all`
6. Before `git commit`:
   - complete a code review
   - run `powershell -ExecutionPolicy Bypass -File scripts/codex/guard.ps1 mark-review`
   - invoke the `auto-verify` skill
   - if `mark-review` or `auto-verify` fails with a non-zero exit code or throws, stop immediately and do not commit
   - run `powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 commit -Message "<message>"`
7. After a successful commit, the wrapper clears the review flag automatically.
8. Before `git push`:
   - run `powershell -ExecutionPolicy Bypass -File scripts/codex/git-safe.ps1 push origin <branch>`
   - never target `develop`, `master`, or `main`
9. If the task touches external SDK/API/CLI behavior, invoke `web-research` before editing.

## Output

Use a compact status note:

```text
## Codex Git Guard
Branch: PASS | FAIL
ReviewFlag: PASS | FAIL | SKIP
PushTarget: PASS | FAIL | SKIP
Overall: PASS | FAIL
```

- State the exact blocking condition when a check fails.
- Do not recommend committing or pushing while the guard status is `FAIL`.
