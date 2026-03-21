---
name: auto-verify
description: |
  Mercury's pre-commit verification gate for dev agents. Runs TypeScript type checking, scope validation, and linting before any commit. Use this skill whenever you're about to commit code, need to verify implementation quality, or want to run a quality gate. Triggers on: "verify", "pre-commit check", "auto-verify", "quality gate", "run checks", "type check", "scope check". This skill should be invoked proactively before every commit during task implementation — skipping verification leads to failed PRs and wasted rework cycles.
---

# Auto-Verify: Pre-Commit Quality Gate

Run this verification pipeline before every commit. Results go into `implementationReceipt.evidence`.

## Pipeline

### Check 1: TypeScript Compilation

```
npx tsc --noEmit
```

PASS = exit code 0. FAIL = fix errors before proceeding.

### Check 2: Scope Validation

```
git diff --cached --name-only
```

Verify each changed file is within TaskBundle's `allowedWriteScope.codePaths` or `allowedWriteScope.kbPaths`. Flag any file in `docsMustNotTouch`. Skip if no TaskBundle context available.

### Check 3: ESLint (if configured)

```
npx eslint --max-warnings 0 <changed-files>
```

Only run if ESLint config exists. Skip with note if not configured.

### Check 4: Git Hygiene

- No debug artifacts (`console.log` with TODO, `.only` in tests)
- Branch name matches TaskBundle `branch` field if specified

## Output

```
## Auto-Verify Results

| Check | Status | Details |
|-------|--------|---------|
| TypeScript | PASS/FAIL | {details} |
| Scope | PASS/FAIL/SKIP | {details} |
| ESLint | PASS/FAIL/SKIP | {details} |
| Git Hygiene | PASS/FAIL | {details} |

Overall: PASS/FAIL
```

## Evidence String

```
auto-verify: PASS (tsc: clean, scope: N files checked, eslint: clean, git: clean)
```

Do not commit with failing checks unless explicitly overridden.
