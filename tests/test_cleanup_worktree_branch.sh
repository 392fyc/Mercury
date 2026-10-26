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

# No test may reach the real GitHub: put a failing `gh` first on PATH. Tests that need a working
# gh prepend their own stub in front of this one.
NOGH_DIR=$(mktemp -d)
printf '#!/usr/bin/env bash\nexit 1\n' > "$NOGH_DIR/gh"
chmod +x "$NOGH_DIR/gh"
export PATH="$NOGH_DIR:$PATH"
trap 'rm -rf "$NOGH_DIR"' EXIT

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
    # Feature branch + worktree sitting on it. The branch gets one commit of its own which is then
    # fast-forwarded into main: a verifiably merged branch (own commit, fully contained in main).
    git branch feat/test
    mkdir -p .worktrees
    git worktree add .worktrees/feat-test feat/test -q
    (cd .worktrees/feat-test && echo "work0" > work0.txt && git add work0.txt && git commit -q -m "work0")
    git merge -q --ff-only feat/test
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
    bash "$SCRIPT" feat/test main --force --worktree-path "$sb/not-a-worktree" 2>&1 | grep -q "not the root of a registered worktree" \
      || { echo "expected 'not the root of a registered worktree' refusal"; exit 1; }
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

# ---------- test: explicit path that is not a registered worktree is refused, nothing deleted ----------
TOTAL=$((TOTAL + 1))
test_force_deletes_branch_even_on_wt_fail() {
  # Formerly: --force deleted the branch even when the worktree path was bogus. Since #663 an
  # explicit path must be a registered worktree root checking out BRANCH; otherwise exit 1 and
  # NOTHING is deleted (branch included). Branch deletion despite worktree failures is covered by
  # the pre-switch and worktree-list failure tests below.
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p "$sb/outside"
    echo keep > "$sb/outside/f"
    git worktree remove --force "$sb/.worktrees/feat-test"
    bash "$SCRIPT" feat/test main --force --worktree-path "$sb/outside" 2>&1
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc"; exit 1; }
    [ -f "$sb/outside/f" ] || { echo "outside dir touched"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch deleted despite refusal"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "explicit non-worktree path is refused and nothing is deleted"
  else fail "explicit non-worktree path is refused and nothing is deleted"
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

    # The script fetches origin after deleting the branch; keep this test offline.
    local real_git_c fetch_bin
    real_git_c=$(command -v git)
    fetch_bin=$(mktemp -d)
    printf '#!/usr/bin/env bash\nif [ "$1" = "fetch" ] || [ "$1" = "ls-remote" ]; then exit 1; fi\nexec "%s" "$@"\n' "$real_git_c" > "$fetch_bin/git"
    chmod +x "$fetch_bin/git"
    export PATH="$fetch_bin:$PATH"

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
    bash "$SCRIPT" feat/test main --force --worktree-path "$traversal" 2>&1 | grep -q "not the root of a registered worktree" \
      || { echo "expected refusal on traversal path"; exit 1; }
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

# ---------- test: --force falls through to branch-d when pre-switch fails (nit 3) ----------
TOTAL=$((TOTAL + 1))
test_force_preswitch_fail_still_deletes_branch() {
  # Simulate pre-switch failure under --force: inject a fake git wrapper on PATH that makes
  # `git switch` and `git checkout` fail, but passes all other git subcommands through.
  # We verify that the branch-d path is still *attempted* (WT_FAIL=1 does not skip it).
  # Because HEAD stays on feat/test (switch was blocked), git will refuse the delete —
  # but the WARN message from the branch-d attempt confirms it fired.
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  # Resolve real git path outside the subshell (before PATH is modified).
  local real_git
  real_git=$(command -v git)
  (
    cd "$sb" || exit 1
    # Remove the real worktree and prune so no other worktree references feat/test.
    "$real_git" worktree remove --force "$sb/.worktrees/feat-test"
    "$real_git" worktree prune
    # Move HEAD onto the branch so a pre-switch is actually attempted by the script.
    "$real_git" switch feat/test

    # Create a fake git wrapper that intercepts switch and checkout.
    local fake_bin
    fake_bin=$(mktemp -d)
    cat > "$fake_bin/git" <<FAKEGIT
#!/usr/bin/env bash
# Intercept switch and checkout; pass everything else to real git.
if [ "\$1" = "switch" ] || [ "\$1" = "checkout" ]; then
  exit 1
fi
exec "$real_git" "\$@"
FAKEGIT
    chmod +x "$fake_bin/git"

    out=$(PATH="$fake_bin:$PATH" bash "$SCRIPT" feat/test main --force 2>&1)
    rc=$?
    rm -rf "$fake_bin"
    # Exit must be 1 (WT_FAIL path, not exit 2).
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc; output: $out"; exit 1; }
    # The WARN for failed switch must appear (pre-switch fired).
    echo "$out" | grep -q "failed to switch off branch" || { echo "no pre-switch warning; output: $out"; exit 1; }
    # The branch-d attempt must also appear in output (fired even though WT_FAIL=1).
    echo "$out" | grep -Eq 'git branch -d|branch -d feat/test failed' || { echo "no branch-d attempt in output; output: $out"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "force mode: pre-switch failure still fires branch-d (nit 3)"
  else fail "force mode: pre-switch failure still fires branch-d (nit 3)"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: --force falls through to branch-d when worktree list fails (nit 4) ----------
TOTAL=$((TOTAL + 1))
test_force_wtlist_fail_still_deletes_branch() {
  # Simulate `git worktree list` failure under --force: inject a fake git wrapper that makes
  # `git worktree list` exit non-zero, passes everything else through.
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  # Resolve real git path outside the subshell (before PATH is modified).
  local real_git
  real_git=$(command -v git)
  (
    cd "$sb" || exit 1
    # Remove the real worktree so the branch is not checked out anywhere else.
    "$real_git" worktree remove --force "$sb/.worktrees/feat-test"

    # Create a fake git wrapper in a temp bin dir.
    local fake_bin
    fake_bin=$(mktemp -d)
    cat > "$fake_bin/git" <<FAKEGIT
#!/usr/bin/env bash
# Intercept \`git worktree list\`; pass everything else to real git.
if [ "\$1" = "worktree" ] && [ "\$2" = "list" ]; then
  echo "simulated worktree list failure" >&2
  exit 1
fi
exec "$real_git" "\$@"
FAKEGIT
    chmod +x "$fake_bin/git"

    PATH="$fake_bin:$PATH" bash "$SCRIPT" feat/test main --force 2>&1
    rc=$?
    rm -rf "$fake_bin"
    # Should exit 1 (WT_FAIL=1 due to list failure) but branch must be deleted.
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc"; exit 1; }
    "$real_git" show-ref --verify --quiet refs/heads/feat/test && { echo "branch NOT deleted despite --force"; exit 1; } || true
  )
  if [ $? -eq 0 ]; then pass "force mode: worktree list failure still fires branch-d (nit 4)"
  else fail "force mode: worktree list failure still fires branch-d (nit 4)"
  fi
  cleanup_sandbox "$sb"
}

# Create a fake `gh` in a fresh dir; prints the dir. $1 = lines `gh pr list` should print (may be
# empty), $2 = exit code for `gh pr list`. Records its arguments in $dir/args.
mk_gh_stub() {
  local dir
  dir=$(mktemp -d)
  printf '%s\n' "$1" > "$dir/out"
  cat > "$dir/gh" <<STUB
#!/usr/bin/env bash
echo "\$*" >> "$dir/args"
if [ "\$1" = "pr" ] && [ "\$2" = "list" ]; then
  cat "$dir/out"
  exit $2
fi
exit 0
STUB
  chmod +x "$dir/gh"
  echo "$dir"
}

# Give feat/test one commit that main does not contain (what a squash merge leaves behind), and
# remove its worktree so the branch is deletable.
make_unmerged_branch() {
  local sb="$1"
  (
    cd "$sb/.worktrees/feat-test" || exit 1
    echo "work" > work.txt
    git add work.txt
    git commit -q -m "work"
  ) || return 1
  git -C "$sb" worktree remove "$sb/.worktrees/feat-test" || return 1
}

# Recreate feat/test as a verifiably merged branch (one own commit, fast-forwarded into main).
recreate_merged_branch() {
  git switch -q -c feat/test
  echo "again" >> work0.txt
  git add work0.txt
  git commit -q -m "again"
  git switch -q main
  git merge -q --ff-only feat/test
}

# ---------- test: --force never deletes a dirty worktree ----------
TOTAL=$((TOTAL + 1))
test_force_keeps_dirty_worktree() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    echo "precious" > "$sb/.worktrees/feat-test/dirty.txt"
    out=$(bash "$SCRIPT" feat/test main --force 2>&1)
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc; $out"; exit 1; }
    echo "$out" | grep -q "uncommitted or ignored content" || { echo "no dirty warning; $out"; exit 1; }
    [ -f "$sb/.worktrees/feat-test/dirty.txt" ] || { echo "dirty file deleted by --force"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch deleted with dirty worktree"; exit 1; }
    # same with an explicit path
    out=$(bash "$SCRIPT" feat/test main --force --worktree-path "$sb/.worktrees/feat-test" 2>&1)
    [ -f "$sb/.worktrees/feat-test/dirty.txt" ] || { echo "dirty file deleted (explicit path)"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "force mode keeps a dirty worktree and its branch"
  else fail "force mode keeps a dirty worktree and its branch"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: squash-merged branch -> -D when tip is inside a merged PR head ----------
TOTAL=$((TOTAL + 1))
test_squash_merged_uses_D() {
  local sb stub
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  make_unmerged_branch "$sb" || { fail "setup failed"; cleanup_sandbox "$sb"; return; }
  local tip
  tip=$(git -C "$sb" rev-parse feat/test)
  stub=$(mk_gh_stub "$tip" 0)
  (
    cd "$sb" || exit 1
    out=$(PATH="$stub:$PATH" bash "$SCRIPT" feat/test main 2>&1)
    rc=$?
    [ "$rc" -eq 0 ] || { echo "exit $rc; $out"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test && { echo "branch kept; $out"; exit 1; }
    echo "$out" | grep -q "merged PR head $tip" || { echo "no evidence line; $out"; exit 1; }
    grep -q -- "--state merged --head feat/test" "$stub/args" || { echo "gh not queried as expected"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "squash merge: branch deleted with -D when tip is in a merged PR head"
  else fail "squash merge: branch deleted with -D when tip is in a merged PR head"
  fi
  rm -rf "$stub"
  cleanup_sandbox "$sb"
}

# ---------- test: tip NOT contained in merged PR head -> branch kept ----------
TOTAL=$((TOTAL + 1))
test_unverified_merge_keeps_branch() {
  local sb stub
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  make_unmerged_branch "$sb" || { fail "setup failed"; cleanup_sandbox "$sb"; return; }
  # A merged PR whose head is the seed commit: it does not contain the extra commit on the branch.
  local seed
  seed=$(git -C "$sb" rev-parse main)
  stub=$(mk_gh_stub "$seed" 0)
  (
    cd "$sb" || exit 1
    out=$(PATH="$stub:$PATH" bash "$SCRIPT" feat/test main 2>&1)
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch deleted without proof; $out"; exit 1; }
    echo "$out" | grep -q "could not verify" || { echo "no warning; $out"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "unverified merge: branch kept with warning"
  else fail "unverified merge: branch kept with warning"
  fi
  rm -rf "$stub"
  cleanup_sandbox "$sb"
}

# ---------- test: gh failing -> status quo (branch kept) ----------
TOTAL=$((TOTAL + 1))
test_gh_unavailable_keeps_branch() {
  local sb stub
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  make_unmerged_branch "$sb" || { fail "setup failed"; cleanup_sandbox "$sb"; return; }
  stub=$(mk_gh_stub "" 1)
  (
    cd "$sb" || exit 1
    out=$(PATH="$stub:$PATH" bash "$SCRIPT" feat/test main 2>&1)
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1 (unverified), got $rc"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch deleted though gh failed"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "gh failure: unmerged branch kept"
  else fail "gh failure: unmerged branch kept"
  fi
  rm -rf "$stub"
  cleanup_sandbox "$sb"
}

# ---------- test: fetch --prune readback warns when remote branch remains ----------
TOTAL=$((TOTAL + 1))
test_remote_readback() {
  local sb remote
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  remote=$(mktemp -d)
  git init -q --bare "$remote"
  (
    cd "$sb" || exit 1
    git remote add origin "$remote"
    git push -q origin feat/test main 2>/dev/null
    out=$(bash "$SCRIPT" feat/test main 2>&1)
    rc=$?
    [ "$rc" -eq 0 ] || { echo "exit $rc; $out"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test && { echo "local branch kept"; exit 1; }
    echo "$out" | grep -q "origin/feat/test still exists" || { echo "no remote-still-exists warning; $out"; exit 1; }
    # Remote branch deleted (as GitHub auto-delete would): no warning.
    recreate_merged_branch
    git --git-dir="$remote" branch -q -D feat/test
    out=$(bash "$SCRIPT" feat/test main 2>&1)
    echo "$out" | grep -q "still exists" && { echo "false warning after remote delete; $out"; exit 1; }
    git show-ref --verify --quiet refs/remotes/origin/feat/test && { echo "stale remote-tracking ref not pruned"; exit 1; }
    # dry-run: no fetch, no warning
    recreate_merged_branch
    out=$(bash "$SCRIPT" feat/test main --dry-run 2>&1)
    echo "$out" | grep -q "still exists" && { echo "dry-run warned"; exit 1; }
    exit 0
  )
  if [ $? -eq 0 ]; then pass "remote readback: warns when remote branch remains, prunes when gone"
  else fail "remote readback: warns when remote branch remains, prunes when gone"
  fi
  rm -rf "$remote"
  cleanup_sandbox "$sb"
}

# ---------- test: protected branches are never cleaned up ----------
TOTAL=$((TOTAL + 1))
test_protected_branches_refused() {
  local sb remote
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  remote=$(mktemp -d)
  git init -q --bare "$remote"
  (
    cd "$sb" || exit 1
    git remote add origin "$remote"
    git push -q origin main:trunk 2>/dev/null
    git fetch -q origin
    git remote set-head origin trunk >/dev/null 2>&1
    for b in develop master trunk; do
      git branch -q "$b"
      out=$(bash "$SCRIPT" "$b" main --force 2>&1)
      rc=$?
      [ "$rc" -eq 2 ] || { echo "$b: expected exit 2, got $rc; $out"; exit 1; }
      echo "$out" | grep -q "protected branch" || { echo "$b: no protected message; $out"; exit 1; }
      git show-ref --verify --quiet "refs/heads/$b" || { echo "$b was deleted"; exit 1; }
    done
    # main itself
    out=$(bash "$SCRIPT" main develop 2>&1); rc=$?
    [ "$rc" -eq 2 ] || { echo "main: expected exit 2, got $rc"; exit 1; }
    git show-ref --verify --quiet refs/heads/main || { echo "main was deleted"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "protected branches (main/master/develop/origin default) are refused"
  else fail "protected branches (main/master/develop/origin default) are refused"
  fi
  rm -rf "$remote"
  cleanup_sandbox "$sb"
}

# ---------- test: branch with no own commit and no merged PR is never deleted ----------
TOTAL=$((TOTAL + 1))
test_newborn_branch_not_deleted() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    git worktree remove --force "$sb/.worktrees/feat-test"
    git branch -q -D feat/test
    # Cut from main, no commit of its own: an ancestor of main, but NOT merged work.
    git branch -q newborn main
    out=$(bash "$SCRIPT" newborn main --force 2>&1)
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc; $out"; exit 1; }
    echo "$out" | grep -q "could not verify" || { echo "no verify warning; $out"; exit 1; }
    git show-ref --verify --quiet refs/heads/newborn || { echo "newborn branch deleted"; exit 1; }
    # Same with a worktree on it
    git worktree add -q "$sb/.worktrees/newborn" newborn
    bash "$SCRIPT" newborn main >/dev/null 2>&1
    [ -d "$sb/.worktrees/newborn" ] || { echo "newborn worktree removed"; exit 1; }
    git show-ref --verify --quiet refs/heads/newborn || { echo "newborn branch deleted (worktree case)"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "branch without own commit or merged PR is not deleted"
  else fail "branch without own commit or merged PR is not deleted"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: lane-level worktrees are skipped by auto-discovery ----------
TOTAL=$((TOTAL + 1))
test_lane_worktree_skipped() {
  local sb
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p .worktrees/lanes
    git worktree add -q -b lane/side/init .worktrees/lanes/side main
    (cd .worktrees/lanes/side && echo l > lane.txt && git add lane.txt && git commit -q -m lane)
    git merge -q --ff-only lane/side/init
    for mode in "" "--force"; do
      out=$(bash "$SCRIPT" lane/side/init main $mode 2>&1)
      rc=$?
      [ "$rc" -eq 1 ] || { echo "[$mode] expected exit 1, got $rc; $out"; exit 1; }
      echo "$out" | grep -q "lane-level worktree" || { echo "[$mode] no lane warning; $out"; exit 1; }
      [ -d "$sb/.worktrees/lanes/side" ] || { echo "[$mode] lane worktree removed"; exit 1; }
      git show-ref --verify --quiet refs/heads/lane/side/init || { echo "[$mode] lane branch deleted"; exit 1; }
    done
    # Explicit --worktree-path (after the operator verified the lane is closed) is honoured.
    bash "$SCRIPT" lane/side/init main --worktree-path "$sb/.worktrees/lanes/side" >/dev/null 2>&1
    rc=$?
    [ "$rc" -eq 0 ] || { echo "explicit path: expected exit 0, got $rc"; exit 1; }
    [ ! -d "$sb/.worktrees/lanes/side" ] || { echo "explicit path: lane worktree still there"; exit 1; }
    git show-ref --verify --quiet refs/heads/lane/side/init && { echo "explicit path: branch kept"; exit 1; }
    exit 0
  )
  if [ $? -eq 0 ]; then pass "lane-level worktree skipped by auto-discovery, handled via --worktree-path"
  else fail "lane-level worktree skipped by auto-discovery, handled via --worktree-path"
  fi
  cleanup_sandbox "$sb"
}

# Extra negative tests (review of #663): the explicit path / dirty-state hazards.
neg_setup() { # prints sandbox; ignores .tmp/ and __pycache__/ like a real project would
  local sb
  sb=$(mk_sandbox) || return 1
  printf '.tmp/\n__pycache__/\n*.pyc\n' >> "$sb/.git/info/exclude"
  echo "$sb"
}

# ---------- test: explicit .worktrees/lanes directory is never deleted ----------
TOTAL=$((TOTAL + 1))
test_neg_explicit_lanes_dir() {
  local sb
  sb=$(neg_setup) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p .worktrees/lanes
    echo "lane data" > .worktrees/lanes/NOTE
    git worktree add -q -b lane/side/init .worktrees/lanes/side main
    echo "uncommitted" > .worktrees/lanes/side/wip.txt
    out=$(bash "$SCRIPT" feat/test main --force --worktree-path "$sb/.worktrees/lanes" 2>&1)
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc; $out"; exit 1; }
    [ -f .worktrees/lanes/NOTE ] && [ -f .worktrees/lanes/side/wip.txt ] || { echo "lanes dir content deleted"; exit 1; }
    [ -d .worktrees/feat-test ] || { echo "task worktree removed"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch deleted"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "explicit .worktrees/lanes is refused; lane content intact"
  else fail "explicit .worktrees/lanes is refused; lane content intact"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: explicit subdirectory of a dirty worktree is refused ----------
TOTAL=$((TOTAL + 1))
test_neg_explicit_subdir() {
  local sb
  sb=$(neg_setup) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p .worktrees/feat-test/sub
    echo "precious" > .worktrees/feat-test/sub/dirty.txt
    out=$(bash "$SCRIPT" feat/test main --force --worktree-path "$sb/.worktrees/feat-test/sub" 2>&1)
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc; $out"; exit 1; }
    [ -f .worktrees/feat-test/sub/dirty.txt ] || { echo "subdirectory content deleted"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "branch deleted"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "explicit subdirectory of a worktree is refused; content intact"
  else fail "explicit subdirectory of a worktree is refused; content intact"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: worktree whose .git link is broken (status fails) is not treated as clean ----------
TOTAL=$((TOTAL + 1))
test_neg_broken_git_link() {
  local sb
  sb=$(neg_setup) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    echo "precious" > .worktrees/feat-test/important.txt
    echo "gitdir: /nonexistent/gitdir" > .worktrees/feat-test/.git
    for args in "--force --worktree-path $sb/.worktrees/feat-test" "--force" ""; do
      # shellcheck disable=SC2086
      out=$(bash "$SCRIPT" feat/test main $args 2>&1)
      rc=$?
      [ "$rc" -eq 1 ] || { echo "[$args] expected exit 1, got $rc; $out"; exit 1; }
      [ -f .worktrees/feat-test/important.txt ] || { echo "[$args] worktree content deleted"; exit 1; }
      git show-ref --verify --quiet refs/heads/feat/test || { echo "[$args] branch deleted"; exit 1; }
    done
  )
  if [ $? -eq 0 ]; then pass "unverifiable (broken .git link) worktree is kept in all modes"
  else fail "unverifiable (broken .git link) worktree is kept in all modes"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: mistyped branch + explicit path never removes the worktree ----------
TOTAL=$((TOTAL + 1))
test_neg_branch_typo_with_path() {
  local sb
  sb=$(neg_setup) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    out=$(bash "$SCRIPT" feat/tset main --force --worktree-path "$sb/.worktrees/feat-test" 2>&1)
    rc=$?
    [ "$rc" -eq 1 ] || { echo "expected exit 1, got $rc; $out"; exit 1; }
    [ -d .worktrees/feat-test ] || { echo "worktree removed for the wrong branch"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test || { echo "real branch deleted"; exit 1; }
    # No such branch and no worktree attached: harmless no-op
    out=$(bash "$SCRIPT" feat/does-not-exist main 2>&1)
    rc=$?
    [ "$rc" -eq 0 ] || { echo "no-op case: expected exit 0, got $rc; $out"; exit 1; }
    echo "$out" | grep -q "nothing to do" || { echo "no 'nothing to do' message"; exit 1; }
  )
  if [ $? -eq 0 ]; then pass "mistyped branch with explicit path is refused; missing branch alone is a no-op"
  else fail "mistyped branch with explicit path is refused; missing branch alone is a no-op"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: ignored files keep a merged worktree; regenerable caches do not ----------
TOTAL=$((TOTAL + 1))
test_neg_ignored_content() {
  local sb
  sb=$(neg_setup) || { fail "setup failed"; return; }
  (
    cd "$sb" || exit 1
    mkdir -p .worktrees/feat-test/.tmp
    echo "secret" > .worktrees/feat-test/.tmp/x
    for args in "" "--force"; do
      # shellcheck disable=SC2086
      out=$(bash "$SCRIPT" feat/test main $args 2>&1)
      rc=$?
      [ "$rc" -eq 1 ] || { echo "[$args] expected exit 1, got $rc; $out"; exit 1; }
      echo "$out" | grep -q "\.tmp/" || { echo "[$args] ignored path not listed; $out"; exit 1; }
      [ -f .worktrees/feat-test/.tmp/x ] || { echo "[$args] ignored file deleted"; exit 1; }
      git show-ref --verify --quiet refs/heads/feat/test || { echo "[$args] branch deleted"; exit 1; }
    done
    # Only a regenerable cache left: cleanup proceeds
    rm -rf .worktrees/feat-test/.tmp
    mkdir -p .worktrees/feat-test/pkg/__pycache__
    echo "x" > .worktrees/feat-test/pkg/__pycache__/m.pyc
    out=$(bash "$SCRIPT" feat/test main 2>&1)
    rc=$?
    [ "$rc" -eq 0 ] || { echo "cache-only: expected exit 0, got $rc; $out"; exit 1; }
    [ ! -d .worktrees/feat-test ] || { echo "cache-only worktree not removed"; exit 1; }
    git show-ref --verify --quiet refs/heads/feat/test && { echo "cache-only branch kept"; exit 1; }
    exit 0
  )
  if [ $? -eq 0 ]; then pass "ignored content keeps the worktree; __pycache__ only does not"
  else fail "ignored content keeps the worktree; __pycache__ only does not"
  fi
  cleanup_sandbox "$sb"
}

# ---------- test: default branch resolved via ls-remote when origin/HEAD is missing ----------
TOTAL=$((TOTAL + 1))
test_default_branch_ls_remote() {
  local sb remote
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  remote=$(mktemp -d)
  git init -q --bare "$remote"
  git --git-dir="$remote" symbolic-ref HEAD refs/heads/trunk
  (
    cd "$sb" || exit 1
    git remote add origin "$remote"
    git push -q origin main:trunk 2>/dev/null
    git symbolic-ref -q refs/remotes/origin/HEAD >/dev/null 2>&1 && { echo "origin/HEAD unexpectedly set"; exit 1; }
    git branch -q trunk
    out=$(bash "$SCRIPT" trunk main 2>&1)
    rc=$?
    [ "$rc" -eq 2 ] || { echo "trunk: expected exit 2, got $rc; $out"; exit 1; }
    git show-ref --verify --quiet refs/heads/trunk || { echo "trunk deleted"; exit 1; }
    # Unknown default branch: warning is printed, fixed list still applies
    git remote remove origin
    out=$(bash "$SCRIPT" develop main 2>&1)
    echo "$out" | grep -q "protected branch" || { echo "develop not protected without origin; $out"; exit 1; }
    git branch -q other main
    out=$(bash "$SCRIPT" other main 2>&1)
    echo "$out" | grep -q "cannot determine origin's default branch" || { echo "no unknown-default warning; $out"; exit 1; }
    exit 0
  )
  if [ $? -eq 0 ]; then pass "default branch found via ls-remote; warning when unknown"
  else fail "default branch found via ls-remote; warning when unknown"
  fi
  rm -rf "$remote"
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
test_force_preswitch_fail_still_deletes_branch
test_force_wtlist_fail_still_deletes_branch
test_force_keeps_dirty_worktree
test_squash_merged_uses_D
test_unverified_merge_keeps_branch
test_gh_unavailable_keeps_branch
test_remote_readback
test_protected_branches_refused
test_newborn_branch_not_deleted
test_lane_worktree_skipped
test_neg_explicit_lanes_dir
test_neg_explicit_subdir
test_neg_broken_git_link
test_neg_branch_typo_with_path
test_neg_ignored_content
test_default_branch_ls_remote

echo ""
echo "Summary: $((TOTAL - FAILED))/$TOTAL tests passed"
exit $FAILED
