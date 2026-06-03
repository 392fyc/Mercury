# -*- coding: utf-8 -*-
"""state — secretary/work dual-mode state machine + secretary note-taking (Issue #468).

Two modes (ADR §5):
  secretary — low-latency transcription + structured note-taking. Records/summarizes
              what the user says; does NOT execute tasks. The fast information-intake
              channel.
  work      — the agent autonomously advances a concrete task using the requirements
              gathered in secretary mode. On a decision point / missing info /
              completion, it does a TTS check-in (announce) and may listen back.

Switching is EXPLICIT (the user declares /secretary or /work) — never auto-inferred.

State is persisted to a JSON file shared by the MCP server (in-session, writes) and
the Stop hook (reads the mode to decide whether to announce). Secretary notes are
appended to a timestamped markdown file so the work-mode agent can read the gathered
requirements back.

Config (env):
    VOICE_STATE_DIR   state dir (default <repo>/.mercury/state)
"""
import os
import json
import tempfile
from pathlib import Path
from datetime import datetime, timezone

MODES = ("idle", "secretary", "work")


def _state_dir():
    override = os.environ.get("VOICE_STATE_DIR")
    if override:
        d = Path(override)
    else:
        # scripts/voice/state.py -> parents[2] == repo root
        d = Path(__file__).resolve().parents[2] / ".mercury" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state_path():
    return _state_dir() / "voice-mode.json"


def _now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write(path, text):
    """Write text to path atomically (write temp + replace) so a concurrent reader
    (the Stop hook) never sees a half-written file."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def get_state():
    """Return the current state dict. Defaults to idle if no state file exists."""
    p = _state_path()
    if not p.exists():
        return {"mode": "idle", "task_context": None, "notes_path": None, "updated_at": None}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"mode": "idle", "task_context": None, "notes_path": None, "updated_at": None}


def get_mode():
    return get_state().get("mode", "idle")


def set_mode(mode, task_context=None):
    """Switch mode (explicit). Entering secretary mode opens a fresh notes file.

    Returns the new state dict. Raises ValueError on an unknown mode.
    """
    if mode not in MODES:
        raise ValueError(f"invalid mode {mode!r} (allowed: {', '.join(MODES)})")
    state = get_state()
    prev_mode = state.get("mode", "idle")
    state["mode"] = mode
    state["updated_at"] = _now_iso()
    if task_context is not None:
        state["task_context"] = task_context
    # Open a fresh notes file when ENTERING secretary from a non-secretary mode (a new
    # intake session). Re-entering secretary from work (secretary->work->secretary,
    # same task) keeps the existing notes file so the task's intake stays in one place.
    if mode == "secretary" and (prev_mode not in ("secretary", "work") or not state.get("notes_path")):
        state["notes_path"] = str(_new_notes_file())
    _atomic_write(_state_path(), json.dumps(state, ensure_ascii=False, indent=2))
    return state


def _new_notes_file():
    """Create a fresh timestamped secretary-notes markdown file and return its path."""
    notes_dir = _state_dir() / "voice-notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    # microsecond stamp so two sessions created within the same second never collide
    # onto one file (second-resolution names would reuse the prior session's notes)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    p = notes_dir / f"secretary-{stamp}.md"
    if not p.exists():
        p.write_text(f"# 秘书模式记录 — {_now_iso()}\n\n", encoding="utf-8")
    return p


def record_note(text, kind="note"):
    """Append a structured, timestamped entry to the current secretary notes file.

    `kind` tags the entry (note / requirement / decision / summary). If no notes file
    is open (not in secretary mode), one is created on demand. Returns the notes path.
    """
    text = (text or "").strip()
    if not text:
        return None
    state = get_state()
    notes_path = state.get("notes_path")
    if not notes_path or not Path(notes_path).exists():
        notes_path = str(_new_notes_file())
        state["notes_path"] = notes_path
        state["updated_at"] = _now_iso()
        _atomic_write(_state_path(), json.dumps(state, ensure_ascii=False, indent=2))
    stamp = datetime.now().strftime("%H:%M:%S")
    tag = {"requirement": "需求", "decision": "决策", "summary": "总结"}.get(kind, "记录")
    with open(notes_path, "a", encoding="utf-8") as f:
        f.write(f"- **[{stamp}] {tag}**：{text}\n")
    return notes_path


def reset():
    """Reset to idle (keeps notes files on disk; just clears the active session)."""
    _atomic_write(_state_path(), json.dumps(
        {"mode": "idle", "task_context": None, "notes_path": None, "updated_at": _now_iso()},
        ensure_ascii=False, indent=2))
