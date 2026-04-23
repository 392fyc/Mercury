#!/usr/bin/env bash
# cleanup-worktree-branch.sh — shared worktree + branch cleanup (dev-pipeline Phase 5 + pr-flow
# Phase 7). SoT for the inline blocks that diverged in S62 (see Mercury #274).
# Usage: cleanup-worktree-branch.sh <BRANCH> <BASE_BRANCH> [--force] [--worktree-path PATH] [--dry-run]
#   --force        dev-pipeline: retry + rm -rf whitelist + unconditional `git branch -d`
#   (no flag)      pr-flow safe mode: preserve dirty worktrees, gated branch delete
#   --worktree-path PATH  skip auto-discovery
#   --dry-run      print destructive commands without executing
# Exit: 0 complete | 1 worktree cleanup incomplete | 2 invalid args / not in a git repo

set -u

die()  { echo "cleanup-worktree-branch: $1" >&2; exit 2; }
warn() { echo "WARN: $1" >&2; }

BRANCH=""
BASE_BRANCH=""
FORCE=0
DRY_RUN=0
EXPLICIT_WT_PATH=""

while [ $# -gt 0 ]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --worktree-path)
      [ $# -ge 2 ] || die "--worktree-path requires a value"
      EXPLICIT_WT_PATH="$2"
      shift 2
      ;;
    -h|--help)
      sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --) shift; break ;;
    -*)
      die "unknown flag: $1"
      ;;
    *)
      if [ -z "$BRANCH" ]; then
        BRANCH="$1"
      elif [ -z "$BASE_BRANCH" ]; then
        BASE_BRANCH="$1"
      else
        die "too many positional arguments; got: $1"
      fi
      shift
      ;;
  esac
done

[ -n "$BRANCH" ] || die "missing BRANCH argument"
[ -n "$BASE_BRANCH" ] || die "missing BASE_BRANCH argument"

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository"
# Canonical form: resolve symlinks and `..` so the rm -rf whitelist cannot be bypassed by
# a caller passing e.g. "$REPO_ROOT/.worktrees/../etc" that textually matches the prefix.
REPO_ROOT_CANON=$(cd "$REPO_ROOT" && pwd -P) || die "cannot canonicalize REPO_ROOT"
MAIN_WT="$REPO_ROOT"

# Cross-repo observability (per Mercury feedback_cross_repo_declare): print repo + remote so
# a user or log reader can verify at a glance which repo this invocation is operating on.
# Strip any embedded credentials (`https://user:token@host/...` or `ssh://user@host/...`) before
# logging — the remote URL may contain secrets and this runs in CI/terminal scrollback contexts.
REPO_REMOTE=$(git remote get-url origin 2>/dev/null || echo "(no origin)")
# Redact credential-bearing userinfo. Rule 1: any userinfo after a URL scheme (covers both
# https://token@ and https://u:p@). Rule 2: SCP-like `user:pass@host:path` (requires `:` inside
# userinfo so plain `git@host:path` SSH — no credentials — passes through unchanged).
REPO_REMOTE_SAFE=$(printf '%s' "$REPO_REMOTE" \
  | sed -E 's#([a-z][a-z0-9+.-]*://)[^/@[:space:]]+@#\1[REDACTED]@#g' \
  | sed -E 's#^[^/@[:space:]]+:[^/@[:space:]]+@#[REDACTED]@#')
echo "cleanup-worktree-branch: repo=$REPO_ROOT remote=$REPO_REMOTE_SAFE branch=$BRANCH base=$BASE_BRANCH force=$FORCE dry-run=$DRY_RUN" >&2

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] $*"
    return 0
  fi
  "$@"
}

# Discover worktrees BEFORE pre-switch: `git switch` changes the main worktree's branch
# association in `git worktree list --porcelain`, altering discovery results.
WT_FAIL=0
WT_PATHS=""
if [ -n "$EXPLICIT_WT_PATH" ]; then
  # --worktree-path is high-risk user input. Reject newlines so a value cannot expand into
  # multiple paths inside the while-read loop below.
  case "$EXPLICIT_WT_PATH" in
    *$'\n'*|*$'\r'*) die "--worktree-path must be a single-line path" ;;
  esac
  WT_PATHS="$EXPLICIT_WT_PATH"
