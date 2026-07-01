# pixellab

Mercury adapter for [PixelLab.ai](https://www.pixellab.ai) pixel-art
generation (v1 REST API). **Original Mercury code, not a cherry-pick** — a
thin direct-HTTP client, so no `upstream-manifest` entry and no SHA pin
(there is no vendored upstream source to track).

`invoke.py` is exactly **200 LOC — at the `adapters/<vendor>/` cap**. Per
CLAUDE.md the cap triggers a "rethink the mounting approach" only *above*
200 ("exceeding 200 lines" in the DO-NOT list; "needs more than that" in
the MUST bullet), so 200 is compliant — consistent with the `adapters/
aseprite/` README's `≤200 LOC` statement. The three endpoints are already
factored to the minimum direct-HTTP surface; there is no vendored upstream
to extract, so the line count is essential, not slop.

## Why REST-direct (not the SDK)

The official `pixellab` Python SDK (v1.0.5) hardcodes
`Usage.type = Literal["usd"]`, but the live API returns
`usage.type == "generations"`. The SDK raises a pydantic
`ValidationError` while parsing the response **even though the image was
generated successfully**. Parsing the raw JSON ourselves sidesteps the
bug. (Verified against `api.pixellab.ai/v1/openapi.json`, 2026-07-01.)

## Endpoints

| `--endpoint` | API path | Output | Notes |
|---|---|---|---|
| `pixflux` | `/v1/generate-image-pixflux` | single PNG (`--out`) | text→pixel; `image.base64` |
| `bitforge` | `/v1/generate-image-bitforge` | single PNG (`--out`) | style/init reference; `image.base64` |
| `animate` | `/v1/animate-with-text` | N frames (`--out-dir`) | **fixed 64×64**; `images[i].base64` |

## Auth

`Authorization: Bearer <token>`. The token is read from `PIXELLAB_API_TOKEN`
(override with `--token-env`); if absent from the environment the adapter
falls back to a minimal parse of the repo-root `.env`. The token value is
never written to stdout/stderr.

## Usage

```bash
# 64×64 pixel pawn (transparent background)
python adapters/pixellab/invoke.py --endpoint pixflux \
  --description "red-haired swordsman, facing south, full body" \
  --width 64 --height 64 --no-background --opt direction south \
  --out staging/pawn_south.png

# keep identity by conditioning on a portrait (resized to output size)
python adapters/pixellab/invoke.py --endpoint bitforge \
  --description "..." --width 64 --height 64 --no-background \
  --init-image portrait.png --init-strength 300 --out staging/pawn.png

# 4-frame walk animation (64×64 only)
python adapters/pixellab/invoke.py --endpoint animate \
  --description "red-haired swordsman" --action walk --n-frames 4 \
  --reference-image staging/pawn_south.png --out-dir staging/walk/
```

`--opt KEY VALUE` is a forward-compatible passthrough for PixelLab string
params (`direction`, `view`, `outline`, `shading`, `detail`, …) so new API
fields work without code changes. Reference images (`--init-image`,
`--style-image`, `--reference-image`) are resized to **exactly** the output
size with NEAREST before encoding — PixelLab requires reference size ==
output size.

The adapter prints a JSON summary to stdout (`endpoint`, output path(s),
`size`, `usd`). Exit codes: `0` success, `1` API/runtime error, `2`
usage/auth error. Requires `Pillow` + `requests`.

## Constraints (verified)

- `animate-with-text` is **fixed at 64×64** at v1 (the adapter forces it).
- `bitforge` max `image_size` is 200×200 at v1; `pixflux` up to
  400×400 / 320×320 / 200×200 by tier.
- The `Base64Image.type` value is sent as `"base64"` (UNVERIFIED exact
  literal in the OpenAPI; matches the SDK + Phase-0 empirical run).

## Layer

This adapter is the I/O boundary for the **pawn** asset type in Mercury's
SoT pixel pipeline. `scripts/sot_pixel/` orchestrates it (character-bible →
PixelLab → pack → verify → Godot SpriteFrames). See
[`.mercury/docs/guides/sot-pixel-pipeline.md`](../../.mercury/docs/guides/sot-pixel-pipeline.md).
