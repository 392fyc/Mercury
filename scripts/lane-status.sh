#!/usr/bin/env bash
# Mercury cross-lane status aggregator (Issue #322, Phase A).
# Polls GitHub Issues with lane:* labels + last-commit timestamps on lane branches.
# Writes .mercury/state/lane-status.json with 15-min staleness gate.
# Atomic write via mktemp + mv to avoid mid-write read corruption.
#
# Also reads the project's lane registry (LANES.md, Issue #616) so each lane
# carries its registered Harness / Session / Peers / Status / Branch /
# Worktree path, and registered lanes without a lane:* label still show up.
#
# Usage: bash scripts/lane-status.sh [--print] [--lanes-file PATH]
#   --lanes-file  registry to read (default: $MERCURY_LANES_FILE, else the
#                 project's lane home via scripts/lib/lane-paths.sh)
# Cron registration (via Claude Code CronCreate, durable: true): see
# .mercury/docs/guides/phase-a-install.md §A2.

set -euo pipefail

# Config — numeric-guard MERCURY_LANE_STALE_MIN so a malformed env var (e.g. "abc")
# doesn't crash arithmetic eval downstream or inject garbage into jq --argjson.
STALE_MIN=${MERCURY_LANE_STALE_MIN:-15}
case "$STALE_MIN" in
  ''|*[!0-9]*) STALE_MIN=15 ;;
esac
PRINT_SUMMARY=false
LANES_FILE="${MERCURY_LANES_FILE:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --print) PRINT_SUMMARY=true; shift ;;
    --lanes-file) shift; [ $# -gt 0 ] && [ -n "$1" ] || { echo "[lane-status] ERROR: --lanes-file needs a value" >&2; exit 2; }
                  LANES_FILE="$1"; shift ;;
    *) shift ;;  # unknown args ignored, as before
  esac
done

# Resolve REPO_ROOT (same pattern as statusline-mercury.sh; MERCURY_TEST_REPO_ROOT override).
if [ -n "${MERCURY_TEST_REPO_ROOT:-}" ]; then
  REPO_ROOT="$MERCURY_TEST_REPO_ROOT"
