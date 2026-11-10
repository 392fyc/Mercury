"""git-safe.ps1 add: files only, plus registered submodule paths (#684). Runs the real script in a throwaway repository."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "packs" / "git-safe" / "scripts"
FORWARDERS = Path(__file__).resolve().parents[3] / "scripts" / "codex"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


def make_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    for key, value in (("core.autocrlf", "false"), ("user.name", "t"), ("user.email", "t@example.com")):
        git(path, "config", key, value)  # the scripts under test commit without the -c identity this helper adds
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


@unittest.skipUnless(PWSH, "PowerShell is not available")
class GitSafeSyncTest(unittest.TestCase):
    """git-safe sync merges origin/master into the current task branch and stops before committing (#716)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.origin = root / "origin.git"
        git(root, "init", "-q", "--bare", "-b", "master", self.origin.as_posix())
        self.trunk = root / "trunk"  # where "other PRs" land on master
        git(root, "clone", "-q", self.origin.as_posix(), "trunk")
        git(self.trunk, "config", "core.autocrlf", "false")
        git(self.trunk, "checkout", "-q", "-b", "master")
        (self.trunk / "shared.txt").write_text("line one\n")
        git(self.trunk, "add", "shared.txt")
        git(self.trunk, "commit", "-q", "-m", "seed")
        git(self.trunk, "push", "-q", "origin", "master")
        self.repo = root / "repo"
        git(root, "clone", "-q", self.origin.as_posix(), "repo")
        for key, value in (("core.autocrlf", "false"), ("user.name", "t"), ("user.email", "t@example.com")):
            git(self.repo, "config", key, value)
        git(self.repo, "checkout", "-q", "-b", "feature/TASK-1-codex")
        (self.repo / "scripts/codex").mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.repo / "scripts/codex" / name)
        (self.repo / ".git/info/exclude").write_text("scripts/\n.mercury/\n")

    def run_ps(self, script, *args):
        r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.repo / "scripts/codex" / script), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.repo, timeout=120)
        return r.returncode, r.stdout + r.stderr

    def sync(self, *args):
        return self.run_ps("git-safe.ps1", "sync", *args)

    def land_on_master(self, name, text):
        (self.trunk / name).write_text(text)
        git(self.trunk, "add", name)
        git(self.trunk, "commit", "-q", "-m", f"land {name}")
        git(self.trunk, "push", "-q", "origin", "master")

    def commit_on_branch(self, name, text):
        (self.repo / name).write_text(text)
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", f"branch {name}")

    def merging(self):
        return (self.repo / ".git/MERGE_HEAD").exists()

    def test_up_to_date_changes_nothing(self):
        head = git(self.repo, "rev-parse", "HEAD")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertIn("sync=up-to-date", out)
        self.assertFalse(self.merging())
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_a_clean_merge_is_staged_then_concluded_with_git_safe_commit(self):
        self.commit_on_branch("mine.txt", "mine\n")
        self.land_on_master("theirs.txt", "theirs\n")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertIn("sync=merged", out)
        self.assertTrue(self.merging())  # stopped before committing
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD"), "2\n")
        self.assertEqual(self.run_ps("git-safe.ps1", "commit", "-Message", "chore(TASK-1): merge master")[0], 1)  # no review mark
        self.assertEqual(self.run_ps("guard.ps1", "mark-review")[0], 0)
        code, out = self.run_ps("git-safe.ps1", "commit", "-Message", "chore(TASK-1): merge master")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.merging())
        parents = git(self.repo, "rev-list", "--parents", "-n", "1", "HEAD").split()
        self.assertEqual(parents[2], git(self.repo, "rev-parse", "origin/master").strip())
        self.assertTrue((self.repo / "theirs.txt").exists())
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip(), "feature/TASK-1-codex")

    def test_a_fast_forward_still_stops_as_a_merge(self):
        self.land_on_master("theirs.txt", "theirs\n")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertTrue(self.merging())  # --no-ff: the branch history stays its own

    def test_conflicts_are_listed_and_left_for_the_agent(self):
        self.commit_on_branch("shared.txt", "branch edit\n")
        self.land_on_master("shared.txt", "master edit\n")
        code, out = self.sync()
        self.assertEqual(code, 1, out)
        self.assertIn("sync=conflicts", out)
        self.assertIn("conflict: shared.txt", out)
        self.assertTrue(self.merging())
        code, out = self.sync()  # a merge in progress is never stacked on
        self.assertNotEqual(code, 0)
        self.assertIn("Another Git operation is in progress (MERGE_HEAD)", out)
        (self.repo / "shared.txt").write_text("branch edit\nmaster edit\n")
        self.assertEqual(self.run_ps("git-safe.ps1", "add", "shared.txt")[0], 0)
        self.assertEqual(self.run_ps("guard.ps1", "mark-review")[0], 0)
        code, out = self.run_ps("git-safe.ps1", "commit", "-Message", "chore(TASK-1): merge master")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.merging())

    def test_refuses_local_changes_protected_branches_and_arguments(self):
        self.land_on_master("theirs.txt", "theirs\n")
        (self.repo / "shared.txt").write_text("unstaged\n")
        code, out = self.sync()
        self.assertNotEqual(code, 0)
        self.assertIn("staged or unstaged changes", out)
        git(self.repo, "add", "shared.txt")
        self.assertIn("staged or unstaged changes", self.sync()[1])
        git(self.repo, "reset", "-q", "--hard")
        for args in (("origin", "develop"), ("-Message", "x")):
            with self.subTest(args=args):
                code, out = self.sync(*args)
                self.assertNotEqual(code, 0)
                self.assertIn("takes no arguments", out)
        for branch, reason in (("master", "Protected branch 'master'"), ("scratch", "Branch 'scratch' does not match")):
            with self.subTest(branch=branch):
                git(self.repo, "checkout", "-q", "-B", branch)
                code, out = self.sync()
                self.assertNotEqual(code, 0)
                self.assertIn(reason, out)
        git(self.repo, "checkout", "-q", "--detach")
        code, out = self.sync()
        self.assertNotEqual(code, 0)
        self.assertIn("Branch 'HEAD' does not match", out)
        self.assertFalse(self.merging())

    def test_refuses_other_operations_in_progress(self):
        self.land_on_master("theirs.txt", "theirs\n")
        marker = self.repo / ".git/CHERRY_PICK_HEAD"
        marker.write_text(git(self.repo, "rev-parse", "HEAD"))
        code, out = self.sync()
        self.assertNotEqual(code, 0)
        self.assertIn("(CHERRY_PICK_HEAD)", out)
        self.assertFalse(self.merging())

    def test_a_failed_merge_without_conflicts_leaves_nothing_behind(self):
        self.land_on_master("theirs.txt", "theirs\n")
        (self.repo / "theirs.txt").write_text("untracked, would be overwritten\n")
        head = git(self.repo, "rev-parse", "HEAD")
        code, out = self.sync()
        self.assertNotEqual(code, 0)
        self.assertIn("git merge of origin/master failed", out)
        self.assertFalse(self.merging())
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual((self.repo / "theirs.txt").read_text(), "untracked, would be overwritten\n")

    def test_a_merge_that_changes_no_file_can_still_be_concluded(self):
        self.commit_on_branch("theirs.txt", "same\n")  # the branch already has what master brings
        self.land_on_master("theirs.txt", "same\n")
        code, out = self.sync()
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.repo, "diff", "--cached", "--name-only"), "")
        self.assertEqual(self.run_ps("guard.ps1", "mark-review")[0], 0)
        code, out = self.run_ps("git-safe.ps1", "commit", "-Message", "chore(TASK-1): merge master")
        self.assertEqual(code, 0, out)
        self.assertFalse(self.merging())

    def test_abort_sync_abandons_a_conflicted_sync_and_restores_the_branch(self):
        self.commit_on_branch("shared.txt", "branch edit\n")
        self.land_on_master("shared.txt", "master edit\n")
        head = git(self.repo, "rev-parse", "HEAD")
        code, out = self.sync()
        self.assertEqual(code, 1, out)
        self.assertIn("git-safe abort-sync", out)
        code, out = self.run_ps("git-safe.ps1", "abort-sync")
        self.assertEqual(code, 0, out)
        self.assertIn("sync=aborted", out)
        self.assertFalse(self.merging())
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual((self.repo / "shared.txt").read_text(), "branch edit\n")
        self.assertEqual(git(self.repo, "status", "--porcelain", "--untracked-files=no"), "")

    def test_abort_sync_refuses_without_a_sync_merge(self):
        code, out = self.run_ps("git-safe.ps1", "abort-sync")
        self.assertNotEqual(code, 0)
        self.assertIn("No merge is in progress", out)
        for args in (("origin",), ("-Message", "x")):
            with self.subTest(args=args):
                code, out = self.run_ps("git-safe.ps1", "abort-sync", *args)
                self.assertNotEqual(code, 0)
                self.assertIn("takes no arguments", out)
        # A merge of something other than trunk commits is left alone.
        git(self.repo, "checkout", "-q", "-b", "feature/side-codex")
        self.commit_on_branch("side.txt", "side\n")
        git(self.repo, "checkout", "-q", "feature/TASK-1-codex")
        self.commit_on_branch("mine.txt", "mine\n")
        git(self.repo, "merge", "-q", "--no-ff", "--no-commit", "feature/side-codex")
        code, out = self.run_ps("git-safe.ps1", "abort-sync")
        self.assertNotEqual(code, 0)
        self.assertIn("not a git-safe sync of origin/master", out)
        self.assertTrue(self.merging())

    def test_abort_sync_refuses_a_protected_branch(self):
        self.land_on_master("theirs.txt", "theirs\n")
        self.assertEqual(self.sync()[0], 0)
        git(self.repo, "branch", "-f", "master", "feature/TASK-1-codex")
        (self.repo / ".git/HEAD").write_text("ref: refs/heads/master\n")  # as if the merge had been started on master
        code, out = self.run_ps("git-safe.ps1", "abort-sync")
        self.assertNotEqual(code, 0)
        self.assertIn("Protected branch 'master'", out)
        self.assertTrue(self.merging())

    def test_an_ordinary_commit_with_nothing_staged_is_still_refused(self):
        self.assertEqual(self.run_ps("guard.ps1", "mark-review")[0], 0)
        code, out = self.run_ps("git-safe.ps1", "commit", "-Message", "chore(TASK-1): nothing")
        self.assertNotEqual(code, 0)
        self.assertIn("No staged changes found", out)




