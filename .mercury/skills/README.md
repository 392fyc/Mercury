# Mercury Skill Library

Operational skill store for the Mercury orchestrator's native skill engine (Issue #141).

Skills are automatically injected into agent dispatch prompts at task execution time, ranked by BM25 relevance to the task context.

## Directory Structure

```
.mercury/skills/
  README.md               — this file
  {skill-name}/
    SKILL.md              — skill definition (frontmatter + body)
    .skill_id             — UUID sidecar: {name}__{imp|cap|fix}_{uuid8}
  pending/
    {draft-name}/         — captured drafts awaiting Main Agent review
      SKILL.md
      .skill_id
```

## SKILL.md Frontmatter Schema

```yaml
name: skill-slug-hyphenated          # required
description: One-sentence for BM25   # required, max 120 chars
category: TOOL_GUIDE|WORKFLOW|REFERENCE  # required
roles: [dev, research, main, design, acceptance, critic]  # required (subset)
origin: IMPORTED|CAPTURED|DERIVED|FIXED  # required
source_task_id: TASK-xxxx            # required for CAPTURED+
source_role: dev                     # required for CAPTURED+
captured_at: ISO8601                 # required for CAPTURED+
captured_by: acceptance-agent        # required for CAPTURED+
tags: [tag1, tag2]                   # optional
generation: 0                        # version DAG depth (0 = root)
parent_skill_ids: []                 # lineage
total_selections: 0                  # times BM25-selected for dispatch
total_applied: 0                     # times agent applied the skill
total_completions: 0                 # times task passed acceptance with skill
total_fallbacks: 0                   # times skill injected but not applied
last_validated_at: ISO8601           # reset on acceptance PASS
```

## Quality Thresholds

| Metric | Threshold | Action |
|--------|-----------|--------|
| `last_validated_at` age | > 90 days | Flagged as STALE at startup |
| `completion_rate` | < 0.3 with > 5 selections | Flagged as UNDERPERFORMING |

## Lifecycle

1. **IMPORTED** — manually authored seed skills (this directory)
2. **CAPTURED** — auto-extracted after acceptance PASS, written to `pending/`
3. **DERIVED** — future: enhanced version of a captured skill
4. **FIXED** — future: repaired version of an underperforming skill

Pending skills require Main Agent review before activation.

## BM25 Field Weights

| Field | Weight |
|-------|--------|
| description | 2.0 |
| tags | 1.5 |
| category | 0.5 |

Max 3 skills injected per dispatch (~4500 tokens, ~5% context budget).
