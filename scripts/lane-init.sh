#!/usr/bin/env bash
# scripts/lane-init.sh — create a valid LANES.md on a machine that has none.
# Issue #607 (the user-level lane registry was lost in the #579 machine
# migration; lane scripts exit with "memory dir not found" / "LANES.md not
# found" until one exists).
#
# Writes a minimal registry that the other lane scripts accept: an
# "## Active Lanes" section holding the `main` lane (Short name, Harness,
# Worktree path, Handoff file, Status) followed by "## Closed Lanes". The
# `main` Worktree path is what lane-assertion.sh compares the cwd against.
# Harness follows the #599 ADR (one harness per lane; claude | codex).
#
# Safe to re-run: an existing LANES.md is never modified or overwritten; a
# symlink or non-regular file at that path is refused, never written through.
#
# Usage:
#   scripts/lane-init.sh [--memory-dir PATH] [--main-worktree PATH]
#                        [--main-harness claude|codex] [--dry-run]
#
# Defaults:
#   --memory-dir     ${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}
#                    (created if missing; LANES.md goes inside it)
#   --main-worktree  the main checkout (first entry of `git worktree list`),
#                    also when run from a linked lane worktree; must be absolute
#   --main-harness   claude
#
# Exit codes:
#   0  LANES.md created (or --dry-run printed it, or a regular LANES.md
#      already existed and was left unchanged)
#   2  invalid args / cannot resolve worktree / LANES.md path is a symlink or
#      not a regular file / memory dir unusable / cannot create or write

set -u

die() { printf 'lane-init: %s\n' "$1" >&2; exit 2; }

MEMORY_DIR=""
MAIN_WORKTREE=""
MAIN_HARNESS=claude
DRY_RUN=0

while [ $# -gt 0 ]; do
  case "$1" in
    --memory-dir)    shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--memory-dir needs a non-empty value"
                     MEMORY_DIR="$1"; shift ;;
    --main-worktree) shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--main-worktree needs a non-empty value"
                     MAIN_WORKTREE="$1"; shift ;;
    --main-harness)  shift; [ $# -gt 0 ] || die "--main-harness needs a value"
                     MAIN_HARNESS="$1"; shift ;;
    --dry-run)       DRY_RUN=1; shift ;;
    -h|--help)
      sed -n '2,31p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  die "unexpected positional argument: $1" ;;
  esac
done

case "$MAIN_HARNESS" in
  claude|codex) ;;
  *) die "--main-harness must be claude or codex: '$MAIN_HARNESS'" ;;
esac

if [ -z "$MEMORY_DIR" ]; then
  MEMORY_DIR="${MERCURY_MEMORY_DIR:-${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/D--Mercury-Mercury/memory}"
fi
LANES_FILE="$MEMORY_DIR/LANES.md"

if [ -z "$MAIN_WORKTREE" ]; then
  # The main checkout is the first entry of `git worktree list`, also when this
  # runs from a linked lane worktree (--show-toplevel would return that one).
  MAIN_WORKTREE=$(git worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')
  [ -n "$MAIN_WORKTREE" ] \
    || die "cannot resolve the main worktree (run inside the Mercury checkout or pass --main-worktree)"
fi
case "$MAIN_WORKTREE" in
  /*|[A-Za-z]:/*|[A-Za-z]:\\*) ;;
  *) die "--main-worktree must be an absolute path: '$MAIN_WORKTREE'" ;;
esac
case "$MAIN_WORKTREE" in
  *'`'*|*$'\n'*) die "--main-worktree must not contain backticks or newlines: '$MAIN_WORKTREE'" ;;
esac

if [ -e "$MEMORY_DIR" ] && [ ! -d "$MEMORY_DIR" ]; then
  die "memory dir exists but is not a directory: $MEMORY_DIR"
fi
if [ -L "$LANES_FILE" ]; then
  die "$LANES_FILE is a symlink — refusing to write through it (inspect it by hand)"
fi
if [ -e "$LANES_FILE" ]; then
  [ -f "$LANES_FILE" ] || die "$LANES_FILE exists but is not a regular file — lane scripts cannot read it"
  printf 'lane-init: %s already exists — left unchanged.\n' "$LANES_FILE"
  exit 0
fi
if [ ! -d "$MAIN_WORKTREE" ]; then
  printf 'lane-init WARN: main worktree %s does not exist here; lane-assertion will block until it does (or pass --main-worktree)\n' "$MAIN_WORKTREE" >&2
fi

CONTENT=$(cat <<EOF
# Mercury Lanes Registry

Created by scripts/lane-init.sh (Issue #607). Lane rules: see the #599 ADR
(.mercury/docs/research/issue-599-cross-harness-lane-isolation-2026-09.md).
Each lane edits only its own section.

## Active Lanes

### \`main\` (default lane)

- **Short name**: \`main\`
- **Harness**: \`${MAIN_HARNESS}\`
- **Worktree path**: \`${MAIN_WORKTREE}\`
- **Handoff file**: \`session-handoff.md\`
- **Status**: \`active\`

## Closed Lanes

(none)
EOF
)

if [ "$DRY_RUN" -eq 1 ]; then
  printf '[dry-run] would create %s with:\n\n%s\n' "$LANES_FILE" "$CONTENT"
  exit 0
fi

mkdir -p "$MEMORY_DIR" 2>/dev/null || die "cannot create memory dir: $MEMORY_DIR"
# Claim the path with noclobber first (fails if anything, including a dangling
# symlink, appeared since the checks above), then fill the file we now own; a
# failed write removes our partial file so a re-run does not see it as valid.
( set -C; : > "$LANES_FILE" ) 2>/dev/null \
  || die "$LANES_FILE appeared while initializing — left untouched"
if ! printf '%s\n' "$CONTENT" > "$LANES_FILE"; then
  rm -f "$LANES_FILE"
  die "cannot write $LANES_FILE (partial file removed)"
fi

printf 'lane-init: created %s (main lane: harness=%s, worktree=%s)\n' \
  "$LANES_FILE" "$MAIN_HARNESS" "$MAIN_WORKTREE"
if [ -z "${MERCURY_MEMORY_DIR:-}" ]; then
  printf 'lane-init: tip: set MERCURY_MEMORY_DIR to this directory for both Claude Code and Codex so neither relies on the default path.\n'
fi
exit 0
