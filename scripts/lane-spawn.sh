#!/usr/bin/env bash
# scripts/lane-spawn.sh — Mercury multi-lane spawn ceremony.
# Implements #323 Phase B B1 (lane creation with documented rollback semantics).
#
# Steps (best-effort sequencing — see Rollback policy below for partial-failure
# semantics; this is NOT cryptographic atomicity):
#   1. Validate <lane>, <issue>, optional --short / --slug args
#   2. Refuse if <lane> already appears in LANES.md "## Active Lanes" section
#   3. Refuse if active-lane count is at HARD-CAP=5 (Rule 7 Delta 7 / Issue #314)
#   4. Claim Issue via scripts/lane-claim.sh (Rule 1.1 probe-after-write, #309)
#   5. Create branch `lane/<short>/<issue>-<slug>` off origin/develop (Rule 2.1)
#   6. Write per-lane handoff template at <memory-dir>/session-handoff-<lane>.md
#      (refuse to overwrite if file exists — protect prior session state)
#   7. Append a new lane section to LANES.md (own section per Rule 6)
#
# Rollback policy: each step is best-effort idempotent. If step 4 fails,
# steps 5–7 are skipped. If step 5 fails, steps 6–7 are skipped. Failed
# step output suggests the manual remediation. Successful steps are NOT
# rolled back automatically — operator decides whether to keep partial
# progress (e.g. the Issue label may already be useful even if the branch
# create fails).
#
# Usage:
#   scripts/lane-spawn.sh <lane> <issue>
#                         [--short SHORT] [--slug SLUG]
#                         [--memory-dir PATH] [--lanes-file PATH]
#                         [--repo-root PATH] [--repo OWNER/REPO]
#                         [--no-claim] [--no-branch]
#                         [--dry-run] [--yes]
#
# Defaults:
#   --short          first 8 chars of <lane> after stripping non-[a-z0-9-]
#   --slug           lowercased Issue title with non-[a-z0-9-] → "-",
#                    truncated so total branch ≤40 chars (Rule 2.1)
#   --memory-dir     ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#   --lanes-file     <memory-dir>/LANES.md
#   --repo-root      `git rev-parse --show-toplevel`
#   --repo           resolved via `gh repo view` or GH_REPO env
#
# Exit codes:
#   0  spawn succeeded (or --dry-run path completed)
#   1  state failure (lane exists / cap reached / Issue not found / handoff
#      exists / lane-claim conflict / branch exists / git step failure /
#      awk LANES.md insert failure / user aborted at confirm prompt)
#   2  argument or environment error (invalid flag / missing gh / cannot
#      resolve repo or memory dir / unsafe --lanes-file path)

set -u

die()  { printf 'lane-spawn: %s\n' "$1" >&2; exit 2; }
warn() { printf 'lane-spawn WARN: %s\n' "$1" >&2; }
fail() { printf 'lane-spawn: %s\n' "$1" >&2; exit 1; }

LANE=""
ISSUE=""
SHORT=""
SLUG=""
MEMORY_DIR=""
LANES_FILE=""
REPO_ROOT=""
REPO=""
NO_CLAIM=0
NO_BRANCH=0
DRY_RUN=0
YES=0

while [ $# -gt 0 ]; do
  case "$1" in
    --short)       shift; [ $# -gt 0 ] || die "--short needs a value"; SHORT="$1"; shift ;;
    --slug)        shift; [ $# -gt 0 ] || die "--slug needs a value"; SLUG="$1"; shift ;;
    --memory-dir)  shift; [ $# -gt 0 ] || die "--memory-dir needs a value"; MEMORY_DIR="$1"; shift ;;
    --lanes-file)  shift; [ $# -gt 0 ] || die "--lanes-file needs a value"; LANES_FILE="$1"; shift ;;
    --repo-root)   shift; [ $# -gt 0 ] || die "--repo-root needs a value"; REPO_ROOT="$1"; shift ;;
    --repo)        shift; [ $# -gt 0 ] || die "--repo needs a value"; REPO="$1"; shift ;;
    --no-claim)    NO_CLAIM=1; shift ;;
    --no-branch)   NO_BRANCH=1; shift ;;
    --dry-run)     DRY_RUN=1; shift ;;
    --yes)         YES=1; shift ;;
    -h|--help)
      sed -n '2,38p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    --) shift
        if [ -z "$LANE" ]   && [ $# -gt 0 ]; then LANE="$1"; shift; fi
        if [ -z "$ISSUE" ]  && [ $# -gt 0 ]; then ISSUE="$1"; shift; fi
        [ $# -eq 0 ] || die "too many positional args after --: $1"
        break ;;
    -*) die "unknown flag: $1" ;;
    *)
      if [ -z "$LANE" ];   then LANE="$1"
      elif [ -z "$ISSUE" ]; then ISSUE="$1"
      else die "too many positional args; got: $1"; fi
      shift ;;
  esac
done

[ -n "$LANE" ]  || die "missing <lane> argument (try --help)"
[ -n "$ISSUE" ] || die "missing <issue> argument (try --help)"

case "$LANE" in
  -*) die "lane name must not start with hyphen: '$LANE'" ;;
  *[!A-Za-z0-9_-]*) die "lane name must be [A-Za-z0-9_-]: '$LANE'" ;;
