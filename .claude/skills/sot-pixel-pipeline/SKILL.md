---
name: sot-pixel-pipeline
description: |
  Mercury's pixel asset pipeline for tactical RPG game art — generate
  engine-ready assets (character portraits, UI icons, battle cut-ins, and
  pawn board-unit sprites) from a character-bible JSON, routing each asset
  type to the correct backend: gpt-image-2 (portrait, icon, cut-in) or
  PixelLab REST direct (pawn, true-pixel 64×64) with optional Aseprite
  palette post-processing and an animate-frames verify gate. Generated
  assets land in the configured staging directory for the user to import
  into their game project — this pipeline never modifies the game
  repository directly. **Use proactively whenever the user asks to generate
  character portraits, UI icons, battle cut-ins, pawn sprites, or any
  board-unit pixel art.** Triggers: 'sot pixel', 'pixel pipeline',
  'pawn sprite', '棋子', 'battle cut-in', 'cut-in', 'ui icon', '图标',
  'character portrait', '立绘', '像素管线', 'sprite frames tres'.
user-invocable: true
allowed-tools: Bash, Read, Write, Glob
---

# sot-pixel-pipeline — Mercury Pixel Asset Pipeline

Skill-level entrypoint for Mercury's per-asset-type pixel art generation
pipeline. The actual generation, post-processing, verify, and Godot export
logic live in `scripts/sot_pixel/`; this skill is the **agent-facing CLI
wrapper** plus a `--bible-template` helper that emits a starter
character-bible JSON.

## When to use

Use this skill when the user wants to generate any of the following
engine-ready game art assets:

- **Character portrait** — anime HD still illustration of a named
  character (full body or bust), one PNG
- **UI icon** — pseudo-pixel icon for a skill bar or item slot (~38px
  display size), one PNG with optional palette quantization
- **Battle cut-in** — NS-Fire-Emblem-style full-screen character
  moment, one PNG with no UI elements or text
- **Pawn sprite** — true-pixel 64×64 board unit, full animation set
  (idle / 4-direction walk / hurt / death), packed sprite sheet plus
  a Godot SpriteFrames `.tres` file

**Do NOT use** for:

- Frame-by-frame fluent battle animations — these require per-frame
  manual QA and are explicitly deferred (not MVP)
- Single one-off images where the full pipeline overhead is
  unnecessary — call `adapters/gpt-image-2/invoke.py` directly
- Video / GIF encoding

## Asset × backend matrix

| Asset | Backend | Post-process | Output |
|---|---|---|---|
| portrait | gpt-image-2 (anime HD) | none | one PNG Texture |
| icon | gpt-image-2 (pseudo-pixel HD) | optional palette quantize (`--quantize`) | one PNG Texture |
| cut-in | gpt-image-2 + "no UI/no text" suffix | none | one PNG Texture |
| pawn | PixelLab REST direct (pixflux / bitforge) | Python packer (always-on) + optional Aseprite | 64×64 sprite sheet + SpriteFrames `.tres` |

## Prerequisites

| Requirement | Why |
|---|---|
| `uv` / `uvx` ≥ 0.10 on PATH | gpt-image-2 adapter mounts upstream via uvx |
| Python 3.11+ | `scripts.sot_pixel` + adapters |
| `OPENAI_API_KEY` (Tier 1+ verified org) | portrait / icon / cut-in generation |
| `PIXELLAB_API_TOKEN` | pawn generation via PixelLab REST |
| `pip install Pillow requests` | packer + verify rubric; **hard fail** without these |
| `aseprite` on PATH (optional) | palette unification enhancement; graceful-skip when absent |
| `.env` at repo root | preferred credential source for `OPENAI_API_KEY` + `PIXELLAB_API_TOKEN` |

See [`.mercury/docs/guides/sot-pixel-pipeline.md`](../../../.mercury/docs/guides/sot-pixel-pipeline.md)
for the full agent calling guide, character-bible JSON schema, asset×backend
matrix details, pawn `.tres` format, Aseprite-optional design rationale,
Phase-0 validated sample references, cost notes, and flow gates.

## Usage

### 1. Bootstrap a starter character-bible JSON

```bash
python .claude/skills/sot-pixel-pipeline/invoke.py --bible-template > bible.json
```

Edit `name`, `identity`, `color_palette`, `style`, and
`reference_images` to match the character. The `reference_images[0]`
field is used as the identity anchor for pawn generation.

### 2a. Generate a portrait

```bash
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset portrait \
    --bible bible.json \
    --out-dir staging/ \
    --name aria
```

### 2b. Generate a UI icon

```bash
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset icon \
    --bible bible.json \
    --out-dir staging/ \
    --name slash_skill \
    --scene "crossed swords, minimalist, icon style" \
    --quantize
```

### 2c. Generate a battle cut-in

```bash
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset cutin \
    --bible bible.json \
    --out-dir staging/ \
    --name aria_cutin \
    --scene "dramatic close-up, weapon raised, determined expression"
```

### 2d. Generate a pawn sprite (→ Godot SpriteFrames .tres)

```bash
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset pawn \
    --bible bible.json \
    --out-dir staging/ \
    --name aria \
    --ref staging/aria_portrait.png \
    --backend pixflux \
    --animate-walk \
    --size 64
```

The pawn lane produces a 64×64 sprite sheet plus
`staging/<name>.tres` containing the exact animation
name set the game engine plays: `idle`, `walk_north`, `walk_south`,
`walk_east`, `walk_west`, `hurt`, `death` (`death` uses `loop=false`;
all others loop). This file can be dropped in directly to replace
the placeholder SpriteFrames resource in the tactical scene's
`AnimatedSprite2D` node.

### 3. Dry-run (no API calls)

