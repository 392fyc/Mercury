"""Module smoke tests — pure-function + dry-run paths, no network.

Run from repo root:

    python -m scripts.sot_pixel.test_smoke

Covers `--help`, per-asset dry-run (side-effect free), the pure-Python
packer, the SpriteFrames `.tres` generator, gpt-image-2 preset argv, the
Aseprite graceful-skip, and the pawn dry-run plan. Never touches the
network and never requires Aseprite.
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
    "style": "2D pixel art, 64x64 tile",
    "lighting": "soft front, no hard shadows",
    "camera": "front, full body",
    "constraints": ["no text", "no watermarks"],
}


def _write_bible(tmp: Path) -> Path:
    path = tmp / "bible.json"
    path.write_text(json.dumps(BIBLE), encoding="utf-8")
    return path


def test_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.sot_pixel", "--help"],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "SoT pixel-asset pipeline" in proc.stdout
    assert "--asset" in proc.stdout and "--bible" in proc.stdout
    print("PASS test_help")


def test_dry_run_each_asset() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bible_path = _write_bible(tmp_path)
        for asset in ("portrait", "icon", "cutin", "pawn"):
            out_dir = tmp_path / f"out_{asset}"
            proc = subprocess.run(
                [sys.executable, "-m", "scripts.sot_pixel",
                 "--asset", asset, "--bible", str(bible_path),
                 "--out-dir", str(out_dir), "--dry-run"],
                capture_output=True, text=True, cwd=ROOT,
            )
            assert proc.returncode == 0, f"{asset}: {proc.stderr}"
            assert not out_dir.exists(), f"{asset} dry-run created files"
            report = json.loads(proc.stdout)
            assert report["dry_run"] is True
    print("PASS test_dry_run_each_asset")


def test_cutin_dry_run_negative_suffix() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bible_path = _write_bible(tmp_path)
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.sot_pixel",
             "--asset", "cutin", "--bible", str(bible_path),
             "--out-dir", str(tmp_path / "out"), "--dry-run"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        assert "no UI, no text" in proc.stdout
        assert "1024x1536" in proc.stdout
    print("PASS test_cutin_dry_run_negative_suffix")


def test_pack_frames() -> None:
    try:
        from PIL import Image
    except ImportError:
        print("SKIP test_pack_frames (Pillow not installed)")
        return
    from scripts.sot_pixel.pack import pack_frames
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        groups: dict[str, list[Path]] = {}
        for anim, color in [("idle", (10, 20, 30)), ("walk_south", (40, 50, 60))]:
            paths = []
            for i in range(2):
                p = tmp_path / f"{anim}_{i}.png"
                Image.new("RGBA", (8, 8), color + (255,)).save(p)
                paths.append(p)
            groups[anim] = paths
        out_sheet = tmp_path / "knight_sheet.png"
        data = pack_frames(groups, out_sheet, fps=8.0)
        # one row per anim, 2 cols -> 16x16 sheet
        with Image.open(out_sheet) as sheet:
            assert sheet.size == (16, 16), sheet.size
        assert out_sheet.with_suffix(".json").exists()
        tags = {t["name"]: (t["from"], t["to"]) for t in data["meta"]["frameTags"]}
        assert tags["idle"] == (0, 1), tags
        assert tags["walk_south"] == (2, 3), tags
        assert data["meta"]["image"] == "knight_sheet.png"
        # second row frame y-offset
        assert data["frames"]["walk_south_0"]["frame"]["y"] == 8
    print("PASS test_pack_frames")


def _synthetic_aseprite_json(anims: list[str], w: int = 64, h: int = 64,
                             fps: float = 8.0) -> dict:
    frames: dict[str, dict] = {}
    tags: list[dict] = []
    dur = int(1000 / fps)
    gi = 0
    for row, anim in enumerate(anims):
        start = gi
        frames[f"{anim}_0"] = {
            "frame": {"x": 0, "y": row * h, "w": w, "h": h},
            "sourceSize": {"w": w, "h": h},
            "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h},
            "duration": dur, "rotated": False, "trimmed": False,
        }
        gi += 1
        tags.append({"name": anim, "from": start, "to": gi - 1,
                     "direction": "forward"})
    return {
        "frames": frames,
        "meta": {"size": {"w": w, "h": len(anims) * h}, "image": "x_sheet.png",
                 "format": "RGBA8888", "scale": "1", "frameTags": tags},
    }


def test_build_spriteframes_tres() -> None:
    from scripts.sot_pixel.godot_import import (
        build_spriteframes_tres, make_pawn_tres,
    )
    anims = ["idle", "walk_south", "walk_north", "walk_east", "walk_west",
             "hurt", "death"]
    data = _synthetic_aseprite_json(anims)
    tres = build_spriteframes_tres("res://x_sheet.png", data, fps_map=8.0)

    assert tres.startswith('[gd_resource type="SpriteFrames"'), tres[:80]
    assert "format=3]" in tres
    assert f"load_steps={1 + len(anims) + 1}" in tres, tres[:80]
    assert 'ext_resource type="Texture2D"' in tres
    assert '[sub_resource type="AtlasTexture" id="At_0"]' in tres
    # walk_north is row index 2 -> y=128
    assert "region = Rect2(0, 128, 64, 64)" in tres, tres
    for anim in anims:
        assert f'&"{anim}"' in tres, anim
    # loop semantics: death false, idle true
    death_block = tres[tres.index('&"death"') - 120:tres.index('&"death"')]
    assert '"loop": false' in death_block, death_block
    idle_block = tres[tres.index('&"idle"') - 120:tres.index('&"idle"')]
    assert '"loop": true' in idle_block, idle_block
    # speed is emitted as a bare float literal (not a quoted string)
    assert '"speed": 8.0' in tres
    assert '"speed": "8.0"' not in tres

    # make_pawn_tres applies the same SoT loop defaults.
    pawn_tres = make_pawn_tres("res://x_sheet.png", data, fps=8.0)
    assert '"name": &"death"' in pawn_tres
    print("PASS test_build_spriteframes_tres")


def test_presets_build_gpt_args() -> None:
    from scripts.image_gen.character_bible import CharacterBible
    from scripts.sot_pixel.presets import build_gpt_args, compose_asset_prompt
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # bible with two reference images
        ref1 = tmp_path / "ref1.png"
        ref2 = tmp_path / "ref2.png"
        ref1.write_bytes(b"x")
        ref2.write_bytes(b"x")
        bible_data = dict(BIBLE)
        bible_data["reference_images"] = [ref1.name, ref2.name]
        bible_path = tmp_path / "bible.json"
        bible_path.write_text(json.dumps(bible_data), encoding="utf-8")
        bible = CharacterBible.load(bible_path)

        cutin_prompt = compose_asset_prompt(bible, "cutin", "leaping strike")
        assert "no UI, no text" in cutin_prompt
        assert "no health bars" in cutin_prompt

        argv = build_gpt_args(bible, "cutin", "leaping strike",
                              tmp_path / "out.png")
        assert argv.count("-i") == 2, argv
        assert "--model" in argv and "gpt-image-2" in argv
        assert "--background" in argv and "opaque" in argv
        assert "1024x1536" in argv
        # cut-in negative suffix must reach the -p prompt argument
        prompt_arg = argv[argv.index("-p") + 1]
        assert "no UI, no text" in prompt_arg

        portrait_argv = build_gpt_args(bible, "portrait", "", tmp_path / "p.png")
        assert "1024x1536" in portrait_argv
        icon_argv = build_gpt_args(bible, "icon", "", tmp_path / "i.png")
        assert "1024x1024" in icon_argv
    print("PASS test_presets_build_gpt_args")


def test_aseprite_graceful_skip() -> None:
    from scripts.sot_pixel import postprocess
    # This test exercises the aseprite-ABSENT graceful-skip path. A dev/CI
    # machine may legitimately have aseprite/LibreSprite installed, so skip
    # rather than hard-fail — the suite must never *require* a given binary
    # state in either direction.
    if postprocess.aseprite_available():
        print("SKIP test_aseprite_graceful_skip (aseprite present)")
        return
    try:
        from PIL import Image
    except ImportError:
        print("SKIP test_aseprite_graceful_skip (Pillow not installed)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        p = tmp_path / "idle_0.png"
        Image.new("RGBA", (8, 8), (1, 2, 3, 255)).save(p)
        result = postprocess.quantize_and_pack({"idle": [p]},
                                               tmp_path / "sheet.png")
        assert result is None, "must return None (not raise) when aseprite absent"
    print("PASS test_aseprite_graceful_skip")


def test_pawn_dry_run_plan() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        bible_path = _write_bible(tmp_path)
        ref = tmp_path / "knight_ref.png"
        ref.write_bytes(b"x")
        out_dir = tmp_path / "out"
        proc = subprocess.run(
            [sys.executable, "-m", "scripts.sot_pixel",
             "--asset", "pawn", "--bible", str(bible_path),
             "--out-dir", str(out_dir), "--ref", str(ref), "--dry-run"],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        assert not out_dir.exists(), "pawn dry-run created files"
        report = json.loads(proc.stdout)
        assert report["dry_run"] is True
        # SoT animation set present
        for anim in ["idle", "walk_south", "walk_north", "walk_east",
                     "walk_west", "hurt", "death"]:
            assert anim in report["animations"], anim
        assert report["frame_size"] == "64x64"
        # init-image threaded into the planned argv
        flat = json.dumps(report["planned_commands"])
        assert "--init-image" in flat
        assert "--width" in flat and "64" in flat
        assert "knight_ref.png" in flat
    print("PASS test_pawn_dry_run_plan")


def main() -> int:
    test_help()
    test_dry_run_each_asset()
    test_cutin_dry_run_negative_suffix()
    test_pack_frames()
    test_build_spriteframes_tres()
    test_presets_build_gpt_args()
    test_aseprite_graceful_skip()
    test_pawn_dry_run_plan()
    print("ALL SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
