"""Read-only contract tests for the SoT canonical lane-authority document."""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path


DESIGN_ROOT = Path(
    os.environ.get("SOT_DESIGN_ROOT", r"D:\ShipOfTheseus\SoT-fyc-space")
)
CANONICAL_DOCUMENT = DESIGN_ROOT / "docs" / "mercury-sot-lane-management.md"
ACCEPTANCE_DOCUMENT = (
    DESIGN_ROOT / "docs" / "migration" / "2026-08-15-codex-lane-acceptance.md"
)
SKILLS_SNAPSHOT = DESIGN_ROOT / "snapshots" / "skills.json"

PROTECTED_FIELDS = ("power", "cd", "range")
STABLE_FIELDS = {
    "id", "code", "group", "category", "class_ids", "text", "status", "version"
}
NORMALIZATION_CONTRACT = {
    "schema": "mercury.rules-parity.v1",
    "class_ids_order": "unicode-code-point-ascending",
    "record_order": "id-ascending",
    "json_key_order": "sorted",
    "json_separators": "comma-colon",
    "ensure_ascii": "false",
    "encoding": "utf-8",
    "trailing_newline": "false",
    "hash_algorithm": "sha-256",
    "difference_semantics": "missing-id-or-stable-field-difference",
}


def _plain(value: str) -> str:
    # Underscores are contract-significant in field names such as class_ids.
    return re.sub(r"[`*~]", "", value).strip()


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", _plain(value)).casefold()


@dataclass(frozen=True)
class MarkdownTable:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class AuthorityRegistry:
    table: MarkdownTable
    field_column: int
    design_column: int
    mirror_column: int
    engine_column: int


def parse_markdown_tables(text: str) -> tuple[MarkdownTable, ...]:
    """Parse ordinary pipe tables without depending on a Markdown renderer."""
    lines = text.splitlines()
    tables: list[MarkdownTable] = []
    index = 0
    separator = re.compile(r"^:?-{3,}:?$")

    def cells(line: str) -> tuple[str, ...]:
        return tuple(part.strip() for part in line.strip().strip("|").split("|"))

    while index + 1 < len(lines):
        header = cells(lines[index]) if "|" in lines[index] else ()
        divider = cells(lines[index + 1]) if "|" in lines[index + 1] else ()
        if (
            len(header) >= 2
            and len(header) == len(divider)
            and all(separator.fullmatch(_plain(part)) for part in divider)
        ):
            rows: list[tuple[str, ...]] = []
            index += 2
            while index < len(lines) and "|" in lines[index]:
                row = cells(lines[index])
                if len(row) != len(header):
                    break
                rows.append(row)
                index += 1
            tables.append(MarkdownTable(header, tuple(rows)))
            continue
        index += 1
    return tuple(tables)


def classify_owner(value: str) -> str | None:
    """Classify a single owner expression; reject negated or mixed owners."""
    normalized = _normalized(value)
    owner_term = r"design-library|设计库|sot(?: main)?|godot|引擎(?:侧)?|mercury"
    if re.search(rf"(?:\bnot\b|不是|并非|非)\s*(?:{owner_term})", normalized):
        raise ValueError(f"negated owner expression is not canonical: {value}")

    has_user = "用户" in normalized or bool(re.search(r"\buser\b", normalized))
    has_sot = bool(re.search(r"\bsot\b", normalized))
    has_engine = "godot" in normalized or "引擎侧" in normalized
    candidates: set[str] = set()
    if "generator-only" in normalized or "无人手写" in normalized:
        candidates.add("generator-only")
    if "发起方" in normalized or "initiator" in normalized:
        candidates.add("initiator")
    if has_user and has_sot:
        candidates.add("user+sot")
    elif has_sot or has_engine:
        candidates.add("sot")
    if "mercury" in normalized:
        candidates.add("mercury")
    if "design-library" in normalized or "设计库" in normalized:
        candidates.add("design-library")
    if len(candidates) > 1:
        raise ValueError(f"mixed owner expression is not canonical: {value}")
    return next(iter(candidates), None)


FIELD_PATTERNS = {
    "power": re.compile(r"(?<![a-z0-9_])power(?![a-z0-9_])", re.IGNORECASE),
    "cd": re.compile(
        r"(?<![a-z0-9_])cd(?![a-z0-9_])|(?<![a-z0-9_])cooldown(?![a-z0-9_])|冷却",
        re.IGNORECASE,
    ),
    "range": re.compile(
        r"(?<![a-z0-9_])range(?:[._][a-z]+)?(?![a-z0-9_])|射程",
        re.IGNORECASE,
    ),
}


