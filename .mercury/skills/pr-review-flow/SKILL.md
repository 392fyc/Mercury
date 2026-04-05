---
name: pr-review-flow
description: Full PR lifecycle — create PR, wait for Argus review, address all threads, then merge.
category: WORKFLOW
roles:
  - dev
origin: IMPORTED
tags:
  - pr
  - argus
  - github
  - review
generation: 0
parent_skill_ids: []
total_selections: 0
total_applied: 0
total_completions: 0
total_fallbacks: 0
last_validated_at: 2026-04-04T00:00:00.000Z
---

# PR → Argus → Merge Flow

## Steps

1. **Create PR** — target `develop`, not `master`
   ```bash
   gh pr create --title "<title>" --body "$(cat <<'EOF'
   ## Summary
   - <bullet>

   ## Test plan
   - [ ] <check>

   🤖 Generated with Claude Code
   EOF
   )"
   ```

2. **Wait for Argus** — poll every 30s
   ```bash
   gh pr checks <PR_NUMBER> --watch
   ```

3. **Address all threads** — respond to every inline comment and outside-diff comment
   - Fix code issues in new commits
   - Reply to informational comments with acknowledgement
   - Do NOT resolve threads — let the reviewer resolve after your fix

4. **Request re-review** — after all threads are addressed
   ```bash
   gh pr comment <PR_NUMBER> --body "@argusai All threads addressed. Please re-review."
   ```

5. **Merge** — only after Argus approves (no pending changes)
   ```bash
   gh pr merge <PR_NUMBER> --squash --delete-branch
   ```

## Critical Rules

- NEVER merge before Argus review completes
- NEVER force-push to `develop` or `master`
- NEVER resolve PR threads yourself — that is the reviewer's action
- Always push after every commit: `git push`
