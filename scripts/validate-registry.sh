#!/bin/sh
# scripts/validate-registry.sh — read-only reconciliation / drift detector for
# the NAS service registry (Issue #163). NEVER writes services.yaml.
#
# Pull model: enumerate what docker is ACTUALLY running, compare against the
# hand-maintained services.yaml registry, and FLAG every divergence. This is
# the half of #163 that a deploy-time push (register-service.sh) structurally
# cannot cover — silent drift only shows up when you scan the live fleet.
#
# Pure BusyBox sh. No jq (docker consumed via Go-template --format). No yq
# (services.yaml read via lib sed/awk). docker is parameterized via
# REGISTRY_DOCKER; tests inject a fixture-printing stub.
#
# Usage:
#   scripts/validate-registry.sh [--registry PATH] [--quiet]
#
# Env (see lib/registry-sh.sh):
#   REGISTRY_FILE    services.yaml path  (or --registry)
#   REGISTRY_DOCKER  docker command prefix (default = NAS sudo+system-docker)
#
# Output:
#   No drift  -> exit 0, stdout "VALIDATE-OK"
#   Any drift -> exit 1, stdout "VALIDATE-DRIFT" + one FINDING line per issue,
#                each tagged with a category:
#     [UNREGISTERED-PROJECT]  running compose project absent from registry
#     [CONTAINER-SET-DRIFT]   registry containers[] != actually-running set
#     [UNRESERVED-PORT]       running host port not in reserved_ports
#     [PORT-CONFLICT]         same host port claimed by >1 compose project
#     [NOT-RUNNING]           registry service has no running containers
#                             (distinct from removed — see note below)
#
# Scope note: subdomain / url route through a token-based cloudflared tunnel
# with no on-NAS ingress config to diff against, so those columns are
# [OUT-OF-SCOPE-UNVERIFIABLE] — printed for visibility, never reconciled.
#
# Restart-transient guard: NOT-RUNNING flags registry services with zero live
# containers as "registered but not running", NOT as "removed". A container
# bouncing during a restart must not be mistaken for a deletion, so this is an
# advisory finding, not an error of registry incorrectness.

set -u

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SELF_DIR/lib/registry-sh.sh"

QUIET=0
while [ $# -gt 0 ]; do
  case "$1" in
    --registry) shift; [ $# -gt 0 ] || reg_die "--registry needs a value"
                REGISTRY_FILE="$1"; shift ;;
    --quiet)    QUIET=1; shift ;;
    -h|--help)  sed -n '2,46p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)         reg_die "unknown flag: $1" ;;
    *)          reg_die "unexpected argument: $1" ;;
  esac
done

[ -f "$REGISTRY_FILE" ] || reg_die "registry file not found: $REGISTRY_FILE (set --registry or REGISTRY_FILE)"

# ---------------------------------------------------------------------------
# Collect live docker state.
# Format fields, pipe-delimited (NOT JSON — no jq on NAS):
#   Names | compose project | compose working_dir | Ports
# config_files label may be comma-separated (multi-file compose); we capture
# working_dir as the project anchor and config_files separately for the doc's
# derivability matrix. Ports field holds the "0.0.0.0:HOST->CONTAINER/proto"
# publish spec(s).
# ---------------------------------------------------------------------------
DOCKER_PS=$(reg_docker ps --format '{{.Names}}|{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.project.working_dir"}}|{{.Ports}}') \
  || reg_die "docker ps failed (REGISTRY_DOCKER=$REGISTRY_DOCKER)"

FINDINGS=$(mktemp "${TMPDIR:-/tmp}/registry-find.XXXXXX") || reg_die "mktemp failed (findings)"
trap 'rm -f "$FINDINGS"' EXIT

finding() { printf '%s\n' "$1" >> "$FINDINGS"; }

