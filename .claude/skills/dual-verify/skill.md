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

## Codex execution path

> **Important:** Codex must be invoked via `! codex "..."` in the terminal — **not** via rescue subagent or MCP.
> Both `codex-rescue` and `mcp__codex__codex` run in headless sandbox (`workspace-write`) which blocks
> `tsc`, `node`, `pnpm`, `npx`. The `! codex` terminal path uses interactive mode where compilers can run.

## Step 1 — Launch parallel reviewers

**Claude Code deep review** (this session):

Perform a full diff review of the current branch against develop:

```bash
git diff develop...HEAD --stat
git diff develop...HEAD
```

Check: TypeScript correctness, logic correctness, integration correctness, OpenSpace schema compliance (for skill engine changes), missing metric paths, memory leaks, security issues.

**Codex audit** (separate session — launch via Agent tool or manually):

```bash
# Prompt for Codex:
# "Audit all changes on feat/<branch> vs develop.
#  Check: code style, edge cases, missing error handling,
#  metrics completeness, package version accuracy, Windows/PowerShell compat."
```

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
touch .claude/hooks/state/review-passed
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
