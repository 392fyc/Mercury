#!/usr/bin/env bash
# Tests for scripts/intel-judge.sh — drive the LLM-judgment + guarded
# issue-create slice WITHOUT a real API key or live GitHub:
#   * --llm-response-file injects a mock Anthropic response (bypasses curl) to
#     cover parse / significance-gate / whitelist / issue-create / degrade paths.
#   * A fake `curl` on PATH exercises the retry→degrade path (HTTP 5xx/4xx).
#   * A fake `gh` on PATH captures the exact argv + body-file of issue-create so
#     the injection + label-whitelist guarantees can be asserted.
#
# Run: bash scripts/test-intel-judge.sh

set -u  # not -e: each scenario inspects rc explicitly

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
JUDGE="$SCRIPT_DIR/intel-judge.sh"
[[ -x "$JUDGE" ]] || { printf 'intel-judge.sh not executable: %s\n' "$JUDGE" >&2; exit 1; }
# Active probe (not `command -v`): a present-but-unrunnable jq (e.g. a winget shim
# without exec permission on MSYS) would otherwise fan out into dozens of
# misleading scenario failures. Skip cleanly instead.
jq -n null >/dev/null 2>&1 || { printf 'SKIP-ALL: jq not runnable\n'; exit 0; }

PASS=0; FAIL=0
declare -a FAILURES=()
assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    PASS=$((PASS + 1)); printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1)); FAILURES+=("$label: expected [$expected], got [$actual]")
    printf '  FAIL %s -- expected [%s], got [%s]\n' "$label" "$expected" "$actual"
  fi
}
assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    PASS=$((PASS + 1)); printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1)); FAILURES+=("$label: missing [$needle]")
    printf '  FAIL %s -- missing [%s]\n' "$label" "$needle"
  fi
}
assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    PASS=$((PASS + 1)); printf '  ok %s\n' "$label"
  else
    FAIL=$((FAIL + 1)); FAILURES+=("$label: unexpected [$needle]")
    printf '  FAIL %s -- unexpectedly present [%s]\n' "$label" "$needle"
  fi
}

WORK_DIR="$(mktemp -d -t intel-judge-test.XXXXXX)" || { printf 'mktemp failed\n' >&2; exit 1; }
trap '[[ -n "${WORK_DIR:-}" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"' EXIT

run_judge() { bash "$JUDGE" "$@"; }

# ── helpers: build a Slice-1-shaped delta report + a mock Anthropic response ──
# Fixture writers hard-fail the suite on a jq error (rather than silently
# producing an empty/garbage fixture that fans out into misleading assertions).
fixture_fail() { printf 'FIXTURE-FAIL: %s\n' "$1" >&2; exit 1; }
write_deltas() {
  # $1 = dest, $2 = label (default intel/sdk)
  local dest="$1" label="${2:-intel/sdk}"
  jq -n --arg label "$label" '{
    checked_at:"2026-05-26T00:00:00Z",
    deltas:[{source:"npm:@anthropic-ai/claude-code", kind:"npm", label:$label, old:"2.1.150", new:"2.2.0"}],
    seeded:[], unchanged:[], errors:[]}' > "$dest" || fixture_fail "write_deltas $dest"
}
write_no_deltas() {
  jq -n '{checked_at:"2026-05-26T00:00:00Z", deltas:[], seeded:["x"], unchanged:[], errors:[]}' > "$1" \
    || fixture_fail "write_no_deltas $1"
}
mk_judgment() { # $1=significant(bool) $2=priority $3=impact $4=summary $5=action
  jq -nc --argjson sig "$1" --arg p "$2" --arg i "$3" --arg s "$4" --arg a "$5" \
    '{significant:$sig, priority:$p, impact:$i, summary:$s, suggested_action:$a}'
}
mk_resp() { # $1 = inner text (judgment JSON, or anything) → Anthropic response shape
  jq -n --arg t "$1" '{id:"msg_x", type:"message", role:"assistant",
    content:[{type:"text", text:$t}], stop_reason:"end_turn"}'
}

DELTAS="$WORK_DIR/deltas.json";     write_deltas "$DELTAS"
NODELTAS="$WORK_DIR/nodeltas.json"; write_no_deltas "$NODELTAS"

