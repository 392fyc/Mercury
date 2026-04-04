---
name: dual-verify
description: |
  Run parallel Claude Code deep-review and Codex code-audit, then consolidate findings before marking PR ready. Use instead of /code-review or auto-verify when doing pre-merge review. Trigger on: "dual verify", "dual-verify", "parallel review", "run dual verify", "双路验证", "双向验证", "并行review", "双路review".
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob, Agent
---

# Dual-Verify

Run Claude Code deep review and Codex code audit in parallel, then consolidate findings and mark review complete.

## When

- Before marking any PR as ready for merge.
- As a replacement for single-agent /code-review.
- Whenever CLAUDE.md requires code review before commit.

## Division of responsibility

| Responsibility | Owner |
|----------------|-------|
| TypeScript `tsc --noEmit` | Claude Code |
| Architecture / logic / integration correctness | Claude Code |
| Code style / edge cases / error handling | Codex rescue subagent |
| Metrics completeness (all 4 paths wired) | Codex rescue subagent |
| Memory leak (Map cleanup on all terminal paths) | Codex rescue subagent |
| Windows/PowerShell compat | Codex rescue subagent |

Codex is invoked via `Agent` tool (rescue subagent) — no manual terminal step required.

## Step 1 — Launch parallel reviewers

**Claude Code deep review** (this session):

```bash
git diff develop...HEAD --stat
git diff develop...HEAD
```

Check: TypeScript correctness (run `npx tsc --noEmit`), logic correctness, integration points, OpenSpace schema compliance, missing metric paths, memory leaks.

**Codex audit** (rescue subagent — launch via Agent tool with `subagent_type: codex:codex-rescue`):

## Step 2 — Collect results

Each reviewer produces:

```text
## <Reviewer> Review Results
Critical: N  High: N  Medium: N  Low: N
- <finding>
Overall: PASS | FAIL | NEEDS-CHANGES
```

## Step 3 — Cross-reference

Produce a consolidated report:

```text
## Dual-Verify Consolidated Report
Branch: <branch>
Claude: PASS | NEEDS-CHANGES
Codex:  PASS | NEEDS-CHANGES

Agreed Issues: <list or none>
Claude-only: <list or none>
Codex-only: <list or none>

Final Verdict: PASS | NEEDS-CHANGES
```

## Step 4 — Fix, verify, mark complete

1. Fix all Critical + High issues.
2. Run `auto-verify` (tsc --noEmit, scope, lint).
3. Set the review-passed flag:

```bash
touch .mercury/state/review-passed
```

4. Commit and push.

## Evidence

```text
dual-verify: PASS (Claude: PASS, Codex: PASS, N issues fixed)
```

## Rules

- Both reviewers must return PASS before proceeding to merge.
- Fix before merge — do not proceed on a split verdict.
- Codex surfaces Windows-specific and platform concerns that may not be visible in Claude's review.

## Fallback

If Codex is unavailable or the session cannot be started:
- Use `/code-review` (Claude Code built-in) as the sole reviewer.
- Document in the PR description that dual-verify was attempted but Codex was unavailable.
- This fallback is acceptable for low-risk changes; high-risk PRs (orchestrator core, auth, schema changes) should wait for Codex availability.
