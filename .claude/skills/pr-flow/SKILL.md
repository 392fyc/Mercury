---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, wait for CI checks, read CodeRabbit and reviewer comments, dispatch fixes if needed, and merge. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments". This skill should be invoked whenever a task reaches the PR stage (after implementation_done + main_review). It replaces the manual C4-C7 steps in the Mercury workflow.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob
---

# PR Flow

Automates the PR lifecycle that follows Mercury's main_review approval. This replaces manual steps C4-C7 in the ideal workflow, reducing human intervention while preserving all safety gates.

## Prerequisites

- `gh` CLI v2.x+ (GitHub CLI, authenticated)
- `git` (configured with push access to remote)
- `jq` (for JSON parsing of API responses)
- Current branch must be a feature branch, not `develop` or `main`

Verify before starting:

```bash
gh --version && git remote -v && jq --version
```

## When

- After a task reaches `main_review` and the main agent approves for acceptance.
- When the user explicitly asks to create, check, or merge a PR.
- After dev work is committed and pushed on a feature branch.

## Pipeline

### Step 1: Create PR (idempotent)

Check if a PR already exists for this branch before creating:

```bash
EXISTING_PR=$(gh pr list --head "$(git branch --show-current)" --json number --jq '.[0].number // empty')
if [ -n "$EXISTING_PR" ]; then
  echo "PR #$EXISTING_PR already exists for this branch, reusing."
  PR_NUMBER=$EXISTING_PR
else
  git push -u origin "$(git branch --show-current)"
  PR_NUMBER=$(gh pr create \
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
  - [ ] CodeRabbit review addressed" \
    --json number --jq '.number')
fi
```

Target branch is always `develop` per Mercury git-flow rules.

After PR creation (or reuse), set PR metadata and trigger review:

```bash
# Set author, assignee, labels, and project
gh pr edit $PR_NUMBER \
  --add-assignee "@me" \
  --add-label "refactor" # or "feature", "bugfix" as appropriate

# Trigger CodeRabbit review explicitly (auto-pause may block automatic review)
gh pr comment $PR_NUMBER --body "@coderabbitai review"
```

> **Why explicit mention?** CodeRabbit auto-pauses review on branches with rapid commits. Always `@coderabbitai review` after the final push to guarantee review starts.

### Step 2: Poll CI Checks (scope-bounded fixes)

Wait for CI checks to complete:

```bash
gh pr checks $PR_NUMBER --watch --fail-fast
```

If checks fail:
1. Read the failing check output
2. **Scope check**: Before fixing, verify the fix touches only files within the task's `allowedWriteScope.codePaths`. If the required fix is outside scope, report to user instead of fixing.
3. Classify the failure:
   - **Lint/format errors**: Fixable in scope — auto-fix and push
   - **Type errors in changed files**: Fixable in scope — fix and push
   - **Test failures in unchanged files**: NOT fixable in scope — report to user
   - **Build/infra failures**: NOT fixable in scope — report to user
4. If fixable, commit as `fix(CI): <what>` and push

### Step 3: Wait for CodeRabbit Review

Mercury rules require waiting for CodeRabbit to **approve** before merge. Poll with timeout:

```bash
# Poll review status (max 15 min, check every 60s)
for i in $(seq 1 15); do
  REVIEWS=$(gh pr reviews $PR_NUMBER --json state --jq '.[].state')
  HAS_APPROVED=$(echo "$REVIEWS" | grep -c "APPROVED" || true)
  HAS_CHANGES=$(echo "$REVIEWS" | grep -c "CHANGES_REQUESTED" || true)
  [ "$HAS_APPROVED" -gt 0 ] && [ "$HAS_CHANGES" -eq 0 ] && break
  sleep 60
done
```

- **Timeout**: 15 minutes max. If CodeRabbit unresponsive, notify user.
- **Completion**: at least one `APPROVED` **and** zero `CHANGES_REQUESTED`.

### Step 4: Parse and Classify CodeRabbit Feedback

Fetch all review comments:

```bash
gh api repos/{owner}/{repo}/pulls/$PR_NUMBER/comments --jq '.[] | {id: .id, path: .path, body: .body}'
```

**Severity mapping** (parse the first line of each comment body):

| CodeRabbit label | Severity | Action |
|------------------|----------|--------|
| `⚠️ Potential issue` + `🔴 Critical` | **Critical** | Must fix before merge |
| `⚠️ Potential issue` + `🟠 Major` | **Major** | Fix if in scope |
| `⚠️ Potential issue` + `🟡 Minor` | **Minor** | Fix if trivial |
| `🧹 Nitpick` + `🔵 Trivial` | **Nitpick** | Optional, apply if <5 min effort |
| `💡 Verification successful` | **Info** | No action needed |

For each Critical or Major comment:
1. Read the comment body for suggested code changes (look for ` ```suggestion` or ` ```diff` blocks)
2. **Scope check**: Verify `path` is within `allowedWriteScope.codePaths`
3. Apply the fix to the referenced file and line
4. If the comment has no concrete suggestion, use your judgment but stay within scope

### Step 5: Apply Fixes and Re-verify

```bash
# After applying fixes:
git add <changed-files>
git commit -m "fix(PR-feedback): <what was fixed>"
git push

# Re-run type check locally
npx tsc --noEmit
```

After pushing fixes, CodeRabbit will re-review the new commits. Repeat Steps 3-4 if needed (max 3 iterations to prevent infinite loops).

### Step 6: Merge and Update Mercury State

Only merge when:
- All CI checks pass
- CodeRabbit review state is `APPROVED` (not just `COMMENTED`)
- No unresolved Critical comments

```bash
# Ask user for confirmation first
gh pr merge $PR_NUMBER --squash --delete-branch
```

After merge, update Mercury task state via RPC:

```bash
# Transition task to done (if orchestrator is running)
curl -s http://localhost:${MERCURY_RPC_PORT:-7654}/rpc -X POST \
  -H "Content-Type: application/json" \
  -d '{"method": "transition_task", "params": {"taskId": "<taskId>", "to": "done"}}'
```

If orchestrator is not running, note the pending state transition in the output for manual follow-up.

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
pr-flow: PR #<number> merged to develop (CI: pass, CodeRabbit: <N> comments addressed, task <taskId> → done)
```

## Safety Rules

- Never merge without CodeRabbit review **approving** (not just commenting)
- Never force-push to the PR branch
- Never merge to main/master directly
- Always ask user confirmation before merge
- If PR has merge conflicts, report and let user decide resolution strategy
- Scope-bound all fixes: never modify files outside `allowedWriteScope`
- Max 3 fix-review iterations to prevent infinite loops
