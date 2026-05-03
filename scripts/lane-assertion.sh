#!/usr/bin/env bash
# lane-assertion.sh — three-way lane consistency check at session start.
#
# Verifies the agent session is bound to a single, coherent lane identity by
# asserting alignment across three independent sources:
#
#   1. [LANE=<name>] marker — first whitespace-delimited token of the user's
#      first prompt (or BOOTSTRAP_PROMPT env var). Set by the /handoff:auto
#      skill on the SHORT_PROMPT, per `feedback_handoff_short_prompt_only.md`
#      lessons learned.
#   2. cwd-encoded — Claude Code derives `~/.claude/projects/<encoded-cwd>/`
#      from the cwd of the `claude` invocation (slash-to-dash encoding,
#      empirically observed; reference:
#      https://code.claude.com/docs/en/claude-directory). The encoded form
#      MUST match the lane's worktree path encoded.
#   3. git current branch — MUST match the lane's branch-prefix convention:
#      - main: `feature/lane-main/TASK-<N>-*` OR `feature/TASK-<N>-*` (legacy)
#        OR `lane/main/<N>-<slug>` (Rule 2.1, future) OR `develop` itself
#        (a session can run on develop while not yet branched).
#      - side: `feature/lane-<lane>/TASK-<N>-*` (legacy) OR
#        `lane/<short>/<N>-<slug>` (Rule 2.1) OR `lane/<short>/init`
#        (scaffold per lane-naming.md §Setup at lane open).
#
# If all three align → exit 0. Mismatch → fail loud with explicit guidance.
# Soft-disable via MERCURY_LANE_ASSERT_DISABLED=1 for break-glass scenarios.
#
# Usage:
#   scripts/lane-assertion.sh [--marker '[LANE=name]'] [--memory-dir PATH]
#                             [--lanes-file PATH] [--repo-root PATH]
#                             [--cwd PATH] [--branch NAME] [--format text|json]
#
# Exit codes:
#   0 — alignment OK
#   1 — marker missing / unparseable
#   2 — cwd mismatch (worktree path drift — Issue #342 routing-bleed risk)
#   3 — branch prefix mismatch
#   4 — Worktree path field missing in LANES.md (Rule 5.1 violation)
#   5 — argument or environment error
#   6 — Worktree path field has duplicate bullets in LANES.md
#
# Marker resolution priority:
#   --marker CLI flag
#   $MERCURY_LANE_MARKER env var
#   $BOOTSTRAP_PROMPT env var (parsed for first token)
#   stdin (parsed for first token)
#
# Reference docs:
#   - .mercury/docs/guides/lane-naming.md §Lane workspace isolation
#   - feedback_lane_protocol.md Rule 5.1 (Per-lane state separation)
#   - Issue #342 (routing-bleed forensic record)
#   - Issue #345 (this assertion implementation)

set -u

PROG="lane-assertion.sh"

die() {
  printf '%s: error: %s\n' "$PROG" "$1" >&2
  exit "${2:-5}"
}

warn() {
  printf '%s: warn: %s\n' "$PROG" "$1" >&2
}

# JSON-string escape: backslash (Windows paths!) and double-quote, plus
# minimal control-char handling. Pure bash parameter expansion, no fork.
# Closes Argus iter1 Minor #346 — `--format json` previously interpolated
# raw strings into the output, producing invalid JSON for Windows paths
# containing backslashes or any value containing literal quotes. Defined
# at script top so all error-branch JSON printers (worktree_path_missing
# at line ~232, worktree_path_duplicate at line ~250, etc.) see it
# regardless of execution path. Fixes Argus iter2 Minor "潜在运行错误".
json_escape() {
  local s="$1"
  s="${s//\\/\\\\}"   # \ → \\  (must be FIRST so \ in subsequent escapes survives)
  s="${s//\"/\\\"}"   # " → \"
  s="${s//$'\n'/\\n}" # newline → \n  (defensive — paths/branches shouldn't contain LF)
  s="${s//$'\r'/\\r}" # CR → \r
  s="${s//$'\t'/\\t}" # tab → \t
  printf '%s' "$s"
}

# Soft-disable break-glass — exits cleanly without running checks.
if [ "${MERCURY_LANE_ASSERT_DISABLED:-}" = "1" ]; then
  printf '%s: soft-disabled via MERCURY_LANE_ASSERT_DISABLED=1\n' "$PROG"
  exit 0
fi

