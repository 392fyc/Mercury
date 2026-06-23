#!/bin/sh
# scripts/lib/registry-sh.sh — shared BusyBox-sh helpers for NAS service-registry
# tooling (Issue #163). Source this file; do not execute directly.
#
#   . "$(dirname "$0")/lib/registry-sh.sh"
#
# Pure POSIX / BusyBox sh — NO bashisms. The NAS (QNAP TS-464 + Container
# Station) ships /bin/sh = BusyBox and /bin/bash symlinked to it. python /
# python3 / yq / jq / flock are ABSENT. Available: BusyBox awk, sed, mkdir,
# mktemp, cp, mv, docker. Therefore:
#   - locking is mkdir-based (no flock)
#   - docker output is consumed via Go-template `--format` (NEVER JSON; no jq)
#   - YAML reads/writes are line-anchored sed/awk (NEVER a YAML re-render)
#
# Everything external is parameterized via env so the suite is detachable and
# testable on a dev box with no NAS / no docker:
#   REGISTRY_FILE     path to services.yaml
#                     (default /share/CACHEDEV1_DATA/homes/392fyc/services.yaml)
#   REGISTRY_DOCKER   docker command prefix; word-split on whitespace into argv.
#                     Default is the NAS sudo+system-docker.sock invocation.
#                     Tests point this at a stub that prints fixture output.
#   REGISTRY_LOCKDIR  mkdir-based lock directory
#                     (default ${TMPDIR:-/tmp}/mercury-registry.lock)
#
# Comment-preserving contract: services.yaml carries a header comment block and
# inline `# argus` style comments on reserved_ports lines. reg_upsert_service /
# reg_reserve_port do block-level / line-level splices only — surrounding bytes
# (header, sibling services, inline comments) are preserved verbatim. There is
# NO full-file YAML re-serialization anywhere in this library.

# Defaults (only set when caller has not already exported them).
: "${REGISTRY_FILE:=/share/CACHEDEV1_DATA/homes/392fyc/services.yaml}"
: "${REGISTRY_DOCKER:=sudo env DOCKER_HOST=unix:///var/run/system-docker.sock /share/CACHEDEV1_DATA/.qpkg/container-station/bin/docker}"
: "${REGISTRY_LOCKDIR:=${TMPDIR:-/tmp}/mercury-registry.lock}"

reg_warn() { printf 'registry-sh WARN: %s\n' "$1" >&2; }
reg_die()  { printf 'registry-sh: %s\n' "$1" >&2; exit 1; }

# ---------------------------------------------------------------------------
# reg_docker <args...>
# Runs the parameterized docker command. REGISTRY_DOCKER is intentionally
# word-split (unquoted) so a multi-word prefix like
# "sudo env DOCKER_HOST=... /path/docker" expands to separate argv entries.
# Tests inject a single-token stub script via REGISTRY_DOCKER.
# ---------------------------------------------------------------------------
reg_docker() {
  # shellcheck disable=SC2086  # deliberate word-split of the command prefix
  $REGISTRY_DOCKER "$@"
}

