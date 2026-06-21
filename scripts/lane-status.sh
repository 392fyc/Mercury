#!/usr/bin/env bash
# Mercury cross-lane status aggregator (Issue #322, Phase A).
# Polls GitHub Issues with lane:* labels + last-commit timestamps on lane branches.
# Writes .mercury/state/lane-status.json with 15-min staleness gate.
# Atomic write via mktemp + mv to avoid mid-write read corruption.
#
# Usage: bash scripts/lane-status.sh [--print]
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
for arg in "$@"; do
  [ "$arg" = "--print" ] && PRINT_SUMMARY=true
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
remote_refs="$(git -C "$REPO_ROOT" ls-remote origin 'refs/heads/feature/lane-*' 2>/dev/null | tr -d '\r' || true)"

now=$(date +%s)
stale_cutoff=$(( now - STALE_MIN * 60 ))

lanes_json="[]"

# Iterate over each lane label
while IFS= read -r label_name; do
  [ -z "$label_name" ] && continue

  # Strip "lane:" prefix to get the lane id
  lane_id="${label_name#lane:}"

  # M2: tolerate gh issue list failure — fall back to empty array
  issues_json="$(gh issue list \
    --label "$label_name" \
    --state open \
    --json number,title,labels,updatedAt \
    --limit 50 2>/dev/null || echo '[]')"

  # Normalize: extract relevant fields only
  issues_normalized="$(echo "$issues_json" | jq '[.[] | {number: .number, title: .title, updated_at: .updatedAt}]' 2>/dev/null || echo '[]')"

  # Find matching branches: feature/lane-<lane_id>/...
  # lane_id may contain hyphens; branch prefix is feature/lane-<lane_id>/
  branch_prefix="feature/lane-${lane_id}/"
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
  done < <(echo "$remote_refs" | grep -F "refs/heads/${branch_prefix}" || true)

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
    '. + [{"name": $name, "issues": $issues, "branches": $branches, "is_stale": $stale}]')

# tr -d '\r': jq on Windows MINGW emits CRLF line endings; strip before read.
done < <(echo "$lane_labels" | jq -r '.[]' 2>/dev/null | tr -d '\r' || true)

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
  echo "$final_json" | jq -r '.lanes[] | "\(.name)\t issues:\((.issues | length))\t branches:\((.branches | length))\t stale:\(.is_stale)"'
  echo "---------------------------------------------------------------"
  echo "Written: $OUTPUT_FILE"
fi
