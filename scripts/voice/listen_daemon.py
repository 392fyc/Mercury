# -*- coding: utf-8 -*-
"""listen_daemon — always-on STT daemon that enqueues utterances (Issue #495 Path 2 Slice 3).

Model A: a SINGLE daemon owns the microphone for a session and writes each finalized
utterance to the per-session transcript queue (`voice_queue`). The MCP server's `listen()`
then READS the queue instead of opening its own InputStream — eliminating the two-streams-
on-one-device contention that surfaces as PortAudio -9998 on Windows (the daemon is the sole
capturer; `listen()` never self-opens while the daemon is live). Path 2 is OPT-IN: if no
daemon is started, `listen()` self-opens the mic exactly as in #468.

§8 adversarial merge gates baked in:
  - Enqueue-only DEDICATED path (NOT #465's `ContinuousListener`): no paste, no Enter, no
    GRACE window. #465's `_finalize` types the transcript into the focused window + presses
    Enter; reusing it here would inject keystrokes into whatever app has focus while the
    agent works (§8 INTEGRATION). This daemon only transcribes + enqueues.
  - Half-duplex: while the TTS playback lock (`voice-tts.lock`) is held by a LIVE holder, the
    daemon DROPS capture — otherwise the open mic hears Kokoro's own output through the
    speakers and transcribes it into phantom "user said" queue entries (§8 acoustic echo).
    True barge-in (talking OVER the agent) needs AEC and is out of scope; this ships
    half-duplex turn-taking (mic deaf while the agent speaks).
  - Self-healing liveness: the pidfile is written only AFTER the InputStream starts and is
    removed on exit (finally + atexit + SIGTERM/SIGINT/SIGBREAK handlers; a hard kill / console-
    window close that bypasses atexit is self-healed on the next startup by the dead-pid steal +
    the heartbeat-staleness check, so a leaked pidfile never traps listen() permanently); its mtime is a
    heartbeat refreshed each loop. `daemon_active()` requires BOTH a live pid AND a fresh
    heartbeat, so a crashed daemon (leaked device / recycled pid) does NOT trap `listen()`
    reading a queue that will never fill (§8 -9998 / pid-recycle).

Reuses `stt.SttEngine` (the shared STT core: CUDA setup, transcribe, VAD calibration); the
VAD segmentation loop here is deliberately GRACE-free + enqueue-only.

Config (env):
    VOICE_QUEUE_SESSION         session key (shared with the MCP server's listen())
    VOICE_STATE_DIR             state dir (default <repo>/.mercury/state)
    VOICE_ZH_MODEL / _DEVICE / _DEVICE_INDEX / _VAD_THRESH   STT (shared with #465)
    VOICE_DAEMON_HEARTBEAT_SEC  heartbeat refresh base (default 5); staleness = 2x
    VOICE_DAEMON_SILENCE_SEC    trailing silence to end an utterance (default 0.8)
    VOICE_DAEMON_MIN_SEC        minimum utterance length (default 0.4)
    VOICE_DAEMON_ONSET_BLOCKS   consecutive voiced blocks to start (default 3)

Run: <repo>/.venv-voice/Scripts/python.exe scripts/voice/listen_daemon.py
"""
import os
import sys
import time
import queue
import signal
import atexit
import threading

# import flat siblings (sys.path[0] == voice/); these are light. The HEAVY deps
# (faster_whisper via stt, sounddevice, numpy) are imported lazily inside run()/_capture
# so importing this module for daemon_active()/wait_for_utterance stays cheap for the
# MCP server, which only needs the liveness + queue-read helpers.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state as _state          # noqa: E402  (state-dir resolution, shared)
import tts as _tts              # noqa: E402  (voice-tts.lock primitives for half-duplex)
import voice_queue as _vq       # noqa: E402


def _env_float(name, default):
    try:
        v = float(os.environ.get(name, "") or default)
        return v if v > 0 else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name, default):
    try:
        v = int(os.environ.get(name, "") or default)
        return v if v > 0 else int(default)
    except (TypeError, ValueError):
        return int(default)


def _heartbeat_sec():
    return _env_float("VOICE_DAEMON_HEARTBEAT_SEC", 5.0)


def _stale_threshold():
    # a daemon whose heartbeat mtime is older than 2x the refresh interval is treated as
    # dead regardless of pid liveness (guards against a recycled pid passing _pid_alive).
    return 2.0 * _heartbeat_sec()


# --- liveness + queue-read helpers (LIGHT — safe for the MCP server to import) ----------

