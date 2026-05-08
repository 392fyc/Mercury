#!/usr/bin/env python3
# UPSTREAM: wuyoscar/gpt_image_2_skill
# SOURCE:   https://github.com/wuyoscar/gpt_image_2_skill
# SHA:      6fdd7243dc9605efcf6d66e9394d3d10fc5141f6
# DATE:     2026-05-08
# ISSUE:    https://github.com/392fyc/Mercury/issues/351
"""Mercury adapter — invoke wuyoscar/gpt_image_2_skill via uvx-pinned-SHA.

Validates env (`OPENAI_API_KEY`, `uvx` on PATH) then forwards argv to the
upstream `gpt-image` console script (entry point `gpt_image_cli.cli:main`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

REPO = "git+https://github.com/wuyoscar/gpt_image_2_skill"
SHA = "6fdd7243dc9605efcf6d66e9394d3d10fc5141f6"
ENTRY = "gpt-image"
HELP_FLAGS = frozenset({"--help", "-h", "--version", "-V"})


def _resolve_uvx() -> str:
    path = shutil.which("uvx")
    if not path:
        sys.stderr.write(
            "error: 'uvx' not found on PATH. install `uv` "
            "(https://docs.astral.sh/uv/) and ensure `uvx` is reachable.\n"
        )
        sys.exit(2)
    return path


def _check_api_key(args: list[str]) -> None:
    if any(a in HELP_FLAGS for a in args):
        return
    if not os.environ.get("OPENAI_API_KEY"):
        sys.stderr.write(
            "error: OPENAI_API_KEY is not set. export OPENAI_API_KEY=sk-... "
            "before invoking (or pass --help/--version to bypass).\n"
        )
        sys.exit(2)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    _check_api_key(args)
    uvx = _resolve_uvx()
    cmd = [uvx, "--from", f"{REPO}@{SHA}", ENTRY, *args]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
