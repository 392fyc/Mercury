#!/usr/bin/env python3
"""animate-frames skill — CLI wrapper around `scripts.image_gen`.

Two modes:

1. `--example <template>` — emit a starter scenes JSON to stdout. No
   subprocess, no API key needed. Templates: `walking-cycle`, `idle`,
   `attack-arc`. Agents typically pipe to a file then edit `scene` text.

2. (default) — forward all arguments verbatim to
   `python -m scripts.image_gen` rooted at this repo. The skill adds no
   logic of its own; it exists so agents have a stable
   `/animate-frames`-style entrypoint that doesn't require knowing the
   `scripts/image_gen/` module path.

Per ADR §7.2.3 (`.mercury/docs/research/pixel-animation-workflow-2026-05-08.md`).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Repo root = three parents up from this file
# (.claude/skills/animate-frames/invoke.py -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[3]

SCENE_TEMPLATES: dict[str, list[dict]] = {
    "walking-cycle": [
        {"index": 0, "scene": "left foot forward, mid-stride, weight on left leg, arms swinging naturally", "filename": "frame_00.png"},
        {"index": 1, "scene": "passing pose, both feet briefly under body, neutral arm position", "filename": "frame_01.png"},
        {"index": 2, "scene": "right foot forward, mid-stride, weight on right leg, arms swinging opposite", "filename": "frame_02.png"},
        {"index": 3, "scene": "passing pose mirrored, both feet briefly under body, neutral arm position", "filename": "frame_03.png"},
    ],
    "idle": [
        {"index": 0, "scene": "relaxed standing pose, arms at sides, neutral expression", "filename": "frame_00.png"},
        {"index": 1, "scene": "subtle breathing motion, shoulders slightly raised, same neutral expression", "filename": "frame_01.png"},
    ],
    "attack-arc": [
        {"index": 0, "scene": "wind-up pose, weapon raised behind head, weight back, focused expression", "filename": "frame_00.png"},
        {"index": 1, "scene": "mid-swing, weapon arcing forward at shoulder height, weight transferring forward", "filename": "frame_01.png"},
        {"index": 2, "scene": "follow-through pose, weapon extended forward at waist height, weight on front foot", "filename": "frame_02.png"},
    ],
}


def _emit_example(name: str) -> int:
    template = SCENE_TEMPLATES.get(name)
    if template is None:
        sys.stderr.write(
            f"error: unknown template {name!r}; "
            f"available: {sorted(SCENE_TEMPLATES)}\n"
        )
        return 2
    json.dump(template, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _print_help() -> int:
    sys.stdout.write(
        "animate-frames — Mercury pixel-frame animation skill\n"
        "\n"
        "Usage:\n"
        "  invoke.py --example {walking-cycle|idle|attack-arc} [> scenes.json]\n"
        "  invoke.py --bible BIBLE --scenes SCENES --out-dir OUT [forwarded args...]\n"
        "\n"
        "The first form emits a starter scenes JSON; the second forwards every\n"
        "argument to `python -m scripts.image_gen` (run that with --help for the\n"
        "full flag list: --model / --size / --quality / --format / --background /\n"
        "--timeout / --max-retries / --max-palette / --dhash-threshold /\n"
        "--ssim-threshold / --loop-closure / --reference-size / --base-image /\n"
        "--dry-run / --allow-skipped-gates).\n"
        "\n"
        "See .mercury/docs/guides/pixel-animation-workflow.md for the full\n"
        "calling guide, JSON schemas, verify rubric, and examples.\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        return _print_help()
    if args[0] == "--example":
        if len(args) < 2:
            sys.stderr.write(
                f"error: --example requires a template name; "
                f"available: {sorted(SCENE_TEMPLATES)}\n"
            )
            return 2
        # Reject mixed-mode invocations like `--example idle --out-dir frames`
        # (Codex Slice C audit Low #1): silently ignoring trailing args
        # would hide caller mistakes by exiting 0 after printing JSON.
        if len(args) > 2:
            sys.stderr.write(
                f"error: --example takes exactly one template name; "
                f"got extra arguments: {args[2:]}\n"
                f"emit a template (`invoke.py --example {args[1]} > scenes.json`) "
                f"and run the pipeline as a separate invocation.\n"
            )
            return 2
        return _emit_example(args[1])
    cmd = [sys.executable, "-m", "scripts.image_gen", *args]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
