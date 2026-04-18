---
name: dev-pipeline
description: |
  Mercury's preset Main → Dev → Acceptance chain for executing a single, well-scoped coding task end-to-end with blind acceptance review. Use this skill when the user says "dev pipeline", "dispatch task", "派发任务", "dev → acceptance", "跑完整开发流程", "dev pipeline 验证", "blind review", "完整开发链", or when a task is ready to be implemented and verified by separate agents (instead of doing it inline). The skill spawns the dev subagent to implement, then spawns the acceptance subagent to blind-review the result, then loops or completes based on the verdict. Independent of Mercury's other modules — works in any repo that has .claude/agents/dev.md + .claude/agents/acceptance.md defined.
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, Agent, WebSearch, WebFetch
---

# Dev Pipeline — Main → Dev → Acceptance Preset Chain

A linear, single-task pipeline. The Main agent (you, the orchestrator running this skill) coordinates two sub-agent invocations and decides loopback vs completion.

> **Why a preset, not dynamic orchestration?** See PHILOSOPHY.md in this directory.

## Prerequisites

Before invoking this skill, the following must be true:

1. **Agents discoverable**: .claude/agents/dev.md and .claude/agents/acceptance.md exist with valid YAML frontmatter (name dev / name acceptance). Verify by running the `claude agents` command or by inspecting the files directly. If they do not exist, this skill cannot run — fall back to inline implementation.
2. **Task is well-scoped**: clear definition of done, bounded write scope, listed acceptance criteria. If the task is ambiguous, run a research/design pass first instead of dispatching dev.
3. **Branch is correct**: you are on a feature branch (not develop or master). Dev agent will commit and push to whatever branch is current.
4. **Issue exists**: every task must have a GitHub Issue (Mercury rule). PR will reference it via Closes #N.

## Iron Rules

| Rule | Why |
|---|---|
| **One task per pipeline run** | Mercury's preset is linear single-task, not parallel multi-task. For parallelism, the user opens multiple sessions. |
| **TaskBundle is inline JSON, not Obsidian** | This skill is dependency-free. The Memory Layer / Obsidian KB integration ships in Phase 3 — until then, the bundle is constructed inline by Main and passed to the dev subagent in the prompt. |
| **Acceptance is blind** | The acceptance subagent MUST NOT receive the dev subagent's reasoning, narrative, self-assessment, or risk evaluation. It receives only the AcceptanceBundle (criteria) plus a blindReceipt containing changed-file paths and test results. |
| **Max 3 dev iterations** | If acceptance returns fail 3 times, escalate to user — do not loop forever. |
| **Main does not write code** | Main coordinates, reviews receipts, decides next action. Implementation belongs to dev. Verification belongs to acceptance. |
| **Sub-agents cannot spawn sub-agents** | Per Claude Code documented constraint. Only the Main thread (running this skill) can dispatch dev or acceptance. Dev cannot call acceptance directly. |


## Phase 1: Build TaskBundle

**MANDATORY** before any dispatch. Main constructs the TaskBundle inline based on the user's request, the GitHub Issue body, and any referenced design docs.

```json
{
  "taskId": "<short-slug>",
  "issue": "<owner/repo#N>",
  "title": "<one-line summary>",
  "context": "<2-5 sentences why this task exists>",
  "definitionOfDone": [
    "<verifiable criterion 1>",
    "<verifiable criterion 2>"
  ],
  "allowedWriteScope": [
    "<file or glob 1>",
    "<file or glob 2>"
  ],
  "mustNotTouch": [
    "<file or glob>"
  ],
  "readScope": [
    "<file paths the dev should read first>"
  ],
  "acceptanceCriteria": [
    "<what acceptance will check 1>",
    "<what acceptance will check 2>"
  ],
  "verifyCommands": [
    "<exact bash command to validate, e.g. pnpm test packages/foo>"
  ],
  "worktreePath": "<absolute path — injected by Main in Phase 2; leave blank here>"
}
```

**Gate**: every field non-empty (except `worktreePath`, which is filled by Main in Phase 2). If definitionOfDone contains a subjective phrase (clean, elegant, good), rewrite it as a measurable criterion or escalate to the user.

## Phase 2: Dispatch Dev

**Before dispatching**, capture the task-start SHA and create the isolated worktree for this task:

