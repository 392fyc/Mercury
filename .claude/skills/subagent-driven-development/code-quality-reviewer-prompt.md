<!-- Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
     Source: https://github.com/obra/superpowers/blob/917e5f5/skills/subagent-driven-development/code-quality-reviewer-prompt.md
     SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d
     Date: 2026-04-10
     Issue: #209 -->

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
