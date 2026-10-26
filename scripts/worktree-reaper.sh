#!/usr/bin/env sh
# scripts/worktree-reaper.sh
# Worktree reaper for Mercury (lifecycle rules: Issue #663).
#
# Scans entries under .worktrees/ and classifies each one:
#   KEEP       open PR on the branch, or a fresh unmerged worktree
#   DIRTY      merged, but the worktree has uncommitted content, or ignored files other than
#              regenerable caches (__pycache__/, .pytest_cache/, *.pyc) that `git worktree remove`
#              would delete (.handoff/, .tmp/, .env ...) — never removed; the paths are listed
#   WOULD REAP / REAPED   merged AND clean: the local HEAD is an ancestor of origin/<default branch>
#              or is contained in the head commit of a merged PR (squash/rebase merge), and the
#              worktree has no staged/unstaged/untracked content. Merged worktrees are not subject
#              to the age threshold. "Merged" means: the tip is equal to / an ancestor of the head
#              commit of a merged PR, OR the branch has at least one commit of its own (branch
#              tip differs from the commit it was created at, per reflog) and is fully contained in the base. A branch with no own commit and no
#              merged PR (e.g. just cut from the base) is NEVER reaped; it is only a CONFIRM
#              candidate once it is older than the threshold.
#   SUBMODULE  merged and clean, but a checked-out submodule is not at its recorded commit or has
#              uncommitted/ignored content (or cannot be verified) — never removed; reason listed
#   SKIP       directories that are not worktree roots (or whose .git link is broken), and the
#              lanes/ directory (long-lived lane worktrees, closed by the lane-close procedure)
#   Protected: main, master, develop and origin's default branch are never touched (KEEP).
#   CONFIRM    NOT merged, no open PR, older than the threshold: listed with branch, tip commit and
#              unmerged commit count for the user to decide. NEVER deleted, in any mode.
# Removal uses a plain `git worktree remove` (never --force) and `git branch -d`, or `-D` only
# with merged-PR evidence. The single exception (Issue #670) is a merged, clean worktree with
# checked-out submodules: git refuses a non-force remove for those (the submodule repositories live
# in <git-dir>/modules). It is removed with one `git worktree remove --force` ONLY when every
# checked-out submodule (<path>/.git exists; recursive) has `git submodule status` prefix " "
# (checked-out commit == recorded commit; "-", "+", "U" all block), is its own repository root, and
# `git status --porcelain --ignored=matching` inside it shows nothing except regenerable caches.
# `git submodule deinit` is deliberately NOT run: init/deinit write the config shared by all
# worktrees, so it would unregister the submodules of the main checkout. Only the checked-out
# commit is verified; commits on other local branches inside a submodule are not examined and are
# lost with the force removal. A worktree with <git-dir>/modules but no checked-out submodule also
# takes this force path. Otherwise the worktree is kept and a `SUBMODULE :` line gives the reason.
# Worktrees without checked-out submodules and without <git-dir>/modules are unaffected.
# Remote branches are left to GitHub's auto-delete-head-branch setting.
# After the user confirms an abandoned branch, delete it manually: worktree, local branch, and
#   gh api repos/<owner>/<repo>/git/ref/heads/<branch>   (read back; SHA must equal the listed tip)
#   gh api -X DELETE repos/<owner>/<repo>/git/refs/heads/<branch>
#
# Usage:
#   ./scripts/worktree-reaper.sh            # dry-run (default — prints WOULD REAP / CONFIRM)
#   ./scripts/worktree-reaper.sh --dry-run  # same
#   ./scripts/worktree-reaper.sh --prune    # remove merged + clean worktrees and their local branches
#
# Environment overrides:
#   WORKTREE_ROOT      directory to scan (default: .worktrees relative to repo root)
#   WORKTREE_AGE_DAYS  age in days after which unmerged worktrees are listed as CONFIRM (default: 7)
#   WORKTREE_BASE_REF  ref that counts as "merged into" (default: origin's default branch)

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

# Base ref: what "merged" is measured against.
BASE_REF="${WORKTREE_BASE_REF:-}"
if [ -z "$BASE_REF" ]; then
  BASE_REF=$(git -C "$REPO_ROOT" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)
fi
if [ -z "$BASE_REF" ]; then
  for cand in origin/develop origin/main origin/master; do
    if git -C "$REPO_ROOT" rev-parse --verify -q "refs/remotes/$cand" >/dev/null 2>&1; then
      BASE_REF="$cand"
      break
    fi
  done
fi

