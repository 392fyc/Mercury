# -*- coding: utf-8 -*-
"""tts — Chinese text-to-speech for agent->user announcements (Issue #468).

Primary backend: Kokoro-FastAPI, an OpenAI-compatible local TTS service
(`POST /v1/audio/speech`, verified at github.com/remsky/Kokoro-FastAPI). Local +
offline + Apache-2.0; ~300ms first-token on an RTX 4060. Run it as a standalone
service (its own Python env, which sidesteps Kokoro's `Requires-Python <3.13` pin —
this daemon stays on 3.14). See scripts/voice/README.md for setup.

Fallback backend: edge-tts (Microsoft Edge online TTS, LGPLv3). Zero-config but
ONLINE — disabled by default; opt in with VOICE_TTS_FALLBACK=edge. Use only for
dev / when the local Kokoro service is unavailable.

Playback (no heavy deps): WAV/PCM via sounddevice; MP3 via the Windows winmm MCI
interface (ctypes). Overlap is prevented BOTH in-process (a threading lock) AND
across processes (a file lock in the state dir): announce() runs in the long-lived
MCP-server process while the Stop hook's stop_notify.py runs in a separate per-turn
process, so an in-process lock alone would let the two speak over each other.

Config (env):
    VOICE_TTS_BASE_URL   Kokoro-FastAPI base   (default http://127.0.0.1:8880/v1)
    VOICE_TTS_VOICE      Mandarin voice        (default zf_xiaobei)
    VOICE_TTS_MODEL      Kokoro model id       (default kokoro)
    VOICE_TTS_SPEED      speech speed          (default 1.0)
    VOICE_TTS_FALLBACK   "edge" enables edge-tts fallback (default "" = off)
    VOICE_TTS_EDGE_VOICE edge-tts voice        (default zh-CN-XiaoxiaoNeural)
    VOICE_TTS_TIMEOUT    Kokoro HTTP timeout s (default 30)
    VOICE_TTS_LOCK_WAIT  cross-proc lock wait s (default 8; 0 = skip if busy)
    VOICE_STATE_DIR      dir holding the cross-process playback lock (default <repo>/.mercury/state)
"""
import io
import os
import sys
import json
import time
import wave
import tempfile
import threading
import urllib.request
import urllib.error
from pathlib import Path

_BASE_URL = os.environ.get("VOICE_TTS_BASE_URL", "http://127.0.0.1:8880/v1").rstrip("/")
_VOICE = os.environ.get("VOICE_TTS_VOICE", "zf_xiaobei")
_MODEL = os.environ.get("VOICE_TTS_MODEL", "kokoro")
_SPEED = float(os.environ.get("VOICE_TTS_SPEED", "1.0") or "1.0")
_FALLBACK = os.environ.get("VOICE_TTS_FALLBACK", "").strip().lower()
_EDGE_VOICE = os.environ.get("VOICE_TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural")
_TIMEOUT = float(os.environ.get("VOICE_TTS_TIMEOUT", "30") or "30")
_LOCK_WAIT = float(os.environ.get("VOICE_TTS_LOCK_WAIT", "8") or "8")
_LOCK_STALE = 90.0  # a lockfile older than this is considered abandoned and stolen

# serialize playback + track the in-flight player so a new utterance can stop it
_play_lock = threading.Lock()  # in-process
_current = {"stream": None, "mci_alias": None}


def _lock_path():
    override = os.environ.get("VOICE_STATE_DIR")
    d = Path(override) if override else Path(__file__).resolve().parents[2] / ".mercury" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "voice-tts.lock"


def _pid_alive(pid):
    """True if process `pid` is still running. Used to avoid stealing a lock from a
    LIVE holder that is merely mid-long-playback (a stale mtime alone must not trigger
    a steal while the holder is alive — that would cause the overlap we're preventing).
    Returns False on any uncertainty so a genuinely-dead holder's lock can still be stolen."""
    if not pid or pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False  # no such process (or access denied → treat as dead/steal-able)
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return bool(ok) and code.value == STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except (OSError, ValueError, OverflowError):
        return False


