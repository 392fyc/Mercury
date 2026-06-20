# -*- coding: utf-8 -*-
"""voice_queue — per-session transcript FIFO with atomic enqueue + exactly-once drain.

Issue #495 Path 2 Slice 2. The always-on STT daemon (Slice 3) writes each finalized
utterance here; the Stop-hook drain (Slice 5) and `listen()` (model A) read from here.
Design: `.mercury/docs/research/issue-495-voice-conversation-design.md` §3.2 (format/API)
+ §8 (the adversarial-review merge gates this module bakes in).

Why a NEW module and not `state.record_note`: record_note is an append-only markdown
note (no consume cursor, no atomic dequeue). #495 needs a FIFO with a consume pointer.
This reuses state's proven atomic-write + state-dir primitives (design §3.2).

Why NOT named `queue.py`: `scripts/voice/` is on `sys.path[0]`, so a `queue.py` here
would shadow the stdlib `queue` that `stt.py` imports (design R10).

Hardening baked in from the §8 adversarial review (each is a merge gate, not optional):
  - Atomic enqueue: write a `.tmp-*.json` temp + `os.replace`, so a concurrent reader
    NEVER sees a half-written file (reuses `state._atomic_write`).
  - Pinned glob `utt-*.json`: the in-flight `.tmp-*.json` temp and the `consumed/`
    archive subdir are excluded by name — a half-write can't be read as a queue item.
  - Per-file resilience: a single corrupt / partial / vanished file is SKIPPED, never
    aborting the whole batch (a batch-level abort would silently drop every utterance).
  - Per-session scoping: Mercury runs concurrent lanes sharing `.mercury/state`, so the
    queue dir is keyed by session — session A's drain can't race session B's items.
  - Exactly-once dequeue: claim via an O_CREAT|O_EXCL exclusive create of the consumed
    marker (the atomic claim token). NOTE — this deviates from the design's §3.2 prose
    ("os.replace == I own it"): os.replace / os.rename are NOT exactly-once on Windows
    under concurrency (empirically, several concurrent movers of one source all
    "succeed" → double-consume; O_EXCL create yields exactly one winner of N racers on
    local NTFS). A lost claim (FileExistsError/OSError) skips that item and keeps
    draining — never double-consumes, never aborts the batch.
  - Time-anchored `pop_latest(watermark_ts)`: returns only utterances with ts strictly
    after the watermark, so `listen()` never returns speech said BEFORE the question.

Config (env):
    VOICE_STATE_DIR       state dir (default <repo>/.mercury/state); shared with state.py
    VOICE_QUEUE_SESSION   default session key when none is passed explicitly
"""
import os
import re
import sys
import json
import hashlib
import itertools
from datetime import datetime, timezone

# this file is scripts/voice/voice_queue.py — import flat siblings (sys.path[0] == voice/),
# matching mcp_server.py's convention so `import state` resolves however we're loaded.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state as _state  # noqa: E402  (reuse _atomic_write + _state_dir — design §3.2)

# per-process monotonic counter: disambiguates two enqueues that land in the same clock
# tick. Windows datetime.now() can be coarse (~1ms), so a microsecond stamp alone is NOT
# collision-free; the zero-padded seq makes the filename unique AND preserves insertion
# order as a same-ts tiebreak when sorting lexicographically. This gives strict FIFO within
# ONE producer process — model A runs a SINGLE daemon per session as the sole producer, so
# cross-process global FIFO is out of scope; were multiple producer processes ever to share
# a session, same-microsecond items would order by filename (an accepted boundary, not a
# regression) (Argus FIFO-boundary finding).
_SEQ = itertools.count()
_SESSION_SANITIZE = re.compile(r"[^A-Za-z0-9._-]")
# aware sentinel so entries with an unparseable ts still sort (as oldest) without raising
_MIN_DT = datetime.min.replace(tzinfo=timezone.utc)


def _safe_session(raw):
    """Map a (semi-trusted) session id to a safe, bounded, collision-free path component.

    The id may arrive from a Stop-hook stdin payload or an env var. Three properties:
      - traversal containment: chars outside [A-Za-z0-9._-] -> `_`, leading/trailing
        dots/underscores stripped, so `../../etc` cannot escape the voice-queue dir.
      - bounded length: the readable slug is capped at 48 chars — an unbounded id would
        otherwise blow the OS filename-length limit on mkdir/open into a controllable DoS
        (Argus security finding).
      - injective mapping: sanitising/capping is lossy, and per-session isolation is a
        SAFETY property (§8 — a shared dir is cross-session contamination), so a short hash
        of the RAW id is appended whenever anything changed. An already-safe, short id
        (e.g. a UUID) maps to itself verbatim, keeping dir names readable.
    """
    raw = str(raw)
    slug = _SESSION_SANITIZE.sub("_", raw).strip("._")[:48].strip("._")
    if slug == raw and slug:
        return slug  # already a safe, short single component (e.g. a UUID) — verbatim
    digest = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{slug}-{digest}" if slug else f"s-{digest}"


