"""Offline API contract, private-state, budget and failure tests. No paid calls."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "packs/core/skills/image-api/scripts/image_api.py"
spec = importlib.util.spec_from_file_location("mercury_image_api", SCRIPT)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)
IMAGE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")


class ImageApiTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text(".private/\n", encoding="utf-8")
        self.private = self.root / ".private"
        self.private.mkdir()
        self.config = self.private / "config.json"
        self.card = self.private / "card.json"
        self.cfg = {"project_root": str(self.root), "state_dir": ".private/state",
                    "key_env": "MERCURY_IMAGE_TEST_KEY", "budget_usd": "2.00",
                    "reserve_usd": "1.00", "max_calls": 2}
        self.job = {"mode": "generate", "prompt": "An original silver leaf, no text."}
        self.save()
        self.response = {"data": [{"b64_json": base64.b64encode(IMAGE).decode()}],
                         "usage": {"input_tokens": 12, "output_tokens": 20,
                                   "input_tokens_details": {"text_tokens": 10, "image_tokens": 2}}}

    def save(self):
        self.config.write_text(json.dumps(self.cfg), encoding="utf-8")
        self.card.write_text(json.dumps(self.job), encoding="utf-8")

    def execute(self, response=None, error=None):
        with patch.dict(os.environ, {"MERCURY_IMAGE_TEST_KEY": "synthetic-private-key"}), \
             patch.object(api, "request", return_value=response or self.response, side_effect=error) as request:
            result = api.run(self.config, self.card, True)
            return result, request

    def accounted(self):
        with closing(sqlite3.connect(self.private / "state/usage.sqlite3")) as db:
            return db.execute("SELECT micro, status FROM calls").fetchall()

    def test_preflight_does_not_read_credentials_create_state_or_call_api(self):
        del self.cfg["key_env"]
        self.cfg["key_file"] = str(self.private / "nonexistent.env")
        self.save()
        with patch.object(api, "request", side_effect=AssertionError("network")):
            result = api.run(self.config, self.card)
        self.assertEqual(result["status"], "dry-run")
        self.assertFalse((self.private / "state").exists())

    def test_generate_settles_actual_usage_and_saves_exact_image(self):
        result, request = self.execute()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["actual_usd"], 0.000666)
        self.assertEqual(self.accounted(), [(666, "ok")])
        output = self.private / "state" / result["run_id"]
        self.assertEqual((output / "image.png").read_bytes(), IMAGE)
        self.assertEqual(result["image_sha256"], hashlib.sha256(IMAGE).hexdigest())
        self.assertEqual(result["dimensions"], [1, 1])
        self.assertNotIn("synthetic-private-key", (output / "receipt.json").read_text())
        self.assertEqual(request.call_args.args[0]["n"], 1)

    def test_edit_sends_exact_reference_bytes_and_records_their_digest(self):
        (self.root / "source.png").write_bytes(IMAGE)
        self.job.update(mode="edit", references=["source.png"])
        self.save()
        result, request = self.execute()
        body, mode, _ = request.call_args.args
        self.assertEqual(mode, "edit")
        sent = body["images"][0]["image_url"].split(",", 1)[1]
        self.assertEqual(base64.b64decode(sent), IMAGE)
        self.assertEqual(result["references"], [{"bytes": len(IMAGE), "sha256": hashlib.sha256(IMAGE).hexdigest()}])
        self.assertNotIn("source.png", json.dumps(result))

    def test_unknown_charge_is_reserved_and_not_automatically_retried(self):
        result, request = self.execute(error=TimeoutError("sensitive server echo"))
        self.assertEqual(result["status"], "unknown-charge")
        self.assertEqual(result["accounted_usd"], 1.0)
        self.assertEqual(self.accounted(), [(1_000_000, "unknown-charge")])
        self.assertEqual(request.call_count, 1)
        self.assertNotIn("sensitive", json.dumps(result))

    def test_incomplete_or_invalid_usage_keeps_reservation(self):
        for bad in (None, {}, {"input_tokens": 12, "output_tokens": -1},
                    {"input_tokens": 13, "output_tokens": 20,
                     "input_tokens_details": {"text_tokens": 10, "image_tokens": 2}}):
            self.assertIsNone(api.usage_cost(bad))
        response = dict(self.response, usage={"input_tokens": 12})
        result, _ = self.execute(response=response)
        self.assertIsNone(result["actual_usd"])
        self.assertEqual(result["accounted_usd"], 1.0)

    def test_usage_receipt_omits_unexpected_server_fields(self):
        self.response["usage"]["private_extra"] = "sensitive server echo"
        result, _ = self.execute()
        self.assertNotIn("sensitive", json.dumps(result))

    def test_actual_over_reservation_blocks_further_calls(self):
        self.cfg["reserve_usd"] = "0.000001"
        self.save()
        result, _ = self.execute()
        self.assertEqual(result["status"], "budget-exceeded")
        with self.assertRaisesRegex(ValueError, "exceeded its reservation"):
            self.execute()
        self.assertEqual(len(self.accounted()), 1)

    def test_call_limit_is_checked_before_network(self):
        self.cfg["max_calls"] = 1
        self.save()
        self.execute()
        with patch.dict(os.environ, {"MERCURY_IMAGE_TEST_KEY": "synthetic-private-key"}), \
             patch.object(api, "request") as request:
            with self.assertRaisesRegex(ValueError, "exhausted"):
                api.run(self.config, self.card, True)
        request.assert_not_called()

    def test_parallel_reservations_cannot_spend_same_budget(self):
        db = self.private / "parallel.sqlite3"
        def reserve(index):
            try:
                api.reserve_call(db, 1_000_000, 8, 1_000_000, str(index))
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(results.count(True), 1)

    def test_missing_credential_leaves_no_reservation(self):
        with patch.dict(os.environ, {"MERCURY_IMAGE_TEST_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "credential unavailable"):
                api.run(self.config, self.card, True)
        self.assertFalse((self.private / "state").exists())

    def test_plaintext_key_config_and_unignored_config_are_refused(self):
        self.cfg["api_key"] = "synthetic-private-key"
        self.save()
        with self.assertRaisesRegex(ValueError, "unknown config"):
            api.run(self.config, self.card)
        del self.cfg["api_key"]
        self.save()
        exposed = self.root / "config.json"
        exposed.write_bytes(self.config.read_bytes())
        with self.assertRaisesRegex(ValueError, "Git-ignored"):
            api.run(exposed, self.card)

    def test_invalid_card_and_state_escape_are_refused_before_network(self):
        self.job["mode"] = "edit"
        self.save()
        with self.assertRaisesRegex(ValueError, "1-16"):
            api.run(self.config, self.card)
        self.job["references"] = ["https://example.invalid/image.png"]
        self.cfg["state_dir"] = "../outside"
        self.save()
        with self.assertRaisesRegex(ValueError, "inside the primary"):
            api.run(self.config, self.card)

    def test_nonprivate_or_tracked_card_is_refused_before_reading_and_network(self):
        exposed = self.root / "exposed-card.json"
        exposed.write_text("not JSON", encoding="utf-8")
        with patch.object(api, "request") as request:
            with self.assertRaisesRegex(ValueError, "Git-ignored"):
                api.run(self.config, exposed, True)
            subprocess.run(["git", "-C", str(self.root), "add", "-f", str(self.card)], check=True)
            with self.assertRaisesRegex(ValueError, "Git-ignored"):
                api.run(self.config, self.card, True)
        request.assert_not_called()

    def test_cli_errors_do_not_echo_secret_or_absolute_paths(self):
        with patch.object(sys, "argv", [str(SCRIPT), "--config", str(self.config), "--card", str(self.card)]), \
             patch.object(api, "load_job", side_effect=OSError("sensitive server echo " + str(self.root))):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(api.main(), 2)
        self.assertEqual(json.loads(stdout.getvalue()), {"status": "refused", "error_type": "OSError"})

    def test_http_redirect_does_not_forward_authorization(self):
        self.assertIsNone(api.NoRedirect().redirect_request(None, None, 302, "moved", {}, "https://other.invalid"))


if __name__ == "__main__":
    unittest.main()
