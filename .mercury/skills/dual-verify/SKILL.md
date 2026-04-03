---
name: dual-verify
description: Parallel Claude Code deep-review + Codex code-audit before PR readiness; replaces single-agent code-review
category: WORKFLOW
roles:
  - main
  - dev
  - acceptance
origin: IMPORTED
source_task_id: TASK-141
source_role: main
captured_at: 2026-04-04T00:00:00Z
captured_by: main-agent
tags:
  - code-review
  - verification
  - parallel
  - pr
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00Z
---

# Dual-Verify: Parallel Claude + Codex Code Review

Run Claude Code deep review and Codex code audit in parallel, then consolidate findings before marking review complete.

## When

- Before marking any PR as ready for merge.
- When the task is implementation-complete and you are preparing the final commit batch.
- As a replacement for single-agent code-review — whenever review is required by CLAUDE.md.
- On the same branch; no worktree isolation required.

## Workflow

### Division of responsibility

| Responsibility | Owner |
|----------------|-------|
| TypeScript `tsc --noEmit` | Claude Code |
| Architecture / logic / integration | Claude Code |
| Code style / edge cases / error handling | Codex (rescue subagent) |
| Metrics completeness (all 4 paths wired) | Codex (rescue subagent) |
| Memory leak (Map cleanup on all paths) | Codex (rescue subagent) |
| Windows/PowerShell compat | Codex (rescue subagent) |

Codex is invoked as rescue subagent — fully automated, no manual terminal step required.

### Step 1 — Launch both reviewers in parallel

**Claude Code** (deep review — TypeScript correctness, architecture, integration, OpenSpace schema):

```bash
git diff develop...HEAD --stat
git diff develop...HEAD
npx tsc --noEmit  # Claude Code's responsibility
```

**Codex** (logic audit — style, edge cases, metrics, memory leaks, Windows compat):

```bash
# Launch via Agent tool with subagent_type: codex:codex-rescue
# Prompt: "Audit feat/<branch> vs develop. Check: code style, edge cases,
# metrics completeness (all 4 paths), memory leak cleanup on terminal paths,
# Windows compat. TypeScript typecheck is not required."
```

### Step 2 — Collect results

Both reviewers produce a structured result block:

```text
## <Reviewer> Review Results
Critical: <count>  High: <count>  Medium: <count>  Low: <count>
<finding-1>
<finding-2>
Overall: PASS | FAIL | NEEDS-CHANGES
```

### Step 3 — Cross-reference findings

Consolidate into a unified report:

```text
## Dual-Verify Consolidated Report
Branch: <branch-name>
Claude: PASS | FAIL | NEEDS-CHANGES
Codex:  PASS | FAIL | NEEDS-CHANGES

### Agreed Issues (both flagged):
- <issue>

### Claude-only:
- <issue>

### Codex-only:
- <issue>

### Resolution Plan:
- <action-1>
- <action-2>

### Final Verdict: PASS | NEEDS-CHANGES
```

### Step 4 — Fix and mark review

1. Fix all Critical and High issues.
2. Address or document Medium issues.
3. Run `auto-verify` to confirm TypeScript and scope pass.
4. Mark review flag:

```bash
touch .claude/hooks/state/review-passed
# or: powershell -File scripts/codex/guard.ps1 mark-review
```

5. Commit and push.

## Output Evidence

Record in `implementationReceipt.evidence`:

```text
dual-verify: PASS (Claude: PASS, Codex: PASS, N critical fixed, M high fixed)
```

## Notes

- Both reviewers must agree on PASS before the PR is marked ready.
- If one reviewer returns NEEDS-CHANGES, fix before proceeding — do not merge on split verdict.
- Codex may surface Windows/PowerShell-specific concerns not visible to Claude; always include Codex in the loop.
- Cross-referencing catches false positives: an issue flagged by only one reviewer should be investigated, not auto-dismissed.

## Fallback

If one reviewer is unavailable, fall back to that reviewer's individual skill:

| Unavailable | Fallback |
|-------------|----------|
| Codex | Claude Code `/code-review` skill (deep diff review) |
| Claude Code | Codex `auto-verify` skill (type-check, scope, lint, docstring) |

Document in the PR description that dual-verify was attempted but one side was unavailable. High-risk PRs (orchestrator core, auth, schema changes) should wait for both reviewers.
