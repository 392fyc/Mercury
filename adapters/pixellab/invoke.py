#!/usr/bin/env python3
"""Mercury adapter — PixelLab.ai v1 REST API (pixel-art generation).

Original Mercury code (not a cherry-pick): a thin direct-HTTP client for 3
PixelLab v1 endpoints. We bypass the official `pixellab` SDK on purpose —
SDK v1.0.5 hardcodes Usage.type=Literal["usd"] but the live API returns
"generations", raising a pydantic error even though the image generated;
parsing raw JSON avoids it. Verified vs api.pixellab.ai/v1/openapi.json
(2026-07-01): pixflux/bitforge -> image.base64 ; animate-with-text ->
images[i].base64 . Auth: Bearer <PIXELLAB_API_TOKEN>. Image inputs are
Base64Image {type,base64} resized (NEAREST) to == image_size; animate is
fixed 64x64. Docs https://www.pixellab.ai/pixellab-api ; report §11/§13.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
from pathlib import Path

API_BASE = "https://api.pixellab.ai/v1"
ENDPOINT_PATHS = {
    "pixflux": "generate-image-pixflux",
    "bitforge": "generate-image-bitforge",
    "animate": "animate-with-text",
}
ANIMATE_SIZE = 64
DEFAULT_TOKEN_ENV = "PIXELLAB_API_TOKEN"


def _load_token(env_name: str) -> str | None:
    """Token from env, falling back to a minimal repo-root .env parse."""
    val = os.environ.get(env_name)
    if val:
        return val
    env_file = Path(__file__).resolve().parents[2] / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, raw = line.partition("=")
                if key.strip() == env_name:
                    return raw.strip().strip('"').strip("'")
    return None


def _b64_image(path: str, width: int, height: int) -> dict:
    """Resize to exactly WxH (NEAREST), return Base64Image (type 'base64')."""
    from PIL import Image  # lazy: only when a reference image is supplied

    with Image.open(path) as im:
        im = im.convert("RGBA")
        if im.size != (width, height):
            im = im.resize((width, height), Image.NEAREST)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
    return {"type": "base64", "base64": base64.b64encode(buf.getvalue()).decode("ascii")}


def _build_payload(a: argparse.Namespace) -> tuple[dict, int, int]:
    if a.endpoint == "animate":
        w = h = ANIMATE_SIZE
        p: dict = {"description": a.description, "action": a.action,
                   "image_size": {"width": w, "height": h},
                   "reference_image": _b64_image(a.reference_image, w, h)}
        if a.n_frames is not None:
            p["n_frames"] = a.n_frames
        if a.image_guidance is not None:
            p["image_guidance_scale"] = a.image_guidance
    else:
        w, h = a.width, a.height
        p = {"description": a.description, "image_size": {"width": w, "height": h}}
        if a.endpoint == "bitforge" and a.style_image:
            p["style_image"] = _b64_image(a.style_image, w, h)
            if a.style_strength is not None:
                p["style_strength"] = a.style_strength
        if a.init_image:
            p["init_image"] = _b64_image(a.init_image, w, h)
            if a.init_strength is not None:
                p["init_image_strength"] = a.init_strength
        if a.isometric:
            p["isometric"] = True
    for key, value in a.opt:  # passthrough: direction/view/outline/shading/detail/...
        p[key] = value
    if a.text_guidance is not None:
        p["text_guidance_scale"] = a.text_guidance
    if a.no_background:
        p["no_background"] = True
    if a.seed is not None:
        p["seed"] = a.seed
    return p, w, h


def _post(endpoint: str, payload: dict, token: str, timeout: float):
    import requests
    return requests.post(
        f"{API_BASE}/{ENDPOINT_PATHS[endpoint]}", json=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=timeout)


def _write_png(b64: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(b64))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python adapters/pixellab/invoke.py",
        description="PixelLab v1 REST adapter (pixflux / bitforge / animate-with-text).")
    p.add_argument("--endpoint", required=True, choices=sorted(ENDPOINT_PATHS))
    p.add_argument("--description", required=True)
    p.add_argument("--width", type=int, default=64, help="output width (animate forced to 64)")
    p.add_argument("--height", type=int, default=64, help="output height (animate forced to 64)")
    p.add_argument("--out", type=Path, help="single-image PNG (pixflux/bitforge)")
    p.add_argument("--out-dir", type=Path, help="frame dir (animate)")
    p.add_argument("--action", help="animate action e.g. 'walk' (required for animate)")
    p.add_argument("--reference-image", help="animate reference sprite (required for animate)")
    p.add_argument("--n-frames", type=int, default=None, help="animate frames 2-20 (def 4)")
    p.add_argument("--init-image", help="pixflux/bitforge init reference (resized to output size)")
    p.add_argument("--init-strength", type=int, default=None, help="init_image_strength 1-999")
    p.add_argument("--style-image", help="bitforge style reference")
    p.add_argument("--style-strength", type=float, default=None, help="bitforge style_strength 0-100")
    p.add_argument("--opt", action="append", nargs=2, metavar=("KEY", "VALUE"), default=[],
                   help="extra string param, e.g. --opt direction south --opt shading 'basic shading'")
    p.add_argument("--text-guidance", type=float, default=None, help="text_guidance_scale 1-20")
    p.add_argument("--image-guidance", type=float, default=None, help="animate image_guidance_scale")
    p.add_argument("--no-background", action="store_true", help="transparent background")
    p.add_argument("--isometric", action="store_true", help="isometric (pixflux/bitforge)")
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--token-env", default=DEFAULT_TOKEN_ENV)
    p.add_argument("--timeout", type=float, default=120.0)
    return p


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.endpoint == "animate" and not (a.action and a.reference_image and a.out_dir):
        sys.stderr.write("error: animate requires --action, --reference-image, --out-dir\n")
        return 2
    if a.endpoint != "animate" and not a.out:
        sys.stderr.write(f"error: --out is required for {a.endpoint}\n")
        return 2
    token = _load_token(a.token_env)
    if not token:
        sys.stderr.write(f"error: API token not in env {a.token_env} or repo .env\n")
        return 2
    try:
        payload, w, h = _build_payload(a)
    except (OSError, ImportError) as exc:
        sys.stderr.write(f"error: reference image read/encode failed: {exc}\n")
        return 2
    try:
        resp = _post(a.endpoint, payload, token, a.timeout)
    except Exception as exc:  # never echo the token in any error path
        sys.stderr.write(f"error: request failed: {exc.__class__.__name__}: {exc}\n")
        return 1
    if resp.status_code != 200:
        sys.stderr.write(f"error: {a.endpoint} HTTP {resp.status_code}: {resp.text[:500]}\n")
        return 1
    try:
        data = resp.json()
    except ValueError:
        sys.stderr.write(f"error: {a.endpoint} returned non-JSON body\n")
        return 1
    usd = (data.get("usage") or {}).get("usd")
    if a.endpoint == "animate":
        frames = data.get("images")
        if not isinstance(frames, list) or not frames:
            sys.stderr.write("error: animate response missing 'images' array\n")
            return 1
        out_paths = []
        for i, frame in enumerate(frames):
            b64 = (frame or {}).get("base64")
            if not b64:
                sys.stderr.write(f"error: animate frame {i} missing base64\n")
                return 1
            fp = a.out_dir / f"frame_{i:02d}.png"
            _write_png(b64, fp)
            out_paths.append(str(fp))
        summary = {"endpoint": "animate", "out_dir": str(a.out_dir), "frames": out_paths,
                   "n_frames": len(out_paths), "size": [w, h], "usd": usd}
    else:
        b64 = (data.get("image") or {}).get("base64")
        if not b64:
            sys.stderr.write(f"error: {a.endpoint} response missing image.base64\n")
            return 1
        _write_png(b64, a.out)
        summary = {"endpoint": a.endpoint, "out": str(a.out), "size": [w, h], "usd": usd}
    json.dump(summary, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
