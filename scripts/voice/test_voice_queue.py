# -*- coding: utf-8 -*-
"""Behaviour tests for the #495 Slice-2 transcript queue (voice_queue.py).

Pure file IO — no microphone, no native deps. Each test runs against a throwaway
VOICE_STATE_DIR so the queue lives entirely under a temp dir. Covers the §8 adversarial
merge gates: atomic enqueue, pinned `utt-*.json` glob (half-write safe), per-file parse
resilience, per-session isolation, exactly-once concurrent drain, and watermark-anchored
pop_latest.

Run: <venv>/python.exe scripts/voice/test_voice_queue.py   (also pytest-compatible)
"""
import os
import sys
import json
import shutil
import tempfile
import threading
import contextlib
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import voice_queue as vq  # noqa: E402


@contextlib.contextmanager
def _temp_state():
    """Point VOICE_STATE_DIR at a fresh temp dir for the duration of one test."""
    prev = os.environ.get("VOICE_STATE_DIR")
    prev_sess = os.environ.get("VOICE_QUEUE_SESSION")
    d = tempfile.mkdtemp(prefix="vq-test-")
    os.environ["VOICE_STATE_DIR"] = d
    os.environ.pop("VOICE_QUEUE_SESSION", None)  # tests pass session explicitly
    try:
        yield d
    finally:
        for key, val in (("VOICE_STATE_DIR", prev), ("VOICE_QUEUE_SESSION", prev_sess)):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        shutil.rmtree(d, ignore_errors=True)


# --- core round-trip ---------------------------------------------------------------

def test_enqueue_peek_roundtrip():
    with _temp_state():
        s = "round"
        assert vq.enqueue("一", session=s)
        assert vq.enqueue("二", session=s)
        items = vq.peek_all(session=s)
        assert [it["text"] for it in items] == ["一", "二"], items
        assert all(it["consumed"] is False for it in items)  # peek does not consume
        assert not vq.is_empty(session=s)


def test_enqueue_empty_returns_none():
    with _temp_state():
        s = "empty-text"
        assert vq.enqueue("", session=s) is None
        assert vq.enqueue("   ", session=s) is None
        assert vq.enqueue(None, session=s) is None
        assert vq.is_empty(session=s)


def test_drain_empty_and_pop_empty():
    with _temp_state():
        assert vq.drain(5, session="none") == []
        assert vq.pop_latest(session="none") is None
        assert vq.is_empty(session="none")
        assert vq.drain(0, session="none") == []  # non-positive max -> no-op


# --- drain FIFO + consumed/ archive ------------------------------------------------

def test_drain_takes_oldest_and_moves_to_consumed():
    with _temp_state():
        s = "fifo"
        vq.enqueue("a", ts="2026-06-21T10:00:00+08:00", session=s)
        vq.enqueue("b", ts="2026-06-21T11:00:00+08:00", session=s)
        vq.enqueue("c", ts="2026-06-21T12:00:00+08:00", session=s)
        drained = vq.drain(2, session=s)
        assert [it["text"] for it in drained] == ["a", "b"], drained  # oldest first
        assert drained[0]["consumed"] is True  # returned dict reflects consumption
        assert [it["text"] for it in vq.peek_all(session=s)] == ["c"]  # third remains
        # the two claimed files left consumed-markers (O_EXCL) and were removed from the queue dir
        assert len(list(vq._consumed_dir(s).glob("utt-*.json"))) == 2
        assert len(list(vq._queue_dir(s).glob("utt-*.json"))) == 1


def test_is_empty_lifecycle():
    with _temp_state():
        s = "lifecycle"
        assert vq.is_empty(session=s)
        vq.enqueue("x", session=s)
        assert not vq.is_empty(session=s)
        vq.drain(10, session=s)
        assert vq.is_empty(session=s)


# --- half-write / corruption safety (§8 merge gates) -------------------------------

