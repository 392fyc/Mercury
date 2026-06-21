#!/usr/bin/env sh
# scripts/worktree-reaper.sh
# Orphan worktree reaper for Mercury dev-pipeline.
#
# Lists entries under .worktrees/, cross-checks against open PRs, and removes
# any worktree whose branch has no open PR and is older than AGE_DAYS days.
#
# Usage:
#   ./scripts/worktree-reaper.sh            # dry-run (default — prints WOULD REAP)
#   ./scripts/worktree-reaper.sh --dry-run  # same
#   ./scripts/worktree-reaper.sh --prune    # actually delete orphaned worktrees
#
# Environment overrides:
#   WORKTREE_ROOT   directory to scan (default: .worktrees relative to repo root)
#   WORKTREE_AGE_DAYS  minimum age in days before a worktree is eligible (default: 7)

set -eu

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
MODE="${1:---dry-run}"
case "$MODE" in
  --dry-run|--prune) ;;
  *)
    printf 'Usage: %s [--dry-run|--prune]\n' "$0" >&2
    exit 1
    ;;
esac

# Locate the repository root so the script works from any cwd.
REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  printf 'ERROR: not inside a git repository\n' >&2
  exit 1
}

WT_ROOT="${WORKTREE_ROOT:-${REPO_ROOT}/.worktrees}"
AGE_DAYS="${WORKTREE_AGE_DAYS:-7}"

# Validate AGE_DAYS: must be a non-negative integer. Negative or non-numeric
# values would produce bogus thresholds (negative → every worktree "eligible").
case "$AGE_DAYS" in
  ''|*[!0-9]*)
    printf 'ERROR: WORKTREE_AGE_DAYS must be a non-negative integer, got "%s"\n' "$AGE_DAYS" >&2
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# Guard: nothing to do if .worktrees/ does not exist
# ---------------------------------------------------------------------------
if [ ! -d "$WT_ROOT" ]; then
  printf 'No %s directory — nothing to do\n' "$WT_ROOT"
  exit 0
fi

# ---------------------------------------------------------------------------
# Fetch open PR branch names. Critical to distinguish "no open PRs" from
# "gh call failed" — in --prune mode an API glitch that returns empty would
# otherwise delete worktrees whose PRs are actually still open.
# ---------------------------------------------------------------------------
ACTIVE_BRANCHES=""
GH_PR_FETCH_OK=0
if command -v gh >/dev/null 2>&1; then
  if ACTIVE_BRANCHES=$(gh pr list --state open --json headRefName --jq '.[].headRefName' 2>/dev/null); then
    GH_PR_FETCH_OK=1
  fi
fi

if [ "$MODE" = "--prune" ] && [ "$GH_PR_FETCH_OK" -ne 1 ]; then
  printf 'ERROR: cannot fetch open PR branches via gh — refusing to prune\n' >&2
  printf '       Install gh, authenticate, or rerun with --dry-run.\n' >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Compute age threshold in epoch seconds
# ---------------------------------------------------------------------------
NOW=$(date +%s)
THRESHOLD=$(( AGE_DAYS * 86400 ))

# ---------------------------------------------------------------------------
# Scan worktrees
# ---------------------------------------------------------------------------
FOUND=0
# Resolve WT_ROOT once so we can confirm each candidate stays inside it.
ROOT_REAL=$(cd "$WT_ROOT" 2>/dev/null && pwd -P || printf '')
for wt in "${WT_ROOT}"/*/; do
  # glob may not expand if directory is empty
  [ -d "$wt" ] || continue
  # Skip symlinks — never follow links out of WT_ROOT
  [ -L "$wt" ] && continue

  # Verify realpath still lives under WT_ROOT (defends against symlink targets
  # inside the directory tree and against mount shenanigans)
  wt_real=$(cd "$wt" 2>/dev/null && pwd -P || printf '')
  if [ -z "$wt_real" ] || [ -z "$ROOT_REAL" ]; then
    continue
  fi
  case "$wt_real" in
    "$ROOT_REAL"/*) ;;
    *)
      printf 'SKIP    : %s — realpath escapes %s\n' "$wt" "$ROOT_REAL" >&2
      continue
      ;;
  esac

  FOUND=1
  task_id=$(basename "$wt")

  # Determine which branch the worktree is on
  branch=$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null || true)

  # Determine mtime of the worktree directory.
  # stat differs between GNU (Linux) and BSD (macOS); try both forms.
  mtime_epoch=$(stat -c '%Y' "$wt" 2>/dev/null \
              || stat -f '%m' "$wt" 2>/dev/null \
              || printf '0')

  age_sec=$(( NOW - mtime_epoch ))

  # Guard: keep worktrees with an open PR regardless of age
  if [ -n "$branch" ] && printf '%s\n' "$ACTIVE_BRANCHES" | grep -qxF "$branch" 2>/dev/null; then
    printf 'KEEP    : %s (active PR on branch "%s")\n' "$task_id" "$branch"
    continue
  fi

  # Guard: keep worktrees that are still fresh
  if [ "$age_sec" -lt "$THRESHOLD" ]; then
    printf 'KEEP    : %s (fresh — age %ds < threshold %ds)\n' \
      "$task_id" "$age_sec" "$THRESHOLD"
    continue
  fi

  # Eligible orphan
  if [ "$MODE" = "--prune" ]; then
    if git worktree remove --force "$wt" 2>/dev/null; then
      # Attempt to delete the branch; non-fatal if it is already gone or merged
      git branch -d "$branch" 2>/dev/null || true
      printf 'REAPED  : %s (branch "%s", age %ds)\n' "$task_id" "$branch" "$age_sec"
    else
      printf 'ERROR   : failed to remove worktree %s\n' "$wt" >&2
    fi
  else
    printf 'WOULD REAP: %s (branch "%s", age %ds >= threshold %ds) [dry-run]\n' \
      "$task_id" "$branch" "$age_sec" "$THRESHOLD"
  fi
done

if [ "$FOUND" = "0" ]; then
  printf 'No worktree entries found under %s\n' "$WT_ROOT"
fi
