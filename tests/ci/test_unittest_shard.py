"""scripts/ci/unittest_shard.py: the parts of a sharded test module run every test exactly once (#806)."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))
import unittest_shard  # noqa: E402


def _suite(n):
    cases = [type(f"Case{c}", (unittest.TestCase,), {f"test_{i}": lambda self: None for i in range(n)}) for c in "AB"]
    return unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in cases)


def _ids(suite):
    return [case.id() for case in unittest_shard._cases(suite)]


class Shard(unittest.TestCase):
    def test_the_parts_together_run_every_test_exactly_once(self):
        whole = _ids(_suite(7))
        for n in (1, 2, 3, 5, 20):
            parts = [_ids(unittest_shard.select(_suite(7), f"{k}/{n}")) for k in range(1, n + 1)]
            self.assertEqual(sorted(i for p in parts for i in p), sorted(whole), n)
            self.assertLessEqual(max(map(len, parts)) - min(map(len, parts)), 1, n)

    def test_unset_or_empty_keeps_every_test(self):
        for spec in ("", "  "):
            self.assertEqual(_ids(unittest_shard.select(_suite(3), spec)), _ids(_suite(3)))

    def test_a_malformed_value_is_an_error(self):
        for spec in ("0/2", "3/2", "1", "1/0", "a/b", "1/2/3", "-1/2", "1 /2"):
            with self.assertRaises(ValueError, msg=spec):
                unittest_shard.select(_suite(3), spec)


class WorkflowParts(unittest.TestCase):
    """The coverage checker counts a `discover` run as the whole file and cannot see a missing part, so this does:
    every job that sets MERCURY_TEST_SHARD=${{ matrix.shard }}/N has the matrix shard: [1, ..., N]."""

    def test_every_sharded_job_runs_all_of_its_parts(self):
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location("check_test_coverage", root / "scripts/ci/check-test-coverage.py")
        checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(checker)
        found = 0
        for wf in sorted((root / ".github/workflows").glob("*.y*ml")):
            doc = checker.parse_yaml(wf.read_text(encoding="utf-8"))
            self.assertNotIn(unittest_shard.ENV, doc.get("env") or {}, f"{wf.name}: set it on the job, not the workflow")
            for jid, job in (doc.get("jobs") or {}).items():
                for step in job.get("steps") or []:
                    self.assertNotIn(unittest_shard.ENV, str(step.get("run", "")), f"{wf.name} {jid}: set it in job env")
                for where in [job] + list(job.get("steps") or []):
                    value = (where.get("env") or {}).get(unittest_shard.ENV)
                    if value is None:
                        continue
                    found += 1
                    m = re.fullmatch(r"\$\{\{\s*matrix\.shard\s*\}\}/([1-9][0-9]*)", value)
                    self.assertTrue(m and where is job, f"{wf.name} {jid}: set {unittest_shard.ENV} on the job as "
                                    f"${{{{ matrix.shard }}}}/N, got {value!r}")
                    matrix = (job.get("strategy") or {}).get("matrix") or {}
                    self.assertFalse({"include", "exclude"} & set(matrix), f"{wf.name} {jid}: include/exclude would "
                                     "change which parts run")
                    shards = matrix.get("shard")
                    self.assertEqual(shards, [str(k) for k in range(1, int(m.group(1)) + 1)], f"{wf.name} {jid}")
        self.assertGreater(found, 0)


if __name__ == "__main__":
    unittest.main()
