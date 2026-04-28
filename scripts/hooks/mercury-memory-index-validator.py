#!/usr/bin/env python3
"""mercury-memory-index-validator.py — Phase F.C lock-in (Issue #331).

SessionEnd hook for Claude Code. Runs `regenerate-memory-index.sh --format diff`
against the Mercury user-memory dir; if drift detected (script exit 1), emits a
warning to stderr (visible to user only — SessionEnd hooks cannot block).

Best-effort observability: never blocks session termination, never raises.
SessionEnd output JSON is ignored by Claude Code per the hook contract; only
stderr is surfaced to user.

Env vars:
  MERCURY_INDEX_VALIDATOR_DISABLED=1  — no-op the hook (soft-disable)
  MERCURY_INDEX_AUTOFIX=1             — when drift detected (regen exit 1),
                                        run `regenerate-memory-index.sh
                                        --in-place` automatically; emit a
                                        confirmation stderr message instead
                                        of a drift warning. Per Issue #331
                                        Hook 2 spec ("Auto-fix with --in-place
                                        if MERCURY_INDEX_AUTOFIX=1 set").
  MERCURY_REPO_ROOT=<path>            — Mercury repo root (required for
                                        deployed hooks; in-repo invocation
                                        auto-resolves via __file__).

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
            encoding="utf-8",
            errors="replace",
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
    except OSError as exc:
        sys.stderr.write(
            "Mercury memory-index validator: subprocess error invoking "
            f"regenerate ({exc.__class__.__name__}: {exc}). Drift check "
            "skipped. Soft-disable: MERCURY_INDEX_VALIDATOR_DISABLED=1.\n"
        )
        return 0
    except UnicodeError:
        return 0

    if result.returncode == 0:
        return 0

    session_id = payload.get("session_id", "unknown") if isinstance(payload, dict) else "unknown"
    stderr_tail = (result.stderr or "").strip().splitlines()[-5:]
    stdout_tail = (result.stdout or "").strip().splitlines()[-5:]

    # Auto-fix path (Issue #331 Hook 2 spec): when drift detected (exit 1) AND
    # MERCURY_INDEX_AUTOFIX=1 set, run --in-place to refresh canonical from
    # per-session sources. Suppresses the drift warning if the auto-fix
    # itself succeeds; emits a confirmation message instead so operators
    # can audit silent rewrites.
    if result.returncode == 1 and os.environ.get("MERCURY_INDEX_AUTOFIX", "").strip() == "1":
        try:
            fix_result = subprocess.run(
                ["bash", str(script_path), "--in-place"],
                cwd=str(repo_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError, UnicodeError) as exc:
            sys.stderr.write(
                "Mercury memory-index validator: MERCURY_INDEX_AUTOFIX=1 set "
                "but --in-place auto-fix failed "
                f"({exc.__class__.__name__}: {exc}). Drift remains; run "
                "manually: `bash scripts/regenerate-memory-index.sh "
                f"--in-place`. (session={session_id})\n"
            )
            return 0
        if fix_result.returncode == 0:
            sys.stderr.write(
                "Mercury memory-index validator: drift detected at session "
                f"end (session={session_id}); MERCURY_INDEX_AUTOFIX=1 set "
                "→ auto-fix via --in-place succeeded. Canonical files "
                "refreshed from per-session sources.\n"
            )
            return 0
        # Auto-fix attempted but failed — fall through to standard warning
        sys.stderr.write(
            "Mercury memory-index validator: MERCURY_INDEX_AUTOFIX=1 set "
            "but --in-place auto-fix exited "
            f"{fix_result.returncode}. Falling back to drift warning.\n"
        )

    if result.returncode == 1:
        # Per regenerate-memory-index.sh exit-code contract: exit 1 == drift
        # detected. Anything else is script failure (parse error / args /
        # missing file) — distinguish so operators do not chase false drift.
        kind_msg = "drift detected"
    else:
        kind_msg = (
            "regenerate validation failed (exit "
            f"{result.returncode} != 1; see exit-code contract in "
            "scripts/regenerate-memory-index.sh header — likely script "
            "error, not drift)"
        )
    sys.stderr.write(
        f"Mercury memory-index validator: {kind_msg} at session end "
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