# Origin's default branch (protected): origin/HEAD, else ls-remote, else unknown (warn).
DEFAULT_BRANCH=$(git -C "$REPO_ROOT" symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)
DEFAULT_BRANCH=${DEFAULT_BRANCH#origin/}
if [ -z "$DEFAULT_BRANCH" ] && git -C "$REPO_ROOT" remote get-url origin >/dev/null 2>&1; then
  _tmo=""
  command -v timeout >/dev/null 2>&1 && _tmo="timeout 20"
  DEFAULT_BRANCH=$(GIT_TERMINAL_PROMPT=0 $_tmo git -C "$REPO_ROOT" ls-remote --symref origin HEAD 2>/dev/null \
    | awk '/^ref:/ { sub("refs/heads/", "", $2); print $2; exit }' || true)
fi
if [ -z "$DEFAULT_BRANCH" ]; then
  printf "WARN: cannot determine origin's default branch — only main, master and develop are protected\n" >&2
fi

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
# Merged-PR helpers (squash/rebase merges leave the branch tip outside the base history)
# ---------------------------------------------------------------------------
# Prints one merged-PR head commit per line for same-repository PRs from branch $1.
merged_pr_heads() {
  command -v gh >/dev/null 2>&1 || return 1
  gh pr list --state merged --head "$1" --json headRefOid,isCrossRepository \
    --jq '.[] | select(.isCrossRepository == false) | .headRefOid' 2>/dev/null
}

# True when $1 is main/master/develop or origin's default branch (nothing on GitHub protects them).
is_protected_branch() {
  case "$1" in main|master|develop) return 0 ;; esac
  [ -n "$DEFAULT_BRANCH" ] && [ "$1" = "$DEFAULT_BRANCH" ]
}

# True when branch $1's tip $2 differs from the commit it was created at (oldest reflog entry),
# i.e. at least one commit was made on the branch. No reflog => false.
branch_has_own_commit() {
  _first=$(git -C "$REPO_ROOT" reflog show --format='%H' "refs/heads/$1" 2>/dev/null | tail -n 1)
  [ -n "$_first" ] && [ "$_first" != "$2" ]
}

# Prints what would be lost by `git worktree remove` in worktree $1 (up to 5 entries, ';'-joined):
# staged/unstaged/untracked files and ignored files except regenerable caches. Empty = clean.
wt_dirty_detail() {
  _out=$(git -C "$1" status --porcelain --ignored=matching 2>/dev/null) || {
    printf 'git status failed (cannot verify)'
    return 0
  }
  printf '%s\n' "$_out" | grep -v '^$' \
    | grep -v -E '^!! (.*/)?(__pycache__|\.pytest_cache)/$' \
    | grep -v -E '^!! .*\.pyc$' \
    | head -n 5 | tr '\n' ';'
  return 0
}

# Submodule check of worktree $1 (Issue #670). Prints "none", "clean" or "bad: <detail>".
# A submodule counts as checked out when <path>/.git exists; the status prefix alone is not trusted
# (a "-" submodule can still hold files). Walks the submodule tree breadth-first with a work list.
#   none:  no checked-out submodule and no <git-dir>/modules.
#   clean: every checked-out submodule (recursive) has prefix " ", is its own repository root and
#          holds nothing but regenerable caches besides tracked content (also when only
#          <git-dir>/modules exists).
#   bad:   anything else, including a command that fails (cannot verify).
wt_submodule_state() {
  _has=0
  _probs=""
  _first=1
  _gd=$(git -C "$1" rev-parse --absolute-git-dir 2>/dev/null || true)
  if [ -n "$_gd" ] && [ -d "$_gd/modules" ]; then _has=1; fi
  _queue="$1"
  while [ -n "$_queue" ]; do
    _dir=$(printf '%s\n' "$_queue" | head -n 1)
    _queue=$(printf '%s\n' "$_queue" | tail -n +2)
    if ! _st=$(git -C "$_dir" submodule status 2>/dev/null); then
      if [ "$_first" -eq 0 ]; then
        _probs="${_probs}submodule ${_dir#"$1"/}: git submodule status failed (cannot verify);"
      elif [ "$_has" -eq 1 ]; then
        _probs="${_probs}git submodule status failed (cannot verify);"
      fi
      _first=0
      continue
    fi
    _first=0
    while IFS= read -r _line; do
      [ -n "$_line" ] || continue
      _prefix=$(printf '%s' "$_line" | cut -c1)
      _rest=$(printf '%s' "$_line" | cut -c2-)
      _rest=${_rest#* }
      _sp=${_rest% (*}
      _sub="$_dir/$_sp"
      [ -e "$_sub/.git" ] || continue   # not checked out: nothing on disk to lose
      _has=1
      _label=${_sub#"$1"/}
      case "$_prefix" in
        " ") ;;
        "-") _probs="${_probs}submodule $_label: checked out but not active (status prefix '-'), cannot verify;"; continue ;;
        *)   _probs="${_probs}submodule $_label: checked-out commit differs from the recorded one or has conflicts (status prefix '$_prefix');"; continue ;;
      esac
      _top=$(git -C "$_sub" rev-parse --show-toplevel 2>/dev/null || true)
      _got=""
      [ -n "$_top" ] && _got=$(cd "$_top" 2>/dev/null && pwd -P || true)
      _want=$(cd "$_sub" 2>/dev/null && pwd -P || true)
      if [ -z "$_got" ] || [ "$_got" != "$_want" ]; then
        _probs="${_probs}submodule $_label: not its own repository root (cannot verify);"
        continue
      fi
      if ! _sout=$(git -C "$_sub" status --porcelain --ignored=matching 2>/dev/null); then
        _probs="${_probs}submodule $_label: git status failed (cannot verify);"
        continue
      fi
      _sbad=$(printf '%s\n' "$_sout" | grep -v '^$' \
        | grep -v -E '^!! (.*/)?(__pycache__|\.pytest_cache)/$' \
        | grep -v -E '^!! .*\.pyc$' \
        | head -n 5 | tr '\n' ';' || true)
      if [ -n "$_sbad" ]; then
        _probs="${_probs}submodule $_label: $_sbad"
        continue
      fi
      _queue="${_queue:+$_queue
}$_sub"
    done <<SUBEOF