@unittest.skipUnless(PWSH, "PowerShell is not available")
class GitSafeFastForwardTest(unittest.TestCase):
    """git-safe fast-forward brings the main checkout on the integration branch up to origin, and nothing else (#741)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        origin = root / "origin.git"
        git(root, "init", "-q", "--bare", "-b", "main", origin.as_posix())
        self.trunk = root / "trunk"  # where merged pull requests land
        git(root, "clone", "-q", origin.as_posix(), "trunk")
        git(self.trunk, "config", "core.autocrlf", "false")
        git(self.trunk, "checkout", "-q", "-b", "main")
        (self.trunk / "shared.txt").write_text("one\n")
        git(self.trunk, "add", "shared.txt")
        git(self.trunk, "commit", "-q", "-m", "seed")
        git(self.trunk, "push", "-q", "origin", "main")
        self.repo = root / "main-checkout"
        git(root, "clone", "-q", origin.as_posix(), "main-checkout")
        for key, value in (("core.autocrlf", "false"), ("user.name", "t"), ("user.email", "t@example.com")):
            git(self.repo, "config", key, value)
        self.scripts = self.repo / ".mercury/packs/git-safe/scripts"
        self.scripts.mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        (self.repo / ".mercury/config.json").write_text('{"params": {"integration_branch": "main"}}')
        (self.repo / ".git/info/exclude").write_text(".mercury/\n")

    def ff(self, *args, cwd=None):
        return run_ps(self.scripts / "git-safe.ps1", "fast-forward", *args, cwd=cwd or self.repo)

    def land(self, name, text):
        (self.trunk / name).write_text(text)
        git(self.trunk, "add", name)
        git(self.trunk, "commit", "-q", "-m", f"land {name}")
        git(self.trunk, "push", "-q", "origin", "main")
        return git(self.trunk, "rev-parse", "HEAD").strip()

    def head(self):
        return git(self.repo, "rev-parse", "HEAD").strip()

    def test_up_to_date_changes_nothing(self):
        head = self.head()
        code, out = self.ff()
        self.assertEqual(code, 0, out)
        self.assertIn("fast-forward=up-to-date", out)
        self.assertEqual(self.head(), head)

    def test_fast_forwards_and_keeps_local_changes_it_does_not_touch(self):
        (self.repo / "shared.txt").write_text("human edit\n")
        (self.repo / "scratch.txt").write_text("untracked\n")
        target = self.land("theirs.txt", "theirs\n")
        code, out = self.ff(cwd=self.trunk)  # the target is the checkout holding the script, not the caller
        self.assertEqual(code, 0, out)
        self.assertIn("fast-forward=updated", out)
        self.assertIn(f"head={target}", out)
        self.assertEqual(self.head(), target)
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD"), "2\n")  # no merge commit
        self.assertEqual((self.repo / "shared.txt").read_text(), "human edit\n")
        self.assertTrue((self.repo / "scratch.txt").exists())

    def test_refuses_to_overwrite_a_local_change_even_with_autostash(self):
        """Review of #741: merge.autoStash would stash the edit, move HEAD and could leave the edit stashed."""
        (self.repo / "shared.txt").write_text("human edit\n")
        self.land("shared.txt", "two\n")
        head = self.head()
        for autostash in ("false", "true"):
            with self.subTest(autostash=autostash):
                git(self.repo, "config", "merge.autoStash", autostash)
                code, out = self.ff()
                self.assertNotEqual(code, 0, out)
                self.assertIn("HEAD was not moved", out)
                self.assertEqual(self.head(), head)
                self.assertEqual((self.repo / "shared.txt").read_text(), "human edit\n")
                self.assertEqual(git(self.repo, "stash", "list").strip(), "")

    def test_refuses_to_overwrite_an_untracked_file(self):
        (self.repo / "theirs.txt").write_text("mine, untracked\n")
        self.land("theirs.txt", "theirs\n")
        head = self.head()
        code, out = self.ff()
        self.assertNotEqual(code, 0, out)
        self.assertEqual(self.head(), head)
        self.assertEqual((self.repo / "theirs.txt").read_text(), "mine, untracked\n")

    def test_refuses_a_linked_worktree_on_the_trunk(self):
        self.land("theirs.txt", "theirs\n")
        git(self.repo, "checkout", "-q", "--detach")
        linked = self.repo.parent / "linked"
        git(self.repo, "worktree", "add", "-q", linked.as_posix(), "main")
        code, out = run_ps(self.scripts / "git-safe.ps1", "fast-forward", cwd=linked)
        self.assertNotEqual(code, 0, out)  # the script copy in the main checkout acts on the main checkout: detached
        scripts = linked / ".mercury/packs/git-safe/scripts"
        scripts.mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, scripts / name)
        (linked / ".mercury/config.json").write_text('{"params": {"integration_branch": "main"}}')
        head = git(linked, "rev-parse", "HEAD").strip()
        code, out = run_ps(scripts / "git-safe.ps1", "fast-forward", cwd=linked)
        self.assertNotEqual(code, 0, out)
        self.assertIn("only on the main checkout", out)
        self.assertEqual(git(linked, "rev-parse", "HEAD").strip(), head)

    def test_without_a_config_the_trunk_is_master(self):
        (self.repo / ".mercury/config.json").unlink()
        code, out = self.ff()
        self.assertNotEqual(code, 0, out)
        self.assertIn("acts only on the integration branch 'master'", out)

    def test_refuses_local_commits(self):
        (self.repo / "local.txt").write_text("local\n")
        git(self.repo, "add", "local.txt")
        git(self.repo, "commit", "-q", "-m", "local")
        self.land("theirs.txt", "theirs\n")
        head = self.head()
        code, out = self.ff()
        self.assertNotEqual(code, 0, out)
        self.assertIn("a fast-forward is impossible", out)
        self.assertEqual(self.head(), head)

    def test_refuses_other_branches_arguments_and_operations_in_progress(self):
        self.land("theirs.txt", "theirs\n")
        head = self.head()
        for args in (("origin", "main"), ("-Message", "x")):
            with self.subTest(args=args):
                code, out = self.ff(*args)
                self.assertNotEqual(code, 0)
                self.assertIn("takes no arguments", out)
        (self.repo / ".git/MERGE_HEAD").write_text(head + "\n")
        code, out = self.ff()
        self.assertNotEqual(code, 0)
        self.assertIn("(MERGE_HEAD)", out)
        (self.repo / ".git/MERGE_HEAD").unlink()
        git(self.repo, "checkout", "-q", "-b", "feature/TASK-4-claude")
        code, out = self.ff()
        self.assertNotEqual(code, 0)
        self.assertIn("acts only on the integration branch 'main'", out)
        git(self.repo, "checkout", "-q", "--detach")
        code, out = self.ff()
        self.assertNotEqual(code, 0)
        self.assertIn("acts only on the integration branch", out)
        self.assertEqual(self.head(), head)


