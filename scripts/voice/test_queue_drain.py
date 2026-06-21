# -*- coding: utf-8 -*-
"""Behaviour tests for the #495 Slice-5 Stop-hook queue drain (queue_drain.py).

Headless — drives queue_drain.main() with a stubbed stdin (the Stop-hook JSON) and captures
stdout (the {"decision":"block",...} injection). Covers the §8 loop-correctness merge gate:
drain+block on a non-empty queue, the OWN continuation counter + cap (so a daemon that keeps
enqueueing can't spin forever), cap-reached DEFERS items (never drops them), a fresh
user-initiated turn resets the chain, and the no-op guards (idle / empty).

Run: <venv>/python.exe scripts/voice/test_queue_drain.py   (also pytest-compatible)
"""
import io
import os
import sys
import json
import shutil
import tempfile
import contextlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import state as _state          # noqa: E402
import voice_queue as _vq       # noqa: E402
import queue_drain as qd        # noqa: E402


@contextlib.contextmanager
def _temp_state():
    keys = ("VOICE_STATE_DIR", "VOICE_QUEUE_SESSION", "VOICE_QUEUE_MAX_CONTINUATIONS",
            "VOICE_QUEUE_MAX_ITEMS", "VOICE_QUEUE_CONT_WINDOW", "VOICE_QUEUE_DRAIN_MAX_CHARS")
    saved = {k: os.environ.get(k) for k in keys}
    d = tempfile.mkdtemp(prefix="qd-test-")
    os.environ["VOICE_STATE_DIR"] = d
    for k in keys[1:]:
        os.environ.pop(k, None)
    try:
        yield d
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(d, ignore_errors=True)


def _run(stdin_obj, mode="work"):
    """Set the voice mode, feed stdin JSON to queue_drain.main(), return (rc, stdout)."""
    _state.set_mode(mode)
    out = io.StringIO()
    old = sys.stdin
    sys.stdin = io.StringIO(json.dumps(stdin_obj))
    try:
        with contextlib.redirect_stdout(out):
            rc = qd.main()
    finally:
        sys.stdin = old
    return rc, out.getvalue()


def _block_reason(stdout):
    return json.loads(stdout.strip())  # raises if not valid JSON


# --- no-op guards -----------------------------------------------------------------------

def test_empty_queue_no_block():
    with _temp_state():
        rc, out = _run({"stop_hook_active": False, "session_id": "e"})
        assert rc == 0 and out.strip() == ""  # nothing queued -> no block


def test_idle_mode_no_block():
    with _temp_state():
        _vq.enqueue("hi", session="i")
        rc, out = _run({"stop_hook_active": False, "session_id": "i"}, mode="idle")
        assert rc == 0 and out.strip() == ""  # idle -> no-op even with a non-empty queue


# --- drain + block ----------------------------------------------------------------------

def test_drains_and_blocks_with_transcript():
    with _temp_state():
        s = "drain"
        _vq.enqueue("跑一下测试", ts="2026-06-21T10:00:00+08:00", session=s)
        _vq.enqueue("顺便看看日志", ts="2026-06-21T10:00:01+08:00", session=s)
        rc, out = _run({"stop_hook_active": False, "session_id": s})
        assert rc == 0
        obj = _block_reason(out)
        assert obj["decision"] == "block"
        assert "跑一下测试" in obj["reason"] and "顺便看看日志" in obj["reason"]
        assert _vq.is_empty(s)  # both consumed


def test_reason_is_valid_json_with_quotes_and_chinese():
    with _temp_state():
        s = "json"
        _vq.enqueue('他说"立刻部署"，对吧\\反斜杠', session=s)
        rc, out = _run({"stop_hook_active": False, "session_id": s})
        obj = _block_reason(out)  # must parse — reason is json.dumps-serialised, never concatenated
        assert '他说"立刻部署"' in obj["reason"]


# --- §8 continuation counter + cap ------------------------------------------------------

def test_continuation_cap_defers_without_dropping():
    with _temp_state():
        os.environ["VOICE_QUEUE_MAX_CONTINUATIONS"] = "2"
        s = "cap"
        _vq.enqueue("a", ts="2026-06-21T10:00:00+08:00", session=s)
        _, out = _run({"stop_hook_active": False, "session_id": s})   # turn 0 -> count 1
        assert _block_reason(out)["decision"] == "block"
        _vq.enqueue("b", ts="2026-06-21T10:00:01+08:00", session=s)
        _, out = _run({"stop_hook_active": True, "session_id": s})    # turn 1 -> count 2
        assert _block_reason(out)["decision"] == "block"
        _vq.enqueue("c", ts="2026-06-21T10:00:02+08:00", session=s)
        rc, out = _run({"stop_hook_active": True, "session_id": s})   # count 2 >= cap 2 -> stop
        assert rc == 0 and out.strip() == ""                          # no block
        assert [it["text"] for it in _vq.peek_all(s)] == ["c"]        # 'c' DEFERRED, not dropped


def test_fresh_user_turn_resets_continuation_chain():
    with _temp_state():
        os.environ["VOICE_QUEUE_MAX_CONTINUATIONS"] = "1"
        s = "reset"
        _vq.enqueue("a", session=s)
        _, out = _run({"stop_hook_active": False, "session_id": s})  # turn 0 -> count 1
        assert _block_reason(out)["decision"] == "block"
        # a continuation now would hit the cap (count 1 >= 1) and defer; but a FRESH user turn
        # (stop_hook_active false) resets the chain -> drains + blocks again
        _vq.enqueue("b", session=s)
        rc, out = _run({"stop_hook_active": False, "session_id": s})  # fresh turn -> count reset
        assert _block_reason(out)["decision"] == "block"
        assert "b" in _block_reason(out)["reason"]


def test_reason_length_is_capped():
    # Argus: max_items caps the COUNT but not chars — a long transcription must not bloat the
    # block output. The assembled transcript is truncated at VOICE_QUEUE_DRAIN_MAX_CHARS.
    with _temp_state():
        os.environ["VOICE_QUEUE_DRAIN_MAX_CHARS"] = "50"
        s = "longcap"
        _vq.enqueue("x" * 300, session=s)
        rc, out = _run({"stop_hook_active": False, "session_id": s})
        reason = _block_reason(out)["reason"]
        assert "（其余略）" in reason            # truncation marker present
        assert reason.count("x") <= 60           # bounded near the 50-char cap, not 300


def test_bad_stdin_json_exits_zero_no_block():
    with _temp_state():
        _vq.enqueue("hi", session="bad")  # queue non-empty, but stdin is garbage
        _state.set_mode("work")
        out = io.StringIO()
        old = sys.stdin
        sys.stdin = io.StringIO("{not valid json")
        try:
            with contextlib.redirect_stdout(out):
                rc = qd.main()
        finally:
            sys.stdin = old
        assert rc == 0 and out.getvalue().strip() == ""  # malformed payload -> no-op, never crashes


def test_env_session_overrides_stdin_session_id():
    with _temp_state():
        os.environ["VOICE_QUEUE_SESSION"] = "env-sess"
        _vq.enqueue("from-env-session", session="env-sess")  # only the ENV session has an item
        # stdin carries a DIFFERENT session_id; env must win (drain reads env-sess, finds the item)
        rc, out = _run({"stop_hook_active": False, "session_id": "stdin-sess"})
        assert _block_reason(out)["decision"] == "block"
        assert "from-env-session" in _block_reason(out)["reason"]


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
