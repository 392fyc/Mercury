#!/usr/bin/env bash
# intel-watch.sh — External-intel deterministic collector (Issue #157 / #453 Slice 1)
#
# Watches a fixed whitelist of external sources, computes a per-source
# fingerprint, compares against persisted state, and reports deltas. This is
# the NO-LLM, NO-issue-create slice: it only collects + detects + (optionally)
# persists state. The LLM significance judgment (Slice 2) and the GHA workflow
# wiring (Slice 3) build on the delta JSON this script emits.
#
# Design authority: .mercury/docs/research/issue-157-external-info-agent-2026-05.md
#   §4.2 (B-class source detection), §5.2 (dedup / state persistence mechanism c),
#   §9 (Phase-2 entrance gate, web-verified 2026-05-25).
#
# Sources monitored (3 targets, 2 source types — ADR §7 step 2):
#   npm:@anthropic-ai/claude-code        dist-tags.latest  (abbreviated packument)
#   npm:@anthropic-ai/claude-agent-sdk   dist-tags.latest
#   changelog:claude-code                sha256 of CHANGELOG.md raw
#
# State backend (pick one; default is dry-run, no persistence):
#   --state-file <path>     local JSON file        (testable, no network/auth)
#   --state-issue <number>  pinned Issue body      (production, via gh api; ADR §5.2 mechanism c)
#
# Mercury-internal tooling under scripts/ — exempt from the 200-LOC adapter cap
# (CLAUDE.md carve-out: this implements an in-repo monitoring protocol, it does
# not mount an external project).
#
# Run locally (dry-run, prints deltas, never writes):
#   bash scripts/intel-watch.sh --state-file /tmp/intel-state.json
# Persist state after detection:
#   bash scripts/intel-watch.sh --state-file /tmp/intel-state.json --commit
# Emit machine-readable delta report for the next slice:
#   bash scripts/intel-watch.sh --state-file /tmp/intel-state.json --json-out /tmp/deltas.json

set -uo pipefail

# ── Source endpoints (overridable for tests via env; defaults are the
#    web-verified official endpoints — ADR §9) ──
NPM_REGISTRY="${INTEL_NPM_REGISTRY:-https://registry.npmjs.org}"
CHANGELOG_URL="${INTEL_CHANGELOG_URL:-https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md}"

# ── Fixed source whitelist. Keys are stable identifiers; nothing here is ever
#    built from external/LLM input (injection + label-pollution guard, ADR §5.1). ──
#    Format per entry: "<key>|<kind>|<arg>|<label>". The arg is a stable literal
#    (package name / logical id) and never an env-derived URL — keeping a possibly
#    `|`-containing URL out of this delimited record preserves the field parse and
#    the whitelist guard. The changelog URL lives only in $CHANGELOG_URL and is
#    consumed directly by collect_source.
SOURCES=(
  "npm:@anthropic-ai/claude-code|npm|@anthropic-ai/claude-code|intel/sdk"
  "npm:@anthropic-ai/claude-agent-sdk|npm|@anthropic-ai/claude-agent-sdk|intel/sdk"
  "changelog:claude-code|changelog|claude-code|intel/sdk"
)

# Allowed source labels — parsed labels are validated against this set so a
# malformed SOURCES entry can never inject an off-whitelist label downstream.
ALLOWED_LABELS=" intel/sdk intel/api-docs intel/oss "

STATE_FILE=""
STATE_ISSUE=""
COMMIT=0
JSON_OUT=""

usage() {
  sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'
}

die() { printf 'intel-watch: %s\n' "$1" >&2; exit 2; }

need_val() { [[ $# -ge 2 && -n "${2:-}" ]] || die "$1 requires a non-empty value"; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --state-file)  need_val "$@"; STATE_FILE="$2"; shift 2 ;;
    --state-issue) need_val "$@"; STATE_ISSUE="$2"; shift 2 ;;
    --commit)      COMMIT=1; shift ;;
    --json-out)    need_val "$@"; JSON_OUT="$2"; shift 2 ;;
    -h|--help)     usage; exit 0 ;;
    *)             die "unknown argument: $1" ;;
  esac
done

