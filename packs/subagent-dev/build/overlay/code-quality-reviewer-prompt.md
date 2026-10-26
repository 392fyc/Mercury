<!-- Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
     Source: https://github.com/obra/superpowers/blob/917e5f5/skills/subagent-driven-development/code-quality-reviewer-prompt.md
     SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d (2026-04-10, Issue #209)
     Mercury adaptation (#509, 2026-07-04): upstream v6.1.x merged spec + code-quality
     review into a single task-reviewer; Mercury intentionally keeps the two-stage
     split. This file is the CODE-QUALITY stage, dispatched AFTER
     spec-reviewer-prompt.md passes. Canonical for Mercury, not a stale reference
     to a deleted upstream file — see SKILL.md "Mercury adaptation" note. -->

# Code Quality Reviewer Prompt Template

Use this template when dispatching a code quality reviewer subagent.

**Purpose:** Verify implementation is well-built (clean, tested, maintainable)

**Only dispatch after spec compliance review passes.**

```
Dispatch a code quality reviewer subagent (or use your project's review process).

  WHAT_WAS_IMPLEMENTED: [from implementer's report]
  PLAN_OR_REQUIREMENTS: Task N from [plan-file]
  BASE_SHA: [commit before task]
  HEAD_SHA: [current commit]
  DESCRIPTION: [task summary]
```

**In addition to standard code quality concerns, the reviewer should check:**
- Does each file have one clear responsibility with a well-defined interface?
- Are units decomposed so they can be understood and tested independently?
- Is the implementation following the file structure from the plan?
- Did this implementation create new files that are already large, or significantly grow existing files? (Don't flag pre-existing file sizes — focus on what this change contributed.)

**Code reviewer returns:** Strengths, Issues (Critical/Important/Minor), Assessment
