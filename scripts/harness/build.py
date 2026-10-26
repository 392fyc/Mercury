#!/usr/bin/env python3
"""Project the Mercury harness (manifest v2) into a target repository.

    python scripts/harness/build.py --target <repo> --check   compare only; any difference exits 1
    python scripts/harness/build.py --target <repo> --apply   write the projection and the lock

The target selects modules in `<target>/mercury.project.json`; the builder projects the files the
manifest declares for them and records every projected file's sha256 in `<target>/.mercury/harness.lock`.
Hand edits to a managed file are never overwritten or deleted: the run stops and reports them.
See harness/README.md for the rules.

Exit codes: 0 in sync / applied, 1 differences or conflicts, 2 invalid input or configuration.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[2]
LOCK_REL = ".mercury/harness.lock"
CONFIG_NAME = "mercury.project.json"
HOSTS = {"codex": ".agents/skills", "claude": ".claude/skills"}
CLAUDE_ENTRY = "claude-entry.md"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class BuildError(Exception):
    """Invalid input or configuration (exit code 2)."""


def digest(data):
    """sha256 of the content; CRLF is folded to LF for text so git's autocrlf cannot change it."""
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def load_json(path, what):
    try:
        raw = path.read_bytes()
        return json.loads(raw.decode("utf-8")), digest(raw)
    except (OSError, ValueError) as err:
        raise BuildError(f"cannot read {what} ({path.name}): {err}")


RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}
BAD_CHARS = set('<>:"|?*~') | {chr(92)}
RESERVED_PATHS = {LOCK_REL, CONFIG_NAME}


def seg_key(seg):
    """Windows drops trailing dots and spaces from a segment and ignores case; compare that way."""
    return seg.rstrip(". ").lower()


