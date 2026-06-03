# -*- coding: utf-8 -*-
"""voice-zh-input — 外围中文语音输入 (Mercury #465)

A standalone, detachable daemon that adds Chinese (zh) voice input to Claude Code's
TUI (or any focused text field), working around the native `/voice` feature which is
server-hardcoded to 20 languages with no Chinese.

Two modes (VOICE_ZH_MODE):
  continuous (default) — always-on, energy-VAD-segmented dictation. Just speak; each
      utterance (speech up to a trailing silence gap) is transcribed and delivered,
      then Enter is auto-pressed after a short confirm window. Pause/resume hotkey.
  toggle — push-key: press the hotkey to start recording, press again to stop.

Pipeline (architecture B, all steps empirically verified in #465):
  mic capture (sounddevice, device-native rate, in-memory)
  -> resample to 16kHz + peak-normalize
  -> faster-whisper large-v3 (language="zh", GPU float16 / CPU int8 fallback)
  -> pyperclip clipboard write -> simulated Ctrl+V (bracketed paste) [-> Enter]

Why clipboard + Ctrl+V and not per-character keystrokes: Windows Terminal/ConHost
mangles simulated Unicode keystrokes (microsoft/terminal#12977), while the TUI
accepts pasted Chinese via bracketed paste. The clipboard carries the Unicode;
Ctrl+V is plain ASCII-level virtual keys (no Unicode bug). Capture is at the device
native rate because forcing 16kHz on the Realtek driver returns digital silence.

Local + private: no audio leaves the machine.

Usage:
    python scripts/voice-zh-input.py        # continuous mode: just speak
    # pause/resume: Ctrl+Alt+P   quit: Ctrl+Alt+Q

Config via env vars:
    VOICE_ZH_MODE        "continuous" | "toggle"     (default "continuous")
    VOICE_ZH_PAUSE       continuous pause/resume     (default "ctrl+alt+p")
    VOICE_ZH_HOTKEY      toggle-mode record key      (default "ctrl+alt+space")
    VOICE_ZH_QUIT        quit combo                  (default "ctrl+alt+q")
    VOICE_ZH_AUTO_ENTER  "1" press Enter after utt.  (default "1")
    VOICE_ZH_GRACE_SEC   confirm window before Enter (default "2.0")
    VOICE_ZH_SILENCE_SEC trailing silence ends utt.  (default "0.8")
    VOICE_ZH_MIN_SEC     ignore shorter blips        (default "0.4")
    VOICE_ZH_VAD_THRESH  "auto" | float energy thr.  (default "auto")
    VOICE_ZH_ONSET_BLOCKS consec. voiced blocks/onset (default "3")
    VOICE_ZH_NOSPEECH_MAX drop seg if no_speech_prob>= (default "0.6")
    VOICE_ZH_LOGPROB_MIN drop seg if avg_logprob <=   (default "-1.0")
    VOICE_ZH_CONT_BEEP   "1" continuous onset/end beep (default "0")
    VOICE_ZH_MODEL       faster-whisper model        (default "large-v3")
    VOICE_ZH_DEVICE      "auto" | "cuda" | "cpu"     (default "auto")
    VOICE_ZH_DEVICE_INDEX explicit input device idx  (default: system default)
    VOICE_ZH_NORMALIZE   "1" peak-normalize capture  (default "1")
    VOICE_ZH_VAD         "1" Silero VAD in whisper   (default "0")
    VOICE_ZH_PASTE       "1" auto Ctrl+V else copy   (default "1")
    VOICE_ZH_MAX_SEC     in-memory recording cap     (default "120")
    VOICE_ZH_BEEP        "1" toggle-mode beeps       (default "1")

Dependencies: see requirements-voice-zh.txt. GPU additionally needs the bundled
nvidia-* cu12 wheels (cublas / cudnn 9.x / cuda-runtime / cuda-nvrtc); this script
registers their DLL dirs automatically. add_dll_directory alone is NOT enough on
Windows — ctranslate2's internal loader resolves the CUDA DLLs via PATH, so the
bin dirs are prepended to PATH as well.
"""
import os
import sys
import time
import queue
import threading

