---
name: git-commit-heredoc-pattern
description: Use a HEREDOC to pass multi-line git commit messages, always co-authored with Claude.
category: TOOL_GUIDE
roles:
  - dev
  - main
  - research
  - design
origin: IMPORTED
tags:
  - git
  - commit
  - heredoc
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Git Commit with HEREDOC

## Rule

Always use a HEREDOC to pass git commit messages. This prevents shell escaping issues with special characters and ensures the co-author trailer is formatted correctly.

## Template

```bash
git commit -m "$(cat <<'EOF'
<type>(<scope>): <subject>

<optional body — explain WHY, not WHAT>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

## Common Types

- `feat` — new feature
- `fix` — bug fix
- `refactor` — restructure without behavior change
- `docs` — documentation only
- `chore` — tooling, deps, config

## Anti-Patterns

- Do NOT use `git commit -m "message"` — breaks on quotes and newlines
- Do NOT use `--no-verify` unless explicitly instructed
- Do NOT amend published commits — create a new commit instead
