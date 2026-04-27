#!/usr/bin/env bash
# scripts/test-lane-auto-report.sh — smoke tests for lane-auto-report.sh (Issue #324, Phase C C1).
# Uses MERCURY_TEST_REPO_ROOT + MERCURY_TEST_NOTIFY_LOG to capture POST payloads
# without requiring a running router.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$SCRIPT_DIR/lane-auto-report.sh"

PASS=0
FAIL=0

pass() { echo "PASS: $1"; PASS=$(( PASS + 1 )); }
fail() { echo "FAIL: $1 — $2"; FAIL=$(( FAIL + 1 )); }

# ---------------------------------------------------------------------------
# Per-test helpers (each test gets its own clean workspace)
# ---------------------------------------------------------------------------
make_workspace() {
  local ws="/tmp/mercury-lane-report-test-$$-${RANDOM}"
  mkdir -p "$ws/.git" "$ws/.mercury/state"
  echo "$ws"
}

write_status() {
  # write_status <file> <name> <is_stale> [issue_numbers_csv] [branch_ref:ts,branch_ref:ts]
  local file="$1" name="$2" stale="$3" issues="${4:-}" branches="${5:-}"
  local issues_json="[]" branches_json="[]"
  if [ -n "$issues" ]; then
    issues_json=$(echo "$issues" | tr ',' '\n' | jq -R 'select(length>0) | tonumber | {number:., title:"t", updated_at:"2026-04-27T00:00:00Z"}' | jq -s '.')
  fi
  if [ -n "$branches" ]; then
    # Branch entries use '=' as ref/timestamp separator because ISO timestamps
    # contain colons; splitting on ':' garbles the ref. Format: ref=ts,ref=ts.
    branches_json="["
    local first=1
    IFS=',' read -ra parts <<< "$branches"
    for p in "${parts[@]}"; do
      local ref="${p%%=*}" ts="${p#*=}"
      [ "$first" = "0" ] && branches_json+=","
      branches_json+="{\"ref\":\"$ref\",\"last_commit_at\":\"$ts\"}"
      first=0
    done
    branches_json+="]"
  fi
  jq -n \
    --arg n "$name" --argjson stale "$stale" \
    --argjson iss "$issues_json" --argjson br "$branches_json" \
    '{last_checked_at:"2026-04-27T00:00:00Z",stale_threshold_minutes:15,
      lanes:[{name:$n,is_stale:$stale,issues:$iss,branches:$br}]}' > "$file"
}

write_status_multi() {
  # write_status_multi <file> <full_lanes_jq_expr>
  # The lanes argument is a jq DSL expression (unquoted keys allowed); we evaluate
  # it via `jq -n` to convert to canonical JSON before --argjson injection.
  local file="$1" lanes_expr="$2"
  local lanes_json
  lanes_json=$(jq -c -n "$lanes_expr")
  jq -n --argjson l "$lanes_json" \
    '{last_checked_at:"2026-04-27T00:00:00Z",stale_threshold_minutes:15,lanes:$l}' > "$file"
}

run_report() {
  # run_report <workspace> -> echoes notify log path
  local ws="$1"
  local log="$ws/.mercury/state/notify.log"
  : > "$log"
  MERCURY_TEST_REPO_ROOT="$ws" \
  MERCURY_TEST_NOTIFY_LOG="$log" \
    bash "$SCRIPT" >/dev/null 2>&1
  echo "$log"
}

count_log() { wc -l < "$1" | tr -d ' '; }
log_titles() { jq -r '.title' "$1" 2>/dev/null || true; }
log_labels() { jq -r '.label' "$1" 2>/dev/null || true; }

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# T1: missing current → exit 0, no log
{
  ws=$(make_workspace)
  log="$ws/.mercury/state/notify.log"; : > "$log"
  if MERCURY_TEST_REPO_ROOT="$ws" MERCURY_TEST_NOTIFY_LOG="$log" \
       bash "$SCRIPT" >/dev/null 2>&1 && [ "$(count_log "$log")" = "0" ]; then
    pass "T1 missing current → no-op"
  else
    fail "T1 missing current → no-op" "exit or log unexpected"
  fi
  rm -rf "$ws"
}

# T2: first run → seeds previous + no notifications
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.json" "main" false "300" "feature/lane-main/x=2026-04-27T00:00:00Z"
  log=$(run_report "$ws")
  if [ -f "$ws/.mercury/state/lane-status.previous.json" ] && [ "$(count_log "$log")" = "0" ]; then
    pass "T2 first run seeds previous + 0 emits"
  else
    fail "T2 first run" "previous missing or unexpected emits"
  fi
  rm -rf "$ws"
}