class _CrossProcLock:
    """Cross-process exclusive lock for audio playback (a lockfile created O_EXCL).

    announce() (MCP-server process) and stop_notify.py (per-turn Stop-hook process)
    are separate processes, so the in-process _play_lock cannot keep them from
    speaking simultaneously — this file lock does. Waits up to `wait` seconds for the
    holder to release; on timeout `acquired` is False and the caller skips playback
    (sequential-not-simultaneous: no garbled overlap). A lockfile older than
    _LOCK_STALE is treated as abandoned (crashed holder) and stolen.
    """

    def __init__(self, wait):
        self.wait = wait
        self.fd = None
        self.path = _lock_path()

    @property
    def acquired(self):
        return self.fd is not None

    def touch(self):
        """Refresh the lockfile mtime — call right before playback so the stale clock
        measures playback time (not synth/download), preventing a long synth from
        pushing total hold time past _LOCK_STALE and triggering a false steal."""
        if self.fd is not None:
            try:
                os.utime(self.path, None)
            except OSError:
                pass

    def __enter__(self):
        deadline = time.time() + self.wait
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(self.path) > _LOCK_STALE:
                        # only steal if the holder is actually dead — a live holder
                        # mid-long-playback keeps its lock no matter how old the mtime,
                        # so a slow synth/long utterance never triggers a false steal
                        try:
                            with open(self.path, encoding="utf-8") as lf:  # close before remove (Windows)
                                holder = int(lf.read().strip() or "0")
                        except (OSError, ValueError):
                            holder = 0
                        if not _pid_alive(holder):
                            os.remove(self.path)  # steal an abandoned (dead-holder) lock
                            continue
                except OSError:
                    pass  # lock vanished between checks — retry the open
                if time.time() >= deadline:
                    return self  # acquired == False
                time.sleep(0.1)

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            try:
                os.remove(self.path)
            except OSError:
                pass
            self.fd = None