esac
case "$ISSUE" in
  ''|*[!0-9]*|0) die "issue must be a positive integer: '$ISSUE'" ;;
esac

# Memory dir resolution mirrors lane-close.sh / lane-sweep.sh.
if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
[ -d "$MEMORY_DIR" ] || die "memory dir not found: $MEMORY_DIR (set --memory-dir or MERCURY_MEMORY_DIR)"
if [ -z "$LANES_FILE" ]; then LANES_FILE="$MEMORY_DIR/LANES.md"; fi
[ -f "$LANES_FILE" ] || die "LANES.md not found: $LANES_FILE"

# Path-traversal defense (Argus #334 iter 2 importance:2/10): if --lanes-file
# was passed explicitly with a path that resolves outside MEMORY_DIR, refuse.
# The default ($MEMORY_DIR/LANES.md) trivially passes. Operators with custom
# layouts can pass --memory-dir to widen the boundary deliberately.
LANES_REAL=$(realpath -m "$LANES_FILE" 2>/dev/null || readlink -f "$LANES_FILE" 2>/dev/null || printf '%s' "$LANES_FILE")
MEMORY_REAL=$(realpath -m "$MEMORY_DIR" 2>/dev/null || readlink -f "$MEMORY_DIR" 2>/dev/null || printf '%s' "$MEMORY_DIR")
case "$LANES_REAL" in
  "${MEMORY_REAL%/}"/*) ;;
  "$MEMORY_REAL") ;;
  *) die "--lanes-file resolves outside --memory-dir: '$LANES_REAL' (memory-dir='$MEMORY_REAL')" ;;
esac

if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "cannot resolve repo root (pass --repo-root)"
fi
[ -d "$REPO_ROOT" ] || die "repo root not a directory: $REPO_ROOT"

# Default --short: lane name lowercased, non-[a-z0-9-] stripped, truncated to 8.
if [ -z "$SHORT" ]; then
  SHORT=$(printf '%s' "$LANE" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9-' | cut -c1-8)
fi
case "$SHORT" in
  ''|-*) die "computed --short is empty or starts with hyphen: '$SHORT' (pass --short explicitly)" ;;
  *[!a-z0-9-]*) die "--short must be [a-z0-9-]: '$SHORT'" ;;
esac
[ "${#SHORT}" -le 8 ] || die "--short must be ≤8 chars: '$SHORT' (${#SHORT} chars)"

# Active-lane uniqueness + cap check via LANES.md.
# Fail-fast on a malformed registry: the spawn ceremony writes a new section
# under "## Active Lanes" via awk in step 8. If that header is missing, the
# awk insert silently no-ops, and a successful spawn would have already
# claimed the Issue + created a branch + written a handoff with no registry
# row — a worse-than-pre-spawn state. Refuse here before any mutation.
if ! grep -q '^## Active Lanes' "$LANES_FILE"; then
  fail "LANES.md missing '## Active Lanes' header — refusing to spawn (fix the registry first): $LANES_FILE"
fi

parse_active_lanes() {
  awk '
    /^## Active Lanes/ { in_active = 1; next }
    /^## / && in_active { in_active = 0 }
    in_active && /^### `[^`]+`/ {
      match($0, /`[^`]+`/)
      name = substr($0, RSTART + 1, RLENGTH - 2)
      print name
    }
  ' "$1"
}
ACTIVE_LANES=$(parse_active_lanes "$LANES_FILE")

# HARD-CAP enforcement counts lanes whose Status resolves to `active`, mirroring
# scripts/lane-cap-check.sh (Rule 7 reference impl). Counting `### ` headings
# alone would over-count paused/deprecated rows that linger in the Active Lanes
# section. awk walks each lane block and emits the lane name only when the
# block's first **Status** line is exactly `active`.
ACTIVE_COUNT=$(awk '
  /^## Active Lanes/ { in_active = 1; in_lane = 0; next }
  /^## / && in_active { in_active = 0; in_lane = 0 }
  in_active && /^### `[^`]+`/ { in_lane = 1; status_seen = 0; next }
  in_lane && !status_seen && /^- \*\*Status\*\*: `?active`?/ {
    status_seen = 1
    count++
    in_lane = 0
  }
  in_lane && /^- \*\*Status\*\*:/ { status_seen = 1; in_lane = 0 }
  END { print count + 0 }
' "$LANES_FILE")

if printf '%s\n' "$ACTIVE_LANES" | grep -qxF "$LANE"; then
  fail "lane '$LANE' already exists in $LANES_FILE Active Lanes section"
fi

# Active short-name uniqueness — Rule 2.1 mandates cross-lane unique short.
# Parse Short-name field from each active lane section; sections without a
# `**Short name**:` line (legacy lanes pre-Rule 2.1) simply emit nothing
# for that section, so they cannot collide with the requested SHORT — they
# are tolerated rather than skipped explicitly. New spawns always get a
# Short field written to LANES.md (see step 4 awk).
EXISTING_SHORTS=$(awk '
  /^## Active Lanes/ { in_active = 1; next }
  /^## / && in_active { in_active = 0 }
  in_active && /^- \*\*Short name\*\*: `[^`]+`/ {
    match($0, /`[^`]+`/)
    print substr($0, RSTART + 1, RLENGTH - 2)
  }
' "$LANES_FILE")
if printf '%s\n' "$EXISTING_SHORTS" | grep -qxF "$SHORT"; then
  fail "short name '$SHORT' already in use by another active lane (pass --short)"
fi

if [ "$ACTIVE_COUNT" -ge 5 ]; then
  fail "HARD-CAP at 5 active lanes reached ($ACTIVE_COUNT/5) — close one before spawning lane '$LANE' (Rule 7 Delta 7 / Issue #314)"
fi

# Resolve repo (only when claim or slug-derivation needs gh).
NEED_GH=0
if [ "$NO_CLAIM" -eq 0 ]; then NEED_GH=1; fi
if [ -z "$SLUG" ]; then NEED_GH=1; fi

if [ "$NEED_GH" -eq 1 ]; then
  # gh CLI required directly. Note: this script does NOT call jq itself —
  # `gh --jq` uses gh's built-in jq engine, not the external binary. The
  # downstream lane-claim.sh wrapper performs its own jq check at exec time.
  command -v gh >/dev/null 2>&1 || die "gh CLI required (or pass --no-claim and --slug to bypass)"
  if [ -z "$REPO" ]; then REPO="${GH_REPO:-}"; fi
  if [ -z "$REPO" ]; then
    REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null) \
      || die "cannot determine target repo (pass --repo or set GH_REPO)"
  fi
  case "$REPO" in
    */*) ;;
    *) die "invalid repo format '$REPO' (expected owner/repo)" ;;
  esac