# ── argv parsing ────────────────────────────────────────────────
MARKER_ARG=""
MEMORY_DIR=""
LANES_FILE=""
REPO_ROOT=""
CWD_OVERRIDE=""
BRANCH_OVERRIDE=""
FORMAT="text"

while [ $# -gt 0 ]; do
  case "$1" in
    --marker)      shift; [ $# -gt 0 ] || die "--marker needs a value" 5
                   MARKER_ARG="$1" ;;
    --memory-dir)  shift; [ $# -gt 0 ] || die "--memory-dir needs a value" 5
                   [ -n "$1" ] || die "--memory-dir requires a non-empty path" 5
                   MEMORY_DIR="$1" ;;
    --lanes-file)  shift; [ $# -gt 0 ] || die "--lanes-file needs a value" 5
                   [ -n "$1" ] || die "--lanes-file requires a non-empty path" 5
                   LANES_FILE="$1" ;;
    --repo-root)   shift; [ $# -gt 0 ] || die "--repo-root needs a value" 5
                   REPO_ROOT="$1" ;;
    --cwd)         shift; [ $# -gt 0 ] || die "--cwd needs a value" 5
                   CWD_OVERRIDE="$1" ;;
    --branch)      shift; [ $# -gt 0 ] || die "--branch needs a value" 5
                   BRANCH_OVERRIDE="$1" ;;
    --format)      shift; [ $# -gt 0 ] || die "--format needs a value" 5
                   case "$1" in text|json) FORMAT="$1" ;;
                                *) die "--format: unknown value '$1' (text|json)" 5 ;; esac ;;
    -h|--help)
      sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
      exit 0 ;;
    *) die "unknown argument: $1" 5 ;;
  esac
  shift
done

# ── memory dir + LANES.md resolution (mirrors lane-cap-check.sh) ─
if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
[ -d "$MEMORY_DIR" ] || die "memory dir not found: $MEMORY_DIR (set --memory-dir or MERCURY_MEMORY_DIR)" 5
[ -z "$LANES_FILE" ] && LANES_FILE="$MEMORY_DIR/LANES.md"
[ -f "$LANES_FILE" ] || die "LANES.md not found: $LANES_FILE" 5

# ── marker extraction ───────────────────────────────────────────
# Try each source in priority order; the FIRST source that yields a
# parseable [LANE=<name>] token wins. A non-empty but malformed earlier
# source does NOT block fallback to a later source — the contract is
# documented as "first source that yields a parseable token wins"
# (lane-naming.md §Δ11). Fixes Codex iter1 Low #1 (audit 2026-05-03).
_try_parse_marker() {
  # stdout: lane name on success; empty on failure. exit code: 0 success / 1 fail.
  local raw="$1" first inner
  [ -z "$raw" ] && return 1
  # Per contract (lane-naming.md §Δ11): the marker MUST be the FIRST
  # whitespace-delimited token. Skip leading whitespace-only lines so
  # `BOOTSTRAP_PROMPT` / stdin that wrap the marker after a blank line
  # are still accepted (Codex iter3 Low #1). The first non-blank line's
  # `$1` is the first whitespace-delimited token; subsequent lines are
  # not consulted (the contract is about the FIRST token, not anywhere).
  first=$(printf '%s' "$raw" | awk '
    /^[[:space:]]*$/ { next }
    { if ($1 ~ /^\[LANE=[a-z0-9-]+\]$/) print $1; exit }')
  [ -z "$first" ] && return 1
  inner="${first#\[LANE=}"
  inner="${inner%\]}"
  case "$inner" in
    ''|*[!a-z0-9-]*) return 1 ;;  # defensive — awk regex already enforced
  esac
  printf '%s' "$inner"
  return 0
}

LANE_NAME=""
for _src in "$MARKER_ARG" "${MERCURY_LANE_MARKER:-}" "${BOOTSTRAP_PROMPT:-}"; do
  parsed=$(_try_parse_marker "$_src") && { LANE_NAME="$parsed"; break; }
done

# Stdin fallback — only consulted if every explicit source failed AND
# stdin is connected to data (not a terminal).
if [ -z "$LANE_NAME" ] && [ ! -t 0 ]; then
  STDIN_DATA=$(cat)
  parsed=$(_try_parse_marker "$STDIN_DATA") && LANE_NAME="$parsed"
fi

if [ -z "$LANE_NAME" ]; then
  if [ "$FORMAT" = "json" ]; then
    printf '{"verdict":"missing_marker","exit":1,"reason":"no parseable [LANE=<name>] marker found in --marker / MERCURY_LANE_MARKER / BOOTSTRAP_PROMPT / stdin"}\n'
  else
    cat >&2 <<EOF
