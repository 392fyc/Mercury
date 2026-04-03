---
name: dual-verify
description: |
  Run parallel Claude Code deep-review and Codex code-audit, then consolidate findings before marking PR ready. Use instead of auto-verify when doing pre-merge review. Trigger on: "dual verify", "dual-verify", "parallel review", "run dual verify", "双路验证", "双向验证", "并行review", "双路review".
---

# Dual-Verify (Codex)

Run a full code audit of the current branch vs develop, produce structured findings, and coordinate with the Claude Code deep review.

> Platform note: examples use PowerShell syntax. For bash, replace `2>$null` with `2>/dev/null`.

> **Execution path note:** This skill must be invoked via `! codex "..."` in the terminal (interactive mode), **not** via the rescue subagent or MCP tool. Both `codex-rescue` subagent and `mcp__codex__codex` run through `codex-companion.mjs` in headless/unattended mode with `sandbox: "workspace-write"`, which blocks `tsc`, `node`, `pnpm`, and `npx`. The terminal `! codex` path uses Codex CLI interactive mode where each command gets user approval and compilers/interpreters can run.

## When

- Before marking any PR as ready for merge.
- When explicitly asked to run dual-verify or parallel review.
- As a replacement for single-agent code-review when paired with Claude Code.

## Step 1 — Collect diff

```powershell
git diff develop...HEAD --stat
git diff develop...HEAD
```

## Step 2 — Codex audit

Check the following:

1. **Code style**: naming conventions, consistency, no dead code left in.
2. **Edge cases**: null/undefined handling, array bounds, empty collection paths.
3. **Error handling**: all async paths have `.catch()` or try/catch; no swallowed errors except intentional best-effort.
4. **Metrics completeness**: for skill engine changes — verify all four metric paths (recordSelection, recordApplied, recordCompletion, recordFallback) are wired and called in the correct lifecycle events.
5. **Package versions**: any new `import` statements — verify package exists and version is accurate via web search.
6. **Windows/PowerShell compatibility**: no Unix-only shell assumptions in scripts; paths use forward slashes or `join()`.
7. **Memory leaks**: Maps and Sets populated during task lifecycle are cleaned up on all terminal paths (PASS, FAIL, BLOCKED).

## Step 3 — Produce results

```text
## Codex Review Results
Branch: <branch-name>
Critical: N  High: N  Medium: N  Low: N

### Findings:
- [CRITICAL] <description>
- [HIGH] <description>
- [MEDIUM] <description>

Overall: PASS | FAIL | NEEDS-CHANGES
```

## Step 4 — Fix and mark review

1. Fix Critical and High issues within scope.
2. Run type-check:

```powershell
cd packages/orchestrator
npx tsc --noEmit
```

3. Mark review complete:

```powershell
powershell -File scripts/codex/guard.ps1 mark-review
```

4. Commit and push using git-safe:

```powershell
powershell -File scripts/codex/git-safe.ps1 commit -Message "fix: <description>"
powershell -File scripts/codex/git-safe.ps1 push origin feat/<branch>
```

## Evidence

Record in receipt:

```text
dual-verify: PASS (Codex: PASS, N critical fixed, M high fixed)
```

## Notes

- Report findings even on PASS so Claude's consolidation has full data.
- Do not mark the PR ready unilaterally — dual-verify requires both sides to agree.
- Codex-only findings should be flagged clearly so Claude can cross-reference.

## Fallback

If Claude Code is unavailable or the dual-verify session cannot be coordinated:
- Run the `auto-verify` skill (Codex built-in quality gate) as the sole reviewer.
- Document in the PR description that dual-verify was attempted but Claude Code was unavailable.
- `auto-verify` covers type-check, scope, lint, and docstring coverage — use it for low-risk changes.
- High-risk PRs (orchestrator core, auth, schema changes) should wait for Claude Code availability.
