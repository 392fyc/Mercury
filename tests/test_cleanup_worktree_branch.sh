#!/usr/bin/env bash
# test_cleanup_worktree_branch.sh — behavior tests for scripts/cleanup-worktree-branch.sh
#
# Usage: bash tests/test_cleanup_worktree_branch.sh
#
# Runs in an ephemeral sandbox under TMPDIR. Each test creates a fresh git repo, sets up the
# pre-conditions, invokes the script, asserts post-conditions, and tears down. Exit code is the
# number of failed tests (0 = all pass).

set -u

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
SCRIPT="$REPO_ROOT/scripts/cleanup-worktree-branch.sh"

[ -x "$SCRIPT" ] || { echo "FAIL: $SCRIPT not executable"; exit 1; }

FAILED=0
TOTAL=0

pass() { echo "PASS: $1"; }
fail() { echo "FAIL: $1"; FAILED=$((FAILED + 1)); }

# Create an isolated sandbox repo. Caller receives the sandbox path on stdout.
mk_sandbox() {
  local sb
  sb=$(mktemp -d)
  (
    cd "$sb" || exit 1
    git init -q -b main
    git config user.email "test@example.com"
    git config user.name "Test"
    echo "seed" > README.md
    git add README.md
    git commit -q -m "seed"
    # Feature branch + worktree sitting on it
    git branch feat/test
    mkdir -p .worktrees
    git worktree add .worktrees/feat-test feat/test -q
  ) || return 1
  echo "$sb"
}

cleanup_sandbox() {
  local sb="$1"
  # best-effort: worktrees might still be registered
  [ -d "$sb" ] || return 0
  (cd "$sb" && git worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0,10)}' | while read -r p; do
    [ "$p" = "$sb" ] && continue
    git worktree remove --force "$p" 2>/dev/null || true
  done) || true
  rm -rf "$sb" 2>/dev/null || true
}

# ---------- test: happy path with --force + explicit path ----------
TOTAL=$((TOTAL + 1))
test_force_explicit_path() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    bash "$SCRIPT" feat/test main --force --worktree-path "$sb/.worktrees/feat-test"
    rc=$?
    [ "$rc" -eq 0 ] || { echo "exit code $rc"; exit 1; }
    # worktree gone
    [ ! -d "$sb/.worktrees/feat-test" ] || { echo "worktree still exists"; exit 1; }
    # branch gone
    git show-ref --verify --quiet refs/heads/feat/test && { echo "branch not deleted"; exit 1; } || true
  )
  if [ $? -eq 0 ]; then pass "force + explicit path removes worktree and branch"
  else fail "force + explicit path removes worktree and branch"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: safe mode + auto-discovery on clean worktree ----------
TOTAL=$((TOTAL + 1))
test_safe_auto_discover() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    bash "$SCRIPT" feat/test main
    rc=$?
    [ "$rc" -eq 0 ] || { echo "exit code $rc"; exit 1; }
    [ ! -d "$sb/.worktrees/feat-test" ] || { echo "worktree still exists"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test && { echo "branch not deleted"; exit 1; } || true
  )
  if [ $? -eq 0 ]; then pass "safe mode + auto-discover removes clean worktree and branch"
  else fail "safe mode + auto-discover removes clean worktree and branch"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: dry-run preserves state ----------
