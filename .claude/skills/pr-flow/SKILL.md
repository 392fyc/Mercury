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

```bash
PR_NUMBER=<number>
PR_URL=<url>
# Auto-detect repo + base branch — works in any GitHub repo, not just Mercury.
OWNER=$(gh repo view --json owner --jq '.owner.login')
REPO_NAME=$(gh repo view --json name --jq '.name')
BASE_BRANCH=$(gh repo view --json defaultBranchRef --jq '.defaultBranchRef.name')
# Mercury convention override: if a "develop" branch exists, prefer it over the default branch
# so feature PRs land on develop first and the default branch stays deploy-clean.
if gh api "repos/${OWNER}/${REPO_NAME}/branches/develop" --silent 2>/dev/null; then
  BASE_BRANCH=develop
fi
ITERATION=0
MAX_ITERATIONS=5
```

## Phase 1: Create PR

**MANDATORY**: Push branch, then create PR with metadata.

```bash
BRANCH=$(git branch --show-current)
git push -u origin "$BRANCH"

# Assignee: prefer the authenticated user so this works in any repo, not just 392fyc's.
# Fall back to @me if the API call fails.
ASSIGNEE=$(gh api user --jq '.login' 2>/dev/null || echo "@me")

# Labels: only apply labels that actually exist in the target repo. Unknown labels fail the PR create.
# To add labels, extend this array. Label names MUST match [A-Za-z0-9:_./-]+ — if you need labels
# with spaces or other special characters, URL-encode them via jq before passing to gh api.
LABEL_ARG=""
for L in "enhancement"; do
  # Validate label name shape before passing to the API path (defense-in-depth against injection
  # and silent probe failures when the label contains characters that need URL encoding).
  case "$L" in
    *[!A-Za-z0-9:_./-]*)
      echo "WARNING: label '$L' contains non-simple characters — skipping; use URL-encoded form or rename the label" >&2
      continue
      ;;
  esac
  if MSYS_NO_PATHCONV=1 gh api "repos/${OWNER}/${REPO_NAME}/labels/${L}" --silent 2>/dev/null; then
    LABEL_ARG="${LABEL_ARG}${LABEL_ARG:+,}${L}"
  fi
done

# Build gh pr create argv explicitly. The ${X:+word} conditional expansion does NOT work here
# because the quotes inside "word" become literal characters after expansion, so gh would receive
# '--label "enhancement"' as a single token. Use an array for safe argument splitting.
PR_ARGS=(
  --base "$BASE_BRANCH"
  --title "<type>(<scope>): description (#issue)"
  --body "$(cat <<'BODY'
## Summary
- bullet points

## Test plan
- [ ] test items

Generated with Claude Code
BODY
)"
  --assignee "$ASSIGNEE"
)
if [ -n "$LABEL_ARG" ]; then
  PR_ARGS+=(--label "$LABEL_ARG")
fi

gh pr create "${PR_ARGS[@]}"
```

**GATE 1**: PR created. Extract and store `PR_NUMBER` and `PR_URL`.

## Phase 2: Poll for Initial Review

**MANDATORY**: Create a CronCreate job to poll. Do NOT use sleep loops.