def _daemon_pid_path(session=None):
    """Per-session daemon pidfile. Keyed by the SAME resolved session as the queue dir, so
    the daemon and the reader (listen()) always agree on which session they share."""
    sess = _vq._resolve_session(session)
    return _state._state_dir() / f"voice-daemon-{sess}.pid"


def daemon_active(session=None):
    """True iff a live daemon owns this session's mic: pidfile present AND its holder pid
    alive AND its heartbeat (mtime) fresh. BOTH matter — a recycled pid would pass
    _pid_alive, but a stale heartbeat reveals the real daemon is gone, so listen() must
    fall back to self-open rather than block on a queue a dead daemon will never fill (§8).
    """
    p = _daemon_pid_path(session)
    try:
        pid = int((p.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return False
    if not _tts._pid_alive(pid):
        return False
    try:
        return (time.time() - p.stat().st_mtime) <= _stale_threshold()
    except OSError:
        return False


def _tts_playing():
    """True iff the TTS playback lock is held by a LIVE holder (the agent is speaking).
    Reuses tts's lock primitives so the daemon stays half-duplex without its own protocol."""
    lp = _tts._lock_path()
    try:
        holder = int((lp.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return False
    return _tts._pid_alive(holder)


def wait_for_utterance(max_seconds=20.0, session=None, poll_sec=0.1):
    """Poll the queue for an utterance spoken AFTER now, up to max_seconds; '' on timeout.

    Time-anchored (§8): the watermark is captured at entry, so a backlog of utterances said
    BEFORE the agent asked is NOT returned here (that backlog is the Stop-hook drain's job).
    This is what listen() calls in model A instead of opening its own InputStream.
    """
    try:
        poll_sec = max(0.01, float(poll_sec))  # clamp: a non-positive/invalid poll would busy-loop
    except (TypeError, ValueError):              # or raise ValueError out of time.sleep (Copilot)
        poll_sec = 0.1
    watermark = time.time()
    deadline = watermark + max(0.0, float(max_seconds) if isinstance(max_seconds, (int, float)) else 0.0)
    while True:
        text = _vq.pop_latest(watermark_ts=watermark, session=session)
        if text:
            return text
        if time.time() >= deadline:
            return ""
        time.sleep(poll_sec)


# --- the daemon -------------------------------------------------------------------------

class EnqueueDaemon:
    """Continuous mic capture -> energy-VAD segmentation -> transcribe -> enqueue.

    A dedicated, GRACE-free, enqueue-only capture loop (no paste / Enter / focus side
    effects). Half-duplex: capture is dropped while the TTS lock is held by a live holder.
    """

    def __init__(self, session=None):
        self.session = session
        self.engine = None
        self.capture_sr = None
        self.blocksize = None
        self.threshold = None
        # bounded queue: the audio callback can NOT block, so on backpressure (e.g. a slow /
        # hung transcribe) it drops the newest block rather than grow memory unbounded — the
        # post-transcribe _drain_queue discards backlog anyway, so dropping under overflow is
        # behaviourally equivalent + safe (Argus memory-risk finding).
        self.q = queue.Queue(maxsize=_env_int("VOICE_DAEMON_QUEUE_MAX", 200))
        self._dropped = 0
        self.stream = None
        self.worker = None
        self.running = False
        self._sd = None
        self._np = None
        # cached half-duplex mute (refreshed at most ~5x/sec, off the audio callback's hot path)
        self._muted = False
        self._mute_check_at = 0.0
        self.silence_sec = _env_float("VOICE_DAEMON_SILENCE_SEC", 0.8)
        self.min_sec = _env_float("VOICE_DAEMON_MIN_SEC", 0.4)
        self.max_sec = _env_float("VOICE_DAEMON_MAX_SEC", 20.0)
        self.onset_blocks = _env_int("VOICE_DAEMON_ONSET_BLOCKS", 3)

    def _load(self):
        # lazy heavy imports: only the daemon process pays for faster_whisper / sounddevice
        import numpy as np
        import sounddevice as sd
        import stt as _stt
        self._np, self._sd = np, sd
        model = os.environ.get("VOICE_ZH_MODEL", "large-v3")
        device = os.environ.get("VOICE_ZH_DEVICE", "auto")
        idx_raw = os.environ.get("VOICE_ZH_DEVICE_INDEX")
        try:
            device_index = int(idx_raw) if idx_raw not in (None, "") else None
        except (TypeError, ValueError):
            print(f"[voice-daemon] invalid VOICE_ZH_DEVICE_INDEX={idx_raw!r}, using default",
                  file=sys.stderr, flush=True)
            device_index = None
        self.engine = _stt.SttEngine(model=model, device=device, device_index=device_index)
        self.engine.load()
        self.capture_sr = self.engine.capture_samplerate()
        self.blocksize = max(256, int(0.05 * self.capture_sr))
        self.threshold = self.engine._calibrate(
            self.capture_sr, os.environ.get("VOICE_ZH_VAD_THRESH", "auto"))

    def _maybe_muted(self):
        now = time.monotonic()
        if now >= self._mute_check_at:
            self._muted = _tts_playing()
            self._mute_check_at = now + 0.2
        return self._muted

    def _on_audio(self, indata, frames, time_info, status):
        if status:
            print(f"[voice-daemon] audio status: {status}", file=sys.stderr, flush=True)
        if self._maybe_muted():
            return  # half-duplex: drop capture while the agent's TTS is playing (§8 echo)
        block = indata.copy().flatten()
        rms = float(self._np.sqrt(self._np.mean(block ** 2)))
        try:
            self.q.put_nowait((block, rms))
        except queue.Full:
            self._dropped += 1  # backpressure: drop rather than block the audio callback

    def _finalize(self, seg, seg_samples, onset_ts=None):
        """Transcribe one utterance and ENQUEUE it under its capture-ONSET timestamp — no
        paste, no Enter, no grace (§8). The onset ts (when the user STARTED speaking, NOT the
        later transcribe-completion time) is what lets listen()'s watermark exclude speech
        begun before the question — enqueuing under the transcribe time would mis-deliver a
        pre-question utterance as the answer (§8 time-anchor)."""
        if seg_samples < self.min_sec * self.capture_sr:
            return
        _touch_pidfile(self.session)  # refresh heartbeat BEFORE the blocking transcribe so a
        #                               slow transcribe can't stale daemon_active() mid-utterance
        try:
            audio = self._np.concatenate(seg, axis=0).flatten()
            text, _ = self.engine.transcribe(audio, self.capture_sr)
            if text:
                path = _vq.enqueue(text, ts=onset_ts, session=self.session)
                # log a redacted summary (length only), NOT the transcript content — the user's
                # speech may carry credentials / private info that must not leak to stderr logs
                # (Argus privacy finding). The text itself lives only in the queue file.
                print(f"[voice-daemon] enqueued utterance ({len(text)} chars) -> {path}",
                      file=sys.stderr, flush=True)
        except Exception as e:  # never let a bad segment kill the daemon
            print(f"[voice-daemon] segment failed: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        finally:
            self._drain_queue()  # discard audio captured during the (blocking) transcribe

    def _drain_queue(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def _run(self):
        in_speech = False
        seg, seg_samples, silence_run = [], 0, 0.0
        pending, voiced_run = [], 0
        onset_ts = 0.0  # wall-clock capture-onset of the current utterance (the queue ts)
        block_dur = self.blocksize / self.capture_sr
        last_hb = 0.0
        while self.running:
            # heartbeat: refresh the pidfile mtime so daemon_active() sees us as live
            now = time.monotonic()
            if now - last_hb >= _heartbeat_sec():
                _touch_pidfile(self.session)
                if self._dropped:  # surface backpressure drops for field debugging, then reset
                    print(f"[voice-daemon] dropped {self._dropped} block(s) under backpressure",
                          file=sys.stderr, flush=True)
                    self._dropped = 0
                last_hb = now
            try:
                block, rms = self.q.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._maybe_muted():
                # the agent started speaking mid-capture -> drop any partial utterance so a
                # half-spoken-then-muted segment is never enqueued
                in_speech, seg, seg_samples, silence_run = False, [], 0, 0.0
                pending, voiced_run = [], 0
                continue
            voiced = rms > self.threshold
            if not in_speech:
                if voiced:
                    if voiced_run == 0:
                        onset_ts = time.time()  # true speech start = first voiced block (§8 anchor)
                    voiced_run += 1
                    pending.append(block)
                    if voiced_run >= self.onset_blocks:
                        in_speech = True
                        seg, seg_samples = pending, sum(len(b) for b in pending)
                        pending, voiced_run, silence_run = [], 0, 0.0
                else:
                    pending, voiced_run = [], 0
                continue
            seg.append(block)
            seg_samples += len(block)
            if voiced:
                silence_run = 0.0
            else:
                silence_run += block_dur
            # finalize on a trailing-silence gap OR a runaway-length cap — a never-silent
            # input (room noise / a stuck mic) would otherwise grow seg unbounded (memory +
            # an oversized transcribe that could stale the heartbeat)
            if silence_run >= self.silence_sec or seg_samples >= self.max_sec * self.capture_sr:
                self._finalize(seg, seg_samples, onset_ts)
                in_speech, seg, seg_samples, silence_run = False, [], 0, 0.0

    def start(self):
        self._load()
        self.stream = self._sd.InputStream(
            samplerate=self.capture_sr, channels=1, dtype="float32",
            blocksize=self.blocksize, device=self.engine.device_index, callback=self._on_audio)
        self.stream.start()  # acquire the device FIRST
        # pidfile is written ONLY after the device is live (and refuses a live foreign owner),
        # so daemon_active() never reports a daemon that hasn't actually acquired the mic (§8)
        _write_pidfile(self.session)
        self.running = True
        # start the consumer LAST: its heartbeat only REFRESHES an existing pidfile (never
        # re-creates), so the worker cannot pre-create the pidfile before the device is up
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def close(self):
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()  # releases the WASAPI device (verified: close frees it)
            except Exception:
                pass
            finally:
                self.stream = None
        w = getattr(self, "worker", None)
        if w is not None:
            w.join(timeout=2.0)


# --- pidfile lifecycle (module-level so signal/atexit handlers can reach it) -------------

_ACTIVE_SESSION = None
_PIDFILE_CLAIMED = False  # True once THIS process actually wrote its pidfile. NOTE: do NOT use
#                           "_ACTIVE_SESSION is None" as the not-claimed sentinel — None is a
#                           VALID session (env/default), so a daemon started with session=None
#                           would otherwise never remove its pidfile on exit (Codex M).


def _write_pidfile(session=None):
    global _ACTIVE_SESSION, _PIDFILE_CLAIMED
    p = _daemon_pid_path(session)
    try:
        existing = int((p.read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        existing = 0
    if existing and existing != os.getpid() and _tts._pid_alive(existing):
        # refuse ONLY if the holder is genuinely active (pid alive AND heartbeat fresh). A
        # recycled pid with a STALE heartbeat is a dead daemon's leftover and is stealable —
        # matching daemon_active()'s pid-AND-freshness liveness, so a crashed-then-pid-recycled
        # daemon never wrongly blocks a fresh start (Copilot).
        try:
            fresh = (time.time() - p.stat().st_mtime) <= _stale_threshold()
        except OSError:
            fresh = False
        if fresh:
            raise RuntimeError(f"another live voice daemon already owns this session (pid {existing})")
    _state._atomic_write(p, str(os.getpid()))  # reuse the proven atomic write
    _ACTIVE_SESSION = session       # set AFTER the write so a refused (live-foreign) claim, which
    _PIDFILE_CLAIMED = True         # raises above, never marks us as the pidfile owner


def _touch_pidfile(session=None):
    p = _daemon_pid_path(session)
    try:
        os.utime(p, None)  # refresh mtime = heartbeat
    except OSError:
        # pidfile externally removed mid-run: re-establish it so the live daemon (which still
        # owns the mic) stays discoverable — otherwise daemon_active() would read False forever
        # and listen() would self-open the device the daemon still holds -> -9998 (Codex M).
        # SAFE because start() now starts the worker only AFTER _write_pidfile (post device),
        # so this heartbeat can never PRE-create the pidfile before the stream is up.
        try:
            _state._atomic_write(p, str(os.getpid()))
        except OSError:
            pass


def _remove_pidfile():
    if not _PIDFILE_CLAIMED:
        return  # this process never wrote a pidfile -> nothing to remove
    p = _daemon_pid_path(_ACTIVE_SESSION)
    try:
        # only remove OUR pidfile (don't clobber a successor daemon that re-claimed the slot)
        if int((p.read_text(encoding="utf-8").strip() or "0")) == os.getpid():
            p.unlink()
    except (OSError, ValueError):
        pass


def main():
    # session is resolved purely from VOICE_QUEUE_SESSION — the SAME channel the MCP server's
    # listen() uses — so the daemon and the reader can never disagree on which queue they
    # share. To run a daemon for a specific lane, set VOICE_QUEUE_SESSION in its environment.
    daemon = EnqueueDaemon(session=None)
    atexit.register(_remove_pidfile)

    def _sig(_signum, _frame):
        daemon.running = False
    for signame in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, signame, None)
        if sig is not None:
            try:
                signal.signal(sig, _sig)
            except (ValueError, OSError):
                pass  # not in main thread / unsupported on this platform
    try:
        daemon.start()
        print(f"[voice-daemon] listening (session={_vq._resolve_session(None)}); "
              "Ctrl+C to stop", file=sys.stderr, flush=True)
        while daemon.running:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        daemon.close()
        _remove_pidfile()
    return 0


if __name__ == "__main__":
    sys.exit(main())