elif [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR/.git" ]; then
  REPO_ROOT="$CLAUDE_PROJECT_DIR"
else
  REPO_ROOT="$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null)"
fi

if [ -z "${REPO_ROOT:-}" ]; then
  echo "[lane-status] ERROR: Cannot resolve repo root. Run from inside the Mercury repo." >&2
  exit 1
fi

STATE_DIR="$REPO_ROOT/.mercury/state"

mkdir -p "$STATE_DIR"

# m1: mktemp for unique tmp file — avoids fixed-name collision under concurrent cron.
TMP_FILE="$(mktemp "$STATE_DIR/lane-status.json.XXXXXX")"
trap 'rm -f "$TMP_FILE"' EXIT

# Source portable date helpers (M1 fix: BSD/macOS date portability).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/date-utils.sh
source "$SCRIPT_DIR/lib/date-utils.sh"
# shellcheck source=lib/lane-paths.sh
source "$SCRIPT_DIR/lib/lane-paths.sh"

# Lane registry (Issue #616). Missing or unreadable -> warn, continue with
# labels/branches only (the cron must never abort on it).
if [ -z "$LANES_FILE" ]; then
  _md="$(lane_memory_dir "$REPO_ROOT" 2>/dev/null || true)"
  [ -n "$_md" ] && LANES_FILE="$_md/LANES.md"
fi
registry_json="{}"
if [ -n "$LANES_FILE" ] && [ -f "$LANES_FILE" ]; then
  # Emit "<lane>\t<field>\t<value>" for every field bullet of a `### \`<lane>\``
  # section under "## Active Lanes" (fenced blocks skipped). Bullets may carry
  # a note before the colon, as lane-assertion.sh and the GUI parser accept:
  #   - **Worktree path** (per Rule 5.1): `D:/x` (materialized 2026-05-04)
  # Any other ### heading ends the current lane section.
  registry_tsv="$(tr -d '\r' < "$LANES_FILE" | awk '
    /^```/ { fence = !fence; next }
    fence { next }
    /^## / { active = ($0 ~ /^## Active Lanes/); lane = ""; next }
    active && /^### / {
      lane = ""
      if (match($0, /^### `[^`]+`/)) { lane = substr($0, 6, RLENGTH - 6); print lane "\t\t" }
      next
    }
    active && lane != "" && /^[[:space:]]*- \*\*[^*]+\*\*( \([^)]*\))?:/ {
      line = $0; sub(/^[[:space:]]*- \*\*/, "", line)
      field = line; sub(/\*\*.*/, "", field)
      val = line; sub(/^[^*]*\*\*( \([^)]*\))?:[[:space:]]*/, "", val)
      print lane "\t" field "\t" val
    }' || true)"
  # Values: the first backticked group when there is one (notes after it are
  # dropped, as in lane-assertion.sh); Peers: every backticked group, else a
  # comma list. The first occurrence of a field wins (lane-close.sh parity).
  # Every lane gets the same keys; absent fields are null.
  registry_json="$(printf '%s\n' "$registry_tsv" | jq -R -s '
    def trim: gsub("^\\s+|\\s+$"; "");
    def first_value: if test("`[^`]+`") then (capture("`(?<v>[^`]+)`").v | trim) else trim end;
    def peer_list: if test("`[^`]+`") then [scan("`([^`]+)`")[0] | trim] else (split(",") | map(trim)) end
                   | map(select(length > 0));
    def key: {"Short name":"short","Harness":"harness","Session":"session","Peers":"peers",
              "Status":"status","Branch":"branch","Worktree path":"worktree","Handoff file":"handoff",
              "Inbox":"inbox"}[.];
    [ split("\n")[] | select(length > 0) | split("\t") ]
    | reduce .[] as $r ({}; .[$r[0]] += {}
        | ($r[1] // "" | if . == "" then null else key end) as $k
        | if $k != null and (.[$r[0]] | has($k) | not)
          then .[$r[0]][$k] = ($r[2] // "" | if $k == "peers" then peer_list else first_value end)
          else . end)
    | with_entries(.value |= (
        {short: null, harness: null, session: null, peers: [], status: null,
         branch: null, worktree: null, handoff: null, inbox: null} + .
        | .harness = ((.harness // "") | ascii_downcase | if . == "" then "claude" else . end)))
  ' 2>/dev/null | tr -d '\r' || echo '{}')"
  [ -n "$registry_json" ] || registry_json='{}'
  [ "$registry_json" = "{}" ] && echo "[lane-status] WARN: no lanes parsed from $LANES_FILE" >&2
else
  echo "[lane-status] WARN: lane registry not found (${LANES_FILE:-unresolved}); registry fields omitted" >&2
fi

# Discover lane labels (convention: lane:* — e.g. lane:main, lane:side-multi-lane).
# M2: tolerate gh + jq failure so script never aborts on transient network issues.
labels_raw="$(gh label list --limit 100 --json name 2>/dev/null || echo '[]')"
# Strip \r to handle Windows CRLF in gh output under MINGW.
lane_labels="$(echo "$labels_raw" | jq -r '[.[] | select(.name | startswith("lane:")) | .name] // []' 2>/dev/null | tr -d '\r' || echo '[]')"

lane_label_count=$(echo "$lane_labels" | jq 'length' 2>/dev/null || echo 0)

if [ "$lane_label_count" -eq 0 ]; then
  echo "[lane-status] WARN: no lane:* labels detected (gh failure or empty result)" >&2
  # Still write valid lane-status.json with empty lanes array below.
fi

# Enumerate live lane branches from remote (refs/heads/feature/lane-<name>/...).
# M2: tolerate git ls-remote failure; tr -d '\r' for MINGW CRLF.
remote_refs="$(git -C "$REPO_ROOT" ls-remote origin 'refs/heads/feature/lane-*' 'refs/heads/lane/*' 2>/dev/null | tr -d '\r' || true)"

now=$(date +%s)
stale_cutoff=$(( now - STALE_MIN * 60 ))

lanes_json="[]"

# Lane ids: every lane:* label, then registered lanes without a label.
label_ids="$(echo "$lane_labels" | jq -r '.[]' 2>/dev/null | tr -d '\r' | sed 's/^lane://' || true)"
registry_ids="$(echo "$registry_json" | jq -r 'keys_unsorted[]' 2>/dev/null | tr -d '\r' || true)"

# Iterate over each lane
while IFS= read -r lane_id; do
  [ -z "$lane_id" ] && continue
  label_name="lane:${lane_id}"
  lane_registry="$(echo "$registry_json" | jq -c --arg n "$lane_id" '.[$n] // null' 2>/dev/null || echo null)"

  # M2: tolerate gh issue list failure — fall back to empty array.
  # A registered lane without a label has no Issues to look up.
  if printf '%s\n' "$label_ids" | grep -Fxq -- "$lane_id"; then
    issues_json="$(gh issue list \
      --label "$label_name" \
      --state open \
      --json number,title,labels,updatedAt \
      --limit 50 2>/dev/null || echo '[]')"
  else
    issues_json='[]'
  fi

  # Normalize: extract relevant fields only
  issues_normalized="$(echo "$issues_json" | jq '[.[] | {number: .number, title: .title, updated_at: .updatedAt}]' 2>/dev/null || echo '[]')"

  # Find matching branches: feature/lane-<lane_id>/...
  # lane_id may contain hyphens; branch prefix is feature/lane-<lane_id>/
  branch_prefix="feature/lane-${lane_id}/"
  # Rule 2.1 branches: lane/<short>/..., short name from the registry.
  # No registered Short name: fall back to the lane name, as lane-assertion.sh does.
  lane_short="$(echo "$lane_registry" | jq -r '.short // empty' 2>/dev/null | tr -d '\r' || true)"
  [ -n "$lane_short" ] || lane_short="$lane_id"
  short_prefix="lane/${lane_short}/"
  branches_json="[]"

  while IFS= read -r ref_line; do
    [ -z "$ref_line" ] && continue
    # ref_line format: "<sha>\trefs/heads/<branch>"
    ref_branch=$(echo "$ref_line" | awk '{print $2}' | sed 's|refs/heads/||' | tr -d '\r')
    # M1: TZ=UTC + --date=format-local emits the timestamp in UTC, then the
    # literal Z suffix is honest. Without TZ=UTC, format-local would format in
    # the local zone but still tack on Z, mislabeling the value (Copilot finding).
    last_commit_at=$(TZ=UTC git -C "$REPO_ROOT" log -1 \
      --date=format-local:'%Y-%m-%dT%H:%M:%SZ' \
      --format=%cd \
      "origin/${ref_branch}" 2>/dev/null | tr -d '\r' || true)
    # Fallback: if format-local unsupported, use %cI and let parse_epoch normalize
    if [ -z "$last_commit_at" ]; then
      last_commit_at=$(git -C "$REPO_ROOT" log -1 --format=%cI "origin/${ref_branch}" 2>/dev/null | tr -d '\r' || true)
    fi
    if [ -n "$last_commit_at" ]; then
      branches_json=$(echo "$branches_json" | jq \
        --arg ref "$ref_branch" \
        --arg ts "$last_commit_at" \
        '. + [{"ref": $ref, "last_commit_at": $ts}]')
    fi
  # m2: use grep -F for fixed-string lane_id lookup — prevents regex metachars in
  # lane_id (e.g. "." or "+") from causing mis-matches.
  done < <(echo "$remote_refs" | grep -F -e "refs/heads/${branch_prefix}" ${short_prefix:+-e "refs/heads/${short_prefix}"} || true)

  # Compute is_stale:
  # A lane is stale if its most-recent issue update AND most-recent branch commit
  # are both older than STALE_MIN minutes (or if there are no issues AND no branches).
  most_recent_issue_epoch=0
  if [ "$(echo "$issues_normalized" | jq 'length')" -gt 0 ]; then
    # tr -d '\r': jq on Windows MINGW emits CRLF; strip before date parsing.
    most_recent_issue_ts=$(echo "$issues_normalized" | jq -r '[.[].updated_at] | max' | tr -d '\r')
    if [ -n "$most_recent_issue_ts" ] && [ "$most_recent_issue_ts" != "null" ]; then
      most_recent_issue_epoch=$(parse_epoch "$most_recent_issue_ts")
    fi
  fi

  most_recent_branch_epoch=0
  if [ "$(echo "$branches_json" | jq 'length')" -gt 0 ]; then
    most_recent_branch_ts=$(echo "$branches_json" | jq -r '[.[].last_commit_at] | max' | tr -d '\r')
    if [ -n "$most_recent_branch_ts" ] && [ "$most_recent_branch_ts" != "null" ]; then
      most_recent_branch_epoch=$(parse_epoch "$most_recent_branch_ts")
    fi
  fi

  # Most recent activity across both sources
  most_recent_epoch=$most_recent_issue_epoch
  if [ "$most_recent_branch_epoch" -gt "$most_recent_epoch" ]; then
    most_recent_epoch=$most_recent_branch_epoch
  fi

  is_stale=false
  if [ "$most_recent_epoch" -eq 0 ] || [ "$most_recent_epoch" -lt "$stale_cutoff" ]; then
    is_stale=true
  fi

  # Append this lane to the lanes array
  lanes_json=$(echo "$lanes_json" | jq \
    --arg name "$lane_id" \
    --argjson issues "$issues_normalized" \
    --argjson branches "$branches_json" \
    --argjson stale "$is_stale" \
    --argjson registry "$lane_registry" \
    '. + [{"name": $name, "issues": $issues, "branches": $branches, "is_stale": $stale, "registry": $registry}]')

done < <({ printf '%s\n' "$label_ids"; printf '%s\n' "$registry_ids" | grep -Fvx -f <(printf '%s\n' "$label_ids" | sed '/^$/d') || true; } | sed '/^$/d')

# Build final JSON and write atomically.
last_checked_at=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

final_json=$(jq -n \
  --arg ts "$last_checked_at" \
  --argjson stale_min "$STALE_MIN" \
  --argjson lanes "$lanes_json" \
  '{
    last_checked_at: $ts,
    stale_threshold_minutes: $stale_min,
    lanes: $lanes
  }')

OUTPUT_FILE="$STATE_DIR/lane-status.json"
echo "$final_json" > "$TMP_FILE"
# Atomic rename. Disarm the EXIT trap only AFTER mv succeeds so a failed mv
# (permission denied, ENOSPC, …) still triggers the cleanup trap and removes
# the orphaned temp file.
mv "$TMP_FILE" "$OUTPUT_FILE" && trap - EXIT

# Optional --print summary table.
if [ "$PRINT_SUMMARY" = "true" ]; then
  echo "Lane Status — $(date -u '+%Y-%m-%dT%H:%M:%SZ') (stale > ${STALE_MIN}m)"
  echo "---------------------------------------------------------------"
  echo "$final_json" | jq -r '.lanes[] | "\(.name)\t harness:\(.registry.harness // "-")\t peers:\((.registry.peers // []) | if length == 0 then "-" else join(",") end)\t issues:\((.issues | length))\t branches:\((.branches | length))\t stale:\(.is_stale)"'
  echo "---------------------------------------------------------------"
  echo "Written: $OUTPUT_FILE"
fi
