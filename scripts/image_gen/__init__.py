"""Mercury image generation pipeline (Phase 2 Slice B per #351 ADR §7.2.2).

Wraps the gpt-image-2 adapter with character-bible composition, reference-chain
orchestration, default verify rubric, and retry loop. Designed to be invoked
as a module: `python -m scripts.image_gen --help`.
"""
