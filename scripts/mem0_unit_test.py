"""Offline unit tests for mem0 adapter helpers.

Exercises pure functions that do NOT require OpenAI or Qdrant. Run this as
the first gate after install; full smoke test (mem0_smoke_test.py) runs
afterwards with OPENAI_API_KEY set.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mem0_hooks  # noqa: E402
import mem0_migrate  # noqa: E402

FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    mark = "OK  " if cond else "FAIL"
    print(f"{mark} {label}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    # _coerce_str: accepted shapes
    check(mem0_hooks._coerce_str("plain") == "plain", "coerce: str passthrough")
    check(mem0_hooks._coerce_str(["a", "b"]) == "a\nb", "coerce: list of str")
    check(
        mem0_hooks._coerce_str([{"content": "x"}, {"content": "y"}]) == "x\ny",
        "coerce: list of dicts with content key",
    )
    check(mem0_hooks._coerce_str([]) == "", "coerce: empty list")

    # _coerce_str: rejected shapes return None (never stringify junk)
    check(mem0_hooks._coerce_str(None) is None, "coerce: None rejected")
    check(mem0_hooks._coerce_str(b"bytes") is None, "coerce: bytes rejected")
    check(mem0_hooks._coerce_str(bytearray(b"x")) is None, "coerce: bytearray rejected")
    check(mem0_hooks._coerce_str((x for x in ["a"])) is None, "coerce: generator rejected")
    check(mem0_hooks._coerce_str(42) is None, "coerce: int rejected")

    # _coerce_str: accepted container shapes
    check(
        mem0_hooks._coerce_str({"content": "x"}) == "x",
        "coerce: dict with content key",
    )
    check(mem0_hooks._coerce_str({"other": 1}) is None, "coerce: dict without content rejected")
    check(mem0_hooks._coerce_str(("a", "b")) == "a\nb", "coerce: tuple of str")

    # _build_config shape + paths resolve
    cfg = mem0_hooks._build_config()
    check("vector_store" in cfg, "config: has vector_store")
    check(cfg["vector_store"]["provider"] == "qdrant", "config: qdrant provider")
    check("path" in cfg["vector_store"]["config"], "config: qdrant path set")
    check(Path(cfg["history_db_path"]).parent.exists(), "config: history dir exists")

    # telemetry env bootstrapped at module import time
    check(os.environ.get("MEM0_TELEMETRY") == "false", "env: MEM0_TELEMETRY set")
    check(
        os.environ.get("ANONYMIZED_TELEMETRY") == "false",
        "env: ANONYMIZED_TELEMETRY set",
    )

    # _parse_frontmatter
    meta, body = mem0_migrate._parse_frontmatter("no frontmatter here")
    check(meta == {} and body == "no frontmatter here", "frontmatter: absent ignored")
    meta, body = mem0_migrate._parse_frontmatter("---\ntitle: t\ntags: a\n---\nbody")
    check(meta == {"title": "t", "tags": "a"} and body == "body", "frontmatter: parsed")
    meta, body = mem0_migrate._parse_frontmatter("---\nbroken")
    check(meta == {} and "broken" in body, "frontmatter: malformed falls through")
    meta, body = mem0_migrate._parse_frontmatter("---\n---\nbody")
    check(meta == {} and body == "body", "frontmatter: empty block stripped")
    meta, body = mem0_migrate._parse_frontmatter(
        "---\nparent:\n  child: value\ntags:\n- a\ntitle: real\n---\nx"
    )
    check(
        meta == {"title": "real"} and body == "x",
        "frontmatter: container keys dropped, scalar kept",
    )

    print(f"\nresult: {len(FAILURES)} failure(s)")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
