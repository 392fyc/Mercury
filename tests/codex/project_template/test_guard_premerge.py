from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


GUARD = Path(__file__).resolve().parents[3] / "scripts/codex/guard.ps1"


GH_FUNCTION = r'''
function global:gh {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GhArgs)
  $global:LASTEXITCODE = 0
  $fixture = Get-Content -LiteralPath $env:GUARD_FIXTURE -Raw | ConvertFrom-Json
  if ($GhArgs.Count -ge 2 -and $GhArgs[0] -eq "auth" -and $GhArgs[1] -eq "status") {
    return
  }
  if ($GhArgs.Count -ge 2 -and $GhArgs[0] -eq "repo" -and $GhArgs[1] -eq "view") {
    Write-Output '{"owner":{"login":"392fyc"},"name":"Mercury"}'
    return
  }
  if ($GhArgs.Count -ge 2 -and $GhArgs[0] -eq "pr" -and $GhArgs[1] -eq "view") {
    Write-Output ($fixture.pr | ConvertTo-Json -Depth 30 -Compress)
    return
  }
  if ($GhArgs.Count -ge 2 -and $GhArgs[0] -eq "api" -and $GhArgs[1] -eq "graphql") {
    $queryArg = @($GhArgs | Where-Object { $_ -like "query=*" })[0]
    if (-not $queryArg) { throw "missing GraphQL query" }
    $query = $queryArg.Substring(6)
    $after = ""
    foreach ($argument in $GhArgs) {
      if ($argument -like "after=*") { $after = $argument.Substring(6) }
    }
    $kind = if ($query -match "statusCheckRollup") { "checks" }
      elseif ($query -match "reviews\(first") { "reviews" }
      elseif ($query -match "reviewThreads") { "threads" }
      else { throw "unknown GraphQL fixture query" }
    $pages = @($fixture.pages.$kind)
    $pageIndex = 0
    if (-not [string]::IsNullOrEmpty($after)) {
      $found = $false
      for ($i = 0; $i -lt $pages.Count - 1; $i++) {
        $previousCursor = if ($kind -eq "checks") {
          $pages[$i].data.repository.object.statusCheckRollup.contexts.pageInfo.endCursor
        } elseif ($kind -eq "reviews") {
          $pages[$i].data.repository.pullRequest.reviews.pageInfo.endCursor
        } else {
          $pages[$i].data.repository.pullRequest.reviewThreads.pageInfo.endCursor
        }
        if ($previousCursor -eq $after) {
          $pageIndex = $i + 1
          $found = $true
          break
        }
      }
      if (-not $found) { throw "unexpected pagination cursor" }
    }
    if ($pageIndex -ge $pages.Count) { throw "fixture page is missing" }
    Add-Content -LiteralPath $env:GUARD_CALL_LOG -Value ("{0}:{1}" -f $kind, $after) -Encoding utf8
    $page = $pages[$pageIndex]
    Write-Output ($page | ConvertTo-Json -Depth 30 -Compress)
    return
  }
  throw "unexpected gh command"
}

try {
  $receiptArgs = @{}
  if (-not [string]::IsNullOrEmpty($env:GUARD_RECEIPT_ARGS)) {
    $receiptParts = @($env:GUARD_RECEIPT_ARGS -split "\|")
    $receiptArgs[$receiptParts[0].TrimStart("-")] = $receiptParts[1]
  }
  & $env:GUARD_SCRIPT pre-merge -PullRequestNumber 123 @receiptArgs
  exit 0
} catch {
  [Console]::Error.WriteLine((($_ | Out-String) + $_.ScriptStackTrace))
  exit 1
}
'''


def connection(nodes=None, *, has_next=False, cursor=None, page_info=True):
    value = {"nodes": [] if nodes is None else nodes}
    if page_info:
        value["pageInfo"] = {"hasNextPage": has_next, "endCursor": cursor}
    return value


