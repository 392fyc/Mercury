---
name: dual-verify
description: |
  Run parallel Claude Code deep-review and Codex code-audit, then consolidate findings before marking PR ready. Use instead of auto-verify when doing pre-merge review. Trigger on: "dual verify", "dual-verify", "parallel review", "run dual verify", "双路验证", "双向验证", "并行review", "双路review".
---

# Dual-Verify (Codex)

Run a full code audit of the current branch vs develop, produce structured findings, and coordinate with the Claude Code deep review.

> Platform note: examples use PowerShell syntax. For bash, replace `2>$null` with `2>/dev/null`.

> **Role boundary:** Codex handles code logic, style, edge cases, metrics completeness, and Windows compat.
> TypeScript compilation (`tsc --noEmit`) is Claude Code's responsibility — do not attempt to run it here.
> Rescue subagent and MCP both operate in headless sandbox; file-reading audit is fully supported.

## When

- Before marking any PR as ready for merge.
- When explicitly asked to run dual-verify or parallel review.
- Invoked as rescue subagent in a Claude Code session.

## Step 1 — Collect diff

```powershell
git diff develop...HEAD --stat
git diff develop...HEAD -- packages/orchestrator/src/
```

## Step 2 — Codex audit

Read and analyze changed files. Check:

1. **Code style**: naming conventions, consistency, no dead code or stray debug artifacts.
2. **Edge cases**: null/undefined handling, empty collection paths, missing guard clauses.
3. **Error handling**: all async paths have `.catch()` or try/catch; no silently swallowed errors except explicitly labeled best-effort.
4. **Metrics completeness**: verify all four OpenSpace-compatible metric paths are wired:
   - `recordSelection()` — called when skill is retrieved for dispatch
   - `recordApplied()` — called at receipt submission (proxy for "agent used skill")
   - `recordCompletion()` — called on acceptance PASS
   - `recordFallback()` — called on terminal failure (maxReworks exceeded or BLOCKED)
5. **Memory leak**: `injectedSkillsByTask` Map entries deleted on ALL terminal acceptance paths (PASS, FAIL-maxReworks, BLOCKED). Rework cycles intentionally retain entries until terminal state.
6. **Windows/PowerShell compat**: no Unix-only shell assumptions; paths use `join()` not string concat.
7. **Package accuracy**: new `import` statements — verify package name and version match codebase expectations.

## Step 3 — Produce results

```text
## Codex Review Results
Branch: <branch-name>
Critical: N  High: N  Medium: N  Low: N

### Findings:
- [CRITICAL] <description>
- [HIGH] <description>
- [MEDIUM] <description>

Overall: PASS | NEEDS-CHANGES
```

## Step 4 — Report back

Return results to the Claude Code session for consolidation. Do not mark review complete unilaterally.

## Evidence

When included in receipt:

```text
dual-verify/codex: PASS (logic audit clean, metrics wired, memory leak fixed on all paths)
```

## Notes

- Report findings even on PASS so Claude's consolidation has full data.
- Codex-only findings (Windows compat, edge cases, metrics gaps) should be flagged clearly.
- TypeScript type errors are Claude Code's responsibility — flag only if apparent upon inspection.

## Fallback

If Claude Code is unavailable:
- Use the `auto-verify` skill (Codex built-in quality gate) as the sole reviewer.
- Document in the PR description that dual-verify was attempted but Claude Code was unavailable.
- High-risk PRs (orchestrator core, auth, schema changes) should wait for Claude Code availability.