def plain_rel(rel, what):
    """A plain relative POSIX path: no drive, no absolute root, no empty, '.' or '..' segments, no odd characters."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise BuildError(f"{what}: not a plain relative path: {rel!r}")
    parts = rel.split("/")
    for seg in parts:
        if seg in ("", ".", "..") or not seg_key(seg) or seg_key(seg) in (".", ".."):
            raise BuildError(f"{what}: empty, '.' or '..' segment in path: {rel!r}")
        if any(ord(c) < 32 or c in BAD_CHARS for c in seg):
            raise BuildError(f"{what}: forbidden character (control, backslash, colon, tilde and similar) in path: {rel!r}")
        if seg_key(seg).split(".")[0].rstrip() in RESERVED:
            raise BuildError(f"{what}: reserved device name in path: {rel!r}")
    return rel


def check_rel(rel, what):
    """A projection path: plain, outside .git/ and harness/, and not a reserved path (all compared Windows-style)."""
    plain_rel(rel, what)
    keys = [seg_key(s) for s in rel.split("/")]
    if ".git" in keys or keys[0] == "harness":
        raise BuildError(f"{what}: path must not be inside .git/ or harness/: {rel!r}")
    if "/".join(keys) in {"/".join(seg_key(s) for s in r.split("/")) for r in RESERVED_PATHS}:
        raise BuildError(f"{what}: path is reserved: {rel!r}")
    return rel


def is_link(path):
    return path.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(path))


def check_inside(base, rel, what):
    """Every existing ancestor must be a real directory (no symlink or junction) that resolves inside base."""
    base_res = base.resolve()
    cur = base
    parts = rel.split("/")
    for i, part in enumerate(parts):
        cur = cur / part
        if not os.path.lexists(cur):
            return
        if is_link(cur):
            raise BuildError(f"{what}: refusing to follow a link at {'/'.join(parts[:i + 1])}")
        try:
            cur.resolve().relative_to(base_res)
        except ValueError:
            raise BuildError(f"{what}: {'/'.join(parts[:i + 1])} resolves outside the repository")
        if i < len(parts) - 1 and not cur.is_dir():
            raise BuildError(f"{what}: {'/'.join(parts[:i + 1])} is a file but a directory is needed")


def inside_source(base, path, what):
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise BuildError(f"{what}: resolves outside harness/")


def skill_roots():
    return tuple(tuple(v.split("/")) for v in HOSTS.values())


def in_skill_namespace(rel):
    parts = tuple(rel.split("/"))
    return any(parts[:2] == root and len(parts) > 2 for root in skill_roots())


def skill_root_of(rel):
    parts = tuple(rel.split("/"))
    return next((root for root in skill_roots() if parts[:2] == root and len(parts) > 2), None)


def skill_meta(skill_dir):
    """Raw `name:` and `description:` lines of SKILL.md frontmatter (kept verbatim)."""
    text = (skill_dir / "SKILL.md").read_bytes().decode("utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    if not m:
        raise BuildError(f"{skill_dir.name}/SKILL.md has no frontmatter")
    lines = m.group(1).split("\n")
    found = []
    for key in ("name:", "description:"):
        idx = next((i for i, l in enumerate(lines) if l.startswith(key)), None)
        if idx is None:
            raise BuildError(f"{skill_dir.name}/SKILL.md frontmatter needs name and description")
        value = lines[idx][len(key):].strip()
        if not value or value[0] in "|>" or (idx + 1 < len(lines) and lines[idx + 1][:1] in (" ", "	")):
            raise BuildError(f"{skill_dir.name}/SKILL.md: {key[:-1]} must be a single-line string to generate an entry")
        found.append(lines[idx])
    return found[0], found[1]


def claude_entry(skill_dir, skill, hosts):
    own = skill_dir / CLAUDE_ENTRY
    if own.is_file():
        return own.read_bytes()
    if "codex" not in hosts:
        raise BuildError(f"skill {skill}: a generated Claude entry points at the codex projection; add host codex or provide {CLAUDE_ENTRY}")
    name, desc = skill_meta(skill_dir)
    path = f"{HOSTS['codex']}/{skill}/SKILL.md"
    body = (f"---\n{name}\n{desc}\n---\n\n# {skill} (Claude Code entry)\n\n"
            f"Read and follow `{path}`. That file is the shared rule source for Claude Code and Codex; "
            f"\"this skill's directory\" in it means `{HOSTS['codex']}/{skill}/`. "
            f"This entry adds no permission and no separate workflow.\n")
    return body.encode("utf-8")


def load_manifest(source):
    manifest, mhash = load_json(source / "harness" / "manifest.json", "manifest")
    mods = isinstance(manifest, dict) and manifest.get("schema_version") == 2 and manifest.get("modules")
    if not isinstance(mods, dict):
        raise BuildError("manifest: schema_version must be 2 and modules must be an object")
    for name, spec in mods.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("skills", {}), dict) or not isinstance(spec.get("files", []), list):
            raise BuildError(f"manifest: module {name} is malformed")
        if not all(isinstance(s, dict) for s in spec.get("skills", {}).values()):
            raise BuildError(f"manifest: module {name}: every skill must be an object")
        if not all(isinstance(f, dict) for f in spec.get("files", [])):
            raise BuildError(f"manifest: module {name}: every file entry must be an object")
    return manifest, mhash


def declared_file_targets(manifest):
    """Single-file targets declared by any module (selected or not); the only deletable paths outside the skill roots."""
    return {f.get("target") for m in manifest["modules"].values() for f in m.get("files", []) if isinstance(f.get("target"), str)}


def load_config(target, manifest):
    cfg, chash = load_json(target / CONFIG_NAME, CONFIG_NAME)
    mods = cfg.get("modules") if isinstance(cfg, dict) else None
    if cfg.get("schema_version") != 1 or not isinstance(mods, list) or not all(isinstance(m, str) for m in mods):
        raise BuildError(f"{CONFIG_NAME}: expected {{\"schema_version\": 1, \"modules\": [names]}}")
    unknown = sorted(set(mods) - set(manifest["modules"]))
    if unknown:
        raise BuildError(f"{CONFIG_NAME}: unknown module(s): {', '.join(unknown)}")
    required = {n for n, m in manifest["modules"].items() if m.get("required")}
    return sorted(set(mods) | required), chash


def desired_files(source, manifest, selected):
    """Map projection path -> bytes for the selected modules."""
    out = {}
    hroot = source / "harness"

    def add(rel, data, owner):
        check_rel(rel, owner)
        if rel in out:
            raise BuildError(f"{owner}: path projected twice: {rel}")
        out[rel] = data

    for mod in selected:
        spec = manifest["modules"][mod]
        owner = f"module {mod}"
        base = hroot / plain_rel(spec.get("path"), f"{owner} path")
        inside_source(hroot, base, owner)
        for skill, sspec in sorted((spec.get("skills") or {}).items()):
            if not NAME_RE.match(skill):
                raise BuildError(f"{owner}: bad skill name {skill!r}")
            sdir = base / "skills" / skill
            inside_source(hroot, sdir, owner)
            if not (sdir / "SKILL.md").is_file():
                raise BuildError(f"{owner}: skill {skill} has no SKILL.md")
            hosts = sspec.get("hosts")
            if not isinstance(hosts, list) or not hosts or not set(hosts) <= set(HOSTS):
                raise BuildError(f"{owner}: skill {skill}: hosts must be a non-empty subset of {sorted(HOSTS)}")
            if "codex" in hosts:
                for f in sorted(sdir.rglob("*")):
                    if is_link(f):
                        raise BuildError(f"{owner}: link in skill source: {f.name}")
                    rel = f.relative_to(sdir).as_posix()
                    if f.is_file() and rel != CLAUDE_ENTRY:
                        add(f"{HOSTS['codex']}/{skill}/{rel}", f.read_bytes(), owner)
            if "claude" in hosts:
                add(f"{HOSTS['claude']}/{skill}/SKILL.md", claude_entry(sdir, skill, hosts), owner)
        for item in spec.get("files") or []:
            src = plain_rel(item.get("source"), f"{owner} file source")
            f = base / src
            inside_source(hroot, f, owner)
            if is_link(f) or not f.is_file():
                raise BuildError(f"{owner}: file source missing or a link: {src}")
            add(item.get("target"), f.read_bytes(), owner)
    return dict(sorted(out.items()))


def read_lock(target, allowed_files):
    path = target / LOCK_REL
    if not path.exists():
        return {}
    data, _ = load_json(path, "lock")
    files = data.get("files") if isinstance(data, dict) else None
    if data.get("schema_version") != 1 or not isinstance(files, dict):
        raise BuildError("lock: unexpected format")
    for rel, h in files.items():
        check_rel(rel, "lock")
        # A tampered lock must not steer deletions outside the projection namespace.
        if not isinstance(h, str) or not (in_skill_namespace(rel) or rel in allowed_files):
            raise BuildError(f"lock: entry outside the projection namespace or malformed: {rel!r}")
    return files


def current_hash(target, rel):
    """None when absent, 'not-a-file' for anything that is not a plain file, else the digest."""
    p = target / rel
    if not os.path.lexists(p):
        return None
    if is_link(p) or not p.is_file():
        return "not-a-file"
    return digest(p.read_bytes())


def plan(source, target):
    manifest, mhash = load_manifest(source)
    selected, chash = load_config(target, manifest)
    want = desired_files(source, manifest, selected)
    lock = read_lock(target, declared_file_targets(manifest))
    check_inside(target, LOCK_REL, "lock")
    for rel in list(want) + list(lock):
        check_inside(target, rel, "target")
    writes, deletes, conflicts = [], [], []
    for rel, data in want.items():
        d, cur, rec = digest(data), current_hash(target, rel), lock.get(rel)
        if cur is None:
            writes.append((rel, "create"))
        elif cur == "not-a-file":
            conflicts.append(f"{rel}: exists but is not a regular file")
        elif cur == d:
            continue  # already identical (also adopts an untracked identical file)
        elif rec is None:
            conflicts.append(f"{rel}: exists with different content and is not in the lock (not managed)")
        elif cur != rec:
            conflicts.append(f"{rel}: modified by hand since the last projection (lock hash differs)")
        else:
            writes.append((rel, "update"))
    for rel, rec in sorted(lock.items()):
        if rel in want:
            continue
        cur = current_hash(target, rel)
        if cur is None:
            continue
        if cur == rec:
            deletes.append(rel)
        else:
            conflicts.append(f"{rel}: no longer declared but modified by hand; not deleted")
    if source.resolve() == target.resolve():
        origin = "self"
    else:
        head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        origin = f"mercury@{head}" if head else "mercury"
    lock_doc = {"schema_version": 1, "source": origin, "manifest_sha256": mhash, "project_config_sha256": chash,
                "files": {rel: digest(d) for rel, d in want.items()}}
    lock_text = json.dumps(lock_doc, indent=2, sort_keys=True) + "\n"
    lp = target / LOCK_REL
    lock_same = lp.is_file() and lp.read_bytes().decode("utf-8").replace("\r\n", "\n") == lock_text
    return want, writes, deletes, conflicts, lock_text, lock_same


def prune_empty_dirs(target, rel):
    """Remove directories emptied by a deletion, up to but never including the skill root (.agents/skills, .claude/skills)."""
    root = skill_root_of(rel)
    if root is None:
        return
    stop = target.joinpath(*root)
    parent = (target / rel).parent
    while parent != stop and stop in parent.parents and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent


def run(source, target, apply):
    want, writes, deletes, conflicts, lock_text, lock_same = plan(source, target)
    report = [f"conflict: {c}" for c in conflicts]
    if not apply:
        report += [f"{kind}: {rel}" for rel, kind in writes] + [f"delete: {rel}" for rel in deletes]
        if not lock_same:
            report.append(f"update: {LOCK_REL}")
        for line in report:
            print(line, file=sys.stderr)
        print("harness: " + (f"{len(report)} difference(s)" if report else f"{len(want)} file(s) in sync"))
        return 1 if report else 0
    if conflicts:
        for line in report:
            print(line, file=sys.stderr)
        print("harness: stopped, nothing written; resolve the conflicts above", file=sys.stderr)
        return 1
    for rel, _ in writes:
        p = target / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(want[rel])
    for rel in deletes:
        (target / rel).unlink()
        prune_empty_dirs(target, rel)
    if not lock_same:
        lp = target / LOCK_REL
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_bytes(lock_text.encode("utf-8"))
    print(f"harness: wrote {len(writes)}, deleted {len(deletes)}, {len(want)} file(s) managed")
    return 0


def main(argv):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--target", required=True, help="repository to project into")
    ap.add_argument("--source", default=str(SOURCE_ROOT), help="Mercury checkout holding harness/ (default: this one)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    target, source = Path(args.target).resolve(), Path(args.source).resolve()
    try:
        if not target.is_dir():
            raise BuildError(f"target is not a directory: {target}")
        return run(source, target, args.apply)
    except BuildError as err:
        print(f"harness: {err}", file=sys.stderr)
        return 2
    except (ValueError, AttributeError, TypeError, OSError) as err:
        print(f"harness: invalid input or unreadable file: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
