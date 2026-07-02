"""Pawn lane — 64x64 SoT board-unit sprites via the PixelLab adapter.

Generates the SoT pawn animation set (one static frame per animation by
default; optional multi-frame walk cycles via the PixelLab `animate`
endpoint), verifies the frames, packs them into a sheet (pure-Python or
Aseprite palette-unified), and emits a Godot `SpriteFrames` `.tres`.

The PixelLab adapter (`adapters/pixellab/invoke.py`) is invoked via
subprocess; its JSON stdout is parsed for output paths. The PixelLab API
token is owned entirely by that adapter — this module never reads it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from scripts.image_gen.character_bible import CharacterBible
from scripts.image_gen.verify import VerifyConfig, verify_frames

from . import godot_import, pack, postprocess

# The SoT SpriteFrames animation name set (scenes/tactical/Unit.tscn).
# Tuple (immutable) so it is a safe default-argument value — a mutable list
# default would risk implicit shared state across calls.
SOT_ANIMS = ("idle", "walk_south", "walk_north", "walk_east", "walk_west",
             "hurt", "death")
SOT_FPS = 8.0
# Frames requested per walk cycle when --animate-walk is on. PixelLab
# animate-with-text accepts 2-20 (default 4); we request it EXPLICITLY so
# the expected frame count is known up front and the frame_count verify
# gate can catch a short API response instead of silently shipping a thin
# .tres (otherwise expected==actual would make that gate tautological).
ANIMATE_N_FRAMES = 4

# PixelLab `view` sets the camera pitch. A top-down tactical board needs
# north/south/east/west to read as four DISTINCT facings. Omitting `view`
# lets PixelLab default to "side" (sidescroller), which collapses
# walk_north and walk_south into near-identical camera-facing frames
# (verified A/B/C 2026-07-02: side leaves walk_north facing the viewer;
# "high top-down" makes it a true back view — see api.pixellab.ai
# openapi.json + pixellab.ai/docs/options/character, which recommends
# high top-down for 4-direction board units). Passing `view` on every
# pixflux/bitforge AND animate call is what actually differentiates the
# facings; `direction` alone is not enough under the side default.
VIEW_CHOICES = ("side", "low top-down", "high top-down")
DEFAULT_VIEW = "high top-down"

ADAPTER_PATH_ENV = "MERCURY_PIXELLAB_ADAPTER"
DEFAULT_ADAPTER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "adapters" / "pixellab" / "invoke.py"
)
_RUN_TIMEOUT = 240.0


def resolve_adapter_path() -> Path:
    override = os.environ.get(ADAPTER_PATH_ENV)
    return Path(override).resolve() if override else DEFAULT_ADAPTER_PATH


def _anim_plan(anim: str) -> tuple[str, str]:
    """Return (facing direction, pose phrase) for an animation name."""
    if anim == "idle":
        return "south", "idle standing pose"
    if anim == "hurt":
        return "south", "recoiling hurt pose"
    if anim == "death":
        return "south", "fallen defeated pose"
    if anim.startswith("walk_"):
        return anim.split("_", 1)[1], "walking pose"
    return "south", anim


def _base_description(bible: CharacterBible) -> str:
    parts = list(bible.identity)
    if bible.style:
        parts.append(bible.style)
    return ", ".join(parts) if parts else bible.name


def _assert_under(path: Path, out_dir: Path, what: str) -> None:
    """Reject an adapter-returned path that resolves outside `out_dir`.

    Trust boundary: a swapped-in adapter (MERCURY_PIXELLAB_ADAPTER) could
    return a path pointing outside out_dir; we later read these paths back
    for verify + packing, so confirm ownership before touching them.
    """
    try:
        path.resolve().relative_to(out_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"pixellab adapter returned {what} outside out_dir: {path}"
        ) from exc


def _safe_out(out_dir: Path, relative: str) -> Path:
    """Resolve `out_dir / relative`, rejecting paths that escape out_dir.

    User-supplied `name` flows into output filenames, so apply the same
    path-traversal hardening as `scripts/image_gen/__main__.py`.
    """
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


def _build_static_argv(adapter: Path, backend: str, desc: str, size: int,
                       out_path: Path, direction: str, view: str,
                       ref: Path | None, seed: int | None) -> list[str]:
    argv = [
        sys.executable, str(adapter),
        "--endpoint", backend,
        "--description", desc,
        "--width", str(size), "--height", str(size),
        "--out", str(out_path),
        "--opt", "direction", direction,
        "--opt", "view", view,
        "--no-background",
    ]
    if ref is not None:
        argv += ["--init-image", str(ref), "--init-strength", "300"]
    if seed is not None:
        argv += ["--seed", str(seed)]
    return argv


def _build_animate_argv(adapter: Path, desc: str, out_subdir: Path,
                        ref_frame: Path, direction: str, view: str,
                        seed: int | None) -> list[str]:
    # animate is fixed 64x64 by the adapter — no --width/--height. Pass
    # direction + view too: animate-with-text defaults to direction="east"
    # / view="side", so without these a walk_north cycle would animate an
    # east-facing sidescroller sprite regardless of its reference frame.
    argv = [
        sys.executable, str(adapter),
        "--endpoint", "animate",
        "--description", desc,
        "--action", "walk",
        "--reference-image", str(ref_frame),
        "--out-dir", str(out_subdir),
        "--n-frames", str(ANIMATE_N_FRAMES),
        "--opt", "direction", direction,
        "--opt", "view", view,
    ]
    if seed is not None:
        argv += ["--seed", str(seed)]
    return argv


def generate_pawn(bible: CharacterBible, out_dir: Path, *,
                  name: str | None = None, size: int = 64,
                  ref: Path | None = None, backend: str = "pixflux",
                  view: str = DEFAULT_VIEW,
                  animations: tuple[str, ...] = SOT_ANIMS,
                  animate_walk: bool = False, quantize: bool = False,
                  max_palette: int = 256,
                  dry_run: bool = False, seed: int | None = None) -> dict:
    """Generate a SoT pawn sprite set and emit a SpriteFrames `.tres`.

    `view` is the PixelLab camera pitch (one of VIEW_CHOICES). It defaults
    to "high top-down" so walk_north/south/east/west render as four
    distinct facings on a top-down board; passing "side" reproduces the
    pre-fix sidescroller framing where north/south collapse together.

    `max_palette` is the verify rubric's palette ceiling. PixelLab's true
    64x64 frames legitimately carry 70-119 colors, so the default 256
    (index-color upper bound) still flags HD/anti-alias leakage without
    false-failing genuine pixel art at the 64-color default.

    Returns a report dict. On `dry_run`, returns the plan (planned argv +
    descriptions) without any network call or file/directory creation.
    """
    # Fail fast on an invalid view: the CLI enforces this via argparse
    # choices, but generate_pawn is also called directly (skill / tests /
    # future workflows), and an unchecked value would only surface as an
    # opaque PixelLab API error downstream.
    if view not in VIEW_CHOICES:
        raise ValueError(
            f"view must be one of {VIEW_CHOICES}, got {view!r}"
        )
    name = name or bible.name
    base_desc = _base_description(bible)
    if ref is None and bible.reference_images:
        ref = bible.reference_images[0]
    adapter = resolve_adapter_path()

    # Validate output names early (path-traversal hardening) even in dry-run.
    sheet_path = _safe_out(out_dir, f"{name}_sheet.png")
    tres_path = _safe_out(out_dir, f"{name}.tres")
    sheet_json_path = _safe_out(out_dir, f"{name}.json")
    frames_root = _safe_out(out_dir, "frames")

    # Plan every PixelLab invocation up front so dry-run and real run share
    # exactly one source of truth.
    plan: list[dict] = []
    for anim in animations:
        direction, pose = _anim_plan(anim)
        desc = f"{base_desc}, {pose}, facing {direction}"
        static_out = _safe_out(out_dir, f"frames/{anim}_00.png")
        plan.append({
            "anim": anim, "kind": "static", "direction": direction,
            "desc": desc, "out": static_out,
            "argv": _build_static_argv(adapter, backend, desc, size,
                                       static_out, direction, view, ref,
                                       seed),
        })
        if animate_walk and anim.startswith("walk_"):
            anim_dir = _safe_out(out_dir, f"frames/{anim}")
            adesc = f"{base_desc}, walking, facing {direction}"
            plan.append({
                "anim": anim, "kind": "animate", "direction": direction,
                "desc": adesc, "out_dir": anim_dir, "ref": static_out,
                "argv": _build_animate_argv(adapter, adesc, anim_dir,
                                            static_out, direction, view,
                                            seed),
            })

    if dry_run:
        return {
            "asset": "pawn", "name": name, "dry_run": True, "passed": True,
            "size": size, "frame_size": f"{size}x{size}", "backend": backend,
            "view": view,
            "animate_walk": animate_walk, "quantize": quantize,
            "ref": str(ref) if ref else None,
            "animations": list(animations),
            "planned_commands": [step["argv"] for step in plan],
            "descriptions": {
                f'{step["anim"]}:{step["kind"]}': step["desc"] for step in plan
            },
            "outputs": {
                "sheet": str(sheet_path), "json": str(sheet_json_path),
                "tres": str(tres_path), "frames_dir": str(frames_root),
            },
        }

    frames_root.mkdir(parents=True, exist_ok=True)

    # Execute: static frames first (they also seed the animate references),
    # then any animate steps.
    static_frames: dict[str, Path] = {}
    animate_frames: dict[str, list[Path]] = {}
    usd_total = 0.0
    for step in plan:
        result = _run_adapter(step["argv"])
        usd = result.get("usd")
        if isinstance(usd, (int, float)):
            usd_total += float(usd)
        # Validate the adapter's response shape before indexing it — a
        # well-formed-JSON-but-wrong-shape reply (e.g. a swapped-in adapter
        # via MERCURY_PIXELLAB_ADAPTER) must surface as a clean RuntimeError
        # the CLI handles, not a raw KeyError/TypeError traceback. Enforce
        # str-typed paths so a truthy-but-wrong-type value (e.g. out=[1] or
        # frames=[None]) cannot slip through into Path() as a TypeError.
        if step["kind"] == "static":
            out = result.get("out")
            if not isinstance(out, str) or not out:
                raise RuntimeError(
                    f"pixellab adapter returned no/invalid 'out' path for "
                    f"{step['anim']} (static frame)"
                )
            out_path = Path(out)
            _assert_under(out_path, out_dir, f"an 'out' path for {step['anim']}")
            static_frames[step["anim"]] = out_path
        else:
            frames = result.get("frames")
            if not isinstance(frames, list) or not frames or \
                    not all(isinstance(p, str) and p for p in frames):
                raise RuntimeError(
                    f"pixellab adapter returned no/invalid 'frames' list for "
                    f"{step['anim']} (animate cycle)"
                )
            frame_paths = [Path(p) for p in frames]
            for fp in frame_paths:
                _assert_under(fp, out_dir, f"a frame path for {step['anim']}")
            animate_frames[step["anim"]] = frame_paths

    groups: dict[str, list[Path]] = {}
    for anim in animations:
        if anim in animate_frames:
            groups[anim] = animate_frames[anim]
        else:
            groups[anim] = [static_frames[anim]]

    all_frames = [p for frames in groups.values() for p in frames]
    # Expected count is PLAN-derived, not len(all_frames): one frame per
    # static anim, ANIMATE_N_FRAMES per animate-walk cycle. Deriving it from
    # the actual frames would make frame_count tautological (expected==actual
    # always), so a short PixelLab animate response would pass silently.
    expected_count = sum(
        ANIMATE_N_FRAMES if (animate_walk and anim.startswith("walk_")) else 1
        for anim in animations
    )
    cfg = VerifyConfig(expected_count=expected_count,
                       reference_size=(size, size),
                       max_palette_size=max_palette, dhash_threshold=12)
    vres = verify_frames(all_frames, cfg)

    quantized = False
    sheet_json: dict | None = None
    if quantize and postprocess.aseprite_available():
        sheet_json = postprocess.quantize_and_pack(groups, sheet_path,
                                                   palette=None, fps=SOT_FPS)
        quantized = sheet_json is not None
    if sheet_json is None:
        sheet_json = pack.pack_frames(groups, sheet_path, fps=SOT_FPS)

    # Canonical descriptor under <name>.json (pack also writes its
    # <name>_sheet.json sidecar); the .tres references res://<name>_sheet.png.
    sheet_json_path.write_text(json.dumps(sheet_json, indent=2),
                               encoding="utf-8")
    tres = godot_import.make_pawn_tres(f"res://{name}_sheet.png", sheet_json,
                                       fps=SOT_FPS)
    tres_path.write_text(tres, encoding="utf-8")

    return {
        "asset": "pawn", "name": name, "dry_run": False,
        "passed": vres.passed, "size": size, "frame_size": f"{size}x{size}",
        "backend": backend, "view": view, "animate_walk": animate_walk,
        "quantized": quantized, "frames_total": len(all_frames),
        "animations": list(animations),
        "usd_total": round(usd_total, 6),
        "verify": {
            "passed": vres.passed,
            "fail_reasons": list(vres.fail_reasons),
            "advisories": list(vres.advisories),
            "summary": vres.summary(),
        },
        "outputs": {
            "sheet": str(sheet_path), "json": str(sheet_json_path),
            "tres": str(tres_path), "frames_dir": str(frames_root),
        },
    }


def _run_adapter(argv: list[str]) -> dict:
    """Run the PixelLab adapter and parse its JSON stdout.

    Raises RuntimeError on non-zero exit or unparseable output. The
    adapter never echoes the API token, so its stderr is safe to surface
    (truncated) for diagnostics.
    """
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=_RUN_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"pixellab adapter could not run: {exc}") from exc
    if proc.returncode != 0:
        raise RuntimeError(
            f"pixellab adapter exited {proc.returncode}: "
            f"{proc.stderr.strip()[:500]}"
        )
    try:
        return json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(
            f"pixellab adapter returned non-JSON stdout: {exc}"
        ) from exc
