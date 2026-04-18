#!/usr/bin/env bash
# tests/test_worktree_reaper.sh
# Unit tests for scripts/worktree-reaper.sh
#
# Tests:
#   1. --dry-run does NOT delete any directory
#   2. --prune deletes only the stale orphan (no open PR, age > 7 days)
#   3. --prune keeps a fresh worktree (age < 7 days)
#   4. --prune keeps a stale worktree that has an open PR
#
# Strategy:
#   - Creates a temporary directory to act as WORKTREE_ROOT
#   - Stubs `gh` and `git` commands via a PATH shim directory
#   - Uses `touch -d '30 days ago'` (or Perl fallback) to backdate the stale entry
#   - Does NOT exercise real git worktree add/remove — stubs git to echo/exit 0
#     for worktree and branch commands so the script can run offline

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REAPER="${SCRIPT_DIR}/../scripts/worktree-reaper.sh"

# ---------------------------------------------------------------------------
# Helper: portable timestamp backdating
# ---------------------------------------------------------------------------
backdate() {
  # $1 = path, $2 = days ago
  local path="$1" days="$2"
  if touch -d "${days} days ago" "$path" 2>/dev/null; then
    return 0
  fi
  # macOS / BSD touch uses -t or -A; fall back to perl
  if command -v perl >/dev/null 2>&1; then
    perl -e "use POSIX; my \$t = time() - ${days}*86400; utime(\$t,\$t,'${path}');"
    return 0
  fi
  echo "SKIP: cannot backdate timestamps on this platform — skipping age-related tests" >&2
  return 1
}

# ---------------------------------------------------------------------------
# Setup: temp workspace
# ---------------------------------------------------------------------------
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

WT_ROOT="${TMP}/worktrees"
SHIM_DIR="${TMP}/shims"
mkdir -p "$WT_ROOT" "$SHIM_DIR"

# Stale orphan: no open PR, age > 7 days
STALE_DIR="${WT_ROOT}/TASK-stale"
mkdir -p "$STALE_DIR"
backdate "$STALE_DIR" 30 || { echo "1..0 # SKIP cannot backdate"; exit 0; }

# Fresh entry: no open PR but younger than 7 days
FRESH_DIR="${WT_ROOT}/TASK-fresh"
mkdir -p "$FRESH_DIR"
# mtime is now (fresh by default)

# Stale-but-has-PR entry: age > 7 days but an open PR exists
STALE_PR_DIR="${WT_ROOT}/TASK-stale-with-pr"
mkdir -p "$STALE_PR_DIR"
backdate "$STALE_PR_DIR" 30

# ---------------------------------------------------------------------------
# Shim: gh — returns headRefName for TASK-stale-with-pr's branch only
# ---------------------------------------------------------------------------
cat > "${SHIM_DIR}/gh" <<'EOF'
#!/usr/bin/env sh
# Fake `gh pr list` — returns one open PR on feat/TASK-stale-with-pr
if [ "$1" = "pr" ] && [ "$2" = "list" ]; then
  printf 'feat/TASK-stale-with-pr\n'
  exit 0
fi
exit 0
EOF
chmod +x "${SHIM_DIR}/gh"

# ---------------------------------------------------------------------------
# Shim: git
#   - rev-parse --show-toplevel  → TMP (repo root so WT_ROOT stays under TMP)
#   - rev-parse --abbrev-ref HEAD (run inside a worktree dir) → derive branch from dirname
#   - worktree remove --force <path> → rm -rf <path>  (simulate removal)
#   - branch -d <branch> → no-op
#   - anything else → delegate to real git if available, else no-op
# ---------------------------------------------------------------------------
cat > "${SHIM_DIR}/git" <<EOF
#!/usr/bin/env sh
# Fake git shim for worktree-reaper tests

# rev-parse --show-toplevel
if [ "\$1" = "rev-parse" ] && [ "\$2" = "--show-toplevel" ]; then
  printf '%s\n' "${TMP}"
  exit 0
fi

# rev-parse --abbrev-ref HEAD  (called with -C <worktree-path>)
if [ "\$1" = "-C" ] && [ "\$3" = "rev-parse" ] && [ "\$4" = "--abbrev-ref" ] && [ "\$5" = "HEAD" ]; then
  wt_path="\$2"
  task=\$(basename "\$wt_path")
  printf 'feat/%s\n' "\$task"
  exit 0
fi

# worktree remove --force <path>
if [ "\$1" = "worktree" ] && [ "\$2" = "remove" ] && [ "\$3" = "--force" ]; then
  rm -rf "\$4"
  exit 0
fi

# branch -d <branch>
if [ "\$1" = "branch" ] && [ "\$2" = "-d" ]; then
  exit 0
fi

# fallback: silently succeed
exit 0
EOF
chmod +x "${SHIM_DIR}/git"

# ---------------------------------------------------------------------------
# Run reaper with shim PATH
# ---------------------------------------------------------------------------
run_reaper() {
  WORKTREE_ROOT="$WT_ROOT" WORKTREE_AGE_DAYS=7 \
    PATH="${SHIM_DIR}:${PATH}" \
    bash "$REAPER" "$@"
}

# ---------------------------------------------------------------------------
# TAP output
# ---------------------------------------------------------------------------
TESTS=4
printf '1..%d\n' "$TESTS"

# Test 1: --dry-run leaves all directories intact
OUTPUT=$(run_reaper --dry-run 2>&1)
if [ -d "$STALE_DIR" ] && [ -d "$FRESH_DIR" ] && [ -d "$STALE_PR_DIR" ]; then
  printf 'ok 1 - dry-run does not delete anything\n'
else
  printf 'not ok 1 - dry-run deleted a directory\n'
  printf '# Output:\n%s\n' "$OUTPUT"
fi

# Test 2: --dry-run output mentions WOULD REAP for stale orphan
if printf '%s\n' "$OUTPUT" | grep -q 'WOULD REAP.*TASK-stale'; then
  printf 'ok 2 - dry-run reports WOULD REAP for stale orphan\n'
else
  printf 'not ok 2 - dry-run did not report WOULD REAP for TASK-stale\n'
  printf '# Output:\n%s\n' "$OUTPUT"
fi

# Test 3: --prune deletes ONLY the stale orphan
run_reaper --prune >/dev/null 2>&1 || true

if [ ! -d "$STALE_DIR" ]; then
  printf 'ok 3 - --prune removed the stale orphan TASK-stale\n'
else
  printf 'not ok 3 - --prune did NOT remove TASK-stale\n'
fi

# Test 4: --prune kept TASK-fresh and TASK-stale-with-pr
if [ -d "$FRESH_DIR" ] && [ -d "$STALE_PR_DIR" ]; then
  printf 'ok 4 - --prune kept TASK-fresh and TASK-stale-with-pr\n'
else
  printf 'not ok 4 - --prune incorrectly deleted a non-orphan entry\n'
  [ -d "$FRESH_DIR" ]    || printf '# TASK-fresh was deleted\n'
  [ -d "$STALE_PR_DIR" ] || printf '# TASK-stale-with-pr was deleted\n'
fi
