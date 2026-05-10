#!/usr/bin/env bash
# handoff-launch.sh — Canonical wt/tmux launcher for /handoff auto mode.
#
# Purpose: Wrap wt (Windows) or tmux (macOS/Linux) invocation so agents never
#          freeform-construct the command line. Prevents the 0x80070002
#          ERROR_FILE_NOT_FOUND failure caused by ShellExecute receiving the
#          entire commandline as a single concatenated string (program path).
#
# Mercury Issue #377 — forensic: side-bug spawn failed with
#   "S5 -d D:\Mercury\Mercury-side-bug -- claude.exe -- LANE=side-bug ..."
#   passed as a single string to ShellExecute. Fix: bash exec form to
#   CreateProcess (Windows) / tmux new-window (macOS/Linux).
#
# Usage:
#   bash scripts/handoff-launch.sh \
#     --lane <name> \
#     --worktree <path> \
#     --handoff-doc <path> \
#     [--title-prefix <str>] \
#     [--dry-run]
#
# Exit codes:
#   0  success (spawned or dry-run)
#   1  launch failed (wt/tmux returned nonzero)
#   2  argument / environment error (bad args, path not found, etc.)

set -u

# ── Trap unhandled errors ──────────────────────────────────────────────────
_on_error() {
  local lineno="${1:-?}"
  echo "ERROR: unhandled error at line $lineno" >&2
  exit 2
}
trap '_on_error $LINENO' ERR

# ── Defaults ──────────────────────────────────────────────────────────────
LANE=""
WORKTREE=""
HANDOFF_DOC=""
TITLE_PREFIX="Handoff:"
DRY_RUN=0

# ── Usage ─────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: bash scripts/handoff-launch.sh OPTIONS

Required:
  --lane <name>          Lane name (matches ^[a-z0-9][a-z0-9-]*$)
  --worktree <path>      Directory for wt -d / tmux -c (must exist)
  --handoff-doc <path>   Handoff markdown file (must exist)

Optional:
  --title-prefix <str>   wt window title prefix (default: "Handoff:")
  --dry-run              Print intended command + SHORT_PROMPT, do not spawn
  -h, --help             Show this help and exit

Exit codes:
  0  success (spawned or dry-run)
  1  launch failed (wt/tmux returned nonzero)
  2  argument / environment error
EOF
}

# ── Argument parsing ───────────────────────────────────────────────────────
if [ $# -eq 0 ]; then
  usage
  exit 2
fi

while [ $# -gt 0 ]; do
  case "$1" in
    --lane)
      shift
      LANE="${1:-}"
      ;;
    --worktree)
      shift
      WORKTREE="${1:-}"
      ;;
    --handoff-doc)
      shift
      HANDOFF_DOC="${1:-}"
      ;;
    --title-prefix)
      shift
      TITLE_PREFIX="${1:-Handoff:}"
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

# ── Validate required args ─────────────────────────────────────────────────
if [ -z "$LANE" ]; then
  echo "ERROR: --lane is required" >&2
  exit 2
fi
if [ -z "$WORKTREE" ]; then
  echo "ERROR: --worktree is required" >&2
  exit 2
fi
if [ -z "$HANDOFF_DOC" ]; then
  echo "ERROR: --handoff-doc is required" >&2
  exit 2
fi

# ── Validate lane name format ──────────────────────────────────────────────
if ! echo "$LANE" | grep -qE '^[a-z0-9][a-z0-9-]*$'; then
  echo "ERROR: invalid lane name '$LANE' — must match ^[a-z0-9][a-z0-9-]*\$" >&2
  exit 2
fi

# ── Validate worktree path ─────────────────────────────────────────────────
if [ ! -d "$WORKTREE" ]; then
  echo "ERROR: worktree path does not exist or is not a directory: $WORKTREE" >&2
  exit 2
fi

# ── Validate handoff-doc path ──────────────────────────────────────────────
if [ ! -f "$HANDOFF_DOC" ]; then
  echo "ERROR: handoff-doc does not exist or is not a file: $HANDOFF_DOC" >&2
  exit 2
fi