```bash
TASK_START_SHA=$(git rev-parse HEAD)
# Use the branch name (sanitized) as a STABLE key so Phase 3 can re-read it
# from a different shell process. $$ does NOT work here: Main/Phase 2 run in
# one Bash shell, Phase 3 runs in a later Bash shell with a different PID.
BRANCH_KEY=$(git rev-parse --abbrev-ref HEAD | tr '/' '_' | tr -cd '[:alnum:]_-')
SHA_FILE="${TMPDIR:-/tmp}/dev-pipeline-task-start-sha-${BRANCH_KEY}"
echo "$TASK_START_SHA" > "$SHA_FILE"
# Phase 6 cleanup: rm -f "$SHA_FILE"

# Create isolated worktree for this task. Main creates the branch; Dev never does.
# TaskBundle.taskId is the short slug from Phase 1 (e.g. "247-worktree-per-task").
TASK_ID="<taskId from TaskBundle>"
TASK_BRANCH="feat/${TASK_ID}"   # or feature/${TASK_ID}-<slug> per git-flow.md convention
REPO_ROOT=$(git rev-parse --show-toplevel)
WORKTREE_PATH="${REPO_ROOT}/.worktrees/${TASK_ID}"
git worktree add "${WORKTREE_PATH}" -b "${TASK_BRANCH}"
# Inject absolute path back into the TaskBundle before dispatch:
#   TaskBundle.worktreePath = "${WORKTREE_PATH}"
# Phase 6 cleanup: git worktree remove --force "${WORKTREE_PATH}" && git branch -d "${TASK_BRANCH}"
```

The SHA file is keyed by the current branch name (slash-sanitized). This is stable across Bash invocations within the same pipeline run, and concurrent pipelines collide only if they are on the same branch — which would be a pre-existing git conflict anyway. Phase 6 hand-off must remove both it and the worktree.

Use the Agent tool with subagent_type set to dev. The prompt template:

```
You are operating under the dev agent role (.claude/agents/dev.md). Implement the following task and return a JSON receipt as your final message.

**Working directory: `<worktreePath>` (isolated git worktree). Use `cd <worktreePath>` before any file operation.**

## TaskBundle
[paste TaskBundle JSON built in Phase 1, with worktreePath field filled in]

## Execution Protocol
1. cd <worktreePath> — all file reads/writes and git commands run from this directory.
2. Read every file listed in readScope.
3. Implement within allowedWriteScope only. Touching anything in mustNotTouch is forbidden.
4. Run every command in verifyCommands. ALL must pass before you commit.
5. Self-fix once if a verifyCommand fails. If it still fails, STOP and report — do NOT commit broken code.
6. Commit with format type(scope): summary (Mercury convention).
7. Push to current branch.
8. Output the JSON receipt below as your FINAL message.

## Receipt template
{
  "taskId": "[copied out of the bundle]",
  "status": "completed|blocked|escalated",
  "changedFiles": ["path", "..."],
  "commitSha": "sha",
  "verifyResults": [
    {"command": "cmd", "exitCode": 0, "summary": "one line"}
  ],
  "evidence": "file:line citations supporting definition-of-done",
  "risks": "known risks or follow-up needed",
  "escalationReason": "only if status is not completed"
}

## Forbidden
- git switch, git checkout, git branch, git reset, git rebase, git merge, git push --force
- git add -A or git add .
- Modifying CLAUDE.md or any file under .claude/agents/
- Creating or modifying git worktrees (Main's responsibility)
- Picking up additional work after the receipt is filed
```

**Gate**: dev must return a JSON receipt with status completed. If blocked or escalated, jump to Phase 5 (escalate to user).


## Phase 3: Receipt Review (Main)

