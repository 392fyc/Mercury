---
name: code-quality
description: Run code review then simplify on recently changed files. Use after completing implementation work, before committing. Combines diff-based review with code simplification.
disable-model-invocation: true
allowed-tools: Read, Grep, Glob, Bash(git diff *), Bash(git log *), Bash(git show *), Agent, Skill(simplify *)
---

# Code Quality Check

Run a two-phase quality check on recently changed code: review first, then simplify.

## Phase 1: Code Review

Review all uncommitted changes (staged + unstaged) against the project's standards.

1. Run `git diff HEAD` to get the full diff
2. For each changed file, read the complete file for context
3. Check for:
   - Logic errors or edge cases
   - Security issues (injection, XSS, hardcoded secrets)
   - Missing error handling at system boundaries
   - Breaking changes to public interfaces
   - Violations of patterns established in CLAUDE.md
4. Report findings with severity: **blocker** (must fix), **warning** (should fix), **nit** (optional)
5. If blockers found, fix them before proceeding to Phase 2

## Phase 2: Code Simplification

After review passes, invoke `/simplify` to check for:
- Code reuse opportunities
- Unnecessary complexity
- Dead code from refactoring

## Output

End with a summary:
- Files reviewed: N
- Blockers: N (fixed: N)
- Warnings: N
- Nits: N
- Simplifications applied: N

If all blockers are resolved, set the review-passed flag:
```bash
date -u +%Y-%m-%dT%H:%M:%SZ > "$CLAUDE_PROJECT_DIR/.claude/hooks/state/review-passed"
```
