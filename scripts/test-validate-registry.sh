#!/bin/sh
# scripts/test-validate-registry.sh — fixture-driven tests for
# scripts/validate-registry.sh (Issue #163). No real NAS / no real docker.
#
# REGISTRY_DOCKER is pointed at a stub that prints the GROUND-TRUTH `docker ps`
# fixture captured from the NAS on 2026-06-23. REGISTRY_FILE points at a temp
# copy of the GROUND-TRUTH services.yaml baseline. Run with BusyBox-compatible
# sh (this file uses no bashisms).
#
# Usage: sh scripts/test-validate-registry.sh [--verbose]
# Exit: 0 all pass / 1 any fail

set -u

VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || { printf 'test: must run inside a git repo\n' >&2; exit 1; }
SCRIPT="$REPO_ROOT/scripts/validate-registry.sh"
[ -f "$SCRIPT" ] || { printf 'test: %s missing\n' "$SCRIPT" >&2; exit 1; }

PASS=0; FAIL=0; CASES=0
TEST_ROOT=$(mktemp -d) || { printf 'test: mktemp -d failed\n' >&2; exit 1; }
trap 'rm -rf "$TEST_ROOT"' EXIT

# --- GROUND-TRUTH services.yaml baseline (verbatim from #163 task brief) ---
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

# --- GROUND-TRUTH docker ps fixture stub ---
# The stub mirrors `docker ps --format '<template>'`. Our validate script always
# calls `ps --format '{{.Names}}|{{...}}|{{...}}|{{.Ports}}'`. The stub ignores
# the exact template and prints the captured 4-field pipe rows. It also handles
# the `--format '{{.Label ...project}}|{{.Names}}'` 2-field form used by the
# register auto-derive path (not exercised here, but kept consistent).
make_docker_stub() {
  stub="$1"
  cat > "$stub" <<'EOF'
#!/bin/sh
# Fixture docker stub. Recognizes the two --format shapes our scripts use.
cmd="$1"; shift
[ "$cmd" = "ps" ] || { echo "stub: unsupported docker subcommand: $cmd" >&2; exit 1; }
fmt=""
while [ $# -gt 0 ]; do
  case "$1" in
    --format) shift; fmt="$1"; shift ;;
    *) shift ;;
  esac
done
case "$fmt" in
  *Ports*)
    # 4-field: Names|project|working_dir|Ports
    cat <<'ROWS'
sot-codex-tunnel-1|sot-codex|/share/CACHEDEV1_DATA/homes/392fyc/sot-codex|
sot-codex-app-1|sot-codex|/share/CACHEDEV1_DATA/homes/392fyc/sot-codex|0.0.0.0:8400->8000/tcp
argus-selfcheck-scheduler|argus|/share/homes/392fyc/argus|
argus|argus|/share/homes/392fyc/argus|0.0.0.0:3000->3000/tcp
argus-tunnel|argus|/share/homes/392fyc/argus|
ROWS
    ;;
  *)
    # 2-field project|Names (register auto-derive)
    cat <<'ROWS'
sot-codex|sot-codex-tunnel-1
sot-codex|sot-codex-app-1
argus|argus-selfcheck-scheduler
argus|argus
argus|argus-tunnel
ROWS
    ;;
esac
EOF
  chmod +x "$stub"
}

# Run validate with a given registry + docker stub, capturing rc/out.
run_validate() {
  reg="$1"; stub="$2"
  LAST_OUT=$(REGISTRY_FILE="$reg" REGISTRY_DOCKER="$stub" sh "$SCRIPT" 2>/dev/null) && LAST_RC=0 || LAST_RC=$?
  [ "$VERBOSE" = "1" ] && printf '--- validate rc=%d ---\n%s\n' "$LAST_RC" "$LAST_OUT"
}

assert_rc() {
  CASES=$((CASES+1))
  if [ "$LAST_RC" = "$2" ]; then PASS=$((PASS+1)); else
    FAIL=$((FAIL+1)); printf 'FAIL: %s (expected rc=%s got %s)\n' "$1" "$2" "$LAST_RC" >&2
  fi
}
assert_out_contains() {
  CASES=$((CASES+1))
  if printf '%s' "$LAST_OUT" | grep -qF -- "$2"; then PASS=$((PASS+1)); else
    FAIL=$((FAIL+1)); printf 'FAIL: %s (stdout missing: %s)\n' "$1" "$2" >&2
  fi
}
assert_out_NOT_contains() {
  CASES=$((CASES+1))
  if printf '%s' "$LAST_OUT" | grep -qF -- "$2"; then
    FAIL=$((FAIL+1)); printf 'FAIL: %s (stdout unexpectedly contains: %s)\n' "$1" "$2" >&2
  else PASS=$((PASS+1)); fi
}

