"""Read-only id extraction from the two external repositories.

Nothing in this module opens a file for writing, and every path it reads
is forced to stay inside one of the two repository roots — see
`resolve_within`, which is the only way this module turns a path string
from the map file into a real path. Repository roots come from environment
variables only (`SOT_ENGINE_REPO` / `SOT_DESIGN_REPO`) — no local path is
hardcoded anywhere in this package.

Two source kinds are supported, matching how each side actually stores
entities:

  `json_dir`    engine side: one entity per file, recursively under a
                directory (`data/affixes/base/af_vanguard.json` counts).
  `json_array`  design side: one snapshot file holding a JSON array of
                entity objects.

Both kinds apply the *same* id rule, so neither side is quietly more
permissive than the other: an entity's id must be a non-empty string, or
an integer (coerced, because the design library's snapshot keys some
tables by database autoincrement id). Anything else — empty string, null,
boolean, float, object — is recorded in `SideIds.no_id` and reported,
never silently accepted or silently dropped.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

ENGINE_ENV = "SOT_ENGINE_REPO"
DESIGN_ENV = "SOT_DESIGN_REPO"

#: Drive-qualified (`D:/x`) or UNC (`//host/share`) prefixes. On POSIX a
#: `Path("D:/x")` is merely relative, but `root / "D:/x"` on Windows throws
#: the root away entirely, so these are rejected as strings on every OS.
_DRIVE_OR_UNC = re.compile(r"^(?:[A-Za-z]:|[\\/]{2})")


class SourceError(Exception):
    """Environment / filesystem problem — the checker cannot even start."""


def bad_relative_path(value: object) -> str | None:
    """Why `value` is unusable as a repo-relative path, or None if it is fine.

    Purely lexical, deliberately: a path is judged before it is joined to
    anything, and a suspicious one is rejected rather than normalised into
    something acceptable. `resolve_within` then re-checks the joined result,
    which is what catches an escape through a symlink that no amount of
    string inspection could see.
    """
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    text = value.strip()
    if _DRIVE_OR_UNC.match(text):
        return (f"{text!r} is drive-qualified or a UNC path; paths must be "
                f"relative to the repository root")
    if text.startswith("/") or text.startswith("\\"):
        return f"{text!r} is absolute; paths must be relative to the root"
    parts = PurePosixPath(text.replace("\\", "/")).parts
    if ".." in parts:
        return (f"{text!r} contains a '..' segment; paths must stay inside "
                f"the repository root")
    return None


def resolve_within(root: Path, rel_path: str) -> Path:
    """Join `rel_path` under `root` and refuse anything that lands outside.

    `root` is expected to be already resolved (`resolve_roots` does that),
    so a symlinked or `..`-laden relative path cannot walk out of the
    repository and into, say, this repository's own files.
    """
    candidate = (root / rel_path).resolve()
    if not candidate.is_relative_to(root):
        raise SourceError(
            f"path {rel_path!r} resolves to {candidate}, which is outside the "
            f"repository root {root} — refusing to read it")
    return candidate


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
        # Resolved here and nowhere else: every containment check downstream
        # compares against this, so it has to be the canonical form.
        try:
            path = Path(raw).expanduser().resolve()
        except OSError as exc:
            problems.append(f"{label} repository path {raw!r} cannot be "
                            f"resolved: {exc}")
            continue
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
    no_id: list[str] = field(default_factory=list)        # "where: why"


def load_json(path: Path) -> object:
    """Read a JSON file, turning every failure mode into `SourceError`.

    `UnicodeDecodeError` is listed explicitly. It is a `ValueError`, not an
    `OSError` and not a `JSONDecodeError`, so it slips through the obvious
    two — and it is a live case here rather than a theoretical one: both
    repositories are full of Chinese text, and one save from an editor that
    defaults to the local codepage produces a file that is valid JSON and
    still undecodable as UTF-8.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise SourceError(
            f"{path} is not valid UTF-8 ({exc}); both repositories are "
            f"expected to store JSON as UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise SourceError(f"{path} is not valid JSON: {exc}") from exc


def normalise_id(value: object) -> tuple[str | None, str]:
    """Apply the one id rule both sides share. Returns (id, reason-if-bad).

    A non-empty string is taken as is. An integer is coerced, because the
    design library's snapshot keys some tables by database autoincrement id
    (`comments`); that coercion is deliberate and documented rather than an
    accident of `str()` being called on whatever showed up. Booleans are
    excluded even though `bool` is an `int`. Everything else — empty
    string, null, float, list, object — is rejected, and the caller reports
    it instead of dropping it.
    """
    if isinstance(value, str):
        if not value.strip():
            return None, "id is an empty string"
        return value, ""
    if isinstance(value, bool):
        return None, "id is a boolean"
    if isinstance(value, int):
        return str(value), ""
    if value is None:
        return None, "id field is absent or null"
    return None, f"id is a {type(value).__name__}, expected string or integer"


def collect_json_dir(root: Path, rel_path: str, id_field: str) -> SideIds:
    """Every `*.json` under `root/rel_path` (recursive) contributes one id.

    A file that yields no usable id is recorded in `SideIds.no_id` and
    surfaces as a finding. Whole directories of non-entity config (the
    engine keeps `data/runloop/*.json` next to entity data) are handled the
    other way round — the map excludes such a directory by name, with a
    stated reason, so nothing gets waved through file by file.
    """
    out = SideIds()
    base = resolve_within(root, rel_path)
    if not base.is_dir():
        raise SourceError(f"engine source directory missing: {base}")
    for path in sorted(_iter_json(base)):
        rel = path.relative_to(root).as_posix()
        obj = load_json(path)
        if not isinstance(obj, dict):
            out.no_id.append(f"{rel}: file holds a JSON "
                             f"{type(obj).__name__}, expected an object")
            continue
        value, why = normalise_id(obj.get(id_field))
        if value is None:
            out.no_id.append(f"{rel}: {why}")
            continue
        if value in out.ids:
            raise SourceError(
                f"duplicate id {value!r} in {base}: {out.origin[value]} and "
                f"{rel}"
            )
        out.ids.add(value)
        out.origin[value] = rel
    return out


def collect_json_array(root: Path, rel_path: str, id_field: str) -> SideIds:
    """Every element of the JSON array at `root/rel_path` contributes one id.

    Same id rule and same "report, do not drop" behaviour as
    `collect_json_dir`; the two sides differ only in how entities are laid
    out on disk, never in how strictly their ids are judged.
    """
    out = SideIds()
    path = resolve_within(root, rel_path)
    if not path.is_file():
        raise SourceError(f"design snapshot missing: {path}")
    obj = load_json(path)
    if not isinstance(obj, list):
        raise SourceError(f"{path} must hold a JSON array, got {type(obj).__name__}")
    rel = path.relative_to(root).as_posix()
    for index, item in enumerate(obj):
        if not isinstance(item, dict):
            out.no_id.append(f"{rel}[{index}]: element is a "
                             f"{type(item).__name__}, expected an object")
            continue
        value, why = normalise_id(item.get(id_field))
        if value is None:
            out.no_id.append(f"{rel}[{index}]: {why}")
            continue
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
    base = resolve_within(root, data_dir)
    if not base.is_dir():
        raise SourceError(f"engine data directory missing: {base}")
    try:
        children = sorted(base.iterdir())
    except OSError as exc:
        raise SourceError(f"cannot list {base}: {exc}") from exc
    entries = {f"{data_dir}/{p.name}" for p in children if p.is_dir()}
    entries |= {f"{data_dir}/{p.name}" for p in children
                if p.is_file() and p.suffix.lower() == ".json"}
    return entries


def design_snapshot_files(root: Path, snapshot_dir: str) -> set[str]:
    """Every `*.json` anywhere under the design `snapshots/` directory.

    Recursive on purpose — a snapshot filed into a new subdirectory is
    still new content the map has not been told about.
    """
    base = resolve_within(root, snapshot_dir)
    if not base.is_dir():
        raise SourceError(f"design snapshot directory missing: {base}")
    return {p.relative_to(root).as_posix() for p in _iter_json(base)}


def _iter_json(base: Path) -> list[Path]:
    """Recursive `*.json` listing that refuses to guess on an unreadable dir.

    Deliberately `os.walk(onerror=...)` rather than `Path.rglob`. Measured
    on Windows: `rglob` over a directory the process may not read does not
    raise — it yields nothing, so the directory silently reads as empty and
    every entity in it vanishes from the checker's view while the run still
    looks healthy. A silent wrong answer is worse than a crash, and much
    worse than a clean error, so the walk error is surfaced as a
    `SourceError` and becomes exit 2 through `main()`.
    """
    found: list[Path] = []

    def _onerror(exc: OSError) -> None:
        raise SourceError(
            f"cannot read {exc.filename or base} while walking {base}: {exc}"
        ) from exc

    for dirpath, _dirnames, filenames in os.walk(base, onerror=_onerror):
        for name in filenames:
            if name.lower().endswith(".json"):
                found.append(Path(dirpath) / name)
    return sorted(found)
