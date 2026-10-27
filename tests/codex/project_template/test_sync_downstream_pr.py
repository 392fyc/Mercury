from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/codex/sync-downstream-pr.py"
SPEC = importlib.util.spec_from_file_location("downstream_pr", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def green_checks():
    return [{"name": name, "status": "COMPLETED", "conclusion": "SUCCESS"}
            for name in MODULE.REQUIRED_CHECK_NAMES]


class MergeGates(unittest.TestCase):
    def test_publish_only_verifies_existing_pr_and_never_considers_merge(self):
        pr = {"number": 7, "headRefName": "codex/mercury-sync-a", "headRefOid": "b"}
        def fake_git(target, *args):
            if args[0] == "status" or args[0] == "merge-base":
                return ""
            return "a"
        with mock.patch.object(MODULE, "sync_module", return_value=object()), \
             mock.patch.object(MODULE, "git", side_effect=fake_git), \
             mock.patch.object(MODULE, "run", side_effect=[json.dumps([pr]), json.dumps([{"headSha": "b"}])]), \
             mock.patch.object(MODULE, "validate_pr_tree") as tree, \
             mock.patch.object(MODULE, "consider_merge") as merge:
            MODULE.update(Path("source"), Path("target"), "owner/repo", "develop", 43,
                          "argus-review[bot]", publish_only=True)
            tree.assert_called_once()
            merge.assert_not_called()

    def test_approval_must_match_current_head_and_named_independent_reviewer(self):
        review = {"user": {"login": "argus-review[bot]"}, "state": "APPROVED", "commit_id": "a"}
        self.assertTrue(MODULE.approval_ready([review], "a", "argus-review[bot]"))
        self.assertFalse(MODULE.approval_ready([review], "b", "argus-review[bot]"))
        self.assertFalse(MODULE.approval_ready([review], "a", "other"))

    def test_any_current_rejection_blocks_and_dismissal_removes_approval(self):
        reviews = [{"user": {"login": "argus-review[bot]"}, "state": "APPROVED", "commit_id": "a"},
                   {"user": {"login": "human"}, "state": "CHANGES_REQUESTED", "commit_id": "a"}]
        self.assertFalse(MODULE.approval_ready(reviews, "a", "argus-review[bot]"))
        reviews.append({"user": {"login": "human"}, "state": "DISMISSED", "commit_id": "a"})
        self.assertTrue(MODULE.approval_ready(reviews, "a", "argus-review[bot]"))
        reviews.append({"user": {"login": "argus-review[bot]"}, "state": "DISMISSED", "commit_id": "a"})
        self.assertFalse(MODULE.approval_ready(reviews, "a", "argus-review[bot]"))

    def test_no_checks_pending_failed_skipped_and_unknown_results_block(self):
        self.assertFalse(MODULE.checks_ready([]))
        self.assertFalse(MODULE.checks_ready([{"name": "unrelated", "status": "COMPLETED", "conclusion": "SUCCESS"}]))
        for conclusion in ["FAILURE", "SKIPPED", "CANCELLED", "NEUTRAL", "ACTION_REQUIRED", None]:
            checks = green_checks()
            checks[0]["conclusion"] = conclusion
            self.assertFalse(MODULE.checks_ready(checks))
        checks = green_checks()
        checks[0]["status"] = "IN_PROGRESS"
        self.assertFalse(MODULE.checks_ready(checks))
        self.assertTrue(MODULE.checks_ready(green_checks()))
        self.assertFalse(MODULE.checks_ready(green_checks() + [{"__typename": "StatusContext", "state": "PENDING"}]))
        self.assertTrue(MODULE.checks_ready(green_checks() + [{"__typename": "StatusContext", "state": "SUCCESS"}]))

    def test_merge_rechecks_tree_and_binds_exact_head_without_bypass(self):
        pr = {"number": 1, "headRefOid": "a", "statusCheckRollup": green_checks(),
              "mergeable": "MERGEABLE", "isDraft": False}
        approval = [{"user": {"login": "argus-review[bot]"}, "state": "APPROVED", "commit_id": "a"}]
        with mock.patch.object(MODULE, "validate_pr_tree") as tree, \
             mock.patch.object(MODULE, "api", return_value=approval), \
             mock.patch.object(MODULE, "current_checks", return_value=green_checks()), \
             mock.patch.object(MODULE, "run") as run:
            MODULE.consider_merge(Path("source"), Path("target"), "owner/repo", pr, "argus-review[bot]", None)
            tree.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[-2:], ["--match-head-commit", "a"])
            self.assertNotIn("--admin", command)

    def test_check_page_limit_refuses_merge_and_missing_checks_wait(self):
        for rollup, expected in [(None, []), ({"contexts": {
                "pageInfo": {"hasNextPage": False}, "nodes": green_checks()}}, green_checks())]:
            response = {"data": {"repository": {"object": {"statusCheckRollup": rollup}}}}
            with mock.patch.object(MODULE, "run", return_value=json.dumps(response)):
                self.assertEqual(MODULE.current_checks("o/r", "a"), expected)
        response = {"data": {"repository": {"object": {"statusCheckRollup": {"contexts": {
            "pageInfo": {"hasNextPage": True}, "nodes": green_checks()}}}}}}
        with mock.patch.object(MODULE, "run", return_value=json.dumps(response)):
            with self.assertRaisesRegex(MODULE.UpdateError, "page"):
                MODULE.current_checks("o/r", "a")

    def test_invalid_tree_never_merges_even_when_review_and_checks_pass(self):
        with mock.patch.object(MODULE, "validate_pr_tree", side_effect=MODULE.UpdateError("bad tree")), \
             mock.patch.object(MODULE, "api") as api, mock.patch.object(MODULE, "run") as run:
            with self.assertRaises(MODULE.UpdateError):
                MODULE.consider_merge(Path("s"), Path("t"), "o/r", {}, "argus-review[bot]", None)
            api.assert_not_called()
            run.assert_not_called()


