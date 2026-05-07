#!/usr/bin/env bash
# Tests for .claude/hooks/push-guard.sh — uses a mock `git` shim placed
# first in PATH for Phase 3 implicit-push tests. Each scenario synthesises
# the JSON the hook receives via stdin, captures stderr + exit code, and
# asserts the parser correctly extracted the push args (or correctly
# allowed non-push commands and lane-branch pushes).
#
# Issue #349 regression: the old per-line greedy regex
# `sed 's/.*git[[:space:]]\+push[[:space:]]*//'` mangled commit-body lines
# carrying `develop`/`master` as standalone tokens. The fix uses a
# quote-aware awk segment splitter + per-segment env-strip + token-array
# slice from the `push` token forward. Body content stays inside the
# `git commit -m "..."` segment and never reaches the push-args walker.
#
# Run: bash scripts/test-push-guard.sh

set -u  # not -e: we explicitly inspect non-zero exits

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOK="$SCRIPT_DIR/../.claude/hooks/push-guard.sh"
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
# Guard mktemp failure explicitly: a missing $WORK_DIR would silently fall
# through to "$WORK_DIR/..." path concatenation that would resolve to
# absolute paths under cwd, risking writes outside the temp namespace.
if ! WORK_DIR="$(mktemp -d -t push-guard-test.XXXXXX)"; then
  printf 'failed to create temp dir for test\n' >&2
  exit 1
fi
trap '[[ -n "${WORK_DIR:-}" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"' EXIT

BIN_DIR="$WORK_DIR/bin"
mkdir -p "$BIN_DIR"

# Mock git shim. push-guard.sh only invokes `git rev-parse --abbrev-ref HEAD`
# during Phase 3 implicit-push detection. The mock returns $MOCK_CURRENT_BRANCH
# (default `feature/test`) so explicit-target scenarios are unaffected and
# implicit-push scenarios can exercise both protected and non-protected
# branch states deterministically.
cat > "$BIN_DIR/git" <<'MOCK_EOF'
#!/usr/bin/env bash
# mock git used by test-push-guard.sh — only emulates rev-parse HEAD
if [[ "${1:-}" == "rev-parse" && "${2:-}" == "--abbrev-ref" && "${3:-}" == "HEAD" ]]; then
  printf '%s\n' "${MOCK_CURRENT_BRANCH:-feature/test}"
  exit 0
fi
exit 0
MOCK_EOF
chmod +x "$BIN_DIR/git"

# Run hook with given command-string. Captures stderr + exit code.
# Optional second arg: ';'-separated VAR=VALUE pairs forwarded to the hook
# environment (e.g. 'MOCK_CURRENT_BRANCH=develop'). Pairs are split into an
# array and passed via `env`, so VALUE may contain spaces or shell
# metacharacters without being interpreted as code.
#
# Limitation (Copilot iter-1 line 96): VALUE cannot contain `;` because that
# is the pair separator. Tests in this harness do not need semicolons in
# values; if a future scenario does, switch to a non-semicolon delimiter
# (e.g. newline-joined pairs) and update the IFS/read accordingly.
# Returns: stderr text on stdout (so callers can grep it), exit-code in $?.
#
# Newline handling: literal newlines in $cmd (e.g. heredoc-style multi-line
# commit bodies, exactly the S83 reproducer pattern) are escaped to JSON
# `\n` sequences so the synthesized JSON is well-formed; jq inside the
# hook then unescapes them back to literal newlines, matching what the
# real Claude Code Bash tool would deliver.
run_hook() {
  local cmd="$1"
  local extra_env="${2:-}"
  # Build JSON input. Escape backslashes, double-quotes, and literal newlines.
  local esc
  esc="${cmd//\\/\\\\}"
  esc="${esc//\"/\\\"}"
  esc="${esc//$'\n'/\\n}"
  local input="{\"tool_input\": {\"command\": \"${esc}\"}}"
  local fake_project="$WORK_DIR/proj-$RANDOM"
  mkdir -p "$fake_project"
  local rc
  local -a extra_env_kv=()
  if [[ -n "$extra_env" ]]; then
    IFS=';' read -r -a extra_env_kv <<< "$extra_env"
  fi
  env "PATH=$BIN_DIR:$PATH" "CLAUDE_PROJECT_DIR=$fake_project" \
    "${extra_env_kv[@]}" bash "$HOOK" 2> "$WORK_DIR/stderr.$$" <<<"$input" >/dev/null
  rc=$?
  cat "$WORK_DIR/stderr.$$" 2>/dev/null || true
  rm -f "$WORK_DIR/stderr.$$"
  return $rc
}

