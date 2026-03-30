---
name: auto-verify
description: |
  Use this skill before every commit and whenever the user asks to verify implementation quality, run checks, or apply a pre-commit quality gate. Trigger proactively on English and Chinese requests such as "verify", "pre-commit check", "quality gate", "run checks", "type check", "lint", "scope check", "验证", "自检", "commit前检查", "检查一下". This skill runs Mercury's local quality gate for dev work: compile or type-check when available, validate scope against the TaskBundle, run lint if configured, and produce evidence for `implementationReceipt.evidence`.
---

# Auto Verify

> Platform note: examples use PowerShell syntax for Codex on Windows. For bash or zsh, replace `2>$null` with `2>/dev/null` and adjust loops accordingly.

## When

- Use before every `git commit` during task implementation.
- Use after a meaningful change set, even if the user did not explicitly ask for checks.
- Use when a TaskBundle expects verification evidence or when you are preparing an implementation receipt.
- Do not skip this skill just because the change looks small.

## Pipeline

1. Collect the changed files:

```powershell
git diff --cached --name-only
```

2. Run type-check or compile checks. Choose the right command for the project:
   - If `package.json` has a `typecheck` or `check` script: run that
   - TypeScript project (`tsconfig.json` exists): `npx tsc --noEmit` or `npx tsc --build` for monorepos
   - JavaScript with JSDoc types: `npx tsc --checkJs --noEmit`
   - Other languages or no type system: record `SKIP` with a note

```powershell
if (Get-Command pnpm -ErrorAction SilentlyContinue) {
  pnpm typecheck
} elseif (Get-Command npm -ErrorAction SilentlyContinue) {
  npm run typecheck
} elseif (Get-Command npx -ErrorAction SilentlyContinue) {
  npx tsc --noEmit
} else {
  throw "No supported package runner found for type-check."
}
```

3. Validate scope against the active TaskBundle when available:
   - every changed file must be inside `allowedWriteScope.codePaths` or `allowedWriteScope.kbPaths`
   - no changed file may violate `docsMustNotTouch`
   - if no TaskBundle context is available, record `SKIP` instead of inventing scope
4. Run lint only if lint config exists:

```powershell
npx eslint --max-warnings 0 <changed-files>
```

5. Check docstring coverage on changed files (whole-file scan, not diff-only):
   - Threshold: 50% of public API surface in changed `.ts` files must have JSDoc
   - Count exported classes, functions, and methods, then count those with `/** ... */`
   - If coverage is below 50%, report which files are under the threshold
   - This scans the entire file, not just diff hunks
   - This aligns with CodeRabbit's pre-merge check (`.coderabbit.yaml` threshold: 50%)

```powershell
git diff --cached --name-only -- '*.ts' | ForEach-Object {
  $f = $_
  $content = Get-Content -Path $f -Raw
  $total = ([regex]::Matches($content, '(?m)^\s*(export (class|function|async function|const)\b|(async )?(get |set )?[a-z]\w*\()')).Count
  if (-not $total) { $total = 0 }
  $documented = ([regex]::Matches($content, '/\*\*[\s\S]*?\*/\s*(export (class|function|async function|const)\b|(async )?(get |set )?[a-z]\w*\()')).Count
  if (-not $documented) { $documented = 0 }
  $pct = if ($total -gt 0) { [math]::Floor(($documented * 100) / $total) } else { 100 }
  if ($pct -lt 50) {
    Write-Output "WARN: $f docstring coverage $pct% ($documented/$total)"
  }
}
```

6. Run git hygiene checks:
   - no stray debug artifacts that obviously should not ship
   - no `.only` in tests
   - branch naming matches the task expectation when a task branch is known
7. If a check fails:
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
