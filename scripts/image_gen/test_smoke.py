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


def main() -> int:
    test_help()
    test_anchor_block_determinism()
    test_dry_run_e2e()
    test_verify_synthetic()
    test_pipeline_command_shape()
    test_soft_gate_advisory()
    test_palette_union_field()
    test_scenes_validation()
    print("ALL SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