fi

# Default --slug: derived from Issue title via gh.
if [ -z "$SLUG" ]; then
  ISSUE_TITLE=$(gh issue view "$ISSUE" --repo "$REPO" --json title --jq .title 2>/dev/null) \
    || fail "cannot fetch Issue #$ISSUE title (Issue missing? gh auth?)"
  [ -n "$ISSUE_TITLE" ] || fail "Issue #$ISSUE has empty title"
  # Derive: lowercase, non-alnum → "-", squeeze repeats, trim leading/trailing "-".
  SLUG=$(printf '%s' "$ISSUE_TITLE" \
    | tr 'A-Z' 'a-z' \
    | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')
fi
case "$SLUG" in
  '') die "computed --slug is empty (pass --slug explicitly)" ;;
  *[!a-z0-9-]*) die "--slug must be [a-z0-9-]: '$SLUG'" ;;
esac

# Branch name + Rule 2.1 ≤40-char cap. Truncate slug if needed.
PREFIX="lane/${SHORT}/${ISSUE}-"
MAX_BRANCH=40
PREFIX_LEN=${#PREFIX}
SLUG_BUDGET=$(( MAX_BRANCH - PREFIX_LEN ))
if [ "$SLUG_BUDGET" -lt 1 ]; then
  fail "branch prefix '$PREFIX' already exceeds Rule 2.1 ≤40-char cap (pass shorter --short)"
fi
if [ "${#SLUG}" -gt "$SLUG_BUDGET" ]; then
  SLUG=$(printf '%s' "$SLUG" | cut -c1-"$SLUG_BUDGET" | sed -E 's/-+$//')
fi
BRANCH="${PREFIX}${SLUG}"

HANDOFF_FILE="$MEMORY_DIR/session-handoff-${LANE}.md"
if [ -e "$HANDOFF_FILE" ]; then
  fail "handoff file already exists: $HANDOFF_FILE (refusing to overwrite — move or delete first)"
fi

# Dry-run gate — print intent and exit before any external mutations.
if [ "$DRY_RUN" -eq 1 ]; then
  printf '[dry-run] lane=%s issue=%s short=%s slug=%s\n' "$LANE" "$ISSUE" "$SHORT" "$SLUG"
  printf '[dry-run] branch=%s (%d chars, cap=%d)\n' "$BRANCH" "${#BRANCH}" "$MAX_BRANCH"
  printf '[dry-run] memory-dir=%s\n' "$MEMORY_DIR"
  printf '[dry-run] handoff=%s\n' "$HANDOFF_FILE"
  printf '[dry-run] LANES.md=%s (%d active lanes pre-spawn)\n' "$LANES_FILE" "$ACTIVE_COUNT"
  if [ "$NO_CLAIM" -eq 0 ]; then
    printf '[dry-run] step 1: scripts/lane-claim.sh %s %s --no-assignee\n' "$LANE" "$ISSUE"
  else
    printf '[dry-run] step 1: SKIPPED (--no-claim)\n'
  fi
  if [ "$NO_BRANCH" -eq 0 ]; then
    printf '[dry-run] step 2: git branch %s origin/develop\n' "$BRANCH"
  else
    printf '[dry-run] step 2: SKIPPED (--no-branch)\n'
  fi
  printf '[dry-run] step 3: write %s\n' "$HANDOFF_FILE"
  printf '[dry-run] step 4: append section to %s\n' "$LANES_FILE"
  exit 0
fi

# Confirm before mutations unless --yes is set. Non-interactive contexts
# (no tty on stdin) MUST pass --yes regardless of --no-claim/--no-branch —
# the script never auto-confirms based on flag combination, only on tty
# detection + explicit --yes.
if [ "$YES" -eq 0 ]; then
  if [ -t 0 ]; then
    printf 'Spawn lane "%s" for issue #%s (branch=%s)? [y/N] ' "$LANE" "$ISSUE" "$BRANCH"
    read -r ans
    case "${ans:-}" in
      y|Y|yes|YES) ;;
      *) fail "aborted by user" ;;
    esac
  else
    fail "refusing in non-interactive context without --yes"
  fi
