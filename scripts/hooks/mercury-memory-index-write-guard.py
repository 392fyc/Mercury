#!/usr/bin/env python3
"""mercury-memory-index-write-guard.py — Phase F.C lock-in (Issue #331).

PreToolUse hook for Claude Code. Intercepts Edit/Write tool calls targeting
the canonical Mercury user-memory index files (MEMORY.md "Project (Session
History)" subsection AND SESSION_INDEX.md table body). Direct edits between
the regenerate-memory-index.sh markers are blocked; operators must edit
per-session files at memory/sessions/S<N>(-<lane>)?.md and re-run
`bash scripts/regenerate-memory-index.sh --in-place` to refresh canonical.

Soft-disable env vars:
  MERCURY_INDEX_GUARD_DISABLED=1  — short-circuit allow (no-op the hook)
  MERCURY_INDEX_REGENERATE=1      — allow (script-driven, defense-in-depth)

Hook contract: return JSON with hookSpecificOutput.permissionDecision="deny"
to block; exit 0 with no JSON to allow. Exit 0 always — never raise.

Per Issue #331 acceptance + Mercury CLAUDE.md §Related Repositories user-level
governance pattern (model: #259).
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

CANONICAL_BASENAMES = ("MEMORY.md", "SESSION_INDEX.md")
BEGIN_MARKER_RE = re.compile(
    r"<!--\s*BEGIN:\s*scripts/regenerate-memory-index\.sh\s+--in-place\b"
)
END_MARKER_RE = re.compile(
    r"<!--\s*END:\s*scripts/regenerate-memory-index\.sh\s+--in-place\s*-->"
)
DENY_REASON = (
    "Mercury memory-index lock-in (Issue #331): canonical {basename} between "
    "regenerate-memory-index.sh BEGIN/END markers is a derived artifact. Edit "
    "memory/sessions/S<N>(-<lane>)?.md instead, then run "
    "`bash scripts/regenerate-memory-index.sh --in-place` to refresh canonical. "
    "Soft-disable: set MERCURY_INDEX_GUARD_DISABLED=1 to bypass this hook."
)


def _resolve_memory_dir() -> Path:
    env = os.environ.get("MERCURY_MEMORY_DIR", "").strip()
    if env:
        return Path(env).expanduser().resolve(strict=False)
    cfg = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    base = Path(cfg).expanduser() if cfg else Path.home() / ".claude"
    return (base / "projects" / "D--Mercury-Mercury" / "memory").resolve(strict=False)


def _canonical_targets() -> set[Path]:
    mem_dir = _resolve_memory_dir()
    return {(mem_dir / name).resolve(strict=False) for name in CANONICAL_BASENAMES}


def _normalize(path_str: str) -> Path | None:
    if not path_str or not isinstance(path_str, str):
        return None
    try:
        return Path(path_str).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


def _is_canonical_target(path: Path, targets: set[Path]) -> bool:
    if path in targets:
        return True
    if path.name not in CANONICAL_BASENAMES:
        return False
    try:
        for t in targets:
            if path.samefile(t):
                return True
    except (OSError, ValueError):
        pass
    return False


def _find_marker_region(text: str) -> tuple[int, int] | None:
    bm = BEGIN_MARKER_RE.search(text)
    if bm is None:
        return None
    em = END_MARKER_RE.search(text, pos=bm.end())
    if em is None:
        return None
    return (bm.start(), em.end())


def _edit_touches_marker_region(file_path: Path, old_string: str) -> bool:
    """Return True iff the canonical file has BEGIN/END markers AND old_string
    falls inside that region.

    Fail-open on either (a) unreadable file (transient Windows file-lock during
    git operations should not produce confusing denies) or (b) absence of
    markers (pre-cutover or post-rollback canonical files have no managed
    region to protect). Per code-review feedback, returning True on these
    conditions confused fail-closed-posture deny errors with legitimate
    "no marker region exists" cases. Production lock-in still works because
    canonical files post-F.B always carry markers; if markers are stripped,
    edits revert to allowed and operators can re-run --in-place to restore.
    """
    if not old_string:
        return False
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError:
        return False
    region = _find_marker_region(text)
    if region is None:
        return False
    start, end = region
    region_text = text[start:end]
    return old_string in region_text


def _emit_deny(basename: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": DENY_REASON.format(basename=basename),
        }
    }
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def main() -> int:
    if os.environ.get("MERCURY_INDEX_GUARD_DISABLED", "").strip() == "1":
        return 0
    if os.environ.get("MERCURY_INDEX_REGENERATE", "").strip() == "1":
        return 0

    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return 0
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            fixed = re.sub(r'(?<!\\)\\(?!["\\])', r"\\\\", raw)
            payload = json.loads(fixed)
    except (json.JSONDecodeError, ValueError, OSError):
        return 0

    if not isinstance(payload, dict):
        return 0

    tool_name = payload.get("tool_name", "")
    if tool_name not in ("Edit", "Write"):
        return 0

    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        return 0

    file_path_str = tool_input.get("file_path", "")
    target = _normalize(file_path_str)
    if target is None:
        return 0

    targets = _canonical_targets()
    if not _is_canonical_target(target, targets):
        return 0

    if tool_name == "Write":
        _emit_deny(target.name)
        return 0

    old_string = tool_input.get("old_string", "")
    if not isinstance(old_string, str):
        return 0
    if _edit_touches_marker_region(target, old_string):
        _emit_deny(target.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
