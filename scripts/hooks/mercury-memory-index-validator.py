#!/usr/bin/env python3
"""mercury-memory-index-validator.py — Phase F.C lock-in (Issue #331).

SessionEnd hook for Claude Code. Runs `regenerate-memory-index.sh --format diff`
against the Mercury user-memory dir; if drift detected (script exit 1), emits a
warning to stderr (visible to user only — SessionEnd hooks cannot block).

Best-effort observability: never blocks session termination, never raises.
SessionEnd output JSON is ignored by Claude Code per the hook contract; only
stderr is surfaced to user.

Soft-disable env var:
  MERCURY_INDEX_VALIDATOR_DISABLED=1  — no-op the hook

Per Issue #331 acceptance + Mercury CLAUDE.md §Related Repositories user-level
governance pattern (model: #259).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REGEN_SCRIPT_REL = Path("scripts") / "regenerate-memory-index.sh"


def _resolve_repo_root() -> Path | None:
    """Resolve Mercury repo root with two strategies, no machine-specific fallback.

    1. `MERCURY_REPO_ROOT` env var (preferred for deployed hooks under
       ~/.claude/hooks/ — set in settings.json env block per deployment guide).
    2. `__file__`-relative heuristic: when this script lives at
       <repo>/scripts/hooks/mercury-memory-index-validator.py, parents[2] is
       the repo root. Works during in-repo invocation and tests; degrades
       gracefully when the script is copied into ~/.claude/hooks/ (parents[2]
       there is ~, which won't contain regenerate-memory-index.sh).

    Returns None when no candidate yields a valid repo (silent no-op per
    SessionEnd observability-only contract). Hardcoded absolute paths are
    forbidden per Mercury CLAUDE.md `feedback_no_hardcoded_paths`.
    """
    env = os.environ.get("MERCURY_REPO_ROOT", "").strip()
    if env:
        candidate = Path(env).expanduser()
        if (candidate / REGEN_SCRIPT_REL).is_file():
            return candidate
    try:
        self_path = Path(__file__).resolve(strict=False)
        candidate = self_path.parents[2]
        if (candidate / REGEN_SCRIPT_REL).is_file():
            return candidate
    except (OSError, IndexError):
        pass
    return None


def main() -> int:
    if os.environ.get("MERCURY_INDEX_VALIDATOR_DISABLED", "").strip() == "1":
        return 0

    try:
        raw = sys.stdin.read()
        if raw.strip():
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                fixed = re.sub(r'(?<!\\)\\(?!["\\])', r"\\\\", raw)
                payload = json.loads(fixed)
        else:
            payload = {}
    except (json.JSONDecodeError, ValueError, OSError):
        payload = {}

    repo_root = _resolve_repo_root()
    if repo_root is None:
        return 0

    script_path = repo_root / REGEN_SCRIPT_REL
    cmd = ["bash", str(script_path), "--format", "diff"]

    env = os.environ.copy()
    env["MERCURY_INDEX_REGENERATE"] = "1"

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            "Mercury memory-index validator: regenerate timed out after 30s — "
            "drift check skipped. Run manually: "
            "`bash scripts/regenerate-memory-index.sh --format diff` from the "
            "Mercury repo. Soft-disable: MERCURY_INDEX_VALIDATOR_DISABLED=1.\n"
        )
        return 0
    except FileNotFoundError:
        sys.stderr.write(
            "Mercury memory-index validator: `bash` executable not on PATH — "
            "drift check skipped. Install Git Bash / MSYS2 / WSL, or set "
            "MERCURY_INDEX_VALIDATOR_DISABLED=1 to suppress this notice.\n"
        )
        return 0
    except OSError:
        return 0

    if result.returncode == 0:
        return 0

    session_id = payload.get("session_id", "unknown") if isinstance(payload, dict) else "unknown"
    stderr_tail = (result.stderr or "").strip().splitlines()[-5:]
    stdout_tail = (result.stdout or "").strip().splitlines()[-5:]
    sys.stderr.write(
        "Mercury memory-index validator: drift detected at session end "
        f"(session={session_id}, regen exit={result.returncode}).\n"
    )
    if stderr_tail:
        sys.stderr.write("  stderr:\n")
        for line in stderr_tail:
            sys.stderr.write(f"    {line}\n")
    if stdout_tail:
        sys.stderr.write("  stdout:\n")
        for line in stdout_tail:
            sys.stderr.write(f"    {line}\n")
    sys.stderr.write(
        "  Run `bash scripts/regenerate-memory-index.sh --in-place` from the "
        "Mercury repo to refresh canonical files. Soft-disable: "
        "MERCURY_INDEX_VALIDATOR_DISABLED=1.\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