# Run a scenario: $1 label, $2 cmd, $3 expected exit, $4 stderr-contains needle.
# Optional $5: stderr-NOT-contains needle. Optional $6: ';'-separated env pairs.
scenario() {
  local label="$1" cmd="$2" want_rc="$3" want_msg="$4" not_msg="${5:-}" extra_env="${6:-}"
  printf '\n[scenario] %s\n' "$label"
  local err rc
  err=$(run_hook "$cmd" "$extra_env")
  rc=$?
  assert_eq "  exit code" "$want_rc" "$rc"
  if [[ -n "$want_msg" ]]; then
    assert_contains "  stderr contains" "$want_msg" "$err"
  fi
  if [[ -n "$not_msg" ]]; then
    assert_not_contains "  stderr does not contain" "$not_msg" "$err"
  fi
}

# ===========================================================================
# Issue #349 — body containing `git push`/`develop`/`master` tokens MUST NOT
# be parsed as push args (S83 reproducer is the primary acceptance gate)
# ===========================================================================

# S83 actual reproducer: heredoc-style multi-line body with `git push hygiene`
# phrase + standalone `develop` and `master` words + real push to lane branch.
# Pre-fix this BLOCKED with rc=2 because per-line sed left body lines intact
# and bash word-splitting exposed `develop`/`master` tokens.
scenario \
  '#349 S83 heredoc body w/ git-push phrase + develop/master words -> not blocked' \
  'git commit -m "$(cat <<EOF
fix: lane-assertion three-way check

refs origin/develop and origin/master
improves git push hygiene for develop branch
EOF
)" && git push origin lane/main/345-handoff-lane-assert' \
  0 ''

scenario \
  '#349 single-line body w/ "develop" word + push to lane branch -> not blocked' \
  'git commit -m "refs develop branch" && git push origin lane/main/foo' \
  0 ''

scenario \
  '#349 single-line body w/ "master" word + push to lane branch -> not blocked' \
  'git commit -m "merge master branch" && git push origin lane/main/foo' \
  0 ''

scenario \
  '#349 body literally contains "git push origin develop" + real push to lane -> not blocked' \
  'git commit -m "avoids git push origin develop bypass" && git push origin lane/main/foo' \
  0 ''

scenario \
  '#349 body w/ multiple "git push" mentions + real push to lane -> not blocked' \
  'git commit -m "fix: handle git push to develop and git push to master" && git push origin lane/main/foo' \
  0 ''

# ===========================================================================
# All existing block paths still fire (regression net for the new
# segment-splitter + token-walker)
# ===========================================================================

scenario \
  'plain `git push origin develop` -> blocked' \
  'git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git push origin master` -> blocked' \
  'git push origin master' \
  2 'BLOCKED'

scenario \
  '`git push origin main` -> blocked' \
  'git push origin main' \
  2 'BLOCKED'

scenario \
  '`git push -f origin master` -> blocked (force flag does not bypass)' \
  'git push -f origin master' \
  2 'BLOCKED'

scenario \
  '`git push --force origin develop` -> blocked' \
  'git push --force origin develop' \
  2 'BLOCKED'

scenario \
  '`git push --all` -> blocked (Phase 1 dangerous flag)' \
  'git push --all' \
  2 'BLOCKED'

scenario \
  '`git push --mirror` -> blocked (Phase 1 dangerous flag)' \
  'git push --mirror' \
  2 'BLOCKED'

scenario \
  '`git push origin HEAD:develop` (refspec RHS) -> blocked' \
  'git push origin HEAD:develop' \
  2 'BLOCKED'

scenario \
  '`git push origin lane/main/foo:develop` (refspec RHS) -> blocked' \
  'git push origin lane/main/foo:develop' \
  2 'BLOCKED'

scenario \
  '`git push origin +develop` (force-push prefix) -> blocked' \
  'git push origin +develop' \
  2 'BLOCKED'

scenario \
  '`git push origin refs/heads/develop` (full ref) -> blocked' \
  'git push origin refs/heads/develop' \
  2 'BLOCKED'

scenario \
  '`git push origin HEAD:refs/heads/develop` (full ref RHS) -> blocked' \
  'git push origin HEAD:refs/heads/develop' \
  2 'BLOCKED'

# Implicit push from current protected branch (Phase 3)
scenario \
  'implicit `git push` from current branch=develop -> blocked' \
  'git push' \
  2 'BLOCKED' '' \
  'MOCK_CURRENT_BRANCH=develop'

