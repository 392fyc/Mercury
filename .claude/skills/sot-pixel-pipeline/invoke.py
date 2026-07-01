#!/usr/bin/env python3
"""sot-pixel-pipeline skill — CLI wrapper around `scripts.sot_pixel`.

Two modes:

1. `--bible-template` — emit a starter character-bible JSON to stdout. No
   subprocess, no API key needed. Agents pipe to a file then edit identity /
   color_palette / reference_images to match the target character.

2. (default) — forward all arguments verbatim to
   `python -m scripts.sot_pixel` rooted at this repo. The skill adds no
   logic of its own; it exists so agents have a stable
   `/sot-pixel-pipeline`-style entrypoint that doesn't require knowing the
   `scripts/sot_pixel/` module path.

Per the SoT pixel asset pipeline selection ADR (§7 / §13,
`.mercury/docs/research/sot-pixel-asset-pipeline-selection-2026-06.md`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Pipeline marker — used only when launching the pipeline subprocess.
# `--help` / `--bible-template` / argparse-error paths return earlier
# without touching it, so standalone use of this skill remains intact
# even when cherry-picked outside a full Mercury checkout.
_PIPELINE_MARKER = Path("scripts") / "sot_pixel" / "__main__.py"

# Starter character-bible JSON emitted by --bible-template.
# Fields mirror the animate-frames character-bible contract (shared schema).
# reference_images[0] is used as the identity anchor for pawn generation
# (PixelLab init_image / style_image must be the same size as --size).
_BIBLE_TEMPLATE: dict = {
    "name": "character-name",
    "identity": [
        "distinctive visual feature A (e.g. hair color and style)",
        "distinctive visual feature B (e.g. weapon or accessory)",
        "color/palette anchor (e.g. primary outfit color)",
        "silhouette anchor (e.g. build / proportions)",
    ],
    "color_palette": [
        "#REPLACE_WITH_HEX_1",
        "#REPLACE_WITH_HEX_2",
        "#REPLACE_WITH_HEX_3",
    ],
    "style": "anime HD illustration, clean linework, high quality",
    "lighting": "soft front-lit, slight rim light",
    "camera": "front-facing, full body",
    "constraints": [
        "no text",
        "no watermarks",
        "no redesign",
        "no background elements",
    ],
    "reference_images": [],
}


def _resolve_pipeline_root() -> Path:
    """Walk parents looking for the pipeline marker.

    Returns the first parent of this file that contains
    `scripts/sot_pixel/__main__.py`. Raises SystemExit if not found —
    used only on the subprocess launch path so standalone
    `--help` / `--bible-template` use remains intact even when
    cherry-picked outside Mercury.
    """
    for parent in Path(__file__).resolve().parents:
        if (parent / _PIPELINE_MARKER).is_file():
            return parent
    raise SystemExit(
        f"sot-pixel-pipeline: cannot locate pipeline "
        f"({_PIPELINE_MARKER}) from any parent of this skill — the "
        f"skill must live inside a Mercury checkout that includes "
        f"`scripts/sot_pixel/` to run the pipeline. (`--help` and "
        f"`--bible-template` work without it; only "
        f"`python -m scripts.sot_pixel` invocation requires the marker.)"
    )


def _emit_bible_template() -> int:
    json.dump(_BIBLE_TEMPLATE, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _print_help() -> int:
    sys.stdout.write(
        "sot-pixel-pipeline — Mercury pixel asset pipeline\n"
        "\n"
        "Usage:\n"
        "  invoke.py --bible-template [> bible.json]\n"
        "  invoke.py --asset {portrait|icon|cutin|pawn} \\\n"
        "            --bible BIBLE.json --out-dir DIR [opts...]\n"
        "\n"
        "The first form emits a starter character-bible JSON; the second\n"
        "forwards every argument to `python -m scripts.sot_pixel` (run\n"
        "that with --help for the full flag list).\n"
        "\n"
        "Common flags (forwarded verbatim):\n"
        "  --asset    {portrait|icon|cutin|pawn}   asset type (required)\n"
        "  --bible    BIBLE.json                   character-bible JSON (required)\n"
        "  --out-dir  DIR                          output directory (required)\n"
        "  --name     NAME                         output file stem\n"
        "  --scene    TEXT                         extra pose/scene description\n"
        "  --dry-run                               print prompts, make no files\n"
        "\n"
        "Pawn-only flags:\n"
        "  --size     N        sprite size in px (default 64)\n"
        "  --ref      FILE     portrait PNG for identity reference\n"
        "  --backend  {pixflux|bitforge}\n"
        "  --animate-walk      generate walk animations in addition to idle\n"
        "  --max-palette N     verify palette ceiling (default 256)\n"
        "  --quantize          apply Aseprite palette quantization\n"
        "  --seed     N        deterministic generation seed\n"
        "\n"
        "Icon-only flag:\n"
        "  --quantize          apply palette quantization post-process\n"
        "\n"
        "See .mercury/docs/guides/sot-pixel-pipeline.md for the full\n"
        "calling guide, asset×backend matrix, pawn .tres format, and\n"
        "end-to-end examples.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        return _print_help()
    if args[0] == "--bible-template":
        # Reject mixed-mode invocations (e.g. --bible-template --out-dir ...)
        # to avoid silently ignoring trailing args after emitting JSON.
        # Never echo trailing arg values — a caller who shell-substituted a
        # token into a positional slot would otherwise see it in stderr.
        if len(args) > 1:
            sys.stderr.write(
                f"error: --bible-template takes no additional arguments; "
                f"got {len(args) - 1} extra argument(s) (values redacted).\n"
                f"emit the template (`invoke.py --bible-template > bible.json`) "
                f"and run the pipeline as a separate invocation.\n"
            )
            return 2
        return _emit_bible_template()
    # Pipeline launch — the only path that needs the marker.
    repo_root = _resolve_pipeline_root()
    cmd = [sys.executable, "-m", "scripts.sot_pixel", *args]
    # A process-launch failure (interpreter misconfigured, permission denied,
    # marker missing) raises OSError before the child runs. Catch at the
    # boundary and surface a clean stderr + 127 exit so callers get a stable
    # contract instead of an unhandled traceback.
    # Avoid echoing the absolute repo_root path — on shared CI logs this
    # would reveal local filesystem layout. The error class is sufficient.
    try:
        proc = subprocess.run(cmd, cwd=str(repo_root))
    except OSError as exc:
        sys.stderr.write(
            f"error: failed to launch `python -m scripts.sot_pixel` "
            f"from the skill's repo root: {exc.__class__.__name__}: {exc.strerror or exc}\n"
        )
        return 127
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
