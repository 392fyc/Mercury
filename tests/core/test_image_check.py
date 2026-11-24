"""Offline tests for the Claude visual check: PNG facts, verdicts, budget and refusals. No paid calls."""
from contextlib import closing, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "packs/core/skills/image-api/scripts/image_check.py"
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location("mercury_image_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(width, height, pixel, color=6, filters=(0,)):
    """An 8-bit PNG; pixel(x, y) gives the channel bytes; rows cycle through the given filter types."""
    channels = {2: 3, 6: 4}[color]
    raw, prev = b"", bytes(width * channels)
    for y in range(height):
        line = b"".join(bytes(pixel(x, y)) for x in range(width))
        kind = filters[y % len(filters)]
        out = bytearray(line)
        for i in range(len(line)):
            a = line[i - channels] if i >= channels else 0
            b, c = prev[i], prev[i - channels] if i >= channels else 0
            p = a + b - c
            paeth = a if abs(p - a) <= abs(p - b) and abs(p - a) <= abs(p - c) else b if abs(p - b) <= abs(p - c) else c
            out[i] = (line[i] - (0, a, b, (a + b) // 2, paeth)[kind]) & 0xFF
        raw += bytes([kind]) + bytes(out)
        prev = line
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def emblem(x, y):
    """Transparent corners, an opaque silver centre."""
    return (200, 200, 210, 255) if 2 <= x <= 5 and 2 <= y <= 5 else (0, 0, 0, 0)


class ImageCheckTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".tmp").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / ".tmp")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text(".private/\n", encoding="utf-8")
        self.private = self.root / ".private"
        (self.private / "run").mkdir(parents=True)
        self.config, self.card = self.private / "check.json", self.private / "card.json"
        self.image = self.private / "run/image.png"
        self.cfg = {"project_root": str(self.root), "state_dir": ".private/state",
                    "anthropic_key_env": "MERCURY_CHECK_TEST_KEY", "budget_usd": "1.00",
                    "reserve_usd": "0.30", "max_calls": 2}
        self.job = {"mode": "generate", "prompt": "A silver emblem.", "size": "8x8",
                    "background": "transparent", "acceptance": ["One silver emblem", "No text"]}
        self.image.write_bytes(png(8, 8, emblem, filters=(0, 1, 2, 3, 4)))
        self.save()
        self.answer = {"items": [{"id": 1, "result": "pass", "reason": "centred"},
                                 {"id": 2, "result": "pass", "reason": "none"}]}

    def save(self):
        self.config.write_text(json.dumps(self.cfg), encoding="utf-8")
        self.card.write_text(json.dumps(self.job), encoding="utf-8")

    def response(self, answer=None, stop="end_turn"):
        return {"stop_reason": stop, "usage": {"input_tokens": 1000, "output_tokens": 500},
                "content": [{"type": "thinking", "thinking": "x"},
                            {"type": "text", "text": json.dumps(answer or self.answer)}]}

    def run_check(self, response=None, error=None, execute=True):
        with patch.dict(os.environ, {"MERCURY_CHECK_TEST_KEY": "synthetic-private-key"}), \
             patch.object(check, "request", return_value=response or self.response(), side_effect=error) as req:
            return check.run(self.config, self.card, self.image, execute), req

    def ledger(self):
        with closing(sqlite3.connect(self.private / "state/check-usage.sqlite3")) as db:
            return db.execute("SELECT micro, status FROM calls").fetchall()

    def test_png_facts_decode_every_filter_type(self):
        facts = check.png_facts(png(8, 8, emblem, filters=(0, 1, 2, 3, 4)))
        self.assertEqual(facts, {"dimensions": [8, 8], "edges_transparent": True})
        opaque = check.png_facts(png(4, 3, lambda x, y: (x * 40, y * 60, 90, 255), filters=(4, 3)))
        self.assertFalse(opaque["edges_transparent"])
        rounded = check.png_facts(png(8, 8, lambda x, y: (0, 0, 0, 0) if x in (0, 7) and y in (0, 7) else (255,) * 4))
        self.assertFalse(rounded["edges_transparent"])  # transparent corners on a white background
        rgb = check.png_facts(png(4, 4, lambda x, y: (1, 2, 3), color=2))
        self.assertEqual(rgb, {"dimensions": [4, 4], "edges_transparent": None})

    def test_corrupt_png_is_refused(self):
        data = bytearray(png(4, 4, emblem))
        data[40] ^= 0xFF
        with self.assertRaises(ValueError):
            check.png_facts(bytes(data))
        whole = png(4, 4, emblem)
        with self.assertRaisesRegex(ValueError, "IEND"):
            check.png_facts(whole[:-12])
        raw = zlib.compress(b"".join(bytes(10) for _ in range(4)))  # RGB rows one byte short
        short = whole[:33] + chunk(b"IDAT", raw) + chunk(b"IEND", b"")
        short = short[:8] + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)) + short[33:]
        with self.assertRaisesRegex(ValueError, "wrong length"):
            check.png_facts(short)
        truncated = whole[:33] + chunk(b"IDAT", zlib.compress(bytes(68))[:-6]) + chunk(b"IEND", b"")
        with self.assertRaisesRegex(ValueError, "truncated"):
            check.png_facts(truncated)
        interlaced = whole[:8] + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 1)) + whole[33:]
        with self.assertRaisesRegex(ValueError, "unsupported"):
            check.png_facts(interlaced)

    def test_an_unknown_filter_in_any_format_fails_without_a_call(self):
        data = png(4, 4, lambda x, y: (1, 2, 3), color=2)
        raw = bytearray(zlib.decompress(data[33 + 8:-12 - 4]))
        raw[13] = 7  # second row of 4 RGB pixels: 1 filter byte + 12 data bytes per row
        self.image.write_bytes(data[:33] + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))
        with self.assertRaisesRegex(ValueError, "unknown PNG filter"):
            check.png_facts(self.image.read_bytes())
        self.job.update(size="4x4", background="opaque")
        self.save()
        result, req = self.run_check()
        self.assertEqual((result["verdict"], result["mechanical"]["png"]), ("fail", False))
        req.assert_not_called()

    def test_an_oversized_image_is_refused_after_a_bounded_read(self):
        self.image.write_bytes(bytes(7 * 1024 * 1024 + 2))
        reads = []
        real_open = Path.open

        def spy(path, *args, **kwargs):
            handle = real_open(path, *args, **kwargs)
            if path == self.image:
                original = handle.read
                handle.read = lambda n=-1: reads.append(n) or original(n)
            return handle
        with patch.object(Path, "open", spy), self.assertRaisesRegex(ValueError, "too large"):
            self.run_check()
        self.assertEqual(reads, [7 * 1024 * 1024 + 1])

    def test_a_report_path_that_is_an_input_is_refused_before_anything_is_written(self):
        card = self.image.parent / "check.json"
        card.write_text(json.dumps(self.job), encoding="utf-8")
        before = card.read_bytes()
        with self.assertRaisesRegex(ValueError, "input"):
            with patch.dict(os.environ, {"MERCURY_CHECK_TEST_KEY": "synthetic-private-key"}), \
                 patch.object(check, "request", return_value=self.response()) as req:
                check.run(self.config, card, self.image, True)
        req.assert_not_called()
        self.assertEqual(card.read_bytes(), before)

    def test_unfilter_yields_rows_one_at_a_time(self):
        rows = check.unfilter(bytes([0, 1, 2]) * 999 + bytes([0, 7, 8]), 2, 1000, 1)
        self.assertEqual((next(rows), list(rows)[-1]), (bytearray([1, 2]), bytearray([7, 8])))
        empty = png(4, 4, emblem)
        empty = empty[:8] + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 0, 8, 6, 0, 0, 0)) + empty[33:]
        with self.assertRaisesRegex(ValueError, "unsupported"):
            check.png_facts(empty)

    def test_an_unreadable_png_is_a_mechanical_fail_with_a_report(self):
        self.image.write_bytes(png(8, 8, emblem)[:-12])
        result, req = self.run_check()
        self.assertEqual((result["verdict"], result["mechanical"]["png"]), ("fail", False))
        req.assert_not_called()
        self.assertTrue((self.image.parent / "check.json").exists())

    def test_transparency_that_cannot_be_verified_does_not_pass(self):
        self.image.write_bytes(png(8, 8, lambda x, y: (1, 2, 3), color=2))
        result, req = self.run_check()
        self.assertEqual((result["verdict"], result["mechanical"]["transparent_edges"]), ("fail", False))
        req.assert_not_called()

    def test_a_tracked_report_is_refused_before_any_call(self):
        report = self.image.parent / "check.json"
        report.write_text("{}", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "-f", str(report)], check=True)
        with self.assertRaisesRegex(ValueError, "Git-ignored"):
            self.run_check()

    def test_a_linked_report_is_refused_before_any_call(self):
        report = self.image.parent / "check.json"
        try:
            report.symlink_to(self.root / "elsewhere.json")
        except OSError:
            self.skipTest("symbolic links are not available")
        with self.assertRaisesRegex(ValueError, "link"):
            self.run_check()

    def test_a_hard_linked_report_is_replaced_and_its_other_name_is_untouched(self):
        other = self.private / "keep.json"
        other.write_text("keep", encoding="utf-8")
        os.link(other, self.image.parent / "check.json")
        result, _ = self.run_check()
        self.assertEqual(result["verdict"], "pass")
        self.assertEqual(other.read_text(encoding="utf-8"), "keep")
        self.assertEqual(json.loads((self.image.parent / "check.json").read_text(encoding="utf-8"))["verdict"], "pass")
        self.assertEqual(sorted(p.name for p in self.image.parent.iterdir()), ["check.json", "image.png"])

    def test_png_header_fields_and_palette_are_checked(self):
        whole = png(4, 4, lambda x, y: (1, 2, 3), color=2)
        for depth, color, compression, method in ((8, 3, 0, 0), (4, 2, 0, 0), (8, 2, 1, 0), (8, 2, 0, 1)):
            bad = whole[:8] + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, depth, color, compression, method, 0))
            with self.subTest(depth=depth, color=color), self.assertRaisesRegex(ValueError, "unsupported"):
                check.png_facts(bad + whole[33:])

    def test_preflight_reads_no_key_and_writes_nothing(self):
        result, req = self.run_check(execute=False)
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(result["mechanical"], {"png": True, "size": True, "transparent_edges": True})
        req.assert_not_called()
        self.assertFalse((self.private / "state").exists())
        self.assertFalse((self.image.parent / "check.json").exists())

    def test_pass_settles_usage_and_writes_check_next_to_image(self):
        result, req = self.run_check()
        self.assertEqual((result["status"], result["verdict"]), ("checked", "pass"))
        self.assertEqual(result["actual_usd"], 0.014)  # 1000 x $4 + 500 x $20 per million tokens
        self.assertEqual(self.ledger(), [(14000, "checked")])
        saved = (self.image.parent / "check.json").read_text(encoding="utf-8")
        self.assertNotIn("synthetic-private-key", saved)
        self.assertNotIn("A silver emblem.", saved)
        body = req.call_args.args[0]
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("A silver emblem.", json.dumps(body))
        self.assertEqual(body["messages"][0]["content"][0]["source"]["media_type"], "image/png")

    def test_any_fail_fails_and_unsure_without_fail_is_unsure(self):
        self.answer["items"][1]["result"] = "unsure"
        self.assertEqual(self.run_check()[0]["verdict"], "unsure")
        self.answer["items"][0]["result"] = "fail"
        self.assertEqual(self.run_check()[0]["verdict"], "fail")

    def test_mechanical_failure_fails_without_a_paid_call(self):
        self.job["size"] = "1024x1024"
        self.save()
        result, req = self.run_check()
        self.assertEqual((result["verdict"], result["mechanical"]["size"]), ("fail", False))
        req.assert_not_called()
        self.image.write_bytes(png(8, 8, lambda x, y: (9, 9, 9, 255)))
        self.job["size"] = "8x8"
        self.save()
        self.assertFalse(self.run_check()[0]["mechanical"]["transparent_edges"])

    def test_missing_acceptance_is_refused(self):
        del self.job["acceptance"]
        self.save()
        with self.assertRaises(ValueError):
            self.run_check()

    def test_incomplete_or_short_answers_are_unsure_but_charged(self):
        result, _ = self.run_check(self.response(stop="max_tokens"))
        self.assertEqual((result["status"], result["verdict"]), ("failed", "unsure"))
        result, _ = self.run_check(self.response({"items": self.answer["items"][:1]}))
        self.assertEqual(result["verdict"], "unsure")
        self.assertEqual(self.ledger(), [(14000, "failed"), (14000, "failed")])

    def test_a_repeated_or_unknown_criterion_id_is_unsure(self):
        self.cfg["max_calls"] = 3
        self.save()
        twice = {"items": [self.answer["items"][0], dict(self.answer["items"][0])]}
        self.assertEqual(self.run_check(self.response(twice))[0]["verdict"], "unsure")
        stray = {"items": [self.answer["items"][0], {"id": 7, "result": "pass", "reason": "x"}]}
        self.assertEqual(self.run_check(self.response(stray))[0]["verdict"], "unsure")
        swapped = {"items": list(reversed(self.answer["items"]))}
        swapped["items"][0] = {"id": 2, "result": "fail", "reason": "text"}
        result = self.run_check(self.response(swapped))[0]
        self.assertEqual([i["result"] for i in result["items"]], ["pass", "fail"])

    def test_network_failure_keeps_the_reservation_and_call_limit_holds(self):
        result, _ = self.run_check(error=OSError("down"))
        self.assertEqual((result["status"], result["accounted_usd"]), ("unknown-charge", 0.3))
        self.run_check()
        with self.assertRaises(ValueError):
            self.run_check()

    def test_unknown_result_values_are_unsure(self):
        self.answer["items"][0]["result"] = "ok"
        self.assertEqual(self.run_check()[0]["verdict"], "unsure")

    def test_a_worktree_root_is_refused(self):
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        tree = self.private / "wt"
        subprocess.run(["git", "-C", str(self.root), "worktree", "add", "-q", "--detach", str(tree)], check=True)
        (tree / ".private").mkdir()
        self.cfg["project_root"] = str(tree)
        moved = tree / ".private/check.json"
        moved.write_text(json.dumps(self.cfg), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "primary checkout"):
            check.run(moved, self.card, self.image)

    def test_a_primary_checkout_with_a_non_ascii_path_is_accepted(self):
        root = self.root / "中文 目录"
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        self.assertEqual(check.primary_root(str(root)), root.resolve())

    def test_main_exit_codes_separate_preflight_from_pass(self):
        argv = ["image_check.py", "--config", str(self.config), "--card", str(self.card), "--image", str(self.image)]
        with patch.object(sys, "argv", argv), patch("builtins.print"):
            self.assertEqual(check.main(), 4)
        with (patch.object(sys, "argv", argv + ["--execute"]), patch("builtins.print"),
              patch.dict(os.environ, {"MERCURY_CHECK_TEST_KEY": "synthetic-private-key"}),
              patch.object(check, "request", return_value=self.response())):
            self.assertEqual(check.main(), 0)

    def test_stdout_is_ascii_so_a_narrow_code_page_cannot_turn_a_paid_result_into_a_refusal(self):
        self.job["acceptance"] = ["只有一个银色徽章", "没有文字"]
        self.save()
        argv = ["image_check.py", "--config", str(self.config), "--card", str(self.card), "--image", str(self.image),
                "--execute"]
        out = io.StringIO()
        with (patch.object(sys, "argv", argv), redirect_stdout(out),
              patch.dict(os.environ, {"MERCURY_CHECK_TEST_KEY": "synthetic-private-key"}),
              patch.object(check, "request", return_value=self.response())):
            self.assertEqual(check.main(), 0)
        self.assertTrue(out.getvalue().isascii())
        self.assertEqual(json.loads(out.getvalue())["items"][0]["criterion"], "只有一个银色徽章")

    def test_a_usage_error_is_a_refusal_not_unsure_and_help_exits_0(self):
        for argv, code in ((["image_check.py", "--config", str(self.config)], 3), (["image_check.py", "--bogus"], 3),
                           (["image_check.py", "--help"], 0)):
            out = io.StringIO()
            with self.subTest(argv=argv), patch.object(sys, "argv", argv), redirect_stdout(out), \
                 patch("sys.stderr", io.StringIO()):
                self.assertEqual(check.main(), code)
            if code == 3:
                self.assertEqual(json.loads(out.getvalue())["status"], "refused")

    def test_a_key_file_named_check_json_is_never_overwritten(self):
        key_file = self.image.parent / "check.json"
        key_file.write_text("ANTHROPIC_API_KEY=synthetic-private-key\n", encoding="utf-8")
        self.cfg.pop("anthropic_key_env")
        self.cfg["anthropic_key_file"] = str(key_file)
        self.save()
        self.image.write_bytes(png(8, 8, emblem)[:-12])  # a mechanical fail would write a report without a key
        with self.assertRaisesRegex(ValueError, "input"):
            self.run_check()
        self.assertEqual(key_file.read_text(encoding="utf-8"), "ANTHROPIC_API_KEY=synthetic-private-key\n")

    def test_unknown_model_and_public_paths_are_refused(self):
        self.cfg["model"] = "claude-unknown"
        self.save()
        with self.assertRaises(ValueError):
            self.run_check(execute=False)
        self.cfg.pop("model")
        self.save()
        public = self.root / "image.png"
        public.write_bytes(self.image.read_bytes())
        with self.assertRaises(ValueError):
            check.run(self.config, self.card, public)


if __name__ == "__main__":
    unittest.main()