scenario \
  'implicit `git push origin` (remote only) from develop -> blocked' \
  'git push origin' \
  2 'BLOCKED' '' \
  'MOCK_CURRENT_BRANCH=develop'

scenario \
  'implicit `git push` from current branch=master -> blocked' \
  'git push' \
  2 'BLOCKED' '' \
  'MOCK_CURRENT_BRANCH=master'

scenario \
  'implicit `git push` from feature branch -> not blocked' \
  'git push' \
  0 '' '' \
  'MOCK_CURRENT_BRANCH=feature/foo'

scenario \
  'implicit `git push` from lane branch -> not blocked' \
  'git push' \
  0 '' '' \
  'MOCK_CURRENT_BRANCH=lane/side-bug/349'

# ===========================================================================
# Quote-bypass attempts: separator chars hidden inside quotes must NOT split
# the segment; separator chars OUTSIDE quotes MUST split.
# ===========================================================================

scenario \
  '`echo ok && git push origin develop` -> blocked (segment 2 fires)' \
  'echo ok && git push origin develop' \
  2 'BLOCKED'

scenario \
  '`echo ok || git push origin develop` -> blocked' \
  'echo ok || git push origin develop' \
  2 'BLOCKED'

scenario \
  '`echo ok ; git push origin develop` -> blocked' \
  'echo ok ; git push origin develop' \
  2 'BLOCKED'

scenario \
  '`true | git push origin develop` -> blocked' \
  'true | git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git commit -m "; git push origin develop"` (separator inside quotes) -> not blocked' \
  'git commit -m "; git push origin develop"' \
  0 ''

scenario \
  "git commit with single-quoted body containing 'git push origin develop' -> not blocked" \
  "git commit -m 'git push origin develop content'" \
  0 ''

scenario \
  '`git commit -m "&& git push origin master"` -> not blocked' \
  'git commit -m "&& git push origin master"' \
  0 ''

# ===========================================================================
# Env-var prefix + command-wrapper bypass attempts
# ===========================================================================

scenario \
  '`GIT_TRACE=1 git push origin develop` -> blocked' \
  'GIT_TRACE=1 git push origin develop' \
  2 'BLOCKED'

scenario \
  '`env GIT_TRACE=1 git push origin develop` -> blocked' \
  'env GIT_TRACE=1 git push origin develop' \
  2 'BLOCKED'

scenario \
  '`command git push origin develop` -> blocked' \
  'command git push origin develop' \
  2 'BLOCKED'

scenario \
  '`exec git push origin develop` -> blocked' \
  'exec git push origin develop' \
  2 'BLOCKED'

scenario \
  '`nohup git push origin master` -> blocked' \
  'nohup git push origin master' \
  2 'BLOCKED'

scenario \
  '`A=1 B=2 git push origin develop` -> blocked (multiple env vars)' \
  'A=1 B=2 git push origin develop' \
  2 'BLOCKED'

# ===========================================================================
# Lane-branch defense (false-positive guards: substring-but-not-token matches)
# ===========================================================================

scenario \
  '`git push origin lane/main/345-handoff-lane-assert` -> not blocked' \
  'git push origin lane/main/345-handoff-lane-assert' \
  0 ''

scenario \
  '`git push origin lane/side-bug/349-push-guard-regex` -> not blocked' \
  'git push origin lane/side-bug/349-push-guard-regex' \
  0 ''

scenario \
  '`git push -u origin lane/side-bug/349` -> not blocked (-u flag)' \
  'git push -u origin lane/side-bug/349' \
  0 ''

scenario \
  '`git push --set-upstream origin lane/side-bug/349` -> not blocked' \
  'git push --set-upstream origin lane/side-bug/349' \
  0 ''

scenario \
  '`git push origin feature/develop-feat` (substring not exact) -> not blocked' \
  'git push origin feature/develop-feat' \
  0 ''

scenario \
  '`git push origin develop-typo` (substring not exact) -> not blocked' \
  'git push origin develop-typo' \
  0 ''

scenario \
  '`git push origin master-backup` (substring not exact) -> not blocked' \
  'git push origin master-backup' \
  0 ''

scenario \
  '`git push origin HEAD:lane/side-bug/349` (refspec to non-protected) -> not blocked' \
  'git push origin HEAD:lane/side-bug/349' \
  0 ''

scenario \
  '`git push -f -u origin lane/main/foo` (multiple flags) -> not blocked' \
  'git push -f -u origin lane/main/foo' \
  0 ''

# ===========================================================================
# Negative path: non-push commands MUST NOT fire the intercept
# ===========================================================================

