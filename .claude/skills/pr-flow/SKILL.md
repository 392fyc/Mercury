---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, poll for review bot, read and triage ALL threads, fix issues, reply to every thread, resolve threads, re-review, and merge after approval. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments", "标准PR流程". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`. It replaces the manual C4-C7 steps in the Mercury workflow.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, WebSearch, WebFetch, Agent, TodoWrite, CronCreate, CronDelete
---

# PR Flow — Mandatory Sequential Protocol

Every phase has a **GATE** that MUST pass before proceeding to the next phase. Do NOT skip gates. Do NOT combine phases. Execute them in strict order.

## Variables

Set these at the start and reference throughout:

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

**GATE 1**: Confirm PR was created. Extract and store `PR_NUMBER` and `PR_URL`.

```bash
# Verify
gh pr view "$PR_NUMBER" --json number,url --jq '{number, url}'
```

## Phase 2: Poll for Review Bot

**MANDATORY**: You MUST create a CronCreate job. Do NOT use sleep loops. Do NOT manually poll.

```
CronCreate:
  cron: "*/10 * * * *"
  prompt: |
    Check PR #<PR_NUMBER> review status.
    1. Run: gh pr view <PR_NUMBER> --json reviews,reviewDecision
    2. If reviewDecision is APPROVED → report to user, delete this cron
    3. If new inline comments from review bot exist → report to user for Phase 3
    4. Track no-activity count in .pr-flow-check-count-<PR_NUMBER>
    5. After 3 quiet checks, post /review (max 3 total triggers per cron_safety rule)
  recurring: true
```

**GATE 2**: Cron job ID is stored. Reviews have arrived (bot has posted comments or review body).

When the cron reports reviews arrived:
1. `CronDelete` the polling job
2. Clean up: `rm -f .pr-flow-check-count-*`
3. Proceed to Phase 3

## Phase 3: Read + Triage ALL Review Threads

**MANDATORY**: Enumerate every thread before fixing anything. Do NOT start fixing while still reading.

### Step 3a: Fetch all inline comments

```bash
MSYS_NO_PATHCONV=1 gh api "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/comments" \
  --jq '.[] | select(.user.login == "argus-review[bot]" or .user.login == "coderabbitai[bot]" or .user.login == "coderabbitai") | {id, path, line, body}'
```

### Step 3b: Fetch review body (outside-diff comments)

```bash
MSYS_NO_PATHCONV=1 gh api "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/reviews" \
  --jq '.[] | select(.user.login == "argus-review[bot]" or .user.login == "coderabbitai[bot]" or .user.login == "coderabbitai") | {id, state, body}'
```

### Step 3c: Parse Argus comment format

Each Argus inline comment has this structure:

```
_<SEVERITY_EMOJI> <Severity>_ | _<Category>_ [| importance: N/10]

**<Description in Chinese>**

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
Current code: ...
Suggested replacement: ...
```
</details>
```

**Severity levels** (process in this order):
- `🔴 Critical` / `importance: 9-10` → MUST fix, blocks merge
- `🟡 Medium` / `importance: 7-8` → SHOULD fix unless strong reason to disagree
- `🔵 Minor` / `importance: 1-6` → fix if trivial, explain if opinionated

### Step 3d: Build triage list

Create a numbered list of ALL threads with: `[id, path, line, severity, category, action: fix|disagree|acknowledge]`

**GATE 3**: All threads are enumerated. Total count matches what the review bot reported. No thread is missing from the triage list.

## Phase 4: Fix Code + Reply to EVERY Thread

**MANDATORY**: Process every thread from the triage list. For each thread:

### Step 4a: Read the code

Use the `🤖 Prompt for AI Agents` block or `path:line` to locate the exact code. Read the file at that location.

### Step 4b: Decide action

| Severity | Committable suggestion? | Action |
|----------|------------------------|--------|
| 🔴 Critical | Yes | Apply the suggestion |
| 🔴 Critical | No | Read context, write fix |
| 🟡 Medium | Yes | Apply unless incorrect |
| 🟡 Medium | No | Fix or explain with reasoning |
| 🔵 Minor | Any | Fix if trivial (<5 lines), explain if opinionated |

### Step 4c: Apply fix (if action=fix)

1. `Read` the file at the specified path
2. `Edit` to apply the fix
3. Note the change for the commit message

### Step 4d: Reply to the thread

**MANDATORY**: Every thread MUST get a reply. Use the PR-number-scoped endpoint:

```bash
MSYS_NO_PATHCONV=1 gh api -X POST \
  "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/comments/<COMMENT_ID>/replies" \
  -f body="<reply>"
```

Reply format by action:
- **Fixed**: `Fixed in <SHA> — <what changed>`
- **Disagree**: `By design. <reasoning>`
- **Acknowledged**: `Acknowledged. <brief explanation>`

### Step 4e: Verify all threads have replies

```bash
# Count threads without our reply
MSYS_NO_PATHCONV=1 gh api "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/comments" \
  --jq '[.[] | select(.user.login == "argus-review[bot]") | .id] | length'
# vs replies
MSYS_NO_PATHCONV=1 gh api "repos/${OWNER}/${REPO_NAME}/pulls/${PR_NUMBER}/comments" \
  --jq '[.[] | select(.in_reply_to_id != null and .user.login != "argus-review[bot]")] | length'