# Working tables, derived from DOCKER_PS via awk/sed (no jq):
#   RUNNING_PROJECTS  unique compose project names that have ≥1 live container
#   RUNNING_CONTS     "project<TAB>container" for the actual running set
#   RUNNING_PORTS     "hostport<TAB>project" for every published host port
RUNNING_CONTS=$(printf '%s\n' "$DOCKER_PS" | awk -F'|' '
  NF >= 2 && $2 != "" { printf "%s\t%s\n", $2, $1 }
')
RUNNING_PROJECTS=$(printf '%s\n' "$RUNNING_CONTS" | awk -F'\t' '{print $1}' | sort -u | sed '/^$/d')

# Extract every "->HOST->CONTAINER" host port. Ports field can carry several
# comma-separated mappings; pull each "0.0.0.0:NNNN->" host port.
RUNNING_PORTS=$(printf '%s\n' "$DOCKER_PS" | awk -F'|' '
  NF >= 4 && $4 != "" {
    proj = $2
    s = $4
    # Walk the field finding ":<digits>->" host-port tokens.
    while (match(s, /:[0-9]+->/)) {
      tok = substr(s, RSTART + 1, RLENGTH - 3)   # strip leading ":" + trailing "->"
      printf "%s\t%s\n", tok, proj
      s = substr(s, RSTART + RLENGTH)
    }
  }
')

# ---------------------------------------------------------------------------
# (a) UNREGISTERED-PROJECT: running compose project not present in registry.
# ---------------------------------------------------------------------------
REG_SERVICES=$(reg_list_services)
printf '%s\n' "$RUNNING_PROJECTS" | sed '/^$/d' | while IFS= read -r proj; do
  if ! printf '%s\n' "$REG_SERVICES" | grep -qx -- "$proj"; then
    finding "[UNREGISTERED-PROJECT] compose project '$proj' is running but absent from registry (services.<name> missing)"
  fi
done

# ---------------------------------------------------------------------------
# (b) CONTAINER-SET-DRIFT: registry containers[] != actual running container
#     set for a registered+running project.
# (e) NOT-RUNNING: registered project with zero live containers (advisory;
#     could be a restart transient, NOT necessarily removed).
# ---------------------------------------------------------------------------
printf '%s\n' "$REG_SERVICES" | sed '/^$/d' | while IFS= read -r svc; do
  actual=$(printf '%s\n' "$RUNNING_CONTS" | awk -F'\t' -v p="$svc" '$1==p {print $2}' | sort -u | sed '/^$/d')
  declared=$(reg_service_containers "$svc" | sort -u | sed '/^$/d')

  if [ -z "$actual" ]; then
    # NOT-RUNNING is only meaningful for docker-backed services: those that
    # declare a `containers:` list or a `compose:` file in the registry. A
    # service with NEITHER (e.g. ssh-tunnel = the system sshd on :22 fronted by
    # cloudflared — not a docker workload) is not something docker ps can ever
    # show, so flagging it NOT-RUNNING is permanent false-positive cron noise.
    # Exempt it: validate manages docker-backed run-state only.
    svc_compose=$(reg_get_field "$svc" compose)
    if [ -z "$declared" ] && [ -z "$svc_compose" ]; then
      continue
    fi
    finding "[NOT-RUNNING] registry service '$svc' has no running containers (registered-but-down; restart-transient — NOT auto-treated as removed)"
    continue
  fi

  # Set difference via temp files + awk (no process substitution — that's a
  # bashism). missing = declared but not running; extra = running but not declared.
  printf '%s\n' "$declared" > "$FINDINGS.decl"
  printf '%s\n' "$actual"   > "$FINDINGS.run"
  missing=$(awk 'NR==FNR{r[$0]=1; next} $0!="" && !($0 in r)' "$FINDINGS.run" "$FINDINGS.decl")
  extra=$(awk 'NR==FNR{d[$0]=1; next} $0!="" && !($0 in d)' "$FINDINGS.decl" "$FINDINGS.run")
  rm -f "$FINDINGS.decl" "$FINDINGS.run"

  if [ -n "$declared" ] && [ -n "$missing" ]; then
    finding "[CONTAINER-SET-DRIFT] service '$svc' registry declares containers not running: $(printf '%s' "$missing" | tr '\n' ' ')"
  fi
  if [ -n "$extra" ]; then
    if [ -z "$declared" ]; then
      finding "[CONTAINER-SET-DRIFT] service '$svc' has no containers[] in registry but runs: $(printf '%s' "$extra" | tr '\n' ' ')"
    else
      finding "[CONTAINER-SET-DRIFT] service '$svc' runs containers not in registry containers[]: $(printf '%s' "$extra" | tr '\n' ' ')"
    fi
  fi
done

# ---------------------------------------------------------------------------
# (c) UNRESERVED-PORT: running host port absent from reserved_ports.
# (d) PORT-CONFLICT: same host port claimed by >1 distinct compose project.
# ---------------------------------------------------------------------------
RESERVED=$(reg_list_reserved_ports | sort -u | sed '/^$/d')

printf '%s\n' "$RUNNING_PORTS" | sed '/^$/d' | awk -F'\t' '{print $1}' | sort -u | while IFS= read -r port; do
  [ -n "$port" ] || continue
  if ! printf '%s\n' "$RESERVED" | grep -qx -- "$port"; then
    owner=$(printf '%s\n' "$RUNNING_PORTS" | awk -F'\t' -v p="$port" '$1==p {print $2; exit}')
    finding "[UNRESERVED-PORT] host port $port (project '$owner') is published but not in reserved_ports"
  fi
done

# Conflict: a host port mapped by more than one distinct project.
printf '%s\n' "$RUNNING_PORTS" | sed '/^$/d' | sort -u | awk -F'\t' '
  { count[$1]++; owners[$1] = owners[$1] " " $2 }
  END {
    for (p in count) if (count[p] > 1) printf "[PORT-CONFLICT]\t%s\t%s\n", p, owners[p]
  }
' | while IFS="$(printf '\t')" read -r tag port owners; do
  finding "$tag host port$([ -n "$port" ] && printf ' %s' "$port") claimed by multiple projects:$owners"
done

# ---------------------------------------------------------------------------
# Out-of-scope advisory (always printed unless --quiet): subdomain/url are not
# reconcilable because cloudflared uses a token-based tunnel with no on-NAS
# ingress map to diff against.
# ---------------------------------------------------------------------------
if [ "$QUIET" = "0" ]; then
  printf '[OUT-OF-SCOPE-UNVERIFIABLE] subdomain/url reconciliation skipped — token-based cloudflared tunnel has no on-NAS ingress config to diff.\n'
fi

# ---------------------------------------------------------------------------
# Report.
# ---------------------------------------------------------------------------
if [ -s "$FINDINGS" ]; then
  printf 'VALIDATE-DRIFT\n'
  sort -u "$FINDINGS"
  exit 1
else
  printf 'VALIDATE-OK\n'
  exit 0
fi