> **Review polling field selection (see #190)**
> Use `reviewDecision` as the authoritative approval gate — it reflects GitHub's own per-reviewer-latest aggregation and flips to `APPROVED` only when all requested reviews are satisfied.
> Always include `state` in the JSON query so polling can stop cleanly on MERGED / CLOSED / DRAFT transitions. A cron that ignores `state` will keep polling a closed PR until it hits the quiet-check timeout, producing false alarms.
> Prefer `latestReviews` over `reviews` when you need "the most recent review per reviewer": `reviews` is the full chronological array and its last element can be an older COMMENTED review that predates a newer APPROVED review from the same reviewer. `latestReviews` is deduplicated per-user.
> For "is there any new inline finding" detection, query `gh api repos/<owner>/<repo>/pulls/<N>/comments --paginate` (review comments endpoint). Always pass `--paginate` — the default page size is 30 and large PRs can exceed it, silently hiding new findings. Track a "last processed comment id or timestamp" per PR so each poll is incremental rather than re-scanning.

```
CronCreate:
  cron: "*/10 * * * *"
  prompt: |
    Check PR #<PR_NUMBER> review status.
    1. Run: gh pr view <PR_NUMBER> --json state,isDraft,reviewDecision,latestReviews
    2. If state is MERGED or CLOSED → PR is terminal, report to user, delete this cron
    3. If isDraft is true → PR moved back to DRAFT, stop polling and report to user, delete this cron
    4. If state is OPEN and reviewDecision is APPROVED → report to user, delete this cron
    5. For new-finding detection:
       a. Read last_checked_iso from .pr-flow-last-checked-<PR_NUMBER> (or use the PR creation time on first run).
       b. Capture the current time as new_checked_iso = now() BEFORE making the API call.
       c. Run: gh api "repos/<OWNER>/<REPO>/pulls/<PR_NUMBER>/comments?since=<last_checked_iso>" --paginate \
                --jq '.[] | select(.user.login == "argus-review[bot]")'
       d. Process any returned comments as new findings.
       e. Atomically overwrite .pr-flow-last-checked-<PR_NUMBER> with new_checked_iso (do NOT set it to the last comment's timestamp — use the pre-API wall clock so comments arriving during the API call are not missed on the next poll).
    6. If new inline comments from argus-review[bot] exist → report to user for Phase 3
    7. Track no-activity count in .pr-flow-check-count-<PR_NUMBER>
    8. After 3 quiet checks, post "@argus-review review" (max 3 total triggers per cron_safety rule)
  recurring: true
```

**GATE 2**: Reviews have arrived (Argus has posted inline comments or review body).

When cron reports reviews arrived:
1. `CronDelete` the polling job
2. Clean up: `rm -f .pr-flow-check-count-* .pr-flow-last-checked-*`
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

Argus receives the push event and will automatically run incremental review. Create a CronCreate job to poll for the response:

> **Incremental detection semantics (see #190)**
> `latestReviews` is deduplicated per reviewer, so `latestReviews[argus]` gives Argus's single newest review regardless of how many COMMENTED rounds preceded it. That is the right field to answer "is Argus's current verdict APPROVED?" but it is NOT sufficient to answer "did any new Argus activity happen after the fix commit" — for that, compare `latestReviews[argus].submittedAt` against the fix commit timestamp, or iterate the full `reviews` array filtered by `submittedAt > fix_commit_time`. The cron below uses the timestamp-comparison approach.
>
> **Reviewer login form — GraphQL vs REST (must match the API)**
> The two GitHub surfaces return different login strings for the same bot account, and mixing them silently drops matches:
> - `gh pr view --json latestReviews` (GraphQL) → `.author.login == "argus-review"` (no `[bot]` suffix)
> - `gh api repos/.../pulls/<N>/reviews` and `.../comments` (REST) → `.user.login == "argus-review[bot]"` (with suffix)
> Use the matching form for each API. The same-PR cron uses both forms intentionally because it calls both surfaces.

```
CronCreate:
  cron: "*/10 * * * *"
  prompt: |
    Check PR #<PR_NUMBER> for Argus incremental review after fix push.
    Concrete jq filter pattern to use in step 1 below (substitute FIX_COMMIT_TIME at cron-creation time, not at cron-runtime).
    The `latestArgus` extraction wraps the `select` in `map(...) | .[0] // {state: null, submittedAt: null}` so a missing reviewer yields a known-shape default object instead of an empty stream that would crash downstream `.submittedAt` dereferences.
      gh pr view <PR_NUMBER> --json state,isDraft,reviewDecision,latestReviews \
        --jq '{decision: .reviewDecision, state, isDraft, latestArgus: ((.latestReviews // []) | map(select(.author.login == "argus-review")) | .[0] // {state: null, submittedAt: null})}'
    1. Run the gh pr view query above.
    2. If state is MERGED or CLOSED → PR is terminal, report to user, delete this cron
    3. If isDraft is true → PR moved to DRAFT, stop polling and report to user, delete this cron
    4. If decision is APPROVED AND latestArgus.submittedAt is non-null AND latestArgus.submittedAt > "<FIX_COMMIT_TIME>" (ISO string comparison, both in UTC Zulu) → report APPROVED to user, delete this cron
    5. For new-finding detection, run (note the REST-side `argus-review[bot]` login form with the `[bot]` suffix — this is NOT the same as the GraphQL `argus-review` form used in step 1):
       gh api "repos/<OWNER>/<REPO>/pulls/<PR_NUMBER>/comments?since=<FIX_COMMIT_TIME>" --paginate \
         --jq '.[] | select(.user.login == "argus-review[bot]") | {id, path, line, body}'
       Report any hits as new findings.
    6. If new COMMENT review with findings → report findings to user
    7. After 6 quiet checks (1 hour), report timeout to user for manual intervention
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

# 3. Zero unresolved threads (paginated query)
UNRESOLVED=0
CURSOR=""
while true; do
  AFTER_ARG=""
  [ -n "$CURSOR" ] && AFTER_ARG=", after: \"$CURSOR\""
  RESULT=$(MSYS_NO_PATHCONV=1 gh api graphql -f query="
  query {
    repository(owner: \"${OWNER}\", name: \"${REPO_NAME}\") {
      pullRequest(number: ${PR_NUMBER}) {
        reviewThreads(first: 100${AFTER_ARG}) {
          pageInfo { hasNextPage endCursor }
          nodes { isResolved }
        }
      }
    }
  }")
  COUNT=$(echo "$RESULT" | jq '[.data.repository.pullRequest.reviewThreads.nodes[] | select(.isResolved==false)] | length')
  UNRESOLVED=$((UNRESOLVED + COUNT))
  HAS_NEXT=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage')
  [ "$HAS_NEXT" != "true" ] && break
  CURSOR=$(echo "$RESULT" | jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor')
done
echo "Unresolved threads: $UNRESOLVED"
[ "$UNRESOLVED" -eq 0 ] || echo "BLOCKED: $UNRESOLVED unresolved threads"
```

**GATE 6**: All checks pass. Then merge:

```bash
gh pr merge "$PR_NUMBER" --squash --delete-branch
```

## Phase 7: Cleanup

```bash
rm -f .pr-flow-iteration-* .pr-flow-check-count-* .pr-flow-last-checked-* .pr-flow-multi.txt

BRANCH=$(gh pr view "$PR_NUMBER" --json headRefName --jq '.headRefName')

# Worktree + branch cleanup is delegated to scripts/cleanup-worktree-branch.sh, which also backs
# Phase 5 of the dev-pipeline skill. Keeping one source of truth avoids drift (see Mercury #274).
#
# No --force flag here: pr-flow cleanup runs AFTER merge, so dirty worktrees usually indicate
# unintended state and should be preserved for human review rather than silently removed.
# No --worktree-path either: pr-flow discovers matching worktrees dynamically because the skill
# does not own the creation step. BASE_BRANCH was computed at the top of the skill.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "$REPO_ROOT" ]; then
  echo "WARNING: cannot determine REPO_ROOT — skipping worktree/branch cleanup"
else
  bash "$REPO_ROOT/scripts/cleanup-worktree-branch.sh" "$BRANCH" "$BASE_BRANCH" \
    || echo "NOTE: cleanup-worktree-branch.sh reported incomplete cleanup — see stderr"
fi
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
