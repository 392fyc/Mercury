#!/usr/bin/env bash
# scripts/lane-paths.sh — print a project's lane paths (Issue #613).
# CLI over scripts/lib/lane-paths.sh; see that file for the resolution rules
# (one lane home per project, based on its main checkout).
#
# Usage:
#   scripts/lane-paths.sh <what> [--repo-root PATH] [--lane NAME]
#     <what>: main-worktree | memory-dir | lanes-file | handoff-dir | handoff-file
#     --repo-root  any checkout or worktree of the project (default: cwd)
#     --lane       lane name for handoff-file (default: main)
#
# Exit codes:
#   0  printed
#   2  invalid args / cannot resolve the project (not inside a git checkout)

set -u

die() { printf 'lane-paths: %s\n' "$1" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/lane-paths.sh
. "$SCRIPT_DIR/lib/lane-paths.sh"

WHAT=""
DIR="."
LANE="main"
while [ $# -gt 0 ]; do
  case "$1" in
    --repo-root) shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--repo-root needs a value"; DIR="$1"; shift ;;
    --lane)      shift; [ $# -gt 0 ] && [ -n "$1" ] || die "--lane needs a value"; LANE="$1"; shift ;;
    -h|--help)   sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) die "unknown flag: $1" ;;
    *)  [ -z "$WHAT" ] || die "unexpected argument: $1"; WHAT="$1"; shift ;;
  esac
done
case "$LANE" in
  -*|*[!A-Za-z0-9_-]*) die "lane name must be [A-Za-z0-9_-]: '$LANE'" ;;
esac

fail_resolve() { die "cannot resolve the project at '$DIR' (run inside a checkout or pass --repo-root)"; }
case "$WHAT" in
  main-worktree) lane_main_worktree "$DIR" || fail_resolve ;;
  memory-dir)    lane_memory_dir "$DIR" || fail_resolve ;;
  lanes-file)    md=$(lane_memory_dir "$DIR") || fail_resolve; printf '%s/LANES.md\n' "$md" ;;
  handoff-dir)   lane_handoff_dir "$DIR" || fail_resolve ;;
  handoff-file)  lane_handoff_file "$LANE" "$DIR" || fail_resolve ;;
  '') die "missing <what> (try --help)" ;;
  *)  die "unknown <what>: $WHAT" ;;
esac