def field_owner_claims(tables: tuple[MarkdownTable, ...], field: str) -> set[str]:
    """Collect field claims only from explicit authority/owner columns."""
    pattern = FIELD_PATTERNS[field]
    claims: set[str] = set()
    for table in tables:
        explicit_owner_columns = tuple(
            index
            for index, header in enumerate(table.headers)
            if re.search(
                r"归属|权威|单写方|现在归谁|允许写者|owner|authority",
                header,
                re.IGNORECASE,
            )
            and not re.search(r"mirror|镜像|representation|表示", header, re.IGNORECASE)
        )
        for row in table.rows:
            if not any(pattern.search(_normalized(cell)) for cell in row):
                continue
            for owner_column in explicit_owner_columns:
                owner = classify_owner(row[owner_column])
                if owner is None:
                    raise ValueError(
                        f"unclassified owner for {field}: {row[owner_column]}"
                    )
                claims.add(owner)
    return claims


def owner_claims_for_scope(
    tables: tuple[MarkdownTable, ...], scope_pattern: str
) -> tuple[tuple[str, str], ...]:
    pattern = re.compile(scope_pattern, re.IGNORECASE)
    claims: list[tuple[str, str]] = []
    for table in tables:
        for row in table.rows:
            if not row or not pattern.search(_normalized(row[0])):
                continue
            owner = classify_owner(row[1]) if len(row) > 1 else None
            if owner is not None:
                claims.append((owner, " | ".join(row)))
    return tuple(claims)


def find_authority_registry(tables: tuple[MarkdownTable, ...]) -> AuthorityRegistry | None:
    """Locate the table separating authority, mirror, and representation."""
    registries: list[AuthorityRegistry] = []
    for table in tables:
        headers = tuple(_normalized(header) for header in table.headers)
        field_columns = [
            index for index, header in enumerate(headers) if header in {"字段", "field"}
        ]
        design_columns = [
            index
            for index, header in enumerate(headers)
            if ("design" in header or "设计库" in header)
            and ("authority" in header or "权威" in header)
        ]
        mirror_columns = [
            index
            for index, header in enumerate(headers)
            if "godot" in header and ("mirror" in header or "镜像" in header)
        ]
        engine_columns = [
            index
            for index, header in enumerate(headers)
            if ("engine" in header or "引擎" in header)
            and ("representation" in header or "taxonomy" in header or "表示" in header)
            and ("only" in header or "独有" in header)
        ]
        if all(
            len(columns) == 1
            for columns in (field_columns, design_columns, mirror_columns, engine_columns)
        ):
            registries.append(
                AuthorityRegistry(
                    table,
                    field_columns[0],
                    design_columns[0],
                    mirror_columns[0],
                    engine_columns[0],
                )
            )
    if len(registries) > 1:
        raise ValueError("multiple machine-readable authority registries found")
    return registries[0] if registries else None


def registry_rows(registry: AuthorityRegistry) -> dict[str, tuple[str, ...]]:
    rows: dict[str, tuple[str, ...]] = {}
    for row in registry.table.rows:
        field = _normalized(row[registry.field_column])
        if field in PROTECTED_FIELDS:
            if field in rows:
                raise ValueError(f"duplicate authority registry row: {field}")
            rows[field] = row
    return rows


def _code_identifiers(value: str) -> set[str]:
    identifiers = set(re.findall(r"`([a-z][a-z0-9_.]*)`", value, re.IGNORECASE))
    identifiers.update(
        match.group(0).casefold()
        for match in re.finditer(
            r"(?<![a-z0-9_])(?:power|cd|cooldown|range(?:[._][a-z]+)?)(?![a-z0-9_])",
            value,
            re.IGNORECASE,
        )
    )
    return {identifier.casefold() for identifier in identifiers}


