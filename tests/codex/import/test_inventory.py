from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from collections import Counter
from dataclasses import replace
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid


inventory = importlib.import_module("scripts.codex.import.inventory")
model = importlib.import_module("scripts.codex.import.model")

AssetRecord = model.AssetRecord
inventory_claude_chats = inventory.inventory_claude_chats
inventory_memories = inventory.inventory_memories
inventory_paths = inventory.inventory_paths
inventory_repo_dirty = inventory.inventory_repo_dirty
records_to_jsonl = inventory.records_to_jsonl


def write_file(path: Path, content: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def create_junction(link: Path, target: Path) -> Path:
    link.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(target)],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise unittest.SkipTest("junction creation is unavailable")
    return link


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", os.fspath(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def make_test_manifest(records: list[AssetRecord]) -> object:
    ordered = sorted(records, key=lambda record: (record.kind, record.source))
    payload = records_to_jsonl(ordered)
    actual_counts = dict(sorted(Counter(record.kind for record in ordered).items()))
    metadata = {
        "record_type": "inventory-metadata",
        "schema_version": 1,
        "production": False,
        "cutoff": "2023-11-14T22:13:20+00:00",
        "tool": {"commit": "fixture", "sha256": "0" * 64},
        "roots": {
            name: {"path": f"D:/{name}", "file_id": f"1:{index}"}
            for index, name in enumerate(("mercury", "godot", "design", "kb"), start=1)
        },
        "expected_counts": actual_counts,
        "actual_counts": actual_counts,
        "record_count": len(ordered),
        "records_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "domain_decisions_sha256": None,
        "unresolved_count": sum(record.disposition is None for record in ordered),
    }
    return model.InventoryManifest(metadata, tuple(ordered))


