from __future__ import annotations

import argparse
from collections import Counter
import ctypes
from ctypes import wintypes
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from typing import Callable, Iterable, Mapping, Sequence
import uuid


if __package__:
    _model = importlib.import_module(f"{__package__}.model")
else:
    repository_root = Path(__file__).absolute().parents[3]
    sys.path.insert(0, os.fspath(repository_root))
    _model = importlib.import_module("scripts.codex.import.model")

AssetRecord = _model.AssetRecord
InventoryManifest = _model.InventoryManifest
compute_asset_id = _model.compute_asset_id
canonical_identity_key = _model.canonical_identity_key


PRODUCTION_ROOTS = {
    "mercury": Path(r"D:\Mercury\Mercury"),
    "godot": Path(r"D:\ShipOfTheseus\Ship_of_Theseus"),
    "design": Path(r"D:\ShipOfTheseus\SoT-fyc-space"),
    "kb": Path(r"D:\ShipOfTheseus\ShipOfTheseus-KB"),
}
PRODUCTION_OUTPUT_ROOT = Path(r"D:\Codex-Migration-Backup\2026-08-15-mercury-sot")
PRODUCTION_OUTPUT_NAMES = {
    "contract": "inventory-contract.json",
    "manifest": "assets.jsonl",
    "metadata": "assets.metadata.json",
    "domain_decisions": "domain-decisions.json",
}
SECURE_ROOT_VERIFIER = Path(__file__).absolute().with_name("secure_backup_root.ps1")
WINDOWS_POWERSHELL = Path(
    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
)
PRODUCTION_EXPECTED_COUNTS = {
    "chat": 68,
    "memory": 378,
    "memory-auxiliary": 3,
    "memory-archive": 3,
}
FROZEN_START = "2026-07-15T15:00:00Z"
FROZEN_AS_OF = "2026-08-14T14:59:59.999999Z"
PRODUCTION_CHAT_PROJECTS = {
    "home": "C--Users-392fy--claude",
    "mercury": "D--Mercury-Mercury",
    "godot": "D--ShipOfTheseus-Ship-of-Theseus",
}
PRODUCTION_CHAT_GROUP_COUNTS = {"home": 45, "mercury": 14, "godot": 9}
WINDOWS_JUNCTION_REPARSE_TAG = 0xA0000003
MEMORY_LAYOUT = {
    "D--Mercury-Mercury": ("mercury", 295, 3),
    "D--ShipOfTheseus-Ship-of-Theseus": ("godot", 57, 0),
    "D--Mercury": ("legacy", 11, 0),
    "D--Mercury-AgentKB": ("legacy", 6, 0),
    "D--Mercury-Argus": ("legacy", 4, 0),
    "D--Mercury-Mercury-side-bug": ("legacy", 2, 0),
    "D--ShipOfTheseus-Ship_of_Theseus": ("legacy", 3, 0),
    "D--Mercury-stock-agent-candidates-TradingAgents": ("archive", 3, 0),
}
MAX_CHAT_LINE_BYTES = 1024 * 1024
MAX_CHAT_PREFIX_BYTES = 8 * 1024 * 1024
SNAPSHOT_RETRIES = 2
DOMAIN_PATTERNS = (
    ("mercury", re.compile(r"(?i)\bmercury\b")),
    ("ship-of-theseus", re.compile(r"(?i)\bship[ _-]?of[ _-]?theseus\b")),
    ("sot", re.compile(r"(?i)\bsot\b")),
    ("godot", re.compile(r"(?i)\bgodot\b")),
    ("obsidian", re.compile(r"(?i)\bobsidian\b")),
    ("kb", re.compile(r"(?i)\bkb\b")),
    ("codex", re.compile(r"(?i)\bcodex\b")),
    ("mcp", re.compile(r"(?i)\bmcp\b")),
    ("talent", re.compile(r"(?i)\btalent\b")),
)
SECRET_FILENAMES = frozenset(
    {
        ".credentials.json",
        ".env",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "secrets.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)
SECRET_SUFFIXES = frozenset({".jks", ".key", ".p12", ".pfx", ".pem"})
SECRET_DIRECTORIES = frozenset({"credential", "credentials", "secret", "secrets"})
DECISION_TEXT_RE = re.compile(r"^[A-Za-z0-9 ._:/#()@+-]{1,240}$")


class InventoryError(ValueError):
    pass


class ContractError(InventoryError):
    pass


class ReparsePointError(InventoryError):
    pass


class SourceChangedError(InventoryError):
    pass


class ManifestError(InventoryError):
    pass


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    sha256: str
    size: int
    mtime_ns: int
    file_id: str
    captured: bytes


@dataclass(frozen=True, slots=True)
class DomainDecision:
    disposition: str
    reason: str
    evidence: str
    subject_sha256: str


@dataclass(slots=True)
class DecisionSet:
    values: dict[str, DomainDecision]
    sha256: str | None = None
    used: set[str] | None = None

    def __post_init__(self) -> None:
        if self.used is None:
            self.used = set()

    def find(self, subject_sha256: str, *keys: str) -> DomainDecision | None:
        for key in keys:
            if key in self.values:
                self.used.add(key)
                decision = self.values[key]
                return decision if decision.subject_sha256 == subject_sha256 else None
        return None

    def assert_all_used(self) -> None:
        unused = sorted(set(self.values) - self.used)
        if unused:
            raise ContractError(f"unused domain decisions: {', '.join(unused)}")


@dataclass(frozen=True, slots=True)
class InventoryContract:
    claude_home: Path
    mercury_root: Path
    godot_root: Path
    design_root: Path
    kb_root: Path
    cutoff: object
    domain_decisions: Path | None = None
    expected_counts: Mapping[str, int] | None = None
    production: bool = False
    tool_commit: str | None = None
    tool_sha256: str | None = None
    as_of: object | None = None
    chat_projects: Mapping[str, str] | None = None
    chat_sessions: Mapping[str, Sequence[str]] | None = None
    require_stable_corpus: bool = False
    frozen_contract_sha256: str | None = None
    frozen_tool: Mapping[str, object] | None = None
    agents_home: Path | None = None
    skill_junction_mirrors: Sequence[Mapping[str, object]] | None = None

    def roots(self) -> dict[str, Path]:
        return {
            "mercury": self.mercury_root,
            "godot": self.godot_root,
            "design": self.design_root,
            "kb": self.kb_root,
        }


def _is_reparse(metadata: os.stat_result) -> bool:
    attributes = getattr(metadata, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(metadata.st_mode) or bool(attributes & reparse_flag)


def _absolute_without_resolve(path: os.PathLike[str] | str, *, label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ContractError(f"{label} must be absolute: {candidate}")
    return Path(os.path.abspath(os.fspath(candidate)))


def _relative_components(root: Path, target: Path) -> tuple[str, ...]:
    try:
        common = os.path.commonpath((os.fspath(root), os.fspath(target)))
    except ValueError as error:
        raise ContractError(f"path is outside approved root: {target}") from error
    if os.path.normcase(common) != os.path.normcase(os.fspath(root)):
        raise ContractError(f"path is outside approved root: {target}")
    relative = os.path.relpath(os.fspath(target), os.fspath(root))
    return () if relative == "." else tuple(Path(relative).parts)


def _assert_not_reparse(path: Path, *, approved_root: Path | None = None) -> None:
    path = _absolute_without_resolve(path, label="path")
    if approved_root is None:
        anchor = Path(path.anchor)
        components = path.parts[1:]
    else:
        anchor = _absolute_without_resolve(approved_root, label="approved root")
        components = _relative_components(anchor, path)
        root_metadata = os.lstat(anchor)
        if _is_reparse(root_metadata):
            raise ReparsePointError(f"reparse point rejected: {anchor}")
    current = anchor
    for component in components:
        current = current / component
        metadata = os.lstat(current)
        if _is_reparse(metadata):
            raise ReparsePointError(f"reparse point rejected: {current}")


def _require_root(
    path: os.PathLike[str] | str,
    *,
    label: str,
    directory: bool = True,
) -> Path:
    candidate = _absolute_without_resolve(path, label=label)
    try:
        _assert_not_reparse(candidate)
        metadata = os.lstat(candidate)
    except FileNotFoundError as error:
        raise ContractError(f"mandatory root is missing: {label}={candidate}") from error
    if directory and not stat.S_ISDIR(metadata.st_mode):
        raise ContractError(f"mandatory root is not a directory: {label}={candidate}")
    return candidate


def _secure_files(root: Path) -> list[Path]:
    root = _require_root(root, label="source root")
    files: list[Path] = []

    def visit(directory: Path) -> None:
        with os.scandir(directory) as entries:
            for entry in sorted(entries, key=lambda item: item.name.casefold()):
                metadata = entry.stat(follow_symlinks=False)
                path = directory / entry.name
                if _is_reparse(metadata):
                    raise ReparsePointError(f"reparse point rejected: {path}")
                if stat.S_ISDIR(metadata.st_mode):
                    visit(path)
                elif stat.S_ISREG(metadata.st_mode):
                    files.append(path)

    visit(root)
    return files


def _canonical_junction_target(raw_target: str) -> Path:
    if not raw_target or "\n" in raw_target:
        raise ReparsePointError("skill junction target is invalid")
    normalized = raw_target
    for prefix in ("\\\\?\\", "\\??\\"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    if normalized.upper().startswith("UNC\\"):
        raise ReparsePointError("skill junction target must be a local absolute path")
    lexical_parts = normalized.replace("/", "\\").split("\\")
    if any(part in {"", ".", ".."} for part in lexical_parts[1:]):
        raise ReparsePointError("skill junction target must be lexically canonical")
    target = Path(normalized)
    if not target.is_absolute() or not target.drive:
        raise ReparsePointError("skill junction target must be a local absolute path")
    return _absolute_without_resolve(target, label="skill junction target")


def _skill_target_member_descriptor(
    skill_name: str, target: Path, path: Path
) -> dict[str, object]:
    snapshot = _snapshot_file(path, approved_root=target)
    relative = Path(os.path.relpath(path, target)).as_posix()
    return {
        "record_role": "relation-target-member",
        "source": path.as_posix(),
        "source_namespace": "claude-user-skill-target",
        "canonical_key": PurePosixPath(skill_name, relative).as_posix(),
        "size": snapshot.size,
        "sha256": snapshot.sha256,
        "mtime_ns": snapshot.mtime_ns,
        "file_id": snapshot.file_id,
    }


def _discover_skill_junction_mirrors(
    claude_home: os.PathLike[str] | str,
    agents_home: os.PathLike[str] | str | None = None,
) -> list[dict[str, object]]:
    home = _require_root(claude_home, label="Claude home")
    skills_root = home / "skills"
    if not os.path.lexists(skills_root):
        return []
    skills_root = _require_root(skills_root, label="Claude user skills root")
    junctions: list[Path] = []
    with os.scandir(skills_root) as entries:
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            metadata = entry.stat(follow_symlinks=False)
            if _is_reparse(metadata):
                junctions.append(skills_root / entry.name)
    if not junctions and agents_home is None:
        return []

    expected_agents = _absolute_without_resolve(
        home.parent / ".agents", label="expected agents home"
    )
    agents = _require_root(
        agents_home if agents_home is not None else expected_agents,
        label="agents home",
    )
    if not _same_absolute_path(agents, expected_agents):
        raise ReparsePointError("skill junction agents home is not the same user root")
    target_root = _require_root(agents / "skills", label="agents skills root")

    relations: list[dict[str, object]] = []
    for link in junctions:
        link_before = os.lstat(link)
        reparse_tag = int(getattr(link_before, "st_reparse_tag", 0))
        if reparse_tag != WINDOWS_JUNCTION_REPARSE_TAG:
            raise ReparsePointError(f"non-junction user skill reparse rejected: {link}")
        try:
            raw_target = os.readlink(link)
        except OSError as error:
            raise ReparsePointError(f"skill junction target cannot be read: {link}") from error
        canonical_target = _canonical_junction_target(raw_target)
        expected_target = target_root / link.name
        if (
            canonical_target.as_posix() != expected_target.as_posix()
            or canonical_target.name != link.name
        ):
            raise ReparsePointError(
                f"skill junction target is not the same-name agents skill: {link}"
            )
        with os.scandir(target_root) as target_entries:
            stored_name_matches = [
                entry.name for entry in target_entries if entry.name == link.name
            ]
        if stored_name_matches != [link.name]:
            raise ReparsePointError(
                f"skill junction target storage name is not an exact match: {link}"
            )
        target = _require_root(expected_target, label="skill junction canonical target")
        _assert_not_reparse(target, approved_root=target_root)
        target_before = os.lstat(target)
        members = sorted(
            (
                _skill_target_member_descriptor(link.name, target, path)
                for path in _secure_files(target)
            ),
            key=lambda item: canonical_identity_key(str(item["canonical_key"])),
        )
        target_after = os.lstat(target)
        link_after = os.lstat(link)
        try:
            raw_target_after = os.readlink(link)
        except OSError as error:
            raise SourceChangedError(f"skill junction changed during snapshot: {link}") from error
        stable = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            int(getattr(value, "st_reparse_tag", 0)),
        )
        if (
            stable(link_before) != stable(link_after)
            or raw_target_after != raw_target
            or stable(target_before) != stable(target_after)
        ):
            raise SourceChangedError(f"skill junction changed during snapshot: {link}")
        relations.append(
            {
                "relation_type": "claude-user-skill-junction",
                "link_path": link.as_posix(),
                "link_identity": {
                    "file_id": _file_id(link_before),
                    "size": link_before.st_size,
                    "mtime_ns": link_before.st_mtime_ns,
                    "reparse_tag": reparse_tag,
                },
                "raw_target": raw_target,
                "canonical_target": target.as_posix(),
                "target_identity": {
                    "file_id": _file_id(target_before),
                    "size": target_before.st_size,
                    "mtime_ns": target_before.st_mtime_ns,
                },
                "target_member_count": len(members),
                "target_members": members,
                "target_members_sha256": hashlib.sha256(
                    _canonical_json(members).encode("utf-8")
                ).hexdigest(),
            }
        )
    return sorted(relations, key=lambda item: str(item["link_path"]).casefold())


def _skill_junction_mirrors_sha256(
    relations: Sequence[Mapping[str, object]],
) -> str:
    return hashlib.sha256(_canonical_json(list(relations)).encode("utf-8")).hexdigest()


def _assert_skill_junction_mirrors(
    claude_home: Path,
    agents_home: Path,
    expected: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    observed = _discover_skill_junction_mirrors(claude_home, agents_home)
    if observed != list(expected):
        raise SourceChangedError("Claude user skill junction target member relations changed")
    return observed


def _secure_user_skill_files(
    root: Path, relations: Sequence[Mapping[str, object]]
) -> list[Path]:
    approved = _require_root(root, label="Claude user skills root")
    allowed = {
        str(item["link_path"]).casefold(): item
        for item in relations
    }
    seen: set[str] = set()
    files: list[Path] = []
    with os.scandir(approved) as entries:
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            metadata = entry.stat(follow_symlinks=False)
            path = approved / entry.name
            key = path.as_posix().casefold()
            if _is_reparse(metadata):
                relation = allowed.get(key)
                if relation is None:
                    raise ReparsePointError(f"reparse point rejected: {path}")
                metadata = os.lstat(path)
                if (
                    _file_id(metadata) != relation["link_identity"]["file_id"]
                    or int(getattr(metadata, "st_reparse_tag", 0))
                    != relation["link_identity"]["reparse_tag"]
                    or os.readlink(path) != relation["raw_target"]
                ):
                    raise SourceChangedError(f"skill junction changed during inventory: {path}")
                seen.add(key)
                continue
            if key in allowed:
                raise SourceChangedError(f"skill junction disappeared during inventory: {path}")
            if stat.S_ISDIR(metadata.st_mode):
                files.extend(_secure_files(path))
            elif stat.S_ISREG(metadata.st_mode):
                files.append(path)
    if seen != set(allowed):
        raise SourceChangedError("frozen skill junction membership changed")
    return files


def _skill_junction_target_records(
    relations: Sequence[Mapping[str, object]], decisions: DecisionSet
) -> list[AssetRecord]:
    records: list[AssetRecord] = []
    for relation in relations:
        link = Path(str(relation["link_path"]))
        for member in relation["target_members"]:
            source = Path(str(member["source"]))
            canonical_key = str(member["canonical_key"])
            snapshot = FileSnapshot(
                sha256=str(member["sha256"]),
                size=int(member["size"]),
                mtime_ns=int(member["mtime_ns"]),
                file_id=str(member["file_id"]),
                captured=b"",
            )
            domain_match = _domain_match(canonical_key)
            record = _make_record(
                source,
                snapshot,
                source_namespace="claude-user-skill-target",
                canonical_key=canonical_key,
                kind="skill",
                domain_evidence=(
                    f"path-domain:{domain_match}" if domain_match else None
                ),
                decisions=decisions,
            )
            relative = PurePosixPath(canonical_key).parts[1:]
            updates: dict[str, object] = {
                "observed_mirrors": ((link / Path(*relative)).as_posix(),)
            }
            if record.disposition != "exclude-secret":
                updates.update(
                    domain_reason="already-native-alias/no-import",
                    disposition="exclude-domain",
                    disposition_status="domain-decided",
                    decision_evidence="junction-relation:direct-same-name",
                )
            record = replace(record, **updates)
            records.append(record)
    return sorted(records, key=_record_sort_key)


def _membership_fingerprint(root: Path, paths: Iterable[Path]) -> tuple[tuple[object, ...], ...]:
    approved = _require_root(root, label="membership root")
    members: list[tuple[object, ...]] = []
    for path in sorted(paths, key=lambda item: item.as_posix().casefold()):
        _assert_not_reparse(path, approved_root=approved)
        snapshot = _snapshot_file(path, approved_root=approved)
        relative = Path(os.path.relpath(path, approved)).as_posix()
        members.append(
            (
                canonical_identity_key(relative),
                snapshot.file_id,
                snapshot.size,
                snapshot.mtime_ns,
                False,
                snapshot.sha256,
            )
        )
    return tuple(members)


def _file_id(metadata: os.stat_result) -> str:
    return f"{metadata.st_dev:x}:{metadata.st_ino:x}"


def _snapshot_file(
    path: Path,
    *,
    approved_root: Path | None = None,
    retries: int = SNAPSHOT_RETRIES,
    capture_limit: int = 0,
    after_read: Callable[[Path, int], None] | None = None,
) -> FileSnapshot:
    path = _absolute_without_resolve(path, label="source path")
    if retries < 0:
        raise ValueError("retries must not be negative")
    for attempt in range(retries + 1):
        _assert_not_reparse(path, approved_root=approved_root)
        digest = hashlib.sha256()
        captured = bytearray()
        byte_count = 0
        with path.open("rb", buffering=0) as stream:
            before = os.fstat(stream.fileno())
            if _is_reparse(before) or not stat.S_ISREG(before.st_mode):
                raise ReparsePointError(f"non-regular or reparse source rejected: {path}")
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                byte_count += len(block)
                if capture_limit == -1:
                    captured.extend(block)
                elif capture_limit > 0 and len(captured) < capture_limit:
                    captured.extend(block[: capture_limit - len(captured)])
            if after_read is not None:
                after_read(path, attempt)
            after = os.fstat(stream.fileno())
        current = os.lstat(path)
        stable_fields = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
        )
        if (
            stable_fields(before) == stable_fields(after) == stable_fields(current)
            and byte_count == before.st_size
            and not _is_reparse(current)
        ):
            return FileSnapshot(
                sha256=digest.hexdigest(),
                size=byte_count,
                mtime_ns=before.st_mtime_ns,
                file_id=_file_id(before),
                captured=bytes(captured),
            )
    raise SourceChangedError(f"source changed during snapshot: {path}")


def _normalize_cutoff(cutoff: object) -> tuple[int, str]:
    if cutoff is None or isinstance(cutoff, bool):
        raise ContractError("an explicit cutoff timestamp is required")
    if isinstance(cutoff, (int, float)):
        value = datetime.fromtimestamp(float(cutoff), timezone.utc)
    elif isinstance(cutoff, datetime):
        value = cutoff
    elif isinstance(cutoff, str):
        normalized = cutoff.strip()
        if normalized.endswith(("Z", "z")):
            normalized = normalized[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(normalized)
        except ValueError as error:
            raise ContractError(f"invalid cutoff timestamp: {cutoff!r}") from error
    else:
        raise ContractError(f"unsupported cutoff timestamp: {cutoff!r}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return int(value.timestamp() * 1_000_000_000), value.isoformat()


def _is_secret_path(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()
    return (
        name in SECRET_FILENAMES
        or name.startswith(".env.")
        or path.suffix.casefold() in SECRET_SUFFIXES
        or any(part in SECRET_DIRECTORIES for part in parts[:-1])
    )


def _kind_for_path(path: Path) -> str:
    parts = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()
    suffix = path.suffix.casefold()
    if suffix == ".jsonl":
        return "chat"
    if "attachments" in parts:
        return "attachment"
    if "backups" in parts or name.endswith((".backup", ".bak")):
        return "backup"
    if "memory" in parts or "memories" in parts:
        return "memory" if suffix in {".md", ".markdown"} else "memory-auxiliary"
    if "skills" in parts:
        return "skill"
    if "agents" in parts:
        return "agent"
    if "commands" in parts:
        return "command"
    if "hooks" in parts:
        return "hook"
    if "workflows" in parts:
        return "workflow"
    if name in {"settings.json", "settings.local.json", ".mcp.json", "config.toml"}:
        return "setting"
    if name in {"claude.md", "agents.md"}:
        return "instruction"
    return "file"


def _domain_match(text: str) -> str | None:
    for label, pattern in DOMAIN_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _safe_decision_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not DECISION_TEXT_RE.fullmatch(value):
        raise ContractError(f"domain decision {field} is not a safe non-secret label")
    return value


def _safe_decision_hash(value: object, *, key: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ContractError(f"domain decision subject_sha256 is invalid for {key}")
    return value


def _load_decisions(path: Path | None) -> DecisionSet:
    if path is None:
        return DecisionSet({})
    decision_path = _require_root(path, label="domain decision file", directory=False)
    snapshot = _snapshot_file(decision_path, capture_limit=-1)
    try:
        document = json.loads(snapshot.captured.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("domain decision file is not valid UTF-8 JSON") from error
    values = document.get("decisions") if isinstance(document, dict) else None
    if not isinstance(values, dict):
        raise ContractError("domain decision file must contain a decisions object")
    decisions: dict[str, DomainDecision] = {}
    for key, raw in values.items():
        if not isinstance(key, str) or not key or not isinstance(raw, dict):
            raise ContractError("invalid domain decision entry")
        disposition = raw.get("disposition")
        if disposition not in _model.VALID_DISPOSITIONS:
            raise ContractError(f"invalid domain decision disposition for {key}")
        decisions[key] = DomainDecision(
            disposition=disposition,
            reason=_safe_decision_text(raw.get("reason"), field="reason"),
            evidence=_safe_decision_text(raw.get("evidence"), field="evidence"),
            subject_sha256=_safe_decision_hash(raw.get("subject_sha256"), key=key),
        )
    return DecisionSet(decisions, sha256=snapshot.sha256)


def _classification(
    *,
    path: Path,
    source_namespace: str,
    canonical_key: str,
    domain_evidence: str | None,
    decisions: DecisionSet,
    decision_aliases: Sequence[str] = (),
    forced_exclusion: tuple[str, str] | None = None,
    subject_sha256: str,
) -> tuple[str, str, str | None, str, str | None]:
    if _is_secret_path(path):
        return (
            "mercury-sot" if domain_evidence else "review-required",
            f"known-secret-container:{path.name.casefold()}",
            "exclude-secret",
            "provisional",
            None,
        )
    if forced_exclusion is not None:
        reason, evidence = forced_exclusion
        return "other", reason, "exclude-domain", "domain-decided", evidence
    if domain_evidence:
        return "mercury-sot", domain_evidence, "import", "provisional", None
    logical_key = f"{source_namespace}:{canonical_key}"
    decision = decisions.find(subject_sha256, *decision_aliases, logical_key)
    if decision is None:
        return "review-required", "no-domain-evidence", None, "unresolved", None
    domain = "other" if decision.disposition == "exclude-domain" else "mercury-sot"
    return (
        domain,
        decision.reason,
        decision.disposition,
        "domain-decided",
        decision.evidence,
    )


def _make_record(
    path: Path,
    snapshot: FileSnapshot,
    *,
    source_namespace: str,
    canonical_key: str,
    kind: str,
    domain_evidence: str | None,
    decisions: DecisionSet,
    decision_aliases: Sequence[str] = (),
    forced_exclusion: tuple[str, str] | None = None,
    dirty_state: str | None = None,
    session_id: str | None = None,
    source_cwd: str | None = None,
    first_user_request_sha256: str | None = None,
) -> AssetRecord:
    domain, reason, disposition, status, evidence = _classification(
        path=path,
        source_namespace=source_namespace,
        canonical_key=canonical_key,
        domain_evidence=domain_evidence,
        decisions=decisions,
        decision_aliases=decision_aliases,
        forced_exclusion=forced_exclusion,
        subject_sha256=first_user_request_sha256 or snapshot.sha256,
    )
    return AssetRecord(
        asset_id=compute_asset_id(source_namespace, canonical_key, snapshot.sha256),
        source=path.as_posix(),
        source_namespace=source_namespace,
        canonical_key=canonical_key,
        kind=kind,
        size=snapshot.size,
        sha256=snapshot.sha256,
        mtime_ns=snapshot.mtime_ns,
        file_id=snapshot.file_id,
        domain=domain,
        domain_reason=reason,
        disposition=disposition,
        disposition_status=status,
        decision_evidence=evidence,
        dirty_state=dirty_state,
        session_id=session_id,
        source_cwd=source_cwd,
        first_user_request_sha256=first_user_request_sha256,
    )


def _text_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    text: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            value = block.get("text")
            if isinstance(value, str):
                text.append(value)
    return "\n".join(text)


def _first_user_context(data: bytes) -> tuple[str, str, str, str | None]:
    consumed = 0
    offset = 0
    while offset < len(data):
        newline = data.find(b"\n", offset)
        if newline < 0:
            line = data[offset:]
            offset = len(data)
        else:
            line = data[offset : newline + 1]
            offset = newline + 1
        consumed += len(line)
        if len(line) > MAX_CHAT_LINE_BYTES or consumed > MAX_CHAT_PREFIX_BYTES:
            break
        try:
            event = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            break
        if not isinstance(event, dict):
            break
        message = event.get("message")
        message_role = message.get("role") if isinstance(message, dict) else None
        event_role = event.get("role")
        event_type = event.get("type")
        legacy_user = (
            "message" in event
            and event_role is None
            and event_type is None
            and message_role is None
        )
        if (
            event_type != "user"
            and event_role != "user"
            and message_role != "user"
            and not legacy_user
        ):
            continue
        text = _text_content(message.get("content") if isinstance(message, dict) else message)
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else "unknown"
        request_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cwd.replace("\\", "/"), text, request_hash, _domain_match(f"{cwd}\n{text}")
    empty_hash = hashlib.sha256(b"").hexdigest()
    return "unknown", "", empty_hash, None


def _cutoff_allows(snapshot: FileSnapshot, cutoff_ns: int | None) -> bool:
    return cutoff_ns is None or snapshot.mtime_ns >= cutoff_ns


def inventory_paths(
    paths: Iterable[os.PathLike[str] | str],
    cutoff: object | None,
    *,
    source_namespace: str = "explicit",
    approved_root: os.PathLike[str] | str | None = None,
    decisions: DecisionSet | None = None,
) -> list[AssetRecord]:
    supplied = list(paths)
    if not supplied:
        return []
    raw_paths = [_absolute_without_resolve(path, label="source path") for path in supplied]
    root = (
        _require_root(approved_root, label="approved root")
        if approved_root is not None
        else _require_root(
            os.path.commonpath(
                [
                    os.fspath(path if path.is_dir() else path.parent)
                    for path in raw_paths
                ]
            ),
            label="approved root",
        )
    )
    cutoff_ns = None if cutoff is None else _normalize_cutoff(cutoff)[0]
    decision_set = decisions or DecisionSet({})
    files: dict[str, Path] = {}
    for raw in raw_paths:
        _assert_not_reparse(raw, approved_root=root)
        if raw.is_dir():
            candidates = _secure_files(raw)
        elif raw.is_file():
            candidates = [raw]
        else:
            raise ContractError(f"source path is not a regular file or directory: {raw}")
        for candidate in candidates:
            _relative_components(root, candidate)
            files.setdefault(os.path.normcase(os.fspath(candidate)), candidate)
    records: list[AssetRecord] = []
    for path in files.values():
        kind = _kind_for_path(path)
        snapshot = _snapshot_file(
            path,
            approved_root=root,
            capture_limit=(MAX_CHAT_PREFIX_BYTES + 1 if kind == "chat" else 0),
        )
        if not _cutoff_allows(snapshot, cutoff_ns):
            continue
        key = Path(os.path.relpath(path, root)).as_posix()
        session_id = source_cwd = request_hash = None
        decision_aliases: tuple[str, ...] = ()
        if kind == "chat":
            cwd, _text, parsed_hash, match = _first_user_context(snapshot.captured)
            evidence = f"first-user-domain:{match}" if match else None
            try:
                session_id = str(uuid.UUID(path.stem))
            except ValueError:
                session_id = None
            if session_id is not None:
                source_cwd = cwd
                request_hash = parsed_hash
                decision_aliases = (session_id,)
        else:
            match = _domain_match(f"{root.as_posix()}\n{key}")
            evidence = f"path-domain:{match}" if match else None
        records.append(
            _make_record(
                path,
                snapshot,
                source_namespace=source_namespace,
                canonical_key=key,
                kind=kind,
                domain_evidence=evidence,
                decisions=decision_set,
                decision_aliases=decision_aliases,
                session_id=session_id,
                source_cwd=source_cwd,
                first_user_request_sha256=request_hash,
            )
        )
    return sorted(records, key=_record_sort_key)


def _project_direct_jsonl(projects_root: Path) -> list[Path]:
    files: list[Path] = []
    with os.scandir(projects_root) as projects:
        for project in sorted(projects, key=lambda entry: entry.name.casefold()):
            metadata = project.stat(follow_symlinks=False)
            project_path = projects_root / project.name
            if _is_reparse(metadata):
                raise ReparsePointError(f"reparse point rejected: {project_path}")
            if not stat.S_ISDIR(metadata.st_mode):
                continue
            with os.scandir(project_path) as children:
                for child in sorted(children, key=lambda entry: entry.name.casefold()):
                    child_metadata = child.stat(follow_symlinks=False)
                    child_path = project_path / child.name
                    if _is_reparse(child_metadata):
                        raise ReparsePointError(f"reparse point rejected: {child_path}")
                    if stat.S_ISREG(child_metadata.st_mode) and child.name.casefold().endswith(".jsonl"):
                        files.append(child_path)
    return files


def inventory_claude_chats(
    root: os.PathLike[str] | str,
    cutoff: object | None,
    *,
    decisions: DecisionSet | None = None,
    expected_count: int | None = None,
    as_of: object | None = None,
) -> list[AssetRecord]:
    projects_root = _require_root(root, label="Claude projects root")
    cutoff_ns = None if cutoff is None else _normalize_cutoff(cutoff)[0]
    as_of_ns = None if as_of is None else _normalize_cutoff(as_of)[0]
    decision_set = decisions or DecisionSet({})
    records: list[AssetRecord] = []
    for path in _project_direct_jsonl(projects_root):
        try:
            session_id = str(uuid.UUID(path.stem))
        except ValueError as error:
            raise ContractError(f"main chat filename is not a session UUID: {path.name}") from error
        snapshot = _snapshot_file(
            path,
            approved_root=projects_root,
            capture_limit=MAX_CHAT_PREFIX_BYTES + 1,
        )
        if not _cutoff_allows(snapshot, cutoff_ns) or (
            as_of_ns is not None and snapshot.mtime_ns > as_of_ns
        ):
            continue
        cwd, _text, request_hash, match = _first_user_context(snapshot.captured)
        evidence = f"first-user-domain:{match}" if match else None
        records.append(
            _make_record(
                path,
                snapshot,
                source_namespace="claude-chat",
                canonical_key=session_id,
                kind="chat",
                domain_evidence=evidence,
                decisions=decision_set,
                decision_aliases=(session_id,),
                session_id=session_id,
                source_cwd=cwd,
                first_user_request_sha256=request_hash,
            )
        )
    if expected_count is not None and len(records) != expected_count:
        raise ContractError(
            f"chat count mismatch: expected {expected_count}, observed {len(records)}"
        )
    return sorted(records, key=_record_sort_key)


def inventory_memories(
    root: os.PathLike[str] | str,
    cutoff: object | None,
) -> list[AssetRecord]:
    memory_root = _require_root(root, label="memory root")
    return inventory_paths(
        _secure_files(memory_root),
        cutoff,
        source_namespace="claude-memory",
        approved_root=memory_root,
    )


def _inventory_approved_memories(
    projects_root: Path,
    *,
    decisions: DecisionSet,
    expected_counts: Mapping[str, int],
) -> list[AssetRecord]:
    records: list[AssetRecord] = []
    for project_name, (group, expected_md, expected_aux) in MEMORY_LAYOUT.items():
        memory_root = _require_root(
            projects_root / project_name / "memory",
            label=f"approved memory root {project_name}",
        )
        files = _secure_files(memory_root)
        md_files = [path for path in files if path.suffix.casefold() in {".md", ".markdown"}]
        aux_files = [path for path in files if path not in md_files]
        if len(md_files) != expected_md or len(aux_files) != expected_aux:
            raise ContractError(
                f"memory count mismatch for {project_name}: expected {expected_md} md/{expected_aux} aux, "
                f"observed {len(md_files)} md/{len(aux_files)} aux"
            )
        for path in files:
            snapshot = _snapshot_file(path, approved_root=memory_root)
            relative = Path(os.path.relpath(path, memory_root)).as_posix()
            if group == "archive":
                namespace = "claude-memory-archive"
                key = f"tradingagents/{relative}"
                kind = "memory-archive"
                forced = (
                    "approved-unrelated-tradingagents-archive",
                    "design-3.2-tradingagents-exclusion",
                )
                evidence = None
            else:
                namespace = "claude-memory"
                key = f"{project_name}/{relative}"
                kind = "memory" if path in md_files else "memory-auxiliary"
                forced = None
                evidence = f"approved-memory-source:{group}"
            records.append(
                _make_record(
                    path,
                    snapshot,
                    source_namespace=namespace,
                    canonical_key=key,
                    kind=kind,
                    domain_evidence=evidence,
                    decisions=decisions,
                    forced_exclusion=forced,
                )
            )
    observed = Counter(record.kind for record in records)
    for kind in ("memory", "memory-auxiliary", "memory-archive"):
        if observed[kind] != expected_counts.get(kind):
            raise ContractError(
                f"{kind} count mismatch: expected {expected_counts.get(kind)}, observed {observed[kind]}"
            )
    return sorted(records, key=_record_sort_key)


def _category_paths(
    claude_home: Path,
    repo_roots: Mapping[str, Path],
    *,
    category: str,
    skill_junction_mirrors: Sequence[Mapping[str, object]] = (),
) -> list[tuple[Path, Path, str, str | None]]:
    values: list[tuple[Path, Path, str, str | None]] = []
    if category == "settings":
        for name in ("settings.json", "settings.local.json", ".mcp.json"):
            path = claude_home / name
            if path.is_file():
                values.append((path, claude_home, "claude-user-setting", "approved-source:user-setting"))
        user_mcp = claude_home.parent / ".claude.json"
        if user_mcp.is_file():
            values.append(
                (
                    user_mcp,
                    claude_home.parent,
                    "claude-user-setting",
                    "approved-source:user-setting",
                )
            )
        repo_relatives = (".claude/settings.json", ".claude/settings.local.json", ".mcp.json")
    elif category == "instructions":
        path = claude_home / "CLAUDE.md"
        if path.is_file():
            values.append((path, claude_home, "claude-user-instruction", "approved-source:user-instruction"))
        repo_relatives = ("CLAUDE.md", ".claude/CLAUDE.md")
    else:
        user_root = claude_home / category
        if user_root.is_dir():
            candidates = (
                _secure_user_skill_files(user_root, skill_junction_mirrors)
                if category == "skills"
                else _secure_files(user_root)
            )
            for path in candidates:
                evidence = None if category == "skills" else f"approved-source:user-{category}"
                values.append((path, user_root, f"claude-user-{category.rstrip('s')}", evidence))
        repo_relatives = (f".claude/{category}",)
    for root_name, repo_root in repo_roots.items():
        for relative in repo_relatives:
            path = repo_root / relative
            if path.is_dir():
                candidates = _secure_files(path)
                logical_root = repo_root
            elif path.is_file():
                _assert_not_reparse(path, approved_root=repo_root)
                candidates = [path]
                logical_root = repo_root
            else:
                continue
            for candidate in candidates:
                values.append(
                    (
                        candidate,
                        logical_root,
                        f"repo-{root_name}",
                        f"approved-repo-root:{root_name}",
                    )
                )
    return values


def _inventory_category(
    claude_home: os.PathLike[str] | str,
    repo_roots: Mapping[str, Path],
    *,
    category: str,
    kind: str,
    decisions: DecisionSet,
    skill_junction_mirrors: Sequence[Mapping[str, object]] = (),
) -> list[AssetRecord]:
    home = _require_root(claude_home, label="Claude home")
    records: list[AssetRecord] = []
    seen_repo_skill: dict[tuple[str, str], AssetRecord] = {}
    seen_repo_skill_index: dict[tuple[str, str], int] = {}
    values = _category_paths(
        home,
        repo_roots,
        category=category,
        skill_junction_mirrors=skill_junction_mirrors,
    )
    if category == "skills":
        for root_name, repo_root in repo_roots.items():
            agents_root = repo_root / ".agents" / "skills"
            if agents_root.is_dir():
                for path in _secure_files(agents_root):
                    values.append(
                        (
                            path,
                            repo_root,
                            f"repo-{root_name}",
                            f"approved-repo-root:{root_name}",
                        )
                    )
        values.sort(
            key=lambda value: (
                0 if "/.agents/skills/" in value[0].as_posix().casefold() else 1,
                value[0].as_posix().casefold(),
            )
        )
    for path, logical_root, namespace, evidence in values:
        _assert_not_reparse(path, approved_root=logical_root)
        key = Path(os.path.relpath(path, logical_root)).as_posix()
        snapshot = _snapshot_file(path, approved_root=logical_root)
        record_kind = kind
        forced = None
        mirror_of = None
        if category == "skills" and namespace.startswith("repo-"):
            relative = PurePosixPath(key)
            if relative.parts[:2] == (".claude", "skills"):
                agents_key = PurePosixPath(".agents", "skills", *relative.parts[2:]).as_posix()
                logical = (namespace, canonical_identity_key(agents_key))
                canonical = seen_repo_skill.get(logical)
                if canonical is not None and canonical.sha256 == snapshot.sha256:
                    updated = replace(
                        canonical,
                        observed_mirrors=tuple(
                            sorted(
                                {*canonical.observed_mirrors, path.as_posix()},
                                key=str.casefold,
                            )
                        ),
                    )
                    records[seen_repo_skill_index[logical]] = updated
                    seen_repo_skill[logical] = updated
                    continue
                if canonical is not None:
                    record_kind = "skill-mirror"
                    namespace = f"{namespace}-skill-mirror"
                    canonical_key = key
                    mirror_of = canonical.asset_id
                    forced = (
                        "superseded-claude-skill-mirror",
                        "canonical-agents-skill-present",
                    )
            elif relative.parts[:2] == (".agents", "skills"):
                canonical_key = key
            else:
                canonical_key = key
        else:
            canonical_key = key
        record = _make_record(
            path,
            snapshot,
            source_namespace=namespace,
            canonical_key=canonical_key,
            kind=record_kind,
            domain_evidence=(
                evidence
                or (
                    f"path-domain:{_domain_match(key)}"
                    if _domain_match(key)
                    else None
                )
            ),
            decisions=decisions,
            forced_exclusion=forced,
        )
        if mirror_of is not None:
            record = replace(record, mirror_of=mirror_of)
        if category == "skills" and namespace.startswith("repo-") and record_kind == "skill":
            logical = (namespace, canonical_identity_key(canonical_key))
            seen_repo_skill[logical] = record
            seen_repo_skill_index[logical] = len(records)
        records.append(record)
    return sorted(records, key=_record_sort_key)


def inventory_claude_settings(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="settings", kind="setting", decisions=decisions or DecisionSet({}))


def inventory_claude_instructions(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="instructions", kind="instruction", decisions=decisions or DecisionSet({}))


def inventory_claude_hooks(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="hooks", kind="hook", decisions=decisions or DecisionSet({}))


def inventory_claude_commands(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="commands", kind="command", decisions=decisions or DecisionSet({}))


def inventory_claude_skills(
    claude_home,
    repo_roots=(),
    *,
    decisions=None,
    skill_junction_mirrors=None,
):
    home = _require_root(claude_home, label="Claude home")
    relations = (
        _discover_skill_junction_mirrors(home)
        if skill_junction_mirrors is None
        else list(skill_junction_mirrors)
    )
    decision_set = decisions or DecisionSet({})
    records = _inventory_category(
        home,
        dict(repo_roots),
        category="skills",
        kind="skill",
        decisions=decision_set,
        skill_junction_mirrors=relations,
    )
    records.extend(_skill_junction_target_records(relations, decision_set))
    return sorted(records, key=_record_sort_key)


def inventory_claude_agents(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="agents", kind="agent", decisions=decisions or DecisionSet({}))


def inventory_claude_workflows(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="workflows", kind="workflow", decisions=decisions or DecisionSet({}))


def inventory_claude_attachments(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="attachments", kind="attachment", decisions=decisions or DecisionSet({}))


def inventory_claude_backups(claude_home, repo_roots=(), *, decisions=None):
    return _inventory_category(claude_home, dict(repo_roots), category="backups", kind="backup", decisions=decisions or DecisionSet({}))


def _validate_git_root(root: Path, *, namespace: str) -> None:
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise ContractError(f"not a Git repository: {root}") from error
    observed = Path(completed.stdout.strip().replace("/", os.sep))
    if os.path.normcase(os.path.abspath(observed)) != os.path.normcase(os.path.abspath(root)):
        raise ContractError(f"Git root is not the exact repository top-level: {root}")


def _git_index_binding(root: Path, relative: str) -> tuple[str | None, str | None]:
    completed = subprocess.run(
        ["git", "-C", os.fspath(root), "ls-files", "--stage", "--", relative],
        check=True,
        capture_output=True,
        text=True,
    )
    if not completed.stdout:
        return None, None
    fields = completed.stdout.split()
    if len(fields) < 2:
        raise InventoryError(f"unexpected Git index record: {relative}")
    return fields[0], fields[1]


def _git_mode(root: Path, relative: str) -> str | None:
    return _git_index_binding(root, relative)[0]


def _git_status(root: Path) -> list[tuple[str, str, str | None]]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            os.fspath(root),
            "-c",
            "core.quotePath=false",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        check=True,
        capture_output=True,
    )
    fields = completed.stdout.split(b"\0")
    entries: list[tuple[str, str, str | None]] = []
    index = 0
    while index < len(fields) and fields[index]:
        field = fields[index]
        if len(field) < 4 or field[2:3] != b" ":
            raise InventoryError("unexpected Git porcelain record")
        status_code = field[:2].decode("ascii")
        relative = field[3:].decode("utf-8", "surrogateescape")
        old_relative = None
        if "R" in status_code or "C" in status_code:
            index += 1
            if index >= len(fields) or not fields[index]:
                raise InventoryError("incomplete Git rename porcelain record")
            old_relative = fields[index].decode("utf-8", "surrogateescape")
        entries.append((status_code, relative, old_relative))
        index += 1
    return entries


def inventory_repo_dirty(
    root: os.PathLike[str] | str,
    cutoff: object | None,
    *,
    source_namespace: str | None = None,
    decisions: DecisionSet | None = None,
) -> list[AssetRecord]:
    repo = _require_root(root, label="repository root")
    _validate_git_root(repo, namespace=source_namespace or "repo")
    namespace = source_namespace or f"repo-{repo.name.casefold()}"
    cutoff_ns = None if cutoff is None else _normalize_cutoff(cutoff)[0]
    decision_set = decisions or DecisionSet({})
    records: list[AssetRecord] = []
    for status_code, relative, _old_relative in _git_status(repo):
        if "D" in status_code:
            raise InventoryError(f"deleted dirty path requires operator resolution: {relative}")
        mode = _git_mode(repo, relative)
        if mode == "160000":
            raise InventoryError(f"dirty gitlink/submodule requires operator resolution: {relative}")
        path = repo / Path(relative)
        try:
            _assert_not_reparse(path, approved_root=repo)
            metadata = os.lstat(path)
        except FileNotFoundError as error:
            raise InventoryError(f"dirty path disappeared during inventory: {relative}") from error
        if stat.S_ISDIR(metadata.st_mode):
            raise InventoryError(f"dirty gitlink/directory cannot be hashed as an asset: {relative}")
        if status_code == "??":
            dirty_state = "untracked"
        elif "R" in status_code:
            dirty_state = "renamed"
        elif "A" in status_code:
            dirty_state = "tracked-added"
        else:
            dirty_state = "tracked-modified"
        snapshot = _snapshot_file(path, approved_root=repo)
        if not _cutoff_allows(snapshot, cutoff_ns):
            continue
        key = Path(relative).as_posix()
        records.append(
            _make_record(
                path,
                snapshot,
                source_namespace=namespace,
                canonical_key=key,
                kind=_kind_for_path(path),
                domain_evidence=f"approved-dirty-root:{namespace}",
                decisions=decision_set,
                dirty_state=dirty_state,
            )
        )
    return sorted(records, key=_record_sort_key)


def _record_sort_key(record: AssetRecord) -> tuple[str, str]:
    return record.kind, record.source


def _merge_records(groups: Iterable[Iterable[AssetRecord]]) -> list[AssetRecord]:
    by_logical_key: dict[tuple[str, str], AssetRecord] = {}
    by_source: dict[str, tuple[str, str]] = {}
    for group in groups:
        for record in group:
            logical_key = (
                record.source_namespace.casefold(),
                canonical_identity_key(record.canonical_key),
            )
            source_key = record.source.casefold()
            other_logical = by_source.get(source_key)
            if other_logical is not None and other_logical != logical_key:
                raise InventoryError(f"source maps to conflicting logical keys: {record.source}")
            previous = by_logical_key.get(logical_key)
            if previous is None:
                by_logical_key[logical_key] = record
                by_source[source_key] = logical_key
                continue
            if previous.sha256 != record.sha256 or previous.source.casefold() != source_key:
                raise InventoryError(
                    f"logical asset has conflicting bytes or provenance: {record.source_namespace}:{record.canonical_key}"
                )
            dirty_state = record.dirty_state or previous.dirty_state
            if previous.dirty_state and record.dirty_state and previous.dirty_state != record.dirty_state:
                raise InventoryError(f"conflicting dirty states for {record.source}")
            semantic = previous
            if previous.kind == "file" and record.kind != "file":
                semantic = record
            by_logical_key[logical_key] = semantic.with_dirty_state(dirty_state)
    return sorted(by_logical_key.values(), key=_record_sort_key)


def _state_entry(label: str, root: Path, path: Path) -> tuple[object, ...]:
    snapshot = _snapshot_file(path, approved_root=root)
    relative = Path(os.path.relpath(path, root)).as_posix()
    return (
        label,
        canonical_identity_key(relative),
        snapshot.file_id,
        snapshot.size,
        snapshot.mtime_ns,
        False,
        snapshot.sha256,
    )


def _capture_corpus_state(
    home: Path,
    roots: Mapping[str, Path],
    contract: InventoryContract,
) -> tuple[tuple[object, ...], ...]:
    entries: list[tuple[object, ...]] = []
    _agents_home, skill_junction_mirrors = _contract_skill_junction_mirrors(
        home, contract
    )
    entries.append(
        (
            "skill-junction-mirrors",
            _canonical_json(skill_junction_mirrors),
            _skill_junction_mirrors_sha256(skill_junction_mirrors),
        )
    )
    projects = _require_root(home / "projects", label="Claude projects root")
    start_ns = None if contract.cutoff is None else _normalize_cutoff(contract.cutoff)[0]
    as_of_ns = None if contract.as_of is None else _normalize_cutoff(contract.as_of)[0]
    project_groups = dict(contract.chat_projects or {})
    observed_sessions = {group: [] for group in project_groups}
    project_to_group = {project: group for group, project in project_groups.items()}
    for path in _project_direct_jsonl(projects):
        group = project_to_group.get(path.parent.name)
        if project_groups and group is None:
            continue
        metadata = os.lstat(path)
        if start_ns is not None and metadata.st_mtime_ns < start_ns:
            continue
        if as_of_ns is not None and metadata.st_mtime_ns > as_of_ns:
            continue
        session_id = str(uuid.UUID(path.stem))
        if group is not None:
            observed_sessions[group].append(session_id)
        entries.append(_state_entry("chat", projects, path))
    if contract.chat_sessions is not None:
        expected_sessions = {
            group: sorted(values) for group, values in contract.chat_sessions.items()
        }
        actual_sessions = {
            group: sorted(values) for group, values in observed_sessions.items()
        }
        if actual_sessions != expected_sessions:
            raise SourceChangedError("frozen chat session membership differs from contract")
    for project_name in MEMORY_LAYOUT:
        memory_root = _require_root(
            projects / project_name / "memory", label=f"approved memory root {project_name}"
        )
        entries.extend(
            _state_entry(f"memory:{project_name}", memory_root, path)
            for path in _secure_files(memory_root)
        )
    for category in (
        "settings",
        "instructions",
        "hooks",
        "commands",
        "skills",
        "agents",
        "workflows",
        "attachments",
        "backups",
    ):
        values = _category_paths(
            home,
            roots,
            category=category,
            skill_junction_mirrors=(
                skill_junction_mirrors if category == "skills" else ()
            ),
        )
        if category == "skills":
            for name, root in roots.items():
                agents_root = root / ".agents" / "skills"
                if agents_root.is_dir():
                    values.extend(
                        (path, root, f"repo-{name}", None)
                        for path in _secure_files(agents_root)
                    )
        entries.extend(
            _state_entry(f"category:{category}", logical_root, path)
            for path, logical_root, _namespace, _evidence in values
        )
    for name, root in sorted(roots.items()):
        for status_code, relative, old_relative in _git_status(root):
            mode, index_object = _git_index_binding(root, relative)
            git_entry: tuple[object, ...] = (
                f"git:{name}",
                status_code,
                canonical_identity_key(Path(relative).as_posix()),
                canonical_identity_key(Path(old_relative).as_posix()) if old_relative else None,
                mode,
                index_object,
            )
            path = root / Path(relative)
            if os.path.lexists(path):
                _assert_not_reparse(path, approved_root=root)
                metadata = os.lstat(path)
                if stat.S_ISREG(metadata.st_mode):
                    snapshot = _snapshot_file(path, approved_root=root)
                    git_entry += (
                        "file",
                        snapshot.file_id,
                        snapshot.size,
                        snapshot.mtime_ns,
                        False,
                        snapshot.sha256,
                    )
                elif mode == "160000" and stat.S_ISDIR(metadata.st_mode):
                    git_entry += (
                        "gitlink",
                        _file_id(metadata),
                        metadata.st_size,
                        metadata.st_mtime_ns,
                        False,
                    )
                else:
                    raise ContractError(
                        f"dirty member cannot be content-bound: {name}:{relative}"
                    )
            else:
                git_entry += ("missing",)
            entries.append(git_entry)
    return tuple(sorted(entries, key=lambda item: repr(item).casefold()))


def _validate_contract(contract: InventoryContract) -> tuple[Path, dict[str, Path], int, str]:
    home = _require_root(contract.claude_home, label="Claude home")
    roots: dict[str, Path] = {}
    for name, supplied in contract.roots().items():
        root = _require_root(supplied, label=f"{name} mandatory root")
        _validate_git_root(root, namespace=f"repo-{name}")
        if contract.production:
            expected = _absolute_without_resolve(PRODUCTION_ROOTS[name], label=f"expected {name} root")
            if os.path.normcase(os.fspath(root)) != os.path.normcase(os.fspath(expected)):
                raise ContractError(f"production {name} root is not the fixed exact root")
        roots[name] = root
    if contract.production:
        expected_home = _absolute_without_resolve(Path.home() / ".claude", label="expected Claude home")
        if os.path.normcase(os.fspath(home)) != os.path.normcase(os.fspath(expected_home)):
            raise ContractError("production Claude home is not the fixed exact root")
    if contract.require_stable_corpus and contract.agents_home is None:
        raise ContractError("frozen collection requires an agents home binding")
    if contract.agents_home is not None:
        agents_home = _require_root(contract.agents_home, label="agents home")
        expected_agents = _absolute_without_resolve(
            home.parent / ".agents", label="expected agents home"
        )
        if not _same_absolute_path(agents_home, expected_agents):
            raise ContractError("agents home is not the same user canonical root")
    cutoff_ns, cutoff_iso = _normalize_cutoff(contract.cutoff)
    return home, roots, cutoff_ns, cutoff_iso


def _contract_skill_junction_mirrors(
    home: Path, contract: InventoryContract
) -> tuple[Path | None, list[dict[str, object]]]:
    expected_agents = _absolute_without_resolve(
        home.parent / ".agents", label="expected agents home"
    )
    agents_home: Path | None
    if contract.agents_home is not None:
        agents_home = _require_root(contract.agents_home, label="agents home")
        if not _same_absolute_path(agents_home, expected_agents):
            raise ContractError("agents home is not the same user canonical root")
    elif os.path.lexists(expected_agents):
        agents_home = _require_root(expected_agents, label="agents home")
    else:
        agents_home = None

    frozen = contract.skill_junction_mirrors
    if frozen is None:
        observed = _discover_skill_junction_mirrors(home, agents_home)
        return agents_home, observed
    if agents_home is None:
        raise SourceChangedError("frozen agents home is missing")
    expected = json.loads(_canonical_json(list(frozen)))
    observed = _assert_skill_junction_mirrors(home, agents_home, expected)
    return agents_home, observed


def _records_payload(records: Sequence[AssetRecord]) -> str:
    return "".join(
        json.dumps(record.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        for record in records
    )


def _member_descriptor(record: AssetRecord) -> dict[str, object]:
    # The externally SHA-bound contract must cover every field that can affect
    # scanning, copying, disposition, or later reconciliation.  Keeping the
    # canonical AssetRecord projection here also prevents schema drift between
    # the manifest and its approved-member ledger.
    return record.to_dict()


def _approved_members(records: Sequence[AssetRecord]) -> list[dict[str, object]]:
    return sorted(
        (_member_descriptor(record) for record in records),
        key=lambda item: (
            item["source_namespace"].casefold(),
            canonical_identity_key(item["canonical_key"]),
        ),
    )


def _approved_members_sha256(members: Sequence[Mapping[str, object]]) -> str:
    return hashlib.sha256(_canonical_json(list(members)).encode("utf-8")).hexdigest()


def records_to_jsonl(records: Iterable[AssetRecord]) -> str:
    return _records_payload(sorted(records, key=_record_sort_key))


def _root_identity(root: Path) -> dict[str, str]:
    metadata = os.lstat(root)
    return {"path": root.as_posix(), "file_id": _file_id(metadata)}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _same_absolute_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(left)) == os.path.normcase(os.fspath(right))


def _production_evidence_paths(
    contract_path: Path,
    manifest_path: Path,
    metadata_path: Path,
    decision_path: Path,
) -> Path:
    root = _absolute_without_resolve(PRODUCTION_OUTPUT_ROOT, label="protected output root")
    supplied = {
        "contract": _absolute_without_resolve(contract_path, label="contract output"),
        "manifest": _absolute_without_resolve(manifest_path, label="manifest output"),
        "metadata": _absolute_without_resolve(metadata_path, label="metadata output"),
        "domain_decisions": _absolute_without_resolve(
            decision_path, label="domain decision file"
        ),
    }
    for label, path in supplied.items():
        expected = root / PRODUCTION_OUTPUT_NAMES[label]
        if not _same_absolute_path(path, expected):
            raise ContractError(
                f"production protected output path is not the fixed exact path: {label}"
            )
    return root


def _assert_production_finals_absent(
    root: Path,
    contract_path: Path,
    manifest_path: Path,
    metadata_path: Path,
) -> None:
    for path in (contract_path, manifest_path, metadata_path):
        if os.path.lexists(path):
            _assert_not_reparse(path, approved_root=root)
            raise FileExistsError(f"production output already exists: {path}")


def _current_user_sid() -> str:
    if os.name != "nt":
        raise ContractError("protected root verification requires Windows")
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    token = wintypes.HANDLE()
    advapi32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
        raise ContractError(f"cannot open current process token: {ctypes.get_last_error()}")
    try:
        size = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token, 1, None, 0, ctypes.byref(size)
        )
        if not size.value:
            raise ContractError("cannot size current process token user")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, 1, buffer, size, ctypes.byref(size)
        ):
            raise ContractError(f"cannot read current process token user: {ctypes.get_last_error()}")

        class SidAndAttributes(ctypes.Structure):
            _fields_ = [("sid", ctypes.c_void_p), ("attributes", wintypes.DWORD)]

        class TokenUser(ctypes.Structure):
            _fields_ = [("user", SidAndAttributes)]

        token_user = ctypes.cast(buffer, ctypes.POINTER(TokenUser)).contents
        sid_string = wintypes.LPWSTR()
        advapi32.ConvertSidToStringSidW.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        )
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        if not advapi32.ConvertSidToStringSidW(token_user.user.sid, ctypes.byref(sid_string)):
            raise ContractError(f"cannot format current user SID: {ctypes.get_last_error()}")
        try:
            return str(sid_string.value)
        finally:
            kernel32.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
    finally:
        kernel32.CloseHandle(token)


def _windows_root_identity(root: Path) -> str:
    if os.name != "nt":
        raise ContractError("protected root identity requires Windows")

    class FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    class ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("attributes", wintypes.DWORD),
            ("creation", FileTime),
            ("last_access", FileTime),
            ("last_write", FileTime),
            ("volume_serial", wintypes.DWORD),
            ("size_high", wintypes.DWORD),
            ("size_low", wintypes.DWORD),
            ("links", wintypes.DWORD),
            ("file_index_high", wintypes.DWORD),
            ("file_index_low", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    kernel32.CreateFileW.restype = wintypes.HANDLE
    handle = kernel32.CreateFileW(
        os.fspath(root),
        0x0080,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000 | 0x00200000,
        None,
    )
    if handle == ctypes.c_void_p(-1).value:
        raise ContractError(f"cannot open protected root identity: {ctypes.get_last_error()}")
    try:
        information = ByHandleFileInformation()
        if not kernel32.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise ContractError(f"cannot read protected root identity: {ctypes.get_last_error()}")
        return (
            f"{information.volume_serial:08X}:"
            f"{information.file_index_high:08X}{information.file_index_low:08X}"
        )
    finally:
        kernel32.CloseHandle(handle)


def _verified_verifier_binding(
    frozen_tool: Mapping[str, object] | None,
) -> dict[str, str]:
    if frozen_tool is None:
        repository = Path(__file__).absolute().parents[3]
        commit = _git_output(repository, "rev-parse", "HEAD")
        expected = None
    else:
        repository = _require_root(
            Path(str(frozen_tool.get("repo_root"))), label="frozen tool repository"
        )
        commit = str(frozen_tool.get("commit"))
        files = frozen_tool.get("files")
        if not isinstance(files, list):
            raise ContractError("frozen verifier tool binding is invalid")
        expected = next(
            (
                item
                for item in files
                if isinstance(item, dict)
                and item.get("path") == "scripts/codex/import/secure_backup_root.ps1"
            ),
            None,
        )
        if expected is None:
            raise ContractError("frozen verifier tool binding is missing")
    current = _tool_file_binding(
        repository,
        repository / "scripts" / "codex" / "import" / "secure_backup_root.ps1",
        commit=commit,
        production=True,
    )
    if expected is not None and current != expected:
        raise ContractError("secure root verifier differs from frozen tool binding")
    return current


def _verify_protected_root(
    root: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    frozen_tool: Mapping[str, object] | None = None,
) -> dict[str, object]:
    expected = _absolute_without_resolve(PRODUCTION_OUTPUT_ROOT, label="protected output root")
    candidate = _absolute_without_resolve(root, label="protected output root")
    if not _same_absolute_path(candidate, expected):
        raise ContractError("protected output root is not the fixed exact root")
    candidate = _require_root(candidate, label="protected output root")
    _assert_not_reparse(candidate)
    current_sid = _current_user_sid()
    identity_before = _windows_root_identity(candidate)
    _verified_verifier_binding(frozen_tool)
    verifier = _require_root(
        SECURE_ROOT_VERIFIER, label="secure backup root verifier", directory=False
    )
    powershell = _require_root(
        WINDOWS_POWERSHELL, label="Windows PowerShell", directory=False
    )
    try:
        completed = runner(
            [
                os.fspath(powershell),
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                os.fspath(verifier),
                "-VerifyTree",
                os.fspath(candidate),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ContractError("protected root VerifyTree failed") from error
    _verified_verifier_binding(frozen_tool)
    identity_after = _windows_root_identity(candidate)
    try:
        receipt = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as error:
        raise ContractError("protected root VerifyTree receipt is invalid") from error
    if not isinstance(receipt, dict) or receipt.get("Mode") != "VerifyTree":
        raise ContractError("protected root VerifyTree receipt is invalid")
    try:
        receipt_path = _absolute_without_resolve(
            Path(str(receipt.get("Path"))), label="VerifyTree receipt path"
        )
    except ContractError as error:
        raise ContractError("protected root VerifyTree receipt path is invalid") from error
    owner_sid = receipt.get("OwnerSid")
    root_identity = receipt.get("RootIdentity")
    tree_hash = receipt.get("TreeSnapshotHash")
    access = receipt.get("Access")
    if (
        not _same_absolute_path(receipt_path, candidate)
        or receipt.get("Encrypted") is not True
        or owner_sid != current_sid
        or root_identity != identity_before
        or identity_after != identity_before
        or not isinstance(tree_hash, str)
        or not re.fullmatch(r"[0-9A-Fa-f]{64}", tree_hash)
        or not isinstance(access, list)
    ):
        raise ContractError("protected root EFS/identity receipt is invalid")
    expected_sids = {owner_sid, "S-1-5-18"}
    observed_sids: set[str] = set()
    canonical_access: list[dict[str, object]] = []
    for entry in access:
        if not isinstance(entry, dict):
            raise ContractError("protected root ACL receipt is invalid")
        sid = entry.get("Sid")
        if (
            sid not in expected_sids
            or sid in observed_sids
            or entry.get("Type") != "Allow"
            or entry.get("Rights") != "FullControl"
            or entry.get("IsInherited") is not False
            or entry.get("InheritanceFlags") != "ContainerInherit, ObjectInherit"
            or entry.get("PropagationFlags") != "None"
        ):
            raise ContractError("protected root ACL receipt is invalid")
        observed_sids.add(str(sid))
        canonical_access.append(dict(entry))
    if observed_sids != expected_sids:
        raise ContractError("protected root ACL receipt is invalid")
    acl_payload = {
        "owner_sid": owner_sid,
        "access": sorted(canonical_access, key=lambda item: str(item["Sid"])),
    }
    return {
        "path": candidate.as_posix(),
        "root_identity": root_identity,
        "owner_sid": owner_sid,
        "acl_sha256": hashlib.sha256(
            _canonical_json(acl_payload).encode("utf-8")
        ).hexdigest(),
        "efs": True,
    }


def _git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", os.fspath(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _tool_file_binding(
    tool_repo: Path,
    path: Path,
    *,
    commit: str,
    production: bool,
) -> dict[str, str]:
    snapshot = _snapshot_file(path, approved_root=tool_repo)
    try:
        relative = Path(os.path.relpath(path, tool_repo)).as_posix()
    except ValueError as error:
        raise ContractError(f"tool is outside Mercury root: {path}") from error
    if relative.startswith("../"):
        raise ContractError(f"tool is outside Mercury root: {path}")
    worktree_blob = _git_output(tool_repo, "hash-object", "--", relative)
    if production:
        listing = _git_output(tool_repo, "ls-tree", commit, "--", relative)
        if not listing:
            raise ContractError(f"tool is not committed at frozen HEAD: {relative}")
        committed_blob = listing.split()[2]
        if committed_blob != worktree_blob:
            raise ContractError(f"tool worktree differs from frozen HEAD: {relative}")
    return {"path": relative, "blob": worktree_blob, "sha256": snapshot.sha256}


def _tool_binding(mercury_root: Path, *, production: bool) -> dict[str, object]:
    tool_repo = mercury_root if production else Path(__file__).absolute().parents[3]
    _validate_git_root(tool_repo, namespace="tool-repository")
    commit = _git_output(tool_repo, "rev-parse", "HEAD")
    files = [
        _tool_file_binding(tool_repo, path, commit=commit, production=production)
        for path in (
            Path(__file__).absolute(),
            Path(_model.__file__).absolute(),
            SECURE_ROOT_VERIFIER,
        )
    ]
    return {"commit": commit, "repo_root": tool_repo.as_posix(), "files": files}


def _frozen_chat_sessions(
    projects_root: Path,
    chat_projects: Mapping[str, str],
    *,
    start_ns: int,
    as_of_ns: int,
) -> dict[str, list[str]]:
    sessions = {group: [] for group in chat_projects}
    allowed_projects = {project: group for group, project in chat_projects.items()}
    for path in _project_direct_jsonl(projects_root):
        group = allowed_projects.get(path.parent.name)
        if group is None:
            continue
        metadata = os.lstat(path)
        if metadata.st_mtime_ns < start_ns or metadata.st_mtime_ns > as_of_ns:
            continue
        try:
            session_id = str(uuid.UUID(path.stem))
        except ValueError as error:
            raise ContractError(f"main chat filename is not a session UUID: {path.name}") from error
        sessions[group].append(session_id)
    return {group: sorted(values) for group, values in sorted(sessions.items())}


def freeze_inventory_contract(
    *,
    contract_path: Path,
    output_path: Path,
    metadata_path: Path,
    claude_home: Path,
    roots: Mapping[str, Path],
    domain_decisions: Path,
    chat_projects: Mapping[str, str] | None = None,
    production: bool = True,
) -> str:
    contract_output = _absolute_without_resolve(contract_path, label="contract output")
    manifest_output = _absolute_without_resolve(output_path, label="manifest output")
    metadata_output = _absolute_without_resolve(metadata_path, label="metadata output")
    if len({contract_output, manifest_output, metadata_output}) != 3:
        raise ContractError("contract, manifest, and metadata paths must be distinct")
    protected_root: Path | None = None
    protected_root_binding: dict[str, object] | None = None
    if production:
        protected_root = _production_evidence_paths(
            contract_output,
            manifest_output,
            metadata_output,
            Path(domain_decisions),
        )
        _assert_production_finals_absent(
            protected_root, contract_output, manifest_output, metadata_output
        )
        protected_root_binding = _verify_protected_root(protected_root)
    home = _require_root(claude_home, label="Claude home")
    agents_home = _require_root(home.parent / ".agents", label="agents home")
    expected_agents_home = _absolute_without_resolve(
        home.parent / ".agents", label="expected agents home"
    )
    if not _same_absolute_path(agents_home, expected_agents_home):
        raise ContractError("agents home is not the same user canonical root")
    skill_junction_mirrors = _discover_skill_junction_mirrors(home, agents_home)
    if set(roots) != set(PRODUCTION_ROOTS):
        raise ContractError("contract must bind exactly four named roots")
    checked_roots: dict[str, Path] = {}
    for name, supplied in roots.items():
        root = _require_root(supplied, label=f"{name} mandatory root")
        _validate_git_root(root, namespace=f"repo-{name}")
        if production:
            expected = _absolute_without_resolve(PRODUCTION_ROOTS[name], label=f"expected {name}")
            if os.path.normcase(os.fspath(root)) != os.path.normcase(os.fspath(expected)):
                raise ContractError(f"production {name} root is not the fixed exact root")
        checked_roots[name] = root
    if production:
        expected_home = _absolute_without_resolve(Path.home() / ".claude", label="expected home")
        if os.path.normcase(os.fspath(home)) != os.path.normcase(os.fspath(expected_home)):
            raise ContractError("production Claude home is not the fixed exact root")
    decision_path = _require_root(domain_decisions, label="domain decision file", directory=False)
    decision_snapshot = _snapshot_file(decision_path)
    start_ns, start_iso = _normalize_cutoff(FROZEN_START)
    as_of_ns, as_of_iso = _normalize_cutoff(FROZEN_AS_OF)
    selected_projects = dict(chat_projects or PRODUCTION_CHAT_PROJECTS)
    if set(selected_projects) != set(PRODUCTION_CHAT_GROUP_COUNTS):
        raise ContractError("chat project mapping must contain home, mercury, and godot")
    if production and selected_projects != PRODUCTION_CHAT_PROJECTS:
        raise ContractError("production chat project mapping is immutable")
    sessions = _frozen_chat_sessions(
        _require_root(home / "projects", label="Claude projects root"),
        selected_projects,
        start_ns=start_ns,
        as_of_ns=as_of_ns,
    )
    observed_group_counts = {group: len(values) for group, values in sessions.items()}
    if observed_group_counts != PRODUCTION_CHAT_GROUP_COUNTS:
        raise ContractError(
            f"chat group count mismatch: expected {PRODUCTION_CHAT_GROUP_COUNTS}, observed {observed_group_counts}"
        )
    tool_binding = _tool_binding(checked_roots["mercury"], production=production)
    sessions_after = _frozen_chat_sessions(
        home / "projects",
        selected_projects,
        start_ns=start_ns,
        as_of_ns=as_of_ns,
    )
    if sessions_after != sessions:
        raise SourceChangedError("chat session membership changed while freezing contract")
    inventory_tool = next(
        item for item in tool_binding["files"] if str(item["path"]).endswith("inventory.py")
    )
    member_contract = InventoryContract(
        claude_home=home,
        mercury_root=checked_roots["mercury"],
        godot_root=checked_roots["godot"],
        design_root=checked_roots["design"],
        kb_root=checked_roots["kb"],
        cutoff=start_iso,
        as_of=as_of_iso,
        domain_decisions=decision_path,
        expected_counts=PRODUCTION_EXPECTED_COUNTS,
        production=production,
        tool_commit=str(tool_binding["commit"]),
        tool_sha256=str(inventory_tool["sha256"]),
        chat_projects=selected_projects,
        chat_sessions=sessions,
        require_stable_corpus=True,
        frozen_tool=tool_binding,
        agents_home=agents_home,
        skill_junction_mirrors=skill_junction_mirrors,
    )
    approved_records = collect_inventory(member_contract).records
    if any(record.disposition is None for record in approved_records):
        raise ContractError("inventory contract cannot freeze unresolved domain members")
    approved_members = _approved_members(approved_records)
    document: dict[str, object] = {
        "record_type": "inventory-contract",
        "schema_version": 3,
        "production": production,
        "window": {"start": start_iso, "as_of": as_of_iso},
        "claude_home": _root_identity(home),
        "agents_home": _root_identity(agents_home),
        "roots": {
            name: _root_identity(root) for name, root in sorted(checked_roots.items())
        },
        "expected": {
            "chat_groups": dict(PRODUCTION_CHAT_GROUP_COUNTS),
            "counts": dict(PRODUCTION_EXPECTED_COUNTS),
        },
        "chat_projects": dict(sorted(selected_projects.items())),
        "chat_sessions": sessions,
        "domain_decisions": {
            "path": decision_path.as_posix(),
            "sha256": decision_snapshot.sha256,
        },
        "tool": tool_binding,
        "approved_members": approved_members,
        "approved_members_sha256": _approved_members_sha256(approved_members),
        "approved_member_count": len(approved_members),
        "skill_junction_mirrors": skill_junction_mirrors,
        "skill_junction_mirror_count": len(skill_junction_mirrors),
        "skill_junction_mirrors_sha256": _skill_junction_mirrors_sha256(
            skill_junction_mirrors
        ),
        "output": {
            "contract": contract_output.as_posix(),
            "manifest": manifest_output.as_posix(),
            "metadata": metadata_output.as_posix(),
            "schema": "inventory-jsonl-v3",
        },
    }
    if protected_root_binding is not None:
        document["protected_root"] = protected_root_binding
    document["contract_payload_sha256"] = hashlib.sha256(
        _canonical_json(document).encode("utf-8")
    ).hexdigest()
    serialized = _canonical_json(document) + "\n"
    if production:
        if protected_root is None or protected_root_binding is None:
            raise ContractError("production protected root binding is missing")
        _assert_production_finals_absent(
            protected_root, contract_output, manifest_output, metadata_output
        )
        prepublish_binding = _verify_protected_root(
            protected_root, frozen_tool=tool_binding
        )
        if prepublish_binding != protected_root_binding:
            raise ContractError(
                "protected root identity, owner, ACL, or EFS changed before contract publish"
            )
    _write_new(contract_output, serialized)
    contract_sha256 = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    try:
        load_inventory_contract(contract_output, contract_sha256, verify_bindings=True)
    except Exception:
        if os.path.lexists(contract_output):
            os.unlink(contract_output)
        raise
    return contract_sha256


def _validate_skill_junction_contract(document: Mapping[str, object]) -> None:
    home_binding = document.get("claude_home")
    agents_binding = document.get("agents_home")
    if not isinstance(home_binding, dict) or not isinstance(agents_binding, dict):
        raise ContractError("inventory contract user source root binding is invalid")
    if set(agents_binding) != {"path", "file_id"}:
        raise ContractError("inventory contract agents home identity is invalid")
    home_path = _absolute_without_resolve(
        Path(str(home_binding.get("path"))), label="contract Claude home"
    )
    agents_path = _absolute_without_resolve(
        Path(str(agents_binding.get("path"))), label="contract agents home"
    )
    if (
        agents_path.as_posix() != agents_binding.get("path")
        or not isinstance(agents_binding.get("file_id"), str)
        or not agents_binding["file_id"]
        or not _same_absolute_path(agents_path, home_path.parent / ".agents")
    ):
        raise ContractError("inventory contract agents home identity is invalid")

    relations = document.get("skill_junction_mirrors")
    if (
        not isinstance(relations, list)
        or type(document.get("skill_junction_mirror_count")) is not int
        or document.get("skill_junction_mirror_count") != len(relations)
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(document.get("skill_junction_mirrors_sha256", ""))
        )
        or _skill_junction_mirrors_sha256(relations)
        != document.get("skill_junction_mirrors_sha256")
    ):
        raise ContractError("inventory contract skill junction binding is invalid")
    expected_order: list[str] = []
    seen_links: set[str] = set()
    seen_targets: set[str] = set()
    seen_link_identities: set[str] = set()
    seen_target_identities: set[str] = set()
    for relation in relations:
        if not isinstance(relation, dict) or set(relation) != {
            "relation_type",
            "link_path",
            "link_identity",
            "raw_target",
            "canonical_target",
            "target_identity",
            "target_member_count",
            "target_members",
            "target_members_sha256",
        }:
            raise ContractError("inventory contract skill junction relation is invalid")
        link_identity = relation.get("link_identity")
        target_identity = relation.get("target_identity")
        if (
            relation.get("relation_type") != "claude-user-skill-junction"
            or not isinstance(link_identity, dict)
            or set(link_identity) != {"file_id", "size", "mtime_ns", "reparse_tag"}
            or not isinstance(target_identity, dict)
            or set(target_identity) != {"file_id", "size", "mtime_ns"}
            or link_identity.get("reparse_tag") != WINDOWS_JUNCTION_REPARSE_TAG
        ):
            raise ContractError("inventory contract skill junction identity is invalid")
        for identity in (link_identity, target_identity):
            if (
                not isinstance(identity.get("file_id"), str)
                or not identity["file_id"]
                or type(identity.get("size")) is not int
                or identity["size"] < 0
                or type(identity.get("mtime_ns")) is not int
                or identity["mtime_ns"] < 0
            ):
                raise ContractError("inventory contract skill junction identity is invalid")
        link = _absolute_without_resolve(
            Path(str(relation.get("link_path"))), label="skill junction link"
        )
        target = _absolute_without_resolve(
            Path(str(relation.get("canonical_target"))), label="skill junction target"
        )
        if (
            link.as_posix() != relation.get("link_path")
            or target.as_posix() != relation.get("canonical_target")
            or link.parent.as_posix() != (home_path / "skills").as_posix()
            or target.parent.as_posix() != (agents_path / "skills").as_posix()
            or link.name != target.name
        ):
            raise ContractError("inventory contract skill junction path is invalid")
        try:
            raw_target = _canonical_junction_target(str(relation.get("raw_target")))
        except ReparsePointError as error:
            raise ContractError("inventory contract skill junction raw target is invalid") from error
        if raw_target.as_posix() != target.as_posix() or raw_target.name != link.name:
            raise ContractError("inventory contract skill junction raw target is invalid")
        link_key = link.as_posix().casefold()
        target_key = target.as_posix().casefold()
        link_file_id = str(link_identity["file_id"])
        target_file_id = str(target_identity["file_id"])
        if (
            link_key in seen_links
            or target_key in seen_targets
            or link_file_id in seen_link_identities
            or target_file_id in seen_target_identities
        ):
            raise ContractError("inventory contract skill junction relation is duplicated")
        seen_links.add(link_key)
        seen_targets.add(target_key)
        seen_link_identities.add(link_file_id)
        seen_target_identities.add(target_file_id)
        expected_order.append(link_key)

        members = relation.get("target_members")
        if (
            not isinstance(members, list)
            or type(relation.get("target_member_count")) is not int
            or relation.get("target_member_count") != len(members)
            or not re.fullmatch(r"[0-9a-f]{64}", str(relation.get("target_members_sha256", "")))
            or hashlib.sha256(_canonical_json(members).encode("utf-8")).hexdigest()
            != relation.get("target_members_sha256")
        ):
            raise ContractError("inventory contract skill target members are invalid")
        member_order: list[str] = []
        member_sources: set[str] = set()
        for member in members:
            if not isinstance(member, dict) or set(member) != {
                "record_role",
                "source",
                "source_namespace",
                "canonical_key",
                "size",
                "sha256",
                "mtime_ns",
                "file_id",
            }:
                raise ContractError("inventory contract skill target member is invalid")
            key = str(member.get("canonical_key"))
            parts = PurePosixPath(key).parts
            raw_parts = key.split("/")
            source = _absolute_without_resolve(
                Path(str(member.get("source"))), label="skill target member source"
            )
            if (
                member.get("record_role") != "relation-target-member"
                or member.get("source_namespace") != "claude-user-skill-target"
                or len(parts) < 2
                or parts[0] != link.name
                or "\\" in key
                or key.startswith("/")
                or "//" in key
                or any(part in {"", ".", ".."} for part in raw_parts)
                or source.as_posix() != member.get("source")
                or not _same_absolute_path(source, target / Path(*parts[1:]))
                or type(member.get("size")) is not int
                or member["size"] < 0
                or type(member.get("mtime_ns")) is not int
                or member["mtime_ns"] < 0
                or not isinstance(member.get("file_id"), str)
                or not member["file_id"]
                or not re.fullmatch(r"[0-9a-f]{64}", str(member.get("sha256", "")))
            ):
                raise ContractError("inventory contract skill target member is invalid")
            logical = canonical_identity_key(key)
            source_key = source.as_posix().casefold()
            if logical in member_order or source_key in member_sources:
                raise ContractError("inventory contract skill target members are duplicated")
            member_order.append(logical)
            member_sources.add(source_key)
        if member_order != sorted(member_order):
            raise ContractError("inventory contract skill target members are not canonical")
    if expected_order != sorted(expected_order):
        raise ContractError("inventory contract skill junction relations are not canonical")


def _validate_contract_document(document: Mapping[str, object]) -> None:
    required_fields = {
        "record_type",
        "schema_version",
        "production",
        "window",
        "claude_home",
        "agents_home",
        "roots",
        "expected",
        "chat_projects",
        "chat_sessions",
        "domain_decisions",
        "tool",
        "approved_members",
        "approved_members_sha256",
        "approved_member_count",
        "skill_junction_mirrors",
        "skill_junction_mirrors_sha256",
        "skill_junction_mirror_count",
        "output",
        "contract_payload_sha256",
    }
    if document.get("production"):
        required_fields.add("protected_root")
    if set(document) != required_fields:
        raise ContractError("inventory contract fields do not match schema")
    if type(document.get("production")) is not bool:
        raise ContractError("inventory contract production flag is invalid")
    expected_window = {
        "start": _normalize_cutoff(FROZEN_START)[1],
        "as_of": _normalize_cutoff(FROZEN_AS_OF)[1],
    }
    if document.get("window") != expected_window:
        raise ContractError("inventory contract frozen window is invalid")
    _validate_skill_junction_contract(document)
    expected = document.get("expected")
    if expected != {
        "chat_groups": PRODUCTION_CHAT_GROUP_COUNTS,
        "counts": PRODUCTION_EXPECTED_COUNTS,
    }:
        raise ContractError("inventory contract expected counts are invalid")
    projects = document.get("chat_projects")
    sessions = document.get("chat_sessions")
    if (
        not isinstance(projects, dict)
        or set(projects) != set(PRODUCTION_CHAT_GROUP_COUNTS)
        or len(set(projects.values())) != 3
        or not all(isinstance(value, str) and value for value in projects.values())
        or not isinstance(sessions, dict)
        or set(sessions) != set(PRODUCTION_CHAT_GROUP_COUNTS)
    ):
        raise ContractError("inventory contract chat grouping is invalid")
    seen_sessions: set[str] = set()
    for group, count in PRODUCTION_CHAT_GROUP_COUNTS.items():
        values = sessions.get(group)
        if not isinstance(values, list) or values != sorted(values) or len(values) != count:
            raise ContractError(f"inventory contract {group} chat sessions are invalid")
        for value in values:
            try:
                canonical_session = str(uuid.UUID(value))
            except (ValueError, AttributeError, TypeError) as error:
                raise ContractError("inventory contract chat session is not a UUID") from error
            if canonical_session != value or value in seen_sessions:
                raise ContractError("inventory contract chat sessions are not unique/canonical")
            seen_sessions.add(value)
    if document.get("production") and projects != PRODUCTION_CHAT_PROJECTS:
        raise ContractError("production chat project grouping is invalid")
    identity_bindings = {"claude_home": document.get("claude_home")}
    roots = document.get("roots")
    if not isinstance(roots, dict) or set(roots) != set(PRODUCTION_ROOTS):
        raise ContractError("inventory contract roots are invalid")
    identity_bindings.update({f"root:{name}": value for name, value in roots.items()})
    for label, binding in identity_bindings.items():
        if not isinstance(binding, dict):
            raise ContractError(f"inventory contract {label} identity is invalid")
        path_value = binding.get("path")
        file_id = binding.get("file_id")
        if not isinstance(path_value, str) or not isinstance(file_id, str) or not file_id:
            raise ContractError(f"inventory contract {label} identity is invalid")
        canonical = _absolute_without_resolve(Path(path_value), label=label).as_posix()
        if canonical != path_value or "\\" in path_value:
            raise ContractError(f"inventory contract {label} path is not canonical")
    if document.get("production"):
        expected_home = (Path.home() / ".claude").absolute().as_posix()
        if document["claude_home"]["path"].casefold() != expected_home.casefold():
            raise ContractError("production Claude home path is invalid")
        for name, path in PRODUCTION_ROOTS.items():
            if roots[name]["path"].casefold() != path.absolute().as_posix().casefold():
                raise ContractError(f"production {name} root path is invalid")
    decision = document.get("domain_decisions")
    if (
        not isinstance(decision, dict)
        or not isinstance(decision.get("path"), str)
        or _absolute_without_resolve(Path(decision["path"]), label="decision path").as_posix()
        != decision["path"]
        or not re.fullmatch(r"[0-9a-f]{64}", str(decision.get("sha256", "")))
    ):
        raise ContractError("inventory contract decision binding is invalid")
    tool = document.get("tool")
    if (
        not isinstance(tool, dict)
        or not re.fullmatch(r"[0-9a-f]{40,64}", str(tool.get("commit", "")))
        or not isinstance(tool.get("repo_root"), str)
        or not isinstance(tool.get("files"), list)
    ):
        raise ContractError("inventory contract tool binding is invalid")
    expected_tool_paths = {
        "scripts/codex/import/inventory.py",
        "scripts/codex/import/model.py",
        "scripts/codex/import/secure_backup_root.ps1",
    }
    observed_tool_paths: set[str] = set()
    for item in tool["files"]:
        if not isinstance(item, dict):
            raise ContractError("inventory contract tool file binding is invalid")
        observed_tool_paths.add(str(item.get("path")))
        if not re.fullmatch(r"[0-9a-f]{40}", str(item.get("blob", ""))) or not re.fullmatch(
            r"[0-9a-f]{64}", str(item.get("sha256", ""))
        ):
            raise ContractError("inventory contract tool file digest is invalid")
    if observed_tool_paths != expected_tool_paths:
        raise ContractError("inventory contract must bind all reviewed Import tools")
    members = document.get("approved_members")
    if (
        not isinstance(members, list)
        or document.get("approved_member_count") != len(members)
        or not re.fullmatch(
            r"[0-9a-f]{64}", str(document.get("approved_members_sha256", ""))
        )
        or _approved_members_sha256(members) != document.get("approved_members_sha256")
    ):
        raise ContractError("inventory contract approved member binding is invalid")
    member_keys: set[tuple[str, str]] = set()
    member_sources: set[str] = set()
    member_ids: set[str] = set()
    member_counts: Counter[str] = Counter()
    expected_member_order: list[tuple[str, str]] = []
    for item in members:
        if not isinstance(item, dict) or set(item) != set(AssetRecord.__dataclass_fields__):
            raise ContractError("inventory contract approved member is invalid")
        values = dict(item)
        if isinstance(values.get("observed_mirrors"), list):
            values["observed_mirrors"] = tuple(values["observed_mirrors"])
        try:
            record = AssetRecord(**values)
        except (TypeError, ValueError) as error:
            raise ContractError("inventory contract approved member is invalid") from error
        if _member_descriptor(record) != item:
            raise ContractError("inventory contract approved member is noncanonical")
        namespace = record.source_namespace
        key = record.canonical_key
        source = record.source
        logical = (namespace.casefold(), canonical_identity_key(key))
        if (
            logical in member_keys
            or source.casefold() in member_sources
            or record.asset_id in member_ids
        ):
            raise ContractError("inventory contract approved members contain duplicates")
        member_keys.add(logical)
        member_sources.add(source.casefold())
        member_ids.add(record.asset_id)
        member_counts[record.kind] += 1
        expected_member_order.append(logical)
    if expected_member_order != sorted(expected_member_order):
        raise ContractError("inventory contract approved members are not canonical")
    for kind, count in PRODUCTION_EXPECTED_COUNTS.items():
        if member_counts[kind] != count:
            raise ContractError(f"inventory contract approved {kind} membership is invalid")
    output = document.get("output")
    if (
        not isinstance(output, dict)
        or set(output) != {"contract", "manifest", "metadata", "schema"}
        or output.get("schema") != "inventory-jsonl-v3"
    ):
        raise ContractError("inventory contract output schema is invalid")
    output_paths: list[str] = []
    for field in ("contract", "manifest", "metadata"):
        value = output.get(field)
        if not isinstance(value, str):
            raise ContractError("inventory contract output path is invalid")
        canonical = _absolute_without_resolve(Path(value), label=f"output {field}").as_posix()
        if canonical != value:
            raise ContractError("inventory contract output path is not canonical")
        output_paths.append(value.casefold())
    if len(set(output_paths)) != 3:
        raise ContractError("inventory contract output paths must be distinct")
    protected_binding = document.get("protected_root")
    if document.get("production"):
        _production_evidence_paths(
            Path(str(output["contract"])),
            Path(str(output["manifest"])),
            Path(str(output["metadata"])),
            Path(str(decision["path"])),
        )
        if (
            not isinstance(protected_binding, dict)
            or set(protected_binding) != {
                "path",
                "root_identity",
                "owner_sid",
                "acl_sha256",
                "efs",
            }
            or not isinstance(protected_binding.get("root_identity"), str)
            or not protected_binding.get("root_identity")
            or not isinstance(protected_binding.get("owner_sid"), str)
            or not str(protected_binding.get("owner_sid")).startswith("S-1-5-")
            or not re.fullmatch(r"[0-9a-f]{64}", str(protected_binding.get("acl_sha256", "")))
            or protected_binding.get("efs") is not True
            or str(protected_binding.get("path", "")).casefold()
            != PRODUCTION_OUTPUT_ROOT.absolute().as_posix().casefold()
        ):
            raise ContractError("inventory contract protected root binding is invalid")
    elif protected_binding is not None:
        raise ContractError("fixture inventory contract must not claim a protected root")


def load_inventory_contract(
    path: Path,
    expected_sha256: str,
    *,
    verify_bindings: bool,
) -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise ContractError("contract SHA-256 is invalid")
    contract_path = _require_root(path, label="inventory contract", directory=False)
    snapshot = _snapshot_file(contract_path, approved_root=contract_path.parent, capture_limit=-1)
    if snapshot.sha256 != expected_sha256:
        raise ContractError("inventory contract SHA-256 mismatch")
    try:
        document = json.loads(snapshot.captured.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContractError("inventory contract is not valid UTF-8 JSON") from error
    if not isinstance(document, dict) or document.get("record_type") != "inventory-contract":
        raise ContractError("invalid inventory contract header")
    if document.get("schema_version") != 3:
        raise ContractError("unsupported inventory contract schema")
    payload_hash = document.pop("contract_payload_sha256", None)
    observed_payload_hash = hashlib.sha256(_canonical_json(document).encode("utf-8")).hexdigest()
    document["contract_payload_sha256"] = payload_hash
    if payload_hash != observed_payload_hash:
        raise ContractError("inventory contract payload hash mismatch")
    _validate_contract_document(document)
    output_binding = document.get("output")
    if (
        not isinstance(output_binding, dict)
        or contract_path.as_posix().casefold()
        != str(output_binding.get("contract", "")).casefold()
    ):
        raise ContractError("inventory contract path differs from its frozen binding")
    if verify_bindings:
        if document.get("production"):
            frozen_tool = document.get("tool")
            if not isinstance(frozen_tool, dict):
                raise ContractError("inventory contract tool binding is invalid")
            observed_protection = _verify_protected_root(
                PRODUCTION_OUTPUT_ROOT, frozen_tool=frozen_tool
            )
            if observed_protection != document.get("protected_root"):
                raise ContractError(
                    "protected root identity, owner, ACL, or EFS differs from inventory contract"
                )
        home_binding = document.get("claude_home")
        if not isinstance(home_binding, dict):
            raise ContractError("inventory contract Claude home binding is invalid")
        home = _require_root(Path(str(home_binding.get("path"))), label="contract Claude home")
        if _root_identity(home) != home_binding:
            raise ContractError("Claude home identity differs from inventory contract")
        agents_binding = document.get("agents_home")
        if not isinstance(agents_binding, dict):
            raise ContractError("inventory contract agents home binding is invalid")
        agents_home = _require_root(
            Path(str(agents_binding.get("path"))), label="contract agents home"
        )
        if (
            _root_identity(agents_home) != agents_binding
            or not _same_absolute_path(agents_home, home.parent / ".agents")
        ):
            raise ContractError("agents home identity differs from inventory contract")
        expected_relations = document.get("skill_junction_mirrors")
        if not isinstance(expected_relations, list):
            raise ContractError("inventory contract skill junction binding is invalid")
        _assert_skill_junction_mirrors(home, agents_home, expected_relations)
        root_bindings = document.get("roots")
        if not isinstance(root_bindings, dict) or set(root_bindings) != set(PRODUCTION_ROOTS):
            raise ContractError("inventory contract root bindings are invalid")
        roots: dict[str, Path] = {}
        for name, binding in root_bindings.items():
            if not isinstance(binding, dict):
                raise ContractError(f"inventory contract {name} root binding is invalid")
            root = _require_root(Path(str(binding.get("path"))), label=f"contract {name} root")
            _validate_git_root(root, namespace=f"repo-{name}")
            if _root_identity(root) != binding:
                raise ContractError(f"{name} root identity differs from inventory contract")
            roots[name] = root
        decision_binding = document.get("domain_decisions")
        if not isinstance(decision_binding, dict):
            raise ContractError("inventory contract decision binding is invalid")
        decision_path = Path(str(decision_binding.get("path")))
        decision_snapshot = _snapshot_file(
            _require_root(decision_path, label="contract decision file", directory=False)
        )
        if decision_snapshot.sha256 != decision_binding.get("sha256"):
            raise ContractError("domain decision file differs from inventory contract")
        tool = document.get("tool")
        if not isinstance(tool, dict) or not isinstance(tool.get("files"), list):
            raise ContractError("inventory contract tool binding is invalid")
        tool_repo = _require_root(Path(str(tool.get("repo_root"))), label="contract tool repository")
        _validate_git_root(tool_repo, namespace="tool-repository")
        if document.get("production") and os.path.normcase(os.fspath(tool_repo)) != os.path.normcase(
            os.fspath(roots["mercury"])
        ):
            raise ContractError("production tool repository differs from Mercury root")
        if _git_output(tool_repo, "rev-parse", "HEAD") != tool.get("commit"):
            raise ContractError("tool commit differs from inventory contract")
        for item in tool["files"]:
            if not isinstance(item, dict):
                raise ContractError("inventory contract tool file binding is invalid")
            relative = str(item.get("path"))
            tool_path = tool_repo / Path(relative)
            tool_snapshot = _snapshot_file(tool_path, approved_root=tool_repo)
            blob = _git_output(tool_repo, "hash-object", "--", relative)
            if tool_snapshot.sha256 != item.get("sha256") or blob != item.get("blob"):
                raise ContractError(f"tool file differs from inventory contract: {relative}")
            if document.get("production"):
                listing = _git_output(tool_repo, "ls-tree", str(tool["commit"]), "--", relative)
                if not listing or listing.split()[2] != blob:
                    raise ContractError(f"tool file is not bound to contract commit: {relative}")
    return document


def collect_inventory(contract: InventoryContract) -> InventoryManifest:
    home, roots, _cutoff_ns, cutoff_iso = _validate_contract(contract)
    expected_counts = dict(contract.expected_counts or PRODUCTION_EXPECTED_COUNTS)
    if contract.production and expected_counts != PRODUCTION_EXPECTED_COUNTS:
        raise ContractError("production expected counts are immutable")
    if contract.require_stable_corpus and contract.domain_decisions is None:
        raise ContractError("frozen collection requires a domain decision file")
    decisions = _load_decisions(contract.domain_decisions)
    repo_roots = {name: root for name, root in roots.items()}
    agents_home, skill_junction_mirrors = _contract_skill_junction_mirrors(
        home, contract
    )
    state_before = (
        _capture_corpus_state(home, roots, contract)
        if contract.require_stable_corpus
        else None
    )
    groups: list[list[AssetRecord]] = [
        inventory_claude_chats(
            home / "projects",
            contract.cutoff,
            decisions=decisions,
            expected_count=expected_counts["chat"],
            as_of=contract.as_of,
        ),
        _inventory_approved_memories(
            home / "projects", decisions=decisions, expected_counts=expected_counts
        ),
        inventory_claude_settings(home, repo_roots, decisions=decisions),
        inventory_claude_instructions(home, repo_roots, decisions=decisions),
        inventory_claude_hooks(home, repo_roots, decisions=decisions),
        inventory_claude_commands(home, repo_roots, decisions=decisions),
        inventory_claude_skills(
            home,
            repo_roots,
            decisions=decisions,
            skill_junction_mirrors=skill_junction_mirrors,
        ),
        inventory_claude_agents(home, repo_roots, decisions=decisions),
        inventory_claude_workflows(home, repo_roots, decisions=decisions),
        inventory_claude_attachments(home, repo_roots, decisions=decisions),
        inventory_claude_backups(home, repo_roots, decisions=decisions),
    ]
    for name, root in roots.items():
        groups.append(
            inventory_repo_dirty(
                root,
                cutoff=None,
                source_namespace=f"repo-{name}",
                decisions=decisions,
            )
        )
    records = _merge_records(groups)
    if contract.require_stable_corpus:
        state_after = _capture_corpus_state(home, roots, contract)
        if state_before != state_after:
            raise SourceChangedError("inventory corpus changed during collection")
    decisions.assert_all_used()
    actual_counts = dict(sorted(Counter(record.kind for record in records).items()))
    for kind, expected in expected_counts.items():
        if actual_counts.get(kind, 0) != expected:
            raise ContractError(
                f"{kind} count mismatch: expected {expected}, observed {actual_counts.get(kind, 0)}"
            )
    payload = _records_payload(records)
    tool_path = Path(__file__).absolute()
    tool_sha256 = contract.tool_sha256 or _snapshot_file(tool_path).sha256
    if not re.fullmatch(r"[0-9a-f]{64}", tool_sha256):
        raise ContractError("tool_sha256 must be lowercase SHA-256")
    if contract.tool_commit:
        tool_commit = contract.tool_commit
    else:
        completed = subprocess.run(
            ["git", "-C", os.fspath(roots["mercury"]), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        tool_commit = completed.stdout.strip()
    metadata = {
        "record_type": "inventory-metadata",
        "schema_version": 1,
        "production": contract.production,
        "cutoff": cutoff_iso,
        "tool": dict(contract.frozen_tool) if contract.frozen_tool else {"commit": tool_commit, "sha256": tool_sha256},
        "roots": {name: _root_identity(root) for name, root in sorted(roots.items())},
        "expected_counts": dict(sorted(expected_counts.items())),
        "actual_counts": actual_counts,
        "record_count": len(records),
        "records_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "domain_decisions_sha256": decisions.sha256,
        "unresolved_count": sum(record.disposition is None for record in records),
        "skill_junction_mirrors": skill_junction_mirrors,
        "skill_junction_mirror_count": len(skill_junction_mirrors),
        "skill_junction_mirrors_sha256": _skill_junction_mirrors_sha256(
            skill_junction_mirrors
        ),
    }
    if agents_home is not None:
        metadata["agents_home"] = _root_identity(agents_home)
    if contract.as_of is not None:
        metadata["window"] = {
            "start": cutoff_iso,
            "as_of": _normalize_cutoff(contract.as_of)[1],
        }
    if contract.frozen_contract_sha256:
        metadata["contract_sha256"] = contract.frozen_contract_sha256
        metadata["claude_home"] = _root_identity(home)
        metadata["chat_sessions"] = {
            group: sorted(values)
            for group, values in (contract.chat_sessions or {}).items()
        }
        metadata["corpus_fingerprint_sha256"] = hashlib.sha256(
            _canonical_json(state_after).encode("utf-8")
        ).hexdigest()
    return InventoryManifest(metadata=metadata, records=tuple(records))


def manifest_to_jsonl(manifest: InventoryManifest) -> str:
    header = json.dumps(
        manifest.metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return header + "\n" + _records_payload(manifest.records)


def _publish_no_overwrite(temporary: Path, output: Path) -> None:
    if os.name == "nt":
        os.rename(temporary, output)
    else:
        os.link(temporary, output)
        os.unlink(temporary)


def _write_new(path: Path, content: str) -> None:
    output = _absolute_without_resolve(path, label="output")
    parent = _require_root(output.parent, label="output parent")
    _assert_not_reparse(parent)
    if os.path.lexists(output):
        raise FileExistsError(f"output already exists: {output}")
    temporary = parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _publish_no_overwrite(temporary, output)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _sealed_manifest(manifest: InventoryManifest) -> InventoryManifest:
    metadata = dict(manifest.metadata)
    metadata.pop("sealed_digest", None)
    payload = _records_payload(manifest.records)
    metadata["sealed_digest"] = hashlib.sha256(
        (_canonical_json(metadata) + "\n" + payload).encode("utf-8")
    ).hexdigest()
    return InventoryManifest(metadata=metadata, records=manifest.records)


def _runtime_contract_from_document(
    document: Mapping[str, object], contract_sha256: str
) -> InventoryContract:
    roots = {
        name: Path(binding["path"])
        for name, binding in document["roots"].items()
    }
    tool = document["tool"]
    inventory_tool = next(
        item for item in tool["files"] if str(item["path"]).endswith("inventory.py")
    )
    return InventoryContract(
        claude_home=Path(document["claude_home"]["path"]),
        mercury_root=roots["mercury"],
        godot_root=roots["godot"],
        design_root=roots["design"],
        kb_root=roots["kb"],
        cutoff=document["window"]["start"],
        as_of=document["window"]["as_of"],
        domain_decisions=Path(document["domain_decisions"]["path"]),
        expected_counts=document["expected"]["counts"],
        production=bool(document["production"]),
        tool_commit=str(tool["commit"]),
        tool_sha256=str(inventory_tool["sha256"]),
        chat_projects=document["chat_projects"],
        chat_sessions=document["chat_sessions"],
        require_stable_corpus=True,
        frozen_contract_sha256=contract_sha256,
        frozen_tool=tool,
        agents_home=Path(document["agents_home"]["path"]),
        skill_junction_mirrors=document["skill_junction_mirrors"],
    )


def _reverify_contract_protection(document: Mapping[str, object]) -> None:
    if not document.get("production"):
        return
    frozen_tool = document.get("tool")
    if not isinstance(frozen_tool, dict):
        raise ContractError("inventory contract tool binding is invalid")
    observed = _verify_protected_root(
        PRODUCTION_OUTPUT_ROOT, frozen_tool=frozen_tool
    )
    if observed != document.get("protected_root"):
        raise ContractError(
            "protected root identity, owner, ACL, or EFS differs from inventory contract"
        )


def collect_frozen_inventory(
    contract_path: Path,
    contract_sha256: str,
    output_path: Path,
) -> InventoryManifest:
    document = load_inventory_contract(
        contract_path, contract_sha256, verify_bindings=True
    )
    output = _absolute_without_resolve(output_path, label="manifest output")
    output_binding = document.get("output")
    if not isinstance(output_binding, dict):
        raise ContractError("inventory contract output binding is invalid")
    if output.as_posix().casefold() != str(output_binding.get("manifest", "")).casefold():
        raise ContractError("manifest output differs from inventory contract")
    metadata_path = _absolute_without_resolve(
        Path(str(output_binding.get("metadata"))), label="metadata output"
    )
    if os.path.lexists(output) or os.path.lexists(metadata_path):
        raise FileExistsError("frozen inventory output already exists")
    contract = _runtime_contract_from_document(document, contract_sha256)
    manifest = collect_inventory(contract)
    _validate_approved_record_membership(manifest.records, document)
    if manifest.metadata["unresolved_count"]:
        raise ContractError("frozen inventory has unresolved domain decisions")
    if load_inventory_contract(
        contract_path, contract_sha256, verify_bindings=True
    ) != document:
        raise SourceChangedError("inventory contract bindings changed during collection")
    metadata = dict(manifest.metadata)
    metadata["schema_version"] = 3
    metadata["contract_payload_sha256"] = document["contract_payload_sha256"]
    metadata["approved_members_sha256"] = document["approved_members_sha256"]
    metadata["approved_member_count"] = document["approved_member_count"]
    if document.get("protected_root") is not None:
        metadata["protected_root"] = document["protected_root"]
    metadata["output"] = dict(output_binding)
    sealed = _sealed_manifest(InventoryManifest(metadata, manifest.records))
    metadata_content = _canonical_json(sealed.metadata) + "\n"
    manifest_content = manifest_to_jsonl(sealed)
    metadata_created = False
    try:
        _reverify_contract_protection(document)
        _write_new(metadata_path, metadata_content)
        metadata_created = True
        _reverify_contract_protection(document)
        _write_new(output, manifest_content)
    except Exception:
        if metadata_created and os.path.lexists(metadata_path):
            os.unlink(metadata_path)
        raise
    return sealed


def _parse_manifest(path: Path) -> tuple[dict, list[AssetRecord]]:
    source = _require_root(path, label="inventory input", directory=False)
    snapshot = _snapshot_file(source, capture_limit=-1)
    try:
        lines = snapshot.captured.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ManifestError("inventory is not valid UTF-8") from error
    if not lines:
        raise ManifestError("inventory is empty")
    try:
        metadata = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ManifestError("inventory metadata header is invalid") from error
    if not isinstance(metadata, dict) or metadata.get("record_type") != "inventory-metadata":
        raise ManifestError("inventory metadata header is missing")
    records: list[AssetRecord] = []
    for line_number, line in enumerate(lines[1:], start=2):
        try:
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError("record is not an object")
            if isinstance(value.get("observed_mirrors"), list):
                value["observed_mirrors"] = tuple(value["observed_mirrors"])
            records.append(AssetRecord(**value))
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ManifestError(f"invalid inventory record at line {line_number}") from error
    return metadata, records


def _joined_source(root: Path, key: str) -> str:
    return root.joinpath(*PurePosixPath(key).parts).as_posix()


def _validate_approved_record_membership(
    records: Sequence[AssetRecord], document: Mapping[str, object]
) -> None:
    expected = document.get("approved_members")
    actual = _approved_members(records)
    if actual != expected:
        raise ManifestError("inventory records differ from the frozen approved corpus members")
    if (
        len(actual) != document.get("approved_member_count")
        or _approved_members_sha256(actual) != document.get("approved_members_sha256")
    ):
        raise ManifestError("inventory approved corpus member digest is invalid")


def _validate_frozen_records(records: Sequence[AssetRecord], document: Mapping[str, object]) -> None:
    _validate_approved_record_membership(records, document)
    home = Path(document["claude_home"]["path"])
    agents_home = Path(document["agents_home"]["path"])
    roots = {
        name: Path(binding["path"])
        for name, binding in document["roots"].items()
    }
    chat_projects = document["chat_projects"]
    chat_sessions = document["chat_sessions"]
    session_groups = {
        session: group
        for group, sessions in chat_sessions.items()
        for session in sessions
    }
    user_kinds = {
        "claude-user-setting": ("setting", "settings"),
        "claude-user-instruction": ("instruction", "instructions"),
        "claude-user-hook": ("hook", "hooks"),
        "claude-user-command": ("command", "commands"),
        "claude-user-skill": ("skill", "skills"),
        "claude-user-agent": ("agent", "agents"),
        "claude-user-workflow": ("workflow", "workflows"),
        "claude-user-attachment": ("attachment", "attachments"),
        "claude-user-backup": ("backup", "backups"),
    }
    target_members: dict[str, tuple[Mapping[str, object], Mapping[str, object]]] = {}
    for relation in document["skill_junction_mirrors"]:
        for member in relation["target_members"]:
            target_members[canonical_identity_key(str(member["canonical_key"]))] = (
                relation,
                member,
            )
    by_id = {record.asset_id: record for record in records}
    for record in records:
        expected_source: str
        if record.source_namespace == "claude-chat":
            if record.kind != "chat" or record.session_id != record.canonical_key:
                raise ManifestError("chat identity fields violate the frozen contract")
            try:
                uuid.UUID(record.session_id or "")
            except ValueError as error:
                raise ManifestError("chat session ID is not a UUID") from error
            if (
                record.session_id not in session_groups
                or not record.source_cwd
                or not re.fullmatch(r"[0-9a-f]{64}", record.first_user_request_sha256 or "")
            ):
                raise ManifestError("chat is missing frozen session/cwd/request metadata")
            group = session_groups[record.session_id]
            expected_source = (
                home
                / "projects"
                / chat_projects[group]
                / f"{record.session_id}.jsonl"
            ).as_posix()
        elif record.source_namespace in {"claude-memory", "claude-memory-archive"}:
            expected_kind = (
                {"memory", "memory-auxiliary"}
                if record.source_namespace == "claude-memory"
                else {"memory-archive"}
            )
            if record.kind not in expected_kind:
                raise ManifestError("memory kind violates its namespace")
            parts = PurePosixPath(record.canonical_key).parts
            if len(parts) < 2:
                raise ManifestError("memory canonical key is incomplete")
            project = (
                "D--Mercury-stock-agent-candidates-TradingAgents"
                if record.source_namespace == "claude-memory-archive"
                else parts[0]
            )
            relative = parts[1:] if record.source_namespace == "claude-memory" else parts[1:]
            expected_source = (home / "projects" / project / "memory" / Path(*relative)).as_posix()
        elif record.source_namespace == "claude-user-skill-target":
            if record.kind != "skill":
                raise ManifestError("skill target member kind violates its namespace")
            bound = target_members.get(canonical_identity_key(record.canonical_key))
            if bound is None:
                raise ManifestError("skill target member is absent from the frozen relation")
            relation, member = bound
            expected_source = _joined_source(
                agents_home / "skills", record.canonical_key
            )
            if (
                record.source != member.get("source")
                or record.sha256 != member.get("sha256")
                or record.size != member.get("size")
                or record.mtime_ns != member.get("mtime_ns")
                or record.file_id != member.get("file_id")
            ):
                raise ManifestError("skill target member differs from its frozen relation")
            relative = PurePosixPath(record.canonical_key).parts[1:]
            expected_observed_mirror = (
                Path(str(relation["link_path"])) / Path(*relative)
            ).as_posix()
            if record.observed_mirrors != (expected_observed_mirror,):
                raise ManifestError("skill junction observed relation is not canonical")
        elif record.source_namespace in user_kinds:
            expected_kind, directory = user_kinds[record.source_namespace]
            if record.kind != expected_kind:
                raise ManifestError("Claude user asset kind violates its namespace")
            if record.source_namespace == "claude-user-setting":
                expected_source = (
                    (home.parent / ".claude.json").as_posix()
                    if record.canonical_key == ".claude.json"
                    else _joined_source(home, record.canonical_key)
                )
            elif record.source_namespace == "claude-user-instruction":
                expected_source = _joined_source(home, record.canonical_key)
            else:
                expected_source = _joined_source(home / directory, record.canonical_key)
        else:
            mirror_match = re.fullmatch(
                r"repo-(mercury|godot|design|kb)-skill-mirror", record.source_namespace
            )
            repo_match = re.fullmatch(r"repo-(mercury|godot|design|kb)", record.source_namespace)
            if mirror_match:
                root_name = mirror_match.group(1)
                if (
                    record.kind != "skill-mirror"
                    or not record.canonical_key.startswith(".claude/skills/")
                    or not record.mirror_of
                    or record.disposition != "exclude-domain"
                ):
                    raise ManifestError("skill mirror relation violates the frozen contract")
            elif repo_match:
                root_name = repo_match.group(1)
                if record.kind != _kind_for_path(Path(record.canonical_key)):
                    raise ManifestError("repository asset kind violates its canonical key")
            else:
                raise ManifestError(f"namespace is not allowed by frozen contract: {record.source_namespace}")
            expected_source = _joined_source(roots[root_name], record.canonical_key)
        if record.source != expected_source:
            raise ManifestError("record source is outside or noncanonical for its namespace/key")
        if record.kind != "chat" and any(
            value is not None
            for value in (
                record.session_id,
                record.source_cwd,
                record.first_user_request_sha256,
            )
        ):
            raise ManifestError("non-chat record contains chat-only metadata")
        if record.dirty_state is not None and not record.source_namespace.startswith("repo-"):
            raise ManifestError("dirty state is valid only for repository assets")
        if record.mirror_of is not None:
            canonical = by_id.get(record.mirror_of)
            if (
                canonical is None
                or canonical.kind != "skill"
                or canonical.source_namespace + "-skill-mirror" != record.source_namespace
            ):
                raise ManifestError("skill mirror points to an invalid canonical asset")
        if record.observed_mirrors:
            if record.source_namespace == "claude-user-skill-target":
                if record.observed_mirrors != (expected_observed_mirror,):
                    raise ManifestError("skill junction observed relation is not canonical")
            elif record.kind != "skill" or not record.source_namespace.startswith("repo-"):
                raise ManifestError("observed mirror relation is attached to an invalid asset")
            else:
                root_name = record.source_namespace.removeprefix("repo-")
                mirror_key = record.canonical_key.replace(".agents/skills/", ".claude/skills/", 1)
                if record.observed_mirrors != (_joined_source(roots[root_name], mirror_key),):
                    raise ManifestError("observed mirror relation is not canonical")


def summarize_manifest(
    path: Path,
    *,
    contract_path: Path | None = None,
    contract_sha256: str | None = None,
) -> dict:
    if (contract_path is None) != (contract_sha256 is None):
        raise ManifestError("contract path and SHA-256 must be supplied together")
    document = (
        load_inventory_contract(contract_path, contract_sha256, verify_bindings=True)
        if contract_path is not None and contract_sha256 is not None
        else None
    )
    metadata, records = _parse_manifest(path)
    if metadata.get("record_type") != "inventory-metadata":
        raise ManifestError("manifest header record_type is invalid")
    expected_schema = 3 if document is not None else 1
    if type(metadata.get("schema_version")) is not int or metadata["schema_version"] != expected_schema:
        raise ManifestError("manifest schema_version is unsupported")
    if type(metadata.get("production")) is not bool:
        raise ManifestError("manifest production flag is invalid")
    cutoff = metadata.get("cutoff")
    try:
        _, canonical_cutoff = _normalize_cutoff(cutoff)
    except ContractError as error:
        raise ManifestError("metadata cutoff is invalid") from error
    if cutoff != canonical_cutoff:
        raise ManifestError("metadata cutoff is not canonical UTC")
    decision_hash = metadata.get("domain_decisions_sha256")
    if decision_hash is not None and not (
        isinstance(decision_hash, str) and re.fullmatch(r"[0-9a-f]{64}", decision_hash)
    ):
        raise ManifestError("metadata domain decision binding is invalid")
    if records != sorted(records, key=_record_sort_key):
        raise ManifestError("inventory records are not in canonical order")
    seen_sources: set[str] = set()
    seen_logical: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    for record in records:
        source = record.source.casefold()
        logical = (
            record.source_namespace.casefold(),
            canonical_identity_key(record.canonical_key),
        )
        if source in seen_sources:
            raise ManifestError(f"duplicate source provenance: {record.source}")
        if logical in seen_logical:
            raise ManifestError(
                f"duplicate namespace/canonical key: {record.source_namespace}:{record.canonical_key}"
            )
        if record.asset_id in seen_ids:
            raise ManifestError(f"duplicate asset_id: {record.asset_id}")
        seen_sources.add(source)
        seen_logical.add(logical)
        seen_ids.add(record.asset_id)
    payload = _records_payload(records)
    if metadata.get("records_sha256") != hashlib.sha256(payload.encode("utf-8")).hexdigest():
        raise ManifestError("records_sha256 does not match canonical records")
    actual_counts = dict(sorted(Counter(record.kind for record in records).items()))
    if metadata.get("actual_counts") != actual_counts:
        raise ManifestError("metadata actual_counts do not match records")
    if metadata.get("record_count") != len(records):
        raise ManifestError("metadata record_count does not match records")
    expected_counts = metadata.get("expected_counts")
    if not isinstance(expected_counts, dict):
        raise ManifestError("metadata expected_counts are missing")
    if metadata.get("production") and expected_counts != PRODUCTION_EXPECTED_COUNTS:
        raise ManifestError("production expected counts are not canonical")
    for kind, expected in expected_counts.items():
        if actual_counts.get(kind, 0) != expected:
            raise ManifestError(
                f"{kind} count mismatch: expected {expected}, observed {actual_counts.get(kind, 0)}"
            )
    roots = metadata.get("roots")
    if not isinstance(roots, dict) or set(roots) != set(PRODUCTION_ROOTS):
        raise ManifestError("metadata must bind all four root identities")
    root_file_ids: set[str] = set()
    for name, identity in roots.items():
        if not isinstance(identity, dict):
            raise ManifestError(f"invalid root identity metadata for {name}")
        root_path = identity.get("path")
        file_id = identity.get("file_id")
        absolute_path = isinstance(root_path, str) and (
            Path(root_path).is_absolute()
            or bool(re.match(r"^[A-Za-z]:/", root_path))
        )
        if (
            not absolute_path
            or "\\" in root_path
            or not isinstance(file_id, str)
            or not file_id
            or any(character.isspace() for character in file_id)
        ):
            raise ManifestError(f"invalid root identity metadata for {name}")
        if file_id in root_file_ids:
            raise ManifestError("root identity metadata contains duplicate file IDs")
        root_file_ids.add(file_id)
        if metadata.get("production") and document is None:
            expected_path = _absolute_without_resolve(
                PRODUCTION_ROOTS[name], label=f"expected {name} root"
            ).as_posix()
            if root_path.casefold() != expected_path.casefold():
                raise ManifestError(f"production root identity path mismatch for {name}")
    tool = metadata.get("tool")
    commit = tool.get("commit") if isinstance(tool, dict) else None
    if document is None:
        if (
            not isinstance(commit, str)
            or not commit
            or not re.fullmatch(r"[0-9a-f]{64}", str(tool.get("sha256", "")))
            or (metadata["production"] and not re.fullmatch(r"[0-9a-f]{40,64}", commit))
        ):
            raise ManifestError("metadata tool binding is invalid")
    unresolved = [record for record in records if record.disposition is None]
    if metadata.get("unresolved_count") != len(unresolved):
        raise ManifestError("metadata unresolved_count does not match records")
    if document is not None:
        input_path = _absolute_without_resolve(path, label="inventory input").as_posix()
        output_binding = document["output"]
        if input_path.casefold() != str(output_binding["manifest"]).casefold():
            raise ManifestError("manifest input differs from frozen contract")
        expected_metadata = {
            "production": document["production"],
            "contract_sha256": contract_sha256,
            "contract_payload_sha256": document["contract_payload_sha256"],
            "window": document["window"],
            "claude_home": document["claude_home"],
            "agents_home": document["agents_home"],
            "roots": document["roots"],
            "tool": document["tool"],
            "expected_counts": document["expected"]["counts"],
            "chat_sessions": document["chat_sessions"],
            "domain_decisions_sha256": document["domain_decisions"]["sha256"],
            "approved_members_sha256": document["approved_members_sha256"],
            "approved_member_count": document["approved_member_count"],
            "skill_junction_mirrors": document["skill_junction_mirrors"],
            "skill_junction_mirror_count": document["skill_junction_mirror_count"],
            "skill_junction_mirrors_sha256": document["skill_junction_mirrors_sha256"],
            "output": document["output"],
        }
        if document.get("protected_root") is not None:
            expected_metadata["protected_root"] = document["protected_root"]
        expected_metadata_fields = set(expected_metadata) | {
            "record_type",
            "schema_version",
            "cutoff",
            "actual_counts",
            "record_count",
            "records_sha256",
            "unresolved_count",
            "corpus_fingerprint_sha256",
            "sealed_digest",
        }
        if set(metadata) != expected_metadata_fields:
            raise ManifestError("formal manifest metadata fields do not match schema")
        for field, expected in expected_metadata.items():
            if metadata.get(field) != expected:
                qualifier = " relation member binding" if field.startswith("skill_junction_") else ""
                raise ManifestError(
                    f"manifest metadata{qualifier} differs from frozen contract: {field}"
                )
        sealed_digest = metadata.get("sealed_digest")
        unsealed = dict(metadata)
        unsealed.pop("sealed_digest", None)
        expected_seal = hashlib.sha256(
            (_canonical_json(unsealed) + "\n" + payload).encode("utf-8")
        ).hexdigest()
        if sealed_digest != expected_seal:
            raise ManifestError("manifest sealed digest is invalid")
        metadata_path = Path(str(document["output"]["metadata"]))
        metadata_snapshot = _snapshot_file(
            _require_root(metadata_path, label="inventory metadata", directory=False),
            capture_limit=-1,
        )
        try:
            sidecar = json.loads(metadata_snapshot.captured.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ManifestError("inventory metadata sidecar is invalid") from error
        if sidecar != metadata:
            raise ManifestError("inventory metadata sidecar differs from manifest header")
        _validate_frozen_records(records, document)
        runtime = _runtime_contract_from_document(document, contract_sha256)
        current_state = _capture_corpus_state(runtime.claude_home, runtime.roots(), runtime)
        current_fingerprint = hashlib.sha256(
            _canonical_json(current_state).encode("utf-8")
        ).hexdigest()
        if metadata.get("corpus_fingerprint_sha256") != current_fingerprint:
            raise ManifestError("current corpus differs from sealed manifest")
    return {
        "actual_counts": actual_counts,
        "by_disposition": dict(
            sorted(Counter(record.disposition or "unresolved" for record in records).items())
        ),
        "by_status": dict(sorted(Counter(record.disposition_status for record in records).items())),
        "total": len(records),
        "unresolved": len(unresolved),
    }


def _collect(args: argparse.Namespace) -> int:
    manifest = collect_frozen_inventory(
        Path(args.contract), args.contract_sha256, Path(args.output)
    )
    print(
        _canonical_json(
            {"output": Path(args.output).absolute().as_posix(), "total": len(manifest.records)},
        )
    )
    return 0


def _summarize(args: argparse.Namespace) -> int:
    summary = summarize_manifest(
        Path(args.input),
        contract_path=Path(args.contract),
        contract_sha256=args.contract_sha256,
    )
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 2 if summary["unresolved"] else 0


def _freeze_contract(args: argparse.Namespace) -> int:
    return freeze_contract_from_args(args)


def freeze_contract_from_args(args: argparse.Namespace) -> int:
    chat_projects = dict(PRODUCTION_CHAT_PROJECTS)
    if args.fixture:
        chat_projects["home"] = args.home_chat_project
    contract_sha256 = freeze_inventory_contract(
        contract_path=Path(args.contract),
        output_path=Path(args.output),
        metadata_path=Path(args.metadata),
        claude_home=Path(args.claude_home),
        roots={
            "mercury": Path(args.mercury_root),
            "godot": Path(args.godot_root),
            "design": Path(args.design_root),
            "kb": Path(args.kb_root),
        },
        domain_decisions=Path(args.domain_decisions),
        chat_projects=chat_projects,
        production=not args.fixture,
    )
    print(
        _canonical_json(
            {
                "contract": Path(args.contract).absolute().as_posix(),
                "contract_sha256": contract_sha256,
            }
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a deterministic Import inventory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser(
        "freeze-contract", help="atomically freeze reviewed inventory bindings"
    )
    freeze.add_argument("--contract", required=True)
    freeze.add_argument("--output", required=True)
    freeze.add_argument("--metadata", required=True)
    freeze.add_argument("--domain-decisions", required=True)
    freeze.add_argument("--claude-home", required=True)
    freeze.add_argument("--mercury-root", required=True)
    freeze.add_argument("--godot-root", required=True)
    freeze.add_argument("--design-root", required=True)
    freeze.add_argument("--kb-root", required=True)
    freeze.add_argument("--fixture", action="store_true", help=argparse.SUPPRESS)
    freeze.add_argument(
        "--home-chat-project",
        default=PRODUCTION_CHAT_PROJECTS["home"],
        help=argparse.SUPPRESS,
    )
    freeze.set_defaults(handler=_freeze_contract)
    collect = subparsers.add_parser("collect", help="write an atomic verified asset manifest")
    collect.add_argument("--output", required=True)
    collect.add_argument("--contract", required=True)
    collect.add_argument("--contract-sha256", required=True)
    collect.set_defaults(handler=_collect)
    summarize = subparsers.add_parser("summarize", help="validate and summarize a manifest")
    summarize.add_argument("--input", required=True)
    summarize.add_argument("--contract", required=True)
    summarize.add_argument("--contract-sha256", required=True)
    summarize.set_defaults(handler=_summarize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return args.handler(args)
    except InventoryError as error:
        print(
            json.dumps(
                {"error": error.__class__.__name__, "message": str(error)},
                separators=(",", ":"),
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