def _resolve_session(session=None):
    # Only an OMITTED session (None) falls back to env/default. ANY explicitly-passed value
    # — even a falsy one like "" / 0 — goes through _safe_session, so two distinct explicit
    # sessions can never be silently merged into one queue dir (cross-session safety; F1).
    if session is not None:
        return _safe_session(session)
    env = os.environ.get("VOICE_QUEUE_SESSION")
    return _safe_session(env) if env else "default"


def _queue_dir(session=None):
    d = _state._state_dir() / "voice-queue" / _resolve_session(session)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _consumed_dir_path(session=None):
    """Return the consumed/ archive path. Does NOT create consumed/ itself, so the read-only
    listing marker-check has no side effect (the parent queue dir is ensured by _queue_dir)."""
    return _queue_dir(session) / "consumed"


def _consumed_dir(session=None):
    d = _consumed_dir_path(session)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now_iso():
    """Current local time as a microsecond-precision ISO8601 string (capture anchor)."""
    return datetime.now(timezone.utc).astimezone().isoformat()


def _parse_ts(value):
    """Normalise an ISO8601 string / epoch float / datetime to an aware datetime, else None.

    Used for ordering and watermark comparison. Comparing aware datetimes is correct across
    timezone offsets — lexicographic string compare is NOT, so we always parse first.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, bool):  # bool is an int subclass — reject before the number branch
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if s[-1:] in ("Z", "z"):
        s = s[:-1] + "+00:00"  # normalise UTC 'Z' suffix (fromisoformat rejects it pre-3.11)
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _to_iso(ts):
    """Normalise a caller-supplied ts (capture-onset time) to ISO8601, else use now()."""
    dt = _parse_ts(ts)
    return dt.isoformat() if dt is not None else _now_iso()


def _list_entries(session=None):
    """Return [(path, data, ts_dt)] of unconsumed utterances, oldest first.

    Pinned to `utt-*.json` (excludes the `.tmp-*.json` enqueue temp and the `consumed/`
    archive subdir, which `Path.glob` does not recurse into). Each file is parsed
    independently: a corrupt / half-written / mid-read-vanished file is skipped, NEVER
    aborting the listing (§8 — a batch-level abort would drop every queued utterance).

    An utterance whose consumed-marker already exists is treated as ALREADY consumed and
    skipped — the marker is the single source of truth for consumed-ness. So a crash (or
    os.remove failure) between claiming and removing the source leaves a harmless orphan
    file on disk, NOT a permanently-stuck ghost that distorts peek_all/is_empty (F2).
    """
    entries = []
    cdir = _consumed_dir_path(session)
    for p in _queue_dir(session).glob("utt-*.json"):
        if not p.is_file():
            continue
        if (cdir / p.name).exists():
            continue  # consumed-marker present -> already claimed; never re-list (F2)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            continue  # partial / corrupt / vanished -> leave for a later pass, skip this one
        if not isinstance(data, dict):
            continue
        text = data.get("text")
        if "ts" not in data or not isinstance(text, str) or not text.strip():
            continue  # malformed: missing ts or no usable string text -> skip, never list (C1)
        entries.append((p, data, _parse_ts(data.get("ts"))))
    # primary: ts (aware); secondary: filename (zero-padded seq) so same-ts items keep
    # insertion order. Unparseable ts sorts oldest via the aware _MIN_DT sentinel.
    entries.sort(key=lambda it: (it[2] or _MIN_DT, it[0].name))
    return entries


def enqueue(text, ts=None, session=None):
    """Atomically append one utterance; return its file path, or None for empty text.

    `ts` is the capture-onset time (ISO8601 / epoch / datetime); defaults to now(). The
    write is atomic (`.tmp-*.json` temp + os.replace) so a concurrent drain never reads a
    half-written file. The filename carries pid + a monotonic seq, so two enqueues in the
    same clock tick never os.replace-clobber each other.
    """
    text = (text or "").strip()
    if not text:
        return None
    qdir = _queue_dir(session)
    payload = json.dumps({"ts": _to_iso(ts), "text": text, "consumed": False}, ensure_ascii=False)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    name = f"utt-{stamp}-{os.getpid()}-{next(_SEQ):06d}.json"
    path = qdir / name
    _state._atomic_write(path, payload)  # mkstemp(.tmp-*.json) + os.replace — shared primitive
    return str(path)


def peek_all(session=None):
    """List unconsumed utterances (dicts), oldest first. Does NOT consume."""
    return [data for _, data, _ in _list_entries(session)]


def is_empty(session=None):
    """True when no (parseable) unconsumed utterance remains — the drain hook's load guard."""
    return not _list_entries(session)


