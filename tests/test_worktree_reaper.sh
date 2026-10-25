#!/usr/bin/env bash
# tests/test_worktree_reaper.sh
# Behavior tests for scripts/worktree-reaper.sh (lifecycle rules: Issue #663).
#
# Strategy:
#   - Builds a real throw-away git repo with real worktrees under <repo>/.worktrees/
#   - Stubs only `gh` (PATH shim): `pr list --state open` and `pr list --state merged --head X`
#   - WORKTREE_BASE_REF=main stands in for origin/<default branch> (no remote needed)
#   - Backdates worktree directories with touch -d to cross the age threshold
#
# Cases (branch -> expected):
#   TASK-merged        ancestor of main, old, clean          -> REAPED (worktree + branch gone)
#   TASK-squash        unmerged commit, fresh, but its tip is a merged PR head, clean
#                                                            -> REAPED with branch -D
#   TASK-merged-dirty  ancestor of main, old, untracked file -> DIRTY, kept
#   TASK-abandoned     unmerged commit, old, no PR           -> CONFIRM only, never deleted
#   TASK-fresh         unmerged commit, fresh                -> KEEP
#   TASK-open-pr       unmerged commit, old, open PR         -> KEEP
#   TASK-newborn       == main tip, fresh, no commit, no PR  -> KEEP (must not be reaped)
#   TASK-newborn-old   == main tip, old, no own commit, no PR -> CONFIRM only (ancestry alone is not
#                      proof of a merge), never deleted
#   lanes/             lane-level worktrees dir -> SKIP line, contents untouched
#   TASK-ignored       merged, old, clean but holds an ignored file (.tmp/x) -> DIRTY, kept
#   TASK-pycache       merged, old, only a __pycache__ ignored -> REAPED
#   TASK-broken        merged, old, .git link broken            -> SKIP, untouched
#   stray/             plain directory under .worktrees         -> SKIP, untouched
#   develop            protected branch, merged, old         -> KEEP (never touched)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="${SCRIPT_DIR}/../scripts/worktree-reaper.sh"

