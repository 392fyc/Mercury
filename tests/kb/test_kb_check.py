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
            Rules: [rules](01-rules/README.md). Research: [research](02-research/README.md).
            """)
        self.write("01-rules/README.md", """\
            # Rules

            | Document | Status |
            |---|---|
            | [principles.md](principles.md) | current |
            """)
        self.write("01-rules/principles.md", "# Principles\nSee [[02-research/notes]] and [notes](../02-research/notes.md).\n")
        self.write("02-research/README.md", """\
            | 文档 | 状态 |
            |---|---|
            | [notes.md](notes.md) | 草稿 |
            """)
        self.write("02-research/notes.md", "# Notes\nBack to [[principles|the rules]].\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")

    def append(self, name, text):
        self.write(name, (self.root / name).read_text(encoding="utf-8") + text)

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
        self.assertIn("kb-check: OK (2 document(s) in 3 director(ies))", out)

    def test_a_directory_without_readme_fails(self):
        self.write("04-research/a.md", "# A\n")
        self.assert_problem("04-research: no README.md index")

    def test_an_unlisted_document_fails(self):
        self.write("01-rules/extra.md", "# Extra\n")
        self.assert_problem("01-rules/README.md: extra.md is not listed in the index")

    def test_only_current_and_draft_are_statuses(self):
        """#736: outdated documents are deleted, so superseded and history are no longer statuses."""
        for status in ("superseded", "history", "已取代", "历史", "active"):
            with self.subTest(status=status):
                self.write("01-rules/README.md", f"| Document | Status |\n|---|---|\n"
                                                 f"| [principles.md](principles.md) | {status} |\n")
                self.assert_problem(f"principles.md has status {status.lower()}; use current or draft")

    def test_an_archive_directory_fails(self):
        for name in ("90-archive", "_archive", "history"):
            with self.subTest(name=name):
                self.write(f"01-rules/{name}/README.md", "| 文档 | 状态 |\n|---|---|\n")
                self.append("01-rules/README.md", f"\n[old]({name}/README.md)\n")
                self.assert_problem(f"01-rules/{name}: archive directory")

    def test_a_fence_closes_only_on_its_own_kind(self):
        """Review of #736: a ``` line inside a ~~~ block must not end it, or later links go unchecked."""
        self.append("01-rules/principles.md", "\n~~~\n```\n[[in-fence]]\n~~~\n[[after-fence]]\n")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertNotIn("in-fence", out)
        self.assertIn("wikilink to missing [[after-fence]]", out)
        self.write("01-rules/principles.md", "# P\n````\n```\n[[in-long-fence]]\n````\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_escaped_alias_bar_parent_and_query_links_resolve(self):
        self.append("01-rules/principles.md", "\n| a | [[principles\\|the rules]] |\n[up](..) [up](../) "
                                              "[raw](principles.md?plain=1)\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_a_link_from_the_filesystem_root_fails(self):
        self.append("01-rules/principles.md", "\n[abs](/docs/01-rules/principles.md)\n")
        self.assert_problem("link /docs/01-rules/principles.md starts with /")

    def test_numbered_names_must_not_repeat(self):
        self.write("01-decisions/README.md", "# Decisions\n")
        self.append("README.md", "\n[decisions](01-decisions/README.md)\n")
        self.assert_problem("numbered names repeat 01: 01-decisions, 01-rules")

    def test_an_unlinked_subdirectory_fails(self):
        self.write("01-rules/sub/README.md", "# Sub\n")
        self.assert_problem("subdirectory sub/ is not linked")

    def test_a_link_to_a_deleted_document_fails_in_any_document(self):
        """#736: once a document is deleted, every link left to it is an error, not only links in indexes."""
        (self.root / "02-research/notes.md").unlink()
        self.write("02-research/README.md", "| 文档 | 状态 |\n|---|---|\n")
        code, out = self.run_check()
        self.assertEqual(code, 1, out)
        self.assertIn("01-rules/principles.md: link to missing ../02-research/notes.md", out)
        self.assertIn("01-rules/principles.md: wikilink to missing [[02-research/notes]]", out)

    def test_an_index_row_to_a_missing_document_fails(self):
        self.append("01-rules/README.md", "| [gone.md](gone.md) | current |\n")
        self.assert_problem("01-rules/README.md: link to missing gone.md")

    def test_wikilinks_resolve_by_root_path_folder_or_unique_name(self):
        self.append("02-research/notes.md", "[[01-rules/principles.md]] ![[principles]] [[notes#Heading]]\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)
        self.append("02-research/notes.md", "[[nowhere|alias]]\n")
        self.assert_problem("02-research/notes.md: wikilink to missing [[nowhere]]")

    def test_links_in_code_comments_and_external_links_are_ignored(self):
        self.append("01-rules/principles.md", "\n```\n[x](gone.md) [[gone]]\n```\n`[[gone]]` <!-- [x](gone.md) -->\n"
                                              "[site](https://example.com/a.md) [top](#principles)\n")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_example_tables_in_code_and_tables_without_status_are_not_indexes(self):
        """Review of #736 (F1, F3): a fenced example index and a "read first" table are not index rows."""
        self.append("01-rules/README.md", """
Read first:

| Document | Why |
|---|---|
| [principles.md](principles.md) | the rules |

Example of an index:

```markdown
| Document | Status |
|---|---|
| [example.md](example.md) | superseded |
```
""")
        code, out = self.run_check()
        self.assertEqual(code, 0, out)

    def test_the_root_holds_only_the_entry_readme(self):
        self.write("notes.md", "# Notes\n")
        self.assert_problem("notes.md: the KB root holds only the entry README")

    def test_a_link_with_the_wrong_letter_case_is_missing(self):
        self.write("01-rules/README.md", "| Document | Status |\n|---|---|\n| [Principles.md](Principles.md) | current |\n")
        self.assert_problem("link to missing Principles.md")

    def test_a_document_that_is_not_utf8_is_a_problem_not_a_crash(self):
        (self.root / "01-rules/README.md").write_bytes(b"| Document | Status |\n|---|---|\n| \xff |\n")
        self.assert_problem("01-rules/README.md: cannot be read as UTF-8 text")
        (self.root / "01-rules/README.md").write_text("| Document | Status |\n|---|---|\n"
                                                      "| [principles.md](principles.md) | current |\n", encoding="utf-8")
        (self.root / "01-rules/principles.md").write_bytes(b"# \xff\n")
        self.assert_problem("01-rules/principles.md: cannot be read as UTF-8 text")

    def test_a_missing_root_is_invalid_input(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), str(self.root / "nope")], capture_output=True,
                                text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
