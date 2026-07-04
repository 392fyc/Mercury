#!/usr/bin/env bash
# Upstream drift check — aggregates upstream-manifest.json from all deployment
# layers and checks whether each cherry-picked artifact's upstream file has
# changed since import.
#
# Manifest discovery (merged in order):
#   project-scope: $REPO_ROOT/.mercury/state/upstream-manifest.json
#   user-scope:    ~/.claude/upstream-manifest.json  (if present)
#
# Compares upstream file blob SHA at recorded import SHA vs current HEAD.
# Does NOT compare local copy (local files have Mercury adaptations/headers).
#
# scope field in manifest:
#   project — artifact lives in the repo (path relative to repo root)
#   user    — artifact lives in user-global dir (~/.claude/...)
#
# Output per artifact: CLEAN | CHANGED | UPSTREAM_GONE | SKIP
# Summary includes per-scope counts.
#
# Flags:
#   --write-back   After checking, stamp last_drift_check (UTC date) on every
#                  PROJECT-manifest entry, recording that a drift review ran.
#                  Use locally / in a PR — NOT from CI (CI must not push to
#                  develop; the scheduled .github/workflows/upstream-drift.yml
#                  runs read-only and files a tracking Issue instead).
#
# Exit codes (so a scheduler/CI can gate on drift):
#   0  no drift (all CLEAN/SKIP)
#   1  at least one CHANGED
#   2  at least one UPSTREAM_GONE (most severe; takes precedence over CHANGED)
#   3  --write-back failed (manifest left unchanged)
#   5  prerequisite missing (jq/gh not installed) or no manifest found
#   64 usage error (bad argument)
# NOTE: drift is signalled ONLY by 1/2. Setup/precondition failures use 5 (not 1)
# so a caller cannot mistake "could not run" for "CHANGED drift" — see the
# rc-mapping in .github/workflows/upstream-drift.yml.
#
# Run manually:        bash scripts/upstream-drift-check.sh
# Backfill timestamps: bash scripts/upstream-drift-check.sh --write-back
#
# Tier 1 (mechanical/deterministic) of Mercury's upstream-staleness mechanism
# (Issue #508). Tier 2 is the periodic /mercury-staleness-audit LLM workflow
# (dead-component + version-lag judgment). Cadence + triage protocol:
# .mercury/docs/guides/upstream-drift-routine.md.

set -euo pipefail

WRITE_BACK=0
usage() {
  echo "Usage: bash scripts/upstream-drift-check.sh [--write-back]"
  echo "  --write-back  stamp last_drift_check (UTC date) on project-manifest entries after checking"
  echo "Exit: 0 clean · 1 CHANGED present · 2 UPSTREAM_GONE present · 3 write-back failed · 5 prerequisite/no-manifest · 64 usage error"
}
for arg in "$@"; do
  case "$arg" in
    --write-back) WRITE_BACK=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown argument '$arg'" >&2; usage >&2; exit 64 ;;
  esac
done

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_MANIFEST="$REPO_ROOT/.mercury/state/upstream-manifest.json"
USER_MANIFEST="${HOME}/.claude/upstream-manifest.json"

for cmd in jq gh; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: $cmd is required" >&2
    exit 5  # prerequisite missing — distinct from the 1/2 drift codes
  fi
done

# Discover and merge manifests from all layers
manifest_files=()
[[ -f "$PROJECT_MANIFEST" ]] && manifest_files+=("$PROJECT_MANIFEST")
[[ -f "$USER_MANIFEST" ]]    && manifest_files+=("$USER_MANIFEST")

