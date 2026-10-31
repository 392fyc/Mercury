"""Tests of packs/kb/scripts/kb_check.py (Mercury #736): each convention it enforces, on a throw-away KB."""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "packs/kb/scripts/kb_check.py"


class KbCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mercury-kb-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "docs"
        self.write("README.md", """\
            # Entry
            Rules: [rules](01-rules/README.md). History: [archive](90-archive/README.md).

            | Directory | Purpose |
            |---|---|
            | [01-rules](01-rules/README.md) | current rules |
            | [90-archive](90-archive/README.md) | history |
            """)
        self.write("01-rules/README.md", """\
            # Rules

            | Document | Status | Superseded by |
            |---|---|---|
            | [principles.md](principles.md) | current | |
            | [old.md](old.md) | superseded | [principles.md](principles.md) |
            """)
        self.write("01-rules/principles.md", "# Principles\n")
        self.write("01-rules/old.md", "# Old\n")
        self.write("90-archive/README.md", """\
            | 文档 | 状态 |
            |---|---|
            | [plan.md](plan.md) | 历史 |
            """)
        self.write("90-archive/plan.md", "# Plan\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")

    def run_check(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.root)], capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        return result.returncode, result.stdout

    def assert_problem(self, fragment):
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn(fragment, out)

    def test_a_conforming_kb_passes(self):
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.assertIn("kb-check: OK (3 document(s) in 3 director(ies))", out)

    def test_a_directory_without_readme_fails(self):
        self.write("04-research/a.md", "# A\n")
        self.assert_problem("04-research: no README.md index")

    def test_an_unlisted_document_fails(self):
        self.write("01-rules/extra.md", "# Extra\n")
        self.assert_problem("01-rules/README.md: extra.md is not listed in the index")

    def test_an_unknown_or_missing_status_fails(self):
        text = (self.root / "01-rules/README.md").read_text(encoding="utf-8")
        (self.root / "01-rules/README.md").write_text(text.replace("| current |", "| active |"), encoding="utf-8")
        self.assert_problem("principles.md has no valid status (active)")

    def test_a_superseded_document_needs_an_existing_replacement(self):
        text = (self.root / "01-rules/README.md").read_text(encoding="utf-8")
        (self.root / "01-rules/README.md").write_text(text.replace("[principles.md](principles.md) |\n", "|\n"),
                                                      encoding="utf-8")
        self.assert_problem("superseded old.md names no existing replacement")

    def test_the_entry_page_must_not_link_superseded_documents(self):
        self.write("README.md", (self.root / "README.md").read_text(encoding="utf-8")
                   + "\nSee also [the old rules](01-rules/old.md).\n")
        self.assert_problem("README.md: entry page links superseded document 01-rules/old.md")

    def test_indexes_outside_the_archive_link_only_its_readme(self):
        self.write("README.md", (self.root / "README.md").read_text(encoding="utf-8")
                   + "\nOld plan: [plan](90-archive/plan.md).\n")
        self.assert_problem("index outside the archive links into it: 90-archive/plan.md")

    def test_numbered_names_must_not_repeat(self):
        self.write("01-decisions/README.md", "# Decisions\n")
        self.write("README.md", (self.root / "README.md").read_text(encoding="utf-8")
                   + "\n[decisions](01-decisions/README.md)\n")
        self.assert_problem("numbered names repeat 01: 01-decisions, 01-rules")

    def test_an_unlinked_subdirectory_and_a_missing_target_fail(self):
        self.write("01-rules/sub/README.md", "# Sub\n")
        self.assert_problem("subdirectory sub/ is not linked")
        self.write("01-rules/README.md", (self.root / "01-rules/README.md").read_text(encoding="utf-8")
                   + "| [sub](sub/README.md) | | |\n| [gone.md](gone.md) | current | |\n")
        self.assert_problem("index links missing gone.md")

    def test_links_in_code_blocks_and_external_links_are_ignored(self):
        self.write("README.md", (self.root / "README.md").read_text(encoding="utf-8")
                   + "\n```\n[x](90-archive/plan.md)\n```\n[site](https://example.com/a.md)\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_example_tables_in_code_and_tables_without_status_are_not_indexes(self):
        """Review of #736 (F1, F3): a fenced example index and a "read first" table are not index rows."""
        self.write("01-rules/README.md", (self.root / "01-rules/README.md").read_text(encoding="utf-8") + """
Read first:

| Document | Why |
|---|---|
| [principles.md](principles.md) | the rules |

Example of an index:

```markdown
| Document | Status |
|---|---|
| [example.md](example.md) | current |
```
""")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_the_root_holds_only_the_entry_readme(self):
        self.write("notes.md", "# Notes\n")
        self.assert_problem("notes.md: the KB root holds only the entry README")

    def test_a_link_with_the_wrong_letter_case_is_missing(self):
        text = (self.root / "01-rules/README.md").read_text(encoding="utf-8")
        (self.root / "01-rules/README.md").write_text(text.replace("[principles.md](principles.md) | current",
                                                                   "[Principles.md](Principles.md) | current"),
                                                      encoding="utf-8")
        self.assert_problem("index links missing Principles.md")

    def test_a_readme_that_is_not_utf8_is_a_problem_not_a_crash(self):
        (self.root / "01-rules/README.md").write_bytes(b"| Document | Status |\n|---|---|\n| \xff |\n")
        self.assert_problem("01-rules/README.md: cannot be read as UTF-8 text")

    def test_a_missing_root_is_invalid_input(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.root / "nope")], capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
