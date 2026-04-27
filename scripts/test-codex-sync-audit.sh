#!/usr/bin/env bash
# Tests for codex-sync-audit.sh — uses a mock codex-companion injected via env var.
#
# No real Codex needed. Each scenario invokes the wrapper against a tiny mock
# script that returns deterministic JSON and asserts exit code + stdout marker.
#
# Run: bash scripts/test-codex-sync-audit.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WRAPPER="$SCRIPT_DIR/codex-sync-audit.sh"
[[ -x "$WRAPPER" ]] || { printf 'wrapper not executable: %s\n' "$WRAPPER" >&2; exit 1; }

PASS=0
FAIL=0
declare -a FAILURES=()

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" == "$actual" ]]; then
    PASS=$((PASS + 1))
    printf '  ✓ %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    FAILURES+=("$label: expected [$expected], got [$actual]")
    printf '  ✗ %s — expected [%s], got [%s]\n' "$label" "$expected" "$actual"
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    PASS=$((PASS + 1))
    printf '  ✓ %s\n' "$label"
  else
    FAIL=$((FAIL + 1))
    FAILURES+=("$label: missing [$needle] in stdout")
    printf '  ✗ %s — missing [%s]\n' "$label" "$needle"
  fi
}

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

PROMPT_FILE="$WORK_DIR/prompt.txt"
printf 'audit prompt\n' > "$PROMPT_FILE"

# --- mock codex-companion script ---
# Behaviour switches on $MOCK_MODE env var: succeeded | failed | timeout | bad_dispatch | bad_jobid
make_mock() {
  local mode="$1"
  local script="$WORK_DIR/mock-companion-$mode.mjs"
  cat > "$script" <<EOF
#!/usr/bin/env node
const args = process.argv.slice(2);
const sub = args[0];
const mode = "$mode";
function emit(obj) { process.stdout.write(JSON.stringify(obj) + "\n"); }
if (sub === "task") {
  if (mode === "bad_dispatch") { process.stderr.write("dispatch failed\n"); process.exit(1); }
  if (mode === "bad_jobid") { emit({ title: "x", summary: "y" }); process.exit(0); }
  emit({ jobId: "mock-job-123", title: "t", summary: "s" });
  process.exit(0);
}
if (sub === "status") {
  if (mode === "timeout") {
    emit({ job: { id: "mock-job-123", status: "running", phase: "thinking" }, waitTimedOut: true, timeoutMs: 1000 });
    process.exit(0);
  }
  if (mode === "failed") {
    emit({ job: { id: "mock-job-123", status: "failed", phase: "error" }, waitTimedOut: false, timeoutMs: 1000 });
    process.exit(0);
  }
  // Terminal-success status from real codex-companion is "completed" (per lib/codex.mjs).
  emit({ job: { id: "mock-job-123", status: "completed", phase: "done" }, waitTimedOut: false, timeoutMs: 1000 });
  process.exit(0);
}
if (sub === "result") {
  // Mirror the real codex-companion task-result payload shape:
  //   storedJob.id, storedJob.result.rawOutput (the verdict text),
  //   storedJob.result.status, storedJob.rendered (formatted display).
  // The wrapper validates .storedJob.id is present before printing the RESULT block.
  emit({
    job: { id: "mock-job-123", status: "completed" },
    storedJob: {
      id: "mock-job-123",
      status: "completed",
      result: {
        rawOutput: "Critical: 0  High: 0  Medium: 0  Low: 0\nOverall: PASS",
        status: "completed",
        threadId: "mock-thread-1"
      },
      rendered: "Mock rendered output"
    }
  });
  process.exit(0);
}
if (sub === "cancel") {
  // Tests may install an EXIT trap; the trap calls cancel on cleanup. Stub it.
  emit({ jobId: process.argv[3] || "unknown", cancelled: true });
  process.exit(0);
}
process.stderr.write("mock: unexpected subcommand " + sub + "\n");
process.exit(2);
EOF
  printf '%s' "$script"
}

run_wrapper() {
  local mode="$1"; shift
  local script
  script="$(make_mock "$mode")"
  CODEX_COMPANION_SCRIPT="$script" "$WRAPPER" "$@"
}

# Each test runs the wrapper, captures stdout + exit code, asserts.
run_capture() {
  local mode="$1"; shift
  local out exit_code
  set +e
  out="$(run_wrapper "$mode" "$@" 2>/dev/null)"
  exit_code=$?
  set -e
  printf '%s\n%d' "$out" "$exit_code"
}

# --- tests ---

printf '\n[1] usage error: missing prompt file\n'
set +e
"$WRAPPER" 2>/dev/null
ec=$?
set -e
assert_eq "exit code 2" "2" "$ec"

