"""Module smoke tests — exercise pure-python paths without network.

Run from repo root:

    python -m scripts.image_gen.test_smoke

Covers `--help`, bible JSON load, anchor-block determinism, dry-run
prompt composition, and verify rubric on a synthetic 4-frame PNG set
(skipped when Pillow is unavailable).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

BIBLE = {
    "name": "knight",
    "identity": ["same green tunic", "same round shield", "same brown boots"],
    "color_palette": ["#3A86FF", "#FF006E", "#FFBE0B"],
    "style": "2D pixel art, 32x32 tile",
    "lighting": "soft front, no hard shadows",
    "camera": "front, full body",
    "constraints": ["no text", "no watermarks"],
}

SCENES = [
    {"index": 0, "scene": "frame 1 of 4 walk cycle, left foot forward", "filename": "f0.png"},
    {"index": 1, "scene": "frame 2 of 4 walk cycle, both feet down", "filename": "f1.png"},
    {"index": 2, "scene": "frame 3 of 4 walk cycle, right foot forward", "filename": "f2.png"},
    {"index": 3, "scene": "frame 4 of 4 walk cycle, both feet down", "filename": "f3.png"},
]


def test_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.image_gen", "--help"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Mercury sprite frame pipeline" in proc.stdout
    assert "--bible" in proc.stdout and "--scenes" in proc.stdout
    print("PASS test_help")


def test_anchor_block_determinism() -> None:
    from scripts.image_gen.character_bible import CharacterBible
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "bible.json"
        path.write_text(json.dumps(BIBLE), encoding="utf-8")
        b1 = CharacterBible.load(path)
        b2 = CharacterBible.load(path)
        a1, a2 = b1.anchor_block(), b2.anchor_block()
        assert a1 == a2, "anchor block must be deterministic"
        assert "same green tunic" in a1
        assert "[no text, no watermarks]" in a1
    print("PASS test_anchor_block_determinism")


def test_dry_run_e2e() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bible_path = tmp_path / "bible.json"
        scenes_path = tmp_path / "scenes.json"
        out_dir = tmp_path / "out"
        bible_path.write_text(json.dumps(BIBLE), encoding="utf-8")
        scenes_path.write_text(json.dumps(SCENES), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.image_gen",
             "--bible", str(bible_path),
             "--scenes", str(scenes_path),
             "--out-dir", str(out_dir),
             "--dry-run"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        for scene in SCENES:
            assert scene["scene"] in proc.stdout, f"missing scene {scene['index']}"
        assert proc.stdout.count("Character Consistency:") == len(SCENES)
        assert not out_dir.exists(), "dry-run must be side-effect free"
    print("PASS test_dry_run_e2e")


def test_verify_synthetic() -> None:
    try:
        from PIL import Image
    except ImportError:
        print("SKIP test_verify_synthetic (Pillow not installed)")
        return
    from scripts.image_gen.verify import VerifyConfig, verify_frames
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        frames: list[Path] = []
        for i in range(4):
            img = Image.new("RGB", (32, 32), color=(58, 134, 255))
            p = tmp_path / f"f{i}.png"
            img.save(p)
            frames.append(p)
        cfg = VerifyConfig(expected_count=4, max_palette_size=64)
        result = verify_frames(frames, cfg)
        assert result.passed, result.fail_reasons

        cfg_bad = VerifyConfig(expected_count=5)
        result_bad = verify_frames(frames, cfg_bad)
        assert not result_bad.passed
        assert any("frame_count" in r for r in result_bad.fail_reasons)
    print("PASS test_verify_synthetic")


def test_pipeline_command_shape() -> None:
    from scripts.image_gen.pipeline import GenerationOptions, build_command
    cmd = build_command(
        prompt="hello",
        out_path=Path("/tmp/x.png"),
        references=[Path("/tmp/ref1.png"), Path("/tmp/ref2.png")],
        opts=GenerationOptions(model="gpt-image-2", size="1024x1024",
                               quality="high", output_format="png",
                               background="opaque"),
    )
    assert "-p" in cmd and "hello" in cmd
    assert "-f" in cmd
    assert cmd.count("-i") == 2
    assert "--background" in cmd and "opaque" in cmd
    assert "--model" in cmd and "gpt-image-2" in cmd
    print("PASS test_pipeline_command_shape")


def test_soft_gate_advisory() -> None:
    """Soft-gate fail must NOT flip VerifyResult.passed (Codex High #1)."""
    try:
        from PIL import Image
        import imagehash  # noqa: F401
    except ImportError:
        print("SKIP test_soft_gate_advisory (Pillow + imagehash required)")
        return
    from scripts.image_gen.verify import VerifyConfig, verify_frames
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        frames: list[Path] = []
        # 2 frames with very different content -> dHash distance > threshold
        for i, color in enumerate([(0, 0, 0), (255, 255, 255)]):
            img = Image.new("RGB", (32, 32), color=color)
            for y in range(0, 32, 4):
                for x in range((y % 8) * 2, 32, 8):
                    img.putpixel((x, y), (i * 200, 100, 200 - i * 100))
            p = tmp_path / f"f{i}.png"
            img.save(p)
            frames.append(p)
        cfg = VerifyConfig(expected_count=2, max_palette_size=512,
                           dhash_threshold=0)  # impossibly tight soft threshold
        result = verify_frames(frames, cfg)
        assert result.passed, (
            "soft-gate failure must not block passed; "
            f"fail_reasons={result.fail_reasons} advisories={result.advisories}"
        )
        # advisory should mention character_consistency
        assert any("character_consistency" in a for a in result.advisories), \
            f"expected character_consistency advisory, got {result.advisories}"
    print("PASS test_soft_gate_advisory")


def test_palette_union_field() -> None:
    """Palette gate detail must expose union_size + per_frame_sizes (Codex Medium #1)."""
    try:
        from PIL import Image
    except ImportError:
        print("SKIP test_palette_union_field (Pillow required)")
        return
    from scripts.image_gen.verify import VerifyConfig, verify_frames
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        frames: list[Path] = []
        for i in range(2):
            img = Image.new("RGB", (4, 4), color=(i * 80, 0, 0))
            p = tmp_path / f"f{i}.png"
            img.save(p)
            frames.append(p)
        cfg = VerifyConfig(expected_count=2, max_palette_size=64)
        result = verify_frames(frames, cfg)
        palette_gate = next(g for g in result.gates if g.name == "palette_quantization")
        assert "union_size" in palette_gate.detail, palette_gate.detail
        assert "per_frame_sizes" in palette_gate.detail, palette_gate.detail
        assert palette_gate.detail["union_size"] >= 2
    print("PASS test_palette_union_field")


def test_scenes_validation() -> None:
    """_load_scenes must reject empty list, non-int index, non-str scene (Codex Medium #2)."""
    from scripts.image_gen.__main__ import _load_scenes
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for label, payload in [
            ("empty array", "[]"),
            ("non-array", '{"index": 0}'),
            ("non-int index", '[{"index": "0", "scene": "x"}]'),
            ("non-str scene", '[{"index": 0, "scene": 5}]'),
            ("empty filename", '[{"index": 0, "scene": "x", "filename": ""}]'),
        ]:
            p = tmp_path / "scenes.json"
            p.write_text(payload, encoding="utf-8")
            try:
                _load_scenes(p, tmp_path / "out")
            except ValueError:
                continue
            raise AssertionError(f"{label} should have raised ValueError")
    print("PASS test_scenes_validation")


def test_path_traversal_rejected() -> None:
    """Filename with `..` or absolute path must not escape out_dir (Argus iter-1 路径穿越)."""
    from scripts.image_gen.__main__ import _load_scenes
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        scenes_path = tmp_path / "scenes.json"
        for label, fname in [
            ("dotdot escape", "../escaped.png"),
            ("nested dotdot", "subdir/../../escaped.png"),
            ("absolute path", str(tmp_path / "outside.png")),
        ]:
            scenes_path.write_text(
                json.dumps([{"index": 0, "scene": "x", "filename": fname}]),
                encoding="utf-8",
            )
            try:
                _load_scenes(scenes_path, out_dir)
            except ValueError:
                continue
            raise AssertionError(f"{label} ({fname!r}) should have raised ValueError")

        # Sanity: a safe nested path must succeed
        scenes_path.write_text(
            json.dumps([{"index": 0, "scene": "x", "filename": "sub/safe.png"}]),
            encoding="utf-8",
        )
        specs = _load_scenes(scenes_path, out_dir)
        assert len(specs) == 1
        assert "safe.png" in str(specs[0].out_path)
    print("PASS test_path_traversal_rejected")


def test_bible_type_validation() -> None:
    """Bible loader must reject non-str list elements (Argus iter-2 类型校验缺失)."""
    from scripts.image_gen.character_bible import CharacterBible
    with tempfile.TemporaryDirectory() as tmp:
        bible_path = Path(tmp) / "bible.json"
        for label, payload in [
            ("non-str identity", {"name": "x", "identity": ["ok", 5]}),
            ("non-str palette", {"name": "x", "color_palette": [None, 7]}),
            ("non-str constraints", {"name": "x", "constraints": [{"k": "v"}]}),
            ("non-str style", {"name": "x", "style": 5}),
            ("non-str lighting", {"name": "x", "lighting": []}),
        ]:
            bible_path.write_text(json.dumps(payload), encoding="utf-8")
            try:
                CharacterBible.load(bible_path)
            except ValueError:
                continue
            raise AssertionError(f"{label} should have raised ValueError")
    print("PASS test_bible_type_validation")


def test_duplicate_scenes_rejected() -> None:
    """_load_scenes must reject duplicate index AND duplicate output path (Argus iter-2 文件覆盖)."""
    from scripts.image_gen.__main__ import _load_scenes
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        scenes_path = tmp_path / "scenes.json"
        # Duplicate index
        scenes_path.write_text(json.dumps([
            {"index": 0, "scene": "a"}, {"index": 0, "scene": "b"},
        ]), encoding="utf-8")
        try:
            _load_scenes(scenes_path, out_dir)
            raise AssertionError("duplicate index should raise")
        except ValueError:
            pass
        # Duplicate filename (different indexes)
        scenes_path.write_text(json.dumps([
            {"index": 0, "scene": "a", "filename": "shared.png"},
            {"index": 1, "scene": "b", "filename": "shared.png"},
        ]), encoding="utf-8")
        try:
            _load_scenes(scenes_path, out_dir)
            raise AssertionError("duplicate filename should raise")
        except ValueError:
            pass
    print("PASS test_duplicate_scenes_rejected")


def test_stderr_sanitization() -> None:
    """_sanitize_stderr must scrub credential-shaped substrings (Argus iter-2 prompt injection)."""
    from scripts.image_gen.retry_loop import _sanitize_stderr
    samples = [
        "401 Unauthorized: invalid sk-proj-AbCdEf1234567890XYZ",
        "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "Authorization: Token ghp_abcdefghij1234567890",
        "AKIAIOSFODNN7EXAMPLE failed",
    ]
    for s in samples:
        sanitized = _sanitize_stderr(s)
        assert "sk-proj-AbCdEf" not in sanitized, sanitized
        assert "eyJhbGciOiJIUzI1" not in sanitized, sanitized
        assert "ghp_abcdefghij" not in sanitized, sanitized
        assert "AKIAIOSFODNN7" not in sanitized, sanitized
    # Truncation
    long = "error: " + ("X" * 500)
    truncated = _sanitize_stderr(long)
    assert len(truncated) <= 200, len(truncated)
    print("PASS test_stderr_sanitization")


def test_sanitize_stderr_full() -> None:
    """sanitize_stderr_full preserves multi-line, scrubs credentials, tail-truncates within budget (Copilot iter-2 #1+#3)."""
    from scripts.image_gen.retry_loop import sanitize_stderr_full
    # Multi-line preservation + extended key=value scrubbing
    multi = (
        "Traceback (most recent call last):\n"
        "  File \"adapter.py\", line 42, in invoke\n"
        "    raise APIError(\"401 Unauthorized\")\n"
        "APIError: 401 Unauthorized\n"
        "ENV: OPENAI_API_KEY=sk-proj-LEAKED_8675309 (debug)\n"
    )
    out = sanitize_stderr_full(multi)
    assert "Traceback" in out, "first line preserved"
    assert "APIError: 401 Unauthorized" in out, "middle line preserved"
    assert "sk-proj-LEAKED" not in out, "credential leaked"
    assert "OPENAI_API_KEY=***" in out, "key=value redaction marker present"

    # Tail-truncate within budget (Copilot iter-2 #1: marker must be
    # inside max_chars, not on top of it).
    big = "PREFIX line\n" + ("filler line\n" * 500) + "TAIL: critical error here"
    truncated = sanitize_stderr_full(big, max_chars=200)
    assert "TAIL: critical error" in truncated, "tail must be preserved"
    assert "[truncated" in truncated, "marker missing"
    assert len(truncated) <= 200, f"over budget: {len(truncated)} > 200"

    # Empty input
    assert sanitize_stderr_full("") == "unknown error"

    # Below threshold returns content unchanged (no marker)
    short = "single short stderr line"
    assert sanitize_stderr_full(short, max_chars=2000) == short
    print("PASS test_sanitize_stderr_full")


def test_verify_short_circuit_empty() -> None:
    """verify_frames must short-circuit on empty list (Copilot verify cascade)."""
    from scripts.image_gen.verify import VerifyConfig, verify_frames
    cfg = VerifyConfig(expected_count=4)
    result = verify_frames([], cfg)
    assert not result.passed
    assert len(result.gates) == 1, f"expected single gate, got {len(result.gates)}"
    assert result.gates[0].name == "frame_count"
    assert any("frame_count" in r for r in result.fail_reasons)
    print("PASS test_verify_short_circuit_empty")


def test_retry_budget_semantics() -> None:
    """run_with_retry must run 1 + max_retries total attempts on failure (ADR §6.4)."""
    from scripts.image_gen.character_bible import CharacterBible
    from scripts.image_gen.pipeline import FrameSpec, GenerationOptions
    from scripts.image_gen.retry_loop import run_with_retry
    from scripts.image_gen.verify import VerifyConfig
    import scripts.image_gen.retry_loop as rl
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bible_path = tmp_path / "bible.json"
        bible_path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
        bible = CharacterBible.load(bible_path)

        call_count = [0]
        def fake_generate(*_args, **_kwargs):
            call_count[0] += 1
            from scripts.image_gen.pipeline import FrameResult
            return [FrameResult(spec=FrameSpec(0, "x", tmp_path / "x.png"),
                                returncode=1, stderr="mock fail",
                                prompt="", out_path=None)]

        original = rl.generate_frames
        rl.generate_frames = fake_generate
        try:
            cfg = VerifyConfig(expected_count=1)
            opts = GenerationOptions()
            report = run_with_retry(bible, [FrameSpec(0, "x", tmp_path / "x.png")],
                                    cfg, opts=opts, max_retries=3)
            assert not report.passed
            assert call_count[0] == 4, (
                f"expected 1+3=4 attempts on failure, got {call_count[0]}"
            )
            # max_retries=0 → 1 attempt
            call_count[0] = 0
            report0 = run_with_retry(bible, [FrameSpec(0, "x", tmp_path / "x.png")],
                                     cfg, opts=opts, max_retries=0)
            assert call_count[0] == 1, (
                f"expected 1 attempt with max_retries=0, got {call_count[0]}"
            )
        finally:
            rl.generate_frames = original
    print("PASS test_retry_budget_semantics")


def test_adapter_path_env_override() -> None:
    """MERCURY_GPT_IMAGE_2_ADAPTER must override DEFAULT_ADAPTER_PATH (Argus iter-1 模块耦合)."""
    import os
    from scripts.image_gen.pipeline import (
        ADAPTER_PATH_ENV, DEFAULT_ADAPTER_PATH, resolve_adapter_path,
    )
    saved = os.environ.get(ADAPTER_PATH_ENV)
    try:
        # Default
        if ADAPTER_PATH_ENV in os.environ:
            del os.environ[ADAPTER_PATH_ENV]
        assert resolve_adapter_path() == DEFAULT_ADAPTER_PATH
        # Override
        with tempfile.TemporaryDirectory() as tmp:
            override = Path(tmp) / "custom_invoke.py"
            override.write_text("# stub")
            os.environ[ADAPTER_PATH_ENV] = str(override)
            resolved = resolve_adapter_path()
            assert resolved == override.resolve(), (
                f"expected env override {override.resolve()}, got {resolved}"
            )
    finally:
        if saved is None:
            os.environ.pop(ADAPTER_PATH_ENV, None)
        else:
            os.environ[ADAPTER_PATH_ENV] = saved
    print("PASS test_adapter_path_env_override")


def main() -> int:
    test_help()
    test_anchor_block_determinism()
    test_dry_run_e2e()
    test_verify_synthetic()
    test_pipeline_command_shape()
    test_soft_gate_advisory()
    test_palette_union_field()
    test_scenes_validation()
    test_path_traversal_rejected()
    test_adapter_path_env_override()
    test_bible_type_validation()
    test_duplicate_scenes_rejected()
    test_stderr_sanitization()
    test_sanitize_stderr_full()
    test_verify_short_circuit_empty()
    test_retry_budget_semantics()
    print("ALL SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
