"""Mercury SoT pixel-asset pipeline — module CLI entry.

Usage:

    python -m scripts.sot_pixel \\
        --asset {portrait|icon|cutin|pawn} \\
        --bible BIBLE.json \\
        --out-dir DIR [opts]

Routes single-image assets (portrait/icon/cutin) to the gpt-image-2
adapter and pawn assets to the PixelLab adapter + spritesheet packer +
SpriteFrames `.tres` generator. `--dry-run` prints planned subprocess
argv + composed prompts and creates no files (no network, no API key).

Exit codes: 0 ok / 1 generation-or-verify-fail / 2 input-or-usage.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from scripts.image_gen.character_bible import CharacterBible

from . import pawn, presets

SINGLE_IMAGE_ASSETS = ("portrait", "icon", "cutin")
_GEN_TIMEOUT = 600.0


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.sot_pixel",
        description="Mercury SoT pixel-asset pipeline (gpt-image-2 + "
                    "PixelLab + Aseprite/Godot packer).",
    )
    p.add_argument("--asset", required=True,
                   choices=["portrait", "icon", "cutin", "pawn"])
    p.add_argument("--bible", type=Path, required=True,
                   help="character bible JSON path")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="staging output directory (created if missing)")
    p.add_argument("--name", default=None,
                   help="asset base name (default: <bible.name>_<asset>)")
    p.add_argument("--scene", default="",
                   help="extra pose/scene for single-image assets")
    p.add_argument("--dry-run", action="store_true",
                   help="print planned argv + prompts, create no files")
    # single-image + pawn share --quantize (PIL for icon, Aseprite for pawn)
    p.add_argument("--quantize", action="store_true",
                   help="reduce palette (icon: PIL when aseprite absent; "
                        "pawn: aseprite palette unification)")
    # pawn-only
    p.add_argument("--size", type=int, default=64, help="pawn frame size")
    p.add_argument("--ref", type=Path, default=None,
                   help="pawn reference sprite (overrides bible ref)")
    p.add_argument("--backend", default="pixflux",
                   choices=["pixflux", "bitforge"],
                   help="pawn static-frame backend")
    p.add_argument("--view", default=pawn.DEFAULT_VIEW,
                   choices=list(pawn.VIEW_CHOICES),
                   help="pawn camera pitch; 'high top-down' (default) makes "
                        "walk_n/s/e/w four distinct facings, 'side' collapses "
                        "north/south (sidescroller framing)")
    p.add_argument("--animate-walk", action="store_true",
                   help="generate walk_* as multi-frame animate cycles")
    p.add_argument("--max-palette", type=int, default=256,
                   help="pawn verify palette ceiling (true-pixel 64x64 "
                        "frames run 70-119 colors; 256 = index-color upper "
                        "bound, still flags HD/anti-alias leakage)")
    p.add_argument("--seed", type=int, default=None, help="pawn seed")
    return p


def _safe_out(out_dir: Path, relative: str) -> Path:
    candidate = (out_dir / relative).resolve()
    root = out_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"{relative!r} resolves outside out_dir "
            f"({candidate} not under {root})"
        ) from exc
    return candidate


def _parse_size(text: str) -> tuple[int, int]:
    w, h = text.lower().split("x", 1)
    return int(w), int(h)


def _emit(report: dict) -> None:
    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


def _run_single_image(args: argparse.Namespace, bible: CharacterBible,
                      name: str) -> int:
    try:
        out_path = _safe_out(args.out_dir, f"{name}.png")
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    preset = presets.ASSET_PRESETS[args.asset]
    argv = presets.build_gpt_args(bible, args.asset, args.scene, out_path)
    prompt = presets.compose_asset_prompt(bible, args.asset, args.scene)

    if args.dry_run:
        _emit({
            "asset": args.asset, "name": name, "dry_run": True,
            "size": preset.size, "quality": preset.quality,
            "background": preset.background, "prompt": prompt,
            "planned_command": argv, "out_path": str(out_path),
        })
        return 0

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"error: cannot create out-dir {args.out_dir}: {exc}\n")
        return 2
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_GEN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"error: gpt-image-2 adapter could not run: {exc}\n")
        return 1
    if proc.returncode != 0:
        sys.stderr.write(
            f"error: gpt-image-2 adapter exited {proc.returncode}: "
            f"{proc.stderr.strip()[:500]}\n"
        )
        return 1
    if not out_path.exists():
        sys.stderr.write(f"error: adapter reported success but {out_path} "
                         "is missing\n")
        return 1

    # Verify dimensions only — HD assets are not palette-gated. Pillow is a
    # hard dependency for the single-image lane's verify/quantize; guard the
    # import so a missing install surfaces the adapter's 0/1/2 error contract
    # (exit 1 + guidance) instead of a raw ImportError traceback.
    try:
        from PIL import Image
    except ImportError:
        sys.stderr.write("error: Pillow is required for single-image "
                         "verify/quantize (pip install Pillow)\n")
        return 1
    with Image.open(out_path) as img:
        actual = img.size
    expected = _parse_size(preset.size)
    dims_ok = actual == expected

    # icon --quantize uses PIL's quantizer directly. The single-image lane
    # has no Aseprite integration (Aseprite is pawn-lane-only), so this runs
    # regardless of whether an aseprite binary is on PATH.
    quantized = False
    if args.asset == "icon" and args.quantize:
        with Image.open(out_path) as img:
            img.convert("RGB").quantize(colors=32).save(out_path)
        quantized = True

    _emit({
        "asset": args.asset, "name": name, "dry_run": False,
        "passed": dims_ok, "out_path": str(out_path),
        "size": {"expected": list(expected), "actual": list(actual)},
        "dimensions_ok": dims_ok, "quantized": quantized,
    })
    return 0 if dims_ok else 1


def _run_pawn(args: argparse.Namespace, bible: CharacterBible,
              name: str) -> int:
    try:
        report = pawn.generate_pawn(
            bible, args.out_dir, name=name, size=args.size, ref=args.ref,
            backend=args.backend, view=args.view,
            animate_walk=args.animate_walk,
            quantize=args.quantize, max_palette=args.max_palette,
            dry_run=args.dry_run, seed=args.seed,
        )
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except (RuntimeError, OSError) as exc:
        sys.stderr.write(f"error: pawn generation failed: {exc}\n")
        return 1
    _emit(report)
    if args.dry_run:
        return 0
    return 0 if report.get("passed", True) else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        bible = CharacterBible.load(args.bible)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"error: failed to load bible {args.bible}: {exc}\n")
        return 2
    name = args.name or f"{bible.name}_{args.asset}"
    # Enforce a flat file stem: _safe_out blocks escaping out_dir, but a name
    # with a path separator would still create nested outputs that diverge
    # from the documented <name>_sheet.png / <name>.tres contract.
    if "/" in name or "\\" in name or name in (".", ".."):
        sys.stderr.write(
            f"error: --name must be a flat file stem with no path "
            f"separators, got {name!r}\n"
        )
        return 2
    if args.asset == "pawn" and (args.size <= 0 or args.max_palette <= 0):
        sys.stderr.write(
            "error: --size and --max-palette must be positive integers\n"
        )
        return 2
    if args.asset in SINGLE_IMAGE_ASSETS:
        return _run_single_image(args, bible, name)
    return _run_pawn(args, bible, name)


if __name__ == "__main__":
    sys.exit(main())
