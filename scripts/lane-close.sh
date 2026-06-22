#!/usr/bin/env bash
# scripts/lane-close.sh — Mercury multi-lane close ceremony.
# Implements Rule 3.2 of feedback_lane_protocol.md (v0.1 Delta 3, Issue #311).
#
# Atomically:
#   1. Validate <lane> exists in LANES.md, status != closed
#   2. Refuse if `.tmp/lane-<lane>/` contains specific dangerous markers:
#      any file matching `*.uncommitted` (the documented Mercury "save me
#      first" suffix) OR a `.git` directory/file (nested checkout artifact).
#      Other untracked content does NOT block close — operators are expected
#      to use the `.uncommitted` suffix to flag inspect-first content.
#   3. Flip the lane's `**Status**: ...` line to `closed` (only that lane's
#      section — Rule 6 ownership)
#   4. Remove `.tmp/lane-<lane>/`
#
# Per Rule 6 only the OWNING lane should run this. The script issues a soft
# warning when the current git branch does not match `feature/lane-<lane>/*`
# (heuristic — not enforced; pass --force-cross-lane to silence).
#
# Usage:
#   scripts/lane-close.sh <lane-name>
#                         [--lanes-file PATH] [--memory-dir PATH]
#                         [--tmp-dir PATH] [--repo-root PATH]
#                         [--yes] [--force-cross-lane] [--dry-run]
#                         [--close-issue --issue N [--rationale TEXT] [--repo OWNER/REPO]]
#
# Defaults:
#   --memory-dir   ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --lanes-file   <memory-dir>/LANES.md
#   --repo-root    `git rev-parse --show-toplevel`
#   --tmp-dir      <repo-root>/.tmp/lane-<lane>
#   --rationale    "Lane <lane-name> closed via scripts/lane-close.sh."
#   --repo         resolved via gh repo view / GH_REPO env (only when --close-issue)
#
# --close-issue (#323 Phase B B2): after Status flip + tmp prune succeed, post
# a closure-rationale comment on Issue #N then `gh issue close N`. Best-effort
# external mutation — failure here logs a WARN but does NOT roll back the
# already-completed local Status flip / tmp prune.
#
# Exit codes:
#   0  lane closed cleanly (or --dry-run path completed)
#   1  validation failed (lane missing / already closed / unsafe tmp dir / aborted)
#   2  invalid args / missing lanes-file / cannot resolve repo root

set -u

die()  { printf 'lane-close: %s\n' "$1" >&2; exit 2; }
warn() { printf 'lane-close WARN: %s\n' "$1" >&2; }
fail() { printf 'lane-close: %s\n' "$1" >&2; exit 1; }

LANE=""
LANES_FILE=""
MEMORY_DIR=""
TMP_DIR=""
REPO_ROOT=""
YES=0
FORCE_CROSS_LANE=0
DRY_RUN=0
CLOSE_ISSUE=0
ISSUE_NUM=""
RATIONALE=""
REPO=""

while [ $# -gt 0 ]; do
  case "$1" in
    --lanes-file)        shift; [ $# -gt 0 ] || die "--lanes-file needs a value"; LANES_FILE="$1"; shift ;;
    --memory-dir)        shift; [ $# -gt 0 ] || die "--memory-dir needs a value"; MEMORY_DIR="$1"; shift ;;
    --tmp-dir)           shift; [ $# -gt 0 ] || die "--tmp-dir needs a value"
                          [ -n "$1" ] || die "--tmp-dir requires a non-empty path (got empty string)"
                          TMP_DIR="$1"; shift ;;
    --repo-root)         shift; [ $# -gt 0 ] || die "--repo-root needs a value"; REPO_ROOT="$1"; shift ;;
    --yes)               YES=1; shift ;;
    --force-cross-lane)  FORCE_CROSS_LANE=1; shift ;;
    --dry-run)           DRY_RUN=1; shift ;;
    --close-issue)       CLOSE_ISSUE=1; shift ;;
    --issue)             shift; [ $# -gt 0 ] || die "--issue needs a value"; ISSUE_NUM="$1"; shift ;;
    --rationale)         shift; [ $# -gt 0 ] || die "--rationale needs a value"; RATIONALE="$1"; shift ;;
    --repo)              shift; [ $# -gt 0 ] || die "--repo needs a value"; REPO="$1"; shift ;;
    -h|--help)
      sed -n '2,40p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do
          [ -z "$LANE" ] || die "too many positional args after --: $1"
          LANE="$1"; shift
        done; break ;;
    -*) die "unknown flag: $1" ;;
    *)
      [ -z "$LANE" ] || die "too many positional args; got: $1"
      LANE="$1"; shift ;;
  esac
