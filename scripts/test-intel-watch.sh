#!/usr/bin/env bash
# Tests for scripts/intel-watch.sh — mock the external sources with local
# file:// fixtures (no network, no GitHub auth) and drive the collector through
# every state transition: seed / unchanged / delta / fetch-error, plus the
# state-backend round trip and the pinned-Issue-body marker extraction contract.
#
# Run: bash scripts/test-intel-watch.sh

set -u  # not -e: each scenario inspects rc explicitly

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
WATCH="$SCRIPT_DIR/intel-watch.sh"
[[ -x "$WATCH" ]] || { printf 'intel-watch.sh not executable: %s\n' "$WATCH" >&2; exit 1; }

for cmd in jq curl; do
  command -v "$cmd" >/dev/null 2>&1 || { printf 'SKIP-ALL: %s unavailable\n' "$cmd"; exit 0; }
done
# sha256 backend family (matches intel-watch.sh's sha256_hex fallback chain) —
# stay portable on macOS/BSD where GNU sha256sum may be absent.
if ! command -v sha256sum >/dev/null 2>&1 \
   && ! command -v shasum   >/dev/null 2>&1 \
   && ! command -v openssl  >/dev/null 2>&1; then
  printf 'SKIP-ALL: no sha256 backend (sha256sum/shasum/openssl)\n'; exit 0
fi

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

WORK_DIR="$(mktemp -d -t intel-watch-test.XXXXXX)" || { printf 'mktemp failed\n' >&2; exit 1; }
trap '[[ -n "${WORK_DIR:-}" && -d "$WORK_DIR" ]] && rm -rf "$WORK_DIR"' EXIT

# ── Build file:// mock registry + changelog. curl reads file:// and ignores
#    the Accept header, so the abbreviated-packument JSON shape is what matters. ──
REG="$WORK_DIR/registry"
mkdir -p "$REG/@anthropic-ai"
write_pkg() { # $1=pkg-basename $2=latest-version
  jq -n --arg v "$2" '{"dist-tags":{latest:$v, next:$v}, versions:{}}' \
    > "$REG/@anthropic-ai/$1"
}
write_pkg "claude-code" "2.1.150"
write_pkg "claude-agent-sdk" "0.3.150"

CHANGELOG="$WORK_DIR/CHANGELOG.md"
printf '# Changelog\n\n## 2.1.150\n\n- initial\n' > "$CHANGELOG"

# file:// URLs. The mingw64 curl build cannot see MSYS virtual paths (/tmp/...),
# so on Windows convert to a mixed Windows path (C:/...) via cygpath; on a POSIX
# runner (GHA ubuntu) there is no cygpath and file://<abs-posix-path> works as-is.
to_file_url() {
  local p="$1"
  if command -v cygpath >/dev/null 2>&1; then
    p="$(cygpath -m "$p")"
  fi
  printf 'file://%s' "$p"
}
export INTEL_NPM_REGISTRY; INTEL_NPM_REGISTRY="$(to_file_url "$REG")"
export INTEL_CHANGELOG_URL; INTEL_CHANGELOG_URL="$(to_file_url "$CHANGELOG")"
# The collector default-denies non-https source URLs; opt in for the file://
# fixtures. Scenario 16 exercises the guard itself with this flag unset.
export INTEL_ALLOW_INSECURE_SOURCE_URLS=1

run_watch() { bash "$WATCH" "$@"; }

# Pre-flight: confirm curl can actually read the mock changelog via file://.
# If not (platform-specific file:// quirk), skip the network-dependent suite
# rather than emit confusing failures.
if ! curl -sS --max-time 5 "$INTEL_CHANGELOG_URL" >/dev/null 2>&1; then
  printf 'SKIP-ALL: curl cannot read file:// fixtures on this platform\n'
  exit 0
fi

# ── Scenario 1: first run on empty state file → all SEEDED, no delta, exit 0 ──
printf '\n[1] first run seeds all sources\n'
S1="$WORK_DIR/state1.json"
J1="$WORK_DIR/report1.json"
out="$(run_watch --state-file "$S1" --json-out "$J1" --commit 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_eq "3 seeded" "3" "$(jq '.seeded|length' "$J1")"
assert_eq "0 delta" "0" "$(jq '.deltas|length' "$J1")"
assert_eq "0 error" "0" "$(jq '.errors|length' "$J1")"
assert_eq "state persisted 3 sources" "3" "$(jq '.sources|length' "$S1")"
assert_eq "claude-code seeded version" "2.1.150" "$(jq -r '.sources["npm:@anthropic-ai/claude-code"]' "$S1")"
assert_contains "changelog fp is sha256" "sha256:" "$(jq -r '.sources["changelog:claude-code"]' "$S1")"