backdate() {
  # $1 = path, $2 = days ago
  if touch -d "$2 days ago" "$1" 2>/dev/null; then return 0; fi
  if command -v perl >/dev/null 2>&1; then
    perl -e "my \$t = time() - $2*86400; utime(\$t,\$t,'$1');"
    return 0
  fi
  return 1
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
REPO="${TMP}/repo"
SHIM="${TMP}/shim"
mkdir -p "$REPO" "$SHIM"

FAILED=0
TESTS=0
ok()   { TESTS=$((TESTS + 1)); printf 'ok %d - %s\n' "$TESTS" "$1"; }
notok() { TESTS=$((TESTS + 1)); FAILED=$((FAILED + 1)); printf 'not ok %d - %s\n' "$TESTS" "$1"; [ -n "${2:-}" ] && printf '# %s\n' "$2"; return 0; }

# ---------------------------------------------------------------------------
# Repo with worktrees
# ---------------------------------------------------------------------------
(
  cd "$REPO" || exit 1
  git init -q -b main
  git config user.email "test@example.com"
  git config user.name "Test"
  echo seed > README.md
  git add README.md
  git commit -q -m seed
) || { echo "1..0 # SKIP cannot create repo"; exit 0; }

git_r() { git -C "$REPO" "$@"; }

# add_wt <task> <branch> [commits]  — creates worktree on a new branch with N commits (default 0)
add_wt() {
  local task="$1" branch="$2" n="${3:-0}" i
  git_r worktree add -q -b "$branch" "$REPO/.worktrees/$task" main 2>/dev/null
  for i in $(seq 1 "$n"); do
    echo "$task $i" > "$REPO/.worktrees/$task/$task-$i.txt"
    git -C "$REPO/.worktrees/$task" add "$task-$i.txt"
    git -C "$REPO/.worktrees/$task" commit -q -m "$task $i"
  done
}

add_wt TASK-newborn feat/TASK-newborn 0
add_wt TASK-newborn-old feat/TASK-newborn-old 0
add_wt TASK-fresh feat/TASK-fresh 1
add_wt TASK-open-pr feat/TASK-open-pr 1
add_wt TASK-abandoned feat/TASK-abandoned 1
add_wt TASK-squash feat/TASK-squash 1
# merged-by-ff: commit on branch, then fast-forward main to it (branch tip becomes ancestor of main)
add_wt TASK-merged feat/TASK-merged 1
add_wt TASK-merged-dirty feat/TASK-merged-dirty 1
add_wt TASK-protected develop 1
git_r merge -q --ff-only feat/TASK-merged
git_r merge -q feat/TASK-merged-dirty -m merge-dirty
git_r merge -q develop -m merge-develop
printf '.tmp/\n__pycache__/\n*.pyc\n' >> "$REPO/.git/info/exclude"
add_wt TASK-ignored feat/TASK-ignored 1
add_wt TASK-pycache feat/TASK-pycache 1
add_wt TASK-broken feat/TASK-broken 1
git_r merge -q feat/TASK-ignored -m merge-ignored
git_r merge -q feat/TASK-pycache -m merge-pycache
git_r merge -q feat/TASK-broken -m merge-broken
mkdir -p "$REPO/.worktrees/TASK-ignored/.tmp" "$REPO/.worktrees/TASK-pycache/pkg/__pycache__"
echo secret > "$REPO/.worktrees/TASK-ignored/.tmp/x"
echo x > "$REPO/.worktrees/TASK-pycache/pkg/__pycache__/m.pyc"
echo important > "$REPO/.worktrees/TASK-broken/keep.txt"
echo "gitdir: /nonexistent/gitdir" > "$REPO/.worktrees/TASK-broken/.git"
mkdir -p "$REPO/.worktrees/stray"
echo keep > "$REPO/.worktrees/stray/f"
# lane-level worktree (merged, clean, old): must be skipped, never reaped
mkdir -p "$REPO/.worktrees/lanes"
git_r worktree add -q -b lane/side/init "$REPO/.worktrees/lanes/side" main

echo "scratch" > "$REPO/.worktrees/TASK-merged-dirty/untracked.txt"

SQUASH_TIP=$(git -C "$REPO/.worktrees/TASK-squash" rev-parse HEAD)

for t in TASK-open-pr TASK-abandoned TASK-merged TASK-merged-dirty TASK-newborn-old TASK-protected TASK-ignored TASK-pycache TASK-broken stray lanes; do
  backdate "$REPO/.worktrees/$t" 30 || { echo "1..0 # SKIP cannot backdate"; exit 0; }
done

# ---------------------------------------------------------------------------
# gh stub
# ---------------------------------------------------------------------------
STUBDATA="${TMP}/ghdata"
mkdir -p "$STUBDATA"
printf 'feat/TASK-open-pr\n' > "$STUBDATA/open.txt"
printf '%s\n' "$SQUASH_TIP" > "$STUBDATA/merged_feat_TASK-squash"
cat > "${SHIM}/gh" <<EOF
#!/usr/bin/env bash
# Fake gh: only \`pr list\` is supported.
[ "\$1" = "pr" ] && [ "\$2" = "list" ] || exit 0
state=""; head=""
shift 2
while [ \$# -gt 0 ]; do
  case "\$1" in
    --state) state="\$2"; shift 2 ;;
    --head) head="\$2"; shift 2 ;;
    *) shift ;;
  esac
done
if [ "\$state" = "open" ]; then cat "${STUBDATA}/open.txt"; exit 0; fi
if [ "\$state" = "merged" ]; then
  f="${STUBDATA}/merged_\$(printf '%s' "\$head" | tr '/' '_')"
  [ -f "\$f" ] && cat "\$f"
  exit 0
fi
exit 0
EOF
chmod +x "${SHIM}/gh"

run_reaper() {
  ( cd "$REPO" && WORKTREE_AGE_DAYS=7 WORKTREE_BASE_REF=main PATH="${SHIM}:${PATH}" bash "$REAPER" "$@" 2>&1 )
}

