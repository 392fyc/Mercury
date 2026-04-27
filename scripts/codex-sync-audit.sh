#!/usr/bin/env bash
# codex-sync-audit.sh — synchronous wrapper around codex-companion task runtime.
#
# Issue: https://github.com/392fyc/Mercury/issues/326
# Bypasses the codex-rescue subagent (saves ~25k tokens of forwarder overhead) and
# delivers a synchronous verdict by chaining task --background + status --wait + result.
#
# Usage:
#   codex-sync-audit.sh <prompt-file> [options]
#
# Options:
#   --timeout SECONDS         total wait budget (default 600 = 10 min)
#   --poll-interval SECONDS   polling interval (default 15)
#   --model MODEL             codex model override (e.g. gpt-5.4-mini, spark)
#   --effort EFFORT           reasoning effort (none|minimal|low|medium|high|xhigh)
#   --write                   allow codex to edit the tree (default: read-only)
#   --read-only               (default — kept as a no-op alias for clarity)
#   --cwd PATH                workspace root (default: pwd)
#   --dry-run                 print resolved commands; do not execute
#   -h | --help               show usage
#
# Exit codes:
#   0    succeeded — codex job reached terminal "completed" (codex-companion's
#        terminal-success status, per lib/codex.mjs; "succeeded" is also accepted
#        defensively). The verdict text lives in `.storedJob.result.rawOutput`
#        (or `.storedJob.rendered` for display) in the JSON payload that follows
#        the RESULT marker. PASS vs NEEDS-CHANGES is determined by parsing that
#        text, NOT by this exit code.
#   1    codex job failed (non-timeout terminal failure)
#   2    usage error
#   3    codex unavailable (CLI not found, jq missing, setup required, or JSON parse error)
#   124  wait timed out — TIMEOUT marker + status snapshot on stdout (job continues in store)
#   130  user interrupted (SIGINT/SIGTERM after dispatch — wrapper attempts to cancel codex job before exit)
#
# Stdout markers (so dual-verify can reliably parse the outcome):
#   ===CODEX-SYNC-AUDIT RESULT===   followed by `result --json` payload
#   ===CODEX-SYNC-AUDIT TIMEOUT===  followed by `status --json` snapshot
#   ===CODEX-SYNC-AUDIT FAILED===   followed by `status --json` snapshot
#
# Env overrides (used by tests; do not rely on these in production callers):
#   CODEX_COMPANION_SCRIPT  full path to codex-companion.mjs
#   CODEX_COMPANION_NODE    node executable (default: node)

set -euo pipefail

usage() {
  # Dump every leading comment line (after the shebang) until the first non-comment
  # line. Avoids hardcoded ranges so future header growth/shrink stays self-consistent.
  awk 'NR==1 && /^#!/ { next }
       /^#/ { sub(/^# ?/, ""); print; next }
       { exit }' "$0"
}

die() {
  printf 'codex-sync-audit: %s\n' "$1" >&2
  exit "${2:-2}"
}

# --- arg parsing ---
TIMEOUT_S=600
POLL_S=15
MODEL=""
EFFORT=""
# Default to read-only — this script is named codex-sync-AUDIT for a reason. Callers
# that genuinely need an edit-capable Codex run must opt in with --write.
WRITE_FLAG=""
CWD_OVERRIDE=""
DRY_RUN=0
PROMPT_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --timeout) [[ $# -ge 2 ]] || die "--timeout requires a value"; TIMEOUT_S="$2"; shift 2 ;;
    --poll-interval) [[ $# -ge 2 ]] || die "--poll-interval requires a value"; POLL_S="$2"; shift 2 ;;
    --model) [[ $# -ge 2 ]] || die "--model requires a value"; MODEL="$2"; shift 2 ;;
    --effort) [[ $# -ge 2 ]] || die "--effort requires a value"; EFFORT="$2"; shift 2 ;;
    --write) WRITE_FLAG="--write"; shift ;;
    --read-only) WRITE_FLAG=""; shift ;;
    --cwd) [[ $# -ge 2 ]] || die "--cwd requires a value"; CWD_OVERRIDE="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --) shift; break ;;
    -*) die "unknown option: $1" ;;
    *)
      if [[ -z "$PROMPT_FILE" ]]; then
        PROMPT_FILE="$1"; shift
      else
        die "unexpected positional argument: $1"
      fi
      ;;
  esac
done

[[ -n "$PROMPT_FILE" ]] || { usage >&2; exit 2; }
[[ -f "$PROMPT_FILE" ]] || die "prompt file not found: $PROMPT_FILE"
[[ -s "$PROMPT_FILE" ]] || die "prompt file is empty: $PROMPT_FILE"

case "$TIMEOUT_S" in ''|*[!0-9]*) die "--timeout must be a non-negative integer" ;; esac
case "$POLL_S" in ''|*[!0-9]*) die "--poll-interval must be a non-negative integer" ;; esac
[[ "$TIMEOUT_S" -gt 0 ]] || die "--timeout must be > 0"
[[ "$POLL_S" -gt 0 ]] || die "--poll-interval must be > 0"
# Upper bounds — guard against arithmetic overflow when multiplying by 1000 to
# build the *_MS values that go to codex-companion. 86400s (24h) is the longest
# reasonable single audit; bash arithmetic is 64-bit on modern platforms but the
# upstream node side may not be, and ms values much larger than 24h serve no
# practical purpose.
[[ "$TIMEOUT_S" -le 86400 ]] || die "--timeout must be ≤ 86400 seconds (24h)"
[[ "$POLL_S" -le "$TIMEOUT_S" ]] || die "--poll-interval ($POLL_S) must be ≤ --timeout ($TIMEOUT_S)"