scenario \
  '`git commit -m "fix: push to develop"` (no real push) -> not intercepted' \
  'git commit -m "fix: push to develop"' \
  0 ''

scenario \
  '`git fetch origin develop` -> not intercepted (not a push)' \
  'git fetch origin develop' \
  0 ''

scenario \
  '`git pull origin develop` -> not intercepted (not a push)' \
  'git pull origin develop' \
  0 ''

scenario \
  '`echo "git push origin develop"` -> not intercepted (echo, not git push)' \
  'echo "git push origin develop"' \
  0 ''

scenario \
  '`gh pr view 5` -> not intercepted (not git push)' \
  'gh pr view 5' \
  0 ''

scenario \
  '`pushd /tmp` -> not intercepted (substring "push" not "git push")' \
  'pushd /tmp' \
  0 ''

# ===========================================================================
# git global options (-C, -c) before the push subcommand
# ===========================================================================

scenario \
  '`git -C /repo push origin develop` -> blocked' \
  'git -C /repo push origin develop' \
  2 'BLOCKED'

scenario \
  '`git -c user.name=foo push origin develop` -> blocked' \
  'git -c user.name=foo push origin develop' \
  2 'BLOCKED'

scenario \
  '`git -C /repo push origin lane/main/foo` -> not blocked' \
  'git -C /repo push origin lane/main/foo' \
  0 ''

scenario \
  '`git --git-dir=/repo/.git push origin develop` -> blocked' \
  'git --git-dir=/repo/.git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git --no-pager push origin develop` -> blocked' \
  'git --no-pager push origin develop' \
  2 'BLOCKED'

# ===========================================================================
# Multi-segment + env-prefix interaction
# ===========================================================================

scenario \
  '`echo ok && GIT_TRACE=1 git push origin develop` -> blocked' \
  'echo ok && GIT_TRACE=1 git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git commit -m "develop" && env GIT_TRACE=1 git push origin lane/main/foo` -> not blocked' \
  'git commit -m "develop" && env GIT_TRACE=1 git push origin lane/main/foo' \
  0 ''

scenario \
  '`git commit -m "ok" && git push origin develop` -> blocked (real push to develop wins)' \
  'git commit -m "ok" && git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git status && git push origin develop` -> blocked (segment 2 fires)' \
  'git status && git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git push origin lane/foo && git status` -> not blocked (lane push first)' \
  'git push origin lane/foo && git status' \
  0 ''

# ===========================================================================
# Edge cases
# ===========================================================================

# Empty COMMAND with "command" key in JSON = parser-fail signal → fail-closed.
# Real Claude Code Bash invocations always carry a non-empty command, so this
# code path only fires under parser malfunction / malformed input.
scenario \
  'empty command WITH command key -> hard-block (parser-fail safety)' \
  '' \
  2 'BLOCKED'

scenario \
  '`git` (just git, no subcommand) -> not intercepted' \
  'git' \
  0 ''

scenario \
  '`git push` from feature branch (mock) -> not blocked' \
  'git push' \
  0 '' '' \
  'MOCK_CURRENT_BRANCH=feature/foo'

# ===========================================================================
# Iter-2 bypass closures (dual-verify findings: claude-only + codex-only)
# ===========================================================================

# CRITICAL #1: literal-quote token in protected-branch position must NOT bypass.
# Awk strips outer quotes during tokenization; defensive quote-strip in Phase 2
# handles any leakage from mid-token quotes / repeated escaping.
scenario \
  '`git push origin "develop"` (double-quoted protected) -> blocked' \
  'git push origin "develop"' \
  2 'BLOCKED'

scenario \
  "git push origin 'develop' (single-quoted protected) -> blocked" \
  "git push origin 'develop'" \
  2 'BLOCKED'

scenario \
  '`git push origin "+develop"` (quoted force-prefix) -> blocked' \
  'git push origin "+develop"' \
  2 'BLOCKED'

scenario \
  '`git push origin "HEAD:develop"` (quoted refspec RHS) -> blocked' \
  'git push origin "HEAD:develop"' \
  2 'BLOCKED'

scenario \
  '`git push origin "refs/heads/develop"` (quoted full ref) -> blocked' \
  'git push origin "refs/heads/develop"' \
  2 'BLOCKED'

# CRITICAL #2: 6+ chained env wrappers must NOT bypass (was capped at 5 passes).
# Token-based wrapper-strip is now unbounded.
scenario \
  '`env env env env env env git push origin develop` (6× env) -> blocked' \
  'env env env env env env git push origin develop' \
  2 'BLOCKED'