$_st
SUBEOF
  done
  if [ -n "$_probs" ]; then
    printf 'bad: %s' "$_probs"
  elif [ "$_has" -eq 1 ]; then
    printf 'clean'
  else
    printf 'none'
  fi
  return 0
}

# Prints the merged-PR head containing commit $2 (ancestor-or-equal) for branch $1; else fails.
merged_pr_head_containing() {
  _heads=$(merged_pr_heads "$1" || true)
  [ -n "$_heads" ] || return 1
  for _oid in $_heads; do
    if git -C "$REPO_ROOT" merge-base --is-ancestor "$2" "$_oid" 2>/dev/null; then
      printf '%s
' "$_oid"
      return 0
    fi
  done
  return 1
}

# ---------------------------------------------------------------------------
# Scan worktrees
# ---------------------------------------------------------------------------
FOUND=0
# Resolve WT_ROOT once so we can confirm each candidate stays inside it.
ROOT_REAL=$(cd "$WT_ROOT" 2>/dev/null && pwd -P || printf '')
for wt in "${WT_ROOT}"/*/; do
  # glob may not expand if directory is empty
  [ -d "$wt" ] || continue
  # Lane-level worktrees (.worktrees/lanes/<short>) are long-lived; never touched here.
  if [ "$(basename "$wt")" = "lanes" ]; then
    printf 'SKIP : lanes (lane-level worktrees are closed by the lane-close procedure)\n'
    continue
  fi
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

  # Only registered worktree roots are handled: a stray directory (or one with a broken .git
  # link) would make `git -C` describe the enclosing repository instead.
  top=$(git -C "$wt" rev-parse --show-toplevel 2>/dev/null || true)
  top_real=$(cd "$top" 2>/dev/null && pwd -P || printf '')
  if [ -z "$top_real" ] || [ "$top_real" != "$wt_real" ]; then
    printf 'SKIP    : %s — not a worktree root (or its .git link is broken); left untouched\n' "$(basename "$wt")"
    continue
  fi

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

  # Guard: keep worktrees with an open PR regardless of age or merge state
  if [ -n "$branch" ] && printf '%s\n' "$ACTIVE_BRANCHES" | grep -qxF "$branch" 2>/dev/null; then
    printf 'KEEP    : %s (active PR on branch "%s")\n' "$task_id" "$branch"
    continue
  fi

  # Detached HEAD: no branch to reason about
  if [ -z "$branch" ] || [ "$branch" = "HEAD" ]; then
    printf 'KEEP    : %s (detached or unreadable HEAD — cannot verify merge state)\n' "$task_id"
    continue
  fi

  if is_protected_branch "$branch"; then
    printf 'KEEP    : %s (protected branch "%s" is never removed)\n' "$task_id" "$branch"
    continue
  fi

  tip=$(git -C "$wt" rev-parse HEAD 2>/dev/null || true)
  if [ -z "$tip" ]; then
    printf 'KEEP    : %s (cannot read HEAD commit)\n' "$task_id"
    continue
  fi

  # --- merge state -------------------------------------------------------
  merged=0
  evidence=""
  pr_head=$(merged_pr_head_containing "$branch" "$tip" || true)
  if [ -n "$pr_head" ]; then
    merged=1
    evidence="contained in merged PR head $pr_head"
  elif [ -n "$BASE_REF" ] && git -C "$REPO_ROOT" merge-base --is-ancestor "$tip" "$BASE_REF" 2>/dev/null; then
    # A branch cut from the base and never committed to is also an ancestor of the base, so
    # ancestry alone proves nothing: require at least one commit made on the branch itself.
    if branch_has_own_commit "$branch" "$tip"; then
      merged=1
      evidence="own commits contained in $BASE_REF"
    fi
  fi

  if [ "$merged" -eq 1 ]; then
    dirty_detail=$(wt_dirty_detail "$wt")
    if [ -n "$dirty_detail" ]; then
      printf 'DIRTY   : %s (branch "%s" is merged — %s — but the worktree has uncommitted or ignored content that would be lost: %s; kept)\n' \
        "$task_id" "$branch" "$evidence" "$dirty_detail"
      continue
    fi
    sub_state=$(wt_submodule_state "$wt")
    case "$sub_state" in
      bad:*)
        printf 'SUBMODULE : %s (branch "%s" is merged — %s — but a checked-out submodule blocks removal: %s; kept)\n' \
          "$task_id" "$branch" "$evidence" "${sub_state#bad: }"
        continue
        ;;
    esac
    if [ "$sub_state" = "clean" ]; then
      # Issue #670: git refuses a non-force remove while submodule data exists. Verified
      # above: worktree clean, every submodule at its recorded commit with no extra content.
      if [ "$MODE" = "--prune" ]; then
        if git -C "$REPO_ROOT" worktree remove --force "$wt" 2>/dev/null; then
          if git -C "$REPO_ROOT" branch -d "$branch" >/dev/null 2>&1; then
            :
          elif [ -n "$pr_head" ] && git -C "$REPO_ROOT" branch -D "$branch" >/dev/null 2>&1; then
            :
          else
            printf 'WARN    : %s worktree removed but local branch "%s" was not deleted\n' "$task_id" "$branch" >&2
          fi
          printf 'REAPED  : %s (branch "%s", %s, submodules verified clean)\n' "$task_id" "$branch" "$evidence"
        else
          printf 'ERROR   : failed to remove worktree %s\n' "$wt" >&2
        fi
      else
        printf 'WOULD REAP: %s (branch "%s", %s, clean, submodules verified clean) [dry-run] would run: git worktree remove --force %s\n' \
          "$task_id" "$branch" "$evidence" "$wt"
      fi
      continue
    fi
    if [ "$MODE" = "--prune" ]; then
      if git -C "$REPO_ROOT" worktree remove "$wt" 2>/dev/null; then
        if git -C "$REPO_ROOT" branch -d "$branch" >/dev/null 2>&1; then
          :
        elif [ -n "$pr_head" ] && git -C "$REPO_ROOT" branch -D "$branch" >/dev/null 2>&1; then
          :
        else
          printf 'WARN    : %s worktree removed but local branch "%s" was not deleted\n' "$task_id" "$branch" >&2
        fi
        printf 'REAPED  : %s (branch "%s", %s)\n' "$task_id" "$branch" "$evidence"
      else
        printf 'ERROR   : failed to remove worktree %s\n' "$wt" >&2
      fi
    else
      printf 'WOULD REAP: %s (branch "%s", %s, clean) [dry-run]\n' "$task_id" "$branch" "$evidence"
    fi
    continue
  fi

  # --- not merged --------------------------------------------------------
  if [ "$age_sec" -lt "$THRESHOLD" ]; then
    printf 'KEEP    : %s (fresh — age %ds < threshold %ds)\n' \
      "$task_id" "$age_sec" "$THRESHOLD"
    continue
  fi

  if [ -n "$BASE_REF" ]; then
    ahead=$(git -C "$REPO_ROOT" rev-list --count "$BASE_REF..$tip" 2>/dev/null || echo '?')
  else
    ahead='?'
  fi
  short=$(git -C "$wt" rev-parse --short HEAD 2>/dev/null || echo "$tip")
  printf 'CONFIRM : %s (branch "%s", tip %s, %s commits not merged, no open PR, age %ds) — needs user confirmation; never deleted automatically\n' \
    "$task_id" "$branch" "$short" "$ahead" "$age_sec"
done

if [ "$FOUND" = "0" ]; then
  printf 'No worktree entries found under %s\n' "$WT_ROOT"
fi