# ── Scenario 1: no delta → exit 0, nothing judged ──
printf '\n[1] no delta → exit 0, nothing to judge\n'
out="$(run_judge --deltas-file "$NODELTAS" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_contains "says nothing to judge" "nothing to judge" "$out"

# ── Scenario 2: significant P2 → dry-run, 1 Issue preview, whitelist labels ──
printf '\n[2] significant P2 → dry-run preview with whitelist labels\n'
R2="$WORK_DIR/r2.json"; mk_resp "$(mk_judgment true P2 capability "major bump" "review changelog")" > "$R2"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R2" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_contains "would create 1 Issue" "would create 1 Issue" "$out"
assert_contains "source label" "--label intel/sdk" "$out"
assert_contains "priority label" "--label P2" "$out"
assert_contains "impact label" "--label impact/capability" "$out"
assert_contains "triage label" "--label intel/needs-triage" "$out"
assert_contains "title is deterministic" "[intel] delta needs triage:" "$out"

# ── Scenario 3: significant P1 → gate passes too ──
printf '\n[3] significant P1 → gate passes\n'
R3="$WORK_DIR/r3.json"; mk_resp "$(mk_judgment true P1 blocking "breaking change" "pin version")" > "$R3"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R3" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_contains "would create Issue" "would create 1 Issue" "$out"
assert_contains "P1 label" "--label P1" "$out"

# ── Scenario 4: not significant → digest, no Issue ──
printf '\n[4] not significant → digest, no Issue\n'
DIG4="$WORK_DIR/digest4.md"
R4="$WORK_DIR/r4.json"; mk_resp "$(mk_judgment false P3 maintenance "routine patch" "none")" > "$R4"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R4" --digest-file "$DIG4" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_not_contains "no Issue created" "would create 1 Issue" "$out"
assert_contains "degraded to digest" "digest" "$out"
assert_contains "digest file has delta" "npm:@anthropic-ai/claude-code" "$(cat "$DIG4")"

# ── Scenario 5: significant but P3 → below gate (≥P2) → digest, no Issue ──
printf '\n[5] significant + P3 → below significance gate → digest\n'
DIG5="$WORK_DIR/digest5.md"
R5="$WORK_DIR/r5.json"; mk_resp "$(mk_judgment true P3 fyi "minor" "fyi")" > "$R5"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R5" --digest-file "$DIG5" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_not_contains "no Issue (P3 gated)" "would create 1 Issue" "$out"
assert_contains "names the gate" "below-significance-gate" "$(cat "$DIG5")"

# ── Scenario 6: off-whitelist priority → degrade to digest (label-injection guard) ──
printf '\n[6] off-whitelist priority → degrade\n'
DIG6="$WORK_DIR/digest6.md"
R6="$WORK_DIR/r6.json"; mk_resp "$(mk_judgment true P9 capability "x" "y")" > "$R6"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R6" --digest-file "$DIG6" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_not_contains "no Issue on bad priority" "would create 1 Issue" "$out"
assert_contains "digest notes off-whitelist priority" "priority-off-whitelist" "$(cat "$DIG6")"

# ── Scenario 7: off-whitelist impact → degrade ──
printf '\n[7] off-whitelist impact → degrade\n'
DIG7="$WORK_DIR/digest7.md"
R7="$WORK_DIR/r7.json"; mk_resp "$(mk_judgment true P2 doomsday "x" "y")" > "$R7"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R7" --digest-file "$DIG7" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_contains "digest notes off-whitelist impact" "impact-off-whitelist" "$(cat "$DIG7")"

# ── Scenario 8: malformed (non-JSON) LLM text → degrade, never crash ──
printf '\n[8] malformed LLM text → degrade to digest\n'
DIG8="$WORK_DIR/digest8.md"
R8="$WORK_DIR/r8.json"; mk_resp "I cannot answer in JSON, sorry." > "$R8"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R8" --digest-file "$DIG8" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_contains "digest notes unparseable" "unparseable" "$(cat "$DIG8")"

# ── Scenario 9: markdown-fenced JSON → tolerated, parses OK ──
printf '\n[9] ```json-fenced response → parses\n'
R9="$WORK_DIR/r9.json"
fenced="$(printf '```json\n%s\n```' "$(mk_judgment true P2 capability "fenced" "act")")"
mk_resp "$fenced" > "$R9"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R9" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_contains "fenced JSON still creates Issue" "would create 1 Issue" "$out"

