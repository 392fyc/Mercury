# .mercury/packs/lanes/scripts/lib/lane-paths.sh — per-project lane paths (multi-project x multi-lane).
# Sourced by the lane scripts; also exposed as a CLI by .mercury/packs/lanes/scripts/lane-paths.sh.
#
# Every project keeps ONE lane home, shared by all of its lanes (worktrees)
# and by both harnesses (Claude Code and Codex). It is based on the project's
# MAIN checkout (first entry of `git worktree list`), never on the current
# worktree, so every lane of a project resolves the same paths and each
# project gets its own registry. The encoding below is lossy (Proj-A, Proj.A
# and "Proj A" map to the same dir, as Claude Code's own project dirs do);
# lane_memory_dir_for_main detects that through the registry's recorded main
# worktree and moves the later project to a hashed sibling dir.
# Submodule / --separate-git-dir checkouts report their git dir as the first
# entry and are not supported as lane homes.
#
#   memory dir   (LANES.md, per-session files)
#       $MERCURY_MEMORY_DIR if set, else
#       the project binding: when the MAIN checkout has a one-line
#       .mercury/memory/lane-home file, the directory it names relative to
#       .mercury/memory (e.g. projects/D--Mercury-Mercury). .mercury/memory is
#       the project's private active memory (gitignored), so the binding is
#       per project and per machine. An invalid binding (empty, '.',
#       absolute, '..', backslash, missing, or resolving outside
#       .mercury/memory) is an error, never a silent fallback (#644). Else
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

# lane_norm_path <path> — comparison key: '/' separators, no trailing '/',
# MSYS /d/... as d:/..., lower case (paths that encode alike differ only in
# non-alphanumerics, so case folding never merges two real projects).
lane_norm_path() {
  printf '%s' "$1" | tr '\134' '/' | sed -e 's#/*$##' -e 's#^/\([A-Za-z]\)/#\1:/#' -e 's#^/\([A-Za-z]\)$#\1:#' \
    | tr '[:upper:]' '[:lower:]'
}

