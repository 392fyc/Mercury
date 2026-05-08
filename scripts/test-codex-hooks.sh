#!/usr/bin/env bash
# Tests for .codex/hooks.json — verifies Mercury hook scripts under .claude/hooks/
# behave correctly when invoked with Codex-shaped stdin payloads.
#
# Codex stdin schema (per https://developers.openai.com/codex/hooks):
#   common: session_id, transcript_path, cwd, hook_event_name, model
#   turn-scoped (PreToolUse/PostToolUse/PermissionRequest/UserPromptSubmit/Stop):
#     adds turn_id
#   PreToolUse/PermissionRequest/PostToolUse: tool_name, tool_use_id, tool_input
#   PostToolUse: tool_response
#   UserPromptSubmit: prompt
#   Stop: stop_hook_active, last_assistant_message
#
# Mercury hook scripts read tool_input via jq from stdin — same shape as Claude
# Code hooks. This test verifies stdin-shape compatibility + exit code semantics
# (0 = pass, 2 = block, others = error).
#
# Run: bash scripts/test-codex-hooks.sh
#
# CONDITIONAL: Codex CLI Windows shell invocation (cmd / bash / direct) is not
# documented (per ADR Q1/Q11). This test invokes hooks via `bash` directly,
# matching the .codex/hooks.json `command` field shape; it does NOT test how
# Codex itself spawns the command. Empirical verification of Codex-side
# invocation requires a live Codex session — see ADR §5 Phase 2 step 8.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_DIR="$REPO_ROOT/.claude/hooks"
HOOK_CONFIG="$REPO_ROOT/.codex/hooks.json"

[[ -f "$HOOK_CONFIG" ]] || { printf 'codex hook config not found: %s\n' "$HOOK_CONFIG" >&2; exit 1; }
[[ -d "$HOOKS_DIR" ]]   || { printf 'hook scripts dir not found: %s\n' "$HOOKS_DIR" >&2; exit 1; }

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
    printf '  FAIL %s -- missing [%s] in head [%s]\n' "$label" "$needle" "${haystack:0:200}"
  fi
}

# Synthesize a Codex-shaped PreToolUse stdin payload for the Bash tool.
codex_pretool_bash() {
  local cmd="$1"
  cat <<EOF
{
  "session_id": "test-session-001",
  "transcript_path": null,
  "cwd": "$REPO_ROOT",
  "hook_event_name": "PreToolUse",
  "model": "gpt-5.4",
  "turn_id": "test-turn-001",
  "tool_name": "Bash",
  "tool_use_id": "tool-001",
  "tool_input": {"command": $(printf '%s' "$cmd" | jq -Rs .)}
}
EOF
}

# Synthesize PreToolUse payload for Codex apply_patch — real shape per
# https://developers.openai.com/codex/hooks + V4A patch format from
# https://developers.openai.com/api/docs/guides/tools-apply-patch:
# tool_input.command carries the patch heredoc with
# "*** (Update|Add|Delete) File: <path>" markers and optional
# "*** Move to: <path>" rename targets.
codex_pretool_apply_patch() {
  local file_path="$1"
  local op="${2:-Update}"
  local patch
  patch="*** Begin Patch
*** $op File: $file_path
@@ context
-old line
+new line
*** End Patch"
  cat <<EOF
{
  "session_id": "test-session-001",
  "transcript_path": null,
  "cwd": "$REPO_ROOT",
  "hook_event_name": "PreToolUse",
  "model": "gpt-5.4",
  "turn_id": "test-turn-001",
  "tool_name": "apply_patch",
  "tool_use_id": "tool-002",
  "tool_input": {"command": $(printf '%s' "$patch" | jq -Rs .)}
}
EOF
}

# Synthesize Codex apply_patch payload that uses *** Move to: rename target.
codex_pretool_apply_patch_move() {
  local src="$1"
  local dst="$2"
  local patch
  patch="*** Begin Patch
*** Update File: $src
*** Move to: $dst
@@ context
-old line
+new line
*** End Patch"
  cat <<EOF
{
  "session_id": "test-session-001",
  "transcript_path": null,
  "cwd": "$REPO_ROOT",
  "hook_event_name": "PreToolUse",
  "model": "gpt-5.4",
  "turn_id": "test-turn-001",
  "tool_name": "apply_patch",
  "tool_use_id": "tool-004",
  "tool_input": {"command": $(printf '%s' "$patch" | jq -Rs .)}
}
EOF
}

# Synthesize Claude Code-shaped PreToolUse for Edit/Write — tool_input.file_path.
claude_pretool_edit() {
  local file_path="$1"
  cat <<EOF
{
  "session_id": "test-session-001",
  "transcript_path": null,
  "cwd": "$REPO_ROOT",
  "hook_event_name": "PreToolUse",
  "model": "gpt-5.4",
  "turn_id": "test-turn-001",
  "tool_name": "Edit",
  "tool_use_id": "tool-003",
  "tool_input": {"file_path": "$file_path"}
}
EOF
}

