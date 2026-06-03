#!/usr/bin/env bash
# voice-stop-notify — Stop hook: speak the agent's reply at turn end (Issue #468).
#
# Opt-in: NOT registered in .claude/settings.json by default (would spawn a Python
# process every turn for the whole team + require .venv-voice). Enable it yourself by
# adding a Stop matcher entry — see scripts/voice/README.md. The worker self-guards:
# it no-ops unless a voice mode is active (mode != idle), so even if registered it is
# silent for non-voice sessions.
#
# Reads the Stop-hook JSON on stdin and pipes it to the voice venv's stop_notify.py.
# Always exits 0 — a TTS hiccup must never block Claude Code.
set -euo pipefail

PROJ="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
PY="$PROJ/.venv-voice/Scripts/python.exe"          # native Windows venv
[ -x "$PY" ] || PY="$PROJ/.venv-voice/bin/python"  # posix venv fallback

# voice venv not installed -> nothing to do
if [ ! -x "$PY" ]; then
  cat >/dev/null 2>&1 || true   # drain stdin
  exit 0
fi

"$PY" "$PROJ/scripts/voice/stop_notify.py" || true
exit 0