# lane_registered_main <LANES.md> — the `main` lane's Worktree path recorded
# in a registry, as written; empty when absent.
lane_registered_main() {
  [ -f "$1" ] || return 0
  awk '
    /^```/ { fence = !fence; next }
    fence { next }
    /^### `/ { in_main = ($0 ~ /^### `main`/); next }
    /^## / { in_main = 0; next }
    in_main && /\*\*Worktree path\*\*/ {
      if (match($0, /`[^`]+`/)) { print substr($0, RSTART + 1, RLENGTH - 2); exit }
    }
  ' "$1" | tr -d '\r'
}

# lane_bound_memory_dir <main checkout path> — the project binding.
# Returns 1 with no output when there is no binding file; returns 2 with a
# message on stderr when the file exists but is invalid.
lane_bound_memory_dir() {
  local f rel
  f="$1/.mercury/memory/lane-home"
  [ -f "$f" ] || return 1
  rel=$(sed -n '1{s/\r$//;s/^[[:space:]]*//;s/[[:space:]]*$//;p;}' "$f")
  local root real
  case "$rel" in
    ""|.|./*|/*|*..*|*'\'*|[A-Za-z]:*)
      printf 'lane-paths: invalid lane-home binding in %s; fix or delete that file\n' "$f" >&2
      return 2 ;;
  esac
  rel="${rel%/}"
  if [ ! -d "$1/.mercury/memory/$rel" ]; then
    printf 'lane-paths: lane-home binding names a missing directory %s; fix or delete %s\n' "$rel" "$f" >&2
    return 2
  fi
  # The bound directory must stay inside .mercury/memory even through links.
  if ! root=$(cd "$1/.mercury/memory" && pwd -P) || ! real=$(cd "$1/.mercury/memory/$rel" && pwd -P); then
    printf 'lane-paths: cannot resolve the lane-home binding; fix or delete %s\n' "$f" >&2
    return 2
  fi
  case "$real" in
    "$root"/*) ;;
    *) printf 'lane-paths: lane-home binding leaves .mercury/memory; fix or delete %s\n' "$f" >&2
       return 2 ;;
  esac
  printf '%s/.mercury/memory/%s\n' "$1" "$rel"
}

# lane_memory_dir_for_main <main checkout path> — no git needed.
# The encoding is lossy (Proj-A and Proj.A both become ...Proj-A). When the
# plain dir already holds a LANES.md whose `main` lane is a DIFFERENT checkout,
# this project uses <encoded>-lh<cksum of its path> instead, so two projects
# never share a registry; non-colliding projects keep the plain dir.
lane_memory_dir_for_main() {
  if [ -n "${MERCURY_MEMORY_DIR:-}" ]; then
    printf '%s\n' "$MERCURY_MEMORY_DIR"
    return 0
  fi
  [ -n "${1:-}" ] || return 1
  local base enc main reg sum rc=0
  lane_bound_memory_dir "$1" || rc=$?
  case "$rc" in
    0) return 0 ;;
    2) return 1 ;;
  esac
  base="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects"
  enc=$(lane_encode_project_dir "$1")
  main=$(lane_norm_path "$1")
  reg=$(lane_registered_main "$base/$enc/memory/LANES.md")
  if [ -n "$reg" ] && [ "$(lane_norm_path "$reg")" != "$main" ]; then
    sum=$(printf '%s' "$main" | cksum | cut -d' ' -f1)
    printf '%s/%s-lh%s/memory\n' "$base" "$enc" "$sum"
    return 0
  fi
  printf '%s/%s/memory\n' "$base" "$enc"
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

# ── Branch naming and integration branch (Issue #703) ─────────────────────
# Branch format: <type>/<purpose>[-<lane>][-<agent>]. The lane and the agent
# are SUFFIXES, never prefixes, so a person reading `git branch` sees the
# purpose first. The lane is a registered lane short name; the agent is
# `claude` or `codex`. A branch belongs to the lane whose short name it ends
# with once the agent suffix is removed; when several registered names match,
# the longest wins (`side-mlane` beats `mlane`).

# lane_branch_types — the allowed <type> prefixes, one per line.
lane_branch_types() {
  printf '%s\n' feature feat fix hotfix docs chore refactor test perf ci build
}

# lane_branch_format_ok <branch> — 0 when <branch> is `<type>/<purpose>` in the
# new format (purpose: [A-Za-z0-9._-], starts with a letter or digit, no '/',
# no length cap). The `lane/`, `codex/`, `claude/` prefixes never match, and
# `mercury/` is reserved for the update bot.
lane_branch_format_ok() {
  case "$1" in
    feature/*|feat/*|fix/*|hotfix/*|docs/*|chore/*|refactor/*|test/*|perf/*|ci/*|build/*) ;;
    *) return 1 ;;
  esac
  local purpose="${1#*/}"
  case "$purpose" in
    ''|*/*|[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*) return 1 ;;
  esac
  return 0
}

# lane_branch_strip_agent <branch> — the branch without a trailing -claude / -codex.
lane_branch_strip_agent() {
  case "$1" in
    *-claude) printf '%s\n' "${1%-claude}" ;;
    *-codex)  printf '%s\n' "${1%-codex}" ;;
    *)        printf '%s\n' "$1" ;;
  esac
}

# lane_branch_owner <branch> <name>... — print the longest <name> that the
# branch ends with (after removing the agent suffix); return 1 when none does.
# Only the final path segment is considered, and a name must follow a '-'.
lane_branch_owner() {
  lane_branch_owner_set "$@"
  [ -n "$LANE_BRANCH_OWNER" ] || return 1
  printf '%s\n' "$LANE_BRANCH_OWNER"
}

# lane_branch_owner_set <branch> <name>... — same rule as lane_branch_owner but
# the result goes to the global LANE_BRANCH_OWNER ("" when none): pure bash, no
# subshell and no process, so scans over many branches and lanes stay cheap.
lane_branch_owner_set() {
  local base="$1" n
  LANE_BRANCH_OWNER=""
  shift
  case "$base" in
    *-claude) base="${base%-claude}" ;;
    *-codex)  base="${base%-codex}" ;;
  esac
  for n in "$@"; do
    [ -n "$n" ] || continue
    case "$base" in
      */*-"$n")
        if [ "${#n}" -gt "${#LANE_BRANCH_OWNER}" ]; then LANE_BRANCH_OWNER="$n"; fi ;;
    esac
  done
}

# lane_registered_pairs <lanes-file> — "<lane>\t<name>" per lane section, where
# <name> is the section's Short name when it has one, else the lane name (the
# fallback the lane scripts use everywhere). Fenced blocks are skipped.
lane_registered_pairs() {
  [ -f "$1" ] || return 1
  tr -d '\r' < "$1" | awk '
    function flush() { if (lane != "") print lane "\t" (short != "" ? short : lane); lane = ""; short = "" }
    /^[[:space:]]*(```|~~~)/ { fence = !fence; next }
    fence { next }
    /^### `[^`]+`/ { flush(); match($0, /^### `[^`]+`/); lane = substr($0, RSTART + 5, RLENGTH - 6); next }
    /^##+ / { flush(); next }
    lane != "" && short == "" && /\*\*Short name\*\*/ {
      s = $0; i = index(s, "**Short name**"); s = substr(s, i + 14)
      c = index(s, ":"); if (c == 0) next
      s = substr(s, c + 1); sub(/^[[:space:]]+/, "", s)
      if (substr(s, 1, 1) == "`") { s = substr(s, 2); b = index(s, "`"); if (b > 0) short = substr(s, 1, b - 1) }
      else { for (i = 1; i <= length(s); i++) { ch = substr(s, i, 1); if (ch == " " || ch == "\t" || ch == "`" || ch == "(") break }
             if (i > 1) short = substr(s, 1, i - 1) }
    }
    END { flush() }
  '
}

# lane_registered_names <lanes-file> — every lane's branch-suffix name, one per line.
lane_registered_names() { lane_registered_pairs "$1" | cut -f2; }

# lane_short_name <lanes-file> <lane> — the lane's branch-suffix name (its Short
# name, else the lane name itself).
lane_short_name() {
  local n
  # ENVIRON, not -v: awk -v would interpret backslash escapes in the lane name.
  n=$(lane_registered_pairs "$1" 2>/dev/null | L="$2" awk -F'\t' '$1 == ENVIRON["L"] { print $2; exit }')
  printf '%s\n' "${n:-$2}"
}

# lane_branch_last_ts <repo-dir> <lane> <lanes-file> — newest committer date
# (unix seconds) over the local and remote branches that belong to <lane>;
# empty when there are none. Only the #703 suffix format counts. A branch belongs to the lane whose registered name
# it ends with (longest wins); the main lane also owns new-format branches no
# other lane claims.
lane_branch_last_ts() {
  local repo="$1" lane="$2" lf="$3" short names ref ts name owner
  short=$(lane_short_name "$lf" "$lane")
  names=$(lane_registered_names "$lf" 2>/dev/null || true)
  while IFS=' ' read -r ref ts; do
    [ -n "$ref" ] && [ -n "$ts" ] || continue
    case "$ref" in
      refs/heads/*) name="${ref#refs/heads/}" ;;
      refs/remotes/*) name="${ref#refs/remotes/}"; name="${name#*/}" ;;
      *) continue ;;
    esac
    [ "$name" = HEAD ] && continue
    # The old main-lane form `feature/TASK-*` is an ordinary new-format name:
    # it counts for main only when no other lane's suffix owns it (#703 review M1).
    lane_branch_format_ok "$name" || continue
    # shellcheck disable=SC2086
    owner=$(lane_branch_owner "$name" "$short" $names || true)
    if [ "$owner" = "$short" ] || { [ "$lane" = main ] && [ -z "$owner" ]; }; then
      printf '%s\n' "$ts"; return 0
    fi
  done < <(git -C "$repo" for-each-ref --sort=-committerdate \
             --format='%(refname) %(committerdate:unix)' refs/heads refs/remotes 2>/dev/null)
  return 0
}

# lane_integration_branch [dir] — the branch task branches start from and merge
# into, without `origin/`. Order, first hit wins, no guessing:
#   1. $MERCURY_BASE_BRANCH
#   2. params.integration_branch in <repo root>/.mercury/config.json
#      (skipped when no working python is found)
#   3. the branch origin/HEAD points to
# Prints nothing and returns 1 when none resolves; callers must fail then.
lane_integration_branch() {
  local dir="${1:-.}" b="" root cfg py
  if [ -n "${MERCURY_BASE_BRANCH:-}" ]; then
    printf '%s\n' "$MERCURY_BASE_BRANCH"; return 0
  fi
  root=$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null) || root=""
  cfg="$root/.mercury/config.json"
  if [ -n "$root" ] && [ -f "$cfg" ]; then
    for py in python3 python; do
      command -v "$py" >/dev/null 2>&1 || continue
      b=$("$py" -I -c 'import json,sys
try:
    v=json.load(open(sys.argv[1],encoding="utf-8")).get("params",{}).get("integration_branch","")
    print(v if isinstance(v,str) else "")
except Exception:
    pass' "$cfg" 2>/dev/null | tr -d '\r') || b=""
      [ -n "$b" ] && break
    done
    if [ -n "$b" ]; then printf '%s\n' "$b"; return 0; fi
  fi
  b=$(git -C "$dir" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null) || b=""
  b="${b#origin/}"
  [ -n "$b" ] || return 1
  printf '%s\n' "$b"
}
