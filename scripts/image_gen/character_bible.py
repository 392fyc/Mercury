"""Character bible loader and anchor block composer.

Per `.mercury/docs/research/pixel-animation-workflow-2026-05-08.md` §5
(Character / Style Consistency 工程实践):

- §5.2 Anchor Block — exact repetition, no synonym swap
- §5.3 Style Block — JSON-encoded style metadata
- §5.6 Cross-session Continuity — Character Bible JSON as ground truth

Schema (loosely; missing keys default to empty/None):

    {
      "name":              "knight",
      "identity":          ["same green tunic", "same round shield", ...],
      "color_palette":     ["#3A86FF", "#FF006E", "#FFBE0B"],
      "style":             "2D pixel art, 32x32 tile",
      "lighting":          "soft front, no hard shadows",
      "camera":            "front, full body",
      "constraints":       ["no text", "no watermarks"],
      "reference_images":  ["sprites/knight_base.png"]
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CharacterBible:
    name: str
    identity: tuple[str, ...] = ()
    color_palette: tuple[str, ...] = ()
    style: str = ""
    lighting: str = ""
    camera: str = ""
    constraints: tuple[str, ...] = ()
    reference_images: tuple[Path, ...] = ()
    source_path: Path | None = field(default=None, compare=False)

    @classmethod
    def load(cls, path: Path) -> "CharacterBible":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"bible {path} must be a JSON object, got {type(data).__name__}")
        name = data.get("name")
        if not name or not isinstance(name, str):
            raise ValueError(f"bible {path} missing required 'name' (string)")
        base = Path(path).resolve().parent
        refs = tuple(
            (base / r) if not Path(r).is_absolute() else Path(r)
            for r in _as_list(data.get("reference_images"))
        )
        return cls(
            name=name,
            identity=tuple(_as_list(data.get("identity"))),
            color_palette=tuple(_as_list(data.get("color_palette"))),
            style=data.get("style", "") or "",
            lighting=data.get("lighting", "") or "",
            camera=data.get("camera", "") or "",
            constraints=tuple(_as_list(data.get("constraints"))),
            reference_images=refs,
            source_path=Path(path).resolve(),
        )

    def anchor_block(self) -> str:
        """Compose the exact-repetition anchor block per ADR §5.2.

        Order is fixed (not configurable) so repeated calls produce
        byte-identical output — that is the entire point of the anchor.
        """
        lines: list[str] = []
        if self.identity:
            lines.append("Character Consistency: [" + ", ".join(self.identity) + "]")
        if self.color_palette:
            lines.append("Color Palette: [" + ", ".join(self.color_palette) + "]")
        if self.style:
            lines.append(f"Style: {self.style}")
        if self.lighting:
            lines.append(f"Lighting: {self.lighting}")
        if self.camera:
            lines.append(f"Camera: {self.camera}")
        if self.constraints:
            lines.append("Constraints: [" + ", ".join(self.constraints) + "]")
        return "\n".join(lines)

    def compose_prompt(self, scene: str, *, feedback: str = "") -> str:
        """Compose final prompt = anchor + scene + optional feedback hint.

        Anchor goes FIRST so the model conditions on identity before
        scene-specific direction. Per ADR §5.3 parameter order:
        scene → subjects → environment → composition → lighting → camera
        → style → constraints — anchor pre-injection covers lighting,
        camera, style, constraints; the scene argument fills the rest.
        """
        parts = [self.anchor_block()]
        scene_clean = (scene or "").strip()
        if scene_clean:
            parts.append(f"Scene: {scene_clean}")
        feedback_clean = (feedback or "").strip()
        if feedback_clean:
            parts.append(f"Adjustments from previous attempt: {feedback_clean}")
        return "\n\n".join(p for p in parts if p)


def _as_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [v for v in value if v is not None and v != ""]
    return [value]
