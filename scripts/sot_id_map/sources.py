"""Read-only id extraction from the two external repositories.

Nothing in this module opens a file for writing, and no path outside the
two repository roots is ever touched. Repository roots come from
environment variables only (`SOT_ENGINE_REPO` / `SOT_DESIGN_REPO`) — no
local path is hardcoded anywhere in this package.

Two source kinds are supported, matching how each side actually stores
entities:

  `json_dir`    engine side: one entity per file, recursively under a
                directory (`data/affixes/base/af_vanguard.json` counts).
  `json_array`  design side: one snapshot file holding a JSON array of
                entity objects.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_ENV = "SOT_ENGINE_REPO"
DESIGN_ENV = "SOT_DESIGN_REPO"


class SourceError(Exception):
    """Environment / filesystem problem — the checker cannot even start."""


@dataclass
class Roots:
    engine: Path
    design: Path


def resolve_roots(env: dict[str, str] | None = None,
                  engine: str | None = None,
                  design: str | None = None) -> Roots:
    """Resolve both repository roots, or raise `SourceError` with guidance.

    Explicit CLI overrides win over the environment. A missing variable is
    a hard error with an actionable message — never a silent skip and
    never a traceback.
    """
    env = os.environ if env is None else env
    problems: list[str] = []
    resolved: dict[str, Path] = {}
    for label, var, override in (("engine", ENGINE_ENV, engine),
                                 ("design", DESIGN_ENV, design)):
        raw = override if override else env.get(var, "")
        if not raw.strip():
            problems.append(
                f"{label} repository path is not set: export {var}=<path to "
                f"the {label} repo checkout> (or pass --{label}-repo)"
            )
            continue
        path = Path(raw).expanduser()
        if not path.is_dir():
            problems.append(
                f"{label} repository path does not exist or is not a "
                f"directory: {path} (from "
                f"{'--' + label + '-repo' if override else var})"
            )
            continue
        resolved[label] = path
    if problems:
        raise SourceError("; ".join(problems))
    return Roots(engine=resolved["engine"], design=resolved["design"])


@dataclass
class SideIds:
    """Ids found on one side of one entity type, plus their provenance.

    `no_id` collects files inside a declared entity directory that carry no
    usable id. They are reported as findings rather than skipped: a new
    engine entity whose author forgot the `id` field would otherwise leave
    the map green while the entity is invisible to it.
    """

    ids: set[str] = field(default_factory=set)
    origin: dict[str, str] = field(default_factory=dict)  # id -> file path
    no_id: list[str] = field(default_factory=list)        # file paths


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SourceError(f"{path} is not valid JSON: {exc}") from exc


def collect_json_dir(root: Path, rel_path: str, id_field: str) -> SideIds:
    """Every `*.json` under `root/rel_path` (recursive) contributes one id.

    A file that yields no usable id is recorded in `SideIds.no_id` and
    surfaces as a finding. Whole directories of non-entity config (the
    engine keeps `data/runloop/*.json` next to entity data) are handled the
    other way round — the map excludes such a directory by name, with a
    stated reason, so nothing gets waved through file by file.
    """
    out = SideIds()
    base = root / rel_path
    if not base.is_dir():
        raise SourceError(f"engine source directory missing: {base}")
    for path in sorted(base.rglob("*.json")):
        rel = path.relative_to(root).as_posix()
        obj = _load_json(path)
        if not isinstance(obj, dict):
            out.no_id.append(rel)
            continue
        value = obj.get(id_field)
        if not isinstance(value, str) or not value:
            out.no_id.append(rel)
            continue
        if value in out.ids:
            raise SourceError(
                f"duplicate id {value!r} in {base}: {out.origin[value]} and "
                f"{path.relative_to(root).as_posix()}"
            )
        out.ids.add(value)
        out.origin[value] = path.relative_to(root).as_posix()
    return out


def collect_json_array(root: Path, rel_path: str, id_field: str) -> SideIds:
    """Every element of the JSON array at `root/rel_path` contributes one id."""
    out = SideIds()
    path = root / rel_path
    if not path.is_file():
        raise SourceError(f"design snapshot missing: {path}")
    obj = _load_json(path)
    if not isinstance(obj, list):
        raise SourceError(f"{path} must hold a JSON array, got {type(obj).__name__}")
    rel = path.relative_to(root).as_posix()
    for index, item in enumerate(obj):
        if not isinstance(item, dict):
            raise SourceError(f"{rel}[{index}] is not an object")
        value = item.get(id_field)
        if value is None:
            raise SourceError(f"{rel}[{index}] has no {id_field!r} field")
        value = str(value)
        if value in out.ids:
            raise SourceError(f"duplicate id {value!r} in {rel}")
        out.ids.add(value)
        out.origin[value] = rel
    return out


def collect(root: Path, spec: dict) -> SideIds:
    kind = spec.get("kind")
    if kind == "json_dir":
        return collect_json_dir(root, spec["path"], spec.get("id_field", "id"))
    if kind == "json_array":
        return collect_json_array(root, spec["path"], spec.get("id_field", "id"))
    raise SourceError(f"unknown source kind {kind!r} in id_map.json")


def engine_scope_entries(root: Path, data_dir: str) -> set[str]:
    """Everything directly under the engine `data/` tree that must be declared.

    Both immediate subdirectories *and* loose `*.json` files sitting
    directly in `data/`. The loose-file half matters: a new entity dropped
    at `data/foo.json` belongs to no declared directory, so without this it
    would slip past the scope guard entirely.
    """
    base = root / data_dir
    if not base.is_dir():
        raise SourceError(f"engine data directory missing: {base}")
    entries = {f"{data_dir}/{p.name}" for p in sorted(base.iterdir())
               if p.is_dir()}
    entries |= {f"{data_dir}/{p.name}" for p in sorted(base.glob("*.json"))}
    return entries


def design_snapshot_files(root: Path, snapshot_dir: str) -> set[str]:
    """Every `*.json` anywhere under the design `snapshots/` directory.

    Recursive on purpose — a snapshot filed into a new subdirectory is
    still new content the map has not been told about.
    """
    base = root / snapshot_dir
    if not base.is_dir():
        raise SourceError(f"design snapshot directory missing: {base}")
    return {p.relative_to(root).as_posix() for p in sorted(base.rglob("*.json"))}
