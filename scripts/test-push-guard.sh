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

# Mock git shim. push-guard.sh invokes `git rev-parse --abbrev-ref HEAD`
# during Phase 3 implicit-push detection, and `git -C <path> rev-parse
# [--path-format=absolute] --git-common-dir` (Issue #552 iter-2, checked
# by just looking for `--git-common-dir` anywhere in the rev-parse args)
# both during the Phase 0 repo-awareness check AND (iter-5) during Pass
# B's same-repo-only cd/pushd tracking gate.
#
# The mock returns $MOCK_CURRENT_BRANCH (default `feature/test`) for the
# HEAD query. For the common-dir query, in priority order:
#   1. $MOCK_COMMON_DIR explicitly SET (even empty, to simulate a
#      resolution failure) — wins unconditionally, for ANY queried path.
#      Used by scenarios that need to isolate pure token-parsing behavior
#      from repo-identity behavior (e.g. the `-C "quoted path"` tests).
#   2. PATH-AWARE PREFIX MATCH (Issue #552 iter-5 — Codex Medium, mutation
#      sensitivity): if the queried `-C <path>` starts with
#      $MOCK_MERCURY_PREFIX (default set by run_hook to $fake_project ==
#      CLAUDE_PROJECT_DIR) it resolves to a fixed `mercury-common-dir`
#      sentinel; if it starts with $MOCK_OTHER_PREFIX (default
#      `/other/repo`) it resolves to a DIFFERENT fixed
#      `other-repo-common-dir` sentinel. This is what makes cd-tracking
#      scenarios actually PROVE the tracker is live: without it, "cd to
#      Mercury" and "stay in the external repo" scenarios could not be
#      told apart by the mock at all (an earlier revision pinned BOTH
#      sides to the same value regardless of path, which passed whether
#      or not Pass B's cd-tracking code ran — no mutation sensitivity).
#      With prefix matching, a scenario proving "cd tracked into Mercury
#      → blocked" only passes if EFFECTIVE_CWD genuinely advanced to a
#      Mercury-prefixed path; deleting Pass B would leave EFFECTIVE_CWD at
#      the original (non-Mercury-prefixed) HOOK_CWD and the scenario would
#      flip to "allowed", failing the assertion.
#   3. Fallback: echo the queried `-C <path>` back as its own common-dir
#      (with a trailing `/.` stripped, crudely mirroring git's own path
#      canonicalization). Used by scenarios with paths outside both
#      prefixes.
cat > "$BIN_DIR/git" <<'MOCK_EOF'
#!/usr/bin/env bash
# mock git used by test-push-guard.sh — emulates rev-parse HEAD / --git-common-dir
raw_c_path=""
had_dash_c=0
if [[ "${1:-}" == "-C" ]]; then
  had_dash_c=1
  raw_c_path="${2:-}"
  shift 2
fi
# Crude canonicalization: strip a trailing "/." so `-C .` (relative,
# resolved by the hook against HOOK_CWD before reaching here) echoes back
# identically to querying HOOK_CWD directly.
raw_c_path="${raw_c_path%/.}"
if [[ "${1:-}" == "rev-parse" && "${2:-}" == "--abbrev-ref" && "${3:-}" == "HEAD" ]]; then
  # Issue #552 iter-8 item2: a `-C <path>`-qualified query answers with
  # $MOCK_CURRENT_BRANCH (the RESOLVED target repo's branch); a bare query
  # (no `-C`, simulating the hook process's own unrelated cwd) answers with
  # $MOCK_CURRENT_BRANCH_BARE, falling back to $MOCK_CURRENT_BRANCH when
  # unset so every pre-iter-8 scenario (which never sets
  # MOCK_CURRENT_BRANCH_BARE) is unaffected. This split is what lets a
  # scenario PROVE Phase 3 now queries the Phase-0-resolved repo instead of
  # the bare process cwd: pin the two to different branches and only the
  # fixed code path reaches the protected one.
  if [[ "$had_dash_c" -eq 1 ]]; then
    printf '%s\n' "${MOCK_CURRENT_BRANCH:-feature/test}"
  else
    printf '%s\n' "${MOCK_CURRENT_BRANCH_BARE:-${MOCK_CURRENT_BRANCH:-feature/test}}"
  fi
  exit 0