```

**GATE 4**: Every bot thread has at least one reply. No unreplied threads remain.

## Phase 5: Commit + Push + Resolve ALL Threads

### Step 5a: Commit and push

```bash
git add <changed-files>
git commit -m "fix: address review feedback — <summary> (#issue)"
git push
```

### Step 5b: Resolve ALL threads via GraphQL

**MANDATORY**: Resolve threads ONLY after push. Use paginated query:

```bash
CURSOR=""
ALL_THREADS="[]"
while true; do
  AFTER_ARG=""
  [ -n "$CURSOR" ] && AFTER_ARG=", after: \"$CURSOR\""
  RESULT=$(MSYS_NO_PATHCONV=1 gh api graphql -f query="
  query {
    repository(owner: \"${OWNER}\", name: \"${REPO_NAME}\") {
      pullRequest(number: ${PR_NUMBER}) {
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

UNRESOLVED=$(echo "$ALL_THREADS" | jq '[.[] | select(.isResolved==false)]')
for THREAD_ID in $(echo "$UNRESOLVED" | jq -r '.[].id'); do
  MSYS_NO_PATHCONV=1 gh api graphql -f query="mutation { resolveReviewThread(input: {threadId: \"$THREAD_ID\"}) { thread { id isResolved } } }"
done
```

### Step 5c: Verify 0 unresolved

**MANDATORY**: Re-query after resolving (the previous `ALL_THREADS` is stale):

```bash
# Re-run the same paginated query, then:
UNRESOLVED_COUNT=$(echo "$ALL_THREADS" | jq '[.[] | select(.isResolved==false)] | length')
echo "Unresolved threads: $UNRESOLVED_COUNT"
```

**GATE 5**: `UNRESOLVED_COUNT == 0`. If not zero, go back to Step 5b. Do NOT proceed to Phase 6 with unresolved threads.

## Phase 6: Request Re-Review

### Step 6a: Increment iteration counter

```bash
ITERATION=$((ITERATION + 1))
if [ "$ITERATION" -ge "$MAX_ITERATIONS" ]; then
  echo "Max iterations reached. Requesting human intervention."
  gh pr comment "$PR_NUMBER" --body "Max review iterations ($MAX_ITERATIONS) reached. Requesting human guidance."
  # STOP — do not continue
fi
```

### Step 6b: Request re-review

```bash
MSYS_NO_PATHCONV=1 gh pr comment "$PR_NUMBER" --body "/review"
```

### Step 6c: Create polling cron (MANDATORY)

Same as Phase 2 — you MUST create a new CronCreate job. Do NOT manually poll.

**GATE 6**: Cron job created. When re-review arrives, return to Phase 3.

## Phase 7: Merge

**MANDATORY pre-merge checks** (all must pass):

```bash
# 1. CI passes
gh pr checks "$PR_NUMBER"

# 2. reviewDecision == APPROVED
DECISION=$(gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision')
[ "$DECISION" = "APPROVED" ] || echo "BLOCKED: decision=$DECISION"

# 3. Zero unresolved threads (re-query fresh)
# (use the same paginated query from Phase 5)
```

**GATE 7**: All three checks pass. Then merge:

```bash
gh pr merge "$PR_NUMBER" --squash --delete-branch
```

## Phase 8: Cleanup

After merge:

```bash
# Clean up state files
rm -f .pr-flow-iteration-* .pr-flow-check-count-* .pr-flow-multi.txt

# Clean up worktree + branch
BRANCH=$(gh pr view "$PR_NUMBER" --json headRefName --jq '.headRefName')
if [ "$(git rev-parse --abbrev-ref HEAD)" = "$BRANCH" ]; then
  git switch develop 2>/dev/null || git checkout develop
fi
git branch -d "$BRANCH" 2>/dev/null || true

# Delete cron if still active
# CronDelete <job_id>
```

Update related issues and Mercury task state if applicable.

## Review Response Protocol

| Severity | Has suggestion? | Action |
|----------|----------------|--------|
| 🔴 Critical (9-10) | Yes | Apply suggestion, reply with SHA |
| 🔴 Critical (9-10) | No | Write fix, reply with SHA |
| 🟡 Medium (7-8) | Yes | Apply unless incorrect, reply |
| 🟡 Medium (7-8) | No | Fix or explain with reasoning |
| 🔵 Minor (1-6) | Any | Fix if trivial, explain if opinionated |
| Disagree | — | Reply with reasoning, resolve thread |
| Out of scope | — | Acknowledge, explain scope, resolve |

**Rules**:
- Every thread MUST get a reply AND be resolved. No exceptions.
- Threads you disagree with still MUST be replied to and resolved.
- The review bot will not approve while unresolved threads remain.
- Use the `🤖 Prompt for AI Agents` block for precise code location.
- Use `📝 Committable suggestion` when available — it's pre-validated by the bot.

## Output

After each phase, report status:

```text
PR: #<number> (<url>)
Review: approved | changes_requested | pending
Threads: <total> total, <fixed> fixed, <disagreed> disagreed, <unresolved> unresolved
Iteration: <N>/<MAX>
Merge: merged | waiting | blocked (<reason>)
```