# ── Scenario 10: INJECTION GUARD — shell metachars in summary stay inert text ──
printf '\n[10] injection guard: shell metachars in summary are inert\n'
SENTINEL="$WORK_DIR/PWNED"
PAYLOAD='harmless then $('"touch $SENTINEL"') `'"touch $SENTINEL"'` ; rm -rf / #'
R10="$WORK_DIR/r10.json"; mk_resp "$(mk_judgment true P2 capability "$PAYLOAD" "also $(printf '%s' '`id`')")" > "$R10"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R10" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
[[ -e "$SENTINEL" ]] && { FAIL=$((FAIL+1)); FAILURES+=("[10] injection executed: sentinel created"); printf '  FAIL injection executed\n'; } \
  || { PASS=$((PASS+1)); printf '  ok no injection (sentinel absent)\n'; }
assert_contains "payload preserved as literal text" 'rm -rf' "$out"

# ── Scenario 11: off-whitelist source label in delta → refuse (exit 2) ──
printf '\n[11] off-whitelist source label → exit 2\n'
BADL="$WORK_DIR/bad-label.json"; write_deltas "$BADL" "intel/evil"
R11="$WORK_DIR/r11.json"; mk_resp "$(mk_judgment true P2 capability "x" "y")" > "$R11"
out="$(run_judge --deltas-file "$BADL" --llm-response-file "$R11" 2>&1)"; rc=$?
assert_eq "exit 2 on bad label" "2" "$rc"
assert_contains "names non-whitelisted label" "non-whitelisted label" "$out"

# ── Scenario 12: --max-issues 0 → cap forces digest even when significant ──
printf '\n[12] --max-issues 0 → digest instead of Issue\n'
DIG12="$WORK_DIR/digest12.md"
R12="$WORK_DIR/r12.json"; mk_resp "$(mk_judgment true P1 blocking "x" "y")" > "$R12"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R12" --digest-file "$DIG12" --max-issues 0 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_not_contains "cap blocks Issue" "would create 1 Issue" "$out"
assert_contains "digest notes cap" "max-issues-cap-zero" "$(cat "$DIG12")"

# ── Scenario 13: degrade with NO --digest-file → exit 3 (delta did not land) ──
printf '\n[13] degrade with no digest-file → exit 3\n'
R13="$WORK_DIR/r13.json"; mk_resp "$(mk_judgment false P3 maintenance "x" "y")" > "$R13"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R13" 2>&1)"; rc=$?
assert_eq "exit 3 when delta cannot land" "3" "$rc"

# ── Scenario 14: usage errors ──
printf '\n[14] usage errors → exit 2\n'
out="$(run_judge 2>&1)"; rc=$?
assert_eq "missing --deltas-file → exit 2" "2" "$rc"
assert_contains "explains requirement" "--deltas-file is required" "$out"
out="$(run_judge --deltas-file "$WORK_DIR/nope.json" 2>&1)"; rc=$?
assert_eq "missing file → exit 2" "2" "$rc"
echo "{ not json" > "$WORK_DIR/badjson.json"
out="$(run_judge --deltas-file "$WORK_DIR/badjson.json" 2>&1)"; rc=$?
assert_eq "bad deltas JSON → exit 2" "2" "$rc"
out="$(run_judge --deltas-file "$DELTAS" --max-issues abc 2>&1)"; rc=$?
assert_eq "non-integer --max-issues → exit 2" "2" "$rc"

# ── Scenario 19: multi-object / multi-text-block response → reject (degrade) ──
# parse_judgment slurps, so two objects (length>1) must NOT validate as one.
printf '\n[19] multi-text-block response → degrade, no Issue\n'
DIG19="$WORK_DIR/digest19.md"
R19="$WORK_DIR/r19.json"
jq -n --arg t1 "$(mk_judgment true P1 blocking "first" "act1")" \
      --arg t2 "$(mk_judgment false P3 fyi "second" "act2")" \
   '{content:[{type:"text",text:$t1},{type:"text",text:$t2}]}' > "$R19"
out="$(run_judge --deltas-file "$DELTAS" --llm-response-file "$R19" --digest-file "$DIG19" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_not_contains "no Issue from multi-object" "would create 1 Issue" "$out"
assert_contains "multi-object degrades as unparseable" "unparseable" "$(cat "$DIG19")"