Pass `--dry-run` to any asset type to print planned API calls and
prompts without generating anything. Useful for budget review and
prompt validation.

### 4. Inspect the JSON report

The pipeline writes a structured JSON report to stdout on every real
run (omitted in `--dry-run` mode). The pawn report (here from a
`--name aria` run without `--animate-walk`, so one static frame per
animation = 7 frames) looks like:

```json
{
  "asset": "pawn",
  "name": "aria",
  "dry_run": false,
  "passed": true,
  "size": 64,
  "frame_size": "64x64",
  "backend": "pixflux",
  "animate_walk": false,
  "quantized": false,
  "frames_total": 7,
  "animations": ["idle", "walk_south", "walk_north", "walk_east", "walk_west", "hurt", "death"],
  "usd_total": 0.0,
  "verify": {
    "passed": true,
    "fail_reasons": [],
    "advisories": [],
    "summary": "VERIFY PASS — 4 gate(s)"
  },
  "outputs": {
    "sheet": "staging/aria_sheet.png",
    "json": "staging/aria.json",
    "tres": "staging/aria.tres",
    "frames_dir": "staging/frames"
  }
}
```

`passed=false` with a non-empty `verify.fail_reasons` means a hard
gate (frame count / dimensions / palette) failed; read
`verify.fail_reasons` + `verify.summary` for which gate failed and
why. Single-image (portrait / icon / cut-in) reports are flatter —
they carry `dimensions_ok` and a `size` object instead of a `verify`
block.

## Pawn → Godot .tres drop-in

The generated `.tres` is a Godot 4 `SpriteFrames` resource with
exactly seven named animations matching the game unit state machine:

| Animation | Loop | Purpose |
|---|---|---|
| `idle` | yes | default standing state |
| `walk_north` | yes | moving up the board |
| `walk_south` | yes | moving down the board |
| `walk_east` | yes | moving right |
| `walk_west` | yes | moving left |
| `hurt` | yes | damage flash |
| `death` | **no** | unit defeat sequence |

All frames are 64×64 pixels. To use: in the Godot editor, open the
tactical scene, select the `AnimatedSprite2D` node of the unit prefab,
and replace its `frames` property with the generated `.tres` file.

## Staging handoff + flow gate

Assets land in the staging directory (`--out-dir`); they are **not**
committed to the game repository. The user imports them into the game
project manually. Mercury changes (pipeline code, adapters, skill)
go to the `develop` branch via PR + dual-verify per the standard
Mercury workflow. The game repository is never touched by this
pipeline.

## Cost guardrails

| Backend | Unit cost | Notes |
|---|---|---|
| gpt-image-2 portrait / cut-in | ~$0.07 / image | medium quality; high ≈ $0.21 |
| gpt-image-2 icon | ~$0.07 / image | medium quality |
| PixelLab pixflux 64×64 | ~$0.008 / call | 40 free trial calls; $30 ≈ ~1 700 calls |
| PixelLab bitforge 128×128 | ~$0.010 / call | |
| Aseprite palette pass | $0 | local CLI, one-time $20 license or free LibreSprite |

Use `--dry-run` before real generation to validate prompts. Run with
`--quality low` for exploratory iterations; `--quality medium` for
staging review; `--quality high` for final delivery.

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `error: failed to load bible …` | Malformed JSON or missing `name` field | `jq . bible.json`; check required field |
| `error: OPENAI_API_KEY not set` | Credential missing for portrait/icon/cutin | Add to `.env` or export in shell |
| `error: PIXELLAB_API_TOKEN not set` | Credential missing for pawn | Add to `.env` or export in shell |
| `error: Pillow not installed` | Missing verify dependency | `pip install Pillow requests` |
| PixelLab call returns pydantic error in logs but image generated | SDK v1.0.5 `Usage` model bug | Adapter uses REST direct (no SDK parse); this is expected and handled |
| `passed=false`, `dimension_uniformity` failed | Pawn reference image (`--ref`) size mismatches `--size` | PixelLab requires reference image dimensions == output size; resize to 64×64 |
| Aseprite palette pass silently skipped | `aseprite` not on PATH | Install Aseprite ($20) or LibreSprite (free); Python packer runs as always-on spine |
| `error: failed to launch …` (exit 127) | Python path or repo root problem | Confirm `python .claude/skills/sot-pixel-pipeline/invoke.py --help` works from repo root |

## Detachability

This skill assumes Mercury's `scripts/sot_pixel/` + `adapters/gpt-image-2/`
+ `adapters/pixellab/` + `adapters/aseprite/` layout. Porting elsewhere
requires copying all four layers plus this skill, or pointing
`MERCURY_GPT_IMAGE_2_ADAPTER` and `MERCURY_PIXELLAB_ADAPTER` env vars at
compatible adapter implementations.

## Related

- [`.mercury/docs/guides/sot-pixel-pipeline.md`](../../../.mercury/docs/guides/sot-pixel-pipeline.md) — full agent calling guide
- [`.mercury/docs/research/sot-pixel-asset-pipeline-selection-2026-06.md`](../../../.mercury/docs/research/sot-pixel-asset-pipeline-selection-2026-06.md) — selection ADR (§6 architecture, §11 Phase-0 results, §13 final matrix)
- [`.claude/skills/animate-frames/SKILL.md`](../animate-frames/SKILL.md) — frame-sequence animation skill (character-bible contract shared)
- [`scripts/sot_pixel/`](../../../scripts/sot_pixel/) — pipeline implementation
- [`adapters/gpt-image-2/`](../../../adapters/gpt-image-2/) — portrait / icon / cut-in backend
- [`adapters/pixellab/`](../../../adapters/pixellab/) — pawn backend (REST direct, bypasses SDK bug)
- [`adapters/aseprite/`](../../../adapters/aseprite/) — optional palette post-process
