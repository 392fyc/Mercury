"""Mercury SoT pixel-asset pipeline (orchestration layer).

Routes Ship-of-Theseus (Godot 4.6 tactical RPG) art generation by asset
type into an engine-ready STAGING directory — the game repo is never
touched.

  portrait / icon / cutin  — HD single-image via the gpt-image-2 adapter
  pawn                      — 64x64 AnimatedSprite2D units via the PixelLab
                              adapter, packed into a SpriteFrames `.tres`

The Python spritesheet packer (`pack`) is the always-on spine; Aseprite
palette unification (`postprocess`) is an optional enhancement that
graceful-skips when the binary is absent. Designed to be invoked as a
module: `python -m scripts.sot_pixel --help`.
"""