# ── Scenario 20: wrong-typed .deltas (not an array) → exit 2 ──
printf '\n[20] non-array .deltas field → exit 2\n'
echo '{"checked_at":"t","deltas":"not-an-array"}' > "$WORK_DIR/badtype.json"
out="$(run_judge --deltas-file "$WORK_DIR/badtype.json" 2>&1)"; rc=$?
assert_eq "exit 2 on non-array deltas" "2" "$rc"
assert_contains "names the contract" "must be a JSON array" "$out"

# ── Scenarios 15-16: fake `gh` on PATH → real issue-create argv/body capture.
#    Probe first; skip cleanly if the shim is not picked up (platform PATH quirk). ──
GHBIN="$WORK_DIR/ghbin"; mkdir -p "$GHBIN"
cat > "$GHBIN/gh" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "--version" ]]; then echo "gh 2.0.0 (stub)"; exit 0; fi   # startup probe
if [[ "$1" == "issue" && "$2" == "create" ]]; then
  printf '%s\0' "$@" > "$GH_ARGV_LOG"          # NUL-delimited argv (exact capture)
  while [[ $# -gt 0 ]]; do
    if [[ "$1" == "--body-file" ]]; then cp "$2" "$GH_BODY_COPY"; fi
    shift
  done
  echo "https://github.com/owner/repo/issues/42"
  exit 0
fi
echo "ghstub: unsupported: $*" >&2; exit 1
STUB
chmod +x "$GHBIN/gh"
GH_STUB_OK=0
if [[ "$(GH_ARGV_LOG=/dev/null PATH="$GHBIN:$PATH" gh issue create --title t 2>/dev/null)" == https://github.com/* ]]; then
  GH_STUB_OK=1
fi

if [[ "$GH_STUB_OK" -eq 1 ]]; then
  printf '\n[15] --create-issue: gh receives whitelist labels + --body-file (no inline body)\n'
  R15="$WORK_DIR/r15.json"; mk_resp "$(mk_judgment true P2 capability "create me" "do x")" > "$R15"
  GH_ARGV_LOG="$WORK_DIR/argv15"; GH_BODY_COPY="$WORK_DIR/body15.md"
  out="$(GH_ARGV_LOG="$GH_ARGV_LOG" GH_BODY_COPY="$GH_BODY_COPY" PATH="$GHBIN:$PATH" \
        bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R15" --create-issue --repo owner/repo 2>&1)"; rc=$?
  assert_eq "exit 0" "0" "$rc"
  assert_contains "reports creation" "created 1 Issue" "$out"
  argv="$(tr '\0' ' ' < "$GH_ARGV_LOG")"
  assert_contains "argv has issue create" "issue create" "$argv"
  assert_contains "argv passes --body-file" "--body-file" "$argv"
  assert_contains "argv has source label" "--label intel/sdk" "$argv"
  assert_contains "argv has priority label" "--label P2" "$argv"
  assert_contains "argv has triage label" "--label intel/needs-triage" "$argv"
  assert_contains "argv has repo" "--repo owner/repo" "$argv"
  assert_not_contains "no inline --body flag" "--body " "$argv"
  assert_contains "body file carries summary" "create me" "$(cat "$GH_BODY_COPY")"

  printf '\n[16] --create-issue: gh failure degrades to digest (delta preserved)\n'
  cat > "$GHBIN/gh" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "--version" ]]; then echo "gh 2.0.0 (stub)"; exit 0; fi   # startup probe
echo "simulated gh failure" >&2; exit 1
STUB
  chmod +x "$GHBIN/gh"
  DIG16="$WORK_DIR/digest16.md"
  R16="$WORK_DIR/r16.json"; mk_resp "$(mk_judgment true P2 capability "x" "y")" > "$R16"
  out="$(PATH="$GHBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R16" \
        --create-issue --digest-file "$DIG16" 2>&1)"; rc=$?
  assert_eq "exit 0 (degraded, delta saved)" "0" "$rc"
  assert_contains "digest notes gh failure" "gh-issue-create-failed" "$(cat "$DIG16")"
else
  printf '\n[15-16] SKIP: gh stub not picked up on PATH (platform shim)\n'
fi

# ── Scenarios 17-18: fake `curl` on PATH → retry→degrade path (no real API).
#    The stub mimics curl -w by printing "<body>\n<http_code>". ──
CURLBIN="$WORK_DIR/curlbin"; mkdir -p "$CURLBIN"
CURL_CALLS="$WORK_DIR/curl_calls"
cat > "$CURLBIN/curl" <<'STUB'
#!/usr/bin/env bash
# The startup probe (`curl --version`) must NOT count as an API attempt.
if [[ "$1" == "--version" ]]; then echo "curl 8.0.0 (stub)"; exit 0; fi
printf 'x\n' >> "$CURL_CALLS"
# Capture argv (NUL-delimited) so a test can assert the API key never appears.
[[ -n "${CURL_ARGV_LOG:-}" ]] && printf '%s\0' "$@" >> "$CURL_ARGV_LOG"
printf '%s\n%s' "$CURL_STUB_BODY" "$CURL_STUB_CODE"
exit 0
STUB
chmod +x "$CURLBIN/curl"
CURL_STUB_OK=0
probe="$(CURL_STUB_BODY='{}' CURL_STUB_CODE=503 CURL_CALLS=/dev/null PATH="$CURLBIN:$PATH" curl -sS x 2>/dev/null)"
[[ "$probe" == *503* ]] && CURL_STUB_OK=1

if [[ "$CURL_STUB_OK" -eq 1 ]]; then
  printf '\n[17] API 5xx exhausts retries → degrade to digest (delta preserved)\n'
  : > "$CURL_CALLS"
  DIG17="$WORK_DIR/digest17.md"
  out="$(ANTHROPIC_API_KEY="fake-key" CURL_STUB_BODY='{"error":"overloaded"}' CURL_STUB_CODE=503 \
        CURL_CALLS="$CURL_CALLS" INTEL_JUDGE_RETRY_BASE_DELAY=0 INTEL_JUDGE_MAX_RETRIES=3 \
        INTEL_ANTHROPIC_API_URL="https://api.anthropic.com/v1/messages" \
        PATH="$CURLBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --digest-file "$DIG17" 2>&1)"; rc=$?
  assert_eq "exit 0 (degraded)" "0" "$rc"
  assert_eq "retried exactly MAX_RETRIES=3 times" "3" "$(wc -l < "$CURL_CALLS" | tr -d ' ')"
  assert_contains "digest notes API unavailable" "anthropic-api-unavailable" "$(cat "$DIG17")"

  printf '\n[18] API 400 (client error) → no retry, degrade once\n'
  : > "$CURL_CALLS"
  DIG18="$WORK_DIR/digest18.md"
  out="$(ANTHROPIC_API_KEY="fake-key" CURL_STUB_BODY='{"error":"bad request"}' CURL_STUB_CODE=400 \
        CURL_CALLS="$CURL_CALLS" INTEL_JUDGE_RETRY_BASE_DELAY=0 INTEL_JUDGE_MAX_RETRIES=3 \
        INTEL_ANTHROPIC_API_URL="https://api.anthropic.com/v1/messages" \
        PATH="$CURLBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --digest-file "$DIG18" 2>&1)"; rc=$?
  assert_eq "exit 0 (degraded)" "0" "$rc"
  assert_eq "4xx not retried (1 call only)" "1" "$(wc -l < "$CURL_CALLS" | tr -d ' ')"

  printf '\n[21] API 429 (rate limit) → IS retried (like 5xx) then degrade\n'
  : > "$CURL_CALLS"
  DIG21="$WORK_DIR/digest21.md"
  out="$(ANTHROPIC_API_KEY="fake-key" CURL_STUB_BODY='{"error":"rate_limited"}' CURL_STUB_CODE=429 \
        CURL_CALLS="$CURL_CALLS" INTEL_JUDGE_RETRY_BASE_DELAY=0 INTEL_JUDGE_MAX_RETRIES=3 \
        INTEL_ANTHROPIC_API_URL="https://api.anthropic.com/v1/messages" \
        PATH="$CURLBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --digest-file "$DIG21" 2>&1)"; rc=$?
  assert_eq "exit 0 (degraded)" "0" "$rc"
  assert_eq "429 retried exactly MAX_RETRIES=3 times" "3" "$(wc -l < "$CURL_CALLS" | tr -d ' ')"
  assert_contains "digest notes API unavailable" "anthropic-api-unavailable" "$(cat "$DIG21")"

  printf '\n[22] secret guard: API key never appears in curl argv (-H @file)\n'
  : > "$CURL_CALLS"
  ARGV22="$WORK_DIR/curl_argv22"; : > "$ARGV22"
  DIG22="$WORK_DIR/digest22.md"
  # 200 with a non-significant judgment → exercises the full live curl path then
  # degrades to digest (no Issue). The key value is a sentinel we grep argv for.
  out="$(ANTHROPIC_API_KEY="sk-secret-sentinel-DO-NOT-LEAK" \
        CURL_STUB_BODY="$(mk_resp "$(mk_judgment false P3 maintenance "x" "y")")" CURL_STUB_CODE=200 \
        CURL_CALLS="$CURL_CALLS" CURL_ARGV_LOG="$ARGV22" INTEL_JUDGE_RETRY_BASE_DELAY=0 \
        PATH="$CURLBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --digest-file "$DIG22" 2>&1)"; rc=$?
  assert_eq "exit 0 (degraded, non-significant)" "0" "$rc"
  argv22="$(tr '\0' ' ' < "$ARGV22")"
  assert_not_contains "key value absent from curl argv" "sk-secret-sentinel-DO-NOT-LEAK" "$argv22"
  assert_not_contains "x-api-key header absent from curl argv" "x-api-key" "$argv22"
  assert_contains "curl reads headers from @file" "-H @" "$argv22"
