"""Mechanical validation of `id_map.json` against both live repositories.

The checker answers exactly one question: *does the hand-maintained map
still describe what is actually in the two repositories?* It never edits
anything, and it never guesses a correspondence — an id it cannot account
for is reported, not silently absorbed.

Failure classes (all of them exit non-zero and name the offending id):

  `undeclared_scope`   an engine `data/` directory or loose file, or a
                       design snapshot file, the map has never heard of
  `uncovered_id`       an id that exists on one side but appears in
                       neither `mappings` nor `unmapped`
  `missing_entity_id`  a file inside a declared entity directory that
                       carries no usable id
  `cross_side_contradiction`
                       the same id string exists on both sides yet is
                       declared unmapped on one of them
  `unknown_id`         the map references an id that does not exist on the
                       side it claims
  `duplicate_id`       the same id claimed twice within one entity type
  `bad_reason`         an `unmapped` reason outside the controlled enum
  `missing_carrier`    `engine_implements_as_field` without an
                       `engine_carrier` pointer that resolves to a real
                       field of a real engine file
  `bad_cardinality`    declared cardinality contradicts the id counts
  `bad_map`            the map file itself is structurally broken
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import sources
from .sources import Roots, SideIds, SourceError

MAP_PATH = Path(__file__).resolve().parent / "id_map.json"

#: `engine_carrier` values look like `data/classes/kensei.json#sword_qi_config`.
CARRIER_SEP = "#"


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - formatting only
        return f"[{self.kind}] {self.message}"


@dataclass
class Report:
    findings: list[Finding]
    stats: dict

    @property
    def ok(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "findings": [{"kind": f.kind, "message": f.message}
                         for f in self.findings],
            "stats": self.stats,
        }


def load_map(path: Path | None = None) -> dict:
    path = MAP_PATH if path is None else path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SourceError(f"cannot read id map {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise SourceError(f"id map {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SourceError(f"id map {path} must be a JSON object")
    return data


def _side_ids(roots: Roots, entity_types: list[dict]) -> dict[tuple[str, str], SideIds]:
    """Collect `(entity type, side) -> ids` for every declared source."""
    collected: dict[tuple[str, str], SideIds] = {}
    for entry in entity_types:
        etype = entry["type"]
        for side, root in (("engine", roots.engine), ("design", roots.design)):
            spec = entry.get(side)
            if not spec:
                continue
            collected[(etype, side)] = sources.collect(root, spec)
    return collected


def _check_scope(roots: Roots, spec: dict, entity_types: list[dict],
                 findings: list[Finding]) -> None:
    """Every engine data dir / design snapshot file must be accounted for."""
    engine_scope = spec["engine_scope"]
    declared = {e["engine"]["path"] for e in entity_types if e.get("engine")}
    declared |= set(engine_scope.get("excluded_paths", {}))
    actual = sources.engine_scope_entries(roots.engine,
                                          engine_scope["data_dir"])
    for missing in sorted(actual - declared):
        findings.append(Finding(
            "undeclared_scope",
            f"engine path {missing} is not declared in id_map.json "
            f"(add an entity_types entry or list it under "
            f"engine_scope.excluded_paths with a reason)"))
    for gone in sorted(declared - actual):
        findings.append(Finding(
            "undeclared_scope",
            f"engine path {gone} is declared in id_map.json but no "
            f"longer exists in the engine repository"))

    design_scope = spec["design_scope"]
    declared_d = {e["design"]["path"] for e in entity_types if e.get("design")}
    declared_d |= set(design_scope.get("excluded_paths", {}))
    actual_d = sources.design_snapshot_files(roots.design,
                                             design_scope["snapshot_dir"])
    for missing in sorted(actual_d - declared_d):
        findings.append(Finding(
            "undeclared_scope",
            f"design snapshot {missing} is not declared in id_map.json "
            f"(add an entity_types entry or list it under "
            f"design_scope.excluded_paths with a reason)"))
    for gone in sorted(declared_d - actual_d):
        findings.append(Finding(
            "undeclared_scope",
            f"design snapshot {gone} is declared in id_map.json but no "
            f"longer exists in the design repository"))


def _claim(claimed: dict[tuple[str, str], dict[str, str]], etype: str,
           side: str, entity_id: str, where: str,
           findings: list[Finding]) -> None:
    bucket = claimed.setdefault((etype, side), {})
    previous = bucket.get(entity_id)
    if previous is not None:
        findings.append(Finding(
            "duplicate_id",
            f"{side} {etype} id {entity_id!r} is claimed twice: "
            f"{previous} and {where}"))
        return
    bucket[entity_id] = where


def _check_known(collected: dict[tuple[str, str], SideIds], etype: str,
                 side: str, entity_id: str, where: str,
                 findings: list[Finding]) -> None:
    known = collected.get((etype, side))
    if known is None:
        findings.append(Finding(
            "bad_map",
            f"{where} references {side} side of entity type {etype!r}, but "
            f"the map declares no {side} source for that type"))
        return
    if entity_id not in known.ids:
        findings.append(Finding(
            "unknown_id",
            f"{where} references {side} {etype} id {entity_id!r}, which does "
            f"not exist in the {side} repository"))


#: Escape hatch for a genuine id collision — see `_check_cross_side`.
SAME_ID_FIELD = "same_id_other_side"


def _check_cross_side(entity_types: list[dict],
                      collected: dict[tuple[str, str], SideIds],
                      unmapped_index: dict[tuple[str, str], dict[str, dict]],
                      findings: list[Finding]) -> None:
    """The same id string on both sides may not be declared unmapped.

    Coverage alone only asks "is this id accounted for", never "is the
    account true". That leaves one way to pass while saying something
    false: split an id that exists on *both* sides into two `unmapped`
    entries whose reasons contradict each other — engine side claiming the
    design library never registered it while the design side claims the
    engine never implemented it, when in fact both have it.

    So: if an id string is present in both side's id universes for one
    entity type, an `unmapped` entry for it must justify itself in a
    `same_id_other_side` note explaining why the same-named entity on the
    other side is not the same thing. That leaves room for a real id
    collision between the two taxonomies without leaving room for a silent
    contradiction.
    """
    for entry in entity_types:
        etype = entry["type"]
        if entry.get("coverage") == "excluded":
            continue
        engine = collected.get((etype, "engine"))
        design = collected.get((etype, "design"))
        if engine is None or design is None:
            continue
        for entity_id in sorted(engine.ids & design.ids):
            for side in ("engine", "design"):
                item = unmapped_index.get((etype, side), {}).get(entity_id)
                if item is None:
                    continue
                if str(item.get(SAME_ID_FIELD, "")).strip():
                    continue
                findings.append(Finding(
                    "cross_side_contradiction",
                    f"{side} {etype} id {entity_id!r} is declared unmapped "
                    f"(reason {item.get('reason')!r}), but that same id also "
                    f"exists on the {'design' if side == 'engine' else 'engine'} "
                    f"side ({engine.origin[entity_id]} and "
                    f"{design.origin[entity_id]}) — map the pair, or state in "
                    f"a {SAME_ID_FIELD!r} field why the two are not the same "
                    f"entity"))


def _cardinality(n_engine: int, n_design: int) -> str:
    left = "1" if n_engine == 1 else "N"
    right = "1" if n_design == 1 else "N"
    return f"{left}:{right}"


def check(roots: Roots, spec: dict) -> Report:
    findings: list[Finding] = []
    entity_types: list[dict] = spec["entity_types"]
    by_type = {e["type"]: e for e in entity_types}
    if len(by_type) != len(entity_types):
        findings.append(Finding("bad_map",
                                "entity_types contains duplicate type names"))
    reason_codes = set(spec["reason_codes"])

    _check_scope(roots, spec, entity_types, findings)
    collected = _side_ids(roots, entity_types)

    # `(type, side) -> {id: where}` — enforces "at most one claim per id".
    claimed: dict[tuple[str, str], dict[str, str]] = {}
    # `(type, side) -> {id: unmapped entry}` — used by the cross-side check.
    unmapped_index: dict[tuple[str, str], dict[str, dict]] = {}

    for index, mapping in enumerate(spec["mappings"]):
        where = f"mappings[{index}]"
        etype = mapping.get("type")
        entry = by_type.get(etype)
        if entry is None:
            findings.append(Finding("bad_map",
                                    f"{where} has unknown entity type {etype!r}"))
            continue
        if entry.get("coverage") == "excluded":
            findings.append(Finding(
                "bad_map",
                f"{where} maps entity type {etype!r}, which the map declares "
                f"out of scope (coverage=excluded)"))
            continue
        engine_ids = mapping.get("engine_ids") or []
        design_ids = mapping.get("design_ids") or []
        if not engine_ids or not design_ids:
            findings.append(Finding(
                "bad_map",
                f"{where} must list at least one engine id and one design id "
                f"(use `unmapped` for one-sided entries)"))
            continue
        if not str(mapping.get("basis", "")).strip():
            findings.append(Finding(
                "bad_map",
                f"{where} has no `basis` — every mapping must cite where the "
                f"correspondence is traceable from"))
        declared_card = mapping.get("cardinality")
        expected_card = _cardinality(len(engine_ids), len(design_ids))
        if declared_card != expected_card:
            findings.append(Finding(
                "bad_cardinality",
                f"{where} declares cardinality {declared_card!r} but lists "
                f"{len(engine_ids)} engine id(s) and {len(design_ids)} design "
                f"id(s) (= {expected_card})"))
        for side, ids in (("engine", engine_ids), ("design", design_ids)):
            for entity_id in ids:
                _check_known(collected, etype, side, entity_id, where, findings)
                _claim(claimed, etype, side, entity_id, where, findings)

    for index, item in enumerate(spec["unmapped"]):
        where = f"unmapped[{index}]"
        etype = item.get("type")
        side = item.get("side")
        entity_id = item.get("id")
        entry = by_type.get(etype)
        if entry is None:
            findings.append(Finding("bad_map",
                                    f"{where} has unknown entity type {etype!r}"))
            continue
        if entry.get("coverage") == "excluded":
            findings.append(Finding(
                "bad_map",
                f"{where} lists entity type {etype!r}, which the map declares "
                f"out of scope (coverage=excluded)"))
            continue
        if side not in ("engine", "design"):
            findings.append(Finding("bad_map",
                                    f"{where} has invalid side {side!r}"))
            continue
        if not isinstance(entity_id, str) or not entity_id:
            findings.append(Finding("bad_map", f"{where} has no id"))
            continue
        reason = item.get("reason")
        if reason not in reason_codes:
            findings.append(Finding(
                "bad_reason",
                f"{where} ({side} {etype} {entity_id!r}) uses reason "
                f"{reason!r}, which is not one of the controlled codes: "
                f"{', '.join(sorted(reason_codes))}"))
        if reason == "engine_implements_as_field":
            _check_carrier(roots, item, where, findings)
        _check_known(collected, etype, side, entity_id, where, findings)
        _claim(claimed, etype, side, entity_id, where, findings)
        unmapped_index.setdefault((etype, side), {})[entity_id] = item

    _check_cross_side(entity_types, collected, unmapped_index, findings)

    # Coverage: every id on a required entity type must have been claimed.
    stats: dict = {"entity_types": {}}
    for entry in entity_types:
        etype = entry["type"]
        per_type: dict = {"coverage": entry.get("coverage", "required")}
        for side in ("engine", "design"):
            known = collected.get((etype, side))
            if known is None:
                per_type[side] = None
                continue
            per_type[side] = len(known.ids)
            for orphan in known.no_id:
                findings.append(Finding(
                    "missing_entity_id",
                    f"{orphan} is inside the declared {side} {etype} source "
                    f"but carries no usable "
                    f"{(entry[side] or {}).get('id_field', 'id')!r} field, so "
                    f"the map cannot see it"))
            if entry.get("coverage") == "excluded":
                continue
            accounted = set(claimed.get((etype, side), {}))
            for entity_id in sorted(known.ids - accounted):
                findings.append(Finding(
                    "uncovered_id",
                    f"{side} {etype} id {entity_id!r} "
                    f"({known.origin[entity_id]}) is neither mapped nor "
                    f"explicitly listed as unmapped"))
        stats["entity_types"][etype] = per_type
    stats["mappings"] = len(spec["mappings"])
    stats["unmapped"] = len(spec["unmapped"])
    stats["engine_ids_total"] = sum(
        len(v.ids) for (t, s), v in collected.items() if s == "engine")
    stats["design_ids_total"] = sum(
        len(v.ids) for (t, s), v in collected.items() if s == "design")
    return Report(findings=findings, stats=stats)


def _check_carrier(roots: Roots, item: dict, where: str,
                   findings: list[Finding]) -> None:
    """`engine_implements_as_field` must point at a real field of a real file.

    This is the reason code for "both sides have this thing, but the engine
    built it as a field of another entity instead of an entity of its own".
    Calling such a case `engine_not_implemented` would be a wrong
    classification, and the entire evidence for the classification is *which
    field* carries it — so the field half of the pointer is resolved too,
    not just the file half. A pointer that names no field, or names one that
    is not in the file, is exactly as unproven as no pointer at all.

    The field part is a dot path (`a.b.c`) walked through nested objects.
    """
    carrier = item.get("engine_carrier")
    example = f"data/classes/kensei.json{CARRIER_SEP}sword_qi_config"
    if not isinstance(carrier, str) or not carrier.strip():
        findings.append(Finding(
            "missing_carrier",
            f"{where} uses reason engine_implements_as_field but has no "
            f"`engine_carrier` (expected e.g. {example!r})"))
        return
    if CARRIER_SEP not in carrier:
        findings.append(Finding(
            "missing_carrier",
            f"{where} has engine_carrier {carrier!r} with no "
            f"{CARRIER_SEP}<field> part — name the field that actually "
            f"carries it (e.g. {example!r})"))
        return
    rel, _, field_path = carrier.partition(CARRIER_SEP)
    path = roots.engine / rel
    if not path.is_file():
        findings.append(Finding(
            "missing_carrier",
            f"{where} points at engine carrier {carrier!r}, but "
            f"{rel} does not exist in the engine repository"))
        return
    if not field_path.strip():
        findings.append(Finding(
            "missing_carrier",
            f"{where} has engine_carrier {carrier!r} with an empty field "
            f"name (expected e.g. {example!r})"))
        return
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        findings.append(Finding(
            "missing_carrier",
            f"{where} points at engine carrier {carrier!r}, but {rel} could "
            f"not be read as JSON: {exc}"))
        return
    cursor = obj
    walked: list[str] = []
    for part in field_path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            at = "".join(f".{p}" for p in walked) or "<top level>"
            findings.append(Finding(
                "missing_carrier",
                f"{where} points at engine carrier {carrier!r}, but field "
                f"{part!r} does not exist at {at} in {rel}"))
            return
        cursor = cursor[part]
        walked.append(part)
