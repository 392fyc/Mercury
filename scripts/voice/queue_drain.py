# -*- coding: utf-8 -*-
"""queue_drain — Stop-hook worker: re-inject voice spoken DURING the turn (#495 Slice 5).

At a turn boundary, drain the per-session transcript queue (what the user said via the
always-on daemon while the agent was working) and emit `{"decision":"block","reason":...}`
exit 0 — the verified form (auto-handoff-stop.sh:129, stop-guard.sh) that makes Claude Code
continue the turn with that speech in context, closing the "things said during the previous
turn are lost" gap.

§8 loop-correctness (the hard merge gate): relying on `stop_hook_active` ALONE to break the
block loop would strand any utterance spoken DURING the continuation turn (it is guarded out
and may never be delivered in work-mode). So this worker OWNS its own continuation counter
(mirroring auto-handoff-stop.sh's RETRY marker, which deliberately AVOIDS stop_hook_active):
it re-blocks on a NEW non-empty queue even when stop_hook_active is true, but caps consecutive
auto-continuations (VOICE_QUEUE_MAX_CONTINUATIONS, default 3) so room noise / the agent's own
TTS picked up by the mic cannot spin forever. At the cap it STOPS blocking and LEAVES the items
queued (NOT consumed) for the next user-initiated turn, logging a deferral nudge.

Self-guards (all exit 0 — a hook must never crash the turn): venv handled by the wrapper;
mode == idle -> no-op; empty queue -> no-op; any error -> no-op.

Text-only: NO synchronous TTS here — a slow Kokoro synth could blow the hook timeout (§8);
the block reason is plain text Claude reads. `reason` is json.dumps-serialised (Chinese /
quotes / backslashes safe), never string-concatenated into JSON.

Invoked by .claude/hooks/voice-queue-drain.sh (opt-in; NOT registered by default — see
scripts/voice/README.md for the settings.json Stop-matcher snippet).
"""
import os
import sys
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state as _state          # noqa: E402
import voice_queue as _vq       # noqa: E402


def _env_int(name, default):
    try:
        v = int(os.environ.get(name, "") or default)
        return v if v > 0 else int(default)
    except (TypeError, ValueError):
        return int(default)


def _resolve_session(payload):
    """env VOICE_QUEUE_SESSION (shared with the daemon) wins; else the Stop-hook's session_id
    from stdin (the per-session key §8 prescribes); else None -> the default bucket."""
    return os.environ.get("VOICE_QUEUE_SESSION") or payload.get("session_id") or None


def _cont_path(session):
    # keyed by the SAME resolved session as the queue dir, so the counter tracks the right queue
    return _state._state_dir() / f"voice-queue-cont-{_vq._resolve_session(session)}"


def _read_count(session):
    try:
        return int((_cont_path(session).read_text(encoding="utf-8").strip() or "0"))
    except (OSError, ValueError):
        return 0


def _write_count(session, n):
    try:
        _state._atomic_write(_cont_path(session), str(int(n)))
    except OSError:
        pass


def _reset_count(session):
    try:
        _cont_path(session).unlink()
    except OSError:
        pass


def _counter_fresh(session, window):
    """True iff the continuation counter file exists AND is fresh (mtime within `window`). A
    chain older than the window (the agent went idle, or a prior session left it) is stale and
    must NOT be inherited — mirrors auto-handoff-stop.sh's mtime-staleness disarm (line 54).
    This is the OWN marker: the loop guard does not rely on stop_hook_active alone (§8)."""
    try:
        return (time.time() - _cont_path(session).stat().st_mtime) <= window
    except OSError:
        return False


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        if _state.get_mode() == "idle":
            return 0  # voice interaction not active — no-op
    except Exception:  # noqa: BLE001 — a hook must never crash the turn
        return 0

    session = _resolve_session(payload)
    try:
        # benign TOCTOU: a daemon enqueue landing AFTER this is_empty() check is simply delivered
        # on the NEXT Stop, not lost — drain() below is the atomic per-session consume (its
        # `not items` branch also covers the reverse race), so the early-return only widens a
        # one-turn defer window, never drops speech.
        if _vq.is_empty(session):
            _reset_count(session)  # clean turn: nothing was said -> reset the continuation chain
            return 0
        cap = _env_int("VOICE_QUEUE_MAX_CONTINUATIONS", 3)
        # Carry the continuation count forward ONLY if this is a continuation of OUR OWN chain:
        # the harness says we're mid-continuation (stop_hook_active) AND our marker is fresh
        # (mtime within the window). A fresh user turn (stop_hook_active falsy), a stale/idle
        # chain, or a continuation another hook caused (our marker absent/old) all reset to 0,
        # so the loop guard never depends on stop_hook_active alone (§8 / Codex).
        window = _env_int("VOICE_QUEUE_CONT_WINDOW", 120)
        in_own_chain = bool(payload.get("stop_hook_active")) and _counter_fresh(session, window)
        count = _read_count(session) if in_own_chain else 0
        if count >= cap:
            # too many consecutive auto-continuations (likely room noise / echo loop). STOP
            # blocking; LEAVE the items queued (do NOT drain/consume) so a later user turn still
            # delivers them — never silently drop the user's speech (§8).
            pending = len(_vq.peek_all(session))
            _reset_count(session)
            print(f"[voice-queue-drain] continuation cap ({cap}) reached — {pending} utterance(s) "
                  "deferred to the next user-initiated turn", file=sys.stderr, flush=True)
            return 0
        items = _vq.drain(_env_int("VOICE_QUEUE_MAX_ITEMS", 5), session)
        if not items:
            _reset_count(session)  # raced empty between is_empty and drain
            return 0
        _write_count(session, count + 1)
        transcript = "\n".join(f"- {it.get('text', '')}" for it in items)
        # bound the injected text: max_items caps the COUNT, but an individual transcription can
        # be long — cap total chars so the Stop-hook block output can't bloat the context / strain
        # the hook (Argus). Truncated content is still in the queue's consumed/ archive.
        cap_chars = _env_int("VOICE_QUEUE_DRAIN_MAX_CHARS", 1000)
        if len(transcript) > cap_chars:
            transcript = transcript[:cap_chars] + "……（其余略）"
        reason = ("用户在你上一回合执行期间通过语音补充了以下内容（按时间顺序），"
                  "请在继续前纳入考虑：\n" + transcript)
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001 — a hook must never crash the turn
        print(f"[voice-queue-drain] error (ignored): {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