sys.stdout.reconfigure(encoding="utf-8")


def _env_num(name, default, cast):
    """Parse a numeric env var; on an invalid value warn and fall back to default,
    so a typo in config never crashes the daemon at module load."""
    v = os.environ.get(name)
    if v in (None, ""):
        return default
    try:
        return cast(v)
    except (ValueError, TypeError):
        print(f"[voice-zh] WARNING: invalid {name}={v!r}, using default {default}", flush=True)
        return default


MODE = os.environ.get("VOICE_ZH_MODE", "continuous")  # "continuous" (VAD) | "toggle" (push-key)
HOTKEY = os.environ.get("VOICE_ZH_HOTKEY", "ctrl+alt+space")  # toggle-mode record key
QUIT = os.environ.get("VOICE_ZH_QUIT", "ctrl+alt+q")
PAUSE_HOTKEY = os.environ.get("VOICE_ZH_PAUSE", "ctrl+alt+p")  # continuous-mode pause/resume
MODEL = os.environ.get("VOICE_ZH_MODEL", "large-v3")
DEVICE = os.environ.get("VOICE_ZH_DEVICE", "auto")
BEEP = os.environ.get("VOICE_ZH_BEEP", "1") == "1"
PASTE = os.environ.get("VOICE_ZH_PASTE", "1") == "1"  # 0 = copy-only (no auto Ctrl+V)
VAD = os.environ.get("VOICE_ZH_VAD", "0") == "1"  # default off: VAD over-trims quiet mics -> empty
NORMALIZE = os.environ.get("VOICE_ZH_NORMALIZE", "1") == "1"  # peak-normalize low-gain capture
DEVICE_INDEX = _env_num("VOICE_ZH_DEVICE_INDEX", None, int)  # optional explicit input device index
SAMPLERATE = 16000  # whisper target rate; capture happens at the device's NATIVE rate
MAX_SEC = _env_num("VOICE_ZH_MAX_SEC", 120, int)  # cap in-memory recording length

# continuous-mode (VAD) tuning
AUTO_ENTER = os.environ.get("VOICE_ZH_AUTO_ENTER", "1") == "1"  # press Enter after grace
GRACE_SEC = _env_num("VOICE_ZH_GRACE_SEC", 2.0, float)  # confirm window before Enter
SILENCE_SEC = _env_num("VOICE_ZH_SILENCE_SEC", 0.8, float)  # trailing silence ends utterance
MIN_UTT_SEC = _env_num("VOICE_ZH_MIN_SEC", 0.4, float)  # ignore shorter blips
_VAD_THRESH = os.environ.get("VOICE_ZH_VAD_THRESH", "auto")  # "auto" calibrates ambient noise
ONSET_BLOCKS = _env_num("VOICE_ZH_ONSET_BLOCKS", 3, int)  # consec. voiced blocks to start
CONT_BEEP = os.environ.get("VOICE_ZH_CONT_BEEP", "0") == "1"  # continuous-mode onset/end beeps (off)

# hallucination / non-speech gating (whisper confidence-based)
NO_SPEECH_MAX = _env_num("VOICE_ZH_NOSPEECH_MAX", 0.6, float)  # drop seg if no_speech_prob >=
LOGPROB_MIN = _env_num("VOICE_ZH_LOGPROB_MIN", -1.0, float)  # drop seg if avg_logprob <=
# HALLUCINATION_MARKERS now live in scripts/voice/stt.py (shared with the #468 MCP server)


# Shared STT primitives live in scripts/voice/stt.py (#468). Importing it runs the
# Windows CUDA/cuDNN DLL-dir registration (must happen before faster_whisper loads),
# so this single import replaces the daemon's former inline _register_cuda_dll_dirs().
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from voice.stt import resample_to_16k, is_hallucination  # noqa: E402