$PROG: BLOCKED — no parseable [LANE=<name>] marker found.
Expected: [LANE=<name>] as the first whitespace-delimited token of the
session's bootstrap prompt (set by /handoff:auto SHORT_PROMPT). Sources
checked, in order: --marker / MERCURY_LANE_MARKER / BOOTSTRAP_PROMPT / stdin.
Soft-disable: export MERCURY_LANE_ASSERT_DISABLED=1
Reference: .mercury/docs/guides/lane-naming.md §Lane workspace isolation Δ11
EOF
  fi
  exit 1
fi

# ── extract Worktree path from LANES.md for this lane ───────────
# LANES.md format: per-lane section starts at `### \`<name>\`` heading.
# `Worktree path` field appears as a `- **Worktree path** [optional notes]: \`<PATH>\``
# bullet. The canonical form is BACKTICK-quoted; parsing prefers backticks
# as the path delimiter so paths containing whitespace or `(` (e.g.
# `/Users/Alice Smith/...`, `C:/Program Files (x86)/...`) survive intact.
# Without backticks, the parser falls back to "rest of line, trimmed" —
# legacy/freeform but still single-line. Fixes Codex iter1 High #1.
#
# Duplicate detection: every match emits one line. Shell counts the lines —
# >1 means the lane section has multiple Worktree path bullets, which is
# ambiguous and unsafe to silently first-wins. Fixes Codex iter1 High #2.
extract_worktree_path() {
  awk -v lane="$1" '
  BEGIN { in_section=0; in_fence=0 }
  /^```/ { in_fence = !in_fence; next }
  in_fence { next }
  /^### `[^`]+`/ {
    match($0, /^### `[^`]+`/)
    hdr=substr($0, RSTART+5, RLENGTH-6)
    in_section=(hdr == lane) ? 1 : 0
    next
  }
  in_section && /\*\*Worktree path\*\*/ {
    s=$0
    i=index(s, "**Worktree path**")
    s=substr(s, i+length("**Worktree path**"))
    # Drop optional " (notes...)" parenthesized block ONLY when it appears
    # immediately before the colon (the LANES.md convention).
    if (substr(s, 1, 2) == " (") {
      end=index(s, ")")
      if (end > 0) s=substr(s, end+1)
    }
    c=index(s, ":")
    if (c == 0) next
    s=substr(s, c+1)
    sub(/^[[:space:]]+/, "", s)
    if (substr(s, 1, 1) == "`") {
      # Backtick-quoted (canonical). Take everything up to closing backtick,
      # then trim trailing whitespace inside the quoted region — protects
      # against invisible-typo paths like `` `/path ` `` (Codex iter2 M#1).
      s=substr(s, 2)
      bt=index(s, "`")
      if (bt > 0) {
        out=substr(s, 1, bt-1)
        sub(/[[:space:]]+$/, "", out)
        if (out != "") print out
      }
    } else {
      # Unquoted (legacy/freeform). Take rest of line, strip trailing space.
      sub(/[[:space:]]+$/, "", s)
      if (s != "") print s
    }
  }
  ' "$2"
}

WORKTREE_PATH_RAW=$(extract_worktree_path "$LANE_NAME" "$LANES_FILE")
if [ -z "$WORKTREE_PATH_RAW" ]; then
  if [ "$FORMAT" = "json" ]; then
    printf '{"verdict":"worktree_path_missing","exit":4,"lane":"%s","reason":"no Worktree path field in LANES.md section"}\n' "$(json_escape "$LANE_NAME")"
  else
    cat >&2 <<EOF
$PROG: BLOCKED — lane '$LANE_NAME' has no Worktree path field in LANES.md.
Per feedback_lane_protocol.md Rule 5.1 (landed S15-side-multi-lane Issue
#342), every active lane MUST declare a Worktree path field. Add the
field to the lane's section in:
  $LANES_FILE
Soft-disable: export MERCURY_LANE_ASSERT_DISABLED=1
EOF
  fi
  exit 4
fi
# Reject duplicate entries — first-wins would let a stale bullet route
# the session to an old worktree.
WORKTREE_COUNT=$(printf '%s\n' "$WORKTREE_PATH_RAW" | grep -c '^.')
if [ "$WORKTREE_COUNT" -gt 1 ]; then
  if [ "$FORMAT" = "json" ]; then
    printf '{"verdict":"worktree_path_duplicate","exit":6,"lane":"%s","count":%d}\n' \
      "$(json_escape "$LANE_NAME")" "$WORKTREE_COUNT"
  else
    cat >&2 <<EOF
$PROG: BLOCKED — lane '$LANE_NAME' has $WORKTREE_COUNT Worktree path bullets in LANES.md.
Multiple entries are ambiguous and a stale first-bullet could silently route
the session to an old worktree. Edit the lane section to keep exactly ONE
Worktree path bullet:
  $LANES_FILE
Soft-disable: export MERCURY_LANE_ASSERT_DISABLED=1
EOF
  fi
  exit 6
fi
WORKTREE_PATH="$WORKTREE_PATH_RAW"

# ── cwd encoding check ──────────────────────────────────────────
# Claude Code's encoding rule (per claude-directory docs + lane-naming.md):
# slash and colon characters in the cwd become dashes, leading dashes are
# stripped. Encode both the actual cwd and the expected worktree path,
# then compare.
encode_path() {
  # Defensive normalization before encoding to dampen trivial-mismatch
  # false-blocks: strip trailing slashes (`D:/Mercury/Mercury/` ≡
  # `D:/Mercury/Mercury`). Case normalization is intentionally skipped —
  # macOS/Linux paths are case-sensitive and silently lowercasing them
  # would mask real divergence on those platforms. Windows operators
  # whose worktree-path field disagrees with cwd by case alone should
  # fix the field, not lean on the assertion. Codex iter1 Medium #1.
  local p="$1"
  # Trim ALL trailing slashes (and Windows backslashes) to handle e.g.
  # "D:/Mercury/Mercury//" — repeat-strip via parameter expansion.
  while :; do
    case "$p" in
      */|*\\) p="${p%/}"; p="${p%\\}" ;;
      *) break ;;
    esac
  done
  printf '%s' "$p" | tr ':\\/' '-' | sed 's/^-*//'
}

ACTUAL_CWD="${CWD_OVERRIDE:-$PWD}"
ACTUAL_CWD_ENC=$(encode_path "$ACTUAL_CWD")
EXPECTED_CWD_ENC=$(encode_path "$WORKTREE_PATH")

if [ "$ACTUAL_CWD_ENC" != "$EXPECTED_CWD_ENC" ]; then
  if [ "$FORMAT" = "json" ]; then
    printf '{"verdict":"cwd_mismatch","exit":2,"lane":"%s","actual_cwd":"%s","expected_worktree":"%s"}\n' \
      "$(json_escape "$LANE_NAME")" "$(json_escape "$ACTUAL_CWD")" "$(json_escape "$WORKTREE_PATH")"
  else
    cat >&2 <<EOF
$PROG: BLOCKED — cwd does not match lane '$LANE_NAME' worktree path.
  actual cwd:        $ACTUAL_CWD
  expected worktree: $WORKTREE_PATH
This is the share-cwd routing-bleed failure mode (Issue #342). The session
is reading the wrong lane's project state (~/.claude/projects/<encoded>/).
Resolution: cd to the lane worktree before launching claude:
  cd "$WORKTREE_PATH" && claude
Soft-disable: export MERCURY_LANE_ASSERT_DISABLED=1
EOF
  fi
  exit 2
fi

# ── branch prefix check ─────────────────────────────────────────
# Resolve current branch. If --branch override given, use it; else query git.
ACTUAL_BRANCH="$BRANCH_OVERRIDE"
if [ -z "$ACTUAL_BRANCH" ]; then
  if [ -n "$REPO_ROOT" ]; then
    ACTUAL_BRANCH=$(git -C "$REPO_ROOT" branch --show-current 2>/dev/null || true)
  else
    ACTUAL_BRANCH=$(git branch --show-current 2>/dev/null || true)
  fi
fi

# Branch validation rules per lane (matches lane-naming.md §Δ6 + main lane
# legacy convention). Detached HEAD and an empty branch (e.g. before any
# checkout) are tolerated as warn — not a fatal mismatch — to avoid
# blocking sessions that legitimately operate without a branch.
branch_ok=0
branch_reason=""
if [ -z "$ACTUAL_BRANCH" ]; then
  warn "git current branch is empty (detached HEAD or non-git cwd) — branch check skipped"
  branch_ok=1
elif [ "$LANE_NAME" = "main" ]; then
  case "$ACTUAL_BRANCH" in
    develop|master|main) branch_ok=1 ;;
    feature/lane-main/TASK-*|feature/TASK-*) branch_ok=1 ;;
    lane/main/*) branch_ok=1 ;;
    *) branch_reason="main lane expects develop / feature/lane-main/* / feature/TASK-* / lane/main/*; got '$ACTUAL_BRANCH'" ;;
  esac
else
  # For side lanes, derive the short name. Convention: <name> with -multi-
  # contractions (e.g. side-multi-lane → side-mlane). Lookup falls back to
  # exact match against branch prefix `lane/<lane-name>/...` if no Short name
  # field is found in LANES.md.
  SHORT_NAME=$(awk -v lane="$LANE_NAME" '
  BEGIN { in_section=0; in_fence=0 }
  /^```/ { in_fence = !in_fence; next }
  in_fence { next }
  /^### `[^`]+`/ {
    match($0, /^### `[^`]+`/)
    hdr=substr($0, RSTART+5, RLENGTH-6)
    in_section=(hdr == lane) ? 1 : 0
    next
  }
  in_section && /\*\*Short name\*\*/ {
    s=$0
    i=index(s, "**Short name**")
    s=substr(s, i+length("**Short name**"))
    c=index(s, ":")
    if (c == 0) next
    s=substr(s, c+1)
    sub(/^[[:space:]]+/, "", s)
    if (substr(s, 1, 1) == "`") {
      # Backtick-quoted (canonical form per Rule 2.1 examples).
      s=substr(s, 2)
      bt=index(s, "`")
      if (bt > 0) { print substr(s, 1, bt-1); exit }
    } else {
      # Unquoted fallback — short name is `[a-z0-9-]+` per Rule 2.1, so the
      # first whitespace/paren is a safe terminator (the char class explicitly
      # excludes both).
      for (i=1; i<=length(s); i++) {
        ch=substr(s,i,1)
        if (ch == " " || ch == "\t" || ch == "`" || ch == "(") break
      }
      if (i > 1) { print substr(s, 1, i-1); exit }
    }
  }
  ' "$LANES_FILE")
  # Without short name, fall back to lane name itself for branch matching.
  [ -z "$SHORT_NAME" ] && SHORT_NAME="$LANE_NAME"
  # Side-lane branch tolerance: only `develop` is the canonical pre-checkout
  # state (lane-naming.md §Setup at lane open uses `git worktree add ...
  # origin/develop` to scaffold). master/main are NOT legitimate side-lane
  # operating branches — accepting them would mean a misrouted session on
  # those branches passes the assertion. Tightened per Argus iter2 Minor
  # "校验放宽风险" (PR #346). Scaffold + work branches remain accepted via
  # the lane/<short>/* and feature/lane-<lane>/TASK-* patterns below.
  case "$ACTUAL_BRANCH" in
    develop) branch_ok=1 ;;  # canonical pre-checkout state for fresh side worktree
    "feature/lane-${LANE_NAME}/TASK-"*) branch_ok=1 ;;
    "lane/${SHORT_NAME}/"*) branch_ok=1 ;;
    *) branch_reason="lane '$LANE_NAME' (short '$SHORT_NAME') expects develop / feature/lane-${LANE_NAME}/TASK-* / lane/${SHORT_NAME}/*; got '$ACTUAL_BRANCH'" ;;
  esac