for cmd in jq curl; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required"
done
# Default-deny non-https source URLs (SSRF / local-file-read guard). The env
# overrides are operator-controlled, but enforce https:// unless a test opts in
# explicitly so a tainted environment can't redirect fetches to file:// or an
# internal host. Tests with file:// fixtures set INTEL_ALLOW_INSECURE_SOURCE_URLS=1.
if [[ "${INTEL_ALLOW_INSECURE_SOURCE_URLS:-0}" != "1" ]]; then
  [[ "$NPM_REGISTRY"  == https://* ]] || die "INTEL_NPM_REGISTRY must be https:// (set INTEL_ALLOW_INSECURE_SOURCE_URLS=1 for file:// test fixtures)"
  [[ "$CHANGELOG_URL" == https://* ]] || die "INTEL_CHANGELOG_URL must be https:// (set INTEL_ALLOW_INSECURE_SOURCE_URLS=1 for file:// test fixtures)"
fi
if ! command -v sha256sum >/dev/null 2>&1 \
   && ! command -v shasum   >/dev/null 2>&1 \
   && ! command -v openssl  >/dev/null 2>&1; then
  die "need one of: sha256sum / shasum / openssl (for CHANGELOG fingerprint)"
fi
if [[ -n "$STATE_FILE" && -n "$STATE_ISSUE" ]]; then
  die "choose at most one state backend (--state-file XOR --state-issue)"
fi
if [[ -n "$STATE_ISSUE" ]]; then
  command -v gh >/dev/null 2>&1 || die "gh is required for --state-issue"
fi

# Markers delimiting the JSON state block inside the pinned Issue body.
STATE_BEGIN="<!-- INTEL_STATE_BEGIN -->"
STATE_END="<!-- INTEL_STATE_END -->"

now_utc() { TZ=UTC date +%Y-%m-%dT%H:%M:%SZ; }

# ── Source collection ── each emits a fingerprint string on stdout, or exits
#    non-zero on a fetch/parse failure (caller treats failure as "error", never
#    as "unchanged" — a failed fetch must not silently suppress a real delta).
fetch_npm() {
  local pkg="$1" body
  body="$(curl -sS --retry 3 --retry-delay 2 --max-time 30 \
            -H "Accept: application/vnd.npm.install-v1+json" \
            "${NPM_REGISTRY}/${pkg}" 2>/dev/null)" || return 1
  local latest
  latest="$(printf '%s' "$body" | jq -r '."dist-tags".latest // empty' 2>/dev/null)" || return 1
  [[ -n "$latest" ]] || return 1
  printf '%s' "$latest"
}

# Portable sha256 of stdin → bare hex digest. Prefers sha256sum (Linux/GHA),
# falls back to shasum -a 256 (macOS default) then openssl dgst -sha256 (broad
# fallback). openssl prints "SHA2-256(stdin)= <hash>", so take the last field.
sha256_hex() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | cut -d' ' -f1
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 | awk '{print $NF}'
  else
    return 1
  fi
}

fetch_changelog() {
  local url="$1" body
  body="$(curl -sS --retry 3 --retry-delay 2 --max-time 30 "$url" 2>/dev/null)" || return 1
  [[ -n "$body" ]] || return 1
  local h
  h="$(printf '%s' "$body" | sha256_hex)" || return 1
  [[ -n "$h" ]] || return 1
  printf 'sha256:%s' "$h"
}

collect_source() {
  # $1=kind $2=arg → fingerprint on stdout, non-zero on failure.
  # The changelog kind ignores the (literal) arg and reads the URL from the
  # global $CHANGELOG_URL, so no env-derived URL ever passes through the
  # |-delimited SOURCES record.
  case "$1" in
    npm)       fetch_npm "$2" ;;
    changelog) fetch_changelog "$CHANGELOG_URL" ;;
    *)         return 1 ;;
  esac
}

# ── State backend I/O ── state is always one JSON object:
#    { "schema_version": 1, "updated_at": "...", "sources": { "<key>": "<fp>" } }
empty_state() { jq -n '{schema_version:1, updated_at:null, sources:{}}'; }

