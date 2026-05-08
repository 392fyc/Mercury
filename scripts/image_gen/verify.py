"""Default verify rubric for generated frame sequences.

Implements ADR §6.1 hard + soft gates over a list of PNG (or other)
frame paths:

  hard  — frame_count, dimension_uniformity, palette_quantization
  soft  — character_consistency (dHash), loop_closure (dHash + SSIM)

`transparent_bg` (ADR §4.4) is omitted at this layer because gpt-image-2
itself does not emit transparent BG (ADR §3.2); the upstream `gpt-image-1.5`
fallback path has its own validation in Phase 3.

Imports of Pillow / numpy / imagehash / scikit-image are guarded so this
module imports cleanly on hosts without the verify stack — `--help` and
dry-run paths must not require the rubric deps. Missing deps degrade
each affected gate to `passed=True, severity="skipped"` with a detail
explaining which package is unavailable, rather than failing the run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    Image = None  # type: ignore[assignment]
    _HAS_PIL = False

try:
    import imagehash
    _HAS_IMAGEHASH = True
except ImportError:
    imagehash = None  # type: ignore[assignment]
    _HAS_IMAGEHASH = False

try:
    import numpy as np
    from skimage.metrics import structural_similarity
    _HAS_SKIMAGE = True
except ImportError:
    np = None  # type: ignore[assignment]
    structural_similarity = None  # type: ignore[assignment]
    _HAS_SKIMAGE = False


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: dict = field(default_factory=dict)
    severity: str = "hard"


@dataclass
class VerifyResult:
    """Verify rubric output.

    `passed` reflects HARD gate status only — soft-gate failures
    (character_consistency, loop_closure) are reported in `advisories`
    but never flip `passed` to False or trigger retries. This matches
    ADR §6.1 where character_consistency and loop_closure are explicitly
    marked soft gates: they're informational signals about drift, not
    blocking gates.
    """
    passed: bool
    gates: list[GateResult] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)
    advisories: list[str] = field(default_factory=list)
    retry_hints: dict = field(default_factory=dict)

    def summary(self) -> str:
        if self.passed:
            tail = f", {len(self.advisories)} advisory" if self.advisories else ""
            return f"VERIFY PASS — {len(self.gates)} gate(s){tail}"
        head = f"VERIFY FAIL — {len(self.fail_reasons)} reason(s)"
        body = "; ".join(self.fail_reasons[:5])
        return f"{head}: {body}" if body else head


@dataclass
class VerifyConfig:
    expected_count: int
    reference_size: tuple[int, int] | None = None
    max_palette_size: int = 64
    dhash_threshold: int = 12
    ssim_threshold: float = 0.6
    require_loop_closure: bool = False


def _check_frame_count(frames: list[Path], expected: int) -> GateResult:
    actual = len(frames)
    return GateResult(
        name="frame_count",
        passed=actual == expected,
        detail={"expected": expected, "actual": actual},
    )


def _check_dimension(frames: list[Path], reference: tuple[int, int] | None
                     ) -> GateResult:
    if not _HAS_PIL:
        return GateResult(name="dimension_uniformity", passed=True,
                          severity="skipped",
                          detail={"reason": "Pillow not installed"})
    sizes = []
    violations = []
    for f in frames:
        try:
            with Image.open(f) as img:
                sizes.append((f.name, img.size))
        except OSError as exc:
            violations.append({"frame": f.name, "error": str(exc)})
    if violations:
        return GateResult(name="dimension_uniformity", passed=False,
                          detail={"violations": violations})
    if not sizes:
        return GateResult(name="dimension_uniformity", passed=False,
                          detail={"reason": "no frames readable"})
    ref = reference or sizes[0][1]
    diffs = [{"frame": n, "size": s} for n, s in sizes if s != ref]
    return GateResult(
        name="dimension_uniformity",
        passed=not diffs,
        detail={"reference_size": ref, "violations": diffs},
    )


def _check_palette(frames: list[Path], max_size: int) -> GateResult:
    if not _HAS_PIL:
        return GateResult(name="palette_quantization", passed=True,
                          severity="skipped",
                          detail={"reason": "Pillow not installed"})
    per_frame_sizes = []
    over = []
    union: set[tuple[int, int, int]] = set()
    for f in frames:
        try:
            with Image.open(f) as img:
                colors = img.convert("RGB").getcolors(maxcolors=2 ** 16)
        except OSError as exc:
            over.append({"frame": f.name, "error": str(exc)})
            continue
        if colors is None:
            n = max_size + 1
        else:
            n = len(colors)
            union.update(c for _, c in colors)
        per_frame_sizes.append({"frame": f.name, "unique_colors": n})
        if n > max_size:
            over.append({"frame": f.name, "unique_colors": n})
    return GateResult(
        name="palette_quantization",
        passed=not over,
        detail={"max_allowed": max_size,
                "per_frame_sizes": per_frame_sizes,
                "union_size": len(union),
                "violations": over},
    )


def _check_consistency(frames: list[Path], threshold: int) -> GateResult:
    if not _HAS_PIL or not _HAS_IMAGEHASH:
        return GateResult(name="character_consistency", passed=True,
                          severity="skipped",
                          detail={"reason": "Pillow + imagehash required"})
    if len(frames) < 2:
        return GateResult(name="character_consistency", passed=True,
                          severity="soft",
                          detail={"reason": "need ≥2 frames"})
    hashes = []
    errors = []
    for f in frames:
        try:
            with Image.open(f) as img:
                hashes.append(imagehash.dhash(img))
        except OSError as exc:
            errors.append({"frame": f.name, "error": str(exc)})
    if errors or len(hashes) < 2:
        return GateResult(name="character_consistency", passed=False,
                          severity="soft",
                          detail={"reason": "image read errors",
                                  "errors": errors,
                                  "readable_count": len(hashes)})
    base = hashes[0]
    distances = [int(h - base) for h in hashes[1:]]
    worst = max(distances) if distances else 0
    return GateResult(
        name="character_consistency",
        passed=worst <= threshold,
        severity="soft",
        detail={"method": "dHash", "threshold": threshold,
                "max_distance": worst, "per_frame_distance": distances},
    )


def _check_loop_closure(frames: list[Path], dhash_threshold: int,
                        ssim_threshold: float) -> GateResult:
    if not _HAS_PIL or not _HAS_IMAGEHASH or not _HAS_SKIMAGE:
        return GateResult(name="loop_closure", passed=True,
                          severity="skipped",
                          detail={"reason":
                                  "Pillow + imagehash + scikit-image required"})
    if len(frames) < 2:
        return GateResult(name="loop_closure", passed=True, severity="soft",
                          detail={"reason": "need ≥2 frames"})
    try:
        with Image.open(frames[0]) as first_img, Image.open(frames[-1]) as last_img:
            first_h = imagehash.dhash(first_img)
            last_h = imagehash.dhash(last_img)
            first_arr = np.asarray(first_img.convert("L"))
            last_arr = np.asarray(last_img.convert("L"))
    except OSError as exc:
        return GateResult(name="loop_closure", passed=False, severity="soft",
                          detail={"reason": "image read error",
                                  "error": str(exc)})
    if first_arr.shape != last_arr.shape:
        return GateResult(name="loop_closure", passed=False, severity="soft",
                          detail={"reason": "first/last shape mismatch",
                                  "first": first_arr.shape,
                                  "last": last_arr.shape})
    dist = int(first_h - last_h)
    sim = float(structural_similarity(first_arr, last_arr))
    passed = dist <= dhash_threshold and sim >= ssim_threshold
    return GateResult(
        name="loop_closure", passed=passed, severity="soft",
        detail={"dhash_distance": dist, "ssim": sim,
                "dhash_threshold": dhash_threshold,
                "ssim_threshold": ssim_threshold},
    )


def verify_frames(frames: list[Path], cfg: VerifyConfig) -> VerifyResult:
    fc_gate = _check_frame_count(frames, cfg.expected_count)
    # Short-circuit when frame_count fails AND we have nothing to verify
    # (typically all generation attempts errored). Running the other
    # gates over an empty / partial list emits noisy "no frames readable"
    # cascade that drowns out the real failure (frame_count). Gate the
    # dependent checks behind frame_count so the retry loop sees a clean
    # single-reason failure that actually matches what happened.
    if not fc_gate.passed and not frames:
        return VerifyResult(
            passed=False,
            gates=[fc_gate],
            fail_reasons=[f"frame_count: {fc_gate.detail}"],
            advisories=[],
            retry_hints={"fix_frame_count": fc_gate.detail},
        )

    gates: list[GateResult] = [
        fc_gate,
        _check_dimension(frames, cfg.reference_size),
        _check_palette(frames, cfg.max_palette_size),
        _check_consistency(frames, cfg.dhash_threshold),
    ]
    if cfg.require_loop_closure:
        gates.append(_check_loop_closure(frames, cfg.dhash_threshold,
                                         cfg.ssim_threshold))

    fail_reasons: list[str] = []
    advisories: list[str] = []
    retry_hints: dict = {}
    for g in gates:
        if g.passed:
            continue
        msg = f"{g.name}: {g.detail}"
        if g.severity == "hard":
            fail_reasons.append(msg)
            if g.name == "frame_count":
                retry_hints["fix_frame_count"] = g.detail
            elif g.name == "dimension_uniformity":
                retry_hints["fix_dimensions"] = g.detail.get("reference_size")
            elif g.name == "palette_quantization":
                retry_hints["reduce_palette_to"] = cfg.max_palette_size
        else:
            advisories.append(msg)

    return VerifyResult(
        passed=not fail_reasons,
        gates=gates,
        fail_reasons=fail_reasons,
        advisories=advisories,
        retry_hints=retry_hints,
    )
