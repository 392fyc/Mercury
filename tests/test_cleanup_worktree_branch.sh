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

# ---------- tests: worktrees with checked-out submodules (Issue #670) ----------
# The submodule sources are local bare repositories (protocol.file.allow=always for the file
# transport): sub.git (contains a nested submodule deps/inner from inner.git). The submodule is added
# inside the task worktree, so its repository lives in the worktree's git-dir
# (<common-dir>/worktrees/<name>/modules), exactly like a real task worktree.
#
# mk_sub_sandbox <src-dir> [recursive:0|1]: mk_sandbox + libs/sub committed on feat/test and
# fast-forwarded into main; recursive=1 also checks out libs/sub/deps/inner in the worktree.
mk_sub_sandbox() {
  local src="$1" rec="${2:-0}" sb
  (
    cd "$src" || exit 1
    git init -q -b main innerseed
    cd innerseed || exit 1
    git config user.email "test@example.com"
    git config user.name "Test"
    echo inner > i.txt
    git add i.txt
    git commit -q -m "inner seed"
    cd .. && git clone -q --bare innerseed inner.git
    git init -q -b main seed
    cd seed || exit 1
    git config user.email "test@example.com"
    git config user.name "Test"
    printf '*.log\n__pycache__/\n*.pyc\n' > .gitignore
    echo one > a.txt
    git add .gitignore a.txt
    git commit -q -m "sub seed"
    git -c protocol.file.allow=always submodule add -q "$src/inner.git" deps/inner
    git commit -q -m "add inner"
    cd .. && git clone -q --bare seed sub.git
  ) >/dev/null 2>&1 || return 1
  sb=$(mk_sandbox) || return 1
  (
    cd "$sb/.worktrees/feat-test" || exit 1
    git -c protocol.file.allow=always submodule add -q "$src/sub.git" libs/sub >/dev/null 2>&1 || exit 1
    if [ "$rec" = "1" ]; then
      git -c protocol.file.allow=always submodule update --init --recursive -q >/dev/null 2>&1 || exit 1
    fi
    git commit -q -m "add submodule"
    cd "$sb" && git merge -q --ff-only feat/test
  ) >/dev/null 2>&1 || return 1
  echo "$sb"
}

