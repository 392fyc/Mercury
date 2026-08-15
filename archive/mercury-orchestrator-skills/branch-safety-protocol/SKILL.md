---
name: branch-safety-protocol
description: Dev agents must never switch branches — always stay on the assigned branch and push after every commit.
category: TOOL_GUIDE
roles:
  - dev
origin: IMPORTED
tags:
  - git
  - branch
  - safety
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# Branch Safety Protocol for Dev Agents

## The Rule

1. **Never switch branches** — work only on the branch specified in the TaskBundle
2. **Push after every commit** — `git push` immediately after `git commit`
3. **Verify branch before first commit** — `git branch --show-current`

## Startup Checklist

```bash
# Verify you are on the correct branch before any writes
git branch --show-current
# Expected: <branch from TaskBundle>

# If wrong branch:
git checkout <correct-branch>
```

## Commit Loop

```bash
# After completing a milestone:
git add <specific-files>   # Never "git add -A" — risks including secrets
git commit -m "$(cat <<'EOF'
feat(<scope>): <description>

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
git push
```

## Why Branch Switching is Forbidden

A prior incident caused workspace regression when a dev agent switched branches mid-task:
- Uncommitted changes from branch A were mixed with branch B work
- The resulting PR contaminated unrelated features
- Required manual revert and re-implementation

## Recovery if You Are on Wrong Branch

1. Do NOT commit anything yet
2. Stash changes: `git stash`
3. Switch to correct branch: `git checkout <correct-branch>`
4. Apply stash: `git stash pop`
5. Verify changes are on correct branch: `git status`
