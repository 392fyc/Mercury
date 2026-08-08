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
import stat
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

    Note this *resolves* aliases rather than reporting them — by design,
    since containment is the question here. Whether the path travelled
    through an alias on the way is a separate question, answered by
    `alias_components`, which has to be asked before this point or the
    evidence is already gone.
    """
    try:
        candidate = (root / rel_path).resolve()
    except (OSError, RuntimeError) as exc:
        # RuntimeError: defensive. `resolve()` is documented to raise it on a
        # symlink loop in some versions, but this machine cannot create
        # symlinks (no privilege) and junctions cannot form a loop, so it was
        # never reproduced here. Caught to keep the exit-code contract, not
        # because a failure was observed.
        raise SourceError(f"cannot resolve {rel_path!r} under {root}: "
                          f"{exc}") from exc
    if not candidate.is_relative_to(root):
        raise SourceError(
            f"path {rel_path!r} resolves to {candidate}, which is outside the "
            f"repository root {root} — refusing to read it")
    return candidate


def alias_components(root: Path, rel_path: str) -> list[str]:
    """Every component of `root/rel_path` that is itself a directory alias.

    The declared source directory *itself* can be a symlink or junction —
    `data/classes` rather than something inside it. `resolve_within` cannot
    see that, because resolving is exactly what erases the evidence, so
    that case used to be followed in silence while an alias one level
    deeper was reported. Same rule for both now: an alias anywhere on the
    declared path is reported.
    """
    found: list[str] = []
    partial = root
    for part in PurePosixPath(rel_path.replace("\\", "/")).parts:
        partial = partial / part
        if partial.is_symlink() or _is_reparse_dir(partial):
            found.append(_rel(partial, root))
    return found


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
        # `--engine-repo=` (explicitly empty) is a mistake, not a request to
        # fall back. Treating the two as the same thing meant a caller who
        # deliberately blanked the flag silently got whatever the ambient
        # environment happened to point at — the opposite of what they asked
        # for, and invisible in the output.
        if override is not None:
            if not override.strip():
                problems.append(
                    f"--{label}-repo was given an empty value; pass a real "
                    f"path or omit the flag to use ${var}")
                continue
            raw = override
        else:
            raw = env.get(var, "")
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
        # RuntimeError is defensive only — see the note in `resolve_within`.
        except (OSError, RuntimeError) as exc:
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
    #: directory symlinks/junctions inside the source, not followed
    links: list[str] = field(default_factory=list)
    #: files that resolve outside the repository root
    escaping: list[str] = field(default_factory=list)


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
    except RecursionError as exc:
        # Reproduced: a deeply nested (but syntactically valid) JSON array
        # blows the decoder's stack. `RecursionError` is a `RuntimeError`,
        # so none of the three above catch it, and it used to surface as a
        # traceback exiting 1 — the code that means "the map has findings".
        raise SourceError(
            f"{path} is nested too deeply for the JSON decoder ({exc}); an "
            f"entity file this deep is not something this checker can read"
        ) from exc


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
    listing = _iter_json(base, root)
    # Aliases on the declared path itself come first: they are the ones
    # `resolve_within` above has already resolved away.
    out.links = alias_components(root, rel_path) + listing.links
    out.escaping = listing.escaping
    for path in listing.files:
        rel = _rel(path, root)
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
    out.links = alias_components(root, rel_path)
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
    listing = Listing()
    listing.links = alias_components(root, data_dir)
    base = resolve_within(root, data_dir)
    if not base.is_dir():
        raise SourceError(f"engine data directory missing: {base}")
    try:
        children = sorted(base.iterdir())
    except OSError as exc:
        raise SourceError(f"cannot list {base}: {exc}") from exc
    entries: set[str] = set()
    for child in children:
        name = f"{data_dir}/{child.name}"
        if child.is_symlink() or _is_reparse_dir(child):
            listing.links.append(_rel(child, root))
        kind = _entry_kind(child)
        if kind == "dir":
            entries.add(name)
        elif kind == "file" and child.suffix.lower() == ".json":
            entries.add(name)
        elif kind == "unknown":
            # Defensive: `Path.is_dir()` swallows OSError and answers False,
            # so an entry that cannot be stat'ed could drop out of the scope
            # set and stop being "undeclared". NOT REPRODUCED — an `icacls
            # /deny` on a directory still stats fine, so this branch has never
            # been observed to run. Kept because the safe answer to "I cannot
            # tell what this is" is to demand it be declared, not to forget it.
            entries.add(name)
    return entries, listing


def _entry_kind(path: Path) -> str:
    """'dir' / 'file' / 'unknown' — never a silent False on a stat failure."""
    try:
        mode = path.stat(follow_symlinks=False).st_mode
    except OSError:
        return "unknown"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        # A link's own kind is whatever it points at; ask, but do not let a
        # broken target turn into "not here".
        try:
            return "dir" if path.is_dir() else "file"
        except OSError:
            return "unknown"
    return "unknown"


def design_snapshot_files(root: Path, snapshot_dir: str) -> tuple[set[str],
                                                                  Listing]:
    """Every `*.json` anywhere under the design `snapshots/` directory.

    Recursive on purpose — a snapshot filed into a new subdirectory is
    still new content the map has not been told about. The `Listing` comes
    back too so the caller can report symlinked directories rather than let
    them vanish from the scope guard's view.
    """
    aliases = alias_components(root, snapshot_dir)
    base = resolve_within(root, snapshot_dir)
    if not base.is_dir():
        raise SourceError(f"design snapshot directory missing: {base}")
    listing = _iter_json(base, root)
    listing.links = aliases + listing.links
    return {_rel(p, root) for p in listing.files}, listing


@dataclass
class Listing:
    """A recursive listing plus everything about it that needs saying."""

    files: list[Path] = field(default_factory=list)
    #: directory symlinks/junctions found on the way, not followed
    links: list[str] = field(default_factory=list)
    #: files that resolve outside the repository root (symlinked away)
    escaping: list[str] = field(default_factory=list)


def _iter_json(base: Path, root: Path) -> Listing:
    """Recursive `*.json` listing that never silently loses content.

    Three ways a walk can quietly lie, all handled here rather than
    discovered later as missing entities:

    * **Unreadable directory.** Deliberately `os.walk(onerror=...)` rather
      than `Path.rglob`. Measured on Windows: `rglob` over a directory the
      process may not read does not raise — it yields nothing, so the
      directory reads as empty and every entity in it vanishes while the
      run still looks healthy.
    * **Directory symlink or junction.** `os.walk` does not follow these by
      default and says nothing about them, so an entity directory reached
      through a link would simply not exist as far as the map is concerned,
      and the scope guard — which only sees the parent — would not notice.
      They are reported instead. Following them is not the fix: a link can
      point outside the repository, which would walk straight through the
      containment guarantee.
    * **File symlink pointing out of the repository.** Followed by the walk
      as an ordinary file, so containment is re-checked per file.

    Nothing here raises for the last two: they are facts about the
    repository, not about the map, so they travel as findings.
    """
    listing = Listing()

    def _onerror(exc: OSError) -> None:
        raise SourceError(
            f"cannot read {exc.filename or base} while walking {base}: {exc}"
        ) from exc

    for dirpath, dirnames, filenames in os.walk(base, onerror=_onerror):
        here = Path(dirpath)
        keep: list[str] = []
        for name in dirnames:
            path = here / name
            if path.is_symlink() or _is_reparse_dir(path):
                listing.links.append(_rel(path, root))
            else:
                keep.append(name)
        # Pruned rather than left to the platform. `os.walk(followlinks=False)`
        # skips POSIX symlinks but, measured here, walks straight through a
        # Windows junction — so the same repository would be read differently
        # on different machines, and one of those readings pulls in content
        # through an alias the map never declared. One rule instead: an
        # aliased directory is never traversed and always reported.
        dirnames[:] = keep
        for name in filenames:
            if not name.lower().endswith(".json"):
                continue
            path = here / name
            try:
                resolved = path.resolve()
            # RuntimeError is defensive only — see the note in `resolve_within`.
            except (OSError, RuntimeError) as exc:
                raise SourceError(f"cannot resolve {path}: {exc}") from exc
            if not resolved.is_relative_to(root):
                listing.escaping.append(f"{_rel(path, root)} -> {resolved}")
                continue
            listing.files.append(path)
    listing.files.sort()
    return listing


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _is_reparse_dir(path: Path) -> bool:
    """True for a Windows junction, which `is_symlink()` reports as False.

    Junctions are a real alias mechanism on this platform (this repository's
    own push-guard notes list them alongside `subst`), and `os.walk` skips
    them exactly like symlinks, so they need the same treatment.
    """
    try:
        return bool(path.stat(follow_symlinks=False).st_reparse_tag)
    except (OSError, AttributeError):
        return False
