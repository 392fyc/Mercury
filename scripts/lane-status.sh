#!/bin/bash
# Mercury cross-lane status aggregator (Issue #322, Phase A).
# Polls GitHub Issues with lane:* labels + last-commit timestamps on lane branches.
# Writes .mercury/state/lane-status.json with 15-min staleness gate.
# Atomic write via tmp file + mv to avoid mid-write read corruption.
#
# Usage: bash scripts/lane-status.sh [--print]
#   --print    Also emit compact summary table to stdout (default: silent for cron)
#
# ## Cron registration (via Claude Code CronCreate tool — do NOT run from script)
#
#   CronCreate:
#     cron: "*/5 * * * *"
#     durable: true   # MANDATORY — survives session restart (per Issue #320 acceptance)
#     prompt: |
#       Run scripts/lane-status.sh --print from the Mercury repo root.
#       If lane-status.json shows any is_stale: true, append a line to
#       .mercury/state/stale-lanes.log with ISO timestamp + lane name.
#
# Note: durable: true is required so the cron survives claude session restarts.
# Verify registration post-restart with: gh api ... | jq '.cron_jobs'

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STALE_MIN=${MERCURY_LANE_STALE_MIN:-15}
PRINT_SUMMARY=false
for arg in "$@"; do
  [ "$arg" = "--print" ] && PRINT_SUMMARY=true
done

# ---------------------------------------------------------------------------
# Resolve REPO_ROOT
# Same pattern as statusline-mercury.sh; supports MERCURY_TEST_REPO_ROOT override.
# ---------------------------------------------------------------------------
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
OUTPUT_FILE="$STATE_DIR/lane-status.json"
TMP_FILE="$STATE_DIR/lane-status.json.tmp"

mkdir -p "$STATE_DIR"

# ---------------------------------------------------------------------------
# Discover lane labels
# Mercury convention: labels named "lane:*" (e.g. lane:main, lane:side-multi-lane).
# `gh issue list --label` requires exact label name; we enumerate via `gh label list`.
# Note: GitHub API --label filter matches exact strings only, no wildcard support.
# ---------------------------------------------------------------------------
lane_labels=$(gh label list --limit 100 --json name --jq '[.[] | select(.name | startswith("lane:")) | .name]')
lane_label_count=$(echo "$lane_labels" | jq 'length')

if [ "$lane_label_count" -eq 0 ]; then
  echo "[lane-status] WARNING: No lane:* labels found. Writing empty lanes array." >&2
fi

# ---------------------------------------------------------------------------
# Enumerate live lane branches from remote
# Pattern: refs/heads/feature/lane-<name>/...
# ---------------------------------------------------------------------------
remote_refs=$(git -C "$REPO_ROOT" ls-remote origin 'refs/heads/feature/lane-*' 2>/dev/null || true)

# ---------------------------------------------------------------------------
# Compute staleness threshold timestamp (seconds since epoch)
# ---------------------------------------------------------------------------
now=$(date +%s)
stale_cutoff=$(( now - STALE_MIN * 60 ))

# ---------------------------------------------------------------------------
# Build lanes JSON array
# ---------------------------------------------------------------------------
lanes_json="[]"

# Iterate over each lane label
while IFS= read -r label_name; do
  # Strip "lane:" prefix to get the lane id
  lane_id="${label_name#lane:}"

  # Fetch open Issues with this label
  issues_json=$(gh issue list \
    --label "$label_name" \
    --state open \
    --json number,title,labels,updatedAt \
    --limit 50 2>/dev/null || echo "[]")

  # Normalize: extract relevant fields only
  issues_normalized=$(echo "$issues_json" | jq '[.[] | {number: .number, title: .title, updated_at: .updatedAt}]')

  # Find matching branches: feature/lane-<lane_id>/...
  # lane_id may contain hyphens; branch prefix is feature/lane-<lane_id>/
  branch_prefix="feature/lane-${lane_id}/"
  branches_json="[]"

  while IFS= read -r ref_line; do
    [ -z "$ref_line" ] && continue
    # ref_line format: "<sha>\trefs/heads/<branch>"
    ref_branch=$(echo "$ref_line" | awk '{print $2}' | sed 's|refs/heads/||')
    # Get last commit timestamp for this branch (ISO 8601)
    last_commit_at=$(git -C "$REPO_ROOT" log -1 --format=%cI "origin/${ref_branch}" 2>/dev/null || true)
    if [ -n "$last_commit_at" ]; then
      branches_json=$(echo "$branches_json" | jq \
        --arg ref "$ref_branch" \
        --arg ts "$last_commit_at" \
        '. + [{"ref": $ref, "last_commit_at": $ts}]')
    fi
  done < <(echo "$remote_refs" | grep "refs/heads/${branch_prefix}" || true)

  # Compute is_stale:
  # A lane is stale if its most-recent issue update AND most-recent branch commit
  # are both older than STALE_MIN minutes (or if there are no issues AND no branches).
  most_recent_issue_epoch=0
  if [ "$(echo "$issues_normalized" | jq 'length')" -gt 0 ]; then
    most_recent_issue_ts=$(echo "$issues_normalized" | jq -r '[.[].updated_at] | max')
    if [ -n "$most_recent_issue_ts" ] && [ "$most_recent_issue_ts" != "null" ]; then
      most_recent_issue_epoch=$(date -d "$most_recent_issue_ts" +%s 2>/dev/null || \
                                date -j -f "%Y-%m-%dT%H:%M:%SZ" "$most_recent_issue_ts" +%s 2>/dev/null || \
                                echo 0)
    fi
  fi

  most_recent_branch_epoch=0
  if [ "$(echo "$branches_json" | jq 'length')" -gt 0 ]; then
    most_recent_branch_ts=$(echo "$branches_json" | jq -r '[.[].last_commit_at] | max')
    if [ -n "$most_recent_branch_ts" ] && [ "$most_recent_branch_ts" != "null" ]; then
      most_recent_branch_epoch=$(date -d "$most_recent_branch_ts" +%s 2>/dev/null || \
                                 date -j -f "%Y-%m-%dT%H:%M:%SZ" "$most_recent_branch_ts" +%s 2>/dev/null || \
                                 echo 0)
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

done < <(echo "$lane_labels" | jq -r '.[]')

# ---------------------------------------------------------------------------
# Build final JSON and write atomically
# ---------------------------------------------------------------------------
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

echo "$final_json" > "$TMP_FILE"
mv "$TMP_FILE" "$OUTPUT_FILE"

# ---------------------------------------------------------------------------
# Optional --print summary table
# ---------------------------------------------------------------------------
if [ "$PRINT_SUMMARY" = "true" ]; then
  echo "Lane Status — $(date -u '+%Y-%m-%dT%H:%M:%SZ') (stale > ${STALE_MIN}m)"
  echo "---------------------------------------------------------------"
  echo "$final_json" | jq -r '.lanes[] | "\(.name)\t issues:\((.issues | length))\t branches:\((.branches | length))\t stale:\(.is_stale)"'
  echo "---------------------------------------------------------------"
  echo "Written: $OUTPUT_FILE"
fi