if [[ ${#manifest_files[@]} -eq 0 ]]; then
  echo "ERROR: no manifest found at project ($PROJECT_MANIFEST) or user ($USER_MANIFEST) level" >&2
  exit 5  # no manifest to check — distinct from the 1/2 drift codes
fi

# Merge all manifest arrays into one temporary file
MERGED="$(mktemp)"
WB_TMP=""
trap 'rm -f "$MERGED" "${WB_TMP:-}"' EXIT
jq -s 'add' "${manifest_files[@]}" > "$MERGED"

count=$(jq 'length' "$MERGED")
sources=""
for f in "${manifest_files[@]}"; do sources="$sources $f"; done

echo "Upstream drift check — $count artifacts"
echo "Manifests:$(echo "$sources")"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "---"

clean=0; changed=0; gone=0; skipped=0
scope_project=0; scope_user=0; scope_unknown=0

for ((i=0; i<count; i++)); do
  local_path=$(jq -r ".[$i].path" "$MERGED")
  scope=$(jq -r ".[$i].scope // \"project\"" "$MERGED")
  upstream_repo=$(jq -r ".[$i].upstream_repo" "$MERGED")
  upstream_path=$(jq -r ".[$i].upstream_path" "$MERGED")
  recorded_sha=$(jq -r ".[$i].upstream_sha_at_import" "$MERGED")

  # Tally scope counts — whitelist-validated
  case "$scope" in
    project) scope_project=$((scope_project + 1)) ;;
    user)    scope_user=$((scope_user + 1)) ;;
    *)       scope_unknown=$((scope_unknown + 1))
             echo "WARNING: unknown scope '$scope' in manifest entry $i — counted separately" >&2 ;;
  esac

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

  # Get blob SHA of the file at the recorded import commit.
  # Branch on gh's EXIT CODE, not on whether the captured string is empty: gh api
  # prints the HTTP-error body (e.g. {"message":"Not Found",...,"status":"404"}) to
  # STDOUT on a 404, so `--jq '.sha'` yields a NON-empty string there. An emptiness
  # check would sail past it and mis-compare a deleted file as CHANGED instead of
  # UPSTREAM_GONE. (Root-caused in #530: obra/superpowers deleted spec-reviewer-prompt.md
  # / code-quality-reviewer-prompt.md upstream; the old `|| true` + `-z` guard reported
  # CHANGED, never GONE, and exited 1 instead of 2.)
  snap_err="$(mktemp)"
  if snap_blob=$(gh api "repos/$upstream_repo/contents/$encoded_path?ref=$recorded_sha" --jq '.sha' 2>"$snap_err"); then
    rm -f "$snap_err"
  else
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
  # Defensive: a 200 response with no .sha field (unexpected for the contents API)
  if [[ -z "$snap_blob" ]]; then
    echo "SKIP (import ref returned no .sha)"
    skipped=$((skipped + 1))
    continue
  fi

  # Get blob SHA of the file at upstream HEAD (same exit-code discipline as the
  # import-SHA fetch above — a 404 body is non-empty on STDOUT, so branch on gh's
  # exit code to catch a file removed from upstream HEAD as UPSTREAM_GONE rather
  # than CHANGED). See #530.
  head_err="$(mktemp)"
  if head_blob=$(gh api "repos/$upstream_repo/contents/$encoded_path" --jq '.sha' 2>"$head_err"); then
    rm -f "$head_err"
  else
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
  # Defensive: a 200 response with no .sha field (unexpected for the contents API)
  if [[ -z "$head_blob" ]]; then
    echo "SKIP (upstream HEAD returned no .sha)"
    skipped=$((skipped + 1))
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
echo "Scopes:  project=$scope_project  user=$scope_user  unknown=$scope_unknown"

# --write-back: stamp last_drift_check on every PROJECT-manifest entry to record
# that a drift review ran on this date. Only the project manifest is rewritten
# (the user-scope manifest, if any, is left to its own owner). A single run
# examines every project entry, so all are stamped uniformly with the run date.
# Semantics: last_drift_check = "when last reviewed", independent of the per-run
# CLEAN/CHANGED status (which is transient and re-derivable by re-running).
if [[ "$WRITE_BACK" -eq 1 ]]; then
  if [[ -f "$PROJECT_MANIFEST" ]]; then
    run_date="$(date -u +%Y-%m-%d)"
    # Create the rewrite temp in the manifest's OWN directory so `mv` is a
    # same-filesystem ATOMIC rename — a temp in $TMPDIR could be on a different
    # mount, degrading mv to a non-atomic cross-device copy that can expose a
    # partially written manifest. Every write-back step (count, mktemp, jq, mv)
    # is guarded to exit 3 ("write-back failed") rather than leaking jq/mv's
    # native status — under `set -e` that status (e.g. mv→1) could otherwise be
    # mistaken by a caller for the CHANGED (1) drift code.
    manifest_dir="$(dirname "$PROJECT_MANIFEST")"
    proj_count=$(jq 'length' "$PROJECT_MANIFEST") || {
      echo "ERROR: write-back failed (cannot count manifest) — manifest unchanged" >&2; exit 3; }
    WB_TMP="$(mktemp "$manifest_dir/.drift-wb.XXXXXX")" || {
      echo "ERROR: write-back failed (cannot create temp in $manifest_dir) — manifest unchanged" >&2; exit 3; }
    if jq --arg d "$run_date" 'map(.last_drift_check = $d)' "$PROJECT_MANIFEST" > "$WB_TMP" \
       && mv "$WB_TMP" "$PROJECT_MANIFEST"; then
      echo "write-back: stamped last_drift_check=$run_date on $proj_count project-manifest entries"
    else
      rm -f "$WB_TMP"
      echo "ERROR: write-back failed (jq/mv error) — manifest left unchanged" >&2
      exit 3
    fi
  else
    echo "write-back: no project manifest at $PROJECT_MANIFEST — nothing to stamp" >&2
  fi
fi

# Exit code reflects the most severe drift found, so a scheduler/CI can gate.
if [[ "$gone" -gt 0 ]]; then exit 2; fi
if [[ "$changed" -gt 0 ]]; then exit 1; fi
exit 0
