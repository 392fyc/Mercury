---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, wait for CI checks, read CodeRabbit and reviewer comments, dispatch fixes if needed, and merge. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`. It replaces the manual C4-C7 steps in the Mercury workflow.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob
---

# PR Flow

## Prerequisites

- `gh` CLI v2.x+ (authenticated)
- `git` with push access to the PR branch
- `jq` for JSON parsing
- Current branch must not be `develop` or `main`

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
  PR_URL=$(gh pr create --base develop --title "$TASK_ID: $SUMMARY" --body "$PR_BODY")
  PR_NUMBER=$(gh pr view "$PR_URL" --json number --jq '.number')
fi
case "$TASK_ID" in BUG-*|fix/*) LABEL=bugfix ;; DOC-*|docs/*) LABEL=documentation ;; *) LABEL=refactor ;; esac
gh pr edit "$PR_NUMBER" --add-assignee "@me" --add-label "$LABEL"
gh pr comment "$PR_NUMBER" --body "@coderabbitai review"
```

### Step 2: Poll CI Checks

```bash
extract_changed_paths() { jq -r '.[]?.path // empty'; }
is_within_scope() { local p="$1"; shift; for root in "$@"; do [[ "$p" == "$root"* ]] && return 0; done; return 1; }
classify_failure() { case "$1" in *eslint*|*lint*|*prettier*) echo lint ;; *tsc*|*typecheck*) echo type ;; *test*|*jest*|*vitest*) echo test ;; *) echo build ;; esac; }
gh pr checks "$PR_NUMBER" --watch --fail-fast
```

### Step 3: Wait for CodeRabbit Review

```bash
MAX_POLLS=15
for i in $(seq 1 "$MAX_POLLS"); do
  REVIEW_DECISION=$(gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision // "REVIEW_REQUIRED"') || { sleep $((i * 10)); continue; }
  [ "$REVIEW_DECISION" = "APPROVED" ] && break
  [ "$REVIEW_DECISION" = "CHANGES_REQUESTED" ] && break
  sleep 60
done
```

### Step 4: Parse and Classify CodeRabbit Feedback

```bash
TYPE_REGEX='Potential issue|Nitpick|Verification successful'
SEVERITY_REGEX='Critical|Major|Minor|Trivial'
COMMENTS=$(gh api repos/{owner}/{repo}/pulls/"$PR_NUMBER"/comments)
ACTIONABLE=$(echo "$COMMENTS" | jq -cr '
  .[]
  | .firstLine = (.body | split("\n")[0])
  | .severity = (if (.firstLine | test("Critical")) then "Critical"
                 elif (.firstLine | test("Major")) then "Major"
                 elif (.firstLine | test("Minor")) then "Minor"
                 elif (.firstLine | test("Trivial")) then "Trivial"
                 else "Info" end)
  | select(.severity == "Critical" or .severity == "Major")
  | {id, path, severity, body}
')
```

### Step 5: Apply Fixes, Validate, and Re-Review