import numpy as np
import sounddevice as sd
import pyperclip
import keyboard
from faster_whisper import WhisperModel

try:
    import winsound  # Windows-only; beeps are best-effort

    def _beep(freq, dur):
        if BEEP:
            try:
                winsound.Beep(freq, dur)
            except RuntimeError:
                pass
except ImportError:  # non-Windows: no beep
    def _beep(freq, dur):
        pass


def _capture_samplerate():
    """Native samplerate of the chosen input device.

    Forcing 16kHz directly on the Realtek capture driver returns digital silence
    (peak 0.0000) instead of erroring, so we always capture at the device's native
    rate and resample to 16kHz afterwards.
    """
    try:
        d = (
            sd.query_devices(DEVICE_INDEX, kind="input")
            if DEVICE_INDEX is not None
            else sd.query_devices(kind="input")
        )
        return int(d["default_samplerate"])
    except Exception:
        return SAMPLERATE


# resample_to_16k / is_hallucination are imported from voice.stt (shared with #468).


def transcribe_segment(model, audio_native, capture_sr):
    """Resample + (optional) peak-normalize native-rate audio, then transcribe zh.

    Non-speech / hallucination segments are dropped via Whisper's per-segment
    no_speech_prob + avg_logprob (the mechanical gate that kills "字幕志愿者"-style
    hallucinations on silence), backed by a signature blocklist. Returns
    (text, peak); peak is measured on the raw native capture so callers can warn on
    near-silent input.
    """
    peak = float(np.max(np.abs(audio_native))) if len(audio_native) else 0.0
    audio = resample_to_16k(audio_native, capture_sr)
    if NORMALIZE and peak > 1e-4:
        audio = (audio / peak * 0.3).astype(np.float32)  # rescue low-gain mics
    segments, _ = model.transcribe(
        audio,
        language="zh",
        beam_size=5,
        vad_filter=VAD,
        condition_on_previous_text=False,  # don't carry hallucinations across calls
        no_speech_threshold=NO_SPEECH_MAX,
        log_prob_threshold=LOGPROB_MIN,
    )
    kept = []
    for s in segments:
        # fail-safe defaults: if a faster-whisper build omits these fields, drop the
        # segment rather than admit a potential hallucination
        if getattr(s, "no_speech_prob", 1.0) >= NO_SPEECH_MAX:
            continue
        if getattr(s, "avg_logprob", -10.0) <= LOGPROB_MIN:
            continue
        kept.append(s.text)
    text = "".join(kept).strip()
    if is_hallucination(text):
        return "", peak
    return text, peak


def deliver_text(text):
    """Deliver transcribed text to the focused field.

    VOICE_ZH_PASTE=0 -> copy-only: leave text on the clipboard for the user to paste
    manually. This is the safe mode, because auto-paste targets whatever window
    happens to be focused when transcription finishes (which can be seconds later) —
    keep the target focused, or use copy-only to avoid pasting into an unintended
    window.

    VOICE_ZH_PASTE=1 (default) -> copy + Ctrl+V, then best-effort restore of the
    prior clipboard *text* (non-text clipboard formats are not preserved). Restore
    runs in a finally so a paste failure can't leave the clipboard overwritten.
    """
    if not PASTE:
        pyperclip.copy(text)
        print("[voice-zh] copied to clipboard (paste manually; VOICE_ZH_PASTE=0)", flush=True)
        return
    try:
        saved = pyperclip.paste()
    except Exception:
        saved = None
    try:
        pyperclip.copy(text)
        time.sleep(0.05)
        keyboard.press_and_release("ctrl+v")
        time.sleep(0.2)  # let the paste land before restoring
    finally:
        if saved is not None:
            try:
                pyperclip.copy(saved)
            except Exception:
                pass


