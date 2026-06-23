#!/bin/sh
# scripts/test-register-service.sh — fixture-driven tests for
# scripts/register-service.sh (Issue #163). No real NAS / no real docker.
#
# Asserts: idempotency (byte-identical re-register), port-collision gate,
# comment preservation (header block + reserved_ports inline comments survive),
# and multi-file config_files / auto-derive container parsing. BusyBox-safe.
#
# Usage: sh scripts/test-register-service.sh [--verbose]
# Exit: 0 all pass / 1 any fail

set -u

VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { printf 'test: must run inside a git repo\n' >&2; exit 1; }
SCRIPT="$REPO_ROOT/scripts/register-service.sh"
[ -f "$SCRIPT" ] || { printf 'test: %s missing\n' "$SCRIPT" >&2; exit 1; }

PASS=0; FAIL=0; CASES=0
TEST_ROOT=$(mktemp -d) || { printf 'test: mktemp -d failed\n' >&2; exit 1; }
trap 'rm -rf "$TEST_ROOT"' EXIT

write_baseline_registry() {
  cat > "$1" <<'EOF'
# NAS Service Registry
# Tracks all services deployed on this NAS via Docker + Cloudflare Tunnel.
# Update this file when adding/removing services.

domain: fyc-space.uk
tunnel: argus

services:
  argus:
    port: 3000
    subdomain: argus
    url: https://argus.fyc-space.uk
    compose: /share/homes/392fyc/argus/docker-compose.yml
    containers: [argus, argus-tunnel]
    purpose: PR code review agent (PR-Agent + GPT-5.3-Codex)
    repo: https://github.com/392fyc/Argus
    cicd: GitHub Actions → cloudflared SSH → docker compose

  ssh-tunnel:
    port: 22
    subdomain: ssh
    url: ssh.fyc-space.uk
    purpose: SSH access via Cloudflare Tunnel (for CI/CD)

reserved_ports:
  - 3000  # argus
  - 22    # ssh
  - 22443 # QNAP Web UI (system)
EOF
}