```bash
normalize_patch() {
  local path="$1" body="$2" diff suggestion
  diff=$(printf '%s\n' "$body" | sed -n '/```diff/,/```/p' | sed '1d;$d')
  [ -n "$diff" ] && { printf '%s\n' "$diff"; return 0; }
  suggestion=$(printf '%s\n' "$body" | sed -n '/```suggestion/,/```/p' | sed '1d;$d')
  [ -n "$suggestion" ] || return 1
  build_patch_from_suggestion "$path" "$suggestion" # pseudo: wrap suggestion as unified diff.
}
extract_patch_paths() { printf '%s\n' "$1" | sed -n 's/^+++ b\///p'; }
validate_scope() { is_within_scope "$1" "${ALLOWED_SCOPE[@]}"; }
MAX_ITERATIONS=3 # CodeRabbit usually converges in 1-2 rounds; keep 1 buffer.
ITERATION=1
while [ "$ITERATION" -le "$MAX_ITERATIONS" ] && [ -n "$ACTIONABLE" ]; do
  echo "$ACTIONABLE" | while read -r item; do
    COMMENT_ID=$(echo "$item" | jq -r '.id'); PATHNAME=$(echo "$item" | jq -r '.path')
    COMMENT_BODY=$(gh api repos/{owner}/{repo}/pulls/comments/"$COMMENT_ID" --jq '.body')
    PATCH_BODY=$(normalize_patch "$PATHNAME" "$COMMENT_BODY") || { echo "skip: no diff/suggestion for $COMMENT_ID"; continue; }
    PATCH_PATHS=$(extract_patch_paths "$PATCH_BODY"); [ -n "$PATCH_PATHS" ] || PATCH_PATHS="$PATHNAME"
    SCOPE_OK=1; while read -r TARGET_PATH; do [ -n "$TARGET_PATH" ] && ! validate_scope "$TARGET_PATH" && SCOPE_OK=0; done <<EOF
$PATCH_PATHS
EOF
    [ "$SCOPE_OK" -eq 1 ] || { echo "skip: out of scope patch $COMMENT_ID"; continue; }
    printf '%s\n' "$PATCH_BODY" | git apply --check || { git restore .; continue; }
    printf '%s\n' "$PATCH_BODY" | git apply
    npx tsc --noEmit || { git restore .; continue; }
    while read -r TARGET_PATH; do [ -n "$TARGET_PATH" ] && git add "$TARGET_PATH"; done <<EOF
$PATCH_PATHS
EOF
    git commit -m "fix(PR-feedback): address comment $COMMENT_ID"
  done
  git push
  COMMENTS=$(gh api repos/{owner}/{repo}/pulls/"$PR_NUMBER"/comments)
  ACTIONABLE=$(echo "$COMMENTS" | jq -cr '.[] | .firstLine = (.body | split("\n")[0]) | .severity = (if (.firstLine | test("Critical")) then "Critical" elif (.firstLine | test("Major")) then "Major" else "Other" end) | select(.severity == "Critical" or .severity == "Major") | {id, path, severity, body}')
  ITERATION=$((ITERATION + 1))
done
```

### Step 6: Merge and Update Mercury State

```bash
check_ci_status() { gh pr checks "$PR_NUMBER" --fail-fast >/dev/null; }
check_review_state() { [ "$(gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision // "REVIEW_REQUIRED"')" = "APPROVED" ]; }
check_unresolved_critical_comments() { [ -z "$ACTIONABLE" ] && return 0; COUNT=$(printf '%s\n' "$ACTIONABLE" | jq -s 'length' 2>/dev/null || echo 0); [ "${COUNT:-0}" -eq 0 ]; }
check_ready_to_merge() { check_ci_status && check_review_state && check_unresolved_critical_comments; }
MERGE_STRATEGY=${MERGE_STRATEGY:-squash}
DELETE_BRANCH=${DELETE_BRANCH:-true}
[ "$DELETE_BRANCH" = "true" ] && DELETE_FLAG="--delete-branch" || DELETE_FLAG=""
check_ready_to_merge || { echo "blocked: merge gates failed"; exit 1; }
if [ "${CONFIRM_MERGE:-}" != "yes" ]; then
  read -r -p "Merge PR #$PR_NUMBER and transition $TASK_ID? [y/N] " CONFIRM
  [ "$CONFIRM" = "y" ] || [ "$CONFIRM" = "Y" ] || { echo "merge cancelled"; exit 1; }
fi
gh pr merge "$PR_NUMBER" "--$MERGE_STRATEGY" $DELETE_FLAG
curl -s http://localhost:${MERCURY_RPC_PORT:-7654}/rpc -X POST -H "Content-Type: application/json" -d "{\"method\":\"transition_task\",\"params\":{\"taskId\":\"$TASK_ID\",\"to\":\"done\"}}"
```

## Output

```text
PR: #<number> (<url>)
CI Checks: PASS | FAIL | FAIL (<job>: <error>)
CodeRabbit: approved | pending | timeout | unavailable
Feedback: <N> critical, <N> major, <N> addressed, iteration=<N>
Merge: merged | waiting | blocked | failed - <reason>
Mercury State: <taskId> -> done | pending manual transition
Evidence: pr-flow: PR #<number> status=<merged|blocked> strategy=<mergeStrategy> ci=<pass|fail> coderabbit=<approved|timeout> iterations=<N> approvers=<login,...> mergeCommit=<sha|pending> task=<taskId>
```
