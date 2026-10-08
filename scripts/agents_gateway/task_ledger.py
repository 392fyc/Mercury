#!/usr/bin/env python3
"""Persistent, fail-closed receipt ledger for dot-link task admissions.

The controlled sender exports metadata only after ``dot_link.py`` verifies the
admission. The REST gateway binds that export to the fixed sender principal and
policy but does not verify signatures. The receiver independently verifies the
exact message and its fixed pin, then obtains human authorization before
calling ``claim``. Ledger states are records, not authorization, and this
module neither authorizes task content nor executes tasks.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 1
MAX_POLICY_BYTES = 64 * 1024
POLICY_FIELDS = {
    "schema_version",
    "generation",
    "pairing_id",
    "key_id",
    "client_id",
    "sender_principal",
    "receiver_principal",
    "recipient_thread_id",
    "protocol_sha256",
    "max_ttl_seconds",
    "activated",
}
IDENTITY_FIELDS = POLICY_FIELDS - {"schema_version", "generation", "activated"}
METADATA_FIELDS = {
    "generation",
    "pairing_id",
    "key_id",
    "client_id",
    "recipient_thread_id",
    "task_id",
    "request_id",
    "attempt",
    "nonce",
    "body_sha256",
    "expires_at",
    "protocol_sha256",
}
STATES = {
    "admitted",
    "running",
    "delivered",
    "failed",
    "blocked",
    "readback_verified",
    "closed",
}
RETRY_TERMINAL_STATES = {"failed", "closed"}
OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
URLSAFE_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
KEY_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
UTC_DEADLINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)


class TaskLedgerError(Exception):
    """A fixed, safe error code; never embeds caller input or filesystem data."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid_constant")


def _read_json(path: Path, error_code: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            raise TaskLedgerError(error_code)
        raw = path.read_bytes()
    except TaskLedgerError:
        raise
    except OSError as exc:
        raise TaskLedgerError(error_code) from exc
    if len(raw) > MAX_POLICY_BYTES:
        raise TaskLedgerError(error_code)
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise TaskLedgerError(error_code) from exc
    if not isinstance(value, dict):
        raise TaskLedgerError(error_code)
    return value


def _safe_text(value: Any, maximum: int = 256) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and OPAQUE_ID_RE.fullmatch(value) is not None
    )