scenario \
  '10× env chain -> blocked' \
  'env env env env env env env env env env git push origin develop' \
  2 'BLOCKED'

scenario \
  'mixed wrapper chain (env+command+exec×3 alternating) -> blocked' \
  'env command exec env command exec env command exec git push origin develop' \
  2 'BLOCKED'

# HIGH (Claude): subshell `( ... )` and group `{ ...; }` must NOT bypass.
# Awk now tokenizes parens/braces; wrapper-strip skips leading `(` `{`.
scenario \
  '`( git push origin develop )` (subshell) -> blocked' \
  '( git push origin develop )' \
  2 'BLOCKED'

scenario \
  '`{ git push origin develop ; }` (brace group) -> blocked' \
  '{ git push origin develop ; }' \
  2 'BLOCKED'

scenario \
  '`( env GIT_TRACE=1 git push origin master )` (subshell + env) -> blocked' \
  '( env GIT_TRACE=1 git push origin master )' \
  2 'BLOCKED'

scenario \
  '`( git push origin lane/foo )` (subshell + safe target) -> not blocked' \
  '( git push origin lane/foo )' \
  0 ''

# HIGH (Codex): quoted env values + quoted -C paths must NOT mistokenize.
# Awk produces a single token for `A="1 2"` (the value-with-space); previous
# bash word-split + sed regex produced two malformed tokens.
scenario \
  '`A="1 2" git push origin develop` (quoted env value w/ space) -> blocked' \
  'A="1 2" git push origin develop' \
  2 'BLOCKED'

scenario \
  '`A="" git push origin develop` (empty quoted env value) -> blocked' \
  'A="" git push origin develop' \
  2 'BLOCKED'

scenario \
  "A='1 2' git push origin develop (single-quoted env value w/ space) -> blocked" \
  "A='1 2' git push origin develop" \
  2 'BLOCKED'

scenario \
  '`env A="1 2" git push origin develop` -> blocked (env wrapper + quoted value)' \
  'env A="1 2" git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git -C "C:/repo with spaces" push origin develop` (quoted -C path) -> blocked' \
  'git -C "C:/repo with spaces" push origin develop' \
  2 'BLOCKED'

scenario \
  "git -c 'user.name=A B' push origin develop (quoted -c value w/ space) -> blocked" \
  "git -c 'user.name=A B' push origin develop" \
  2 'BLOCKED'

scenario \
  '`git -C "C:/repo with spaces" push origin lane/main/foo` (quoted -C, safe target) -> not blocked' \
  'git -C "C:/repo with spaces" push origin lane/main/foo' \
  0 ''

# CRITICAL (Codex): break-on-first segment match must be replaced by
# evaluate-all-segments. `git push origin lane/foo && git push origin develop`
# was rc=0 in iter-1 because only the first segment was inspected.
scenario \
  '`git push origin lane/foo && git push origin develop` -> blocked (segment 2 fires)' \
  'git push origin lane/foo && git push origin develop' \
  2 'BLOCKED'

scenario \
  '`git push origin develop && git push origin lane/foo` -> blocked (segment 1 fires)' \
  'git push origin develop && git push origin lane/foo' \
  2 'BLOCKED'

scenario \
  '`git push origin lane/a && git push origin lane/b` (both safe) -> not blocked' \
  'git push origin lane/a && git push origin lane/b' \
  0 ''

scenario \
  'three pushes, last protected -> blocked' \
  'git push origin lane/a && git push origin lane/b && git push origin master' \
  2 'BLOCKED'

scenario \
  'three pushes, middle protected -> blocked' \
  'git push origin lane/a && git push origin develop && git push origin lane/b' \
  2 'BLOCKED'

# ===========================================================================
# Iter-3 bypass closures (codex iter-2 audit)
# ===========================================================================

# Codex Critical: jq absent + escaped quotes in JSON command bypass via
# truncating sed fallback. Fix hard-blocks when jq is unavailable. Test via
# MERCURY_PUSH_GUARD_TEST_FORCE_NO_JQ=1 env override (Windows-portable —
# avoids fragile copy-system-utils-without-jq PATH gymnastics).
scenario \
  'jq absent + non-empty command -> hard-block (parser-fail)' \
  'git push origin lane/foo' \
  2 'BLOCKED' '' \
  'MERCURY_PUSH_GUARD_TEST_FORCE_NO_JQ=1'

