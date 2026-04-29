#!/usr/bin/env bash
# Tests for .claude/hooks/pr-merge-guard.sh — uses a mock `gh` shim placed
# first in PATH. Each scenario synthesises the JSON the hook receives via
# stdin, captures stderr + exit code, and asserts the parser correctly
# extracted the PR selector (or correctly skipped non-merge commands).
#
# No real gh / network needed. The mock returns deterministic values keyed
# off the `--json FIELD` argument, so the hook always traverses to either:
#   - "BLOCKED: No automated review found for PR #N"  (parsing OK, no review)
#   - "BLOCKED: could not determine PR number ..."    (parsing FAILED)
#
# Issue #339 regression: the old greedy regex
# `^.*[[:space:]]merge([[:space:]]+|$)` consumed up to the last in-body
# occurrence of `merge` as a word, dropping the PR selector. The fix uses
# the already-built _tokens array indexed by _merge_idx.
#
# Run: bash scripts/test-pr-merge-guard.sh

set -u  # not -e: we explicitly inspect non-zero exits

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/../.claude/hooks/pr-merge-guard.sh"
[[ -f "$HOOK" ]] || { printf 'hook not found: %s\n' "$HOOK" >&2; exit 1; }

PASS=0
FAIL=0
declare -a FAILURES=()

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    PASS=$((PASS + 1))
    printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    FAILURES+=("$label: expected [$expected], got [$actual]")
    printf '  FAIL %s -- expected [%s], got [%s]\n' "$label" "$expected" "$actual"
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    PASS=$((PASS + 1))
    printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    FAILURES+=("$label: missing [$needle] in [$haystack]")
    printf '  FAIL %s -- missing [%s] in [%s]\n' "$label" "$needle" "$haystack"
  fi
}

assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    PASS=$((PASS + 1))
    printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    FAILURES+=("$label: unexpected [$needle] in [$haystack]")
    printf '  FAIL %s -- unexpected [%s] in [%s]\n' "$label" "$needle" "$haystack"
  fi
}

# Per-scenario isolated working dir so each run gets a fresh state dir.
WORK_DIR="$(mktemp -d -t pr-merge-guard-test.XXXXXX)"
trap 'rm -rf "$WORK_DIR"' EXIT

BIN_DIR="$WORK_DIR/bin"
mkdir -p "$BIN_DIR"

# Mock gh shim. Returns deterministic values for the calls the hook makes,
# AND records each invocation (full argv) to $MOCK_GH_LOG so tests can assert
# repo-flag propagation, not just selector extraction.
#
# Walk argv to find `--json FIELD` and selector (first non-flag positional
# after `pr view`). Also capture `--repo VALUE` / `--repo=VALUE` / `-R VALUE`
# into `repo_seen` so the log records what repo the hook downstream-passed.
cat > "$BIN_DIR/gh" <<'MOCK_EOF'
#!/usr/bin/env bash
# mock gh used by test-pr-merge-guard.sh
if [[ -n "${MOCK_GH_LOG:-}" ]]; then
  printf 'gh' >> "$MOCK_GH_LOG"
  for a in "$@"; do
    printf ' %q' "$a" >> "$MOCK_GH_LOG"
  done
  printf '\n' >> "$MOCK_GH_LOG"
fi

sub1="${1:-}"
sub2="${2:-}"
shift || true
shift || true
json_field=""
selector=""
repo_seen=""
prev=""
for arg in "$@"; do
  if [[ "$prev" == "--json" ]]; then
    json_field="$arg"
    prev=""
    continue
  fi
  if [[ "$prev" == "-R" || "$prev" == "--repo" ]]; then
    repo_seen="$arg"
    prev=""
    continue
  fi
  if [[ "$prev" == "-q" || "$prev" == "--jq" ]]; then
    prev=""
    continue
  fi
  case "$arg" in
    --json|--jq|-q|-R|--repo) prev="$arg"; continue ;;
    --repo=*) repo_seen="${arg#--repo=}"; continue ;;
    --jq=*) continue ;;
    -*) continue ;;
    *)
      [[ -z "$selector" ]] && selector="$arg"
      ;;
  esac
done

