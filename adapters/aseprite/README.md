# Aseprite adapter (optional palette-unification post-process)

Thin batch-mode pass-through to the Aseprite (or LibreSprite) CLI, used by
the SoT pixel pipeline as an **optional** palette-unification step. The
always-on packing spine is the pure-Python packer
(`scripts/sot_pixel/pack.py`); this adapter only runs when a usable binary
is present and the caller asks for quantization.

This is original Mercury code (a thin wrapper around a local binary, like
`adapters/pixellab/`), not a cherry-pick — no upstream manifest entry is
required. `invoke.py` is **≤200 LOC** per the external-tool adapter cap.

## Graceful skip

Aseprite is not assumed to be installed. Detect a usable binary:

```bash
python adapters/aseprite/invoke.py --detect   # exit 0 found / 1 not found
```

`scripts/sot_pixel/postprocess.py` calls this; when it reports "not found"
the pipeline transparently falls back to the Python packer.

Binary resolution order: `MERCURY_ASEPRITE_BIN` env override (absolute
path or PATH name) → first of `aseprite` / `LibreSprite` on PATH.

## Why the one-shot `--split-tags` does not work

```bash
# BROKEN — do not use:
aseprite -b frames/*.png --sheet out.png --split-tags
```

Loose PNG frames carry no tags, so `--split-tags` is a no-op; flag order
matters; and `--split-tags` combined with a packed sheet is broken. The
correct path is **two steps**.

## The two-step workflow

1. **Import + tag** (Lua) — load the ordered PNG frames into one sprite
   (one cel per frame), assign tags via `sprite:newTag`, optionally apply
   a shared palette + `convertColorMode(ColorMode.INDEXED)`, save a
   tagged `.ase`:

   ```bash
   aseprite -b --script adapters/aseprite/import_and_tag.lua \
     --script-param frames=<comma-separated PNG paths> \
     --script-param "tags=idle:1-2;walk_south:3-6" \
     --script-param palette=<palette file | ""> \
     --script-param out=tagged.ase
   ```

   Tag ranges are **1-based inclusive** frame numbers (Aseprite
   convention); the caller converts from the pipeline's 0-based global
   indices.

2. **Export** the packed sheet + `json-hash` data:

   ```bash
   aseprite -b tagged.ase \
     --sheet out.png --sheet-type packed \
     --data out.json --format json-hash --list-tags
   ```

The emitted `out.json` matches the schema produced by
`scripts/sot_pixel/pack.py`, so `scripts/sot_pixel/godot_import.py`
consumes either source identically.
