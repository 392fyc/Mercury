---
name: auto-verify
description: |
  Mercury's pre-commit verification gate for dev agents. Runs TypeScript type checking, scope validation, and linting before any commit. Use this skill whenever you're about to commit code, need to verify implementation quality, or want to run a quality gate. Triggers on: "verify", "验证", "pre-commit check", "auto-verify", "quality gate", "自检", "检查", "commit前检查", "run checks", "type check", "scope check". This skill should be invoked proactively before every commit during task implementation — skipping verification leads to failed PRs and wasted rework cycles. Even if you're confident the code is correct, run the checks anyway; TypeScript catches things humans miss.
user-invocable: true
allowed-tools: Bash, Read, Grep, Glob
---

# Auto-Verify: Pre-Commit Quality Gate

This skill runs a structured verification pipeline before committing code. It exists because Mercury's SoT workflow requires evidence of passing checks in the `implementationReceipt.evidence` array, and because catching issues before commit is 10x cheaper than catching them during PR review or acceptance.

## When to Run

Run this skill:
- Before every `git commit` during task implementation
- After completing a work item (W1, W2, etc.) in a multi-step task
- When the TaskBundle includes `verifyGate.autoChecks`
- When you want to confirm your changes haven't broken anything

## Verification Pipeline

Execute these checks in order. Each check produces a PASS/FAIL result. All checks must pass before committing.

### Check 1: TypeScript Compilation

```bash
npx tsc --noEmit 2>&1
```

- PASS: exit code 0, no errors
- FAIL: any compilation error — fix before proceeding
- Record: number of errors if any, first error message

### Check 2: Scope Validation

Verify that all changed files fall within the TaskBundle's `allowedWriteScope`.

```bash
git diff --cached --name-only
```

For each changed file, check:
- Is it under one of `allowedWriteScope.codePaths`?
- Is it under one of `allowedWriteScope.kbPaths`?
- Is it in `docsMustNotTouch`? (must NOT be modified)

If scope information isn't available (no active TaskBundle), skip this check with a warning rather than blocking.

- PASS: all files within scope
- FAIL: list each out-of-scope file with the violated boundary
- Record: total files checked, any violations

### Check 3: ESLint (if configured)

```bash
npx eslint --max-warnings 0 <changed-files> 2>&1
```

Only run if an ESLint config exists (`.eslintrc.*`, `eslint.config.*`, or `eslintConfig` in package.json). If no config exists, skip with a note.

- PASS: exit code 0, no errors or warnings
- FAIL: list errors/warnings by file
- Record: error count, warning count

### Check 4: Git Hygiene

Quick sanity checks:
- No untracked files that should be staged (look for new files in `allowedWriteScope`)
- No debug artifacts left behind (`console.log` with TODO markers, `.only` in tests)
- Branch name matches expected pattern (if TaskBundle specifies `branch`)

## Output Format

After running all checks, produce a structured result block:

```
## Auto-Verify Results

| Check | Status | Details |
|-------|--------|---------|
| TypeScript | PASS/FAIL | {error count or "clean"} |
| Scope | PASS/FAIL/SKIP | {violation count or "all files in scope"} |
| ESLint | PASS/FAIL/SKIP | {error/warning count or "clean"} |
| Git Hygiene | PASS/FAIL | {issues found or "clean"} |

**Overall: PASS/FAIL**
```

## Recording Evidence

The verification result should be included in `implementationReceipt.evidence` as a string entry:

```
"auto-verify: PASS (tsc: clean, scope: 12 files checked, eslint: clean, git: clean)"
```

If any check fails:

```
"auto-verify: FAIL (tsc: 3 errors in role-loader.ts, scope: PASS, eslint: SKIP, git: PASS)"
```

## Failure Handling

When a check fails:
1. Report the failure clearly with file names and line numbers
2. Attempt to fix if the fix is obvious and within scope (e.g., missing import, unused variable)
3. Re-run the failed check after fixing
4. If the fix requires changes outside `allowedWriteScope`, escalate — do not fix

Do not commit with failing checks. The only exception is if the user explicitly overrides with "commit anyway" or equivalent.
