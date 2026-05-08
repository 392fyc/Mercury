"""Mercury image generation pipeline — module CLI entry.

Usage:

    python -m scripts.image_gen \\
        --bible char.json \\
        --scenes scenes.json \\
        --out-dir out/

Scenes JSON is a list of objects: `{"index": 0, "scene": "...", "filename": "frame_00.png"}`.
Use `--dry-run` to preview composed prompts without invoking the adapter
(no API key required for `--help` or `--dry-run`).
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from .character_bible import CharacterBible
from .pipeline import FrameSpec, GenerationOptions
from .retry_loop import run_with_retry
from .verify import VerifyConfig

SIZE_CHOICES_HELP = "e.g. 1024x1024 (forwarded to upstream gpt-image)"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.image_gen",
        description="Mercury sprite frame pipeline (gpt-image-2 adapter + "
                    "verify rubric + retry loop)",
    )
    p.add_argument("--bible", type=Path, required=True,
                   help="character bible JSON path")
    p.add_argument("--scenes", type=Path, required=True,
                   help="scenes JSON path: list of {index, scene, filename}")
    p.add_argument("--out-dir", type=Path, required=True,
                   help="frame output directory (created if missing)")
    p.add_argument("--base-image", type=Path, default=None,
                   help="extra reference image appended to bible refs")
    p.add_argument("--model", default="gpt-image-2",
                   help="upstream --model (default: gpt-image-2)")
    p.add_argument("--size", default="1024x1024", help=SIZE_CHOICES_HELP)
    p.add_argument("--quality", default="high",
                   choices=["auto", "low", "medium", "high"])
    p.add_argument("--format", dest="output_format", default="png",
                   choices=["png", "jpeg", "webp"])
    p.add_argument("--background", default=None, choices=["auto", "opaque"],
                   help="gpt-image-2 does not support transparent")
    p.add_argument("--timeout", type=float, default=300.0,
                   help="per-frame adapter timeout, seconds")
    p.add_argument("--max-retries", type=int, default=3,
                   help="hard cap per ADR §6.4 (default 3)")
    p.add_argument("--max-palette", type=int, default=64,
                   help="verify.palette_quantization upper bound")
    p.add_argument("--dhash-threshold", type=int, default=12,
                   help="verify.character_consistency dHash distance threshold")
    p.add_argument("--ssim-threshold", type=float, default=0.6,
                   help="verify.loop_closure SSIM threshold")
    p.add_argument("--loop-closure", action="store_true",
                   help="enable verify.loop_closure soft gate (default off)")
    p.add_argument("--reference-size", default=None,
                   help="expected WxH for verify.dimension_uniformity")
    p.add_argument("--dry-run", action="store_true",
                   help="print composed prompts and exit (no adapter call)")
    return p


def _parse_size(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    if "x" not in text.lower():
        raise ValueError(f"--reference-size must be WxH, got {text!r}")
    w, h = text.lower().split("x", 1)
    return (int(w), int(h))


def _load_scenes(path: Path, out_dir: Path) -> list[FrameSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"scenes file {path} must be a JSON array")
    specs: list[FrameSpec] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"scenes[{i}] must be an object, got {type(item).__name__}")
        idx = item.get("index", i)
        scene = item.get("scene", "")
        fname = item.get("filename") or f"frame_{idx:02d}.png"
        specs.append(FrameSpec(index=idx, scene=scene, out_path=out_dir / fname))
    return specs


def _serialize(report) -> dict:
    return {
        "passed": report.passed,
        "total_attempts": report.total_attempts,
        "final_fail_reasons": list(report.final_fail_reasons),
        "attempts": [
            {
                "attempt": a.attempt,
                "passed": a.passed,
                "feedback_used": a.feedback_used,
                "frame_results": [
                    {
                        "index": r.spec.index,
                        "success": r.success,
                        "returncode": r.returncode,
                        "out_path": str(r.out_path) if r.out_path else None,
                    }
                    for r in a.frame_results
                ],
                "verify": {
                    "passed": a.verify.passed,
                    "fail_reasons": a.verify.fail_reasons,
                    "gates": [asdict(g) for g in a.verify.gates],
                },
            }
            for a in report.attempts
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    bible = CharacterBible.load(args.bible)
    frames = _load_scenes(args.scenes, args.out_dir)

    if args.dry_run:
        for f in frames:
            sys.stdout.write(
                f"--- frame {f.index} -> {f.out_path}\n"
                f"{bible.compose_prompt(f.scene)}\n\n"
            )
        return 0

    args.out_dir.mkdir(parents=True, exist_ok=True)

    opts = GenerationOptions(
        model=args.model, size=args.size, quality=args.quality,
        output_format=args.output_format, background=args.background,
        timeout=args.timeout, base_image=args.base_image,
    )
    cfg = VerifyConfig(
        expected_count=len(frames),
        reference_size=_parse_size(args.reference_size),
        max_palette_size=args.max_palette,
        dhash_threshold=args.dhash_threshold,
        ssim_threshold=args.ssim_threshold,
        require_loop_closure=args.loop_closure,
    )
    report = run_with_retry(bible, frames, cfg, opts=opts,
                            max_retries=args.max_retries)
    json.dump(_serialize(report), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
