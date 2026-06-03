# -*- coding: utf-8 -*-
"""stop_notify — Stop-hook worker: speak the agent's reply at turn end (Issue #468).

The fallback half of the bidirectional loop (ADR §4 path B). Claude Code fires the
Stop hook when the agent finishes a turn; this worker reads the hook's stdin JSON,
and — only while a voice mode is active (mode != idle) — speaks the agent's last
message via local TTS so the user is prompted to resume voice interaction.

Extracting the reply text (ADR §6 risk): prefer the `last_assistant_message` field
if the hook payload carries it (community-observed, NOT in the official hooks-doc
field list — so treated as best-effort); otherwise fall back to parsing the last
assistant message out of the transcript_path JSONL (`.message.content[].text`). The
`/clear`-corrupts-transcript caveat is why last_assistant_message is preferred.

Self-guards (never disrupt a non-voice session):
  - mode == idle            -> exit silently
  - stop_hook_active == true -> exit (avoid any re-entrancy)
  - any error               -> exit 0 (a TTS hiccup must never block Claude)

Invoked by .claude/hooks/voice-stop-notify.sh (opt-in; not registered by default).
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _last_assistant_from_transcript(path):
    """Return the text of the last assistant message in a Claude Code transcript JSONL."""
    if not path or not os.path.exists(path):
        return ""
    last = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = obj.get("message") or {}
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    last = content
                elif isinstance(content, list):
                    parts = [c.get("text", "") for c in content
                             if isinstance(c, dict) and c.get("type") == "text"]
                    if parts:
                        last = "".join(parts)
    except OSError:
        return ""
    return last


def _strip_markdown(text):
    """Light markdown cleanup so TTS does not read code fences / heading marks aloud."""
    import re
    text = re.sub(r"```.*?```", "（代码块略）", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_>]+", "", text)
    return text.strip()


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0

    if payload.get("stop_hook_active"):
        return 0  # avoid any re-entrancy

    try:
        import state as _state
    except Exception:
        return 0
    if _state.get_mode() == "idle":
        return 0  # voice interaction not active — no-op

    text = (payload.get("last_assistant_message") or "").strip()
    if not text:
        text = _last_assistant_from_transcript(payload.get("transcript_path"))
    text = _strip_markdown(text)
    if not text:
        return 0

    # cap spoken length so a long reply does not monopolize playback
    cap = int(os.environ.get("VOICE_STOP_MAX_CHARS", "400") or "400")
    if len(text) > cap:
        text = text[:cap] + "……（后续内容请看屏幕）"

    try:
        import tts as _tts
        _tts.speak(text, blocking=True)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
