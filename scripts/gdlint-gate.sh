#!/usr/bin/env bash
# gdlint-gate.sh — Mercury GDScript pre-commit gate via gdtoolkit (Scony/godot-gdscript-toolkit).
#   Runs gdlint (style+syntax) + gdformat --check (formatting) + optional gdparse (pure syntax),
#   all READ-ONLY (gdformat uses --check; nothing is rewritten).
#
# This is the SoT-workflow tool from roadmap §3 — Mercury hosts it; it does NOT modify the SoT
# repos. Meant to be run by a dev subagent / CI against changed .gd files or directories.
#
# Tooling (web-verified + probed 2026-06-22):
#   gdtoolkit 4.5.0 — PyPI, pure-Python, no engine. https://pypi.org/project/gdtoolkit/
#   Exit codes: gdlint 0=clean/non-0=problems; gdformat --check 0=formatted/non-0=would-reformat
#     (never writes); gdparse 0=syntax ok.
#   Directory recursion: gdlint <dir> AND gdformat --check <dir> BOTH recurse into subdirectories
#     (probed: a nested .gd with an unused-argument was reported). So directory args are passed
#     straight through to gdlint/gdformat. gdparse does NOT take a directory, so we expand it.
#
# Usage:
#   ./gdlint-gate.sh <file_or_dir> [more files/dirs...]
# Env:
#   GDTOOLKIT_BIN       dir holding gdlint/gdformat/gdparse (default: Mercury's bundled venv at
#                       .mercury/tools/gdtoolkit-venv — Scripts/ on Windows, bin/ on POSIX,
#                       auto-probed). Install: python -m venv <dir> &&
#                       <dir>/Scripts/python -m pip install "gdtoolkit==4.*"   (Windows)
#                       <dir>/bin/python -m pip install "gdtoolkit==4.*"       (POSIX)
#   GDLINT_GATE_FORMAT  0 to skip the gdformat --check stage (default 1)
#   GDLINT_GATE_PARSE   1 to also run gdparse per .gd file (default 0; gdlint already parses)
#
# Exit: 0 = clean; 1 = lint/format problems found; 2 = usage/environment error.
set -uo pipefail

if [[ "$#" -lt 1 ]]; then
  echo "usage: $0 <file_or_dir> [more...]" >&2
  exit 2
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Default venv layout differs by platform: Windows puts entry points under Scripts/, POSIX
# under bin/. Probe both so the default works on Git Bash AND Linux CI without overrides.
DEFAULT_VENV="$REPO_ROOT/.mercury/tools/gdtoolkit-venv"
if [[ -n "${GDTOOLKIT_BIN:-}" ]]; then
  BIN="$GDTOOLKIT_BIN"
elif [[ -d "$DEFAULT_VENV/Scripts" ]]; then
  BIN="$DEFAULT_VENV/Scripts"
else
  BIN="$DEFAULT_VENV/bin"
fi

# Tools carry a .exe suffix in a Windows venv, none on POSIX — resolve either.
resolve() {
  if [[ -f "$BIN/$1.exe" ]]; then echo "$BIN/$1.exe"
  elif [[ -f "$BIN/$1" ]]; then echo "$BIN/$1"
  else echo ""; fi
}
GDLINT="$(resolve gdlint)"; GDFORMAT="$(resolve gdformat)"; GDPARSE="$(resolve gdparse)"

# Fail closed on a requested-but-missing tool, so a gate is never SILENTLY skipped (a skipped
# gate that still PASSes is worse than a loud environment error).
if [[ -z "$GDLINT" ]]; then
  echo "ERROR: gdlint not found under '$BIN'. Set GDTOOLKIT_BIN or install gdtoolkit:" >&2
  echo "  python -m venv <dir> && <dir>/Scripts/python -m pip install \"gdtoolkit==4.*\"  (Windows)" >&2
  echo "  python -m venv <dir> && <dir>/bin/python -m pip install \"gdtoolkit==4.*\"      (POSIX)" >&2
  exit 2
fi
if [[ "${GDLINT_GATE_FORMAT:-1}" == "1" && -z "$GDFORMAT" ]]; then
  echo "ERROR: gdformat requested (GDLINT_GATE_FORMAT=1) but not found under '$BIN'." >&2
  echo "       Install gdtoolkit, or set GDLINT_GATE_FORMAT=0 to skip the format stage." >&2
  exit 2
fi
if [[ "${GDLINT_GATE_PARSE:-0}" == "1" && -z "$GDPARSE" ]]; then
  echo "ERROR: gdparse requested (GDLINT_GATE_PARSE=1) but not found under '$BIN'." >&2
  echo "       Install gdtoolkit, or set GDLINT_GATE_PARSE=0 to skip the parse stage." >&2
  exit 2
fi

problems=0

for target in "$@"; do
  if [[ ! -e "$target" ]]; then
    echo "WARN: skip non-existent path: $target" >&2
    problems=$((problems+1))  # fail-closed: a bad path shouldn't silently pass the gate
    continue
  fi

  # gdlint and gdformat both recurse directories (probed) — pass the target straight through.
  echo "== gdlint: $target =="
  if ! "$GDLINT" "$target"; then problems=$((problems+1)); fi

  if [[ "${GDLINT_GATE_FORMAT:-1}" == "1" ]]; then
    echo "== gdformat --check: $target =="
    if ! "$GDFORMAT" --check "$target"; then problems=$((problems+1)); fi
  fi

  if [[ "${GDLINT_GATE_PARSE:-0}" == "1" ]]; then
    # gdparse is per-file; expand a directory to its .gd files via find.
    if [[ -d "$target" ]]; then
      found_any=0
      while IFS= read -r -d '' f; do
        found_any=1
        "$GDPARSE" "$f" >/dev/null 2>&1 || { echo "gdparse syntax error: $f" >&2; problems=$((problems+1)); }
      done < <(find "$target" -name '*.gd' -print0 2>/dev/null)
      # Fail-closed: an empty traversal (no .gd, or find errored) on an explicitly requested
      # parse stage must count as a problem, not silently read as clean.
      if [[ "$found_any" -eq 0 ]]; then
        echo "WARN: gdparse found no .gd under '$target' (empty dir or traversal error) — counting as a problem (fail-closed)" >&2
        problems=$((problems+1))
      fi
    else
      "$GDPARSE" "$target" >/dev/null 2>&1 || { echo "gdparse syntax error: $target" >&2; problems=$((problems+1)); }
    fi
  fi
done

echo "--------------------------------------"
if [[ "$problems" -gt 0 ]]; then
  echo "gdlint-gate: FAIL — $problems check(s) reported problems"
  exit 1
fi
echo "gdlint-gate: PASS"
exit 0
