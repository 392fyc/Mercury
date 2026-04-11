#!/usr/bin/env bash
# Upstream drift check — reads .mercury/state/upstream-manifest.json and checks
# whether each cherry-picked artifact's upstream file has changed since import.
#
# Compares upstream file blob SHA at recorded import SHA vs current HEAD.
# Does NOT compare local copy (local files have Mercury adaptations/headers).
#
# Supports two deployment scopes tracked in manifest:
#   scope=project  — file lives in the repo (path relative to repo root)
#   scope=user     — file lives in user-global dir (e.g. ~/.claude/skills/)
#
# Output per artifact: CLEAN | CHANGED | UPSTREAM_GONE | SKIP
# Summary includes per-scope counts.
#
# Run manually: bash scripts/upstream-drift-check.sh
# DO NOT wire into CI or kb-lint — follow-up automation is tracked separately.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MANIFEST="$REPO_ROOT/.mercury/state/upstream-manifest.json"

if [[ ! -f "$MANIFEST" ]]; then
  echo "ERROR: manifest not found at $MANIFEST" >&2
  exit 1
fi

for cmd in jq gh; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd is required" >&2
    exit 1
  fi
done

count=$(jq 'length' "$MANIFEST")
clean=0; changed=0; gone=0; skipped=0
scope_project=0; scope_user=0

echo "Upstream drift check — $count artifacts"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "---"

for ((i=0; i<count; i++)); do
  local_path=$(jq -r ".[$i].path" "$MANIFEST")
  scope=$(jq -r ".[$i].scope // \"project\"" "$MANIFEST")
  upstream_repo=$(jq -r ".[$i].upstream_repo" "$MANIFEST")
  upstream_path=$(jq -r ".[$i].upstream_path" "$MANIFEST")
  recorded_sha=$(jq -r ".[$i].upstream_sha_at_import" "$MANIFEST")

  # Tally scope counts
  if [[ "$scope" == "user" ]]; then
    scope_user=$((scope_user + 1))
  else
    scope_project=$((scope_project + 1))
  fi

  printf "[%-7s] %-65s " "$scope" "$local_path"

  if [[ "$recorded_sha" == "UNKNOWN_VERIFY_MANUALLY" || "$recorded_sha" == "null" ]]; then
    echo "SKIP (no recorded SHA)"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ -z "$upstream_repo" || "$upstream_repo" == "null" || \
        -z "$upstream_path" || "$upstream_path" == "null" ]]; then
    echo "SKIP (manifest missing upstream_repo or upstream_path)"
    skipped=$((skipped + 1))
    continue
  fi

  # URL-encode upstream_path to handle spaces, #, ? and other special characters
  encoded_path=$(jq -rn --arg p "$upstream_path" '$p | @uri')

  # Get blob SHA of the file at the recorded import commit
  snap_err="$(mktemp)"
  snap_blob=$(gh api "repos/$upstream_repo/contents/$encoded_path?ref=$recorded_sha" \
    --jq '.sha' 2>"$snap_err" || true)
  if [[ -z "$snap_blob" ]]; then
    if grep -q "404" "$snap_err" 2>/dev/null; then
      echo "UPSTREAM_GONE (import SHA unreachable: $recorded_sha)"
      gone=$((gone + 1))
    else
      echo "SKIP (gh api error checking import SHA — not 404)"
      skipped=$((skipped + 1))
    fi
    rm -f "$snap_err"
    continue
  fi
  rm -f "$snap_err"

  # Get blob SHA of the file at upstream HEAD
  head_err="$(mktemp)"
  head_blob=$(gh api "repos/$upstream_repo/contents/$encoded_path" \
    --jq '.sha' 2>"$head_err" || true)
  if [[ -z "$head_blob" ]]; then
    if grep -q "404" "$head_err" 2>/dev/null; then
      echo "UPSTREAM_GONE (file removed from upstream HEAD)"
      gone=$((gone + 1))
    else
      echo "SKIP (gh api error checking upstream HEAD — not 404)"
      skipped=$((skipped + 1))
    fi
    rm -f "$head_err"
    continue
  fi
  rm -f "$head_err"

  if [[ "$snap_blob" == "$head_blob" ]]; then
    echo "CLEAN"
    clean=$((clean + 1))
  else
    echo "CHANGED (upstream updated since import)"
    changed=$((changed + 1))
  fi
done

echo "---"
echo "Summary: CLEAN=$clean  CHANGED=$changed  UPSTREAM_GONE=$gone  SKIP=$skipped  total=$count"
echo "Scopes:  project=$scope_project  user=$scope_user"
