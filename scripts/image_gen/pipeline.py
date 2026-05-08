"""Reference chain orchestration — invoke gpt-image-2 adapter per frame.

Per ADR §5.5 (`pixel-animation-workflow-2026-05-08.md`): GPT Image 2 has
no `seed` parameter; identity stability comes from anchor-block exact
repetition + a reference image chain. Each frame call forwards the
character bible's reference images plus an optional shared `base_image`
so every frame conditions on the same ground truth (rather than chaining
frame N onto frame N-1, which accumulates drift).

The adapter is invoked via subprocess to preserve the SHA-pin isolation
established in Slice A (`adapters/gpt-image-2/invoke.py`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .character_bible import CharacterBible

ADAPTER_PATH_ENV = "MERCURY_GPT_IMAGE_2_ADAPTER"
DEFAULT_ADAPTER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "adapters"
    / "gpt-image-2"
    / "invoke.py"
)


def resolve_adapter_path() -> Path:
    """Resolve the gpt-image-2 adapter path.

    Honors `MERCURY_GPT_IMAGE_2_ADAPTER` env var when set (decouples
    Slice B from any single repo layout); otherwise falls back to the
    sibling `adapters/gpt-image-2/invoke.py` resolved from this module's
    location.
    """
    override = os.environ.get(ADAPTER_PATH_ENV)
    return Path(override).resolve() if override else DEFAULT_ADAPTER_PATH


# Backward-compat for callers that imported the module-level constant.
ADAPTER_PATH = DEFAULT_ADAPTER_PATH


@dataclass(frozen=True)
class FrameSpec:
    index: int
    scene: str
    out_path: Path


@dataclass
class FrameResult:
    spec: FrameSpec
    returncode: int
    stderr: str = ""
    prompt: str = ""
    out_path: Path | None = None

    @property
    def success(self) -> bool:
        return (
            self.returncode == 0
            and self.out_path is not None
            and self.out_path.exists()
        )


@dataclass
class GenerationOptions:
    model: str = "gpt-image-2"
    size: str = "1024x1024"
    quality: str = "high"
    output_format: str = "png"
    background: str | None = None
    timeout: float = 300.0
    base_image: Path | None = None
    extra_args: tuple[str, ...] = field(default_factory=tuple)


def build_command(prompt: str, out_path: Path, references: list[Path],
                  opts: GenerationOptions,
                  adapter_path: Path | None = None) -> list[str]:
    adapter = adapter_path or resolve_adapter_path()
    cmd: list[str] = [
        sys.executable, str(adapter),
        "-p", prompt,
        "-f", str(out_path),
        "--model", opts.model,
        "--size", opts.size,
        "--quality", opts.quality,
        "--format", opts.output_format,
    ]
    if opts.background:
        cmd.extend(["--background", opts.background])
    for ref in references:
        cmd.extend(["-i", str(ref)])
    cmd.extend(opts.extra_args)
    return cmd


def invoke_adapter(prompt: str, out_path: Path, references: list[Path],
                   opts: GenerationOptions,
                   adapter_path: Path | None = None) -> tuple[int, str]:
    adapter = adapter_path or resolve_adapter_path()
    if not adapter.exists():
        return 127, f"adapter not found at {adapter}"
    cmd = build_command(prompt, out_path, references, opts, adapter_path=adapter)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=opts.timeout)
    except subprocess.TimeoutExpired as exc:
        return 124, f"adapter timed out after {exc.timeout}s"
    except OSError as exc:
        return 127, f"adapter invocation failed: {exc}"
    return proc.returncode, proc.stderr


def generate_frames(bible: CharacterBible, frames: list[FrameSpec],
                    opts: GenerationOptions | None = None,
                    feedback: str = "") -> list[FrameResult]:
    """Generate every frame using the same per-frame reference chain.

    `feedback` is propagated into every frame's prompt; the retry loop
    sets it from the previous attempt's verify failure summary.
    """
    opts = opts or GenerationOptions()
    references = list(bible.reference_images)
    if opts.base_image:
        references.append(opts.base_image)
    results: list[FrameResult] = []
    for spec in frames:
        prompt = bible.compose_prompt(spec.scene, feedback=feedback)
        rc, stderr = invoke_adapter(prompt, spec.out_path, references, opts)
        out_ok = spec.out_path if rc == 0 and spec.out_path.exists() else None
        results.append(FrameResult(
            spec=spec, returncode=rc, stderr=stderr,
            prompt=prompt, out_path=out_ok,
        ))
    return results
