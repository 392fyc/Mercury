#!/usr/bin/env python3
"""kb.py — command-line access to Mercury_KB, filesystem-first.

Replaces the obsidian MCP server for day-to-day knowledge-base work.

Why not route everything through Obsidian
-----------------------------------------
All three Obsidian-side options require the desktop app to be running:

  - the official Obsidian CLI — "Note that the Obsidian app must be running."
    (https://obsidian.md/cli); it is a remote control for a live app, not a
    headless tool
  - the Local REST API on localhost — a *plugin*, so it dies with the app
  - the MCP server — same, plus it leaks processes between sessions

The vault is a plain git repository full of markdown. So reading, writing and
searching go straight to the files: no app, no network, no daemon. Only the
things the filesystem genuinely cannot do — Obsidian's own search index, its
command palette, the currently-open note — go over the REST API, and those
subcommands say so plainly when it is unavailable rather than pretending.

Configuration (never hardcoded)
-------------------------------
  MERCURY_KB_DIR    vault root; falls back to `kb_dir` in .handoff-config
  OBSIDIAN_HOST     default http://localhost:27123      (REST subcommands only)
  OBSIDIAN_API_KEY  required for REST subcommands

Exit codes
----------
  0  success
  1  not found / operation failed
  2  environment problem (vault not configured, path escapes the vault)
  3  REST API unavailable or unauthenticated (REST subcommands only)
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KB_DIR_ENV = "MERCURY_KB_DIR"
HOST_ENV = "OBSIDIAN_HOST"
KEY_ENV = "OBSIDIAN_API_KEY"
DEFAULT_HOST = "http://localhost:27123"

# Directories that are vault machinery rather than notes. `.git` also matters
# for correctness, not just noise: it contains blobs that look like text and
# would otherwise show up in `grep`.
SKIP_DIRS = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}

EXIT_OK, EXIT_FAIL, EXIT_ENV, EXIT_REST = 0, 1, 2, 3


def _is_pipe_error(exc: BaseException) -> bool:
    """True for a downstream-closed-the-pipe error, on any platform.

    Windows raises OSError EINVAL rather than BrokenPipeError, so testing the
    exception type alone misses it there. Deliberately narrow: a genuine I/O
    failure must still propagate rather than be reported as success.

    Called from exactly two places, both wrapped tightly around a stdout
    operation: `emit()` (the write) and `_finish()` (the deliberate flush).
    An earlier version instead wrapped whole subcommands in `except OSError`,
    which was wrong — a filesystem EINVAL raised while reading a note was
    then indistinguishable from a closed pipe and got reported as exit 0.
    Keeping the predicate narrow is only half of it; where it is applied
    matters as much.

    KNOWN IMPRECISION: on Windows a closed pipe surfaces as EINVAL, an errno
    generic enough that a genuine EINVAL from stdout itself would also be
    read as a closed pipe and keep the command's prior exit code. There is no
    way found to distinguish the two from the exception alone. The exposure is
    bounded — it applies only to stdout writes and flushes, not to any
    filesystem operation — but it is real, and is not claimed to be handled.
    """
    return isinstance(exc, BrokenPipeError) or (
        isinstance(exc, OSError) and exc.errno in (errno.EPIPE, errno.EINVAL))


class KbError(Exception):
    """Fatal, with an exit code attached."""

    def __init__(self, message: str, code: int = EXIT_FAIL) -> None:
        super().__init__(message)
        self.code = code


class _PipeClosed(Exception):
    """The downstream reader went away. Normal for `kb ... | head`."""


def emit(text: str = "") -> None:
    """Write one line to stdout, mapping a closed pipe to `_PipeClosed`.

    Every stdout write in this program goes through here, and that scoping is
    the point. An earlier version wrapped whole subcommands in
    `except OSError` to catch the pipe, which meant a filesystem EINVAL raised
    while reading a note was indistinguishable from a closed pipe and got
    reported as success. Narrowing the predicate was not enough on its own —
    it has to be applied to the output operation rather than to the command.
    """
    try:
        sys.stdout.write(text + "\n")
    except OSError as exc:
        if _is_pipe_error(exc):
            raise _PipeClosed from exc
        raise


# --------------------------------------------------------------------------
# vault resolution + path containment
# --------------------------------------------------------------------------

def vault_root() -> Path:
    """Resolve the vault root from env, else from .handoff-config."""
    raw = os.environ.get(KB_DIR_ENV, "").strip()
    source = KB_DIR_ENV
    if not raw:
        cfg = Path(__file__).resolve().parent.parent / ".handoff-config"
        if cfg.is_file():
            for line in cfg.read_text(encoding="utf-8").splitlines():
                key, sep, value = line.partition("=")
                if sep and key.strip() == "kb_dir":
                    raw, source = value.strip(), f"{cfg.name}:kb_dir"
                    break
    if not raw:
        raise KbError(
            f"vault not configured: set {KB_DIR_ENV}, or add kb_dir to "
            f".handoff-config", EXIT_ENV)
    root = Path(raw).expanduser()
    if not root.is_dir():
        raise KbError(f"vault path from {source} is not a directory: {root}",
                      EXIT_ENV)
    return root.resolve()


def resolve_in_vault(root: Path, rel: str) -> Path:
    """Join `rel` under `root`, refusing anything that lands outside.

    Same containment rule as scripts/sot_id_map/sources.py: resolve first,
    then check, so a `..`-laden path or a symlink/junction cannot walk out of
    the vault and read or overwrite an unrelated file. `root` is already
    resolved by `vault_root`.
    """
    try:
        candidate = (root / rel).resolve()
    except (OSError, RuntimeError) as exc:
        raise KbError(f"cannot resolve {rel!r} under {root}: {exc}",
                      EXIT_ENV) from exc
    if not candidate.is_relative_to(root):
        raise KbError(
            f"path {rel!r} resolves to {candidate}, which is outside the vault "
            f"{root} — refusing", EXIT_ENV)
    return candidate


def is_reparse_dir(path: Path) -> bool:
    """True for a Windows junction, which `is_symlink()` reports as False."""
    try:
        return bool(path.stat(follow_symlinks=False).st_reparse_tag)
    except (OSError, AttributeError):
        return False


def iter_notes(root: Path, subdir: str = ""):
    """Every *.md under the vault, skipping machinery directories.

    Uses os.walk with an onerror hook rather than rglob: rglob swallows
    permission errors and silently yields nothing, which reads exactly like
    "the directory is empty" (learned the hard way — see #556).

    CONTAINMENT APPLIES HERE TOO, and the obvious reading of `followlinks`
    is wrong on Windows. `followlinks=False` does NOT stop os.walk from
    descending into a junction: measured on this machine with a real junction
    placed inside the vault, os.walk went straight through it and `ls` /
    `grep` returned a file living outside.

    So aliases are pruned during the walk AND every yielded path is checked
    for containment. The second check is not redundant: pruning depends on a
    stat succeeding, and a path can also reach outside via a component that
    was already resolved before the walk began.

    KNOWN LIMIT — time-of-check/time-of-use. Containment is verified when the
    path is yielded, not when the caller opens it. An alias swapped into place
    in between would be followed by the subsequent read. Not fixed: doing so
    needs open-then-verify against a file handle, and Windows offers no
    portable O_NOFOLLOW. The threat model also does not motivate it — writing
    into the vault is already required to set the race up, and anyone with
    that can read the same content directly. Recorded so the guarantee is not
    read as stronger than it is.
    """
    base = resolve_in_vault(root, subdir) if subdir else root
    if not base.is_dir():
        raise KbError(f"not a directory: {base}", EXIT_FAIL)

    problems: list[str] = []
    for dirpath, dirnames, filenames in os.walk(
            base, onerror=lambda e: problems.append(str(e)), followlinks=False):
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS:
                continue
            child = Path(dirpath) / d
            if child.is_symlink() or is_reparse_dir(child):
                print(f"warning: not descending into alias "
                      f"{child} (symlink or junction)", file=sys.stderr)
                continue
            keep.append(d)
        dirnames[:] = keep

        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            candidate = Path(dirpath) / name
            try:
                if not candidate.resolve().is_relative_to(root):
                    print(f"warning: skipping {candidate} — resolves outside "
                          f"the vault", file=sys.stderr)
                    continue
            except (OSError, RuntimeError) as exc:
                print(f"warning: skipping {candidate}: {exc}", file=sys.stderr)
                continue
            yield candidate
    for problem in problems:
        print(f"warning: could not read: {problem}", file=sys.stderr)


def rel_of(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


# --------------------------------------------------------------------------
# REST (only for what the filesystem cannot do)
# --------------------------------------------------------------------------

def rest_call(path: str, method: str = "GET", payload: dict | None = None,
              content_type: str = "application/json") -> dict:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise KbError(
            f"{KEY_ENV} is not set — this subcommand needs the Obsidian Local "
            f"REST API, which also requires the Obsidian app to be running. "
            f"Filesystem subcommands (ls/cat/write/append/find/grep) need "
            f"neither.", EXIT_REST)
    host = os.environ.get(HOST_ENV, "").strip() or DEFAULT_HOST
    url = urllib.parse.urljoin(host.rstrip("/") + "/", path.lstrip("/"))
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        hint = (" — check OBSIDIAN_API_KEY" if exc.code == 401 else "")
        raise KbError(f"REST {method} {url} failed: HTTP {exc.code}{hint}\n"
                      f"{detail}", EXIT_REST) from exc
    except urllib.error.URLError as exc:
        raise KbError(
            f"cannot reach the Obsidian Local REST API at {host}: {exc.reason}\n"
            f"Is Obsidian running with the Local REST API plugin enabled? "
            f"Filesystem subcommands do not need it.", EXIT_REST) from exc
    return json.loads(body) if body.strip() else {}


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_status(args) -> int:
    root = vault_root()
    notes = list(iter_notes(root))
    emit(f"vault      {root}")
    emit(f"notes      {len(notes)} markdown file(s)")
    emit(f"git        {'yes' if (root / '.git').exists() else 'no'}")
    host = os.environ.get(HOST_ENV, "").strip() or DEFAULT_HOST
    if not os.environ.get(KEY_ENV, "").strip():
        emit(f"rest       not configured ({KEY_ENV} unset) — "
              f"filesystem subcommands unaffected")
        return EXIT_OK
    try:
        info = rest_call("/")
    except KbError as exc:
        emit(f"rest       unavailable at {host}")
        emit(f"           {exc}".replace("\n", "\n           "))
        return EXIT_OK  # not a failure: the filesystem path still works
    versions = info.get("versions", {})
    emit(f"rest       reachable at {host} "
          f"(obsidian {versions.get('obsidian', '?')}, "
          f"plugin {versions.get('self', '?')}, "
          f"authenticated={info.get('authenticated')})")
    return EXIT_OK


def cmd_ls(args) -> int:
    root = vault_root()
    count = 0
    for path in iter_notes(root, args.subdir or ""):
        emit(rel_of(root, path))
        count += 1
    return EXIT_OK if count else EXIT_FAIL


def cmd_cat(args) -> int:
    root = vault_root()
    path = resolve_in_vault(root, args.path)
    if not path.is_file():
        raise KbError(f"no such note: {args.path}", EXIT_FAIL)
    emit(path.read_text(encoding="utf-8").rstrip("\n"))
    return EXIT_OK


def _write(args, mode: str) -> int:
    root = vault_root()
    path = resolve_in_vault(root, args.path)
    body = sys.stdin.read()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, encoding="utf-8", newline="\n") as handle:
        handle.write(body)
    # Read back rather than trusting the write: a successful open() says
    # nothing about what actually landed on disk.
    written = path.read_text(encoding="utf-8")
    if mode == "w" and written != body:
        raise KbError(f"write verification failed for {args.path}", EXIT_FAIL)
    print(f"{'wrote' if mode == 'w' else 'appended to'} "
          f"{rel_of(root, path)} ({len(body)} chars, file now "
          f"{len(written)} chars)", file=sys.stderr)
    return EXIT_OK


def cmd_write(args) -> int:
    return _write(args, "w")


def cmd_append(args) -> int:
    return _write(args, "a")


def cmd_find(args) -> int:
    root = vault_root()
    needle = args.pattern.lower()
    hits = [rel_of(root, p) for p in iter_notes(root)
            if needle in p.name.lower()]
    for hit in hits:
        emit(hit)
    return EXIT_OK if hits else EXIT_FAIL


def cmd_grep(args) -> int:
    root = vault_root()
    needle = args.query if args.case_sensitive else args.query.lower()
    hits = 0
    for path in iter_notes(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"warning: skipped {rel_of(root, path)}: {exc}",
                  file=sys.stderr)
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            haystack = line if args.case_sensitive else line.lower()
            if needle in haystack:
                emit(f"{rel_of(root, path)}:{lineno}:{line.strip()}")
                hits += 1
    return EXIT_OK if hits else EXIT_FAIL


def cmd_osearch(args) -> int:
    """Obsidian's own search index — the filesystem cannot reproduce this."""
    result = rest_call(f"/search/simple/?query="
                       f"{urllib.parse.quote(args.query)}", method="POST")
    if not result:
        print("(no matches)", file=sys.stderr)
        return EXIT_FAIL
    emit(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_commands(args) -> int:
    """List Obsidian command-palette commands (REST-only capability)."""
    result = rest_call("/commands/")
    emit(json.dumps(result, ensure_ascii=False, indent=2))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kb",
        description="Mercury_KB access. Filesystem-first: ls/cat/write/append/"
                    "find/grep work with Obsidian closed. osearch/commands "
                    "need Obsidian running.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="vault path, note count, REST reachability"
                   ).set_defaults(func=cmd_status)

    p = sub.add_parser("ls", help="list notes (filesystem)")
    p.add_argument("subdir", nargs="?", help="limit to a subdirectory")
    p.set_defaults(func=cmd_ls)

    p = sub.add_parser("cat", help="print a note (filesystem)")
    p.add_argument("path")
    p.set_defaults(func=cmd_cat)

    p = sub.add_parser("write", help="overwrite a note from stdin (filesystem)")
    p.add_argument("path")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("append", help="append to a note from stdin (filesystem)")
    p.add_argument("path")
    p.set_defaults(func=cmd_append)

    p = sub.add_parser("find", help="find notes by filename (filesystem)")
    p.add_argument("pattern")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("grep", help="full-text search (filesystem)")
    p.add_argument("query")
    p.add_argument("-s", "--case-sensitive", action="store_true")
    p.set_defaults(func=cmd_grep)

    p = sub.add_parser("osearch", help="Obsidian index search (needs Obsidian)")
    p.add_argument("query")
    p.set_defaults(func=cmd_osearch)

    sub.add_parser("commands", help="list Obsidian commands (needs Obsidian)"
                   ).set_defaults(func=cmd_commands)
    return parser


def main(argv: list[str] | None = None) -> int:
    # The vault is full of CJK. Emit UTF-8 regardless of the console code page
    # so piping and redirection get correct bytes; `errors="replace"` keeps a
    # legacy console from turning a display problem into a crash.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    try:
        sys.stdin.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    args = build_parser().parse_args(argv)
    try:
        rc = args.func(args)
    except KbError as exc:
        print(f"kb: {exc}", file=sys.stderr)
        rc = exc.code
    except _PipeClosed:
        # `kb grep x | head` — the reader is done, we are done. Not a failure.
        rc = EXIT_OK
    except OSError as exc:
        # A real filesystem failure. Report it like an error instead of
        # dumping a traceback, and never as success. Note this is reached
        # only for non-pipe errors: pipe-shaped ones are raised as
        # _PipeClosed by emit(), from the write itself.
        print(f"kb: {exc}", file=sys.stderr)
        rc = EXIT_FAIL
    return _finish(rc)


def _finish(rc: int) -> int:
    """Flush stdout before returning, classifying any failure honestly.

    `kb grep x | head` is a normal way to use this, so a downstream close must
    not become a failure. Catching the write was not enough, and the naive
    version was wrong twice over:

      1. Windows reports a closed pipe as OSError EINVAL, not BrokenPipeError,
         so `except BrokenPipeError` never fires.
      2. Whether the error surfaces inside a `try` at all depends on where the
         buffer happens to fill. At ~700 lines it raised inside emit() and was
         caught; at ~2000 it did not raise until the interpreter's own
         exit-time flush — after main() had already returned 0 — turning the
         exit code into 120 with an "Exception ignored" trailer.

    So flush deliberately here, while there is still a frame to handle it.
    Buffer size stops mattering. Crucially, only pipe-shaped failures are
    absorbed: a real I/O failure while flushing must not be reported as
    success, or the exit-code contract is a lie.
    """
    try:
        sys.stdout.flush()
    except ValueError:
        # Stream already closed. Nothing buffered can still be lost.
        return rc
    except OSError as exc:
        if _is_pipe_error(exc):
            try:
                os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            except OSError:
                pass  # exit code is already decided; nothing further to do
            return rc
        print(f"kb: could not flush stdout: {exc}", file=sys.stderr)
        return rc if rc != EXIT_OK else EXIT_FAIL
    return rc


if __name__ == "__main__":
    sys.exit(main())
