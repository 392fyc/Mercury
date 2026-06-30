"""Optional Aseprite palette-unification post-process.

Wraps the two-step Aseprite workflow (import+tag Lua, then packed export)
behind the same `groups -> json-hash dict` contract as `pack.pack_frames`.
Aseprite is OPTIONAL: when no usable binary is present (or the workflow
fails for an Aseprite-related reason), this module logs an advisory to
stderr and returns None so the caller falls back to the pure-Python
packer. It MUST NOT raise merely because Aseprite is absent.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import pack

ADAPTER_DIR = (
    Path(__file__).resolve().parent.parent.parent / "adapters" / "aseprite"
)
ASEPRITE_ADAPTER = ADAPTER_DIR / "invoke.py"
LUA_SCRIPT = ADAPTER_DIR / "import_and_tag.lua"
_DETECT_TIMEOUT = 15.0
_RUN_TIMEOUT = 180.0


def aseprite_available() -> bool:
    """True when a usable Aseprite/LibreSprite binary is resolvable.

    Prefers the adapter's `--detect` (single source of truth, honours
    `MERCURY_ASEPRITE_BIN`); falls back to a direct PATH probe.
    """
    if ASEPRITE_ADAPTER.is_file():
        try:
            proc = subprocess.run(
                [sys.executable, str(ASEPRITE_ADAPTER), "--detect"],
                capture_output=True, text=True, timeout=_DETECT_TIMEOUT,
            )
            return proc.returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    return any(shutil.which(c) for c in ("aseprite", "LibreSprite"))


def quantize_and_pack(groups: dict[str, list[Path]], out_sheet: Path,
                      palette: Path | None = None,
                      fps: float = 8.0) -> dict | None:
    """Pack `groups` through Aseprite (palette-unified) or return None.

    Returns the Aseprite `--data json-hash` dict on success, or None when
    Aseprite is unavailable / the export fails — never raises for those
    cases, so the caller can fall back to `pack.pack_frames`.
    """
    if not aseprite_available():
        sys.stderr.write(
            "advisory: aseprite/LibreSprite unavailable; skipping palette "
            "unification (falling back to the pure-Python packer)\n"
        )
        return None
    if not groups:
        raise ValueError("groups is empty — nothing to pack")

    # Flatten to global order + 1-based inclusive tag ranges for the Lua.
    ordered: list[Path] = []
    tag_specs: list[str] = []
    global_idx = 0
    for name, paths in groups.items():
        start = global_idx
        for path in paths:
            ordered.append(Path(path))
            global_idx += 1
        tag_specs.append(f"{name}:{start + 1}-{global_idx}")

    # Validate uniform frame size up front (same rule as the Python packer).
    pack.frame_dimensions(ordered)

    out_sheet.parent.mkdir(parents=True, exist_ok=True)
    json_out = out_sheet.with_suffix(".json")
    tagged_ase = out_sheet.with_name(out_sheet.stem + "_tagged.ase")
    frames_csv = ",".join(str(p.resolve()) for p in ordered)
    tags_param = ";".join(tag_specs)

    step1 = [
        sys.executable, str(ASEPRITE_ADAPTER),
        "--script", str(LUA_SCRIPT),
        "--script-param", f"frames={frames_csv}",
        "--script-param", f"tags={tags_param}",
        "--script-param", f"palette={str(palette) if palette else ''}",
        "--script-param", f"out={tagged_ase}",
    ]
    step2 = [
        sys.executable, str(ASEPRITE_ADAPTER), str(tagged_ase),
        "--sheet", str(out_sheet), "--sheet-type", "packed",
        "--data", str(json_out), "--format", "json-hash", "--list-tags",
    ]
    try:
        if not _run_step(step1, "import+tag"):
            return None
        if not _run_step(step2, "sheet export"):
            return None
        return json.loads(json_out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(
            f"advisory: aseprite post-process failed ({exc}); falling back "
            "to the pure-Python packer\n"
        )
        return None
    finally:
        try:
            tagged_ase.unlink()
        except OSError:
            pass


def _run_step(cmd: list[str], label: str) -> bool:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_RUN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"advisory: aseprite {label} could not run ({exc})\n")
        return False
    if proc.returncode != 0:
        sys.stderr.write(
            f"advisory: aseprite {label} exited {proc.returncode}: "
            f"{proc.stderr.strip()[:500]}\n"
        )
        return False
    return True