def _load_model():
    """Load WhisperModel, honoring VOICE_ZH_DEVICE. 'auto' tries CUDA then CPU.

    The CUDA cuBLAS failure only surfaces at inference time (not at construction),
    so 'auto' validates the device with a tiny warmup transcription before
    committing to it.
    """
    device_map = {
        "cuda": [("cuda", "float16")],
        "cpu": [("cpu", "int8")],
        "auto": [("cuda", "float16"), ("cpu", "int8")],
    }
    if DEVICE not in device_map:
        print(
            f"[voice-zh] FATAL: invalid VOICE_ZH_DEVICE={DEVICE!r} "
            f"(allowed: {', '.join(device_map)})",
            flush=True,
        )
        sys.exit(2)
    candidates = device_map[DEVICE]
    warmup = np.zeros(SAMPLERATE, dtype=np.float32)  # 1s of silence
    for dev, comp in candidates:
        try:
            print(f"[voice-zh] loading {MODEL} on {dev}/{comp} ...", flush=True)
            t0 = time.time()
            model = WhisperModel(MODEL, device=dev, compute_type=comp)
            # force a real inference pass to validate the device end-to-end
            list(model.transcribe(warmup, language="zh")[0])
            print(f"[voice-zh] ready on {dev}/{comp} ({time.time() - t0:.1f}s)", flush=True)
            return model
        except Exception as e:
            print(f"[voice-zh] {dev} unavailable: {type(e).__name__}: {e}", flush=True)
    print("[voice-zh] FATAL: no usable device (cuda/cpu)", flush=True)
    sys.exit(2)


class Recorder:
    """Toggle-driven mic recorder. State machine across hotkey presses."""

    def __init__(self, model):
        self.model = model
        self.stream = None
        self.frames = []
        self.recording = False
        self._samples = 0
        self.capture_sr = _capture_samplerate()  # device native rate; resampled later
        self._max_samples = MAX_SEC * self.capture_sr
        self._capped = False
        self._shutdown = threading.Event()  # set by close() to abort in-flight deliver
        self._deliver_lock = threading.Lock()  # serialize deliver vs shutdown atomically
        dev = DEVICE_INDEX if DEVICE_INDEX is not None else "default"
        print(f"[voice-zh] input device = {dev} @ {self.capture_sr}Hz -> 16kHz", flush=True)

    def _on_audio(self, indata, frames, time_info, status):
        if status:
            print(f"[voice-zh] audio status: {status}", flush=True)
        if self._samples >= self._max_samples:
            if not self._capped:  # warn once, then drop overflow to bound RAM
                self._capped = True
                print(
                    f"[voice-zh] recording hit {MAX_SEC}s cap — extra audio dropped; "
                    "press hotkey to stop",
                    flush=True,
                )
            return
        self.frames.append(indata.copy())
        self._samples += len(indata)

    def toggle(self):
        if not self.recording:
            self._start()
        else:
            self._stop_and_inject()

    def _start(self):
        self.frames = []
        self._samples = 0
        self._capped = False
        self.stream = sd.InputStream(
            samplerate=self.capture_sr,
            channels=1,
            dtype="float32",
            device=DEVICE_INDEX,
            callback=self._on_audio,
        )
        self.stream.start()
        self.recording = True
        _beep(880, 120)
        print("[voice-zh] ● recording... (press hotkey again to stop)", flush=True)

    def _stop_and_inject(self):
        self.recording = False
        try:
            self.stream.stop()
            self.stream.close()
        finally:
            self.stream = None  # never orphan the PortAudio stream, even on stop() error
        _beep(440, 120)
        if not self.frames:
            print("[voice-zh] (no audio captured)", flush=True)
            return
        if self._samples < MIN_UTT_SEC * self.capture_sr:
            print("[voice-zh] (too short)", flush=True)
            return
        try:
            audio = np.concatenate(self.frames, axis=0).flatten()
            dur = self._samples / self.capture_sr
            print(f"[voice-zh] transcribing {dur:.1f}s ...", flush=True)
            t0 = time.time()
            text, peak = transcribe_segment(self.model, audio, self.capture_sr)
            print(f"[voice-zh] -> {text!r} (peak={peak:.4f}, {time.time() - t0:.1f}s)", flush=True)
            if peak < 1e-3:
                print(
                    "[voice-zh] WARNING: near-silent capture — raise mic level or set "
                    "VOICE_ZH_DEVICE_INDEX (run this script with --list-devices to find the index)",
                    flush=True,
                )
            if text:
                with self._deliver_lock:
                    if not self._shutdown.is_set():  # don't paste after quit
                        deliver_text(text)
        except Exception as e:
            # keep the daemon alive on any transcribe/inject failure; next toggle works
            print(f"[voice-zh] transcribe failed: {type(e).__name__}: {e}", flush=True)

    def close(self):
        """Guaranteed teardown of any active stream (called on daemon exit)."""
        self._shutdown.set()
        with self._deliver_lock:
            pass  # wait for any in-flight deliver to finish; block new ones
        self.recording = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            finally:
                self.stream = None