# ── Scenario 2: re-run against committed state with no upstream change → unchanged ──
printf '\n[2] re-run with no change → unchanged\n'
J2="$WORK_DIR/report2.json"
out="$(run_watch --state-file "$S1" --json-out "$J2" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_eq "0 delta" "0" "$(jq '.deltas|length' "$J2")"
assert_eq "0 seeded" "0" "$(jq '.seeded|length' "$J2")"
assert_eq "3 unchanged" "3" "$(jq '.unchanged|length' "$J2")"
assert_contains "dry-run notice" "state not persisted" "$out"

# ── Scenario 3: upstream version bump → DELTA with old/new ──
printf '\n[3] npm version bump → delta\n'
write_pkg "claude-code" "2.1.151"
J3="$WORK_DIR/report3.json"
out="$(run_watch --state-file "$S1" --json-out "$J3" 2>&1)"; rc=$?
assert_eq "exit 0" "0" "$rc"
assert_eq "1 delta" "1" "$(jq '.deltas|length' "$J3")"
assert_eq "delta source" "npm:@anthropic-ai/claude-code" "$(jq -r '.deltas[0].source' "$J3")"
assert_eq "delta old" "2.1.150" "$(jq -r '.deltas[0].old' "$J3")"
assert_eq "delta new" "2.1.151" "$(jq -r '.deltas[0].new' "$J3")"
assert_eq "delta label whitelisted" "intel/sdk" "$(jq -r '.deltas[0].label' "$J3")"
# dry-run did NOT persist → state file still has old version
assert_eq "state unchanged after dry-run" "2.1.150" "$(jq -r '.sources["npm:@anthropic-ai/claude-code"]' "$S1")"

# ── Scenario 4: changelog content change → delta on the hash source ──
printf '\n[4] changelog edit → hash delta\n'
printf '# Changelog\n\n## 2.1.151\n\n- new entry\n' > "$CHANGELOG"
J4="$WORK_DIR/report4.json"
run_watch --state-file "$S1" --json-out "$J4" >/dev/null 2>&1
# claude-code (2.1.151) + changelog both differ from committed state now → 2 deltas
assert_eq "2 deltas (npm + changelog)" "2" "$(jq '.deltas|length' "$J4")"
assert_contains "changelog in deltas" "changelog:claude-code" "$(jq -r '.deltas[].source' "$J4")"

# ── Scenario 5: --commit persists the new fingerprints ──
printf '\n[5] commit persists new state\n'
run_watch --state-file "$S1" --commit >/dev/null 2>&1; rc=$?
assert_eq "commit exit 0" "0" "$rc"
assert_eq "state now 2.1.151" "2.1.151" "$(jq -r '.sources["npm:@anthropic-ai/claude-code"]' "$S1")"
# subsequent run is clean
run_watch --state-file "$S1" --json-out "$WORK_DIR/r5b.json" >/dev/null 2>&1
assert_eq "clean after commit" "0" "$(jq '.deltas|length' "$WORK_DIR/r5b.json")"

# ── Scenario 6: fetch error → reported as error, exit 1, state NOT corrupted ──
printf '\n[6] unreachable source → error, exit 1, state preserved\n'
J6="$WORK_DIR/report6.json"
before="$(jq -r '.sources["npm:@anthropic-ai/claude-code"]' "$S1")"
out="$(INTEL_NPM_REGISTRY="file://$WORK_DIR/nonexistent-registry" \
       run_watch --state-file "$S1" --json-out "$J6" --commit 2>&1)"; rc=$?
assert_eq "exit 1 on fetch error" "1" "$rc"
assert_eq "2 npm errors" "2" "$(jq '.errors|length' "$J6")"
assert_contains "error names a source" "npm:@anthropic-ai/claude-code" "$(jq -r '.errors[]' "$J6")"
# changelog still reachable → it stays in state; errored npm sources keep old fp
assert_eq "errored source kept old fp" "$before" "$(jq -r '.sources["npm:@anthropic-ai/claude-code"]' "$S1")"

# ── Scenario 7: corrupt state file → exit 3, no silent reset ──
printf '\n[7] corrupt state file → exit 3\n'
echo "{ not json" > "$WORK_DIR/bad.json"
out="$(run_watch --state-file "$WORK_DIR/bad.json" 2>&1)"; rc=$?
assert_eq "exit 3 on bad state" "3" "$rc"
assert_contains "names the problem" "not valid JSON" "$out"