fi
if [[ "${1:-}" == "rev-parse" ]]; then
  shift
  for a in "$@"; do
    if [[ "$a" == "--git-common-dir" ]]; then
      if [[ -n "${MOCK_COMMON_DIR+set}" ]]; then
        printf '%s\n' "${MOCK_COMMON_DIR}"
      elif [[ -n "${MOCK_MERCURY_PREFIX:-}" && ( "$raw_c_path" == "$MOCK_MERCURY_PREFIX" || "$raw_c_path" == "$MOCK_MERCURY_PREFIX"/* ) ]]; then
        printf '%s\n' "mercury-common-dir-sentinel"
      elif [[ -n "${MOCK_OTHER_PREFIX:-}" && ( "$raw_c_path" == "$MOCK_OTHER_PREFIX" || "$raw_c_path" == "$MOCK_OTHER_PREFIX"/* ) ]]; then
        printf '%s\n' "other-repo-common-dir-sentinel"
      else
        printf '%s\n' "${raw_c_path}"
      fi
      exit 0
    fi
  done
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
  local fake_project="$WORK_DIR/proj-$RANDOM"
  mkdir -p "$fake_project"
  # Issue #552 iter-5: scenarios that need the LITERAL Mercury path inside
  # the command string itself (e.g. `cd __MERCURY_PATH__ && git push
  # origin develop`) cannot reference `$fake_project` directly — it's a
  # fresh mktemp-random value generated INSIDE this function, unknown to
  # the static scenario string written before run_hook ever runs. The
  # `__MERCURY_PATH__` placeholder is substituted here, after
  # `$fake_project` is computed, closing that chicken-and-egg gap.
  cmd="${cmd//__MERCURY_PATH__/$fake_project}"
  # Issue #552: `cwd` mirrors the hook stdin JSON's real top-level field
  # (https://code.claude.com/docs/en/hooks — "current working directory
  # when the hook is invoked"). Defaults to $fake_project (== the process's
  # CLAUDE_PROJECT_DIR) so pre-#552 scenarios naturally resolve "same repo"
  # via the mock git's `-C <path>` echo-back behavior and stay unaffected.
  # A scenario can override via the special `JSON_CWD=<value>` pair in
  # extra_env (stripped below before forwarding the rest as real env vars —
  # it is a test-harness-only signal, not read by the hook process itself).
  #
  # MSYS2_ARG_CONV_EXCL note (Windows/Git-Bash only, harmless elsewhere):
  # `jq` below is a native Windows binary (jq.exe). MSYS auto-translates
  # any argv that LOOKS like a POSIX path (e.g. our mktemp-produced
  # `/tmp/push-guard-test.XXXXXX/proj-NNNNN`) into its Windows form
  # (`C:/Users/.../Temp/...`) before jq ever sees it — but only for THAT
  # jq invocation's argv, not for the same value used directly as a bash
  # env var (`CLAUDE_PROJECT_DIR="$fake_project"` below, untouched). Same
  # physical directory, two divergent string forms reaching push-guard.sh's
  # Phase 0 repo-awareness check — a false "different repo" positive that
  # is purely a test-fixture MSYS artifact (production `cwd` values come
  # from Claude Code itself, never through this argv-translation path).
  # `MSYS2_ARG_CONV_EXCL='*'` suppresses the translation for this jq call
  # so `$json_cwd` reaches the JSON payload byte-for-byte identical to how
  # `CLAUDE_PROJECT_DIR` reaches the hook process.
  local json_cwd="$fake_project"
  local -a extra_env_kv=()
  if [[ -n "$extra_env" ]]; then
    IFS=';' read -r -a extra_env_kv <<< "$extra_env"
  fi
  local -a real_env_kv=()
  local kv
  for kv in "${extra_env_kv[@]}"; do
    case "$kv" in
      JSON_CWD=*) json_cwd="${kv#JSON_CWD=}" ;;
      *) real_env_kv+=( "$kv" ) ;;
    esac
  done
  # Build JSON via jq -n so all JSON escape rules are handled correctly
  # (closes Copilot iter-2 line-117 finding: the previous manual escape
  # only covered \\, ", and \n; control chars like \r, \t, and 0x00..0x1f
  # would have produced malformed JSON). jq is required by the hook
  # itself, so the dependency was already implicit; making it explicit in
  # the harness is a robustness no-op for our environment but eliminates
  # a class of test-side input encoding bugs.
  local input
  input=$(MSYS2_ARG_CONV_EXCL='*' jq -nc --arg cmd "$cmd" --arg cwd "$json_cwd" '{tool_input: {command: $cmd}, cwd: $cwd}') || {
    printf 'run_hook: jq -n failed to encode JSON input\n' >&2
    return 1
  }
  local rc
  # MOCK_MERCURY_PREFIX/MOCK_OTHER_PREFIX default to $fake_project (==
  # CLAUDE_PROJECT_DIR, so unqualified scenarios naturally see "the same
  # repo as Mercury" by construction) and the conventional `/other/repo`
  # literal already used throughout this suite's `JSON_CWD=`/`-C` values.
  # Scenarios needing a different mapping override via extra_env (later
  # `env` argv entries win).
  env "PATH=$BIN_DIR:$PATH" "CLAUDE_PROJECT_DIR=$fake_project" \
    "MOCK_MERCURY_PREFIX=$fake_project" "MOCK_OTHER_PREFIX=/other/repo" \
    "${real_env_kv[@]}" bash "$HOOK" 2> "$WORK_DIR/stderr.$$" <<<"$input" >/dev/null
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

# Issue #552: `-C /repo` is a fake, non-existent path — under repo-awareness
# it now (correctly) resolves as "a different repo" and would be ALLOWED
# through on its own. This scenario predates #552 and exists to test `-C`
# TOKEN PARSING robustness (quote/wrapper handling), not repo identity, so
# MOCK_COMMON_DIR pins the resolved common-dir to match Mercury's regardless
# of the literal `-C` value, isolating the parsing behavior under test.
scenario \
  '`git -C /repo push origin develop` -> blocked' \
  'git -C /repo push origin develop' \
  2 'BLOCKED' '' \
  'MOCK_COMMON_DIR=pinned-same-repo'

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

# Issue #552: same rationale as the `-C /repo` scenario above — pin
# MOCK_COMMON_DIR so this stays a pure quote-parsing test, unaffected by
# the new repo-awareness check.
scenario \
  '`git -C "C:/repo with spaces" push origin develop` (quoted -C path) -> blocked' \
  'git -C "C:/repo with spaces" push origin develop' \
  2 'BLOCKED' '' \
  'MOCK_COMMON_DIR=pinned-same-repo'

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
# Iter-8 bypass closures (Argus iter-3 + Copilot iter-2)
# ===========================================================================

# Copilot iter-2 line-199 (real bypass): backslash-newline INSIDE double
# quotes is bash line-continuation (drops both `\` and newline). Iter-7 awk
# only handled this OUTSIDE quotes. Inside dq, `\` was appended as literal
# and the implicit EOL inserted a space, splitting `develop` into
# `devel\<space>op` — missing the PROTECTED grep.
scenario \
  $'`git push origin "devel\\<NL>op"` (dq line-cont across develop) -> blocked' \
  $'git push origin "devel\\\nop"' \
  2 'BLOCKED'

scenario \
  $'`git push origin "maste\\<NL>r"` (dq line-cont across master) -> blocked' \
  $'git push origin "maste\\\nr"' \
  2 'BLOCKED'

scenario \
  $'`git push origin "lane/\\<NL>main/foo"` (dq line-cont safe target) -> not blocked' \
  $'git push origin "lane/\\\nmain/foo"' \
  0 ''

# Carriage-return + tab in command (regression for Copilot iter-2 line-117
# JSON-escaping). Pre-fix: harness escaped only \, ", \n; \r and \t in cmd
# would have produced malformed JSON. With jq -n encoding, any literal
# byte is encoded correctly.
scenario \
  $'`git push origin lane/foo\\twith-tab` (literal tab in target — JSON encoding) -> not blocked' \
  $'git push origin lane/foo\twith-tab' \
  0 ''

scenario \
  $'CR in body (literal \\r) does not crash JSON encoding — not blocked' \
  $'git commit -m "develop\\rmaster" && git push origin lane/foo' \
  0 ''

# ===========================================================================
# Issue #552 — repo-awareness: non-Mercury-repo pushes must be allowed
# through even when they target develop/master/main; Mercury-repo pushes
# must still be blocked in every form.
#
# Mock semantics (see mock git above): a `-C <path>` query echoes <path>
# back as its own common-dir, unless $MOCK_COMMON_DIR is explicitly set
# (even to empty), which then wins for ALL common-dir queries. So:
#   - `-C /other/repo` or a JSON `cwd` of `/other/repo` naturally resolves
#     to a common-dir that differs from MERCURY_COMMON_DIR (resolved from
#     CLAUDE_PROJECT_DIR == $fake_project) -> Phase 0 fires -> allowed.
#   - Omitting any override, or targeting $fake_project itself, resolves
#     "same repo" -> falls through to existing Phase 1-3 -> blocked as before.
#   - `MOCK_COMMON_DIR=` (explicitly empty) forces BOTH sides to resolve
#     empty, simulating a real `rev-parse` failure -> Phase 0 cannot
#     determine anything -> fail-closed -> falls through -> blocked.
#   - `MOCK_COMMON_DIR=<fixed value>` (non-empty) pins BOTH sides to the
#     SAME value regardless of path — models "different path, same repo"
#     (e.g. a linked worktree vs its main checkout).
# ===========================================================================

scenario \
  '#552 non-Mercury repo `git push origin master` via cwd -> allowed (out of scope)' \
  'git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 non-Mercury repo `git push origin develop` via cwd -> allowed' \
  'git push origin develop' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 non-Mercury repo `git -C /other/repo push origin master` (-C authoritative) -> allowed' \
  'git -C /other/repo push origin master' \
  0 ''

scenario \
  '#552 non-Mercury repo implicit `git push` from branch=develop -> allowed (Phase 3 also skipped)' \
  'git push' \
  0 '' '' \
  'JSON_CWD=/other/repo;MOCK_CURRENT_BRANCH=develop'

scenario \
  '#552 non-Mercury repo refspec `git push origin HEAD:master` -> allowed' \
  'git push origin HEAD:master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 non-Mercury repo `-C` wins over a Mercury-repo cwd -> allowed' \
  'git -C /other/repo push origin develop' \
  0 '' '' \
  'JSON_CWD=/keep/this/as/mercury'

# Regression net: Mercury-repo pushes (no cwd/-C divergence — the run_hook
# default) still block in every existing form.
scenario \
  '#552 Mercury repo `git push origin develop` (default cwd) -> still blocked' \
  'git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 Mercury repo `git push origin master` (default cwd, no -C) -> still blocked' \
  'git push origin master' \
  2 'BLOCKED'

scenario \
  '#552 Mercury repo implicit `git push` from branch=master -> still blocked' \
  'git push' \
  2 'BLOCKED' '' \
  'MOCK_CURRENT_BRANCH=master'

# Fail-closed: common-dir resolution fails (rev-parse returns nothing for
# EITHER side) -> falls through to existing Phase 1-3 logic -> still blocked.
scenario \
  '#552 common-dir resolution fails (MOCK_COMMON_DIR empty) -> fail-closed, still blocked' \
  'git push origin develop' \
  2 'BLOCKED' '' \
  'MOCK_COMMON_DIR=;JSON_CWD=/other/repo'

scenario \
  '#552 non-Mercury repo, safe target `git push origin lane/foo` -> allowed (unaffected either way)' \
  'git push origin lane/foo' \
  0 '' '' \
  'JSON_CWD=/other/repo'

# ===========================================================================
# Issue #552 iter-2 (dual-verify NEEDS-CHANGES, real hook-call
# reproductions — see Issue #552 fix-history comment in push-guard.sh for
# the T1-T5 evidence this section formalizes into the regression suite)
# ===========================================================================

# T1 (Critical, most severe): a linked-worktree cwd — the hook's own NORMAL
# execution context for every Mercury dev agent — must NOT be misread as
# "a different repo" just because its `-C`/cwd path string differs from
# CLAUDE_PROJECT_DIR. MOCK_COMMON_DIR pins both sides to the SAME value,
# modeling "worktree path differs, but --git-common-dir agrees" (exactly
# what real git does — verified live: `D:/Mercury/Mercury/.claude/
# worktrees/agent-.../` and `D:/Mercury/Mercury` both resolve
# --git-common-dir to `D:/Mercury/Mercury/.git`).
scenario \
  '#552 iter-2 T1: worktree cwd (differs from CLAUDE_PROJECT_DIR) but same git-common-dir -> still blocked' \
  'git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/some/worktree/path;MOCK_COMMON_DIR=pinned-common-dir'

# T2: inline `GIT_DIR=`/`GIT_WORK_TREE=` env assignments (direct prefix and
# `env`-wrapped form) redirect the real target repo — Phase 0 must not be
# swayed by an accompanying non-Mercury cwd; the whole segment falls back
# to Phase 1-3 fail-closed instead.
scenario \
  '#552 iter-2 T2a: inline GIT_DIR= assignment -> fail-closed, still blocked despite non-Mercury cwd' \
  'GIT_DIR=/other/repo/.git git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-2 T2b: inline GIT_WORK_TREE= assignment -> fail-closed, still blocked' \
  'GIT_WORK_TREE=/other/repo git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-2 T2c: `env GIT_DIR=... GIT_WORK_TREE=...` wrapper form -> fail-closed, still blocked' \
  'env GIT_DIR=/other/repo/.git GIT_WORK_TREE=/other/repo git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# T3: `--git-dir`/`--work-tree` global options (both `=` and separate-arg
# forms) — same rationale as T2, expressed as git flags instead of env vars.
scenario \
  '#552 iter-2 T3a: `--git-dir=`/`--work-tree=` inline -> fail-closed, still blocked' \
  'git --git-dir=/other/repo/.git --work-tree=/other/repo push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-2 T3b: `--git-dir`/`--work-tree` separate-arg -> fail-closed, still blocked' \
  'git --git-dir /other/repo/.git --work-tree /other/repo push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# T4/T5 regression net (already covered above by the pre-existing "default
# cwd" and "non-Mercury repo" scenarios, restated here for direct
# traceability to the Issue #552 iter-2 review table).
scenario \
  '#552 iter-2 T4: main-checkout cwd, `git push origin develop` -> still blocked (unchanged)' \
  'git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-2 T5: non-Mercury-repo cwd, `git push origin master` -> allowed (unchanged)' \
  'git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

# Repeated `-C`: git's real semantics is cumulative-relative (`-C a -C b`
# == `-C a/b`), not "last value wins" — unmodeled, so Phase 0 must skip
# entirely (fail-closed) rather than resolve against just the last `-C`.
# A protected target must still block; a safe target is unaffected (Phase
# 0 skip only withholds an ALLOW, it never manufactures a BLOCK).
scenario \
  '#552 iter-2: repeated `-C` (cumulative-relative semantics unmodeled) -> fail-closed, protected target still blocked' \
  'git -C /other/repo -C /another/segment push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-2: repeated `-C`, safe target -> not blocked (Phase 0 skip does not manufacture a block)' \
  'git -C /other/repo -C /another/segment push origin lane/foo' \
  0 ''

# Relative `-C` must resolve against HOOK_CWD (where the command actually
# executes), not the hook process's own cwd.
scenario \
  '#552 iter-2: relative `-C .` resolves against HOOK_CWD (default = CLAUDE_PROJECT_DIR) -> still blocked (same repo)' \
  'git -C . push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-2: relative `-C sub` from a non-Mercury HOOK_CWD -> allowed (still resolves to the non-Mercury repo)' \
  'git -C sub push origin develop' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-2: relative `-C` with no HOOK_CWD to anchor it -> cannot resolve, fail-closed, still blocked' \
  'git -C sub push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD='

# ===========================================================================
# Issue #552 iter-3 (superseded by iter-4 below for `cd`/`pushd`/`popd` —
# see the iter-4 block for those; `--chdir`/glued `-C<path>` are still
# flat, position-agnostic Pass A triggers unaffected by the iter-4
# cd-tracking rewrite, kept here unchanged)
# ===========================================================================

# `env --chdir=<dir> <cmd>` — verified real GNU coreutils env(1) behavior,
# runs <cmd> with its cwd changed to <dir> before exec. Unlike `cd`/
# `pushd` (now tracked precisely — see iter-4 below), `--chdir` stays a
# blunt "disable Phase 0 for the whole command" trigger regardless of
# where it appears, since resolving its exact effect on a later segment's
# cwd would require the same tracking complexity for comparatively rare
# real-world usage.
scenario \
  '#552 `env --chdir=<dir> git push origin develop` -> whole-command Phase 0 disabled, still blocked' \
  'env --chdir=/mercury git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 `env --chdir <dir> git push origin develop` (separate-arg form) -> still blocked' \
  'env --chdir /mercury git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# Glued `-C<path>` (no separator) — verified live that real git rejects
# this as `unknown option`, so it is NOT exploitable today; still flagged
# defensively (zero-cost — see fix-history comment in push-guard.sh).
scenario \
  '#552 glued `-C<path>` (git rejects this syntax; defensive-only) -> still blocked' \
  'git -C/mercury push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# ── Phase-0-existence pin (Codex: fail-closed scenarios alone cannot
# prove Phase 0 is doing anything, since Phase 1-3 blocks by default with
# or without it) ──────────────────────────────────────────────────────
# This scenario ONLY passes if Phase 0 is actually resolving the target
# repo and granting an allow: with Phase 0 deleted entirely, Phase 1-3
# alone would see `push origin develop` and unconditionally block it
# regardless of `cwd`. A non-Mercury-repo push to a branch NAMED `develop`
# passing with exit 0 is therefore direct positive evidence that Phase 0
# is live, not just that Phase 1-3 didn't misfire.
scenario \
  '#552 Phase-0-existence pin: non-Mercury repo push to a branch literally named `develop` -> allowed (only possible because Phase 0 is active)' \
  'git push origin develop' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 regression: `-C <absolute-path>` (properly separated, not glued) still resolves and allows a force-push to +master' \
  'git -C /other/repo push origin +master' \
  0 ''

# ===========================================================================
# Issue #552 iter-4/iter-5 (dual-verify NEEDS-CHANGES rounds 3-4 — Codex +
# coordinator independent repro; see the iter-4/iter-5 fix-history
# comments in push-guard.sh for full before/after rationale).
#
# Mutation-sensitivity note (Codex iter-5 Medium, accepted): these
# scenarios use PATH-AWARE mock mapping (`MOCK_MERCURY_PREFIX`/
# `MOCK_OTHER_PREFIX`, defaulted by run_hook to `$fake_project`/
# `/other/repo` — see the mock git comment above) instead of a blanket
# `MOCK_COMMON_DIR` pin. A blanket pin makes BOTH sides of every
# comparison resolve identically regardless of whether the tracker ran at
# all, so a scenario built on it passes whether or not Pass B's cd-
# tracking exists — it proves nothing. With prefix-based mapping, a "cd
# INTO Mercury -> blocked" scenario genuinely requires EFFECTIVE_CWD to
# have advanced to a Mercury-prefixed path (via `__MERCURY_PATH__`,
# substituted to the real `$fake_project` at run_hook time — see its
# comment) for Phase 0 to correctly withhold the allow; deleting Pass B
# would leave `EFFECTIVE_CWD` at the original `/other/repo` HOOK_CWD and
# the scenario would flip results.
# ===========================================================================

# ── MUST BLOCK: cd/pushd/popd/source/indirection actually reaches
# Mercury, tracked from an external starting cwd ───────────────────────
scenario \
  '#552: `cd <mercury> && git push origin develop` -> tracked (path-aware mock), resolves to Mercury, still blocked' \
  'cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `\cd <mercury> && git push origin develop` (backslash-escaped, awk still tokenizes to bare `cd`) -> tracked, still blocked' \
  '\cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `builtin cd <mercury> && git push origin develop` (wrapper-stripped before cd-detection) -> tracked, still blocked' \
  'builtin cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `command cd <mercury> && git push origin develop` -> tracked, still blocked' \
  'command cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `pushd <mercury> && git push origin develop` -> tracked same as cd, still blocked' \
  'pushd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `popd && git push origin develop` -> popd needs an untracked stack, UNSAFE_STATE unconditionally, still blocked' \
  'popd && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `source ./x.sh; git push origin develop` (two segments — the VISIBLE push in segment 2 blocks via ordinary Phase 1-3, independent of Phase 0)' \
  'source ./x.sh; git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `. ./x.sh && git push origin develop` (dot-source form) -> same as source, still blocked' \
  '. ./x.sh && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `X=cd; $X <mercury> && git push origin develop` (variable holding "cd") -> command-position token is `$X`, contains `$`, UNSAFE_STATE, still blocked' \
  'X=cd; $X __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# ── Issue #552 iter-5: MUST BLOCK — the REVERSE direction (cwd=Mercury,
# push genuinely executes IN Mercury; a `cd` toward an external repo is
# present in the token stream but never actually takes effect in the
# parent shell — short-circuited, subshell, pipeline, coproc, or a
# never-taken branch). Default `run_hook` cwd (no JSON_CWD override) IS
# Mercury (`$fake_project` == `CLAUDE_PROJECT_DIR`); the `cd` targets
# `/other/repo` (the default `MOCK_OTHER_PREFIX`). The iter-4 tracker
# would have wrongly advanced EFFECTIVE_CWD to `/other/repo` for all six;
# the iter-5 same-repo-only gate refuses to cross OUT of Mercury and sets
# UNSAFE_STATE instead — Phase 0 never gets a chance to grant a false
# ALLOW on a push that is really hitting Mercury.
# ===========================================================================
scenario \
  '#552 iter-5: `false && cd <external>; git push origin develop` (short-circuited, cd never runs) -> UNSAFE_STATE, still blocked' \
  'false && cd /other/repo; git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-5: `true || cd <external>; git push origin develop` (short-circuited, cd never runs) -> UNSAFE_STATE, still blocked' \
  'true || cd /other/repo; git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-5: `( cd <external> ; : ) ; git push origin develop` (subshell — cd does not affect the parent shell) -> UNSAFE_STATE, still blocked' \
  '( cd /other/repo ; : ) ; git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-5: `cd <external> | cat ; git push origin develop` (pipeline stage — builtin cd in a pipeline runs in a subshell) -> UNSAFE_STATE, still blocked' \
  'cd /other/repo | cat ; git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-5: `coproc cd <external>; git push origin develop` (background coproc job, not the parent shell) -> UNSAFE_STATE, still blocked' \
  'coproc cd /other/repo; git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-5: `if false; then cd <external>; fi; git push origin develop` (never-taken branch) -> UNSAFE_STATE, still blocked' \
  'if false; then cd /other/repo; fi; git push origin develop' \
  2 'BLOCKED'

# ── `eval`: iter-5 removes the iter-4 supplementary text-payload scan
# (unreliable — bypassable via string construction, false-positive-prone
# on benign payloads). `eval` still sets UNSAFE_STATE (disables Phase 0's
# potential ALLOW) but Phase 1-3 has no visibility into the quoted payload
# either (same as the pre-#552 `bash -c "..."` limitation) — so a push
# hidden entirely inside `eval`'s quotes is neither allowed NOR blocked by
# this hook; it is simply invisible, exactly as it always was before #552
# shipped. Not a regression: this hook never protected against it.
scenario \
  '#552 iter-5: `eval "cd <mercury> && git push origin develop"` -> UNSAFE_STATE only (no hard-block; payload invisible to Phase 1-3, same as pre-#552)' \
  'eval "cd __MERCURY_PATH__ && git push origin develop"' \
  0 ''
scenario \
  '#552 iter-5: `eval '"'"'echo git push origin develop'"'"'` (benign payload that only PRINTS the text) -> not blocked (the removed scan would have false-positived here)' \
  "eval 'echo git push origin develop'; git push origin lane/foo" \
  0 ''

# ── MUST ALLOW: cd stays within (or `cd` is not really a shell cd at
# all in) a non-Mercury repo — Issue #552's entire reason to exist ────
scenario \
  '#552: external repo `cd docs && git push origin master` (natural subdirectory workflow) -> tracked, same-repo (path-aware mock), allowed' \
  'cd docs && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-5: external repo `cd docs && cd .. && git push origin master` (multi-hop same-repo tracking) -> both hops resolve to the SAME repo, allowed' \
  'cd docs && cd .. && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: external repo `git commit -m cd && git push origin master` (`cd` is COMMIT MESSAGE DATA, not command position) -> not treated as cd, allowed' \
  'git commit -m cd && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: external repo `echo cd && git push origin master` (`cd` is an argument to echo, not command position) -> not treated as cd, allowed' \
  'echo cd && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: external repo `git add -A && git commit -m x && git push origin master` (no state-transition token at all) -> unaffected, allowed' \
  'git add -A && git commit -m x && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

# ── cd-tracking correctness: unsafe forms fall back to whole-command
# disable (fail-closed) rather than being silently mistracked ─────────
scenario \
  '#552: bare `cd` (no args, targets unknowable $HOME) && git push origin develop -> UNSAFE_STATE, still blocked' \
  'cd && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `cd -` (previous dir, unknowable) && git push origin develop -> UNSAFE_STATE, still blocked' \
  'cd - && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `cd $VAR` (variable in arg) && git push origin develop -> UNSAFE_STATE, still blocked' \
  'cd $VAR && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `cd dir1 dir2` (multiple args, ambiguous) && git push origin develop -> UNSAFE_STATE, still blocked' \
  'cd dir1 dir2 && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552: `cd *` (glob metachar in arg) && git push origin develop -> UNSAFE_STATE, still blocked' \
  'cd * && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-5: `cd <unresolvable-target>` (candidate common-dir resolution fails) -> UNSAFE_STATE, still blocked' \
  'cd /nonexistent-target && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo;MOCK_COMMON_DIR='

# ===========================================================================
# Issue #552 iter-7 (dual-verify round 5 — Codex source-inlined review,
# 2 real Critical regressions reproduced live by the coordinator; see the
# iter-7 fix-history comment in push-guard.sh for the full grammar
# rationale). Bash's simple-command grammar is `[assignment|redirection]*
# command-name [argument|redirection]*` — the wrapper-strip walkers
# already modeled the assignment half (`VAR=value` skip); iter-7 completes
# the redirection half. This also incidentally closes a PRE-EXISTING gap
# (present before Issue #552 ever shipped): `>/dev/null git push origin
# develop` in Mercury was never blocked because the walker required `git`
# at the very first token, and a leading redirect token stopped it cold.
# ===========================================================================

# Both coordinator-reproduced Critical cases (external cwd, cd genuinely
# lands in Mercury, redirect prefix previously hid the `cd`/`git` from the
# wrapper-strip walker entirely).
scenario \
  '#552 iter-7: `>/dev/null cd <mercury> && git push origin develop` (glued redirect prefix) -> tracked, still blocked' \
  '>/dev/null cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-7: `CDPATH=<mercury> cd <relative> && git push origin develop` (inline CDPATH token) -> UNSAFE_STATE, still blocked' \
  'CDPATH=__MERCURY_PATH__ cd .probe && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# Pre-existing gap (present before #552, closed incidentally by the same
# redirect-prefix fix): a leading redirect previously hid `git` itself
# from process_segment's own wrapper-strip walker.
scenario \
  '#552 iter-7: `>/dev/null git push origin develop` in Mercury (pre-existing gap, not #552-introduced) -> now blocked' \
  '>/dev/null git push origin develop' \
  2 'BLOCKED'

# Redirect-prefix operator-form coverage: glued target, separate target,
# fd-numbered, and chained (multiple redirects before the command name).
scenario \
  '#552 iter-7: `2>&1 cd <mercury> && git push origin develop` (fd-duplication operator, glued) -> tracked, still blocked' \
  '2>&1 cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-7: `> /dev/null cd <mercury> && git push origin develop` (separate-token target) -> tracked, still blocked' \
  '> /dev/null cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-7: `2> /dev/null git push origin develop` in Mercury (fd-numbered, separate target) -> blocked' \
  '2> /dev/null git push origin develop' \
  2 'BLOCKED'

scenario \
  '#552 iter-7: `>/dev/null 2>&1 cd <mercury> && git push origin develop` (chained redirects) -> tracked, still blocked' \
  '>/dev/null 2>&1 cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# CDPATH: inherited hook-process environment (no inline token) — a
# RELATIVE `cd` argument cannot be safely tracked when CDPATH is set
# (bash may search CDPATH's directories instead of the naive
# EFFECTIVE_CWD-join this hook otherwise assumes); an ABSOLUTE `cd`
# argument is unaffected (bash never consults CDPATH for it) and stays
# trackable.
scenario \
  '#552 iter-7: inherited $CDPATH (hook env, no inline token) + relative `cd` -> UNSAFE_STATE, still blocked' \
  'cd .probe && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo;CDPATH=/some/cdpath/entry'

scenario \
  '#552 iter-7: inherited $CDPATH (hook env) + ABSOLUTE `cd` -> unaffected, tracking still works, allowed' \
  'cd /other/repo && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo;CDPATH=/some/cdpath/entry'

# ── Regression net (Codex Medium — "verify these are not broken", the
# coordinator's explicit acceptance-list item): a redirect AFTER the real
# command name (not a prefix) must be handled exactly as before — this is
# NOT prefix territory, so the iter-7 skip logic must never touch it.
scenario \
  '#552 iter-7 regression: `git push origin master > log.txt` (trailing redirect, external repo) -> unaffected, still allowed' \
  'git push origin master > log.txt' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-7 regression: `git push origin develop > log.txt` (trailing redirect, Mercury, protected target) -> still blocked' \
  'git push origin develop > log.txt' \
  2 'BLOCKED'

# ===========================================================================
# Issue #552 iter-8 (dual-verify round 6 — Codex 3-Critical claim,
# coordinator cross-checked each independently: only `>|` was a real gap;
# `GIT_COMMON_DIR` alone does NOT redirect git's repo selection (verified
# twice, added defensively anyway at zero cost); implicit-push wrong-repo
# resolution is real but PRE-EXISTING (present before #552 ever shipped).
# Direction this round: reverse the prefix-token default from "unknown ==
# harmless" to "unknown == unsafe" instead of enumerating a 4th redirect
# operator; item1 additionally needed a root-cause awk tokenizer fix
# (`|` was unconditionally treated as a pipe/OR separator, fragmenting
# `>|/dev/null` into a bare `>` token + a fresh, metacharacter-free
# `/dev/null` segment that a pure default-deny check alone would NOT have
# caught — see the push-guard.sh iter-8 fix-history comment for detail).
# ===========================================================================

# item1: `>|` (force-overwrite redirect, closes even under `set -C`) was
# fragmented by the awk tokenizer's unconditional `|`-as-separator rule,
# hiding the `cd`/`git` pair from the wrapper-strip walker exactly like the
# iter-7 `&`-fragmentation bug did for `2>&1`. Fixed at the tokenizer level
# (not by enumerating `>|` in the redirect-prefix operator list).
scenario \
  '#552 iter-8 item1: `>|/dev/null cd <mercury> && git push origin develop` (force-overwrite redirect, awk `|`-fragmentation fix) -> tracked, still blocked' \
  '>|/dev/null cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# item1 regression net (coordinator's explicit acceptance-list ask): a tail
# redirect and a `$`-in-data-position construct must remain unaffected by
# both the awk `|`-fix and the default-deny reversal.
scenario \
  '#552 iter-8 regression: `git push origin master > log.txt` (external, tail redirect) -> unaffected, still allowed' \
  'git push origin master > log.txt' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-8 regression: `git commit -m "fix $foo" && git push origin master` (external, `$` in data position, not prefix) -> unaffected, still allowed' \
  'git commit -m "fix $foo" && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-8 regression: `cd docs && git push origin master` (external, plain relative cd) -> unaffected, still allowed' \
  'cd docs && git push origin master' \
  0 '' '' \
  'JSON_CWD=/other/repo'

# item (default-deny reversal, general case): an unrecognized token
# containing shell metacharacters in prefix position (neither a known safe
# prefix shape nor a plain command-name token) now falls back to
# Phase 1-3 instead of being silently skipped as "probably harmless".
scenario \
  '#552 iter-8: `<>` glued token (Codex claimed leak; coordinator verified this ALREADY blocked pre-iter-8 too) in prefix position -> tracked, still blocked' \
  '<> cd __MERCURY_PATH__ && git push origin develop' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# item2 (Phase 3 pre-existing bug, closed incidentally by #552's
# repo-awareness plumbing): implicit push (`git push`/`git push origin`,
# no refspec) must resolve the current branch of the ACTUAL target repo
# (Phase-0-resolved `-C`/GIT_DIR path), not the bare hook-process cwd.
# MOCK_CURRENT_BRANCH answers `-C`-qualified queries (the resolved Mercury
# target); MOCK_CURRENT_BRANCH_BARE answers bare queries (the hook
# process's own external cwd) — pinning them to different branches proves
# which one Phase 3 actually consults.
scenario \
  '#552 iter-8 item2: `git -C <mercury> push origin` (implicit, no refspec) from external hook cwd -> resolves Mercury target repo branch (develop), not bare hook cwd branch -> blocked' \
  'git -C __MERCURY_PATH__ push origin' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo;MOCK_CURRENT_BRANCH=develop;MOCK_CURRENT_BRANCH_BARE=feature/unrelated'

scenario \
  '#552 iter-8 item2 regression: `git -C <other> push origin` (implicit, hook cwd = Mercury, target repo = external on unprotected branch) -> allowed, not wrongly blocked by bare-cwd branch' \
  'git -C /other/repo push origin' \
  0 '' '' \
  'MOCK_CURRENT_BRANCH=feature/unrelated;MOCK_CURRENT_BRANCH_BARE=develop'

# item3: GIT_COMMON_DIR added to the unsafe-env-var set defensively (zero
# cost) even though real testing found it does NOT actually redirect git's
# repo selection on its own — see the push-guard.sh comment for the
# honest caveat. This scenario only proves the trigger fires, not that a
# real bypass existed.
scenario \
  '#552 iter-8 item3: `GIT_COMMON_DIR=<mercury>/.git git push origin master` (external, defensive trigger) -> UNSAFE_STATE, blocked' \
  'GIT_COMMON_DIR=__MERCURY_PATH__/.git git push origin master' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

# ===========================================================================
# Issue #552 iter-9 (dual-verify round 7 — coordinator caught iter-8's
# Phase 3 fix as only half-done): a cross-repo `cd` legitimately triggers
# UNSAFE_STATE (per the iter-5 same-repo-only-advancement invariant), so
# Phase 0 never runs and `_repo_path` stays empty. iter-8 fell back to the
# bare `git rev-parse --abbrev-ref HEAD` check in that case — the exact
# wrong-cwd resolution the fix exists to eliminate. iter-9: when
# `_repo_path` is empty at Phase 3, block unconditionally (fail-closed)
# instead of guessing via a known-wrong bare rev-parse.
# ===========================================================================

scenario \
  '#552 iter-9: `cd <mercury> && git push origin` (implicit, no refspec, cross-repo cd -> UNSAFE_STATE -> unresolvable target) -> fail-closed, now blocked' \
  'cd __MERCURY_PATH__ && git push origin' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-9 regression: `git -C <mercury> push origin` (implicit, iter-8 path, _repo_path resolves) -> still blocked via `-C` branch query, not the new fail-closed branch' \
  'git -C __MERCURY_PATH__ push origin' \
  2 'BLOCKED' '' \
  'JSON_CWD=/other/repo;MOCK_CURRENT_BRANCH=develop;MOCK_CURRENT_BRANCH_BARE=feature/unrelated'

scenario \
  '#552 iter-9 regression: external repo, no cross-repo cd, implicit `git push origin` on unprotected branch -> _repo_path resolves to external, Phase 0 returns early, Phase 3 never reached, still allowed' \
  'git push origin' \
  0 '' '' \
  'JSON_CWD=/other/repo'

scenario \
  '#552 iter-9 regression: external repo, same-repo `cd docs && git push origin` (tracked, same-repo advancement, not UNSAFE_STATE) -> _repo_path resolves to external, still allowed' \
  'cd docs && git push origin' \
  0 '' '' \
  'JSON_CWD=/other/repo'

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
