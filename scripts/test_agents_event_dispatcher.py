from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agents_event_dispatcher as dispatcher_module


NOW = dt.datetime(2026, 10, 5, 12, 0, tzinfo=dt.timezone.utc)
THREAD_ID = "01a0fffa-a9e3-70a1-a80f-6dfcd59a879a"


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class FakeQueue:
    def __init__(self, events: list[dict[str, object]] | None = None, *, consume_responses: list[object] | None = None):
        self.events = list(events or [])
        self.consume_responses = list(consume_responses or [])
        self.list_calls: list[int] = []
        self.consume_calls: list[str] = []

    def list_events(self, after: int) -> dict[str, object]:
        self.list_calls.append(after)
        events = [item for item in self.events if int(item["seq"]) > after]
        return {"events": events[:100], "next_after": int(events[min(len(events), 100) - 1]["seq"]) if events else after}

    def consume(self, event_id: str) -> dict[str, object]:
        self.consume_calls.append(event_id)
        if self.consume_responses:
            outcome = self.consume_responses.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        return {"event_id": event_id, "status": "consumed"}


class FakeBackend:
    def __init__(self, result: dispatcher_module.NativeResult | BaseException | None = None):
        self.result = result
        self.calls: list[tuple[dispatcher_module.Policy, str, Path]] = []

    def run(self, policy: dispatcher_module.Policy, prompt: str, output_path: Path) -> dispatcher_module.NativeResult:
        self.calls.append((policy, prompt, output_path))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result or successful_native_result()


def successful_native_result(receipt: dict[str, object] | None = None, *, startup_warning: bool = True) -> dispatcher_module.NativeResult:
    receipt_value = receipt or {
        "event_id": "evt-probe-01",
        "task_id": "task-probe-01",
        "request_id": "req-probe-01",
        "challenge": "A" * 43,
        "body_sha256": "0" * 64,
        "executed": True,
        "godot_executed": False,
        "production_modified": False,
    }
    message = encoded(receipt_value).decode("utf-8")
    events = [
        {"type": "thread.started", "thread_id": "native-thread-1"},
        {"type": "turn.started", "turn_id": "native-turn-1"},
        {"type": "item.completed", "item": {"id": "message-1", "type": "agent_message", "text": message}},
        {"type": "turn.completed", "turn_id": "native-turn-1"},
    ]
    if startup_warning:
        events.insert(1, {"type": "item.completed", "item": {
            "id": "item_0", "type": "error", "message": dispatcher_module.DISABLED_HOST_STARTUP_WARNING}})
    return dispatcher_module.NativeResult(0, b"\n".join(encoded(item) for item in events) + b"\n", message.encode("utf-8"))