printf '\n[2] usage error: prompt file does not exist\n'
set +e
"$WRAPPER" /nonexistent/prompt.txt 2>/dev/null
ec=$?
set -e
assert_eq "exit code 2" "2" "$ec"

printf '\n[3] usage error: empty prompt file\n'
empty="$WORK_DIR/empty.txt"
: > "$empty"
set +e
"$WRAPPER" "$empty" 2>/dev/null
ec=$?
set -e
assert_eq "exit code 2" "2" "$ec"

printf '\n[4] usage error: bad timeout (non-numeric)\n'
set +e
"$WRAPPER" "$PROMPT_FILE" --timeout abc 2>/dev/null
ec=$?
set -e
assert_eq "exit code 2" "2" "$ec"

printf '\n[4b] usage error: timeout above 86400-second cap\n'
set +e
"$WRAPPER" "$PROMPT_FILE" --timeout 90000 2>/dev/null
ec=$?
set -e
assert_eq "exit code 2 on > 86400s timeout" "2" "$ec"

printf '\n[4c] usage error: poll interval larger than timeout\n'
set +e
"$WRAPPER" "$PROMPT_FILE" --timeout 60 --poll-interval 120 2>/dev/null
ec=$?
set -e
assert_eq "exit code 2 on poll > timeout" "2" "$ec"

printf '\n[5] usage error: unknown option\n'
set +e
"$WRAPPER" "$PROMPT_FILE" --not-a-flag 2>/dev/null
ec=$?
set -e
assert_eq "exit code 2" "2" "$ec"

printf '\n[6] dependency error: companion script does not exist\n'
set +e
CODEX_COMPANION_SCRIPT="/no/such/script.mjs" "$WRAPPER" "$PROMPT_FILE" 2>/dev/null
ec=$?
set -e
assert_eq "exit code 3" "3" "$ec"

printf '\n[6b] cache discovery picks highest semver, not lex order (default sort -V path)\n'
# Set up a fake $HOME with five cached plugin versions: 1.0.2, 1.0.10, 2.0.0, 9.9.9, 10.0.0.
# Lex sort would mis-order 10.0.0 < 9.9.9 and 1.0.10 < 1.0.2; semver must pick 10.0.0 first.
fake_home="$WORK_DIR/fake-home-semver"
for v in 1.0.2 1.0.10 2.0.0 9.9.9 10.0.0; do
  d="$fake_home/.claude/plugins/cache/openai-codex/codex/$v/scripts"
  mkdir -p "$d"
  : > "$d/codex-companion.mjs"
done
combined="$(set +e; HOME="$fake_home" USERPROFILE="$fake_home" CLAUDE_PLUGIN_ROOT="" CODEX_COMPANION_SCRIPT="" "$WRAPPER" "$PROMPT_FILE" --dry-run 2>/dev/null; printf '\n%d' "$?")"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0 in dry-run" "0" "$ec"
if [[ "$out" == *"/10.0.0/scripts/codex-companion.mjs"* ]]; then
  PASS=$((PASS + 1))
  printf '  ✓ resolved to highest semver (10.0.0 wins over 9.9.9 / 2.0.0)\n'
else
  FAIL=$((FAIL + 1))
  FAILURES+=("semver discovery picked wrong version. Output: $out")
  printf '  ✗ semver discovery picked wrong version — output:\n%s\n' "$out"
fi

printf '\n[6c] awk fallback semver sort works without sort -V\n'
# Shadow `sort` so the wrapper's capability probe (`printf ... | sort -V`) fails, forcing
# the awk fallback path. The shadow rejects -V invocations but otherwise delegates to real
# sort so the wrapper's other sort calls (in the awk pipeline itself) still work.
fake_bin="$WORK_DIR/fake-bin-no-sortV"
mkdir -p "$fake_bin"
real_sort="$(command -v sort)"
cat > "$fake_bin/sort" <<EOF
#!/usr/bin/env bash
for arg in "\$@"; do
  if [[ "\$arg" == "-V" || "\$arg" == "--version-sort" || "\$arg" == "-V"* ]]; then
    printf 'fake-sort: -V not supported\n' >&2
    exit 2
  fi
done
exec "$real_sort" "\$@"
EOF
chmod +x "$fake_bin/sort"
combined="$(set +e; PATH="$fake_bin:$PATH" HOME="$fake_home" USERPROFILE="$fake_home" CLAUDE_PLUGIN_ROOT="" CODEX_COMPANION_SCRIPT="" "$WRAPPER" "$PROMPT_FILE" --dry-run 2>/dev/null; printf '\n%d' "$?")"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0 in dry-run (fallback path)" "0" "$ec"
if [[ "$out" == *"/10.0.0/scripts/codex-companion.mjs"* ]]; then
  PASS=$((PASS + 1))
  printf '  ✓ awk fallback also picks 10.0.0\n'
