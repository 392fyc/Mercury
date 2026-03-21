---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, wait for CI checks, read CodeRabbit and reviewer comments, dispatch fixes if needed, and merge. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments". This skill should be invoked whenever a task reaches the PR stage (after implementation_done + main_review).
---

# PR Flow

## Prerequisites

- `gh` CLI v2.x+ (authenticated)
- `git` (with push access)
- `jq` (JSON parsing)

> **Platform note**: Examples use bash syntax. For PowerShell, replace `$(...)` with `$()`, `grep -c` with `Select-String`, and adjust variable syntax.

## When

- After a task reaches `main_review` and the main agent approves.
- When the user asks to create, check, or merge a PR.
- After dev work is committed and pushed on a feature branch.

## Pipeline

1. **Create PR (idempotent)** — Check for existing PR before creating:

```bash
EXISTING_PR=$(gh pr list --head "$(git branch --show-current)" --json number --jq '.[0].number // empty')
if [ -n "$EXISTING_PR" ]; then
  echo "PR #$EXISTING_PR already exists, reusing."
  PR_NUMBER=$EXISTING_PR
else
  git push -u origin "$(git branch --show-current)"
  PR_NUMBER=$(gh pr create \
    --base develop \
    --title "<taskId>: <short summary>" \
    --body "## Summary
<bullets from receipt>

## Task
- TaskId: <taskId>" \
    --json number --jq '.number')
fi
```

2. **Poll CI checks (scope-bounded fixes)**:

```bash
gh pr checks $PR_NUMBER --watch --fail-fast
```

If checks fail:
- **Scope check first**: Verify fix is within `allowedWriteScope.codePaths`
- Lint/format in scope → auto-fix and push
- Type errors in changed files → fix and push
- Test failures in unchanged files → report to user (out of scope)
- Build/infra failures → report to user (out of scope)

3. **Wait for CodeRabbit review** (Mercury rule: never merge before review **approves**):

```bash
for i in $(seq 1 15); do
  REVIEWS=$(gh pr reviews $PR_NUMBER --json state --jq '.[].state')
  HAS_APPROVED=$(echo "$REVIEWS" | grep -c "APPROVED" || true)
  HAS_CHANGES=$(echo "$REVIEWS" | grep -c "CHANGES_REQUESTED" || true)
  [ "$HAS_APPROVED" -gt 0 ] && [ "$HAS_CHANGES" -eq 0 ] && break
  sleep 60
done
```

- **Timeout**: 15 minutes max. If unresponsive, escalate to user.
- **Completion**: at least one `APPROVED` **and** zero `CHANGES_REQUESTED`.

4. **Parse and classify CodeRabbit feedback**:

```bash
gh api repos/{owner}/{repo}/pulls/$PR_NUMBER/comments --jq '.[] | {id: .id, path: .path, body: .body}'
```

Severity mapping (parse first line of comment body):

| CodeRabbit label | Severity | Action |
|---|---|---|
| `⚠️ Potential issue` + `🔴 Critical` | Critical | Must fix |
| `⚠️ Potential issue` + `🟠 Major` | Major | Fix if in scope |
| `⚠️ Potential issue` + `🟡 Minor` | Minor | Fix if trivial |
| `🧹 Nitpick` + `🔵 Trivial` | Nitpick | Optional |
| `💡 Verification successful` | Info | No action |

For each Critical/Major:
- Read for `suggestion` or `diff` blocks in comment body
- **Scope check**: Verify `path` is within `allowedWriteScope`
- Apply fix, commit as `fix(PR-feedback): <what>`, push
- Max 3 fix-review iterations

5. **Merge and update Mercury state** (ask user confirmation first):

```bash
gh pr merge $PR_NUMBER --squash --delete-branch
```

After merge, update task state:

```powershell
# PowerShell (Codex environment)
Invoke-RestMethod -Uri "http://localhost:$($env:MERCURY_RPC_PORT ?? 7654)/rpc" `
  -Method POST -ContentType "application/json" `
  -Body '{"method":"transition_task","params":{"taskId":"<taskId>","to":"done"}}'
```

If orchestrator is not running, note pending transition in output.

## Output

```text
## PR Flow Results
PR: #<number> (<url>)
CI Checks: PASS | FAIL
CodeRabbit: approved | pending | N comments
Feedback: <N> critical, <N> major, <N> suggestions addressed
Mercury State: <taskId> → done | pending manual transition
Status: merged | waiting | blocked
```

## Evidence

```text
pr-flow: PR #<number> merged to develop (CI: pass, CodeRabbit: <N> addressed, task <taskId> → done)
```
