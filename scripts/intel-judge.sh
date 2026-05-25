#!/usr/bin/env bash
# intel-judge.sh — External-intel LLM significance judgment + guarded issue-create
#                  (Issue #157 / #453 Slice 2)
#
# Consumes the delta report emitted by intel-watch.sh (Slice 1, via --json-out),
# runs a SINGLE Anthropic Messages API call to judge whether the batch of deltas
# is significant enough to warrant a human-triaged GitHub Issue, then — behind
# hard anti-spam + injection guardrails — either opens exactly one
# `intel/needs-triage` Issue or degrades the batch into a digest. No business
# code is touched; nothing is pushed to a protected branch.
#
# Design authority: .mercury/docs/research/issue-157-external-info-agent-2026-05.md
#   §5.1 (label whitelist + injection guard), §5.2 (significance gate = dedup
#   layer 3), §5.3 (Issue-vs-digest split), §7 step 4-5 (Phase-2 LLM call + error
#   degradation + guarded gh issue create).
#
# Anthropic Messages API (web-verified 2026-05-26 against
#   https://platform.claude.com/docs/en/api/messages):
#   POST https://api.anthropic.com/v1/messages
#   headers: x-api-key: $KEY | anthropic-version: 2023-06-01 | content-type: application/json
#   body:    { "model", "max_tokens", "system"?, "messages":[{"role","content"}] }
#   response text lives in content[].text (TextBlock, type=="text").
#   model: claude-haiku-4-5 (cheap, sufficient for significance judgment).
#
# Mercury-internal tooling under scripts/ — exempt from the 200-LOC adapter cap
# (CLAUDE.md carve-out: implements an in-repo monitoring protocol, does NOT mount
# an external project).
#
# ── INJECTION GUARD (first-class constraint, ADR §5.1/§7 step 5) ──
#   * LLM output and external release-note / changelog text NEVER reach a shell
#     or `gh` command as a parsed token. All such text flows into (a) the API
#     request body via `jq --arg` (jq escapes it) and (b) the Issue body via a
#     `--body-file` temp file. It is never concatenated into a command string,
#     never `eval`-ed, and never used to build a label.
#   * Labels come ONLY from fixed whitelists. The LLM may *select* a priority /
#     impact from a closed enum; a value outside the enum is rejected (the Issue
#     is downgraded to digest), so a poisoned LLM response cannot inject a label.
#   * The Issue title is built from the delta source-keys (intel-watch.sh's fixed
#     SOURCES literals) + a delta count — never LLM text. deltas.json is trusted
#     input (it is intel-watch.sh's own output); regardless, the title reaches
#     `gh` only as a single discrete argv element and is never reparsed by a shell.
#
# Usage:
#   # dry-run (default): judge + print what WOULD be created, never calls gh
#   bash scripts/intel-judge.sh --deltas-file deltas.json
#   # really create the Issue (Slice 3 / GHA wires this on the significant path)
#   bash scripts/intel-judge.sh --deltas-file deltas.json --create-issue --repo owner/name
#   # degrade target for non-significant / error batches
#   bash scripts/intel-judge.sh --deltas-file deltas.json --digest-file digest.md
#   # TEST HOOK: inject a mock API response (no key, no network)
#   bash scripts/intel-judge.sh --deltas-file deltas.json --llm-response-file mock.json
#
# Exit codes (contract for the Slice 3 orchestrator):
#   0  deltas landed (Issue created / dry-run previewed / written to digest /
#      judged non-significant) → caller MAY now persist intel-watch state.
#   2  usage error (bad/missing args) → nothing happened.
#   3  delta did NOT land (e.g. digest write failed) → caller MUST NOT persist
#      state, so the batch is retried whole next run (never silently dropped).

set -uo pipefail

# ── Configuration / args ──
DELTAS_FILE=""
CONTEXT_FILE=""                 # optional extra context (e.g. changelog excerpt),
                                #   orchestrator-fed; only ever enters the API body
DIGEST_FILE=""                  # where non-significant / degraded batches are recorded
CREATE_ISSUE=0                  # default dry-run; --create-issue actually calls gh
REPO=""                         # owner/name for `gh issue create --repo`
MAX_ISSUES=1                    # PoC hard cap: only 0 (force-digest) vs ≥1 (one
                                #   Issue for the whole batch) are meaningful this
                                #   slice; the multi-Issue path does not exist yet
