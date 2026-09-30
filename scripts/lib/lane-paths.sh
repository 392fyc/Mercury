# scripts/lib/lane-paths.sh — per-project lane paths (multi-project x multi-lane).
# Sourced by the lane scripts; also exposed as a CLI by scripts/lane-paths.sh.
#
# Every project keeps ONE lane home, shared by all of its lanes (worktrees)
# and by both harnesses (Claude Code and Codex). It is based on the project's
# MAIN checkout (first entry of `git worktree list`), never on the current
# worktree, so every lane of a project resolves the same paths and each
# project gets its own registry. The encoding below is lossy (Proj-A, Proj.A
# and "Proj A" map to the same dir, exactly as Claude Code's own project dirs
# do), so keep main-checkout paths distinct in their ASCII letters/digits.
# Submodule / --separate-git-dir checkouts report their git dir as the first
# entry and are not supported as lane homes.
#
#   memory dir   (LANES.md, per-session files)
#       $MERCURY_MEMORY_DIR if set, else
#       ${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded main checkout>/memory
#       <encoded> = every non-alphanumeric char of the path replaced by '-',
#       the same rule Claude Code uses for its own project dirs, so for
#       D:/Mercury/Mercury this is the historical D--Mercury-Mercury.
#       Bytes are replaced under LC_ALL=C, so a non-ASCII path encodes the
#       same in every shell (one '-' per byte); prefer ASCII paths.
#       Claude Code's >200-char truncate+hash rule is not reproduced.
#       MERCURY_MEMORY_DIR is a single global override: leave it unset when
#       working on more than one project.
#   handoff dir  (session-handoff[-<lane>].md; transient, never durable memory)
#       <kb_dir>/handoff when the MAIN checkout's .handoff-config has
#       `kb_dir=<existing dir>`, else <main checkout>/.handoff
#
# Issue #613 (#599 ADR follow-up). Functions print the value and return 1
# (printing nothing) when the project cannot be resolved.

# lane_main_worktree [dir] — the project's main checkout, in the form git and
# LANES.md use (D:/... on Git for Windows via cygpath -m when available).
lane_main_worktree() {
  local dir="${1:-.}" p
  p=$(git -C "$dir" worktree list --porcelain 2>/dev/null | sed -n '1s/^worktree //p')
  [ -n "$p" ] || return 1
  if command -v cygpath >/dev/null 2>&1; then
    p=$(cygpath -m "$p" 2>/dev/null || printf '%s' "$p")
  fi
  printf '%s\n' "$p"
}

# lane_encode_project_dir <path> — Claude Code's project dir name for <path>.
lane_encode_project_dir() {
  printf '%s' "$1" | LC_ALL=C sed 's/[^A-Za-z0-9]/-/g'
}

# lane_memory_dir_for_main <main checkout path> — no git needed.
lane_memory_dir_for_main() {
  if [ -n "${MERCURY_MEMORY_DIR:-}" ]; then
    printf '%s\n' "$MERCURY_MEMORY_DIR"
    return 0
  fi
  [ -n "${1:-}" ] || return 1
  printf '%s/projects/%s/memory\n' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" \
    "$(lane_encode_project_dir "$1")"
}

# lane_memory_dir [dir]
lane_memory_dir() {
  if [ -n "${MERCURY_MEMORY_DIR:-}" ]; then
    printf '%s\n' "$MERCURY_MEMORY_DIR"
    return 0
  fi
  local main
  main=$(lane_main_worktree "${1:-.}") || return 1
  lane_memory_dir_for_main "$main"
}

# lane_kb_dir [dir] — kb_dir of the MAIN checkout's .handoff-config (same
# parsing as the handoff skill: trailing space, quotes, backslashes), printed
# only when it names an existing directory.
lane_kb_dir() {
  local main cfg kb
  main=$(lane_main_worktree "${1:-.}") || return 1
  cfg="$main/.handoff-config"
  [ -f "$cfg" ] || return 1
  kb=$(sed -n 's/^[[:space:]]*kb_dir[[:space:]]*=[[:space:]]*//p' "$cfg" | head -1)
  kb="${kb%"${kb##*[![:space:]]}"}"
  kb="${kb%\"}"; kb="${kb#\"}"
  kb=$(printf '%s' "$kb" | tr '\134' '/')
  [ -n "$kb" ] && [ -d "$kb" ] || return 1
  printf '%s\n' "$kb"
}

# lane_handoff_dir [dir]
lane_handoff_dir() {
  local kb main
  if kb=$(lane_kb_dir "${1:-.}"); then
    printf '%s/handoff\n' "$kb"
    return 0
  fi
  main=$(lane_main_worktree "${1:-.}") || return 1
  printf '%s/.handoff\n' "$main"
}

# lane_handoff_file <lane> [dir] — main lane has no suffix.
lane_handoff_file() {
  local lane="$1" hd
  hd=$(lane_handoff_dir "${2:-.}") || return 1
  if [ "$lane" = "main" ]; then
    printf '%s/session-handoff.md\n' "$hd"
  else
    printf '%s/session-handoff-%s.md\n' "$hd" "$lane"
  fi
}