STUB="$TEST_ROOT/docker-stub.sh"
make_docker_stub "$STUB"

# === Test 1: baseline registry vs ground-truth docker → drift, 3 core findings ===
REG1="$TEST_ROOT/services1.yaml"
write_baseline_registry "$REG1"
run_validate "$REG1" "$STUB"
assert_rc "drift_exit_nonzero" 1
assert_out_contains "drift_marker" "VALIDATE-DRIFT"
# Core assertion 1: sot-codex project not in registry.
assert_out_contains "unregistered_sot_codex" "[UNREGISTERED-PROJECT]"
assert_out_contains "unregistered_sot_codex_name" "sot-codex"
# Core assertion 2: argus containers[] missing argus-selfcheck-scheduler.
assert_out_contains "container_set_drift" "[CONTAINER-SET-DRIFT]"
assert_out_contains "container_set_drift_selfcheck" "argus-selfcheck-scheduler"
# Core assertion 3: host port 8400 not reserved.
assert_out_contains "unreserved_8400" "[UNRESERVED-PORT]"
assert_out_contains "unreserved_8400_port" "8400"

# === Test 2: argus port 3000 IS reserved → must NOT be flagged unreserved ===
assert_out_NOT_contains "port_3000_not_flagged" "host port 3000 (project"

# === Test 3: ssh-tunnel registered but not running → NOT-RUNNING, not removed ===
# ssh-tunnel (port 22) has no container in the docker fixture.
assert_out_contains "ssh_not_running" "[NOT-RUNNING]"
assert_out_contains "ssh_not_running_name" "ssh-tunnel"
# Restart-transient guard wording present (not treated as removed).
assert_out_contains "not_running_transient_note" "restart-transient"

# === Test 4: out-of-scope advisory for subdomain/url ===
assert_out_contains "oos_advisory" "[OUT-OF-SCOPE-UNVERIFIABLE]"

# === Test 5: clean registry (matches docker exactly) → VALIDATE-OK exit 0 ===
# Build a registry that fully matches the docker fixture: argus (3 containers),
# sot-codex (2 containers, port 8400) registered; reserved_ports includes both.
# Drop ssh-tunnel (not running → would be NOT-RUNNING noise).
REG5="$TEST_ROOT/services5.yaml"
cat > "$REG5" <<'EOF'
# NAS Service Registry
domain: fyc-space.uk
tunnel: argus

services:
  argus:
    port: 3000
    subdomain: argus
    url: https://argus.fyc-space.uk
    containers: [argus, argus-tunnel, argus-selfcheck-scheduler]
    purpose: PR review
  sot-codex:
    port: 8400
    subdomain: sot
    url: https://sot.fyc-space.uk
    containers: [sot-codex-tunnel-1, sot-codex-app-1]
    purpose: SoT codex

reserved_ports:
  - 3000  # argus
  - 8400  # sot-codex
EOF
run_validate "$REG5" "$STUB"
assert_rc "clean_exit_zero" 0
assert_out_contains "clean_marker" "VALIDATE-OK"
assert_out_NOT_contains "clean_no_drift_marker" "VALIDATE-DRIFT"

# === Test 6: --registry flag overrides REGISTRY_FILE selection ===
LAST_OUT=$(REGISTRY_DOCKER="$STUB" sh "$SCRIPT" --registry "$REG5" 2>/dev/null) && LAST_RC=0 || LAST_RC=$?
assert_rc "registry_flag" 0
assert_out_contains "registry_flag_ok" "VALIDATE-OK"

# === Test 7: missing registry file → error exit ===
LAST_OUT=$(REGISTRY_DOCKER="$STUB" sh "$SCRIPT" --registry "$TEST_ROOT/nope.yaml" 2>/dev/null) && LAST_RC=0 || LAST_RC=$?
assert_rc "missing_registry_err" 1

printf '\n=== validate-registry test summary ===\n'
printf 'cases: %d  pass: %d  fail: %d\n' "$CASES" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