# --- dependency resolution ---
NODE_BIN="${CODEX_COMPANION_NODE:-node}"
command -v "$NODE_BIN" >/dev/null 2>&1 || die "node not found in PATH (set CODEX_COMPANION_NODE to override)" 3
command -v jq >/dev/null 2>&1 || die "jq not found in PATH (required to parse codex-companion JSON)" 3

# Sort lines containing a path with a version segment (e.g. .../codex/1.0.10/scripts/...)
# in descending semver order. Read from stdin, write to stdout. Probes once at module load
# whether the local sort understands -V; if so, uses it. Otherwise falls back to an awk
# comparator that splits the version segment and compares numerically per-component.
SORT_V_OK=
sort_versions_desc() {
  if [[ -z "$SORT_V_OK" ]]; then
    if printf '1.0.2\n1.0.10\n' | sort -V >/dev/null 2>&1; then
      SORT_V_OK=1
    else
      SORT_V_OK=0
      printf 'codex-sync-audit: sort -V unavailable; using awk-based version sort fallback\n' >&2
    fi
  fi
  if [[ "$SORT_V_OK" -eq 1 ]]; then
    sort -V -r
  else
    # Extract the version directory (.../codex/<version>/scripts/codex-companion.mjs),
    # decorate with a sortable numeric key, sort descending, strip the key.
    awk -F/ '
      {
        # Find the segment immediately before "scripts" — that is the version dir.
        ver = ""
        for (i = 1; i <= NF; i++) {
          if ($i == "scripts" && i > 1) { ver = $(i-1); break }
        }
        n = split(ver, parts, ".")
        # Build a 4-field zero-padded key (1.0.10 → 0000001 0000000 0000010 0000000).
        # 4 fields covers semver MAJOR.MINOR.PATCH with optional pre-release counter.
        key = ""
        for (i = 1; i <= 4; i++) {
          v = (i <= n ? parts[i] : 0) + 0
          key = key sprintf("%07d ", v)
        }
        print key $0
      }
    ' | sort -r | sed 's/^[0-9 ]* //'
  fi
}

