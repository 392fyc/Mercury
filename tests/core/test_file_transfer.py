"""Byte delivery, recovery, and no-overwrite tests for the test-file-delivery skill."""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "packs/core/skills/test-file-delivery/scripts/transfer.py"
spec = importlib.util.spec_from_file_location("file_transfer", SCRIPT)
transfer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transfer)


class FileTransferTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".tmp"
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="file-transfer-test-", dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "sample.bin"
        self.data = bytes(range(256)) * 75 + b"\x00\xff\x80"
        self.source.write_bytes(self.data)
        self.outgoing = self.root / "outgoing"
        self.manifest = self.outgoing / "manifest.json"
        self.incoming = self.root / "incoming"
        self.dest = self.root / "received.bin"
        transfer.prepare(self.source, self.outgoing, 8192)
        self.lines = (self.outgoing / "packets.jsonl").read_text().splitlines()

    def batch(self, lines):
        path = self.root / "batch.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def receive(self, lines):
        return transfer.receive(self.manifest, self.batch(lines), self.incoming)

    def test_reordered_duplicate_packets_restore_exact_binary(self):
        self.receive(list(reversed(self.lines)) + self.lines[:1])
        result = transfer.assemble(self.incoming, self.dest)
        self.assertEqual(self.dest.read_bytes(), self.data)
        self.assertEqual(result["sha256"], transfer.sha(self.data))

    def test_missing_part_is_resumable(self):
        self.receive(self.lines[:1])
        with self.assertRaisesRegex(ValueError, "missing part: 2"):
            transfer.assemble(self.incoming, self.dest)
        self.assertFalse(self.dest.exists())
        emitted = io.StringIO()
        with contextlib.redirect_stdout(emitted):
            transfer.emit(self.outgoing, 2, 3)
        self.receive(emitted.getvalue().splitlines())
        transfer.assemble(self.incoming, self.dest)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_truncated_json_keeps_previous_verified_part(self):
        with self.assertRaises(ValueError):
            self.receive([self.lines[0], self.lines[1][:-25]])
        self.assertEqual((self.incoming / "part-00000001.bin").read_bytes(), self.data[:8192])
        self.assertFalse((self.incoming / "part-00000002.bin").exists())
        self.receive(self.lines[1:])
        transfer.assemble(self.incoming, self.dest)
        self.assertEqual(self.dest.read_bytes(), self.data)

    def test_packet_corruption_is_rejected(self):
        original = json.loads(self.lines[0][len(transfer.PREFIX):])
        for key, value in (("transfer_id", "0" * 32), ("offset", 1), ("bytes", 1),
                           ("sha256", "0" * 64), ("data", "?" * len(original["data"]))):
            with self.subTest(field=key):
                packet = dict(original, **{key: value})
                with self.assertRaises(ValueError):
                    self.receive([json.dumps(packet)])
        self.assertFalse((self.incoming / "part-00000001.bin").exists())

    def test_full_digest_and_existing_destination_protect_delivery(self):
        self.receive(self.lines)
        first = self.incoming / "part-00000001.bin"
        first.write_bytes(b"x" * 8192)
        with self.assertRaisesRegex(ValueError, "full file digest"):
            transfer.assemble(self.incoming, self.dest)
        self.assertFalse(self.dest.exists())
        with self.assertRaisesRegex(ValueError, "conflicting saved part"):
            self.receive(self.lines[:1])
        self.assertEqual(first.read_bytes(), b"x" * 8192)
        first.write_bytes(self.data[:8192])
        self.dest.write_bytes(b"keep this")
        with self.assertRaises(FileExistsError):
            transfer.assemble(self.incoming, self.dest)
        self.assertEqual(self.dest.read_bytes(), b"keep this")
        self.assertEqual(list(self.root.glob("*.incoming-*")), [])

    def test_huge_part_count_fails_on_first_missing_part(self):
        self.receive(self.lines[:1])
        manifest_path = self.incoming / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest.update(total_bytes=100000000000, chunk_bytes=1, parts=100000000000)
        manifest_path.write_text(json.dumps(manifest))
        # No list sized by parts is built, and no nonexistent later part is visited.
        (self.incoming / "part-00000001.bin").unlink()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "assemble", str(self.incoming), "--dest", str(self.dest)],
            capture_output=True, text=True, timeout=5,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing part: 1", result.stderr)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob("*.incoming-*")), [])

    def test_oversized_block_and_packet_line_are_rejected(self):
        manifest = json.loads(self.manifest.read_text())
        manifest.update(total_bytes=65537, chunk_bytes=65537, parts=1)
        other = self.root / "oversized.json"
        other.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "chunk size exceeds"):
            transfer.manifest_at(other)
        with self.assertRaisesRegex(ValueError, "chunk size must be between"):
            transfer.prepare(self.source, self.root / "oversized", 65537)
        with self.assertRaisesRegex(ValueError, "packet line too long"):
            self.receive(["x" * (2 * transfer.MAX_CHUNK_BYTES + 1)])
        other.write_bytes(b" " * 8193)
        with self.assertRaisesRegex(ValueError, "manifest exceeds"):
            transfer.manifest_at(other)

    def test_unsupported_hardlink_keeps_parts_and_does_not_publish(self):
        self.receive(self.lines)
        with mock.patch.object(transfer.os, "link", side_effect=OSError("unsupported")):
            with self.assertRaises(OSError):
                transfer.assemble(self.incoming, self.dest)
        self.assertFalse(self.dest.exists())
        self.assertEqual(list(self.root.glob("*.incoming-*")), [])
        self.assertEqual((self.incoming / "part-00000001.bin").read_bytes(), self.data[:8192])

    def test_write_flush_and_close_failures_clean_pending_file(self):
        self.receive(self.lines)
        original_open = Path.open

        for failure in ("write", "flush", "close"):
            class BrokenWriter:
                def __init__(self, raw):
                    self.raw = raw

                def __enter__(self):
                    return self

                def write(self, data):
                    if failure == "write":
                        raise OSError("write failed")
                    return self.raw.write(data)

                def flush(self):
                    if failure == "flush":
                        raise OSError("flush failed")
                    return self.raw.flush()

                def __exit__(self, *args):
                    self.raw.close()
                    if failure in ("write", "close"):
                        raise OSError("close failed")

            def injected_open(path, *args, **kwargs):
                raw = original_open(path, *args, **kwargs)
                return BrokenWriter(raw) if ".incoming-" in path.name else raw

            with self.subTest(failure=failure), mock.patch.object(Path, "open", injected_open):
                with self.assertRaises(OSError):
                    transfer.assemble(self.incoming, self.dest)
                self.assertFalse(self.dest.exists())
                self.assertEqual(list(self.root.glob("*.incoming-*")), [])

    def test_manifest_cannot_route_paths_or_mix_transfers(self):
        self.receive(self.lines[:1])
        manifest = json.loads(self.manifest.read_text())
        manifest["transfer_id"] = "0" * 32
        other = self.root / "other.json"
        other.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "another manifest"):
            transfer.receive(other, self.batch(self.lines), self.incoming)
        for name in ("../escape.bin", "..\\escape.bin", "/escape.bin"):
            manifest["name"] = name
            other.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "label, not a path"):
                transfer.manifest_at(other)

    def test_empty_file_round_trip(self):
        empty = self.root / "empty.bin"
        empty.write_bytes(b"")
        outgoing = self.root / "empty-out"
        incoming = self.root / "empty-in"
        transfer.prepare(empty, outgoing, 8192)
        transfer.receive(outgoing / "manifest.json", outgoing / "packets.jsonl", incoming)
        transfer.assemble(incoming, self.dest)
        self.assertEqual(self.dest.read_bytes(), b"")

    def test_cli_failure_is_nonzero_and_does_not_echo_private_path(self):
        private_input = self.root / "private-location" / "not-present.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "receive", "--manifest", str(private_input),
             "--input", str(private_input), "--out", str(self.incoming)],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("private-location", result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