else
  printf '\n[17-18,21-22] SKIP: curl stub not picked up on PATH (platform shim)\n'
fi

# ── Scenarios 23-25: startup security guards (no curl/gh stub needed) ──
printf '\n[23] SSRF guard: non-official API URL in live mode → exit 2\n'
out="$(INTEL_ANTHROPIC_API_URL="https://evil.example/v1/messages" \
      bash "$JUDGE" --deltas-file "$DELTAS" 2>&1)"; rc=$?
assert_eq "exit 2 on custom live URL" "2" "$rc"
assert_contains "names SSRF guard" "SSRF guard" "$out"

printf '\n[24] non-integer numeric env → exit 2 (startup, not mid-run)\n'
out="$(INTEL_JUDGE_MAX_RETRIES="abc" bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R2" 2>&1)"; rc=$?
assert_eq "exit 2 on bad MAX_RETRIES" "2" "$rc"
assert_contains "names the var" "MAX_RETRIES" "$out"

printf '\n[25] context-file outside the working tree → exit 2 (data-exfil guard)\n'
OUTCTX="$(mktemp "${TMPDIR:-/tmp}/intel-outside-tree.XXXXXX")"; printf 'ctx\n' > "$OUTCTX"
out="$(bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R2" --context-file "$OUTCTX" 2>&1)"; rc=$?
assert_eq "exit 2 on out-of-tree context" "2" "$rc"
assert_contains "names the working-tree guard" "working tree" "$out"
rm -f "$OUTCTX"

