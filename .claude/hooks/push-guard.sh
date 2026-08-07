#!/usr/bin/env bash
# GATE: block direct push to protected branches (develop / master / main) —
# all merges must go through PRs. Runtime deps: bash, awk, grep, jq (jq is
# REQUIRED — Codex iter-2 Critical closed the legacy sed-fallback bypass;
# the hook hard-blocks if jq is absent).
#
# Fix history:
#   Session 1-side-bug / Issue #349 — Bug: greedy regex consumed body text.
#     The push-args extractor was `sed 's/.*git[[:space:]]\+push[[:space:]]*//'`
#     applied per-line. When `git commit -m "...heredoc body...with git push
#     hygiene for develop branch..." && git push origin lane/main/foo` ran,
#     sed stripped only the line containing the LAST `git push` per-line.
#     Body lines without `git push` survived intact; bash word-splitting on
#     PUSH_ARGS exposed standalone `develop`/`master` tokens from those body
#     lines, mistakenly blocking real push to a non-protected lane branch.
#
#     Iter 1 fix mirrored S82 #339 pattern from pr-merge-guard.sh: quote-
#     aware awk segment splitter + per-segment env-strip + bash token-array
#     slice from `push` token forward. Body content stays inside the
#     `git commit -m "..."` segment.
#
#     Iter 2 (dual-verify findings): the iter-1 fix retained 6 bypass holes
#     identified by parallel review (Claude code-reviewer + Codex audit):
#       - Quoted protected-branch token: `git push origin "develop"` rc=0
#         (NORMALIZED retained literal quote chars, missed PROTECTED grep)
#       - 6+ env wrapper chain: `env env env env env env git push origin
#         develop` rc=0 (5-pass env-strip cap exhausted)
#       - Subshell/group: `( git push origin develop )` and `{ git push
#         origin develop; }` rc=0 (awk did not tokenize parens / braces)
#       - Quoted env values: `A="1 2" git push origin develop` rc=0 (regex
#         env-strip + bash word-split mangled quoted value with whitespace)
#       - Quoted -C paths: `git -C "C:/repo with spaces" push origin
#         develop` rc=0 (same root cause — bash word-split blind to quotes)
#       - Break-on-first: `git push origin lane/foo && git push origin
#         develop` rc=0 (first segment matched, second never inspected)
#       - Codex Medium: parser failure (jq/awk absent) silently exited 0
#         instead of fail-closing the gate.
#
#     Iter 2 fix: replace the regex env-strip + bash word-split combo with a
#     single quote-aware awk tokenizer that emits one shell token per line
#     plus segment-end markers between separators. Bash reads the stream,
#     groups tokens per segment, and runs Phase 1/2/3 inline per segment
#     (no break-on-first). Wrapper-strip is now token-based and unbounded.
#     Quote-strip in Phase 2 normalization handles literal quote chars
#     defensively. `(` `)` `{` `}` are tokenized; `(` `{` are skipped during
#     wrapper-strip. Hard-block on awk failure or unrecoverable extraction.
#
#   Issue #552 — Bug: no repository awareness. This guard's protection
#     semantics (direct-push-to-protected-branch forbidden) are a Mercury-
#     repo-specific CLAUDE.md rule, but the hook fired on ANY repo's
#     `git push`, including a design-library repo whose established
#     workflow is master-direct-commit. Fix: Phase 0 repo-awareness check
#     inserted before Phase 1-3, per push segment — resolves the segment's
#     actual target repo (`-C <path>` if present, else the hook stdin
#     JSON's `cwd` field, confirmed present on all hook events per
#     https://code.claude.com/docs/en/hooks) and compares its git toplevel
#     against Mercury's toplevel. Non-Mercury repos are allowed through.
#     Any failure to resolve either toplevel falls through unchanged to
#     the existing fail-closed Phase 1-3 logic — this check can only ever
#     grant a POSITIVELY CONFIRMED skip, never a bypass from uncertainty.
#
#     Iter 2 (dual-verify NEEDS-CHANGES on both sides, real hook-call
#     reproductions, not theoretical) found 3 regressive bypasses in the
#     iter-1 Phase 0:
#       - Critical: wrong identity predicate. `--show-toplevel` returns the
#         PER-WORKTREE path; a Mercury dev-agent linked worktree (this
#         hook's own normal execution context, `.claude/worktrees/<id>/`)
#         has a toplevel that differs from the main checkout's, so a push
#         FROM a worktree was misclassified as "a different repo" and
#         allowed through — the worst possible direction of error. Fixed:
#         compare `--git-common-dir` instead (identical across all linked
#         worktrees of the same repo; verified via `git worktree list` +
#         `rev-parse` on this exact environment, see Phase 0 comment below).
#       - Critical: unmodelled repo selectors treated as harmless noise.
#         `--git-dir`/`--work-tree` (space or `=` form) were silently
#         skipped by the global-option walker without being recorded;
#         inline `GIT_DIR=`/`GIT_WORK_TREE=` env assignments were absorbed
#         by the generic `VAR=value` wrapper-strip case the same way;
#         repeated `-C` (git's real semantics: each accumulates RELATIVE TO
#         the previous, not "last wins") only kept the final occurrence.
#         Any of these let a command's ACTUAL target repo diverge from
#         what Phase 0 resolved, silently allowing a Mercury push through
#         under a resolved-but-wrong "different repo" verdict. Fixed: any
#         of these three signals sets UNRESOLVABLE_SELECTOR and Phase 0 is
#         skipped ENTIRELY for that segment (falls to Phase 1-3 unchanged)
#         — fail-closed, never a source of leniency.
#       - Medium: a relative `-C <path>` was resolved against the HOOK
#         PROCESS's own cwd, not against `HOOK_CWD` (where the actual
#         command executes) — base mismatch. Fixed: relative `-C` values
#         are joined onto `$HOOK_CWD`; if `HOOK_CWD` is unavailable to
#         anchor it, treated as unresolvable (falls under the same
#         fail-closed skip as above, not as "no -C at all").
#
#     Documented non-fix (Low, out of threat model, same tier as the
#     existing `bash -c "..."` recursive-shell limitation noted elsewhere
#     in this file): a `subst` drive letter, UNC path, junction, or
#     symlink can make the SAME physical repo resolve to two different
#     `--git-common-dir` strings, which would incorrectly allow a Mercury
#     push through. Requires deliberately constructing such an alias;
#     not addressed here.