done

[ -n "$LANE" ] || die "missing <lane-name> argument (try --help)"
case "$LANE" in
  -*) die "lane name must not start with hyphen: '$LANE'" ;;
  *[!A-Za-z0-9_-]*) die "lane name must be [A-Za-z0-9_-]: '$LANE'" ;;
esac

# --close-issue arg interlock: requires --issue N (positive int).
if [ "$CLOSE_ISSUE" -eq 1 ]; then
  [ -n "$ISSUE_NUM" ] || die "--close-issue requires --issue N"
  case "$ISSUE_NUM" in
    ''|*[!0-9]*|0) die "--issue must be a positive integer: '$ISSUE_NUM'" ;;
  esac
fi
# --issue / --rationale / --repo without --close-issue is a wiring mistake —
# surface it loudly. A typo dropping --close-issue would otherwise look
# correctly configured for remote closure while only closing locally.
if [ "$CLOSE_ISSUE" -eq 0 ] && { [ -n "$ISSUE_NUM" ] || [ -n "$RATIONALE" ] || [ -n "$REPO" ]; }; then
  die "--issue/--rationale/--repo only meaningful with --close-issue"
fi

if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
if [ -z "$LANES_FILE" ]; then LANES_FILE="$MEMORY_DIR/LANES.md"; fi
[ -f "$LANES_FILE" ] || die "LANES.md not found: $LANES_FILE"

if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "cannot resolve repo root (pass --repo-root)"
fi
[ -d "$REPO_ROOT" ] || die "repo root not a directory: $REPO_ROOT"

if [ -z "$TMP_DIR" ]; then TMP_DIR="$REPO_ROOT/.tmp/lane-$LANE"; fi

# Tmp dir safety gate: refuse anything outside the repo's .tmp/ subtree, OR
# anything that resolves to a root-like / repo-root / parent-of-repo path.
# This blocks `--tmp-dir /` `--tmp-dir $HOME` `--tmp-dir $REPO_ROOT` etc.
# from triggering rm -rf later. Rule: TMP_DIR must be REPO_ROOT/.tmp/<something>
# and the <something> must be non-empty. Empty / root paths refused outright.
case "$TMP_DIR" in
  ''|/|/.|/..) die "refusing unsafe --tmp-dir: '$TMP_DIR' (empty or root path)" ;;
esac
# Realpath comparison defends against `--tmp-dir $REPO_ROOT/.tmp/lane-foo/..`
# tricks. realpath is GNU; on systems without it, fall back to readlink -f then
# raw path comparison (best-effort).
TMP_DIR_REAL=$(realpath -m "$TMP_DIR" 2>/dev/null \
            || readlink -f "$TMP_DIR" 2>/dev/null \
            || printf '%s' "$TMP_DIR")
REPO_ROOT_REAL=$(realpath -m "$REPO_ROOT" 2>/dev/null \
              || readlink -f "$REPO_ROOT" 2>/dev/null \
              || printf '%s' "$REPO_ROOT")
