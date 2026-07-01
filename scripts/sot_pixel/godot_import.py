"""Godot 4.x SpriteFrames `.tres` generator (pipeline PATH B).

Consumes an Aseprite `--data json-hash` dict (emitted either by Aseprite
or by `pack.pack_frames`) and emits a `SpriteFrames` resource backed by a
packed sheet + one `AtlasTexture` sub-resource per frame.

Ground truth = SoT `scenes/tactical/Unit.tscn` + `scripts/units/unit.gd`:
pawns are 64x64 `AnimatedSprite2D` units whose animation name set is
exactly idle / walk_north / walk_south / walk_east / walk_west / hurt /
death, with loop=true for everything except death.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# SoT loop defaults: every pawn animation loops except `death`.
DEFAULT_NON_LOOPING = frozenset({"death"})
DEFAULT_FPS = 8.0


def _fps_for(anim: str, fps_map: float | dict) -> float:
    if isinstance(fps_map, dict):
        return float(fps_map.get(anim, DEFAULT_FPS))
    return float(fps_map)


def _loop_for(anim: str, loop_map: dict[str, bool] | None) -> bool:
    if loop_map is not None and anim in loop_map:
        return bool(loop_map[anim])
    return anim not in DEFAULT_NON_LOOPING


def _fmt(x: float) -> str:
    """Format a float as a Godot `.tres` literal (integers keep a `.0`)."""
    x = float(x)
    if x.is_integer():
        return f"{int(x)}.0"
    return repr(x)


def build_spriteframes_tres(sheet_res_path: str, aseprite_json: dict,
                            fps_map: float | dict = DEFAULT_FPS,
                            loop_map: dict[str, bool] | None = None) -> str:
    """Render a `SpriteFrames` `.tres` from a json-hash dict.

    `frames` is treated as insertion-ordered; `meta.frameTags[*].from/to`
    are GLOBAL indices into that order (one tag == one animation). Each
    referenced frame becomes an `AtlasTexture` sub-resource with a
    `Rect2` cut from the frame's `{x,y,w,h}`. Per-frame `duration`
    multiplier = frame.duration_ms / (1000 / fps) (1.0 when they agree).
    """
    frames = aseprite_json.get("frames")
    meta = aseprite_json.get("meta")
    if not isinstance(frames, dict) or not isinstance(meta, dict):
        raise ValueError("aseprite_json must have dict 'frames' and 'meta'")
    frame_list = list(frames.values())
    tags = meta.get("frameTags")
    if not isinstance(tags, list) or not tags:
        raise ValueError("aseprite_json.meta.frameTags must be a non-empty list")

    sub_blocks: list[str] = []
    anim_blocks: list[str] = []
    atlas_idx = 0
    for tag in tags:
        name = tag["name"]
        frm, to = int(tag["from"]), int(tag["to"])
        if frm < 0 or to >= len(frame_list) or frm > to:
            raise ValueError(
                f"frameTag {name!r} range {frm}-{to} out of bounds "
                f"(have {len(frame_list)} frames)"
            )
        speed = _fps_for(name, fps_map)
        looping = _loop_for(name, loop_map)
        nominal_ms = 1000.0 / speed if speed else 0.0
        frame_refs: list[str] = []
        for gi in range(frm, to + 1):
            region = frame_list[gi]["frame"]
            atlas_id = f"At_{atlas_idx}"
            sub_blocks.append(
                f'[sub_resource type="AtlasTexture" id="{atlas_id}"]\n'
                f'atlas = ExtResource("1_sheet")\n'
                f'region = Rect2({region["x"]}, {region["y"]}, '
                f'{region["w"]}, {region["h"]})\n'
            )
            dur_ms = frame_list[gi].get("duration", nominal_ms)
            mult = (dur_ms / nominal_ms) if nominal_ms else 1.0
            frame_refs.append(
                f'{{"duration": {_fmt(mult)}, '
                f'"texture": SubResource("{atlas_id}")}}'
            )
            atlas_idx += 1
        anim_blocks.append(
            "{\n"
            f'"frames": [{", ".join(frame_refs)}],\n'
            f'"loop": {"true" if looping else "false"},\n'
            f'"name": &"{name}",\n'
            f'"speed": {_fmt(speed)}\n'
            "}"
        )

    # load_steps = ext_resource(1) + sub_resources(atlas_idx) + the resource.
    load_steps = 1 + atlas_idx + 1
    parts = [
        f'[gd_resource type="SpriteFrames" load_steps={load_steps} format=3]\n',
        f'\n[ext_resource type="Texture2D" path="{sheet_res_path}" '
        f'id="1_sheet"]\n',
        "\n" + "\n".join(sub_blocks),
        "\n[resource]\n"
        "animations = [" + ", ".join(anim_blocks) + "]\n",
    ]
    return "".join(parts)


def make_pawn_tres(sheet_res_path: str, aseprite_json: dict,
                   fps: float = DEFAULT_FPS) -> str:
    """SpriteFrames `.tres` with SoT pawn loop defaults (death=false)."""
    return build_spriteframes_tres(sheet_res_path, aseprite_json,
                                   fps_map=fps, loop_map=None)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.sot_pixel.godot_import",
        description="Generate a Godot SpriteFrames .tres from an Aseprite "
                    "json-hash sheet descriptor.",
    )
    p.add_argument("--sheet-json", type=Path, required=True,
                   help="Aseprite/pack json-hash descriptor")
    p.add_argument("--sheet-res", required=True,
                   help='res:// path to the sheet PNG, e.g. '
                        '"res://knight_sheet.png"')
    p.add_argument("--out", type=Path, required=True,
                   help="output .tres path")
    p.add_argument("--fps", type=float, default=DEFAULT_FPS)
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        data = json.loads(args.sheet_json.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"error: failed to load {args.sheet_json}: {exc}\n")
        return 2
    try:
        tres = make_pawn_tres(args.sheet_res, data, fps=args.fps)
    except (ValueError, KeyError) as exc:
        sys.stderr.write(f"error: cannot build SpriteFrames: {exc}\n")
        return 1
    try:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(tres, encoding="utf-8")
    except OSError as exc:
        sys.stderr.write(f"error: cannot write {args.out}: {exc}\n")
        return 1
    sys.stdout.write(f"wrote {args.out}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
