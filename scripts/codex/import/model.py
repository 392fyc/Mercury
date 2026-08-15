from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
from pathlib import PurePosixPath, PureWindowsPath
import re
from typing import Any


VALID_DISPOSITIONS = frozenset({"import", "exclude-secret", "exclude-domain"})
VALID_DISPOSITION_STATUSES = frozenset({"provisional", "domain-decided", "unresolved"})
VALID_DOMAINS = frozenset({"mercury-sot", "other", "review-required"})
VALID_DIRTY_STATES = frozenset(
    {"tracked-modified", "tracked-added", "renamed", "untracked"}
)
VALID_KINDS = frozenset(
    {
        "agent",
        "attachment",
        "backup",
        "chat",
        "command",
        "file",
        "hook",
        "instruction",
        "memory",
        "memory-archive",
        "memory-auxiliary",
        "setting",
        "skill",
        "skill-mirror",
        "workflow",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]*$")


def canonical_identity_key(value: str) -> str:
    return value.replace("\\", "/").casefold()


def compute_asset_id(source_namespace: str, canonical_key: str, sha256: str) -> str:
    identity = json.dumps(
        {
            "canonical_key": canonical_identity_key(canonical_key),
            "sha256": sha256,
            "source_namespace": source_namespace.casefold(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "asset-" + hashlib.sha256(identity).hexdigest()


def _is_absolute_provenance(source: str) -> bool:
    return PureWindowsPath(source).is_absolute() or PurePosixPath(source).is_absolute()


@dataclass(frozen=True, slots=True)
class AssetRecord:
    asset_id: str
    source: str
    source_namespace: str
    canonical_key: str
    kind: str
    size: int
    sha256: str
    mtime_ns: int
    file_id: str
    domain: str
    domain_reason: str
    disposition: str | None
    disposition_status: str
    decision_evidence: str | None = None
    dirty_state: str | None = None
    session_id: str | None = None
    source_cwd: str | None = None
    first_user_request_sha256: str | None = None
    mirror_of: str | None = None
    observed_mirrors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        source_parts = self.source.split("/")
        if (
            "\\" in self.source
            or not _is_absolute_provenance(self.source)
            or "//" in self.source
            or any(part in {".", ".."} for part in source_parts)
        ):
            raise ValueError("source must be an absolute POSIX-normalized provenance path")
        if not NAMESPACE_RE.fullmatch(self.source_namespace):
            raise ValueError(f"invalid source namespace: {self.source_namespace!r}")
        key = PurePosixPath(self.canonical_key)
        if (
            not self.canonical_key
            or "\\" in self.canonical_key
            or "//" in self.canonical_key
            or key.is_absolute()
            or any(part in {"", ".", ".."} for part in self.canonical_key.split("/"))
        ):
            raise ValueError("canonical_key must be a normalized relative POSIX path")
        if self.kind not in VALID_KINDS:
            raise ValueError(f"invalid kind: {self.kind!r}")
        if self.size < 0:
            raise ValueError("size must not be negative")
        if not SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        if self.mtime_ns < 0:
            raise ValueError("mtime_ns must not be negative")
        if not self.file_id or any(character.isspace() for character in self.file_id):
            raise ValueError("file_id must be a nonempty value-free identity")
        if self.domain not in VALID_DOMAINS:
            raise ValueError(f"invalid domain: {self.domain!r}")
        if not self.domain_reason or "\n" in self.domain_reason:
            raise ValueError("domain_reason must be a nonempty single line")
        if self.disposition_status not in VALID_DISPOSITION_STATUSES:
            raise ValueError(
                f"invalid disposition_status: {self.disposition_status!r}"
            )
        if self.disposition_status == "unresolved":
            if self.disposition is not None:
                raise ValueError("unresolved record must not have a disposition")
        elif self.disposition not in VALID_DISPOSITIONS:
            raise ValueError(f"invalid disposition: {self.disposition!r}")
        if self.disposition_status == "domain-decided" and not self.decision_evidence:
            raise ValueError("domain-decided record requires decision evidence")
        if self.decision_evidence is not None and (
            not self.decision_evidence or "\n" in self.decision_evidence
        ):
            raise ValueError("decision_evidence must be a nonempty single line")
        if self.dirty_state is not None and self.dirty_state not in VALID_DIRTY_STATES:
            raise ValueError(f"invalid dirty_state: {self.dirty_state!r}")
        if self.session_id is not None:
            if self.kind != "chat":
                raise ValueError("session metadata is valid only for chat records")
            if not self.source_cwd or not SHA256_RE.fullmatch(
                self.first_user_request_sha256 or ""
            ):
                raise ValueError("chat records require cwd and first-user-request hash")
        if self.mirror_of is not None:
            if self.kind != "skill-mirror" or not re.fullmatch(
                r"asset-[0-9a-f]{64}", self.mirror_of
            ):
                raise ValueError("mirror_of is valid only for skill-mirror asset IDs")
        if not isinstance(self.observed_mirrors, tuple):
            raise ValueError("observed_mirrors must be a canonical tuple")
        if tuple(sorted(set(self.observed_mirrors), key=str.casefold)) != self.observed_mirrors:
            raise ValueError("observed_mirrors must be unique and canonically sorted")
        for mirror in self.observed_mirrors:
            if (
                "\\" in mirror
                or not _is_absolute_provenance(mirror)
                or "//" in mirror
                or any(part in {".", ".."} for part in mirror.split("/"))
            ):
                raise ValueError("observed mirror must be an absolute canonical provenance")
        expected_id = compute_asset_id(
            self.source_namespace, self.canonical_key, self.sha256
        )
        if self.asset_id != expected_id:
            raise ValueError("asset_id does not match namespace/key/content identity")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["observed_mirrors"] = list(self.observed_mirrors)
        return value

    def with_dirty_state(self, dirty_state: str | None) -> "AssetRecord":
        return replace(self, dirty_state=dirty_state)


@dataclass(frozen=True, slots=True)
class InventoryManifest:
    metadata: dict[str, Any]
    records: tuple[AssetRecord, ...]
