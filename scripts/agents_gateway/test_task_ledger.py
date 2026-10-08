from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from task_ledger import TaskLedger, TaskLedgerError


class MutableClock:
    def __init__(self, value: float = 1_900_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def digest(character: str) -> str:
    return character * 64


class TaskLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "task-ledger.sqlite3"
        self.policy_path = self.root / "task-ledger-policy.json"
        self.clock = MutableClock()
        self.identity = {
            "pairing_id": "pairing-623",
            "key_id": "sha256:" + digest("a"),
            "client_id": "client-623",
            "sender_principal": "codex-sender",
            "receiver_principal": "dot-receiver",
            "recipient_thread_id": "dot-thread-623",
            "protocol_sha256": digest("b"),
            "max_ttl_seconds": 3600,
        }
        self.policy = TaskLedger.initialize(
            self.db, self.policy_path, self.identity, activate=True
        )
        self.generation = self.policy["generation"]
        self.ledger = TaskLedger(self.db, self.policy_path, self.clock)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def metadata(self, **changes: object) -> dict[str, object]:
        expires = dt.datetime.fromtimestamp(
            self.clock() + 600, tz=dt.timezone.utc
        ).replace(microsecond=0)
        result: dict[str, object] = {
            "generation": self.generation,
            "pairing_id": self.identity["pairing_id"],
            "key_id": self.identity["key_id"],
            "client_id": self.identity["client_id"],
            "recipient_thread_id": self.identity["recipient_thread_id"],
            "task_id": "task-623",
            "request_id": "request-623-1",
            "attempt": 1,
            "nonce": "nonce-623-1",
            "body_sha256": digest("c"),
            "expires_at": expires.isoformat().replace("+00:00", "Z"),
            "protocol_sha256": self.identity["protocol_sha256"],
        }
        result.update(changes)
        return result

    def assert_error(self, code: str, callback) -> None:
        with self.assertRaises(TaskLedgerError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def admit_one(self) -> dict[str, object]:
        return self.ledger.admit("codex-sender", self.metadata())

    def claim_one(self) -> dict[str, object]:
        return self.ledger.claim(
            "dot-receiver", "request-623-1", digest("c"), self.generation
        )

    def test_restart_duplicate_claim_and_status_readback(self) -> None:
        first = self.admit_one()
        self.assertEqual(first["state"], "admitted")
        self.assertFalse(first["duplicate"])
        self.assertFalse(first["start_granted"])
        self.assertEqual(first["execution_start_count"], 0)

        claimed = self.claim_one()
        self.assertEqual(claimed["state"], "running")
        self.assertTrue(claimed["start_granted"])
        self.assertEqual(claimed["execution_start_count"], 1)
        self.assertFalse(claimed["human_authorization_checked"])

        restarted = TaskLedger(self.db, self.policy_path, self.clock)
        duplicate_admit = restarted.admit("codex-sender", self.metadata())
        self.assertTrue(duplicate_admit["duplicate"])
        self.assertEqual(duplicate_admit["state"], "running")
        duplicate_claim = restarted.claim(
            "dot-receiver", "request-623-1", digest("c"), self.generation
        )
        self.assertTrue(duplicate_claim["duplicate"])
        self.assertFalse(duplicate_claim["start_granted"])
        self.assertEqual(duplicate_claim["execution_start_count"], 1)
        self.assertEqual(
            restarted.status("codex-sender", "request-623-1")["state"], "running"
        )
        public_status = restarted.status("dot-receiver")
        self.assertEqual(public_status["pairing_id"], self.identity["pairing_id"])
        self.assertEqual(public_status["key_id"], self.identity["key_id"])
        self.assertEqual(public_status["client_id"], self.identity["client_id"])
        self.assertEqual(
            public_status["recipient_thread_id"], self.identity["recipient_thread_id"]
        )
        self.assertFalse(public_status["human_authorization_checked"])

    def test_concurrent_claim_grants_exactly_one_start(self) -> None:
        self.admit_one()
        other = TaskLedger(self.db, self.policy_path, self.clock)
        gate = threading.Barrier(3)
        results: list[dict[str, object] | TaskLedgerError] = []
        result_lock = threading.Lock()

        def run(ledger: TaskLedger) -> None:
            gate.wait()
            try:
                result: dict[str, object] | TaskLedgerError = ledger.claim(
                    "dot-receiver", "request-623-1", digest("c"), self.generation
                )
            except TaskLedgerError as exc:
                result = exc
            with result_lock:
                results.append(result)

        workers = [
            threading.Thread(target=run, args=(self.ledger,)),
            threading.Thread(target=run, args=(other,)),
        ]
        for worker in workers:
            worker.start()
        gate.wait()
        for worker in workers:
            worker.join(timeout=10)
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(isinstance(result, dict) for result in results))
        self.assertEqual(sum(bool(result["start_granted"]) for result in results), 1)
        self.assertEqual(sum(int(result["execution_start_count"]) for result in results), 2)

    def test_request_nonce_and_task_attempt_conflicts(self) -> None:
        self.admit_one()
        exact = self.ledger.admit("codex-sender", self.metadata())
        self.assertTrue(exact["duplicate"])

        self.assert_error(
            "request_conflict",
            lambda: self.ledger.admit(
                "codex-sender", self.metadata(body_sha256=digest("d"), nonce="nonce-other")
            ),
        )
        self.assert_error(
            "nonce_conflict",
            lambda: self.ledger.admit(
                "codex-sender",
                self.metadata(request_id="request-new", nonce="nonce-623-1"),
            ),
        )
        self.assert_error(
            "attempt_conflict",
            lambda: self.ledger.admit(
                "codex-sender",
                self.metadata(request_id="request-new", nonce="nonce-new"),
            ),
        )
        self.assert_error(
            "task_unresolved",
            lambda: self.ledger.admit(
                "codex-sender",
                self.metadata(request_id="request-new", nonce="nonce-new", attempt=2),
            ),
        )

        self.claim_one()
        self.ledger.transition(
            "dot-receiver", "request-623-1", digest("c"), self.generation,
            "running", "failed", digest("e"),
        )
        next_attempt = self.ledger.admit(
            "codex-sender",
            self.metadata(request_id="request-new", nonce="nonce-new", attempt=2),
        )
        self.assertEqual(next_attempt["state"], "admitted")

        self.ledger.claim(
            "dot-receiver", "request-new", digest("c"), self.generation
        )
        self.ledger.transition(
            "dot-receiver", "request-new", digest("c"), self.generation,
            "running", "blocked", digest("f"),
        )
        self.assert_error(
            "task_unresolved",
            lambda: self.ledger.admit(
                "codex-sender",
                self.metadata(
                    request_id="request-third",
                    nonce="nonce-third",
                    attempt=3,
                ),
            ),
        )

    def test_urlsafe_nonce_accepts_leading_underscore_and_hyphen(self) -> None:
        underscore = self.ledger.admit(
            "codex-sender",
            self.metadata(request_id="request-underscore", nonce="_nonce"),
        )
        hyphen = self.ledger.admit(
            "codex-sender",
            self.metadata(
                task_id="task-urlsafe",
                request_id="request-hyphen",
                attempt=1,
                nonce="-nonce",
            ),
        )
        self.assertEqual(underscore["state"], "admitted")
        self.assertEqual(hyphen["state"], "admitted")
        self.assert_error(
            "metadata_invalid",
            lambda: self.ledger.admit(
                "codex-sender",
                self.metadata(
                    task_id="task-invalid-nonce",
                    request_id="request-invalid-nonce",
                    nonce="=invalid",
                ),
            ),
        )

    def test_wrong_principal_route_generation_and_ttl_fail_closed(self) -> None:
        self.assert_error("principal_forbidden", lambda: self.ledger.admit("dot-receiver", self.metadata()))
        self.assert_error("principal_forbidden", lambda: self.ledger.status("unrelated"))
        self.assert_error(
            "metadata_policy_mismatch",
            lambda: self.ledger.admit("codex-sender", self.metadata(recipient_thread_id="other-thread")),
        )
        self.assert_error(
            "metadata_policy_mismatch",
            lambda: self.ledger.admit("codex-sender", self.metadata(generation=str(uuid.uuid4()))),
        )
        self.assert_error(
            "metadata_policy_mismatch",
            lambda: self.ledger.admit("codex-sender", self.metadata(pairing_id="other-pair")),
        )
        self.assert_error(
            "request_ttl_exceeded",
            lambda: self.ledger.admit(
                "codex-sender",
                self.metadata(
                    expires_at=dt.datetime.fromtimestamp(
                        self.clock() + 3601, tz=dt.timezone.utc
                    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                ),
            ),
        )
        expired = dt.datetime.fromtimestamp(
            self.clock() - 1, tz=dt.timezone.utc
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        self.assert_error(
            "request_expired",
            lambda: self.ledger.admit("codex-sender", self.metadata(expires_at=expired)),
        )
        self.assert_error(
            "metadata_invalid",
            lambda: self.ledger.admit(
                "codex-sender", self.metadata(attempt=2**63)
            ),
        )
        self.admit_one()
        self.assert_error(
            "principal_forbidden",
            lambda: self.ledger.claim("codex-sender", "request-623-1", digest("c"), self.generation),
        )
        self.assert_error(
            "generation_mismatch",
            lambda: self.ledger.claim("dot-receiver", "request-623-1", digest("c"), str(uuid.uuid4())),
        )
        self.assert_error(
            "request_binding_mismatch",
            lambda: self.ledger.claim("dot-receiver", "request-623-1", digest("d"), self.generation),
        )
        self.clock.advance(601)
        self.assert_error(
            "request_expired", self.claim_one
        )

    def test_transition_permissions_idempotency_and_order(self) -> None:
        self.admit_one()
        self.claim_one()
        self.assert_error(
            "principal_forbidden",
            lambda: self.ledger.transition(
                "codex-sender", "request-623-1", digest("c"), self.generation,
                "running", "delivered", digest("e"), "page-623",
            ),
        )
        delivered = self.ledger.transition(
            "dot-receiver", "request-623-1", digest("c"), self.generation,
            "running", "delivered", digest("e"), "page-623",
        )
        self.assertEqual(delivered["state"], "delivered")
        replay = self.ledger.transition(
            "dot-receiver", "request-623-1", digest("c"), self.generation,
            "running", "delivered", digest("e"), "page-623",
        )
        self.assertTrue(replay["duplicate"])
        self.assert_error(
            "state_conflict",
            lambda: self.ledger.transition(
                "dot-receiver", "request-623-1", digest("c"), self.generation,
                "running", "delivered", digest("f"), "page-623",
            ),
        )
        verified = self.ledger.transition(
            "codex-sender", "request-623-1", digest("c"), self.generation,
            "delivered", "readback_verified", digest("a"), "page-623",
        )
        self.assertEqual(verified["state"], "readback_verified")
        closed = self.ledger.transition(
            "dot-receiver", "request-623-1", digest("c"), self.generation,
            "readback_verified", "closed", digest("b"), "page-623",
        )
        self.assertEqual(closed["state"], "closed")
        self.assert_error(
            "transition_forbidden",
            lambda: self.ledger.transition(
                "codex-sender", "request-623-1", digest("c"), self.generation,
                "closed", "running", digest("d"),
            ),
        )

    def test_missing_empty_corrupt_and_mismatched_storage_fail_closed(self) -> None:
        missing = TaskLedger(self.root / "missing.sqlite3", self.policy_path, self.clock)
        self.assert_error("storage_unavailable", missing.policy)

        empty = self.root / "empty.sqlite3"
        empty.touch()
        self.assert_error("storage_schema_invalid", lambda: TaskLedger(empty, self.policy_path).policy())

        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not an sqlite database")
        self.assert_error("storage_corrupt", lambda: TaskLedger(corrupt, self.policy_path).policy())

        connection = sqlite3.connect(self.db)
        connection.execute("DROP INDEX task_records_by_task_attempt")
        connection.commit()
        connection.close()
        self.assert_error("storage_marker_mismatch", self.ledger.policy)

        marker_db = self.root / "marker.sqlite3"
        marker_policy = self.root / "marker-policy.json"
        TaskLedger.initialize(marker_db, marker_policy, self.identity, activate=True)
        connection = sqlite3.connect(marker_db)
        connection.execute(
            "UPDATE ledger_meta SET value=? WHERE key='generation'", (str(uuid.uuid4()),)
        )
        connection.commit()
        connection.close()
        self.assert_error("storage_marker_mismatch", lambda: TaskLedger(marker_db, marker_policy).policy())

    def test_policy_binding_and_inactive_generation(self) -> None:
        changed = json.loads(self.policy_path.read_text(encoding="utf-8"))
        changed["key_id"] = "sha256:" + digest("f")
        alternate_policy = self.root / "alternate-policy.json"
        alternate_policy.write_text(json.dumps(changed), encoding="utf-8")
        self.assert_error(
            "storage_marker_mismatch",
            lambda: TaskLedger(self.db, alternate_policy).policy(),
        )

        inactive_db = self.root / "inactive.sqlite3"
        inactive_policy = self.root / "inactive-policy.json"
        inactive = TaskLedger.initialize(
            inactive_db, inactive_policy, self.identity, activate=False
        )
        self.assertFalse(inactive["activated"])
        self.assert_error(
            "ledger_inactive",
            lambda: TaskLedger(inactive_db, inactive_policy, self.clock).admit(
                "codex-sender",
                {**self.metadata(), "generation": inactive["generation"]},
            ),
        )
        self.assertFalse(TaskLedger(inactive_db, inactive_policy).status("dot-receiver")["activated"])

    def test_initialize_is_exclusive_and_backup_is_archive_only(self) -> None:
        self.assert_error(
            "destination_exists",
            lambda: TaskLedger.initialize(self.db, self.policy_path, self.identity, activate=True),
        )
        self.admit_one()
        backup = self.root / "backup.sqlite3"
        result = self.ledger.backup_to(backup)
        self.assertEqual(result["status"], "backed_up")
        archived = TaskLedger(backup, self.policy_path, self.clock)
        status = archived.status("dot-receiver", "request-623-1")
        self.assertEqual(status["state"], "admitted")
        self.assertTrue(status["archive_only"])
        self.assert_error(
            "ledger_archive_only",
            lambda: archived.claim("dot-receiver", "request-623-1", digest("c"), self.generation),
        )
        original = self.db.read_bytes()
        backup_before = backup.read_bytes()
        self.assert_error("destination_exists", lambda: self.ledger.backup_to(backup))
        self.assertEqual(self.db.read_bytes(), original)
        self.assertEqual(backup.read_bytes(), backup_before)

    def test_initialize_cli_creates_one_generation_without_overwrite(self) -> None:
        cli_db = self.root / "cli-ledger.sqlite3"
        cli_policy = self.root / "cli-policy.json"
        identity_json = self.root / "identity.json"
        identity_json.write_text(json.dumps(self.identity), encoding="utf-8")
        command = [
            sys.executable,
            "-B",
            str(HERE / "task_ledger.py"),
            "init",
            "--db",
            str(cli_db),
            "--policy",
            str(cli_policy),
            "--identity-json",
            str(identity_json),
            "--activate",
        ]
        created = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(created.returncode, 0, created.stderr)
        result = json.loads(created.stdout)
        self.assertTrue(result["activated"])
        self.assertTrue(uuid.UUID(result["generation"]))
        database_before = cli_db.read_bytes()
        policy_before = cli_policy.read_bytes()
        repeated = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(repeated.returncode, 2)
        self.assertIn("destination_exists", repeated.stderr)
        self.assertEqual(cli_db.read_bytes(), database_before)
        self.assertEqual(cli_policy.read_bytes(), policy_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
