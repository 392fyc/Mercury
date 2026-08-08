"""Engine <-> design id map checker — module CLI entry.

Usage:

    export SOT_ENGINE_REPO=/path/to/Ship_of_Theseus
    export SOT_DESIGN_REPO=/path/to/SoT-fyc-space
    python -m scripts.sot_id_map

Reads both repositories read-only and validates `id_map.json` against
them. `--json` prints a machine-readable report instead of the text one.

Exit codes: 0 map matches both repositories / 1 validation findings /
2 environment or usage error (missing variable, unreadable map).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import checker
from .sources import SourceError, resolve_roots


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m scripts.sot_id_map",
        description="Validate the Ship-of-Theseus engine <-> design-library "
                    "id map against both repositories (read-only).",
    )
    p.add_argument("--engine-repo", default=None,
                   help=f"engine repo root (default: ${checker.sources.ENGINE_ENV})")
    p.add_argument("--design-repo", default=None,
                   help=f"design repo root (default: ${checker.sources.DESIGN_ENV})")
    p.add_argument("--map", dest="map_path", type=Path, default=None,
                   help="id map JSON path (default: the packaged id_map.json)")
    p.add_argument("--json", action="store_true",
                   help="emit a JSON report instead of the text report")
    return p


def _force_utf8_output() -> None:
    """Findings can contain non-ASCII ids — do not let the console eat them.

    The design library's tag registry is keyed by Chinese strings, so a
    finding naming one of those ids is unprintable on a default Windows
    console (GBK), and the resulting UnicodeEncodeError would replace a
    useful report with a traceback.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = _parser().parse_args(argv)
    try:
        roots = resolve_roots(engine=args.engine_repo, design=args.design_repo)
        spec = checker.load_map(args.map_path)
        report = checker.check(roots, spec)
    except SourceError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2
    except KeyError as exc:
        sys.stderr.write(f"error: id map is missing required key {exc}\n")
        return 2

    if args.json:
        json.dump(report.as_dict(), sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _print_text(report, roots)
    return 0 if report.ok else 1


def _print_text(report: checker.Report, roots) -> None:
    out = sys.stdout
    out.write(f"engine repo: {roots.engine}\n")
    out.write(f"design repo: {roots.design}\n\n")
    out.write(f"{'entity type':<16}{'engine':>8}{'design':>8}  coverage\n")
    for etype, per in report.stats["entity_types"].items():
        eng = "-" if per["engine"] is None else str(per["engine"])
        des = "-" if per["design"] is None else str(per["design"])
        out.write(f"{etype:<16}{eng:>8}{des:>8}  {per['coverage']}\n")
    out.write(
        f"\nengine ids: {report.stats['engine_ids_total']}   "
        f"design ids: {report.stats['design_ids_total']}   "
        f"mappings: {report.stats['mappings']}   "
        f"unmapped: {report.stats['unmapped']}\n")
    if report.ok:
        out.write("\nOK: every id on both sides is mapped or explicitly "
                  "unmapped, and every referenced id exists.\n")
        return
    out.write(f"\nFAIL: {len(report.findings)} finding(s)\n")
    for finding in report.findings:
        out.write(f"  {finding}\n")


if __name__ == "__main__":
    sys.exit(main())