INPUT=$(cat)

# Debug logging: opt-in via GUARD_DEBUG=1 to avoid persisting sensitive payloads.
_PROJECT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
STATE_DIR="$_PROJECT/.mercury/state"
mkdir -p "$STATE_DIR" 2>/dev/null
LOG_FILE="$STATE_DIR/push-guard-debug.log"

debug_log() {
  [ "${GUARD_DEBUG:-0}" = "1" ] && echo "[$(date -Iseconds)] $1" >> "$LOG_FILE"
}

# Issue #552 — path normalization for repo-awareness comparisons. Lowercase
# + backslash-to-slash + strip trailing slash so Windows drive-letter paths
# with differing case or separator style (e.g. `D:\Mercury\Mercury` vs
# `d:/mercury/mercury/`) compare equal.
_normalize_path() {
  local p="$1"
  p="${p,,}"
  p="${p//\\//}"
  p="${p%/}"
  printf '%s' "$p"
}

# Issue #552 iter-2 — resolve a directory's git COMMON dir (the shared
# `.git` across all linked worktrees of the same repo), NOT its toplevel.
# A Mercury dev-agent worktree (`.claude/worktrees/<id>/`, this hook's own
# normal execution context) has its own distinct toplevel but shares the
# SAME common-dir as the main checkout — comparing toplevel wrongly
# classified a worktree-originated push as "a different repo" (a real
# regression, reproduced with real hook calls: worktree toplevel
# `D:/Mercury/Mercury/.claude/worktrees/agent-.../`, main-checkout toplevel
# `D:/Mercury/Mercury` — different strings, same repo). Common-dir is
# identical for both: `D:/Mercury/Mercury/.git`.
#
# `--path-format=absolute` (forces the printed path to be absolute and
# canonical) requires git >= 2.31 — verified via the official upstream
# release notes: "git rev-parse can be explicitly told to give output as
# absolute or relative path with the --path-format=(absolute|relative)
# option" (https://github.com/git/git/blob/master/Documentation/RelNotes/2.31.0.adoc,
# 2026-08-08). `--git-common-dir` itself is unaffected by `--path-format`
# per the git-rev-parse manual page (https://git-scm.com/docs/git-rev-parse)
# and has existed since git's worktree feature (long predates 2.31), so it
# still works standalone on older git — but may then print a path relative
# to the queried directory rather than absolute. `_git_common_dir()` tries
# the modern absolute form first and falls back to resolving a relative
# result with `cd`.
_git_common_dir() {
  local dir="$1"
  local out
  out=$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -n "$out" ]; then
    printf '%s' "$out"
    return 0
  fi
  # --path-format unsupported (git < 2.31) or the combined call failed for
  # another reason — retry the plain (pre-2.31-compatible) form.
  out=$(git -C "$dir" rev-parse --git-common-dir 2>/dev/null)
  [ -n "$out" ] || return 1
  case "$out" in
    /*|[A-Za-z]:[\\/]*|\\\\*)
      # Already absolute (POSIX root, Windows drive-letter, or UNC).
      printf '%s' "$out" ;;
    *)
      # Relative result — resolve it against the queried directory.
      ( cd "$dir" 2>/dev/null && cd "$out" 2>/dev/null && pwd )
      ;;
  esac
}

# Issue #552 — Mercury repo's own git common-dir, resolved once. Used by
# the Phase 0 repo-awareness check in process_segment() to decide whether a
# `git push` segment targets THIS repo (protection applies) or a different
# one (out of scope, allowed through). Empty on resolution failure — Phase 0
# treats that as "cannot determine", falling through to the existing
# fail-closed Phase 1-3 logic unchanged.
MERCURY_COMMON_DIR=$(_git_common_dir "$_PROJECT")

if [ "${GUARD_DEBUG:-0}" = "1" ] && [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 102400 ]; then
  tail -100 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

debug_log "INPUT=$INPUT"

# jq availability is the gating discriminator — see Mercury security policy
# block below for why the sed fallback is unsafe. Test override:
# MERCURY_PUSH_GUARD_TEST_FORCE_NO_JQ=1 forces the no-jq branch even when jq
# is installed (used by scripts/test-push-guard.sh).
HAS_JQ=0
if [ "${MERCURY_PUSH_GUARD_TEST_FORCE_NO_JQ:-0}" != "1" ] && command -v jq >/dev/null 2>&1; then
  HAS_JQ=1
fi

# Detect whether INPUT carries a `command` key BEFORE extraction. Used as a
# fail-closed signal: if the key exists but COMMAND is empty after extraction,
# the parser failed and the hook MUST hard-block rather than fall through to
# exit 0 (Codex iter-2 Medium). Use a structured jq check when available so
# string content inside payload values containing the literal word `command`
# (e.g. a Write tool's content) does not falsely trigger the hard-block path
# on machines without jq (Argus iter-2 Medium #1 + Copilot Line 72).
HAS_CMD_KEY=0
if [ "$HAS_JQ" -eq 1 ]; then
  if printf '%s' "$INPUT" | jq -e '(.tool_input // null) | (type=="object") and has("command")' >/dev/null 2>&1; then
    HAS_CMD_KEY=1
  fi
elif printf '%s' "$INPUT" | grep -q '"command"[[:space:]]*:'; then
  # No-jq fallback — anchor on `"command":` to reduce string-content false
  # positives. Still imperfect (a value `"\"command\":..."` would match), but
  # the hard-block below makes this branch a fail-closed gate, not a bypass.
  HAS_CMD_KEY=1
fi

# Mercury security policy (Codex iter-2 Critical): jq is required for safe
# COMMAND extraction. The legacy sed fallback (`[^"]*` capture) truncates at
# any embedded `"`, so JSON like `"command":"git push origin \"develop\""`
# extracts as `git push origin \` — non-empty (so the empty-COMMAND check
# below does not fire), and the truncated tail bypasses the protected-branch
# grep. Hard-block instead.
if [ "$HAS_JQ" -eq 0 ] && [ "$HAS_CMD_KEY" -eq 1 ]; then
  # block_parser_fail() defined below — declare it inline to avoid forward-ref
  # noise. We could hoist, but the body is tiny and the second use site is the
  # primary one.
  printf 'BLOCKED: push-guard requires jq for safe command parsing; sed fallback cannot handle escaped quotes inside the command string.\n' >&2
  printf 'Install jq (https://jqlang.github.io/jq/) on PATH and retry.\n' >&2
  [ "${GUARD_DEBUG:-0}" = "1" ] && echo "[$(date -Iseconds)] BLOCKED (parser-fail): jq absent" >> "$LOG_FILE"
  exit 2
fi

if [ "$HAS_JQ" -eq 1 ]; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
  # Issue #552 — hook stdin JSON's top-level `cwd` field: "Current working
  # directory when the hook is invoked" (confirmed present on all hook
  # events, https://code.claude.com/docs/en/hooks). Fallback target-repo
  # signal for the Phase 0 repo-awareness check when the push segment has
  # no explicit `-C <path>`. Empty on missing/malformed field — Phase 0
  # then has nothing to resolve and falls through fail-closed.
  HOOK_CWD=$(echo "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
else
  # HAS_JQ=0 AND HAS_CMD_KEY=0 — INPUT did not carry a "command" key (e.g.
  # tool wasn't Bash). Nothing to gate.
  exit 0
fi

debug_log "COMMAND=$COMMAND"

# Helper: emit the standard BLOCKED message and exit
block_push() {
  local reason="$1"
  debug_log "BLOCKED: $reason"
  cat >&2 <<'MSG'
BLOCKED: Direct push to a protected branch (develop / master / main) is forbidden (CLAUDE.md rule).
All merges into develop must go through a Pull Request.
Use: git push -u origin <feature-branch> && gh pr create --base develop
MSG
  exit 2
}

# Helper: emit a parser-failure block and exit (fail-closed semantics).
block_parser_fail() {
  local reason="$1"
  debug_log "BLOCKED (parser-fail): $reason"
  printf 'BLOCKED: push-guard could not safely parse the command (%s).\n' "$reason" >&2
  printf 'Aborting to avoid fail-open. Verify the command manually or check that jq is installed.\n' >&2
  exit 2
}

# Fail-closed: COMMAND empty but the JSON did contain "command".
if [ -z "$COMMAND" ]; then
  if [ "$HAS_CMD_KEY" -eq 1 ]; then
    block_parser_fail "command extraction returned empty for non-empty input"
  fi
  # No command key at all → the bash invocation didn't supply one (e.g. tool
  # not Bash); nothing to gate.
  exit 0
fi

# ── Quote-aware awk tokenizer ──────────────────────────────────────────
# Emits a typed line-protocol stream:
#   `TOK\t<value>` — one shell-style token per line
#   `SEG`          — segment terminator (between `;`, `|`, `||`, `&`, `&&`,
#                    `(`, `)`, `{`, `}`, backtick, or end-of-line)
#
# The `TOK\t` prefix avoids the iter-1..5 token-collision bypass (Argus
# iter-2 Critical): a real shell token equal to the previous protocol
# sentinel `__SEGEND__` would have been mis-classified as a separator,
# splitting `git push origin __SEGEND__ develop` into two segments and
# letting the protected push slip past the walker. With the prefix, ANY
# token value (including the literal string `SEG`) is unambiguously a
# token, not a separator.
#
# Inside single or double quotes, separators are buffered as part of the
# current token and quote delimiters are stripped (mirrors bash word-
# expansion). Outside quotes, whitespace splits tokens; `(` `)` `{` `}`
# and backtick are statement-grouping / command-substitution boundaries
# (emitted as `SEG` so contents are walked as their own segment).
#
# This replaces the previous "regex env-strip + bash word-split" pipeline,
# which mangled quoted env values (e.g. `A="1 2"`) and quoted -C paths
# (e.g. `git -C "C:/repo with spaces"`). Token boundaries now match shell
# semantics for our limited subset (no command substitution recursion into
# quoted strings, no here-docs — same caveat as the sibling pr-merge-guard.sh).
_TOK_STREAM=$(printf '%s\n' "$COMMAND" | awk -v SQ="'" '
BEGIN { in_sq=0; in_dq=0; tok=""; in_tok=0; line_continue=0 }
function flush_tok() {
  if (in_tok) { print "TOK\t" tok; tok=""; in_tok=0 }
}
{
  line = $0
  n = length(line)
  for (i = 1; i <= n; i++) {
    c = substr(line, i, 1)
    if (in_sq) {
      if (c == SQ) { in_sq = 0; continue }
      tok = tok c
      continue
    }
    if (in_dq) {
      if (c == "\\") {
        # POSIX-ish: \" \\ \$ \` are escape sequences inside double quotes;
        # any other backslash is literal. Trailing `\` at end of line inside
        # dq is line-continuation (drop both \ and newline) — bash semantics.
        # Closes Copilot iter-2 line-199 finding: `git push origin "devel\
        # op"` parses as `git push origin "develop"` in bash, but iter-7 awk
        # appended literal `\` then space, leaving `devel\ op` token that
        # missed the PROTECTED grep.
        nc = (i < n) ? substr(line, i+1, 1) : ""
        if (nc == "\"" || nc == "\\" || nc == "$" || nc == "`") {
          tok = tok nc; i++; continue
        }
        if (nc == "") {
          line_continue = 1
          continue
        }
        tok = tok c
        continue
      }
      if (c == "\"") { in_dq = 0; continue }
      tok = tok c
      continue
    }
    # Outside any quotes
    # Backslash-escape: bash drops the backslash and treats the next char as
    # literal. So `de\velop` parses as `develop`. Without this, our literal
    # PROTECTED grep would miss obfuscated targets (Codex iter-2 High #1).
    if (c == "\\") {
      nc = (i < n) ? substr(line, i+1, 1) : ""
      if (nc != "") {
        tok = tok nc
        in_tok = 1
        i++
        continue
      }
      # Trailing backslash at end of line — bash treats this as line
      # continuation: drop the backslash AND the newline, joining current
      # token with next line. Set line_continue so the per-line tail block
      # neither flushes nor emits SEG (Codex iter-3 Medium).
      line_continue = 1
      continue
    }
    if (c == SQ)   { in_sq = 1; in_tok = 1; continue }
    if (c == "\"") { in_dq = 1; in_tok = 1; continue }
    if (c == ";")  { flush_tok(); print "SEG"; continue }
    if (c == "|") {
      nc = (i < n) ? substr(line, i+1, 1) : ""
      flush_tok(); print "SEG"
      if (nc == "|") i++
      continue
    }
    if (c == "&") {
      nc = (i < n) ? substr(line, i+1, 1) : ""
      flush_tok(); print "SEG"
      if (nc == "&") i++
      continue
    }
    # `(` `)` `{` `}` and backtick are statement-grouping / command-substitution
    # boundaries. Treat them as segment separators so the contents are walked
    # as their own segment(s). Closes Codex iter-4 High:
    #   - `( cmd )` subshell bodies
    #   - `{ cmd; }` brace-group bodies
    #   - `case x in x) cmd ;; esac` case-pattern bodies (`)` separator)
    #   - `echo $(git push origin develop)` top-level command-substitution
    #   - `` echo `git push origin develop` `` legacy backtick form
    # Limitation: backticks INSIDE double quotes are still buffered (would
    # need recursive parsing — rare in Claude Code output, accepted).
    if (c == "(" || c == ")" || c == "{" || c == "}" || c == "`") {
      flush_tok()
      print "SEG"
      continue
    }
    if (c == " " || c == "\t") {
      flush_tok()
      continue
    }
    tok = tok c
    in_tok = 1
  }
  # End of awk input line. Three cases by precedence:
  #   1. line_continue is set (trailing `\` at EOL, anywhere — outside quotes
  #      OR inside double quotes): drop the implicit newline entirely. The
  #      token continues onto the next line (bash line-continuation).
  #   2. Inside a quoted region without line_continue: preserve the line
  #      break as a literal space (bash semantics for unescaped newline
  #      inside quotes).
  #   3. Outside quotes: bare newline = statement separator. Closes Codex
  #      iter-3 High (`echo ok\ngit push origin develop` would stay one
  #      segment otherwise).
  if (line_continue) {
    line_continue = 0
  } else if (in_sq || in_dq) {
    tok = tok " "; in_tok = 1
  } else {
    flush_tok()
    print "SEG"
  }
}
END {
  flush_tok()
  print "SEG"
}
') || block_parser_fail "awk tokenizer exited non-zero"

# Group tokens into segments and run Phase 1/2/3 inline per segment.
# Per-segment phase evaluation closes the iter-1 break-on-first bypass:
#   `git push origin lane/foo && git push origin develop` evaluates BOTH.
PROTECTED='^(develop|master|main)$'

declare -a CUR_SEG=()

process_segment() {
  local -a tokens=( "$@" )
  local ntok=${#tokens[@]}
  local i=0

  # Issue #552 iter-2 — set to 1 the instant this segment carries a repo
  # selector Phase 0 cannot safely model (inline GIT_DIR=/GIT_WORK_TREE=,
  # --git-dir/--work-tree, or repeated -C). When set, Phase 0 is skipped
  # ENTIRELY below — this segment falls straight through to the existing
  # Phase 1-3 logic, exactly as if Issue #552 had never shipped. Never used
  # to grant a skip, only to withhold one.
  local UNRESOLVABLE_SELECTOR=0

  # Wrapper-strip: skip leading env-var assignments, command wrappers, and
  # subshell/group open chars. Loop until no transformation applies — no
  # iteration cap (closes the iter-1 6+ env wrapper bypass; transforms shrink
  # the prefix monotonically so termination is bounded by O(ntok)).
  local _seen_change=1
  while [ "$_seen_change" -eq 1 ] && [ "$i" -lt "$ntok" ]; do
    _seen_change=0
    local tok="${tokens[$i]}"
    case "$tok" in
      # Bash logical-NOT (`! cmd` runs cmd, inverts exit code). `(` `{` are
      # NOT in this list — iter-5 promotes them to segment separators (awk
      # tokenizer emits SEG on those chars), so they never reach the walker
      # as tokens. Closes Codex iter-2 High #2 + iter-4 case/function/
      # subshell bypasses uniformly.
      "!")
        i=$((i + 1)); _seen_change=1 ;;
      # Bash control-flow reserved words that can prefix a command in a guarded
      # segment. After the segment splitter cuts on `;`, a segment like
      # `if cond; then git push origin develop; fi` produces three segments;
      # segment 2 starts with `then`, which without this case would stop the
      # walker before reaching `git`. Closes Codex iter-3 High. Block-closer
      # words (`fi`/`done`/`esac`) are intentionally absent — they don't
      # precede a command, but if they did the walker would still exit cleanly.
      if|then|else|elif|do|while|until)
        i=$((i + 1)); _seen_change=1 ;;
      # Sudo/doas added per Copilot iter-1 finding (line 300): `sudo git push
      # origin develop` previously bypassed because `sudo` led the segment.
      # Documented limitation: `bash -c "git push origin develop"` is NOT
      # caught — the inner -c arg would need recursive shell parsing. Rare
      # in Claude Code output.
      env|command|exec|builtin|nohup|time|sudo|doas)
        i=$((i + 1)); _seen_change=1 ;;
      # `coproc` and `function` may take an optional/required NAME after the
      # keyword. Skip the keyword, then peek: if the next token is a plain
      # identifier (not `git`, not a flag), skip it too. Disambiguation rule:
      # NEVER skip a literal `git` token — that defeats the walker. The body
      # of `function f { ... }` is segregated into its own segment by the
      # `{` separator, so we only need to handle the declaration prefix here.
      # Closes Codex iter-4 High (`coproc git push ...` + `function f {...}`).
      coproc|function)
        i=$((i + 1)); _seen_change=1
        if [ "$i" -lt "$ntok" ]; then
          local _next="${tokens[$i]}"
          case "$_next" in
            git) ;;  # don't consume, walker needs to match
            [A-Za-z_][A-Za-z0-9_-]*) i=$((i + 1)) ;;
          esac
        fi
        ;;
      # env value-taking flags (separate-arg form): skip flag + value.
      # Long forms `--unset`/`--split-string`/`--chdir` are also separate-arg
      # value-taking (mirrored from env(1)).
      -u|-S|-C|--unset|--split-string|--chdir)
        i=$((i + 2)); _seen_change=1 ;;
      # Long-form flag with inline value (`--foo=bar`) — single token.
      --*=*)
        i=$((i + 1)); _seen_change=1 ;;
      # Generic flag (long-form `--foo` or short-form `-X[Y...]`) — assume
      # flag-only. Closes the iter-2 `env --ignore-environment git push ...`
      # bypass (Codex High #2): the previous pattern `--|-[A-Za-z]*` did NOT
      # match `--ignore-environment` because `[A-Za-z]` does not match `-`.
      --|--*|-[A-Za-z]*)
        i=$((i + 1)); _seen_change=1 ;;
      # VAR=value assignment (now quote-aware via awk tokenization).
      # Issue #552 iter-2 (Codex): `GIT_DIR=`/`GIT_WORK_TREE=` inline env
      # assignments — whether as a direct prefix (`GIT_DIR=x git push ...`)
      # or via `env` (`env GIT_DIR=x git push ...`, since `env` itself is
      # already stripped by the wrapper case above, leaving this same
      # `VAR=value` token next) — silently redirect which repo the
      # subsequent `git` invocation targets. Flag as unresolvable rather
      # than let Phase 0 resolve against the wrong (unmodified) directory.
      [A-Za-z_]*=*)
        case "$tok" in
          GIT_DIR=*|GIT_WORK_TREE=*) UNRESOLVABLE_SELECTOR=1 ;;
        esac
        i=$((i + 1)); _seen_change=1 ;;
    esac
  done

  [ "$i" -lt "$ntok" ] || return 0
  [ "${tokens[$i]}" = "git" ] || return 0
  i=$((i + 1))

  # Walk git global options until `push` or non-option non-push token.
  # GIT_C_PATH captures a `-C <path>` global option value (if present) —
  # used by the Phase 0 repo-awareness check below (Issue #552) since `-C`
  # overrides the process cwd for that git invocation and is therefore the
  # authoritative signal for which repo this segment's push targets.
  local push_idx=-1
  local GIT_C_PATH=""
  local GIT_C_COUNT=0
  while [ "$i" -lt "$ntok" ]; do
    local tok="${tokens[$i]}"
    case "$tok" in
      push) push_idx=$i; break ;;
      -C)
        # Issue #552 iter-2 (Codex): git's real multi -C semantics is
        # cumulative-relative (`git -C a -C b` == `-C a/b`, each new -C
        # resolved relative to the previous one's result), not "last
        # value wins". Modeling that correctly needs a full relative-path
        # join chain; instead we just count occurrences and flag >1 as
        # unresolvable below (GIT_C_PATH is still captured for the common
        # single-`-C` case, but ignored whenever GIT_C_COUNT>1).
        GIT_C_PATH="${tokens[$((i + 1))]:-}"
        GIT_C_COUNT=$((GIT_C_COUNT + 1))
        i=$((i + 2)); continue ;;
      --git-dir|--work-tree)
        # Issue #552 iter-2 (Codex): these redirect which repo/worktree
        # git operates on independently of cwd/-C — Phase 0 cannot safely
        # resolve the true target without replicating git's own selector
        # precedence rules. Flag as unresolvable; still skip flag+value so
        # the rest of the walker (Phase 1-3 fallback) is unaffected.
        UNRESOLVABLE_SELECTOR=1
        i=$((i + 2)); continue ;;
      -c|--namespace|--super-prefix)
        # value-taking global option: skip flag + value in one step
        i=$((i + 2)); continue ;;
      --git-dir=*|--work-tree=*)
        UNRESOLVABLE_SELECTOR=1
        i=$((i + 1)); continue ;;
      --namespace=*|--super-prefix=*)
        # inlined value: skip flag only
        i=$((i + 1)); continue ;;
      --help|--version|-h|-v|-p|--paginate|-P|--no-pager|--bare|--no-replace-objects|--literal-pathspecs|--no-optional-locks)
        i=$((i + 1)); continue ;;
      -*)
        # Unknown global option — assume flag-only (best-effort).
        i=$((i + 1)); continue ;;
      *)
        break ;;  # Non-option non-push token → not a push invocation
    esac
  done

  [ "$push_idx" -ge 0 ] || return 0

  [ "$GIT_C_COUNT" -gt 1 ] && UNRESOLVABLE_SELECTOR=1

  # ── Phase 0: repo-awareness (Issue #552) ──────────────────────────────
  # Skipped entirely (falls straight to Phase 1-3, as if #552 never
  # shipped) whenever this segment carries a repo selector we cannot
  # safely model — see UNRESOLVABLE_SELECTOR comment above.
  if [ "$UNRESOLVABLE_SELECTOR" -eq 0 ]; then
    # Resolve which repo this segment's `git push` actually targets:
    # `-C <path>` (authoritative — overrides cwd for this invocation) else
    # the hook stdin JSON's `cwd` field. A relative `-C` value is resolved
    # against `HOOK_CWD` (where the command actually executes), NOT this
    # hook process's own cwd — those can differ. If `-C` is relative and
    # `HOOK_CWD` is unavailable to anchor it, there is nothing safe to
    # resolve, so `_repo_path` stays empty and Phase 0 falls through below
    # exactly like any other resolution failure.
    local _repo_path=""
    if [ -n "$GIT_C_PATH" ]; then
      case "$GIT_C_PATH" in
        /*|[A-Za-z]:[\\/]*|\\\\*)
          # Already absolute (POSIX root, Windows drive-letter, or UNC).
          _repo_path="$GIT_C_PATH" ;;
        *)
          [ -n "$HOOK_CWD" ] && _repo_path="$HOOK_CWD/$GIT_C_PATH" ;;
      esac
    else
      _repo_path="$HOOK_CWD"
    fi

    # If that resolves to a git common-dir that differs from Mercury's own,
    # this push is out of this guard's scope — allow it through. Any
    # failure to resolve (rev-parse errors, empty signal, MERCURY_COMMON_DIR
    # itself unresolved) falls through unchanged to Phase 1-3 below —
    # fail-closed, never a bypass source.
    if [ -n "$_repo_path" ] && [ -n "$MERCURY_COMMON_DIR" ]; then
      local _target_common_dir
      _target_common_dir=$(_git_common_dir "$_repo_path")
      if [ -n "$_target_common_dir" ] && [ "$(_normalize_path "$_target_common_dir")" != "$(_normalize_path "$MERCURY_COMMON_DIR")" ]; then
        debug_log "ALLOWED (non-Mercury repo): target='$_target_common_dir' mercury='$MERCURY_COMMON_DIR'"
        return 0
      fi
    fi
  fi

  # Slice push tokens (after the `push` token itself).
  local -a push_toks=()
  local j=$((push_idx + 1))
  while [ "$j" -lt "$ntok" ]; do
    push_toks+=( "${tokens[$j]}" )
    j=$((j + 1))
  done

  # ── Phase 1: dangerous flags ──
  local t
  for t in "${push_toks[@]}"; do
    case "$t" in
      --all|--mirror) block_push "--all or --mirror flag detected" ;;
    esac
  done

  # ── Phase 2: explicit refspec walk ──
  local SKIPPED_REMOTE=false
  local HAS_EXPLICIT_TARGET=false
  for t in "${push_toks[@]}"; do
    case "$t" in --*|-*) continue ;; esac
    if [ "$SKIPPED_REMOTE" = false ]; then
      # First non-flag arg is USUALLY the remote name, but `git push REFSPEC`
      # (no remote) is also valid bash/git syntax. Git treats an arg as a
      # refspec rather than a remote when:
      #   - it contains `:` (LHS:RHS form, e.g. `HEAD:develop`)
      #   - it starts with `+` (force-push prefix, e.g. `+develop`)
      #   - it starts with `refs/` (fully-qualified ref, e.g. `refs/heads/develop`)
      # Without this heuristic, those forms slip past Phase 2 because
      # SKIPPED_REMOTE eats the refspec as "remote", leaving no target seen.
      # Trade-off: a remote literally named `refs/heads/develop` would now
      # produce a false-positive block, but such remote names are virtually
      # nonexistent (git's remote-name conventions are alphanumeric only).
      # Closes Argus iter-1 Minor "可能绕过" finding (#352).
      case "$t" in
        *:*|+*|refs/*)
          # Looks like a refspec — fall through to target processing below.
          ;;
        *)
          SKIPPED_REMOTE=true
          continue
          ;;
      esac
    fi
    HAS_EXPLICIT_TARGET=true

    # Defensive quote-strip on the push target. Awk strips outer quotes
    # during tokenization, but mid-token quote chars or repeated escaping
    # could leak. Mirrors the sibling pr-merge-guard.sh pattern
    # (REPO_FLAG / PR_SELECTOR strip lines 255-256, 299-300).
    local NORMALIZED="$t"
    NORMALIZED="${NORMALIZED%\"}"; NORMALIZED="${NORMALIZED#\"}"
    NORMALIZED="${NORMALIZED%\'}"; NORMALIZED="${NORMALIZED#\'}"
    # Strip leading `+` (force-push prefix)
    NORMALIZED="${NORMALIZED##+}"

    if printf '%s' "$NORMALIZED" | grep -q ':'; then
      local REFSPEC_TARGET="${NORMALIZED##*:}"
      REFSPEC_TARGET="${REFSPEC_TARGET#refs/heads/}"
      REFSPEC_TARGET="${REFSPEC_TARGET%\"}"; REFSPEC_TARGET="${REFSPEC_TARGET#\"}"
      REFSPEC_TARGET="${REFSPEC_TARGET%\'}"; REFSPEC_TARGET="${REFSPEC_TARGET#\'}"
      if printf '%s' "$REFSPEC_TARGET" | grep -qE "$PROTECTED"; then
        block_push "refspec target '$REFSPEC_TARGET' from '$t'"
      fi
      continue
    fi

    NORMALIZED="${NORMALIZED#refs/heads/}"
    if printf '%s' "$NORMALIZED" | grep -qE "$PROTECTED"; then
      block_push "direct target '$NORMALIZED' from '$t'"
    fi
  done

  # ── Phase 3: implicit push from current branch (no explicit refspec) ──
  if [ "$HAS_EXPLICIT_TARGET" = false ]; then
    local CURRENT_BRANCH
    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
    if printf '%s' "$CURRENT_BRANCH" | grep -qE "$PROTECTED"; then
      block_push "implicit push from current branch '$CURRENT_BRANCH'"
    fi
  fi
}

# Read the awk-emitted typed line-protocol stream, group tokens into segments,
# and process. Lines beginning with `TOK<TAB>` are tokens (with the TAB-and-
# everything-before stripped); the bare line `SEG` is a segment terminator.
# Any other line shape is a protocol violation — ignored to fail closed.
TAB=$(printf '\t')
TOK_PREFIX="TOK${TAB}"
while IFS= read -r line; do
  if [ "$line" = "SEG" ]; then
    if [ "${#CUR_SEG[@]}" -gt 0 ]; then
      process_segment "${CUR_SEG[@]}"
      CUR_SEG=()
    fi
  else
    case "$line" in
      "${TOK_PREFIX}"*)
        # Strip the TOK\t prefix; the remainder (which may itself equal
        # the literal string `SEG`) is the token value.
        CUR_SEG+=( "${line#${TOK_PREFIX}}" )
        ;;
      *)
        # Unrecognized line shape — defensive ignore. The awk tokenizer is
        # the sole producer; this branch only fires under tampering.
        ;;
    esac
  fi
done <<EOF
$_TOK_STREAM
EOF

# Defensive flush: awk's END block always emits a trailing SEG, but guard
# the case where the stream was empty or truncated.
if [ "${#CUR_SEG[@]}" -gt 0 ]; then
  process_segment "${CUR_SEG[@]}"
fi

exit 0