scenario \
  'jq absent + would-be-allowed safe push still blocks (fail-closed)' \
  'git push origin lane/main/345' \
  2 'BLOCKED' '' \
  'MERCURY_PUSH_GUARD_TEST_FORCE_NO_JQ=1'

# Codex High #1: backslash escape outside double quotes. Bash parses
# `git push origin de\velop` as `git push origin develop`. The iter-2 awk
# tokenizer treated `\` as a literal char, leaving `de\velop` in the token,
# missing the protected-branch grep. Iter-3 awk now consumes the backslash
# and appends the escaped char.
scenario \
  '`git push origin de\velop` (outside-quote backslash escape) -> blocked' \
  'git push origin de\velop' \
  2 'BLOCKED'

scenario \
  '`git push origin maste\r` (outside-quote backslash escape) -> blocked' \
  'git push origin maste\r' \
  2 'BLOCKED'

scenario \
  '`git push origin HEAD:de\velop` (escaped refspec RHS) -> blocked' \
  'git push origin HEAD:de\velop' \
  2 'BLOCKED'

scenario \
  '`git push origin lane\/main\/foo` (gratuitous escapes on safe target) -> not blocked' \
  'git push origin lane\/main\/foo' \
  0 ''

# Codex High #2: prefix-grammar bypasses via `!` (bash logical NOT) and
# `--longopt` after a wrapper (`env --ignore-environment ...`).
scenario \
  '`! git push origin develop` (bash NOT) -> blocked' \
  '! git push origin develop' \
  2 'BLOCKED'

scenario \
  '`! ! git push origin develop` (double NOT) -> blocked' \
  '! ! git push origin develop' \
  2 'BLOCKED'

scenario \
  '`env --ignore-environment git push origin develop` -> blocked' \
  'env --ignore-environment git push origin develop' \
  2 'BLOCKED'

scenario \
  '`env -i git push origin develop` (short-form) -> blocked' \
  'env -i git push origin develop' \
  2 'BLOCKED'

scenario \
  '`env --null git push origin develop` (long flag-only) -> blocked' \
  'env --null git push origin develop' \
  2 'BLOCKED'

scenario \
  '`env --chdir=/tmp git push origin develop` (--longopt=value inline) -> blocked' \
  'env --chdir=/tmp git push origin develop' \
  2 'BLOCKED'

scenario \
  '`env --unset PATH git push origin develop` (separate-arg long value) -> blocked' \
  'env --unset PATH git push origin develop' \
  2 'BLOCKED'

scenario \
  '`env --chdir /tmp git push origin develop` (separate-arg --chdir) -> blocked' \
  'env --chdir /tmp git push origin develop' \
  2 'BLOCKED'

scenario \
  '`! ( env --ignore-environment git push origin master )` (combined !) -> blocked' \
  '! ( env --ignore-environment git push origin master )' \
  2 'BLOCKED'

# ===========================================================================
# Iter-4 bypass closures (codex iter-3 audit)
# ===========================================================================

# Codex iter-3 High: bare newlines outside quotes were not segment separators.
# `echo ok\ngit push origin develop` stayed one segment (echo, ok, git, push,
# origin, develop), walker exited on `echo`, the develop push was never
# inspected. Iter-4 awk emits __SEGEND__ at end-of-line outside quotes.
scenario \
  $'newline-separated `echo ok` + `git push origin develop` -> blocked' \
  $'echo ok\ngit push origin develop' \
  2 'BLOCKED'

scenario \
  $'newline-separated `git push origin lane/foo` + `git push origin develop` -> blocked' \
  $'git push origin lane/foo\ngit push origin develop' \
  2 'BLOCKED'

scenario \
  $'two-line both-safe pushes -> not blocked' \
  $'git push origin lane/a\ngit push origin lane/b' \
  0 ''

scenario \
  $'commit + newline + push to lane (S83-style minus heredoc) -> not blocked' \
  $'git commit -m "fix: develop master refs"\ngit push origin lane/main/foo' \
  0 ''

# Codex iter-3 High: bash control-flow reserved words (`if`/`then`/`else`/
# `elif`/`do`/`while`/`until`) added to wrapper-strip so guarded segments
# starting with these words still walk into the inner command.
scenario \
  '`if true; then git push origin develop; fi` (then-prefix) -> blocked' \
  'if true; then git push origin develop; fi' \
  2 'BLOCKED'

scenario \
  '`while true; do git push origin master; done` (do-prefix) -> blocked' \
  'while true; do git push origin master; done' \
  2 'BLOCKED'

scenario \
  '`if x; then echo safe; else git push origin develop; fi` (else-prefix) -> blocked' \
  'if x; then echo safe; else git push origin develop; fi' \
  2 'BLOCKED'

