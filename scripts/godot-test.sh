#!/usr/bin/env bash
# godot-test.sh — Mercury wrapper to run a Godot 4 headless SceneTree test and return a
#   structured pass/fail/inconclusive that is robust against Godot's exit-code quirks.
#
# READ-ONLY INTENT on the target project, with one honest caveat: this wrapper itself only
#   runs `--script` (+ an optional `--import` pre-warm touching the gitignored .godot/ cache)
#   and never edits project source. HOWEVER `--script` is not *inherently* read-only — the
#   invoked GDScript could write files. This tool is meant for SoT's existing test scripts,
#   which are read-only; point it only at trusted test scripts. It is the SoT-workflow tool
#   from roadmap §3 — Mercury hosts it; it does NOT modify the SoT repos.
#
# Web-verified guardrails (2026-06-22):
#   - SceneTree.quit(N) exit code: probed reliable on Godot 4.6.1, but OLDER 4.x had
#     quit(1) -> exit 0 (https://github.com/godotengine/godot/issues/88055). We therefore
#     decide from BOTH the exit code AND a parsed stdout summary, and REFUSE to report `pass`
#     when no summary is parseable (verdict=inconclusive) so the quirk can never become a
#     silent false-pass.
#   - Optional pre-import (`--headless --import --quit-after 2`) registers resources/classes
#     and dodges the importer race (https://github.com/godotengine/godot/issues/77508).
#     Default OFF — see GODOT_PREIMPORT. NOTE: --import writes the project's gitignored
#     .godot/ cache (not source); off by default also avoids racing a live editor.
#   - GODOT_DISABLE_LEAK_CHECKS=1 so the exit code reflects tests, not editor shutdown.
#   - Godot 4 needs NO xvfb; use --headless (https://github.com/godotengine/godot/issues/43444).
#
# Summary-line contract (parsed for the verdict): a line containing a failure count as
#   "<N> 失败" or "<N> fail[ed|s|ures]" (digit first; ASCII or full-width space). SoT emits
#   "--- 结果:<P> 过 / <F> 失败 ---". If this contract drifts, the verdict degrades to
#   `inconclusive` (never a silent pass) — keep the producing test's summary in this shape.
#
# Usage:
#   GODOT_BIN=/path/to/Godot.exe ./godot-test.sh <test_script_res_path> [project_dir]
# Env:
#   GODOT_BIN        path to the Godot 4 executable (required if `godot` not on PATH)
#   SOT_PROJECT      default project dir when [project_dir] arg is omitted
#   GODOT_PREIMPORT  1 to pre-import (fresh checkout / no .godot cache). Default 0.
#
# Exit: 0 = all passed; 1 = failure OR inconclusive (fail-closed); 2 = usage/environment error.
set -uo pipefail

TEST_SCRIPT="${1:-}"
PROJECT_DIR="${2:-${SOT_PROJECT:-}}"

if [[ -z "$TEST_SCRIPT" || -z "$PROJECT_DIR" ]]; then
  echo "usage: GODOT_BIN=<godot> $0 <test_script_res_path> [project_dir]" >&2
  echo "       (or export SOT_PROJECT for the default project dir)" >&2
  exit 2
fi

GODOT="${GODOT_BIN:-}"
if [[ -z "$GODOT" ]]; then
  if command -v godot >/dev/null 2>&1; then GODOT="$(command -v godot)"; else
    echo "ERROR: Godot binary not found. Set GODOT_BIN=/path/to/Godot.exe" >&2
    exit 2
  fi
fi
if [[ ! -f "$GODOT" ]]; then
  echo "ERROR: GODOT_BIN '$GODOT' is not a file" >&2
  exit 2
fi
if [[ ! -f "$PROJECT_DIR/project.godot" ]]; then
  echo "ERROR: '$PROJECT_DIR' is not a Godot project (no project.godot)" >&2
  exit 2
fi

export GODOT_DISABLE_LEAK_CHECKS=1

echo "== godot-test: $TEST_SCRIPT =="
echo "   project: $PROJECT_DIR"
echo "   godot:   $GODOT"

if [[ "${GODOT_PREIMPORT:-0}" == "1" ]]; then
  echo "   pre-import: on"
  "$GODOT" --headless --path "$PROJECT_DIR" --import --quit-after 2 >/dev/null 2>&1 || true
fi

OUT="$("$GODOT" --headless --path "$PROJECT_DIR" --script "$TEST_SCRIPT" 2>&1)"
CODE=$?

printf '%s\n' "$OUT"
echo "--------------------------------------"

# A 126/127 exit (or failure to even launch) is an ENVIRONMENT error, not a test failure.
if [[ "$CODE" -eq 126 || "$CODE" -eq 127 ]]; then
  echo "ERROR: failed to launch Godot (exit $CODE) — check GODOT_BIN='$GODOT'" >&2
  exit 2
fi

# Display anchor: prefer a real summary line, fall back to any pass/fail-ish line.
SUMMARY="$(printf '%s\n' "$OUT" | grep -E '结果|---.*(过|失败)' | tail -1)"
[[ -z "$SUMMARY" ]] && SUMMARY="$(printf '%s\n' "$OUT" | grep -iE 'result|[0-9]+[ 　]*(过|失败|pass|fail)' | tail -1)"

# Verdict driver: parse a failure count in either language, tolerating a full-width space.
FAILN="$(printf '%s\n' "$OUT" | grep -oiE '[0-9]+[ 　]*(失败|failed|fails|failures|fail)' | grep -oE '[0-9]+' | tail -1)"

VERDICT="pass"; REASON=""
if [[ "$CODE" -ne 0 ]]; then VERDICT="fail"; REASON="exit code $CODE"; fi
if printf '%s\n' "$OUT" | grep -qE 'SCRIPT ERROR|Parse Error'; then
  VERDICT="fail"; REASON="${REASON:+$REASON; }hard error in stdout (SCRIPT ERROR/Parse Error)"
fi
if [[ -n "${FAILN:-}" && "$FAILN" -gt 0 ]]; then
  VERDICT="fail"; REASON="${REASON:+$REASON; }${FAILN} failure(s) reported in summary"
fi
# Fail-closed: exit 0 with no parseable summary/failure-count is NOT a pass — the test may
# never have reported (e.g. early return) or the summary format drifted. Refuse to assume pass.
if [[ "$VERDICT" == "pass" && "$CODE" -eq 0 && ( -z "$SUMMARY" || -z "${FAILN:-}" ) ]]; then
  VERDICT="inconclusive"
  REASON="exit 0 but no parseable pass/fail summary found — refusing to assume pass (see summary-line contract)"
fi
# Surface a signal disagreement (engine exit-code quirk) instead of hiding it.
if [[ "$CODE" -eq 0 && -n "${FAILN:-}" && "$FAILN" -gt 0 ]]; then
  REASON="$REASON [WARN: exit 0 but stdout shows ${FAILN} failures — engine exit-code quirk #88055?]"
fi

echo "godot-test verdict: $VERDICT  ${SUMMARY:+| summary: $SUMMARY}  ${REASON:+($REASON)}"
[[ "$VERDICT" == "pass" ]] && exit 0 || exit 1
