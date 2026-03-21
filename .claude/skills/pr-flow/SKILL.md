---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, wait for CI checks, read CodeRabbit and reviewer comments, dispatch fixes if needed, and merge. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`. It replaces the manual C4-C7 steps in the Mercury workflow.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob
---
# PR Flow
Automates the PR lifecycle that starts after `implementation_done` + `main_review`, while keeping Mercury's merge gates explicit.
## Prerequisites
- `gh` CLI v2.x+ (authenticated)
- `git` with push access to the PR branch
- `jq` for JSON parsing
- Current branch must be a feature/refactor branch, not `develop` or `main`
- Examples use bash; Codex/Windows may mirror the same logic in PowerShell
## When
- After dev work reaches `implementation_done`, is committed and pushed, and the task has passed `main_review`.
- When the user explicitly asks to create, inspect, or merge a PR.
- When CodeRabbit or CI feedback on an existing PR needs another fix-review iteration.
## Pipeline
### Step 0: Verify Prerequisites
```bash
set -euo pipefail
gh --version
gh auth status
git remote -v
jq --version
BRANCH=$(git branch --show-current)
test "$BRANCH" != "develop" && test "$BRANCH" != "main"
gh api rate_limit --jq '.resources.core.remaining'
```
Abort early if auth fails, the branch is protected, or rate limit is already low.
### Step 1: Create or Reuse the PR
```bash
BRANCH=$(git branch --show-current)
TASK_ID=${BRANCH##*/}
SUMMARY=$(jq -r '.summary // "PR update"' Mercury_KB/10-tasks/"$TASK_ID".receipt.json 2>/dev/null || echo "PR update")
DOD_COUNT=$(jq -r '(.definitionOfDone // []) | length' Mercury_KB/10-tasks/"$TASK_ID".json 2>/dev/null || echo "unknown")
PR_BODY=$(cat <<EOF
## Summary
$SUMMARY

## Task
- TaskId: $TASK_ID
- Branch: $BRANCH
- DoD items completed: $DOD_COUNT
EOF
)
EXISTING_PR=$(gh pr list --head "$BRANCH" --json number --jq '.[0].number // empty')
if [ -n "$EXISTING_PR" ]; then
  PR_NUMBER=$EXISTING_PR
else
  git push -u origin "$BRANCH"
  PR_NUMBER=$(gh pr create --base develop --title "$TASK_ID: $SUMMARY" --body "$PR_BODY" --json number --jq '.number')
fi
case "$TASK_ID" in
  BUG-*|fix/*) LABEL=bugfix ;;
  DOC-*|docs/*) LABEL=documentation ;;
  *) LABEL=refactor ;;
esac
gh pr edit "$PR_NUMBER" --add-assignee "@me" --add-label "$LABEL"
gh pr comment "$PR_NUMBER" --body "@coderabbitai review"
```
Use the explicit `@coderabbitai review` comment when implicit invocation is disabled or CodeRabbit has auto-paused.
### Step 2: Poll CI Checks
```bash
extract_changed_paths() { jq -r '.[]?.path // empty'; }
is_within_scope() { local p="$1"; shift; for root in "$@"; do [[ "$p" == "$root"* ]] && return 0; done; return 1; }
classify_failure() {
  case "$1" in
    *eslint*|*lint*|*prettier*) echo lint ;;
    *tsc*|*typecheck*) echo type ;;
    *test*|*jest*|*vitest*) echo test ;;
    *) echo build ;;
  esac
}
gh pr checks "$PR_NUMBER" --watch --fail-fast
```
If checks fail, extract the affected paths, call `is_within_scope(path, allowedWriteScope.codePaths...)`, auto-fix only in-scope `lint` or `type` failures, and report out-of-scope `test` or `build` failures to the user.
### Step 3: Wait for CodeRabbit Review
```bash
MAX_POLLS=15
for i in $(seq 1 "$MAX_POLLS"); do
  REVIEWS_JSON=$(gh pr view "$PR_NUMBER" --json reviews) || { sleep $((i * 10)); continue; }
  HAS_APPROVED=$(echo "$REVIEWS_JSON" | jq '[.reviews[]? | select(.state == "APPROVED")] | length')
  HAS_CHANGES=$(echo "$REVIEWS_JSON" | jq '[.reviews[]? | select(.state == "CHANGES_REQUESTED")] | length')
  [ "$HAS_APPROVED" -gt 0 ] && [ "$HAS_CHANGES" -eq 0 ] && break
  sleep 60
done
```
Timeout is 15 minutes total with 60-second polls and backoff only on transient `gh` failures. Completion requires at least one `APPROVED` review and zero `CHANGES_REQUESTED`; otherwise escalate instead of merging.
### Step 4: Parse and Classify CodeRabbit Feedback
```bash
TYPE_REGEX='Potential issue|Nitpick|Verification successful'
SEVERITY_REGEX='Critical|Major|Minor|Trivial'
COMMENTS=$(gh api repos/{owner}/{repo}/pulls/"$PR_NUMBER"/comments)
ACTIONABLE=$(echo "$COMMENTS" | jq -cr '
  .[]
  | .firstLine = (.body | split("\n")[0])
  | .kind = (if (.firstLine | test("Potential issue")) then "issue"
             elif (.firstLine | test("Nitpick")) then "nitpick"
             else "info" end)
  | .severity = (if (.firstLine | test("Critical")) then "Critical"
                 elif (.firstLine | test("Major")) then "Major"
                 elif (.firstLine | test("Minor")) then "Minor"
                 elif (.firstLine | test("Trivial")) then "Trivial"
                 else "Info" end)
  | select(.severity == "Critical" or .severity == "Major")
  | {id, path, severity, body}
')
```
The first-line parser is intentionally explicit: `/(Potential issue|Nitpick|Verification successful).*?(Critical|Major|Minor|Trivial)?/`. Treat out-of-scope paths or comments without ` ```suggestion` / ` ```diff` content as `skip`, not as silent success.
### Step 5: Apply Fixes, Validate, and Re-Review
```bash
validate_scope() { is_within_scope "$1" "${ALLOWED_SCOPE[@]}"; }
MAX_ITERATIONS=3 # CodeRabbit usually converges in 1-2 rounds; keep 1 buffer to avoid infinite loops.
ITERATION=1
while [ "$ITERATION" -le "$MAX_ITERATIONS" ] && [ -n "$ACTIONABLE" ]; do
  echo "$ACTIONABLE" | while read -r item; do
    COMMENT_ID=$(echo "$item" | jq -r '.id')
    PATHNAME=$(echo "$item" | jq -r '.path')
    DIFF=$(gh api repos/{owner}/{repo}/pulls/comments/"$COMMENT_ID" --jq '.body' | sed -n '/```diff/,/```/p' | sed '1d;$d')
    validate_scope "$PATHNAME" || { echo "skip: out of scope $PATHNAME"; continue; }
    [ -n "$DIFF" ] || { echo "skip: no concrete diff for $COMMENT_ID"; continue; }
    printf '%s\n' "$DIFF" | git apply --check || { git restore .; continue; }
    printf '%s\n' "$DIFF" | git apply
    validate_scope "$PATHNAME" && npx tsc --noEmit || { git restore .; continue; }
    git add "$PATHNAME"
    git commit -m "fix(PR-feedback): address comment $COMMENT_ID"
  done
  git push
  COMMENTS=$(gh api repos/{owner}/{repo}/pulls/"$PR_NUMBER"/comments)
  ACTIONABLE=$(echo "$COMMENTS" | jq -cr '.[] | .firstLine = (.body | split("\n")[0]) | .severity = (if (.firstLine | test("Critical")) then "Critical" elif (.firstLine | test("Major")) then "Major" else "Other" end) | select(.severity == "Critical" or .severity == "Major") | {id, path, severity, body}')
  ITERATION=$((ITERATION + 1))
done
```
On any apply, scope, or type-check failure, restore the working tree, skip that comment, and keep the failure visible in the final report.
### Step 6: Merge and Update Mercury State
```bash
check_ci_status() { gh pr checks "$PR_NUMBER" --fail-fast >/dev/null; }
check_review_state() {
  gh pr view "$PR_NUMBER" --json reviews \
    | jq -e '[.reviews[]? | select(.state == "APPROVED")] | length > 0 and ([.reviews[]? | select(.state == "CHANGES_REQUESTED")] | length == 0)' >/dev/null
}
check_unresolved_critical_comments() {
  [ "$(echo "$ACTIONABLE" | jq -s 'length')" -eq 0 ]
}
check_ready_to_merge() { check_ci_status && check_review_state && check_unresolved_critical_comments; }
MERGE_STRATEGY=${MERGE_STRATEGY:-squash}
DELETE_BRANCH=${DELETE_BRANCH:-true}
DELETE_FLAG=""
[ "$DELETE_BRANCH" = "true" ] && DELETE_FLAG="--delete-branch"
check_ready_to_merge
gh pr merge "$PR_NUMBER" "--$MERGE_STRATEGY" $DELETE_FLAG
curl -s http://localhost:${MERCURY_RPC_PORT:-7654}/rpc -X POST -H "Content-Type: application/json" -d '{"method":"transition_task","params":{"taskId":"<taskId>","to":"done"}}'
```
Ask the user before the final merge. If any gate fails, exit non-zero and report the blocking condition instead of proceeding.
## Output
```text
## PR Flow Results
PR: #<number> (<url>)
CI Checks: PASS | FAIL | FAIL (<job>: <error>)
CodeRabbit: approved | pending | timeout | unavailable
Feedback: <N> critical, <N> major, <N> addressed, iteration=<N>
Merge: merged | waiting | blocked | failed - <reason>
Mercury State: <taskId> -> done | pending manual transition
```
## Evidence
```text
pr-flow: PR #<number> status=<merged|blocked> strategy=<mergeStrategy> ci=<pass|fail> coderabbit=<approved|timeout> iterations=<N> approvers=<login,...> mergeCommit=<sha|pending> task=<taskId>
```
Persist the evidence string into `TaskBundle.evidence` or the equivalent PR-resolution log entry after the workflow finishes.
## Safety Rules
- Never merge without an explicit gate check.
- Never force-push to the PR branch.
- Never modify files outside `allowedWriteScope`.
- Never bypass a timed-out or unavailable CodeRabbit review without user approval.