scenario \
  '`if x; then echo a; elif y; then git push origin develop; fi` (elif-prefix) -> blocked' \
  'if x; then echo a; elif y; then git push origin develop; fi' \
  2 'BLOCKED'

scenario \
  '`until git push origin develop; do : ; done` (until-prefix) -> blocked' \
  'until git push origin develop; do : ; done' \
  2 'BLOCKED'

scenario \
  '`if true; then git push origin lane/foo; fi` (then-prefix, safe target) -> not blocked' \
  'if true; then git push origin lane/foo; fi' \
  0 ''

# Codex iter-3 Medium: trailing backslash line-continuation outside quotes.
# `git push origin devel\<NL>op` should parse as `git push origin develop`
# (bash drops both `\` and newline, joining lines). Iter-3 awk dropped the
# backslash but flushed the token at end-of-line, so `devel` and `op` became
# separate tokens, missing the PROTECTED grep. Iter-4 awk tracks
# line_continue state to suppress the end-of-line flush + __SEGEND__.
scenario \
  $'`git push origin devel\\<NL>op` (line-continuation across develop) -> blocked' \
  $'git push origin devel\\\nop' \
  2 'BLOCKED'

scenario \
  $'`git push origin maste\\<NL>r` (line-continuation across master) -> blocked' \
  $'git push origin maste\\\nr' \
  2 'BLOCKED'

scenario \
  $'`git \\<NL>push origin develop` (line-cont across cmd boundary) -> blocked' \
  $'git \\\npush origin develop' \
  2 'BLOCKED'

scenario \
  $'`git push origin lane/\\<NL>main/foo` (line-cont safe target) -> not blocked' \
  $'git push origin lane/\\\nmain/foo' \
  0 ''

# ===========================================================================
# Iter-5 bypass closures (codex iter-4 audit)
# ===========================================================================

# Codex iter-4 High: `coproc` keyword unhandled. `coproc git push origin
# develop` previously had `coproc` as leading token → walker exit before git.
scenario \
  '`coproc git push origin develop` (coproc unnamed) -> blocked' \
  'coproc git push origin develop' \
  2 'BLOCKED'

scenario \
  '`coproc CO git push origin develop` (coproc named) -> blocked' \
  'coproc CO git push origin develop' \
  2 'BLOCKED'

scenario \
  '`coproc git push origin lane/foo` (coproc safe target) -> not blocked' \
  'coproc git push origin lane/foo' \
  0 ''

# Codex iter-4 High: top-level command substitution `$(...)` and backtick
# `` `...` `` previously buffered as opaque tokens → inner `git push` invisible.
# Iter-5 awk treats `(` `)` `{` `}` and backtick as segment separators;
# inner content becomes its own segment.
scenario \
  '`echo $(git push origin develop)` (command substitution dollar-paren) -> blocked' \
  'echo $(git push origin develop)' \
  2 'BLOCKED'

scenario \
  '`echo $(git push origin lane/foo)` (cmdsub safe target) -> not blocked' \
  'echo $(git push origin lane/foo)' \
  0 ''

scenario \
  'backtick command substitution with protected target -> blocked' \
  'echo `git push origin develop`' \
  2 'BLOCKED'

scenario \
  'backtick command substitution with safe target -> not blocked' \
  'echo `git push origin lane/foo`' \
  0 ''

# Codex iter-4 High: `case ... in PAT) CMD ;; esac` compound-command body.
# Iter-5 `)` separator makes the body a clean segment.
scenario \
  '`case x in x) git push origin develop ;; esac` -> blocked' \
  'case x in x) git push origin develop ;; esac' \
  2 'BLOCKED'

scenario \
  '`case x in x) git push origin lane/foo ;; esac` (safe target) -> not blocked' \
  'case x in x) git push origin lane/foo ;; esac' \
  0 ''

# Codex iter-4 High: `function f { ... }; f` and `f() { ... }; f` body.
# Iter-5: `{` separator splits body out; `function`+name skip handles
# declaration prefix.
scenario \
  '`function f { git push origin develop; }; f` -> blocked' \
  'function f { git push origin develop; }; f' \
  2 'BLOCKED'

scenario \
  '`f() { git push origin develop; }; f` (no function keyword) -> blocked' \
  'f() { git push origin develop; }; f' \
  2 'BLOCKED'

scenario \
  '`function f { git push origin lane/foo; }; f` (safe target) -> not blocked' \
  'function f { git push origin lane/foo; }; f' \
  0 ''

