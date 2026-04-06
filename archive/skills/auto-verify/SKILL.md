---
name: auto-verify
description: |
  Use this skill before every commit and whenever the user asks to verify implementation quality, run checks, or apply a pre-commit quality gate. Trigger proactively on English and Chinese requests such as "verify", "pre-commit check", "quality gate", "run checks", "type check", "lint", "scope check", "验证", "自检", "commit前检查", "检查一下". This skill runs Mercury's local quality gate for dev work: compile or type-check when available, validate scope against the TaskBundle, run lint if configured, and produce evidence for `implementationReceipt.evidence`.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob
---

# Auto Verify

## When

- Use before every `git commit` during task implementation.
- Use after a meaningful change set, even if the user did not explicitly ask for checks.
- Use when a TaskBundle expects verification evidence or when you are preparing an implementation receipt.
- Do not skip this skill just because the change looks small.

## Pipeline

1. Collect the changed files:

```bash
git diff --cached --name-only
```

1. Run type-check or compile checks. Choose the right command for the project:
   - If `package.json` has a `typecheck` or `check` script: run that (e.g., `npm run typecheck`)
   - TypeScript project (tsconfig.json exists): `npx tsc --noEmit` or `npx tsc --build` for monorepos
   - JavaScript with JSDoc types: `npx tsc --checkJs --noEmit`
   - Other languages or no type system: record `SKIP` with a note

```bash
# Preferred: use repo script if available
npm run typecheck 2>/dev/null || npx tsc --noEmit
```

1. Validate scope against the active TaskBundle when available:
   - every changed file must be inside `allowedWriteScope.codePaths` or `allowedWriteScope.kbPaths`
   - no changed file may violate `docsMustNotTouch`
   - if no TaskBundle context is available, record `SKIP` instead of inventing scope
1. Run lint only if lint config exists:

```bash
npx eslint --max-warnings 0 <changed-files>
```

1. Check docstring coverage on changed files (whole-file scan, not diff-only):
   - Threshold: 50% of public API surface in changed `.ts` files must have JSDoc
   - Check: for each changed `.ts` file, count exported classes/functions/methods and
     count those with a `/** ... */` comment immediately preceding them
   - If coverage < 50%, report which exports are missing JSDoc
   - Note: this scans the entire file, not just the diff hunks — pre-existing undocumented
     exports in a touched file will also count
   - This aligns with the review bot's pre-merge check (docstring threshold: 50%)

```bash
# Quick heuristic: count exports vs documented exports in changed .ts files.
# Note: the regex is a rough approximation — it catches most exported declarations
# but may miss or over-count some patterns (e.g. re-exports, decorators).
git diff --cached --name-only -- '*.ts' | while IFS= read -r f; do
  TOTAL=$(grep -cE '^\s*(export (class|function|async function|const)|^\s+(async )?(get |set )?[a-z]\w*\()' "$f" 2>/dev/null || echo 0)
  DOCUMENTED=$(grep -B1 -E '^\s*(export (class|function|async function|const)|^\s+(async )?(get |set )?[a-z]\w*\()' "$f" 2>/dev/null | grep -c '^\s*\*/' || echo 0)
  [ "$TOTAL" -gt 0 ] && PCT=$((DOCUMENTED * 100 / TOTAL)) || PCT=100
  [ "$PCT" -lt 50 ] && echo "WARN: $f docstring coverage $PCT% (${DOCUMENTED}/${TOTAL})"
done
```

1. Run git hygiene checks:
   - no stray debug artifacts that obviously should not ship
   - no `.only` in tests
   - branch naming matches the task expectation when a task branch is known
1. If a check fails:
   - fix obvious in-scope issues
   - rerun the failed check
   - escalate instead of committing if the failure requires out-of-scope changes

## Output

Produce a compact result block:

```text
## Auto-Verify Results
TypeCheck: PASS | FAIL | SKIP
Scope: PASS | FAIL | SKIP
Lint: PASS | FAIL | SKIP
DocString: PASS | WARN (<files>) | SKIP
GitHygiene: PASS | FAIL
Overall: PASS | FAIL
```

- Say exactly which check failed and why.
- Do not recommend committing with failing checks unless the user explicitly overrides.

## Evidence

Record a one-line entry suitable for `implementationReceipt.evidence`, for example:

```text
auto-verify: PASS (tsc: clean, scope: 8 files checked, lint: clean, docstring: 75%, git: clean)
```

If anything failed, keep the failure summary and the rerun result.
