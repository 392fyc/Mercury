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
interface (ctypes). A new speak() stops any in-flight playback first, so rapid
successive agent turns never overlap audio.

Config (env):
    VOICE_TTS_BASE_URL   Kokoro-FastAPI base   (default http://127.0.0.1:8880/v1)
    VOICE_TTS_VOICE      Mandarin voice        (default zf_xiaobei)
    VOICE_TTS_MODEL      Kokoro model id       (default kokoro)
    VOICE_TTS_SPEED      speech speed          (default 1.0)
    VOICE_TTS_FALLBACK   "edge" enables edge-tts fallback (default "" = off)
    VOICE_TTS_EDGE_VOICE edge-tts voice        (default zh-CN-XiaoxiaoNeural)
    VOICE_TTS_TIMEOUT    Kokoro HTTP timeout s (default 30)
"""
import io
import os
import sys
import json
import wave
import tempfile
import threading
import urllib.request
import urllib.error

_BASE_URL = os.environ.get("VOICE_TTS_BASE_URL", "http://127.0.0.1:8880/v1").rstrip("/")
_VOICE = os.environ.get("VOICE_TTS_VOICE", "zf_xiaobei")
_MODEL = os.environ.get("VOICE_TTS_MODEL", "kokoro")
_SPEED = float(os.environ.get("VOICE_TTS_SPEED", "1.0") or "1.0")
_FALLBACK = os.environ.get("VOICE_TTS_FALLBACK", "").strip().lower()
_EDGE_VOICE = os.environ.get("VOICE_TTS_EDGE_VOICE", "zh-CN-XiaoxiaoNeural")
_TIMEOUT = float(os.environ.get("VOICE_TTS_TIMEOUT", "30") or "30")

# serialize playback + track the in-flight player so a new utterance can stop it
_play_lock = threading.Lock()
_current = {"stream": None, "mci_alias": None}


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
    sd.play(audio, sr)
    sd.wait()
    _current["stream"] = None


def _mci(command):
    """Send a Windows MCI string command via winmm (best-effort). Returns 0 on success."""
    if os.name != "nt":
        return -1
    import ctypes
    return ctypes.windll.winmm.mciSendStringW(command, None, 0, None)


def _play_mp3_file(path):
    """Play an MP3 file via the Windows winmm MCI interface (no extra deps)."""
    if os.name != "nt":
        # non-Windows best-effort: let the OS open it (rarely hit; edge fallback is dev-only)
        import subprocess
        subprocess.Popen(["xdg-open", path])
        return
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

    Stops any in-flight playback first (no overlap on rapid agent turns). Returns a
    dict {ok, backend, error} describing the outcome (never raises on a TTS failure —
    a missing voice service must not crash the agent loop).
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "backend": None, "error": "empty text"}

    def _do():
        with _play_lock:
            _stop_current()
            # primary: Kokoro-FastAPI (local, offline)
            try:
                data = _kokoro_synthesize(text, response_format="wav")
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
