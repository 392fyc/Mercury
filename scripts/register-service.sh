#!/bin/sh
# scripts/register-service.sh — deploy-time push writer for the NAS service
# registry (Issue #163). Idempotently upserts one services.<name> entry,
# reserves its host port, and timestamp-backs-up services.yaml before writing.
#
# This is the PUSH half of #163: a deploy step calls this to keep the registry
# current at the moment a service lands. validate-registry.sh is the PULL half
# that catches anything the push missed.
#
# Pure BusyBox sh. No jq / yq. docker (for --containers auto-derive) is
# parameterized via REGISTRY_DOCKER; the registry write goes through
# lib/registry-sh.sh comment-preserving block-splice.
#
# Usage:
#   scripts/register-service.sh --name NAME --port PORT [options]
#
#   --name NAME          (required) service key under services:
#   --port PORT          (required) published host port
#   --subdomain SUB      cloudflared subdomain (human-only; not derivable)
#   --url URL            public url (human-only)
#   --compose PATH       compose file path
#   --containers "a,b"   comma- or space-separated container names. When omitted,
#                        derived from running docker for this project (root fix
#                        for stale hand-typed lists).
#   --purpose TEXT       human description (human-only)
#   --repo URL           source repo (human-only)
#   --cicd TEXT          CI/CD note (human-only)
#   --registry PATH      services.yaml path (or REGISTRY_FILE env)
#
# Port-collision gate: if --port is already claimed by a DIFFERENT service name
# or appears in reserved_ports owned by another service, exit non-zero with
# REGISTER-CONFLICT and write nothing. Re-registering the SAME name with its
# existing port is idempotent and allowed.
#
# Output:
#   success  -> exit 0, stdout "REGISTER-OK <name>"
#   conflict -> exit 2, stdout "REGISTER-CONFLICT ..."

set -u

SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SELF_DIR/lib/registry-sh.sh"

NAME=""; PORT=""; SUBDOMAIN=""; URL=""; COMPOSE=""; CONTAINERS=""
PURPOSE=""; REPO=""; CICD=""

while [ $# -gt 0 ]; do
  case "$1" in
    --name)       shift; NAME="${1:-}"; shift ;;
    --port)       shift; PORT="${1:-}"; shift ;;
    --subdomain)  shift; SUBDOMAIN="${1:-}"; shift ;;
    --url)        shift; URL="${1:-}"; shift ;;
    --compose)    shift; COMPOSE="${1:-}"; shift ;;
    --containers) shift; CONTAINERS="${1:-}"; shift ;;
    --purpose)    shift; PURPOSE="${1:-}"; shift ;;
    --repo)       shift; REPO="${1:-}"; shift ;;
    --cicd)       shift; CICD="${1:-}"; shift ;;
    --registry)   shift; REGISTRY_FILE="${1:-}"; shift ;;
    -h|--help)    sed -n '2,44p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*)           reg_die "unknown flag: $1" ;;
    *)            reg_die "unexpected argument: $1" ;;
  esac
done

[ -n "$NAME" ] || reg_die "--name is required"
[ -n "$PORT" ] || reg_die "--port is required"
case "$PORT" in ''|*[!0-9]*) reg_die "--port must be numeric (got '$PORT')" ;; esac
case "$NAME" in *[!A-Za-z0-9._-]*) reg_die "--name must match [A-Za-z0-9._-] (got '$NAME')" ;; esac
[ -f "$REGISTRY_FILE" ] || reg_die "registry file not found: $REGISTRY_FILE (set --registry or REGISTRY_FILE)"

# Serialize concurrent registrations.
reg_lock

# ---------------------------------------------------------------------------
# Port-collision gate. The port belongs to NAME if NAME already declares it.
# Reject when a DIFFERENT service declares this port, or when it's reserved but
# our name doesn't currently own it (and isn't being (re)registered to it).
# Same-name re-register is idempotent → allowed.
# ---------------------------------------------------------------------------
EXISTING_PORT=$(reg_get_field "$NAME" port)

for svc in $(reg_list_services); do
  [ "$svc" = "$NAME" ] && continue
  other_port=$(reg_get_field "$svc" port)
  if [ -n "$other_port" ] && [ "$other_port" = "$PORT" ]; then
    printf 'REGISTER-CONFLICT host port %s already claimed by service "%s" (refusing to register "%s")\n' \
      "$PORT" "$svc" "$NAME"
    exit 2
  fi
done

