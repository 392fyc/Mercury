#!/usr/bin/env bash
# cleanup-worktree-branch.sh — shared worktree + branch cleanup (dev-pipeline Phase 5 + pr-flow
# Phase 7). SoT for the inline blocks that diverged in S62 (see Mercury #274; lifecycle rules in #663).
# Usage: cleanup-worktree-branch.sh <BRANCH> <BASE_BRANCH> [--force] [--worktree-path PATH] [--dry-run]
#   --force        Windows file-lock retry: retries a NON-force `git worktree remove`, then an rm -rf
#                  fallback limited to .worktrees/. Never removes a worktree that has uncommitted
#                  content (warns and skips). Branch delete is still attempted if the worktree stays.
#   (no flag)      safe mode: preserve dirty worktrees, skip branch delete if worktree cleanup failed
#   --worktree-path PATH  skip auto-discovery (also the only way to process a worktree under
#                  .worktrees/lanes/, which auto-discovery skips; verify the lane is closed first)
#   --dry-run      print destructive commands without executing
# Worktree safety (every removal): the path must be a registered worktree root (git worktree list)
#   that checks out exactly <BRANCH>; its state must be provably clean: no staged/unstaged/untracked
#   files AND no ignored files other than regenerable caches (__pycache__/, .pytest_cache/, *.pyc),
#   because `git worktree remove` deletes ignored files too (.handoff/, .tmp/, .env ...). A dirty
#   or unverifiable (git status fails) worktree is skipped and listed. rm -rf is a last resort only
#   for a registered, clean root whose plain remove failed (file lock), and never under .worktrees/lanes.
# Safety gates (checked before anything is removed): the branch must not be main, master, develop or
#   origin's default branch (nothing on GitHub protects them), and it must be verified as merged:
#   its tip is equal to / an ancestor of the head commit of a merged PR, or it has at least one
#   commit of its own (branch reflog) and is fully contained in <BASE_BRANCH>. A branch with no own
#   commit and no merged PR (e.g. freshly cut from the base) is never deleted. Unverified: exit 1.
# Branch delete: `git branch -d`; if that fails (squash/rebase merge) and `gh` is available, the
#   branch tip must be an ancestor of (or equal to) a merged PR head commit, then `git branch -D`
#   is used and the evidence is printed. Otherwise the branch is kept with a warning.
# After a successful local branch delete (not dry-run): `git fetch --prune origin`; if the remote
#   branch still exists, warn that GitHub auto-delete did not remove it (exit code unchanged).
# Exit: 0 complete | 1 worktree cleanup incomplete or merge unverified | 2 invalid args / protected branch / not in a git repository

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

# Origin's default branch: origin/HEAD, else `git ls-remote --symref origin HEAD`, else unknown
# (then only the fixed list main/master/develop protects, and a warning says so).
DEFAULT_BRANCH=""
DEFAULT_BRANCH_DONE=0
resolve_default_branch() {
  [ "$DEFAULT_BRANCH_DONE" -eq 1 ] && return 0
  DEFAULT_BRANCH_DONE=1
  local d
  d=$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null) || d=""
  d=${d#origin/}
  if [ -z "$d" ] && git remote get-url origin >/dev/null 2>&1; then
    local tmo=""
    command -v timeout >/dev/null 2>&1 && tmo="timeout 20"
    d=$(GIT_TERMINAL_PROMPT=0 $tmo git ls-remote --symref origin HEAD 2>/dev/null \
          | awk '/^ref:/ { sub("refs/heads/", "", $2); print $2; exit }') || d=""
  fi
  if [ -z "$d" ]; then
    warn "cannot determine origin's default branch (no origin/HEAD, ls-remote failed) — only main, master and develop are protected"
  fi
  DEFAULT_BRANCH="$d"
}

# True when $1 must never be deleted: main/master/develop or origin's default branch.
is_protected_branch() {
  case "$1" in main|master|develop) return 0 ;; esac
  resolve_default_branch
  [ -n "$DEFAULT_BRANCH" ] && [ "$1" = "$DEFAULT_BRANCH" ]
}

