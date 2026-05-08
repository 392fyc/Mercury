#!/usr/bin/env python3
# UPSTREAM: wuyoscar/gpt_image_2_skill
# SOURCE:   https://github.com/wuyoscar/gpt_image_2_skill
# SHA:      6fdd7243dc9605efcf6d66e9394d3d10fc5141f6
# DATE:     2026-05-08
# ISSUE:    https://github.com/392fyc/Mercury/issues/351
"""Mercury adapter — invoke wuyoscar/gpt_image_2_skill via uvx-pinned-SHA.

Validates `uvx` is on PATH then forwards argv to the upstream `gpt-image`
console script (entry point `gpt_image_cli.cli:main`). Authentication is
delegated to upstream — `gpt_image_cli` resolves `OPENAI_API_KEY` from env
or via `python-dotenv` (.env file).

The forwarded environment is filtered to an allowlist (OS basics, locale,
proxy, OPENAI_*, LC_*) so unrelated parent-process credentials (cloud
provider tokens, GitHub tokens, etc.) do not leak into the upstream
process.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

REPO = "git+https://github.com/wuyoscar/gpt_image_2_skill"
SHA = "6fdd7243dc9605efcf6d66e9394d3d10fc5141f6"
ENTRY = "gpt-image"

_ALLOWED_NAMES_UPPER = frozenset({
    "PATH", "PATHEXT",
    "HOME", "USERPROFILE", "USERNAME",
    "TEMP", "TMP", "TMPDIR",
    "SYSTEMROOT", "WINDIR", "COMSPEC",
    "LANG", "TZ",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "ALL_PROXY",
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
})
_ALLOWED_PREFIXES_UPPER = ("OPENAI_", "LC_")


def _resolve_uvx() -> str:
    path = shutil.which("uvx")
    if not path:
        sys.stderr.write(
            "error: 'uvx' not found on PATH. install `uv` "
            "(https://docs.astral.sh/uv/) and ensure `uvx` is reachable.\n"
        )
        sys.exit(2)
    return path


def _filtered_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper in _ALLOWED_NAMES_UPPER or upper.startswith(_ALLOWED_PREFIXES_UPPER):
            env[key] = value
    return env


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    uvx = _resolve_uvx()
    cmd = [uvx, "--from", f"{REPO}@{SHA}", ENTRY, *args]
    try:
        return subprocess.call(cmd, env=_filtered_env())
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        sys.stderr.write(f"error: failed to invoke 'uvx': {exc}\n")
        return 127


if __name__ == "__main__":
    sys.exit(main())