else
  if ! WT_LIST=$(git worktree list --porcelain 2>&1); then
    warn "git worktree list failed — skipping worktree removal"
    if [ "$FORCE" -eq 1 ]; then
      WT_FAIL=1
      WT_PATHS=""
    else
      exit 1
    fi
  else
    WT_PATHS=$(printf '%s\n' "$WT_LIST" | awk -v ref="branch refs/heads/$BRANCH" '
      /^worktree / { path = substr($0, 10) }
      $0 == ref { print path }
    ')
  fi
fi
# Pre-switch off BRANCH if HEAD is on it (required before branch -d).
if [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "$BRANCH" ]; then
  if ! run git switch -- "$BASE_BRANCH" 2>/dev/null; then
    # NOTE: `git checkout` fallback uses `refs/heads/$BASE_BRANCH` rather than a bare
    # branch name for two reasons:
    #   1. `git checkout -- "$BASE_BRANCH"` would trigger pathspec mode (`--` is a pathspec
    #      separator for checkout, unlike for switch), so the `--` form is not usable here.
    #   2. A bare branch name that happens to start with `-` would be reparsed as a flag.
    #      The full ref form `refs/heads/<name>` is unambiguous — it is always a ref path,
    #      never interpreted as an option.
    if ! run git checkout "refs/heads/$BASE_BRANCH" 2>/dev/null; then
      if [ "$FORCE" -eq 1 ]; then
        warn "failed to switch off branch $BRANCH to $BASE_BRANCH — skipping worktree removal; branch deletion still attempted under --force"
        WT_FAIL=1
      else
        warn "failed to switch off branch $BRANCH to $BASE_BRANCH — skipping cleanup"
        exit 1
      fi
    fi
  fi
fi

remove_force() {
  # dev-pipeline: retry Windows file-lock, then rm -rf within $REPO_ROOT/.worktrees/ whitelist.
  # Canonicalize the path via `cd && pwd -P` before the whitelist check so a caller cannot
  # escape via `..` segments (textual prefix match alone is not enough — #277 review finding).
  local wt="$1"
  run git worktree remove --force -- "$wt" && return 0
  sleep 2
  run git worktree remove --force -- "$wt" && return 0
  warn "git worktree remove retry failed for $wt"
  [ -n "$wt" ] && [ -d "$wt" ] && [ ! -L "$wt" ] || return 1
  local wt_canon
  wt_canon=$(cd "$wt" 2>/dev/null && pwd -P) || {
    warn "cannot canonicalize $wt — refusing rm -rf fallback"
    return 1
  }
  case "$wt_canon" in
    "$REPO_ROOT_CANON/.worktrees/"*)
      if run rm -rf -- "$wt_canon"; then
        run git worktree prune 2>/dev/null || true   # clear stale metadata
        return 0
      fi
      warn "rm -rf fallback failed for $wt_canon"
      return 1
      ;;
    *)
      warn "refuse to rm -rf path outside $REPO_ROOT_CANON/.worktrees/ (canonical: $wt_canon)"
      return 1
      ;;
  esac
}

remove_safe() {
  # pr-flow: preserve dirty worktrees. Inaccessible → prune; clean → plain remove.
  local wt="$1"
  if ! git -C "$wt" status --porcelain >/dev/null 2>&1; then
    warn "worktree $wt is inaccessible — pruning"
    run git worktree prune --expire=now || return 1
    # In dry-run mode `run` is a no-op: the prune above didn't actually execute, so the
    # post-action verification below would incorrectly report failure. Short-circuit.
    [ "$DRY_RUN" -eq 1 ] && return 0
    if git worktree list --porcelain | grep -Fq "worktree $wt"; then
      warn "worktree $wt still registered after prune"
      return 1
    fi
    return 0
  fi
  if [ -n "$(git -C "$wt" status --porcelain)" ]; then
    warn "worktree $wt has uncommitted changes — skipping"
    return 1
  fi
  run git worktree remove -- "$wt"
}

while IFS= read -r wt_path; do
  [ -z "$wt_path" ] && continue
  [ "$wt_path" = "$MAIN_WT" ] && continue
  if [ "$FORCE" -eq 1 ]; then
    remove_force "$wt_path" || WT_FAIL=1
  else
    remove_safe "$wt_path" || WT_FAIL=1
  fi
done <<< "$WT_PATHS"

# ---------- branch deletion ----------
# Mode semantics:
#   --force (dev-pipeline): ALWAYS attempt `git branch -d`. The original Phase 5 block did this
#     unconditionally because on Windows file-lock, `git worktree remove --force` can partially
#     succeed (metadata gone, directory retained) — the branch is still deletable even though
#     WT_FAIL=1. git itself refuses safely if the branch is still checked out somewhere.
#   safe mode (pr-flow): skip branch deletion when worktree cleanup is incomplete, matching the
#     original Phase 7 guard (dirty worktree → preserve branch so the user can inspect).

if [ "$WT_FAIL" -eq 0 ]; then
  if ! run git branch -d -- "$BRANCH"; then
    warn "git branch -d $BRANCH failed (likely still registered in a worktree)"
  fi
  exit 0
elif [ "$FORCE" -eq 1 ]; then
  if ! run git branch -d -- "$BRANCH"; then
    warn "git branch -d $BRANCH failed (worktree cleanup incomplete — expected)"
  fi
  exit 1
else
  echo "Skipping branch deletion — worktree cleanup incomplete" >&2
  exit 1
fi