def forbidden_prose_authority_claims(text: str) -> tuple[str, ...]:
    """Scan every non-table line for a protected-field engine authority claim."""
    violations: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        normalized = _normalized(stripped)
        fields = [
            field for field, pattern in FIELD_PATTERNS.items() if pattern.search(normalized)
        ]
        if not fields:
            continue

        negative_design = bool(
            re.search(r"(?:\bnot\b|不是|并非)\s*(?:design-library|设计库)", normalized)
        )
        design_absent = (
            "设计库无对应列" in normalized or "no design-library column" in normalized
        )
        engine_exclusive = bool(
            re.search(r"引擎(?:侧)?独有|engine[- ]only|godot[- ]only|sot[- ]only", normalized)
        )
        non_mirror = bool(
            re.search(r"非镜像|non[- ]mirror|not (?:a )?mirror", normalized)
        )
        explicit_engine_authority = bool(
            re.search(
                r"(?:authority|authoritative|权威|归属|归).{0,50}(?:sot|godot|引擎)",
                normalized,
            )
            or re.search(
                r"(?:sot|godot|引擎).{0,50}(?:authority|authoritative|权威|owns?|独有)",
                normalized,
            )
        )
        safe_denial = bool(
            re.search(r"不(?:赋予|形成|构成|拥有|改变).{0,40}权威", normalized)
            or re.search(r"(?:non-authoritative|非权威|not authoritative)", normalized)
            or re.search(r"只能.{0,20}(?:mirror|镜像)", normalized)
        )
        safe_range_taxonomy = bool(
            "range.type" in normalized
            and re.search(r"taxonomy|representation|表示|分类", normalized)
            and re.search(
                r"non-authoritative|非权威|不(?:改变|构成).{0,30}(?:design-library|设计库).{0,15}权威",
                normalized,
            )
        )
        unsafe = negative_design or design_absent
        unsafe = unsafe or (explicit_engine_authority and not safe_denial)
        unsafe = unsafe or ((engine_exclusive or non_mirror) and not safe_range_taxonomy)
        if unsafe:
            violations.append(f"line {line_number} [{','.join(fields)}]: {stripped}")
    return tuple(violations)


