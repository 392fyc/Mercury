# SoT Pixel Asset Pipeline — Agent Calling Guide

Authoritative agent-facing guide for Mercury's pixel asset pipeline
(selection ADR + Phase-0 validation:
[`sot-pixel-asset-pipeline-selection-2026-06.md`](../research/sot-pixel-asset-pipeline-selection-2026-06.md)
§6 architecture, §11 Phase-0 results, §13 final matrix). Pairs with the
[`animate-frames` guide](pixel-animation-workflow.md) — the two pipelines
share the character-bible JSON contract and the verify gate.

## TL;DR

```bash
# 0. Bootstrap a character bible.
python .claude/skills/sot-pixel-pipeline/invoke.py --bible-template > aria.json
# Edit name / identity / color_palette / reference_images.

# 1a. Character portrait (anime HD, one PNG).
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset portrait --bible aria.json --out-dir staging/ --name aria

# 1b. UI icon (pseudo-pixel HD, optional palette quantize).
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset icon --bible aria.json --out-dir staging/ \
    --name slash_skill --scene "crossed swords, minimalist" --quantize

# 1c. Battle cut-in (NS-Fire-Emblem style, no UI/text).
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset cutin --bible aria.json --out-dir staging/ \
    --name aria_cutin --scene "dramatic close-up, weapon raised, determined expression"

# 1d. Pawn sprite → Godot SpriteFrames .tres (true pixel 64×64).
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset pawn --bible aria.json --out-dir staging/ \
    --name aria --ref staging/aria_portrait.png \
    --backend pixflux --animate-walk --size 64

# 2. Dry-run any asset first (no API calls, no cost).
python .claude/skills/sot-pixel-pipeline/invoke.py \
    --asset portrait --bible aria.json --out-dir staging/ --dry-run
```

## Architecture

Three backend adapters + two always-on shared layers:

```
character-bible JSON                 ← identity anchor; same schema as
        │                              animate-frames (shared contract)
        ▼
┌──────────────────── asset-type routing ────────────────────────────┐
│  portrait  →  adapters/gpt-image-2/   (anime HD, one PNG)         │
│  icon      →  adapters/gpt-image-2/   (pseudo-pixel HD, one PNG)  │
│  cut-in    →  adapters/gpt-image-2/   ("no UI/no text" suffix)    │
│  pawn      →  adapters/pixellab/      (REST direct, 64×64 sheet)  │
└──────────────────────────┬─────────────────────────────────────────┘
                           ▼
         Python packer (always-on spine)
         + optional Aseprite CLI palette post-process
         (graceful-skip when aseprite not on PATH)
                           ▼
         animate-frames verify gate
         (frame count / dimensions / palette / dHash consistency)
                           ▼
         Godot SpriteFrames .tres  ←── pawn lane only
                           ▼
                 staging directory
             (user imports to game project;
              game repository is never touched)
```

The **Python packer** is the always-on spine for the pawn lane: it packs
per-frame PNGs into a single sprite sheet and writes the `.tres` file
even when Aseprite is absent. Aseprite is an *enhancement* layer — when
`aseprite` is on PATH and `--quantize` is requested, it runs palette
unification before packing; otherwise the packer proceeds without
palette reduction (frames are packed as-is and the pawn report's
`quantized` field stays `false`).

Layer locations:

```
adapters/gpt-image-2/      # portrait / icon / cut-in backend
└── invoke.py              # uvx-pinned mount (wuyoscar/gpt_image_2_skill, MIT)

adapters/pixellab/         # pawn backend (REST direct, bypasses SDK bug — see §PixelLab notes)
└── invoke.py

adapters/aseprite/         # optional palette post-process
└── invoke.py              # graceful-skip when aseprite not on PATH

scripts/sot_pixel/         # pipeline orchestration (uncapped LOC, Mercury-internal)
├── __init__.py            # package docstring + asset-routing overview
├── presets.py             # gpt-image-2 portrait / icon / cut-in presets + argv
├── pawn.py                # PixelLab call + verify + pack + .tres orchestration
├── pack.py                # pure-Pillow spritesheet packer (always-on spine)
├── postprocess.py         # optional Aseprite palette unification (graceful-skip)
├── godot_import.py        # Godot SpriteFrames .tres generator
├── test_smoke.py          # offline smoke suite (dry-run + pure-function paths)
└── __main__.py            # `python -m scripts.sot_pixel` CLI

# CharacterBible (bible load + validate) and the verify rubric are reused
# from scripts/image_gen/ (shared with the animate-frames pipeline) — not
# duplicated here.

.claude/skills/sot-pixel-pipeline/   # agent-facing skill wrapper
├── SKILL.md
└── invoke.py              # thin wrapper + --bible-template helper
```

