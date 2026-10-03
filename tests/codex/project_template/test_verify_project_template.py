from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SYNC_SCRIPT = REPOSITORY_ROOT / "scripts" / "codex" / "sync-project-template.py"
VERIFY_SCRIPT = REPOSITORY_ROOT / "scripts" / "codex" / "verify-project-template.py"


class ProjectTemplateVerifyTests(unittest.TestCase):
    def test_unmanaged_reserved_agent_is_rejected_without_touching_overlay(self) -> None:
        agents = self.target / ".codex/agents"
        agents.mkdir()
        overlay = agents / "sot-domain.toml"
        overlay.write_bytes(b"domain overlay\n")
        extra = agents / "MERCURY-unmanaged.toml"
        extra.write_bytes(b"unowned active agent\n")
        result = self._verify()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("reserved Mercury path", result.stderr)
        self.assertEqual(overlay.read_bytes(), b"domain overlay\n")

    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.root = Path(self._temporary_directory.name)
        self.source = self.root / "mercury"
        self.target = self.root / "downstream"
        self.source.mkdir()
        self.target.mkdir()

        scripts = self.source / "scripts" / "codex"
        scripts.mkdir(parents=True)
        shutil.copy2(SYNC_SCRIPT, scripts / SYNC_SCRIPT.name)
        shutil.copy2(VERIFY_SCRIPT, scripts / VERIFY_SCRIPT.name)

        self.template = self.source / ".mercury" / "templates" / "codex-project"
        project = self.template / "project"
        project.mkdir(parents=True)
        self.manifest_path = self.template / "manifest.json"
        self.template_file = project / "mercury-task-contract.md"
        self.template_file.write_bytes(b"# Task contract v1\n")
        self._write_manifest()

        self._git("init", "--quiet")
        self._git("config", "user.name", "Template Verify Test")
        self._git("config", "user.email", "template-verify@example.invalid")
        self.commit = self._commit("initial Mercury template")
        self._git("update-ref", "refs/remotes/origin/develop", self.commit)

        applied = self._sync("apply", self.commit)
        self.assertEqual(applied.returncode, 0, applied.stderr)

    def _write_manifest(self) -> None:
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_repo": "392fyc/Mercury",
                    "lock": "mercury-template.lock",
                    "files": [
                        {
                            "source": "project/mercury-task-contract.md",
                            "destination": "project/mercury-task-contract.md",
                        }
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=self.source,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def _commit(self, message: str) -> str:
        self._git("add", "-A")
        self._git("commit", "--quiet", "-m", message)
        return self._git("rev-parse", "HEAD")

    def _sync(self, command: str, commit: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.source / "scripts" / "codex" / SYNC_SCRIPT.name),
                command,
                "--target",
                str(self.target),
                "--source-commit",
                commit,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def _verify(
        self, *arguments: str
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.source / "scripts" / "codex" / VERIFY_SCRIPT.name),
                "--target",
                str(self.target),
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _snapshot(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def test_current_canonical_template_verifies_read_only(self) -> None:
        before = self._snapshot(self.target)

        result = self._verify()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verified Mercury template source", result.stdout)
        self.assertEqual(before, self._snapshot(self.target))

    def test_tampered_managed_file_fails_without_changing_target(self) -> None:
        managed_file = self.target / ".codex" / "project" / "mercury-task-contract.md"
        managed_file.write_bytes(b"edited downstream content\n")
        before = self._snapshot(self.target)

        result = self._verify()

        self.assertEqual(result.returncode, 1)
        self.assertIn("template drift detected", result.stderr)
        self.assertEqual(before, self._snapshot(self.target))

    def test_unmerged_source_commit_is_rejected(self) -> None:
        self.template_file.write_bytes(b"# Task contract on a private commit\n")
        private_commit = self._commit("unmerged template change")
        applied = self._sync("apply", private_commit)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        before = self._snapshot(self.target)

        result = self._verify()

        self.assertEqual(result.returncode, 2)
        self.assertIn("not an ancestor", result.stderr)
        self.assertEqual(before, self._snapshot(self.target))

    def test_canonical_historical_lock_is_accepted_when_source_is_an_ancestor(self) -> None:
        old_commit = self.commit
        self.template_file.write_bytes(b"# Task contract v2\n")
        new_upstream_commit = self._commit("advance trusted upstream")
        self._git("update-ref", "refs/remotes/origin/develop", new_upstream_commit)
        before = self._snapshot(self.target)

        result = self._verify()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(old_commit, result.stdout)
        self.assertEqual(before, self._snapshot(self.target))

    def test_noncanonical_old_lock_is_rejected(self) -> None:
        lock_path = self.target / ".codex" / "mercury-template.lock"
        lock = json.loads(lock_path.read_bytes())
        lock_path.write_text(json.dumps(lock, indent=4) + "\n", encoding="utf-8")
        before = self._snapshot(self.target)

        result = self._verify()

        self.assertEqual(result.returncode, 2)
        self.assertIn("not the canonical lock", result.stderr)
        self.assertEqual(before, self._snapshot(self.target))

    def test_verifier_does_not_execute_downstream_code(self) -> None:
        marker = self.root / "downstream-code-ran"
        executable = self.target / ".codex" / "project" / "unexpected.py"
        executable.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
            encoding="utf-8",
        )

        result = self._verify()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertTrue(executable.exists())


if __name__ == "__main__":
    unittest.main()