LLM_RESPONSE_FILE=""            # TEST HOOK: raw API response JSON, bypasses curl
MODEL="${INTEL_JUDGE_MODEL:-claude-haiku-4-5}"
API_KEY_ENV="${INTEL_JUDGE_API_KEY_ENV:-ANTHROPIC_API_KEY}"  # NAME of the env var
                                #   holding the key — the key value is never an argv
ANTHROPIC_API_URL="${INTEL_ANTHROPIC_API_URL:-https://api.anthropic.com/v1/messages}"
MAX_RETRIES="${INTEL_JUDGE_MAX_RETRIES:-3}"
RETRY_BASE_DELAY="${INTEL_JUDGE_RETRY_BASE_DELAY:-2}"  # seconds; tests set 0 for speed
API_TIMEOUT="${INTEL_JUDGE_API_TIMEOUT:-60}"          # per-attempt curl --max-time

# ── Fixed whitelists (label-injection guard) — the ONLY label values that can
#    ever reach `gh`. Parsed/LLM-derived values are validated against these. ──
ALLOWED_SOURCE_LABELS=" intel/sdk intel/api-docs intel/oss "
ALLOWED_PRIORITIES=" P1 P2 P3 "
ALLOWED_IMPACTS=" impact/blocking impact/capability impact/maintenance impact/fyi "
TRIAGE_LABEL="intel/needs-triage"
# Significance gate: build an Issue only when significant AND priority ≥ P2
# (P1 highest, P3 lowest → {P1,P2}). Everything else degrades to digest.
GATE_PRIORITIES=" P1 P2 "

die() { printf 'intel-judge: %s\n' "$1" >&2; exit 2; }
need_val() { [[ $# -ge 2 && -n "${2:-}" ]] || die "$1 requires a non-empty value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --deltas-file)       need_val "$@"; DELTAS_FILE="$2"; shift 2 ;;
    --context-file)      need_val "$@"; CONTEXT_FILE="$2"; shift 2 ;;
    --digest-file)       need_val "$@"; DIGEST_FILE="$2"; shift 2 ;;
    --repo)              need_val "$@"; REPO="$2"; shift 2 ;;
    --max-issues)        need_val "$@"; MAX_ISSUES="$2"; shift 2 ;;
    --llm-response-file) need_val "$@"; LLM_RESPONSE_FILE="$2"; shift 2 ;;
    --create-issue)      CREATE_ISSUE=1; shift ;;
    -h|--help)           sed -n '2,55p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)                   die "unknown argument: $1" ;;
  esac
done

[[ -n "$DELTAS_FILE" ]] || die "--deltas-file is required"
[[ -f "$DELTAS_FILE" ]] || die "deltas file not found: $DELTAS_FILE"
[[ "$MAX_ISSUES" =~ ^[0-9]+$ ]] || die "--max-issues must be a non-negative integer"
# Active dependency probes (not just `command -v`): on MSYS a tool can be present
# on PATH yet fail to execute (e.g. a winget shim without exec permission). Probe
# that each tool actually RUNS, so a broken interpreter fails fast here as a
# startup error (exit 2) rather than mid-run, where a `jq` failure would otherwise
# muddy the exit-code contract (a runtime fault must degrade, not look like usage).
jq -n null >/dev/null 2>&1 || die "jq is required and must be runnable"
if [[ -z "$LLM_RESPONSE_FILE" ]]; then
  curl --version >/dev/null 2>&1 || die "curl is required and must be runnable (unless --llm-response-file)"
fi
if [[ "$CREATE_ISSUE" -eq 1 ]]; then
  gh --version >/dev/null 2>&1 || die "gh is required and must be runnable for --create-issue"
fi
if [[ -n "$CONTEXT_FILE" && ! -f "$CONTEXT_FILE" ]]; then
  die "context file not found: $CONTEXT_FILE"
fi

# ── Mercury context fed to the LLM so it can judge relevance, not just novelty.
#    Double-quoted with trailing-backslash continuation (the line breaks collapse);
#    the text contains no $, backtick, or " so nothing expands. ──
MERCURY_CONTEXT="Mercury is a lightweight, modular multi-agent harness built on \
Claude Code. It depends on the Claude Code CLI (npm @anthropic-ai/claude-code), \
the Claude Agent SDK (npm @anthropic-ai/claude-agent-sdk), and the Anthropic \
Messages API. Significant = a change that could break the harness, deprecate \
something Mercury relies on, or offer a capability worth adopting. A routine \
patch bump with no substantive changelog is NOT significant."