def run_ps(script, *args, cwd):
    r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=cwd, timeout=120)
    return r.returncode, r.stdout + r.stderr


@unittest.skipUnless(PWSH, "PowerShell is not available")
class GitSafePackTest(unittest.TestCase):
    """The pack copy acts on the work tree that holds it and keeps its review mark in the Git directory (#723)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.caller = root / "caller"  # the checkout the agent happens to run in
        make_repo(self.caller)
        git(self.caller, "checkout", "-q", "-b", "feature/TASK-2-claude")
        self.project = root / "project"  # the downstream project with the pack installed
        make_repo(self.project)
        git(self.project, "checkout", "-q", "-b", "docs/kb-links-claude")
        self.scripts = self.project / ".mercury/packs/git-safe/scripts"
        self.scripts.mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        (self.project / ".git/info/exclude").write_text(".mercury/\n")

    def test_the_target_is_the_work_tree_holding_the_script_not_the_caller(self):
        (self.project / "note.md").write_text("note\n")
        (self.caller / "note.md").write_text("caller\n")
        code, out = run_ps(self.scripts / "git-safe.ps1", "add", "note.md", cwd=self.caller)
        self.assertEqual(code, 0, out)
        self.assertIn("note.md", git(self.project, "diff", "--cached", "--name-only"))
        self.assertEqual(git(self.caller, "diff", "--cached", "--name-only").strip(), "")
        code, out = run_ps(self.scripts / "guard.ps1", "status", cwd=self.caller)
        self.assertEqual(code, 0, out)
        self.assertIn("branch=docs/kb-links-claude", out)

    def test_the_review_mark_lives_in_the_git_directory(self):
        (self.project / "note.md").write_text("note\n")
        git(self.project, "add", "note.md")
        code, out = run_ps(self.scripts / "guard.ps1", "mark-review", cwd=self.project)
        self.assertEqual(code, 0, out)
        self.assertTrue((self.project / ".git/mercury-review-passed").is_file())
        self.assertFalse((self.project / ".mercury/state").exists())
        code, out = run_ps(self.scripts / "git-safe.ps1", "commit", "-Message", "docs(TASK-2): note", cwd=self.caller)
        self.assertEqual(code, 0, out)
        self.assertIn("docs(TASK-2): note", git(self.project, "log", "-1", "--format=%s"))
        self.assertEqual(git(self.caller, "log", "--format=%s").strip(), "seed")

    def test_an_unusable_integration_branch_is_refused(self):
        (self.project / ".mercury/config.json").write_text('{"params": {"integration_branch": "main; rm -rf"}}')
        code, out = run_ps(self.scripts / "git-safe.ps1", "sync", cwd=self.project)
        self.assertNotEqual(code, 0, out)
        # PowerShell colours and wraps the error to the console width (with a "| " gutter on Linux); compare the bare words.
        bare = re.sub(r"\x1b\[[0-9;]*m|[\s|]", "", out)
        self.assertIn("notaplainbranchname", bare)


@unittest.skipUnless(PWSH, "PowerShell is not available")
class GitSafeSyncIntegrationBranchTest(unittest.TestCase):
    """sync merges origin/<params.integration_branch>, here main, not master (#723)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        origin = root / "origin.git"
        git(root, "init", "-q", "--bare", "-b", "main", origin.as_posix())
        self.trunk = root / "trunk"
        git(root, "clone", "-q", origin.as_posix(), "trunk")
        git(self.trunk, "config", "core.autocrlf", "false")
        git(self.trunk, "checkout", "-q", "-b", "main")
        (self.trunk / "shared.txt").write_text("one\n")
        git(self.trunk, "add", "shared.txt")
        git(self.trunk, "commit", "-q", "-m", "seed")
        git(self.trunk, "push", "-q", "origin", "main")
        self.repo = root / "repo"
        git(root, "clone", "-q", origin.as_posix(), "repo")
        for key, value in (("core.autocrlf", "false"), ("user.name", "t"), ("user.email", "t@example.com")):
            git(self.repo, "config", key, value)
        git(self.repo, "checkout", "-q", "-b", "feature/TASK-3-codex")
        self.scripts = self.repo / ".mercury/packs/git-safe/scripts"
        self.scripts.mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        (self.repo / ".mercury/config.json").write_text('{"params": {"integration_branch": "main"}}')
        (self.repo / ".git/info/exclude").write_text(".mercury/\n")

    def test_sync_merges_the_integration_branch(self):
        (self.trunk / "theirs.txt").write_text("theirs\n")
        git(self.trunk, "add", "theirs.txt")
        git(self.trunk, "commit", "-q", "-m", "land theirs")
        git(self.trunk, "push", "-q", "origin", "main")
        code, out = run_ps(self.scripts / "git-safe.ps1", "sync", cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("sync=merged", out)
        self.assertTrue((self.repo / "theirs.txt").is_file())
        self.assertTrue((self.repo / ".git/MERGE_HEAD").exists())


@unittest.skipUnless(PWSH, "PowerShell is not available")
class ForwarderTest(unittest.TestCase):
    """Mercury's scripts/codex entries forward to the pack with their parameters intact (#723).

    Runs in a throw-away repository laid out like Mercury and on a task branch, so the result never depends on what the
    Mercury checkout running the tests has checked out (CI checks out a detached merge commit)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "mercury"
        make_repo(self.repo)
        git(self.repo, "checkout", "-q", "-b", "feature/TASK-5-claude")
        for rel, source in (("scripts/codex", FORWARDERS), ("packs/git-safe/scripts", SCRIPTS)):
            (self.repo / rel).mkdir(parents=True)
            for name in ("git-safe.ps1", "guard.ps1"):
                shutil.copyfile(source / name, self.repo / rel / name)
        (self.repo / ".git/info/exclude").write_text("scripts/" + chr(10) + "packs/" + chr(10))
        self.forwarders = self.repo / "scripts/codex"

    def test_remaining_arguments_reach_the_pack_script_and_its_exit_code_comes_back(self):
        # Only the pack script knows that master is protected, so this message proves the arguments arrived.
        code, out = run_ps(self.forwarders / "git-safe.ps1", "push", "origin", "HEAD:master", cwd=self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("targets a protected branch", out)

    def test_named_parameters_reach_the_guard(self):
        # The receipt check runs inside the pack guard before anything else, so it fires only if -NativeReviewReceipt arrived.
        code, out = run_ps(self.forwarders / "guard.ps1", "status", "-NativeReviewReceipt", "x.json", cwd=self.repo)
        self.assertEqual(code, 1, out)
        self.assertIn("valid only with the pre-merge action", out)
        code, out = run_ps(self.forwarders / "guard.ps1", "status", cwd=self.repo)
        self.assertEqual(code, 0, out)
        self.assertIn("branch=feature/TASK-5-claude", out)

@unittest.skipUnless(PWSH, "PowerShell is not available")
class ProtectionTest(unittest.TestCase):
    """A custom integration branch is protected, and inherited Git variables are refused (#723)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "project"
        make_repo(self.repo)
        git(self.repo, "checkout", "-q", "-b", "feature/TASK-4-codex")
        self.scripts = self.repo / ".mercury/packs/git-safe/scripts"
        self.scripts.mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        (self.repo / ".mercury/config.json").write_text('{"params": {"integration_branch": "trunk"}}')
        (self.repo / ".git/info/exclude").write_text(".mercury/\n")

    def test_pushing_to_the_integration_branch_is_refused(self):
        for target in ("HEAD:trunk", "HEAD:refs/heads/trunk", "HEAD:master"):
            with self.subTest(target=target):
                code, out = run_ps(self.scripts / "git-safe.ps1", "push", "origin", target, cwd=self.repo)
                self.assertEqual(code, 1, out)
                self.assertIn("protected branch", out)

    def test_inherited_git_variables_are_refused(self):
        env = dict(os.environ, GIT_DIR=str(self.repo / ".git"))
        r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.scripts / "guard.ps1"), "status"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.repo, env=env, timeout=60)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("GIT_DIR", r.stdout + r.stderr)

GBK_WRAPPER = """param([string]$Script, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::GetEncoding(936)
if ([Console]::OutputEncoding.CodePage -ne 936) { exit 99 }
$ErrorActionPreference = "Continue"
$at = [array]::IndexOf($Rest, "-Message")
if ($at -ge 0) { & $Script $Rest[0] -Message $Rest[$at + 1] } else { & $Script @Rest }
exit $LASTEXITCODE
"""


@unittest.skipUnless(PWSH and os.name == "nt", "the console code page is a Windows concern")
class NonAsciiRepositoryPathTest(unittest.TestCase):
    """Real git under a GBK console: 5 Chinese characters are 15 UTF-8 bytes, which GBK cannot read back (#766)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name) / "张三的项目" / "repo"
        make_repo(self.repo)
        git(self.repo, "checkout", "-q", "-b", "feature/TASK-1-codex")
        (self.repo / "scripts/codex").mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.repo / "scripts/codex" / name)
        self.wrapper = Path(tmp.name) / "gbk.ps1"
        self.wrapper.write_text(GBK_WRAPPER, encoding="ascii")
        self.upstream = Path(tmp.name) / "upstream"
        make_repo(self.upstream)
        git(self.repo, "clone", "-q", self.upstream.as_posix(), "sources/子模块")

    def linked_worktree(self, branch):
        tree = self.repo.parent / "linked"
        git(self.repo, "worktree", "add", "-q", "-b", branch, str(tree))
        (tree / "scripts/codex").mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, tree / "scripts/codex" / name)
        return tree

    def run_script(self, name, *args, repo=None):
        r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.wrapper),
                            str((repo or self.repo) / "scripts/codex" / name), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.repo)
        return r.returncode, r.stdout + r.stderr

    def test_a_linked_worktree_marks_review_and_commits(self):
        # A linked worktree's git paths are absolute, so they carry the Chinese part of the path.
        tree = self.linked_worktree("feature/TASK-2-codex")
        (tree / "plain.txt").write_text("x\n")
        self.assertEqual(self.run_script("git-safe.ps1", "add", "plain.txt", repo=tree)[0], 0)
        code, out = self.run_script("guard.ps1", "mark-review", repo=tree)
        self.assertEqual(code, 0, out)
        code, out = self.run_script("git-safe.ps1", "commit", "-Message", "chore(TASK-2): add", repo=tree)
        self.assertEqual(code, 0, out)
        self.assertIn("plain.txt", git(tree, "show", "--name-only", "--format=", "HEAD"))

    def test_a_linked_worktree_sees_a_merge_in_progress(self):
        tree = self.linked_worktree("feature/TASK-2-codex")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "other")
        git(tree, "merge", "-q", "--no-ff", "--no-commit", "feature/TASK-1-codex")
        code, out = self.run_script("git-safe.ps1", "sync", repo=tree)
        self.assertNotEqual(code, 0, out)
        self.assertIn("MERGE_HEAD", out)

    def test_guard_status_runs(self):
        code, out = self.run_script("guard.ps1", "status")
        self.assertEqual(code, 0, out)

    def test_git_safe_adds_a_plain_file(self):
        (self.repo / "plain.txt").write_text("x\n")
        code, out = self.run_script("git-safe.ps1", "add", "plain.txt")
        self.assertEqual(code, 0, out)
        self.assertIn("plain.txt", git(self.repo, "diff", "--cached", "--name-only"))

    def test_git_safe_stages_a_registered_non_ascii_submodule(self):
        (self.repo / ".gitmodules").write_text(
            '[submodule "sources/子模块"]\n\tpath = sources/子模块\n\turl = https://github.com/o/r.git\n', encoding="utf-8")
        code, out = self.run_script("git-safe.ps1", "add", "sources/子模块")
        self.assertEqual(code, 0, out)
        self.assertIn("160000", git(self.repo, "ls-files", "-s", "--", "sources"))



@unittest.skipUnless(PWSH, "PowerShell is not available")
class PushTargetTest(unittest.TestCase):
    """A push is checked by what Git would really update, not only by its words (#772)."""

    BRANCH = "feature/TASK-9-claude"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.origin = Path(tmp.name) / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.origin)], check=True)
        self.repo = Path(tmp.name) / "project"
        make_repo(self.repo)
        git(self.repo, "branch", "-M", "master")
        git(self.repo, "remote", "add", "origin", self.origin.as_posix())
        git(self.repo, "push", "-q", "origin", "master")
        git(self.repo, "checkout", "-q", "-b", self.BRANCH)
        (self.repo / "work.txt").write_text("work\n")
        git(self.repo, "add", "work.txt")
        git(self.repo, "commit", "-q", "-m", "work")
        self.scripts = self.repo / ".mercury/packs/git-safe/scripts"
        self.scripts.mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(SCRIPTS / name, self.scripts / name)
        (self.repo / ".git/info/exclude").write_text(".mercury/\n")
        self.master = self.remote_ref("master")

    def remote_ref(self, branch):
        r = subprocess.run(["git", "-C", str(self.origin), "rev-parse", "--verify", "-q", f"refs/heads/{branch}"],
                           capture_output=True, text=True)
        return r.stdout.strip() or None

    def push(self, *args):
        return run_ps(self.scripts / "git-safe.ps1", "push", *args, cwd=self.repo)

    def test_an_explicit_task_branch_push_still_works_and_sets_the_upstream(self):
        code, out = self.push("-u", "origin", self.BRANCH)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.remote_ref(self.BRANCH), git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual(git(self.repo, "config", f"branch.{self.BRANCH}.remote").strip(), "origin")

    def test_a_push_without_a_refspec_is_refused(self):
        code, out = self.push("origin")
        self.assertEqual(code, 1, out)
        self.assertIn("Name what to push", out)

    def test_force_update_and_pattern_refspecs_are_refused(self):
        for spec, message in ((f"+HEAD:refs/heads/{self.BRANCH}", "Force-update"),
                              ("refs/heads/*:refs/heads/*", "Pattern")):
            with self.subTest(spec=spec):
                code, out = self.push("origin", spec)
                self.assertEqual(code, 1, out)
                self.assertIn(message, out)
        self.assertIsNone(self.remote_ref(self.BRANCH))

    def test_a_configured_push_mapping_cannot_reach_a_protected_branch(self):
        git(self.repo, "config", "remote.origin.push", f"refs/heads/{self.BRANCH}:refs/heads/master")
        # A branch name is mapped through remote.origin.push, so the words name the task branch while Git would
        # update master: only the dry run sees that.
        code, out = self.push("origin", self.BRANCH)
        self.assertEqual(code, 1, out)
        self.assertIn("protected branch", out)
        self.assertEqual(self.remote_ref("master"), self.master)
        # HEAD is not mapped (Git 2.43, measured here): it updates the branch of the same name.
        code, out = self.push("origin", "HEAD")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.remote_ref("master"), self.master)
        self.assertEqual(self.remote_ref(self.BRANCH), git(self.repo, "rev-parse", "HEAD").strip())

    def test_arguments_that_could_turn_the_dry_run_into_a_real_push_are_refused(self):
        git(self.repo, "config", "remote.origin.push", f"refs/heads/{self.BRANCH}:refs/heads/master")
        for args in (("--no-dry-run", "origin", self.BRANCH), ("--no-dry", "origin", self.BRANCH),
                     ("origin", "--", self.BRANCH)):
            with self.subTest(args=args):
                code, out = self.push(*args)
                self.assertEqual(code, 1, out)
                self.assertEqual(self.remote_ref("master"), self.master)
                self.assertIsNone(self.remote_ref(self.BRANCH))

    def test_an_option_value_that_looks_like_a_flag_is_kept(self):
        # "-q" here is the value of --push-option, not the quiet flag; dropping it would make the branch name the
        # repository. (The short -o cannot reach the script: PowerShell reads it as -OutVariable/-OutBuffer.)
        git(self.origin, "config", "receive.advertisePushOptions", "true")
        code, out = self.push("--push-option", "-q", "origin", self.BRANCH)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.remote_ref(self.BRANCH), git(self.repo, "rev-parse", "HEAD").strip())

    def test_the_pre_push_hook_runs_once_even_with_verify(self):
        hooks = self.repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        log = self.repo.parent / "hook-runs.txt"
        hook = hooks / "pre-push"
        hook.write_text(f'#!/bin/sh\necho run >> "{log.as_posix()}"\n', newline="\n")
        hook.chmod(0o755)
        code, out = self.push("--verify", "origin", self.BRANCH)
        self.assertEqual(code, 0, out)
        self.assertEqual(log.read_text().splitlines(), ["run"])

    def test_an_option_without_its_value_is_refused_before_any_push(self):
        log = self.repo.parent / "hook-runs.txt"
        hook = self.repo / ".git" / "hooks" / "pre-push"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text('#!/bin/sh\necho run >> "' + log.as_posix() + '"\n', newline="\n")
        hook.chmod(0o755)
        for option in ("--push-option", "--repo", "--receive-pack", "--exec"):
            with self.subTest(option=option):
                code, out = self.push("origin", self.BRANCH, option)
                self.assertEqual(code, 1, out)
                self.assertIn("needs a value", out)
        self.assertFalse(log.exists())                      # the dry run never ran the hook
        self.assertIsNone(self.remote_ref(self.BRANCH))

    def test_combined_short_options_still_push(self):
        code, out = self.push("-qu", "origin", self.BRANCH)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.remote_ref(self.BRANCH), git(self.repo, "rev-parse", "HEAD").strip())
        self.assertEqual(git(self.repo, "config", f"branch.{self.BRANCH}.remote").strip(), "origin")

    def test_a_quiet_push_and_an_up_to_date_push_both_succeed(self):
        code, out = self.push("-q", "origin", self.BRANCH)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.remote_ref(self.BRANCH), git(self.repo, "rev-parse", "HEAD").strip())
        code, out = self.push("origin", self.BRANCH)       # nothing new: porcelain reports "="
        self.assertEqual(code, 0, out)

    def test_a_push_git_would_reject_is_not_attempted(self):
        # The remote task branch moved on; a plain push would be a non-fast-forward.
        other = Path(self.repo.parent) / "other"
        git(self.repo.parent, "clone", "-q", self.origin.as_posix(), "other")
        git(other, "checkout", "-q", "-b", self.BRANCH)
        (other / "theirs.txt").write_text("theirs\n")
        git(other, "add", "theirs.txt")
        git(other, "commit", "-q", "-m", "theirs")
        git(other, "push", "-q", "origin", self.BRANCH)
        theirs = self.remote_ref(self.BRANCH)
        code, out = self.push("origin", self.BRANCH)
        self.assertEqual(code, 1, out)
        self.assertIn("Git said:", out)
        self.assertEqual(self.remote_ref(self.BRANCH), theirs)


if __name__ == "__main__":
    unittest.main()