def _claim(path, session=None):
    """Atomically claim one utterance; True iff THIS caller won it (exactly-once).

    The claim token is an O_CREAT|O_EXCL exclusive create of the consumed-marker — a real
    atomic test-and-set on local NTFS (verified: exactly one winner of N concurrent
    racers). os.replace/os.rename are NOT exactly-once on Windows under concurrency, so
    they are deliberately NOT used here. The winner archives the payload into the marker
    and removes the source from the live queue. A lost claim (FileExistsError) or any
    OSError returns False so the caller skips this item and keeps draining — never
    double-consumes, never aborts the batch.

    The marker is RETAINED, never deleted: it is the token a consumer holding a stale
    pre-removal snapshot hits (FileExistsError) to correctly skip an already-claimed item.
    Deleting it would reopen a double-consume window. Filenames are per-process unique
    (microsecond + pid + monotonic seq), so a retained marker can never false-reject a
    future utterance. consumed/ growth is bounded per session; pruning is a session-scoped
    janitor concern (out of Slice-2 scope).
    """
    dest = _consumed_dir(session) / path.name
    try:
        # O_BINARY (Windows-only; 0 elsewhere) keeps the archived payload byte-exact
        fd = os.open(str(dest), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0))
    except FileExistsError:
        return False  # another consumer already claimed this exact utterance -> skip
    except OSError:
        return False  # vanished dir / perms -> treat as a lost claim, never abort the batch
    # We won the claim: the marker's existence is the exactly-once token. Everything below
    # is best-effort — archiving the payload / removing the source must never raise out of
    # here and abort the rest of the batch.
    try:
        try:
            data = path.read_bytes()  # archive the consumed payload (best-effort)
            off = 0  # os.write may write fewer bytes than requested -> loop until all written
            while off < len(data):
                written = os.write(fd, data[off:])
                if not written:
                    break  # defensive: don't spin if the fd refuses further progress
                off += written
        except OSError:
            pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(str(path))  # drop from the live queue (best-effort; marker is truth)
        except OSError:
            pass
    return True


def drain(max_items, session=None):
    """Consume up to `max_items` oldest utterances; return the claimed dicts, oldest first.

    Each item is claimed via an exclusive consumed-marker (O_CREAT|O_EXCL) then best-effort
    removed from the live queue — NOT an os.replace move; an item lost to a concurrent
    consumer is skipped (never double-consumed, never aborting the batch). `max_items` is a
    boundary input (e.g. an env var), so it is coerced to int and a non-int / non-positive
    value is a no-op rather than a TypeError that would abort consumption.
    """
    try:
        max_items = int(max_items)
    except (TypeError, ValueError):
        return []
    if max_items <= 0:
        return []
    claimed = []
    for p, data, _ in _list_entries(session):
        if len(claimed) >= max_items:
            break
        if _claim(p, session):
            data["consumed"] = True  # in-memory only: reflect that THIS call consumed it
            claimed.append(data)
    return claimed


def pop_latest(watermark_ts=None, session=None):
    """Consume + return the text of the most-recent utterance strictly after `watermark_ts`.

    `watermark_ts` (ISO8601 / epoch float / datetime) anchors listen() to its question:
    utterances enqueued at or before it are NOT returned, so listen() never picks up speech
    the user said before being asked. None -> no anchor (returns the latest overall).
    A watermark that was REQUESTED but is unparseable fails CLOSED (returns None) rather
    than silently degrading to no-anchor — never risk returning pre-question speech (F3).
    Returns None when nothing qualifies. A lost race falls through to the next-newest.
    """
    entries = _list_entries(session)
    if watermark_ts is not None:
        wm = _parse_ts(watermark_ts)
        if wm is None:
            return None  # anchor requested but uninterpretable -> fail closed (gate #6)
        entries = [it for it in entries if it[2] is not None and it[2] > wm]
    for p, data, _ in reversed(entries):  # newest first
        if _claim(p, session):
            return data.get("text")
    return None