fi

# Step 1: claim Issue (Rule 1.1 wrapper).
if [ "$NO_CLAIM" -eq 0 ]; then
  CLAIM_SCRIPT="$REPO_ROOT/scripts/lane-claim.sh"
  [ -x "$CLAIM_SCRIPT" ] || die "lane-claim.sh not executable at $CLAIM_SCRIPT (Rule 1.1 dep #309)"
  GH_REPO="$REPO" "$CLAIM_SCRIPT" "$LANE" "$ISSUE" --no-assignee \
    || fail "lane-claim.sh failed for lane=$LANE issue=#$ISSUE (see message above; nothing further mutated)"
  printf 'lane-spawn: claimed issue #%s with lane:%s\n' "$ISSUE" "$LANE"
fi

# Step 2: create branch off origin/develop.
if [ "$NO_BRANCH" -eq 0 ]; then
  if git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/heads/$BRANCH" >/dev/null 2>&1; then
    fail "branch '$BRANCH' already exists locally (delete or rename first)"
  fi
  if ! git -C "$REPO_ROOT" rev-parse --verify --quiet "refs/remotes/origin/develop" >/dev/null 2>&1; then
    fail "origin/develop not found locally — run 'git fetch origin' first"
  fi
  git -C "$REPO_ROOT" branch "$BRANCH" "origin/develop" \
    || fail "git branch $BRANCH origin/develop failed"
  printf 'lane-spawn: created branch %s\n' "$BRANCH"
fi