class ContinuousListener:
    """Always-on, energy-VAD-segmented dictation.

    Listens continuously; an utterance is the audio between speech onset and a
    trailing silence gap (SILENCE_SEC). Each utterance is transcribed and delivered;
    with AUTO_ENTER, Enter is pressed after a GRACE_SEC confirm window (cancellable
    by pressing the pause hotkey). The pause hotkey toggles listening on/off.

    Energy VAD threshold is calibrated from ~1s of ambient noise at startup (or set
    explicitly via VOICE_ZH_VAD_THRESH). Low-gain mics work best with the mic level
    raised in Windows — if utterances are missed, raise the mic level or lower
    VOICE_ZH_VAD_THRESH.
    """

    def __init__(self, model):
        self.model = model
        self.capture_sr = _capture_samplerate()
        self.blocksize = max(256, int(0.05 * self.capture_sr))  # ~50ms blocks
        self.q = queue.Queue()
        self.stream = None
        self.worker = None
        self.running = False
        self.paused = False
        self._cancel_send = threading.Event()  # set on pause; reset per-utterance in _finalize
        self._busy = threading.Event()  # set while a finalize is running (gates capture)
        self._shutdown = threading.Event()  # set by close() to abort in-flight deliver/Enter
        self._deliver_lock = threading.Lock()  # serialize deliver vs shutdown atomically
        self.threshold = None  # set in start()

    def _calibrate(self):
        if _VAD_THRESH != "auto":
            try:
                return float(_VAD_THRESH)
            except (ValueError, TypeError):
                print(
                    f"[voice-zh] WARNING: invalid VOICE_ZH_VAD_THRESH={_VAD_THRESH!r}, "
                    "falling back to auto-calibration",
                    flush=True,
                )
        print("[voice-zh] calibrating ambient noise ~1s — stay quiet ...", flush=True)
        fallback = 1.5e-3
        try:
            n = int(1.0 * self.capture_sr)
            a = sd.rec(n, samplerate=self.capture_sr, channels=1, dtype="float32", device=DEVICE_INDEX)
            sd.wait()
            noise = float(np.sqrt(np.mean(a.flatten() ** 2)))
        except Exception as e:
            # mic busy / device unavailable: don't block startup, use a conservative default
            print(
                f"[voice-zh] WARNING: calibration failed ({type(e).__name__}: {e}); "
                f"using default vad_threshold={fallback} (set VOICE_ZH_VAD_THRESH to override)",
                flush=True,
            )
            return fallback
        thr = max(noise * 4.0, fallback)  # well above the noise floor to avoid false triggers
        print(
            f"[voice-zh] noise_rms={noise:.5f} -> vad_threshold={thr:.5f} "
            "(raise mic level / set VOICE_ZH_VAD_THRESH if it mis-triggers)",
            flush=True,
        )
        return thr

    def _on_audio(self, indata, frames, time_info, status):
        if status:
            print(f"[voice-zh] audio status: {status}", flush=True)
        if self.paused or self._busy.is_set():
            return  # drop capture while paused or while a finalize is in flight (no backlog)
        block = indata.copy().flatten()
        rms = float(np.sqrt(np.mean(block ** 2)))
        self.q.put((block, rms))

    def _finalize(self, seg, seg_samples):
        if seg_samples < MIN_UTT_SEC * self.capture_sr:
            return
        self._busy.set()  # stop capture during transcribe+grace so no stale backlog builds
        self._cancel_send.clear()  # per-utterance reset (pause during grace re-sets it)
        try:
            audio = np.concatenate(seg, axis=0).flatten()
            dur = seg_samples / self.capture_sr
            print(f"[voice-zh] transcribing {dur:.1f}s ...", flush=True)
            t0 = time.time()
            text, peak = transcribe_segment(self.model, audio, self.capture_sr)
            print(f"[voice-zh] -> {text!r} (peak={peak:.4f}, {time.time() - t0:.1f}s)", flush=True)
            if not text:
                return
            with self._deliver_lock:
                if self._shutdown.is_set():
                    return  # never paste into the focused window during quit
                deliver_text(text)
            if not AUTO_ENTER:
                return
            if self._grace_cancelled():
                print("[voice-zh] auto-enter cancelled", flush=True)
                return
            with self._deliver_lock:  # Enter under the same lock + shutdown recheck (atomic)
                if self._shutdown.is_set():
                    print("[voice-zh] auto-enter cancelled", flush=True)
                else:
                    keyboard.press_and_release("enter")
                    print("[voice-zh] ↵ sent", flush=True)
        except Exception as e:
            print(f"[voice-zh] segment failed: {type(e).__name__}: {e}", flush=True)
        finally:
            self._busy.clear()
            self._drain_queue()  # discard anything captured in the gap before busy took effect

    def _drain_queue(self):
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def _grace_cancelled(self):
        """Wait GRACE_SEC; True if the user cancelled (pause) or the daemon is quitting."""
        deadline = time.time() + GRACE_SEC
        while time.time() < deadline:
            if self._cancel_send.is_set() or self.paused or self._shutdown.is_set():
                return True
            time.sleep(0.05)
        return False

    def _run(self):
        in_speech = False
        seg, seg_samples, silence_run = [], 0, 0.0
        pending, pending_samples, voiced_run = [], 0, 0  # onset debounce buffer
        block_dur = self.blocksize / self.capture_sr
        cap = MAX_SEC * self.capture_sr
        while self.running:
            try:
                try:
                    block, rms = self.q.get(timeout=0.1)
                    got = True
                except queue.Empty:
                    got = False
                if self.paused:  # drop any partial utterance while paused
                    # reset even on an empty queue, else a partial utterance captured
                    # right before pause would survive and merge with post-resume speech
                    in_speech = False
                    seg, seg_samples, silence_run = [], 0, 0.0
                    pending, pending_samples, voiced_run = [], 0, 0
                    continue
                if not got:
                    continue
                voiced = rms > self.threshold
                if not in_speech:
                    # require ONSET_BLOCKS consecutive voiced blocks before starting,
                    # so a single noise spike does not open an utterance (kills false beeps)
                    if voiced:
                        voiced_run += 1
                        pending.append(block)
                        pending_samples += len(block)
                        if voiced_run >= ONSET_BLOCKS:
                            in_speech = True
                            seg, seg_samples = pending, pending_samples
                            pending, pending_samples, voiced_run = [], 0, 0
                            silence_run = 0.0
                            if CONT_BEEP:
                                _beep(880, 80)
                    else:
                        pending, pending_samples, voiced_run = [], 0, 0
                    continue
                # in_speech
                seg.append(block)
                seg_samples += len(block)
                if voiced:
                    silence_run = 0.0
                else:
                    silence_run += block_dur
                    if silence_run >= SILENCE_SEC:
                        if CONT_BEEP:
                            _beep(440, 80)
                        self._finalize(seg, seg_samples)
                        in_speech = False
                        seg, seg_samples, silence_run = [], 0, 0.0
                        continue
                if seg_samples >= cap:  # safety: bound a runaway utterance
                    self._finalize(seg, seg_samples)
                    in_speech = False
                    seg, seg_samples, silence_run = [], 0, 0.0
            except Exception as e:
                # never let the consumer thread die — that would zombie the daemon
                print(f"[voice-zh] listener error: {type(e).__name__}: {e}", flush=True)
                in_speech = False
                seg, seg_samples, silence_run = [], 0, 0.0
                pending, pending_samples, voiced_run = [], 0, 0

    def toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self._cancel_send.set()  # cancel any pending auto-enter (latched until next utterance)
            print("[voice-zh] ⏸ paused", flush=True)
        else:
            # do NOT clear _cancel_send here: a pause within a grace window must keep the
            # auto-Enter cancelled even after a quick resume. It is reset per-utterance in _finalize.
            print("[voice-zh] ▶ resumed", flush=True)

    def start(self):
        self.threshold = self._calibrate()
        self.running = True
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        self.stream = sd.InputStream(
            samplerate=self.capture_sr,
            channels=1,
            dtype="float32",
            blocksize=self.blocksize,
            device=DEVICE_INDEX,
            callback=self._on_audio,
        )
        self.stream.start()

    def close(self):
        # set shutdown first so an in-flight _finalize aborts before delivering/Enter,
        # and so any partially-captured utterance is intentionally dropped on quit
        self._shutdown.set()
        with self._deliver_lock:
            pass  # wait for any in-flight deliver to finish; block new ones
        self.running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            finally:
                self.stream = None
        if self.worker is not None:
            self.worker.join(timeout=2.0)
            self.worker = None