EXPECTED_PREFIX="${REPO_ROOT_REAL%/}/.tmp/"
case "$TMP_DIR_REAL" in
  "$REPO_ROOT_REAL"|"${REPO_ROOT_REAL%/}")
    die "refusing --tmp-dir that resolves to repo root: '$TMP_DIR' → '$TMP_DIR_REAL'" ;;
  "$EXPECTED_PREFIX"*)
    # Resolved path must contain a non-empty leaf beyond .tmp/
    LEAF="${TMP_DIR_REAL#$EXPECTED_PREFIX}"
    [ -n "$LEAF" ] || die "refusing --tmp-dir without leaf segment: '$TMP_DIR' → '$TMP_DIR_REAL'"
    # Reject any `..` segment in the leaf (handles `lane-foo/..`, `..`,
    # `a/../b`, etc.). Belt-and-braces: realpath/readlink fallback may have
    # left literal `..` segments unresolved. Match by exact equality OR
    # bordered by `/` so that names containing `..` as a literal substring
    # (e.g. `lane-..foo`) are NOT falsely flagged.
    case "$LEAF" in
      ..|*/..|../*|*/../*) die "refusing --tmp-dir with '..' segment: '$TMP_DIR' → '$TMP_DIR_REAL'" ;;
    esac
    # Lane-name consistency check (Argus #327 iter 4/5 finding #3): the
    # leaf's first segment MUST be `lane-<LANE>` (or equal to it). Without
    # this guard, `lane-close.sh foo --tmp-dir .tmp/lane-bar` would
    # silently `rm -rf` the WRONG lane's tmp dir while flipping foo's
    # Status. The lane name was already validated against [A-Za-z0-9_-]
    # so direct string equality is safe.
    EXPECTED_LEAF_PREFIX="lane-${LANE}"
    case "$LEAF" in
      "$EXPECTED_LEAF_PREFIX"|"$EXPECTED_LEAF_PREFIX"/*) ;;
      *) die "refusing --tmp-dir whose leaf does not match 'lane-${LANE}': '$TMP_DIR' → '$TMP_DIR_REAL' (leaf=${LEAF})" ;;
    esac
    ;;
  *)
    die "refusing --tmp-dir outside ${EXPECTED_PREFIX}*: '$TMP_DIR' → '$TMP_DIR_REAL'" ;;
esac

# Validate lane is registered in LANES.md and detect current status.
# The `### \`<lane>\`` heading anchors the section; the next line containing
# `**Status**:` (potentially several lines later) is the lane's status line.
LANE_HEADING_RE="^### \`${LANE}\`"
if ! grep -q "$LANE_HEADING_RE" "$LANES_FILE"; then
  fail "lane '$LANE' not found in $LANES_FILE"
fi

# Extract the lane's section (heading line through to the next heading or EOF).
LANE_SECTION=$(awk -v re="$LANE_HEADING_RE" '
  $0 ~ re { in_section = 1; print; next }
  /^### / && in_section { exit }
  /^## / && in_section { exit }
  in_section { print }
' "$LANES_FILE")

CUR_STATUS=$(printf '%s\n' "$LANE_SECTION" \
  | grep -m1 '^- \*\*Status\*\*:' \
  | sed -E 's/^- \*\*Status\*\*: `?([A-Za-z_-]+)`?.*/\1/' \
  || true)

if [ -z "$CUR_STATUS" ]; then
  fail "could not parse current Status for lane '$LANE' (expected line: '- **Status**: \`active\`' or similar)"
fi

if [ "$CUR_STATUS" = "closed" ]; then
  fail "lane '$LANE' is already closed (Status=$CUR_STATUS)"
fi

# Owning-lane heuristic: warn if current branch doesn't match lane prefix.
# Skipped under --force-cross-lane and silently in non-git contexts.
if [ "$FORCE_CROSS_LANE" -eq 0 ]; then
  CUR_BRANCH=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [ -n "$CUR_BRANCH" ] && [ "$CUR_BRANCH" != "HEAD" ]; then
    case "$CUR_BRANCH" in
      "feature/lane-${LANE}/"*) ;;
      *) warn "current branch '$CUR_BRANCH' does not match owning-lane prefix 'feature/lane-${LANE}/*' — pass --force-cross-lane to silence this warning" ;;
    esac
  fi
fi

# Tmp dir safety check: refuse close if the tmp dir exists AND contains files
# matching dangerous patterns. The ".uncommitted" suffix is the documented
# Mercury convention for "save me first" markers; ".git" artifacts indicate
# a nested checkout (likely accidental).
if [ -d "$TMP_DIR" ]; then
  UNSAFE=$(find "$TMP_DIR" \( -name '*.uncommitted' -o -name '.git' \) -print 2>/dev/null | head -n5)
  if [ -n "$UNSAFE" ]; then
    fail "refusing to remove $TMP_DIR — found unsafe entries (resolve manually first):
$UNSAFE"
  fi
fi

