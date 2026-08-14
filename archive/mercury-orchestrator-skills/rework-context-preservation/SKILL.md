---
name: rework-context-preservation
description: During rework, address only acceptance findings and preserve all passing work without scope creep.
category: WORKFLOW
roles:
  - dev
origin: IMPORTED
tags:
  - rework
  - acceptance
  - context
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Rework Context Preservation

## Situation

You received a rework request (acceptance verdict: `fail` or `partial`). Your task is to fix ONLY the identified gaps without undoing passing work.

## Protocol

1. **Read the findings list** — each item is a specific gap needing a fix
2. **Read the recommendations** — suggested remediation steps by the acceptance agent
3. **Check what already passed** — do NOT modify accepted code
4. **Fix the gaps** — targeted changes only
5. **Re-verify** — confirm each finding is addressed before committing

## Rework Commit Template

```bash
git commit -m "$(cat <<'EOF'
fix(<scope>): address acceptance findings — <brief description>

Findings addressed:
- <finding 1>
- <finding 2>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
git push
```

## Anti-Patterns

- Do NOT refactor passing code during rework — acceptance may fail on different grounds
- Do NOT skip reading the findings — guessing wastes cycles
- Do NOT add new features during rework — scope creep causes re-rejection
- Do NOT commit without addressing every finding in the list

## Receipt Update

When submitting the rework receipt, reference each finding by index:
```
evidence: ["Finding 1: fixed in src/foo.ts:42", "Finding 2: added test in test/bar.test.ts"]
```