# ── Scenario 8: --commit with no backend → usage error (exit 2) ──
printf '\n[8] --commit without backend → exit 2\n'
out="$(run_watch --commit 2>&1)"; rc=$?
assert_eq "exit 2" "2" "$rc"
assert_contains "explains requirement" "requires a state backend" "$out"

# ── Scenario 9: two state backends → mutual exclusion (exit 2) ──
printf '\n[9] both backends → exit 2\n'
out="$(run_watch --state-file "$S1" --state-issue 99 2>&1)"; rc=$?
assert_eq "exit 2" "2" "$rc"
assert_contains "explains XOR" "at most one state backend" "$out"

# ── Scenario 10: pinned-Issue-body marker extraction contract ──
# state_read uses this awk+sed pipeline to pull JSON from the Issue body. Verify
# the exact extraction contract here without needing gh/network.
printf '\n[10] Issue-body marker extraction contract\n'
BODY="$WORK_DIR/issue-body.md"
cat > "$BODY" <<'EOF'
# 🛰️ Intel-Watch State (auto-managed)

prose that must be ignored

<!-- INTEL_STATE_BEGIN -->
```json
{"schema_version":1,"sources":{"k":"v"}}
```
<!-- INTEL_STATE_END -->

trailing prose ignored
EOF
extracted="$(awk -v b="<!-- INTEL_STATE_BEGIN -->" -v e="<!-- INTEL_STATE_END -->" \
  'index($0,b){f=1;next} index($0,e){f=0} f' "$BODY" | sed '/^```/d')"
assert_eq "extracted is valid JSON" "v" "$(printf '%s' "$extracted" | jq -r '.sources.k')"
assert_contains "no fence leaked" "schema_version" "$extracted"
# a body with no markers (fresh empty Issue) extracts to whitespace → empty-state path
empty_extract="$(printf '# just a heading\n' | awk -v b="<!-- INTEL_STATE_BEGIN -->" -v e="<!-- INTEL_STATE_END -->" \
  'index($0,b){f=1;next} index($0,e){f=0} f' | sed '/^```/d' | tr -d '[:space:]')"
assert_eq "no-marker body extracts empty" "" "$empty_extract"

# ── Scenarios 11-13: end-to-end --state-issue path via a fake `gh` on PATH.
# This exercises the real state_read Issue branch (incl. the marker-integrity
# gate) without a live GitHub round trip. The live edit/write round trip is
# Slice 3. Probe first; skip cleanly if the stub is not picked up on PATH. ──
GHBIN="$WORK_DIR/ghbin"; mkdir -p "$GHBIN"
cat > "$GHBIN/gh" <<'STUB'
#!/usr/bin/env bash
if [[ "$1" == "issue" && "$2" == "view" ]]; then cat "$FAKE_BODY"; exit 0; fi
if [[ "$1" == "issue" && "$2" == "edit" ]]; then exit 0; fi
echo "ghstub: unsupported: $*" >&2; exit 1
STUB
chmod +x "$GHBIN/gh"
PROBE="$WORK_DIR/probe.md"; printf 'no markers\n' > "$PROBE"
GH_STUB_OK=0
if [[ "$(FAKE_BODY="$PROBE" PATH="$GHBIN:$PATH" gh issue view 1 --json body -q .body 2>/dev/null)" == "no markers" ]]; then
  GH_STUB_OK=1
fi

if [[ "$GH_STUB_OK" -eq 1 ]]; then
  # reset mock to a known head so deltas are deterministic
  write_pkg "claude-code" "2.1.151"
  write_pkg "claude-agent-sdk" "0.3.150"

  printf '\n[11] state-issue: complete marker pair → state is read (produces delta)\n'
  BODY11="$WORK_DIR/body11.md"
  cat > "$BODY11" <<'EOF'
# Intel-Watch State