# ----------------------------------------------------------------------------- synth
def _kokoro_synthesize(text, response_format="wav"):
    """POST to Kokoro-FastAPI's OpenAI-compatible speech endpoint. Returns audio bytes.

    Raises urllib.error.URLError if the service is unreachable (caller falls back).
    """
    payload = json.dumps({
        "model": _MODEL,
        "input": text,
        "voice": _VOICE,
        "response_format": response_format,
        "speed": _SPEED,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{_BASE_URL}/audio/speech",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.read()


def _edge_synthesize(text):
    """Synthesize via edge-tts (online). Returns MP3 bytes. Requires `edge-tts` installed."""
    import asyncio
    import edge_tts

    async def _run():
        communicate = edge_tts.Communicate(text, _EDGE_VOICE)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    return asyncio.run(_run())


# --------------------------------------------------------------------------- playback
def _stop_current():
    """Stop any in-flight playback (sounddevice stream or MCI mp3)."""
    st = _current.get("stream")
    if st is not None:
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        _current["stream"] = None
    alias = _current.get("mci_alias")
    if alias is not None:
        _mci(f"stop {alias}")
        _mci(f"close {alias}")
        _current["mci_alias"] = None


def _play_wav_bytes(data):
    """Play WAV bytes via sounddevice (blocking)."""
    import numpy as np
    import sounddevice as sd
    with wave.open(io.BytesIO(data), "rb") as wf:
        sr = wf.getframerate()
        n = wf.getnframes()
        ch = wf.getnchannels()
        raw = wf.readframes(n)
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        audio = audio.reshape(-1, ch)
    _current["stream"] = True
    try:
        sd.play(audio, sr)
        sd.wait()
    finally:
        # reset even if play/wait raises, so a later _stop_current() won't sd.stop() spuriously
        _current["stream"] = None


def _mci(command):
    """Send a Windows MCI string command via winmm (best-effort). Returns 0 on success."""
    if os.name != "nt":
        return -1
    import ctypes
    return ctypes.windll.winmm.mciSendStringW(command, None, 0, None)


def _play_mp3_file(path):
    """Play an MP3 file via the Windows winmm MCI interface (no extra deps), BLOCKING
    until playback finishes (so the cross-process lock is held for the real duration)."""
    if os.name != "nt":
        # Mercury target is native Windows. A detached `xdg-open` would return immediately
        # and release the playback lock while audio is still playing (breaking the
        # no-overlap guarantee), and macOS has no xdg-open anyway. Fail loudly instead of
        # a misleading "spoken" result — speak() catches this and reports ok=False.
        raise NotImplementedError("mp3 fallback playback is unsupported on non-Windows")
    import time
    alias = "voicetts"
    _mci(f'open "{path}" type mpegvideo alias {alias}')
    _current["mci_alias"] = alias
    # length in ms, then play and wait
    import ctypes
    buf = ctypes.create_unicode_buffer(64)
    ctypes.windll.winmm.mciSendStringW(f"status {alias} length", buf, 64, None)
    try:
        length_ms = int(buf.value)
    except ValueError:
        length_ms = 0
    _mci(f"play {alias}")
    if length_ms > 0:
        time.sleep(length_ms / 1000.0 + 0.2)
    _mci(f"close {alias}")
    _current["mci_alias"] = None


# ------------------------------------------------------------------------------- API
def speak(text, blocking=True):
    """Speak `text` in Chinese. Tries Kokoro-FastAPI, then edge-tts (if enabled).

    No-overlap guarantee: serialized by an in-process lock (`_play_lock`) AND a
    cross-process file lock (`_CrossProcLock`), because announce() and the Stop hook's
    stop_notify.py run in different processes. If another process is already speaking
    and the lock can't be acquired within VOICE_TTS_LOCK_WAIT seconds, this call skips
    playback (returns ok=False, error="busy") rather than overlap. Returns a dict
    {ok, backend, error}; never raises on a TTS failure — a missing voice service must
    not crash the agent loop.
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "backend": None, "error": "empty text"}
    # Bound spoken length at the speak() layer so EVERY caller (announce, stop_notify, …)
    # produces playback short enough to stay well under the cross-process lock's stale
    # window — long detail belongs on screen, not read aloud.
    cap = int(os.environ.get("VOICE_TTS_MAX_CHARS", "600") or "600")
    if len(text) > cap:
        text = text[:cap] + "……（详情见屏幕）"

    def _do():
        with _play_lock:                       # in-process exclusion
            with _CrossProcLock(_LOCK_WAIT) as cpl:  # cross-process exclusion
                if not cpl.acquired:
                    return {"ok": False, "backend": None,
                            "error": "busy (another voice process is speaking)"}
                _stop_current()
                # primary: Kokoro-FastAPI (local, offline)
                try:
                    data = _kokoro_synthesize(text, response_format="wav")
                    cpl.touch()  # reset stale clock to playback start (synth time already spent)
                    _play_wav_bytes(data)
                    return {"ok": True, "backend": "kokoro", "error": None}
                except Exception as e:
                    kokoro_err = f"{type(e).__name__}: {e}"
                    print(f"[voice-tts] kokoro unavailable: {kokoro_err}", file=sys.stderr, flush=True)
                # fallback: edge-tts (online), only if opted in
                if _FALLBACK == "edge":
                    try:
                        mp3 = _edge_synthesize(text)
                        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="voicetts_")
                        os.close(fd)
                        with open(path, "wb") as f:
                            f.write(mp3)
                        cpl.touch()  # reset stale clock to playback start
                        try:
                            _play_mp3_file(path)
                        finally:
                            try:
                                os.remove(path)
                            except OSError:
                                pass
                        return {"ok": True, "backend": "edge", "error": None}
                    except Exception as e:
                        return {"ok": False, "backend": "edge", "error": f"{type(e).__name__}: {e}"}
                return {"ok": False, "backend": "kokoro", "error": kokoro_err}

    if blocking:
        return _do()
    threading.Thread(target=_do, daemon=True).start()
    return {"ok": True, "backend": "async", "error": None}


def health():
    """Best-effort check that the Kokoro-FastAPI service is reachable. Returns bool."""
    try:
        req = urllib.request.Request(f"{_BASE_URL}/audio/voices", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False
