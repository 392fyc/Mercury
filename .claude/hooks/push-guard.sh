#!/usr/bin/env bash
# GATE: block direct push to develop/master — all merges must go through PRs.
# Token cost: ZERO. No external deps.

INPUT=$(cat)

# Extract command
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

# Only intercept git push commands
printf '%s' "$COMMAND" | grep -qE 'git\s+push' || exit 0

# Block push to protected branches (develop, master, main)
# Check if the push target includes a protected branch name
if printf '%s' "$COMMAND" | grep -qE 'git\s+push.*\b(origin\s+)?(develop|master|main)\b'; then
  # Allow if pushing a feature branch that happens to contain the word
  # e.g. "git push -u origin fix/develop-typo" should NOT be blocked
  # Only block when the protected branch IS the push target (last argument)
  LAST_ARG=$(printf '%s' "$COMMAND" | grep -oE '\S+$')
  if [ "$LAST_ARG" = "develop" ] || [ "$LAST_ARG" = "master" ] || [ "$LAST_ARG" = "main" ]; then
    cat >&2 <<'MSG'
BLOCKED: Direct push to develop/master is forbidden (CLAUDE.md rule).
All merges into develop must go through a Pull Request.
Use: git push -u origin <feature-branch> && gh pr create --base develop
MSG
    exit 2
  fi
fi

exit 0