# Step 3: write handoff template.
# Trap heredoc/redirect failure (disk full, permissions, ENOSPC) before any
# LANES.md mutation in step 4 — leaving step 4 to run on a missing handoff
# would create a registry row pointing at a file that never existed.
TODAY=$(date -u +'%Y-%m-%d')
if ! cat > "$HANDOFF_FILE" <<EOF
---
name: session_handoff_${LANE}
description: Initial handoff for lane '${LANE}' (Issue #${ISSUE}). Written by scripts/lane-spawn.sh.
type: project
originSessionId: spawn-${LANE}-${ISSUE}-${TODAY}
---

# Lane '${LANE}' — Initial Session Handoff

## Starting Prompt

This is Mercury **lane '${LANE}'** session 1 (lane info in \`LANES.md\` + \`feedback_lane_protocol.md\`).

### 当前状态

- 仓库 \`$(basename "$REPO_ROOT")\` branch \`${BRANCH}\` (off origin/develop)
- 关联 Issue: [#${ISSUE}](https://github.com/${REPO:-OWNER/REPO}/issues/${ISSUE})
- 第一步：\`git fetch origin && git switch ${BRANCH}\`

### 主任务

参考 Issue #${ISSUE} body for spec.

### 边界 (lane 不做)

- Rule 4: 不动 \`.mercury/docs/DIRECTION.md\` / \`.mercury/docs/EXECUTION-PLAN.md\` (开 Issue 给 main lane)
- Rule 6: 不编辑 LANES.md 其他 lane 的 section
- Rule 7: shared index append-only (S{N}-${LANE} 自己 row OK，其他 lane row 永不动)

### 关键参考

- \`memory/feedback_lane_protocol.md\` v0+v0.1+v0.2 — 7+ rules
- \`memory/LANES.md\` — lane registry
- Issue #${ISSUE} body — task spec

## Task State

- **Primary**: 实施 Issue #${ISSUE}
- **Branch**: \`${BRANCH}\`
- **In progress**: 无

## User Instructions

(written by lane-spawn.sh on ${TODAY} — replace with session-specific content as work begins)
EOF
then
  fail "failed to write handoff template at $HANDOFF_FILE (disk / permissions / quota — LANES.md NOT mutated)"
fi
printf 'lane-spawn: wrote handoff %s\n' "$HANDOFF_FILE"

# Step 4: append lane section to LANES.md (own section per Rule 6).
# Strategy: locate the "## Active Lanes" header, then locate the next "## "
# header (Closed Lanes / Governance / Rollback). Insert new section at the
# end of the Active Lanes block (immediately before the next "## " header).
# The awk script exits 7 if either (a) the Active Lanes header was never seen,
# or (b) end-of-file was reached without inserting (header missing trailing
# `## ` AND we somehow fell through). Both cases preserve LANES.md unchanged
# because mv only runs on awk success.
# mktemp template: BSD/macOS mktemp requires an explicit XXXXXX template; GNU
# mktemp without a template silently uses "tmp.XXXXXXXXXX" but the explicit
# form works on both. Honor $TMPDIR for sandboxed CI environments.
TMP_LANES=$(mktemp "${TMPDIR:-/tmp}/lane-spawn.XXXXXX")
awk -v lane="$LANE" -v branch="$BRANCH" -v issue="$ISSUE" \
    -v handoff="session-handoff-${LANE}.md" -v short="$SHORT" -v today="$TODAY" '
  BEGIN { saw_active = 0; in_active = 0; printed = 0 }
  /^## Active Lanes/ { saw_active = 1; in_active = 1; print; next }
  /^## / && in_active && !printed {
    # Insert new section right before this next "## " header.
    print "### `" lane "`"
    print ""
    print "- **Short name**: `" short "`"
    print "- **Branch**: `" branch "`"
    print "- **Handoff file**: `" handoff "`"
    print "- **Status**: `active`"
    print "- **Spawned**: " today " (Issue #" issue ")"
    print ""
    in_active = 0
    printed = 1
    print
    next
  }
  { print }
  END {
    if (saw_active && !printed) {
      # Active Lanes was the LAST `## ` block → append at EOF.
      print ""
      print "### `" lane "`"
      print ""
      print "- **Short name**: `" short "`"
      print "- **Branch**: `" branch "`"
      print "- **Handoff file**: `" handoff "`"
      print "- **Status**: `active`"
      print "- **Spawned**: " today " (Issue #" issue ")"
      printed = 1
    }
    if (!saw_active || !printed) {
      # Defense in depth — the precheck above should have caught
      # !saw_active, but if LANES.md mutated between precheck and now,
      # surface a deterministic failure rather than a silent no-op.
      exit 7
    }
  }
' "$LANES_FILE" > "$TMP_LANES" && mv "$TMP_LANES" "$LANES_FILE" || {
  rm -f "$TMP_LANES"
  fail "failed to append lane section to $LANES_FILE — Active Lanes header may have been removed mid-spawn (manual edit required)"
}
printf 'lane-spawn: appended lane section to %s\n' "$LANES_FILE"

printf '\nlane-spawn: lane "%s" spawned (issue=#%s branch=%s).\n' "$LANE" "$ISSUE" "$BRANCH"
printf 'next: git switch %s && start work\n' "$BRANCH"
exit 0
