#!/usr/bin/env python3
"""Verify a downstream Codex template lock against trusted Mercury history."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path
from typing import Sequence


SOURCE_REPO = "392fyc/Mercury"
DEFAULT_SOURCE_REF = "origin/develop"
LOWER_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SYNC_SCRIPT = Path(__file__).resolve().with_name("sync-project-template.py")


def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "mercury_sync_project_template_for_verification", SYNC_SCRIPT
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the adjacent Mercury template synchronizer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SYNC = _load_sync_module()


def _trusted_ref(repository_root: Path, value: str) -> str:
    if not value or value != value.strip():
        raise SYNC.SyncError("--source-ref must name a remote-tracking branch")
    if value.startswith("refs/remotes/"):
        ref = value
    else:
        if "/" not in value or value.startswith("refs/"):
            raise SYNC.SyncError(
                "--source-ref must be a remote-tracking branch such as origin/develop"
            )
        ref = f"refs/remotes/{value}"
    if not ref.startswith("refs/remotes/"):
        raise SYNC.SyncError("--source-ref must be under refs/remotes")
    try:
        SYNC._run_git(repository_root, "check-ref-format", ref)
    except SYNC.SyncError as exc:
        raise SYNC.SyncError("--source-ref is not a valid remote-tracking ref") from exc
    return ref


def _resolve_ref(repository_root: Path, ref: str) -> str:
    try:
        output = SYNC._run_git(
            repository_root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"
        )
    except SYNC.SyncError as exc:
        raise SYNC.SyncError(
            "--source-ref does not resolve to a commit in the Mercury repository"
        ) from exc
    commit = output.decode("ascii", errors="strict").strip()
    if LOWER_COMMIT_PATTERN.fullmatch(commit) is None:
        raise SYNC.SyncError("--source-ref did not resolve to a full commit SHA")
    return commit


def _require_ancestor(repository_root: Path, source_commit: str, source_ref_commit: str) -> None:
    try:
        SYNC._run_git(
            repository_root,
            "merge-base",
            "--is-ancestor",
            source_commit,
            source_ref_commit,
        )
    except SYNC.SyncError as exc:
        if str(exc) == "git command failed with exit code 1":
            raise SYNC.SyncError(
                "lock source_commit is not an ancestor of the trusted upstream ref"
            ) from None
        raise


def _reject_unmanaged_reserved_files(codex_root: Path, template) -> None:
    allowed = {item.destination.as_posix() for item in template.files}
    allowed.add(template.lock.as_posix())
    for directory in [codex_root, *(codex_root / group for group in sorted(SYNC.SUPPORTED_DESTINATION_GROUPS))]:
        SYNC._assert_directory_chain_safe(codex_root, directory)
        if not directory.is_dir():
            continue
        for path in directory.iterdir():
            if path.name.casefold().startswith("mercury-"):
                relative = path.relative_to(codex_root).as_posix()
                if relative not in allowed:
                    raise SYNC.SyncError(f"unmanaged file uses a reserved Mercury path: {relative}")


def _verify(target: Path, source_ref: str) -> int:
    repository_root = Path(__file__).resolve().parents[2]
    SYNC._validate_repository_root(repository_root)
    ref = _trusted_ref(repository_root, source_ref)
    upstream_commit = _resolve_ref(repository_root, ref)

    safe_target = SYNC._assert_target_chain_safe(target)
    codex_root = SYNC._codex_root(safe_target)
    lock_relative = SYNC._lock_path("mercury-template.lock", "template lock")
    lock_path = SYNC._path_for_destination(codex_root, lock_relative)
    lock_state = SYNC._file_state(lock_path)
    if not lock_state.exists:
        raise SYNC.SyncError("target has no .codex/mercury-template.lock")
    if not lock_state.safe_regular or not lock_state.single_link or lock_state.content is None:
        raise SYNC.SyncError("target lock is not a safe, single-link regular file")

    lock = SYNC._parse_existing_lock(lock_state.content)
    if lock.source_repo != SOURCE_REPO:
        raise SYNC.SyncError("lock source_repo must identify Mercury (392fyc/Mercury)")
    source_commit = SYNC._validate_source_commit(repository_root, lock.source_commit)
    _require_ancestor(repository_root, source_commit, upstream_commit)

    template = SYNC._load_template(repository_root, source_commit)
    if template.source_repo != SOURCE_REPO:
        raise SYNC.SyncError("locked Mercury manifest source_repo is not Mercury")
    expected_lock = SYNC._lock_bytes(template, source_commit)
    if lock.canonical_bytes != expected_lock:
        raise SYNC.SyncError(
            "target lock is not the canonical lock generated from its Mercury source_commit"
        )

    _reject_unmanaged_reserved_files(codex_root, template)
    check_result = SYNC._check(safe_target, template, expected_lock)
    if check_result != 0:
        return check_result
    print(f"verified Mercury template source {source_commit} under {ref}")
    return 0


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--source-ref", default=DEFAULT_SOURCE_REF)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return _verify(args.target, args.source_ref)
    except SYNC.SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except (OSError, UnicodeError) as exc:
        print(f"error: verification filesystem or encoding failure: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