def check_page(nodes=None, *, has_next=False, cursor=None, rollup=True, page_info=True):
    result = {"data": {"repository": {"object": {}}}}
    if rollup:
        result["data"]["repository"]["object"]["statusCheckRollup"] = {
            "contexts": connection(nodes, has_next=has_next, cursor=cursor, page_info=page_info)
        }
    else:
        result["data"]["repository"]["object"]["statusCheckRollup"] = None
    return result


def review_page(nodes=None, *, has_next=False, cursor=None, page_info=True):
    return {"data": {"repository": {"pullRequest": {
        "reviews": connection(nodes, has_next=has_next, cursor=cursor, page_info=page_info)
    }}}}


def thread_page(nodes=None, *, has_next=False, cursor=None, page_info=True):
    return {"data": {"repository": {"pullRequest": {
        "reviewThreads": connection(nodes, has_next=has_next, cursor=cursor, page_info=page_info)
    }}}}


def success_check():
    return {"__typename": "CheckRun", "name": "test", "status": "COMPLETED", "conclusion": "SUCCESS"}


def review(state, reviewer="reviewer", submitted_at="2026-10-03T00:00:00Z"):
    return {"state": state, "submittedAt": submitted_at, "author": {"login": reviewer}}


class GuardPreMergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mercury-guard-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self._git("init", "--initial-branch=lane/guard/621-test", str(self.root))
        self._git("config", "user.name", "Guard Test")
        self._git("config", "user.email", "guard@example.invalid")
        (self.root / "tracked.txt").write_text("fixture\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "fixture")
        self.head = self._git("rev-parse", "HEAD").strip()
        script_dir = self.root / "scripts" / "codex"
        script_dir.mkdir(parents=True)
        shutil.copy2(GUARD, script_dir / "guard.ps1")
        self.wrapper = self.root / "run-guard.ps1"
        self.wrapper.write_text(GH_FUNCTION, encoding="utf-8")
        self.fixture_path = self.root / "fixture.json"
        self.call_log = self.root / "calls.log"
        self.receipt_path = self.root / "native-review.json"
        self.env = os.environ.copy()
        self.env.update({
            "GUARD_SCRIPT": str(script_dir / "guard.ps1"),
            "GUARD_FIXTURE": str(self.fixture_path),
            "GUARD_CALL_LOG": str(self.call_log),
            "GUARD_RECEIPT_ARGS": "",
        })
        self.pwsh = shutil.which("pwsh") or shutil.which("powershell")
        if not self.pwsh:
            self.skipTest("PowerShell is required for guard integration fixtures")
        self.setUpFixture()

    def _git(self, *args):
        result = subprocess.run(["git", "-C", str(self.root), *args], check=True,
                                capture_output=True, text=True, encoding="utf-8")
        return result.stdout

    def setUpFixture(self):
        self.fixture = {
            "pr": {
                "number": 123,
                "state": "OPEN",
                "isDraft": False,
                "mergeable": "MERGEABLE",
                "reviewDecision": "REVIEW_REQUIRED",
                "statusCheckRollup": [success_check()],
                "headRefName": "lane/guard/621-test",
                "headRefOid": self.head,
                "baseRefName": "develop",
            },
            "pages": {
                "checks": [check_page([success_check()])],
                "reviews": [review_page([])],
                "threads": [thread_page([])],
            },
        }
        self.write_fixture()
        self.write_receipt()

    def write_fixture(self):
        self.fixture_path.write_text(json.dumps(self.fixture), encoding="utf-8")

    def write_receipt(self, **changes):
        receipt = {
            "repository": "392fyc/Mercury",
            "pull_request": 123,
            "head": self.head,
            "verdict": "pass",
            "reviewer": "native-subagent",
            "agent_id": "agent-621",
            "reviewed_at": "2026-10-03T00:00:00Z",
            "findings": [],
        }
        receipt.update(changes)
        self.receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    def run_guard(self, *, receipt=True):
        self.env["GUARD_RECEIPT_ARGS"] = f"-NativeReviewReceipt|{self.receipt_path}" if receipt else ""
        if self.call_log.exists():
            self.call_log.unlink()
        command = [self.pwsh, "-NoLogo", "-NoProfile", "-NonInteractive",
                   "-ExecutionPolicy", "Bypass", "-File", str(self.wrapper)]
        return subprocess.run(command, cwd=self.root, env=self.env, capture_output=True,
                              text=True, encoding="utf-8", timeout=30)

    def assert_rejected(self, *, receipt=True):
        result = self.run_guard(receipt=receipt)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def test_default_mode_still_requires_github_approval(self):
        self.assert_rejected(receipt=False)
        self.fixture["pr"]["reviewDecision"] = "APPROVED"
        self.write_fixture()
        self.assertEqual(self.run_guard(receipt=False).returncode, 0)

    def test_valid_receipt_allows_missing_approval_with_green_complete_ci(self):
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("native_review_receipt=validated", result.stdout)
        self.assertNotIn("APPROVED", result.stdout)
        self.assertNotIn("errors", self.fixture["pages"]["checks"][0])

    def test_receipt_identity_and_findings_are_exact(self):
        for changes in [
            {"head": "0" * 40},
            {"repository": "other/Mercury"},
            {"pull_request": 124},
            {"verdict": "fail"},
            {"findings": [{"issue": "finding"}]},
            {"findings": None},
        ]:
            with self.subTest(changes=changes):
                self.write_receipt(**changes)
                self.assert_rejected()
        self.write_receipt()
        self.fixture["pr"]["headRefOid"] = "0" * 40
        self.write_fixture()
        self.assert_rejected()

    def test_native_receipt_does_not_override_pr_review_state(self):
        for changes in [
            {"state": "CLOSED"},
            {"isDraft": True},
            {"mergeable": "CONFLICTING"},
        ]:
            with self.subTest(changes=changes):
                self.fixture["pr"].update(changes)
                self.write_fixture()
                self.assert_rejected()
                self.fixture["pr"].update({"state": "OPEN", "isDraft": False, "mergeable": "MERGEABLE"})
        self.fixture["pr"]["reviewDecision"] = "CHANGES_REQUESTED"
        self.write_fixture()
        self.assert_rejected()

    def test_wrong_base_branch_is_rejected(self):
        self.fixture["pr"]["baseRefName"] = "main"
        self.write_fixture()
        self.assert_rejected()

    def test_native_receipt_never_overrides_active_reviewer_rejection(self):
        self.fixture["pages"]["reviews"] = [review_page([review("CHANGES_REQUESTED"), review("COMMENT")])]
        self.write_fixture()
        self.assert_rejected()

    def test_native_review_history_is_paginated_before_bypass(self):
        self.fixture["pages"]["reviews"] = [
            review_page([review("COMMENT")], has_next=True, cursor="review-page-2"),
            review_page([review("CHANGES_REQUESTED", reviewer="second-reviewer")]),
        ]
        self.write_fixture()
        self.assert_rejected()
        self.assertIn("reviews:review-page-2", self.call_log.read_text(encoding="utf-8"))

    def test_same_timestamp_rejection_wins_over_approval(self):
        timestamp = "2026-10-03T00:00:00Z"
        self.fixture["pages"]["reviews"] = [review_page([
            review("APPROVED", submitted_at=timestamp),
            review("CHANGES_REQUESTED", submitted_at=timestamp),
        ])]
        self.write_fixture()
        self.assert_rejected()

    def test_later_substantive_dismissal_clears_rejection(self):
        self.fixture["pages"]["reviews"] = [review_page([
            review("CHANGES_REQUESTED", submitted_at="2026-10-02T00:00:00Z"),
            review("COMMENT", submitted_at="2026-10-03T00:00:00Z"),
            review("DISMISSED", submitted_at="2026-10-04T00:00:00Z"),
        ])]
        self.write_fixture()
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_resolved_and_unresolved_threads_both_block_native_bypass(self):
        for thread in [{"isResolved": True}, {"isResolved": False}]:
            with self.subTest(thread=thread):
                self.fixture["pages"]["threads"] = [thread_page([thread])]
                self.write_fixture()
                self.assert_rejected()

    def test_default_allows_resolved_thread_but_rejects_unresolved_thread(self):
        self.fixture["pr"]["reviewDecision"] = "APPROVED"
        self.fixture["pages"]["threads"] = [thread_page([{"isResolved": True}])]
        self.write_fixture()
        self.assertEqual(self.run_guard(receipt=False).returncode, 0)
        self.fixture["pages"]["threads"] = [thread_page([{"isResolved": False}])]
        self.write_fixture()
        self.assert_rejected(receipt=False)

    def test_review_thread_pagination_counts_resolved_threads(self):
        self.fixture["pages"]["threads"] = [
            thread_page([], has_next=True, cursor="thread-page-2"),
            thread_page([{"isResolved": True}]),
        ]
        self.write_fixture()
        result = self.assert_rejected()
        calls = self.call_log.read_text(encoding="utf-8")
        self.assertIn("threads:thread-page-2", calls, result.stderr + calls)

    def test_missing_or_incomplete_thread_connection_fails_closed(self):
        self.fixture["pages"]["threads"] = [thread_page([], page_info=False)]
        self.write_fixture()
        self.assert_rejected()
        self.fixture["pages"]["threads"] = [thread_page([])]
        del self.fixture["pages"]["threads"][0]["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        self.write_fixture()
        self.assert_rejected()

    def test_native_ci_requires_actual_successes_and_full_pagination(self):
        bad_pages = [
            [check_page([], rollup=False)],
            [check_page([])],
            [check_page([dict(success_check(), conclusion="SKIPPED")])],
            [check_page([dict(success_check(), conclusion="NEUTRAL")])],
            [check_page([{"__typename": "FutureCheck", "name": "unknown"}])],
            [check_page([success_check()], page_info=False)],
            [check_page([success_check()], has_next=True, cursor=None)],
            [check_page([success_check()], has_next=True, cursor="check-page-2"),
             check_page([dict(success_check(), conclusion="FAILURE")])],
        ]
        for pages in bad_pages:
            with self.subTest(pages=pages):
                self.fixture["pages"]["checks"] = pages
                self.write_fixture()
                self.assert_rejected()
        self.fixture["pages"]["checks"] = [check_page([success_check()])]
        self.write_fixture()
        self.assertEqual(self.run_guard().returncode, 0)
        self.fixture["pages"]["checks"] = [
            check_page([success_check()], has_next=True, cursor="check-page-2"),
            check_page([success_check()]),
        ]
        self.write_fixture()
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("checks:check-page-2", self.call_log.read_text(encoding="utf-8"))

    def test_default_mode_keeps_existing_skipped_and_neutral_check_behavior(self):
        self.fixture["pr"]["reviewDecision"] = "APPROVED"
        self.fixture["pr"]["statusCheckRollup"] = [
            dict(success_check(), conclusion="SKIPPED"),
            dict(success_check(), conclusion="NEUTRAL"),
        ]
        self.write_fixture()
        self.assertEqual(self.run_guard(receipt=False).returncode, 0)

    def test_graphql_errors_and_incomplete_review_pagination_fail_closed(self):
        page = review_page([])
        page["errors"] = [{"message": "fixture failure"}]
        self.fixture["pages"]["checks"] = [page]
        self.write_fixture()
        self.assert_rejected()
        self.fixture["pages"]["checks"] = [check_page([success_check()])]
        self.fixture["pages"]["reviews"] = [review_page([], page_info=False)]
        self.write_fixture()
        self.assert_rejected()


if __name__ == "__main__":
    unittest.main()
