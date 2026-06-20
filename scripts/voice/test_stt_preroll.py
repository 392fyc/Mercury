# -*- coding: utf-8 -*-
"""Behaviour tests for the #495 Slice-1 pre-record ring buffer in stt.listen_once.

Drives the REAL listen_once VAD loop with a fake sounddevice.InputStream that feeds a
preset block sequence, and a fake engine.transcribe that captures the assembled audio —
so we test the actual segmentation logic with no live microphone.

Runnable headless: the heavy native deps (faster_whisper / sounddevice) are stubbed if
absent, so `import stt` and these tests work in CI as well as on a voice box.

Run: <venv>/python.exe scripts/voice/test_stt_preroll.py   (also pytest-compatible)
"""
import io
import os
import sys
import types
import contextlib

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# faster_whisper is a hard production import in stt.py, but this test never instantiates a
# model (engine.transcribe is faked), so inject a stub whenever it isn't already loaded.
# Unconditional (no real-import probe) keeps the test hermetic and independent of whether
# faster_whisper's native layer imports cleanly in this env. sounddevice is already guarded
# inside stt (-> sd=None) and we replace stt.sd with a fake below.
if "faster_whisper" not in sys.modules:
    _fw = types.ModuleType("faster_whisper")
    _fw.WhisperModel = object
    sys.modules["faster_whisper"] = _fw

import stt as _stt  # noqa: E402

SR = 16000
BLOCKSIZE = max(256, int(0.05 * SR))  # mirrors listen_once: 800 @ 16k
SILENCE = 0.0          # rms 0 < threshold
VOICED = 0.5           # rms 0.5 > threshold (we pass vad_thresh=0.1 explicitly)
THRESH = 0.1


def _seq(lead_in, onset, body, trailing, sr=SR):
    """lead_in silence + onset voiced + body voiced + trailing silence."""
    bs = max(256, int(0.05 * sr))
    sil = np.full(bs, SILENCE, dtype=np.float32)
    voi = np.full(bs, VOICED, dtype=np.float32)
    return ([sil] * lead_in) + ([voi] * onset) + ([voi] * body) + ([sil] * trailing)


class _FakeStream:
    def __init__(self, blocks, callback):
        self._blocks, self._cb = blocks, callback

    def __enter__(self):
        for b in self._blocks:
            self._cb(b, len(b), None, None)  # fill the queue synchronously
        return self

    def __exit__(self, *a):
        return False


class _FakeSd:
    def __init__(self, blocks, sr=SR):
        self._blocks, self._sr = blocks, sr

    def InputStream(self, **kw):
        return _FakeStream(self._blocks, kw["callback"])

    def query_devices(self, *a, **k):
        return {"default_samplerate": self._sr}


def _run(env_value, blocks, sr=SR, silence_sec=0.8, min_sec=0.4):
    """Run listen_once against fake audio; return (captured_audio, stderr_text, text)."""
    if env_value is None:
        os.environ.pop("VOICE_PRE_RECORD_SEC", None)
    else:
        os.environ["VOICE_PRE_RECORD_SEC"] = env_value
    eng = _stt.SttEngine(model="x", device="cpu")
    eng.model = object()  # bypass the load() guard
    captured = {}
    eng.transcribe = lambda audio, capture_sr: (captured.__setitem__("audio", audio) or ("OK", 0.0))
    saved = _stt.sd
    _stt.sd = _FakeSd(blocks, sr=sr)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            text = eng.listen_once(max_seconds=5, silence_sec=silence_sec,
                                   min_sec=min_sec, vad_thresh=THRESH)
    finally:
        _stt.sd = saved
        os.environ.pop("VOICE_PRE_RECORD_SEC", None)
    return captured.get("audio"), err.getvalue(), text


def _nblocks(audio, sr=SR):
    return 0 if audio is None else len(audio) // max(256, int(0.05 * sr))


def _is_silence(chunk):
    return float(np.max(np.abs(chunk))) < 0.01


# 4 lead-in silence, 3 onset voiced, 5 body voiced, 18 trailing silence (>0.8s -> break)
SEQ = _seq(4, 3, 5, 18)


def test_preroll_on_preserves_leadin():
    audio, _, _ = _run("0.3", SEQ)
    assert audio is not None
    # ring maxlen = max(ceil(0.3/0.05)=6, onset_blocks=3) = 6 -> seg seeded with last 6
    # pre-onset blocks = 3 lead-in silence + 3 onset voiced. First 3 blocks must be silence.
    assert _is_silence(audio[: 3 * BLOCKSIZE]), "lead-in silence was clipped"
    assert not _is_silence(audio[3 * BLOCKSIZE : 6 * BLOCKSIZE]), "onset voiced missing"


def test_preroll_off_no_leadin():
    on, _, _ = _run("0.3", SEQ)
    off, _, _ = _run("0", SEQ)
    # off path seeds from `pending` (onset blocks only) -> no lead-in, starts voiced
    assert not _is_silence(off[: BLOCKSIZE]), "off path should start at onset (voiced)"
    # difference is exactly the lead-in: pre_blocks(6) - onset_blocks(3) = 3 blocks
    assert _nblocks(on) - _nblocks(off) == 3, (_nblocks(on), _nblocks(off))