resolve_companion_script() {
  if [[ -n "${CODEX_COMPANION_SCRIPT:-}" ]]; then
    [[ -f "$CODEX_COMPANION_SCRIPT" ]] || die "CODEX_COMPANION_SCRIPT does not point to a file: $CODEX_COMPANION_SCRIPT" 3
    printf '%s' "$CODEX_COMPANION_SCRIPT"
    return
  fi

  # Build candidates as a true precedence chain (skip empties so an unset
  # CLAUDE_PLUGIN_ROOT does not produce a literal "/scripts/codex-companion.mjs"
  # candidate, and HOME and USERPROFILE are tried independently rather than picking
  # one — Git Bash on Windows often has both set to different paths).
  local candidates=()
  if [[ -n "${CLAUDE_PLUGIN_ROOT:-}" ]]; then
    candidates+=("$CLAUDE_PLUGIN_ROOT/scripts/codex-companion.mjs")
  fi
  # The cache path includes a plugin version segment (e.g. .../cache/openai-codex/codex/1.0.2/...)
  # that changes on every plugin upgrade — globbing the version directory lets the wrapper survive
  # codex-plugin version bumps without code edits. Newest-wins is good enough; users can always
  # set CODEX_COMPANION_SCRIPT to pin a specific path.
  # Build unique prefix list explicitly. The Claude config root is variable on this team —
  # Mercury CLAUDE.md treats `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` as the canonical user-level
  # path (so users with a non-default config dir still get plugins discovered). Treat
  # CLAUDE_CONFIG_DIR as a full config root (already includes ".claude/..."-style structure
  # if user uses it that way), but for the conventional case where CLAUDE_CONFIG_DIR is
  # *itself* the root that contains "plugins/", we map it directly. HOME and USERPROFILE
  # add their `.claude` segment as before.
  local -a config_roots=()
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    config_roots+=("$CLAUDE_CONFIG_DIR")
  fi
  if [[ -n "${HOME:-}" ]]; then
    config_roots+=("$HOME/.claude")
  fi
  if [[ -n "${USERPROFILE:-}" && "${USERPROFILE:-}" != "${HOME:-}" ]]; then
    config_roots+=("$USERPROFILE/.claude")
  fi
  local prefix
  for prefix in "${config_roots[@]}"; do
    candidates+=("$prefix/plugins/marketplaces/openai-codex/plugins/codex/scripts/codex-companion.mjs")
    # Glob each available cached version, sort by semver high-to-low, take the newest.
    # Prefer GNU/BSD `sort -V` (version sort: 1.0.10 > 1.0.2, 10.0.0 > 9.9.9). If the
    # local sort lacks -V (pre-7.0 GNU coreutils, very old BSD sort) we fall back to
    # a portable awk comparator that splits the version segment and compares each
    # numeric component. Lex sort alone would mis-order multi-digit versions and is
    # not a safe last resort here.
    local cache_glob="$prefix/plugins/cache/openai-codex/codex"
    if [[ -d "$cache_glob" ]]; then
      local versioned
      while IFS= read -r versioned; do
        [[ -f "$versioned" ]] && candidates+=("$versioned")
      done < <(find "$cache_glob" -maxdepth 3 -name codex-companion.mjs -path '*/scripts/codex-companion.mjs' 2>/dev/null | sort_versions_desc)
    fi
  done
  [[ ${#candidates[@]} -gt 0 ]] || die "no Claude config root resolvable (set CLAUDE_CONFIG_DIR, HOME, USERPROFILE, or CLAUDE_PLUGIN_ROOT) — cannot locate codex plugin" 3

  local cand
  for cand in "${candidates[@]}"; do
    if [[ -f "$cand" ]]; then
      printf '%s' "$cand"
      return
    fi
  done

  die "codex-companion.mjs not found — install the codex plugin or set CODEX_COMPANION_SCRIPT" 3
}

COMPANION_SCRIPT="$(resolve_companion_script)"
WORKSPACE_CWD="${CWD_OVERRIDE:-$PWD}"
[[ -d "$WORKSPACE_CWD" ]] || die "workspace cwd not a directory: $WORKSPACE_CWD"

run_companion() {
  # Always run companion from the workspace cwd so it associates jobs with this repo.
  ( cd "$WORKSPACE_CWD" && "$NODE_BIN" "$COMPANION_SCRIPT" "$@" )
}

# Run jq with controlled error handling. Reads JSON from $1, applies filter $2,
# exits via die() with code 3 if jq fails (so the caller never sees a raw
# `set -e` jq exit). Writes the filter result to stdout when successful.
jq_or_die() {
  local payload="$1" filter="$2" label="$3" out
  if ! out="$(printf '%s' "$payload" | jq -r "$filter" 2>/dev/null)"; then
    printf '%s\n' "$payload" >&2
    die "could not parse $label JSON (jq filter: $filter)" 3
  fi
  printf '%s' "$out"
}

# Cancellation trap: once we know the codex job id, ANY exit path that doesn't go
# through the "we observed a terminal state" route should cancel the job, otherwise
# the detached worker keeps running orphaned in the codex store. This covers:
#   - SIGINT / SIGTERM during status --wait
#   - set -e exits triggered by jq_or_die() after dispatch
#   - any other nonzero exit before TERMINAL_SEEN is set
JOB_ID=""
TERMINAL_SEEN=0
cleanup_on_exit() {
  if [[ -n "$JOB_ID" && "$TERMINAL_SEEN" -eq 0 ]]; then
    printf 'codex-sync-audit: cancelling job %s before exit\n' "$JOB_ID" >&2
    run_companion cancel "$JOB_ID" --json >/dev/null 2>&1 || true
  fi
}
cleanup_on_signal() {
  local sig="$1"
  printf 'codex-sync-audit: %s received\n' "$sig" >&2
  exit 130  # EXIT trap will run cleanup_on_exit
}
arm_cleanup_traps() {
  trap 'cleanup_on_exit' EXIT
  trap 'cleanup_on_signal SIGINT' INT
  trap 'cleanup_on_signal SIGTERM' TERM
}

# --- build dispatch argv ---
DISPATCH_ARGS=(task --background --json --prompt-file "$PROMPT_FILE")
[[ -n "$WRITE_FLAG" ]] && DISPATCH_ARGS+=("$WRITE_FLAG")
[[ -n "$MODEL" ]]      && DISPATCH_ARGS+=(--model "$MODEL")
[[ -n "$EFFORT" ]]     && DISPATCH_ARGS+=(--effort "$EFFORT")

TIMEOUT_MS=$(( TIMEOUT_S * 1000 ))
POLL_MS=$(( POLL_S * 1000 ))

if [[ "$DRY_RUN" -eq 1 ]]; then
  printf 'DRY RUN — would invoke:\n'
  printf '  cwd: %s\n' "$WORKSPACE_CWD"
  printf '  companion: %s %s\n' "$NODE_BIN" "$COMPANION_SCRIPT"
  printf '  dispatch: %q ' "${DISPATCH_ARGS[@]}"; printf '\n'
  printf '  wait: status <jobId> --wait --timeout-ms %d --poll-interval-ms %d --json\n' "$TIMEOUT_MS" "$POLL_MS"
  printf '  result: result <jobId> --json\n'
  exit 0
fi

# --- 1. dispatch ---
LAUNCH_JSON="$(run_companion "${DISPATCH_ARGS[@]}")" || die "codex-companion task --background dispatch failed (exit $?)" 3

JOB_ID="$(jq_or_die "$LAUNCH_JSON" '.jobId // empty' 'launch')"
[[ -n "$JOB_ID" ]] || {
  printf '%s\n' "$LAUNCH_JSON" >&2
  die "could not extract jobId from launch payload" 3
}

# Job is now live in the codex store; arm cleanup traps so any non-terminal exit
# (signal OR jq_or_die failure OR unexpected case) cancels the job rather than orphaning it.
arm_cleanup_traps

# --- 2. wait ---
STATUS_JSON="$(run_companion status "$JOB_ID" --wait --timeout-ms "$TIMEOUT_MS" --poll-interval-ms "$POLL_MS" --json)" || die "codex-companion status --wait failed (exit $?)" 3

WAIT_TIMED_OUT="$(jq_or_die "$STATUS_JSON" '.waitTimedOut // false' 'status')"
JOB_STATUS="$(jq_or_die "$STATUS_JSON" '.job.status // "unknown"' 'status')"

if [[ "$WAIT_TIMED_OUT" == "true" ]]; then
  # Wait timeout — the job continues running in the store. Mark TERMINAL_SEEN so
  # the EXIT trap does NOT cancel it (callers may want to refetch result later).
  TERMINAL_SEEN=1
  printf '===CODEX-SYNC-AUDIT TIMEOUT===\n'
  printf '%s\n' "$STATUS_JSON"
  exit 124
fi

# --- 3. branch on terminal status ---
# Status taxonomy per codex-companion: terminal-success uses "completed"
# (lib/codex.mjs:356); we also accept "succeeded" defensively in case upstream
# changes. Terminal-failure uses "failed" (tracked-jobs.mjs); cancelled uses
# "cancelled" (codex-companion.mjs).
case "$JOB_STATUS" in
  completed|succeeded)
    RESULT_JSON="$(run_companion result "$JOB_ID" --json)" || die "codex-companion result fetch failed (exit $?)" 3
    # Validate the payload before emitting it. We require .storedJob.id to be present
    # because callers parse .storedJob.result.rawOutput / .storedJob.rendered from
    # this same shape; a missing storedJob means the companion misbehaved and the
    # caller would otherwise silently see no verdict.
    STORED_JOB_ID="$(jq_or_die "$RESULT_JSON" '.storedJob.id // empty' 'result')"
    [[ -n "$STORED_JOB_ID" ]] || die "result payload missing .storedJob.id (companion returned malformed JSON)" 3
    TERMINAL_SEEN=1  # job ran to success — no need to cancel
    printf '===CODEX-SYNC-AUDIT RESULT===\n'
    printf '%s\n' "$RESULT_JSON"
    exit 0
    ;;
  failed|cancelled)
    TERMINAL_SEEN=1  # job ran to a terminal failure — already finalised
    printf '===CODEX-SYNC-AUDIT FAILED===\n'
    printf '%s\n' "$STATUS_JSON"
    exit 1
    ;;
  *)
    printf '===CODEX-SYNC-AUDIT FAILED===\n'
    printf '%s\n' "$STATUS_JSON"
    die "unexpected non-terminal status after wait: $JOB_STATUS" 1
    ;;
esac
