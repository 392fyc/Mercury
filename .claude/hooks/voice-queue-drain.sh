#!/usr/bin/env bash
# voice-queue-drain — Stop hook: re-inject voice the user spoke DURING the turn (Issue #495 Slice 5).
#
# Opt-in: NOT registered in .claude/settings.json by default (would spawn a Python process every
# turn for the whole team + require .venv-voice). Enable it yourself by appending a Stop matcher
# entry alongside stop-guard.sh / voice-stop-notify.sh — see scripts/voice/README.md §Path 2.
# The worker self-guards: it no-ops unless a voice mode is active AND the transcript queue is
# non-empty, so even if registered it is silent for non-voice sessions.
#
# Reads the Stop-hook JSON on stdin and pipes it to the voice venv's queue_drain.py. The worker
# may emit a top-level {"decision":"block","reason":...} to continue the turn with the queued
# speech in context. `"$PY" ... || true` preserves that stdout (only the exit code is swallowed),
# so the block decision reaches Claude Code even if Python exits non-zero. Always exits 0 — a
# queue hiccup must never block Claude Code.
set -euo pipefail

PROJ="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
# VOICE_QUEUE_DRAIN_PYTHON overrides the interpreter for non-default deploy layouts; else fall
# back to the documented shared venv (.venv-voice) on native Windows / posix.
PY="${VOICE_QUEUE_DRAIN_PYTHON:-}"
if [ -z "$PY" ]; then
  PY="$PROJ/.venv-voice/Scripts/python.exe"          # native Windows venv
  [ -x "$PY" ] || PY="$PROJ/.venv-voice/bin/python"  # posix venv fallback
fi

# voice venv not installed -> nothing to do
if [ ! -x "$PY" ]; then
  cat >/dev/null 2>&1 || true   # drain stdin
  exit 0
fi

"$PY" "$PROJ/scripts/voice/queue_drain.py" || true
exit 0