# Wrapper that runs the script from the sandbox root and keeps stdout+stderr in $out, status in $rc.
# run_sub_case <label> <mutation> <hide:0|1|2> <expect:removed|kept> [extra script args]
# Environment: SUB_SETUP (normal|recursive|main-init|inactive|conflict|badmodules|uncheckedout|modulesonly),
#              SUB_TARGET (path of the mutated submodule inside the worktree, default libs/sub).
# mutation: none | cache | modified | staged | untracked | ignored | moved
# hide=1 sets submodule.<name>.ignore=all in the worktree and hide=2 also inside libs/sub, so the
# superproject's own `git status` stays quiet and the submodule-specific check is the only defense.
# For "kept" cases the sentinel (file content or submodule HEAD) must still be there afterwards.
run_sub_case() {
  local label="$1" mutation="$2" hide="$3" expect="$4"; shift 4
  local setup="${SUB_SETUP:-normal}" target="${SUB_TARGET:-libs/sub}" rec=0
  TOTAL=$((TOTAL + 1))
  [ "$setup" = "recursive" ] && rec=1
  local src sb out rc wt sub head_before head_after ok=1 gd
  src=$(mktemp -d)
  sb=$(mk_sub_sandbox "$src" "$rec") || { fail "$label (setup failed)"; rm -rf "$src"; return; }
  wt="$sb/.worktrees/feat-test"
  sub="$wt/$target"
  gd=$(git -C "$wt" rev-parse --absolute-git-dir)
  case "$setup" in
    main-init) git -C "$sb" -c protocol.file.allow=always submodule update --init -q >/dev/null 2>&1 ;;
    inactive)  git -C "$wt" config --unset "submodule.libs/sub.url" ;;
    conflict)
      local h
      h=$(git -C "$wt/libs/sub" rev-parse HEAD)
      printf '0 0000000000000000000000000000000000000000\tlibs/sub\n160000 %s 1\tlibs/sub\n160000 %s 2\tlibs/sub\n160000 %s 3\tlibs/sub\n' "$h" "$h" "$h" \
        | git -C "$wt" update-index --index-info ;;
    badmodules)
      printf '[broken\n' >> "$wt/.gitmodules"
      git -C "$wt" update-index --assume-unchanged .gitmodules ;;
    uncheckedout|modulesonly)
      rm -rf "$wt/libs/sub" && mkdir "$wt/libs/sub"
      [ "$setup" = "uncheckedout" ] && rm -rf "$gd/modules" ;;
  esac
  case "$mutation" in
    cache)     mkdir -p "$sub/pkg/__pycache__" && echo x > "$sub/pkg/__pycache__/m.pyc" ;;
    modified)  echo more >> "$sub/a.txt" ;;
    staged)    echo s > "$sub/staged.txt" && git -C "$sub" add staged.txt ;;
    untracked) echo u > "$sub/untracked.txt" ;;
    ignored)   echo i > "$sub/debug.log" ;;
    moved)     git -C "$sub" -c user.email=t@example.com -c user.name=T commit -q --allow-empty -m moved ;;
  esac
  if [ "$hide" != "0" ]; then
    git -C "$wt" config submodule.libs/sub.ignore all
    [ "$hide" = "2" ] && git -C "$wt/libs/sub" config submodule.deps/inner.ignore all
  fi
  head_before=""
  [ -e "$sub/.git" ] && head_before=$(git -C "$sub" rev-parse HEAD 2>/dev/null)
  out=$(cd "$sb" && bash "$SCRIPT" feat/test main "$@" 2>&1); rc=$?
  if [ "$expect" = "removed" ]; then
    [ "$rc" -eq 0 ] || { echo "exit $rc: $out"; ok=0; }
    [ ! -d "$wt" ] || { echo "worktree still exists: $out"; ok=0; }
    git -C "$sb" show-ref --verify --quiet refs/heads/feat/test && { echo "branch not deleted: $out"; ok=0; }
    git -C "$sb" worktree list --porcelain | grep -q "feat-test" && { echo "worktree still registered"; ok=0; }
    printf '%s' "$out" | grep -q 'submodule deinit' && { echo "deinit mentioned: $out"; ok=0; }
  else
    [ "$rc" -eq 1 ] || { echo "exit $rc (want 1): $out"; ok=0; }
    [ -d "$wt" ] || { echo "worktree removed: $out"; ok=0; }
    git -C "$sb" show-ref --verify --quiet refs/heads/feat/test || { echo "branch deleted: $out"; ok=0; }
    printf '%s' "$out" | grep -q 'libs/sub\|submodule\|cannot verify' || { echo "reason does not name the submodule: $out"; ok=0; }
    case "$mutation" in
      modified)  grep -q '^more$' "$sub/a.txt" || { echo "sentinel line in a.txt lost"; ok=0; } ;;
      staged)    [ -f "$sub/staged.txt" ] || { echo "staged.txt lost"; ok=0; } ;;
      untracked) [ -f "$sub/untracked.txt" ] || { echo "untracked.txt lost"; ok=0; } ;;
      ignored)   [ -f "$sub/debug.log" ] || { echo "debug.log lost"; ok=0; } ;;
    esac
    if [ -n "$head_before" ]; then
      head_after=$(git -C "$sub" rev-parse HEAD 2>/dev/null)
      [ "$head_before" = "$head_after" ] || { echo "submodule HEAD changed"; ok=0; }
    fi
  fi
  if [ "$ok" -eq 1 ]; then pass "$label"; else fail "$label"; fi
  cleanup_sandbox "$sb"
  rm -rf "$src" 2>/dev/null || true
}

