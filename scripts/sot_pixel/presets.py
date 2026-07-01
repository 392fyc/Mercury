"""gpt-image-2 single-image asset presets (portrait / icon / cut-in).

Each preset fixes the gpt-image-2 generation parameters (size, quality,
background) and a deterministic prompt suffix appended to the character
bible's composed prompt. gpt-image-2 cannot emit transparent backgrounds
(only auto/opaque), so every HD asset uses an opaque background.

`build_gpt_args` returns the argv for the gpt-image-2 adapter
(`adapters/gpt-image-2/invoke.py`) — it never reads `OPENAI_API_KEY`;
authentication is delegated entirely to the adapter/upstream.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from scripts.image_gen.character_bible import CharacterBible

ADAPTER_PATH_ENV = "MERCURY_GPT_IMAGE_2_ADAPTER"
DEFAULT_ADAPTER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "adapters" / "gpt-image-2" / "invoke.py"
)


@dataclass(frozen=True)
class AssetPreset:
    """gpt-image-2 parameters + prompt suffix for one HD asset type."""
    size: str
    quality: str
    background: str
    prompt_suffix: str


# Prompt suffixes are exact-repetition fragments (no synonym swap) so a
# given asset type renders consistently across calls. The cut-in suffix
# carries a hard negative tail because battle cut-ins must never contain
# UI chrome — gpt-image-2 otherwise tends to hallucinate health bars and
# interface text into dramatic close-ups.
ASSET_PRESETS: dict[str, AssetPreset] = {
    "portrait": AssetPreset(
        size="1024x1536",
        quality="high",
        background="opaque",
        prompt_suffix=", full-body character portrait, anime high-detail, "
                      "clean background",
    ),
    "icon": AssetPreset(
        size="1024x1024",
        quality="high",
        background="opaque",
        prompt_suffix=", game UI skill/item icon, ornate frame, centered, "
                      "high-detail pseudo-pixel style",
    ),
    "cutin": AssetPreset(
        size="1024x1536",
        quality="high",
        background="opaque",
        prompt_suffix=", dynamic battle cut-in close-up, NS-Fire-Emblem "
                      "style, dramatic lighting"
                      " — no UI, no text, no health bars, no interface "
                      "elements, no letters, no numbers",
    ),
}


def resolve_adapter_path() -> Path:
    """Resolve the gpt-image-2 adapter path (env override aware).

    Honors `MERCURY_GPT_IMAGE_2_ADAPTER` (shared convention with
    `scripts.image_gen.pipeline`) so this layer is not pinned to one repo
    layout; otherwise falls back to the sibling adapter.
    """
    override = os.environ.get(ADAPTER_PATH_ENV)
    return Path(override).resolve() if override else DEFAULT_ADAPTER_PATH


def compose_asset_prompt(bible: CharacterBible, asset: str,
                         scene: str = "") -> str:
    """Compose the final prompt = bible anchor + scene + preset suffix.

    The preset suffix is appended verbatim to the bible's composed prompt
    (`CharacterBible.compose_prompt`), which already pins identity, style,
    lighting, camera, and constraints.
    """
    preset = _require_preset(asset)
    return bible.compose_prompt(scene) + preset.prompt_suffix


def build_gpt_args(bible: CharacterBible, asset: str, scene: str,
                   out_path: Path, size_override: str | None = None,
                   quality_override: str | None = None) -> list[str]:
    """Build the gpt-image-2 adapter argv for one HD single-image asset.

    Forwards one `-i` reference per `bible.reference_images`. Does NOT
    read or forward `OPENAI_API_KEY` — the adapter owns auth.
    """
    preset = _require_preset(asset)
    prompt = compose_asset_prompt(bible, asset, scene)
    adapter = resolve_adapter_path()
    argv: list[str] = [
        sys.executable, str(adapter),
        "-p", prompt,
        "-f", str(out_path),
        "--model", "gpt-image-2",
        "--size", size_override or preset.size,
        "--quality", quality_override or preset.quality,
        "--format", "png",
        "--background", preset.background,
    ]
    for ref in bible.reference_images:
        argv.extend(["-i", str(ref)])
    return argv


def _require_preset(asset: str) -> AssetPreset:
    try:
        return ASSET_PRESETS[asset]
    except KeyError as exc:
        raise ValueError(
            f"unknown single-image asset {asset!r}; "
            f"expected one of {sorted(ASSET_PRESETS)}"
        ) from exc