if [ "$DRY_RUN" -eq 1 ]; then
  printf '[dry-run] would flip Status of lane %s from %s to closed in %s\n' "$LANE" "$CUR_STATUS" "$LANES_FILE"
  if [ -d "$TMP_DIR" ]; then
    printf '[dry-run] would rm -rf %s\n' "$TMP_DIR"
  else
    printf '[dry-run] no tmp dir at %s (skip rm)\n' "$TMP_DIR"
  fi
  if [ "$CLOSE_ISSUE" -eq 1 ]; then
    printf '[dry-run] would gh issue comment #%s + gh issue close #%s (rationale=%s)\n' \
      "$ISSUE_NUM" "$ISSUE_NUM" "${RATIONALE:-(default)}"
  fi
  exit 0
fi

# Confirm before destructive action unless --yes.
if [ "$YES" -eq 0 ]; then
  if [ -t 0 ]; then
    printf 'Close lane %s? Will flip Status to `closed` and rm -rf %s [y/N] ' "$LANE" "$TMP_DIR"
    read -r ans
    case "${ans:-}" in
      y|Y|yes|YES) ;;
      *) fail "aborted by user" ;;
    esac
  else
    fail "refusing destructive action without --yes in non-interactive context"
  fi
fi

# Edit LANES.md: replace the Status line within the lane's own section ONLY.
# Strategy: stream through file, toggle in-section flag on heading match,
# turn off on next heading; rewrite the first matching Status line inside
# the section, then exit-flag to prevent further edits to other sections.
TMP_LANES=$(mktemp)
awk -v re="$LANE_HEADING_RE" '
  BEGIN { in_section = 0; replaced = 0 }
  $0 ~ re { in_section = 1; print; next }
  in_section && (/^### / || /^## /) { in_section = 0; print; next }
  in_section && !replaced && /^- \*\*Status\*\*:/ {
    sub(/`[A-Za-z_-]+`/, "`closed`")
    sub(/\*\*Status\*\*: [A-Za-z_-]+/, "**Status**: `closed`")
    print
    replaced = 1
    next
  }
  { print }
  END { if (!replaced) exit 7 }
' "$LANES_FILE" > "$TMP_LANES" || {
  rm -f "$TMP_LANES"
  fail "failed to rewrite Status line — Status pattern not matched in lane section"
}

mv "$TMP_LANES" "$LANES_FILE"

# Remove tmp dir if present.
if [ -d "$TMP_DIR" ]; then
  rm -rf "$TMP_DIR" || fail "rm -rf $TMP_DIR failed"
  printf 'lane-close: removed %s\n' "$TMP_DIR"
else
  printf 'lane-close: no tmp dir at %s (nothing to remove)\n' "$TMP_DIR"
fi

printf 'lane-close: lane %s flipped to `closed` in %s\n' "$LANE" "$LANES_FILE"

# Optional --close-issue: best-effort GitHub Issue closure with rationale.
# Local Status flip + tmp prune already succeeded above, so a gh failure here
# emits WARN but does NOT exit non-zero — operator may run `gh issue close` by
# hand later. Local LANES.md state remains the authoritative record.
if [ "$CLOSE_ISSUE" -eq 1 ]; then
  if ! command -v gh >/dev/null 2>&1; then
    warn "gh CLI not on PATH — skipping --close-issue (lane is already closed locally)"
  else
    if [ -z "$REPO" ]; then REPO="${GH_REPO:-}"; fi
    if [ -z "$REPO" ]; then
      REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null) \
        || warn "cannot determine target repo for --close-issue (pass --repo or set GH_REPO)"
    fi
    if [ -z "$RATIONALE" ]; then
      RATIONALE="Lane \`$LANE\` closed via \`scripts/lane-close.sh\` (#323 Phase B B2)."
    fi
    if [ -n "$REPO" ]; then
      if gh issue comment "$ISSUE_NUM" --repo "$REPO" --body "$RATIONALE" >/dev/null 2>&1; then
        printf 'lane-close: posted closure rationale on #%s\n' "$ISSUE_NUM"
      else
        warn "gh issue comment #$ISSUE_NUM failed — closure rationale NOT posted (post manually if needed)"
      fi
      if gh issue close "$ISSUE_NUM" --repo "$REPO" --reason completed >/dev/null 2>&1; then
        printf 'lane-close: closed issue #%s\n' "$ISSUE_NUM"
      else
        warn "gh issue close #$ISSUE_NUM failed — issue NOT closed remotely (close manually if needed)"
      fi
    fi
  fi
fi

exit 0