## Prerequisites

| Requirement | Why | Verification |
|---|---|---|
| `uv` / `uvx` ≥ 0.10 on PATH | gpt-image-2 adapter mount | `uvx --version` |
| Python 3.11+ | pipeline + adapters | `python --version` |
| `OPENAI_API_KEY` (Tier 1+ verified org) | portrait / icon / cut-in generation | `echo $OPENAI_API_KEY` (presence only) |
| `PIXELLAB_API_TOKEN` | pawn generation | `echo $PIXELLAB_API_TOKEN` (presence only) |
| `pip install Pillow requests` | packer + verify rubric | `python -c "import PIL, requests"` |
| `aseprite` on PATH (optional) | palette unification enhancement | `aseprite --version` |

Both API tokens are read from `.env` at the repo root first; shell
environment variables take precedence. **Never echo token values in
scripts or logs.**

Install required Python deps:

```bash
pip install Pillow requests
```

For the verify gate's dHash gate (pawn lane only):

```bash
pip install ImageHash
```

## Character-Bible JSON Schema

Shared with the animate-frames pipeline. Full schema lives in
[`pixel-animation-workflow.md`](pixel-animation-workflow.md) §"Character
Bible JSON schema"; the key differences for this pipeline are:

| Field | Notes specific to this pipeline |
|---|---|
| `name` | Used as the default output file stem when `--name` is not given |
| `identity` | Exact-repetition anchor phrases; keep to 3–6 items |
| `color_palette` | Hex list; forwarded as prompt anchor for gpt-image-2 + as target palette hint for Aseprite post-process |
| `reference_images` | `reference_images[0]` is the **identity anchor for pawn generation** — PixelLab uses it as `init_image` / `style_image`; must be the same size as `--size` (default 64×64) |

Bootstrap a starter template:

```bash
python .claude/skills/sot-pixel-pipeline/invoke.py --bible-template > bible.json
```

Template fields marked `REPLACE_WITH_*` must be filled before any real
generation run (they are intentionally invalid to surface missing edits
fast). `--dry-run` validates prompt composition without API cost.

### Bible authoring tips

- Write `identity` items as **exact-repetition visual phrases** — the
  same text goes into every prompt, so "red braided hair to waist" is
  more stable than "distinctive red hair."
- Keep `color_palette` short (3–6 hex values). More than 8 entries
  adds noise rather than constraint.
- For the pawn lane, **generate the portrait first** and add the
  resulting PNG as `reference_images[0]`. Feeding the same-character
  portrait as `init_image` is the strongest identity anchor available
  in PixelLab.
- Do not put scene-specific or asset-specific text in the bible. The
  bible applies unchanged across all asset types; per-asset variation
  goes in `--scene`.

## Asset × Backend Matrix (Locked, Phase-0 Verified)

| Asset | Backend | Style | Prompt suffix | Phase-0 sample |
|---|---|---|---|---|
| portrait | gpt-image-2 | anime HD | none | `gpt_portrait_01.png` |
| icon | gpt-image-2 | pseudo-pixel HD | none | `gpt_icon_slash_01.png`, `gpt_icon_iai_01.png` |
| cut-in | gpt-image-2 | NS-FE style | "no UI, no text, no HUD" | `gpt_battle_cutin_01.png` |
| pawn | PixelLab pixflux / bitforge | true-pixel | character ref via `init_image` | `pixellab_pixflux_64.png`, `pixellab_initimg_128.png`, `pixellab_bitforge_*.png` |

