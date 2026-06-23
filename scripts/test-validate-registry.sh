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

# === Test 3: ssh-tunnel is a NON-docker service (no containers[]/compose) →
# EXEMPT from NOT-RUNNING. docker ps can never show the system sshd, so flagging
# it would be permanent cron false-positive noise. It must NOT be flagged. ===
assert_out_NOT_contains "ssh_exempt_not_running_name" "service 'ssh-tunnel' has no running"

# === Test 4: out-of-scope advisory for subdomain/url ===
assert_out_contains "oos_advisory" "[OUT-OF-SCOPE-UNVERIFIABLE]"

# === Test 5: clean registry that INCLUDES the non-docker ssh-tunnel service →
# VALIDATE-OK exit 0. This is the regression the #163 dual-verify caught: a
# registry carrying ssh-tunnel (no containers[]/compose) with NO other real
# drift must reconcile clean, not loop on permanent NOT-RUNNING noise. ===
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
  ssh-tunnel:
    port: 22
    subdomain: ssh
    url: ssh.fyc-space.uk
    purpose: SSH access via Cloudflare Tunnel (for CI/CD)

reserved_ports:
  - 3000  # argus
  - 8400  # sot-codex
  - 22    # ssh
EOF
run_validate "$REG5" "$STUB"
assert_rc "clean_exit_zero" 0
assert_out_contains "clean_marker" "VALIDATE-OK"
assert_out_NOT_contains "clean_no_drift_marker" "VALIDATE-DRIFT"
# ssh-tunnel present in the clean registry must NOT be flagged NOT-RUNNING.
assert_out_NOT_contains "clean_ssh_exempt" "[NOT-RUNNING]"

# === Test 5b: a DOCKER-BACKED service that is fully down (declares containers[]
# but none are running) DOES trigger NOT-RUNNING + exit 1 — that's real drift,
# distinct from the ssh-tunnel exemption above. ===
REG5B="$TEST_ROOT/services5b.yaml"
cat > "$REG5B" <<'EOF'
# NAS Service Registry
domain: fyc-space.uk
tunnel: argus

services:
  argus:
    port: 3000
    containers: [argus, argus-tunnel, argus-selfcheck-scheduler]
    purpose: PR review
  sot-codex:
    port: 8400
    containers: [sot-codex-tunnel-1, sot-codex-app-1]
    purpose: SoT codex
  ghost:
    port: 9999
    compose: /share/homes/392fyc/ghost/docker-compose.yml
    containers: [ghost-app, ghost-db]
    purpose: docker service that is fully down

reserved_ports:
  - 3000  # argus
  - 8400  # sot-codex
  - 9999  # ghost
EOF
run_validate "$REG5B" "$STUB"
assert_rc "docker_down_exit_one" 1
assert_out_contains "docker_down_not_running" "[NOT-RUNNING]"
assert_out_contains "docker_down_ghost_name" "ghost"

# === Test 6: --registry flag overrides REGISTRY_FILE selection ===
LAST_OUT=$(REGISTRY_DOCKER="$STUB" sh "$SCRIPT" --registry "$REG5" 2>/dev/null) && LAST_RC=0 || LAST_RC=$?
assert_rc "registry_flag" 0
assert_out_contains "registry_flag_ok" "VALIDATE-OK"

# === Test 7: missing registry file → error exit ===
LAST_OUT=$(REGISTRY_DOCKER="$STUB" sh "$SCRIPT" --registry "$TEST_ROOT/nope.yaml" 2>/dev/null) && LAST_RC=0 || LAST_RC=$?
assert_rc "missing_registry_err" 1

# === Test 8 (finding 8): service / port membership must use FIXED-STRING match,
# not regex. A running compose project whose name contains regex metachars (`.`)
# must be reported UNREGISTERED when it is genuinely absent — a `grep -x` (regex)
# could let a metachar accidentally match a different registered name. We use a
# project name `a.c` and a registry that contains only `abc`: with regex `-x`,
# `a.c` would match the line `abc` and the drift would be MISSED. ===
REGEX_STUB="$TEST_ROOT/docker-regex.sh"
cat > "$REGEX_STUB" <<'EOF'
#!/bin/sh
cmd="$1"; shift
[ "$cmd" = "ps" ] || { echo "stub: unsupported: $cmd" >&2; exit 1; }
fmt=""
while [ $# -gt 0 ]; do case "$1" in --format) shift; fmt="$1"; shift ;; *) shift ;; esac; done
case "$fmt" in
  *Ports*)
    cat <<'ROWS'
a.c-app|a.c|/share/homes/392fyc/adotc|0.0.0.0:7100->7000/tcp
ROWS
    ;;
  *)
    cat <<'ROWS'
a.c|a.c-app
ROWS
    ;;
esac
EOF
chmod +x "$REGEX_STUB"
REG8="$TEST_ROOT/services8.yaml"
cat > "$REG8" <<'EOF'
# NAS Service Registry
domain: fyc-space.uk
tunnel: argus

services:
  abc:
    port: 7000
    containers: [abc-app]
    purpose: decoy whose name regex-matches a.c

reserved_ports:
  - 7000  # abc
EOF
run_validate "$REG8" "$REGEX_STUB"
# Running project `a.c` is genuinely absent from the registry (only `abc` exists).
# With fixed-string matching it MUST be flagged UNREGISTERED.
assert_rc "regex_membership_drift" 1
assert_out_contains "regex_membership_unregistered" "[UNREGISTERED-PROJECT]"
assert_out_contains "regex_membership_proj_name" "'a.c'"

# === Test 9 (finding 9): reg_get_field must read a service whose KEY LINE carries
# an inline `# comment` — reg_list_services already accepts such keys, so the two
# must agree on what a service key line is. ===
REG9="$TEST_ROOT/services9.yaml"
cat > "$REG9" <<'EOF'
# NAS Service Registry
domain: fyc-space.uk
tunnel: argus

services:
  argus:  # inline comment on the service key line
    port: 3000
    subdomain: argus
    containers: [argus, argus-tunnel]
    purpose: PR review

reserved_ports:
  - 3000  # argus
EOF
GET_PORT=$(REGISTRY_FILE="$REG9" sh -c '. "'"$REPO_ROOT"'/scripts/lib/registry-sh.sh"; reg_get_field argus port')
CASES=$((CASES+1))
if [ "$GET_PORT" = "3000" ]; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: getfield_inline_comment_key_port (expected 3000, got "%s")\n' "$GET_PORT" >&2
fi
GET_SUB=$(REGISTRY_FILE="$REG9" sh -c '. "'"$REPO_ROOT"'/scripts/lib/registry-sh.sh"; reg_get_field argus subdomain')
CASES=$((CASES+1))
if [ "$GET_SUB" = "argus" ]; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: getfield_inline_comment_key_subdomain (expected argus, got "%s")\n' "$GET_SUB" >&2
fi
# And the list helper still lists it (parity check).
LISTED=$(REGISTRY_FILE="$REG9" sh -c '. "'"$REPO_ROOT"'/scripts/lib/registry-sh.sh"; reg_list_services')
CASES=$((CASES+1))
if printf '%s\n' "$LISTED" | grep -qxF 'argus'; then PASS=$((PASS+1)); else
  FAIL=$((FAIL+1)); printf 'FAIL: getfield_inline_comment_key_listed (argus not listed)\n' >&2
fi

printf '\n=== validate-registry test summary ===\n'
printf 'cases: %d  pass: %d  fail: %d\n' "$CASES" "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
