#!/usr/bin/env bash
# scripts/ci/run-isolated.sh <test-script> [args...]   (Mercury #686)
# <test-script> is a shell script (run with bash) or a Node test file *.test.cjs|js|mjs (run with node --test).
# `run-isolated.sh -- <command...>` runs any command (e.g. a Python unittest run) in the same isolated environment.
#
# Runs one shell test with every per-user location redirected into a throw-away directory, so the
# test can neither read nor write the real home of whoever runs it (a developer machine has a real
# ~/.handoff and ~/.claude/projects; CI has none, and the tests must not depend on either).
#
# What a lane or cleanup test can reach, found by reading the scripts (#686):
#   - .mercury/packs/lanes/scripts/lib/lane-paths.sh: ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/...
#     (memory dir) and, through .handoff-config, a kb_dir; also MERCURY_MEMORY_DIR as an override
#   - git: ~/.gitconfig and ~/.config/git (user identity, init.defaultBranch, safe.directory)
#   - gh:  ~/.config/gh and GH_TOKEN (a test that forgets to stub gh would talk to the network)
#   - mktemp / TMPDIR: the system temp directory, and any repository that happens to enclose it
#     (GIT_CEILING_DIRECTORIES)
#   - Windows-native children (git.exe, python.exe): USERPROFILE, APPDATA, LOCALAPPDATA, TEMP
# Each is pointed below into $ISO. The exit code of the test is the exit code of this script.
set -u

[ $# -ge 1 ] || { echo "usage: run-isolated.sh <test-script> [args...] | run-isolated.sh -- <command...>" >&2; exit 64; }
if [ "$1" = "--" ]; then
  shift
  [ $# -ge 1 ] || { echo "run-isolated: no command after --" >&2; exit 64; }
  label=$1; mode=command
else
  test_script=$1; shift
  [ -f "$test_script" ] || { echo "run-isolated: no such test: $test_script" >&2; exit 64; }
  test_script=$(cd "$(dirname "$test_script")" && pwd)/$(basename "$test_script")
  label=$(basename "$test_script")
  set -- "$test_script" "$@"; mode=file
fi

ISO=$(mktemp -d "${TMPDIR:-/tmp}/mercury-iso.XXXXXX") || exit 70
case "$(basename "$ISO")" in mercury-iso.*) ;; *) echo "run-isolated: unexpected temp dir $ISO" >&2; exit 70 ;; esac
cleanup() { cd / && rm -rf -- "$ISO"; }
trap cleanup EXIT

mkdir -p "$ISO/home" "$ISO/claude" "$ISO/codex" "$ISO/xdg/config" "$ISO/xdg/data" "$ISO/xdg/cache" \
         "$ISO/tmp" "$ISO/gh" "$ISO/appdata" "$ISO/localappdata"
native() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }

export HOME="$ISO/home"
export USERPROFILE; USERPROFILE=$(native "$ISO/home")
export APPDATA; APPDATA=$(native "$ISO/appdata")
export LOCALAPPDATA; LOCALAPPDATA=$(native "$ISO/localappdata")
export TMPDIR="$ISO/tmp"
export TMP; TMP=$(native "$ISO/tmp")
export TEMP="$TMP"
export XDG_CONFIG_HOME="$ISO/xdg/config" XDG_DATA_HOME="$ISO/xdg/data" XDG_CACHE_HOME="$ISO/xdg/cache"
export CLAUDE_CONFIG_DIR="$ISO/claude" CODEX_HOME="$ISO/codex" GH_CONFIG_DIR="$ISO/gh"
unset MERCURY_MEMORY_DIR CLAUDE_PROJECT_DIR GH_TOKEN GITHUB_TOKEN GH_HOST GH_ENTERPRISE_TOKEN
# A git hook or a calling git command leaves these set; a test repository must not inherit them.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_PREFIX GIT_OBJECT_DIRECTORY GIT_NAMESPACE

cat > "$ISO/gitconfig" <<'GITCFG'
[user]
	name = Mercury Test
	email = test@example.invalid
[safe]
	directory = *
GITCFG
export GIT_CONFIG_GLOBAL="$ISO/gitconfig"
# No other source of git settings: not the system file (init.defaultBranch, credential helpers, core.autocrlf), not
# `git -c` leftovers from a calling git command (GIT_CONFIG_PARAMETERS, GIT_CONFIG_COUNT and its KEY_n / VALUE_n).
export GIT_CONFIG_NOSYSTEM=1
unset GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT
for name in $(compgen -e | grep -E '^GIT_CONFIG_(KEY|VALUE)_[0-9]+$'); do unset "$name"; done
# Git finds a repository by walking up from the working directory. A temporary directory that is not a repository
# must stay "not a repository", as on CI; on a developer machine the system temp dir can sit inside a repository
# (observed: the user's home directory was one, so lane-spawn resolved its "main checkout" to the home directory).
# $ISO is the ceiling: git looks inside it, never above it.
export GIT_CEILING_DIRECTORIES="$ISO"

echo "run-isolated: $label with HOME=$HOME"
if [ "$mode" = file ]; then
  case "$1" in
    *.cjs|*.js|*.mjs) node --test "$@" ;;
    *) bash "$@" ;;
  esac
else
  "$@"
fi
rc=$?
touched=$(find "$ISO/home" "$ISO/claude" "$ISO/codex" "$ISO/xdg" "$ISO/gh" "$ISO/appdata" "$ISO/localappdata" -mindepth 1 -type f 2>/dev/null | wc -l | tr -d ' ')
echo "run-isolated: exit $rc; files the test left in the isolated user dirs: $touched"
exit "$rc"
