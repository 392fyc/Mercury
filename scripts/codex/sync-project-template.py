#!/usr/bin/env python3
"""Install or verify Mercury's manifest-owned portable Codex project layer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Sequence


MANIFEST_GIT_PATH = PurePosixPath(
    ".mercury/templates/codex-project/manifest.json"
)
TEMPLATE_GIT_ROOT = MANIFEST_GIT_PATH.parent
SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-fA-F]{40}")
LOWER_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
SUPPORTED_DESTINATION_GROUPS = frozenset({"agents", "project", "rules"})
REPARSE_POINT_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
WINDOWS_RESERVED_BASENAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
    | {f"com{number}" for number in "¹²³"}
    | {f"lpt{number}" for number in "¹²³"}
)
WINDOWS_INVALID_CHARACTERS = frozenset('<>:"|?*')
GIT_ENVIRONMENT_ALLOWLIST = frozenset(
    {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "HOME",
        "USERPROFILE",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "PATHEXT",
        "COMSPEC",
    }
)
GIT_TIMEOUT_SECONDS = 30


class SyncError(Exception):
    """Raised when inputs are invalid or unsafe."""


@dataclass(frozen=True)
class TemplateFile:
    source: PurePosixPath
    destination: PurePosixPath
    content: bytes


@dataclass(frozen=True)
class Template:
    schema_version: int
    source_repo: str
    lock: PurePosixPath
    manifest_sha256: str
    files: tuple[TemplateFile, ...]


@dataclass(frozen=True)
class ExistingLock:
    schema_version: int
    source_repo: str
    source_commit: str
    manifest_sha256: str
    files: dict[str, str]
    canonical_bytes: bytes


@dataclass(frozen=True)
class FileState:
    exists: bool
    safe_regular: bool
    single_link: bool
    content: bytes | None
    st_dev: int | None
    st_ino: int | None
    st_size: int | None
    st_mtime_ns: int | None


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _portable_relative_path(value: Any, field: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise SyncError(f"{field} must be a non-empty POSIX path")
    if "\\" in value or ":" in value:
        raise SyncError(f"{field} must use a portable POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value != path.as_posix():
        raise SyncError(f"{field} must be a normalized POSIX relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise SyncError(f"{field} cannot contain empty, current, or parent segments")
    for part in path.parts:
        if part.endswith((".", " ")):
            raise SyncError(f"{field} cannot contain a segment ending in a dot or space")
        if any(character in WINDOWS_INVALID_CHARACTERS for character in part):
            raise SyncError(f"{field} contains a Windows-invalid character")
        if any(ord(character) < 32 or ord(character) == 127 for character in part):
            raise SyncError(f"{field} contains a control character")
        device_basename = part.split(".", 1)[0].casefold()
        if device_basename in WINDOWS_RESERVED_BASENAMES:
            raise SyncError(f"{field} contains a Windows reserved device name")
    return path


def _destination_path(value: Any, field: str) -> PurePosixPath:
    destination = _portable_relative_path(value, field)
    if len(destination.parts) < 2:
        raise SyncError(f"{field} must include a group directory")
    if destination.parts[0] not in SUPPORTED_DESTINATION_GROUPS:
        raise SyncError(f"{field} must be under agents, project, or rules")
    if not destination.name.startswith("mercury-"):
        raise SyncError(f"{field} basename must start with mercury-")
    return destination


def _lock_path(value: Any, field: str) -> PurePosixPath:
    lock = _portable_relative_path(value, field)
    if lock != PurePosixPath("mercury-template.lock"):
        raise SyncError(f"{field} must be exactly mercury-template.lock")
    return lock


def _portable_source_repo(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise SyncError(f"{field} must be a non-empty portable repository name")
    _portable_relative_path(value, field)
    return value


def _json_without_duplicate_keys(content: bytes, field: str) -> Any:
    def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SyncError(f"{field} contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(content, object_pairs_hook=pairs_hook)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SyncError(f"{field} is not valid UTF-8 JSON: {exc}") from exc


def _clean_git_environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if key.upper() in GIT_ENVIRONMENT_ALLOWLIST
    }


def _run_git(repository_root: Path, *arguments: str) -> bytes:
    failure_message: str | None = None
    result: subprocess.CompletedProcess[bytes] | None = None
    try:
        result = subprocess.run(
            [
                "git",
                "--no-replace-objects",
                "-C",
                str(repository_root),
                *arguments,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env=_clean_git_environment(),
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        failure_message = (
            f"git command timed out after {GIT_TIMEOUT_SECONDS} seconds"
        )
    except OSError:
        failure_message = "cannot start git command"
    if failure_message is not None:
        raise SyncError(failure_message) from None
    if result is None:
        raise SyncError("git command did not produce a result") from None
    if result.returncode != 0:
        raise SyncError(
            f"git command failed with exit code {result.returncode}"
        ) from None
    return result.stdout


def _validate_repository_root(repository_root: Path) -> None:
    output = _run_git(repository_root, "rev-parse", "--show-toplevel")
    try:
        discovered = Path(output.decode("utf-8").strip()).resolve(strict=True)
        expected = repository_root.resolve(strict=True)
    except (UnicodeDecodeError, OSError) as exc:
        raise SyncError(f"cannot validate the Mercury repository root: {exc}") from exc
    if os.path.normcase(str(discovered)) != os.path.normcase(str(expected)):
        raise SyncError("git show-toplevel does not match the sync tool repository")


def _validate_source_commit(repository_root: Path, source_commit: str) -> str:
    commit = source_commit.lower()
    try:
        object_type = _run_git(repository_root, "cat-file", "-t", commit).strip()
    except SyncError as exc:
        raise SyncError(
            f"--source-commit does not identify a commit in the Mercury repository: {commit}"
        ) from exc
    if object_type != b"commit":
        raise SyncError(
            f"--source-commit must identify a commit object, not {object_type.decode('ascii', errors='replace')}"
        )
    return commit


def _git_blob(repository_root: Path, source_commit: str, path: PurePosixPath) -> bytes:
    try:
        return _run_git(repository_root, "cat-file", "blob", f"{source_commit}:{path.as_posix()}")
    except SyncError as exc:
        raise SyncError(
            f"cannot read {path.as_posix()} from source commit {source_commit}"
        ) from exc


def _load_template(repository_root: Path, source_commit: str) -> Template:
    manifest_bytes = _git_blob(repository_root, source_commit, MANIFEST_GIT_PATH)
    manifest = _json_without_duplicate_keys(manifest_bytes, "template manifest")
    if not isinstance(manifest, dict):
        raise SyncError("template manifest must be a JSON object")
    if set(manifest) != {"schema_version", "source_repo", "lock", "files"}:
        raise SyncError(
            "template manifest must contain exactly schema_version, source_repo, lock, and files"
        )
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise SyncError("template manifest schema_version must be 1")
    source_repo = _portable_source_repo(manifest.get("source_repo"), "template manifest source_repo")
    lock = _lock_path(manifest.get("lock"), "template manifest lock")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise SyncError("template manifest files must be a non-empty list")

    parsed_entries: list[tuple[PurePosixPath, PurePosixPath]] = []
    for index, entry in enumerate(raw_files):
        if not isinstance(entry, dict) or set(entry) != {"source", "destination"}:
            raise SyncError(f"files[{index}] must contain exactly source and destination")
        source = _portable_relative_path(entry["source"], f"files[{index}].source")
        destination = _destination_path(entry["destination"], f"files[{index}].destination")
        parsed_entries.append((source, destination))

    destination_names = [destination.as_posix() for _, destination in parsed_entries]
    if destination_names != sorted(destination_names):
        raise SyncError("template manifest files must be sorted by destination")
    if len(destination_names) != len(set(destination_names)):
        raise SyncError("template manifest destinations must be unique")
    if len(destination_names) != len({name.casefold() for name in destination_names}):
        raise SyncError("template manifest destinations must be casefold-unique")
    source_names = [source.as_posix() for source, _ in parsed_entries]
    if len(source_names) != len(set(source_names)):
        raise SyncError("template manifest sources must be unique")
    if len(source_names) != len({name.casefold() for name in source_names}):
        raise SyncError("template manifest sources must be casefold-unique")

    files = tuple(
        TemplateFile(
            source=source,
            destination=destination,
            content=_git_blob(repository_root, source_commit, TEMPLATE_GIT_ROOT / source),
        )
        for source, destination in parsed_entries
    )
    return Template(
        schema_version=schema_version,
        source_repo=source_repo,
        lock=lock,
        manifest_sha256=_sha256(manifest_bytes),
        files=files,
    )


def _lock_bytes(template: Template, source_commit: str) -> bytes:
    lock = {
        "schema_version": template.schema_version,
        "source_repo": template.source_repo,
        "source_commit": source_commit,
        "manifest_sha256": template.manifest_sha256,
        "files": {
            item.destination.as_posix(): _sha256(item.content) for item in template.files
        },
    }
    return (json.dumps(lock, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _is_reparse_or_symlink(file_stat: os.stat_result) -> bool:
    attributes = getattr(file_stat, "st_file_attributes", 0)
    return stat.S_ISLNK(file_stat.st_mode) or bool(attributes & REPARSE_POINT_ATTRIBUTE)


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SyncError(f"cannot inspect {path}: {exc}") from exc


def _assert_target_chain_safe(target: Path) -> Path:
    absolute = Path(os.path.abspath(target))
    current = absolute
    while True:
        file_stat = _lstat(current)
        if file_stat is not None and _is_reparse_or_symlink(file_stat):
            raise SyncError(f"target or its parent is a symlink, junction, or reparse point: {current}")
        if current.parent == current:
            break
        current = current.parent
    target_stat = _lstat(absolute)
    if target_stat is None or not stat.S_ISDIR(target_stat.st_mode):
        raise SyncError("--target must name an existing directory")
    return absolute


def _assert_directory_chain_safe(root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(root)
    except ValueError as exc:
        raise SyncError("destination escapes the target .codex directory") from exc
    root_stat = _lstat(root)
    if root_stat is not None and (
        _is_reparse_or_symlink(root_stat) or not stat.S_ISDIR(root_stat.st_mode)
    ):
        raise SyncError(f"destination parent is not a safe directory: {root}")
    current = root
    for part in relative.parts:
        current = current / part
        file_stat = _lstat(current)
        if file_stat is None:
            continue
        if _is_reparse_or_symlink(file_stat) or not stat.S_ISDIR(file_stat.st_mode):
            raise SyncError(f"destination parent is not a safe directory: {current}")


def _codex_root(target: Path) -> Path:
    safe_target = _assert_target_chain_safe(target)
    codex_root = safe_target / ".codex"
    file_stat = _lstat(codex_root)
    if file_stat is not None and (
        _is_reparse_or_symlink(file_stat) or not stat.S_ISDIR(file_stat.st_mode)
    ):
        raise SyncError("target .codex path is not a safe directory")
    return codex_root


def _path_for_destination(codex_root: Path, destination: PurePosixPath) -> Path:
    path = codex_root.joinpath(*destination.parts)
    _assert_directory_chain_safe(codex_root, path.parent)
    return path


def _file_state(path: Path) -> FileState:
    file_stat = _lstat(path)
    if file_stat is None:
        return FileState(False, False, False, None, None, None, None, None)
    if _is_reparse_or_symlink(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        return FileState(
            True,
            False,
            file_stat.st_nlink == 1,
            None,
            file_stat.st_dev,
            file_stat.st_ino,
            file_stat.st_size,
            file_stat.st_mtime_ns,
        )
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise SyncError(f"cannot read destination {path}: {exc}") from exc
    after_read_stat = _lstat(path)
    if after_read_stat is None or _is_reparse_or_symlink(after_read_stat):
        raise SyncError(f"destination changed while it was inspected: {path}")
    before_identity = (
        file_stat.st_mode,
        file_stat.st_nlink,
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
    )
    after_identity = (
        after_read_stat.st_mode,
        after_read_stat.st_nlink,
        after_read_stat.st_dev,
        after_read_stat.st_ino,
        after_read_stat.st_size,
        after_read_stat.st_mtime_ns,
    )
    if before_identity != after_identity or not stat.S_ISREG(after_read_stat.st_mode):
        raise SyncError(f"destination changed while it was inspected: {path}")
    return FileState(
        True,
        True,
        after_read_stat.st_nlink == 1,
        content,
        after_read_stat.st_dev,
        after_read_stat.st_ino,
        after_read_stat.st_size,
        after_read_stat.st_mtime_ns,
    )


def _parse_existing_lock(content: bytes) -> ExistingLock:
    try:
        lock = _json_without_duplicate_keys(content, "existing lock")
        if not isinstance(lock, dict) or set(lock) != {
            "schema_version", "source_repo", "source_commit", "manifest_sha256", "files"
        }:
            raise SyncError("existing lock has unknown fields or is missing required fields")
        if type(lock["schema_version"]) is not int or lock["schema_version"] != 1:
            raise SyncError("existing lock schema_version must be 1")
        source_repo = _portable_source_repo(
            lock["source_repo"], "existing lock source_repo"
        )
        if not isinstance(lock["source_commit"], str) or LOWER_COMMIT_PATTERN.fullmatch(lock["source_commit"]) is None:
            raise SyncError("existing lock source_commit must be a lowercase 40-hex commit")
        if not isinstance(lock["manifest_sha256"], str) or DIGEST_PATTERN.fullmatch(lock["manifest_sha256"]) is None:
            raise SyncError("existing lock manifest_sha256 must be a lowercase SHA-256 digest")
        files = lock["files"]
        if not isinstance(files, dict) or not files:
            raise SyncError("existing lock files must be a non-empty object")
        ownership: dict[str, str] = {}
        for destination_text, digest in files.items():
            destination = _destination_path(destination_text, "existing lock file")
            if not isinstance(digest, str) or DIGEST_PATTERN.fullmatch(digest) is None:
                raise SyncError("existing lock file digest must be a lowercase SHA-256 digest")
            ownership[destination.as_posix()] = digest
        return ExistingLock(
            schema_version=lock["schema_version"],
            source_repo=source_repo,
            source_commit=lock["source_commit"],
            manifest_sha256=lock["manifest_sha256"],
            files=ownership,
            canonical_bytes=content,
        )
    except SyncError as exc:
        if str(exc).startswith("existing lock"):
            raise
        raise SyncError(f"existing lock is invalid: {exc}") from exc


def _load_existing_lock(
    lock_path: Path, state: FileState
) -> ExistingLock | None:
    if not state.exists:
        return None
    if not state.safe_regular or not state.single_link or state.content is None:
        raise SyncError("existing lock is not a safe, single-link regular file")
    try:
        return _parse_existing_lock(state.content)
    except SyncError as exc:
        raise SyncError(f"existing lock is invalid: {exc}") from exc


def _authenticate_existing_lock(
    repository_root: Path,
    template: Template,
    existing_lock: ExistingLock,
) -> None:
    if existing_lock.source_repo != template.source_repo:
        raise SyncError(
            "existing lock source_repo does not match template source_repo"
        )
    old_source_commit = _validate_source_commit(
        repository_root, existing_lock.source_commit
    )
    old_template = _load_template(repository_root, old_source_commit)
    reconstructed_lock = _lock_bytes(old_template, old_source_commit)
    if existing_lock.canonical_bytes != reconstructed_lock:
        raise SyncError(
            "existing lock is not the canonical lock generated from its source_commit"
        )


def _destination_pairs(codex_root: Path, template: Template) -> list[tuple[Path, bytes]]:
    return [
        (_path_for_destination(codex_root, item.destination), item.content)
        for item in template.files
    ]


def _display_destination(destination: PurePosixPath) -> str:
    return f".codex/{destination.as_posix()}"


def _check(target: Path, template: Template, expected_lock: bytes) -> int:
    codex_root = _codex_root(target)
    drift: list[str] = []
    for item, (destination, expected) in zip(template.files, _destination_pairs(codex_root, template)):
        state = _file_state(destination)
        if not state.safe_regular or not state.single_link or state.content != expected:
            drift.append(_display_destination(item.destination))
    lock_path = _path_for_destination(codex_root, template.lock)
    lock_state = _file_state(lock_path)
    if not lock_state.safe_regular or not lock_state.single_link or lock_state.content != expected_lock:
        drift.append(f".codex/{template.lock.as_posix()}")
    if drift:
        print("template drift detected: " + ", ".join(drift), file=sys.stderr)
        return 1
    print("portable Codex project template is current")
    return 0


def _ensure_safe_directory(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    if _lstat(current) is None:
        current.mkdir()
    for part in relative.parts:
        current = current / part
        file_stat = _lstat(current)
        if file_stat is None:
            current.mkdir()
            file_stat = _lstat(current)
        if file_stat is None or _is_reparse_or_symlink(file_stat) or not stat.S_ISDIR(file_stat.st_mode):
            raise SyncError(f"cannot create a safe destination directory: {current}")


def _assert_existing_destination_safe(path: Path) -> None:
    file_stat = _lstat(path)
    if file_stat is None:
        return
    if _is_reparse_or_symlink(file_stat) or not stat.S_ISREG(file_stat.st_mode):
        raise SyncError(
            f"destination changed to an unsafe file before atomic replace: {path}"
        )


def _assert_state_unchanged(path: Path, expected: FileState) -> None:
    current = _file_state(path)
    if current != expected:
        raise SyncError(f"destination changed after synchronization planning: {path}")


def _assert_replaced_file_safe(path: Path) -> None:
    file_stat = _lstat(path)
    if (
        file_stat is None
        or _is_reparse_or_symlink(file_stat)
        or not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_nlink != 1
    ):
        raise SyncError(f"atomic replacement did not produce a safe regular file: {path}")


def _atomic_replace(
    path: Path,
    content: bytes,
    codex_root: Path,
    expected_state: FileState,
) -> None:
    _ensure_safe_directory(path.parent, codex_root)
    _assert_directory_chain_safe(codex_root, path.parent)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, 0o644)
        _assert_directory_chain_safe(codex_root, path.parent)
        _assert_existing_destination_safe(path)
        _assert_state_unchanged(path, expected_state)
        os.replace(temporary_path, path)
        _assert_replaced_file_safe(path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _unlink_if_unchanged(
    path: Path, expected_state: FileState, codex_root: Path
) -> None:
    _assert_directory_chain_safe(codex_root, path.parent)
    _assert_state_unchanged(path, expected_state)
    path.unlink()


def _apply(
    repository_root: Path,
    target: Path,
    template: Template,
    expected_lock: bytes,
) -> int:
    codex_root = _codex_root(target)
    lock_path = _path_for_destination(codex_root, template.lock)
    lock_state = _file_state(lock_path)
    old_lock = _load_existing_lock(lock_path, lock_state)
    if old_lock is not None:
        _authenticate_existing_lock(repository_root, template, old_lock)
    new_ownership = {
        item.destination.as_posix(): _sha256(item.content) for item in template.files
    }
    if old_lock is not None:
        old_casefold_ownership = {
            destination.casefold(): destination for destination in old_lock.files
        }
        for destination in new_ownership:
            previous_spelling = old_casefold_ownership.get(destination.casefold())
            if previous_spelling is not None and previous_spelling != destination:
                raise SyncError(
                    "case-only ownership rename is unsafe across template versions: "
                    f"{previous_spelling} -> {destination}"
                )
    destinations = _destination_pairs(codex_root, template)

    write_plan: list[tuple[Path, bytes, FileState]] = []
    for item, (destination, content) in zip(template.files, destinations):
        state = _file_state(destination)
        if state.exists and not state.safe_regular:
            raise SyncError(f"managed destination is not a safe regular file: {destination}")
        previously_owned = (
            old_lock is not None
            and item.destination.as_posix() in old_lock.files
        )
        if state.exists and state.content != content and not previously_owned:
            raise SyncError(
                "unowned destination conflicts with generated content: "
                f"{destination}"
            )
        if not state.exists or not state.single_link or state.content != content:
            write_plan.append((destination, content, state))

    delete_plan: list[tuple[Path, FileState]] = []
    if old_lock is not None:
        for destination_text, old_digest in old_lock.files.items():
            if destination_text in new_ownership:
                continue
            destination_relative = _destination_path(destination_text, "existing lock file")
            destination = _path_for_destination(codex_root, destination_relative)
            state = _file_state(destination)
            if not state.exists:
                continue
            if not state.safe_regular or not state.single_link:
                raise SyncError(f"previously owned file is not safe to remove: {destination}")
            if state.content is None or _sha256(state.content) != old_digest:
                raise SyncError(f"modified previously owned file blocks apply: {destination}")
            delete_plan.append((destination, state))

    for destination, content, planned_state in write_plan:
        _atomic_replace(destination, content, codex_root, planned_state)
    for destination, planned_state in delete_plan:
        _unlink_if_unchanged(destination, planned_state, codex_root)
    if not lock_state.exists or not lock_state.single_link or lock_state.content != expected_lock:
        _atomic_replace(lock_path, expected_lock, codex_root, lock_state)
    print(f"applied {len(destinations)} manifest-owned template files")
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "apply"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--target", type=Path, required=True)
        command_parser.add_argument("--source-commit", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if SOURCE_COMMIT_PATTERN.fullmatch(args.source_commit) is None:
        print("error: --source-commit must be exactly 40 hexadecimal characters", file=sys.stderr)
        return 2
    repository_root = Path(__file__).resolve().parents[2]
    try:
        _validate_repository_root(repository_root)
        source_commit = _validate_source_commit(repository_root, args.source_commit)
        template = _load_template(repository_root, source_commit)
        expected_lock = _lock_bytes(template, source_commit)
        if args.command == "check":
            return _check(args.target, template, expected_lock)
        return _apply(repository_root, args.target, template, expected_lock)
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"error: filesystem operation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
