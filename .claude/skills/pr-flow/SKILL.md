---
name: pr-flow
description: |
  Automate the full PR lifecycle with Argus review bot: create PR, poll for review, read findings, fix issues, push and wait for Argus fix-detection resolve + incremental review, merge after approval. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments", "标准PR流程". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`. It replaces the manual C4-C7 steps in the Mercury workflow.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, WebSearch, WebFetch, Agent, TodoWrite, CronCreate, CronDelete
---

# PR Flow — Argus-Compatible Sequential Protocol

Every phase has a **GATE** that MUST pass before proceeding. Do NOT skip gates.

## Argus Behavior Model

Understand these Argus capabilities before executing:

- **Fix-detection resolve (B-1)**: Argus compares new commit diff against open threads by file+line. If code at the thread location changed, Argus auto-resolves the thread.
- **New findings block APPROVE (A)**: If the current review iteration produces ANY new findings, Argus returns COMMENT, not APPROVE. APPROVE only happens when: zero new findings AND all threads resolved.
- **Reply-aware resolution (C)**: When a thread has a human/agent reply, Argus uses LLM to classify: ACCEPT (resolve + ack), REJECT (keep open + follow-up), ESCALATE (mark for human). Max 3 reply rounds per thread.

**Agent behavioral rules:**

| Rule | Detail |
|------|--------|
| **禁止手动 resolve thread** | All resolve by Argus fix-detection or reply-aware resolution |
| **禁止回复 fix comments** | Don't reply "Fixed in xxx" — diff is the explanation |
| **Push 后必须等待** | Wait for Argus incremental review before next action |
| **只在 disagree 时回复** | Reply only when NOT fixing — Argus LLM will classify the reply |
| **以 Argus review 结果为准** | Don't guess whether something is resolved |

## Variables

```
PR_NUMBER=<number>
PR_URL=<url>
OWNER=392fyc
REPO_NAME=Mercury
ITERATION=0
MAX_ITERATIONS=5
```

## Phase 1: Create PR

**MANDATORY**: Push branch, then create PR with metadata.

```bash
BRANCH=$(git branch --show-current)
git push -u origin "$BRANCH"
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

**GATE 1**: PR created. Extract and store `PR_NUMBER` and `PR_URL`.

## Phase 2: Poll for Initial Review

**MANDATORY**: Create a CronCreate job to poll. Do NOT use sleep loops.

```
CronCreate:
  cron: "*/10 * * * *"
  prompt: |
    Check PR #<PR_NUMBER> review status.
    1. Run: gh pr view <PR_NUMBER> --json reviews,reviewDecision
    2. If reviewDecision is APPROVED → report to user, delete this cron
    3. If new inline comments from argus-review[bot] exist → report to user for Phase 3
    4. Track no-activity count in .pr-flow-check-count-<PR_NUMBER>
    5. After 3 quiet checks, post "@argus-review review" (max 3 total triggers per cron_safety rule)
  recurring: true
```

**GATE 2**: Reviews have arrived (Argus has posted inline comments or review body).

When cron reports reviews arrived:
1. `CronDelete` the polling job
2. Clean up: `rm -f .pr-flow-check-count-*`
3. Proceed to Phase 3

## Phase 3: Read + Triage ALL Findings

**MANDATORY**: Read every finding before fixing anything.

### Step 3a: Fetch all inline comments

```bash
MSYS_NO_PATHCONV=1 gh api "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/comments" \
  --jq '.[] | select(.user.login == "argus-review[bot]") | {id, path, line, body}'
```

### Step 3b: Fetch review body

```bash
MSYS_NO_PATHCONV=1 gh api "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/reviews" \
  --jq '.[] | select(.user.login == "argus-review[bot]") | {id, state, body}'
```

### Step 3c: Parse Argus comment format

Each inline comment has this structure:

```
_<SEVERITY_EMOJI> <Severity>_ | _<Category>_ [| importance: N/10]

**<Description>**

<details><summary>📝 Suggestion</summary>
```code
<suggested fix>
```
</details>

<details><summary>📝 Committable suggestion</summary>
```suggestion
<ready-to-apply code>
```
</details>