# Prints the merged-PR head commit that contains the branch tip, returns 0; else returns 1.
merged_pr_head_containing_tip() {
  command -v gh >/dev/null 2>&1 || return 1
  local tip heads oid
  tip=$(git rev-parse --verify -q "refs/heads/$BRANCH") || return 1
  heads=$(gh pr list --state merged --head "$BRANCH" --json headRefOid,isCrossRepository \
            --jq '.[] | select(.isCrossRepository == false) | .headRefOid' 2>/dev/null) || return 1
  while IFS= read -r oid; do
    [ -z "$oid" ] && continue
    if git merge-base --is-ancestor "$tip" "$oid" 2>/dev/null; then
      printf '%s\n' "$oid"
      return 0
    fi
  done <<< "$heads"
  return 1
}

# True when the branch tip differs from the commit it was created at (oldest reflog entry), i.e. at
# least one commit was made on the branch itself. No reflog (expired/unavailable) => false.
branch_has_own_commit() {
  local tip first
  tip=$(git rev-parse --verify -q "refs/heads/$BRANCH") || return 1
  first=$(git reflog show --format='%H' "refs/heads/$BRANCH" 2>/dev/null | tail -n 1)
  [ -n "$first" ] && [ "$first" != "$tip" ]
}

# Merge verification (Issue #663). Returns 0 and sets MERGE_EVIDENCE when verified.
verify_branch_merged() {
  local tip oid base_ok=1
  tip=$(git rev-parse --verify -q "refs/heads/$BRANCH") || return 1
  if oid=$(merged_pr_head_containing_tip); then
    MERGE_EVIDENCE="tip contained in merged PR head $oid"
    return 0
  fi
  if git merge-base --is-ancestor "$tip" "refs/heads/$BASE_BRANCH" 2>/dev/null \
     || git merge-base --is-ancestor "$tip" "refs/remotes/origin/$BASE_BRANCH" 2>/dev/null; then
    base_ok=0
  fi
  if [ "$base_ok" -eq 0 ] && branch_has_own_commit; then
    MERGE_EVIDENCE="own commits fully contained in $BASE_BRANCH"
    return 0
  fi
  return 1
}

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] $*"
    return 0
  fi
  "$@"
}

# Canonical absolute path (symlinks and `..` resolved); fails if the path does not exist.
canon() { (cd "$1" 2>/dev/null && pwd -P); }

# If $1 is the root of a registered worktree (compared canonically against
# `git worktree list --porcelain`), print its checked-out ref (refs/heads/x, or DETACHED) and
# return 0; otherwise return 1.
wt_registered_branch() {
  local want p="" b="" line c
  want=$(canon "$1") || return 1
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) p=${line#worktree }; b="" ;;
      "branch "*) b=${line#branch } ;;
      "")
        if [ -n "$p" ]; then
          c=$(canon "$p") || c=""
          if [ -n "$c" ] && [ "$c" = "$want" ]; then
            printf '%s\n' "${b:-DETACHED}"
            return 0
          fi
        fi
        p=""; b=""
        ;;
    esac
  done < <(git worktree list --porcelain 2>/dev/null; echo)
  return 1
}

# Three-state check of worktree $1. Sets WT_STATE = clean | dirty | unknown and WT_DETAIL.
#   unknown: not a registered worktree root, or `git status` fails (broken .git link, bad index).
#   dirty:   staged/unstaged/untracked files, or ignored files other than regenerable caches.
WT_STATE=""
WT_DETAIL=""
wt_state() {
  local wt="$1" out bad
  WT_DETAIL=""
  if ! wt_registered_branch "$wt" >/dev/null; then
    WT_STATE=unknown; WT_DETAIL="not a registered worktree root"; return 0
  fi
  if ! out=$(git -C "$wt" status --porcelain --ignored=matching 2>/dev/null); then
    WT_STATE=unknown; WT_DETAIL="git status failed"; return 0
  fi
  bad=$(printf '%s\n' "$out" | grep -v '^$' \
          | grep -v -E '^!! (.*/)?(__pycache__|\.pytest_cache)/$' \
          | grep -v -E '^!! .*\.pyc$' || true)
  if [ -n "$bad" ]; then
    WT_STATE=dirty
    WT_DETAIL=$(printf '%s\n' "$bad" | head -n 5 | tr '\n' ';')
    return 0
  fi
  WT_STATE=clean
  return 0
}

