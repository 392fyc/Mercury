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
from .retry_loop import run_with_retry, sanitize_stderr
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
    p.add_argument("--allow-skipped-gates", action="store_true",
                   help="opt-in: run verify even when hard-gate deps "
                        "(Pillow, imagehash, scikit-image) are missing — "
                        "skipped gates would otherwise hard-fail in non-dry-run mode")
    return p


def _parse_size(text: str | None) -> tuple[int, int] | None:
    if not text:
        return None
    if "x" not in text.lower():
        raise ValueError(f"--reference-size must be WxH, got {text!r}")
    w, h = text.lower().split("x", 1)
    return (int(w), int(h))


def _resolve_safe_out_path(out_dir: Path, filename: str, idx: int) -> Path:
    """Resolve `out_dir / filename` and verify it stays inside `out_dir`.

    Path-traversal hardening per Argus iter-1: scene filenames originate
    from user-supplied JSON, so absolute paths and `..` segments must
    not escape the declared output directory.
    """
    candidate = (out_dir / filename).resolve()
    out_root = out_dir.resolve()
    try:
        candidate.relative_to(out_root)
    except ValueError as exc:
        raise ValueError(
            f"scenes[{idx}].filename {filename!r} resolves outside out_dir "
            f"({candidate} not under {out_root})"
        ) from exc
    return candidate


def _load_scenes(path: Path, out_dir: Path) -> list[FrameSpec]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"scenes file {path} must be a JSON array")
    if not raw:
        raise ValueError(f"scenes file {path} is empty — must list ≥1 frame")
    specs: list[FrameSpec] = []
    seen_paths: set[Path] = set()
    seen_indexes: set[int] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(
                f"scenes[{i}] must be an object, got {type(item).__name__}")
        idx = item.get("index", i)
        if not isinstance(idx, int) or isinstance(idx, bool):
            raise ValueError(
                f"scenes[{i}].index must be int, got {type(idx).__name__}")
        if idx in seen_indexes:
            raise ValueError(
                f"scenes[{i}].index={idx} is a duplicate; each frame "
                f"must have a unique index"
            )
        seen_indexes.add(idx)
        scene = item.get("scene", "")
        if not isinstance(scene, str):
            raise ValueError(
                f"scenes[{i}].scene must be str, got {type(scene).__name__}")
        fname_raw = item.get("filename")
        if fname_raw is None:
            fname = f"frame_{idx:02d}.png"
        elif isinstance(fname_raw, str) and fname_raw:
            fname = fname_raw
        else:
            raise ValueError(
                f"scenes[{i}].filename must be a non-empty str, "
                f"got {type(fname_raw).__name__}")
        out_path = _resolve_safe_out_path(out_dir, fname, i)
        if out_path in seen_paths:
            raise ValueError(
                f"scenes[{i}].filename {fname!r} resolves to {out_path}, "
                f"which is already targeted by an earlier frame; each "
                f"scene must produce a unique output path (overwrite "
                f"would silently desync frame_count from on-disk artifacts)"
            )
        seen_paths.add(out_path)
        specs.append(FrameSpec(index=idx, scene=scene, out_path=out_path))
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
                        # stderr is sanitized via the shared `sanitize_stderr`
                        # used by the retry feedback path so credential-shaped
                        # tokens (sk-proj-…, ghp_…, Bearer …) never reach the
                        # JSON report. Codex Slice C audit Medium #1: docs
                        # tell agents to inspect this field for timeout/auth/
                        # rate-limit failures, so it must be present.
                        "stderr": sanitize_stderr(r.stderr) if r.stderr else "",
                        "out_path": str(r.out_path) if r.out_path else None,
                    }
                    for r in a.frame_results
                ],
                "verify": {
                    "passed": a.verify.passed,
                    "fail_reasons": a.verify.fail_reasons,
                    # Soft-gate output (character_consistency, loop_closure)
                    # lands here. Codex Slice C audit Medium #2: the workflow
                    # guide schema commits to `verify.advisories`, so the
                    # serializer must emit it even when empty.
                    "advisories": list(a.verify.advisories),
                    "gates": [asdict(g) for g in a.verify.gates],
                },
            }
            for a in report.attempts
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    # Boundary error handling per Argus iter-2 (possible issue 5/10):
    # bible JSON decode, scenes JSON decode, OS read errors, and value
    # validation should surface clean stderr + stable exit codes rather
    # than raw Python tracebacks at the system boundary.
    try:
        bible = CharacterBible.load(args.bible)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"error: failed to load bible {args.bible}: {exc}\n")
        return 2
    try:
        frames = _load_scenes(args.scenes, args.out_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"error: failed to load scenes {args.scenes}: {exc}\n")
        return 2

    if args.dry_run:
        for f in frames:
            sys.stdout.write(
                f"--- frame {f.index} -> {f.out_path}\n"
                f"{bible.compose_prompt(f.scene)}\n\n"
            )
        return 0

    # Non-dry-run hard-gate dependency check (Argus iter-1 校验弱化).
    # When Pillow/imagehash/scikit-image are missing, verify gates degrade
    # to severity="skipped" with passed=True, which would otherwise let a
    # generation cycle "pass" without any actual frame inspection. Hard
    # fail unless user explicitly opts in via --allow-skipped-gates.
    from .verify import _HAS_PIL, _HAS_IMAGEHASH, _HAS_SKIMAGE
    missing: list[str] = []
    if not _HAS_PIL:
        missing.append("Pillow")
    if not _HAS_IMAGEHASH:
        missing.append("ImageHash")
    if args.loop_closure and not _HAS_SKIMAGE:
        missing.append("scikit-image")
    if missing and not args.allow_skipped_gates:
        sys.stderr.write(
            f"error: verify rubric dependencies missing: {missing}. "
            f"install via `pip install Pillow ImageHash scikit-image numpy` "
            f"or pass --allow-skipped-gates to run without these gates.\n"
        )
        return 2

    try:
        args.out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"error: cannot create out-dir {args.out_dir}: {exc}\n")
        return 2

    try:
        ref_size = _parse_size(args.reference_size)
    except ValueError as exc:
        sys.stderr.write(f"error: --reference-size invalid: {exc}\n")
        return 2

    opts = GenerationOptions(
        model=args.model, size=args.size, quality=args.quality,
        output_format=args.output_format, background=args.background,
        timeout=args.timeout, base_image=args.base_image,
    )
    cfg = VerifyConfig(
        expected_count=len(frames),
        reference_size=ref_size,
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