def test_halfwrite_tmp_excluded_by_glob():
    with _temp_state():
        s = "tmp-safe"
        vq.enqueue("real", session=s)
        qd = vq._queue_dir(s)
        # a half-written enqueue temp (the .tmp-*.json os.replace target) must be invisible
        (qd / ".tmp-inflight.json").write_text('{"ts": "2026', encoding="utf-8")
        assert [it["text"] for it in vq.peek_all(session=s)] == ["real"]
        drained = vq.drain(10, session=s)
        assert [it["text"] for it in drained] == ["real"]  # no exception, tmp untouched
        assert (qd / ".tmp-inflight.json").exists()  # pinned glob never read/moved it


def test_corrupt_utt_skipped_batch_survives():
    with _temp_state():
        s = "corrupt"
        vq.enqueue("ok1", ts="2026-06-21T10:00:00+08:00", session=s)
        vq.enqueue("ok2", ts="2026-06-21T11:00:00+08:00", session=s)
        qd = vq._queue_dir(s)
        # a truncated file that DOES match utt-*.json: a single bad file must be skipped
        # per-file, never aborting the whole batch (would silently drop ok1+ok2 otherwise)
        (qd / "utt-99999999-0-999999.json").write_text('{"ts": "2026-06-21T13:00', encoding="utf-8")
        assert sorted(it["text"] for it in vq.peek_all(session=s)) == ["ok1", "ok2"]
        drained = vq.drain(10, session=s)
        assert sorted(it["text"] for it in drained) == ["ok1", "ok2"]  # batch survived
        assert (qd / "utt-99999999-0-999999.json").exists()  # left for a later pass


def test_malformed_text_skipped():
    # C1: an item with a ts but no usable string "text" is malformed and must be skipped —
    # never listed/drained/counted — while a valid item alongside it is still delivered.
    with _temp_state():
        s = "notext"
        vq.enqueue("ok", ts="2026-06-21T11:00:00+08:00", session=s)
        qd = vq._queue_dir(s)
        for nm, payload in (
            ("utt-bad1-0-000000.json", {"ts": "2026-06-21T10:00:00+08:00", "consumed": False}),
            ("utt-bad2-0-000000.json", {"ts": "2026-06-21T10:00:00+08:00", "text": 123}),
            ("utt-bad3-0-000000.json", {"ts": "2026-06-21T10:00:00+08:00", "text": "   "}),
        ):
            (qd / nm).write_text(json.dumps(payload), encoding="utf-8")
        assert [it["text"] for it in vq.peek_all(session=s)] == ["ok"]
        assert [it["text"] for it in vq.drain(10, session=s)] == ["ok"]
        assert vq.is_empty(session=s)  # the malformed orphans don't keep is_empty False


def test_drain_coerces_max_items():
    # A1: max_items is a boundary input (env var). A non-int / non-positive value must be a
    # no-op (never a TypeError that aborts consumption); a float is truncated via int().
    with _temp_state():
        s = "coerce"
        vq.enqueue("a", ts="2026-06-21T10:00:00+08:00", session=s)
        vq.enqueue("b", ts="2026-06-21T11:00:00+08:00", session=s)
        vq.enqueue("c", ts="2026-06-21T12:00:00+08:00", session=s)
        assert vq.drain("not-an-int", session=s) == []  # bad str -> no-op, no crash
        assert vq.drain(None, session=s) == []           # None -> no-op
        assert vq.drain(-1, session=s) == []             # non-positive -> no-op
        assert [it["text"] for it in vq.drain(2.9, session=s)] == ["a", "b"]  # int(2.9)=2


def test_atomic_enqueue_leaves_no_tmp():
    with _temp_state():
        s = "atomic"
        vq.enqueue("done", session=s)
        qd = vq._queue_dir(s)
        assert list(qd.glob(".tmp-*.json")) == []  # os.replace consumed the temp
        assert len(list(qd.glob("utt-*.json"))) == 1


