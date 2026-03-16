#!/bin/bash
# GATE: block stop if staged uncommitted changes exist.
# Token cost: ZERO. No external deps.

cd "$CLAUDE_PROJECT_DIR" 2>/dev/null || exit 0

STAGED=$(git diff --cached --name-only 2>/dev/null)

if [ -n "$STAGED" ]; then
  echo '{"decision":"block","reason":"Staged uncommitted changes detected. Commit before stopping (CLAUDE.md: commit at every checkpoint)."}'
  exit 0
fi

exit 0