def _labeled_value(text: str, key: str) -> str | None:
    match = re.search(
        rf"(?:^|[;；])\s*`?{re.escape(key)}`?\s*[:=]\s*([^;；]+)",
        _plain(text),
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def carrier_contract_errors(
    field: str, design_cell: str, mirror_cell: str
) -> tuple[str, ...]:
    """Validate executable source/consumer metadata without reading live values."""
    errors: list[str] = []
    source = _labeled_value(design_cell, "source_attributes")
    expected_sources = (
        {"effect", "rules", "notes"}
        if field in {"power", "cd"}
        else {"range_min", "range_max", "range_shape"}
    )
    if source is None:
        errors.append(f"{field}: missing source_attributes")
    else:
        source_tokens = re.findall(r"[a-z][a-z0-9_]*", source, re.IGNORECASE)
        if len(source_tokens) != len(expected_sources) or {
            token.casefold() for token in source_tokens
        } != expected_sources:
            errors.append(f"{field}: source_attributes must be the exact schema set")

    source_carrier = _labeled_value(design_cell, "source_carrier")
    expected_carriers = (
        {"skill.effect", "skill.rules", "skill.notes"}
        if field in {"power", "cd"}
        else {"skill.range_min", "skill.range_max", "skill.range_shape"}
    )
    carrier_contract = (
        "Skill.effect/Skill.rules/Skill.notes"
        if field in {"power", "cd"}
        else "Skill.range_min/Skill.range_max/Skill.range_shape"
    )
    if source_carrier is None:
        errors.append(f"{field}: missing source_carrier")
    else:
        normalized_carrier = _normalized(source_carrier)
        carrier_fields = re.findall(r"skill\.[a-z_]+", normalized_carrier)
        if len(carrier_fields) != 3 or set(carrier_fields) != expected_carriers:
            errors.append(
                f"{field}: source_carrier must use only {carrier_contract}"
            )

    consumer = _labeled_value(mirror_cell, "consumer_key")
    expected_consumer = "cooldown" if field == "cd" else (
        "range.type" if field == "range" else "power"
    )
    if consumer is None or _normalized(consumer) != expected_consumer:
        errors.append(f"{field}: consumer_key must equal {expected_consumer}")

    mirror_scope = _labeled_value(mirror_cell, "mirror_scope")
    natural_scope = re.search(
        r"仅限.{0,30}(?:存在|已有).{0,20}设计库.{0,20}(?:canonical )?(?:record|记录)",
        _normalized(mirror_cell),
    )
    if (
        mirror_scope is None
        or _normalized(mirror_scope) != "design-record-present"
    ) and not natural_scope:
        errors.append(f"{field}: mirror_scope must be design-record-present")

    if field in {"power", "cd"}:
        exact_semantics = {
            "scalar_status": "blocked/unverifiable until normalized field",
            "parse_semantics": "no mechanical scalar",
            "empty_semantics": "unresolved/unverifiable/no overwrite",
            "placeholder_semantics": "unresolved/unverifiable/no overwrite",
            "concept_only_semantics": "non-scalar unresolved/no overwrite",
        }
        for key, expected in exact_semantics.items():
            value = _labeled_value(design_cell, key)
            if value is None:
                errors.append(f"{field}: missing {key}")
                continue
            if _normalized(value) != expected:
                errors.append(f"{field}: {key} must equal {expected}")
        placeholder_token = _labeled_value(design_cell, "placeholder_token")
        if placeholder_token != "[占位]":
            errors.append(f"{field}: placeholder_token must be literal [占位]")
        mirror_status = _labeled_value(mirror_cell, "mirror_status")
        if (
            mirror_status is None
            or _normalized(mirror_status)
            != "blocked/unverifiable until normalized field"
        ):
            errors.append(
                f"{field}: mirror_status must equal blocked/unverifiable until normalized field"
            )
        if re.search(
            r"(?:现值|current value).{0,30}(?:已验证|verified).{0,20}(?:mirror|镜像)",
            _normalized(mirror_cell),
        ):
            errors.append(f"{field}: unverifiable current values cannot be called verified mirrors")
    return tuple(errors)


def snapshot_placeholder_contract_errors(
    design_cell: str, records: object
) -> tuple[str, ...]:
    """Bind the registry token to committed carrier shapes without exposing values."""
    token = _labeled_value(design_cell, "placeholder_token")
    if token is None:
        return ("missing placeholder_token for snapshot binding",)
    if not isinstance(records, list) or not records:
        return ("skills snapshot must be a non-empty record array",)

    carrier_fields = ("effect", "rules", "notes")
    malformed_records = 0
    occurrences = {field: 0 for field in carrier_fields}
    for record in records:
        if not isinstance(record, dict) or any(
            not isinstance(record.get(field), str) for field in carrier_fields
        ):
            malformed_records += 1
            continue
        for field in carrier_fields:
            occurrences[field] += record[field].count(token)

    errors: list[str] = []
    if malformed_records:
        errors.append(
            f"snapshot carrier shape invalid for {malformed_records} record(s)"
        )
    if any(occurrences[field] == 0 for field in carrier_fields):
        errors.append("placeholder_token has no snapshot carrier occurrences")
        errors.append(
            "snapshot placeholder occurrence counts: "
            + ", ".join(
                f"{field}={occurrences[field]}" for field in carrier_fields
            )
        )
    return tuple(errors)


def godot_only_test_record_scope_errors(text: str) -> tuple[str, ...]:
    """Require the 15 source-less Godot records to be a bounded test-only exception."""
    paragraphs = [
        paragraph
        for paragraph in re.split(r"\n\s*\n", text)
        if re.search(r"\b15\b", paragraph)
        and re.search(r"godot|引擎", paragraph, re.IGNORECASE)
        and re.search(r"test|测试", paragraph, re.IGNORECASE)
    ]
    if not paragraphs:
        return ("missing paragraph for 15 Godot-only test records",)
    normalized = _normalized("\n".join(paragraphs))
    checks = {
        "records must be test-only": (
            r"test-only|仅限测试|测试专用",
            r"(?:不是|并非|not)\s*test-only|非测试专用",
        ),
        "records must be non-authoritative": (
            r"non-authoritative|非权威",
            r"(?:不是|并非|not)\s*(?:non-authoritative|非权威)",
        ),
        "records must not flow back": (
            r"不回流|禁止回流|no[- ]backflow",
            r"(?:允许|可以|可)\s*回流|allow(?:ed)?\s+(?:backflow|flow back)|"
            r"(?:不回流|禁止回流|no[- ]backflow).{0,16}(?:不适用|无需|not applicable)",
        ),
        "production migration gate is missing": (
            r"生产迁移|production migration",
            r"(?:生产迁移|production migration).{0,30}"
            r"(?:不必|无需|不需要|可跳过|not required|optional)",
        ),
        "design-record prerequisite is missing": (
            r"(?:建立|创建|补建).{0,30}设计库.{0,20}(?:record|记录)",
            r"(?:不必|无需|不需要|not required).{0,24}"
            r"(?:建立|创建|补建).{0,30}设计库.{0,20}(?:record|记录)",
        ),
        "explicit-exclude alternative is missing": (
            r"显式.{0,20}排除|explicit.{0,20}exclude",
            r"(?:不必|无需|不需要|not required).{0,24}"
            r"(?:显式.{0,20}排除|explicit.{0,20}exclude)",
        ),
    }
    return tuple(
        message
        for message, (required, forbidden) in checks.items()
        if not re.search(required, normalized) or re.search(forbidden, normalized)
    )


def parse_acceptance_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    duplicates: set[str] = set()

    def record(key: str, value: str) -> None:
        if key in fields:
            duplicates.add(key)
        else:
            fields[key] = value

    for table in parse_markdown_tables(text):
        for row in table.rows:
            if len(row) >= 2:
                key = _normalized(row[0]).replace(" ", "_")
                record(key, _plain(row[1]))
    for line in text.splitlines():
        match = re.match(
            r"^\s*[-*]?\s*`?([a-z][a-z0-9_]*)`?\s*:\s*(.*?)\s*$", line
        )
        if match:
            record(match.group(1).casefold(), _plain(match.group(2)))
    if duplicates:
        raise ValueError(
            "duplicate acceptance keys: " + ", ".join(sorted(duplicates))
        )
    return fields


def acceptance_contract_errors(
    text: str, expected_authority_test_count: int | None = None
) -> tuple[str, ...]:
    try:
        fields = parse_acceptance_fields(text)
    except ValueError as error:
        return (str(error),)
    evidence_fields = {
        "api_http_status", "api_record_count", "api_normalized_sha256",
        "snapshot_record_count", "snapshot_normalized_sha256",
        "normalized_difference_count", "parity_result",
        "design_commit_placeholder", "design_commit_final",
    }
    required = evidence_fields | set(NORMALIZATION_CONTRACT) | {"stable_fields"}
    errors = [f"missing field: {key}" for key in sorted(required - fields.keys())]
    if errors:
        return tuple(errors)

    for key, expected in NORMALIZATION_CONTRACT.items():
        if _normalized(fields[key]) != expected:
            errors.append(f"{key} must equal {expected}")
    stable_fields = [
        token.casefold()
        for token in re.findall(r"[a-z][a-z0-9_]*", fields["stable_fields"], re.IGNORECASE)
    ]
    if len(stable_fields) != len(STABLE_FIELDS) or set(stable_fields) != STABLE_FIELDS:
        errors.append("stable_fields must be the exact eight-field set")

    expected_values = {
        "api_http_status": "200",
        "api_record_count": "23",
        "snapshot_record_count": "23",
        "normalized_difference_count": "0",
        "parity_result": "pass",
    }
    for key, expected in expected_values.items():
        if _normalized(fields[key]) != expected:
            errors.append(f"{key} must equal {expected}")
    for key in ("api_normalized_sha256", "snapshot_normalized_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", fields[key], re.IGNORECASE):
            errors.append(f"{key} must be SHA-256 hex")
    if (
        fields["api_normalized_sha256"].casefold()
        != fields["snapshot_normalized_sha256"].casefold()
    ):
        errors.append("API and snapshot normalized SHA-256 values must be identical")
    for key in ("design_commit_placeholder", "design_commit_final"):
        if not re.fullmatch(
            r"(?:pending|pending-until-commit|[0-9a-f]{40})",
            fields[key],
            re.IGNORECASE,
        ):
            errors.append(f"{key} must be pending or a commit SHA")

    if expected_authority_test_count is not None:
        split_keys = {
            "authority_test_total", "authority_test_passed", "authority_test_status"
        }
        present_split = split_keys & fields.keys()
        if present_split:
            missing_split = split_keys - fields.keys()
            errors.extend(
                f"missing field: {key}" for key in sorted(missing_split)
            )
            if not missing_split:
                if fields["authority_test_total"] != str(expected_authority_test_count):
                    errors.append("authority_test_total must match discovered suite count")
                if fields["authority_test_passed"] != str(expected_authority_test_count):
                    errors.append("authority_test_passed must match discovered suite count")
                if _normalized(fields["authority_test_status"]) != "pass":
                    errors.append("authority_test_status must equal PASS")
        else:
            expected_result = (
                f"{expected_authority_test_count}/{expected_authority_test_count} PASS"
            )
            if fields.get("authority_test_result") != expected_result:
                errors.append(
                    f"authority_test_result must equal {expected_result}"
                )
    return tuple(errors)


def valid_acceptance_fixture(authority_test_count: int = 1) -> str:
    shared_hash = "a" * 64
    return "\n".join(
        (
            "schema: mercury.rules-parity.v1",
            "stable_fields: id, code, group, category, class_ids, text, status, version",
            "class_ids_order: unicode-code-point-ascending",
            "record_order: id-ascending",
            "json_key_order: sorted",
            "json_separators: comma-colon",
            "ensure_ascii: false",
            "encoding: utf-8",
            "trailing_newline: false",
            "hash_algorithm: sha-256",
            "difference_semantics: missing-id-or-stable-field-difference",
            "api_http_status: 200",
            "api_record_count: 23",
            f"api_normalized_sha256: {shared_hash}",
            "snapshot_record_count: 23",
            f"snapshot_normalized_sha256: {shared_hash}",
            "normalized_difference_count: 0",
            "parity_result: PASS",
            "design_commit_placeholder: pending-until-commit",
            "design_commit_final: pending-until-commit",
            f"authority_test_result: {authority_test_count}/{authority_test_count} PASS",
        )
    )


def discovered_test_count() -> int:
    return unittest.defaultTestLoader.loadTestsFromModule(
        sys.modules[__name__]
    ).countTestCases()


class ParserRegressionTests(unittest.TestCase):
    def test_owner_parser_rejects_negated_and_mixed_owners(self) -> None:
        for value in (
            "SoT main (Godot), not design-library",
            "design-library + SoT main",
            "不是设计库，而是 Godot",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                classify_owner(value)

    def test_prose_scanner_catches_out_of_table_engine_authority(self) -> None:
        synthetic = "power / cd / range authority is SoT main (Godot)."
        violations = forbidden_prose_authority_claims(synthetic)
        self.assertEqual(len(violations), 1)
        self.assertIn("power,cd,range", violations[0])

    def test_acceptance_rejects_well_formed_but_unequal_hashes(self) -> None:
        valid = valid_acceptance_fixture(authority_test_count=1)
        self.assertEqual(acceptance_contract_errors(valid, 1), ())
        unequal = valid.replace(
            "api_normalized_sha256: " + "a" * 64,
            "api_normalized_sha256: " + "b" * 64,
        )
        self.assertIn(
            "API and snapshot normalized SHA-256 values must be identical",
            acceptance_contract_errors(unequal, 1),
        )

    def test_complete_but_inverted_carrier_semantics_are_rejected(self) -> None:
        design = (
            "source_attributes: effect, rules, notes; "
            "source_carrier: (Skill.effect, Skill.rules, Skill.notes) decision text; "
            "scalar_status: not blocked/not unverifiable because normalized field exists; "
            "parse_semantics: mechanical scalar supported; "
            "empty_semantics: resolved/verifiable/overwrite; "
            "placeholder_token: [占位]; "
            "placeholder_semantics: resolved/verifiable/overwrite; "
            "concept_only_semantics: scalar resolved/overwrite"
        )
        mirror = (
            "consumer_key: power; mirror_scope: design-record-present; "
            "mirror_status: verified mirror"
        )
        errors = carrier_contract_errors("power", design, mirror)
        self.assertIn(
            "power: scalar_status must equal blocked/unverifiable until normalized field",
            errors,
        )
        self.assertIn("power: parse_semantics must equal no mechanical scalar", errors)
        self.assertIn(
            "power: mirror_status must equal blocked/unverifiable until normalized field",
            errors,
        )

    def test_fake_placeholder_token_is_rejected(self) -> None:
        valid_design = (
            "source_attributes: effect, rules, notes; "
            "source_carrier: (Skill.effect, Skill.rules, Skill.notes) decision text; "
            "scalar_status: blocked/unverifiable until normalized field; "
            "parse_semantics: no mechanical scalar; "
            "empty_semantics: unresolved/unverifiable/no overwrite; "
            "placeholder_token: [占位]; "
            "placeholder_semantics: unresolved/unverifiable/no overwrite; "
            "concept_only_semantics: non-scalar unresolved/no overwrite"
        )
        valid_mirror = (
            "consumer_key: power; mirror_scope: design-record-present; "
            "mirror_status: blocked/unverifiable until normalized field"
        )
        self.assertEqual(
            carrier_contract_errors("power", valid_design, valid_mirror), ()
        )
        fake = valid_design.replace("[占位]", "[not-a-real-source-token]")
        self.assertIn(
            "power: placeholder_token must be literal [占位]",
            carrier_contract_errors("power", fake, valid_mirror),
        )
        valid_range_design = (
            "source_attributes: range_min, range_max, range_shape; "
            "source_carrier: (Skill.range_min, Skill.range_max, Skill.range_shape)"
        )
        valid_range_mirror = (
            "consumer_key: range.type; mirror_scope: design-record-present"
        )
        self.assertEqual(
            carrier_contract_errors(
                "range", valid_range_design, valid_range_mirror
            ),
            (),
        )
        fake_range_carrier = valid_range_design.replace(
            "(Skill.range_min, Skill.range_max, Skill.range_shape)",
            "(Skill.effect, Skill.rules, Skill.notes)",
        )
        self.assertIn(
            "range: source_carrier must use only "
            "Skill.range_min/Skill.range_max/Skill.range_shape",
            carrier_contract_errors(
                "range", fake_range_carrier, valid_range_mirror
            ),
        )
        snapshot_records = [
            {"effect": "[占位]", "rules": "[占位]", "notes": "[占位]"}
        ]
        self.assertEqual(
            snapshot_placeholder_contract_errors(valid_design, snapshot_records),
            (),
        )
        fake_snapshot_token = valid_design.replace(
            "placeholder_token: [占位]", "placeholder_token: [fake-token]"
        )
        self.assertIn(
            "placeholder_token has no snapshot carrier occurrences",
            snapshot_placeholder_contract_errors(
                fake_snapshot_token, snapshot_records
            ),
        )
        missing_snapshot_token = re.sub(
            r"placeholder_token:[^;]+;\s*", "", valid_design
        )
        self.assertIn(
            "missing placeholder_token for snapshot binding",
            snapshot_placeholder_contract_errors(
                missing_snapshot_token, snapshot_records
            ),
        )

    def test_acceptance_rejects_stale_authority_test_result(self) -> None:
        valid = valid_acceptance_fixture(authority_test_count=1)
        stale = valid.replace("authority_test_result: 1/1 PASS", "authority_test_result: 0/0 PASS")
        self.assertIn(
            "authority_test_result must equal 1/1 PASS",
            acceptance_contract_errors(stale, 1),
        )
        conflicting_duplicates = (
            valid
            + "\nparity_result: FAIL"
            + "\nauthority_test_result: 0/0 PASS"
        )
        self.assertIn(
            "duplicate acceptance keys: authority_test_result, parity_result",
            acceptance_contract_errors(conflicting_duplicates, 1),
        )

    def test_ambiguous_free_text_carrier_is_not_executable(self) -> None:
        errors = carrier_contract_errors(
            "power",
            "由设计规则文本承载；未新增物理字段或列",
            "Godot 消费侧只读镜像",
        )
        self.assertIn("power: missing source_attributes", errors)
        self.assertIn("power: consumer_key must equal power", errors)
        self.assertIn("power: missing scalar_status", errors)
        self.assertIn("power: missing placeholder_semantics", errors)

    def test_source_less_godot_records_need_a_bounded_exception(self) -> None:
        ambiguous = (
            "引擎侧 20 条技能只有 5 条有设计库对应，其余 15 条是引擎自有测试单位技能。"
        )
        errors = godot_only_test_record_scope_errors(ambiguous)
        self.assertIn("records must be test-only", errors)
        self.assertIn("records must be non-authoritative", errors)
        self.assertIn("production migration gate is missing", errors)
        inverted = (
            "15 条 Godot test-only 记录不是 non-authoritative，允许回流，"
            "no backflow 不适用；生产迁移不必创建设计库 canonical record，"
            "也不必 explicit exclude。"
        )
        inverted_errors = godot_only_test_record_scope_errors(inverted)
        for expected in (
            "records must be non-authoritative",
            "records must not flow back",
            "production migration gate is missing",
            "design-record prerequisite is missing",
            "explicit-exclude alternative is missing",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, inverted_errors)


class LaneAuthorityContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.canonical_text = CANONICAL_DOCUMENT.read_text(encoding="utf-8")
        cls.tables = parse_markdown_tables(cls.canonical_text)

    def assert_scope_owner(self, pattern: str, expected: str) -> None:
        claims = owner_claims_for_scope(self.tables, pattern)
        self.assertTrue(claims, f"no canonical table row matched scope pattern: {pattern}")
        self.assertEqual(
            {owner for owner, _ in claims},
            {expected},
            "conflicting scope owners:\n" + "\n".join(row for _, row in claims),
        )

    def test_power_cd_and_range_have_only_design_library_authority(self) -> None:
        for field in PROTECTED_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(
                    field_owner_claims(self.tables, field),
                    {"design-library"},
                    f"{field} must have one authority and no conflicting owner claims",
                )

    def test_machine_registry_separates_authority_mirror_and_engine_representation(self) -> None:
        registry = find_authority_registry(self.tables)
        self.assertIsNotNone(
            registry,
            "missing field/design-authority/Godot-mirror/engine-representation registry",
        )
        assert registry is not None
        rows = registry_rows(registry)
        self.assertEqual(set(rows), set(PROTECTED_FIELDS))
        for header in registry.table.headers:
            self.assertNotRegex(
                _normalized(header),
                r"设计库无对应列|no design-library column",
                "registry must not use a design-absent bucket for protected fields",
            )
        for field, row in rows.items():
            design_ids = _code_identifiers(row[registry.design_column])
            engine_ids = _code_identifiers(row[registry.engine_column])
            self.assertEqual(
                design_ids & engine_ids,
                set(),
                f"{field} repeats an identifier in authority and engine-only columns",
            )
            self.assertTrue(_plain(row[registry.mirror_column]))
        self.assertFalse(
            FIELD_PATTERNS["power"].search(rows["power"][registry.engine_column])
        )
        self.assertFalse(FIELD_PATTERNS["cd"].search(rows["cd"][registry.engine_column]))

    def test_design_carriers_and_range_type_role_are_explicit(self) -> None:
        registry = find_authority_registry(self.tables)
        self.assertIsNotNone(registry, "authority registry is required before carrier checks")
        assert registry is not None
        self.assertTrue(
            SKILLS_SNAPSHOT.is_file(),
            f"committed skills snapshot is missing: {SKILLS_SNAPSHOT}",
        )
        try:
            snapshot_records = json.loads(SKILLS_SNAPSHOT.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            self.fail(
                "committed skills snapshot is invalid JSON at "
                f"line {error.lineno}, column {error.colno}"
            )
        if not isinstance(snapshot_records, list):
            self.fail("committed skills snapshot root shape must be an array")
        rows = registry_rows(registry)
        self.assertEqual(set(rows), set(PROTECTED_FIELDS))
        for field in PROTECTED_FIELDS:
            errors = carrier_contract_errors(
                field,
                rows[field][registry.design_column],
                rows[field][registry.mirror_column],
            )
            with self.subTest(field=field):
                self.assertEqual(
                    errors,
                    (),
                    f"{field} carrier contract errors:\n" + "\n".join(errors),
                )
                if field in {"power", "cd"}:
                    snapshot_errors = snapshot_placeholder_contract_errors(
                        rows[field][registry.design_column], snapshot_records
                    )
                    self.assertEqual(
                        snapshot_errors,
                        (),
                        "snapshot placeholder binding errors:\n"
                        + "\n".join(snapshot_errors),
                    )

        mirror = _normalized(rows["range"][registry.mirror_column])
        engine = _normalized(rows["range"][registry.engine_column])
        locations = [
            name
            for name, value in (("mirror", mirror), ("engine", engine))
            if "range.type" in value
        ]
        self.assertEqual(
            len(locations), 1, "range.type must appear in exactly one representation column"
        )
        if locations == ["mirror"]:
            self.assertRegex(mirror, r"mirror|镜像|consumer|消费|只读")
        elif locations == ["engine"]:
            self.assertRegex(engine, r"taxonomy|representation|表示|分类")
            self.assertRegex(
                engine,
                r"non-authoritative|非权威|不(?:改变|构成).{0,30}(?:design-library|设计库).{0,15}权威",
            )

    def test_godot_only_test_records_are_bounded_non_authoritative_exceptions(self) -> None:
        errors = godot_only_test_record_scope_errors(self.canonical_text)
        self.assertEqual(
            errors,
            (),
            "Godot-only test record scope errors:\n" + "\n".join(errors),
        )

    def test_no_prose_assigns_protected_fields_to_engine_authority(self) -> None:
        self.assertEqual(
            forbidden_prose_authority_claims(self.canonical_text),
            (),
            "protected-field prose contains engine/SoT/Godot authority conflicts",
        )

    def test_directory_single_writer_matrix_is_canonical(self) -> None:
        self.assert_scope_owner(r"设计库.*app.*tests.*scripts", "mercury")
        self.assert_scope_owner(r"设计库.*生产.*活数据", "user+sot")
        self.assert_scope_owner(r"(?:godot.*全域|ship_of_theseus.*全域)", "sot")
        self.assert_scope_owner(r"(?:^kb\b|shipoftheseus-kb)", "user+sot")
        self.assert_scope_owner(r"(?:跨.*lane.*文档|设计库.*docs.*协调)", "initiator")

    def test_snapshots_are_generator_only(self) -> None:
        claims = owner_claims_for_scope(self.tables, r"设计库.*snapshots")
        self.assertTrue(claims, "canonical snapshot row is missing")
        self.assertEqual({owner for owner, _ in claims}, {"generator-only"})
        self.assertTrue(
            any(
                re.search(r"generator|导出|export_snapshot", row, re.IGNORECASE)
                for _, row in claims
            ),
            "snapshot row must name generator/export provenance",
        )

    def test_kb_rules_catalog_is_a_non_authoritative_mirror(self) -> None:
        paragraphs = [
            paragraph
            for paragraph in re.split(r"\n\s*\n", self.canonical_text)
            if "rules-catalog" in paragraph.casefold()
        ]
        self.assertTrue(paragraphs, "canonical document must define KB rules-catalog")
        paragraph = "\n".join(paragraphs)
        self.assertRegex(paragraph, r"(?i)(?:非权威|non-authoritative)")
        self.assertRegex(paragraph, r"(?i)(?:mirror|镜像)")
        self.assertRegex(paragraph, r"冲突|conflict")
        self.assertRegex(paragraph, r"(?i)(?:design[- ]table|设计(?:库)?表)")

    def test_acceptance_records_reproducible_api_snapshot_parity(self) -> None:
        self.assertTrue(
            ACCEPTANCE_DOCUMENT.is_file(),
            f"acceptance document is missing: {ACCEPTANCE_DOCUMENT}",
        )
        errors = acceptance_contract_errors(
            ACCEPTANCE_DOCUMENT.read_text(encoding="utf-8"),
            discovered_test_count(),
        )
        self.assertEqual(errors, (), "acceptance contract errors:\n" + "\n".join(errors))


if __name__ == "__main__":
    unittest.main()