SYSTEM_PROMPT="You are an external-intelligence triage assistant for the Mercury \
project. You are given one or more detected upstream deltas (version bumps / \
changelog hash changes). Judge whether the batch is worth a human-reviewed \
GitHub issue. Respond with EXACTLY one JSON object and nothing else (no prose, \
no markdown fences), with these keys: \
\"significant\" (boolean), \
\"priority\" (one of \"P1\",\"P2\",\"P3\"), \
\"impact\" (one of \"blocking\",\"capability\",\"maintenance\",\"fyi\"), \
\"summary\" (string, <=2 sentences), \
\"suggested_action\" (string, <=2 sentences). \
Be conservative: only mark significant=true when a human should look."

# ── digest degradation ── append a record so a delta is NEVER lost when the LLM
#    path can't produce a clean judgment or the Issue can't be created.
append_digest() {
  # $1 = reason, $2 = deltas JSON array
  local reason deltas="$2" ts
  # Keep the reason single-line: it can interpolate LLM-derived values (priority/
  # impact) which, if poisoned with newlines, would otherwise inject extra lines
  # into the digest markdown. The values stay inert (printf %s, never exec) — this
  # only keeps the digest well-formed.
  reason="$(printf '%s' "$1" | tr -d '\n\r')"
  ts="$(TZ=UTC date +%Y-%m-%dT%H:%M:%SZ)"
  [[ -n "$DIGEST_FILE" ]] || { printf 'intel-judge: %s (no --digest-file; %s deltas NOT persisted)\n' \
        "$reason" "$(jq 'length' <<<"$deltas")" >&2; return 1; }
  {
    printf '## intel digest entry — %s\n\n' "$ts"
    # `--` so a format string starting with '-' is not parsed as a printf option.
    printf -- '- reason: %s\n' "$reason"
    # Render deltas deterministically from the structured report (no LLM text).
    jq -r '.[] | "- delta: \(.source) (\(.kind)) \(.old) -> \(.new) [\(.label)]"' <<<"$deltas"
    printf '\n'
  } >> "$DIGEST_FILE" \
    || { printf 'intel-judge: failed to append digest %s\n' "$DIGEST_FILE" >&2; return 1; }
  printf 'degraded to digest (%s): %s delta(s) recorded in %s\n' \
    "$reason" "$(jq 'length' <<<"$deltas")" "$DIGEST_FILE"
}

# ── build the Anthropic request body via jq (escapes ALL injected text) ──
build_request_body() {
  # $1 = deltas JSON array; reads $CONTEXT_FILE if set.
  local deltas="$1" delta_lines context=""
  # Deterministic, structured delta summary — never raw LLM/command text.
  delta_lines="$(jq -r '.[] | "- \(.source) (\(.kind)): \(.old) -> \(.new)"' <<<"$deltas")"
  if [[ -n "$CONTEXT_FILE" ]]; then
    context="$(cat "$CONTEXT_FILE")"
  fi
  local user_prompt
  user_prompt="$(printf 'Mercury context:\n%s\n\nDetected deltas:\n%s\n' \
                   "$MERCURY_CONTEXT" "$delta_lines")"
  if [[ -n "$context" ]]; then
    user_prompt="$(printf '%s\nRelevant changelog/release excerpt:\n%s\n' \
                     "$user_prompt" "$context")"
  fi
  user_prompt="$(printf '%s\nReturn only the JSON object described in the system prompt.' \
                   "$user_prompt")"
  jq -n \
    --arg model "$MODEL" \
    --arg system "$SYSTEM_PROMPT" \
    --arg user "$user_prompt" \
    '{model:$model, max_tokens:1024, system:$system,
      messages:[{role:"user", content:$user}]}'
}

