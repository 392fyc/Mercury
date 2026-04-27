#!/usr/bin/env bash
# Mercury lane auto-report (Issue #324, Phase C C1).
# Diffs lane-status.json snapshots and POSTs transition notifications to the
# mercury-channel-router /notify endpoint, which forwards to Telegram.
#
# Usage: bash scripts/lane-auto-report.sh
# Cron registration (Claude Code CronCreate, durable: true): see
# .mercury/docs/guides/phase-c-install.md §C1.
#
# Env vars:
#   MERCURY_LANE_REPORT_QUIET=1  — produce diffs but skip POST (dry-run)
#   MERCURY_ROUTER_PORT          — defaults to 8788
#   MERCURY_TEST_REPO_ROOT       — override repo root (test harness)
#   MERCURY_TEST_TOKEN_FILE      — override token file path (test harness)
#   MERCURY_TEST_NOTIFY_LOG      — append POST payloads here instead of curl (test harness)

set -euo pipefail

ROUTER_PORT="${MERCURY_ROUTER_PORT:-8788}"
QUIET="${MERCURY_LANE_REPORT_QUIET:-0}"
TOKEN_FILE="${MERCURY_TEST_TOKEN_FILE:-$HOME/.mercury/router.token}"
NOTIFY_LOG="${MERCURY_TEST_NOTIFY_LOG:-}"

# Resolve REPO_ROOT (same pattern as lane-status.sh).
if [ -n "${MERCURY_TEST_REPO_ROOT:-}" ]; then
  REPO_ROOT="$MERCURY_TEST_REPO_ROOT"
elif [ -n "${CLAUDE_PROJECT_DIR:-}" ] && [ -d "$CLAUDE_PROJECT_DIR/.git" ]; then
  REPO_ROOT="$CLAUDE_PROJECT_DIR"
else
  REPO_ROOT="$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || true)"
fi

if [ -z "${REPO_ROOT:-}" ]; then
  echo "[lane-auto-report] ERROR: cannot resolve repo root" >&2
  exit 1
fi

STATE_DIR="$REPO_ROOT/.mercury/state"
CURRENT="$STATE_DIR/lane-status.json"
PREVIOUS="$STATE_DIR/lane-status.previous.json"

if [ ! -f "$CURRENT" ]; then
  echo "[lane-auto-report] WARN: $CURRENT missing — run lane-status.sh first" >&2
  exit 0
fi

# Atomic single-instance lock via mkdir (POSIX-portable, MINGW-safe). If two cron
# ticks overlap, the loser exits cleanly without corrupting previous snapshot.
LOCKDIR="$STATE_DIR/lane-auto-report.lock.d"
if ! mkdir "$LOCKDIR" 2>/dev/null; then
  echo "[lane-auto-report] another instance running ($LOCKDIR exists); skipping" >&2
  exit 0
fi
trap 'rmdir "$LOCKDIR" 2>/dev/null || true' EXIT

# First run — seed previous and exit (no diff to report).
if [ ! -f "$PREVIOUS" ]; then
  cp "$CURRENT" "$PREVIOUS"
  echo "[lane-auto-report] seeded baseline; no diff on first run" >&2
  exit 0
fi

post_notify() {
  local severity="$1" title="$2" body="$3" label="$4"
  local payload
  # -c keeps each emit on a single line — needed so callers (tests, log scrapers)
  # can count emissions with `wc -l` and stream-parse with line-delimited JSON.
  payload=$(jq -nc \
    --arg sev "$severity" --arg ttl "$title" --arg bod "$body" --arg lbl "$label" \
    '{severity:$sev,title:$ttl,body:$bod,label:$lbl}')

  if [ -n "$NOTIFY_LOG" ]; then
    printf '%s\n' "$payload" >> "$NOTIFY_LOG"
    return 0
  fi
  if [ "$QUIET" = "1" ]; then
    echo "[lane-auto-report] (quiet) would POST: $title" >&2
    return 0
  fi
  if [ ! -r "$TOKEN_FILE" ]; then
    echo "[lane-auto-report] ERROR: cannot read token at $TOKEN_FILE" >&2
    return 1
  fi
  local token
  token="$(tr -d '\r\n' < "$TOKEN_FILE")"
  curl --silent --show-error --fail --max-time 5 \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json" \
    -X POST \
    --data "$payload" \
    "http://127.0.0.1:${ROUTER_PORT}/notify" >/dev/null \
    || { echo "[lane-auto-report] WARN: POST failed for $title" >&2; return 1; }
}

emit_count=0
emit_failed=0
emit() {
  if post_notify "$@"; then
    emit_count=$(( emit_count + 1 ))
  else
    emit_failed=$(( emit_failed + 1 ))
  fi
}

