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
# 拉起哪个 CLI。默认 claude —— 保持迁移前的行为不变，改这个脚本不应该顺带改掉现役默认。
# Mercury Issue #571 / G5：迁到 Codex 后 /handoff auto 需要拉起 codex，
# 但两者的 prompt 传递形式不同（见下方 build 段），硬编码 claude 会让 Codex 侧静默丢掉 prompt。
HARNESS="${MERCURY_HANDOFF_HARNESS:-claude}"

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
  --harness <name>       Which CLI to launch: claude | codex
                         (default: \$MERCURY_HANDOFF_HARNESS, else claude)
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
    --harness)
      shift
      HARNESS="${1:-}"
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

# ── Validate harness ───────────────────────────────────────────────────────
case "$HARNESS" in
  claude|codex) ;;
  *)
    echo "ERROR: invalid --harness '$HARNESS' — must be 'claude' or 'codex'" >&2
    exit 2
    ;;
esac

# ── Build the harness invocation ───────────────────────────────────────────
# 两者接收初始 prompt 的形式**不同**，这是必须分开处理的原因：
#   claude -- "<prompt>"   —— 需要 `--` 把 prompt 与自身的 flag 分开
#   codex "<prompt>"       —— 位置参数直接启动交互式会话；`codex --help` 的
#                             用法行是 `codex [OPTIONS] [PROMPT]`，没有 `--`
# 若照抄 claude 的写法给 codex 加 `--`，prompt 会被当成 flag 解析而丢掉。
if [ "$HARNESS" = "claude" ]; then
  HARNESS_ARGV_SEP="--"
else
  HARNESS_ARGV_SEP=""
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
  echo "HARNESS:       $HARNESS"
  echo "SHORT_PROMPT:  $SHORT_PROMPT"
  echo "PLATFORM:      $PLATFORM ($TARGET)"
  echo ""
  if [ "$TARGET" = "windows" ]; then
    echo "COMMAND: wt -w 0 nt --title \"$TITLE_PREFIX $LANE\" -d \"$WORKTREE\" -- $HARNESS ${HARNESS_ARGV_SEP:+$HARNESS_ARGV_SEP }\"$SHORT_PROMPT\""
  else
    SHORT_PROMPT_QUOTED=$(printf '%q' "$SHORT_PROMPT")
    echo "COMMAND: tmux new-window -n handoff -c \"$WORKTREE\" \"bash -c '$HARNESS ${HARNESS_ARGV_SEP:+$HARNESS_ARGV_SEP }\\\"\\\$1\\\"' _ $SHORT_PROMPT_QUOTED\""
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
  # Windows path: invoke wt as a foreground bash command. Each argument
  # is a separate token in argv (CreateProcess form), bypassing the
  # ShellExecute single-string trap (Issue #377). Do NOT route through
  # PowerShell Start-Process: that receives the full commandline as
  # -FilePath and passes it to ShellExecute as one string, producing
  # 0x80070002 ERROR_FILE_NOT_FOUND.
  # 用数组按需追加分隔符：HARNESS_ARGV_SEP 对 codex 是空串，
  # 若直接写进 argv 会给 wt 多传一个**空参数**（不是"没有参数"）。
  HARNESS_ARGV=("$HARNESS")
  if [ -n "$HARNESS_ARGV_SEP" ]; then
    HARNESS_ARGV+=("$HARNESS_ARGV_SEP")
  fi
  HARNESS_ARGV+=("$SHORT_PROMPT")
  if wt -w 0 nt --title "$TITLE_PREFIX $LANE" -d "$WORKTREE" -- "${HARNESS_ARGV[@]}"; then
    EXIT_CODE=0
  else
    EXIT_CODE=$?
  fi
else
  # Unix path (macOS / Linux): use tmux new-window.
  # Route through bash -c so printf %q output is interpreted by bash,
  # not POSIX sh (dash/busybox), which doesn't support $'...' quoting.
  # Pass SHORT_PROMPT as a positional arg ($1) to the inner bash invocation
  # rather than interpolating it into the command string.  Defense-in-depth:
  # even though the metachar guard above rejects ;/&/|/$(`), routing through
  # $1 means the prompt never enters the bash -c command-text re-parsing path.
  #
  # Parsing layers:
  #   Layer 1 — tmux dispatches via /bin/sh -c "<outer-string>".
  #             printf %q escapes SHORT_PROMPT for this sh layer.
  #   Layer 2 — sh sees: bash -c '<harness> [--] "$1"' _ <quoted-prompt>
  #             single-quoted literal is the bash script; prompt is argv[1].
  #   Layer 3 — bash expands "$1" → prompt value; no re-parsing occurs.
  SHORT_PROMPT_QUOTED=$(printf '%q' "$SHORT_PROMPT")
  # 内层脚本文本随 harness 而变：claude 需要 `--`，codex 不需要（见上方 build 段）。
  # 这里拼的是**脚本文本**而非 argv，所以用字符串而不是数组；
  # ${VAR:+...} 保证 sep 为空时不会留下多余空格。
  INNER_SCRIPT="$HARNESS ${HARNESS_ARGV_SEP:+$HARNESS_ARGV_SEP }\"\$1\""
  if tmux new-window -n handoff -c "$WORKTREE" \
       "bash -c '$INNER_SCRIPT' _ $SHORT_PROMPT_QUOTED"; then
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