def _validate_policy(value: dict[str, Any]) -> dict[str, Any]:
    if set(value) != POLICY_FIELDS:
        raise TaskLedgerError("policy_schema_invalid")
    if (
        isinstance(value["schema_version"], bool)
        or not isinstance(value["schema_version"], int)
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise TaskLedgerError("policy_schema_invalid")
    try:
        generation = str(uuid.UUID(value["generation"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise TaskLedgerError("policy_invalid") from exc
    if generation != value["generation"]:
        raise TaskLedgerError("policy_invalid")
    for field in (
        "pairing_id",
        "client_id",
        "sender_principal",
        "receiver_principal",
        "recipient_thread_id",
    ):
        if not _safe_text(value[field]):
            raise TaskLedgerError("policy_invalid")
    if not isinstance(value["key_id"], str) or KEY_ID_RE.fullmatch(value["key_id"]) is None:
        raise TaskLedgerError("policy_invalid")
    if not isinstance(value["protocol_sha256"], str) or SHA256_RE.fullmatch(value["protocol_sha256"]) is None:
        raise TaskLedgerError("policy_invalid")
    maximum = value["max_ttl_seconds"]
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 86400:
        raise TaskLedgerError("policy_invalid")
    if not isinstance(value["activated"], bool):
        raise TaskLedgerError("policy_invalid")
    return value


def _binding_digest(policy: dict[str, Any]) -> str:
    binding = {key: policy[key] for key in IDENTITY_FIELDS}
    canonical = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _schema_digest(connection: sqlite3.Connection) -> str:
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "WHERE type IN ('table','index') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type,name"
    ).fetchall()
    schema = [tuple(row) for row in rows]
    canonical = json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sqlite_uri(path: Path, mode: str) -> str:
    if mode not in {"rw", "ro"}:
        raise TaskLedgerError("storage_unavailable")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise TaskLedgerError("storage_unavailable") from exc
    return resolved.as_uri() + "?mode=" + mode


def _deadline_epoch(value: Any) -> float:
    if not isinstance(value, str) or UTC_DEADLINE_RE.fullmatch(value) is None:
        raise TaskLedgerError("metadata_invalid")
    try:
        deadline = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise TaskLedgerError("metadata_invalid") from exc
    return deadline.timestamp()


class TaskLedger:
    """SQLite-backed task admission state on an existing persistent volume."""

    def __init__(
        self,
        db: str | Path,
        policy: str | Path,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db)
        self.policy_path = Path(policy)
        self.clock = clock

    @classmethod
    def initialize(
        cls,
        db: str | Path,
        policy: str | Path,
        identity: dict[str, Any],
        *,
        activate: bool = False,
    ) -> dict[str, Any]:
        """Create a new generation only when both destination files are absent.

        The identity object contains exactly the fixed fields except
        schema_version, generation, and activated. Initialization is explicit;
        normal TaskLedger construction never creates a database or policy.
        """
        db_path = Path(db)
        policy_path = Path(policy)
        if db_path.resolve(strict=False) == policy_path.resolve(strict=False):
            raise TaskLedgerError("paths_must_differ")
        if not isinstance(identity, dict) or set(identity) != IDENTITY_FIELDS:
            raise TaskLedgerError("identity_schema_invalid")
        if any(path.exists() or path.is_symlink() for path in (db_path, policy_path)):
            raise TaskLedgerError("destination_exists")
        if not db_path.parent.is_dir() or not policy_path.parent.is_dir():
            raise TaskLedgerError("destination_parent_missing")
        policy_value = _validate_policy(
            {
                "schema_version": SCHEMA_VERSION,
                "generation": str(uuid.uuid4()),
                **identity,
                "activated": activate,
            }
        )
        db_created = False
        try:
            descriptor = os.open(
                db_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
                0o600,
            )
            os.close(descriptor)
            db_created = True
            connection = sqlite3.connect(
                _sqlite_uri(db_path, "rw"), uri=True, isolation_level=None, timeout=5
            )
            try:
                connection.execute("PRAGMA busy_timeout = 5000")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = FULL")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE ledger_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE task_records ("
                    "request_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt INTEGER NOT NULL, "
                    "nonce TEXT NOT NULL UNIQUE, generation TEXT NOT NULL, pairing_id TEXT NOT NULL, "
                    "key_id TEXT NOT NULL, client_id TEXT NOT NULL, recipient_thread_id TEXT NOT NULL, "
                    "body_sha256 TEXT NOT NULL, expires_at TEXT NOT NULL, protocol_sha256 TEXT NOT NULL, "
                    "state TEXT NOT NULL CHECK(state IN ("
                    "'admitted','running','delivered','failed','blocked','readback_verified','closed')), "
                    "execution_start_count INTEGER NOT NULL CHECK(execution_start_count IN (0,1)), "
                    "evidence_sha256 TEXT, result_page_id TEXT, created_at REAL NOT NULL, "
                    "updated_at REAL NOT NULL, UNIQUE(task_id, attempt))"
                )
                connection.execute(
                    "CREATE INDEX task_records_by_task_attempt ON task_records(task_id, attempt)"
                )
                markers = {
                    "schema_version": str(SCHEMA_VERSION),
                    "generation": policy_value["generation"],
                    "binding_sha256": _binding_digest(policy_value),
                    "schema_sha256": _schema_digest(connection),
                    "archive_only": "0",
                }
                connection.executemany(
                    "INSERT INTO ledger_meta(key,value) VALUES(?,?)", markers.items()
                )
                connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()
            _write_new_policy(policy_path, policy_value)
        except TaskLedgerError:
            raise
        except FileExistsError as exc:
            raise TaskLedgerError("destination_exists") from exc
        except (OSError, sqlite3.Error) as exc:
            # If database creation partially succeeded, leave it in place. A
            # missing policy or incomplete schema is deliberately non-usable.
            raise TaskLedgerError("initialize_failed") from exc
        ledger = cls(db_path, policy_path)
        return ledger.policy()

    def _load_policy(self) -> dict[str, Any]:
        return _validate_policy(_read_json(self.policy_path, "policy_unavailable"))

    def _connect(self, policy: dict[str, Any]) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            if self.db_path.is_symlink() or not self.db_path.is_file():
                raise TaskLedgerError("storage_unavailable")
            connection = sqlite3.connect(
                _sqlite_uri(self.db_path, "rw"),
                uri=True,
                isolation_level=None,
                timeout=5,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA synchronous = FULL")
            check = connection.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise TaskLedgerError("storage_corrupt")
            self._validate_schema_and_markers(connection, policy)
            return connection
        except TaskLedgerError:
            if connection is not None:
                connection.close()
            raise
        except sqlite3.DatabaseError as exc:
            if connection is not None:
                connection.close()
            raise TaskLedgerError("storage_corrupt") from exc
        except (OSError, sqlite3.Error) as exc:
            if connection is not None:
                connection.close()
            raise TaskLedgerError("storage_unavailable") from exc

    @staticmethod
    def _validate_schema_and_markers(
        connection: sqlite3.Connection, policy: dict[str, Any]
    ) -> dict[str, str]:
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if not {"ledger_meta", "task_records"} <= tables:
                raise TaskLedgerError("storage_schema_invalid")
            meta_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(ledger_meta)")
            }
            task_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(task_records)")
            }
            if meta_columns != {"key", "value"} or task_columns != {
                "request_id", "task_id", "attempt", "nonce", "generation", "pairing_id",
                "key_id", "client_id", "recipient_thread_id", "body_sha256", "expires_at",
                "protocol_sha256", "state", "execution_start_count", "evidence_sha256",
                "result_page_id", "created_at", "updated_at",
            }:
                raise TaskLedgerError("storage_schema_invalid")
            markers = {
                row["key"]: row["value"]
                for row in connection.execute("SELECT key,value FROM ledger_meta").fetchall()
            }
        except sqlite3.Error as exc:
            raise TaskLedgerError("storage_schema_invalid") from exc
        expected = {
            "schema_version": str(SCHEMA_VERSION),
            "generation": policy["generation"],
            "binding_sha256": _binding_digest(policy),
            "schema_sha256": _schema_digest(connection),
        }
        if any(markers.get(key) != value for key, value in expected.items()):
            raise TaskLedgerError("storage_marker_mismatch")
        if markers.get("archive_only") not in {"0", "1"}:
            raise TaskLedgerError("storage_marker_mismatch")
        return markers

    def _open(self) -> tuple[dict[str, Any], sqlite3.Connection, dict[str, str]]:
        policy = self._load_policy()
        connection = self._connect(policy)
        try:
            markers = self._validate_schema_and_markers(connection, policy)
        except Exception:
            connection.close()
            raise
        return policy, connection, markers

    @staticmethod
    def _validate_principal(policy: dict[str, Any], principal: str, role: str) -> None:
        expected = policy[role + "_principal"]
        if principal != expected:
            raise TaskLedgerError("principal_forbidden")

    @staticmethod
    def _require_active(policy: dict[str, Any], markers: dict[str, str]) -> None:
        if not policy["activated"]:
            raise TaskLedgerError("ledger_inactive")
        if markers["archive_only"] == "1":
            raise TaskLedgerError("ledger_archive_only")

    @staticmethod
    def _validate_metadata(policy: dict[str, Any], metadata: Any, now: float) -> dict[str, Any]:
        if not isinstance(metadata, dict) or set(metadata) != METADATA_FIELDS:
            raise TaskLedgerError("metadata_schema_invalid")
        for field in ("generation", "pairing_id", "key_id", "client_id", "recipient_thread_id", "protocol_sha256"):
            if metadata[field] != policy[field]:
                raise TaskLedgerError("metadata_policy_mismatch")
        for field in ("task_id", "request_id"):
            if not _safe_text(metadata[field]):
                raise TaskLedgerError("metadata_invalid")
        nonce = metadata["nonce"]
        if not isinstance(nonce, str) or URLSAFE_NONCE_RE.fullmatch(nonce) is None:
            raise TaskLedgerError("metadata_invalid")
        if not isinstance(metadata["body_sha256"], str) or SHA256_RE.fullmatch(metadata["body_sha256"]) is None:
            raise TaskLedgerError("metadata_invalid")
        attempt = metadata["attempt"]
        if (
            isinstance(attempt, bool)
            or not isinstance(attempt, int)
            or not 0 <= attempt <= (2**63 - 1)
        ):
            raise TaskLedgerError("metadata_invalid")
        expiry = _deadline_epoch(metadata["expires_at"])
        ttl = expiry - now
        if ttl <= 0:
            raise TaskLedgerError("request_expired")
        if ttl > policy["max_ttl_seconds"]:
            raise TaskLedgerError("request_ttl_exceeded")
        return dict(metadata)

    @staticmethod
    def _metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {field: row[field] for field in METADATA_FIELDS}

    @classmethod
    def _record(
        cls,
        row: sqlite3.Row,
        *,
        duplicate: bool,
        start_granted: bool = False,
        archive_only: bool = False,
    ) -> dict[str, Any]:
        return {
            "metadata": cls._metadata_from_row(row),
            "state": row["state"],
            "execution_start_count": row["execution_start_count"],
            "evidence_sha256": row["evidence_sha256"],
            "result_page_id": row["result_page_id"],
            "duplicate": duplicate,
            "start_granted": start_granted,
            "human_authorization_checked": False,
            "archive_only": archive_only,
        }

    def policy(self) -> dict[str, Any]:
        policy, connection, markers = self._open()
        connection.close()
        return {
            "schema_version": policy["schema_version"],
            "generation": policy["generation"],
            "protocol_sha256": policy["protocol_sha256"],
            "activated": policy["activated"],
            "archive_only": markers["archive_only"] == "1",
            "human_authorization_checked": False,
        }

    def status(self, principal: str, request_id: str | None = None) -> dict[str, Any]:
        policy, connection, markers = self._open()
        try:
            if principal not in {policy["sender_principal"], policy["receiver_principal"]}:
                raise TaskLedgerError("principal_forbidden")
            if request_id is None:
                return {
                    "schema_version": policy["schema_version"],
                    "generation": policy["generation"],
                    "pairing_id": policy["pairing_id"],
                    "key_id": policy["key_id"],
                    "client_id": policy["client_id"],
                    "recipient_thread_id": policy["recipient_thread_id"],
                    "protocol_sha256": policy["protocol_sha256"],
                    "activated": policy["activated"],
                    "archive_only": markers["archive_only"] == "1",
                    "human_authorization_checked": False,
                }
            if not _safe_text(request_id):
                raise TaskLedgerError("request_id_invalid")
            row = connection.execute(
                "SELECT * FROM task_records WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise TaskLedgerError("request_not_found")
            return self._record(
                row,
                duplicate=False,
                archive_only=markers["archive_only"] == "1",
            )
        finally:
            connection.close()

    def admit(self, principal: str, metadata: dict[str, Any]) -> dict[str, Any]:
        policy, connection, markers = self._open()
        try:
            self._validate_principal(policy, principal, "sender")
            self._require_active(policy, markers)
            connection.execute("BEGIN IMMEDIATE")
            self._validate_schema_and_markers(connection, policy)
            checked = self._validate_metadata(policy, metadata, self.clock())
            prior = connection.execute(
                "SELECT * FROM task_records WHERE request_id=?", (checked["request_id"],)
            ).fetchone()
            if prior is not None:
                if self._metadata_from_row(prior) != checked:
                    raise TaskLedgerError("request_conflict")
                connection.execute("COMMIT")
                return self._record(prior, duplicate=True)
            nonce = connection.execute(
                "SELECT request_id FROM task_records WHERE nonce=?", (checked["nonce"],)
            ).fetchone()
            if nonce is not None:
                raise TaskLedgerError("nonce_conflict")
            latest = connection.execute(
                "SELECT attempt,state FROM task_records WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                (checked["task_id"],),
            ).fetchone()
            if latest is not None:
                same_attempt = connection.execute(
                    "SELECT request_id FROM task_records WHERE task_id=? AND attempt=?",
                    (checked["task_id"], checked["attempt"]),
                ).fetchone()
                if same_attempt is not None:
                    raise TaskLedgerError("attempt_conflict")
                if latest["state"] not in RETRY_TERMINAL_STATES:
                    raise TaskLedgerError("task_unresolved")
                if checked["attempt"] != latest["attempt"] + 1:
                    raise TaskLedgerError("attempt_not_next")
            now = self.clock()
            connection.execute(
                "INSERT INTO task_records("
                "request_id,task_id,attempt,nonce,generation,pairing_id,key_id,client_id,"
                "recipient_thread_id,body_sha256,expires_at,protocol_sha256,state,"
                "execution_start_count,evidence_sha256,result_page_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'admitted',0,NULL,NULL,?,?)",
                (
                    checked["request_id"], checked["task_id"], checked["attempt"], checked["nonce"],
                    checked["generation"], checked["pairing_id"], checked["key_id"], checked["client_id"],
                    checked["recipient_thread_id"], checked["body_sha256"], checked["expires_at"],
                    checked["protocol_sha256"], now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM task_records WHERE request_id=?", (checked["request_id"],)
            ).fetchone()
            connection.execute("COMMIT")
            return self._record(row, duplicate=False)
        except TaskLedgerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.IntegrityError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise TaskLedgerError("metadata_conflict") from exc
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise TaskLedgerError("storage_unavailable") from exc
        finally:
            connection.close()

    def claim(
        self,
        principal: str,
        request_id: str,
        body_sha256: str,
        generation: str,
    ) -> dict[str, Any]:
        policy, connection, markers = self._open()
        try:
            self._validate_principal(policy, principal, "receiver")
            self._require_active(policy, markers)
            if not _safe_text(request_id) or not isinstance(body_sha256, str) or SHA256_RE.fullmatch(body_sha256) is None:
                raise TaskLedgerError("claim_invalid")
            if generation != policy["generation"]:
                raise TaskLedgerError("generation_mismatch")
            connection.execute("BEGIN IMMEDIATE")
            self._validate_schema_and_markers(connection, policy)
            row = connection.execute(
                "SELECT * FROM task_records WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise TaskLedgerError("request_not_found")
            if row["body_sha256"] != body_sha256 or row["generation"] != generation:
                raise TaskLedgerError("request_binding_mismatch")
            if row["state"] == "admitted":
                if _deadline_epoch(row["expires_at"]) <= self.clock():
                    raise TaskLedgerError("request_expired")
                cursor = connection.execute(
                    "UPDATE task_records SET state='running',execution_start_count=1,updated_at=? "
                    "WHERE request_id=? AND state='admitted' AND execution_start_count=0",
                    (self.clock(), request_id),
                )
                if cursor.rowcount != 1:
                    raise TaskLedgerError("claim_conflict")
                row = connection.execute(
                    "SELECT * FROM task_records WHERE request_id=?", (request_id,)
                ).fetchone()
                connection.execute("COMMIT")
                return self._record(row, duplicate=False, start_granted=True)
            connection.execute("COMMIT")
            return self._record(row, duplicate=True, start_granted=False)
        except TaskLedgerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise TaskLedgerError("storage_unavailable") from exc
        finally:
            connection.close()

    def transition(
        self,
        principal: str,
        request_id: str,
        body_sha256: str,
        generation: str,
        expected_state: str,
        state: str,
        evidence_sha256: str,
        result_page_id: str | None = None,
    ) -> dict[str, Any]:
        policy, connection, markers = self._open()
        try:
            self._require_active(policy, markers)
            if principal not in {policy["sender_principal"], policy["receiver_principal"]}:
                raise TaskLedgerError("principal_forbidden")
            if not _safe_text(request_id) or not isinstance(body_sha256, str) or SHA256_RE.fullmatch(body_sha256) is None:
                raise TaskLedgerError("transition_invalid")
            if generation != policy["generation"]:
                raise TaskLedgerError("generation_mismatch")
            if expected_state not in STATES or state not in STATES:
                raise TaskLedgerError("state_invalid")
            if not isinstance(evidence_sha256, str) or SHA256_RE.fullmatch(evidence_sha256) is None:
                raise TaskLedgerError("evidence_invalid")
            if result_page_id is not None and not _safe_text(result_page_id, 256):
                raise TaskLedgerError("result_reference_invalid")

            transition_roles = {
                ("running", "delivered"): "receiver",
                ("running", "failed"): "receiver",
                ("running", "blocked"): "receiver",
                ("delivered", "readback_verified"): "sender",
                ("readback_verified", "closed"): "receiver",
            }
            state_roles = {
                "admitted": "sender",
                "running": "receiver",
                "delivered": "receiver",
                "failed": "receiver",
                "blocked": "receiver",
                "readback_verified": "sender",
                "closed": "receiver",
            }
            connection.execute("BEGIN IMMEDIATE")
            self._validate_schema_and_markers(connection, policy)
            row = connection.execute(
                "SELECT * FROM task_records WHERE request_id=?", (request_id,)
            ).fetchone()
            if row is None:
                raise TaskLedgerError("request_not_found")
            if row["body_sha256"] != body_sha256 or row["generation"] != generation:
                raise TaskLedgerError("request_binding_mismatch")
            if row["state"] == state:
                self._validate_principal(policy, principal, state_roles[state])
                if row["evidence_sha256"] == evidence_sha256 and row["result_page_id"] == result_page_id:
                    connection.execute("COMMIT")
                    return self._record(row, duplicate=True)
                raise TaskLedgerError("state_conflict")
            if row["state"] != expected_state:
                raise TaskLedgerError("state_conflict")
            role = transition_roles.get((expected_state, state))
            if role is None:
                raise TaskLedgerError("transition_forbidden")
            self._validate_principal(policy, principal, role)
            cursor = connection.execute(
                "UPDATE task_records SET state=?,evidence_sha256=?,result_page_id=?,updated_at=? "
                "WHERE request_id=? AND state=? AND body_sha256=? AND generation=?",
                (
                    state, evidence_sha256, result_page_id, self.clock(), request_id,
                    expected_state, body_sha256, generation,
                ),
            )
            if cursor.rowcount != 1:
                raise TaskLedgerError("state_conflict")
            row = connection.execute(
                "SELECT * FROM task_records WHERE request_id=?", (request_id,)
            ).fetchone()
            connection.execute("COMMIT")
            return self._record(row, duplicate=False)
        except TaskLedgerError:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise TaskLedgerError("storage_unavailable") from exc
        finally:
            connection.close()

    def backup_to(self, target: str | Path) -> dict[str, Any]:
        """Create an archive-only SQLite backup at a path that does not exist."""
        policy, source, markers = self._open()
        if markers["archive_only"] == "1":
            source.close()
            raise TaskLedgerError("ledger_archive_only")
        target_path = Path(target)
        if target_path.exists() or target_path.is_symlink():
            source.close()
            raise TaskLedgerError("destination_exists")
        if not target_path.parent.is_dir():
            source.close()
            raise TaskLedgerError("destination_parent_missing")
        descriptor = None
        destination = None
        try:
            descriptor = os.open(
                target_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_BINARY", 0),
                0o600,
            )
            os.close(descriptor)
            descriptor = None
            destination = sqlite3.connect(
                _sqlite_uri(target_path, "rw"), uri=True, isolation_level=None, timeout=5
            )
            destination.execute("PRAGMA busy_timeout = 5000")
            destination.execute("PRAGMA synchronous = FULL")
            source.backup(destination)
            destination.execute("BEGIN IMMEDIATE")
            destination.execute("UPDATE ledger_meta SET value='1' WHERE key='archive_only'")
            destination.execute("COMMIT")
            check = destination.execute("PRAGMA quick_check").fetchone()
            if check is None or check[0] != "ok":
                raise TaskLedgerError("backup_invalid")
            copied = dict(markers)
            copied["archive_only"] = "1"
            if copied["generation"] != policy["generation"]:
                raise TaskLedgerError("backup_invalid")
            return {
                "status": "backed_up",
                "generation": policy["generation"],
                "archive_only": True,
            }
        except TaskLedgerError:
            raise
        except FileExistsError as exc:
            raise TaskLedgerError("destination_exists") from exc
        except (OSError, sqlite3.Error) as exc:
            # Keep any partial target as an unusable artifact; never overwrite it.
            raise TaskLedgerError("backup_failed") from exc
        finally:
            if destination is not None:
                destination.close()
            source.close()


def _write_new_policy(path: Path, policy: dict[str, Any]) -> None:
    raw = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise TaskLedgerError("policy_write_failed") from exc


def _load_identity(path: Path) -> dict[str, Any]:
    value = _read_json(path, "identity_invalid")
    if set(value) != IDENTITY_FIELDS:
        raise TaskLedgerError("identity_schema_invalid")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init", help="create a new, exclusive ledger generation")
    init_parser.add_argument("--db", required=True)
    init_parser.add_argument("--policy", required=True)
    init_parser.add_argument("--identity-json", required=True)
    init_parser.add_argument("--activate", action="store_true", help="explicitly provision this generation active")
    backup_parser = commands.add_parser("backup", help="write an archive-only backup to a nonexistent path")
    backup_parser.add_argument("--db", required=True)
    backup_parser.add_argument("--policy", required=True)
    backup_parser.add_argument("--target", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = TaskLedger.initialize(
                args.db,
                args.policy,
                _load_identity(Path(args.identity_json)),
                activate=args.activate,
            )
        else:
            result = TaskLedger(args.db, args.policy).backup_to(args.target)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0
    except TaskLedgerError as exc:
        print(json.dumps({"error": exc.code}, separators=(",", ":")), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