# ── Scenarios 26-29 + 31-33: cross-run open-Issue dedup (--dedup, ADR §5.2 layer 2).
#    (Scenario 30, defined after this block, covers the unrelated --context-file
#    boundary.) Fresh gh stub answers `issue list --json body` with the JSON array in
#    $GH_DEDUP_EXISTING and records whether `issue create` was invoked. ──
R26="$WORK_DIR/r26.json"; mk_resp "$(mk_judgment true P2 capability "dedup" "act")" > "$R26"

printf '\n[26] --dedup without --repo → exit 2 (config error, before any API call)\n'
out="$(bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R26" --create-issue --dedup 2>&1)"; rc=$?
assert_eq "exit 2" "2" "$rc"
assert_contains "names --dedup/--repo requirement" "--dedup requires --repo" "$out"

DDBIN="$WORK_DIR/ddbin"; mkdir -p "$DDBIN"
cat > "$DDBIN/gh" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "--version" ]]; then echo "gh 2.0.0 (stub)"; exit 0; fi
if [[ "$1" == "issue" && "$2" == "list" ]]; then
  if [[ "${GH_LIST_FAIL:-0}" == "1" ]]; then echo "simulated gh list failure" >&2; exit 1; fi
  # Emulate `gh issue list --json body`: print the configured JSON array (default []).
  printf '%s\n' "${GH_DEDUP_EXISTING:-[]}"; exit 0