else
  FAIL=$((FAIL + 1))
  FAILURES+=("awk fallback picked wrong version. Output: $out")
  printf '  ✗ awk fallback picked wrong version — output:\n%s\n' "$out"
fi

printf '\n[7] success path: emits RESULT marker, exit 0\n'
combined="$(run_capture succeeded "$PROMPT_FILE")"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0" "0" "$ec"
assert_contains "RESULT marker present" "===CODEX-SYNC-AUDIT RESULT===" "$out"
# The wrapper validates .storedJob.id is present before emitting; the mock provides
# a real storedJob.result.rawOutput that mirrors the actual codex-companion shape.
assert_contains "rawOutput field present in payload" "rawOutput" "$out"
assert_contains "Overall verdict line in rawOutput" "Overall: PASS" "$out"

printf '\n[8] failed path: emits FAILED marker, exit 1\n'
combined="$(run_capture failed "$PROMPT_FILE")"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 1" "1" "$ec"
assert_contains "FAILED marker present" "===CODEX-SYNC-AUDIT FAILED===" "$out"

printf '\n[9] timeout path: emits TIMEOUT marker, exit 124\n'
combined="$(run_capture timeout "$PROMPT_FILE")"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 124" "124" "$ec"
assert_contains "TIMEOUT marker present" "===CODEX-SYNC-AUDIT TIMEOUT===" "$out"
assert_contains "waitTimedOut: true serialised" '"waitTimedOut":true' "$out"

printf '\n[10] dispatch failure: exit 3\n'
set +e
script="$(make_mock bad_dispatch)"
CODEX_COMPANION_SCRIPT="$script" "$WRAPPER" "$PROMPT_FILE" >/dev/null 2>/dev/null
ec=$?
set -e
assert_eq "exit code 3" "3" "$ec"

printf '\n[11] missing jobId in dispatch payload: exit 3\n'
set +e
script="$(make_mock bad_jobid)"
CODEX_COMPANION_SCRIPT="$script" "$WRAPPER" "$PROMPT_FILE" >/dev/null 2>/dev/null
ec=$?
set -e
assert_eq "exit code 3" "3" "$ec"

printf '\n[12] dry-run: no companion invocation needed\n'
combined="$(run_capture succeeded "$PROMPT_FILE" --dry-run)"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0" "0" "$ec"
assert_contains "DRY RUN banner" "DRY RUN" "$out"
assert_contains "wait command shown" "status <jobId> --wait --timeout-ms 600000" "$out"
assert_contains "poll interval shown" "poll-interval-ms 15000" "$out"

printf '\n[13] dry-run with custom timeout/poll\n'
combined="$(run_capture succeeded "$PROMPT_FILE" --dry-run --timeout 900 --poll-interval 30)"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0" "0" "$ec"
assert_contains "custom timeout-ms" "timeout-ms 900000" "$out"
assert_contains "custom poll-ms" "poll-interval-ms 30000" "$out"

printf '\n[14] dry-run is read-only by default (no --write in dispatch)\n'
combined="$(run_capture succeeded "$PROMPT_FILE" --dry-run)"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0" "0" "$ec"
if [[ "$out" == *"--write"* ]]; then
  FAIL=$((FAIL + 1))
  FAILURES+=("default mode incorrectly passed --write")
  printf '  ✗ default suppresses --write — but --write still found\n'
else
  PASS=$((PASS + 1))
  printf '  ✓ default suppresses --write (read-only by default)\n'
fi

printf '\n[15] dry-run with --write opts in to write mode\n'
combined="$(run_capture succeeded "$PROMPT_FILE" --dry-run --write)"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0" "0" "$ec"
assert_contains "--write opt-in honored" "--write" "$out"

printf '\n[16] dry-run --read-only is a no-op alias\n'
combined="$(run_capture succeeded "$PROMPT_FILE" --dry-run --read-only)"
out="${combined%$'\n'*}"
ec="${combined##*$'\n'}"
assert_eq "exit code 0" "0" "$ec"
if [[ "$out" == *"--write"* ]]; then
  FAIL=$((FAIL + 1))
  FAILURES+=("explicit --read-only still passed --write")
  printf '  ✗ explicit --read-only suppresses --write — but --write still found\n'
else
  PASS=$((PASS + 1))
  printf '  ✓ explicit --read-only suppresses --write\n'
fi

