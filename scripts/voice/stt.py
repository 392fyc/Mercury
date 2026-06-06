# -*- coding: utf-8 -*-
"""stt — faster-whisper STT engine + single-utterance blocking capture (Issue #468).

Carries forward the empirically-verified STT primitives from #465
(`scripts/voice-zh-input.py`): the Windows CUDA/cuDNN DLL-dir registration,
native-rate capture + linear resample to 16 kHz (forcing 16 kHz on the Realtek
driver returns digital silence), and the no_speech_prob / avg_logprob +
signature-blocklist hallucination gate that kills Whisper's "字幕志愿者"-style
silence hallucinations.

This module is the shared core: `scripts/voice-zh-input.py` (the input daemon)
and `scripts/voice/mcp_server.py` (the in-session MCP server) both use it, so the
hard-won primitives live in exactly one place.

Local + private: no audio leaves the machine. All STT runs on the local
faster-whisper model (GPU float16 / CPU int8 fallback).
"""
import os
import sys
import site
import time
import queue
import threading
from pathlib import Path

SAMPLERATE = 16000  # faster-whisper expects 16 kHz mono float32

# signature phrases Whisper emits on silence/noise — never legitimate dictation here
# (carried over from #465; extend here and both daemon + MCP server benefit)
HALLUCINATION_MARKERS = (
    "字幕志愿者", "字幕由", "amara.org", "明镜与点点", "点点栏目",
    "请不吝点赞", "点赞订阅", "订阅转发", "谢谢观看", "谢谢大家观看",
)


def _register_cuda_dll_dirs():
    """Make the bundled NVIDIA CUDA/cuDNN DLLs loadable by ctranslate2 on Windows.

    add_dll_directory alone is NOT enough on Windows — ctranslate2's internal loader
    resolves the CUDA DLLs via PATH, so the bin dirs are prepended to PATH as well.
    Must run BEFORE faster_whisper is imported. (Verified in #465.)
    """
    if os.name != "nt":
        return  # Windows-only workaround; never mutate PATH elsewhere
    bins = []
    for base in site.getsitepackages():
        base_real = os.path.realpath(base)
        for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
            p = Path(base) / "nvidia" / sub / "bin"
            if not p.is_dir():
                continue
            real = os.path.realpath(str(p))
            # only accept real dirs that stay under the site-packages root, and dedupe,
            # to avoid widening the DLL search path via stray symlinks / repeats.
            # normcase both sides: on Windows the prefix compare must be case-insensitive
            # (and drive-letter/sep normalized) or a legitimate CUDA dir gets wrongly
            # rejected (→ GPU load fails, needless CPU fallback).
            if not os.path.normcase(real).startswith(os.path.normcase(base_real)) or real in bins:
                continue
            try:
                os.add_dll_directory(real)
            except (OSError, AttributeError):
                pass
            bins.append(real)
    if bins:
        os.environ["PATH"] = os.pathsep.join(bins) + os.pathsep + os.environ.get("PATH", "")


_register_cuda_dll_dirs()

import numpy as np  # noqa: E402  (after DLL-dir registration)

try:
    import sounddevice as sd  # noqa: E402
except OSError:  # PortAudio not present (e.g. headless CI) — capture disabled, transcribe still works
    sd = None
from faster_whisper import WhisperModel  # noqa: E402


def resample_to_16k(audio, src_sr):
    """Linear resample mono float32 audio to 16 kHz (good enough for STT)."""
    if src_sr == SAMPLERATE or len(audio) == 0:
        return audio
    n = int(round(len(audio) * SAMPLERATE / src_sr))
    if n <= 0:
        return audio
    x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    x_new = np.linspace(0.0, 1.0, num=n, endpoint=False)
    return np.interp(x_new, x_old, audio).astype(np.float32)


def is_hallucination(text):
    """True if text matches a known Whisper-on-silence hallucination signature."""
    t = "".join(text.lower().split())
    return any(m in t for m in HALLUCINATION_MARKERS)


