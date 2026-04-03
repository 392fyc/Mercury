# Mercury Skill Library — Index

**Section**: 08-skills  
**Created**: 2026-04-04  
**Maintained by**: Main Agent  
**Implementation**: Issue #141

This section stores the KB mirror of all active Mercury skills. The operational skill store lives in `.mercury/skills/` — these KB entries provide long-term archival, Obsidian search indexing, and human review.

---

## Active Skills

| Skill | Category | Roles | Origin | Generation |
|-------|----------|-------|--------|------------|
| [git-commit-heredoc-pattern](git-commit-heredoc-pattern.md) | TOOL_GUIDE | dev, main, research, design | IMPORTED | 0 |
| [pr-coderabbit-flow](pr-coderabbit-flow.md) | WORKFLOW | dev | IMPORTED | 0 |
| [kb-obsidian-access](kb-obsidian-access.md) | TOOL_GUIDE | main, research | IMPORTED | 0 |
| [dispatch-scope-validation](dispatch-scope-validation.md) | TOOL_GUIDE | main | IMPORTED | 0 |
| [research-web-verify](research-web-verify.md) | TOOL_GUIDE | research, dev, main | IMPORTED | 0 |
| [acceptance-receipt-check](acceptance-receipt-check.md) | TOOL_GUIDE | acceptance, main | IMPORTED | 0 |
| [branch-safety-protocol](branch-safety-protocol.md) | TOOL_GUIDE | dev | IMPORTED | 0 |
| [issue-before-task](issue-before-task.md) | WORKFLOW | main | IMPORTED | 0 |
| [rework-context-preservation](rework-context-preservation.md) | WORKFLOW | dev | IMPORTED | 0 |
| [research-deep-protocol](research-deep-protocol.md) | WORKFLOW | research | IMPORTED | 0 |

## Pending Review

Skills captured after acceptance PASS are written to `.mercury/skills/pending/` by the skill capturer. Main Agent reviews and promotes them to active status.

| File | Source Task | Captured At |
|------|-------------|-------------|
| _(none yet)_ | | |

---

## Structure

```
Mercury_KB/08-skills/
  INDEX.md                        — this file
  {skill-name}.md                 — KB mirror of each active skill
.mercury/skills/
  {skill-name}/
    SKILL.md                      — operational skill (loaded by SkillRegistry)
    .skill_id                     — UUID sidecar
  pending/
    {draft-name}/
      SKILL.md                    — draft, pending Main Agent review
      .skill_id
```

## Quality Metrics Legend

Skills in `.mercury/skills/` track:
- `total_selections`: times injected into a dispatch prompt
- `total_completions`: times the task passed acceptance while skill was injected
- `total_fallbacks`: times the skill was injected but task did not use it
- `completion_rate` = total_completions / total_applied
- Staleness: skills with `last_validated_at` older than 90 days are flagged at startup
- Underperforming: skills with completion_rate < 0.3 and > 5 selections are flagged