printf '\n[17] result payload missing .storedJob.id triggers controlled exit 3\n'
# Build a one-off mock that returns a result payload missing the required id field.
bad_result="$WORK_DIR/mock-companion-bad-result.mjs"
cat > "$bad_result" <<'MOCK_EOF'
#!/usr/bin/env node
const sub = process.argv[2];
function emit(o) { process.stdout.write(JSON.stringify(o) + "\n"); }
if (sub === "task")   { emit({ jobId: "mock-job-123" }); process.exit(0); }
if (sub === "status") { emit({ job: { id: "mock-job-123", status: "completed" }, waitTimedOut: false, timeoutMs: 1000 }); process.exit(0); }
if (sub === "result") { emit({ this_is: "missing storedJob entirely" }); process.exit(0); }
if (sub === "cancel") { emit({ cancelled: true }); process.exit(0); }
process.exit(2);
MOCK_EOF
set +e
CODEX_COMPANION_SCRIPT="$bad_result" "$WRAPPER" "$PROMPT_FILE" >/dev/null 2>/dev/null
ec=$?
set -e
assert_eq "exit code 3 on missing storedJob.id (post-jq guard)" "3" "$ec"

printf '\n[18] result payload that breaks jq_or_die parse triggers exit 3\n'
# Genuine malformed JSON path — the result subcommand emits invalid JSON syntax,
# jq_or_die() should catch the parse failure and die with exit 3.
malformed_jq="$WORK_DIR/mock-companion-malformed-jq.mjs"
cat > "$malformed_jq" <<'MOCK_EOF'
#!/usr/bin/env node
const sub = process.argv[2];
function emit(o) { process.stdout.write(JSON.stringify(o) + "\n"); }
if (sub === "task")   { emit({ jobId: "mock-job-123" }); process.exit(0); }
if (sub === "status") { emit({ job: { id: "mock-job-123", status: "completed" }, waitTimedOut: false, timeoutMs: 1000 }); process.exit(0); }
if (sub === "result") { process.stdout.write("{ this is not json }\n"); process.exit(0); }
if (sub === "cancel") { emit({ cancelled: true }); process.exit(0); }
process.exit(2);
MOCK_EOF
set +e
CODEX_COMPANION_SCRIPT="$malformed_jq" "$WRAPPER" "$PROMPT_FILE" >/dev/null 2>/dev/null
ec=$?
set -e
assert_eq "exit code 3 on malformed JSON (jq parse failure)" "3" "$ec"

printf '\n[19] EXIT trap cancels orphaned job on post-dispatch failure\n'
# When jq_or_die fails after JOB_ID is known, the EXIT trap must call `cancel`.
# We verify by having the mock log every subcommand invocation and asserting
# that "cancel" appears in the log AFTER a malformed-result run.
trap_log="$WORK_DIR/cancel-trap.log"
# Node on Windows + Git Bash needs a path Node can resolve. mktemp -d returns
# a /tmp-style path that Node sometimes can't open. Convert via cygpath -m
# (mixed: forward-slash + drive letter) when on Windows; otherwise pass through.
if command -v cygpath >/dev/null 2>&1; then
  trap_log_node="$(cygpath -m "$trap_log")"
else
  trap_log_node="$trap_log"
fi
trap_mock="$WORK_DIR/mock-companion-trap.mjs"
# .mjs files are ESM-only — `require` is undefined. Use named ESM import.
cat > "$trap_mock" <<MOCK_EOF
#!/usr/bin/env node
import { appendFileSync } from "node:fs";
const sub = process.argv[2];
appendFileSync(${trap_log_node@Q}, sub + "\n");
function emit(o) { process.stdout.write(JSON.stringify(o) + "\n"); }
if (sub === "task")   { emit({ jobId: "mock-job-123" }); process.exit(0); }
if (sub === "status") { emit({ job: { id: "mock-job-123", status: "completed" }, waitTimedOut: false, timeoutMs: 1000 }); process.exit(0); }
if (sub === "result") { process.stdout.write("{ malformed }\n"); process.exit(0); }
if (sub === "cancel") { emit({ cancelled: true }); process.exit(0); }
process.exit(2);
MOCK_EOF
: > "$trap_log"
set +e
CODEX_COMPANION_SCRIPT="$trap_mock" "$WRAPPER" "$PROMPT_FILE" >/dev/null 2>/dev/null
ec=$?
set -e
assert_eq "exit code 3 propagates" "3" "$ec"
if grep -q '^cancel$' "$trap_log"; then
  PASS=$((PASS + 1))
  printf '  ✓ EXIT trap invoked cancel subcommand\n'
else
  FAIL=$((FAIL + 1))
  FAILURES+=("EXIT trap did not invoke cancel; trap_log was: $(cat "$trap_log" | tr '\n' ' ')")
  printf '  ✗ EXIT trap did not invoke cancel — trap log: %s\n' "$(tr '\n' ' ' < "$trap_log")"
fi

# --- summary ---
printf '\n%s\n' '----------------------------------------'
printf 'tests: %d passed, %d failed\n' "$PASS" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  printf '\nFailures:\n'
  for f in "${FAILURES[@]}"; do printf '  - %s\n' "$f"; done
  exit 1
fi
exit 0