# --- per-session scoping (§8: concurrent lanes share .mercury/state) ---------------

def test_per_session_isolation():
    with _temp_state():
        vq.enqueue("forA", session="A")
        vq.enqueue("forB", session="B")
        assert [it["text"] for it in vq.peek_all(session="A")] == ["forA"]
        assert [it["text"] for it in vq.peek_all(session="B")] == ["forB"]
        vq.drain(10, session="A")  # draining A must not touch B
        assert vq.is_empty(session="A")
        assert not vq.is_empty(session="B")


def test_session_sanitization_no_traversal():
    with _temp_state() as d:
        root = (Path(d) / "voice-queue").resolve()
        for raw in ("../../etc", "..", "a/../../b", "/abs/path"):
            qd = vq._queue_dir(raw).resolve()
            assert str(qd).startswith(str(root) + os.sep), (raw, qd, root)  # confined under root
            assert os.sep not in qd.name and "/" not in qd.name, (raw, qd.name)  # one component
        assert vq._queue_dir("../../etc").name.startswith("etc-")  # sanitised slug + hash suffix


def test_session_resolution_and_injectivity():
    with _temp_state():
        # an already-safe id (UUID-like) maps to itself, verbatim and readable
        assert vq._queue_dir("5e193a21-c931-4b46").name == "5e193a21-c931-4b46"
        # distinct raw ids that sanitise to the SAME slug must NOT share a queue dir (F2:
        # per-session isolation is a safety property — a collision = cross-session leak)
        assert vq._queue_dir("a/b").name != vq._queue_dir("a:b").name
        # an OMITTED session (None) -> shared default bucket
        assert vq._queue_dir(None).name == "default"
        # an EXPLICIT falsy value (0 / "") is honoured as its own bucket, NEVER merged into
        # default or another session — two distinct explicit values never collide (F1/A3)
        assert vq._queue_dir("").name != "default"
        assert vq._queue_dir(0).name != "default"
        assert vq._queue_dir("").name != vq._queue_dir(0).name
        assert vq._queue_dir("").name != vq._queue_dir(None).name
        # length-bounded: an unbounded session id must not blow the OS filename limit (A2)
        assert len(vq._queue_dir("x" * 5000).name) <= 61


# --- exactly-once under concurrency (§8 test 7) ------------------------------------

def test_concurrent_drain_consumes_each_once():
    with _temp_state():
        s = "race"
        texts = [f"u{i}" for i in range(8)]
        for i, t in enumerate(texts):
            vq.enqueue(t, ts=f"2026-06-21T10:00:{i:02d}+08:00", session=s)
        results = []
        lock = threading.Lock()
        n = 4
        barrier = threading.Barrier(n)

        def worker():
            barrier.wait()  # release all threads together to maximise O_EXCL claim contention
            got = vq.drain(100, session=s)
            with lock:
                results.extend(it["text"] for it in got)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # exactly-once: every utterance consumed once, none twice, none dropped
        assert sorted(results) == sorted(texts), results
        assert len(results) == len(texts)
        assert vq.is_empty(session=s)


def test_retained_marker_blocks_reconsume_and_no_ghost():
    with _temp_state():
        s = "marker"
        p = vq.enqueue("a", session=s)  # enqueue returns the utt file path
        assert [it["text"] for it in vq.drain(10, session=s)] == ["a"]  # consumed -> marker kept
        # stale-snapshot replay / crash-mid-claim: the same utt file is present while its
        # consumed-marker still exists. It must be treated as already-consumed — not
        # re-listed, not re-consumed, and NOT a permanent ghost that distorts is_empty /
        # peek_all (the marker is the single source of truth for consumed-ness) (F2).
        Path(p).write_text(
            json.dumps({"ts": "2026-06-21T10:00:00+08:00", "text": "a", "consumed": False}),
            encoding="utf-8")
        assert vq.drain(10, session=s) == []      # claim refused, not re-consumed
        assert vq.peek_all(session=s) == []        # ghost not surfaced
        assert vq.is_empty(session=s) is True      # is_empty not distorted