branch_exists() { git_r show-ref --verify --quiet "refs/heads/$1"; }

printf '1..%s\n' "28"

# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------
OUT=$(run_reaper --dry-run)
ALL_PRESENT=1
for t in newborn newborn-old protected ignored pycache broken fresh open-pr abandoned squash merged merged-dirty; do
  [ -d "$REPO/.worktrees/TASK-$t" ] || ALL_PRESENT=0
done
[ "$ALL_PRESENT" -eq 1 ] && ok "dry-run deletes nothing" || notok "dry-run deleted a worktree" "$OUT"

printf '%s\n' "$OUT" | grep -q 'WOULD REAP.*TASK-merged ' && ok "dry-run: merged+clean reported as WOULD REAP" || notok "dry-run: no WOULD REAP for TASK-merged" "$OUT"
printf '%s\n' "$OUT" | grep -q 'WOULD REAP.*TASK-squash' && ok "dry-run: squash-merged (merged PR head) reported as WOULD REAP" || notok "dry-run: no WOULD REAP for TASK-squash" "$OUT"
printf '%s\n' "$OUT" | grep -q 'CONFIRM.*TASK-abandoned.*1 commits not merged' && ok "dry-run: abandoned branch listed as CONFIRM with unmerged count" || notok "dry-run: no CONFIRM line for TASK-abandoned" "$OUT"
printf '%s\n' "$OUT" | grep -q 'CONFIRM.*TASK-newborn-old' && ok "dry-run: aged branch without own commit is CONFIRM, not WOULD REAP" || notok "dry-run: TASK-newborn-old not CONFIRM" "$OUT"
printf '%s\n' "$OUT" | grep -q 'WOULD REAP.*TASK-newborn' && notok "dry-run: newborn worktree marked WOULD REAP" "$OUT" || ok "dry-run: no newborn worktree is WOULD REAP"
printf '%s\n' "$OUT" | grep -q 'KEEP.*TASK-protected.*protected' && ok "dry-run: protected branch reported KEEP" || notok "dry-run: protected branch not KEEP" "$OUT"
printf '%s\n' "$OUT" | grep -q 'DIRTY.*TASK-ignored.*\.tmp/' && ok "dry-run: ignored content reported as DIRTY with the path" || notok "dry-run: TASK-ignored not DIRTY" "$OUT"
printf '%s\n' "$OUT" | grep -q 'DIRTY.*TASK-merged-dirty' && ok "dry-run: merged but dirty reported as DIRTY" || notok "dry-run: no DIRTY line" "$OUT"

# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------
OUT=$(run_reaper --prune)

{ [ ! -d "$REPO/.worktrees/TASK-merged" ] && ! branch_exists feat/TASK-merged; } \
  && ok "prune: merged+clean worktree and local branch removed" || notok "prune: TASK-merged not fully removed" "$OUT"

{ [ ! -d "$REPO/.worktrees/TASK-squash" ] && ! branch_exists feat/TASK-squash; } \
  && ok "prune: squash-merged worktree removed and branch deleted via -D" || notok "prune: TASK-squash not fully removed" "$OUT"

{ [ -f "$REPO/.worktrees/TASK-merged-dirty/untracked.txt" ] && branch_exists feat/TASK-merged-dirty; } \
  && ok "prune: dirty merged worktree and branch kept" || notok "prune: dirty merged worktree was touched" "$OUT"
printf '%s\n' "$OUT" | grep -q 'DIRTY.*TASK-merged-dirty' && ok "prune: DIRTY line printed" || notok "prune: no DIRTY line" "$OUT"

{ [ -d "$REPO/.worktrees/TASK-abandoned" ] && branch_exists feat/TASK-abandoned; } \
  && ok "prune: unmerged abandoned worktree and branch NOT deleted" || notok "prune: unmerged abandoned worktree was deleted" "$OUT"