def _list_devices():
    """Print input-capable devices so the user can pick VOICE_ZH_DEVICE_INDEX."""
    print("=== input devices (use the index as VOICE_ZH_DEVICE_INDEX) ===", flush=True)
    hostapis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] >= 1:
            api = hostapis[d["hostapi"]]["name"]
            print(f"  [{i}] {api} {int(d['default_samplerate'])}Hz {d['name']}", flush=True)


def main():
    if "--list-devices" in sys.argv:
        _list_devices()
        return
    mode = MODE
    if mode not in ("continuous", "toggle"):
        print(
            f"[voice-zh] WARNING: unknown VOICE_ZH_MODE={MODE!r}, using 'continuous' "
            "(valid: continuous, toggle)",
            flush=True,
        )
        mode = "continuous"
    model = _load_model()
    if mode == "toggle":
        rec = Recorder(model)
        keyboard.add_hotkey(HOTKEY, rec.toggle)
        print(f"[voice-zh] toggle mode | {HOTKEY} = record | {QUIT} = quit", flush=True)
        try:
            keyboard.wait(QUIT)
        except KeyboardInterrupt:
            pass
        finally:
            rec.close()
    else:  # continuous
        lis = ContinuousListener(model)
        lis.start()
        keyboard.add_hotkey(PAUSE_HOTKEY, lis.toggle_pause)
        enter_note = "auto-Enter after {:.0f}s grace".format(GRACE_SEC) if AUTO_ENTER else "no auto-Enter"
        print(
            f"[voice-zh] continuous mode | just speak ({enter_note}) | "
            f"{PAUSE_HOTKEY} = pause/resume | {QUIT} = quit",
            flush=True,
        )
        try:
            keyboard.wait(QUIT)
        except KeyboardInterrupt:
            pass
        finally:
            lis.close()
    print("[voice-zh] bye", flush=True)


if __name__ == "__main__":
    main()
