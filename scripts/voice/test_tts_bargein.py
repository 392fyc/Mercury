# -*- coding: utf-8 -*-
"""Behaviour tests for the #495 Slice-4 barge-in / interruptible TTS playback (tts.py).

Headless — sounddevice is stubbed (a fake module injected into sys.modules), so the
interruptible-playback loop is exercised with no real audio device. Covers the §8 merge
gates for the barge-in stop-signal: generation-binding (target pid), clear-on-lock-acquire,
and interruptible `_play_wav_bytes` (normal playback completes; a signal targeting THIS pid
stops it early and is consumed; a signal targeting a different pid is ignored).

Run: <venv>/python.exe scripts/voice/test_tts_bargein.py   (also pytest-compatible)
"""
import io
import os
import sys
import time
import wave
import types
import shutil
import tempfile
import threading
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tts as _tts  # noqa: E402


@contextlib.contextmanager
def _temp_state():
    prev = os.environ.get("VOICE_STATE_DIR")
    d = tempfile.mkdtemp(prefix="tts-bargein-")
    os.environ["VOICE_STATE_DIR"] = d
    try:
        yield d
    finally:
        if prev is None:
            os.environ.pop("VOICE_STATE_DIR", None)
        else:
            os.environ["VOICE_STATE_DIR"] = prev
        shutil.rmtree(d, ignore_errors=True)


@contextlib.contextmanager
def _fake_sd(sim_dur=0.2):
    """Inject a fake `sounddevice` that simulates a real, interruptible playback: wait()
    blocks for sim_dur OR until stop() (mirroring how a real sd.stop() makes sd.wait()
    return), so the background-drain + poll loop is exercised — including a late signal that
    arrives mid-playback, not only one pre-written before the call."""
    prev = sys.modules.get("sounddevice")
    fake = types.ModuleType("sounddevice")
    stop_evt = threading.Event()
    state = {"played": False, "stopped": False, "waited": False}

    def _play(audio, sr):
        state["played"] = True
        stop_evt.clear()

    def _stop():
        state["stopped"] = True
        stop_evt.set()  # a real sd.stop() makes the in-flight sd.wait() return

    def _wait():
        state["waited"] = True
        stop_evt.wait(sim_dur)  # blocks until stop() or the simulated clip duration elapses

    fake.play, fake.stop, fake.wait = _play, _stop, _wait
    sys.modules["sounddevice"] = fake
    try:
        yield state
    finally:
        stop_evt.set()  # release any lingering wait before restoring
        if prev is None:
            sys.modules.pop("sounddevice", None)
        else:
            sys.modules["sounddevice"] = prev


def _wav_bytes(dur=0.02, sr=8000):
    n = max(1, int(dur * sr))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * n)
    return buf.getvalue()


# --- stop-signal generation-binding ----------------------------------------------------

def test_request_stop_targets_this_pid():
    with _temp_state():
        _tts.request_stop(os.getpid())
        assert _tts._pending_stop_for_me() is True
        assert _tts._stop_signal_path().exists()


def test_stop_signal_for_other_pid_ignored():
    with _temp_state():
        _tts.request_stop(os.getpid() + 1)  # target a DIFFERENT pid
        assert _tts._pending_stop_for_me() is False  # not for us -> ignored (gen-binding §8)


def test_request_stop_rejects_bad_target():
    with _temp_state():
        for bad in (0, -1, None, "x"):
            _tts.request_stop(bad)
            assert not _tts._stop_signal_path().exists(), bad  # no signal written for a bad target


def test_pending_stop_handles_corrupt_signal():
    with _temp_state():
        _tts._stop_signal_path().write_text("{not json", encoding="utf-8")
        assert _tts._pending_stop_for_me() is False  # corrupt -> no valid signal, never raises


# --- clear-on-acquire (a stale signal can't truncate the NEXT playback, §8) -------------

def test_lock_acquire_clears_stale_signal():
    with _temp_state():
        _tts.request_stop(424242)  # a stale signal from some prior playback
        assert _tts._stop_signal_path().exists()
        with _tts._CrossProcLock(1) as lock:
            assert lock.acquired
            assert not _tts._stop_signal_path().exists()  # cleared at the start of THIS playback


# --- interruptible _play_wav_bytes ------------------------------------------------------

def test_play_wav_normal_completes():
    with _temp_state(), _fake_sd(sim_dur=0.1) as sd:
        _tts._play_wav_bytes(_wav_bytes(dur=0.02))
        assert sd["played"] is True
        assert sd["waited"] is True    # ran to completion -> drained
        assert sd["stopped"] is False  # no barge-in


def test_play_wav_bargein_stops_early():
    with _temp_state(), _fake_sd(sim_dur=2.0) as sd:
        _tts.request_stop(os.getpid())            # barge-in targeting THIS playback (pre-written)
        _tts._play_wav_bytes(_wav_bytes(dur=2.0))  # a long clip; must stop EARLY on the signal
        assert sd["stopped"] is True
        assert not _tts._stop_signal_path().exists()  # signal consumed


def test_play_wav_late_signal_during_playback():
    # the signal arrives AFTER playback starts (not pre-written) — the poll loop must honour it
    # mid-playback, covering the whole real duration with no uninterruptible tail (Codex M/Low).
    with _temp_state(), _fake_sd(sim_dur=2.0) as sd:
        def _late():
            time.sleep(0.1)
            _tts.request_stop(os.getpid())
        threading.Thread(target=_late, daemon=True).start()
        _tts._play_wav_bytes(_wav_bytes(dur=2.0))
        assert sd["stopped"] is True               # interrupted by the late signal
        assert not _tts._stop_signal_path().exists()


def test_play_wav_ignores_foreign_signal():
    with _temp_state(), _fake_sd(sim_dur=0.1) as sd:
        _tts.request_stop(os.getpid() + 1)         # a signal for a DIFFERENT playback
        _tts._play_wav_bytes(_wav_bytes(dur=0.02))  # must NOT be truncated by it
        assert sd["stopped"] is False
        assert sd["waited"] is True


def _main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
    sys.exit(0 if passed == len(tests) else 1)


if __name__ == "__main__":
    _main()
