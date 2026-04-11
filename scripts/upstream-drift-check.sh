#!/usr/bin/env bash
# Upstream drift check — reads .mercury/state/upstream-manifest.json and checks
# whether each cherry-picked artifact's upstream file has changed since import.
#
# Compares upstream file blob SHA at recorded import SHA vs current HEAD.
# Does NOT compare local copy (local files have Mercury adaptations/headers).
#
# Output per artifact: CLEAN | CHANGED | UPSTREAM_GONE | SKIP
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

echo "Upstream drift check — $count artifacts"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "---"

for ((i=0; i<count; i++)); do
  local_path=$(jq -r ".[$i].path" "$MANIFEST")
  upstream_repo=$(jq -r ".[$i].upstream_repo" "$MANIFEST")
  upstream_path=$(jq -r ".[$i].upstream_path" "$MANIFEST")
  recorded_sha=$(jq -r ".[$i].upstream_sha_at_import" "$MANIFEST")

  printf "%-72s " "$local_path"

  if [[ "$recorded_sha" == "UNKNOWN_VERIFY_MANUALLY" || "$recorded_sha" == "null" ]]; then
    echo "SKIP (no recorded SHA)"
    skipped=$((skipped + 1))
    continue
  fi

  # Get blob SHA of the file at the recorded import commit
  snap_blob=$(gh api "repos/$upstream_repo/contents/$upstream_path?ref=$recorded_sha" \
    --jq '.sha' 2>/dev/null || echo "")
  if [[ -z "$snap_blob" ]]; then
    echo "UPSTREAM_GONE (import SHA unreachable: $recorded_sha)"
    gone=$((gone + 1))
    continue
  fi

  # Get blob SHA of the file at upstream HEAD
  head_blob=$(gh api "repos/$upstream_repo/contents/$upstream_path" \
    --jq '.sha' 2>/dev/null || echo "")
  if [[ -z "$head_blob" ]]; then
    echo "UPSTREAM_GONE (file removed from upstream HEAD)"
    gone=$((gone + 1))
    continue
  fi

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
