"""Pure-Pillow spritesheet packer — the always-on packing spine.

Lays per-animation frame groups into a single RGBA sheet (one row per
animation) and emits an Aseprite `--data json-hash`-compatible dict so
`godot_import` consumes the Python-packed output identically to a real
Aseprite export.

`frameTags` from/to indices are GLOBAL and cumulative across animations
in pack (insertion) order — `godot_import` indexes `frames` by that same
global position, so the two must agree exactly.
"""
from __future__ import annotations

from pathlib import Path


def frame_dimensions(frames: list[Path]) -> tuple[int, int]:
    """Return the uniform (width, height) of `frames`, else raise.

    Shared with `postprocess` so both packing paths reject ragged frame
    sets the same way before doing any layout work.
    """
    from PIL import Image  # lazy: --help / dry-run must not require Pillow
    if not frames:
        raise ValueError("no frames to pack")
    size: tuple[int, int] | None = None
    for f in frames:
        with Image.open(f) as img:
            this = img.size
        if size is None:
            size = this
        elif this != size:
            raise ValueError(
                f"frame {f.name} is {this}, expected uniform {size}; "
                f"all frames must share one dimension"
            )
    assert size is not None  # non-empty loop guarantees assignment
    return size


def pack_frames(groups: dict[str, list[Path]], out_sheet: Path,
                fps: float = 8.0) -> dict:
    """Pack `groups` into `out_sheet` (RGBA) and return the json-hash dict.

    `groups` maps animation_name -> ordered frame PNG paths (all the same
    size). One row per animation. Also writes the dict to
    `out_sheet.with_suffix(".json")`. Raises ValueError on empty input or
    mixed frame dimensions.
    """
    from PIL import Image  # lazy: --help / dry-run must not require Pillow
    if not groups:
        raise ValueError("groups is empty — nothing to pack")
    anim_names = list(groups.keys())
    ordered: list[Path] = [p for name in anim_names for p in groups[name]]
    w, h = frame_dimensions(ordered)

    max_cols = max((len(groups[name]) for name in anim_names), default=0)
    sheet_w = max_cols * w
    sheet_h = len(anim_names) * h
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))

    frames_dict: dict[str, dict] = {}
    frame_tags: list[dict] = []
    duration_ms = int(1000 / fps)
    global_idx = 0
    for row, name in enumerate(anim_names):
        paths = groups[name]
        start = global_idx
        for col, path in enumerate(paths):
            x, y = col * w, row * h
            with Image.open(path) as img:
                sheet.paste(img.convert("RGBA"), (x, y))
            frames_dict[f"{name}_{col}"] = {
                "frame": {"x": x, "y": y, "w": w, "h": h},
                "sourceSize": {"w": w, "h": h},
                "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h},
                "duration": duration_ms,
                "rotated": False,
                "trimmed": False,
            }
            global_idx += 1
        frame_tags.append({
            "name": name,
            "from": start,
            "to": global_idx - 1,
            "direction": "forward",
        })

    out_sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_sheet)

    data = {
        "frames": frames_dict,
        "meta": {
            "size": {"w": sheet_w, "h": sheet_h},
            "image": out_sheet.name,
            "format": "RGBA8888",
            "scale": "1",
            "frameTags": frame_tags,
        },
    }
    _write_json(data, out_sheet.with_suffix(".json"))
    return data


def _write_json(data: dict, path: Path) -> None:
    import json
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