# Compute diff via jq — fields tracked: lane name, is_stale, issue numbers, branch refs+commits.
# CRLF strip on Windows MINGW: without `tr -d '\r'`, prev_lanes_csv ends with \r,
# the inside([...]) check below compares "main" vs "main\r" and misreports steady
# lanes as added/removed every cycle.
prev_lanes_csv="$(jq -r '[.lanes[].name] | sort | join(",")' "$PREVIOUS" | tr -d '\r')"
curr_lanes_csv="$(jq -r '[.lanes[].name] | sort | join(",")' "$CURRENT" | tr -d '\r')"

# NOTE: jq on Windows MINGW emits CRLF; every `jq -r` capture below is piped
# through `tr -d '\r'` so string comparisons (and --arg substitutions) match.

# 1. Added lanes
added=$(jq -r --arg p "$prev_lanes_csv" \
  '.lanes[] | select(([.name] | inside(($p | split(",")))) | not) | .name' "$CURRENT" | tr -d '\r')
while IFS= read -r lane; do
  [ -z "$lane" ] && continue
  emit info "lane:$lane spawned" "New lane registered" "$lane"
done <<< "$added"

# 2. Removed lanes
removed=$(jq -r --arg c "$curr_lanes_csv" \
  '.lanes[] | select(([.name] | inside(($c | split(",")))) | not) | .name' "$PREVIOUS" | tr -d '\r')
while IFS= read -r lane; do
  [ -z "$lane" ] && continue
  emit info "lane:$lane closed" "Lane no longer present" "$lane"
done <<< "$removed"

# 3. Per-lane field transitions (intersect set)
intersect=$(jq -r --arg p "$prev_lanes_csv" \
  '.lanes[] | select([.name] | inside(($p | split(",")))) | .name' "$CURRENT" | tr -d '\r')
while IFS= read -r lane; do
  [ -z "$lane" ] && continue

  prev_lane=$(jq --arg n "$lane" '.lanes[] | select(.name == $n)' "$PREVIOUS")
  curr_lane=$(jq --arg n "$lane" '.lanes[] | select(.name == $n)' "$CURRENT")

  # 3a. is_stale flip
  prev_stale=$(echo "$prev_lane" | jq -r '.is_stale' | tr -d '\r')
  curr_stale=$(echo "$curr_lane" | jq -r '.is_stale' | tr -d '\r')
  if [ "$prev_stale" != "$curr_stale" ]; then
    if [ "$curr_stale" = "true" ]; then
      emit info "lane:$lane stale" "Lane went idle (no activity > stale window)" "$lane"
    else
      emit info "lane:$lane active" "Lane resumed activity" "$lane"
    fi
  fi

  # 3b. Issue set change
  prev_issues=$(echo "$prev_lane" | jq -r '[.issues[].number] | sort | join(",")' | tr -d '\r')
  curr_issues=$(echo "$curr_lane" | jq -r '[.issues[].number] | sort | join(",")' | tr -d '\r')
  if [ "$prev_issues" != "$curr_issues" ]; then
    emit info "lane:$lane issues changed" "Issues: ${prev_issues:-none} -> ${curr_issues:-none}" "$lane"
  fi

  # 3c. Branch commit jump (per branch ref)
  while IFS= read -r ref; do
    [ -z "$ref" ] && continue
    prev_ts=$(echo "$prev_lane" | jq -r --arg r "$ref" '.branches[] | select(.ref == $r) | .last_commit_at // ""' | tr -d '\r')
    curr_ts=$(echo "$curr_lane" | jq -r --arg r "$ref" '.branches[] | select(.ref == $r) | .last_commit_at // ""' | tr -d '\r')
    if [ -n "$curr_ts" ] && [ "$prev_ts" != "$curr_ts" ]; then
      if [ -z "$prev_ts" ]; then
        emit info "lane:$lane branch added" "$ref @ $curr_ts" "$lane"
      else
        emit info "lane:$lane commit" "$ref: $prev_ts -> $curr_ts" "$lane"
      fi
    fi
  done < <(echo "$curr_lane" | jq -r '.branches[].ref' | tr -d '\r')
done <<< "$intersect"

# Promote current → previous only when all emissions succeeded. On partial
# failure, leave previous as-is so the next cron tick retries the same diff —
# better to re-emit a notification than silently drop one.
# Promotion uses mktemp+mv so a half-written previous is never readable by an
# overlapping reader (matches lane-status.sh atomic-write pattern).
if [ "$emit_failed" -eq 0 ]; then
  PROMOTE_TMP="$(mktemp "$STATE_DIR/lane-status.previous.json.XXXXXX")"
  cp "$CURRENT" "$PROMOTE_TMP" && mv "$PROMOTE_TMP" "$PREVIOUS"
  echo "[lane-auto-report] emitted $emit_count notifications" >&2
else
  echo "[lane-auto-report] emitted $emit_count, FAILED $emit_failed — previous snapshot retained for retry" >&2
  exit 1
fi