# T3: identical snapshots → 0 emits
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.json" "main" false "300" "feature/lane-main/x=2026-04-27T00:00:00Z"
  cp "$ws/.mercury/state/lane-status.json" "$ws/.mercury/state/lane-status.previous.json"
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "0" ]; then
    pass "T3 identical → 0 emits"
  else
    fail "T3 identical → 0 emits" "got $(count_log "$log")"
  fi
  rm -rf "$ws"
}

# T4: lane added → emit "spawned"
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "" ""
  write_status_multi "$ws/.mercury/state/lane-status.json" \
    '[{name:"main",is_stale:false,issues:[],branches:[]},{name:"side-x",is_stale:false,issues:[],branches:[]}]'
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "1" ] && log_titles "$log" | grep -q "side-x spawned"; then
    pass "T4 lane added → spawned notification"
  else
    fail "T4 lane added" "got $(count_log "$log") emits, titles=$(log_titles "$log")"
  fi
  rm -rf "$ws"
}

# T5: lane removed → emit "closed"
{
  ws=$(make_workspace)
  write_status_multi "$ws/.mercury/state/lane-status.previous.json" \
    '[{name:"main",is_stale:false,issues:[],branches:[]},{name:"side-x",is_stale:false,issues:[],branches:[]}]'
  write_status "$ws/.mercury/state/lane-status.json" "main" false "" ""
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "1" ] && log_titles "$log" | grep -q "side-x closed"; then
    pass "T5 lane removed → closed notification"
  else
    fail "T5 lane removed" "got $(count_log "$log") emits, titles=$(log_titles "$log")"
  fi
  rm -rf "$ws"
}

# T6: is_stale false→true emits "stale"
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "" ""
  write_status "$ws/.mercury/state/lane-status.json" "main" true "" ""
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "1" ] && log_titles "$log" | grep -q "main stale"; then
    pass "T6 is_stale flip false→true → stale notification"
  else
    fail "T6 is_stale flip" "got $(count_log "$log") emits, titles=$(log_titles "$log")"
  fi
  rm -rf "$ws"
}

# T7: is_stale true→false emits "active"
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" true "" ""
  write_status "$ws/.mercury/state/lane-status.json" "main" false "" ""
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "1" ] && log_titles "$log" | grep -q "main active"; then
    pass "T7 is_stale flip true→false → active notification"
  else
    fail "T7 is_stale flip back" "got $(count_log "$log") emits, titles=$(log_titles "$log")"
  fi
  rm -rf "$ws"
}

# T8: issue set change emits "issues changed"
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "300" ""
  write_status "$ws/.mercury/state/lane-status.json" "main" false "300,301" ""
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "1" ] && log_titles "$log" | grep -q "main issues changed"; then
    pass "T8 issue set change → notification"
  else
    fail "T8 issue change" "got $(count_log "$log") emits, titles=$(log_titles "$log")"
  fi
  rm -rf "$ws"
}

# T9: branch commit jump emits "commit"
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "" "feature/lane-main/x=2026-04-27T00:00:00Z"
  write_status "$ws/.mercury/state/lane-status.json"          "main" false "" "feature/lane-main/x=2026-04-27T01:00:00Z"
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "1" ] && log_titles "$log" | grep -q "main commit"; then
    pass "T9 branch commit jump → notification"
  else
    fail "T9 commit jump" "got $(count_log "$log") emits, titles=$(log_titles "$log")"
  fi
  rm -rf "$ws"
}

# T10: new branch added to existing lane emits "branch added"
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "" ""
  write_status "$ws/.mercury/state/lane-status.json"          "main" false "" "feature/lane-main/y=2026-04-27T01:00:00Z"
  log=$(run_report "$ws")
  if [ "$(count_log "$log")" = "1" ] && log_titles "$log" | grep -q "main branch added"; then
    pass "T10 new branch on existing lane → notification"
  else
    fail "T10 new branch" "got $(count_log "$log") emits, titles=$(log_titles "$log")"
  fi
  rm -rf "$ws"
}

# T11: QUIET mode → no log writes (but exit 0)
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "" ""
  write_status_multi "$ws/.mercury/state/lane-status.json" \
    '[{name:"main",is_stale:false,issues:[],branches:[]},{name:"side-x",is_stale:false,issues:[],branches:[]}]'
  log="$ws/.mercury/state/notify.log"; : > "$log"
  # QUIET=1 with no NOTIFY_LOG should not POST
  unset_log_run() {
    MERCURY_TEST_REPO_ROOT="$ws" \
    MERCURY_LANE_REPORT_QUIET=1 \
      bash "$SCRIPT" >/dev/null 2>&1
  }
  if unset_log_run && [ "$(count_log "$log")" = "0" ]; then
    pass "T11 QUIET mode → no notifications written"
  else
    fail "T11 QUIET mode" "exit or unexpected log writes"
  fi
  rm -rf "$ws"
}

