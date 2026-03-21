---
name: pr-flow
description: |
  Automate the full PR lifecycle: create PR, wait for CI checks, read CodeRabbit and reviewer comments, dispatch fixes if needed, and merge. Use this skill when the user says "PR", "pull request", "create PR", "merge PR", "提PR", "合并", "PR流程", "开PR", "check PR status", "review comments". Use this skill after dev work reaches `implementation_done`, the branch is pushed, and the task has passed `main_review`.
---

# PR Flow

## Prerequisites

- `gh` CLI v2.x+ (authenticated)
- `git` with push access to the PR branch
- `jq` for JSON parsing
- PowerShell available for the Mercury RPC example
- TaskBundle JSON must include `allowedWriteScope.codePaths`

> **Platform note**: GitHub and CodeRabbit examples use bash syntax so the `.agents` and `.claude` variants stay aligned. The final RPC step also shows a PowerShell/Codex alternative for Windows.

## Pipeline

1. **Step 0: Verify prerequisites**

```bash
set -euo pipefail
gh --version
gh auth status
git remote -v
jq --version
BRANCH=$(git branch --show-current)
test "$BRANCH" != "develop" && test "$BRANCH" != "main"
# Extract TASK_ID: try branch suffix first, then scan branch name for TASK-XXX pattern
TASK_ID=${BRANCH##*/}
if ! [[ "$TASK_ID" =~ ^TASK- ]]; then
  TASK_ID=$(echo "$BRANCH" | grep -oE 'TASK-[A-Z]+-[0-9]+' | head -1)
fi
TASK_FILE=""
if [ -n "$TASK_ID" ] && [ -f "Mercury_KB/10-tasks/$TASK_ID.json" ]; then
  TASK_FILE="Mercury_KB/10-tasks/$TASK_ID.json"
fi
declare -a ALLOWED_SCOPE=()
if [ -n "$TASK_FILE" ]; then
  while IFS= read -r path; do [ -n "$path" ] && ALLOWED_SCOPE+=("$path"); done < <(jq -r '.allowedWriteScope.codePaths[]? // empty' "$TASK_FILE")
fi
gh api rate_limit --jq '.resources.core.remaining'
```

1. **Create or reuse the PR**

```bash
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
case "$TASK_ID" in TASK-BUG-*|TASK-FIX-*) LABEL=bugfix ;; TASK-DOC-*) LABEL=documentation ;; TASK-GUI-*) LABEL=enhancement ;; *) LABEL=refactor ;; esac
gh pr edit "$PR_NUMBER" --add-assignee "@me" --add-label "$LABEL"
if ! gh api repos/{owner}/{repo}/issues/"$PR_NUMBER"/comments --jq '.[].body' | grep -Fq "@coderabbitai review"; then gh pr comment "$PR_NUMBER" --body "@coderabbitai review"; fi
```

1. **Poll CI Checks**

```bash
extract_changed_paths() { jq -r '.[]?.path // empty'; }
is_within_scope() { local p="$1"; shift; for root in "$@"; do case "$p" in "$root"/*|"$root") return 0;; esac; done; return 1; }
if ! gh pr checks "$PR_NUMBER" --watch; then echo "CI failed — inspect output before continuing"; exit 1; fi
```

1. **Wait for CodeRabbit review**

```bash
MAX_POLLS=15
for i in $(seq 1 "$MAX_POLLS"); do
  REVIEW_DECISION=$(gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision // "REVIEW_REQUIRED"') || { sleep $((i * 10)); continue; }
  [ "$REVIEW_DECISION" = "APPROVED" ] && break
  [ "$REVIEW_DECISION" = "CHANGES_REQUESTED" ] && break
  sleep 60
done
```

1. **Parse and classify CodeRabbit feedback**

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

1. **Apply fixes, validate, and re-review**