# Synthesize UserPromptSubmit payload.
codex_user_prompt() {
  local prompt="$1"
  cat <<EOF
{
  "session_id": "test-session-001",
  "transcript_path": null,
  "cwd": "$REPO_ROOT",
  "hook_event_name": "UserPromptSubmit",
  "model": "gpt-5.4",
  "turn_id": "test-turn-001",
  "prompt": $(printf '%s' "$prompt" | jq -Rs .)
}
EOF
}

# ---- Test cases ----

printf '\n=== push-guard.sh — Codex PreToolUse Bash ===\n'

# Case 1: non-push command should pass (exit 0)
out=$(codex_pretool_bash 'git status' | bash "$HOOKS_DIR/push-guard.sh" 2>&1)
rc=$?
assert_eq "push-guard allows non-push" "0" "$rc"

# Case 2: push to lane branch should pass
out=$(codex_pretool_bash 'git push origin lane/side-bug/357-codex-hooks' | bash "$HOOKS_DIR/push-guard.sh" 2>&1)
rc=$?
assert_eq "push-guard allows lane branch" "0" "$rc"

# Case 3: push to develop must block (exit 2)
out=$(codex_pretool_bash 'git push origin develop' | bash "$HOOKS_DIR/push-guard.sh" 2>&1)
rc=$?
assert_eq "push-guard blocks develop" "2" "$rc"

# Case 4: push to master must block (exit 2)
out=$(codex_pretool_bash 'git push origin master' | bash "$HOOKS_DIR/push-guard.sh" 2>&1)
rc=$?
assert_eq "push-guard blocks master" "2" "$rc"

printf '\n=== scope-guard.sh — Codex PreToolUse apply_patch ===\n'