# ── call the API with bounded exponential backoff. Returns the raw response body
#    on a 2xx, non-zero otherwise. 4xx (except 429) is a client error and is NOT
#    retried (it won't fix itself); 429/5xx/transport failures are retried. ──
call_anthropic() {
  local body_file="$1" attempt=1 delay="$RETRY_BASE_DELAY" key
  key="${!API_KEY_ENV:-}"
  [[ -n "$key" ]] || { printf 'intel-judge: env %s is empty (no API key)\n' "$API_KEY_ENV" >&2; return 1; }
  while (( attempt <= MAX_RETRIES )); do
    local resp http payload
    # -w appends the HTTP status on its own trailing line; --data @file keeps the
    # (already jq-escaped) body off the command line.
    resp="$(curl -sS --max-time "$API_TIMEOUT" -X POST "$ANTHROPIC_API_URL" \
              -H "x-api-key: $key" \
              -H "anthropic-version: 2023-06-01" \
              -H "content-type: application/json" \
              --data @"$body_file" \
              -w $'\n%{http_code}' 2>/dev/null)"
    local curl_rc=$?
    http="${resp##*$'\n'}"
    payload="${resp%$'\n'*}"
    if [[ $curl_rc -eq 0 && "$http" == 2* ]]; then
      printf '%s' "$payload"; return 0
    fi
    if [[ $curl_rc -eq 0 && "$http" == 4* && "$http" != "429" ]]; then
      printf 'intel-judge: Anthropic API client error HTTP %s (not retried)\n' "$http" >&2
      return 1
    fi
    printf 'intel-judge: API attempt %s/%s failed (curl_rc=%s http=%s); retrying\n' \
      "$attempt" "$MAX_RETRIES" "$curl_rc" "${http:-none}" >&2
    (( attempt < MAX_RETRIES )) && [[ "$delay" != "0" ]] && sleep "$delay"
    delay=$(( delay * 2 )); attempt=$(( attempt + 1 ))
  done
  printf 'intel-judge: Anthropic API exhausted %s attempts\n' "$MAX_RETRIES" >&2
  return 1
}

# ── extract + validate the judgment JSON from an API response ──
parse_judgment() {
  # $1 = raw API response body → prints validated judgment JSON on stdout, or
  # non-zero if the response can't be turned into a well-formed judgment.
  local resp="$1" text judg
  text="$(jq -r '.content[]? | select(.type=="text") | .text' 2>/dev/null <<<"$resp")" || return 1
  [[ -n "$text" ]] || return 1
  # Tolerate a model that wraps JSON in ```json fences despite instructions.
  text="$(sed '/^[[:space:]]*```/d' <<<"$text")"
  # Must be EXACTLY ONE JSON object with all required keys of the right type.
  # Slurp (-s) so a multi-object stream or multi-text-block response (which jq -e
  # would otherwise validate object-by-object and accept) becomes a length>1 array
  # and is rejected → degrade, never a half-parsed judgment.
  judg="$(jq -se '
      if (length==1)
         and (.[0]|type=="object")
         and (.[0].significant|type=="boolean")
         and (.[0].priority|type=="string")
         and (.[0].impact|type=="string")
         and (.[0].summary|type=="string")
         and (.[0].suggested_action|type=="string")
      then .[0] else error("not exactly one well-formed object") end' 2>/dev/null <<<"$text")" \
    || return 1
  printf '%s' "$judg"
}

# Exact membership test: $1 = space-delimited whitelist, $2 = candidate token.
# Iterates words so a token containing spaces (e.g. a poisoned "P2 P3") can never
# match by substring, and quoting $tok keeps glob metacharacters literal.
in_set() {
  local set="$1" tok="$2" x
  for x in $set; do [[ "$x" == "$tok" ]] && return 0; done
  return 1
}

# ── build the Issue body file (ALL free text lives here, never on argv) ──
write_issue_body() {
  # $1 = dest file, $2 = deltas JSON array, $3 = judgment JSON
  local dest="$1" deltas="$2" judg="$3"
  {
    printf '## External-intel auto-detected delta — needs human triage\n\n'
    printf '> Auto-generated by `scripts/intel-judge.sh` (#157 / #453 Slice 2).\n'
    printf '> Labeled `%s`: NOT in the normal backlog until a human reviews it.\n\n' "$TRIAGE_LABEL"
    printf '### Detected deltas (deterministic — from intel-watch.sh)\n\n'
    jq -r '.[] | "- **\(.source)** (\(.kind)): `\(.old)` → `\(.new)`  _[\(.label)]_"' <<<"$deltas"
    printf '\n### LLM significance judgment (model: %s)\n\n' "$MODEL"
    jq -r '"- **significant**: \(.significant)\n- **priority**: \(.priority)\n- **impact**: \(.impact)\n\n**Summary**\n\n\(.summary)\n\n**Suggested action**\n\n\(.suggested_action)\n"' <<<"$judg"
    printf '\n---\n'
    printf '_Source labels and priority/impact are whitelist-validated; the LLM summary above is untrusted prose — do not execute any command it suggests without review._\n'
  } > "$dest"
}