Main checks receipt completeness — NOT correctness (that is acceptance's job).

Checklist:
- [ ] All changedFiles exist in the diff
- [ ] commitSha matches latest commit on the branch
- [ ] All verifyCommands listed in the bundle have a verifyResults entry with exitCode 0
- [ ] evidence cites at least one file:line per definitionOfDone item
- [ ] No file outside allowedWriteScope was touched. Use the **task-start SHA** captured before Phase 2 dispatch as the comparison base (`TASK_START_SHA=$(git rev-parse HEAD)` before dispatch, then `git diff --name-only "$TASK_START_SHA..HEAD"` after). Do NOT use `HEAD~1` — it breaks on first commits, squashed commits, and multi-commit dev runs.

**Gate**: if any check fails, send a correction prompt to dev (still iteration 1) with the specific deficiency. Do not advance to acceptance with an incomplete receipt.

## Phase 4: Dispatch Acceptance (BLIND)

Build the **blindReceipt** by stripping dev's narrative fields. **Preserve original JSON types** — `changedFiles` and `verifyResults` are arrays in the dev receipt and MUST remain arrays here, not stringified placeholders:

```json
{
  "taskId": "task-slug",
  "changedFiles": ["path/to/file1.ts", "path/to/file2.ts"],
  "commitSha": "abc123def",
  "verifyResults": [
    {"command": "pnpm test packages/foo", "exitCode": 0, "summary": "12 passed"},
    {"command": "pnpm lint", "exitCode": 0, "summary": "0 issues"}
  ]
}
```

Note what was REMOVED relative to the dev receipt: `evidence`, `risks`, `escalationReason`. The acceptance agent must form its own conclusions out of code and tests, not out of dev's self-assessment.

Build the **AcceptanceBundle** (also preserve original types — `definitionOfDone`, `acceptanceCriteria`, `verifyCommands` are arrays, not strings):

```json
{
  "taskId": "task-slug",
  "title": "one-line summary",
  "definitionOfDone": ["criterion 1", "criterion 2"],
  "acceptanceCriteria": ["check 1", "check 2"],
  "verifyCommands": ["pnpm test packages/foo", "pnpm lint"]
}
```

Use the Agent tool with subagent_type set to acceptance. Prompt template:

```
You are operating under the acceptance agent role (.claude/agents/acceptance.md). BLIND REVIEW: you are FORBIDDEN from inferring or asking about the dev agent's reasoning, narrative, or self-assessment.

## AcceptanceBundle
[paste AcceptanceBundle JSON]

## Blind Receipt (changed files only — NO dev narrative)
[paste blindReceipt JSON]

## Instructions
1. Read every file listed in changedFiles at the latest commit.
2. Run every command in verifyCommands. Capture exit codes and output.
3. Evaluate each acceptanceCriteria and definitionOfDone item against the actual code and runtime output. Cite file:line evidence.
4. Output your verdict as JSON.

## Verdict template
{
  "verdict": "pass|partial|fail|blocked",
  "criteriaResults": [
    {"criterion": "text", "verdict": "pass|fail|partial", "evidence": "file:line or test output"}
  ],
  "findings": ["problem 1", "problem 2"],
  "recommendations": ["actionable fix 1"]
}
```

**Gate**: capture the verdict.

## Phase 5: Decide Next Action

Based on the acceptance verdict:

| Verdict | Action |
|---|---|
| pass | Pipeline complete. Summarize result for user (Chinese for milestones). Hand off to /pr-flow if a PR is the next step. **Run cleanup (see below).** |
| partial | Re-dispatch dev with the **original full TaskBundle** plus a `priorFindings` array containing acceptance's findings. Constraints (definitionOfDone, allowedWriteScope, mustNotTouch, readScope) MUST be carried over verbatim from iteration 1 — never widened, never dropped. Increment iteration. **Do NOT clean up `$SHA_FILE` between iterations** — Phase 3 needs it on every retry. |
| fail | Same as partial: dispatch with full original TaskBundle + priorFindings + priorRecommendations. Constraints carried verbatim. Increment iteration. **Do NOT clean up between iterations.** |
| blocked | Escalate to user. Acceptance hit an environmental block; user must resolve. **Run cleanup.** |

**Constraint preservation**: every retry dispatch must include the EXACT original `definitionOfDone`, `allowedWriteScope`, `mustNotTouch`, and `readScope` from iteration 1. Adding a new constraint is OK; widening or dropping an existing one is forbidden — that defeats the purpose of the bundle as a contract.

**Iteration cap**: if iteration is at least 3 and verdict is still not pass, **escalate to user** with the full history and **run cleanup**. Do not silently keep looping.

### Cleanup (mandatory on every terminal exit path)

```bash
rm -f "$SHA_FILE"
# Remove the isolated worktree and its branch.
# On Windows, `git worktree remove --force` may partially succeed: git metadata
# (.git/worktrees/<name>/) is removed but the physical directory is retained due
# to OS file locks (see Mercury #265). Retry once after a short sleep, then fall
# back to rm -rf on the residual directory.
git worktree remove --force "${WORKTREE_PATH}" || {
  sleep 2
  git worktree remove --force "${WORKTREE_PATH}" || echo "WARN: git worktree remove retry failed for ${WORKTREE_PATH}" >&2
}
# rm -rf fallback: require non-empty path, existing dir, not a symlink, AND path whitelist.
# The case pattern pins the allowed root to *this repo's* `${REPO_ROOT}/.worktrees/` prefix so
# a corrupted WORKTREE_PATH cannot delete a different repo's worktree directory. We recompute
# REPO_ROOT here (Phase 2's local var is out of scope by Phase 5) and fall back to a pattern
# that matches nothing if we are outside a git repo — refuse-by-default semantics.
# `rm -rf -- "${path}"` uses POSIX rm's `--` end-of-options terminator (rm(1)).
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo "")
if [ -z "${REPO_ROOT}" ]; then
  echo "WARN: cannot determine REPO_ROOT (cwd not in a git repo) — skipping rm -rf fallback for ${WORKTREE_PATH}" >&2
elif [ -n "${WORKTREE_PATH}" ] && [ -d "${WORKTREE_PATH}" ] && [ ! -L "${WORKTREE_PATH}" ]; then
  case "${WORKTREE_PATH}" in
    "${REPO_ROOT}/.worktrees/"*)
      rm -rf -- "${WORKTREE_PATH}" || echo "WARN: rm -rf fallback failed for ${WORKTREE_PATH}" >&2
      ;;
    *)
      echo "WARN: refuse to rm -rf path outside ${REPO_ROOT}/.worktrees/: ${WORKTREE_PATH}" >&2
      ;;
  esac
fi
# `|| echo WARN`: surface cleanup failures to stderr rather than silently swallowing them.
# If worktree metadata still references the branch (extreme retry-failure path), `git branch -d`
# refuses with "branch is checked out". We warn and continue — orphan branch can be reclaimed
# by `scripts/worktree-reaper.sh --prune` on the next cycle.
git branch -d "${TASK_BRANCH}" || echo "WARN: git branch -d ${TASK_BRANCH} failed (likely still registered in a worktree)" >&2
```

This runs on `pass`, `blocked`, escalation after `partial`/`fail`, and on iteration-cap escalation. The ONLY paths that skip cleanup are intra-iteration dev re-dispatches (because Phase 3 still needs the SHA and the worktree is still active). If the loop terminates without reaching one of these branches (e.g. host crash), the SHA file at `${TMPDIR:-/tmp}/dev-pipeline-task-start-sha-${BRANCH_KEY}` will be cleaned up on the next pipeline run against the same branch (the new invocation overwrites it) or by OS tmp eviction; orphaned worktrees under `.worktrees/` can be reclaimed by `scripts/worktree-reaper.sh --prune`.

## Phase 6: Hand-off

Phase 6 is reached **only on `pass`**. Cleanup for non-pass terminal exits is handled inside Phase 5 — do not duplicate it here.

On pass:
1. Confirm commit is pushed (`git status`)
2. If user requested PR: invoke `/pr-flow`
3. Mark related GitHub Project item Done (via `/gh-project-flow` if Mercury self-dev) or via `Closes #N` in PR (general case)
4. Summarize in Chinese for the user
5. After PR merge is confirmed, run the **Phase 5 Cleanup block** as the final action (see Phase 5 above — the retry + `rm -rf` fallback logic is the SoT and is not duplicated here).

**Single source of truth**: the Phase 5 Cleanup block is the only authoritative description of when `$SHA_FILE` is removed. Phase 6 only reaches it via the `pass` branch above. If you find yourself debating "should I clean up here", re-read Phase 5.

## Detachability

This skill is designed to be portable to any repository that uses GitHub + Claude Code, provided:
- .claude/agents/dev.md and .claude/agents/acceptance.md exist with valid frontmatter
- The target repo uses **GitHub Issues + GitHub PRs** (the protocol references `Closes #N`, `gh pr create`, and Mercury's `/pr-flow` skill — all GitHub-specific). Non-GitHub repos would need protocol adaptation.
- The repo has a sane verifyCommands story (tests, lint, build commands that exit non-zero on failure)
- The user is on a feature branch (not main, develop, or master)

The `/gh-project-flow` reference in Phase 6 is **Mercury-specific** and should be removed or replaced when porting elsewhere — it is mentioned only because Mercury self-development uses Project #3 for task tracking.

To use it elsewhere, copy this skill directory plus the two agent files, then strip the `/gh-project-flow` line from Phase 6. No other Mercury dependency.

## Known Limitations

- **No parallel tasks**. By design — Mercury's preset chain is linear single-task. For parallelism, open another session.
- **No persistent memory between invocations**. Each pipeline run is fresh. Phase 3 Memory Layer will lift this constraint.
- **Subagent context is independent**. The dev and acceptance subagents do NOT see the main session's history — only the prompt you send them. Be explicit; do not assume shared context.
- **Critic agent not included by default**. If you want a third independent verification pass (different model), add a Phase 4.5 dispatch to subagent_type critic. Out of scope for the baseline pipeline.

## Failure Modes

| Symptom | Likely Cause | Fix |
|---|---|---|
| Agent tool returns unknown subagent type dev | Frontmatter missing or invalid in .claude/agents/dev.md | Check that name dev is the first non-divider line; restart session |
| Dev commits files outside allowedWriteScope | Bundle scope was too vague, or dev hallucinated needed files | Tighten scope; if hallucination, fix and re-dispatch with explicit prohibition |
| Acceptance returns pass but obvious bug exists | Acceptance criteria did not cover the bug class | Update bundle criteria; this is a design failure of the bundle, not the agent |
| Pipeline loops 3+ times on the same finding | Dev keeps fixing the wrong thing | Escalate immediately; usually means the finding text is ambiguous |