# Discover worktrees BEFORE pre-switch: `git switch` changes the main worktree's branch
# association in `git worktree list --porcelain`, altering discovery results.
WT_FAIL=0
LANE_SKIPPED=0
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
    if [ "$FORCE" -eq 1 ]; then
      warn "git worktree list failed — --force: continuing to branch deletion anyway"
      WT_FAIL=1
      WT_PATHS=""
    else
      warn "git worktree list failed — aborting cleanup (safe mode)"
      exit 1
    fi
  else
    WT_PATHS=$(printf '%s\n' "$WT_LIST" | awk -v ref="branch refs/heads/$BRANCH" '
      /^worktree / { path = substr($0, 10) }
      $0 == ref { print path }
    ')
    # Lane-level worktrees (.worktrees/lanes/) are long-lived: skip them in auto-discovery.
    KEPT_PATHS=""
    while IFS= read -r _p; do
      [ -z "$_p" ] && continue
      case "$_p" in
        */.worktrees/lanes/*)
          warn "worktree $_p is a lane-level worktree — skipping (close the lane via the lane-close procedure, then pass --worktree-path explicitly)"
          LANE_SKIPPED=1
          ;;
        *) KEPT_PATHS="${KEPT_PATHS}${_p}"$'\n' ;;
      esac
    done <<< "$WT_PATHS"
    WT_PATHS="${KEPT_PATHS%$'\n'}"
  fi
fi
# Gates: nothing is removed or switched before both pass.
if is_protected_branch "$BRANCH"; then
  die "refusing to clean up protected branch '$BRANCH' (main, master, develop and origin's default branch are never deleted)"
fi
# An explicit --worktree-path must be a registered worktree root that checks out exactly BRANCH.
if [ -n "$EXPLICIT_WT_PATH" ]; then
  if ! _ref=$(wt_registered_branch "$EXPLICIT_WT_PATH"); then
    warn "--worktree-path $EXPLICIT_WT_PATH is not the root of a registered worktree — refusing; nothing deleted"
    exit 1
  fi
  if [ "$_ref" != "refs/heads/$BRANCH" ]; then
    warn "--worktree-path $EXPLICIT_WT_PATH checks out '$_ref', not 'refs/heads/$BRANCH' — refusing; nothing deleted"
    exit 1
  fi
  if [ "$(canon "$EXPLICIT_WT_PATH")" = "$REPO_ROOT_CANON" ]; then
    warn "--worktree-path is the main checkout — refusing; nothing deleted"
    exit 1
  fi
fi
# No local branch: no worktree deletion is allowed on its behalf.
if ! git rev-parse --verify -q "refs/heads/$BRANCH" >/dev/null 2>&1; then
  if [ -n "$WT_PATHS" ]; then
    warn "branch $BRANCH does not exist locally but worktree(s) are attached — refusing; nothing deleted"
    exit 1
  fi
  echo "cleanup-worktree-branch: branch $BRANCH does not exist locally and no worktree is attached — nothing to do" >&2
  exit 0
fi
MERGE_EVIDENCE=""
if ! verify_branch_merged; then
  warn "could not verify that $BRANCH is merged (no merged PR head contains its tip, and it is not a branch with its own commits fully contained in $BASE_BRANCH) — nothing deleted"
  exit 1
fi
echo "cleanup-worktree-branch: merge verified: $MERGE_EVIDENCE" >&2

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
        # Clear any previously-discovered worktree paths so the removal loop below does not
        # attempt `git worktree remove` on entries that are still attached to the branch we
        # could not switch away from (would fail every time, adding noise to the WARN stream).
        WT_PATHS=""
      else
        warn "failed to switch off branch $BRANCH to $BASE_BRANCH — skipping cleanup"
        exit 1
      fi
    fi
  fi
fi

skip_unclean() {
  # Reports a dirty/unverifiable worktree; returns 0 when the caller must skip it.
  case "$WT_STATE" in
    dirty)   warn "worktree $1 has uncommitted or ignored content that would be lost ($WT_DETAIL) — skipping (--force does not override this)"; return 0 ;;
    unknown) warn "cannot verify that worktree $1 is clean ($WT_DETAIL) — skipping"; return 0 ;;
  esac
  return 1
}

remove_force() {
  # Windows file-lock retry. NEVER uses `git worktree remove --force` and never deletes a worktree
  # that is dirty or cannot be verified clean (#663). Clean registered root: plain remove (retried
  # once). rm -rf is the last resort for exactly one case: registered root, still clean, plain
  # remove failed (file lock), canonical path under $REPO_ROOT/.worktrees/ and NOT under lanes/.
  local wt="$1"
  wt_state "$wt"
  if skip_unclean "$wt"; then return 1; fi
  run git worktree remove -- "$wt" && return 0
  sleep 2
  run git worktree remove -- "$wt" && return 0
  warn "git worktree remove retry failed for $wt"
  [ -n "$wt" ] && [ -d "$wt" ] && [ ! -L "$wt" ] || return 1
  local wt_canon
  wt_canon=$(canon "$wt") || {
    warn "cannot canonicalize $wt — refusing rm -rf fallback"
    return 1
  }
  wt_state "$wt_canon"
  if [ "$WT_STATE" != "clean" ]; then
    warn "refuse to rm -rf $wt_canon: not a registered, verified-clean worktree root ($WT_STATE: $WT_DETAIL)"
    return 1
  fi
  case "$wt_canon" in
    "$REPO_ROOT_CANON/.worktrees/lanes"|"$REPO_ROOT_CANON/.worktrees/lanes/"*)
      warn "refuse to rm -rf lane-level path $wt_canon"
      return 1
      ;;
    "$REPO_ROOT_CANON/.worktrees/"?*)
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
  # pr-flow: preserve dirty/unverifiable worktrees. Directory gone (stale registration) → prune.
  local wt="$1"
  if [ ! -d "$wt" ]; then
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
  wt_state "$wt"
  if skip_unclean "$wt"; then return 1; fi
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

# A skipped lane-level worktree means worktree cleanup is incomplete.
[ "$LANE_SKIPPED" -eq 1 ] && WT_FAIL=1

# ---------- branch deletion ----------
# Mode semantics:
#   --force (Windows file-lock retry): ALWAYS attempt branch deletion. On Windows file-lock, a plain
#     `git worktree remove` can partially succeed (metadata gone, directory retained) — the branch is
#     still deletable even though WT_FAIL=1. git itself refuses safely if the branch is still
#     checked out somewhere (a skipped dirty worktree keeps its branch this way).
#   safe mode (pr-flow): skip branch deletion when worktree cleanup is incomplete, matching the
#     original Phase 7 guard (dirty worktree → preserve branch so the user can inspect).
#
# Squash/rebase merges leave the branch tip outside the base history, so `git branch -d` refuses.
# Then, and only then, ask GitHub: if the tip is contained in the head commit of a merged PR from
# this same repository, `git branch -D` is safe.

# Returns 0 when the local branch is gone (or would be, in dry-run).
delete_local_branch() {
  local ctx="$1" oid
  if run git branch -d -- "$BRANCH"; then
    return 0
  fi
  if oid=$(merged_pr_head_containing_tip); then
    echo "branch $BRANCH tip is contained in merged PR head $oid — using git branch -D" >&2
    if run git branch -D -- "$BRANCH"; then
      return 0
    fi
    warn "git branch -D $BRANCH failed ($ctx)"
    return 1
  fi
  warn "git branch -d $BRANCH failed ($ctx); could not verify the tip is contained in a merged PR head — branch kept"
  return 1
}

# After a real local delete: re-read origin and warn if the remote branch is still there.
verify_remote_gone() {
  [ "$DRY_RUN" -eq 1 ] && return 0
  git remote get-url origin >/dev/null 2>&1 || return 0
  if ! GIT_TERMINAL_PROMPT=0 git fetch --prune origin >/dev/null 2>&1; then
    warn "git fetch --prune origin failed — cannot confirm the remote branch state"
    return 0
  fi
  if git show-ref --verify --quiet "refs/remotes/origin/$BRANCH"; then
    warn "remote branch origin/$BRANCH still exists — GitHub did not auto-delete it (after user confirmation: read back repos/<owner>/<repo>/git/ref/heads/$BRANCH, compare the SHA with the confirmed tip, then gh api -X DELETE .../git/refs/heads/$BRANCH)"
  fi
  return 0
}

if [ "$WT_FAIL" -eq 0 ]; then
  if delete_local_branch "likely still registered in a worktree"; then
    verify_remote_gone
  fi
  exit 0
elif [ "$FORCE" -eq 1 ]; then
  if delete_local_branch "worktree cleanup incomplete — expected"; then
    verify_remote_gone
  fi
  exit 1
else
  echo "Skipping branch deletion — worktree cleanup incomplete" >&2
  exit 1
fi
