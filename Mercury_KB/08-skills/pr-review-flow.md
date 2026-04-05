# pr-review-flow

**Category**: WORKFLOW  
**Roles**: dev  
**Origin**: IMPORTED  
**Generation**: 1

Full PR lifecycle — create PR, poll for review bot, fix all threads, resolve, merge.

## Summary

Simplified reference for the PR review flow. The full protocol with mandatory sequential gates lives in `.claude/skills/pr-flow/SKILL.md`.

## Key Steps

1. Create PR targeting `develop`
2. Create CronCreate polling job (MANDATORY)
3. Read ALL threads before fixing
4. Fix + reply to every thread
5. Push + resolve all threads via GraphQL
6. Verify 0 unresolved (gate)
7. Request `/review` for re-review
8. Merge after `reviewDecision=APPROVED`

## Source

- Operational: `.mercury/skills/pr-review-flow/SKILL.md`
- Full protocol: `.claude/skills/pr-flow/SKILL.md`
