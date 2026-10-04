#!/usr/bin/env python3
"""End-to-end tests for the dot-link Ed25519 CLI using temporary identities."""

from __future__ import annotations

import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / ".agents" / "skills" / "dot-link" / "scripts" / "dot_link.py"
OPENSSL_VALUE = os.environ.get("DOT_LINK_OPENSSL") or shutil.which("openssl")
OPENSSL = Path(OPENSSL_VALUE) if OPENSSL_VALUE else None


def run_cli(*args: str, expect: int = 0) -> subprocess.CompletedProcess[str]:
    child_environment = dict(os.environ)
    child_environment["PYTHONUTF8"] = "1"
    result = subprocess.run(
        [sys.executable, str(CLI), *args],
        env=child_environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != expect:
        raise AssertionError(
            f"CLI exit {result.returncode}, expected {expect}: stdout={result.stdout!r}, stderr={result.stderr!r}"
        )
    return result


class DotLinkEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if OPENSSL is None or not OPENSSL.is_file():
            raise RuntimeError(
                "OpenSSL is required for dot-link tests; set DOT_LINK_OPENSSL or add openssl to PATH"
            )
        cls.temp = tempfile.TemporaryDirectory(prefix="dot-link-test-")
        cls.root = Path(cls.temp.name)
        cls.state_a = cls.root / "identity-a"
        cls.state_b = cls.root / "identity-b"
        cls.state_a.mkdir()
        cls.state_b.mkdir()
        run_cli("init", "--state-dir", str(cls.state_a), "--openssl", str(OPENSSL))
        run_cli("init", "--state-dir", str(cls.state_b), "--openssl", str(OPENSSL))
        cls.profile_a = json.loads((cls.state_a / "public-profile.json").read_text(encoding="utf-8"))
        cls.profile_b = json.loads((cls.state_b / "public-profile.json").read_text(encoding="utf-8"))
        cls.config_a = cls.root / "config-a.json"
        cls.config_a.write_text(
            json.dumps(
                {
                    "openssl": str(OPENSSL),
                    "private_key": str(cls.state_a / "private.pem"),
                    "public_profile": str(cls.state_a / "public-profile.json"),
                    "pairing_id": "pairing-test-1",
                    "recipient_thread_id": "recipient-001",
                    "protocol": "dot-local/0.2",
                    "recipient_host_id": "test-host",
                    "python": sys.executable,
                    "protocol_page_id": "test-protocol-page",
                    "receiver_status": "pending_human_binding",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        cls.pending_pin = cls.root / "pin-pending.json"
        cls.active_pin = cls.root / "pin-active.json"
        cls._write_pin(cls.pending_pin, cls.profile_a, "pending_human_binding")
        cls._write_pin(cls.active_pin, cls.profile_a, "active")
        cls.pin_wrong = cls.root / "pin-wrong.json"
        cls._write_pin(cls.pin_wrong, cls.profile_b, "active")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    @staticmethod
    def _write_pin(path: Path, profile: dict[str, str], authorization_status: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "public_key_pem": profile["public_key_pem"],
                    "key_id": profile["key_id"],
                    "client_id": profile["client_id"],
                    "pairing_id": "pairing-test-1",
                    "recipient_thread_id": "recipient-001",
                    "authorization_status": authorization_status,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _body(self, **changes):
        body = {
            "protocol": "dot-local/0.2",
            "pairing_id": "pairing-test-1",
            "client_id": self.profile_a["client_id"],
            "key_id": self.profile_a["key_id"],
            "type": "TASK",
            "recipient_thread_id": "recipient-001",
            "source_thread_id": "sender-thread-001",
            "reply_thread_id": None,
            "task_id": "task-001",
            "request_id": "request-001",
            "attempt": 1,
            "seq": 1,
            "in_reply_to": None,
            "nonce": "nonce-001",
            "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=30))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "scope": {"repo": "Mercury", "paths": ["scripts/codex/"]},
            "payload": {"instruction": "review this bounded change"},
            "artifacts": [],
            "delivery_status": "proposed",
            "domain_verdict": None,
        }
        body.update(changes)
        return body

    def _sign(self, name: str, body: dict, *, source: bool = True) -> Path:
        body_path = self.root / f"{name}-body.json"
        envelope_path = self.root / f"{name}-envelope.json"
        body_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        run_cli("sign", "--config", str(self.config_a), "--body", str(body_path), "--out", str(envelope_path))
        return envelope_path

    def _verify(self, pin: Path, envelope: Path, *extra: str, expect: int = 0):
        result = run_cli(
            "verify", "--pin", str(pin), "--envelope", str(envelope), "--openssl", str(OPENSSL), *extra,
            expect=expect,
        )
        if expect:
            return result
        return json.loads(result.stdout)

    @staticmethod
    def _load_envelope(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _save_envelope(path: Path, envelope: dict) -> None:
        path.write_text(json.dumps(envelope, separators=(",", ":")), encoding="utf-8")

    def _resign_raw_body(self, body_bytes: bytes, name: str) -> Path:
        raw_path = self.root / f"{name}-raw.json"
        sig_path = self.root / f"{name}-sig.bin"
        raw_path.write_bytes(body_bytes)
        signed = subprocess.run(
            [
                str(OPENSSL), "pkeyutl", "-sign", "-inkey", str(self.state_a / "private.pem"),
                "-rawin", "-in", str(raw_path), "-out", str(sig_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(signed.returncode, 0, signed.stderr.decode("utf-8", "replace"))
        envelope_path = self.root / f"{name}-raw-envelope.json"
        envelope_path.write_text(
            json.dumps(
                {
                    "encoding": "base64",
                    "signed_body_b64": base64.b64encode(body_bytes).decode("ascii"),
                    "signature_b64": base64.b64encode(sig_path.read_bytes()).decode("ascii"),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        return envelope_path

    def test_init_sign_verify_and_authorization_are_separate(self):
        envelope = self._sign("basic", self._body())
        result = self._verify(
            self.pending_pin, envelope, "--transport-source", "sender-thread-001"
        )
        self.assertEqual(result["status"], "verified_unaccepted")
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertFalse(result["authorization"]["identity_trusted"])
        self.assertFalse(result["authorization"]["task_authorization_checked"])
        self.assertFalse(result["acceptance"]["ready_for_authorization_review"])
        self.assertFalse(result["acceptance"]["execution_allowed"])
        self.assertEqual(result["verified_body"]["payload"]["instruction"], "review this bounded change")

        active = self._verify(self.active_pin, envelope)
        self.assertTrue(active["authorization"]["identity_trusted"])
        self.assertFalse(active["authorization"]["task_authorization_checked"])
        self.assertEqual(active["acceptance"]["status"], "verified_unaccepted")
        self.assertFalse(active["acceptance"]["execution_allowed"])

    def test_init_refuses_to_overwrite_existing_identity(self):
        result = run_cli(
            "init", "--state-dir", str(self.state_a), "--openssl", str(OPENSSL), expect=2
        )
        self.assertIn("拒绝覆盖", result.stderr)

    def test_type_payload_and_route_tampering_fail_signature(self):
        original = self._sign("tamper", self._body())
        for field, value in (
            ("type", "CANCEL"),
            ("payload", {"instruction": "altered"}),
            ("recipient_thread_id", "recipient-other"),
        ):
            with self.subTest(field=field):
                envelope = self._load_envelope(original)
                signed = json.loads(base64.b64decode(envelope["signed_body_b64"]).decode("utf-8"))
                signed[field] = value
                envelope["signed_body_b64"] = base64.b64encode(
                    json.dumps(signed, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
                ).decode("ascii")
                tampered = self.root / f"tampered-{field}.json"
                self._save_envelope(tampered, envelope)
                result = self._verify(self.pending_pin, tampered, expect=2)
                self.assertIn("签名验证失败", result.stderr)

    def test_wrong_key_and_transport_source_are_rejected(self):
        envelope = self._sign("wrong-key", self._body())
        self._verify(self.pin_wrong, envelope, expect=2)
        self._verify(self.pending_pin, envelope, "--transport-source", "other-thread", expect=2)

        wrong_route = self._body(recipient_thread_id="recipient-other", request_id="request-wrong-route")
        raw = json.dumps(wrong_route, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        wrong_route_envelope = self._resign_raw_body(raw, "wrong-route")
        result = self._verify(self.pending_pin, wrong_route_envelope, expect=2)
        self.assertIn("recipient_thread_id", result.stderr)

    def test_expired_signed_body_is_rejected(self):
        body = self._body(
            expires_at=(dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1))
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        envelope = self._resign_raw_body(raw, "expired")
        result = self._verify(self.pending_pin, envelope, expect=2)
        self.assertIn("已过期", result.stderr)

    def test_duplicate_json_keys_and_outer_shadow_fields_are_rejected(self):
        original = self._sign("duplicate", self._body())
        envelope = self._load_envelope(original)
        signed = base64.b64decode(envelope["signed_body_b64"])
        duplicate = signed.replace(b'"type":"TASK"', b'"type":"TASK","type":"TASK"', 1)
        self.assertNotEqual(duplicate, signed)
        body_envelope = self._resign_raw_body(duplicate, "duplicate-body")
        result = self._verify(self.pending_pin, body_envelope, expect=2)
        self.assertIn("重复字段", result.stderr)

        outer = self._load_envelope(original)
        outer["payload"] = {"shadow": "must not be trusted"}
        outer_path = self.root / "outer-shadow.json"
        self._save_envelope(outer_path, outer)
        result = self._verify(self.pending_pin, outer_path, expect=2)
        self.assertIn("未知字段", result.stderr)

        raw_outer = original.read_text(encoding="utf-8").strip()
        duplicate_outer = self.root / "duplicate-outer.json"
        duplicate_outer.write_text(raw_outer.replace('"encoding":"base64"', '"encoding":"base64","encoding":"base64"'), encoding="utf-8")
        result = self._verify(self.pending_pin, duplicate_outer, expect=2)
        self.assertIn("重复字段", result.stderr)

    def test_replay_ledger_duplicate_request_conflict_and_nonce_reuse(self):
        ledger = self.root / "ledger.sqlite"
        initial = self._sign("ledger-first", self._body())
        accepted = self._verify(
            self.active_pin, initial, "--ledger", str(ledger), "--accept"
        )
        self.assertEqual(accepted["acceptance"]["status"], "accepted")
        self.assertTrue(accepted["acceptance"]["ready_for_authorization_review"])
        self.assertFalse(accepted["authorization"]["task_authorization_checked"])
        self.assertFalse(accepted["acceptance"]["execution_allowed"])
        self.assertFalse(accepted["acceptance"]["payload_executed"])

        duplicate = self._verify(
            self.active_pin, initial, "--ledger", str(ledger), "--accept"
        )
        self.assertEqual(duplicate["acceptance"]["status"], "duplicate")
        self.assertFalse(duplicate["acceptance"]["ready_for_authorization_review"])
        self.assertFalse(duplicate["acceptance"]["execution_allowed"])

        request_conflict = self._sign(
            "ledger-request-conflict",
            self._body(payload={"instruction": "different"}, nonce="nonce-new"),
        )
        result = self._verify(
            self.active_pin, request_conflict, "--ledger", str(ledger), "--accept", expect=2
        )
        self.assertIn("request_id", result.stderr)

        nonce_reuse = self._sign(
            "ledger-nonce-reuse",
            self._body(request_id="request-new", nonce="nonce-001", payload={"instruction": "new request"}),
        )
        result = self._verify(
            self.active_pin, nonce_reuse, "--ledger", str(ledger), "--accept", expect=2
        )
        self.assertIn("nonce", result.stderr)

    def test_same_key_verifies_message_from_new_source_thread(self):
        envelope = self._sign(
            "new-source", self._body(source_thread_id="sender-thread-after-restart", request_id="request-new-source")
        )
        result = self._verify(
            self.pending_pin, envelope, "--transport-source", "sender-thread-after-restart"
        )
        self.assertEqual(result["verification"]["key_id"], self.profile_a["key_id"])
        self.assertEqual(result["verified_body"]["source_thread_id"], "sender-thread-after-restart")


if __name__ == "__main__":
    unittest.main(verbosity=2)