class Diagnostics(unittest.TestCase):
    def test_failure_names_command_and_keeps_redacted_stderr_tail(self):
        token = "ghs_" + "a" * 30
        script = ("import sys; sys.stderr.write('fatal: unable to access "
                  f"https://x-access-token:{token}@github.com/o/r: denied'); sys.exit(3)")
        with self.assertRaises(MODULE.UpdateError) as caught:
            MODULE.run([sys.executable, "-c", script])
        message = str(caught.exception)
        self.assertIn("exit 3", message)
        self.assertIn(Path(sys.executable).name, message)
        self.assertIn("denied", message)
        self.assertNotIn(token, message)
        self.assertNotIn("x-access-token:", message)

    def test_long_arguments_and_auth_headers_are_not_echoed(self):
        described = MODULE.describe(["gh", "api", "graphql", "-f", "query=" + "x" * 500,
                                     "-H", "Authorization: Bearer ghp_" + "b" * 36])
        self.assertLess(len(described), 300)
        self.assertNotIn("x" * 100, described)
        self.assertNotIn("ghp_", described)

    def test_new_untracked_manifest_file_is_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            MODULE.git(repo, "init", "--initial-branch=develop")
            MODULE.git(repo, "config", "user.name", "Test")
            MODULE.git(repo, "config", "user.email", "test@example.invalid")
            (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
            (repo / ".codex").mkdir()
            (repo / ".codex/mercury-template.lock").write_text("old\n", encoding="utf-8")
            MODULE.git(repo, "add", "--", ".gitignore", ".codex")
            MODULE.git(repo, "commit", "-m", "initial")
            (repo / ".codex/mercury-template.lock").write_text("new\n", encoding="utf-8")
            (repo / ".codex/project").mkdir()
            (repo / ".codex/project/mercury-new-contract.md").write_text("new\n", encoding="utf-8")
            (repo / "ignored.txt").write_text("x\n", encoding="utf-8")
            self.assertEqual(MODULE.pending_paths(repo), [
                ".codex/mercury-template.lock", ".codex/project/mercury-new-contract.md"])
            # A managed file the manifest dropped is still reported, so `git add --` stages it.
            (repo / ".codex/mercury-template.lock").unlink()
            self.assertIn(".codex/mercury-template.lock", MODULE.pending_paths(repo))
            MODULE.git(repo, "add", "--", *MODULE.pending_paths(repo))
            self.assertEqual(MODULE.pending_paths(repo), [])


class GitTreeVerification(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name) / "source"
        self.target = Path(self.tmp.name) / "target"
        self.source.mkdir()
        self.target.mkdir()
        self.module = MODULE.sync_module(SCRIPT.parents[2])
        for repo in [self.source, self.target]:
            MODULE.git(repo, "init", "--initial-branch=develop")
            MODULE.git(repo, "config", "user.name", "Test")
            MODULE.git(repo, "config", "user.email", "test@example.invalid")
            MODULE.git(repo, "config", "core.autocrlf", "false")
        root = ".mercury/templates/codex-project"
        shutil.copytree(SCRIPT.parents[2] / root, self.source / root)
        MODULE.git(self.source, "add", "--", root)
        MODULE.git(self.source, "commit", "-m", "initial")
        old = MODULE.git(self.source, "rev-parse", "HEAD")
        old_template = self.module._load_template(self.source, old)
        self.module._apply(self.source, self.target, old_template,
                           self.module._lock_bytes(old_template, old))
        (self.target / "domain.txt").write_text("overlay", encoding="utf-8")
        MODULE.git(self.target, "add", "--", ".codex", "domain.txt")
        MODULE.git(self.target, "commit", "-m", "initial downstream")
        MODULE.git(self.target, "update-ref", "refs/remotes/origin/develop", "HEAD")
        MODULE.git(self.target, "remote", "add", "origin", str(self.target))
        template_file = self.source / root / "project/mercury-task-contract.md"
        template_file.write_bytes(template_file.read_bytes() + b"\nUpdated source contract.\n")
        MODULE.git(self.source, "add", "--", str(template_file.relative_to(self.source)))
        MODULE.git(self.source, "commit", "-m", "upstream update")
        self.new = MODULE.git(self.source, "rev-parse", "HEAD")
        MODULE.git(self.source, "update-ref", "refs/remotes/origin/develop", self.new)
        new_template = self.module._load_template(self.source, self.new)
        self.module._apply(self.source, self.target, new_template,
                           self.module._lock_bytes(new_template, self.new))
        MODULE.git(self.target, "add", "--", ".codex")
        MODULE.git(self.target, "commit", "-m", "generated update")

    def pr(self):
        head = MODULE.git(self.target, "rev-parse", "HEAD")
        MODULE.git(self.target, "update-ref", "refs/pull/1/head", head)
        return {"number": 1, "headRefOid": head, "baseRefName": "develop",
                "headRefName": f"codex/mercury-sync-{self.new}", "isCrossRepository": False}

    def test_real_generated_upgrade_is_accepted(self):
        MODULE.validate_pr_tree(self.source, self.target, self.pr(), self.module)
        self.assertEqual((self.target / "domain.txt").read_text(), "overlay")

    def test_domain_change_is_rejected(self):
        (self.target / "domain.txt").write_text("changed", encoding="utf-8")
        MODULE.git(self.target, "add", "--", "domain.txt")
        MODULE.git(self.target, "commit", "-m", "unrelated change")
        with self.assertRaisesRegex(MODULE.UpdateError, "outside"):
            MODULE.validate_pr_tree(self.source, self.target, self.pr(), self.module)

    def test_git_path_containing_newline_cannot_be_split_into_allowed_paths(self):
        blob = MODULE.git(self.target, "rev-parse", "HEAD:.codex/project/mercury-task-contract.md")
        unusual = ".codex\nrogue"
        # Git plumbing can represent this path even on Windows, where a normal
        # working-tree file with control characters cannot be created.
        listing = subprocess.run(["git", "-C", str(self.target), "ls-tree", "-z", "HEAD"],
                                 capture_output=True, check=True).stdout
        listing += f"100644 blob {blob}\t{unusual}\0".encode()
        tree = subprocess.run(["git", "-C", str(self.target), "mktree", "-z"],
                              input=listing, capture_output=True, check=True).stdout.decode().strip()
        commit = MODULE.git(self.target, "commit-tree", tree, "-p", "HEAD", "-m", "newline path")
        MODULE.git(self.target, "update-ref", "HEAD", commit)
        with self.assertRaisesRegex(MODULE.UpdateError, "outside"):
            MODULE.validate_pr_tree(self.source, self.target, self.pr(), self.module)

    def test_generated_tamper_and_fork_are_rejected(self):
        pr = self.pr()
        pr["isCrossRepository"] = True
        with self.assertRaisesRegex(MODULE.UpdateError, "same-repository"):
            MODULE.validate_pr_tree(self.source, self.target, pr, self.module)
        path = self.target / ".codex/project/mercury-task-contract.md"
        path.write_bytes(b"manually changed\n")
        MODULE.git(self.target, "add", "--", str(path.relative_to(self.target)))
        MODULE.git(self.target, "commit", "-m", "manual tamper")
        with self.assertRaisesRegex(MODULE.UpdateError, "differs"):
            MODULE.validate_pr_tree(self.source, self.target, self.pr(), self.module)

    def test_lock_reformatting_does_not_authorize_merge(self):
        path = self.target / ".codex/mercury-template.lock"
        path.write_text(json.dumps(json.loads(path.read_bytes())), encoding="utf-8")
        MODULE.git(self.target, "add", "--", str(path.relative_to(self.target)))
        MODULE.git(self.target, "commit", "-m", "noncanonical lock")
        with self.assertRaises(self.module.SyncError):
            MODULE.validate_pr_tree(self.source, self.target, self.pr(), self.module)


if __name__ == "__main__":
    unittest.main()
