#!/usr/bin/env bash
# Armed auto-handoff Stop hook — Mercury Issue #469.
#
# Problem (recurring): in /handoff auto, the agent writes the doc + prints the
# Starting Prompt, then STOPS without running the launcher → no new session
# spawns. Behavioral SKILL.md banners (#470) are a stopgap; per the user's own
# CLAUDE.md principle ("end-of-turn automation must be a hook, not model
# memory"), only a mechanical Stop-hook reliably forces the handoff.
#
# Mechanism:
#   - /handoff auto ARMS a pending launch by writing the flag below (lane /
#     handoff_doc / worktree) BEFORE printing the prompt. The normal path then
#     runs `handoff-launch.sh && rm -f <flag>` atomically, so a complying agent
#     leaves no flag → this hook no-ops.
#   - If the agent stops while still armed (the bug), this hook mechanically
#     runs the canonical launcher and disarms (model B). On launcher failure it
#     blocks-with-reason ONCE so the agent can retry with full context
#     (model A fallback); a second armed stop is then allowed (own retry marker)
#     to avoid an infinite loop / runaway spawn.
#
# Stale-flag safety (every terminal path must end armed=false unless a live
# launch is pending): a flag can leak across sessions if a path exits while
# armed. Three guards prevent a leaked flag from later ghost-spawning:
#   (1) break-glass disarms instead of pure no-op;
#   (2) an mtime staleness guard disarms flags older than the live-turn window;
#   (3) the staged-change deferral is bypass-aware — it only defers when
#       stop-guard.sh will actually block (interactive mode); in bypass /
#       dontAsk mode stop-guard does NOT block, so we must not leave a stale
#       flag — fall through and launch.
#
# Zero hard deps beyond sed/git + the shared permission-mode.sh lib (same one
# stop-guard.sh uses). Break-glass: MERCURY_AUTO_HANDOFF_STOP_DISABLED=1.

_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$_PROJECT" 2>/dev/null || exit 0

FLAG="$_PROJECT/.mercury/state/auto-handoff-armed"
RETRY="$_PROJECT/.mercury/state/auto-handoff-retried"
[ -f "$FLAG" ] || { rm -f "$RETRY" 2>/dev/null; exit 0; }   # not armed → allow stop

# Break-glass: disable the mechanism AND disarm, so a pending flag cannot leak
# into a later session and ghost-spawn (Codex audit M2).
if [ "${MERCURY_AUTO_HANDOFF_STOP_DISABLED:-}" = "1" ]; then
  rm -f "$FLAG" "$RETRY"
  echo "auto-handoff: disabled via MERCURY_AUTO_HANDOFF_STOP_DISABLED — disarmed pending flag." >&2
  exit 0
fi

STOP_INPUT=$(cat 2>/dev/null || true)

# Staleness guard: an armed turn fires within minutes; a flag older than the
# live-turn window is a cross-session leak, not a live handoff. Disarm, never
# launch (defense-in-depth against any path that leaves a flag armed).
if [ -n "$(find "$FLAG" -mmin +120 2>/dev/null)" ]; then
  rm -f "$FLAG" "$RETRY"
  echo "auto-handoff: armed flag is stale (>120m, likely a prior session) — disarmed, not launching." >&2
  exit 0
fi

# Defer to stop-guard.sh ONLY when it will actually block. With changes staged
# but uncommitted, stop-guard blocks in interactive mode (agent commits, the
# next stop re-enters here cleanly) — but in bypass/dontAsk mode it does NOT
# block, so deferring would strand a stale flag (Codex audit H1). In that case
# fall through and launch.
if [ -n "$(git diff --cached --name-only 2>/dev/null)" ]; then
  # shellcheck source=/dev/null
  . "$(dirname "$0")/lib/permission-mode.sh" 2>/dev/null || true
  PERM_MODE=""
  if command -v get_permission_mode >/dev/null 2>&1; then
    PERM_MODE=$(get_permission_mode "$STOP_INPUT")
  fi
  if command -v is_bypass_mode >/dev/null 2>&1 && is_bypass_mode "$PERM_MODE"; then
    : # bypass mode: stop-guard won't block → fall through to launch (no stale flag)
  else
    exit 0   # interactive: stop-guard will block; we launch after the agent commits
  fi
fi

# Loop guard (own marker, NOT stop_hook_active — stop-guard sets that flag when
# it blocks for uncommitted changes, which would wrongly trip us). If we already
# intervened once, disarm and allow the stop so we never spin / spawn twice.
if [ -f "$RETRY" ]; then
  rm -f "$FLAG" "$RETRY"
  echo "WARNING: auto-handoff launcher still not complete after one retry — disarmed to avoid loop. Run scripts/handoff-launch.sh manually." >&2
  exit 0
fi

# Parse the flag (key=value).
_get() { sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$FLAG" | head -1; }
LANE=$(_get lane)
HANDOFF_DOC=$(_get handoff_doc)
WORKTREE=$(_get worktree)

# Incomplete arm or missing doc → cannot safely launch; disarm + allow stop.
if [ -z "$LANE" ] || [ -z "$HANDOFF_DOC" ] || [ -z "$WORKTREE" ] || [ ! -f "$HANDOFF_DOC" ]; then
  rm -f "$FLAG" "$RETRY"
  echo "WARNING: auto-handoff armed but flag incomplete or handoff doc missing — disarmed, not launching." >&2
  exit 0
fi

# Mechanical launch via the canonical, dual-verified launcher (#377).
if LAUNCH_OUT=$(bash "$_PROJECT/scripts/handoff-launch.sh" \
      --lane "$LANE" --worktree "$WORKTREE" --handoff-doc "$HANDOFF_DOC" 2>&1); then
  rm -f "$FLAG" "$RETRY"
  echo "auto-handoff: launcher spawned new session (#469 armed Stop-hook): ${LAUNCH_OUT}" >&2
  exit 0
fi

# Launcher failed: mark one retry + block ONCE so the agent retries with full
# tool context. Static reason string → always valid JSON (Windows paths contain
# backslashes and MUST NOT be interpolated into JSON). Next armed stop hits the
# retry guard above and is allowed.
: > "$RETRY"
echo "auto-handoff launcher failed: ${LAUNCH_OUT}" >&2
echo '{"decision":"block","reason":"Auto-handoff is ARMED but scripts/handoff-launch.sh failed. Read .mercury/state/auto-handoff-armed for lane/worktree/handoff_doc, run scripts/handoff-launch.sh with those args, then delete that flag file. Do NOT stop until the new session is spawned (see stderr for the launcher error)."}'
exit 0
