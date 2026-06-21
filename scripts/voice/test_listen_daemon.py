# -*- coding: utf-8 -*-
"""Behaviour tests for the #495 Slice-3 enqueue daemon (listen_daemon.py).

Headless — no microphone, no faster_whisper, no sounddevice. listen_daemon imports those
HEAVY deps lazily (inside run()/_load), so the liveness + queue-read + finalize logic is
unit-testable with a stubbed engine. Covers the §8 merge gates: enqueue-only (NO paste /
keyboard side effects), daemon_active() needs a live pid AND a fresh heartbeat, half-duplex
mute against the TTS lock, time-anchored wait_for_utterance, and pidfile self-heal.

Run: <venv>/python.exe scripts/voice/test_listen_daemon.py   (also pytest-compatible)
"""
import os
import sys
import time
import types
import shutil
import tempfile
import threading
import contextlib
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state as _state          # noqa: E402
import tts as _tts              # noqa: E402
import voice_queue as _vq       # noqa: E402
import listen_daemon as ld      # noqa: E402


@contextlib.contextmanager
def _temp_state():
    prev = os.environ.get("VOICE_STATE_DIR")
    prev_sess = os.environ.get("VOICE_QUEUE_SESSION")
    prev_bi = os.environ.get("VOICE_BARGEIN")
    d = tempfile.mkdtemp(prefix="ld-test-")
    os.environ["VOICE_STATE_DIR"] = d
    os.environ.pop("VOICE_QUEUE_SESSION", None)
    os.environ.pop("VOICE_BARGEIN", None)  # hermetic: barge-in OFF unless a test sets it
    try:
        yield d
    finally:
        for key, val in (("VOICE_STATE_DIR", prev), ("VOICE_QUEUE_SESSION", prev_sess),
                         ("VOICE_BARGEIN", prev_bi)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(d, ignore_errors=True)


# --- §8 INTEGRATION: enqueue-only, NO paste / keyboard side effects ---------------------

def test_finalize_enqueues_only():
    with _temp_state():
        s = "fin"
        d = ld.EnqueueDaemon(session=s)
        d._np = np
        d.capture_sr = 16000
        d.min_sec = 0.1
        d.engine = types.SimpleNamespace(transcribe=lambda audio, sr: ("你好", 0.0))
        d._finalize([np.zeros(8000, dtype=np.float32)], 8000)  # 0.5s > min_sec
        assert [it["text"] for it in _vq.peek_all(session=s)] == ["你好"]


def test_finalize_drops_too_short():
    with _temp_state():
        s = "short"
        d = ld.EnqueueDaemon(session=s)
        d._np = np
        d.capture_sr = 16000
        d.min_sec = 0.4
        d.engine = types.SimpleNamespace(transcribe=lambda audio, sr: ("x", 0.0))
        d._finalize([np.zeros(1000, dtype=np.float32)], 1000)  # ~0.06s < min_sec
        assert _vq.is_empty(session=s)  # gated out, transcribe never reached


def test_run_segments_on_silence_and_caps_runaway():
    # drive the real _run VAD loop headless by feeding blocks straight into the queue (no
    # sounddevice). Asserts a trailing-silence gap finalizes, AND a never-silent run hits the
    # max_sec cap instead of growing seg unbounded.
    def _run_over(blocks, max_sec):
        with _temp_state():
            d = ld.EnqueueDaemon(session="run")
            d.capture_sr = 16000
            d.blocksize = 800
            d.threshold = 0.1
            d.silence_sec = 0.15  # 3 blocks @50ms
            d.min_sec = 0.0
            d.max_sec = max_sec
            d.onset_blocks = 2
            d._maybe_muted = lambda: False
            sizes = []
            d._finalize = lambda seg, n, ts=None: sizes.append(n)
            for b in blocks:
                d.q.put((b, float(np.sqrt(np.mean(b ** 2)))))
            d.running = True
            th = threading.Thread(target=d._run, daemon=True)
            th.start()
            for _ in range(60):  # poll up to ~3s for a finalize (condition-based, not a fixed sleep)
                if sizes:
                    break
                time.sleep(0.05)
            d.running = False
            th.join(timeout=1.0)
            return sizes

    voi = np.full(800, 0.5, dtype=np.float32)
    sil = np.zeros(800, dtype=np.float32)
    # silence finalize (max_sec large so the cap never fires first): 2 onset + 2 body voiced +
    # 3 trailing silence (silence_run reaches 0.15s on the 3rd) -> seg = 7 blocks = 5600
    s1 = _run_over([voi] * 4 + [sil] * 3, max_sec=1.0)
    assert s1 and s1[0] == 7 * 800, s1
    # runaway cap (never silent): 8 continuous voiced -> cap fires exactly at max_sec*sr (4800)
    s2 = _run_over([voi] * 8, max_sec=0.3)
    assert s2 and s2[0] == int(0.3 * 16000), s2


def test_finalize_enqueues_under_capture_onset_ts():
    # §8 time-anchor: enqueue under the capture-ONSET time, NOT the transcribe-completion time,
    # else an utterance begun BEFORE listen()'s watermark (but transcribed after) is mis-
    # delivered as the answer. The daemon passes onset_ts; assert the stored ts reflects it.
    with _temp_state():
        s = "onset"
        d = ld.EnqueueDaemon(session=s)
        d._np = np
        d.capture_sr = 16000
        d.min_sec = 0.0
        d.engine = types.SimpleNamespace(transcribe=lambda audio, sr: ("早说的", 0.0))
        old_onset = datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp()  # epoch, as the daemon passes
        d._finalize([np.zeros(8000, dtype=np.float32)], 8000, old_onset)
        assert [it["text"] for it in _vq.peek_all(session=s)] == ["早说的"]
        # stored under the OLD onset -> a 2021 watermark excludes it, a 2019 watermark includes it
        assert _vq.pop_latest(watermark_ts="2021-01-01T00:00:00Z", session=s) is None
        assert _vq.pop_latest(watermark_ts="2019-01-01T00:00:00Z", session=s) == "早说的"


def test_daemon_has_no_paste_side_effects():
    # the dedicated #495 daemon must NEVER paste into the focused window or press keys —
    # that is #465's job, and reusing it here would inject keystrokes into the active app.
    with open(ld.__file__, encoding="utf-8") as f:  # explicit close (Windows handle/lock safety)
        src = f.read()
    for forbidden in ("deliver_text", "keyboard", "press_and_release", "import keyboard"):
        assert forbidden not in src, f"daemon must not reference {forbidden!r}"


# --- §8 self-heal liveness: daemon_active needs live pid AND fresh heartbeat -------------

def test_daemon_active_requires_live_pid_and_fresh_heartbeat():
    with _temp_state():
        s = "active"
        p = ld._daemon_pid_path(s)
        assert ld.daemon_active(s) is False                 # no pidfile
        _state._atomic_write(p, str(os.getpid()))           # our (live) pid, fresh mtime
        assert ld.daemon_active(s) is True
        old = time.time() - (ld._stale_threshold() + 60)    # stale heartbeat
        os.utime(p, (old, old))
        assert ld.daemon_active(s) is False                 # live pid but stale -> dead
        _state._atomic_write(p, "0")                        # pid 0 = guaranteed-dead sentinel
        assert ld.daemon_active(s) is False                 # fresh mtime but dead pid


def test_write_pidfile_steals_dead_then_refuses_live_foreign():
    with _temp_state():
        s = "owner"
        p = ld._daemon_pid_path(s)
        _state._atomic_write(p, "0")            # dead owner -> _write_pidfile may steal
        ld._write_pidfile(s)
        assert int(p.read_text(encoding="utf-8")) == os.getpid()
        # a LIVE foreign owner must block startup (simulate via _pid_alive monkeypatch)
        _state._atomic_write(p, "424242")
        orig = _tts._pid_alive
        _tts._pid_alive = lambda pid: True
        try:
            raised = False
            try:
                ld._write_pidfile(s)
            except RuntimeError:
                raised = True
            assert raised, "must refuse to start when a live foreign daemon owns the session"
        finally:
            _tts._pid_alive = orig


def test_main_runs_without_nameerror():
    # the CLI entry must not crash on startup — a missing var in the startup print would
    # NameError AFTER start() and silently exit via finally (headless tests that skip main()
    # miss it). start()/close() are stubbed so no real audio device is touched.
    with _temp_state():
        orig_start, orig_close = ld.EnqueueDaemon.start, ld.EnqueueDaemon.close
        ld.EnqueueDaemon.start = lambda self: None  # leaves running=False -> wait loop skipped
        ld.EnqueueDaemon.close = lambda self: None
        try:
            assert ld.main() == 0  # must run through the startup print + finally with no NameError
        finally:
            ld.EnqueueDaemon.start, ld.EnqueueDaemon.close = orig_start, orig_close


def test_write_then_remove_pidfile_with_session_none():
    # Codex M: main() claims with session=None; _remove_pidfile MUST still remove that pidfile
    # on shutdown. The old `_ACTIVE_SESSION is None -> early return` guard wrongly skipped it
    # (None is a valid session), leaving a stale pidfile that could falsely read as a live
    # daemon after pid recycling. Now gated on the _PIDFILE_CLAIMED flag instead.
    with _temp_state():
        ld._write_pidfile(None)
        p = ld._daemon_pid_path(None)
        assert p.exists()
        ld._remove_pidfile()
        assert not p.exists(), "shutdown must remove the pidfile even when session is None"


def test_write_pidfile_steals_stale_heartbeat_even_if_pid_alive():
    # Copil­ot: refuse must match daemon_active()'s liveness — a recycled pid that happens to be
    # alive but whose heartbeat is STALE is a dead daemon's leftover and must be stealable, not
    # wrongly block a fresh start.
    with _temp_state():
        s = "stale-steal"
        p = ld._daemon_pid_path(s)
        _state._atomic_write(p, "424242")
        old = time.time() - (ld._stale_threshold() + 60)
        os.utime(p, (old, old))                 # stale heartbeat
        orig = _tts._pid_alive
        _tts._pid_alive = lambda pid: True       # pretend the recycled pid is alive
        try:
            ld._write_pidfile(s)                 # stale -> stealable despite "alive" pid
            assert int(p.read_text(encoding="utf-8")) == os.getpid()
        finally:
            _tts._pid_alive = orig


def test_wait_for_utterance_clamps_bad_poll_sec():
    # Copilot: a non-positive / invalid poll_sec must not busy-loop or raise out of time.sleep
    with _temp_state():
        for bad in (0, -1, "x", None):
            assert ld.wait_for_utterance(max_seconds=0.15, session="clamp", poll_sec=bad) == ""


def test_touch_pidfile_recreates_when_externally_removed():
    # Codex M: if the pidfile is removed mid-run, the heartbeat re-establishes it so the live
    # daemon (still owning the mic) stays discoverable — daemon_active() must not read False
    # forever (which would send listen() to self-open the device the daemon holds -> -9998).
    with _temp_state():
        s = "reheal"
        ld._write_pidfile(s)
        p = ld._daemon_pid_path(s)
        p.unlink()                       # external removal mid-run
        ld._touch_pidfile(s)             # heartbeat re-establishes it
        assert p.exists()
        assert int(p.read_text(encoding="utf-8")) == os.getpid()


def test_remove_pidfile_only_removes_own():
    with _temp_state():
        s = "rm"
        ld._write_pidfile(s)
        p = ld._daemon_pid_path(s)
        assert p.exists()
        ld._remove_pidfile()
        assert not p.exists()
        # a successor's pidfile (different pid) must NOT be clobbered by our atexit handler
        ld._write_pidfile(s)
        _state._atomic_write(p, "424242")  # successor re-claimed the slot
        ld._remove_pidfile()
        assert p.exists(), "must not remove a successor daemon's pidfile"


# --- §8 half-duplex: drop capture while the TTS lock is held by a live holder ------------

def test_tts_playing_detects_live_holder():
    with _temp_state():
        assert ld._tts_playing() is False
        lp = _tts._lock_path()
        _state._atomic_write(lp, str(os.getpid()))
        assert ld._tts_playing() is True
        _state._atomic_write(lp, "0")  # dead holder
        assert ld._tts_playing() is False


def test_on_audio_drops_under_backpressure():
    # bounded queue (Argus memory-risk): a full queue must make the audio callback DROP the
    # newest block (never raise, never grow), so a slow/hung transcribe can't leak memory.
    with _temp_state():
        d = ld.EnqueueDaemon(session="bp")
        d._np = np
        d._muted = False
        d._mute_check_at = float("inf")  # use the cached (False) mute state, no file IO
        d.q = ld.queue.Queue(maxsize=2)
        blk = np.zeros(800, dtype=np.float32)
        for _ in range(5):
            d._on_audio(blk, 800, None, None)  # 5 puts into a maxsize-2 queue
        assert d.q.qsize() == 2  # capped, not grown
        assert d._dropped == 3   # the 3 overflow blocks were dropped without raising


def test_maybe_muted_caches_then_reflects_lock():
    with _temp_state():
        d = ld.EnqueueDaemon(session="mute")
        d._mute_check_at = 0.0
        assert d._maybe_muted() is False
        lp = _tts._lock_path()
        _state._atomic_write(lp, str(os.getpid()))
        d._mute_check_at = 0.0   # force a re-check past the cache TTL
        assert d._maybe_muted() is True
        lp.unlink()
        d._mute_check_at = 0.0
        assert d._maybe_muted() is False


# --- §8 time-anchored listen(): wait_for_utterance ignores pre-question backlog ----------

def test_wait_for_utterance_timeout_returns_empty():
    with _temp_state():
        t0 = time.time()
        assert ld.wait_for_utterance(max_seconds=0.2, session="t", poll_sec=0.05) == ""
        assert time.time() - t0 >= 0.18  # actually waited ~max_seconds


def test_wait_for_utterance_ignores_pre_watermark_backlog():
    with _temp_state():
        s = "wfu_old"
        _vq.enqueue("old", ts="2020-01-01T00:00:00Z", session=s)  # far before any watermark
        assert ld.wait_for_utterance(max_seconds=0.2, session=s, poll_sec=0.05) == ""
        assert [it["text"] for it in _vq.peek_all(session=s)] == ["old"]  # left for the drain


def test_wait_for_utterance_returns_post_watermark():
    with _temp_state():
        s = "wfu_new"

        def producer():
            time.sleep(0.15)  # enqueue AFTER wait_for_utterance captures its watermark
            _vq.enqueue("delayed", session=s)

        threading.Thread(target=producer, daemon=True).start()
        got = ld.wait_for_utterance(max_seconds=2.0, session=s, poll_sec=0.05)
        assert got == "delayed", got


def test_bargein_off_half_duplex_mutes():
    # default (VOICE_BARGEIN unset): half-duplex — muted while a live holder owns the TTS lock
    with _temp_state():
        d = ld.EnqueueDaemon(session="bi-off")
        assert d._bargein is False
        _state._atomic_write(_tts._lock_path(), str(os.getpid()))  # TTS playing (our live pid)
        d._mute_check_at = 0.0
        assert d._maybe_muted() is True


def test_bargein_on_signals_stop_on_onset():
    # VOICE_BARGEIN on: the daemon does NOT mute during playback and, on user onset, writes a
    # barge-in stop-signal targeting the TTS lock holder (tts.request_stop).
    with _temp_state():
        d = ld.EnqueueDaemon(session="bi-on")
        d._bargein = True
        d.capture_sr, d.blocksize, d.threshold = 16000, 800, 0.1
        d.silence_sec, d.min_sec, d.max_sec, d.onset_blocks = 0.15, 0.0, 1.0, 2
        d._finalize = lambda seg, n, ts=None: None  # don't transcribe in this test
        _state._atomic_write(_tts._lock_path(), str(os.getpid()))  # TTS playing (our live pid)
        assert d._maybe_muted() is False  # barge-in mode listens through playback
        voi = np.full(800, 0.5, dtype=np.float32)
        for b in [voi] * 4:
            d.q.put((b, float(np.sqrt(np.mean(b ** 2)))))
        d.running = True
        th = threading.Thread(target=d._run, daemon=True)
        th.start()
        fired = False
        for _ in range(60):
            if _tts._pending_stop_for_me():
                fired = True
                break
            time.sleep(0.05)
        d.running = False
        th.join(timeout=1.0)
        assert fired, "onset under barge-in must request_stop targeting the TTS lock holder"


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
