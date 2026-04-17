"""Phase A smoke test for mem0 adapter. Verifies all four P1-bug guards.

Run after `pip install -r requirements-mem0.txt` and setting $OPENAI_API_KEY.
Exit code 0 = all guards pass, non-zero = regression.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mem0_hooks import add_safe, reset_for_tests, search_safe  # noqa: E402

USER = "mercury-smoke"
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    mark = "OK  " if cond else "FAIL"
    print(f"{mark} {label}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set — abort")
        return 2
    reset_for_tests()

    # guard #4099: empty payload must not hallucinate
    check(add_safe("", user_id=USER) is None, "#4099 empty string rejected")
    check(add_safe("   \n  ", user_id=USER) is None, "#4099 whitespace rejected")

    # guard #4799: list content coerced, no AttributeError
    list_input = [{"content": "Mercury runs on Windows 11"}, {"content": "uses pnpm"}]
    try:
        r = add_safe(list_input, user_id=USER, skip_dedup=True)
        check(r is not None, "#4799 list coerced -> str and stored")
    except AttributeError as exc:
        check(False, f"#4799 list raised AttributeError: {exc}")

    # guard #4453: search_safe never forwards threshold kwarg
    results = search_safe("Mercury Windows", user_id=USER, limit=3)
    check(isinstance(results, list), "#4453 search returns list without threshold")

    # guard #4536: dedup reject near-duplicate
    seed = "Mercury branch model is develop->master"
    add_safe(seed, user_id=USER, skip_dedup=True)
    hits = search_safe(seed, user_id=USER, limit=3)
    top = max((h.get("score", 0) for h in hits), default=0)
    print(f"     dedup probe: top score for identical text = {top:.4f}")
    again = add_safe(seed, user_id=USER)
    check(again is None, f"#4536 dedup_guard rejected near-duplicate (again={again!r})")

    print(f"\nresult: {len(FAILURES)} failure(s)")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    raise SystemExit(main())
