---
name: subagent-driven-development
description: >-
  Execute plans by dispatching fresh subagent per task, with two-stage review
  (spec compliance then code quality). Use when implementing a multi-task plan
  within the current session using isolated subagents.
user-invocable: true
allowed-tools: Read, Glob, Grep, Agent
upstream_source: "https://github.com/obra/superpowers"
upstream_sha: "917e5f53b16b115b70a3a355ed5f4993b9f8b73d"
upstream_license: "MIT"
cherry_picked_in: 216
cherry_picked_at: "2026-04-10"
---

<!-- Cherry-picked from obra/superpowers (MIT, Copyright 2025 Jesse Vincent)
     Source: https://github.com/obra/superpowers/blob/917e5f5/skills/subagent-driven-development/SKILL.md
     SHA: 917e5f53b16b115b70a3a355ed5f4993b9f8b73d
     Date: 2026-04-10
     Issue: #209 -->

# Subagent-Driven Development

Execute plan by dispatching fresh subagent per task, with two-stage review after each: spec compliance review first, then code quality review.

**Why subagents:** You delegate tasks to specialized agents with isolated context. By precisely crafting their instructions and context, you ensure they stay focused and succeed at their task. They should never inherit your session's context or history — you construct exactly what they need. This also preserves your own context for coordination work.

**Core principle:** "Fresh subagent per task + two-stage review (spec then quality) = high quality, fast iteration"

## When to Use

Use subagent-driven development when you have an implementation plan with mostly independent tasks that you want to execute within the current session. It differs from the "executing-plans" approach by keeping you in the same session while dispatching fresh subagents per task without context pollution.

## The Process

The workflow involves:

1. Reading the plan and extracting all tasks with full context
2. Creating a TodoWrite to track progress
3. For each task:
   - Dispatch an implementer subagent
   - Address any questions before implementation proceeds
   - Implementer implements, tests, commits, and self-reviews
   - Dispatch spec compliance reviewer
   - Dispatch code quality reviewer
   - Mark task complete once approved
4. After all tasks, dispatch final code reviewer
5. Use your project's branch completion workflow (e.g., Mercury's `/pr-flow` skill).

## Model Selection

Tailor model capability to task complexity: "Use the least powerful model that can handle each role to conserve cost and increase speed." Mechanical implementation tasks use faster models; integration tasks use standard models; architecture and review tasks use the most capable models.

## Key Principles

- Never skip reviews or proceed with unfixed issues
- Always follow spec compliance review before code quality review
- Answer implementer questions completely before allowing them to proceed
- Implement review loops: when issues are found, the same implementer fixes them, then reviewers verify again
- Never dispatch multiple implementation subagents in parallel
- Provide full task context directly rather than having subagents read files

## Subagent Prompt Templates

See the following files in this skill directory for dispatch templates:

- **[implementer-prompt.md](implementer-prompt.md)** — Template for dispatching implementation subagents
- **[spec-reviewer-prompt.md](spec-reviewer-prompt.md)** — Template for spec compliance review subagents
- **[code-quality-reviewer-prompt.md](code-quality-reviewer-prompt.md)** — Template for code quality review subagents
