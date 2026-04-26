#!/bin/bash
# Mercury 5h-quota statusline (Issue #322 / #320, PR #321 §Dim 4.4 spec).
# Reads Claude Code statusline JSON via stdin; writes .mercury/state/auto-run-paused
# when 5h usage >= PAUSE_THRESHOLD (default 95). Two-source resume confirmation.
# REPO_ROOT via $CLAUDE_PROJECT_DIR (preferred) or git rev-parse fallback.
# See PR #321 research doc for full rationale on FLOOR-not-round + non-strict semantics.

# Note: NOT using `set -euo pipefail` here because statusline runs frequently in arbitrary cwds
# (any user shell prompt). A non-zero exit from `set -e` would break the Claude Code UI display
# in non-repo directories. Instead, every external command is wrapped with `|| true` and explicit
# null-checks below.

PAUSE_THRESHOLD=${MERCURY_PAUSE_THRESHOLD:-95}  # per Issue #320 acceptance >=95%
WARN_THRESHOLD=${MERCURY_WARN_THRESHOLD:-85}    # early-warning color only

# Resolve repo root via $CLAUDE_PROJECT_DIR (Claude Code-injected env var, set in any session
# attached to a project) FIRST; fall back to git rev-parse for non-Claude shells.
# Support MERCURY_TEST_REPO_ROOT override for test isolation.
if [ -n "${MERCURY_TEST_REPO_ROOT:-}" ]; then
  REPO_ROOT="$MERCURY_TEST_REPO_ROOT"
elif [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR/.git" ]; then
  REPO_ROOT="$CLAUDE_PROJECT_DIR"
else
  REPO_ROOT="$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || true)"
fi

if [ -z "$REPO_ROOT" ]; then
  # Outside any git repo / Mercury project: still display usage but skip marker writes.
  STATE_DIR=""
  MARKER=""
else
  STATE_DIR="$REPO_ROOT/.mercury/state"
  MARKER="$STATE_DIR/auto-run-paused"
fi

input=$(cat)

# Extract rate_limits fields (null-safe with // 0 / // "")
five_hour_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // 0')
resets_at=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // 0')
now=$(date +%s)

# FLOOR (not round) for threshold comparison — avoid early false-trigger when 94.6% rounds to 95.
# Rationale: pause should fire at "definitely >=95", not "rounds to >=95".
pct_floor=$(echo "$five_hour_pct" | cut -d. -f1)
[ -z "$pct_floor" ] && pct_floor=0

# Pause logic: write marker if FLOORED threshold exceeded (only when in a git repo)
if [ -n "$MARKER" ]; then
  if [ "$pct_floor" -ge "$PAUSE_THRESHOLD" ]; then
    mkdir -p "$STATE_DIR"
    # m3: coerce non-numeric resets_at to 0 before writing marker (defense-in-depth).
    if ! [[ "$resets_at" =~ ^[0-9]+$ ]]; then
      resets_at=0
    fi
    # Note: marker write is non-atomic (no flock for Windows MINGW portability).
    # Concurrent statusline refreshes converge within one refresh cycle.
    echo "$resets_at" > "$MARKER"
  # Resume logic: delete marker only after BOTH (a) stored window passed AND (b) current usage
  # also below threshold. Two-source confirmation reduces false-positive resume risk.
  elif [ -f "$MARKER" ]; then
    stored_reset=$(cat "$MARKER" 2>/dev/null || echo 0)
    if ! [[ "$stored_reset" =~ ^[0-9]+$ ]]; then
      echo "[statusline-mercury] Invalid pause marker; removing corrupted file." >&2
      rm -f "$MARKER"
    elif [ "$now" -ge "$stored_reset" ] && [ "$pct_floor" -lt "$PAUSE_THRESHOLD" ]; then
      # Both signals agree usage has cleared: stored window expired AND current % < threshold.
      rm -f "$MARKER"
    fi
    # If only one signal clears, leave marker in place — next refresh re-evaluates.
  fi
fi

# Display output (display rounds for readability, but pause logic uses floor)
pct_bar=$(printf '%.0f' "$five_hour_pct" 2>/dev/null || echo "?")
seven_pct=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // "?"')
model=$(echo "$input" | jq -r '.model.display_name // "?"')
ctx=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)

# Color code: green < 70%, yellow at 70 or WARN_THRESHOLD (default 85), red at PAUSE_THRESHOLD (default 95)
if [ "$pct_floor" -ge "$PAUSE_THRESHOLD" ]; then color='\033[31m'  # red — pause point
elif [ "$pct_floor" -ge "$WARN_THRESHOLD" ]; then color='\033[33m' # yellow — early warn
elif [ "$pct_floor" -ge 70 ]; then color='\033[33m'                 # yellow — soft warn
else color='\033[32m'                                                # green
fi
reset='\033[0m'

echo -e "${color}5h: ${pct_bar}%${reset} | 7d: ${seven_pct}% | ctx: ${ctx}% | ${model}"