fi
if [[ "$1" == "issue" && "$2" == "create" ]]; then
  : > "$GH_CREATE_CALLED"   # record that create was invoked
  while [[ $# -gt 0 ]]; do [[ "$1" == "--body-file" ]] && cp "$2" "$GH_BODY_COPY"; shift; done
  echo "https://github.com/owner/repo/issues/77"; exit 0
fi
echo "ddstub: unsupported: $*" >&2; exit 1
STUB
chmod +x "$DDBIN/gh"

if [[ "$GH_STUB_OK" -eq 1 ]]; then
  printf '\n[27] --dedup: no matching open Issue → creates + body carries dedup marker\n'
  CR27="$WORK_DIR/created27"; rm -f "$CR27"; BODY27="$WORK_DIR/body27.md"
  out="$(GH_DEDUP_EXISTING='[{"body":"unrelated open issue without any key"}]' GH_CREATE_CALLED="$CR27" GH_BODY_COPY="$BODY27" \
        PATH="$DDBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R26" \
        --create-issue --dedup --repo owner/repo 2>&1)"; rc=$?
  assert_eq "exit 0" "0" "$rc"
  assert_eq "create WAS called" "yes" "$([[ -f "$CR27" ]] && echo yes || echo no)"
  assert_contains "body carries dedup marker" "intel-dedup-key:" "$(cat "$BODY27")"
  DKEY="$(grep -oE 'intel-dedup-key: [0-9a-f]+' "$BODY27" | head -n1 | awk '{print $2}')"

  printf '\n[28] --dedup: open Issue already embeds same key → skip create (treated as landed)\n'
  CR28="$WORK_DIR/created28"; rm -f "$CR28"
  EXISTING_BODY="prior open triage issue
<!-- intel-dedup-key: $DKEY -->"
  EXISTING_JSON="$(jq -nc --arg b "$EXISTING_BODY" '[{body:$b}]')" || fixture_fail "existing_json 28"
  out="$(GH_DEDUP_EXISTING="$EXISTING_JSON" GH_CREATE_CALLED="$CR28" GH_BODY_COPY="$WORK_DIR/body28.md" \
        PATH="$DDBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R26" \
        --create-issue --dedup --repo owner/repo 2>&1)"; rc=$?
  assert_eq "exit 0 (skipped, landed)" "0" "$rc"
  assert_contains "reports dedup skip" "skipping create" "$out"
  assert_eq "create NOT called" "no" "$([[ -f "$CR28" ]] && echo yes || echo no)"

  printf '\n[29] --dedup: gh issue list failure → defer (exit 3, create NOT called)\n'
  CR29="$WORK_DIR/created29"; rm -f "$CR29"
  out="$(GH_LIST_FAIL=1 GH_DEDUP_EXISTING="ignored" GH_CREATE_CALLED="$CR29" GH_BODY_COPY="$WORK_DIR/body29.md" \
        PATH="$DDBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R26" \
        --create-issue --dedup --repo owner/repo 2>&1)"; rc=$?
  assert_eq "exit 3 (deferred, not landed)" "3" "$rc"
  assert_contains "reports dedup query failure" "dedup query failed" "$out"
  assert_eq "create NOT called on query failure" "no" "$([[ -f "$CR29" ]] && echo yes || echo no)"

  printf '\n[31] --dedup: same new but different old → distinct key, NOT deduped (Codex collision fix)\n'
  DALT="$WORK_DIR/deltas_altold.json"
  jq -n '{checked_at:"2026-05-26T00:00:00Z",
    deltas:[{source:"npm:@anthropic-ai/claude-code", kind:"npm", label:"intel/sdk", old:"2.1.0", new:"2.2.0"}],
    seeded:[], unchanged:[], errors:[]}' > "$DALT" || fixture_fail "deltas_altold"
  CR31="$WORK_DIR/created31"; rm -f "$CR31"
  # Existing open Issue carries DKEY (from [27], old=2.1.150→2.2.0). This batch is
  # old=2.1.0→2.2.0 (same new, different old) → different key → must NOT be skipped.
  EXISTING_BODY31="prior issue
<!-- intel-dedup-key: $DKEY -->"
  EXISTING_JSON31="$(jq -nc --arg b "$EXISTING_BODY31" '[{body:$b}]')" || fixture_fail "existing_json 31"
  out="$(GH_DEDUP_EXISTING="$EXISTING_JSON31" GH_CREATE_CALLED="$CR31" GH_BODY_COPY="$WORK_DIR/body31.md" \
        PATH="$DDBIN:$PATH" bash "$JUDGE" --deltas-file "$DALT" --llm-response-file "$R26" \
        --create-issue --dedup --repo owner/repo 2>&1)"; rc=$?
  assert_eq "exit 0" "0" "$rc"
  assert_eq "create WAS called (distinct transition, not deduped)" "yes" "$([[ -f "$CR31" ]] && echo yes || echo no)"

  printf '\n[32] --dedup: open Issues EXCEED the scan cap → defer (exit 3, create NOT called)\n'
  CR32="$WORK_DIR/created32"; rm -f "$CR32"
  # LIMIT=1 + a 2-element list (no marker) → the code fetches limit+1=2 and gets 2,
  # so n_open(2) > limit(1) → listing may be truncated → cannot confirm dedup → defer.
  out="$(INTEL_JUDGE_DEDUP_LIST_LIMIT=1 GH_DEDUP_EXISTING='[{"body":"open issue a"},{"body":"open issue b"}]' \
        GH_CREATE_CALLED="$CR32" GH_BODY_COPY="$WORK_DIR/body32.md" \
        PATH="$DDBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R26" \
        --create-issue --dedup --repo owner/repo 2>&1)"; rc=$?
  assert_eq "exit 3 (exceeds cap, deferred)" "3" "$rc"
  assert_contains "reports cap defer" "exceed the --limit" "$out"
  assert_eq "create NOT called at cap" "no" "$([[ -f "$CR32" ]] && echo yes || echo no)"

  printf '\n[33] --dedup: open Issues EXACTLY at the cap → complete listing, creates (no false defer)\n'
  CR33="$WORK_DIR/created33"; rm -f "$CR33"
  # LIMIT=2 + a 2-element list (no marker) → fetch limit+1=3 returns 2, so n_open(2)
  # is NOT > limit(2): the listing is complete, no marker → create (Argus minor fix:
  # exactly `limit` open Issues must not be a false-positive defer).
  out="$(INTEL_JUDGE_DEDUP_LIST_LIMIT=2 GH_DEDUP_EXISTING='[{"body":"open issue a"},{"body":"open issue b"}]' \
        GH_CREATE_CALLED="$CR33" GH_BODY_COPY="$WORK_DIR/body33.md" \
        PATH="$DDBIN:$PATH" bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R26" \
        --create-issue --dedup --repo owner/repo 2>&1)"; rc=$?
  assert_eq "exit 0 (complete listing at cap)" "0" "$rc"
  assert_eq "create WAS called (no false defer at exactly cap)" "yes" "$([[ -f "$CR33" ]] && echo yes || echo no)"