test_submodule_cases() {
  run_sub_case "submodule clean at recorded commit: worktree and branch removed" none 0 removed
  run_sub_case "submodule clean + --force: worktree and branch removed" none 0 removed --force
  run_sub_case "submodule with only regenerable caches: removed" cache 0 removed
  run_sub_case "submodule modified file: kept, file content intact" modified 0 kept
  run_sub_case "submodule modified file (superproject status hidden): kept" modified 1 kept
  run_sub_case "submodule staged file (superproject status hidden): kept" staged 1 kept
  run_sub_case "submodule untracked file: kept, file intact" untracked 0 kept
  run_sub_case "submodule untracked file (superproject status hidden): kept" untracked 1 kept
  run_sub_case "submodule ignored non-cache file: kept, file intact" ignored 0 kept
  run_sub_case "submodule ignored non-cache file + --force: kept, file intact" ignored 0 kept --force
  run_sub_case "submodule checked out at a different commit: kept, HEAD unchanged" moved 0 kept
  run_sub_case "submodule at different commit (superproject status hidden): kept" moved 1 kept
  # recursive
  SUB_SETUP=recursive run_sub_case "nested submodules all clean: removed" none 0 removed
  SUB_SETUP=recursive SUB_TARGET=libs/sub/deps/inner run_sub_case "nested submodule untracked file (all status hidden): kept, file intact" untracked 2 kept
  SUB_SETUP=recursive SUB_TARGET=libs/sub/deps/inner run_sub_case "nested submodule ignored file (all status hidden): kept, file intact" ignored 2 kept
  SUB_SETUP=recursive SUB_TARGET=libs/sub/deps/inner run_sub_case "nested submodule at a different commit (all status hidden): kept" moved 2 kept
  # status prefix "-" but still checked out (url missing from the shared config): cannot verify
  SUB_SETUP=inactive run_sub_case "checked out but inactive submodule with ignored file: kept, file intact" ignored 0 kept
  # prefix U (unmerged gitlink) and a failing submodule status
  SUB_SETUP=conflict run_sub_case "unmerged submodule entry (status U): kept" none 0 kept
  SUB_SETUP=badmodules run_sub_case "git submodule status fails (broken .gitmodules): kept" none 0 kept
  # no checked-out submodule in the task worktree
  SUB_SETUP=uncheckedout run_sub_case "submodule not checked out and no modules dir: plain removal" none 0 removed
  SUB_SETUP=modulesonly run_sub_case "only <git-dir>/modules left (no checked-out submodule): force path removes" none 0 removed
}

# The main checkout has the submodule initialized too: cleaning the task worktree must leave the main
# checkout's url config, status prefix and files alone (init/deinit would write the shared config).
TOTAL=$((TOTAL + 1))
test_main_checkout_submodule_untouched() {
  local src sb out rc ok=1 pre
  src=$(mktemp -d)
  sb=$(mk_sub_sandbox "$src" 0) || { fail "main checkout submodule (setup failed)"; rm -rf "$src"; return; }
  git -C "$sb" -c protocol.file.allow=always submodule update --init -q >/dev/null 2>&1
  [ -f "$sb/libs/sub/a.txt" ] || { echo "setup: main checkout submodule missing"; ok=0; }
  out=$(cd "$sb" && bash "$SCRIPT" feat/test main 2>&1); rc=$?
  [ "$rc" -eq 0 ] || { echo "exit $rc: $out"; ok=0; }
  [ ! -d "$sb/.worktrees/feat-test" ] || { echo "worktree still exists"; ok=0; }
  [ -n "$(git -C "$sb" config --get submodule.libs/sub.url)" ] || { echo "main checkout url config lost"; ok=0; }
  pre=$(git -C "$sb" submodule status | cut -c1)
  [ "$pre" = " " ] || { echo "main checkout status prefix is '$pre'"; ok=0; }
  [ -f "$sb/libs/sub/a.txt" ] || { echo "main checkout submodule files lost"; ok=0; }
  if [ "$ok" -eq 1 ]; then pass "main checkout keeps its submodule url, status and files after worktree cleanup"
  else fail "main checkout keeps its submodule url, status and files after worktree cleanup"; fi
  cleanup_sandbox "$sb"
  rm -rf "$src" 2>/dev/null || true
}

# dry-run: prints only remove --force (no deinit), changes nothing
TOTAL=$((TOTAL + 1))
test_submodule_dry_run() {
  local src sb out rc ok=1
  src=$(mktemp -d)
  sb=$(mk_sub_sandbox "$src" 0) || { fail "submodule dry-run (setup failed)"; rm -rf "$src"; return; }
  out=$(cd "$sb" && bash "$SCRIPT" feat/test main --dry-run 2>&1); rc=$?
  [ "$rc" -eq 0 ] || { echo "exit $rc: $out"; ok=0; }
  printf '%s' "$out" | grep -q '\[dry-run\] git worktree remove --force' || { echo "no remove --force plan: $out"; ok=0; }
  printf '%s' "$out" | grep -q 'deinit' && { echo "deinit in dry-run output: $out"; ok=0; }
  [ -f "$sb/.worktrees/feat-test/libs/sub/a.txt" ] || { echo "worktree touched in dry-run"; ok=0; }
  git -C "$sb" show-ref --verify --quiet refs/heads/feat/test || { echo "branch removed in dry-run"; ok=0; }
  if [ "$ok" -eq 1 ]; then pass "submodule worktree dry-run only prints remove --force, no deinit"
  else fail "submodule worktree dry-run only prints remove --force, no deinit"; fi
  cleanup_sandbox "$sb"
  rm -rf "$src" 2>/dev/null || true
}

