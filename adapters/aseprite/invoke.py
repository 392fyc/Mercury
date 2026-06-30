#!/usr/bin/env python3
"""Mercury adapter — Aseprite / LibreSprite CLI batch pass-through.

Original Mercury code (not a cherry-pick): a thin wrapper that resolves
the Aseprite (or LibreSprite) binary and forwards argv to it in batch
mode (`-b`). Aseprite is an OPTIONAL palette-unification enhancement for
the SoT pixel pipeline — the always-on packing spine is the pure-Python
packer (`scripts/sot_pixel/pack.py`), so this adapter is allowed to be
absent and callers graceful-skip via `--detect`.

Binary resolution order:
  1. `MERCURY_ASEPRITE_BIN` env override (absolute path or PATH name)
  2. first of `aseprite` / `LibreSprite` found on PATH

No credentials are involved (local rendering tool), so the parent
environment is forwarded unfiltered.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

BIN_ENV = "MERCURY_ASEPRITE_BIN"
CANDIDATES = ("aseprite", "LibreSprite")


def resolve_binary() -> str | None:
    """Resolve a usable Aseprite/LibreSprite binary path, or None."""
    override = os.environ.get(BIN_ENV)
    if override:
        if os.path.isfile(override):
            return override
        return shutil.which(override)
    for candidate in CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _detect() -> int:
    """Print the resolved binary path; exit 0 found / 1 not found."""
    bin_path = resolve_binary()
    if bin_path:
        sys.stdout.write(f"{bin_path}\n")
        return 0
    sys.stderr.write(
        "aseprite: no usable binary found "
        f"(set {BIN_ENV} or install aseprite/LibreSprite on PATH)\n"
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--detect" in args:
        return _detect()
    bin_path = resolve_binary()
    if bin_path is None:
        sys.stderr.write(
            "error: aseprite/LibreSprite binary not found. "
            f"set {BIN_ENV} or install it on PATH. "
            "(aseprite is an optional palette-unification step; the "
            "pipeline falls back to the pure-Python packer without it.)\n"
        )
        return 127
    cmd = [bin_path, "-b", *args]
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        sys.stderr.write(f"error: failed to invoke aseprite: {exc}\n")
        return 127


if __name__ == "__main__":
    sys.exit(main())