# docker stub for auto-derive: sot-codex has two containers (multi-file compose).
make_docker_stub() {
  cat > "$1" <<'EOF'
#!/bin/sh
cmd="$1"; shift
[ "$cmd" = "ps" ] || exit 1
fmt=""
while [ $# -gt 0 ]; do case "$1" in --format) shift; fmt="$1"; shift ;; *) shift ;; esac; done
# project|Names form (auto-derive)
cat <<'ROWS'
sot-codex|sot-codex-tunnel-1
sot-codex|sot-codex-app-1
argus|argus
argus|argus-tunnel
argus|argus-selfcheck-scheduler
ROWS
EOF
  chmod +x "$1"
}

assert_rc() {
  CASES=$((CASES+1))
  if [ "$LAST_RC" = "$2" ]; then PASS=$((PASS+1)); else
    FAIL=$((FAIL+1)); printf 'FAIL: %s (expected rc=%s got %s)\n' "$1" "$2" "$LAST_RC" >&2
    [ -n "${LAST_OUT:-}" ] && printf '  out: %s\n' "$LAST_OUT" >&2
  fi
}
assert_out_contains() {
  CASES=$((CASES+1))
  if printf '%s' "$LAST_OUT" | grep -qF -- "$2"; then PASS=$((PASS+1)); else
    FAIL=$((FAIL+1)); printf 'FAIL: %s (stdout missing: %s)\n' "$1" "$2" >&2
  fi
}
assert_file_contains() {
  CASES=$((CASES+1))
  if grep -qF -- "$2" "$3"; then PASS=$((PASS+1)); else
    FAIL=$((FAIL+1)); printf 'FAIL: %s (file missing: %s)\n' "$1" "$2" >&2
  fi
}
pass_case() { CASES=$((CASES+1)); PASS=$((PASS+1)); }
fail_case() { CASES=$((CASES+1)); FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1" >&2; }

STUB="$TEST_ROOT/docker-stub.sh"
make_docker_stub "$STUB"
export MERCURY_REGISTER_TIMESTAMP="20260623-000000"

reg_lockdir() { printf '%s/lock-%s' "$TEST_ROOT" "$1"; }

run_register() {
  # $1 = registry file, rest = args. Unique lockdir per call (no stale lock).
  rf="$1"; shift
  ld="$TEST_ROOT/lock-$$-$CASES"
  LAST_OUT=$(REGISTRY_FILE="$rf" REGISTRY_DOCKER="$STUB" REGISTRY_LOCKDIR="$ld" \
             sh "$SCRIPT" "$@" 2>&1) && LAST_RC=0 || LAST_RC=$?
  rmdir "$ld" 2>/dev/null || true
  [ "$VERBOSE" = "1" ] && printf '--- register rc=%d ---\n%s\n' "$LAST_RC" "$LAST_OUT"
}

# === Test 1: register a NEW service → REGISTER-OK, upserted, port reserved ===
REG1="$TEST_ROOT/r1.yaml"
write_baseline_registry "$REG1"
run_register "$REG1" --name sot-codex --port 8400 --subdomain sot \
  --url https://sot.fyc-space.uk --containers "sot-codex-tunnel-1,sot-codex-app-1" \
  --purpose "SoT codex"
assert_rc "new_service_ok" 0
assert_out_contains "new_service_marker" "REGISTER-OK sot-codex"
assert_file_contains "new_service_in_file" "  sot-codex:" "$REG1"
assert_file_contains "new_service_port" "    port: 8400" "$REG1"
# containers[] is rendered SORTED (byte-identical regardless of input order).
assert_file_contains "new_service_containers" "containers: [sot-codex-app-1, sot-codex-tunnel-1]" "$REG1"
# Port reserved.
assert_file_contains "new_port_reserved" "- 8400" "$REG1"

# === Test 2: header comment block preserved verbatim after upsert ===
assert_file_contains "header_line1" "# NAS Service Registry" "$REG1"
assert_file_contains "header_line2" "# Tracks all services deployed on this NAS via Docker + Cloudflare Tunnel." "$REG1"
# === reserved_ports inline comments preserved verbatim ===
assert_file_contains "reserved_inline_argus" "- 3000  # argus" "$REG1"
assert_file_contains "reserved_inline_ssh" "- 22    # ssh" "$REG1"
assert_file_contains "reserved_inline_qnap" "- 22443 # QNAP Web UI (system)" "$REG1"
# === sibling service argus preserved (its containers[] untouched) ===
assert_file_contains "sibling_argus_kept" "containers: [argus, argus-tunnel]" "$REG1"

# === Test 3: idempotency — re-register same args → byte-identical services.yaml ===
SNAP=$(cat "$REG1")
run_register "$REG1" --name sot-codex --port 8400 --subdomain sot \
  --url https://sot.fyc-space.uk --containers "sot-codex-tunnel-1,sot-codex-app-1" \
  --purpose "SoT codex"
assert_rc "idempotent_ok" 0
if [ "$(cat "$REG1")" = "$SNAP" ]; then pass_case; else
  fail_case "idempotent_byte_identical (re-register changed the file)"
  if [ "$VERBOSE" = "1" ]; then
    printf '%s\n' "$SNAP" > "$TEST_ROOT/snap.txt"
    diff "$TEST_ROOT/snap.txt" "$REG1" >&2 || true
  fi
fi

# === Test 4: port-collision gate — different name claims argus's port 3000 ===
REG4="$TEST_ROOT/r4.yaml"
write_baseline_registry "$REG4"
BEFORE4=$(cat "$REG4")
run_register "$REG4" --name newthing --port 3000
assert_rc "collision_exit_nonzero" 2
assert_out_contains "collision_marker" "REGISTER-CONFLICT"
assert_out_contains "collision_names_argus" "argus"
# File must be UNCHANGED on conflict (no write).
if [ "$(cat "$REG4")" = "$BEFORE4" ]; then pass_case; else
  fail_case "collision_no_write (file changed despite conflict)"
fi

# === Test 5: same-name re-register to its own port is NOT a conflict ===
run_register "$REG4" --name argus --port 3000 --purpose "PR code review agent (PR-Agent + GPT-5.3-Codex)"
assert_rc "same_name_no_conflict" 0
assert_out_contains "same_name_ok" "REGISTER-OK argus"

# === Test 6: reserved-but-unowned port (22443 QNAP) blocks a new service ===
REG6="$TEST_ROOT/r6.yaml"
write_baseline_registry "$REG6"
run_register "$REG6" --name webui --port 22443
assert_rc "reserved_unowned_conflict" 2
assert_out_contains "reserved_unowned_marker" "REGISTER-CONFLICT"
assert_out_contains "reserved_unowned_inreserved" "reserved_ports"

# === Test 7: --containers auto-derive from docker (multi-container project) ===
REG7="$TEST_ROOT/r7.yaml"
write_baseline_registry "$REG7"
run_register "$REG7" --name sot-codex --port 8400
assert_rc "autoderive_ok" 0
# Stub returns sot-codex-tunnel-1 + sot-codex-app-1; rendered SORTED.
assert_file_contains "autoderive_containers" "containers: [sot-codex-app-1, sot-codex-tunnel-1]" "$REG7"

# === Test 8: upsert REPLACES an existing service subtree (not duplicate) ===
REG8="$TEST_ROOT/r8.yaml"
write_baseline_registry "$REG8"
# Re-register argus with a changed purpose; should replace, not append.
run_register "$REG8" --name argus --port 3000 --purpose "CHANGED PURPOSE TEXT" \
  --containers "argus,argus-tunnel"
assert_rc "replace_ok" 0
assert_file_contains "replace_new_purpose" "CHANGED PURPOSE TEXT" "$REG8"
# Exactly one `  argus:` service key (no duplication).
ARGUS_COUNT=$(grep -c '^  argus:' "$REG8")
CASES=$((CASES+1))
if [ "$ARGUS_COUNT" -eq 1 ]; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: replace_single_argus (expected 1 argus: key, got %d)\n' "$ARGUS_COUNT" >&2
fi
# ssh-tunnel sibling still present after replacing argus.
assert_file_contains "replace_sibling_ssh_kept" "  ssh-tunnel:" "$REG8"

# === Test 9: missing required --name / --port rejected ===
REG9="$TEST_ROOT/r9.yaml"
write_baseline_registry "$REG9"
run_register "$REG9" --port 9999
assert_rc "missing_name_err" 1
run_register "$REG9" --name onlyname
assert_rc "missing_port_err" 1

# === Test 10: a .bak timestamped backup is created on write ===
REG10="$TEST_ROOT/r10.yaml"
write_baseline_registry "$REG10"
run_register "$REG10" --name sot-codex --port 8400 --containers "sot-codex-app-1"
assert_rc "bak_write_ok" 0
CASES=$((CASES+1))
if [ -f "$REG10.bak-20260623-000000" ]; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: bak_created (%s.bak-20260623-000000 missing)\n' "$REG10" >&2
fi

# === Test 11: merge-upsert — partial re-register must NOT drop stored fields ===
# Register argus fully (baseline already has it), then re-register passing ONLY
# --name + --port. subdomain/url/compose/repo/cicd must all survive.
REG11="$TEST_ROOT/r11.yaml"
write_baseline_registry "$REG11"
run_register "$REG11" --name argus --port 3000
assert_rc "merge_partial_ok" 0
assert_file_contains "merge_keep_subdomain" "    subdomain: argus" "$REG11"
assert_file_contains "merge_keep_url"       "    url: https://argus.fyc-space.uk" "$REG11"
assert_file_contains "merge_keep_compose"   "    compose: /share/homes/392fyc/argus/docker-compose.yml" "$REG11"
assert_file_contains "merge_keep_repo"      "    repo: https://github.com/392fyc/Argus" "$REG11"
assert_file_contains "merge_keep_cicd"      "    cicd: GitHub Actions" "$REG11"
assert_file_contains "merge_keep_containers" "containers: [argus, argus-tunnel]" "$REG11"
# A new explicit value DOES override while the rest is retained.
run_register "$REG11" --name argus --port 3000 --subdomain newargus
assert_rc "merge_override_ok" 0
assert_file_contains "merge_override_subdomain" "    subdomain: newargus" "$REG11"
assert_file_contains "merge_override_keep_url" "    url: https://argus.fyc-space.uk" "$REG11"

# === Test 12: auto-derive idempotency under shuffled docker enumeration order ===
# Two docker stubs return the SAME container set in DIFFERENT orders. Registering
# sot-codex (no --containers) against each must yield a byte-identical file
# (containers[] is sorted, so order in docker output is irrelevant).
STUB_A="$TEST_ROOT/docker-A.sh"
STUB_B="$TEST_ROOT/docker-B.sh"
cat > "$STUB_A" <<'EOF'
#!/bin/sh
cmd="$1"; shift; [ "$cmd" = "ps" ] || exit 1
while [ $# -gt 0 ]; do case "$1" in --format) shift; shift ;; *) shift ;; esac; done
cat <<'ROWS'
sot-codex|sot-codex-app-1
sot-codex|sot-codex-tunnel-1
ROWS
EOF
cat > "$STUB_B" <<'EOF'
#!/bin/sh
cmd="$1"; shift; [ "$cmd" = "ps" ] || exit 1
while [ $# -gt 0 ]; do case "$1" in --format) shift; shift ;; *) shift ;; esac; done
cat <<'ROWS'
sot-codex|sot-codex-tunnel-1
sot-codex|sot-codex-app-1
ROWS
EOF
chmod +x "$STUB_A" "$STUB_B"
REG12A="$TEST_ROOT/r12a.yaml"; REG12B="$TEST_ROOT/r12b.yaml"
write_baseline_registry "$REG12A"; write_baseline_registry "$REG12B"
ld="$TEST_ROOT/lock-12a"
LAST_OUT=$(REGISTRY_FILE="$REG12A" REGISTRY_DOCKER="$STUB_A" REGISTRY_LOCKDIR="$ld" sh "$SCRIPT" --name sot-codex --port 8400 2>&1) && LAST_RC=0 || LAST_RC=$?
rmdir "$ld" 2>/dev/null || true
assert_rc "shuffle_a_ok" 0
ld="$TEST_ROOT/lock-12b"
LAST_OUT=$(REGISTRY_FILE="$REG12B" REGISTRY_DOCKER="$STUB_B" REGISTRY_LOCKDIR="$ld" sh "$SCRIPT" --name sot-codex --port 8400 2>&1) && LAST_RC=0 || LAST_RC=$?
rmdir "$ld" 2>/dev/null || true
assert_rc "shuffle_b_ok" 0
CASES=$((CASES+1))
if [ "$(cat "$REG12A")" = "$(cat "$REG12B")" ]; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: autoderive_byte_identical (shuffled docker order changed the file)\n' >&2
  [ "$VERBOSE" = "1" ] && diff "$REG12A" "$REG12B" >&2
fi
# And the sorted rendering is deterministic.
assert_file_contains "shuffle_sorted" "containers: [sot-codex-app-1, sot-codex-tunnel-1]" "$REG12A"

# === Test 13: precise sibling preservation — replacing a MIDDLE service leaves
# the bytes before/after it (blank separators, inline comments, neighbor blocks)
# exactly intact, not merely "still grep-able". ===
REG13="$TEST_ROOT/r13.yaml"
cat > "$REG13" <<'EOF'
# NAS Service Registry
domain: fyc-space.uk
tunnel: argus

services:
  alpha:
    port: 1000
    purpose: first service

  # a standalone comment that separates beta from alpha
  beta:
    port: 2000
    purpose: middle service to be replaced

  gamma:
    port: 3000
    purpose: last service

reserved_ports:
  - 1000  # alpha
  - 2000  # beta
  - 3000  # gamma
EOF
run_register "$REG13" --name beta --port 2000 --purpose "REPLACED middle"
assert_rc "sibling_replace_ok" 0
# alpha block byte-for-byte intact (key + child + trailing blank + the comment).
EXPECT_ALPHA=$(printf '  alpha:\n    port: 1000\n    purpose: first service\n\n  # a standalone comment that separates beta from alpha')
CASES=$((CASES+1))
if printf '%s' "$(cat "$REG13")" | tr '\n' '\001' | grep -qF -- "$(printf '%s' "$EXPECT_ALPHA" | tr '\n' '\001')"; then
  PASS=$((PASS+1))
else
  FAIL=$((FAIL+1)); printf 'FAIL: sibling_alpha_and_comment_exact (separator/comment before replaced service was altered)\n' >&2
  [ "$VERBOSE" = "1" ] && cat "$REG13" >&2
fi
# gamma block (the service AFTER the replaced one) byte-for-byte intact, incl.
# the blank line that separates it from beta.
EXPECT_GAMMA=$(printf '\n  gamma:\n    port: 3000\n    purpose: last service\n')
CASES=$((CASES+1))
if printf '%s' "$(cat "$REG13")" | tr '\n' '\001' | grep -qF -- "$(printf '%s' "$EXPECT_GAMMA" | tr '\n' '\001')"; then
  PASS=$((PASS+1))
else
  FAIL=$((FAIL+1)); printf 'FAIL: sibling_gamma_exact (separator/neighbor after replaced service was altered)\n' >&2
  [ "$VERBOSE" = "1" ] && cat "$REG13" >&2
fi
assert_file_contains "sibling_beta_replaced" "    purpose: REPLACED middle" "$REG13"

# === Test 14: free-form escaping — a --purpose carrying YAML-hazard chars (#)
# must not break the file and must read back intact via the lib reader. ===
REG14="$TEST_ROOT/r14.yaml"
write_baseline_registry "$REG14"
HAZ='deploy #42: needs "review" & care'
run_register "$REG14" --name sot-codex --port 8400 --containers "sot-codex-app-1" --purpose "$HAZ"
assert_rc "escape_ok" 0
# The value must be double-quoted (it carries a '#').
assert_file_contains "escape_quoted" '    purpose: "deploy #42:' "$REG14"
# Read it back through the same lib reader the rest of the suite uses.
READBACK=$(REGISTRY_FILE="$REG14" sh -c '. "'"$REPO_ROOT"'/scripts/lib/registry-sh.sh"; reg_get_field sot-codex purpose')
CASES=$((CASES+1))
if [ "$READBACK" = "$HAZ" ]; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: escape_readback (got: %s)\n' "$READBACK" >&2
fi
# The file must still parse cleanly: argus sibling + reserved_ports intact.
assert_file_contains "escape_file_intact" "  argus:" "$REG14"
assert_file_contains "escape_reserved_intact" "- 3000  # argus" "$REG14"

# === Test 15: a flag value with an embedded newline is REJECTED (no write) ===
REG15="$TEST_ROOT/r15.yaml"
write_baseline_registry "$REG15"
BEFORE15=$(cat "$REG15")
NL=$(printf 'line1\nline2')
run_register "$REG15" --name sot-codex --port 8400 --purpose "$NL"
assert_rc "newline_rejected" 1
CASES=$((CASES+1))
if [ "$(cat "$REG15")" = "$BEFORE15" ]; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: newline_no_write (file changed despite rejected newline value)\n' >&2
fi

printf '\n=== register-service test summary ===\n'
printf 'cases: %d  pass: %d  fail: %d\n' "$CASES" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
