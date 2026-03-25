#!/usr/bin/env bash
# GATE: block direct push to develop/master — all merges must go through PRs.
# Token cost: ZERO. No external deps.

INPUT=$(cat)

# Debug: log raw input for diagnosing guard bypass (consistent with pre-commit-guard.sh)
STATE_DIR="$(dirname "$0")/state"
if ! mkdir -p "$STATE_DIR"; then
  echo "WARNING: cannot create state dir: $STATE_DIR" >&2
  # Continue execution — only debug logging is affected
fi
LOG_FILE="$STATE_DIR/push-guard-debug.log"
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 102400 ]; then
  tail -100 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi
echo "[$(date -Iseconds)] INPUT=$INPUT" >> "$LOG_FILE"

# Extract command
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

# Debug: log extracted command
echo "[$(date -Iseconds)] COMMAND=$COMMAND" >> "$LOG_FILE"

# Only intercept git push commands
printf '%s' "$COMMAND" | grep -qE '\bgit\s+push\b' || exit 0

# Block push to protected branches (develop, master, main)
# Extract all arguments after "git push", excluding flags (starting with -)
# and the remote name (first non-flag arg, typically "origin").
# Then normalize each target: strip leading "+", strip "refs/heads/" prefix,
# extract refspec RHS (part after ":"), and check against protected names.
PUSH_ARGS=$(printf '%s' "$COMMAND" | sed 's/.*git[[:space:]]\+push[[:space:]]*//')
PROTECTED="^(develop|master|main)$"
SKIPPED_REMOTE=false

for TOKEN in $PUSH_ARGS; do
  # Skip flags like --force, -u, --set-upstream
  case "$TOKEN" in --*|-*) continue ;; esac

  # Skip the first non-flag arg (remote name, e.g. "origin")
  if [ "$SKIPPED_REMOTE" = false ]; then
    SKIPPED_REMOTE=true
    continue
  fi

  # Normalize: strip leading "+" (force-push prefix)
  NORMALIZED="${TOKEN##+}"

  # Check refspec RHS if present (e.g. HEAD:develop → develop)
  if printf '%s' "$NORMALIZED" | grep -q ':'; then
    REFSPEC_TARGET="${NORMALIZED##*:}"
    # Strip refs/heads/ prefix from refspec target
    REFSPEC_TARGET="${REFSPEC_TARGET#refs/heads/}"
    if printf '%s' "$REFSPEC_TARGET" | grep -qE "$PROTECTED"; then
      echo "[$(date -Iseconds)] BLOCKED: refspec target '$REFSPEC_TARGET' from '$TOKEN'" >> "$LOG_FILE"
      cat >&2 <<'MSG'
BLOCKED: Direct push to develop/master is forbidden (CLAUDE.md rule).
All merges into develop must go through a Pull Request.
Use: git push -u origin <feature-branch> && gh pr create --base develop
MSG
      exit 2
    fi
    continue
  fi

  # Strip refs/heads/ prefix (e.g. refs/heads/develop → develop)
  NORMALIZED="${NORMALIZED#refs/heads/}"

  # Check if the normalized token is a protected branch
  if printf '%s' "$NORMALIZED" | grep -qE "$PROTECTED"; then
    echo "[$(date -Iseconds)] BLOCKED: direct target '$NORMALIZED' from '$TOKEN'" >> "$LOG_FILE"
    cat >&2 <<'MSG'
BLOCKED: Direct push to develop/master is forbidden (CLAUDE.md rule).
All merges into develop must go through a Pull Request.
Use: git push -u origin <feature-branch> && gh pr create --base develop
MSG
    exit 2
  fi
done

exit 0