<details><summary>🤖 Prompt for AI Agents</summary>
```text
In file `<path>` around lines <N>-<N>:
<machine-readable description>
```
</details>
```

**Severity levels**:
- `🔴 Critical` / `importance: 9-10` → MUST fix
- `🟡 Medium` / `importance: 7-8` → SHOULD fix unless strong reason to disagree
- `🔵 Minor` / `importance: 1-6` → Fix if trivial, explain if opinionated

### Step 3d: Build triage list

For each finding, decide: `fix` or `disagree`

**GATE 3**: All findings enumerated with action decisions.

## Phase 4: Fix Code

For each finding marked `fix`:

### Step 4a: Locate and read the code

Use `🤖 Prompt for AI Agents` block or `path:line` to find exact location.

### Step 4b: Apply fix

| Severity | Committable suggestion? | Action |
|----------|------------------------|--------|
| 🔴 Critical | Yes | Apply the suggestion |
| 🔴 Critical | No | Read context, write fix |
| 🟡 Medium | Yes | Apply unless incorrect |
| 🟡 Medium | No | Fix based on description |
| 🔵 Minor | Any | Fix if trivial (<5 lines) |

### Step 4c: Handle disagree findings

For findings marked `disagree`: reply with reasoning. Do NOT resolve.

```bash
MSYS_NO_PATHCONV=1 gh api -X POST \
  "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/comments/<COMMENT_ID>/replies" \
  -f body="<reasoning why this is by design or out of scope>"
```

Argus will LLM-classify the reply:
- **ACCEPT** → Argus resolves the thread automatically
- **REJECT** → Argus posts follow-up, thread stays open → read follow-up, decide again
- **ESCALATE** → Thread marked for human intervention → stop processing this thread

**GATE 4**: All `fix` items have code changes. All `disagree` items have reply posted.

## Phase 5: Push and Wait

### Step 5a: Commit and push

```bash
git add <changed-files>
git commit -m "fix: address review feedback — <summary> (#issue)"
git push
```

### Step 5b: Wait for Argus incremental review

**MANDATORY**: Do NOT resolve threads. Do NOT post re-review triggers. Just wait.

Create a CronCreate job to poll for Argus response:

```
CronCreate:
  cron: "*/10 * * * *"
  prompt: |
    Check PR #<PR_NUMBER> for Argus incremental review after fix push.
    1. Run: gh pr view <PR_NUMBER> --json reviews,reviewDecision
    2. Check for new reviews from argus-review[bot] after the fix commit
    3. If APPROVED → report to user, delete this cron
    4. If new COMMENT review with findings → report findings to user
    5. After 3 quiet checks, post "@argus-review review -i" (incremental, max 3 triggers)
  recurring: true
```

### Step 5c: Process incremental review result

When Argus responds:
- **APPROVED** (no new findings, all threads resolved) → Proceed to Phase 6
- **COMMENT** (new findings) → Increment ITERATION, return to Phase 3
- **REQUEST_CHANGES** (critical/major blocking) → Increment ITERATION, return to Phase 3

```bash
ITERATION=$((ITERATION + 1))
if [ "$ITERATION" -ge "$MAX_ITERATIONS" ]; then
  echo "Max iterations reached. Requesting human intervention."
  gh pr comment "$PR_NUMBER" --body "Max review iterations ($MAX_ITERATIONS) reached. Requesting human guidance."
  # STOP — do not continue
fi
```

**GATE 5**: Argus has posted incremental review result. Action determined.

## Phase 6: Merge

**MANDATORY pre-merge checks** (all must pass):

```bash
# 1. CI passes
gh pr checks "$PR_NUMBER"

# 2. reviewDecision == APPROVED
DECISION=$(gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision')
[ "$DECISION" = "APPROVED" ] || echo "BLOCKED: decision=$DECISION"

# 3. Zero unresolved threads
MSYS_NO_PATHCONV=1 gh api graphql -f query="
query {
  repository(owner: \"${OWNER}\", name: \"${REPO_NAME}\") {
    pullRequest(number: ${PR_NUMBER}) {
      reviewThreads(first: 100) {
        nodes { isResolved }
      }
    }
  }
}" --jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved==false)] | length'
```

**GATE 6**: All checks pass. Then merge:

```bash
gh pr merge "$PR_NUMBER" --squash --delete-branch
```

## Phase 7: Cleanup

```bash
rm -f .pr-flow-iteration-* .pr-flow-check-count-* .pr-flow-multi.txt

BRANCH=$(gh pr view "$PR_NUMBER" --json headRefName --jq '.headRefName')
if [ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ]; then
  git switch develop 2>/dev/null || git checkout develop
fi
git branch -d "$BRANCH" 2>/dev/null || true
```

Update related issues and Mercury task state if applicable.

## Output

After each phase, report status:

```text
PR: #<number> (<url>)
Review: approved | changes_requested | pending
Threads: <total> total, <resolved> resolved, <open> open
Iteration: <N>/<MAX>
Merge: merged | waiting | blocked (<reason>)
```