Phase-0 samples are at `D:\ShipOfTheseus\resource\mercury-playground\`
(user staging area; not committed to either repository).

### gpt-image-2 lane (portrait / icon / cut-in)

- One image per call via `generations` or `edits` endpoint.
- gpt-image-2 does **not** support transparent background (`auto` /
  `opaque` only). Use PNG with a neutral background and crop in the
  game editor if needed.
- The cut-in prompt automatically appends the suffix
  `"no UI, no text, no HUD, no speech bubbles"` to prevent the model
  from generating Fire-Emblem-style UI overlays that appeared in
  Phase-0 samples without the suffix.
- `--scene` is concatenated after the character-bible anchor block and
  before the asset-type suffix. Keep it to one descriptive sentence.
- `--quantize` (icon only): runs PIL's palette quantizer (32 colors)
  on the generated PNG to flatten it toward a limited palette. The
  single-image lane has **no** Aseprite integration (Aseprite is used
  only in the pawn lane), so this is a pure-PIL step that always runs
  when `--quantize` is passed.

### PixelLab lane (pawn)

- REST direct to `api.pixellab.ai/v1/` — bypasses the official Python
  SDK due to a pydantic parse bug in SDK v1.0.5 (`Usage` model has
  `Literal["usd"]` but API returns `usage.type='generations'`). The
  image is generated correctly; only the response parsing throws.
  The adapter uses `requests` directly with the token from
  `client.headers()` for authentication.
- Two backends selectable via `--backend`:
  - `pixflux` — PixelLab's Pixflux model; best for 64×64 fine pixel detail.
  - `bitforge` — PixelLab's BitForge model; stronger for stylistic coherence
    from a reference portrait.
- `--ref PORTRAIT.png` sets `reference_images[0]` → used as
  `init_image` / `style_image`. **The reference image must be exactly
  `--size × --size` pixels.** Pre-resize if needed:
  ```bash
  python -c "from PIL import Image; Image.open('portrait.png').resize((64,64)).save('ref_64.png')"
  ```
- `--animate-walk` generates walk-direction frames in addition to idle
  and hurt / death, producing the full 7-animation set.
- `no_background=True` is set by default (PixelLab supports transparent
  background; the packer composites onto a checkerboard for preview).
- PixelLab `animate-with-text` is capped at **64×64** output (verified
  Phase-0). For larger pawns use `--size 128` with bitforge (4 frames
  max per call; the packer makes multiple calls for the full set).
- PixelLab isometric mode: pass `--isometric` (not yet wired in the
  pawn lane MVP; add when the game's board perspective is decided).

## Pawn Lane Detail

### Animation set

The generated `.tres` carries exactly these named animations, matching
the game unit state machine (`scenes/tactical/Unit.tscn`):

| Animation name | Loop | Typical frame count | Notes |
|---|---|---|---|
| `idle` | yes | 4 | default board standing state |
| `walk_north` | yes | 4 | moving up |
| `walk_south` | yes | 4 | moving down |
| `walk_east` | yes | 4 | moving right |
| `walk_west` | yes | 4 | moving left (mirror of east if `--animate-walk` omits it) |
| `hurt` | yes | 2 | damage flash |
| `death` | **no** | 4 | unit defeat; `loop=false` is critical — set once, stays on last frame |

Frame counts are best-effort defaults from PixelLab's animation call.
The packer normalises whatever PixelLab returns into the `.tres` structure.

### Sprite sheet layout

The packer outputs a single `<name>_sheet.png` with all animations laid
out in a grid (one row per animation, frames left-to-right) plus a
`<name>.json` descriptor (Aseprite `json-hash` shape) that the `.tres`
generator consumes. The `.tres` encodes the frame rectangles from this
layout. All files are written to `--out-dir`.

> The packer additionally emits a `<name>_sheet.json` sidecar (same
> content as `<name>.json`) — a harmless intermediate artifact of the
> packing step. The canonical descriptor the pipeline reads is
> `<name>.json`; the sidecar can be ignored or deleted.

### Godot drop-in

1. Copy `<name>_sheet.png` + `<name>.tres` to the
   game project's resource directory.
2. In the Godot editor, open `scenes/tactical/Unit.tscn`.
3. Select the `AnimatedSprite2D` node.
4. In the Inspector, set `Frames` → load `<name>.tres`.
5. The unit immediately uses the new animations with no GDScript
   changes needed — animation names match the state machine strings.

## Aseprite-Optional Design

The pipeline has a two-tier post-process architecture:

| Tier | Tool | Availability | What it does |
|---|---|---|---|
| Always-on spine | Python (PIL) | no external dep | Pack frames → sprite sheet; write SpriteFrames `.tres` (no palette reduction — frames packed as-is) |
| Enhancement layer | Aseprite CLI | optional ($20 or LibreSprite free) | Palette unification across frames; index-mode export; advanced per-frame tag output |

This design ensures the pipeline produces valid Godot-importable output
even in environments where Aseprite is not installed. The enhancement
layer adds colour consistency across a multi-frame pawn set when
`--quantize` is used.

**Graceful-skip logic**: `scripts/sot_pixel/postprocess.py` probes the
Aseprite binary via `adapters/aseprite/invoke.py --detect` before
packing. If no usable binary is present it writes an advisory to stderr
and returns `None`, so `pawn.py` falls back to the pure-Python packer
(`pack.pack_frames`) and the pawn report's `quantized` field stays
`false`. The run always continues.

**Free alternatives**:
- LibreSprite (GPL v2, fork of old Aseprite) — CLI-compatible for batch
  operations; some advanced features differ.
- Aseprite self-compiled from source (EULA allows personal compile;
  output assets are commercially usable).

## Phase-0 Validated Samples Reference

All samples are at `D:\ShipOfTheseus\resource\mercury-playground\`
(user staging area; never committed). Key findings from the ADR §11:

| Sample | Finding |
|---|---|
| `gpt_portrait_01.png` | anime HD portrait quality high; character identity stable |
| `gpt_icon_slash_01.png`, `gpt_icon_iai_01.png` | pseudo-pixel style; cohesive; suitable for 38px icon slots |
| `gpt_battle_cutin_01.png` | NS-FE style quality reached; pipeline auto-suffix suppressed UI overlay |
| `pixellab_pixflux_64.png` | true-pixel 64×64; clean edges; distinct at board scale |
| `pixellab_initimg_128.png` | portrait→pixel conversion with `init_image`; highest identity fidelity |
| `pixellab_bitforge_portrait_128.png`, `pixellab_bitforge_sprite_128.png` | bitforge with reference; good style coherence |
| `pixellab_anim_attack_00-03.png` + sheet + `.gif` | `action` field controlled the attack motion reliably (non-random); 4-frame 64×64 |

**rika status (2026-07-01)**: rika API returned HTTP 403 during
Phase-0. rika is not a dependency of this pipeline. Frame-by-frame
battle animations are deferred (non-MVP). The pipeline uses gpt-image-2
for battle cut-ins and PixelLab for pawn animations.

## Cost / Rate Limits

### gpt-image-2

| Quality | Cost / image |
|---|---|
| `low` | ~$0.006 |
| `medium` | ~$0.053–0.07 |
| `high` | ~$0.19–0.21 |

OpenAI Tier 1 = 5 IPM. For exploratory iterations use `--quality low`
+ `--dry-run` first. Batch API gives 50% discount (async, 24h max;
not yet wired in the pipeline — Phase 3 follow-up).

### PixelLab

| Endpoint | Cost / call |
|---|---|
| Pixflux 64×64 | ~$0.00793 |
| 8-direction character 64×64 | ~$0.0173 |
| BitForge / Pro 256×256 | ~$0.095 |

Free tier: 40 trial calls (no credit card). $30 credit ≈ 1 700+ Pixflux
calls or ~350 8-direction calls. Subscriptions from $12/month (Tier 1
annual).

### Aseprite

One-time $19.99 (Steam) or free (LibreSprite / self-compile). No per-use cost.

### Worst-case budget estimate

A complete first character (1 portrait + 2 icons + 1 cut-in + 1 pawn
with walk animations) at medium/standard quality:

| Asset | Calls | Unit | Subtotal |
|---|---|---|---|
| portrait | 1 | $0.07 | $0.07 |
| icons ×2 | 2 | $0.07 | $0.14 |
| cut-in | 1 | $0.07 | $0.07 |
| pawn (7 animations, pixflux) | ~10 | $0.008 | $0.08 |
| **Total** | | | **~$0.36** |

With retry budget (`--max-retries 2`): worst case ~$0.80 for the full
character set. Well within the $30 one-time budget validated in the ADR.

## Flow Gates

1. **Dry-run gate**: always run `--dry-run` before real generation to
   validate prompt composition and catch bible validation errors at
   zero cost.
2. **Verify gate**: the pipeline runs the animate-frames verify rubric
   on pawn frames (frame count / dimension uniformity / palette
   quantization / dHash consistency). A `passed=false` report is a
   hard stop — inspect `verify.fail_reasons` + `verify.summary` and
   rerun.
3. **Visual review**: inspect generated PNGs before staging handoff.
   The pipeline cannot judge artistic intent.
4. **Staging handoff**: assets land in `--out-dir`; the user imports
   them into the game project manually. Mercury never commits to the
   game repository.
5. **Mercury change gate**: pipeline code, adapters, and skill changes
   go to the `develop` branch via PR + dual-verify per the Mercury
   standard workflow. No exceptions.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `error: failed to load bible` | Malformed JSON or missing `name` field | `jq . bible.json`; check required field |
| `error: OPENAI_API_KEY not set` | Credential missing for portrait/icon/cutin | Add to `.env` or export in shell |
| `error: PIXELLAB_API_TOKEN not set` | Credential missing for pawn | Add to `.env` or export in shell |
| `error: Pillow not installed` | Missing required dep | `pip install Pillow requests` |
| PixelLab pydantic error in logs | SDK v1.0.5 `Usage` model bug | Adapter uses REST direct; this is handled — image was generated; check `outputs.sheet` path |
| `passed=false`, `dimension_uniformity` | Pawn ref image size mismatch | PixelLab requires `init_image` == output size; resize reference to 64×64 before passing |
| Aseprite pass silently skipped | `aseprite` not on PATH | Install Aseprite / LibreSprite or accept the pure-Python packer fallback; the pawn report's `quantized` stays `false` and an advisory is written to stderr |
| Cut-in has UI overlays or text | Missing "no UI/no text" suffix | Confirm `--asset cutin` (suffix is auto-applied); do not pass competing `--scene` text that re-introduces UI |
| `.tres` animations wrong loop setting | `death` animation must have `loop=false` | Check `.tres` output; `loop=false` is the default for the `death` key (`DEFAULT_NON_LOOPING` in `scripts/sot_pixel/godot_import.py`) — if wrong, file a bug there |
| `error: failed to launch …` (exit 127) | Python path or repo-root resolution | Confirm `python .claude/skills/sot-pixel-pipeline/invoke.py --help` works from repo root |
| PixelLab `animate-with-text` capped at 64px | API constraint | Use `--size 64` (default); for 128px use bitforge single-image calls + manual animation |

## Mercury Workflow Integration

```
1. Write/update character-bible JSON (version-controlled under assets/characters/)
2. Dry-run → validate prompts
3. Real generation → inspect PNGs + verify report
4. Stage → copy outputs to game project staging directory
5. User imports assets to game project (Mercury never touches game repo)
6. Pipeline code changes → develop branch → PR + dual-verify → merge
```

The pipeline plugs into `/dev-pipeline` via `verifyCommands` using
`--dry-run` (exits 0 without API contact; validates prompt composition
and bible schema).

## See Also

- [Selection ADR + Phase-0 results](../research/sot-pixel-asset-pipeline-selection-2026-06.md) — §6 architecture, §11 Phase-0 results, §13 final matrix
- [animate-frames guide](pixel-animation-workflow.md) — frame-sequence animation pipeline (shared character-bible contract)
- [`SKILL.md`](../../.claude/skills/sot-pixel-pipeline/SKILL.md) — skill entrypoint
- [`adapters/gpt-image-2/`](../../adapters/gpt-image-2/) — portrait / icon / cut-in adapter (Slice A, MIT)
- [`adapters/pixellab/`](../../adapters/pixellab/) — pawn adapter (REST direct)
- [`adapters/aseprite/`](../../adapters/aseprite/) — palette post-process adapter (optional)
- [`scripts/sot_pixel/`](../../scripts/sot_pixel/) — pipeline orchestration
- [`CLAUDE.md`](../../CLAUDE.md) §"External-project adapters" — 200-LOC adapter rule + `scripts/` carve-out