# T12: previous promoted after diff
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "300" ""
  write_status "$ws/.mercury/state/lane-status.json"          "main" false "300,301" ""
  run_report "$ws" >/dev/null
  prev_issues=$(jq -r '.lanes[0].issues | length' "$ws/.mercury/state/lane-status.previous.json")
  if [ "$prev_issues" = "2" ]; then
    pass "T12 previous promoted to current after run"
  else
    fail "T12 previous promotion" "previous issue count = $prev_issues, expected 2"
  fi
  rm -rf "$ws"
}

# T13: combined transitions (lane add + is_stale flip on existing) emits 2 notifications
{
  ws=$(make_workspace)
  write_status_multi "$ws/.mercury/state/lane-status.previous.json" \
    '[{name:"main",is_stale:false,issues:[],branches:[]}]'
  write_status_multi "$ws/.mercury/state/lane-status.json" \
    '[{name:"main",is_stale:true,issues:[],branches:[]},{name:"side-x",is_stale:false,issues:[],branches:[]}]'
  log=$(run_report "$ws")
  added=$(log_titles "$log" | grep -c "side-x spawned" || true)
  staled=$(log_titles "$log" | grep -c "main stale" || true)
  if [ "$added" = "1" ] && [ "$staled" = "1" ]; then
    pass "T13 combined transitions → 2 notifications"
  else
    fail "T13 combined" "added=$added staled=$staled total=$(count_log "$log")"
  fi
  rm -rf "$ws"
}

# T15: lock contention — pre-existing lock dir → second invocation exits 0 without diffing
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "" ""
  write_status_multi "$ws/.mercury/state/lane-status.json" \
    '[{name:"main",is_stale:false,issues:[],branches:[]},{name:"side-x",is_stale:false,issues:[],branches:[]}]'
  # Pre-create the lockdir to simulate an in-flight first invocation
  mkdir "$ws/.mercury/state/lane-auto-report.lock.d"
  log="$ws/.mercury/state/notify.log"; : > "$log"
  set +e
  MERCURY_TEST_REPO_ROOT="$ws" MERCURY_TEST_NOTIFY_LOG="$log" \
    bash "$SCRIPT" >/dev/null 2>&1
  rc=$?
  set -e
  # Lock-skipped run must NOT promote previous (still has only main, not side-x)
  prev_lane_count=$(jq -r '.lanes | length' "$ws/.mercury/state/lane-status.previous.json")
  if [ "$rc" = "0" ] && [ "$(count_log "$log")" = "0" ] && [ "$prev_lane_count" = "1" ]; then
    pass "T15 lock contention → exit 0, no emits, previous untouched"
  else
    fail "T15 lock contention" "rc=$rc emits=$(count_log "$log") prev_lanes=$prev_lane_count"
  fi
  rmdir "$ws/.mercury/state/lane-auto-report.lock.d" 2>/dev/null || true
  rm -rf "$ws"
}

# T14: POST failure (missing token, no NOTIFY_LOG, QUIET off) → previous NOT promoted, exit 1
{
  ws=$(make_workspace)
  write_status "$ws/.mercury/state/lane-status.previous.json" "main" false "" ""
  write_status_multi "$ws/.mercury/state/lane-status.json" \
    '[{name:"main",is_stale:false,issues:[],branches:[]},{name:"side-x",is_stale:false,issues:[],branches:[]}]'
  # Capture pre-run previous to compare against
  pre_hash=$(jq -S '.' "$ws/.mercury/state/lane-status.previous.json" | sha1sum | awk '{print $1}')
  # Point token file at non-existent path so post_notify hits the read-error branch
  set +e
  MERCURY_TEST_REPO_ROOT="$ws" \
  MERCURY_TEST_TOKEN_FILE="$ws/no-such-token" \
    bash "$SCRIPT" >/dev/null 2>&1
  rc=$?
  set -e
  post_hash=$(jq -S '.' "$ws/.mercury/state/lane-status.previous.json" | sha1sum | awk '{print $1}')
  if [ "$rc" = "1" ] && [ "$pre_hash" = "$post_hash" ]; then
    pass "T14 POST failure → exit 1 + previous unchanged for retry"
  else
    fail "T14 POST failure" "rc=$rc pre=$pre_hash post=$post_hash"
  fi
  rm -rf "$ws"
}

# ---------------------------------------------------------------------------
echo
echo "================================="
echo "lane-auto-report tests: PASS=$PASS FAIL=$FAIL"
echo "================================="
[ "$FAIL" -eq 0 ]