# Case 5: apply_patch on permitted path (Codex shape — tool_input.command) should pass
out=$(codex_pretool_apply_patch "$REPO_ROOT/.codex/hooks.json" "Update" | bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
rc=$?
assert_eq "scope-guard allows .codex/hooks.json (Codex apply_patch)" "0" "$rc"

# Case 5b: apply_patch targeting C:\Program Files MUST block (exit 2)
# Prior to Issue #357 fix, scope-guard parsed only tool_input.file_path; on
# Codex apply_patch it saw an empty path and silently exited 0 (fail-open).
out=$(codex_pretool_apply_patch 'C:/Program Files/Foo/bar.exe' "Add" | bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
rc=$?
assert_eq "scope-guard blocks C:/Program Files (Codex apply_patch)" "2" "$rc"

# Case 5c: apply_patch with multi-file patch — first violating path must block
patch="*** Begin Patch
*** Update File: $REPO_ROOT/.codex/hooks.json
@@ context
+ok
*** Add File: C:/Program Files/Evil/installer.exe
@@ context
+bad
*** End Patch"
payload=$(cat <<EOF
{"session_id":"t","transcript_path":null,"cwd":"$REPO_ROOT","hook_event_name":"PreToolUse","model":"gpt-5.4","turn_id":"t1","tool_name":"apply_patch","tool_use_id":"t","tool_input":{"command":$(printf '%s' "$patch" | jq -Rs .)}}
EOF
)
out=$(printf '%s' "$payload" | bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
rc=$?
assert_eq "scope-guard blocks multi-file patch with C:/Program Files entry" "2" "$rc"

# Case 5d: Claude Code shape (tool_input.file_path) on permitted path — backward compat
out=$(claude_pretool_edit "$REPO_ROOT/.codex/hooks.json" | bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
rc=$?
assert_eq "scope-guard allows .codex/hooks.json (Claude Edit shape)" "0" "$rc"

# Case 5e: Claude Code shape on C:/Program Files MUST block — backward compat
out=$(claude_pretool_edit 'C:/Program Files/Foo/bar.exe' | bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
rc=$?
assert_eq "scope-guard blocks C:/Program Files (Claude Edit shape)" "2" "$rc"

# Case 5f: Codex *** Move to: rename target on a blocked path MUST block.
# Iter 2 audit found that the previous regex only matched Update/Add/Delete
# and missed the V4A "Move to:" rename directive — a patch could update an
# allowed file then move it to C:/Program Files/... and bypass the gate.
out=$(codex_pretool_apply_patch_move "$REPO_ROOT/.codex/hooks.json" 'C:/Program Files/Evil/installer.exe' | bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
rc=$?
assert_eq "scope-guard blocks Move-to: C:/Program Files (V4A rename)" "2" "$rc"

# Case 5g/5h — jq-absent fallback paths. Strategy: find jq's directory and
# construct a PATH that excludes ONLY that directory (preserving every other
# bin location, so bash itself, grep, sed, printf still resolve). On git-bash
# this works because jq is colocated with other bins in /usr/bin but `env -i`
# breaks shell init, so we keep the parent env intact and only prune jq.
#
# Skip the jq-absent tests if jq cannot be located (no real way to verify).
JQ_BIN="$(command -v jq 2>/dev/null)"
if [ -n "$JQ_BIN" ]; then
  JQ_DIR="$(dirname "$JQ_BIN")"
  # Build a PATH that drops $JQ_DIR. Use awk to filter colon-separated entries.
  PATH_NO_JQ=$(printf '%s' "$PATH" | awk -v RS=: -v jqd="$JQ_DIR" 'NR>1{printf ":"} $0!=jqd{printf "%s",$0}')
  # Sanity check — jq must NOT be on the trimmed PATH.
  if ! PATH="$PATH_NO_JQ" command -v jq >/dev/null 2>&1; then
    # Case 5g: Codex apply_patch with jq absent MUST fail closed (V4A signature
    # detection in raw JSON triggers exit 2 — iter 2 audit fix).
    payload_no_jq=$(codex_pretool_apply_patch 'C:/Program Files/Foo/bar.exe' "Add")
    out=$(printf '%s' "$payload_no_jq" | PATH="$PATH_NO_JQ" bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
    rc=$?
    assert_eq "scope-guard fails closed when jq absent + Codex command shape" "2" "$rc"

    # Case 5h: Claude Code file_path shape with jq absent — sed fallback still
    # blocks Program Files paths (this path was already working before iter 2).
    payload_claude=$(claude_pretool_edit 'C:/Program Files/Foo/bar.exe')
    out=$(printf '%s' "$payload_claude" | PATH="$PATH_NO_JQ" bash "$HOOKS_DIR/scope-guard.sh" 2>&1)
    rc=$?
    assert_eq "scope-guard sed-fallback blocks Program Files (Claude shape, no jq)" "2" "$rc"
  else
    printf '  skip jq-absent tests — could not prune jq from PATH (other jq on PATH?)\n'
  fi
else
  printf '  skip jq-absent tests — jq not on PATH for harness builder\n'
fi

printf '\n=== session-init.sh — Codex UserPromptSubmit ===\n'

# Case 6: UserPromptSubmit shape should be accepted (exit 0, side-effect: writes state file)
out=$(codex_user_prompt 'hello' | bash "$HOOKS_DIR/session-init.sh" 2>&1)
rc=$?
assert_eq "session-init accepts Codex stdin" "0" "$rc"

printf '\n=== stop-guard.sh — Codex Stop event ===\n'

# Case 7: Stop event shape should be accepted
out=$(printf '{"session_id":"test","transcript_path":null,"cwd":"%s","hook_event_name":"Stop","model":"gpt-5.4","turn_id":"t1","stop_hook_active":false,"last_assistant_message":null}' "$REPO_ROOT" | bash "$HOOKS_DIR/stop-guard.sh" 2>&1)
rc=$?
[[ "$rc" -eq 0 || "$rc" -eq 2 ]]
assert_eq "stop-guard accepts Codex stdin" "0" "$?"

printf '\n=== Codex hooks.json schema validation ===\n'

# Case 8: hooks.json must be valid JSON
jq empty "$HOOK_CONFIG" 2>/dev/null
assert_eq "hooks.json is valid JSON" "0" "$?"

# Case 9: hooks.json must declare 5 events (PreToolUse, PostToolUse, UserPromptSubmit, Stop)
events=$(jq -r '.hooks | keys[]' "$HOOK_CONFIG" 2>/dev/null | sort | tr '\n' ',')
assert_contains "hooks.json has PreToolUse" "PreToolUse" "$events"
assert_contains "hooks.json has PostToolUse" "PostToolUse" "$events"
assert_contains "hooks.json has UserPromptSubmit" "UserPromptSubmit" "$events"
assert_contains "hooks.json has Stop" "Stop" "$events"

# Case 10: every command must reference a script under .claude/hooks/ or adapters/
missing=0
while IFS= read -r cmd; do
  # Extract referenced script path from "bash \"...\"" or "node \"...\"" form
  script=$(printf '%s' "$cmd" | sed -nE 's@.*"\$\(git rev-parse --show-toplevel\)/([^"]+)".*@\1@p')
  [[ -z "$script" ]] && continue
  if [[ ! -f "$REPO_ROOT/$script" ]]; then
    printf '  MISSING script referenced: %s\n' "$script" >&2
    missing=$((missing + 1))
  fi
done < <(jq -r '.. | objects | select(has("command")) | .command' "$HOOK_CONFIG" 2>/dev/null)
assert_eq "all hooks.json scripts exist on disk" "0" "$missing"

# Case 11: timeouts must be within Codex bounds (1-3600s) — sane values
bad_timeout=0
while IFS= read -r t; do
  [[ -z "$t" || "$t" == "null" ]] && continue
  # Strip whitespace; verify integer; range-check via arithmetic context.
  t="${t//[[:space:]]/}"
  if ! [[ "$t" =~ ^[0-9]+$ ]] || (( t < 1 || t > 3600 )); then
    printf '  bad timeout: %s\n' "$t" >&2
    bad_timeout=$((bad_timeout + 1))
  fi
done < <(jq -r '.. | objects | select(has("timeout")) | .timeout' "$HOOK_CONFIG" 2>/dev/null)
assert_eq "all timeouts within sane bounds" "0" "$bad_timeout"

# ---- Summary ----

printf '\n=== Summary ===\n'
printf 'pass: %d\nfail: %d\n' "$PASS" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  printf '\nfailures:\n'
  for f in "${FAILURES[@]}"; do
    printf '  - %s\n' "$f"
  done
  exit 1
fi
exit 0