# A worktree without submodules must never see --force
TOTAL=$((TOTAL + 1))
test_no_submodule_no_force() {
  local sb out
  sb=$(mk_sandbox) || { fail "setup failed"; return; }
  out=$(cd "$sb" && bash "$SCRIPT" feat/test main --dry-run 2>&1)
  if printf '%s' "$out" | grep -q 'remove --force\|submodule deinit'; then
    fail "worktree without submodules: dry-run plan must not contain --force or deinit"
  else
    pass "worktree without submodules: dry-run plan has neither --force nor deinit"
  fi
  cleanup_sandbox "$sb"
}

# --force rm -rf fallback must refuse a worktree holding a checked-out submodule. The plain removal
# path is made to fail with a stub `git` that fails `worktree remove` (simulated Windows file lock).
TOTAL=$((TOTAL + 1))
test_rmrf_refuses_checked_out_submodule() {
  local src sb out rc ok=1 realgit stub wt
  src=$(mktemp -d)
  sb=$(mk_sub_sandbox "$src" 0) || { fail "rm -rf submodule guard (setup failed)"; rm -rf "$src"; return; }
  wt="$sb/.worktrees/feat-test"
  # Make the submodule check see "no submodule data" (modules removed, prefix "-" with no .git) is not
  # possible while a .git exists, so test the guard function directly instead.
  out=$(
    # shellcheck disable=SC1090
    eval "$(sed -n '/^wt_has_checked_out_gitlink() {/,/^}/p' "$SCRIPT")"
    wt_has_checked_out_gitlink "$wt" && echo GUARD_TRUE || echo GUARD_FALSE
  )
  [ "$out" = "GUARD_TRUE" ] || { echo "guard did not detect checked-out gitlink: $out"; ok=0; }
  rm -rf "$wt/libs/sub" && mkdir "$wt/libs/sub"
  out=$(
    eval "$(sed -n '/^wt_has_checked_out_gitlink() {/,/^}/p' "$SCRIPT")"
    wt_has_checked_out_gitlink "$wt" && echo GUARD_TRUE || echo GUARD_FALSE
  )
  [ "$out" = "GUARD_FALSE" ] || { echo "guard fired for an empty submodule dir: $out"; ok=0; }
  if [ "$ok" -eq 1 ]; then pass "rm -rf fallback guard detects only checked-out submodules"
  else fail "rm -rf fallback guard detects only checked-out submodules"; fi
  cleanup_sandbox "$sb"
  rm -rf "$src" 2>/dev/null || true
}

# The guard must still see a gitlink whose path git would C-quote (non-ASCII, quote, space).
TOTAL=$((TOTAL + 1))
test_rmrf_guard_quoted_path() {
  local repo p out
  repo=$(mktemp -d)
  p="libs/子模块 'q'"
  git -C "$repo" init -q
  git -C "$repo" update-index --add --cacheinfo "160000,$(printf '%040d' 1),$p"
  mkdir -p "$repo/$p" && : > "$repo/$p/.git"
  out=$(
    eval "$(sed -n '/^wt_has_checked_out_gitlink() {/,/^}/p' "$SCRIPT")"
    wt_has_checked_out_gitlink "$repo" && echo GUARD_TRUE || echo GUARD_FALSE
  )
  if [ "$out" = "GUARD_TRUE" ]; then pass "rm -rf fallback guard sees gitlinks with quoted (non-ASCII) paths"
  else fail "rm -rf fallback guard sees gitlinks with quoted (non-ASCII) paths (got $out)"; fi
  rm -rf "$repo" 2>/dev/null || true
}

test_submodule_cases
test_submodule_dry_run
test_no_submodule_no_force
test_main_checkout_submodule_untouched
test_rmrf_refuses_checked_out_submodule
test_rmrf_guard_quoted_path

echo ""
echo "Summary: $((TOTAL - FAILED))/$TOTAL tests passed"
exit $FAILED
