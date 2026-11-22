"""Run one part of a unittest module, so CI can spread a slow test file over parallel jobs (Mercury #806).

A test module opts in with one import, which gives it unittest's own `load_tests` hook:

    sys.path.insert(0, str(ROOT / "scripts" / "ci"))
    from unittest_shard import load_tests  # noqa: E402,F401

`MERCURY_TEST_SHARD=K/N` (1 <= K <= N) then keeps every N-th test of the module, starting at the K-th, in the
order unittest loads them (sorted class and method names, so every job sees the same order). The N parts together
run every test exactly once. Unset or empty keeps every test, so a local run is unchanged. A malformed value is an
error, not "run everything", so a typo in a workflow cannot silently run a part twice or not at all. A module that
does not opt in runs whole in every part: its tests are repeated, never lost.
"""
from __future__ import annotations

import os
import re
import unittest

ENV = "MERCURY_TEST_SHARD"


def parse(spec: str) -> tuple[int, int] | None:
    """(K, N) from "K/N"; None for an unset or empty value; ValueError for anything else."""
    spec = spec.strip()
    if not spec:
        return None
    m = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", spec)
    if not m or int(m.group(1)) > int(m.group(2)):
        raise ValueError(f"{ENV} must be K/N with 1 <= K <= N, got {spec!r}")
    return int(m.group(1)), int(m.group(2))


def _cases(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _cases(item)
        else:
            yield item


def select(tests: unittest.TestSuite, spec: str) -> unittest.TestSuite:
    part = parse(spec)
    if part is None:
        return tests
    k, n = part
    return unittest.TestSuite(case for i, case in enumerate(_cases(tests)) if i % n == k - 1)


def load_tests(loader, tests, pattern):
    return select(tests, os.environ.get(ENV, ""))
