---
name: pr-review-flow
description: Full PR lifecycle — create PR, poll for review bot, fix all threads, resolve, merge. Simplified reference.
category: WORKFLOW
roles:
  - dev
origin: IMPORTED
tags:
  - pr
  - review-bot
  - github
  - review
generation: 1
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-06T00:00:00.000Z
---

# PR → Review → Merge Flow

Simplified reference. Full protocol: `.claude/skills/pr-flow/SKILL.md`

## Steps (strict sequential order)

1. **Create PR** — target `develop`, not `master`
2. **MANDATORY: Create CronCreate polling job** — poll `gh pr view <N> --json reviews,reviewDecision` every 10min
3. **Read ALL threads** — enumerate inline comments + review body before fixing anything
4. **Fix + Reply to EVERY thread** — use `🤖 Prompt for AI Agents` block for code location
5. **Push + Resolve ALL threads** — resolve via GraphQL `resolveReviewThread` mutation
6. **Verify 0 unresolved** — re-query threads, GATE must pass before re-review
7. **Request /review** — create new CronCreate for re-review polling
8. **Merge** — only after `reviewDecision == APPROVED` AND 0 unresolved threads

## Gates (MUST pass before proceeding)

| Gate | Condition |
|------|-----------|
| After Phase 1 | PR created, PR_NUMBER stored |
| After Phase 2 | CronCreate job ID stored, reviews arrived |
| After Phase 3 | All threads enumerated, triage list complete |
| After Phase 4 | Every bot thread has a reply |
| After Phase 5 | 0 unresolved threads (verified via re-query) |
| After Phase 7 | reviewDecision=APPROVED, CI passes, 0 unresolved |

## Critical Rules

- NEVER merge before review bot approves
- NEVER skip CronCreate — use non-blocking polling
- NEVER start fixing before reading ALL threads
- NEVER request re-review while unresolved threads remain
- ALWAYS reply to every thread, even if you disagree
- ALWAYS resolve threads after pushing fixes
- ALWAYS push after every commit: `git push`