```bash
normalize_patch() {
  local path="$1" body="$2" diff suggestion
  diff=$(printf '%s\n' "$body" | sed -n '/```diff/,/```/p' | sed '1d;$d')
  [ -n "$diff" ] && { printf '%s\n' "$diff"; return 0; }
  suggestion=$(printf '%s\n' "$body" | sed -n '/```suggestion/,/```/p' | sed '1d;$d')
  [ -n "$suggestion" ] || return 1
  printf 'skip: suggestion-only comment for %s (no patch builder implemented)\n' "$path" >&2; return 1
}
extract_patch_paths() { printf '%s\n' "$1" | sed -n 's/^+++ b\///p'; }
validate_scope() { is_within_scope "$1" "${ALLOWED_SCOPE[@]}"; }
refresh_actionable() { COMMENTS=$(gh api repos/{owner}/{repo}/pulls/"$PR_NUMBER"/comments); ACTIONABLE=$(echo "$COMMENTS" | jq -cr '.[] | .firstLine = (.body | split("\n")[0]) | .severity = (if (.firstLine | test("Critical")) then "Critical" elif (.firstLine | test("Major")) then "Major" else "Other" end) | select(.severity == "Critical" or .severity == "Major") | {id, path, severity, body}'); }
wait_for_refresh() { gh pr checks "$PR_NUMBER" --watch || true; for i in $(seq 1 10); do refresh_actionable; [ -n "$ACTIONABLE" ] || break; sleep 30; done; }
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
    printf '%s\n' "$PATCH_BODY" | git apply --check || { git checkout -- $PATCH_PATHS 2>/dev/null; continue; }
    printf '%s\n' "$PATCH_BODY" | git apply
    npx tsc --noEmit || { git checkout -- $PATCH_PATHS 2>/dev/null; continue; }
    while read -r TARGET_PATH; do [ -n "$TARGET_PATH" ] && git add "$TARGET_PATH"; done <<EOF
$PATCH_PATHS
EOF
    git diff --cached --quiet && { echo "skip: no staged changes for $COMMENT_ID"; continue; }
    git commit -m "fix(PR-feedback): address comment $COMMENT_ID"
  done
  git push
  wait_for_refresh
  ITERATION=$((ITERATION + 1))
done
```

1. **Merge and update Mercury state**

```bash
check_ci_status() { gh pr checks "$PR_NUMBER" >/dev/null; }
check_review_state() { [ "$(gh pr view "$PR_NUMBER" --json reviewDecision --jq '.reviewDecision // "REVIEW_REQUIRED"')" = "APPROVED" ]; }
# NOTE: REST API pull request comments do not expose "resolved" state.
# GraphQL reviewThreads.isResolved is the accurate source but requires more complexity.
# Current approach: re-count severity from latest comment bodies as a practical approximation.
check_unresolved_critical_comments() { LIVE_COMMENTS=$(gh api repos/{owner}/{repo}/pulls/"$PR_NUMBER"/comments); LIVE_ACTIONABLE=$(echo "$LIVE_COMMENTS" | jq -cr '.[] | .firstLine = (.body | split("\n")[0]) | .severity = (if (.firstLine | test("Critical")) then "Critical" elif (.firstLine | test("Major")) then "Major" else "Other" end) | select(.severity == "Critical" or .severity == "Major") | {id, path, severity, body}'); [ -z "$LIVE_ACTIONABLE" ] && return 0; COUNT=$(printf '%s\n' "$LIVE_ACTIONABLE" | jq -s 'length' 2>/dev/null || echo 0); [ "${COUNT:-0}" -eq 0 ]; }
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
```

```powershell
$taskId = if ($env:TASK_ID) { $env:TASK_ID } else { throw "Set TASK_ID before calling Mercury RPC." }
$port = if ($env:MERCURY_RPC_PORT) { $env:MERCURY_RPC_PORT } else { "7654" }
$body = @{ method = "transition_task"; params = @{ taskId = $taskId; to = "done" } } | ConvertTo-Json -Depth 3 -Compress
for ($attempt = 1; $attempt -le 3; $attempt++) {
  try {
    Invoke-RestMethod -Uri "http://localhost:$port/rpc" -Method POST -ContentType "application/json" -Body $body -TimeoutSec 5
    break
  } catch {
    if ($attempt -eq 3) { Write-Warning "Mercury RPC unavailable: $($_.Exception.Message). Transition pending manual update." }
    else { Start-Sleep -Seconds (2 * $attempt) }
  }
}
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
