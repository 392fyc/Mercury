"""One-shot AgentKB Markdown -> mem0 migration.

Iterates Markdown under $AGENTKB_DIR (or --source), strips YAML frontmatter,
and calls add_safe() with parsed metadata. Read-only on source files.

Usage:
    python scripts/mem0_migrate.py [--source PATH] [--user-id ID] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mem0_hooks import add_safe  # noqa: E402


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal YAML frontmatter extractor.

    Accepts only root-level ``key: value`` lines. Indented continuation lines,
    list items (``- ...``), and nested mappings are silently dropped rather
    than promoted to new keys. Empty frontmatter (``---\\n---\\n``) is
    recognised and stripped. Anything that does not look like frontmatter is
    returned untouched so the body survives intact.
    """
    if not text.startswith("---\n"):
        return {}, text
    # Start the close-marker search at index 3 so the shared '\n' between
    # opening and closing fences counts for both, making empty frontmatter
    # ('---\n---\nbody') parse correctly.
    end = text.find("\n---\n", 3)
    if end == -1:
        return {}, text
    header = text[4:end]
    body = text[end + 5 :]
    meta: dict[str, Any] = {}
    if not header.strip():
        return meta, body
    for raw in header.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # drop indented / list-item / non-key lines — keep strict root-level only
        if raw != stripped or stripped.startswith("- ") or ":" not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip().strip("'\"")
        # empty scalar = YAML mapping container; drop rather than store a
        # misleading empty string keyed by a parent node name.
        if not key or not value:
            continue
        meta[key] = value
    return meta, body


def migrate(source: Path, user_id: str, dry_run: bool) -> tuple[int, int]:
    added = 0
    skipped = 0
    for path in sorted(source.rglob("*.md")):
        rel = path.relative_to(source).as_posix()
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"SKIP {rel}: {exc}")
            skipped += 1
            continue
        meta, body = _parse_frontmatter(raw)
        body = body.strip()
        if not body:
            skipped += 1
            continue
        meta["source_path"] = rel
        if dry_run:
            print(f"DRY  {rel} ({len(body)} chars, meta={list(meta)})")
            added += 1
            continue
        try:
            result = add_safe(body, user_id=user_id, metadata=meta)
        except Exception as exc:
            print(f"ERR  {rel}: {exc}")
            skipped += 1
            continue
        if result is None:
            print(f"SKIP {rel}: empty or dedup")
            skipped += 1
        else:
            print(f"ADD  {rel}")
            added += 1
    return added, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        default=os.environ.get("AGENTKB_DIR"),
        help="Directory containing AgentKB Markdown (default: $AGENTKB_DIR)",
    )
    parser.add_argument("--user-id", default="mercury")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.source:
        parser.error("--source or $AGENTKB_DIR required")
    source = Path(args.source).resolve()
    if not source.is_dir():
        parser.error(f"source not a directory: {source}")
    added, skipped = migrate(source, args.user_id, args.dry_run)
    print(f"\nDone. added={added} skipped={skipped} mode={'dry-run' if args.dry_run else 'live'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