class InventoryPathTests(unittest.TestCase):
    def test_inventory_is_sorted_stable_hashed_and_fully_classified(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            fixtures = [
                write_file(
                    root / "projects" / "mercury" / "session.jsonl",
                    b'{"cwd":"D:/Mercury/Mercury","message":"SoT"}\n',
                ),
                write_file(root / "projects" / "mercury" / "memory" / "MEMORY.md"),
                write_file(root / "projects" / "mercury" / "memory" / "cache.pyc"),
                write_file(root / ".agents" / "skills" / "review" / "SKILL.md"),
                write_file(root / ".claude" / "agents" / "reviewer.md"),
                write_file(root / ".claude" / "commands" / "ship.md"),
                write_file(root / ".claude" / "hooks" / "stop.py"),
                write_file(root / ".claude" / "workflows" / "verify.js"),
                write_file(root / ".claude" / "settings.json"),
                write_file(root / "backups" / "settings.json.backup"),
                write_file(root / "attachments" / "diagram.png"),
            ]

            first = inventory_paths(list(reversed(fixtures)), cutoff=None)
            second = inventory_paths(fixtures, cutoff=None)

            self.assertEqual(first, second)
            self.assertEqual(
                [(record.kind, record.source) for record in first],
                sorted((record.kind, record.source) for record in first),
            )
            self.assertEqual(
                {
                    "agent",
                    "attachment",
                    "backup",
                    "chat",
                    "command",
                    "hook",
                    "memory",
                    "memory-auxiliary",
                    "setting",
                    "skill",
                    "workflow",
                },
                {record.kind for record in first},
            )
            self.assertTrue(all("\\" not in record.source for record in first))
            self.assertTrue(all(record.domain == "mercury-sot" for record in first))
            self.assertTrue(all(record.disposition == "import" for record in first))
            self.assertEqual(len(first), len({record.asset_id for record in first}))

            chat = next(record for record in first if record.kind == "chat")
            self.assertEqual(
                hashlib.sha256(fixtures[0].read_bytes()).hexdigest(), chat.sha256
            )
            self.assertEqual(fixtures[0].stat().st_size, chat.size)

    def test_asset_id_changes_with_normalized_source_or_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            left = write_file(root / "memory" / "left.md", b"same")
            right = write_file(root / "memory" / "right.md", b"same")

            left_record = inventory_paths([left], cutoff=None)[0]
            right_record = inventory_paths([right], cutoff=None)[0]
            self.assertNotEqual(left_record.asset_id, right_record.asset_id)

            left.write_bytes(b"changed")
            changed_record = inventory_paths([left], cutoff=None)[0]
            self.assertNotEqual(left_record.asset_id, changed_record.asset_id)

    def test_cutoff_is_inclusive_and_accepts_iso_utc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_file(Path(directory).resolve() / "Mercury" / "memory" / "note.md")
            os.utime(path, (1_700_000_000, 1_700_000_000))

            self.assertEqual(1, len(inventory_paths([path], cutoff=1_700_000_000)))
            self.assertEqual(
                1,
                len(
                    inventory_paths(
                        [path], cutoff="2023-11-14T22:13:20Z"
                    )
                ),
            )
            self.assertEqual(0, len(inventory_paths([path], cutoff=1_700_000_001)))

    def test_directory_inputs_are_recursive_deduplicated_and_absolute_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            child = write_file(root / "memory" / "note.md")
            records = inventory_paths([root, child], cutoff=None)
            self.assertEqual(1, len(records))

            with self.assertRaisesRegex(ValueError, "absolute"):
                inventory_paths([Path("relative.md")], cutoff=None)

    def test_secret_disposition_uses_path_policy_and_never_serializes_value(self) -> None:
        secret_value = "example_token_value_1234567890"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            secret_paths = [
                write_file(root / ".env", secret_value.encode()),
                write_file(root / "credentials.json", secret_value.encode()),
                write_file(root / "certificates" / "client.pem", secret_value.encode()),
                write_file(root / "secrets" / "service.txt", secret_value.encode()),
            ]

            records = inventory_paths(secret_paths, cutoff=None)
            self.assertEqual(
                ["exclude-secret"] * len(records),
                [record.disposition for record in records],
            )
            encoded = records_to_jsonl(records)
            self.assertNotIn(secret_value, encoded)

    def test_each_record_has_exactly_one_valid_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_file(Path(directory).resolve() / "Mercury" / "memory" / "file.md")
            valid = inventory_paths([path], cutoff=None)[0]
            self.assertEqual("import", valid.disposition)
            invalid = valid.to_dict()
            invalid["disposition"] = "import,exclude-domain"
            with self.assertRaises(ValueError):
                AssetRecord(**invalid)

    def test_json_lines_are_stable_sorted_and_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            paths = [
                write_file(root / "memory" / "z.md", b"z"),
                write_file(root / "memory" / "a.md", b"a"),
            ]
            records = inventory_paths(paths, cutoff=None)
            encoded = records_to_jsonl(list(reversed(records)))

            self.assertTrue(encoded.endswith("\n"))
            decoded = [json.loads(line) for line in encoded.splitlines()]
            self.assertEqual(
                sorted(decoded, key=lambda item: (item["kind"], item["source"])),
                decoded,
            )
            self.assertEqual(records[0].to_dict(), decoded[0])


class ChatDomainTests(unittest.TestCase):
    def test_only_first_user_message_controls_chat_domain(self) -> None:
        events = [
            {"type": "assistant", "message": {"role": "assistant", "content": "Mercury"}},
            {"type": "user", "cwd": "C:/Users/example", "message": {"role": "user", "content": "cooking notes"}},
            {"type": "user", "cwd": "D:/Mercury/Mercury", "message": {"role": "user", "content": "Mercury"}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            chat = write_file(
                Path(directory).resolve() / "projects" / "unrelated" / "session.jsonl",
                "".join(json.dumps(event) + "\n" for event in events).encode(),
            )
            record = inventory_paths([chat], cutoff=None)[0]
            self.assertEqual("review-required", record.domain)
            self.assertIsNone(record.disposition)
            self.assertEqual("unresolved", record.disposition_status)

    def test_first_user_message_supports_structured_claude_content(self) -> None:
        event = {
            "type": "user",
            "cwd": "C:/Users/example",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "content": "ignored"},
                    {"type": "text", "text": "Review the Godot MCP setup"},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            chat = write_file(
                Path(directory).resolve() / "projects" / "home" / "session.jsonl",
                (json.dumps(event) + "\n").encode(),
            )
            record = inventory_paths([chat], cutoff=None)[0]
            self.assertEqual("mercury-sot", record.domain)
            self.assertEqual("import", record.disposition)

    def test_malformed_oversized_and_missing_user_chats_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "projects" / "home"
            malformed = write_file(root / "malformed.jsonl", b'{"type":"user"\n')
            oversized = write_file(
                root / "oversized.jsonl",
                b'{"type":"user","message":"Mercury ' + b"x" * (2 * 1024 * 1024) + b'"}\n',
            )
            missing = write_file(
                root / "missing.jsonl",
                b'{"type":"assistant","message":{"role":"assistant","content":"Mercury"}}\n',
            )

            records = inventory_paths([malformed, oversized, missing], cutoff=None)
            self.assertEqual(
                ["review-required"] * 3, [record.domain for record in records]
            )
            self.assertEqual([None] * 3, [record.disposition for record in records])

    def test_chat_adapter_applies_cutoff_without_reading_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "projects"
            project = root / "D--Mercury-Mercury"
            old = write_file(project / f"{uuid.uuid4()}.jsonl", b'{"message":"Mercury"}\n')
            recent = write_file(project / f"{uuid.uuid4()}.jsonl", b'{"message":"SoT"}\n')
            write_file(root / "not-chat.md")
            os.utime(old, (100, 100))
            os.utime(recent, (200, 200))

            records = inventory_claude_chats(root, cutoff=150)
            self.assertEqual([recent.resolve().as_posix()], [record.source for record in records])


class MemoryAdapterTests(unittest.TestCase):
    def test_memory_adapter_separates_markdown_and_auxiliary_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury" / "memory"
            write_file(root / "MEMORY.md")
            write_file(root / "index.json")
            write_file(root / "helper.py")
            write_file(root / "cache.pyc")

            records = inventory_memories(root, cutoff=None)
            self.assertEqual(
                ["memory", "memory-auxiliary", "memory-auxiliary", "memory-auxiliary"],
                [record.kind for record in records],
            )


class RepoDirtyAdapterTests(unittest.TestCase):
    def test_repo_dirty_distinguishes_tracked_and_untracked_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve() / "MercuryRepo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.email", "inventory@example.invalid")
            run_git(repo, "config", "user.name", "Inventory Test")
            tracked = write_file(repo / "tracked.txt", b"before")
            run_git(repo, "add", "tracked.txt")
            run_git(repo, "commit", "-q", "-m", "fixture")
            tracked.write_bytes(b"after")
            untracked = write_file(repo / "untracked.txt", b"new")

            before_status = run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
            before_head = run_git(repo, "rev-parse", "HEAD")
            before_index = run_git(repo, "write-tree")

            records = inventory_repo_dirty(repo, cutoff=None)

            self.assertEqual(
                ["file", "file"], [record.kind for record in records]
            )
            self.assertEqual(
                ["tracked-modified", "untracked"],
                [record.dirty_state for record in records],
            )
            self.assertEqual(
                {tracked.resolve().as_posix(), untracked.resolve().as_posix()},
                {record.source for record in records},
            )
            self.assertEqual(before_status, run_git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"))
            self.assertEqual(before_head, run_git(repo, "rev-parse", "HEAD"))
            self.assertEqual(before_index, run_git(repo, "write-tree"))

    def test_repo_dirty_requires_an_absolute_git_root(self) -> None:
        with self.assertRaisesRegex(ValueError, "absolute"):
            inventory_repo_dirty(Path("relative-repo"), cutoff=None)


class CliTests(unittest.TestCase):
    def test_parser_supports_the_frozen_contract_command_sequence(self) -> None:
        parser = inventory.build_parser()
        freeze = parser.parse_args(
            [
                "freeze-contract",
                "--contract", "D:/protected/inventory-contract.json",
                "--output", "D:/protected/assets.jsonl",
                "--metadata", "D:/protected/assets.metadata.json",
                "--domain-decisions", "D:/review/domain-decisions.json",
                "--claude-home", "C:/Users/fixture/.claude",
                "--mercury-root", "D:/Mercury/Mercury",
                "--godot-root", "D:/ShipOfTheseus/Ship_of_Theseus",
                "--design-root", "D:/ShipOfTheseus/SoT-fyc-space",
                "--kb-root", "D:/ShipOfTheseus/ShipOfTheseus-KB",
            ]
        )
        collect = parser.parse_args(
            [
                "collect",
                "--contract", "D:/protected/inventory-contract.json",
                "--contract-sha256", "1" * 64,
                "--output", "D:/protected/assets.jsonl",
            ]
        )
        summarize = parser.parse_args(
            [
                "summarize",
                "--contract", "D:/protected/inventory-contract.json",
                "--contract-sha256", "1" * 64,
                "--input", "D:/protected/assets.jsonl",
            ]
        )

        self.assertEqual("freeze-contract", freeze.command)
        self.assertEqual("collect", collect.command)
        self.assertEqual("summarize", summarize.command)

    def test_atomic_manifest_and_summarize_round_trip_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            source = write_file(root / "memory" / "note.md", b"note")
            output = root / "out" / "assets.jsonl"
            output.parent.mkdir(parents=True)
            records = inventory_paths([source], cutoff=None)
            payload = records_to_jsonl(records)
            metadata = {
                "record_type": "inventory-metadata",
                "schema_version": 1,
                "production": False,
                "cutoff": "2023-11-14T22:13:20+00:00",
                "tool": {"commit": "fixture", "sha256": "0" * 64},
                "roots": {
                    name: {"path": f"D:/{name}", "file_id": f"1:{index}"}
                    for index, name in enumerate(("mercury", "godot", "design", "kb"), start=1)
                },
                "expected_counts": {"memory": 1},
                "actual_counts": {"memory": 1},
                "record_count": 1,
                "records_sha256": hashlib.sha256(payload.encode()).hexdigest(),
                "domain_decisions_sha256": None,
                "unresolved_count": 0,
            }
            manifest = model.InventoryManifest(metadata, tuple(records))
            inventory._write_new(output, inventory.manifest_to_jsonl(manifest))
            self.assertTrue(output.is_file())
            self.assertEqual(2, len(output.read_text(encoding="utf-8").splitlines()))

            summary = inventory.summarize_manifest(output)
            self.assertEqual(1, summary["total"])
            self.assertEqual({"import": 1}, summary["by_disposition"])


if __name__ == "__main__":
    unittest.main()


class ReviewFindingRedTests(unittest.TestCase):
    def test_chat_adapter_selects_only_direct_main_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory).resolve() / "projects"
            project = projects / "D--Mercury-Mercury"
            main = write_file(
                project / f"{uuid.uuid4()}.jsonl",
                b'{"message":"Mercury"}\n',
            )
            write_file(
                project / "subagents" / f"{uuid.uuid4()}.jsonl",
                b'{"message":"Mercury"}\n',
            )

            records = inventory_claude_chats(projects, cutoff=None)
            self.assertEqual([main.as_posix()], [record.source for record in records])

    def test_production_collect_requires_explicit_cutoff_and_contract(self) -> None:
        parser = inventory.build_parser()
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["collect", "--output", "D:/protected/assets.jsonl"])
        self.assertEqual(
            {
                "mercury",
                "godot",
                "design",
                "kb",
            },
            set(inventory.PRODUCTION_ROOTS),
        )

    def test_production_contract_rejects_root_aliases_even_when_they_are_git_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            home = base / ".claude"
            root = base / "alias"
            home.mkdir()
            root.mkdir()
            contract = inventory.InventoryContract(
                claude_home=home,
                mercury_root=root,
                godot_root=root,
                design_root=root,
                kb_root=root,
                cutoff="2023-11-14T22:13:20Z",
                production=True,
            )
            with mock.patch.object(inventory, "_validate_git_root"):
                with self.assertRaisesRegex(Exception, "fixed exact root"):
                    inventory._validate_contract(contract)

    def test_asset_id_is_stable_across_observed_roots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            left = write_file(base / "machine-a" / "memory" / "note.md", b"same")
            right = write_file(base / "machine-b" / "memory" / "note.md", b"same")

            left_record = inventory.inventory_paths(
                [left],
                cutoff=None,
                source_namespace="claude-memory",
                approved_root=left.parents[1],
            )[0]
            right_record = inventory.inventory_paths(
                [right],
                cutoff=None,
                source_namespace="claude-memory",
                approved_root=right.parents[1],
            )[0]
            self.assertEqual(left_record.canonical_key, right_record.canonical_key)
            self.assertEqual(left_record.asset_id, right_record.asset_id)

    def test_ambiguous_chat_requires_controlled_domain_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            projects = Path(directory).resolve() / "projects"
            session_id = str(uuid.uuid4())
            chat = write_file(
                projects / "C--Users-example" / f"{session_id}.jsonl",
                b'{"type":"user","cwd":"C:/Users/example","message":{"role":"user","content":"cooking"}}\n',
            )
            record = inventory_claude_chats(projects, cutoff=None)[0]
            self.assertEqual(session_id, record.session_id)
            self.assertIsNone(record.disposition)
            self.assertEqual("unresolved", record.disposition_status)
            self.assertEqual("no-domain-evidence", record.domain_reason)

    def test_summary_rejects_duplicate_and_spoofed_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            source = write_file(root / "memory" / "note.md", b"note")
            record = inventory_paths([source], cutoff=None)[0]
            manifest = root / "duplicate.jsonl"
            manifest.write_text(records_to_jsonl([record, record]), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    os.fspath(Path(inventory.__file__).resolve()),
                    "summarize",
                    "--input",
                    os.fspath(manifest),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(0, completed.returncode)

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_direct_and_nested_junctions_are_rejected_before_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            outside = base / "outside"
            outside.mkdir()
            write_file(outside / "secret.txt", b"outside")
            direct = base / "direct-link"
            nested_root = base / "nested-root"
            nested_root.mkdir()
            nested = nested_root / "nested-link"
            for link in (direct, nested):
                completed = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", os.fspath(link), os.fspath(outside)],
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    self.skipTest("junction creation is unavailable")

            with self.assertRaisesRegex(ValueError, "reparse"):
                inventory_paths([direct], cutoff=None)
            with self.assertRaisesRegex(ValueError, "reparse"):
                inventory_paths([nested_root], cutoff=None)

    @unittest.skipUnless(os.name == "nt", "Windows junction regression")
    def test_git_untracked_junction_cannot_escape_repo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            repo = base / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            outside = base / "outside"
            outside.mkdir()
            write_file(outside / "secret.txt", b"outside")
            junction = repo / "escape"
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", os.fspath(junction), os.fspath(outside)],
                capture_output=True,
                text=True,
            )
            if completed.returncode != 0:
                self.skipTest("junction creation is unavailable")

            with self.assertRaisesRegex(ValueError, "reparse"):
                inventory_repo_dirty(repo, cutoff=None)

    def test_snapshot_detects_concurrent_change_on_one_open_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_file(Path(directory).resolve() / "source.bin", b"before")

            def mutate(_path: Path, _attempt: int) -> None:
                with path.open("ab") as stream:
                    stream.write(b"changed")

            with self.assertRaisesRegex(Exception, "changed during snapshot"):
                inventory._snapshot_file(path, retries=1, after_read=mutate)

    def test_atomic_publish_cleans_owned_temp_and_leaves_final_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "assets.jsonl"

            class BrokenContent:
                pass

            with self.assertRaises(Exception):
                inventory._write_new(output, BrokenContent())
            self.assertFalse(output.exists())
            self.assertEqual([], list(output.parent.glob(f".{output.name}.*.tmp")))

    def test_known_dot_credentials_container_is_never_proposed_for_import(self) -> None:
        secret = "fixture_secret_value_1234567890"
        with tempfile.TemporaryDirectory() as directory:
            path = write_file(
                Path(directory).resolve() / "Mercury" / ".credentials.json",
                secret.encode(),
            )
            record = inventory_paths([path], cutoff=None)[0]
            self.assertEqual("exclude-secret", record.disposition)
            self.assertEqual("provisional", record.disposition_status)
            self.assertNotIn(secret, records_to_jsonl([record]))

    def test_semantic_kind_and_dirty_state_merge_without_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve() / "Mercury"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.email", "inventory@example.invalid")
            run_git(repo, "config", "user.name", "Inventory Test")
            skill = write_file(repo / ".agents" / "skills" / "review" / "SKILL.md", b"before")
            run_git(repo, "add", ".agents/skills/review/SKILL.md")
            run_git(repo, "commit", "-q", "-m", "fixture")
            skill.write_bytes(b"after")

            semantic = inventory.inventory_paths(
                [skill],
                cutoff=None,
                source_namespace="repo-mercury",
                approved_root=repo,
            )
            dirty = inventory.inventory_repo_dirty(
                repo, cutoff=None, source_namespace="repo-mercury"
            )
            merged = inventory._merge_records([semantic, dirty])
            self.assertEqual(1, len(merged))
            self.assertEqual("skill", merged[0].kind)
            self.assertEqual("tracked-modified", merged[0].dirty_state)

    def test_deleted_gitlink_and_nonrepo_fail_with_actionable_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve() / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            run_git(repo, "config", "user.email", "inventory@example.invalid")
            run_git(repo, "config", "user.name", "Inventory Test")
            deleted = write_file(repo / "deleted.txt", b"tracked")
            run_git(repo, "add", "deleted.txt")
            run_git(repo, "commit", "-q", "-m", "fixture")
            deleted.unlink()
            with self.assertRaisesRegex(Exception, "deleted dirty path"):
                inventory_repo_dirty(repo, cutoff=None)

            nonrepo = Path(directory).resolve() / "not-a-repo"
            nonrepo.mkdir()
            with self.assertRaisesRegex(Exception, "not a Git repository"):
                inventory_repo_dirty(nonrepo, cutoff=None)

    def test_asset_record_rejects_relative_provenance_and_spoofed_id(self) -> None:
        with self.assertRaises(ValueError):
            AssetRecord(
                asset_id="spoofed",
                source="relative/file.md",
                source_namespace="claude-memory",
                canonical_key="file.md",
                kind="memory",
                size=1,
                sha256="0" * 64,
                mtime_ns=1,
                file_id="1:1",
                domain="mercury-sot",
                domain_reason="fixture",
                disposition="import",
                disposition_status="provisional",
            )

    def test_explicit_source_contract_and_metadata_count_gate_exist(self) -> None:
        self.assertTrue(callable(inventory.collect_inventory))
        for name in (
            "inventory_claude_settings",
            "inventory_claude_instructions",
            "inventory_claude_hooks",
            "inventory_claude_commands",
            "inventory_claude_skills",
            "inventory_claude_agents",
            "inventory_claude_workflows",
            "inventory_claude_attachments",
            "inventory_claude_backups",
        ):
            self.assertTrue(callable(getattr(inventory, name)))


def create_git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.email", "inventory@example.invalid")
    run_git(path, "config", "user.name", "Inventory Test")
    write_file(path / "tracked.txt", b"before")
    run_git(path, "add", "tracked.txt")
    run_git(path, "commit", "-q", "-m", "fixture")
    (path / "tracked.txt").write_bytes(b"after")
    return path


def create_counted_files(root: Path, count: int, suffix: str = ".md") -> None:
    for index in range(count):
        write_file(root / f"item-{index:03d}{suffix}", f"fixture-{index}".encode())


class ProductionSourceFixtureTests(unittest.TestCase):
    def test_complete_contract_selects_exact_frozen_counts_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            claude_home = base / "home" / ".claude"
            projects = claude_home / "projects"
            cutoff_epoch = 1_785_542_400
            chat_groups = {
                "C--Users-fixture--claude": 45,
                "D--Mercury-Mercury": 14,
                "D--ShipOfTheseus-Ship-of-Theseus": 9,
            }
            for project_name, count in chat_groups.items():
                for _ in range(count):
                    session_id = str(uuid.uuid4())
                    chat = write_file(
                        projects / project_name / f"{session_id}.jsonl",
                        b'{"type":"user","cwd":"D:/Mercury/Mercury","message":{"role":"user","content":"Mercury SoT"}}\n',
                    )
                    os.utime(chat, (cutoff_epoch, cutoff_epoch))
            write_file(
                projects
                / "D--Mercury-Mercury"
                / "subagents"
                / f"{uuid.uuid4()}.jsonl",
                b'{"message":"Mercury"}\n',
            )

            create_counted_files(projects / "D--Mercury-Mercury" / "memory", 295)
            write_file(projects / "D--Mercury-Mercury" / "memory" / "design.json")
            write_file(projects / "D--Mercury-Mercury" / "memory" / "verify.py")
            write_file(projects / "D--Mercury-Mercury" / "memory" / "cache.pyc")
            create_counted_files(
                projects / "D--ShipOfTheseus-Ship-of-Theseus" / "memory", 57
            )
            legacy_counts = {
                "D--Mercury": 11,
                "D--Mercury-AgentKB": 6,
                "D--Mercury-Argus": 4,
                "D--Mercury-Mercury-side-bug": 2,
                "D--ShipOfTheseus-Ship_of_Theseus": 3,
            }
            for project_name, count in legacy_counts.items():
                create_counted_files(projects / project_name / "memory", count)
            create_counted_files(
                projects / "D--Mercury-stock-agent-candidates-TradingAgents" / "memory",
                3,
            )

            write_file(claude_home / "settings.json")
            write_file(claude_home / "CLAUDE.md")
            write_file(claude_home / "hooks" / "session-start.py")
            write_file(claude_home / "commands" / "nas-ssh.md")
            write_file(claude_home / "skills" / "mercury-review" / "SKILL.md")
            write_file(claude_home / "agents" / "review.md")
            write_file(claude_home / "workflows" / "verify.js")
            write_file(claude_home / "attachments" / "diagram.png")
            write_file(claude_home / "backups" / "settings.json.backup")
            agents_home = claude_home.parent / ".agents"
            empty_target = agents_home / "skills" / "empty-target"
            populated_target = agents_home / "skills" / "mercury-target"
            empty_target.mkdir(parents=True)
            target_member = write_file(populated_target / "SKILL.md", b"target-only")
            create_junction(claude_home / "skills" / "empty-target", empty_target)
            populated_link = create_junction(
                claude_home / "skills" / "mercury-target", populated_target
            )

            roots = {
                "mercury": create_git_repo(base / "Mercury"),
                "godot": create_git_repo(base / "Godot"),
                "design": create_git_repo(base / "Design"),
                "kb": create_git_repo(base / "KB"),
            }
            canonical_skill = write_file(
                roots["mercury"] / ".agents" / "skills" / "review" / "SKILL.md",
                b"same-skill",
            )
            write_file(
                roots["mercury"] / ".claude" / "skills" / "review" / "SKILL.md",
                canonical_skill.read_bytes(),
            )
            for name in ("divergent-one", "divergent-two"):
                write_file(
                    roots["mercury"] / ".agents" / "skills" / name / "SKILL.md",
                    f"canonical-{name}".encode(),
                )
                write_file(
                    roots["mercury"] / ".claude" / "skills" / name / "SKILL.md",
                    f"mirror-{name}".encode(),
                )
            run_git(
                roots["mercury"],
                "add",
                ".agents/skills",
                ".claude/skills",
            )
            run_git(roots["mercury"], "commit", "-q", "-m", "skill mirrors")

            contract = inventory.InventoryContract(
                claude_home=claude_home,
                mercury_root=roots["mercury"],
                godot_root=roots["godot"],
                design_root=roots["design"],
                kb_root=roots["kb"],
                cutoff=inventory.FROZEN_START,
                domain_decisions=None,
                expected_counts={
                    "chat": 68,
                    "memory": 378,
                    "memory-auxiliary": 3,
                    "memory-archive": 3,
                },
                production=False,
                tool_commit="fixture-commit",
                tool_sha256="0" * 64,
            )
            manifest = inventory.collect_inventory(contract)

            counts: dict[str, int] = {}
            for record in manifest.records:
                counts[record.kind] = counts.get(record.kind, 0) + 1
            self.assertEqual(68, counts["chat"])
            self.assertEqual(378, counts["memory"])
            self.assertEqual(3, counts["memory-auxiliary"])
            self.assertEqual(3, counts["memory-archive"])
            self.assertEqual(4, sum(record.dirty_state is not None for record in manifest.records))
            self.assertEqual(
                {"mercury", "godot", "design", "kb"},
                set(manifest.metadata["roots"]),
            )
            self.assertEqual(
                "2026-07-15T15:00:00+00:00", manifest.metadata["cutoff"]
            )
            self.assertEqual(contract.expected_counts, manifest.metadata["expected_counts"])
            self.assertFalse(any(record.disposition is None for record in manifest.records))
            target_record = next(
                record for record in manifest.records if record.source == target_member.as_posix()
            )
            self.assertEqual("claude-user-skill-target", target_record.source_namespace)
            self.assertEqual("skill", target_record.kind)
            self.assertEqual("exclude-domain", target_record.disposition)
            self.assertEqual("domain-decided", target_record.disposition_status)
            self.assertEqual(
                "already-native-alias/no-import", target_record.domain_reason
            )
            self.assertEqual(
                "junction-relation:direct-same-name",
                target_record.decision_evidence,
            )
            self.assertEqual(
                ((populated_link / "SKILL.md").as_posix(),),
                target_record.observed_mirrors,
            )
            self.assertEqual(2, manifest.metadata["skill_junction_mirror_count"])
            self.assertEqual(
                [0, 1],
                [
                    relation["target_member_count"]
                    for relation in manifest.metadata["skill_junction_mirrors"]
                ],
            )
            populated_relation = manifest.metadata["skill_junction_mirrors"][1]
            self.assertEqual(target_member.as_posix(), populated_relation["target_members"][0]["source"])
            self.assertNotIn("disposition", populated_relation["target_members"][0])
            self.assertNotIn("asset_id", populated_relation["target_members"][0])
            self.assertEqual(
                1,
                sum(
                    record.kind == "skill" and record.canonical_key.endswith("review/SKILL.md")
                    for record in manifest.records
                    if record.source_namespace == "repo-mercury"
                ),
            )
            divergent = [record for record in manifest.records if record.kind == "skill-mirror"]
            self.assertEqual(2, len(divergent))
            self.assertTrue(all(record.mirror_of for record in divergent))
            identical = next(
                record
                for record in manifest.records
                if record.source_namespace == "repo-mercury"
                and record.canonical_key == ".agents/skills/review/SKILL.md"
            )
            self.assertEqual(1, len(identical.observed_mirrors))

            decision_file = write_file(base / "domain-decisions.json", b'{"decisions":{}}')
            frozen_contract = base / "inventory-contract.json"
            frozen_output = base / "formal-assets.jsonl"
            frozen_metadata = base / "formal-assets.metadata.json"
            with redirect_stdout(io.StringIO()) as freeze_stdout:
                freeze_rc = inventory.main(
                    [
                        "freeze-contract",
                        "--contract", os.fspath(frozen_contract),
                        "--output", os.fspath(frozen_output),
                        "--metadata", os.fspath(frozen_metadata),
                        "--domain-decisions", os.fspath(decision_file),
                        "--claude-home", os.fspath(claude_home),
                        "--mercury-root", os.fspath(roots["mercury"]),
                        "--godot-root", os.fspath(roots["godot"]),
                        "--design-root", os.fspath(roots["design"]),
                        "--kb-root", os.fspath(roots["kb"]),
                        "--fixture",
                        "--home-chat-project", "C--Users-fixture--claude",
                    ]
                )
            self.assertEqual(0, freeze_rc)
            contract_sha = json.loads(freeze_stdout.getvalue())["contract_sha256"]
            frozen_document = inventory.load_inventory_contract(
                frozen_contract, contract_sha, verify_bindings=True
            )
            self.assertEqual(agents_home.as_posix(), frozen_document["agents_home"]["path"])
            self.assertEqual(2, frozen_document["skill_junction_mirror_count"])
            self.assertEqual(
                hashlib.sha256(
                    inventory._canonical_json(
                        frozen_document["skill_junction_mirrors"]
                    ).encode("utf-8")
                ).hexdigest(),
                frozen_document["skill_junction_mirrors_sha256"],
            )
            target_before = os.stat(target_member)
            target_original = target_member.read_bytes()
            target_member.write_bytes(b"other-bytes")
            os.utime(
                target_member,
                ns=(target_before.st_atime_ns, target_before.st_mtime_ns),
            )
            with redirect_stderr(io.StringIO()):
                drift_collect_rc = inventory.main(
                    [
                        "collect",
                        "--contract", os.fspath(frozen_contract),
                        "--contract-sha256", contract_sha,
                        "--output", os.fspath(frozen_output),
                    ]
                )
            self.assertNotEqual(0, drift_collect_rc)
            self.assertFalse(frozen_output.exists())
            self.assertFalse(frozen_metadata.exists())
            target_member.write_bytes(target_original)
            os.utime(
                target_member,
                ns=(target_before.st_atime_ns, target_before.st_mtime_ns),
            )
            with redirect_stdout(io.StringIO()) as collect_stdout:
                collect_rc = inventory.main(
                    [
                        "collect",
                        "--contract", os.fspath(frozen_contract),
                        "--contract-sha256", contract_sha,
                        "--output", os.fspath(frozen_output),
                    ]
                )
            self.assertEqual(0, collect_rc)
            self.assertEqual(452 + 9 + 5 + 4 + 1, json.loads(collect_stdout.getvalue())["total"])
            self.assertTrue(frozen_output.is_file())
            self.assertTrue(frozen_metadata.is_file())
            with redirect_stdout(io.StringIO()) as summary_stdout:
                summarize_rc = inventory.main(
                    [
                        "summarize",
                        "--contract", os.fspath(frozen_contract),
                        "--contract-sha256", contract_sha,
                        "--input", os.fspath(frozen_output),
                    ]
                )
            self.assertEqual(0, summarize_rc)
            self.assertEqual(
                json.loads(collect_stdout.getvalue())["total"],
                json.loads(summary_stdout.getvalue())["total"],
            )
            late_target_member = write_file(populated_target / "late.md", b"late")
            try:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    drift_summary_rc = inventory.main(
                        [
                            "summarize",
                            "--contract", os.fspath(frozen_contract),
                            "--contract-sha256", contract_sha,
                            "--input", os.fspath(frozen_output),
                        ]
                    )
                self.assertNotEqual(0, drift_summary_rc)
            finally:
                late_target_member.unlink()

            def freeze_for(stem: str) -> tuple[Path, str, Path, Path]:
                contract_path = base / f"{stem}-contract.json"
                output_path = base / f"{stem}.jsonl"
                metadata_path = base / f"{stem}.metadata.json"
                sha = inventory.freeze_inventory_contract(
                    contract_path=contract_path,
                    output_path=output_path,
                    metadata_path=metadata_path,
                    claude_home=claude_home,
                    roots=roots,
                    domain_decisions=decision_file,
                    chat_projects={
                        "home": "C--Users-fixture--claude",
                        "mercury": "D--Mercury-Mercury",
                        "godot": "D--ShipOfTheseus-Ship-of-Theseus",
                    },
                    production=False,
                )
                return contract_path, sha, output_path, metadata_path

            legitimate_metadata, legitimate_records = inventory._parse_manifest(frozen_output)

            with self.subTest(regression="production-prepublish-protection-drift"):
                protected = base / "protected"
                protected.mkdir()
                protected_decisions = write_file(
                    protected / "domain-decisions.json", b'{"decisions":{}}'
                )
                protected_contract = protected / "inventory-contract.json"
                protected_output = protected / "assets.jsonl"
                protected_metadata = protected / "assets.metadata.json"
                fixture_tool = inventory._tool_binding(roots["mercury"], production=False)
                initial_protection = {
                    "path": protected.as_posix(),
                    "root_identity": "00000001:0000000000000002",
                    "owner_sid": "S-1-5-21-1001",
                    "acl_sha256": "0" * 64,
                    "efs": True,
                }
                drifted_protection = {
                    **initial_protection,
                    "root_identity": "00000001:0000000000000003",
                }
                protections = iter((initial_protection, drifted_protection))
                order: list[str] = []
                real_collect_for_order = inventory.collect_inventory
                real_write_for_order = inventory._write_new

                def verify_for_order(_root, **_kwargs):
                    order.append("verify")
                    return next(protections)

                def collect_for_order(*args, **kwargs):
                    order.append("collect")
                    return real_collect_for_order(*args, **kwargs)

                def write_for_order(*args, **kwargs):
                    order.append("write")
                    return real_write_for_order(*args, **kwargs)

                with mock.patch.object(inventory, "PRODUCTION_OUTPUT_ROOT", protected), mock.patch.object(
                    inventory, "PRODUCTION_ROOTS", roots
                ), mock.patch.object(
                    inventory,
                    "PRODUCTION_CHAT_PROJECTS",
                    {
                        "home": "C--Users-fixture--claude",
                        "mercury": "D--Mercury-Mercury",
                        "godot": "D--ShipOfTheseus-Ship-of-Theseus",
                    },
                ), mock.patch.object(
                    inventory.Path, "home", return_value=claude_home.parent
                ), mock.patch.object(
                    inventory, "_verify_protected_root", side_effect=verify_for_order
                ), mock.patch.object(
                    inventory, "_tool_binding", return_value=fixture_tool
                ), mock.patch.object(
                    inventory, "collect_inventory", side_effect=collect_for_order
                ), mock.patch.object(
                    inventory, "_write_new", side_effect=write_for_order
                ):
                    with self.assertRaisesRegex(inventory.ContractError, "protected|identity|ACL"):
                        inventory.freeze_inventory_contract(
                            contract_path=protected_contract,
                            output_path=protected_output,
                            metadata_path=protected_metadata,
                            claude_home=claude_home,
                            roots=roots,
                            domain_decisions=protected_decisions,
                            chat_projects={
                                "home": "C--Users-fixture--claude",
                                "mercury": "D--Mercury-Mercury",
                                "godot": "D--ShipOfTheseus-Ship-of-Theseus",
                            },
                            production=True,
                        )
                self.assertEqual(["verify", "collect", "verify"], order[:3])
                self.assertNotIn("write", order)
                self.assertFalse(protected_contract.exists())

            with self.subTest(regression="live-content-change-with-restored-mtime"):
                memory_record = next(
                    record for record in legitimate_records if record.kind == "memory"
                )
                memory_path = Path(memory_record.source)
                original_bytes = memory_path.read_bytes()
                original_stat = os.stat(memory_path)
                replacement = bytes(
                    (byte ^ 0x01) for byte in original_bytes
                )
                try:
                    with memory_path.open("r+b", buffering=0) as stream:
                        stream.write(replacement)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.utime(
                        memory_path,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )
                    changed_stat = os.stat(memory_path)
                    self.assertEqual(
                        (original_stat.st_ino, original_stat.st_size, original_stat.st_mtime_ns),
                        (changed_stat.st_ino, changed_stat.st_size, changed_stat.st_mtime_ns),
                    )
                    with self.assertRaisesRegex(Exception, "corpus|content|member"):
                        inventory.summarize_manifest(
                            frozen_output,
                            contract_path=frozen_contract,
                            contract_sha256=contract_sha,
                        )
                finally:
                    with memory_path.open("r+b", buffering=0) as stream:
                        stream.write(original_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.utime(
                        memory_path,
                        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
                    )

            with self.subTest(regression="self-consistent-unapproved-member"):
                member_contract, member_sha, member_output, member_sidecar = freeze_for(
                    "forged-member"
                )
                member_document = inventory.load_inventory_contract(
                    member_contract, member_sha, verify_bindings=True
                )
                forged_records = list(legitimate_records)
                member_index = next(
                    index for index, record in enumerate(forged_records)
                    if record.kind == "memory"
                )
                original_member = forged_records[member_index]
                bad_key = "UNAPPROVED-PROJECT/nonexistent.md"
                forged_records[member_index] = replace(
                    original_member,
                    canonical_key=bad_key,
                    source=(
                        claude_home
                        / "projects"
                        / "UNAPPROVED-PROJECT"
                        / "memory"
                        / "nonexistent.md"
                    ).as_posix(),
                    asset_id=inventory.compute_asset_id(
                        original_member.source_namespace,
                        bad_key,
                        original_member.sha256,
                    ),
                )
                forged_records.sort(key=inventory._record_sort_key)
                forged_payload = inventory._records_payload(forged_records)
                forged_counts = dict(
                    sorted(Counter(item.kind for item in forged_records).items())
                )
                forged_metadata = dict(legitimate_metadata)
                forged_metadata.update(
                    contract_sha256=member_sha,
                    contract_payload_sha256=member_document["contract_payload_sha256"],
                    output=member_document["output"],
                    tool=member_document["tool"],
                    roots=member_document["roots"],
                    claude_home=member_document["claude_home"],
                    window=member_document["window"],
                    chat_sessions=member_document["chat_sessions"],
                    expected_counts=member_document["expected"]["counts"],
                    actual_counts=forged_counts,
                    record_count=len(forged_records),
                    records_sha256=hashlib.sha256(forged_payload.encode()).hexdigest(),
                )
                forged_manifest = inventory._sealed_manifest(
                    model.InventoryManifest(forged_metadata, tuple(forged_records))
                )
                inventory._write_new(
                    member_sidecar,
                    inventory._canonical_json(forged_manifest.metadata) + "\n",
                )
                inventory._write_new(
                    member_output, inventory.manifest_to_jsonl(forged_manifest)
                )
                with self.assertRaisesRegex(Exception, "approved|corpus|member"):
                    inventory.summarize_manifest(
                        member_output,
                        contract_path=member_contract,
                        contract_sha256=member_sha,
                    )

            with self.subTest(regression="self-consistent-junction-decision-reversal"):
                decision_contract, decision_sha, decision_output, decision_sidecar = freeze_for(
                    "forged-junction-decision"
                )
                decision_document = inventory.load_inventory_contract(
                    decision_contract, decision_sha, verify_bindings=True
                )
                forged_records = list(legitimate_records)
                target_index = next(
                    index
                    for index, record in enumerate(forged_records)
                    if record.source_namespace == "claude-user-skill-target"
                )
                forged_records[target_index] = replace(
                    forged_records[target_index],
                    domain="other",
                    domain_reason="controlled-decision:synthetic-review",
                    disposition="exclude-domain",
                    disposition_status="domain-decided",
                    decision_evidence="synthetic-review-evidence",
                )
                forged_records.sort(key=inventory._record_sort_key)
                forged_payload = inventory._records_payload(forged_records)
                forged_metadata = dict(legitimate_metadata)
                forged_metadata.update(
                    contract_sha256=decision_sha,
                    contract_payload_sha256=decision_document["contract_payload_sha256"],
                    output=decision_document["output"],
                    tool=decision_document["tool"],
                    roots=decision_document["roots"],
                    claude_home=decision_document["claude_home"],
                    agents_home=decision_document["agents_home"],
                    window=decision_document["window"],
                    chat_sessions=decision_document["chat_sessions"],
                    expected_counts=decision_document["expected"]["counts"],
                    approved_members_sha256=decision_document["approved_members_sha256"],
                    approved_member_count=decision_document["approved_member_count"],
                    skill_junction_mirrors=decision_document["skill_junction_mirrors"],
                    skill_junction_mirror_count=decision_document[
                        "skill_junction_mirror_count"
                    ],
                    skill_junction_mirrors_sha256=decision_document[
                        "skill_junction_mirrors_sha256"
                    ],
                    actual_counts=dict(
                        sorted(Counter(item.kind for item in forged_records).items())
                    ),
                    record_count=len(forged_records),
                    records_sha256=hashlib.sha256(forged_payload.encode()).hexdigest(),
                )
                forged_manifest = inventory._sealed_manifest(
                    model.InventoryManifest(forged_metadata, tuple(forged_records))
                )
                inventory._write_new(
                    decision_sidecar,
                    inventory._canonical_json(forged_manifest.metadata) + "\n",
                )
                inventory._write_new(
                    decision_output, inventory.manifest_to_jsonl(forged_manifest)
                )
                with self.assertRaisesRegex(Exception, "approved|decision|member"):
                    inventory.summarize_manifest(
                        decision_output,
                        contract_path=decision_contract,
                        contract_sha256=decision_sha,
                    )

            forged_contract, forged_sha, forged_output, forged_sidecar = freeze_for("forged-452")
            forged_document = inventory.load_inventory_contract(
                forged_contract, forged_sha, verify_bindings=True
            )
            core_kinds = {"chat", "memory", "memory-auxiliary", "memory-archive"}
            forged_records = []
            for index, record in enumerate(
                item for item in legitimate_records if item.kind in core_kinds
            ):
                changes = {"source": f"D:/outside/{index:03d}.bin"}
                if record.kind == "chat":
                    changes.update(
                        session_id=None,
                        source_cwd=None,
                        first_user_request_sha256=None,
                    )
                forged_records.append(replace(record, **changes))
            self.assertEqual(452, len(forged_records))
            forged_payload = records_to_jsonl(forged_records)
            forged_counts = dict(sorted(Counter(item.kind for item in forged_records).items()))
            forged_metadata = dict(legitimate_metadata)
            forged_metadata.update(
                contract_sha256=forged_sha,
                contract_payload_sha256=forged_document["contract_payload_sha256"],
                output=forged_document["output"],
                tool=forged_document["tool"],
                roots=forged_document["roots"],
                claude_home=forged_document["claude_home"],
                window=forged_document["window"],
                chat_sessions=forged_document["chat_sessions"],
                expected_counts=forged_document["expected"]["counts"],
                actual_counts=forged_counts,
                record_count=len(forged_records),
                records_sha256=hashlib.sha256(forged_payload.encode()).hexdigest(),
            )
            forged_manifest = inventory._sealed_manifest(
                model.InventoryManifest(forged_metadata, tuple(forged_records))
            )
            inventory._write_new(forged_sidecar, inventory._canonical_json(forged_manifest.metadata) + "\n")
            inventory._write_new(forged_output, inventory.manifest_to_jsonl(forged_manifest))
            with self.assertRaisesRegex(Exception, "chat|outside|namespace|approved|corpus|member"):
                inventory.summarize_manifest(
                    forged_output,
                    contract_path=forged_contract,
                    contract_sha256=forged_sha,
                )

            tamper_contract, tamper_sha, tamper_output, tamper_sidecar = freeze_for("tampered")
            tamper_document = inventory.load_inventory_contract(
                tamper_contract, tamper_sha, verify_bindings=True
            )
            tampered_metadata = dict(legitimate_metadata)
            tampered_metadata.update(
                contract_sha256=tamper_sha,
                contract_payload_sha256=tamper_document["contract_payload_sha256"],
                output=tamper_document["output"],
                tool={"commit": "f" * 40, "repo_root": "D:/elsewhere", "files": []},
            )
            tampered_manifest = inventory._sealed_manifest(
                model.InventoryManifest(tampered_metadata, tuple(legitimate_records))
            )
            inventory._write_new(
                tamper_sidecar, inventory._canonical_json(tampered_manifest.metadata) + "\n"
            )
            inventory._write_new(tamper_output, inventory.manifest_to_jsonl(tampered_manifest))
            with self.assertRaisesRegex(Exception, "tool"):
                inventory.summarize_manifest(
                    tamper_output,
                    contract_path=tamper_contract,
                    contract_sha256=tamper_sha,
                )

            extra_contract, extra_sha, extra_output, extra_sidecar = freeze_for(
                "unexpected-metadata"
            )
            extra_document = inventory.load_inventory_contract(
                extra_contract, extra_sha, verify_bindings=True
            )
            extra_metadata = dict(legitimate_metadata)
            extra_metadata.update(
                contract_sha256=extra_sha,
                contract_payload_sha256=extra_document["contract_payload_sha256"],
                output=extra_document["output"],
                tool=extra_document["tool"],
                unexpected_claim=True,
            )
            extra_manifest = inventory._sealed_manifest(
                model.InventoryManifest(extra_metadata, tuple(legitimate_records))
            )
            inventory._write_new(
                extra_sidecar, inventory._canonical_json(extra_manifest.metadata) + "\n"
            )
            inventory._write_new(
                extra_output, inventory.manifest_to_jsonl(extra_manifest)
            )
            with self.assertRaisesRegex(Exception, "schema|field|metadata"):
                inventory.summarize_manifest(
                    extra_output,
                    contract_path=extra_contract,
                    contract_sha256=extra_sha,
                )

            unstable_contract, unstable_sha, unstable_output, unstable_sidecar = freeze_for(
                "unstable-corpus"
            )
            real_backups = inventory.inventory_claude_backups

            def mutate_backups(*args, **kwargs):
                result = real_backups(*args, **kwargs)
                write_file(claude_home / "backups" / "late-created.backup", b"late")
                return result

            with mock.patch.object(inventory, "inventory_claude_backups", mutate_backups):
                with redirect_stderr(io.StringIO()):
                    unstable_rc = inventory.main(
                        [
                            "collect",
                            "--contract", os.fspath(unstable_contract),
                            "--contract-sha256", unstable_sha,
                            "--output", os.fspath(unstable_output),
                        ]
                    )
            self.assertNotEqual(0, unstable_rc)
            self.assertFalse(unstable_output.exists())
            self.assertFalse(unstable_sidecar.exists())

            with self.subTest(regression="git-content-change-with-restored-mtime"):
                content_contract, content_sha, content_output, content_sidecar = freeze_for(
                    "unstable-git-content"
                )
                tracked = roots["mercury"] / "tracked.txt"
                tracked_bytes = tracked.read_bytes()
                tracked_stat = os.stat(tracked)
                real_dirty_content = inventory.inventory_repo_dirty
                mutated = False

                def mutate_git_content(root, *args, **kwargs):
                    nonlocal mutated
                    result = real_dirty_content(root, *args, **kwargs)
                    if Path(root) == roots["mercury"] and not mutated:
                        mutated = True
                        with tracked.open("r+b", buffering=0) as stream:
                            stream.write(b"later")
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.utime(
                            tracked,
                            ns=(tracked_stat.st_atime_ns, tracked_stat.st_mtime_ns),
                        )
                    return result

                try:
                    with mock.patch.object(
                        inventory, "inventory_repo_dirty", mutate_git_content
                    ):
                        with redirect_stderr(io.StringIO()):
                            content_rc = inventory.main(
                                [
                                    "collect",
                                    "--contract", os.fspath(content_contract),
                                    "--contract-sha256", content_sha,
                                    "--output", os.fspath(content_output),
                                ]
                            )
                finally:
                    with tracked.open("r+b", buffering=0) as stream:
                        stream.write(tracked_bytes)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.utime(
                        tracked,
                        ns=(tracked_stat.st_atime_ns, tracked_stat.st_mtime_ns),
                    )
                self.assertNotEqual(0, content_rc)
                self.assertFalse(content_output.exists())
                self.assertFalse(content_sidecar.exists())

            git_contract, git_sha, git_output, git_sidecar = freeze_for("unstable-git")
            real_dirty = inventory.inventory_repo_dirty

            def mutate_git(root, *args, **kwargs):
                result = real_dirty(root, *args, **kwargs)
                if Path(root) == roots["mercury"]:
                    write_file(roots["mercury"] / "late untracked.txt", b"late")
                return result

            with mock.patch.object(inventory, "inventory_repo_dirty", mutate_git):
                with redirect_stderr(io.StringIO()):
                    git_rc = inventory.main(
                        [
                            "collect",
                            "--contract", os.fspath(git_contract),
                            "--contract-sha256", git_sha,
                            "--output", os.fspath(git_output),
                        ]
                    )
            self.assertNotEqual(0, git_rc)
            self.assertFalse(git_output.exists())
            self.assertFalse(git_sidecar.exists())


@unittest.skipUnless(os.name == "nt", "Windows junction contract regressions")
class UserSkillJunctionTests(unittest.TestCase):
    def _roots(self, base: Path) -> tuple[Path, Path, Path]:
        home = base / "home" / ".claude"
        agents_home = base / "home" / ".agents"
        (home / "skills").mkdir(parents=True)
        (agents_home / "skills").mkdir(parents=True)
        return home, agents_home, agents_home / "skills"

    def test_empty_direct_same_name_junction_is_a_visible_relation_not_an_asset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, agents_home, targets = self._roots(Path(directory).resolve())
            target = targets / "empty-skill"
            target.mkdir()
            link = create_junction(home / "skills" / "empty-skill", target)

            records = inventory.inventory_claude_skills(home)
            relations = inventory._discover_skill_junction_mirrors(home, agents_home)

            self.assertEqual([], records)
            self.assertEqual(1, len(relations))
            relation = relations[0]
            self.assertEqual("claude-user-skill-junction", relation["relation_type"])
            self.assertEqual(link.as_posix(), relation["link_path"])
            self.assertEqual(target.as_posix(), relation["canonical_target"])
            self.assertEqual(0, relation["target_member_count"])
            self.assertEqual([], relation["target_members"])
            self.assertEqual(
                hashlib.sha256(b"[]").hexdigest(), relation["target_members_sha256"]
            )

    def test_populated_direct_same_name_junction_members_are_canonical_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, agents_home, targets = self._roots(Path(directory).resolve())
            target = targets / "review-skill"
            member = write_file(target / "SKILL.md", b"review instructions")
            create_junction(home / "skills" / "review-skill", target)

            records = inventory.inventory_claude_skills(home)
            relation = inventory._discover_skill_junction_mirrors(home, agents_home)[0]

            self.assertEqual(1, len(records))
            record = records[0]
            self.assertEqual(member.as_posix(), record.source)
            self.assertEqual("claude-user-skill-target", record.source_namespace)
            self.assertEqual("review-skill/SKILL.md", record.canonical_key)
            self.assertEqual("skill", record.kind)
            self.assertEqual("exclude-domain", record.disposition)
            self.assertEqual("domain-decided", record.disposition_status)
            self.assertEqual("already-native-alias/no-import", record.domain_reason)
            self.assertEqual(
                "junction-relation:direct-same-name",
                record.decision_evidence,
            )
            self.assertEqual(
                ((home / "skills" / "review-skill" / "SKILL.md").as_posix(),),
                record.observed_mirrors,
            )
            self.assertEqual(1, relation["target_member_count"])
            descriptor = relation["target_members"][0]
            self.assertEqual(
                {
                    "record_role",
                    "source",
                    "source_namespace",
                    "canonical_key",
                    "size",
                    "sha256",
                    "mtime_ns",
                    "file_id",
                },
                set(descriptor),
            )
            self.assertEqual("relation-target-member", descriptor["record_role"])
            self.assertEqual("claude-user-skill-target", descriptor["source_namespace"])
            self.assertEqual("review-skill/SKILL.md", descriptor["canonical_key"])
            self.assertEqual(member.as_posix(), descriptor["source"])
            self.assertEqual(hashlib.sha256(b"review instructions").hexdigest(), descriptor["sha256"])
            self.assertNotIn("disposition", descriptor)
            self.assertNotIn("asset_id", descriptor)

    def test_native_alias_secret_container_keeps_secret_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, _agents_home, targets = self._roots(Path(directory).resolve())
            target = targets / "secret-skill"
            write_file(target / ".credentials.json", b'{"fixture":"value"}')
            create_junction(home / "skills" / "secret-skill", target)

            record = inventory.inventory_claude_skills(home)[0]

            self.assertEqual("exclude-secret", record.disposition)
            self.assertEqual("provisional", record.disposition_status)

    def test_approved_member_binding_rejects_target_decision_reversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, agents_home, targets = self._roots(Path(directory).resolve())
            target = targets / "mercury-skill"
            write_file(target / "SKILL.md", b"mercury fixture")
            create_junction(home / "skills" / "mercury-skill", target)
            record = inventory.inventory_claude_skills(home)[0]
            self.assertEqual("exclude-domain", record.disposition)
            members = inventory._approved_members([record])
            document = {
                "approved_members": members,
                "approved_member_count": 1,
                "approved_members_sha256": inventory._approved_members_sha256(members),
            }
            reversed_record = replace(
                record,
                domain="other",
                domain_reason="controlled-decision:synthetic-review",
                disposition="exclude-domain",
                disposition_status="domain-decided",
                decision_evidence="synthetic-review-evidence",
            )

            with self.assertRaises(inventory.ManifestError):
                inventory._validate_approved_record_membership(
                    [reversed_record], document
                )

    def test_case_only_link_target_name_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, agents_home, targets = self._roots(Path(directory).resolve())
            target = targets / "review-skill"
            target.mkdir()
            create_junction(home / "skills" / "Review-Skill", target)

            with self.assertRaises(inventory.ReparsePointError):
                inventory._discover_skill_junction_mirrors(home, agents_home)

    def test_link_target_bytes_membership_and_identity_drift_fail_closed(self) -> None:
        for mutation in ("link", "bytes", "membership", "target-identity"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                home, agents_home, targets = self._roots(Path(directory).resolve())
                target = targets / "stable-skill"
                member = write_file(target / "SKILL.md", b"before")
                link = create_junction(home / "skills" / "stable-skill", target)
                frozen = inventory._discover_skill_junction_mirrors(home, agents_home)

                if mutation == "link":
                    os.rmdir(link)
                    create_junction(link, target)
                elif mutation == "bytes":
                    original = os.stat(member)
                    member.write_bytes(b"after!")
                    os.utime(member, ns=(original.st_atime_ns, original.st_mtime_ns))
                elif mutation == "membership":
                    write_file(target / "added.md", b"added")
                else:
                    old_target = targets / "old-stable-skill"
                    target.rename(old_target)
                    target.mkdir()
                    write_file(target / "SKILL.md", b"before")

                with self.assertRaises(inventory.SourceChangedError):
                    inventory._assert_skill_junction_mirrors(home, agents_home, frozen)

    def test_external_mismatched_chained_cycle_and_nested_reparse_are_rejected(self) -> None:
        scenarios = ("external", "mismatched", "chained", "cycle", "nested")
        for scenario in scenarios:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                base = Path(directory).resolve()
                home, agents_home, targets = self._roots(base)
                link_name = "review-skill"
                link = home / "skills" / link_name
                cleanup: list[Path] = []
                if scenario == "external":
                    target = base / "outside" / link_name
                    target.mkdir(parents=True)
                    create_junction(link, target)
                elif scenario == "mismatched":
                    target = targets / "different-name"
                    target.mkdir()
                    create_junction(link, target)
                elif scenario == "chained":
                    real = targets / "real"
                    real.mkdir()
                    target = create_junction(targets / link_name, real)
                    create_junction(link, target)
                elif scenario == "cycle":
                    target = targets / link_name
                    target.mkdir()
                    create_junction(link, target)
                    target.rmdir()
                    create_junction(target, link)
                    cleanup.extend((target, link))
                else:
                    target = targets / link_name
                    nested_target = targets / "nested-real"
                    target.mkdir()
                    nested_target.mkdir()
                    create_junction(target / "nested", nested_target)
                    create_junction(link, target)

                try:
                    with self.assertRaises(inventory.ReparsePointError):
                        inventory._discover_skill_junction_mirrors(home, agents_home)
                finally:
                    for path in cleanup:
                        try:
                            os.rmdir(path)
                        except OSError:
                            pass

    def test_other_category_and_nested_skill_reparse_remain_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            home, _agents_home, _targets = self._roots(base)
            outside = base / "outside"
            outside.mkdir()
            create_junction(home / "hooks" / "linked", outside)
            regular_skill = home / "skills" / "ordinary"
            regular_skill.mkdir()
            create_junction(regular_skill / "nested", outside)

            with self.assertRaises(inventory.ReparsePointError):
                inventory.inventory_claude_hooks(home)
            with self.assertRaises(inventory.ReparsePointError):
                inventory.inventory_claude_skills(home)

    def test_relation_schema_rejects_alias_members_and_self_consistent_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home, agents_home, targets = self._roots(Path(directory).resolve())
            target = targets / "schema-skill"
            write_file(target / "SKILL.md", b"schema")
            link = create_junction(home / "skills" / "schema-skill", target)
            relations = inventory._discover_skill_junction_mirrors(home, agents_home)
            base_document = {
                "claude_home": inventory._root_identity(home),
                "agents_home": inventory._root_identity(agents_home),
                "skill_junction_mirrors": relations,
                "skill_junction_mirror_count": 1,
                "skill_junction_mirrors_sha256": inventory._skill_junction_mirrors_sha256(relations),
            }

            for mutation in (
                "alias-source",
                "extra-field",
                "wrong-raw-target",
                "raw-dot-segment",
                "case-only-target",
                "boolean-relation-count",
                "boolean-member-count",
                "dot-segment",
                "repeated-separator",
            ):
                tampered = json.loads(json.dumps(base_document))
                relation = tampered["skill_junction_mirrors"][0]
                if mutation == "alias-source":
                    relation["target_members"][0]["source"] = (
                        link / "SKILL.md"
                    ).as_posix()
                    relation["target_members_sha256"] = hashlib.sha256(
                        inventory._canonical_json(relation["target_members"]).encode("utf-8")
                    ).hexdigest()
                elif mutation == "extra-field":
                    relation["target_members"][0]["disposition"] = "import"
                    relation["target_members_sha256"] = hashlib.sha256(
                        inventory._canonical_json(relation["target_members"]).encode("utf-8")
                    ).hexdigest()
                elif mutation == "wrong-raw-target":
                    relation["raw_target"] = r"\\?\C:\outside\schema-skill"
                elif mutation == "raw-dot-segment":
                    relation["raw_target"] = relation["raw_target"].replace(
                        "\\schema-skill", "\\.\\schema-skill"
                    )
                elif mutation == "case-only-target":
                    relation["canonical_target"] = relation["canonical_target"].replace(
                        "schema-skill", "Schema-Skill"
                    )
                    relation["raw_target"] = relation["raw_target"].replace(
                        "schema-skill", "Schema-Skill"
                    )
                elif mutation == "boolean-relation-count":
                    tampered["skill_junction_mirror_count"] = True
                elif mutation == "boolean-member-count":
                    relation["target_member_count"] = True
                else:
                    member = relation["target_members"][0]
                    member["canonical_key"] = (
                        "schema-skill/./SKILL.md"
                        if mutation == "dot-segment"
                        else "schema-skill//SKILL.md"
                    )
                    relation["target_members_sha256"] = hashlib.sha256(
                        inventory._canonical_json(relation["target_members"]).encode("utf-8")
                    ).hexdigest()
                tampered["skill_junction_mirrors_sha256"] = (
                    inventory._skill_junction_mirrors_sha256(
                        tampered["skill_junction_mirrors"]
                    )
                )
                with self.subTest(mutation=mutation):
                    with self.assertRaises(inventory.ContractError):
                        inventory._validate_skill_junction_contract(tampered)


class CorpusStabilityTests(unittest.TestCase):
    def test_membership_fingerprint_detects_add_delete_rename_swap_and_identity_change(self) -> None:
        mutations = ("add", "delete", "rename", "swap", "identity")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                first = write_file(root / "first.md", b"first")
                second = write_file(root / "second.md", b"second")
                before = inventory._membership_fingerprint(root, [first, second])
                if mutation == "add":
                    write_file(root / "third.md", b"third")
                    paths = [first, second, root / "third.md"]
                elif mutation == "delete":
                    first.unlink()
                    paths = [second]
                elif mutation == "rename":
                    renamed = first.with_name("renamed.md")
                    first.rename(renamed)
                    paths = [renamed, second]
                elif mutation == "swap":
                    first.unlink()
                    replacement = write_file(root / "replacement.md", b"replacement")
                    paths = [second, replacement]
                else:
                    first.unlink()
                    write_file(first, b"new identity")
                    paths = [first, second]
                after = inventory._membership_fingerprint(root, paths)
                self.assertNotEqual(before, after)

    def test_membership_fingerprint_detects_same_inode_size_and_mtime_byte_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            member = write_file(root / "member.md", b"AAAA")
            original = os.stat(member)
            before = inventory._membership_fingerprint(root, [member])

            with member.open("r+b", buffering=0) as stream:
                stream.write(b"BBBB")
                stream.flush()
                os.fsync(stream.fileno())
            os.utime(member, ns=(original.st_atime_ns, original.st_mtime_ns))

            current = os.stat(member)
            self.assertEqual((original.st_ino, original.st_size, original.st_mtime_ns),
                             (current.st_ino, current.st_size, current.st_mtime_ns))
            self.assertNotEqual(before, inventory._membership_fingerprint(root, [member]))


class FrozenContractTests(unittest.TestCase):
    def test_contract_atomically_binds_window_sessions_roots_decisions_tools_and_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            home = base / "home" / ".claude"
            (home.parent / ".agents" / "skills").mkdir(parents=True)
            projects = home / "projects"
            project_names = {
                "home": "C--Users-fixture--claude",
                "mercury": "D--Mercury-Mercury",
                "godot": "D--ShipOfTheseus-Ship-of-Theseus",
            }
            expected = {"home": 45, "mercury": 14, "godot": 9}
            in_window = 1_785_542_400
            sessions: dict[str, list[str]] = {}
            for group, count in expected.items():
                sessions[group] = []
                for _ in range(count):
                    session_id = str(uuid.uuid4())
                    sessions[group].append(session_id)
                    chat = write_file(
                        projects / project_names[group] / f"{session_id}.jsonl",
                        b'{"type":"user","message":{"role":"user","content":"Mercury"}}\n',
                    )
                    os.utime(chat, (in_window, in_window))
            outside_window_sessions = []
            for timestamp in (1_784_127_599, 1_786_719_600):
                session_id = str(uuid.uuid4())
                outside_window_sessions.append(session_id)
                chat = write_file(
                    projects
                    / project_names["home"]
                    / f"{session_id}.jsonl",
                    b'{"type":"user","message":{"role":"user","content":"Mercury"}}\n',
                )
                os.utime(chat, (timestamp, timestamp))
            create_counted_files(projects / "D--Mercury-Mercury" / "memory", 295)
            write_file(projects / "D--Mercury-Mercury" / "memory" / "design.json")
            write_file(projects / "D--Mercury-Mercury" / "memory" / "verify.py")
            write_file(projects / "D--Mercury-Mercury" / "memory" / "cache.pyc")
            create_counted_files(
                projects / "D--ShipOfTheseus-Ship-of-Theseus" / "memory", 57
            )
            for project_name, count in {
                "D--Mercury": 11,
                "D--Mercury-AgentKB": 6,
                "D--Mercury-Argus": 4,
                "D--Mercury-Mercury-side-bug": 2,
                "D--ShipOfTheseus-Ship_of_Theseus": 3,
            }.items():
                create_counted_files(projects / project_name / "memory", count)
            create_counted_files(
                projects / "D--Mercury-stock-agent-candidates-TradingAgents" / "memory",
                3,
            )
            roots = {
                name: create_git_repo(base / name)
                for name in ("mercury", "godot", "design", "kb")
            }
            decisions = write_file(base / "domain-decisions.json", b'{"decisions":{}}')
            contract_path = base / "inventory-contract.json"
            output = base / "assets.jsonl"
            metadata = base / "assets.metadata.json"

            contract_sha = inventory.freeze_inventory_contract(
                contract_path=contract_path,
                output_path=output,
                metadata_path=metadata,
                claude_home=home,
                roots=roots,
                domain_decisions=decisions,
                chat_projects=project_names,
                production=False,
            )
            document = inventory.load_inventory_contract(
                contract_path, contract_sha, verify_bindings=True
            )

            self.assertEqual("2026-07-15T15:00:00+00:00", document["window"]["start"])
            self.assertEqual(
                "2026-08-14T14:59:59.999999+00:00", document["window"]["as_of"]
            )
            self.assertEqual(expected, document["expected"]["chat_groups"])
            self.assertEqual(
                {name: sorted(values) for name, values in sessions.items()},
                document["chat_sessions"],
            )
            self.assertTrue(
                all(
                    session not in document["chat_sessions"]["home"]
                    for session in outside_window_sessions
                )
            )
            self.assertEqual({"mercury", "godot", "design", "kb"}, set(document["roots"]))
            self.assertEqual(decisions.as_posix(), document["domain_decisions"]["path"])
            self.assertEqual(output.as_posix(), document["output"]["manifest"])
            self.assertEqual(metadata.as_posix(), document["output"]["metadata"])
            self.assertEqual(3, document["schema_version"])
            self.assertEqual((home.parent / ".agents").as_posix(), document["agents_home"]["path"])
            self.assertEqual([], document["skill_junction_mirrors"])
            self.assertEqual(
                {
                    "inventory.py",
                    "model.py",
                    "secure_backup_root.ps1",
                },
                {Path(item["path"]).name for item in document["tool"]["files"]},
            )
            self.assertRegex(document["contract_payload_sha256"], r"^[0-9a-f]{64}$")
            for index, mutation in enumerate(
                (
                    ("window", {"start": "2026-07-15T15:00:01+00:00", "as_of": document["window"]["as_of"]}),
                    ("expected", {"chat_groups": {"home": 44, "mercury": 15, "godot": 9}, "counts": document["expected"]["counts"]}),
                    ("output", {**document["output"], "schema": "invented-schema"}),
                )
            ):
                field, replacement = mutation
                tampered = json.loads(json.dumps(document))
                tampered[field] = replacement
                tampered.pop("contract_payload_sha256")
                tampered["contract_payload_sha256"] = hashlib.sha256(
                    inventory._canonical_json(tampered).encode()
                ).hexdigest()
                tampered_path = base / f"self-consistent-tampered-{index}.json"
                serialized = inventory._canonical_json(tampered) + "\n"
                inventory._write_new(tampered_path, serialized)
                tampered_sha = hashlib.sha256(serialized.encode()).hexdigest()
                with self.subTest(field=field):
                    with self.assertRaises(inventory.ContractError):
                        inventory.load_inventory_contract(
                            tampered_path, tampered_sha, verify_bindings=False
                        )
            with self.assertRaises(FileExistsError):
                inventory.freeze_inventory_contract(
                    contract_path=contract_path,
                    output_path=output,
                    metadata_path=metadata,
                    claude_home=home,
                    roots=roots,
                    domain_decisions=decisions,
                    chat_projects=project_names,
                    production=False,
                )
            decisions.write_bytes(b'{"decisions":{"changed":{}}}')
            with self.assertRaisesRegex(Exception, "decision"):
                inventory.load_inventory_contract(
                    contract_path, contract_sha, verify_bindings=True
                )


    def test_missing_mandatory_root_and_count_drift_fail_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            claude_home = base / ".claude"
            claude_home.mkdir()
            roots = {
                "mercury": create_git_repo(base / "Mercury"),
                "godot": create_git_repo(base / "Godot"),
                "design": create_git_repo(base / "Design"),
                "kb": base / "missing-kb",
            }
            contract = inventory.InventoryContract(
                claude_home=claude_home,
                mercury_root=roots["mercury"],
                godot_root=roots["godot"],
                design_root=roots["design"],
                kb_root=roots["kb"],
                cutoff="2023-11-14T22:13:20Z",
                expected_counts={"chat": 68, "memory": 378, "memory-auxiliary": 3, "memory-archive": 3},
                production=False,
                tool_commit="fixture",
                tool_sha256="0" * 64,
            )
            with self.assertRaisesRegex(Exception, "mandatory root"):
                inventory.collect_inventory(contract)

            roots["kb"] = create_git_repo(roots["kb"])
            (claude_home / "projects").mkdir()
            count_drift_contract = replace(contract, kb_root=roots["kb"])
            with self.assertRaisesRegex(Exception, "chat count mismatch"):
                inventory.collect_inventory(count_drift_contract)


class ProtectedOutputTests(unittest.TestCase):
    def test_production_freeze_rejects_wrong_output_root_before_source_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            with self.assertRaisesRegex(inventory.ContractError, "protected output"):
                inventory.freeze_inventory_contract(
                    contract_path=base / "inventory-contract.json",
                    output_path=base / "assets.jsonl",
                    metadata_path=base / "assets.metadata.json",
                    claude_home=base / "missing-home",
                    roots={},
                    domain_decisions=base / "domain-decisions.json",
                    production=True,
                )

    def test_production_freeze_rejects_each_existing_final_before_source_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = {
                "contract": root / "inventory-contract.json",
                "manifest": root / "assets.jsonl",
                "metadata": root / "assets.metadata.json",
            }
            protection = {
                "path": root.as_posix(),
                "root_identity": "00000001:0000000000000002",
                "owner_sid": "S-1-5-21-1001",
                "acl_sha256": "0" * 64,
                "efs": True,
            }
            for label, existing in paths.items():
                with self.subTest(label=label):
                    write_file(existing, b"pre-existing")
                    try:
                        with mock.patch.object(inventory, "PRODUCTION_OUTPUT_ROOT", root), mock.patch.object(
                            inventory, "_verify_protected_root", return_value=protection
                        ):
                            observed_error: Exception | None = None
                            try:
                                inventory.freeze_inventory_contract(
                                    contract_path=paths["contract"],
                                    output_path=paths["manifest"],
                                    metadata_path=paths["metadata"],
                                    claude_home=root / "missing-home",
                                    roots={},
                                    domain_decisions=root / "domain-decisions.json",
                                    production=True,
                                )
                            except Exception as error:
                                observed_error = error
                            self.assertIsInstance(observed_error, FileExistsError)
                            self.assertRegex(str(observed_error), "output|exists")
                    finally:
                        existing.unlink(missing_ok=True)

    def test_protected_root_verifier_rejects_invalid_efs_acl_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            verifier = getattr(inventory, "_verify_protected_root", lambda *_args, **_kwargs: None)
            invalid_receipt = {
                "Mode": "VerifyTree",
                "Path": root.as_posix(),
                "OwnerSid": "S-1-5-21-1001",
                "Access": [],
                "Encrypted": False,
                "FileCount": 5,
                "DirectoryCount": 0,
                "RootIdentity": "1:2",
                "TreeSnapshotHash": "0" * 64,
            }

            observed_executable: list[str] = []

            def runner(arguments, **_kwargs):
                observed_executable.append(arguments[0])
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=json.dumps(invalid_receipt), stderr=""
                )

            with mock.patch.object(inventory, "PRODUCTION_OUTPUT_ROOT", root, create=True):
                with self.assertRaisesRegex(inventory.ContractError, "EFS|ACL|protected"):
                    verifier(root, runner=runner)
            self.assertEqual(
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                observed_executable[0],
            )

    def test_protected_root_verifier_rejects_valid_shape_wrong_owner_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            fake_owner = "S-1-5-21-999999999-999999999-999999999-9999"
            receipt = {
                "Mode": "VerifyTree",
                "Path": root.as_posix(),
                "OwnerSid": fake_owner,
                "Access": [
                    {
                        "Sid": "S-1-5-18",
                        "Type": "Allow",
                        "Rights": "FullControl",
                        "IsInherited": False,
                        "InheritanceFlags": "ContainerInherit, ObjectInherit",
                        "PropagationFlags": "None",
                    },
                    {
                        "Sid": fake_owner,
                        "Type": "Allow",
                        "Rights": "FullControl",
                        "IsInherited": False,
                        "InheritanceFlags": "ContainerInherit, ObjectInherit",
                        "PropagationFlags": "None",
                    },
                ],
                "Encrypted": True,
                "FileCount": 5,
                "DirectoryCount": 0,
                "RootIdentity": "FFFFFFFF:FFFFFFFFFFFFFFFF",
                "TreeSnapshotHash": "0" * 64,
            }

            def runner(*_args, **_kwargs):
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=json.dumps(receipt), stderr=""
                )

            with mock.patch.object(inventory, "PRODUCTION_OUTPUT_ROOT", root):
                with self.assertRaisesRegex(inventory.ContractError, "owner|identity|protected"):
                    inventory._verify_protected_root(root, runner=runner)

    def test_tool_binding_includes_the_committed_root_verifier(self) -> None:
        repository = Path(inventory.__file__).resolve().parents[3]
        binding = inventory._tool_binding(repository, production=False)
        self.assertEqual(
            {
                "inventory.py",
                "model.py",
                "secure_backup_root.ps1",
            },
            {Path(item["path"]).name for item in binding["files"]},
        )

    def test_tool_file_binding_rejects_working_bytes_different_from_head(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory).resolve() / "repo"
            repository.mkdir()
            run_git(repository, "init", "-q")
            run_git(repository, "config", "user.email", "inventory@example.invalid")
            run_git(repository, "config", "user.name", "Inventory Test")
            verifier = write_file(
                repository / "scripts" / "codex" / "import" / "secure_backup_root.ps1",
                b"committed verifier",
            )
            run_git(repository, "add", verifier.relative_to(repository).as_posix())
            run_git(repository, "commit", "-q", "-m", "verifier")
            commit = run_git(repository, "rev-parse", "HEAD").strip()
            verifier.write_bytes(b"drifted verifier")
            binder = getattr(inventory, "_tool_file_binding", lambda *_args, **_kwargs: None)

            with self.assertRaisesRegex(inventory.ContractError, "HEAD|worktree|tool"):
                binder(repository, verifier, commit=commit, production=True)


class DecisionAndClassificationTests(unittest.TestCase):
    def test_changed_chat_request_invalidates_the_old_domain_decision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            projects = root / "projects"
            session_id = str(uuid.uuid4())
            chat = projects / "C--Users-fixture--claude" / f"{session_id}.jsonl"
            old_request = "cooking notes"
            new_request = "different unrelated notes"
            decision_file = write_file(
                root / "decisions.json",
                json.dumps(
                    {
                        "decisions": {
                            session_id: {
                                "subject_sha256": hashlib.sha256(old_request.encode()).hexdigest(),
                                "disposition": "exclude-domain",
                                "reason": "manual-domain-review",
                                "evidence": "TASK-571-stale-review",
                            }
                        }
                    }
                ).encode(),
            )
            write_file(
                chat,
                (
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": "C:/Users/fixture",
                            "message": {"role": "user", "content": new_request},
                        }
                    )
                    + "\n"
                ).encode(),
            )

            records = inventory_claude_chats(
                projects,
                cutoff=None,
                decisions=inventory._load_decisions(decision_file),
            )

            self.assertEqual(1, len(records))
            self.assertIsNone(records[0].disposition)
            self.assertEqual("unresolved", records[0].disposition_status)
            self.assertEqual(
                hashlib.sha256(new_request.encode()).hexdigest(),
                records[0].first_user_request_sha256,
            )

    def test_controlled_chat_decision_records_reason_evidence_and_request_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            projects = base / "projects"
            session_id = str(uuid.uuid4())
            message = "cooking notes"
            chat = write_file(
                projects / "C--Users-fixture" / f"{session_id}.jsonl",
                (
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": "C:/Users/fixture",
                            "message": {"role": "user", "content": message},
                        }
                    )
                    + "\n"
                ).encode(),
            )
            decision_file = base / "domain-decisions.json"
            decision_file.write_text(
                json.dumps(
                    {
                        "decisions": {
                            session_id: {
                                "subject_sha256": hashlib.sha256(message.encode()).hexdigest(),
                                "disposition": "exclude-domain",
                                "reason": "manual-domain-review",
                                "evidence": "TASK-571-fixture-review",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            decisions = inventory._load_decisions(decision_file)

            record = inventory.inventory_claude_chats(
                projects, cutoff=None, decisions=decisions
            )[0]

            self.assertEqual(chat.as_posix(), record.source)
            self.assertEqual("exclude-domain", record.disposition)
            self.assertEqual("domain-decided", record.disposition_status)
            self.assertEqual("manual-domain-review", record.domain_reason)
            self.assertEqual("TASK-571-fixture-review", record.decision_evidence)
            self.assertEqual(
                hashlib.sha256(message.encode()).hexdigest(),
                record.first_user_request_sha256,
            )
            self.assertEqual("C:/Users/fixture", record.source_cwd)
            decisions.assert_all_used()

    def test_unresolved_manifest_summarize_is_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            chat = write_file(
                root / "projects" / "home" / f"{uuid.uuid4()}.jsonl",
                b'{"type":"user","message":{"role":"user","content":"cooking"}}\n',
            )
            record = inventory_paths([chat], cutoff=None)[0]
            manifest = make_test_manifest([record])
            output = root / "unresolved.jsonl"
            inventory._write_new(output, inventory.manifest_to_jsonl(manifest))

            self.assertEqual(1, inventory.summarize_manifest(output)["unresolved"])

    def test_collect_does_not_publish_an_unresolved_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            chat = write_file(
                root / "projects" / "home" / f"{uuid.uuid4()}.jsonl",
                b'{"type":"user","message":{"role":"user","content":"cooking"}}\n',
            )
            manifest = make_test_manifest([inventory_paths([chat], cutoff=None)[0]])
            output = root / "must-not-exist.jsonl"
            args = inventory.build_parser().parse_args(
                [
                    "collect",
                    "--output",
                    os.fspath(output),
                    "--contract",
                    os.fspath(root / "contract.json"),
                    "--contract-sha256",
                    "0" * 64,
                ]
            )

            with mock.patch.object(
                inventory,
                "collect_frozen_inventory",
                side_effect=inventory.ContractError("unresolved-domain"),
            ):
                with self.assertRaisesRegex(inventory.ContractError, "unresolved"):
                    inventory._collect(args)

            self.assertFalse(output.exists())

    def test_user_settings_and_skills_are_explicit_bulk_import_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve() / ".claude"
            write_file(home / "settings.json")
            write_file(home.parent / ".claude.json")
            write_file(home / "skills" / "cooking" / "SKILL.md")

            settings = inventory.inventory_claude_settings(home)
            skill = inventory.inventory_claude_skills(home)[0]

            self.assertEqual({"settings.json", ".claude.json"}, {Path(item.source).name for item in settings})
            self.assertTrue(all(item.disposition == "import" for item in settings))
            self.assertTrue(all(item.disposition_status == "provisional" for item in settings))
            self.assertTrue(
                all(item.domain_reason == "approved-source:user-setting" for item in settings)
            )
            self.assertEqual("import", skill.disposition)
            self.assertEqual("provisional", skill.disposition_status)
            self.assertEqual("approved-source:user-skills", skill.domain_reason)

    def test_different_claude_skill_mirror_is_explicitly_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            home = base / ".claude"
            home.mkdir()
            repo = create_git_repo(base / "Mercury")
            write_file(repo / ".agents" / "skills" / "review" / "SKILL.md", b"canonical")
            write_file(repo / ".claude" / "skills" / "review" / "SKILL.md", b"old mirror")

            records = inventory.inventory_claude_skills(home, {"mercury": repo})
            mirror = next(record for record in records if record.kind == "skill-mirror")
            self.assertEqual("exclude-domain", mirror.disposition)
            self.assertEqual("domain-decided", mirror.disposition_status)
            self.assertEqual("canonical-agents-skill-present", mirror.decision_evidence)

    def test_divergent_skill_mirror_survives_the_final_merge_with_a_relation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            home = base / ".claude-home"
            repo = base / "Mercury"
            home.mkdir()
            canonical_path = write_file(
                repo / ".agents" / "skills" / "review" / "SKILL.md", b"canonical"
            )
            mirror_path = write_file(
                repo / ".claude" / "skills" / "review" / "SKILL.md", b"divergent"
            )

            merged = inventory._merge_records(
                [inventory.inventory_claude_skills(home, {"mercury": repo})]
            )

            self.assertEqual(2, len(merged))
            canonical = next(item for item in merged if item.source == canonical_path.as_posix())
            mirror = next(item for item in merged if item.source == mirror_path.as_posix())
            self.assertEqual("repo-mercury-skill-mirror", mirror.source_namespace)
            self.assertEqual(".claude/skills/review/SKILL.md", mirror.canonical_key)
            self.assertEqual(canonical.asset_id, mirror.mirror_of)
            self.assertEqual("exclude-domain", mirror.disposition)


class ManifestValidationTests(unittest.TestCase):
    def test_asset_record_rejects_dot_segments_and_repeated_separators(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = inventory_paths(
                [write_file(Path(directory).resolve() / "root" / "note.md")], cutoff=None
            )[0]
            for changes in (
                {"source": record.source.replace("/root/note.md", "/root/../note.md")},
                {"source": record.source.replace("/root/note.md", "//root/note.md")},
                {"canonical_key": "memory/./note.md"},
                {"canonical_key": "memory//note.md"},
            ):
                with self.subTest(changes=changes):
                    with self.assertRaises(ValueError):
                        replace(record, **changes)

    def test_summary_rejects_header_cutoff_and_decision_hash_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            record = inventory_paths(
                [write_file(root / "memory" / "a.md", b"a")], cutoff=None
            )[0]
            baseline = make_test_manifest([record])
            mutations = (
                {"schema_version": 2},
                {"record_type": "asset"},
                {"cutoff": "not-a-timestamp"},
                {"domain_decisions_sha256": "not-a-hash"},
            )
            for index, mutation in enumerate(mutations):
                metadata = dict(baseline.metadata)
                metadata.update(mutation)
                path = root / f"tampered-{index}.jsonl"
                inventory._write_new(
                    path,
                    inventory.manifest_to_jsonl(
                        model.InventoryManifest(metadata, baseline.records)
                    ),
                )
                with self.subTest(mutation=mutation):
                    with self.assertRaises(inventory.ManifestError):
                        inventory.summarize_manifest(path)

    def test_summary_rejects_duplicates_wrong_order_and_expected_count_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            records = inventory_paths(
                [
                    write_file(root / "memory" / "a.md", b"a"),
                    write_file(root / "memory" / "z.md", b"z"),
                ],
                cutoff=None,
            )

            duplicate_manifest = make_test_manifest([records[0], records[0]])
            duplicate_path = root / "duplicate.jsonl"
            duplicate_path.parent.mkdir(parents=True, exist_ok=True)
            inventory._write_new(
                duplicate_path, inventory.manifest_to_jsonl(duplicate_manifest)
            )
            with self.assertRaisesRegex(Exception, "duplicate source"):
                inventory.summarize_manifest(duplicate_path)

            ordered_manifest = make_test_manifest(records)
            reversed_records = tuple(reversed(ordered_manifest.records))
            reversed_payload = records_to_jsonl(reversed_records)
            reversed_metadata = dict(ordered_manifest.metadata)
            reversed_metadata["records_sha256"] = hashlib.sha256(
                reversed_payload.encode()
            ).hexdigest()
            reversed_manifest = model.InventoryManifest(
                reversed_metadata, reversed_records
            )
            reversed_path = root / "reversed.jsonl"
            inventory._write_new(
                reversed_path, inventory.manifest_to_jsonl(reversed_manifest)
            )
            with self.assertRaisesRegex(Exception, "canonical order"):
                inventory.summarize_manifest(reversed_path)

            drift_metadata = dict(ordered_manifest.metadata)
            drift_metadata["expected_counts"] = {"memory": 3}
            drift_manifest = model.InventoryManifest(
                drift_metadata, ordered_manifest.records
            )
            drift_path = root / "drift.jsonl"
            inventory._write_new(drift_path, inventory.manifest_to_jsonl(drift_manifest))
            with self.assertRaisesRegex(Exception, "count mismatch"):
                inventory.summarize_manifest(drift_path)

    def test_summary_rejects_invalid_root_identity_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "Mercury"
            record = inventory_paths(
                [write_file(root / "memory" / "note.md")], cutoff=None
            )[0]
            manifest = make_test_manifest([record])
            invalid_metadata = dict(manifest.metadata)
            invalid_metadata["roots"] = dict(invalid_metadata["roots"])
            invalid_metadata["roots"]["mercury"] = {
                "path": "relative/root",
                "file_id": "",
            }
            invalid = model.InventoryManifest(invalid_metadata, manifest.records)
            output = root / "invalid-root.jsonl"
            output.parent.mkdir(parents=True, exist_ok=True)
            inventory._write_new(output, inventory.manifest_to_jsonl(invalid))
            with self.assertRaisesRegex(Exception, "root identity"):
                inventory.summarize_manifest(output)


class AtomicAndGitEdgeTests(unittest.TestCase):
    def test_fsync_failure_and_existing_output_are_fail_preserving(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            failed = root / "failed.jsonl"
            with mock.patch.object(inventory.os, "fsync", side_effect=OSError("fixture")):
                with self.assertRaises(OSError):
                    inventory._write_new(failed, "payload")
            self.assertFalse(failed.exists())
            self.assertEqual([], list(root.glob(f".{failed.name}.*.tmp")))

            existing = root / "existing.jsonl"
            existing.write_bytes(b"original")
            with self.assertRaises(FileExistsError):
                inventory._write_new(existing, "replacement")
            self.assertEqual(b"original", existing.read_bytes())

    def test_snapshot_retries_once_then_binds_final_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = write_file(Path(directory).resolve() / "source.bin", b"before")

            def mutate_once(_path: Path, attempt: int) -> None:
                if attempt == 0:
                    path.write_bytes(b"after")

            snapshot = inventory._snapshot_file(
                path, retries=1, after_read=mutate_once
            )
            self.assertEqual(hashlib.sha256(b"after").hexdigest(), snapshot.sha256)
            self.assertEqual(5, snapshot.size)

    def test_git_rename_spaces_untracked_and_gitlink_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            repo = create_git_repo(base / "repo")
            old = write_file(repo / "old name.txt", b"old")
            run_git(repo, "add", "old name.txt")
            run_git(repo, "commit", "-q", "-m", "space fixture")
            new = repo / "new name.txt"
            old.rename(new)
            run_git(repo, "add", "-A")
            untracked = write_file(repo / "untracked name.txt", b"new")

            records = inventory_repo_dirty(repo, cutoff=None)
            states = {record.source: record.dirty_state for record in records}
            self.assertEqual("renamed", states[new.as_posix()])
            self.assertEqual("untracked", states[untracked.as_posix()])

            child = base / "child"
            child.mkdir()
            run_git(child, "init", "-q")
            run_git(child, "config", "user.email", "inventory@example.invalid")
            run_git(child, "config", "user.name", "Inventory Test")
            write_file(child / "child.txt", b"before")
            run_git(child, "add", "child.txt")
            run_git(child, "commit", "-q", "-m", "child")
            subprocess.run(
                [
                    "git",
                    "-c",
                    "protocol.file.allow=always",
                    "-C",
                    os.fspath(repo),
                    "submodule",
                    "add",
                    "-q",
                    os.fspath(child),
                    "vendor",
                ],
                check=True,
                capture_output=True,
            )
            run_git(repo, "add", ".gitmodules", "vendor")
            run_git(repo, "commit", "-q", "-m", "gitlink")
            (repo / "vendor" / "child.txt").write_bytes(b"after")
            with self.assertRaisesRegex(Exception, "gitlink/submodule"):
                inventory_repo_dirty(repo, cutoff=None)