# reserved_ports collision: a reserved port not currently owned by NAME and not
# the port NAME is already registered with. New services taking a port that is
# reserved-but-unowned is a conflict (a non-service reservation like QNAP Web UI
# 22443, or a leftover). Same-name idempotent re-register keeps its own port.
if [ "$EXISTING_PORT" != "$PORT" ]; then
  if reg_list_reserved_ports | grep -qx -- "$PORT"; then
    # Is this reserved port owned by some OTHER service?
    owner=""
    for svc in $(reg_list_services); do
      if [ "$(reg_get_field "$svc" port)" = "$PORT" ]; then owner="$svc"; break; fi
    done
    if [ "$owner" != "$NAME" ]; then
      printf 'REGISTER-CONFLICT host port %s is already in reserved_ports%s (refusing to register "%s")\n' \
        "$PORT" "$([ -n "$owner" ] && printf ' (owned by %s)' "$owner" || printf ' (reserved, no owning service)')" "$NAME"
      exit 2
    fi
  fi
fi

# ---------------------------------------------------------------------------
# Derive --containers from running docker when omitted (root fix for stale
# hand-maintained lists). Normalize comma/space separated input to a clean
# "[a, b, c]" rendering either way.
# ---------------------------------------------------------------------------
if [ -z "$CONTAINERS" ]; then
  CONTAINERS=$(reg_docker ps --format '{{.Label "com.docker.compose.project"}}|{{.Names}}' 2>/dev/null \
    | awk -F'|' -v p="$NAME" '$1==p {print $2}' | sed '/^$/d' | paste -sd, - 2>/dev/null)
  # paste may be absent on some BusyBox builds — fall back to tr+sed.
  if [ -z "$CONTAINERS" ]; then
    CONTAINERS=$(reg_docker ps --format '{{.Label "com.docker.compose.project"}}|{{.Names}}' 2>/dev/null \
      | awk -F'|' -v p="$NAME" '$1==p {print $2}' | sed '/^$/d' | tr '\n' ',' | sed 's/,$//')
  fi
fi
# Normalize separators to ", " and build a YAML flow-sequence.
CONT_LIST=""
if [ -n "$CONTAINERS" ]; then
  CONT_LIST=$(printf '%s' "$CONTAINERS" | tr ',' ' ' | tr -s ' ' \
    | sed 's/^ *//; s/ *$//' | tr ' ' '\n' | sed '/^$/d' | paste -sd, - 2>/dev/null)
  if [ -z "$CONT_LIST" ]; then
    CONT_LIST=$(printf '%s' "$CONTAINERS" | tr ',' ' ' | tr -s ' ' \
      | sed 's/^ *//; s/ *$//' | tr ' ' ',')
  fi
  # Add space after each comma for readability: a,b -> a, b
  CONT_LIST=$(printf '%s' "$CONT_LIST" | sed 's/,/, /g')
fi

# ---------------------------------------------------------------------------
# Render the service block. Only emit fields that were supplied (or derived) so
# we don't write empty keys. 2-space indent for the service key, 4-space for
# children — matching the existing services.yaml shape.
# ---------------------------------------------------------------------------
BLOCK=$(mktemp "${TMPDIR:-/tmp}/registry-newblock.XXXXXX") || reg_die "mktemp failed (block)"
{
  printf '  %s:\n' "$NAME"
  printf '    port: %s\n' "$PORT"
  [ -n "$SUBDOMAIN" ] && printf '    subdomain: %s\n' "$SUBDOMAIN"
  [ -n "$URL" ]       && printf '    url: %s\n' "$URL"
  [ -n "$COMPOSE" ]   && printf '    compose: %s\n' "$COMPOSE"
  [ -n "$CONT_LIST" ] && printf '    containers: [%s]\n' "$CONT_LIST"
  [ -n "$PURPOSE" ]   && printf '    purpose: %s\n' "$PURPOSE"
  [ -n "$REPO" ]      && printf '    repo: %s\n' "$REPO"
  [ -n "$CICD" ]      && printf '    cicd: %s\n' "$CICD"
} > "$BLOCK"

# Timestamp for the .bak suffix — caller-local time. Fall back if date is odd.
BAK_TS="${MERCURY_REGISTER_TIMESTAMP:-$(date +%Y%m%d-%H%M%S 2>/dev/null)}"
[ -n "$BAK_TS" ] || BAK_TS="manual"

# Comment-preserving upsert + port reservation (both atomic via lib).
reg_upsert_service "$NAME" "$BAK_TS" < "$BLOCK"
rm -f "$BLOCK"
reg_reserve_port "$PORT"

reg_unlock

printf 'REGISTER-OK %s\n' "$NAME"
exit 0