def test_archive_write_failure_does_not_abort_drain():
    # F1: a best-effort archive write that raises mid-claim must NOT abort the batch; the
    # claim (marker) is the exactly-once token, so the item is still consumed exactly once.
    with _temp_state():
        s = "writefail"
        vq.enqueue("a", ts="2026-06-21T10:00:00+08:00", session=s)
        vq.enqueue("b", ts="2026-06-21T11:00:00+08:00", session=s)
        orig_write = vq.os.write
        vq.os.write = lambda fd, data: (_ for _ in ()).throw(OSError("disk full (injected)"))
        try:
            drained = vq.drain(10, session=s)  # must NOT raise despite os.write failing
        finally:
            vq.os.write = orig_write
        assert sorted(it["text"] for it in drained) == ["a", "b"], drained
        assert vq.is_empty(session=s)  # sources still removed -> consumed exactly once


# --- watermark-anchored pop_latest (§8: no pre-question speech) --------------------

def test_pop_latest_watermark_iso():
    with _temp_state():
        s = "wm"
        vq.enqueue("old", ts="2026-06-21T10:00:00+08:00", session=s)
        vq.enqueue("mid", ts="2026-06-21T12:00:00+08:00", session=s)
        vq.enqueue("new", ts="2026-06-21T14:00:00+08:00", session=s)
        wm = "2026-06-21T11:00:00+08:00"  # between old and mid
        assert vq.pop_latest(watermark_ts=wm, session=s) == "new"   # newest after wm
        assert vq.pop_latest(watermark_ts=wm, session=s) == "mid"   # next newest after wm
        assert vq.pop_latest(watermark_ts=wm, session=s) is None    # old is before wm
        # the pre-watermark utterance is never returned by a watermarked pop
        assert [it["text"] for it in vq.peek_all(session=s)] == ["old"]


def test_pop_latest_watermark_epoch_float():
    with _temp_state():
        s = "wm-epoch"
        vq.enqueue("e_old", ts="2026-06-21T10:00:00+08:00", session=s)
        vq.enqueue("e_new", ts="2026-06-21T14:00:00+08:00", session=s)
        # §3.3 listen() may pass time.time() (epoch float) as the anchor
        epoch_wm = datetime.fromisoformat("2026-06-21T12:00:00+08:00").timestamp()
        assert vq.pop_latest(watermark_ts=epoch_wm, session=s) == "e_new"
        assert vq.pop_latest(watermark_ts=epoch_wm, session=s) is None  # e_old before wm


def test_pop_latest_bad_watermark_fails_closed():
    # F3: a watermark that was REQUESTED but is unparseable must fail closed (return None),
    # not silently degrade to no-anchor and risk returning pre-question speech. The queue
    # must be left intact so a later well-formed anchor can still consume it.
    with _temp_state():
        s = "bad-wm"
        vq.enqueue("hi", ts="2026-06-21T10:00:00+08:00", session=s)
        for bad in ("not-a-date", "", True, [], {}):
            assert vq.pop_latest(watermark_ts=bad, session=s) is None, bad
        assert [it["text"] for it in vq.peek_all(session=s)] == ["hi"]  # nothing consumed
        # a valid anchor still works afterwards
        assert vq.pop_latest(watermark_ts="2026-06-21T09:00:00+08:00", session=s) == "hi"


def test_pop_latest_no_watermark_returns_newest():
    with _temp_state():
        s = "no-wm"
        vq.enqueue("p1", ts="2026-06-21T10:00:00+08:00", session=s)
        vq.enqueue("p2", ts="2026-06-21T11:00:00+08:00", session=s)
        assert vq.pop_latest(session=s) == "p2"  # latest
        assert vq.pop_latest(session=s) == "p1"
        assert vq.pop_latest(session=s) is None


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