printf '%s\n' "$OUT" | grep -q 'CONFIRM.*TASK-abandoned' && ok "prune: CONFIRM line printed for abandoned branch" || notok "prune: no CONFIRM line" "$OUT"

{ [ -d "$REPO/.worktrees/TASK-open-pr" ] && branch_exists feat/TASK-open-pr; } \
  && ok "prune: stale worktree with open PR kept" || notok "prune: open-PR worktree deleted" "$OUT"
{ [ -d "$REPO/.worktrees/TASK-fresh" ] && branch_exists feat/TASK-fresh; } \
  && ok "prune: fresh unmerged worktree kept" || notok "prune: fresh worktree deleted" "$OUT"
{ [ -d "$REPO/.worktrees/TASK-newborn" ] && branch_exists feat/TASK-newborn; } \
  && ok "prune: newborn worktree (no commits yet) kept" || notok "prune: newborn worktree deleted" "$OUT"

{ [ -d "$REPO/.worktrees/TASK-newborn-old" ] && branch_exists feat/TASK-newborn-old; } \
  && ok "prune: aged branch without own commit kept (CONFIRM only)" || notok "prune: TASK-newborn-old deleted" "$OUT"
printf '%s\n' "$OUT" | grep -q 'SKIP : lanes (lane-level worktrees are closed by the lane-close procedure)' \
  && ok "prune: lanes directory is skipped with a SKIP line" || notok "prune: no SKIP line for lanes" "$OUT"
{ [ -d "$REPO/.worktrees/lanes/side" ] && branch_exists lane/side/init; } \
  && ok "prune: lane-level worktree and branch untouched" || notok "prune: lane worktree touched" "$OUT"
{ [ -f "$REPO/.worktrees/TASK-ignored/.tmp/x" ] && branch_exists feat/TASK-ignored; } \
  && ok "prune: worktree with ignored content kept" || notok "prune: ignored content deleted" "$OUT"
{ [ ! -d "$REPO/.worktrees/TASK-pycache" ] && ! branch_exists feat/TASK-pycache; } \
  && ok "prune: worktree with only __pycache__ ignored is reaped" || notok "prune: pycache-only worktree not reaped" "$OUT"
{ [ -f "$REPO/.worktrees/TASK-broken/keep.txt" ] && branch_exists feat/TASK-broken; } \
  && ok "prune: worktree with broken .git link untouched" || notok "prune: broken-link worktree touched" "$OUT"
{ [ -f "$REPO/.worktrees/stray/f" ]; } \
  && ok "prune: stray non-worktree directory untouched" || notok "prune: stray directory deleted" "$OUT"
{ [ -d "$REPO/.worktrees/TASK-protected" ] && branch_exists develop; } \
  && ok "prune: protected branch 'develop' and its worktree kept" || notok "prune: protected branch touched" "$OUT"

# Never --force: a locked worktree cannot be removed by a plain remove, so it must survive.
add_wt TASK-locked feat/TASK-locked 1
git_r merge -q feat/TASK-locked -m merge-locked
git_r worktree lock "$REPO/.worktrees/TASK-locked"
backdate "$REPO/.worktrees/TASK-locked" 30
OUT=$(run_reaper --prune)
[ -d "$REPO/.worktrees/TASK-locked" ] && ok "prune: locked merged worktree survives (no --force)" || notok "prune: locked worktree removed (force used?)" "$OUT"
git_r worktree unlock "$REPO/.worktrees/TASK-locked"

# prune refuses when gh cannot list open PRs
EMPTYBIN="${TMP}/nogh"
mkdir -p "$EMPTYBIN"
cat > "$EMPTYBIN/gh" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
chmod +x "$EMPTYBIN/gh"
( cd "$REPO" && PATH="${EMPTYBIN}:${PATH}" WORKTREE_BASE_REF=main bash "$REAPER" --prune >/dev/null 2>&1 )
[ $? -ne 0 ] && ok "prune refuses to run when gh cannot list open PRs" || notok "prune ran without gh PR data"

printf '# %d tests, %d failed\n' "$TESTS" "$FAILED"
exit "$FAILED"
