# Pixel Animation Workflow — Agent Calling Guide

Authoritative agent-facing guide for Mercury's pixel-frame animation
pipeline (Issue [#351](https://github.com/392fyc/Mercury/issues/351),
Phase 2 Slice C). Pairs with the
[Phase 1 ADR](../research/pixel-animation-workflow-2026-05-08.md), which
holds the full design rationale, sources, and risk register; this
document is the operational reference an agent reads before invoking
`/animate-frames`.

## TL;DR

```bash
# 1. Write a character bible JSON (see schema below).
cat > knight.json <<'EOF'
{
  "name": "knight",
  "identity": ["green tunic with brass buckles", "round wooden shield with iron rim", "short brown hair", "stocky proportions"],
  "color_palette": ["#3A4F3A", "#A87432", "#7C5A3F", "#1F1F1F"],
  "style": "2D pixel art, 32x32 tile, 4-color outline",
  "lighting": "soft front, no hard shadows",
  "camera": "front, full body",
  "constraints": ["no text", "no watermarks", "no redesign"],
  "reference_images": ["sprites/knight_base.png"]
}
EOF

# 2. Bootstrap a starter scenes JSON.
python .claude/skills/animate-frames/invoke.py --example walking-cycle > scenes.json

# 3. Run the pipeline (real generation requires OPENAI_API_KEY).
python .claude/skills/animate-frames/invoke.py \
    --bible knight.json --scenes scenes.json --out-dir frames/ \
    --reference-size 1024x1024 --max-retries 3
```

The pipeline writes a JSON report to stdout. `passed=true` means every
hard gate passed and every frame was generated; the frames are at
`frames/frame_00.png` … `frames/frame_03.png`.

## Architecture

Three layers, oldest → newest:

```
adapters/gpt-image-2/         # Slice A — uvx-pinned-SHA mount of upstream
└── invoke.py                 #          (wuyoscar/gpt_image_2_skill, MIT)

scripts/image_gen/            # Slice B — Mercury pipeline (uncapped LOC)
├── character_bible.py        #          load + anchor block composition
├── pipeline.py               #          per-frame reference chain
├── verify.py                 #          default rubric (hard + soft gates)
├── retry_loop.py             #          1 + max_retries with feedback
└── __main__.py               #          `python -m scripts.image_gen` CLI

.claude/skills/animate-frames/    # Slice C — agent-facing skill wrapper
├── SKILL.md                      #          frontmatter + usage doc
└── invoke.py                     #          thin wrapper + --example helper
```

The skill calls `scripts.image_gen` as a subprocess. `scripts.image_gen`
calls the adapter via subprocess (with a credential-allowlist filtered
env). The adapter calls upstream via `uvx --from "git+..."` at the pinned
SHA. Each layer is replaceable — point
`MERCURY_GPT_IMAGE_2_ADAPTER` at a different adapter binary to swap the
backend without touching the pipeline or the skill.

## Prerequisites

| Requirement | Why | Verification |
|---|---|---|
| `uv` / `uvx` ≥ 0.10 | Slice A mount mechanism | `uvx --version` |
| Python 3.11+ | Slice B + adapter | `python --version` |
| `OPENAI_API_KEY` (Tier 1+ verified org) | Real generation | `[ -n "$OPENAI_API_KEY" ] && echo set` (presence only — never echo any portion of the value) |
| `Pillow` | verify dimension + palette gates | `python -c "import PIL"` |
| `ImageHash` | verify character_consistency dHash | `python -c "import imagehash"` |
| `scikit-image` + `numpy` | verify loop_closure SSIM (only when `--loop-closure` enabled) | `python -c "import skimage, numpy"` |

Install verify deps:

```bash
pip install Pillow ImageHash scikit-image numpy
```

If you skip these, the rubric gates that need them degrade to
`severity=skipped, passed=true` — the run will *appear* to pass without
actually inspecting frames. Slice B treats this as a hard fail by
default; pass `--allow-skipped-gates` only when you understand the
implication (e.g. `--dry-run` testing).

## Character Bible JSON schema

| Field | Type | Required | Purpose |
|---|---|---|---|
| `name` | string | ✅ | Human-readable identifier; surfaces in error messages and logs |
| `identity` | list[string] | recommended | Anchor block "Character Consistency: [...]"; **exact-repetition** items per ADR §5.2 |
| `color_palette` | list[string] | recommended | Hex colors; surfaces as anchor block "Color Palette: [...]" |
| `style` | string | recommended | One-line style descriptor (medium, resolution, art direction) |
| `lighting` | string | recommended | Lighting / shading constraint |
| `camera` | string | recommended | Angle + distance descriptor |
| `constraints` | list[string] | recommended | Anchor block "Constraints: [...]"; e.g. "no text", "no watermarks" |
| `reference_images` | list[string] | optional | Paths (relative to bible JSON or absolute) — forwarded to adapter as `-i` flags |

Loaded by `scripts.image_gen.character_bible.CharacterBible.load`. Strict
type validation: every list element must be a string (not int, dict, or
null); a single string in a list field is permitted as convenience.

The anchor block is composed deterministically — same bible JSON
always emits byte-identical anchor text. Order is fixed (identity →
palette → style → lighting → camera → constraints) and not
configurable; see `character_bible.py:74` for the canonical order.

### Bible authoring tips (per ADR §5)

- **Use exact-repetition phrases** in `identity`. Don't write "knight",
  "warrior", "soldier" interchangeably across frames — pick one and
  repeat verbatim.
- **Keep `identity` small** (3–6 items). Each item should be a concrete
  visual attribute the model can fix on (palette / silhouette /
  proportion / costume detail).
- **`reference_images` is the strongest anchor**. Even a single well-lit
  base sprite reduces drift more than any prose. Per ADR §5.5, the best
  practice is: generate base character first, then use it as ref for
  every subsequent frame.
- **Don't put scene-specific text in the bible**. The bible should
  apply unchanged across all frames; per-frame variation lives in the
  scenes JSON.

## Scenes JSON schema

A JSON array; each element is one frame:

| Field | Type | Required | Purpose |
|---|---|---|---|
| `index` | int | optional (defaults to array position) | Stable frame identifier; surfaces in JSON report |
| `scene` | string | recommended | Per-frame action / pose description; concatenated after the anchor block |
| `filename` | string | optional (defaults to `frame_NN.png`) | Output filename within `--out-dir`; **must not contain `..` or absolute paths** (rejected by path-traversal hardening) |

Constraints enforced by `scripts.image_gen.__main__._load_scenes`:

- Top-level value must be a JSON array, non-empty
- `index` must be unique across all frames (no duplicates)
- Resolved output paths must be unique (two frames cannot target the
  same file — would silently desync `frame_count` from on-disk artifacts)
- `filename` must resolve under `--out-dir` (no `../` escapes, no
  absolute paths into other directories)

### Templates

`invoke.py --example <name>` emits canonical scenes JSON for three
common patterns:

| Template | Frames | Use case |
|---|---|---|
| `walking-cycle` | 4 | Side-view walk loop; pairs naturally with `--loop-closure` |
| `idle` | 2 | Standing breath / blink loop |
| `attack-arc` | 3 | Wind-up → swing → follow-through |

Templates are starting points. Edit the `scene` text freely to match
your character and direction.

## Anchor block + per-frame prompt

The composed prompt sent to gpt-image-2 for each frame is:

```
<anchor block from bible>

Scene: <per-frame scene text>

[Adjustments from previous attempt: <feedback>]   # only on retry
```

Anchor goes first so the model conditions on identity before scene
direction. The optional adjustments line is added by the retry loop and
contains either previous-attempt verify failures or sanitized adapter
stderr (credentials scrubbed — see `retry_loop.py:_SECRET_PATTERNS`).

## Verify rubric

Two tiers per ADR §6.1; configured via `VerifyConfig`:

### Hard gates (a failure flips `passed=false` and triggers retry)

| Gate | What it checks | Threshold knob |
|---|---|---|
| `frame_count` | Number of generated PNGs equals `len(scenes)` | (implicit) |
| `dimension_uniformity` | All frames share size; matches `--reference-size` if set | `--reference-size WxH` |
| `palette_quantization` | Each frame's unique RGB color count ≤ N | `--max-palette N` (default 64) |

### Soft gates (advisory; reported but never block)

| Gate | What it checks | Threshold knob |
|---|---|---|
| `character_consistency` | dHash distance from frame 0 ≤ N | `--dhash-threshold N` (default 12) |
| `loop_closure` | First/last frame dHash ≤ N AND SSIM ≥ M (off by default) | `--dhash-threshold` + `--ssim-threshold` + `--loop-closure` |

Soft-gate failures populate `verify.advisories` instead of
`verify.fail_reasons`; they do not flip `passed` to false. To make a
soft gate hard, lower its threshold so violations also show in
`fail_reasons` via the corresponding hard gate (e.g. tighten
`--max-palette` if palette drift correlates with character drift).

### Threshold tuning

Thresholds in `VerifyConfig` are **calibration-grade defaults**, not
golden truth. Per ADR §11 UNVERIFIED items #9, dHash and SSIM
thresholds for pixel art have not been empirically validated. Tune them
based on your specific style:

- **High-contrast pixel art (4-bit palette)**: tighten `--dhash-threshold`
  to 8 and `--max-palette` to 16
- **Soft-edge sprites with anti-aliasing**: relax `--dhash-threshold` to
  16 and `--ssim-threshold` to 0.5
- **Loop-closure check**: enable only for cyclic animations
  (`walking-cycle`, `idle` blink) — not for unidirectional arcs
  (`attack-arc`)

## Retry semantics

`run_with_retry` in `scripts/image_gen/retry_loop.py` implements ADR
§6.4:

- **Total attempts** = `1 + max_retries` (default = 4 invocations)
- **Feedback injection** — each retry's prompt includes the previous
  attempt's verify failures and (sanitized) adapter stderr as an
  "Adjustments from previous attempt:" line
- **Secret scrubbing** — `OPENAI_API_KEY`-shaped tokens, GitHub PATs,
  AWS access keys, Bearer tokens, and `Authorization:` lines are
  redacted before composition into the next prompt
- **Final report** — `attempts[]` array contains every attempt's frame
  results + verify verdict + feedback used; `final_fail_reasons`
  populates only when the final attempt failed

When all retries exhaust, the pipeline exits 1 (vs 0 on success, 2 on
input validation error). Inspect `attempts[*].verify.gates` to see
which gate consistently failed across attempts — usually that points
to a bible / scene authoring issue rather than a model issue.

## Cost / rate limits

GPT Image 2 pricing (per ADR §3.4):

| Quality (1024×1024) | Cost / image |
|---|---|
| `low` | ~$0.006 |
| `medium` | ~$0.053 |
| `high` | ~$0.211 |

| OpenAI Tier | IPM | TPM |
|---|---|---|
| 1 | 5 | 100,000 |
| 2 | 20 | 250,000 |
| 3 | 50 | 800,000 |
| 4 | 150 | 3,000,000 |
| 5 | 250 | 8,000,000 |

For agent-driven generation:

- **Worst-case budget per invocation** = `frames × (1 + max_retries) × per_image_cost`. A 4-frame walking-cycle with `--max-retries=3` at high quality ≈ $3.4.
- **Rate-limited at Tier 1** to ~5 frames per minute; if your sequence is
  longer, plan for `--timeout` headroom or upgrade tier.
- **Batch API** offers 50% discount for asynchronous batches up to 24h
  — currently not exposed by the pipeline (Phase 3 follow-up).

Use `--quality medium` and `--dry-run` first to validate prompt
composition before incurring `--quality high` costs.

## End-to-end example: 4-frame walking cycle

```bash
# Setup
mkdir -p .tmp/walk-demo && cd .tmp/walk-demo

# 1. Bible
cat > knight.json <<'EOF'
{
  "name": "knight",
  "identity": [
    "green tunic with brass buckles",
    "round wooden shield with iron rim",
    "short brown hair under leather cap",
    "stocky proportions, square shoulders"
  ],
  "color_palette": ["#3A4F3A", "#A87432", "#7C5A3F", "#1F1F1F", "#E0D8B0"],
  "style": "2D pixel art, 32x32 tile, hard 1-pixel outline, no anti-aliasing",
  "lighting": "soft front-left, no cast shadows",
  "camera": "side view, full body, character centered",
  "constraints": ["no text", "no watermarks", "no redesign", "no perspective tricks"]
}
EOF

# 2. Scenes
python .claude/skills/animate-frames/invoke.py --example walking-cycle > scenes.json

# 3. Dry-run prompts (no API call)
python .claude/skills/animate-frames/invoke.py \
    --bible knight.json --scenes scenes.json --out-dir frames/ --dry-run

# 4. Real generation
python .claude/skills/animate-frames/invoke.py \
    --bible knight.json --scenes scenes.json --out-dir frames/ \
    --quality medium --reference-size 1024x1024 --max-retries 3 \
    --loop-closure --dhash-threshold 10 \
    > report.json

# 5. Inspect
jq '.passed, .total_attempts, .final_fail_reasons' report.json
ls frames/
```

Expected on success: `frames/frame_00.png` … `frames/frame_03.png`,
`report.json` with `"passed": true`, all four frames at the configured
size.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `error: failed to load bible` | Malformed JSON, missing `name`, wrong types | `jq . bible.json`; verify required field |
| `error: scenes[i].filename ... resolves outside out-dir` | Path traversal in filename | Use plain filenames; pipeline rejects `..` and absolutes |
| `error: scenes[i].index=N is a duplicate` | Two frames with same `index` | Make indexes unique; if intentional duplicate output, use distinct filenames |
| `error: verify rubric dependencies missing: ['Pillow']` | Verify deps not installed | `pip install Pillow ImageHash scikit-image numpy` |
| `passed=false`, `frame_count` failing across all attempts | Adapter timeouts, API rate limit, auth | Inspect `attempts[*].frame_results[*].stderr` (credentials scrubbed); check `OPENAI_API_KEY` tier |
| `passed=false`, `palette_quantization` failing | Generated frames have too many unique colors (anti-aliasing, gradients) | Strengthen bible's `style` constraint ("no anti-aliasing", "indexed palette only"); raise `--max-palette` if palette drift is acceptable |
| `passed=true` but `character_consistency` advisory present | Soft gate flagged drift; not a failure | If drift unacceptable, tighten `--dhash-threshold` and re-run; or add reference images to bible |
| Need transparent background but `gpt-image-2` doesn't support it (per ADR §3.2) | `--background` argparse choices are limited to `auto \| opaque` precisely because `gpt-image-2` rejects `transparent` upstream | Switch model: pass `--model gpt-image-1.5` (which supports transparent BG); the adapter forwards model selection upstream verbatim |
| Adapter cold start very slow | First `uvx` invocation downloads upstream | Subsequent calls cached; budget ~5–15s on first run |

## Mercury workflow integration

The animation pipeline plugs into Mercury's standard skill chain:

1. **Research / decide on visual asset spec** — ADR or task description
   pins style, palette, frame count, identity rules
2. **Write bible + scenes JSON** — typically committed under
   `.tmp/<task>/` for one-off generation, or under
   `assets/sprites/<character>/` for durable assets
3. **Invoke `/animate-frames`** — produces frames + report
4. **Verify report** — agent or human inspects `passed`, advisories,
   and reviews PNGs visually
5. **Commit** — frames + report + bible + scenes go in via standard
   `/dual-verify` → `/pr-flow` flow if the assets are durable; one-off
   generation leaves artifacts in `.tmp/` per `.gitignore`

When integrating into `/dev-pipeline`, the bundle's `verifyCommands`
should include the dry-run path (`--dry-run` exits 0 without API
contact) so acceptance can validate prompt composition without
incurring generation cost.

## Phase 3 plug-in roadmap

Per ADR §7.3 / §6.2, these are explicitly **out of Phase 2 scope** but
have a defined plug-in surface for Phase 3:

| Capability | Insertion point | Notes |
|---|---|---|
| FLUX 2 backend | `MERCURY_GPT_IMAGE_2_ADAPTER` env var → alternate adapter | Native 10 ref-image support; better for strict pixel-level continuity |
| Recraft V3 (icon/UI sets) | New adapter under `adapters/recraft-v3/` | Set-mode for same-style batch generation |
| LPIPS perceptual gate | New gate in `verify.py` (mirror `_check_consistency`) | >500MB model; only use if dHash insufficient |
| CLIP image-text alignment | New gate; sample 3 frames per attempt | 340MB model; verifies prompt → frame alignment |
| Batch API (50% off) | New code path in `pipeline.py:invoke_adapter` | Async, up to 24h turnaround |
| Video / GIF encoding | Post-processing step after pipeline success | `ffmpeg` or `imageio`; out of scope for image-only pipeline |
| LoRA fine-tuning workflow | Separate skill; not pipeline-integrated | CharForge / Scenario; high cost |

## See also

- [Phase 1 ADR](../research/pixel-animation-workflow-2026-05-08.md) — full design rationale, sources, risks
- [Slice A adapter](../../../adapters/gpt-image-2/README.md)
- [Slice B pipeline](../../../scripts/image_gen/__main__.py) — `python -m scripts.image_gen --help`
- [Slice C skill](../../../.claude/skills/animate-frames/SKILL.md)
- [`CLAUDE.md`](../../../CLAUDE.md) §"External-project adapters" — 200-LOC adapter rule + `scripts/` carve-out
- [`CLAUDE.md`](../../../CLAUDE.md) §"Cherry-pick protocol" — applied to Slice A; **not** to Slice B/C (Mercury-original)