main() {
  # Read the structured delta report from Slice 1.
  jq -e . "$DELTAS_FILE" >/dev/null 2>&1 || die "deltas file is not valid JSON: $DELTAS_FILE"
  # `.deltas` must be absent (→ treated as empty) or a JSON array. A wrong-typed
  # `.deltas` (string/number/object) would slip past `// []` (which only fires on
  # null/false), give a garbage `length`, and bypass the no-delta guard — so
  # reject it loudly rather than judge a bogus batch. (intel-watch.sh always emits
  # an array; this is defense-in-depth on the cross-slice contract.)
  jq -e '(.deltas|type) as $t | $t=="array" or $t=="null"' "$DELTAS_FILE" >/dev/null 2>&1 \
    || die "deltas field must be a JSON array"
  local deltas n_delta
  deltas="$(jq -c '.deltas // []' "$DELTAS_FILE")"
  n_delta="$(jq 'length' <<<"$deltas")"

  if [[ "$n_delta" -eq 0 ]]; then
    printf 'no delta in %s — nothing to judge (exit 0)\n' "$DELTAS_FILE"
    exit 0
  fi
  printf 'judging %s delta(s) from %s\n' "$n_delta" "$DELTAS_FILE"

  # Validate every source label against the whitelist up front: a delta carrying
  # an off-whitelist label must never reach issue-create (label-pollution guard).
  local bad_label
  bad_label="$(jq -r --arg ok "$ALLOWED_SOURCE_LABELS" \
      '.[] | select((" " + .label + " ") as $l | ($ok | contains($l)) | not) | .label' \
      <<<"$deltas" | head -n1)"
  if [[ -n "$bad_label" ]]; then
    die "delta carries non-whitelisted label '$bad_label' — refusing to proceed"
  fi

  # ── obtain the judgment: mock file (tests) or live API with retry ──
  local api_resp
  if [[ -n "$LLM_RESPONSE_FILE" ]]; then
    [[ -f "$LLM_RESPONSE_FILE" ]] || die "llm-response file not found: $LLM_RESPONSE_FILE"
    api_resp="$(cat "$LLM_RESPONSE_FILE")"
  else
    # Runtime temp/build failures degrade (delta preserved) rather than die: by
    # this point a delta exists and must land somewhere, so a transient I/O fault
    # must NOT exit 2 (which the contract reserves for startup/usage errors).
    local body_tmp
    if ! body_tmp="$(mktemp "${TMPDIR:-/tmp}/intel-judge-req.XXXXXX")"; then
      append_digest "request-temp-create-failed" "$deltas" || exit 3
      exit 0
    fi
    if ! build_request_body "$deltas" > "$body_tmp"; then
      rm -f "$body_tmp"
      append_digest "request-body-build-failed" "$deltas" || exit 3
      exit 0
    fi
    if ! api_resp="$(call_anthropic "$body_tmp")"; then
      rm -f "$body_tmp"
      # API failed after retries → degrade (delta preserved in digest).
      append_digest "anthropic-api-unavailable" "$deltas" || exit 3
      exit 0
    fi
    rm -f "$body_tmp"
  fi

  # ── parse + validate; any malformation degrades to digest (never crash) ──
  local judg
  if ! judg="$(parse_judgment "$api_resp")"; then
    append_digest "llm-response-unparseable" "$deltas" || exit 3
    exit 0
  fi

  local significant priority impact
  significant="$(jq -r '.significant' <<<"$judg")"
  priority="$(jq -r '.priority' <<<"$judg")"
  impact="$(jq -r '.impact' <<<"$judg")"

  # priority/impact must be in the closed enums — a poisoned value degrades.
  if ! in_set "$ALLOWED_PRIORITIES" "$priority"; then
    append_digest "llm-priority-off-whitelist:$priority" "$deltas" || exit 3
    exit 0
  fi
  if ! in_set "$ALLOWED_IMPACTS" "impact/$impact"; then
    append_digest "llm-impact-off-whitelist:$impact" "$deltas" || exit 3
    exit 0
  fi

  # ── significance gate (dedup layer 3 / ADR §5.2.3) ──
  if [[ "$significant" != "true" ]] || ! in_set "$GATE_PRIORITIES" "$priority"; then
    printf 'judged not actionable (significant=%s priority=%s) → digest, no Issue\n' \
      "$significant" "$priority"
    append_digest "below-significance-gate(significant=$significant,priority=$priority)" "$deltas" \
      || exit 3
    exit 0
  fi

  # ── significant + ≥P2 → build exactly one Issue (≤MAX_ISSUES) ──
  if [[ "$MAX_ISSUES" -lt 1 ]]; then
    printf 'significant batch but --max-issues=%s → digest instead of Issue\n' "$MAX_ISSUES"
    append_digest "max-issues-cap-zero" "$deltas" || exit 3
    exit 0
  fi

  # Labels: union of whitelisted source labels + validated priority + impact +
  # triage. Built ONLY from whitelist-validated values — no LLM/external text.
  local source_labels
  source_labels="$(jq -r '[.[].label] | unique | .[]' <<<"$deltas")"
  local -a label_args=()
  local lbl
  while IFS= read -r lbl; do
    [[ -n "$lbl" ]] || continue
    in_set "$ALLOWED_SOURCE_LABELS" "$lbl" || die "internal: label '$lbl' escaped whitelist check"
    label_args+=(--label "$lbl")
  done <<<"$source_labels"
  label_args+=(--label "$priority")
  label_args+=(--label "impact/$impact")
  label_args+=(--label "$TRIAGE_LABEL")

  # Deterministic title: fixed prefix + source-key list + delta count. The source
  # keys originate from intel-watch.sh's fixed SOURCES whitelist (repo literals),
  # and are passed as a single argv element — never concatenated into a command.
  local title src_csv n_src plural=""
  src_csv="$(jq -r '[.[].source] | unique | join(", ")' <<<"$deltas")"
  n_src="$(jq -r '[.[].source] | unique | length' <<<"$deltas")"
  [[ "$n_src" -gt 1 ]] && plural="s"
  title="[intel] delta needs triage: ${src_csv} (${n_src} source${plural})"

  local body_tmp
  # Runtime temp failure degrades (delta preserved), not exit 2 — see the request
  # temp note above; exit 2 is reserved for startup/usage errors.
  if ! body_tmp="$(mktemp "${TMPDIR:-/tmp}/intel-judge-body.XXXXXX")"; then
    append_digest "issue-body-temp-create-failed" "$deltas" || exit 3
    exit 0
  fi
  write_issue_body "$body_tmp" "$deltas" "$judg" \
    || { rm -f "$body_tmp"; append_digest "issue-body-write-failed" "$deltas" || exit 3; exit 0; }

  if [[ "$CREATE_ISSUE" -ne 1 ]]; then
    printf '(dry-run: would create 1 Issue; pass --create-issue to actually create)\n'
    printf '  title : %s\n' "$title"
    printf '  labels:'; printf ' %s' "${label_args[@]}"; printf '\n'
    printf '  body  : %s (%s bytes)\n' "$body_tmp" "$(wc -c <"$body_tmp" | tr -d ' ')"
    printf '  ---- body preview ----\n'
    sed 's/^/  | /' "$body_tmp"
    rm -f "$body_tmp"
    exit 0
  fi

  # Real creation. body via --body-file (no inline body); title/labels passed as
  # discrete argv elements (bash does not re-parse them as a command string).
  # KNOWN RESIDUAL (PoC-acceptable): gh-create is treated as all-or-nothing. If gh
  # ever exits non-zero AFTER the server already created the Issue, the delta is
  # ALSO appended to the digest → double-landed. The ≤1-Issue/run cap bounds it to
  # at most one duplicate, and Slice 3's cross-run open-Issue dedup (ADR §5.2
  # layer 2) closes it for good. Not fixable inside one un-idempotent gh call here.
  local -a repo_args=()
  [[ -n "$REPO" ]] && repo_args=(--repo "$REPO")
  if gh issue create "${repo_args[@]}" --title "$title" --body-file "$body_tmp" "${label_args[@]}" >/dev/null 2>&1; then
    printf 'created 1 Issue: %s\n' "$title"
    rm -f "$body_tmp"
    exit 0
  else
    rm -f "$body_tmp"
    # gh failed → degrade so the significant delta is not lost (ADR §7 step 4).
    append_digest "gh-issue-create-failed" "$deltas" || exit 3
    exit 0
  fi
}

main
