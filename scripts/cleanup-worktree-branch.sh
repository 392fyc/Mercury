#!/usr/bin/env bash
# cleanup-worktree-branch.sh — shared worktree + branch cleanup for dev-pipeline + pr-flow.
#
# Unifies two prior inline implementations (dev-pipeline/SKILL.md Phase 5 cleanup block
# and pr-flow/SKILL.md Phase 7 cleanup block) behind one SoT script to eliminate drift.
#
# Usage:
#   cleanup-worktree-branch.sh <BRANCH> <BASE_BRANCH> [options]
#
# Positional:
#   BRANCH        Branch whose worktree(s) should be removed and whose local ref should be deleted.
#   BASE_BRANCH   Branch to switch HEAD to before worktree removal (if HEAD is currently on BRANCH).
#
# Options:
#   --force                    Use `git worktree remove --force` + Windows file-lock retry + rm -rf fallback.
#                              Skips uncommitted-changes check. Intended for dev-pipeline where the
#                              worktree is throwaway by construction.
#   --worktree-path <path>     Explicit worktree path to clean (skip discovery). Intended for
#                              dev-pipeline where the path was created by the same skill run.
#                              Without this flag, the script discovers all worktrees whose branch
#                              matches BRANCH via `git worktree list --porcelain`.
#   --dry-run                  Print destructive commands without executing them.
#
# Exit codes:
#   0   All worktrees removed AND local branch deleted.
#   1   Worktree cleanup incomplete (at least one worktree remove failed or a worktree had
#       uncommitted changes in safe mode). Local branch is NOT deleted in this case.
#   2   Invalid arguments or unrecoverable git error (not inside a repo, etc.).
#
# Safety:
#   The rm -rf fallback (activated only under --force + Windows file-lock) is restricted to paths
#   matching "<REPO_ROOT>/.worktrees/*" and refuses to follow symlinks. Any other path triggers a
#   warning and is left on disk.

set -u

# ---------- utilities ----------

die() {
  echo "cleanup-worktree-branch: $1" >&2
  exit 2
}

warn() {
  echo "WARN: $1" >&2
}

# ---------- arg parse ----------

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
      sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
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
MAIN_WT="$REPO_ROOT"

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] $*"
    return 0
  fi
  "$@"
}

# ---------- discover worktrees ----------
# IMPORTANT: discovery MUST happen BEFORE the pre-switch below. `git switch` changes
# the main worktree's branch association in `git worktree list --porcelain`, which
# would alter what auto-discovery finds.

WT_FAIL=0
WT_PATHS=""

if [ -n "$EXPLICIT_WT_PATH" ]; then
  WT_PATHS="$EXPLICIT_WT_PATH"
else
  if ! WT_LIST=$(git worktree list --porcelain 2>&1); then
    warn "git worktree list failed — skipping worktree and branch cleanup"
    exit 1
  fi
  WT_PATHS=$(echo "$WT_LIST" | awk -v ref="branch refs/heads/$BRANCH" '
    /^worktree / { path = substr($0, 10) }
    $0 == ref { print path }
  ')
fi

# ---------- pre-switch (leave BRANCH if HEAD is on it) ----------

if [ "$(git rev-parse --abbrev-ref HEAD 2>/dev/null)" = "$BRANCH" ]; then
  if ! run git switch "$BASE_BRANCH" 2>/dev/null; then
    if ! run git checkout "$BASE_BRANCH" 2>/dev/null; then
      warn "failed to switch off branch $BRANCH to $BASE_BRANCH — skipping cleanup"
      exit 1
    fi
  fi
fi

# ---------- worktree removal ----------

remove_force() {
  # dev-pipeline semantics: --force, retry once on transient Windows file lock,
  # fall back to rm -rf within the $REPO_ROOT/.worktrees/ whitelist.
  local wt="$1"
  if run git worktree remove --force "$wt"; then
    return 0
  fi
  sleep 2
  if run git worktree remove --force "$wt"; then
    return 0
  fi
  warn "git worktree remove retry failed for $wt"
  if [ -n "$wt" ] && [ -d "$wt" ] && [ ! -L "$wt" ]; then
    case "$wt" in
      "$REPO_ROOT/.worktrees/"*)
        if run rm -rf -- "$wt"; then
          # Prune stale worktree metadata that still references the now-deleted path,
          # otherwise subsequent `git branch -d` may refuse ("branch is checked out").
          run git worktree prune 2>/dev/null || true
          return 0
        fi
        warn "rm -rf fallback failed for $wt"
        return 1
        ;;
      *)
        warn "refuse to rm -rf path outside $REPO_ROOT/.worktrees/: $wt"
        return 1
        ;;
    esac
  fi
  return 1
}

remove_safe() {
  # pr-flow semantics: no --force. Inaccessible worktrees are pruned; dirty worktrees are
  # preserved (and reported as failure so the branch is not deleted).
  local wt="$1"
  if ! git -C "$wt" status --porcelain >/dev/null 2>&1; then
    warn "worktree $wt is inaccessible — pruning"
    if ! run git worktree prune --expire=now; then
      return 1
    fi
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
  run git worktree remove "$wt"
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
  if ! run git branch -d "$BRANCH"; then
    warn "git branch -d $BRANCH failed (likely still registered in a worktree)"
  fi
  exit 0
elif [ "$FORCE" -eq 1 ]; then
  if ! run git branch -d "$BRANCH"; then
    warn "git branch -d $BRANCH failed (worktree cleanup incomplete — expected)"
  fi
  exit 1
else
  echo "Skipping branch deletion — worktree cleanup incomplete" >&2
  exit 1
fi