# Combined obfuscation patterns
scenario \
  '`( ! coproc env --ignore-environment git push origin master )` (max obfuscation) -> blocked' \
  '( ! coproc env --ignore-environment git push origin master )' \
  2 'BLOCKED'

scenario \
  'nested `( ( git push origin develop ) )` -> blocked' \
  '( ( git push origin develop ) )' \
  2 'BLOCKED'

# ===========================================================================
# Iter-6 bypass closure (Argus iter-1 finding on PR #352)
# ===========================================================================

# Argus Minor #1: `git push REFSPEC` (no remote arg) is valid syntax —
# git treats `HEAD:develop` as a refspec, not a remote name. Iter-5
# SKIPPED_REMOTE eat-first-non-flag heuristic missed this case and let
# Phase 3 fall through to current-branch check.
scenario \
  '`git push HEAD:develop` (no remote, refspec only) -> blocked' \
  'git push HEAD:develop' \
  2 'BLOCKED'

scenario \
  '`git push HEAD:master` (no remote, refspec) -> blocked' \
  'git push HEAD:master' \
  2 'BLOCKED'

scenario \
  '`git push +develop` (no remote, force prefix) -> blocked' \
  'git push +develop' \
  2 'BLOCKED'

scenario \
  '`git push +HEAD:develop` (no remote, force + refspec) -> blocked' \
  'git push +HEAD:develop' \
  2 'BLOCKED'

scenario \
  '`git push refs/heads/develop` (no remote, fully-qualified ref) -> blocked' \
  'git push refs/heads/develop' \
  2 'BLOCKED'

scenario \
  '`git push refs/heads/master` (no remote, fully-qualified ref) -> blocked' \
  'git push refs/heads/master' \
  2 'BLOCKED'

scenario \
  '`git push HEAD:lane/foo` (no remote, refspec to safe target) -> not blocked' \
  'git push HEAD:lane/foo' \
  0 ''

scenario \
  '`git push +lane/foo` (force-push to safe target, no remote) -> not blocked' \
  'git push +lane/foo' \
  0 ''

scenario \
  '`git push refs/heads/lane/foo` (no remote, fully-qualified safe ref) -> not blocked' \
  'git push refs/heads/lane/foo' \
  0 ''

# ===========================================================================
# Iter-7 bypass closures (Argus iter-2 + Copilot iter-1)
# ===========================================================================

# Argus iter-2 Critical (token-collision): with the iter-1..6 untyped
# protocol (`__SEGEND__` literal as separator marker), a real shell token
# value equal to that literal would mis-classify as a separator. The fix
# adopts a typed line-protocol: `TOK\t<value>` for tokens, `SEG` for
# separators. With TOK\t prefix, ANY token value (including literally
# `SEG` or `__SEGEND__`) is unambiguously a token.
scenario \
  '`git push origin __SEGEND__ develop` (legacy-sentinel token-collision) -> blocked' \
  'git push origin __SEGEND__ develop' \
  2 'BLOCKED'

scenario \
  '`git push origin SEG develop` (current-sentinel token-collision) -> blocked' \
  'git push origin SEG develop' \
  2 'BLOCKED'

scenario \
  '`git push origin __SEGEND__:develop` (sentinel inside refspec) -> blocked' \
  'git push origin __SEGEND__:develop' \
  2 'BLOCKED'

# Copilot iter-1 line 300: `sudo`/`doas` were missing from wrapper-strip,
# so `sudo git push origin develop` slipped past as non-git leading token.
scenario \
  '`sudo git push origin develop` -> blocked' \
  'sudo git push origin develop' \
  2 'BLOCKED'

scenario \
  '`sudo -E git push origin develop` (preserve env flag) -> blocked' \
  'sudo -E git push origin develop' \
  2 'BLOCKED'

scenario \
  '`sudo -u username git push origin develop` (sudo with -u value) -> blocked' \
  'sudo -u username git push origin develop' \
  2 'BLOCKED'

scenario \
  '`doas git push origin develop` (BSD doas wrapper) -> blocked' \
  'doas git push origin develop' \
  2 'BLOCKED'

scenario \
  '`sudo --preserve-env=PATH git push origin master` (long flag inline value) -> blocked' \
  'sudo --preserve-env=PATH git push origin master' \
  2 'BLOCKED'

scenario \
  '`sudo git push origin lane/foo` (sudo + safe target) -> not blocked' \
  'sudo git push origin lane/foo' \
  0 ''

# ===========================================================================
# Summary
# ===========================================================================

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
