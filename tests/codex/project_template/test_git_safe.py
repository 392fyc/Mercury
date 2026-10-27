"""git-safe.ps1 add: files only, plus registered submodule paths (#684). Runs the real script in a throwaway repository."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts" / "codex"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "core.autocrlf", "false")
    (path / "seed.txt").write_text("seed\n")
    git(path, "add", "seed.txt")
    git(path, "commit", "-q", "-m", "seed")


@unittest.skipUnless(PWSH, "PowerShell is not available")
class GitSafeAddTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "repo"
        make_repo(self.repo)
        git(self.repo, "checkout", "-q", "-b", "feature/TASK-1-codex")
        (self.repo / "scripts/codex").mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.repo / "scripts/codex" / name)
        # a clone that sits inside the repository, as `mercury.py source add` leaves it
        self.upstream = Path(tmp.name) / "upstream"
        make_repo(self.upstream)
        git(self.repo, "clone", "-q", self.upstream.as_posix(), "sources/sub")

    def add(self, *paths):
        r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.repo / "scripts/codex/git-safe.ps1"), "add", *paths],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.repo)
        return r.returncode, r.stdout + r.stderr

    def staged(self):
        return git(self.repo, "ls-files", "-s", "--", "sources", ".gitmodules", "plain", "other", ".dotdir", ".gitignore")

    def register(self, path="sources/sub"):
        (self.repo / ".gitmodules").write_text(f'[submodule "{path}"]\n\tpath = {path}\n\turl = https://github.com/o/r.git\n\tshallow = true\n')

    def test_a_registered_submodule_path_is_staged_as_a_gitlink(self):
        self.register()
        code, out = self.add(".gitmodules", "sources/sub")
        self.assertEqual(code, 0, out)
        head = git(self.upstream, "rev-parse", "HEAD").strip()
        self.assertIn(f"160000 {head} 0\tsources/sub", self.staged())
        self.assertIn(".gitmodules", self.staged())

    @unittest.skipUnless(sys.platform == "win32", "a backslash is a path separator only on Windows")
    def test_a_registered_path_written_with_a_backslash_is_accepted(self):
        self.register()
        code, out = self.add(".gitmodules", "sources" + chr(92) + "sub")
        self.assertEqual(code, 0, out)
        self.assertIn("160000", self.staged())

    def test_a_git_work_tree_that_is_not_registered_is_refused(self):
        code, out = self.add("sources/sub")
        self.assertNotEqual(code, 0)
        self.assertIn("Directories are not allowed", out)
        self.register("sources/elsewhere")
        self.assertNotEqual(self.add("sources/sub")[0], 0)
        self.assertEqual(self.staged(), "")

    def test_a_registered_path_that_is_a_plain_directory_is_refused(self):
        (self.repo / "plain").mkdir()
        (self.repo / "plain/f.txt").write_text("x\n")
        self.register("plain")
        code, out = self.add("plain")
        self.assertNotEqual(code, 0)
        self.assertIn("Directories are not allowed", out)
        self.assertEqual(self.staged(), "")

    def test_other_directories_and_spellings_stay_refused(self):
        self.register()
        (self.repo / "other").mkdir()
        (self.repo / "other/f.txt").write_text("x\n")
        for path in ("other", "sources", "sources/sub/", "Sources/Sub", "sources/sub/.", ".", "sources/*", ":(top)sources/sub", "-A"):
            with self.subTest(path=path):
                self.assertNotEqual(self.add(path)[0], 0)
        self.assertEqual(self.staged(), "")

    def test_plain_files_still_work(self):
        (self.repo / "other").mkdir()
        (self.repo / "other/f.txt").write_text("x\n")
        self.assertEqual(self.add("other/f.txt")[0], 0)
        self.assertIn("other/f.txt", self.staged())

    def test_an_unregistered_dot_directory_is_refused(self):
        """#685: a name starting with a dot is not exempt from the directory rule, whether it is a plain directory or a clone."""
        (self.repo / ".dotdir").mkdir()
        (self.repo / ".dotdir/f.txt").write_text("x\n")
        self.register()  # registers sources/sub only; .dotdir stays unregistered
        code, out = self.add(".dotdir")
        self.assertNotEqual(code, 0)
        self.assertIn("Directories are not allowed", out)
        shutil.rmtree(self.repo / ".dotdir")
        git(self.repo, "clone", "-q", self.upstream.as_posix(), ".dotdir")
        code, out = self.add(".dotdir")
        self.assertNotEqual(code, 0)
        self.assertIn("Directories are not allowed", out)
        self.assertEqual(self.staged(), "")

    def test_a_plain_dot_file_is_staged(self):
        """#685: `.gitignore` is a file, so it must stage even though its name starts with a dot."""
        (self.repo / ".gitignore").write_text("*.tmp\n")
        code, out = self.add(".gitignore")
        self.assertEqual(code, 0, out)
        self.assertIn(".gitignore", self.staged())

    def test_the_branch_check_still_applies(self):
        self.register()
        git(self.repo, "checkout", "-q", "-b", "scratch")
        code, out = self.add(".gitmodules", "sources/sub")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.staged(), "")


@unittest.skipUnless(PWSH, "PowerShell is not available")
class BranchNamingTest(unittest.TestCase):
    """Branches are named for their purpose; agent and lane are suffixes only (#703)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "repo"
        make_repo(self.repo)
        (self.repo / "scripts/codex").mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.repo / "scripts/codex" / name)
        (self.repo / "plain").write_text("x\n")

    def add_on(self, branch):
        git(self.repo, "checkout", "-q", "-B", branch)
        r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.repo / "scripts/codex/git-safe.ps1"), "add", "plain"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.repo)
        return r.returncode, r.stdout + r.stderr

    def test_purpose_named_branches_are_accepted(self):
        for branch in ("feature/TASK-703-claude", "fix/guard-premerge-guard-codex", "docs/a-very-long-purpose-name-that-exceeds-forty-characters-codex"):
            code, out = self.add_on(branch)
            self.assertEqual(code, 0, f"{branch}: {out}")

    def test_branches_from_before_the_rule_can_still_be_committed_to(self):
        for branch in ("feature/TASK-657", "lane/guard/621-test"):
            code, out = self.add_on(branch)
            self.assertEqual(code, 0, f"{branch}: {out}")

    def test_agent_or_lane_prefixes_and_missing_agent_suffix_are_refused(self):
        for branch in ("codex/fix-guard", "claude/talent-design", "mercury/update-0123456789ab", "feature/nested/path-claude", "FEATURE/TASK-703-CLAUDE", "feature/new-work"):
            code, out = self.add_on(branch)
            self.assertNotEqual(code, 0, branch)
            self.assertIn("<type>/<purpose>[-<lane>]-<agent>", out, branch)


if __name__ == "__main__":
    unittest.main()