state_read() {
  if [[ -n "$STATE_FILE" ]]; then
    if [[ -f "$STATE_FILE" ]]; then
      jq -e . "$STATE_FILE" 2>/dev/null || { printf 'intel-watch: state file is not valid JSON: %s\n' "$STATE_FILE" >&2; return 1; }
    else
      empty_state
    fi
  elif [[ -n "$STATE_ISSUE" ]]; then
    local body nbegin nend extracted
    body="$(gh issue view "$STATE_ISSUE" --json body -q .body 2>/dev/null)" \
      || { printf 'intel-watch: cannot read state Issue #%s\n' "$STATE_ISSUE" >&2; return 1; }
    # Marker integrity gate: a fresh state Issue has neither marker → seed from
    # empty; a complete pair (exactly one each) → trust the extraction; any other
    # count means a truncated/corrupted body → fail loud (do NOT trust a partial
    # BEGIN..EOF extraction, which could re-seed stale state and re-spam).
    nbegin="$(printf '%s' "$body" | grep -cF "$STATE_BEGIN")"
    nend="$(printf '%s' "$body" | grep -cF "$STATE_END")"
    if [[ "$nbegin" -eq 0 && "$nend" -eq 0 ]]; then
      empty_state; return 0
    fi
    if [[ "$nbegin" -ne 1 || "$nend" -ne 1 ]]; then
      printf 'intel-watch: state Issue #%s has malformed markers (begin=%s end=%s)\n' \
        "$STATE_ISSUE" "$nbegin" "$nend" >&2
      return 1
    fi
    # Extract the JSON between the markers, stripping the ```json fences.
    extracted="$(printf '%s' "$body" \
      | awk -v b="$STATE_BEGIN" -v e="$STATE_END" '
          index($0,b){f=1; next} index($0,e){f=0} f' \
      | sed '/^```/d')"
    if [[ -n "$(printf '%s' "$extracted" | tr -d '[:space:]')" ]]; then
      printf '%s' "$extracted" | jq -e . 2>/dev/null \
        || { printf 'intel-watch: state Issue #%s body has malformed JSON block\n' "$STATE_ISSUE" >&2; return 1; }
    else
      empty_state
    fi
  else
    empty_state  # dry-run with no backend: always seeds from empty
  fi
}

state_write() {
  # $1 = new state JSON (string)
  local new_state="$1"
  if [[ -n "$STATE_FILE" ]]; then
    # Atomic whole-body replacement: stage to a temp in the same directory, then
    # rename (atomic on the same filesystem on both Linux and MSYS/NTFS). A crash
    # mid-write thus leaves the prior state intact rather than a truncated file.
    local tmp; tmp="$(mktemp "${STATE_FILE}.tmp.XXXXXX")" \
      || { printf 'intel-watch: cannot create temp for state write\n' >&2; return 1; }
    printf '%s\n' "$new_state" > "$tmp"
    mv -f "$tmp" "$STATE_FILE" \
      || { rm -f "$tmp"; printf 'intel-watch: failed to move state into place\n' >&2; return 1; }
  elif [[ -n "$STATE_ISSUE" ]]; then
    # Explicit template: BSD/macOS mktemp requires one (bare `mktemp` errors).
    local tmp; tmp="$(mktemp "${TMPDIR:-/tmp}/intel-watch-issue.XXXXXX")" \
      || { printf 'intel-watch: cannot create temp for Issue body\n' >&2; return 1; }
    {
      printf '# 🛰️ Intel-Watch State (auto-managed — do not edit manually)\n\n'
      printf 'Authoritative state store for the external-intel agent (#157 / #453).\n'
      printf 'The JSON block below is read and rewritten atomically each run;\n'
      printf 'manual edits are overwritten. See `scripts/intel-watch.sh`.\n\n'
      printf '%s\n' "$STATE_BEGIN"
      printf '```json\n%s\n```\n' "$new_state"
      printf '%s\n' "$STATE_END"
    } > "$tmp"
    gh issue edit "$STATE_ISSUE" --body-file "$tmp" >/dev/null \
      || { rm -f "$tmp"; printf 'intel-watch: failed to write state Issue #%s\n' "$STATE_ISSUE" >&2; return 1; }
    rm -f "$tmp"
  fi
  # No backend (pure dry-run): nothing to persist.
}

# ── Main collect+diff loop ──
main() {
  local checked_at state
  checked_at="$(now_utc)"
  state="$(state_read)" || exit 3

  local deltas='[]' seeded='[]' unchanged='[]' errors='[]'
  local new_sources; new_sources="$(printf '%s' "$state" | jq -c '.sources')"

  local entry key kind arg label cur old
  for entry in "${SOURCES[@]}"; do
    IFS='|' read -r key kind arg label <<<"$entry"
    [[ "$ALLOWED_LABELS" == *" $label "* ]] \
      || die "internal: source '$key' has non-whitelisted label '$label'"
    old="$(printf '%s' "$state" | jq -r --arg k "$key" '.sources[$k] // empty')"

    if ! cur="$(collect_source "$kind" "$arg")"; then
      errors="$(jq -c --arg k "$key" '. + [$k]' <<<"$errors")"
      printf 'ERROR    %s (fetch failed — state preserved, not treated as unchanged)\n' "$key" >&2
      continue
    fi

    if [[ -z "$old" ]]; then
      seeded="$(jq -c --arg k "$key" '. + [$k]' <<<"$seeded")"
      printf 'SEEDED   %s = %s\n' "$key" "$cur"
    elif [[ "$old" != "$cur" ]]; then
      deltas="$(jq -c --arg k "$key" --arg kind "$kind" --arg label "$label" --arg old "$old" --arg new "$cur" \
        '. + [{source:$k, kind:$kind, label:$label, old:$old, new:$new}]' <<<"$deltas")"
      printf 'DELTA    %s: %s -> %s\n' "$key" "$old" "$cur"
    else
      unchanged="$(jq -c --arg k "$key" '. + [$k]' <<<"$unchanged")"
      printf 'unchanged %s = %s\n' "$key" "$cur"
    fi
    new_sources="$(jq -c --arg k "$key" --arg v "$cur" '.[$k]=$v' <<<"$new_sources")"
  done

  local report
  report="$(jq -n \
    --arg checked_at "$checked_at" \
    --argjson deltas "$deltas" \
    --argjson seeded "$seeded" \
    --argjson unchanged "$unchanged" \
    --argjson errors "$errors" \
    '{checked_at:$checked_at, deltas:$deltas, seeded:$seeded, unchanged:$unchanged, errors:$errors}')"

  if [[ -n "$JSON_OUT" ]]; then
    # Atomic write + explicit failure surfacing: a full disk / missing dir /
    # permission error must not be swallowed (caller would misjudge success).
    local out_tmp
    out_tmp="$(mktemp "${JSON_OUT}.tmp.XXXXXX")" \
      || { printf 'intel-watch: cannot create temp for --json-out\n' >&2; exit 3; }
    printf '%s\n' "$report" > "$out_tmp" \
      || { rm -f "$out_tmp"; printf 'intel-watch: failed to write --json-out temp\n' >&2; exit 3; }
    mv -f "$out_tmp" "$JSON_OUT" \
      || { rm -f "$out_tmp"; printf 'intel-watch: failed to move --json-out into place\n' >&2; exit 3; }
  fi

  local n_delta n_seed n_err
  n_delta="$(jq '.deltas|length' <<<"$report")"
  n_seed="$(jq '.seeded|length' <<<"$report")"
  n_err="$(jq '.errors|length' <<<"$report")"
  printf -- '--- summary: %s delta, %s seeded, %s error, %s unchanged @ %s ---\n' \
    "$n_delta" "$n_seed" "$n_err" "$(jq '.unchanged|length' <<<"$report")" "$checked_at"

  if [[ "$COMMIT" -eq 1 ]]; then
    if [[ -z "$STATE_FILE" && -z "$STATE_ISSUE" ]]; then
      printf 'intel-watch: --commit requires a state backend (--state-file or --state-issue)\n' >&2
      exit 2
    fi
    local new_state
    new_state="$(jq -n --argjson src "$new_sources" --arg ts "$checked_at" \
      '{schema_version:1, updated_at:$ts, sources:$src}')"
    state_write "$new_state" || exit 3
    if [[ "$n_err" -gt 0 ]]; then
      printf 'state committed (%s sources; %s errored — errored sources retain prior fingerprint)\n' \
        "$(jq '.sources|length' <<<"$new_state")" "$n_err"
    else
      printf 'state committed (%s sources)\n' "$(jq '.sources|length' <<<"$new_state")"
    fi
  else
    printf '(dry-run: state not persisted; pass --commit to persist)\n'
  fi

  # Exit non-zero if any source failed to fetch, so the caller (GHA) can react.
  [[ "$n_err" -eq 0 ]] || exit 1
}

main