fi

if [ "$branch_ok" -ne 1 ]; then
  if [ "$FORMAT" = "json" ]; then
    printf '{"verdict":"branch_mismatch","exit":3,"lane":"%s","actual_branch":"%s","reason":"%s"}\n' \
      "$(json_escape "$LANE_NAME")" "$(json_escape "$ACTUAL_BRANCH")" "$(json_escape "$branch_reason")"
  else
    cat >&2 <<EOF
$PROG: BLOCKED — branch does not match lane '$LANE_NAME' convention.
  $branch_reason
Reference: .mercury/docs/guides/lane-naming.md §Δ6 short branch prefix
Soft-disable: export MERCURY_LANE_ASSERT_DISABLED=1
EOF
  fi
  exit 3
fi

# ── all checks passed ───────────────────────────────────────────
if [ "$FORMAT" = "json" ]; then
  printf '{"verdict":"aligned","exit":0,"lane":"%s","cwd":"%s","branch":"%s","worktree":"%s"}\n' \
    "$(json_escape "$LANE_NAME")" "$(json_escape "$ACTUAL_CWD")" "$(json_escape "$ACTUAL_BRANCH")" "$(json_escape "$WORKTREE_PATH")"
else
  printf '%s: aligned — lane=%s cwd=%s branch=%s\n' "$PROG" "$LANE_NAME" "$ACTUAL_CWD" "$ACTUAL_BRANCH"
fi
exit 0
