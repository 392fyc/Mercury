# Based on wuyoscar/gpt_image_2_skill (MIT) SHA: 6fdd7243dc9605efcf6d66e9394d3d10fc5141f6

# gpt-image-2

Mercury adapter for OpenAI `gpt-image-2` image generation. Mounts
[`wuyoscar/gpt_image_2_skill`](https://github.com/wuyoscar/gpt_image_2_skill)
(MIT) at a pinned SHA via `uvx`, with no submodule and no vendored copy of
the upstream source.

## How it works

`invoke.py` is a thin pass-through. It validates `uvx` is on PATH and
`OPENAI_API_KEY` is exported, then runs:

```
uvx --from "git+https://github.com/wuyoscar/gpt_image_2_skill@<SHA>" gpt-image <args>
```

All arguments are forwarded verbatim to the upstream `gpt-image` console
script. The pinned SHA is encoded in `invoke.py` (see `SHA` constant); the
manifest entry tracks `pyproject.toml` so `scripts/upstream-drift-check.sh`
flags upstream version bumps (deps, entry points) that may affect the
adapter contract.

## Requirements

| Tool | Minimum |
|---|---|
| `uv` / `uvx` | 0.10+ (see [docs.astral.sh/uv](https://docs.astral.sh/uv/)) |
| Python | 3.11+ (upstream requirement; `uvx` provisions) |
| `OPENAI_API_KEY` | required for actual generation; bypassed for `--help`/`--version` |

First invocation downloads upstream + builds an isolated venv (~5–15s cold
start). Subsequent invocations are cached.

## Usage

```
python adapters/gpt-image-2/invoke.py --help
python adapters/gpt-image-2/invoke.py -p "pixel art knight, idle pose" -f knight.png
```

Mercury's `scripts/image_gen/` layer (Slice B) wraps this adapter with
character-bible composition, reference chain orchestration, and verify rubric.
End users typically call the layer above; this adapter is the I/O boundary.

## Cherry-pick metadata

See [`UPSTREAM.md`](UPSTREAM.md) for upstream pin, license attribution, and
drift-check policy. Manifest entry in
`.mercury/state/upstream-manifest.json`.

## License

`invoke.py` is original Mercury code (MIT, project license). Upstream
`gpt-image-cli` is MIT (preserved verbatim via uvx — not redistributed by
Mercury).