TOTAL=$((TOTAL + 1))
test_dry_run() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    out=$(bash "$SCRIPT" feat/test main --force --dry-run 2>&1)
    rc=$?
    [ "$rc" -eq 0 ] || { echo "exit code $rc"; exit 1; }
    # worktree + branch must still exist
    [ -d "$sb/.worktrees/feat-test" ] || { echo "worktree removed in dry-run"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch removed in dry-run"; exit 1; }
    echo "$out" | grep -q '\[dry-run\]' || { echo "no [dry-run] marker in output"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "dry-run preserves worktree and branch"
  else fail "dry-run preserves worktree and branch"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: safe mode refuses dirty worktree ----------
TOTAL=$((TOTAL + 1))
test_safe_mode_dirty_skip() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    # Introduce uncommitted changes in the worktree
    echo "dirty" > "$sb/.worktrees/feat-test/dirty.txt"
    bash "$SCRIPT" feat/test main 2>&1
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc"; exit 1; }
    # worktree + branch preserved
    [ -d "$sb/.worktrees/feat-test" ] || { echo "worktree removed despite dirty"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch removed despite dirty worktree"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "safe mode skips dirty worktree and preserves branch"
  else fail "safe mode skips dirty worktree and preserves branch"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: missing args -> exit 2 ----------
TOTAL=$((TOTAL + 1))
test_missing_args() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    bash "$SCRIPT" 2>/dev/null
    rc=$?
    [ "$rc" -eq 2 ] || { echo "expected exit 2, got $rc"; exit 1; }
    bash "$SCRIPT" feat/test 2>/dev/null
    rc=$?
    [ "$rc" -eq 2 ] || { echo "expected exit 2 for one-arg, got $rc"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "missing args exit 2"
  else fail "missing args exit 2"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: rm -rf fallback refuses path outside .worktrees/ ----------
TOTAL=$((TOTAL + 1))
test_rmrf_whitelist_guard() {
  # Bind a path that does NOT match $REPO_ROOT/.worktrees/. Use --force + explicit path.
  # The script will attempt `git worktree remove --force` which succeeds if git knows it, or
  # fails (retry + fallback). We only care that the fallback does not fire outside the
  # whitelist. Simulate by pointing to a path outside .worktrees/ that git has never registered
  # — `git worktree remove --force` returns non-zero, retry non-zero, then the case-guard
  # should refuse.
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p "$sb/not-a-worktree"
    echo "sentinel" > "$sb/not-a-worktree/SENTINEL"
    bash "$SCRIPT" feat/test main --force --worktree-path "$sb/not-a-worktree" 2>&1 | grep -q "refuse to rm -rf" \
      || { echo "expected 'refuse to rm -rf' warning"; exit 1; }
    # Sentinel must still be present — rm -rf fallback MUST NOT have fired
    [ -f "$sb/not-a-worktree/SENTINEL" ] || { echo "sentinel deleted — whitelist bypass"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "rm -rf fallback refuses path outside .worktrees/"
  else fail "rm -rf fallback refuses path outside .worktrees/"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: pre-switch fires when HEAD is on target branch ----------
TOTAL=$((TOTAL + 1))
test_pre_switch_runs() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    # Main worktree HEAD is on main. Move it to feat/test so cleanup must switch off.
    # Remove the extra worktree first (so switching is legal — can't checkout a branch
    # that's already checked out in another worktree).
    git worktree remove --force "$sb/.worktrees/feat-test"
    git switch feat/test
    bash "$SCRIPT" feat/test main
    rc=$?
    [ "$rc" -eq 0 ] || { echo "exit code $rc"; exit 1; }
    # HEAD should have moved back to main
    [ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "HEAD not switched to main"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test && { echo "branch not deleted"; exit 1; } || true
  )
  if [ $? -eq 0 ]; then pass "pre-switch moves HEAD to base branch before cleanup"
  else fail "pre-switch moves HEAD to base branch before cleanup"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: --force still deletes branch when worktree cleanup fails ----------
TOTAL=$((TOTAL + 1))
test_force_deletes_branch_even_on_wt_fail() {
  # Simulate the Windows file-lock scenario: --force + a worktree that git removes from
  # metadata but whose physical directory we then block. On Linux we simulate by pointing
  # --worktree-path at a real directory that is NOT under `$REPO_ROOT/.worktrees/` so the
  # rm -rf fallback refuses, forcing WT_FAIL=1. In --force mode the branch should STILL
  # be deleted (matches pre-refactor dev-pipeline behavior).
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p "$sb/outside"
    # Remove the real worktree first so the branch is not "checked out" anywhere.
    git worktree remove --force "$sb/.worktrees/feat-test"
    bash "$SCRIPT" feat/test main --force --worktree-path "$sb/outside" 2>&1
    rc=$?
    # Exit 1 because WT_FAIL=1 (whitelist refused), but branch should be deleted.
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test && { echo "branch NOT deleted in force-mode"; exit 1; } || true
  )
  if [ $? -eq 0 ]; then pass "force mode deletes branch even when worktree cleanup fails"
  else fail "force mode deletes branch even when worktree cleanup fails"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: --worktree-path rejects newline injection ----------
TOTAL=$((TOTAL + 1))
test_worktree_path_rejects_newline() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p "$sb/innocent-target"
    echo "sentinel" > "$sb/innocent-target/SENTINEL"
    git worktree remove --force "$sb/.worktrees/feat-test"
    # Path with embedded newline: the first line is an attacker-controlled target.
    local malicious="$sb/innocent-target"$'\n'"$sb/.worktrees/feat-test"
    out=$(bash "$SCRIPT" feat/test main --force --worktree-path "$malicious" 2>&1)
    rc=$?
    [ "$rc" -eq 2 ] || { echo "expected exit 2, got $rc"; exit 1; }
    echo "$out" | grep -q "single-line path" || { echo "no rejection message"; exit 1; }
    [ -f "$sb/innocent-target/SENTINEL" ] || { echo "sentinel deleted — newline injection worked"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "--worktree-path rejects newline-injected value"
  else fail "--worktree-path rejects newline-injected value"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: remote URL credentials are stripped from observability echo ----------
TOTAL=$((TOTAL + 1))
test_remote_credential_strip() {
  # Covers three forms Argus flagged across iter-3 and iter-4:
  #   (a) https://user:token@host/path — classic HTTPS with token
  #   (b) ssh://user:token@host/path   — SSH URL form with token
  #   (c) user:token@host:path         — SCP-like form with credentials
  # All must be redacted. Plain `git@host:path` SSH (no colon in userinfo) must pass through.
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    git worktree remove --force "$sb/.worktrees/feat-test"

    for url_variant in \
      "https://user:supersecret@example.com/repo.git" \
      "ssh://user:supersecret@example.com/repo.git" \
      "user:supersecret@example.com:repo.git" \
      "https://supersecret@example.com/repo.git" \
      "https://supersecret-gh-pat-12345@example.com/repo.git"; do
      git remote remove origin 2>/dev/null || true
      git remote add origin "$url_variant"
      # Reuse the same branch for each variant: recreate feat/test if it was deleted by a prior run.
      git show-ref --verify --quiet refs/heads/feat/test || git branch feat/test
      out=$(bash "$SCRIPT" feat/test main 2>&1)
      echo "$out" | grep -q "supersecret" && { echo "credential leaked for variant: $url_variant => $out"; exit 1; }
      echo "$out" | grep -q "\[REDACTED\]@" || { echo "no [REDACTED] marker for variant: $url_variant"; exit 1; }
    done

    # Bare git@host:path SSH (no colon in userinfo — not a credential form) must pass through unchanged.
    git remote remove origin
    git remote add origin "git@example.com:repo.git"
    git show-ref --verify --quiet refs/heads/feat/test || git branch feat/test
    out=$(bash "$SCRIPT" feat/test main 2>&1)
    echo "$out" | grep -q "remote=git@example.com:repo.git" || { echo "plain git@host:path mangled: $out"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "remote URL credentials stripped across https/ssh/scp-like forms"
  else fail "remote URL credentials stripped across https/ssh/scp-like forms"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: rm -rf whitelist resists `..` path traversal ----------
TOTAL=$((TOTAL + 1))
test_rmrf_traversal_guard() {
  # Attacker passes a path that textually starts with "$REPO_ROOT/.worktrees/" but, after
  # canonicalization, resolves OUTSIDE the whitelist via `..`. The script MUST refuse and
  # preserve the sentinel file in the escape target.
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p "$sb/escape-target"
    echo "sentinel" > "$sb/escape-target/SENTINEL"
    # Traversal path: textually inside .worktrees/ but resolves to $sb/escape-target.
    local traversal="$sb/.worktrees/../escape-target"
    git worktree remove --force "$sb/.worktrees/feat-test"
    bash "$SCRIPT" feat/test main --force --worktree-path "$traversal" 2>&1 | grep -q "refuse to rm -rf" \
      || { echo "expected 'refuse to rm -rf' warning on traversal path"; exit 1; }
    [ -f "$sb/escape-target/SENTINEL" ] || { echo "sentinel deleted — traversal guard bypassed"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "rm -rf refuses canonicalized path outside whitelist (traversal guard)"
  else fail "rm -rf refuses canonicalized path outside whitelist (traversal guard)"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: branch with no registered worktree -> safe mode still deletes branch ----------
TOTAL=$((TOTAL + 1))
test_no_worktree_still_deletes_branch() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    # Remove the worktree first so the branch exists but has no registered worktree.
    git worktree remove --force "$sb/.worktrees/feat-test"
    bash "$SCRIPT" feat/test main
    rc=$?
    [ "$rc" -eq 0 ] || { echo "expected exit 0, got $rc"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test && { echo "branch not deleted"; exit 1; } || true
  )
  if [ $? -eq 0 ]; then pass "safe mode deletes branch when no worktree is registered"
  else fail "safe mode deletes branch when no worktree is registered"
  fi
  cleanup_sandbox "$sb"
}

# ---------- run all tests ----------

test_force_explicit_path
test_safe_auto_discover
test_dry_run
test_safe_mode_dirty_skip
test_missing_args
test_rmrf_whitelist_guard
test_pre_switch_runs
test_force_deletes_branch_even_on_wt_fail
test_no_worktree_still_deletes_branch
test_rmrf_traversal_guard
test_remote_credential_strip
test_worktree_path_rejects_newline

echo ""
echo "Summary: $((TOTAL - FAILED))/$TOTAL tests passed"
exit $FAILED