if [[ "$sub1" == "pr" && "$sub2" == "view" ]]; then
  case "$json_field" in
    number)
      case "$selector" in
        http*pull/*)
          printf '%s\n' "${selector##*pull/}" | sed 's|/.*||;s|?.*||;s|#.*||'
          ;;
        ''|--*)
          # No selector — fallback "current branch PR". Test scenarios that
          # rely on this set MOCK_FALLBACK_PR in env.
          printf '%s\n' "${MOCK_FALLBACK_PR:-}"
          ;;
        *[!0-9]*)
          # Branch name lookup — mock returns 9
          printf '%s\n' "9"
          ;;
        *)
          printf '%s\n' "$selector"
          ;;
      esac
      ;;
    reviewDecision)
      printf '%s\n' "${MOCK_REVIEW_DECISION:-REVIEW_REQUIRED}"
      ;;
    baseRepository)
      printf 'owner/repo\n'
      ;;
  esac
  exit 0
fi

if [[ "$sub1" == "pr" && "$sub2" == "checks" ]]; then
  # No CI status (empty)
  exit 0
fi

if [[ "$sub1" == "api" ]]; then
  # No bot reviews (empty)
  exit 0
fi

if [[ "$sub1" == "repo" && "$sub2" == "view" ]]; then
  printf 'owner/repo\n'
  exit 0
fi

exit 0
MOCK_EOF
chmod +x "$BIN_DIR/gh"

# Per-test gh call log. Cleared at the start of each scenario so assertions
# only see the calls made by the current run.
MOCK_GH_LOG="$WORK_DIR/gh-calls.log"

# Run hook with given command-string. Captures stderr + exit code.
# Optional second arg: env-var assignments separated by ';' (e.g.
# 'MOCK_FALLBACK_PR=42;MOCK_REVIEW_DECISION=APPROVED').
# Returns: stderr text on stdout (so callers can grep it), exit-code in $?.
run_hook() {
  local cmd="$1"
  local extra_env="${2:-}"
  # Build JSON input. Escape backslashes and double-quotes in cmd.
  local esc
  esc="${cmd//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  local input="{\"tool_input\": {\"command\": \"${esc}\"}}"
  local fake_project="$WORK_DIR/proj-$RANDOM"
  mkdir -p "$fake_project"
  : > "$MOCK_GH_LOG"  # reset log
  local rc
  if [[ -n "$extra_env" ]]; then
    # eval is intentional — extra_env is test-controlled, not user input.
    eval "PATH=\"$BIN_DIR:\$PATH\" CLAUDE_PROJECT_DIR=\"$fake_project\" MOCK_GH_LOG=\"$MOCK_GH_LOG\" $extra_env bash \"$HOOK\" 2> \"$WORK_DIR/stderr.\$\$\"" <<<"$input" >/dev/null
  else
    PATH="$BIN_DIR:$PATH" CLAUDE_PROJECT_DIR="$fake_project" MOCK_GH_LOG="$MOCK_GH_LOG" \
      bash "$HOOK" 2> "$WORK_DIR/stderr.$$" <<<"$input" >/dev/null
  fi
  rc=$?
  cat "$WORK_DIR/stderr.$$" 2>/dev/null || true
  rm -f "$WORK_DIR/stderr.$$"
  return $rc
}

# Run a scenario: $1 label, $2 cmd, $3 expected exit, $4 stderr-contains needle.
# Optional $5: stderr-NOT-contains needle.
scenario() {
  local label="$1" cmd="$2" want_rc="$3" want_msg="$4" not_msg="${5:-}"
  printf '\n[scenario] %s\n' "$label"
  local err rc
  err=$(run_hook "$cmd")
  rc=$?
  assert_eq "  exit code" "$want_rc" "$rc"
  if [[ -n "$want_msg" ]]; then
    assert_contains "  stderr contains" "$want_msg" "$err"
  fi
  if [[ -n "$not_msg" ]]; then
    assert_not_contains "  stderr does not contain" "$not_msg" "$err"
  fi
}

# ---------------------------------------------------------------------------
# Issue #339 — body containing the word `merge` must NOT eat the PR selector
# ---------------------------------------------------------------------------

scenario \
  '#339 body has standalone " merge " word -> PR # still extracted' \
  'gh pr merge 338 --body "I plan to merge this into develop"' \
  2 \
  'PR #338' \
  'could not determine'

scenario \
  '#339 body has multiple " merge " words -> PR # still extracted' \
  'gh pr merge 338 -b "merge into develop and merge cleanly"' \
  2 \
  'PR #338' \
  'could not determine'

scenario \
  '#339 -t/--subject has " merge " word -> PR # still extracted' \
  'gh pr merge 338 -t "merge into develop"' \
  2 \
  'PR #338' \
  'could not determine'

# ---------------------------------------------------------------------------
# Pre-existing behaviour (must still work — substring not at word boundary,
# plain merge, repo flags, URL/branch selectors)
# ---------------------------------------------------------------------------

scenario \
  'plain `gh pr merge 338`' \
  'gh pr merge 338' \
  2 \
  'PR #338'

scenario \
  'body has hyphenated `admin-merge` (not a token) -> still works' \
  'gh pr merge 338 --body "S6/S80 admin-merge precedent"' \
  2 \
  'PR #338'

scenario \
  'body passed with --body-file (path token) -> PR # extracted, file token skipped' \
  'gh pr merge 338 --body-file /tmp/body.txt' \
  2 \
  'PR #338'

scenario \
  'global -R before pr subcommand' \
  'gh -R owner/repo pr merge 5' \
  2 \
  'PR #5'

scenario \
  'local -R after merge subcommand' \
  'gh pr merge 5 -R owner/repo' \
  2 \
  'PR #5'

scenario \
  '--repo=owner/repo style' \
  'gh pr merge 5 --repo=owner/repo' \
  2 \
  'PR #5'

# Codex Low #1: -R/--repo propagation must reach downstream `gh pr view ...`
# / `gh api ...` calls. Mock now logs every gh invocation; assert the repo
# value appears on the `--json reviewDecision` query (the primary gate).
printf '\n[scenario] -R/--repo propagation reaches downstream gh calls\n'
err=$(run_hook 'gh -R owner/repo pr merge 5')
rc=$?
assert_eq "  exit code" "2" "$rc"
log=$(cat "$MOCK_GH_LOG" 2>/dev/null || true)
# The reviewDecision call must carry --repo owner/repo (or -R owner/repo).
review_call=$(printf '%s\n' "$log" | grep -E 'pr view.*reviewDecision' || true)
assert_contains "  review-decision call carries repo flag" "owner/repo" "$review_call"

printf '\n[scenario] local -R after merge propagates to downstream gh calls\n'
err=$(run_hook 'gh pr merge 5 -R owner/repo')
rc=$?
assert_eq "  exit code" "2" "$rc"
log=$(cat "$MOCK_GH_LOG" 2>/dev/null || true)
review_call=$(printf '%s\n' "$log" | grep -E 'pr view.*reviewDecision' || true)
assert_contains "  review-decision call carries repo flag" "owner/repo" "$review_call"

scenario \
  'URL selector (number parsed from /pull/N path)' \
  'gh pr merge https://github.com/owner/repo/pull/9' \
  2 \
  'PR #9'

scenario \
  'branch-name selector resolves via gh (mock returns 9)' \
  'gh pr merge feat/my-branch' \
  2 \
  'PR #9'

# ---------------------------------------------------------------------------
# Negative path: non-merge commands must NOT fire the intercept
# ---------------------------------------------------------------------------

scenario \
  'git commit body containing the literal "gh pr merge" -> not intercepted' \
  'git commit -m "todo: gh pr merge after review"' \
  0 \
  ''

scenario \
  'echo containing "gh pr merge" -> not intercepted' \
  'echo "documentation: gh pr merge usage"' \
  0 \
  ''

scenario \
  '`gh pr view 338` (not merge) -> not intercepted' \
  'gh pr view 338' \
  0 \
  ''

# ---------------------------------------------------------------------------
# Multi-segment + env-prefix scenarios that exercise interaction between the
# segment splitter, env-strip, and the new token-slice tail extractor.
# ---------------------------------------------------------------------------

scenario \
  'multi-segment: `echo ok && gh pr merge 338 -b "merge into develop"`' \
  'echo ok && gh pr merge 338 -b "merge into develop"' \
  2 \
  'PR #338' \
  'could not determine'

scenario \
  'env-prefix: `GH_TOKEN=x gh pr merge 338 -b "merge soon"`' \
  'GH_TOKEN=x gh pr merge 338 -b "merge soon"' \
  2 \
  'PR #338' \
  'could not determine'

# ---------------------------------------------------------------------------
# Edge case: `gh pr merge` with no selector and no current-branch PR
# ---------------------------------------------------------------------------

scenario \
  'no selector + no fallback PR -> BLOCKED could-not-determine' \
  'gh pr merge' \
  2 \
  'could not determine PR number'

# Codex Low #2: empty-tail success path. With no selector but a current-branch
# PR resolved by `gh pr view --json number`, the hook must proceed past the
# "could not determine" gate and run the review check against the resolved PR.
printf '\n[scenario] no selector + MOCK_FALLBACK_PR=42 -> proceeds to review check on PR #42\n'
err=$(run_hook 'gh pr merge' 'MOCK_FALLBACK_PR=42')
rc=$?
assert_eq "  exit code" "2" "$rc"
assert_contains "  stderr resolves to PR #42" "PR #42" "$err"
assert_not_contains "  stderr does not say could-not-determine" "could not determine" "$err"

# ---------------------------------------------------------------------------
# Bypass flag: human-approved merge proceeds
# ---------------------------------------------------------------------------

printf '\n[scenario] bypass flag (touch state file) lets merge proceed (rc=0)\n'
fake_project="$WORK_DIR/proj-bypass"
mkdir -p "$fake_project/.mercury/state"
touch "$fake_project/.mercury/state/pr-merge-approved-338"
input='{"tool_input": {"command": "gh pr merge 338"}}'
PATH="$BIN_DIR:$PATH" CLAUDE_PROJECT_DIR="$fake_project" \
  bash "$HOOK" 2> "$WORK_DIR/bypass.err" <<<"$input"
bypass_rc=$?
assert_eq "  bypass exit code" "0" "$bypass_rc"
# Flag must be consumed
if [[ -e "$fake_project/.mercury/state/pr-merge-approved-338" ]]; then
  FAIL=$((FAIL + 1))
  FAILURES+=("bypass flag still exists after consumption")
  printf '  FAIL bypass flag still present\n'
else
  PASS=$((PASS + 1))
  printf '  ok bypass flag consumed\n'
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf '\n=== Summary ===\n'
printf 'pass: %d\n' "$PASS"
printf 'fail: %d\n' "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  printf '\nFailures:\n'
  for f in "${FAILURES[@]}"; do
    printf '  - %s\n' "$f"
  done
  exit 1
fi
exit 0