class DispatcherFixture:
    def __init__(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "worker"
        self.root.mkdir()
        self.policy_path = self.base / "installation" / "dispatcher.json"
        self.policy_path.parent.mkdir()
        self.client_config = self.base / "ssh.json"
        self.client_config.write_text("{}", encoding="utf-8")
        self.codex_exe = self.base / "codex.exe"
        self.codex_exe.write_bytes(b"MZ-test executable placeholder")
        self.ledger_path = self.root / "ledger.sqlite3"
        self.registry_path = self.root / "registry.json"
        self.schema_path = self.root / "receipt.schema.json"
        self.schema_path.write_bytes(encoded(dispatcher_module.OUTPUT_SCHEMA))
        self.body_path = self.root / "probe-01.json"
        self.auth_path = self.root / "authorization-01.json"
        self.event_id = "evt-probe-01"
        self.task_id = "task-probe-01"
        self.request_id = "req-probe-01"
        self.challenge = "A" * 43
        self.expires_at = "2026-10-05T13:00:00Z"
        self.body: dict[str, object] = {
            "schema": dispatcher_module.BODY_SCHEMA,
            "event_id": self.event_id,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "challenge": self.challenge,
            "source_thread_id": THREAD_ID,
            "reply_to_thread_id": THREAD_ID,
            "expires_at": self.expires_at,
            "scope": dispatcher_module.SCOPE,
            "godot_executed": False,
            "production_modified": False,
        }
        self.authorization: dict[str, object] = {
            "schema": dispatcher_module.AUTH_SCHEMA,
            "record_id": "auth-probe-01",
            "thread_id": THREAD_ID,
            "authorized_at": "2026-10-05T11:55:00Z",
            "expires_at": self.expires_at,
            "event_id": self.event_id,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "scope": dispatcher_module.SCOPE,
            "allowed_actions": dispatcher_module.ALLOWED_ACTIONS,
            "original_user_message": "Execute one synthetic local notification probe.",
            "revoked": False,
        }
        self.write_json(self.body_path, self.body)
        self.write_json(self.auth_path, self.authorization)
        self.entry: dict[str, object] = {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "kind": dispatcher_module.PROBE_KIND,
            "body_sha256": hashlib.sha256(self.body_path.read_bytes()).hexdigest(),
            "body_file": self.body_path.name,
            "authorization_record_file": self.auth_path.name,
            "authorization_record_sha256": hashlib.sha256(self.auth_path.read_bytes()).hexdigest(),
            "source_thread_id": THREAD_ID,
            "reply_to_thread_id": THREAD_ID,
            "expires_at": self.expires_at,
        }
        self.write_registry([self.entry])
        self.policy_data: dict[str, object] = {
            "schema": dispatcher_module.POLICY_SCHEMA,
            "recipient": dispatcher_module.RECIPIENT,
            "client_config": str(self.client_config.resolve()),
            "codex_exe": str(self.codex_exe.resolve()),
            "model": dispatcher_module.MODEL,
            "provider": dispatcher_module.PROVIDER,
            "worker_root": str(self.root.resolve()),
            "ledger_path": str(self.ledger_path.resolve()),
            "registry_path": str(self.registry_path.resolve()),
            "output_schema_path": str(self.schema_path.resolve()),
            "initial_after": 1,
            "poll_interval_seconds": 15,
            "native_timeout_seconds": 30,
        }
        self.write_json(self.policy_path, self.policy_data)
        self.policy = dispatcher_module.load_policy(self.policy_path)

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.write_bytes(encoded(value))
        if os.name != "nt":
            path.chmod(0o600)

    def write_registry(self, entries: list[dict[str, object]]) -> None:
        self.write_json(self.registry_path, {"schema": dispatcher_module.REGISTRY_SCHEMA, "entries": entries})

    def event(self, **changes: object) -> dict[str, object]:
        value: dict[str, object] = {
            "seq": 2,
            "event_id": self.event_id,
            "recipient": dispatcher_module.RECIPIENT,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "kind": dispatcher_module.PROBE_KIND,
            "body_sha256": self.entry["body_sha256"],
            "status": "sent",
            "created_at": "2026-10-05T11:58:00Z",
        }
        value.update(changes)
        return value

    def receipt(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "request_id": self.request_id,
            "challenge": self.challenge,
            "body_sha256": self.entry["body_sha256"],
            "executed": True,
            "godot_executed": False,
            "production_modified": False,
        }

    def valid_backend(self) -> FakeBackend:
        return FakeBackend(successful_native_result(self.receipt()))

    def dispatcher(self, queue: FakeQueue, backend: FakeBackend, reports: list[str] | None = None) -> dispatcher_module.Dispatcher:
        return dispatcher_module.Dispatcher(
            self.policy,
            queue,
            backend,
            clock=lambda: NOW,
            reporter=(reports.append if reports is not None else lambda _message: None),
        )

    def close(self) -> None:
        self.temp.cleanup()


class DispatcherTests(unittest.TestCase):
    def test_receipt_schema_has_explicit_api_property_types(self):
        schema = dispatcher_module.OUTPUT_SCHEMA
        self.assertEqual(set(schema['required']), set(schema['properties']))
        for name, definition in schema['properties'].items():
            with self.subTest(property=name):
                expected_type = 'boolean' if name in {'executed', 'godot_executed', 'production_modified'} else 'string'
                self.assertEqual(definition.get('type'), expected_type)
                if expected_type == 'boolean':
                    self.assertIs(type(definition['const']), bool)

    def setUp(self) -> None:
        self.fixture = DispatcherFixture()
        self.system_config_paths = patch.object(
            dispatcher_module.CodexBackend, "_system_codex_config_paths", return_value=()
        )
        self.system_config_paths.start()
        self.addCleanup(self.system_config_paths.stop)
        original_check = dispatcher_module.CodexBackend._reject_existing_configuration
        fixture_root = Path(os.path.abspath(self.fixture.base))

        def check_fixture_configuration(path: Path, label: str) -> None:
            # Keep real fixture files and dangling links visible, while giving
            # tests a clean ancestor tree independent of the host's dotfiles.
            absolute = Path(os.path.abspath(path))
            if absolute.is_relative_to(fixture_root):
                original_check(path, label)

        self.configuration_probe = patch.object(
            dispatcher_module.CodexBackend, "_reject_existing_configuration",
            side_effect=check_fixture_configuration,
        )
        self.configuration_probe.start()
        self.addCleanup(self.configuration_probe.stop)

    def tearDown(self) -> None:
        self.fixture.close()

    def test_empty_poll_never_starts_native_model(self):
        queue, backend = FakeQueue(), FakeBackend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            result = dispatcher.run_once()
        self.assertEqual(result.status, "empty")
        self.assertEqual(queue.list_calls, [1])
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_unknown_event_stays_pending_and_is_reported_once(self):
        queue = FakeQueue([self.fixture.event(event_id="evt-unknown", task_id="task-unknown", request_id="req-unknown")])
        backend, reports = FakeBackend(), []
        with self.fixture.dispatcher(queue, backend, reports) as dispatcher:
            first = dispatcher.run_once()
            second = dispatcher.run_once()
            cursor = dispatcher.cursor
        self.assertEqual((first.status, second.status), ("unknown_pending", "unknown_pending"))
        self.assertEqual(len(reports), 1)
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])
        self.assertEqual(cursor, 1)

    def test_body_tamper_is_rejected_before_native_start(self):
        self.fixture.body_path.write_bytes(encoded(dict(self.fixture.body, challenge="B" * 43)))
        if os.name != "nt":
            self.fixture.body_path.chmod(0o600)
        queue, backend = FakeQueue([self.fixture.event()]), FakeBackend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            with self.assertRaises(dispatcher_module.DispatchError):
                dispatcher.run_once()
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_policy_rejects_extra_fields_and_reparse_inputs(self):
        self.fixture.policy_data["unexpected"] = True
        self.fixture.write_json(self.fixture.policy_path, self.fixture.policy_data)
        with self.assertRaises(dispatcher_module.DispatchError):
            dispatcher_module.load_policy(self.fixture.policy_path)
        self.fixture.policy_data.pop("unexpected")
        self.fixture.write_json(self.fixture.policy_path, self.fixture.policy_data)
        with patch.object(dispatcher_module, "_is_reparse", return_value=True):
            with self.assertRaises(dispatcher_module.DispatchError):
                dispatcher_module.load_policy(self.fixture.policy_path)

    @unittest.skipIf(os.name == "nt", "POSIX write-bit check is platform-specific")
    def test_untrusted_writable_policy_is_rejected(self):
        self.fixture.policy_path.chmod(0o666)
        with self.assertRaises(dispatcher_module.DispatchError):
            dispatcher_module.load_policy(self.fixture.policy_path)

    def test_authorization_record_must_match_exact_local_scope(self):
        self.fixture.authorization["allowed_actions"] = dispatcher_module.ALLOWED_ACTIONS + ["publish_update"]
        self.fixture.write_json(self.fixture.auth_path, self.fixture.authorization)
        self.fixture.entry["authorization_record_sha256"] = hashlib.sha256(self.fixture.auth_path.read_bytes()).hexdigest()
        self.fixture.write_registry([self.fixture.entry])
        queue, backend = FakeQueue([self.fixture.event()]), FakeBackend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            with self.assertRaises(dispatcher_module.DispatchError):
                dispatcher.run_once()
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_expired_registration_is_rejected_before_native_start(self):
        self.fixture.body["expires_at"] = "2026-10-05T11:59:59Z"
        self.fixture.authorization["expires_at"] = self.fixture.body["expires_at"]
        self.fixture.write_json(self.fixture.body_path, self.fixture.body)
        self.fixture.write_json(self.fixture.auth_path, self.fixture.authorization)
        if os.name != "nt":
            self.fixture.body_path.chmod(0o600)
            self.fixture.auth_path.chmod(0o600)
        self.fixture.entry["expires_at"] = self.fixture.body["expires_at"]
        self.fixture.entry["body_sha256"] = hashlib.sha256(self.fixture.body_path.read_bytes()).hexdigest()
        self.fixture.entry["authorization_record_sha256"] = hashlib.sha256(self.fixture.auth_path.read_bytes()).hexdigest()
        self.fixture.write_registry([self.fixture.entry])
        queue, backend = FakeQueue([self.fixture.event()]), FakeBackend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            with self.assertRaises(dispatcher_module.DispatchError):
                dispatcher.run_once()
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_registry_cannot_extend_direct_authorization_expiry(self):
        extended_expiry = "2026-10-05T14:00:00Z"
        self.fixture.body["expires_at"] = extended_expiry
        self.fixture.write_json(self.fixture.body_path, self.fixture.body)
        if os.name != "nt":
            self.fixture.body_path.chmod(0o600)
        self.fixture.entry["expires_at"] = extended_expiry
        self.fixture.entry["body_sha256"] = hashlib.sha256(self.fixture.body_path.read_bytes()).hexdigest()
        self.fixture.write_registry([self.fixture.entry])
        queue, backend = FakeQueue([self.fixture.event()]), FakeBackend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            with self.assertRaises(dispatcher_module.DispatchError):
                dispatcher.run_once()
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_authorization_cannot_outlive_its_24_hour_window(self):
        expiry = "2026-10-06T11:55:01Z"
        self.fixture.authorization["expires_at"] = expiry
        self.fixture.body["expires_at"] = expiry
        self.fixture.write_json(self.fixture.auth_path, self.fixture.authorization)
        self.fixture.write_json(self.fixture.body_path, self.fixture.body)
        if os.name != "nt":
            self.fixture.auth_path.chmod(0o600)
            self.fixture.body_path.chmod(0o600)
        self.fixture.entry["expires_at"] = expiry
        self.fixture.entry["body_sha256"] = hashlib.sha256(self.fixture.body_path.read_bytes()).hexdigest()
        self.fixture.entry["authorization_record_sha256"] = hashlib.sha256(self.fixture.auth_path.read_bytes()).hexdigest()
        self.fixture.write_registry([self.fixture.entry])
        queue, backend = FakeQueue([self.fixture.event()]), FakeBackend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            with self.assertRaises(dispatcher_module.DispatchError):
                dispatcher.run_once()
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_success_records_receipt_consumes_and_advances_cursor(self):
        queue, backend = FakeQueue([self.fixture.event()]), self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            result = dispatcher.run_once()
            self.assertEqual(dispatcher.cursor, 2)
        self.assertEqual(result.status, "server_consumed")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(queue.consume_calls, [self.fixture.event_id])
        self.assertIn("Do not use tools", backend.calls[0][1])
        self.assertIn(self.fixture.challenge, backend.calls[0][1])

    def test_cached_pending_duplicate_retries_consume_without_native_turn(self):
        queue = FakeQueue(
            [self.fixture.event(), self.fixture.event(seq=3, status="pending"), self.fixture.event(seq=4, status="sent")]
        )
        backend = self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            first = dispatcher.run_once()
            second = dispatcher.run_once()
            third = dispatcher.run_once()
            cursor = dispatcher.cursor
        self.assertEqual((first.status, second.status, third.status), ("server_consumed",) * 3)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(queue.consume_calls, [self.fixture.event_id] * 3)
        self.assertEqual(cursor, 4)

    def test_cached_server_consumed_duplicate_advances_without_consume_or_native(self):
        queue = FakeQueue([self.fixture.event(), self.fixture.event(seq=3, status="consumed")])
        backend = self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            first = dispatcher.run_once()
            second = dispatcher.run_once()
            cursor = dispatcher.cursor
        self.assertEqual((first.status, second.status), ("server_consumed", "server_consumed"))
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(queue.consume_calls, [self.fixture.event_id])
        self.assertEqual(cursor, 3)

    def test_conflicting_duplicate_is_blocked_without_second_native_turn(self):
        queue = FakeQueue([self.fixture.event(), self.fixture.event(seq=3, body_sha256="f" * 64)])
        backend = self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            first = dispatcher.run_once()
            second = dispatcher.run_once()
        self.assertEqual(first.status, "server_consumed")
        self.assertEqual(second.status, "conflict")
        self.assertEqual(len(backend.calls), 1)

    def test_native_failure_blocks_claim_and_never_retries(self):
        queue = FakeQueue([self.fixture.event()])
        failed = FakeBackend(dispatcher_module.NativeResult(2, b"", None))
        with self.fixture.dispatcher(queue, failed) as dispatcher:
            first = dispatcher.run_once()
        retry_backend = self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, retry_backend) as dispatcher:
            second = dispatcher.run_once()
        self.assertEqual((first.status, second.status), ("blocked", "blocked"))
        self.assertEqual(len(failed.calls), 1)
        self.assertEqual(retry_backend.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_running_claim_after_crash_becomes_blocked_without_rerun(self):
        class SimulatedProcessCrash(BaseException):
            pass

        queue = FakeQueue([self.fixture.event()])
        crashing = FakeBackend(SimulatedProcessCrash())
        with self.assertRaises(SimulatedProcessCrash):
            with self.fixture.dispatcher(queue, crashing) as dispatcher:
                dispatcher.run_once()
        retry = self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, retry) as dispatcher:
            result = dispatcher.run_once()
        self.assertEqual(result.status, "blocked")
        self.assertEqual(len(crashing.calls), 1)
        self.assertEqual(retry.calls, [])
        self.assertEqual(queue.consume_calls, [])

    def test_receipt_before_consume_recovery_does_not_rerun_native(self):
        class SimulatedProcessCrash(BaseException):
            pass

        queue = FakeQueue([self.fixture.event()], consume_responses=[SimulatedProcessCrash()])
        backend = self.fixture.valid_backend()
        with self.assertRaises(SimulatedProcessCrash):
            with self.fixture.dispatcher(queue, backend) as dispatcher:
                dispatcher.run_once()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            result = dispatcher.run_once()
        self.assertEqual(result.status, "server_consumed")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(queue.consume_calls, [self.fixture.event_id, self.fixture.event_id])

    def test_consume_failure_recovers_from_verified_receipt(self):
        queue = FakeQueue(
            [self.fixture.event()],
            consume_responses=[OSError("offline"), {"event_id": self.fixture.event_id, "status": "consumed"}],
        )
        backend = self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            first = dispatcher.run_once()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            second = dispatcher.run_once()
        self.assertEqual((first.status, second.status), ("consume_pending", "server_consumed"))
        self.assertEqual(len(backend.calls), 1)

    def test_two_instances_cannot_claim_the_same_ledger(self):
        queue, backend = FakeQueue([self.fixture.event()]), self.fixture.valid_backend()
        with self.fixture.dispatcher(queue, backend):
            with self.assertRaises(dispatcher_module.AlreadyRunning):
                self.fixture.dispatcher(queue, backend)
        self.assertEqual(backend.calls, [])

    def test_completed_turn_and_last_message_must_match(self):
        result = self.fixture.valid_backend().result
        assert result is not None
        truncated = dispatcher_module.NativeResult(0, result.stdout.replace(b"turn.completed", b"turn.started"), result.last_message)
        with self.assertRaises(dispatcher_module.DispatchError):
            dispatcher_module._parse_completed_turn(truncated.stdout, truncated.last_message)

    def test_completed_turn_rejects_unknown_event_and_item_types(self):
        result = self.fixture.valid_backend().result
        assert result is not None
        lines = result.stdout.splitlines()
        unsupported_event = encoded({"type": "tool.operation.started"})
        with self.assertRaises(dispatcher_module.DispatchError):
            dispatcher_module._parse_completed_turn(b"\n".join([unsupported_event, *lines]), result.last_message)

        api_error = encoded({"type": "error", "message": "synthetic test failure"})
        with self.assertRaises(dispatcher_module.DispatchError):
            dispatcher_module._parse_completed_turn(b"\n".join([api_error, *lines]), result.last_message)

        failed_turn = encoded({"type": "turn.failed"})
        with self.assertRaises(dispatcher_module.DispatchError):
            dispatcher_module._parse_completed_turn(b"\n".join([failed_turn, *lines]), result.last_message)

        unsupported_item = encoded({"type": "item.started", "item": {"type": "command_execution"}})
        with self.assertRaises(dispatcher_module.DispatchError):
            dispatcher_module._parse_completed_turn(b"\n".join([unsupported_item, *lines]), result.last_message)

    def test_receipt_boolean_types_are_strict(self):
        receipt = self.fixture.receipt()
        receipt["executed"] = 1
        queue, backend = FakeQueue([self.fixture.event()]), FakeBackend(successful_native_result(receipt))
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            result = dispatcher.run_once()
        self.assertEqual(result.status, "blocked")
        self.assertEqual(queue.consume_calls, [])

    def test_exact_disabled_host_startup_warning_can_precede_completed_receipt(self):
        result = self.fixture.valid_backend().result
        assert result is not None
        queue = FakeQueue([self.fixture.event()])
        backend = FakeBackend(result)
        with self.fixture.dispatcher(queue, backend) as receiver:
            outcome = receiver.run_once()
        self.assertEqual(outcome.status, "server_consumed")
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(queue.consume_calls, [self.fixture.event_id])

    def test_startup_warning_exception_rejects_changed_repeated_or_late_errors(self):
        result = successful_native_result(self.fixture.receipt(), startup_warning=False)
        lines = result.stdout.splitlines()
        warning = {"type": "item.completed", "item": {
            "id": "item_0", "type": "error", "message": dispatcher_module.DISABLED_HOST_STARTUP_WARNING}}
        original = encoded(warning)
        changed_message = encoded({**warning, "item": {**warning["item"], "message": "different error"}})
        changed_id = encoded({**warning, "item": {**warning["item"], "id": "item_1"}})
        extra_field = encoded({**warning, "unexpected": True})
        variants = [
            lines,
            [lines[0], changed_message, *lines[1:]],
            [lines[0], changed_id, *lines[1:]],
            [lines[0], extra_field, *lines[1:]],
            [lines[0], original, original, *lines[1:]],
            [*lines[:2], original, *lines[2:]],
            [original, *lines],
            [lines[0], original, *lines[1:-1], encoded({"type": "turn.failed"})],
        ]
        for index, records in enumerate(variants):
            with self.subTest(variant=index):
                with self.assertRaises(dispatcher_module.DispatchError):
                    dispatcher_module._parse_completed_turn(b"\n".join(records), result.last_message)

    def test_other_errors_and_tools_remain_rejected_after_pinned_warning(self):
        result = successful_native_result(self.fixture.receipt())
        lines = result.stdout.splitlines()
        forbidden = [
            {"type": "error", "message": "API error"},
            {"type": "item.completed", "item": {"id": "item_1", "type": "error", "message": "other warning"}},
            {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution"}},
            {"type": "tool.operation.started"},
            {"type": "turn.failed"},
        ]
        for record in forbidden:
            with self.subTest(record=record):
                with self.assertRaises(dispatcher_module.DispatchError):
                    dispatcher_module._parse_completed_turn(
                        b"\n".join([*lines[:3], encoded(record), *lines[3:]]), result.last_message
                    )

    def test_server_consumed_without_local_receipt_is_blocked(self):
        queue = FakeQueue([self.fixture.event(status="consumed")])
        backend = FakeBackend()
        with self.fixture.dispatcher(queue, backend) as dispatcher:
            result = dispatcher.run_once()
            cursor = dispatcher.cursor
        self.assertEqual(result.status, "blocked")
        self.assertEqual(backend.calls, [])
        self.assertEqual(queue.consume_calls, [])
        self.assertEqual(cursor, 1)

    def test_command_uses_native_fixed_argv_and_openai_provider(self):
        output_path = self.fixture.root / ".receipt.json"
        argv = dispatcher_module.CodexBackend.build_argv(self.fixture.policy, "fixed", output_path)
        self.assertEqual(argv[0], str(self.fixture.codex_exe.resolve()))
        self.assertIn("--ephemeral", argv)
        self.assertIn("--sandbox", argv)
        self.assertIn("read-only", argv)
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("--ignore-user-config", argv)
        self.assertIn("--strict-config", argv)
        disabled_features = [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == "--disable"]
        self.assertEqual(disabled_features, list(dispatcher_module.DISABLED_CODEX_FEATURES))
        self.assertEqual(argv[argv.index("--enable") + 1], "skip_host_skill_discovery")
        self.assertIn("--json", argv)
        self.assertIn("--model", argv)
        self.assertIn(dispatcher_module.MODEL, argv)
        self.assertIn('model_provider="openai"', argv)
        config_values = [argv[index + 1] for index, item in enumerate(argv[:-1]) if item == "--config"]
        self.assertEqual(
            config_values,
            [
                'model_provider="openai"',
                'web_search="disabled"',
                "project_doc_max_bytes=0",
                "project_root_markers=[]",
                "tools.experimental_request_user_input.enabled=false",
                "suppress_unstable_features_warning=true",
            ],
        )
        self.assertNotIn("--provider", argv)
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", argv)
        self.assertEqual(argv[-1], "fixed")

    def test_native_environment_preserves_auth_context_and_disables_execution_server(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        (code_home / "config.toml").write_text('[mcp_servers."user-example"]\n', encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "CODEX_HOME": str(code_home),
                "CODEX_EXEC_SERVER_URL": "http://127.0.0.1:4123",
                "OPENAI_API_KEY": "test-only-value",
            },
        ):
            environment = dispatcher_module.CodexBackend._native_environment(self.fixture.root)
        self.assertEqual(environment["CODEX_HOME"], str(code_home))
        self.assertEqual(environment["OPENAI_API_KEY"], "test-only-value")
        self.assertEqual(environment["CODEX_EXEC_SERVER_URL"], "none")

    def test_noise_execution_selector_is_removed_without_changing_parent_environment(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        noise = {
            "CODEX_EXEC_SERVER_NOISE_REGISTRY_URL": "https://example.invalid",
            "CODEX_EXEC_SERVER_NOISE_ENVIRONMENT_ID": "test-only-environment",
            "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN": "test-only-value",
            "CODEX_EXEC_SERVER_NOISE_FUTURE_SELECTOR": "test-only-value",
        }
        parent = {"CODEX_HOME": str(code_home), "OPENAI_API_KEY": "test-only-value", **noise}
        with patch.object(dispatcher_module.os, "environ", parent):
            environment = dispatcher_module.CodexBackend._native_environment(self.fixture.root)
            self.assertEqual(parent, {"CODEX_HOME": str(code_home), "OPENAI_API_KEY": "test-only-value", **noise})
        self.assertFalse(any(key.upper().startswith("CODEX_EXEC_SERVER_NOISE_") for key in environment))
        self.assertEqual(environment["CODEX_HOME"], str(code_home))
        self.assertEqual(environment["OPENAI_API_KEY"], "test-only-value")
        self.assertEqual(environment["CODEX_EXEC_SERVER_URL"], "none")
        parent["codex_exec_server_noise_auth_token"] = "test-only-value"
        with patch.object(dispatcher_module.os, "environ", parent):
            environment = dispatcher_module.CodexBackend._native_environment(self.fixture.root)
        self.assertFalse(any(key.upper().startswith("CODEX_EXEC_SERVER_NOISE_") for key in environment))

    def test_existing_codex_environments_config_blocks_before_version_or_spawn(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        (code_home / "environments.toml").write_text("[environments.untrusted]\n", encoding="utf-8")
        output_path = self.fixture.root / ".receipt.json"
        backend = dispatcher_module.CodexBackend()
        with patch.dict(os.environ, {"CODEX_HOME": str(code_home)}):
            with patch.object(dispatcher_module.subprocess, "run") as version_check:
                with patch.object(dispatcher_module.subprocess, "Popen") as native_spawn:
                    result = backend.run(self.fixture.policy, "fixed", output_path)
        self.assertEqual(result.error, "custom_codex_environments_is_unavailable_to_this_worker")
        version_check.assert_not_called()
        native_spawn.assert_not_called()

    @unittest.skipIf(os.name == "nt", "POSIX can create a dangling symlink without elevated privileges")
    def test_dangling_codex_environments_symlink_blocks_before_spawn(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        (code_home / "environments.toml").symlink_to(code_home / "missing-environments.toml")
        with patch.dict(os.environ, {"CODEX_HOME": str(code_home)}):
            with self.assertRaises(dispatcher_module.DispatchError):
                dispatcher_module.CodexBackend._native_environment(self.fixture.root)

    def test_git_marker_without_project_config_does_not_block_native_environment(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        git_marker = self.fixture.base / ".git"
        git_marker.write_text("test-only repository marker", encoding="utf-8")
        with patch.dict(os.environ, {"CODEX_HOME": str(code_home)}):
            environment = dispatcher_module.CodexBackend._native_environment(self.fixture.root)
        self.assertEqual(environment["CODEX_EXEC_SERVER_URL"], "none")
        git_marker.unlink()

    def test_project_codex_config_blocks_before_native_spawn(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        project_config = self.fixture.root / ".codex" / "config.toml"
        project_config.parent.mkdir()
        project_config.write_text("test-only project configuration", encoding="utf-8")
        with patch.dict(os.environ, {"CODEX_HOME": str(code_home)}):
            with patch.object(dispatcher_module.subprocess, "run") as version_check:
                with patch.object(dispatcher_module.subprocess, "Popen") as native_spawn:
                    result = dispatcher_module.CodexBackend().run(
                        self.fixture.policy, "fixed", self.fixture.root / ".receipt.json"
                    )
        self.assertEqual(result.returncode, 1)
        version_check.assert_not_called()
        native_spawn.assert_not_called()

    def test_windows_managed_config_or_requirements_blocks_before_spawn(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        managed_root = self.fixture.base / "managed" / "OpenAI" / "Codex"
        managed_root.mkdir(parents=True)
        for name in ("config.toml", "requirements.toml"):
            with self.subTest(name=name):
                managed_file = managed_root / name
                managed_file.write_text("test-only managed configuration", encoding="utf-8")
                with patch.object(
                    dispatcher_module.CodexBackend, "_system_codex_config_paths", return_value=(managed_file,)
                ):
                    with patch.dict(os.environ, {"CODEX_HOME": str(code_home)}):
                        with patch.object(dispatcher_module.subprocess, "run") as version_check:
                            with patch.object(dispatcher_module.subprocess, "Popen") as native_spawn:
                                result = dispatcher_module.CodexBackend().run(
                                    self.fixture.policy, "fixed", self.fixture.root / ".receipt.json"
                                )
                self.assertEqual(result.returncode, 1)
                version_check.assert_not_called()
                native_spawn.assert_not_called()
                managed_file.unlink()

    def test_unsupported_codex_version_blocks_before_native_spawn(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        output_path = self.fixture.root / ".receipt.json"
        backend = dispatcher_module.CodexBackend()
        version_output = dispatcher_module.subprocess.CompletedProcess([], 0, b"codex-cli 0.156.0\n", b"")
        with patch.dict(os.environ, {"CODEX_HOME": str(code_home)}):
            with patch.object(dispatcher_module.subprocess, "run", return_value=version_output) as version_check:
                with patch.object(dispatcher_module.subprocess, "Popen") as native_spawn:
                    result = backend.run(self.fixture.policy, "fixed", output_path)
        self.assertEqual(result.error, "native_codex_version_is_unsupported")
        version_check.assert_called_once()
        native_spawn.assert_not_called()

    def test_codex_version_is_verified_once_per_backend_instance(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        backend = dispatcher_module.CodexBackend()
        environment = {"CODEX_HOME": str(code_home), "CODEX_EXEC_SERVER_URL": "none"}
        version_output = dispatcher_module.subprocess.CompletedProcess([], 0, b"codex-cli 0.156.1\n", b"")
        with patch.object(dispatcher_module.subprocess, "run", return_value=version_output) as version_check:
            backend._verify_cli_version(self.fixture.policy.codex_exe, environment)
            backend._verify_cli_version(self.fixture.policy.codex_exe, environment)
        version_check.assert_called_once()

    def test_native_spawn_receives_isolated_environment_and_fixed_cli_version(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        output_path = self.fixture.root / ".receipt.json"
        expected = successful_native_result(self.fixture.receipt())
        assert expected.last_message is not None
        version_output = dispatcher_module.subprocess.CompletedProcess([], 0, b"codex-cli 0.156.1\n", b"")

        class FakeNativeProcess:
            returncode = 0

            def __init__(self, _argv, **kwargs):
                kwargs["stdout"].write(expected.stdout)
                expected_output_path.write_bytes(expected.last_message)

            def poll(self):
                return 0

        expected_output_path = output_path
        with patch.dict(
            os.environ,
            {"CODEX_HOME": str(code_home), "CODEX_EXEC_SERVER_URL": "http://127.0.0.1:4123", "OPENAI_API_KEY": "test-only-value",
             "CODEX_EXEC_SERVER_NOISE_REGISTRY_URL": "https://example.invalid",
             "CODEX_EXEC_SERVER_NOISE_ENVIRONMENT_ID": "test-only-environment",
             "CODEX_EXEC_SERVER_NOISE_AUTH_TOKEN": "test-only-value"},
        ):
            with patch.object(dispatcher_module.subprocess, "run", return_value=version_output) as version_check:
                with patch.object(dispatcher_module.subprocess, "Popen", side_effect=FakeNativeProcess) as native_spawn:
                    result = dispatcher_module.CodexBackend().run(self.fixture.policy, "fixed", output_path)
        process_environment = native_spawn.call_args.kwargs["env"]
        self.assertEqual(result.returncode, 0)
        self.assertEqual(process_environment["CODEX_EXEC_SERVER_URL"], "none")
        self.assertEqual(process_environment["CODEX_HOME"], str(code_home))
        self.assertEqual(process_environment["OPENAI_API_KEY"], "test-only-value")
        self.assertFalse(any(key.upper().startswith("CODEX_EXEC_SERVER_NOISE_") for key in process_environment))
        self.assertEqual(version_check.call_args.kwargs["env"], process_environment)

    def test_loop_count_is_bounded_before_loading_policy(self):
        with self.assertRaises(SystemExit) as result:
            dispatcher_module.main(["--loop", str(dispatcher_module.MAX_LOOP_COUNT + 1)])
        self.assertEqual(result.exception.code, 2)

    def test_missing_native_receipt_preserves_exit_code_and_stream(self):
        code_home = self.fixture.base / ".codex"
        code_home.mkdir()
        raw = b'{"type":"error","message":"test-only startup failure"}\n'
        version_output = dispatcher_module.subprocess.CompletedProcess([], 0, b"codex-cli 0.156.1\n", b"")
        for exit_code in (0, 7):
            with self.subTest(exit_code=exit_code):
                class MissingReceiptProcess:
                    returncode = exit_code

                    def __init__(self, _argv, **kwargs):
                        kwargs["stdout"].write(raw)

                    def poll(self):
                        return self.returncode

                with patch.dict(os.environ, {"CODEX_HOME": str(code_home)}):
                    with patch.object(dispatcher_module.subprocess, "run", return_value=version_output):
                        with patch.object(dispatcher_module.subprocess, "Popen", side_effect=MissingReceiptProcess):
                            result = dispatcher_module.CodexBackend().run(
                                self.fixture.policy, "fixed", self.fixture.root / ".missing-receipt.json"
                            )
                self.assertEqual(result.returncode, exit_code)
                self.assertEqual(result.stdout, raw)
                self.assertIsNone(result.last_message)
                self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
