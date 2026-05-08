---
name: animate-frames
description: |
  Mercury's pixel-frame animation pipeline — generate a sprite frame
  sequence (e.g. 4-frame walking cycle, idle pose set, attack arc) from a
  character bible JSON + scenes JSON, with built-in verify rubric (frame
  count, dimension uniformity, palette quantization, dHash consistency,
  optional loop closure) and structured retry loop. **Use proactively
  whenever the user asks to generate any frame sequence, sprite sheet,
  walking cycle, or animated pixel asset** — even if they don't say
  "animate-frames" explicitly. Triggers: 'animate frames', 'sprite
  sheet', 'sprite frames', 'walking cycle', 'frame sequence', 'pixel
  animation', '生成精灵帧', '动画帧序列', '像素动画', '精灵表'. Wraps
  `scripts.image_gen` (Slice B) which in turn invokes the
  `adapters/gpt-image-2/` adapter (Slice A, MIT mount of
  `wuyoscar/gpt_image_2_skill` via uvx-pinned-SHA). Phase 2 Slice C of
  Mercury Issue #351.
user-invocable: true
allowed-tools: Bash, Read, Write, Glob
---

# animate-frames — Mercury Pixel Frame Animation

Skill-level entrypoint for Mercury's per-frame sprite generation pipeline.
The actual generation, verify, and retry logic live in
`scripts/image_gen/` (Slice B); this skill is the **agent-facing CLI
wrapper** plus a small template helper that emits a starter scenes JSON.

## When to use

Use this skill when the user wants:

- A 2D pixel sprite frame sequence (walking cycle, idle, attack arc, …)
- A multi-pose character sheet generated against the **same** identity
- Any animation-grade frame set where character / palette / dimension
  consistency matters more than per-frame artistic flourish
- Cross-session character continuity (the character bible JSON is the
  ground truth across model sessions per ADR §5.6)

**Do NOT use** for:

- Single one-off images — call the adapter directly
  (`adapters/gpt-image-2/invoke.py -p "..." -f out.png`)
- Video / GIF encoding (out of Phase 2 scope per ADR §7.3)
- LoRA fine-tuning / motion-quality scoring (Phase 3)

## Prerequisites

| Requirement | Why |
|---|---|
| `uv` / `uvx` ≥ 0.10 on PATH | Slice A adapter mounts upstream via `uvx --from "git+..."` |
| Python 3.11+ | `scripts.image_gen` + adapter |
| `OPENAI_API_KEY` (Tier 1+ verified org) | Real generation; not needed for `--help` / `--dry-run` / `--example` |
| `pip install Pillow ImageHash scikit-image numpy` | Verify rubric hard gates. The pipeline **hard-fails** in non-`--dry-run` mode if any of these are missing (Pillow, ImageHash always; scikit-image only when `--loop-closure`). Pass `--allow-skipped-gates` to opt into running with the affected gates degraded to `severity=skipped, passed=true` — see Failure modes for the false-positive caveat. |

First adapter invocation downloads upstream and builds an isolated venv
(~5–15s cold start). Subsequent invocations are cached by `uvx`.

See [`.mercury/docs/guides/pixel-animation-workflow.md`](../../../.mercury/docs/guides/pixel-animation-workflow.md)
for the full agent calling guide, character bible JSON schema, scene
schema, verify rubric details, retry semantics, and end-to-end examples.

## Inputs

- **Character bible JSON** — identity, palette, style, lighting, camera,
  constraints, reference images. Schema in workflow guide §"Character
  Bible JSON schema". Loaded by `scripts.image_gen.character_bible`.
- **Scenes JSON** — array of `{index, scene, filename}` objects, one per
  frame. Schema in workflow guide §"Scenes JSON schema". Loaded by
  `scripts.image_gen.__main__._load_scenes`.

## Usage

### 1. Bootstrap a starter scenes JSON

```bash
python .claude/skills/animate-frames/invoke.py --example walking-cycle > scenes.json
```

Templates available: `walking-cycle` (4 frames), `idle` (2 frames),
`attack-arc` (3 frames). Outputs canonical JSON the pipeline accepts
verbatim — agents typically tweak the `scene` text only.