class SttEngine:
    """Persistent faster-whisper engine: load once, transcribe / listen many times.

    Parametrized (no module-level config globals) so a long-lived process such as the
    MCP server can hold one warm model and switch model size per mode (e.g. a smaller
    model for low-latency secretary mode). Mirrors #465's transcribe/load behavior.
    """

    def __init__(self, model="large-v3", device="auto", normalize=True, vad=False,
                 no_speech_max=0.6, logprob_min=-1.0, device_index=None):
        self.model_name = model
        self.device = device
        self.normalize = normalize
        self.vad = vad
        self.no_speech_max = no_speech_max
        self.logprob_min = logprob_min
        self.device_index = device_index
        self.model = None

    def load(self):
        """Load WhisperModel, honoring device ('auto' tries CUDA then CPU).

        The CUDA cuBLAS failure only surfaces at inference time (not at construction),
        so 'auto' validates the device with a tiny warmup transcription before
        committing to it. Raises RuntimeError if no device is usable.
        """
        device_map = {
            "cuda": [("cuda", "float16")],
            "cpu": [("cpu", "int8")],
            "auto": [("cuda", "float16"), ("cpu", "int8")],
        }
        if self.device not in device_map:
            raise ValueError(f"invalid device {self.device!r} (allowed: {', '.join(device_map)})")
        warmup = np.zeros(SAMPLERATE, dtype=np.float32)  # 1s of silence
        for dev, comp in device_map[self.device]:
            try:
                print(f"[voice-stt] loading {self.model_name} on {dev}/{comp} ...", file=sys.stderr, flush=True)
                t0 = time.time()
                model = WhisperModel(self.model_name, device=dev, compute_type=comp)
                list(model.transcribe(warmup, language="zh")[0])  # validate device end-to-end
                print(f"[voice-stt] ready on {dev}/{comp} ({time.time() - t0:.1f}s)", file=sys.stderr, flush=True)
                self.model = model
                return self
            except Exception as e:
                print(f"[voice-stt] {dev} unavailable: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        raise RuntimeError("no usable STT device (cuda/cpu)")

    def transcribe(self, audio_native, capture_sr):
        """Resample + optional peak-normalize native-rate audio, then transcribe zh.

        Non-speech / hallucination segments are dropped via Whisper's per-segment
        no_speech_prob + avg_logprob, backed by a signature blocklist. Returns
        (text, peak); peak is measured on the raw native capture so callers can warn
        on near-silent input.
        """
        if self.model is None:
            raise RuntimeError("SttEngine.load() not called")
        peak = float(np.max(np.abs(audio_native))) if len(audio_native) else 0.0
        audio = resample_to_16k(audio_native, capture_sr)
        if self.normalize and peak > 1e-4:
            audio = (audio / peak * 0.3).astype(np.float32)  # rescue low-gain mics
        segments, _ = self.model.transcribe(
            audio,
            language="zh",
            beam_size=5,
            vad_filter=self.vad,
            condition_on_previous_text=False,  # don't carry hallucinations across calls
            no_speech_threshold=self.no_speech_max,
            log_prob_threshold=self.logprob_min,
        )
        kept = []
        for s in segments:
            # fail-safe: if a faster-whisper build omits these fields, drop the segment
            # rather than admit a potential hallucination
            if getattr(s, "no_speech_prob", 1.0) >= self.no_speech_max:
                continue
            if getattr(s, "avg_logprob", -10.0) <= self.logprob_min:
                continue
            kept.append(s.text)
        text = "".join(kept).strip()
        if is_hallucination(text):
            return "", peak
        return text, peak

    def capture_samplerate(self):
        """Native samplerate of the chosen input device (forcing 16 kHz returns silence
        on the Realtek driver, so we capture native + resample)."""
        if sd is None:
            return SAMPLERATE
        try:
            d = (sd.query_devices(self.device_index, kind="input")
                 if self.device_index is not None else sd.query_devices(kind="input"))
            return int(d["default_samplerate"])
        except Exception:
            return SAMPLERATE

    def listen_once(self, max_seconds=20.0, silence_sec=0.8, min_sec=0.4,
                    vad_thresh="auto", onset_blocks=3):
        """Block until one utterance is captured (energy-VAD-segmented), then transcribe.

        Records continuously; an utterance is the audio between speech onset (after
        `onset_blocks` consecutive voiced blocks) and a trailing silence gap
        (`silence_sec`), capped at `max_seconds`. Returns the transcribed zh text
        (empty string on silence/timeout/hallucination). This is the blocking
        record-and-return primitive behind the MCP `listen` tool (mirrors #465's
        ContinuousListener VAD, simplified to a single utterance).
        """
        if sd is None:
            raise RuntimeError("sounddevice/PortAudio unavailable — cannot capture audio")
        if self.model is None:
            raise RuntimeError("SttEngine.load() not called")
        capture_sr = self.capture_samplerate()
        blocksize = max(256, int(0.05 * capture_sr))  # ~50ms blocks
        block_dur = blocksize / capture_sr
        q = queue.Queue()

        def _on_audio(indata, frames, time_info, status):
            block = indata.copy().flatten()
            rms = float(np.sqrt(np.mean(block ** 2)))
            q.put((block, rms))

        threshold = self._calibrate(capture_sr, vad_thresh)
        seg, seg_samples, silence_run = [], 0, 0.0
        pending, pending_samples, voiced_run = [], 0, 0
        in_speech = False
        # absolute wall cap = recording cap + a wait-for-onset grace. The grace bounds how
        # long we block waiting for the user to START speaking; keep it modest so a silent
        # listen() returns well within Claude Code's tool-call timeout (ADR §6 risk).
        onset_grace = float(os.environ.get("VOICE_LISTEN_ONSET_GRACE", "10") or "10")
        deadline = time.time() + max_seconds + onset_grace
        cap = int(max_seconds * capture_sr)

        with sd.InputStream(samplerate=capture_sr, channels=1, dtype="float32",
                            blocksize=blocksize, device=self.device_index, callback=_on_audio):
            while time.time() < deadline:
                try:
                    block, rms = q.get(timeout=0.1)
                except queue.Empty:
                    continue
                voiced = rms > threshold
                if not in_speech:
                    if voiced:
                        voiced_run += 1
                        pending.append(block)
                        pending_samples += len(block)
                        if voiced_run >= onset_blocks:
                            in_speech = True
                            seg, seg_samples = pending, pending_samples
                            pending, pending_samples, voiced_run = [], 0, 0
                            silence_run = 0.0
                    else:
                        pending, pending_samples, voiced_run = [], 0, 0
                    continue
                seg.append(block)
                seg_samples += len(block)
                if voiced:
                    silence_run = 0.0
                else:
                    silence_run += block_dur
                    if silence_run >= silence_sec:
                        break
                if seg_samples >= cap:
                    break

        if seg_samples < min_sec * capture_sr:
            return ""
        audio = np.concatenate(seg, axis=0).flatten()
        text, _peak = self.transcribe(audio, capture_sr)
        return text

    def _calibrate(self, capture_sr, vad_thresh):
        """Energy-VAD threshold: explicit float, env override, or ~1s ambient-noise
        auto-calibration (`noise * VOICE_VAD_FACTOR`).

        Resolution order: `vad_thresh` argument → `VOICE_VAD_THRESH` env (absolute)
        → auto-calibration. The default factor is 2.5: low-gain laptop mic arrays
        (e.g. Realtek at max +30dB boost) produce normal-speech block RMS only
        ~3.5x the noise floor, so the previous 4.0 sat ABOVE real speech and listen()
        silently dropped utterances (#472). 2.5 keeps headroom against the floor
        while admitting quiet-but-real speech.
        """
        if vad_thresh != "auto":
            try:
                v = float(vad_thresh)
                if np.isfinite(v) and v > 0:
                    return v
            except (ValueError, TypeError):
                pass
            print(f"[voice-stt] invalid vad_thresh={vad_thresh!r} (need finite > 0), "
                  "falling back to env/auto", file=sys.stderr, flush=True)
        # env overrides must be finite AND positive: 0/negative makes every block voiced
        # (listen self-triggers on room noise), nan/inf makes `rms > threshold` never
        # succeed (listen silently drops ALL speech — the exact failure mode #472 fixes).
        # A mistyped env var must warn + fall back, never silently break the chain.
        env_thresh = os.environ.get("VOICE_VAD_THRESH")
        if env_thresh:
            try:
                v = float(env_thresh)
                if np.isfinite(v) and v > 0:
                    return v
            except ValueError:
                pass
            print(f"[voice-stt] invalid VOICE_VAD_THRESH={env_thresh!r} (need finite > 0), "
                  "falling back to auto", file=sys.stderr, flush=True)
        factor = 2.5
        env_factor = os.environ.get("VOICE_VAD_FACTOR")
        if env_factor:
            try:
                f = float(env_factor)
                if np.isfinite(f) and f > 0:
                    factor = f
                else:
                    raise ValueError(env_factor)
            except ValueError:
                print(f"[voice-stt] invalid VOICE_VAD_FACTOR={env_factor!r} (need finite > 0), "
                      f"using default {factor}", file=sys.stderr, flush=True)
        fallback = 1.5e-3
        try:
            n = int(1.0 * capture_sr)
            a = sd.rec(n, samplerate=capture_sr, channels=1, dtype="float32", device=self.device_index)
            sd.wait()
            noise = float(np.sqrt(np.mean(a.flatten() ** 2)))
        except Exception:
            return fallback
        return max(noise * factor, fallback)  # above the noise floor to avoid false triggers