<!-- INTEL_STATE_BEGIN -->
```json
{"schema_version":1,"updated_at":"old","sources":{"npm:@anthropic-ai/claude-code":"0.0.1","npm:@anthropic-ai/claude-agent-sdk":"0.3.150","changelog:claude-code":"sha256:stale"}}
```
<!-- INTEL_STATE_END -->
EOF
  J11="$WORK_DIR/r11.json"
  FAKE_BODY="$BODY11" PATH="$GHBIN:$PATH" bash "$WATCH" --state-issue 1 --json-out "$J11" >/dev/null 2>&1; rc=$?
  assert_eq "exit 0" "0" "$rc"
  assert_eq "stale claude-code → delta old 0.0.1" "0.0.1" "$(jq -r '.deltas[] | select(.source=="npm:@anthropic-ai/claude-code") | .old' "$J11")"
  assert_eq "delta new 2.1.151" "2.1.151" "$(jq -r '.deltas[] | select(.source=="npm:@anthropic-ai/claude-code") | .new' "$J11")"
  assert_eq "agent-sdk unchanged (read worked)" "0" "$(jq '[.deltas[]|select(.source=="npm:@anthropic-ai/claude-agent-sdk")]|length' "$J11")"

  printf '\n[12] state-issue: no markers (fresh Issue) → seed from empty\n'
  BODY12="$WORK_DIR/body12.md"; printf '# fresh state issue, no markers yet\n' > "$BODY12"
  J12="$WORK_DIR/r12.json"
  FAKE_BODY="$BODY12" PATH="$GHBIN:$PATH" bash "$WATCH" --state-issue 1 --json-out "$J12" >/dev/null 2>&1; rc=$?
  assert_eq "exit 0" "0" "$rc"
  assert_eq "3 seeded from empty" "3" "$(jq '.seeded|length' "$J12")"
  assert_eq "0 delta on fresh issue" "0" "$(jq '.deltas|length' "$J12")"

  printf '\n[13] state-issue: half marker pair (END lost) → fail loud (exit 3)\n'
  BODY13="$WORK_DIR/body13.md"
  cat > "$BODY13" <<'EOF'
# corrupted: BEGIN present, END truncated

<!-- INTEL_STATE_BEGIN -->
```json
{"schema_version":1,"sources":{"npm:@anthropic-ai/claude-code":"2.1.151"}}
```
EOF
  out="$(FAKE_BODY="$BODY13" PATH="$GHBIN:$PATH" bash "$WATCH" --state-issue 1 2>&1)"; rc=$?
  assert_eq "exit 3 on malformed markers" "3" "$rc"
  assert_contains "names malformed markers" "malformed markers" "$out"
else
  printf '\n[11-13] SKIP: gh stub not picked up on PATH (platform shim) — Issue-path covered by contract test [10]\n'
fi

# ── Scenario 14: portable sha256 backends agree (covers sha256_hex fallbacks) ──
# Mirrors the three extraction expressions in sha256_hex: sha256sum/shasum use
# `cut -d' ' -f1`, openssl ("SHA2-256(stdin)= <hash>") uses `awk '{print $NF}'`.
printf '\n[14] portable sha256 backends agree\n'
sample="intel-watch portability probe"
ref=""
if command -v sha256sum >/dev/null 2>&1; then ref="$(printf '%s' "$sample" | sha256sum | cut -d' ' -f1)"; fi
if command -v shasum >/dev/null 2>&1; then
  alt="$(printf '%s' "$sample" | shasum -a 256 | cut -d' ' -f1)"
  if [[ -n "$ref" ]]; then assert_eq "shasum == sha256sum" "$ref" "$alt"; else ref="$alt"; fi
fi
if command -v openssl >/dev/null 2>&1; then
  alt="$(printf '%s' "$sample" | openssl dgst -sha256 | awk '{print $NF}')"
  if [[ -n "$ref" ]]; then assert_eq "openssl == ref" "$ref" "$alt"; fi
fi
if [[ -z "$ref" ]]; then printf '  (no sha256 backend present to cross-check)\n'; fi

# ── Scenario 15: value-bearing flag missing its value → exit 2 (no shift overrun) ──
printf '\n[15] flag missing value → exit 2\n'
out="$(run_watch --state-file 2>&1)"; rc=$?
assert_eq "exit 2 on missing value" "2" "$rc"
assert_contains "explains requirement" "requires a non-empty value" "$out"

# ── Scenario 16: non-https source URL without opt-in → default-denied (exit 2) ──
printf '\n[16] non-https source URL default-denied\n'
out="$(INTEL_ALLOW_INSECURE_SOURCE_URLS=0 INTEL_NPM_REGISTRY="http://evil.example/r" \
       bash "$WATCH" --state-file "$WORK_DIR/s16.json" 2>&1)"; rc=$?
assert_eq "exit 2 on non-https" "2" "$rc"
assert_contains "names the guard" "must be https" "$out"

# ── Summary ──
printf '\n=== intel-watch tests: %s passed, %s failed ===\n' "$PASS" "$FAIL"
if [[ "$FAIL" -gt 0 ]]; then
  printf 'Failures:\n'; for f in "${FAILURES[@]}"; do printf '  - %s\n' "$f"; done
  exit 1
fi
exit 0