The character bible JSON is project-specific and not auto-templated;
write it by hand following the schema in the workflow guide.

### 2. Run the pipeline

```bash
python .claude/skills/animate-frames/invoke.py \
    --bible knight.json \
    --scenes scenes.json \
    --out-dir frames/
```

All flags after `--bible` are forwarded verbatim to
`python -m scripts.image_gen` — see `--help` for the full list (model,
size, quality, format, background, timeout, max-retries, palette /
dHash / SSIM thresholds, `--loop-closure`, `--reference-size`,
`--dry-run`, `--allow-skipped-gates`).

### 3. Inspect the JSON report

The pipeline writes a structured report to stdout:

```json
{
  "passed": true,
  "total_attempts": 1,
  "final_fail_reasons": [],
  "attempts": [
    {
      "attempt": 1,
      "passed": true,
      "frame_results": [...],
      "verify": {
        "passed": true,
        "fail_reasons": [],
        "gates": [...]
      }
    }
  ]
}
```

`passed=false` with non-empty `final_fail_reasons` means the retry
budget (default 3) was exhausted; inspect each attempt's
`verify.gates` to see which gate failed and why.

## Iron rules

| Rule | Why |
|---|---|
| **Skill is a thin wrapper** | All gen / verify / retry logic lives in `scripts/image_gen/`. Do not duplicate logic here. |
| **Forward args verbatim** | Agents and humans who learn the `scripts.image_gen` CLI should not need to relearn anything for the skill path. `--example` is the only skill-only flag. |
| **No `OPENAI_API_KEY` reads** | The adapter resolves credentials. The skill never reads, logs, or writes the key. |
| **Templates are starter scaffolds** | The `--example` templates are conventional starting points, not enforced contracts. Users edit `scene` text freely. |

## Cost guardrails

GPT Image 2 Tier 1 = 5 IPM, ~$0.053 / 1024² medium frame. A 4-frame
walking cycle with `--max-retries=3` worst case = 16 generations ≈
$0.85; budget upward as quality / frame count scales. Batch API
provides 50% discount — see workflow guide §"Cost / rate limits".

## Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `error: failed to load bible …` | Malformed JSON or missing `name` | Validate with `jq . bible.json`; check workflow guide schema |
| `error: scenes[i].filename … resolves outside out-dir` | Path traversal in scene filenames (`../`, absolute) | Use plain filenames (`frame_00.png`); pipeline rejects on path-traversal hardening |
| `error: verify rubric dependencies missing: ['Pillow', ...]` | Verify deps not installed (pipeline hard-fails by default) | `pip install Pillow ImageHash scikit-image numpy` (recommended) **OR** pass `--allow-skipped-gates` to bypass — note this is an explicit safety bypass intended for `--dry-run` / dev scenarios; with skipped gates the run can report `passed=true` without any real frame inspection (false-positive risk). |
| `passed=false`, `frame_count` failed | Adapter timeout / API rate limit / auth | Check `attempts[*].frame_results[*].stderr` (credentials are scrubbed); raise `--timeout`; check OPENAI tier |
| `passed=false`, `character_consistency` failing | Identity drift across frames | Add stronger anchor block items; supply more reference images in the bible; lower `--dhash-threshold` for stricter gate |

## Detachability

This skill assumes Mercury's `scripts/image_gen/` + `adapters/gpt-image-2/`
layout. Porting elsewhere requires copying both layers plus this skill,
or pointing `MERCURY_GPT_IMAGE_2_ADAPTER` env var at an adapter that
implements the same `-p / -f / -i / --model / --size / --quality /
--format / --background` CLI surface.

## Related

- [`.mercury/docs/guides/pixel-animation-workflow.md`](../../../.mercury/docs/guides/pixel-animation-workflow.md) — full agent calling guide
- [`.mercury/docs/research/pixel-animation-workflow-2026-05-08.md`](../../../.mercury/docs/research/pixel-animation-workflow-2026-05-08.md) — Phase 1 ADR
- [`scripts/image_gen/`](../../../scripts/image_gen/) — Slice B pipeline
- [`adapters/gpt-image-2/`](../../../adapters/gpt-image-2/) — Slice A adapter