def test_invalid_env_falls_back_to_default():
    on_ref = _nblocks(_run("0.3", SEQ)[0])
    for bad in ("abc", "nan", "-1", "inf", "-inf"):
        audio, err, _ = _run(bad, SEQ)
        assert "invalid VOICE_PRE_RECORD_SEC" in err, f"{bad}: expected warning"
        assert _nblocks(audio) == on_ref, f"{bad}: should behave as default 0.3"
        assert _is_silence(audio[: 3 * BLOCKSIZE]), f"{bad}: lead-in lost"


def test_huge_finite_value_no_crash():
    # finite-but-huge (and finite-but-over-MAX) must NOT crash listen() at ceil(sec/block_dur)
    # / deque(maxlen=...). They warn + fall back to the default (Codex #495 audit).
    on_ref = _nblocks(_run("0.3", SEQ)[0])
    for big in ("1e308", "1e6", "100"):
        audio, err, _ = _run(big, SEQ)
        assert "invalid VOICE_PRE_RECORD_SEC" in err, f"{big}: expected out-of-range warning"
        assert _nblocks(audio) == on_ref, f"{big}: should fall back to default 0.3"
        assert _is_silence(audio[: 3 * BLOCKSIZE]), f"{big}: lead-in lost"


def test_large_but_valid_value_ok():
    # a large-but-valid value (<=10s) is accepted with no warning and does not crash
    audio, err, text = _run("5", SEQ)
    assert "invalid VOICE_PRE_RECORD_SEC" not in err, "5s is valid, should not warn"
    assert text == "OK"
    assert _is_silence(audio[: 4 * BLOCKSIZE]), "lead-in expected for a large ring"


def test_upper_bound_boundary():
    # PRE_RECORD_MAX_SEC boundary: exactly 10 accepted (no warn); just over -> warn + fallback
    _, err_ok, text_ok = _run("10", SEQ)
    assert "invalid VOICE_PRE_RECORD_SEC" not in err_ok, "10s (== bound) must be accepted"
    assert text_ok == "OK"
    audio_over, err_over, _ = _run("10.001", SEQ)
    assert "invalid VOICE_PRE_RECORD_SEC" in err_over, "just over the bound must warn"
    assert _is_silence(audio_over[: 3 * BLOCKSIZE]), "over-bound -> fallback default 0.3"


def test_empty_string_uses_default_silently():
    audio, err, _ = _run("", SEQ)
    assert "invalid" not in err, "empty string should be silently treated as unset"
    assert _is_silence(audio[: 3 * BLOCKSIZE]), "empty -> default 0.3 -> lead-in present"


def test_preroll_off_no_leadin_value():
    off, _, _ = _run("0", SEQ)
    assert not _is_silence(off[: BLOCKSIZE]), "0 disables pre-record"


def test_subonset_value_no_regression():
    # 0.05s -> ceil(0.05/0.05)=1 block, bumped up to onset_blocks(3) by max(). The deque
    # then holds exactly the 3 onset blocks (no extra lead-in) -> identical to OFF, NEVER
    # dropping onset audio. Locks the max(ceil, onset_blocks) hardening.
    sub, _, _ = _run("0.05", SEQ)
    off, _, _ = _run("0", SEQ)
    assert _nblocks(sub) == _nblocks(off), (_nblocks(sub), _nblocks(off))
    assert not _is_silence(sub[: BLOCKSIZE]), "sub-onset value must not inject lead-in"


def test_min_sec_gate_unaffected_by_preroll():
    # A too-short utterance (3 onset + 2 trailing = 0.25s of content < min_sec 0.4s) must be
    # gated to "" with the feature ON and OFF alike. Pre-fix, the silence lead-in inflated
    # seg_samples and let it pass with ON. silence_sec=0.1 -> break after 2 trailing blocks.
    short = _seq(4, 3, 0, 4)
    _, _, on = _run("0.3", short, silence_sec=0.1)
    _, _, off = _run("0", short, silence_sec=0.1)
    assert off == "", "control: too-short utterance gated with feature off"
    assert on == "", "pre-roll lead-in must NOT let a too-short utterance pass the min_sec gate"


def test_unset_uses_default():
    audio, _, _ = _run(None, SEQ)
    assert _is_silence(audio[: 3 * BLOCKSIZE]), "default (unset) should pre-record"


def test_maxlen_scales_with_samplerate():
    # block_dur is fixed at 0.05s (blocksize = 0.05*sr), so pre_blocks is sr-independent
    # but SAMPLES per block scale: lead-in DURATION stays 0.3s. At 48k the 3 lead-in blocks
    # are 3*2400 samples of silence.
    sr = 48000
    bs = int(0.05 * sr)
    audio, _, _ = _run("0.3", _seq(4, 3, 5, 18, sr=sr), sr=sr)
    assert _is_silence(audio[: 3 * bs]), "lead-in lost at 48k"
    assert not _is_silence(audio[3 * bs : 6 * bs]), "onset voiced missing at 48k"


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