# ── Construct SHORT_PROMPT canonically (Δ11 contract) ─────────────────────
SHORT_PROMPT="[LANE=${LANE}] Continue from session handoff. Read ${HANDOFF_DOC} as your first action."

# ── Validate SHORT_PROMPT contains no wt/tmux metacharacters ─────────────
# Check for: ; & | $( ` (command separator, background, pipe, subst, backtick)
if echo "$SHORT_PROMPT" | grep -qE '[;|&]|\$\(|`'; then
  echo "ERROR: SHORT_PROMPT contains forbidden metacharacters (; & | \$( \`)" >&2
  echo "       SHORT_PROMPT: $SHORT_PROMPT" >&2
  exit 2
fi

# ── Detect platform ───────────────────────────────────────────────────────
PLATFORM="$(uname -s 2>/dev/null || echo "unknown")"

case "$PLATFORM" in
  MINGW*|MSYS*|CYGWIN*)
    TARGET="windows"
    ;;
  Darwin|Linux)
    TARGET="unix"
    ;;
  *)
    echo "ERROR: unsupported platform '$PLATFORM' — cannot determine wt/tmux path" >&2
    exit 2
    ;;
esac

# ── Dry-run: print and exit ────────────────────────────────────────────────
if [ "$DRY_RUN" -eq 1 ]; then
  echo "=== handoff-launch dry-run ==="
  echo "LANE:          $LANE"
  echo "WORKTREE:      $WORKTREE"
  echo "HANDOFF_DOC:   $HANDOFF_DOC"
  echo "TITLE_PREFIX:  $TITLE_PREFIX"
  echo "SHORT_PROMPT:  $SHORT_PROMPT"
  echo "PLATFORM:      $PLATFORM ($TARGET)"
  echo ""
  if [ "$TARGET" = "windows" ]; then
    echo "COMMAND: wt -w 0 nt --title \"$TITLE_PREFIX $LANE\" -d \"$WORKTREE\" -- claude -- \"$SHORT_PROMPT\""
  else
    SHORT_PROMPT_QUOTED=$(printf '%q' "$SHORT_PROMPT")
    echo "COMMAND: tmux new-window -n handoff -c \"$WORKTREE\" \"bash -c \\\"claude -- $SHORT_PROMPT_QUOTED\\\"\""
  fi
  exit 0
fi

# ── Spawn ─────────────────────────────────────────────────────────────────
# Use if-then-else form so the ERR trap (set -e / trap ERR) is suppressed
# for the wt/tmux call per bash spec: commands in the test position of an
# if statement are exempt from the ERR trap.  This ensures a real launch
# failure reaches the EXIT_CODE branch and exits 1 (launch failure) rather
# than 2 (unhandled error) via the trap.
if [ "$TARGET" = "windows" ]; then
  # Windows path: invoke wt directly via bash exec — each argument is a
  # separate token, bypassing ShellExecute single-string trap (Issue #377).
  # Do NOT route through PowerShell Start-Process: that receives the full
  # commandline as -FilePath and passes it to ShellExecute as one string,
  # producing 0x80070002 ERROR_FILE_NOT_FOUND.
  if wt -w 0 nt --title "$TITLE_PREFIX $LANE" -d "$WORKTREE" -- claude -- "$SHORT_PROMPT"; then
    EXIT_CODE=0
  else
    EXIT_CODE=$?
  fi
else
  # Unix path (macOS / Linux): use tmux new-window.
  # Route through bash -c so printf %q output is interpreted by bash,
  # not POSIX sh (dash/busybox), which doesn't support $'...' quoting.
  SHORT_PROMPT_QUOTED=$(printf '%q' "$SHORT_PROMPT")
  if tmux new-window -n handoff -c "$WORKTREE" \
       "bash -c \"claude -- $SHORT_PROMPT_QUOTED\""; then
    EXIT_CODE=0
  else
    EXIT_CODE=$?
  fi
fi

if [ "$EXIT_CODE" -ne 0 ]; then
  echo "ERROR: launch failed (exit $EXIT_CODE)" >&2
  exit 1
fi

echo "handoff-launch: spawned new tab for lane '$LANE' at '$WORKTREE'"
exit 0
