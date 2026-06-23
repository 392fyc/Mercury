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

# Value-taking flags must have a value present BEFORE we shift to consume it.
# Without this guard, a value flag in last position (e.g. `--subdomain` with no
# following arg) leaves $#=1; the second `shift` then runs on an empty argument
# set and aborts under BusyBox sh. Mirror validate-registry.sh's `--registry`
# pattern: require `[ $# -ge 2 ]` for every value flag.
while [ $# -gt 0 ]; do
  case "$1" in
    --name)       [ $# -ge 2 ] || reg_die "--name requires a value";       shift; NAME="$1"; shift ;;
    --port)       [ $# -ge 2 ] || reg_die "--port requires a value";       shift; PORT="$1"; shift ;;
    --subdomain)  [ $# -ge 2 ] || reg_die "--subdomain requires a value";  shift; SUBDOMAIN="$1"; shift ;;
    --url)        [ $# -ge 2 ] || reg_die "--url requires a value";        shift; URL="$1"; shift ;;
    --compose)    [ $# -ge 2 ] || reg_die "--compose requires a value";    shift; COMPOSE="$1"; shift ;;
    --containers) [ $# -ge 2 ] || reg_die "--containers requires a value"; shift; CONTAINERS="$1"; shift ;;
    --purpose)    [ $# -ge 2 ] || reg_die "--purpose requires a value";    shift; PURPOSE="$1"; shift ;;
    --repo)       [ $# -ge 2 ] || reg_die "--repo requires a value";       shift; REPO="$1"; shift ;;
    --cicd)       [ $# -ge 2 ] || reg_die "--cicd requires a value";       shift; CICD="$1"; shift ;;
    --registry)   [ $# -ge 2 ] || reg_die "--registry requires a value";   shift; REGISTRY_FILE="$1"; shift ;;
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
# Symlink-redirect defense (fail-fast, before locking): refuse to write through
# a symlinked registry file — the lib's mktemp+mv would otherwise replace the
# link target, a link-hijack write primitive. The lib enforces this again at the
# actual write site; this is the early, friendly rejection.
[ -L "$REGISTRY_FILE" ] && reg_die "registry file must not be a symlink: $REGISTRY_FILE"
[ -w "$REGISTRY_FILE" ] || reg_die "registry file is not writable: $REGISTRY_FILE"

# Reject control chars / newlines in any free-form flag value: a newline would
# splice an extra line into the YAML structure, control chars corrupt it. Done
# before any write. We delete only the C0 control range (0x00-0x1F) + DEL
# (0x7F); anything left there is a control byte. UTF-8 multibyte text (e.g. the
# "→" in the baseline cicd field) has high bytes that are NOT control chars and
# must pass — so we match the cntrl class explicitly, NOT "everything non-print"
# (the latter would wrongly reject multibyte UTF-8 under a byte-wise C locale).
reg_reject_control() {
  rc_label="$1"; rc_val="$2"
  [ -n "$rc_val" ] || return 0
  # Count control bytes via wc -c (NOT $(...) capture of the bytes themselves —
  # command substitution strips trailing newlines, which would hide a trailing
  # \n). Any control byte (incl. interior/trailing newline, tab, CR) -> reject.
  rc_count=$(printf '%s' "$rc_val" | tr -dc '[:cntrl:]' | wc -c | tr -d ' ')
  [ "$rc_count" = "0" ] || reg_die "$rc_label must not contain newlines or control characters"
}
reg_reject_control "--subdomain" "$SUBDOMAIN"
reg_reject_control "--url" "$URL"
reg_reject_control "--compose" "$COMPOSE"
reg_reject_control "--containers" "$CONTAINERS"
reg_reject_control "--purpose" "$PURPOSE"
reg_reject_control "--repo" "$REPO"
reg_reject_control "--cicd" "$CICD"

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
  if reg_list_reserved_ports | grep -qxF -- "$PORT"; then
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
# Merge-upsert: when NAME already exists, read its current fields so that flags
# NOT supplied on THIS invocation retain their stored values (partial
# re-registration must not destroy untouched fields). A brand-new service
# renders from flags alone (absent fields are not emitted).
#
# We must distinguish "flag not passed" (CONTAINERS unset by the caller) from
# "flag passed empty". A getopts-style empty string is still a pass; we treat
# the parsed default "" as not-passed for the merge (the CLI offers no way to
# clear a field, by design — clearing would be a destructive op out of scope).
# ---------------------------------------------------------------------------
SERVICE_EXISTS=0
for _svc in $(reg_list_services); do
  [ "$_svc" = "$NAME" ] && { SERVICE_EXISTS=1; break; }
done

if [ "$SERVICE_EXISTS" = "1" ]; then
  [ -n "$SUBDOMAIN" ] || SUBDOMAIN=$(reg_get_field "$NAME" subdomain)
  [ -n "$URL" ]       || URL=$(reg_get_field "$NAME" url)
  [ -n "$COMPOSE" ]   || COMPOSE=$(reg_get_field "$NAME" compose)
  [ -n "$PURPOSE" ]   || PURPOSE=$(reg_get_field "$NAME" purpose)
  [ -n "$REPO" ]      || REPO=$(reg_get_field "$NAME" repo)
  [ -n "$CICD" ]      || CICD=$(reg_get_field "$NAME" cicd)
  # containers: keep the stored set if neither --containers nor a docker derive
  # is wanted. We mark whether the existing value should be reused.
  if [ -z "$CONTAINERS" ]; then
    EXISTING_CONTAINERS=$(reg_service_containers "$NAME" | paste -sd, - 2>/dev/null)
    [ -n "$EXISTING_CONTAINERS" ] || EXISTING_CONTAINERS=$(reg_service_containers "$NAME" | tr '\n' ',' | sed 's/,$//')
    CONTAINERS="$EXISTING_CONTAINERS"
  fi
fi

# ---------------------------------------------------------------------------
# Derive --containers from running docker when omitted AND not retained from an
# existing entry (root fix for stale hand-maintained lists). Normalize
# comma/space separated input to a clean "[a, b, c]" rendering either way, with
# the names SORTED so the same set always renders byte-identically regardless of
# docker enumeration order.
# ---------------------------------------------------------------------------
if [ -z "$CONTAINERS" ]; then
  # Run `docker ps` ONCE and check its exit status. A docker failure (daemon
  # down, permission denied) must NOT be swallowed and silently treated as
  # "this project has no containers" — that would write an empty/incomplete
  # containers[]. Only a SUCCESSFUL ps that returns no matching rows is a
  # legitimate empty result (containers[] left unset, not emitted).
  DOCKER_PS_OUT=$(reg_docker ps --format '{{.Label "com.docker.compose.project"}}|{{.Names}}') \
    || reg_die "docker ps failed during --containers auto-derive (daemon down / permission?). Pass --containers explicitly or fix docker. (REGISTRY_DOCKER=$REGISTRY_DOCKER)"
  DERIVED=$(printf '%s\n' "$DOCKER_PS_OUT" \
    | awk -F'|' -v p="$NAME" '$1==p {print $2}' | sed '/^$/d' | sort)
  CONTAINERS=$(printf '%s' "$DERIVED" | sed '/^$/d' | paste -sd, - 2>/dev/null)
  # paste may be absent on some BusyBox builds — fall back to tr+sed.
  if [ -z "$CONTAINERS" ] && [ -n "$DERIVED" ]; then
    CONTAINERS=$(printf '%s\n' "$DERIVED" | sed '/^$/d' | tr '\n' ',' | sed 's/,$//')
  fi
fi
# Normalize separators to ", " and build a YAML flow-sequence. Sort the names so
# the rendering is byte-identical for a given set (no churn on re-register).
CONT_LIST=""
if [ -n "$CONTAINERS" ]; then
  CONT_SORTED=$(printf '%s' "$CONTAINERS" | tr ',' ' ' | tr -s ' ' \
    | sed 's/^ *//; s/ *$//' | tr ' ' '\n' | sed '/^$/d' | sort)
  CONT_LIST=$(printf '%s\n' "$CONT_SORTED" | sed '/^$/d' | paste -sd, - 2>/dev/null)
  if [ -z "$CONT_LIST" ]; then
    CONT_LIST=$(printf '%s\n' "$CONT_SORTED" | sed '/^$/d' | tr '\n' ',' | sed 's/,$//')
  fi
  # Add space after each comma for readability: a,b -> a, b
  CONT_LIST=$(printf '%s' "$CONT_LIST" | sed 's/,/, /g')
fi

# ---------------------------------------------------------------------------
# Scalar YAML rendering. Free-form fields (subdomain/url/compose/purpose/repo/
# cicd) can carry characters that change YAML parsing or truncate the value:
#   - a ` #` sequence starts an inline comment (would truncate on read-back)
#   - a leading YAML indicator (- ? : # & * ! | > % @ ` " ' [ ] { } , and more)
#   - an interior ": " maps-key sequence
# When ANY risk marker is present we double-quote the value and escape interior
# double-quotes + backslashes (YAML double-quoted scalar rules). reg_get_field
# strips one layer of surrounding quotes, so the value reads back intact.
# Newlines/control chars are already rejected above, so quoting is sufficient.
# ---------------------------------------------------------------------------
emit_field() {
  ef_key="$1"; ef_val="$2"
  [ -n "$ef_val" ] || return 0
  ef_needs_quote=0
  case "$ef_val" in
    *" #"*|*'#'*) ef_needs_quote=1 ;;          # inline-comment hazard
    *": "*|*":")  ef_needs_quote=1 ;;          # mapping-key hazard
    "-"*|"?"*)    ef_needs_quote=1 ;;          # leading block-seq / complex-key indicator
    [\ \"\'\:\#\&\*\!\|\>\%\@\`\[\]\{\}\,]*) ef_needs_quote=1 ;;  # leading indicator
    *) ef_needs_quote=0 ;;
  esac
  if [ "$ef_needs_quote" = "1" ]; then
    # Escape \ then " for a YAML double-quoted scalar.
    ef_esc=$(printf '%s' "$ef_val" | sed 's/\\/\\\\/g; s/"/\\"/g')
    printf '    %s: "%s"\n' "$ef_key" "$ef_esc"
  else
    printf '    %s: %s\n' "$ef_key" "$ef_val"
  fi
}

# ---------------------------------------------------------------------------
# Render the service block. Only emit fields that were supplied (or derived/
# merged) so we don't write empty keys. 2-space indent for the service key,
# 4-space for children — matching the existing services.yaml shape.
# ---------------------------------------------------------------------------
BLOCK=$(mktemp "${TMPDIR:-/tmp}/registry-newblock.XXXXXX") || reg_die "mktemp failed (block)"
{
  printf '  %s:\n' "$NAME"
  printf '    port: %s\n' "$PORT"
  emit_field subdomain "$SUBDOMAIN"
  emit_field url "$URL"
  emit_field compose "$COMPOSE"
  [ -n "$CONT_LIST" ] && printf '    containers: [%s]\n' "$CONT_LIST"
  emit_field purpose "$PURPOSE"
  emit_field repo "$REPO"
  emit_field cicd "$CICD"
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
