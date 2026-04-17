"""Mercury adapter for mem0 self-hosted memory layer.

Wraps mem0ai.Memory with four mandatory guards against known P1 bugs:
- #4099 empty-payload hallucination -> add_safe refuses empty content
- #4799 list-content AttributeError -> add_safe coerces list -> str
- #4453 threshold filtering broken  -> search_safe never forwards threshold
- #4536 contradicting-facts silent corruption -> dedup_guard cosine reject

Preconditions (see research doc memory-layer-rebuild-2026-04-16.md):
single-user runtime, string content only, non-Gemini-3 models, telemetry off.
"""

from __future__ import annotations

import os

# Must run before anything that could trigger a `mem0` import anywhere in the
# process; mem0's PostHog telemetry reads these at module-load time. Force
# (not setdefault) so a parent process that exported MEM0_TELEMETRY=true
# cannot re-enable PostHog reporting behind our back.
os.environ["MEM0_TELEMETRY"] = "false"
os.environ["ANONYMIZED_TELEMETRY"] = "false"

import json  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any  # noqa: E402

_DEFAULT_USER = "mercury"
_DEDUP_THRESHOLD = 0.92

_memory_singleton: Any = None
_lock = threading.Lock()


def _default_qdrant_path() -> str:
    root = os.environ.get("MERCURY_MEM0_QDRANT_PATH")
    if root:
        return root
    base = Path(__file__).resolve().parents[1] / ".mercury" / "state" / "mem0" / "qdrant"
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def _default_history_path() -> str:
    root = os.environ.get("MERCURY_MEM0_HISTORY_PATH")
    if root:
        return root
    base = Path(__file__).resolve().parents[1] / ".mercury" / "state" / "mem0"
    base.mkdir(parents=True, exist_ok=True)
    return str(base / "history.db")


def _build_config() -> dict[str, Any]:
    override = os.environ.get("MERCURY_MEM0_CONFIG")
    if override:
        base_dir = Path(__file__).resolve().parents[1]
        cfg_path = Path(override).expanduser().resolve()
        # Bad JSON is a caller mistake — raise loudly rather than mask with
        # defaults; path / missing-file issues warn and fall through.
        try:
            cfg_path.relative_to(base_dir)
        except ValueError:
            print(
                f"[mem0_hooks] WARNING: MERCURY_MEM0_CONFIG={override} is outside"
                f" the repo ({base_dir}); using defaults.",
                file=sys.stderr,
            )
        else:
            if cfg_path.is_file():
                return json.loads(cfg_path.read_text(encoding="utf-8"))
            print(
                f"[mem0_hooks] WARNING: MERCURY_MEM0_CONFIG={override} not found;"
                " using defaults.",
                file=sys.stderr,
            )
    return {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "mercury",
                "path": _default_qdrant_path(),
                "on_disk": True,
            },
        },
        "history_db_path": _default_history_path(),
    }


def get_memory() -> Any:
    global _memory_singleton
    if _memory_singleton is not None:
        return _memory_singleton
    with _lock:
        if _memory_singleton is None:
            from mem0 import Memory  # type: ignore

            _memory_singleton = Memory.from_config(_build_config())
    return _memory_singleton


def _coerce_str(content: Any) -> str | None:
    """Return a safe string for mem0, or None if the input should be rejected.

    Accepted: str, dict (pulls "content" key), list/tuple of the above.
    Rejected (returns None): None, bytes, bytearray, set (iteration order is
    non-deterministic → memory contents would drift across runs), and any
    other shape — generators / arbitrary Iterables are refused so we never
    silently consume a large stream or mis-concatenate unexpected objects.
    """
    if content is None:
        return None
    if isinstance(content, (bytes, bytearray)):
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "content" in content:
            return str(content["content"])
        return None
    if isinstance(content, (list, tuple)):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "content" in item:
                piece = item["content"]
                if isinstance(piece, str):
                    parts.append(piece)
                else:
                    # nested non-string content — reject whole container rather
                    # than silently stringify None/bytes/objects into the memory
                    return None
            else:
                return None
        return "\n".join(p for p in parts if p)
    return None


def dedup_guard(content: str, user_id: str) -> bool:
    """Return True if content is novel enough to add, False otherwise.

    Fails CLOSED: if the dedup search itself errors, we skip the add so a
    broken search layer cannot silently re-enable #4536 corruption.
    """
    try:
        existing = search_safe(content, user_id=user_id, limit=3)
    except Exception as exc:
        print(f"[mem0_hooks] dedup_guard search failed, skipping add: {exc}")
        return False
    for hit in existing or []:
        score = hit.get("score") if isinstance(hit, dict) else None
        if isinstance(score, (int, float)) and score >= _DEDUP_THRESHOLD:
            return False
    return True


def add_safe(
    content: Any,
    user_id: str = _DEFAULT_USER,
    metadata: dict[str, Any] | None = None,
    skip_dedup: bool = False,
) -> Any:
    """Safe wrapper around Memory.add(). Returns result dict, or None if skipped."""
    coerced = _coerce_str(content)
    if coerced is None:
        return None
    text = coerced.strip()
    if not text:
        return None
    if not skip_dedup and not dedup_guard(text, user_id=user_id):
        return None
    mem = get_memory()
    return mem.add(text, user_id=user_id, metadata=metadata or {})


def search_safe(
    query: str,
    user_id: str = _DEFAULT_USER,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Safe wrapper around Memory.search(). Never passes threshold (bug #4453)."""
    if not query or not query.strip():
        return []
    mem = get_memory()
    result = mem.search(query=query, user_id=user_id, limit=limit)
    if isinstance(result, dict):
        rows = result.get("results", [])
        return rows if isinstance(rows, list) else []
    if isinstance(result, list):
        return result
    print(f"[mem0_hooks] unexpected search() return type: {type(result).__name__}")
    return []


def reset_for_tests() -> None:
    global _memory_singleton
    with _lock:
        _memory_singleton = None
