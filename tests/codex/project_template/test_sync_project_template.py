from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPOSITORY_ROOT / "scripts" / "codex" / "sync-project-template.py"


def _load_sync_module():
    module_name = "mercury_sync_project_template_under_test"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load sync-project-template.py for unit tests")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


SYNC_MODULE = _load_sync_module()


class SyncImplementationUnitTests(unittest.TestCase):
    def test_git_subprocess_has_timeout_and_timeout_error_is_sanitized(self) -> None:
        expired = subprocess.TimeoutExpired(
            cmd=["git", "secret-argument"], timeout=30
        )
        with mock.patch.object(
            SYNC_MODULE.subprocess, "run", side_effect=expired
        ) as run:
            with self.assertRaises(SYNC_MODULE.SyncError) as caught:
                SYNC_MODULE._run_git(Path("repository"), "cat-file", "-t", "secret")

        self.assertIn("timed out", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertTrue(caught.exception.__suppress_context__)
        rendered = "".join(
            traceback.format_exception(
                type(caught.exception), caught.exception, caught.exception.__traceback__
            )
        )
        self.assertNotIn("secret", rendered)
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_git_os_error_does_not_retain_sensitive_exception_context(self) -> None:
        with mock.patch.object(
            SYNC_MODULE.subprocess,
            "run",
            side_effect=OSError("secret executable or environment detail"),
        ):
            with self.assertRaises(SYNC_MODULE.SyncError) as caught:
                SYNC_MODULE._run_git(
                    Path("secret-repository"), "cat-file", "-t", "secret-object"
                )

        self.assertEqual(str(caught.exception), "cannot start git command")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        rendered = "".join(
            traceback.format_exception(
                type(caught.exception), caught.exception, caught.exception.__traceback__
            )
        )
        self.assertNotIn("secret", rendered)

    def test_git_nonzero_exit_does_not_expose_stderr_or_arguments(self) -> None:
        completed = subprocess.CompletedProcess(
            args=["git", "secret-argument"],
            returncode=17,
            stdout=b"",
            stderr=b"secret stderr from git",
        )
        with mock.patch.object(SYNC_MODULE.subprocess, "run", return_value=completed):
            with self.assertRaises(SYNC_MODULE.SyncError) as caught:
                SYNC_MODULE._run_git(
                    Path("secret-repository"), "cat-file", "-t", "secret-object"
                )

        self.assertEqual(
            str(caught.exception), "git command failed with exit code 17"
        )
        self.assertIsNone(caught.exception.__cause__)

    def test_git_environment_uses_only_cross_platform_allowlist(self) -> None:
        provided = {
            "Path": "path-value",
            "systemroot": "system-root",
            "WINDIR": "windows-directory",
            "HOME": "home-directory",
            "USERPROFILE": "user-profile",
            "TMP": "tmp-directory",
            "temp": "temp-directory",
            "LANG": "C.UTF-8",
            "lc_all": "C",
            "HTTPS_PROXY": "must-not-pass",
            "SSL_CERT_FILE": "must-not-pass",
            "SSH_AUTH_SOCK": "must-not-pass",
            "GIT_DIR": "must-not-pass",
            "UNRELATED": "must-not-pass",
        }
        with mock.patch.dict(SYNC_MODULE.os.environ, provided, clear=True):
            cleaned = SYNC_MODULE._clean_git_environment()

        self.assertEqual(
            {key.upper() for key in cleaned},
            {
                "PATH",
                "SYSTEMROOT",
                "WINDIR",
                "HOME",
                "USERPROFILE",
                "TMP",
                "TEMP",
                "LANG",
                "LC_ALL",
            },
        )
        normalized = {key.upper(): value for key, value in cleaned.items()}
        self.assertEqual(normalized["PATH"], "path-value")
        self.assertEqual(normalized["SYSTEMROOT"], "system-root")

    def test_atomic_replace_rechecks_paths_in_security_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_root = Path(temporary_directory) / ".codex"
            destination = codex_root / "agents" / "mercury-dev.toml"
            destination.parent.mkdir(parents=True)
            planned_state = SYNC_MODULE._file_state(destination)
            events: list[str] = []

            real_guard = SYNC_MODULE._assert_directory_chain_safe
            real_mkstemp = SYNC_MODULE.tempfile.mkstemp
            real_destination_check = SYNC_MODULE._assert_existing_destination_safe
            real_replace = SYNC_MODULE.os.replace
            real_result_check = SYNC_MODULE._assert_replaced_file_safe

            def guard(*args, **kwargs):
                events.append("guard")
                return real_guard(*args, **kwargs)

            def make_temp(*args, **kwargs):
                events.append("mkstemp")
                return real_mkstemp(*args, **kwargs)

            def destination_check(*args, **kwargs):
                events.append("destination-check")
                return real_destination_check(*args, **kwargs)

            def replace(*args, **kwargs):
                events.append("replace")
                return real_replace(*args, **kwargs)

            def result_check(*args, **kwargs):
                events.append("result-check")
                return real_result_check(*args, **kwargs)

            with (
                mock.patch.object(
                    SYNC_MODULE, "_assert_directory_chain_safe", side_effect=guard
                ),
                mock.patch.object(
                    SYNC_MODULE.tempfile, "mkstemp", side_effect=make_temp
                ),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_existing_destination_safe",
                    side_effect=destination_check,
                ),
                mock.patch.object(SYNC_MODULE.os, "replace", side_effect=replace),
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_replaced_file_safe",
                    side_effect=result_check,
                ),
            ):
                SYNC_MODULE._atomic_replace(
                    destination, b"generated\n", codex_root, planned_state
                )

            self.assertEqual(
                events,
                [
                    "guard",
                    "mkstemp",
                    "guard",
                    "destination-check",
                    "replace",
                    "result-check",
                ],
            )
            self.assertEqual(destination.read_bytes(), b"generated\n")

    def test_atomic_replace_aborts_when_destination_recheck_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_root = Path(temporary_directory) / ".codex"
            destination = codex_root / "agents" / "mercury-dev.toml"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"before\n")
            planned_state = SYNC_MODULE._file_state(destination)
            with (
                mock.patch.object(
                    SYNC_MODULE,
                    "_assert_existing_destination_safe",
                    side_effect=SYNC_MODULE.SyncError("destination changed"),
                ),
                mock.patch.object(SYNC_MODULE.os, "replace") as replace,
            ):
                with self.assertRaises(SYNC_MODULE.SyncError):
                    SYNC_MODULE._atomic_replace(
                        destination, b"after\n", codex_root, planned_state
                    )

            replace.assert_not_called()
            self.assertEqual(destination.read_bytes(), b"before\n")

    def test_atomic_replace_does_not_overwrite_file_appearing_after_missing_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_root = Path(temporary_directory) / ".codex"
            destination = codex_root / "agents" / "mercury-dev.toml"
            destination.parent.mkdir(parents=True)
            planned_state = SYNC_MODULE._file_state(destination)
            appeared_bytes = b"appeared after planning\n"
            destination.write_bytes(appeared_bytes)

            with self.assertRaises(SYNC_MODULE.SyncError):
                SYNC_MODULE._atomic_replace(
                    destination,
                    b"generated\n",
                    codex_root,
                    planned_state,
                )

            self.assertEqual(destination.read_bytes(), appeared_bytes)
            self.assertEqual(
                [path.name for path in destination.parent.iterdir()],
                [destination.name],
            )

    def test_unlink_does_not_delete_file_replaced_after_planning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            codex_root = Path(temporary_directory) / ".codex"
            destination = codex_root / "agents" / "mercury-old.toml"
            destination.parent.mkdir(parents=True)
            destination.write_bytes(b"planned old content\n")
            planned_state = SYNC_MODULE._file_state(destination)
            destination.unlink()
            replacement_bytes = b"ordinary replacement\n"
            destination.write_bytes(replacement_bytes)

            with self.assertRaises(SYNC_MODULE.SyncError):
                SYNC_MODULE._unlink_if_unchanged(
                    destination, planned_state, codex_root
                )

            self.assertEqual(destination.read_bytes(), replacement_bytes)

    def test_replaced_file_validation_rejects_hardlink_and_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            hardlink = root / "hardlink"
            peer = root / "peer"
            peer.write_bytes(b"shared\n")
            os.link(peer, hardlink)
            with self.assertRaises(SYNC_MODULE.SyncError):
                SYNC_MODULE._assert_replaced_file_safe(hardlink)

            directory = root / "directory"
            directory.mkdir()
            with self.assertRaises(SYNC_MODULE.SyncError):
                SYNC_MODULE._assert_replaced_file_safe(directory)


class ProjectTemplateSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._temporary_directory.cleanup)
        self.root = Path(self._temporary_directory.name)
        self.source = self.root / "source"
        self.target = self.root / "target"
        self.source.mkdir()
        self.target.mkdir()

        script_destination = self.source / "scripts" / "codex" / SCRIPT.name
        script_destination.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, script_destination)
        self.script = script_destination

        template = self.source / ".mercury" / "templates" / "codex-project"
        (template / "agents").mkdir(parents=True)
        (template / "rules").mkdir()
        (template / "project").mkdir()
        (template / "agents" / "mercury-dev.toml").write_bytes(
            b'name = "mercury-dev"\n'
        )
        (template / "project" / "mercury-task-contract.md").write_bytes(
            b"# Task contract\n"
        )
        (template / "rules" / "mercury-git-safety.rules").write_bytes(
            b'prefix_rule(pattern=["git", "push"], decision="forbidden")\n'
        )
        self.template = template
        self.manifest_path = template / "manifest.json"
        self._write_manifest(self._default_files())

        self._git("init", "--quiet")
        self._git("config", "user.name", "Template Test")
        self._git("config", "user.email", "template-test@example.invalid")
        self.commit = self._commit("initial fixture")

    @staticmethod
    def _default_files() -> list[dict[str, str]]:
        return [
            {
                "source": "agents/mercury-dev.toml",
                "destination": "agents/mercury-dev.toml",
            },
            {
                "source": "project/mercury-task-contract.md",
                "destination": "project/mercury-task-contract.md",
            },
            {
                "source": "rules/mercury-git-safety.rules",
                "destination": "rules/mercury-git-safety.rules",
            },
        ]

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

    def _write_manifest(
        self,
        files: list[dict[str, str]],
        *,
        lock: str = "mercury-template.lock",
    ) -> None:
        self.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source_repo": "392fyc/Mercury",
                    "lock": lock,
                    "files": files,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def _run(
        self,
        command: str,
        *,
        commit: str | None = None,
        target: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(self.script),
                command,
                "--target",
                str(target or self.target),
                "--source-commit",
                commit or self.commit,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def _make_directory_link(self, link: Path, target: Path) -> None:
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as symlink_error:
            if os.name != "nt":
                self.skipTest(f"directory symlink creation is unavailable: {symlink_error}")
            junction = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            if junction.returncode != 0:
                self.skipTest(
                    "directory symlink and junction creation are unavailable: "
                    + junction.stderr.strip()
                )
        self.addCleanup(self._remove_directory_link, link)

    @staticmethod
    def _remove_directory_link(link: Path) -> None:
        if os.path.lexists(link):
            os.rmdir(link)

    @staticmethod
    def _tree_snapshot(root: Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        }

    def test_check_before_apply_reports_full_codex_paths_without_writing(self) -> None:
        before = self._tree_snapshot(self.target)
        result = self._run("check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(".codex/agents/mercury-dev.toml", result.stderr)
        self.assertIn(".codex/mercury-template.lock", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))
        self.assertFalse((self.target / ".codex").exists())

    def test_apply_then_check_is_idempotent_and_preserves_sot_overlay(self) -> None:
        sot_agent = self.target / ".codex" / "agents" / "sot-designlib.toml"
        sot_agent.parent.mkdir(parents=True)
        sot_bytes = b'name = "sot-designlib"\r\n'
        sot_agent.write_bytes(sot_bytes)
        first_apply = self._run("apply")
        first_snapshot = self._tree_snapshot(self.target)
        check = self._run("check")
        second_apply = self._run("apply")
        second_snapshot = self._tree_snapshot(self.target)
        self.assertEqual(first_apply.returncode, 0, first_apply.stderr)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(second_apply.returncode, 0, second_apply.stderr)
        self.assertEqual(first_snapshot, second_snapshot)
        self.assertEqual(sot_agent.read_bytes(), sot_bytes)

    def test_lock_is_mercury_owned_deterministic_portable_and_complete(self) -> None:
        result = self._run("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        lock_path = self.target / ".codex" / "mercury-template.lock"
        lock_bytes = lock_path.read_bytes()
        lock = json.loads(lock_bytes)
        committed_manifest = subprocess.run(
            ["git", "show", f"{self.commit}:.mercury/templates/codex-project/manifest.json"],
            cwd=self.source,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(lock["schema_version"], 1)
        self.assertEqual(lock["source_repo"], "392fyc/Mercury")
        self.assertEqual(lock["source_commit"], self.commit)
        self.assertEqual(
            lock["manifest_sha256"], hashlib.sha256(committed_manifest).hexdigest()
        )
        self.assertEqual(
            list(lock["files"]),
            [
                "agents/mercury-dev.toml",
                "project/mercury-task-contract.md",
                "rules/mercury-git-safety.rules",
            ],
        )
        for destination, digest in lock["files"].items():
            installed = self.target / ".codex" / Path(destination)
            self.assertEqual(hashlib.sha256(installed.read_bytes()).hexdigest(), digest)
        rendered = lock_bytes.decode("utf-8")
        self.assertNotIn(str(self.source), rendered)
        self.assertNotIn(str(self.target), rendered)
        self.assertNotIn("\\", rendered)

    def test_source_commit_must_exist_in_the_script_repository(self) -> None:
        before = self._tree_snapshot(self.target)
        result = self._run("apply", commit="a" * 40)
        self.assertEqual(result.returncode, 2)
        self.assertIn("does not identify a commit", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_source_commit_rejects_an_annotated_tag_object_sha(self) -> None:
        self._git("tag", "-a", "template-test-tag", "-m", "annotated test tag")
        tag_object = self._git("rev-parse", "refs/tags/template-test-tag")
        self.assertEqual(self._git("cat-file", "-t", tag_object), "tag")
        before = self._tree_snapshot(self.target)

        result = self._run("apply", commit=tag_object)

        self.assertEqual(result.returncode, 2)
        self.assertIn("commit object", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_git_repository_environment_pollution_is_ignored(self) -> None:
        polluted_environment = os.environ.copy()
        polluted_environment.update(
            {
                "GIT_DIR": str(self.root / "poisoned-git-dir"),
                "GIT_WORK_TREE": str(self.root / "poisoned-work-tree"),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "core.replaceRefs",
                "GIT_CONFIG_VALUE_0": "true",
            }
        )

        result = self._run("apply", environment=polluted_environment)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self._run("check").returncode, 0)

    def test_template_bytes_come_from_commit_not_checkout(self) -> None:
        committed_manifest = self.manifest_path.read_bytes()
        (self.template / "agents" / "mercury-dev.toml").write_bytes(
            b'name = "checkout-only-change"\r\n'
        )
        self.manifest_path.write_bytes(b"not-json\r\n")
        first = self._run("apply")
        first_lock = (self.target / ".codex" / "mercury-template.lock").read_bytes()
        shutil.rmtree(self.target)
        self.target.mkdir()
        (self.template / "agents" / "mercury-dev.toml").write_bytes(
            b'name = "another-checkout-change"\n'
        )
        second = self._run("apply")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(
            (self.target / ".codex" / "agents" / "mercury-dev.toml").read_bytes(),
            b'name = "mercury-dev"\n',
        )
        self.assertEqual(
            (self.target / ".codex" / "mercury-template.lock").read_bytes(),
            first_lock,
        )
        self.assertEqual(
            json.loads(first_lock)["manifest_sha256"],
            hashlib.sha256(committed_manifest).hexdigest(),
        )

    def test_apply_refuses_unknown_lock_before_writing(self) -> None:
        lock = self.target / ".codex" / "mercury-template.lock"
        lock.parent.mkdir(parents=True)
        lock.write_bytes(b"unknown owner\n")
        sot = self.target / ".codex" / "agents" / "sot-kb.toml"
        sot.parent.mkdir(parents=True)
        sot.write_bytes(b"sot-owned\n")
        before = self._tree_snapshot(self.target)
        result = self._run("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("existing lock", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_apply_without_lock_does_not_overwrite_same_named_downstream_file(self) -> None:
        downstream = self.target / ".codex" / "agents" / "mercury-dev.toml"
        downstream.parent.mkdir(parents=True)
        downstream.write_bytes(b"downstream-owned content\n")
        before = self._tree_snapshot(self.target)

        result = self._run("apply")

        self.assertEqual(result.returncode, 2)
        self.assertIn("unowned destination", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))
        self.assertFalse(
            (self.target / ".codex" / "mercury-template.lock").exists()
        )

    def test_apply_without_lock_can_claim_equal_hardlinked_content_safely(self) -> None:
        sot_overlay = self.target / ".codex" / "agents" / "sot-overlay.toml"
        generated = self.target / ".codex" / "agents" / "mercury-dev.toml"
        sot_overlay.parent.mkdir(parents=True)
        expected = b'name = "mercury-dev"\n'
        sot_overlay.write_bytes(expected)
        os.link(sot_overlay, generated)
        self.assertGreater(os.lstat(generated).st_nlink, 1)

        result = self._run("apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(generated.read_bytes(), expected)
        self.assertEqual(sot_overlay.read_bytes(), expected)
        self.assertEqual(os.lstat(generated).st_nlink, 1)
        self.assertTrue(
            (self.target / ".codex" / "mercury-template.lock").is_file()
        )

    def test_apply_can_overwrite_drift_at_an_authenticated_owned_destination(self) -> None:
        self.assertEqual(self._run("apply").returncode, 0)
        generated = self.target / ".codex" / "agents" / "mercury-dev.toml"
        generated.write_bytes(b"owned drift\n")

        result = self._run("apply")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(generated.read_bytes(), b'name = "mercury-dev"\n')
        self.assertEqual(self._run("check").returncode, 0)

    def test_apply_does_not_overwrite_new_destination_absent_from_old_ownership(self) -> None:
        self._write_manifest(self._default_files()[:2])
        self.commit = self._commit("publish template without rules destination")
        self.assertEqual(self._run("apply").returncode, 0)

        self._write_manifest(self._default_files())
        self.commit = self._commit("add rules destination")
        downstream = (
            self.target / ".codex" / "rules" / "mercury-git-safety.rules"
        )
        downstream.parent.mkdir(parents=True, exist_ok=True)
        downstream.write_bytes(b"downstream-owned rule\n")
        before = self._tree_snapshot(self.target)

        result = self._run("apply")

        self.assertEqual(result.returncode, 2)
        self.assertIn("unowned destination", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_apply_rejects_case_only_ownership_rename_before_writing(self) -> None:
        old_files = [
            {
                "source": "agents/mercury-dev.toml",
                "destination": "agents/mercury-Dev.toml",
            }
        ]
        self._write_manifest(old_files)
        self.commit = self._commit("publish mixed-case owned destination")
        self.assertEqual(self._run("apply").returncode, 0)

        new_files = [
            {
                "source": "agents/mercury-dev.toml",
                "destination": "agents/mercury-dev.toml",
            }
        ]
        self._write_manifest(new_files)
        self.commit = self._commit("rename owned destination by case only")
        before = self._tree_snapshot(self.target)

        result = self._run("apply")

        self.assertEqual(result.returncode, 2)
        self.assertIn("case-only ownership rename", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_apply_removes_unmodified_files_dropped_from_manifest(self) -> None:
        old_source = self.template / "agents" / "mercury-old.toml"
        old_source.write_bytes(b"old generated file\n")
        files = self._default_files()
        files.insert(1, {"source": "agents/mercury-old.toml", "destination": "agents/mercury-old.toml"})
        self._write_manifest(files)
        self.commit = self._commit("add old owned file")
        self.assertEqual(self._run("apply").returncode, 0)
        old_destination = self.target / ".codex" / "agents" / "mercury-old.toml"
        self.assertTrue(old_destination.exists())
        self._write_manifest(self._default_files())
        self.commit = self._commit("drop old owned file")
        result = self._run("apply")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old_destination.exists())
        self.assertEqual(self._run("check").returncode, 0)

    def test_modified_dropped_file_aborts_apply_before_any_write(self) -> None:
        old_source = self.template / "agents" / "mercury-old.toml"
        old_source.write_bytes(b"old generated file\n")
        files = self._default_files()
        files.insert(1, {"source": "agents/mercury-old.toml", "destination": "agents/mercury-old.toml"})
        self._write_manifest(files)
        self.commit = self._commit("add old owned file")
        self.assertEqual(self._run("apply").returncode, 0)
        old_destination = self.target / ".codex" / "agents" / "mercury-old.toml"
        old_destination.write_bytes(b"downstream modification\n")
        (self.template / "agents" / "mercury-dev.toml").write_bytes(b"new dev\n")
        self._write_manifest(self._default_files())
        self.commit = self._commit("drop old and change current")
        before = self._tree_snapshot(self.target)
        result = self._run("apply")
        self.assertEqual(result.returncode, 2)
        self.assertIn("modified previously owned file", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_old_lock_cannot_claim_non_mercury_or_traversal_paths(self) -> None:
        self.assertEqual(self._run("apply").returncode, 0)
        lock_path = self.target / ".codex" / "mercury-template.lock"
        for illegal in ("agents/sot-designlib.toml", "../mercury-escape.toml"):
            with self.subTest(illegal=illegal):
                lock = json.loads(lock_path.read_bytes())
                lock["files"][illegal] = "0" * 64
                lock_path.write_text(json.dumps(lock), encoding="utf-8")
                before = self._tree_snapshot(self.target)
                result = self._run("apply")
                self.assertEqual(result.returncode, 2)
                self.assertIn("existing lock", result.stderr)
                self.assertEqual(before, self._tree_snapshot(self.target))
                lock["files"].pop(illegal)
                lock_path.write_text(json.dumps(lock), encoding="utf-8")

    def test_hardlinked_generated_file_is_drift_and_apply_replaces_only_its_link(self) -> None:
        self.assertEqual(self._run("apply").returncode, 0)
        generated = self.target / ".codex" / "agents" / "mercury-dev.toml"
        sot_file = self.target / ".codex" / "agents" / "sot-overlay.toml"
        sot_bytes = generated.read_bytes()
        sot_file.write_bytes(sot_bytes)
        generated.unlink()
        os.link(sot_file, generated)
        self.assertGreater(os.lstat(generated).st_nlink, 1)
        check = self._run("check")
        apply = self._run("apply")
        self.assertEqual(check.returncode, 1)
        self.assertEqual(apply.returncode, 0, apply.stderr)
        self.assertEqual(sot_file.read_bytes(), sot_bytes)
        self.assertEqual(os.lstat(generated).st_nlink, 1)
        self.assertEqual(self._run("check").returncode, 0)

    def test_destination_symlink_is_drift_and_apply_refuses_it(self) -> None:
        external = self.root / "external-file"
        external.write_bytes(b"external\n")
        generated = self.target / ".codex" / "agents" / "mercury-dev.toml"
        generated.parent.mkdir(parents=True)
        try:
            generated.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")
        check = self._run("check")
        apply = self._run("apply")
        self.assertEqual(check.returncode, 1)
        self.assertEqual(apply.returncode, 2)
        self.assertEqual(external.read_bytes(), b"external\n")

    def test_target_or_destination_parent_symlink_is_rejected(self) -> None:
        real_target = self.root / "real-target"
        real_target.mkdir()
        target_link = self.root / "target-link"
        self._make_directory_link(target_link, real_target)
        target_result = self._run("apply", target=target_link)
        self.assertEqual(target_result.returncode, 2)
        self.assertFalse((real_target / ".codex").exists())
        external_agents = self.root / "external-agents"
        external_agents.mkdir()
        codex = self.target / ".codex"
        codex.mkdir()
        self._make_directory_link(codex / "agents", external_agents)
        parent_result = self._run("apply")
        self.assertEqual(parent_result.returncode, 2)
        self.assertEqual(list(external_agents.iterdir()), [])

    def test_manifest_rejects_non_mercury_destinations_and_traversal(self) -> None:
        invalid_files = [
            [{"source": "agents/mercury-dev.toml", "destination": "agents/sot-designlib.toml"}],
            [{"source": "agents/mercury-dev.toml", "destination": "../agents/mercury-dev.toml"}],
            [{"source": "../outside/mercury-dev.toml", "destination": "agents/mercury-dev.toml"}],
        ]
        for index, files in enumerate(invalid_files):
            with self.subTest(files=files):
                self._write_manifest(files)
                self.commit = self._commit(f"invalid manifest {index}")
                before = self._tree_snapshot(self.target)
                result = self._run("apply")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(before, self._tree_snapshot(self.target))

    def test_manifest_rejects_unsorted_and_duplicate_entries(self) -> None:
        valid = self._default_files()[:2]
        invalid_files = [list(reversed(valid)), [valid[0], valid[0]]]
        for index, files in enumerate(invalid_files):
            with self.subTest(files=files):
                self._write_manifest(files)
                self.commit = self._commit(f"invalid ordering {index}")
                result = self._run("apply")
                self.assertEqual(result.returncode, 2)
                self.assertFalse((self.target / ".codex" / "mercury-template.lock").exists())

    def test_manifest_lock_must_be_a_single_mercury_owned_basename(self) -> None:
        for index, invalid_lock in enumerate(
            (
                "template.lock",
                "sot-template.lock",
                "project/mercury-template.lock",
                "../mercury-template.lock",
                "mercury\\template.lock",
            )
        ):
            with self.subTest(lock=invalid_lock):
                self._write_manifest(self._default_files(), lock=invalid_lock)
                self.commit = self._commit(f"invalid lock {index}")
                before = self._tree_snapshot(self.target)
                result = self._run("apply")
                self.assertEqual(result.returncode, 2)
                self.assertEqual(before, self._tree_snapshot(self.target))

    def test_manifest_rejects_other_mercury_owned_lock_basenames(self) -> None:
        self._write_manifest(
            self._default_files(), lock="mercury-project-template.lock"
        )
        self.commit = self._commit("use non-canonical owned lock basename")
        before = self._tree_snapshot(self.target)

        result = self._run("apply")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_apply_rejects_old_lock_from_a_different_source_repo_before_writing(self) -> None:
        self.assertEqual(self._run("apply").returncode, 0)
        lock_path = self.target / ".codex" / "mercury-template.lock"
        lock = json.loads(lock_path.read_bytes())
        lock["source_repo"] = "another-owner/Mercury"
        lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        before = self._tree_snapshot(self.target)

        result = self._run("apply")

        self.assertEqual(result.returncode, 2)
        self.assertIn("source_repo", result.stderr)
        self.assertEqual(before, self._tree_snapshot(self.target))

    def test_forged_old_locks_cannot_authorize_deletion(self) -> None:
        scenarios = ("fake-commit", "forged-files", "forged-hash")
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                shutil.rmtree(self.target)
                self.target.mkdir()
                self.assertEqual(self._run("apply").returncode, 0)
                lock_path = self.target / ".codex" / "mercury-template.lock"
                lock = json.loads(lock_path.read_bytes())
                protected = self.target / ".codex" / "agents" / "mercury-old.toml"
                protected_bytes = b"must not be deleted\n"
                protected.write_bytes(protected_bytes)
                lock["files"]["agents/mercury-old.toml"] = hashlib.sha256(
                    protected_bytes
                ).hexdigest()
                if scenario == "fake-commit":
                    lock["source_commit"] = "a" * 40
                elif scenario == "forged-hash":
                    lock["manifest_sha256"] = "0" * 64
                lock_path.write_bytes(
                    (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode("utf-8")
                )
                before = self._tree_snapshot(self.target)

                result = self._run("apply")

                self.assertEqual(result.returncode, 2)
                self.assertEqual(before, self._tree_snapshot(self.target))
                self.assertEqual(protected.read_bytes(), protected_bytes)

    def test_manifest_rejects_windows_unsafe_and_casefold_duplicate_paths(self) -> None:
        invalid_file_sets = [
            [{"source": "agents/CON.toml", "destination": "agents/mercury-dev.toml"}],
            [{"source": "agents/mercury-dev.toml. ", "destination": "agents/mercury-dev.toml"}],
            [{"source": "agents/mercury?.toml", "destination": "agents/mercury-dev.toml"}],
            [{"source": "agents/mercury-\u0001.toml", "destination": "agents/mercury-dev.toml"}],
            [
                {"source": "agents/mercury-dev.toml", "destination": "agents/mercury-Dev.toml"},
                {"source": "project/mercury-task-contract.md", "destination": "agents/mercury-dev.toml"},
            ],
            [
                {"source": "agents/mercury-dev.toml", "destination": "agents/mercury-dev.toml"},
                {"source": "agents/MERCURY-DEV.toml", "destination": "project/mercury-task-contract.md"},
            ],
        ]
        for index, files in enumerate(invalid_file_sets):
            with self.subTest(files=files):
                self._write_manifest(files)
                self.commit = self._commit(f"unsafe portable path {index}")
                before = self._tree_snapshot(self.target)

                result = self._run("apply")

                self.assertEqual(result.returncode, 2)
                self.assertEqual(before, self._tree_snapshot(self.target))


if __name__ == "__main__":
    unittest.main()
