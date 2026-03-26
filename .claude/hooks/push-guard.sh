#!/usr/bin/env bash
# GATE: block direct push to develop/master — all merges must go through PRs.
# Token cost: ZERO. No external deps.

INPUT=$(cat)

# Debug logging: opt-in via GUARD_DEBUG=1 to avoid persisting sensitive payloads.
STATE_DIR="$(dirname "$0")/state"
if ! mkdir -p "$STATE_DIR"; then
  echo "WARNING: cannot create state dir: $STATE_DIR" >&2
fi
LOG_FILE="$STATE_DIR/push-guard-debug.log"

debug_log() {
  [ "${GUARD_DEBUG:-0}" = "1" ] && echo "[$(date -Iseconds)] $1" >> "$LOG_FILE"
}

if [ "${GUARD_DEBUG:-0}" = "1" ] && [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 102400 ]; then
  tail -100 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

debug_log "INPUT=$INPUT"

# Extract command
if command -v jq >/dev/null 2>&1; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
else
  COMMAND=$(echo "$INPUT" | sed -n 's/.*"command"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)
fi

debug_log "COMMAND=$COMMAND"

# Only intercept git push commands
printf '%s' "$COMMAND" | grep -qE '\bgit\s+push\b' || exit 0

# Helper: emit the standard BLOCKED message and exit
block_push() {
  local reason="$1"
  debug_log "BLOCKED: $reason"
  cat >&2 <<'MSG'
BLOCKED: Direct push to develop/master is forbidden (CLAUDE.md rule).
All merges into develop must go through a Pull Request.
Use: git push -u origin <feature-branch> && gh pr create --base develop
MSG
  exit 2
}

# ── Phase 1: Detect dangerous flags before token parsing ──
# --all pushes ALL local branches (including protected ones).
# --mirror mirrors ALL refs and force-updates + prunes the remote.
if printf '%s' "$COMMAND" | grep -qE '(^|\s)--(all|mirror)(\s|$)'; then
  block_push "--all or --mirror flag detected"
fi

# ── Phase 2: Parse explicit refspec targets ──
PUSH_ARGS=$(printf '%s' "$COMMAND" | sed 's/.*git[[:space:]]\+push[[:space:]]*//')
PROTECTED="^(develop|master|main)$"
SKIPPED_REMOTE=false
HAS_EXPLICIT_TARGET=false

for TOKEN in $PUSH_ARGS; do
  # Skip flags like --force, -u, --set-upstream
  case "$TOKEN" in --*|-*) continue ;; esac

  # Skip the first non-flag arg (remote name, e.g. "origin")
  if [ "$SKIPPED_REMOTE" = false ]; then
    SKIPPED_REMOTE=true
    continue
  fi

  HAS_EXPLICIT_TARGET=true

  # Normalize: strip leading "+" (force-push prefix)
  NORMALIZED="${TOKEN##+}"

  # Check refspec RHS if present (e.g. HEAD:develop → develop)
  if printf '%s' "$NORMALIZED" | grep -q ':'; then
    REFSPEC_TARGET="${NORMALIZED##*:}"
    REFSPEC_TARGET="${REFSPEC_TARGET#refs/heads/}"
    if printf '%s' "$REFSPEC_TARGET" | grep -qE "$PROTECTED"; then
      block_push "refspec target '$REFSPEC_TARGET' from '$TOKEN'"
    fi
    continue
  fi

  # Strip refs/heads/ prefix (e.g. refs/heads/develop → develop)
  NORMALIZED="${NORMALIZED#refs/heads/}"

  # Check if the normalized token is a protected branch
  if printf '%s' "$NORMALIZED" | grep -qE "$PROTECTED"; then
    block_push "direct target '$NORMALIZED' from '$TOKEN'"
  fi
done

# ── Phase 3: Handle implicit push (no explicit refspec) ──
# `git push` or `git push origin` with no refspec uses push.default (typically
# "simple"), which pushes the current branch to its upstream. If the current
# branch IS a protected branch, this is an implicit push to develop/master.
if [ "$HAS_EXPLICIT_TARGET" = false ]; then
  CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
  if printf '%s' "$CURRENT_BRANCH" | grep -qE "$PROTECTED"; then
    block_push "implicit push from current branch '$CURRENT_BRANCH'"
  fi
fi

exit 0