else
  printf '\n[27-29,31-33] SKIP: gh stub not picked up on PATH (platform shim)\n'
fi

# ── Scenario 30: the --context-file boundary is the git worktree root, so a context
#    file in the repo ROOT is accepted even when invoked from a SUBDIR (the PR #456
#    Argus advisory; old code used `pwd -P` and would fail-closed-reject it). No gh
#    needed. The startup path-validation accepts it; --llm-response-file then bypasses
#    the (unused) body build, so a clean dry-run preview exit 0 means "accepted". ──
printf '\n[30] context-file in repo root accepted when invoked from a subdir (boundary widening)\n'
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
POSCTX="$REPO_ROOT/.intel-ctx-positive-$$.tmp"; printf 'ctx-positive\n' > "$POSCTX"
out="$(cd "$SCRIPT_DIR" && bash "$JUDGE" --deltas-file "$DELTAS" --llm-response-file "$R26" \
      --context-file "$POSCTX" 2>&1)"; rc=$?
rm -f "$POSCTX"
assert_eq "exit 0 (context in repo root accepted from subdir)" "0" "$rc"
assert_not_contains "no working-tree rejection" "working tree" "$out"

# ── Summary ──
printf '\n=== intel-judge tests: %s passed, %s failed ===\n' "$PASS" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  printf 'Failures:\n'; for f in "${FAILURES[@]}"; do printf '  - %s\n' "$f"; done
  exit 1
fi
exit 0
