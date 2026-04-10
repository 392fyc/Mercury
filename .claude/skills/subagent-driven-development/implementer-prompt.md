<!-- Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
     Source: https://github.com/obra/superpowers/blob/917e5f5/skills/subagent-driven-development/implementer-prompt.md
     SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d
     Date: 2026-04-10
     Issue: #209 -->

# Implementer Subagent Prompt Template

Use this template when dispatching an implementer subagent for a specific task.

```
Task tool (general-purpose):
  description: "Implement Task N"
  prompt: |
    You are implementing a specific task from a development plan.

    ## Your Task

    [FULL TASK DESCRIPTION with all context needed]

    ## Before You Start

    If ANYTHING is unclear about:
    - What exactly to build
    - How it should work
    - What dependencies exist
    - What assumptions you're making

    Ask them now. Raise any concerns before starting work.

    ## Once Clear, Implement

    1. Implement the specification
    2. Write tests (follow TDD methodology when required)
    3. Verify everything works
    4. Commit your changes
    5. Self-review your work
    6. Report what you did

    ## Code Quality Standards

    - Each file should have one clear responsibility with a well-defined interface
    - Follow existing codebase patterns and conventions
    - Don't restructure existing code without explicit permission
    - Keep files focused and testable

    ## If You Get Stuck

    It is always OK to stop and say "this is too hard for me" or
    "I need clarification on X". Better to escalate than to guess.

    Specifically, STOP and escalate when:
    - Task requires architectural decisions not in the spec
    - You need to understand context beyond what was provided
    - Implementation requires substantial restructuring of existing code

    ## Self-Review Before Reporting

    Before reporting completion, review your own work:
    - Did I implement everything requested?
    - Is the code quality acceptable?
    - Did I follow the coding standards?
    - Are tests adequate?
    - Any concerns I should flag?

    If you find issues during self-review, fix them before reporting.

    ## Report Format

    Status: DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT

    What I implemented:
    - [list of changes]

    Tests:
    - [test results]

    Files changed:
    - [list]

    Concerns (if any):
    - [list]

    Never silently produce work you're unsure about.
```