# ---------------------------------------------------------------------------
# reg_list_services
# Emits one service name per line (the keys under top-level `services:`).
# A service key is a 2-space-indented `name:` line inside the services block;
# nested keys (port:, subdomain:, ...) are 4-space indented and excluded.
# Tolerates blank lines and comment lines inside the block.
# ---------------------------------------------------------------------------
reg_list_services() {
  [ -f "$REGISTRY_FILE" ] || return 0
  awk '
    /^services:[[:space:]]*$/ { in_svc = 1; next }
    # A new top-level key (column 0, non-space, non-comment) ends the block.
    in_svc && /^[^[:space:]#]/ { in_svc = 0 }
    in_svc {
      # Service entries are exactly 2 leading spaces then `name:` (optionally
      # followed by an inline comment). 4-space-indented child keys are excluded.
      if (match($0, /^  [A-Za-z0-9._-]+:[[:space:]]*(#.*)?$/)) {
        line = $0
        sub(/^  /, "", line)
        sub(/:.*$/, "", line)
        print line
      }
    }
  ' "$REGISTRY_FILE"
}

# ---------------------------------------------------------------------------
# reg_get_field <name> <field>
# Prints the scalar value of services.<name>.<field>. <field> is matched at
# 4-space indentation inside the named service block. Strips a trailing inline
# `# comment` and surrounding quotes/whitespace. Empty output = absent.
# For list-valued fields (e.g. containers: [a, b]) the raw bracket text is
# returned; callers split it (see reg_service_containers).
# ---------------------------------------------------------------------------
reg_get_field() {
  reg_name="$1"
  reg_field="$2"
  [ -f "$REGISTRY_FILE" ] || return 0
  awk -v want="$reg_name" -v field="$reg_field" '
    /^services:[[:space:]]*$/ { in_svc = 1; next }
    in_svc && /^[^[:space:]#]/ { in_svc = 0 }
    in_svc && match($0, /^  ([A-Za-z0-9._-]+):[[:space:]]*$/) {
      cur = $0; sub(/^  /, "", cur); sub(/:.*$/, "", cur)
      in_target = (cur == want) ? 1 : 0
      next
    }
    in_svc && in_target {
      # field line at 4 spaces: "    field: value"
      pat = "^    " field ":[[:space:]]*"
      if (match($0, pat)) {
        val = substr($0, RLENGTH + 1)
        # Trim leading whitespace before inspecting for a quote.
        sub(/^[[:space:]]+/, "", val)
        if (match(val, /^"/)) {
          # Double-quoted scalar: the value is everything up to the closing
          # quote; a `#` INSIDE the quotes is data, not a comment. Take the
          # substring between the first and the matching closing quote, undoing
          # the \" and \\ escapes the writer applied.
          rest = substr(val, 2)
          out = ""
          i = 1
          n = length(rest)
          while (i <= n) {
            c = substr(rest, i, 1)
            if (c == "\\" && i < n) { out = out substr(rest, i+1, 1); i += 2; continue }
            if (c == "\"") break
            out = out c; i++
          }
          print out
          exit
        }
        if (match(val, /^'"'"'/)) {
          # Single-quoted scalar: take up to the closing single quote.
          rest = substr(val, 2)
          q = index(rest, "'"'"'")
          if (q > 0) print substr(rest, 1, q-1); else print rest
          exit
        }
        # Plain scalar: strip a trailing inline comment preceded by whitespace,
        # then trim trailing whitespace.
        sub(/[[:space:]]+#.*$/, "", val)
        sub(/[[:space:]]+$/, "", val)
        print val
        exit
      }
    }
  ' "$REGISTRY_FILE"
}

# ---------------------------------------------------------------------------
# reg_service_containers <name>
# Emits the registry-declared container names for a service, one per line,
# parsed from `containers: [a, b, c]`. Empty if the field is absent.
# ---------------------------------------------------------------------------
reg_service_containers() {
  raw=$(reg_get_field "$1" containers)
  [ -n "$raw" ] || return 0
  printf '%s\n' "$raw" \
    | sed 's/^\[//; s/\]$//' \
    | tr ',' '\n' \
    | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' \
    | sed '/^$/d'
}

# ---------------------------------------------------------------------------
# reg_list_reserved_ports
# Emits one reserved port number per line from the reserved_ports list. Inline
# `# label` comments are stripped. Tolerates `- 3000  # argus` formatting.
# ---------------------------------------------------------------------------
reg_list_reserved_ports() {
  [ -f "$REGISTRY_FILE" ] || return 0
  awk '
    /^reserved_ports:[[:space:]]*$/ { in_rp = 1; next }
    in_rp && /^[^[:space:]#-]/ { in_rp = 0 }
    in_rp && match($0, /^[[:space:]]*-[[:space:]]*[0-9]+/) {
      n = $0
      sub(/^[[:space:]]*-[[:space:]]*/, "", n)
      sub(/[^0-9].*$/, "", n)
      if (n != "") print n
    }
  ' "$REGISTRY_FILE"
}

# ---------------------------------------------------------------------------
# reg_lock
# mkdir-based mutual exclusion. mkdir is atomic on POSIX filesystems: it either
# creates the dir (we hold the lock) or fails (someone else holds it). On
# success we install an EXIT trap so the lock is released even on abnormal exit.
# Caller must `set -u`-safe; this uses no flock.
# ---------------------------------------------------------------------------
reg_lock() {
  if mkdir "$REGISTRY_LOCKDIR" 2>/dev/null; then
    # shellcheck disable=SC2064  # expand REGISTRY_LOCKDIR now, on trap install
    trap "rmdir '$REGISTRY_LOCKDIR' 2>/dev/null" EXIT INT TERM
    return 0
  fi
  reg_die "could not acquire lock $REGISTRY_LOCKDIR (another run in progress, or stale lock dir — rmdir it if no run is active)"
}

# reg_unlock — explicit early release (the EXIT trap also covers this).
reg_unlock() {
  rmdir "$REGISTRY_LOCKDIR" 2>/dev/null || true
  trap - EXIT INT TERM
}

# ---------------------------------------------------------------------------
# reg_reserve_port <port>
# Appends `  - <port>` to reserved_ports if the port is not already present.
# Idempotent. Preserves existing list formatting + inline comments (append-only;
# never reflows the list). Atomic write via mktemp + mv.
# ---------------------------------------------------------------------------
reg_reserve_port() {
  rp_port="$1"
  case "$rp_port" in
    ''|*[!0-9]*) reg_die "reg_reserve_port: port must be numeric (got '$rp_port')" ;;
  esac
  if reg_list_reserved_ports | grep -qx -- "$rp_port"; then
    return 0
  fi
  rp_tmp=$(mktemp "${TMPDIR:-/tmp}/registry-rp.XXXXXX") || reg_die "mktemp failed (reserve_port)"
  awk -v port="$rp_port" '
    /^reserved_ports:[[:space:]]*$/ { in_rp = 1; print; next }
    in_rp && /^[^[:space:]#-]/ {
      # Leaving the list region without having printed our port — append before
      # the new top-level key.
      print "  - " port
      in_rp = 0; printed = 1
    }
    { print }
    END { if (in_rp && !printed) print "  - " port }
  ' "$REGISTRY_FILE" > "$rp_tmp" || { rm -f "$rp_tmp"; reg_die "awk reserve_port failed"; }
  mv -f "$rp_tmp" "$REGISTRY_FILE" || { rm -f "$rp_tmp"; reg_die "mv failed (reserve_port)"; }
}

# ---------------------------------------------------------------------------
# reg_upsert_service <name> <bak_suffix> <<EOF ...block... EOF
# Comment-preserving block replace. Reads the rendered service block (already
# formatted, 2-space-indented `  <name>:` plus 4-space children) from stdin and
# either replaces the existing services.<name>: subtree or appends it at the end
# of the services: block. The header comment block, sibling services, and all
# inline comments are preserved byte-for-byte.
#
# Backup: before mutating, copies REGISTRY_FILE to
# "<REGISTRY_FILE>.bak-<bak_suffix>" (caller supplies a local timestamp). Atomic
# write via mktemp + mv.
#
# A service subtree spans from its `  <name>:` line through every following
# 4-space-or-more indented child line. The skip STOPS — and the line is
# preserved — at the first blank line, comment line, or 2-space sibling key that
# follows the children: those bytes logically belong to the separator before /
# the start of the next sibling, NOT to the replaced service, so dropping them
# would corrupt "siblings preserved byte-for-byte".
# ---------------------------------------------------------------------------
reg_upsert_service() {
  up_name="$1"
  up_bak="$2"
  [ -f "$REGISTRY_FILE" ] || reg_die "reg_upsert_service: REGISTRY_FILE not found: $REGISTRY_FILE"

  up_block=$(mktemp "${TMPDIR:-/tmp}/registry-block.XXXXXX") || reg_die "mktemp failed (upsert block)"
  cat > "$up_block"

  # Timestamped backup before any mutation.
  cp "$REGISTRY_FILE" "${REGISTRY_FILE}.bak-${up_bak}" \
    || { rm -f "$up_block"; reg_die "backup failed: ${REGISTRY_FILE}.bak-${up_bak}"; }

  up_tmp=$(mktemp "${TMPDIR:-/tmp}/registry-up.XXXXXX") || { rm -f "$up_block"; reg_die "mktemp failed (upsert tmp)"; }

  awk -v want="$up_name" -v blockfile="$up_block" '
    function emit_block(   line) {
      while ((getline line < blockfile) > 0) print line
      close(blockfile)
    }
    BEGIN { in_svc = 0; skipping = 0; replaced = 0 }

    # Enter services block.
    /^services:[[:space:]]*$/ { in_svc = 1; print; next }

    # A top-level key (column 0, non-space, non-comment) ends the services block.
    # If we were still inside and never matched the target, append it here.
    in_svc && /^[^[:space:]#]/ {
      if (!replaced) { emit_block(); replaced = 1 }
      in_svc = 0; skipping = 0
      print; next
    }

    # While skipping the old target subtree, drop ONLY its own child lines
    # (indent >= 4 spaces). Stop skipping and PRESERVE the line on the first
    # blank line, comment line, or 2-space sibling key — those bytes belong to
    # the separator / next sibling, not to the replaced service.
    skipping {
      if (match($0, /^    /)) {
        next           # 4-space-or-deeper child line of the target — drop
      }
      skipping = 0     # blank / comment / sibling — fall through and keep it
    }

    # A 2-space-indented service key inside the block.
    in_svc && match($0, /^  ([A-Za-z0-9._-]+):/) {
      cur = $0; sub(/^  /, "", cur); sub(/:.*$/, "", cur)
      if (cur == want) {
        emit_block(); replaced = 1
        skipping = 1   # swallow the old subtree (this line + its children)
        next
      }
    }

    { print }

    END {
      # services block ran to EOF without a trailing top-level key and target
      # was never found — append it.
      if (in_svc && !replaced) emit_block()
    }
  ' "$REGISTRY_FILE" > "$up_tmp" || { rm -f "$up_block" "$up_tmp"; reg_die "awk upsert failed"; }

  rm -f "$up_block"
  mv -f "$up_tmp" "$REGISTRY_FILE" || { rm -f "$up_tmp"; reg_die "mv failed (upsert)"; }
}
