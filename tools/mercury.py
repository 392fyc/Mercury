#!/usr/bin/env python3
"""Install Mercury packs into a target project.

    python tools/mercury.py check --target <dir>    compare only; any difference exits 1
    python tools/mercury.py apply --target <dir>    write the installation and the lock

The target selects packs in `<target>/.mercury/config.json`. Every file of a selected pack except its
`build/` directory is copied verbatim to `<target>/.mercury/packs/<pack>/`. Each skill the pack declares
also gets a thin entry (`.claude/skills/mercury-<skill>/SKILL.md`, `.agents/skills/mercury-<skill>/SKILL.md`)
that points at the full skill under `.mercury/packs/`. Every installed file's sha256 is recorded in
`<target>/.mercury/lock.json`. Hand edits to a managed file are never overwritten or deleted.
See packs/README.md for the rules.

Exit codes: 0 in sync / applied, 1 differences or conflicts, 2 invalid input or configuration.
"""
import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_REL = ".mercury/config.json"
LOCK_REL = ".mercury/lock.json"
PACKS_REL = ".mercury/packs"
HOSTS = {"codex": ".agents/skills", "claude": ".claude/skills"}
STUB_PREFIX = "mercury-"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SKIP_DIRS = {"__pycache__"}


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
RESERVED_PATHS = {LOCK_REL, CONFIG_REL}


def seg_key(seg):
    """Windows drops trailing dots and spaces from a segment and ignores case; compare that way."""
    return seg.rstrip(". ").lower()


def fold(rel):
    """The key under which Windows sees one path: every segment folded with seg_key."""
    return "/".join(seg_key(s) for s in rel.split("/"))


def plain_rel(rel, what):
    """A plain relative POSIX path: no drive, no absolute root, no empty, '.' or '..' segments, no odd characters."""
    if not isinstance(rel, str) or not rel or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
        raise BuildError(f"{what}: not a plain relative path: {rel!r}")
    for seg in rel.split("/"):
        if seg in ("", ".", "..") or not seg_key(seg) or seg_key(seg) in (".", ".."):
            raise BuildError(f"{what}: empty, '.' or '..' segment in path: {rel!r}")
        if seg != seg.rstrip(". "):
            # Windows drops a trailing dot or space, so "mercury-./x" would alias "mercury-/x".
            raise BuildError(f"{what}: path segment ends with a dot or space: {rel!r}")
        if any(ord(c) < 32 or c in BAD_CHARS for c in seg):
            raise BuildError(f"{what}: forbidden character (control, backslash, colon, tilde and similar) in path: {rel!r}")
        if seg_key(seg).split(".")[0].rstrip() in RESERVED:
            raise BuildError(f"{what}: reserved device name in path: {rel!r}")
    return rel


def check_rel(rel, what):
    """An installation path: plain, outside .git/, and not a reserved path (all compared Windows-style)."""
    plain_rel(rel, what)
    keys = [seg_key(s) for s in rel.split("/")]
    if ".git" in keys:
        raise BuildError(f"{what}: path must not be inside .git/: {rel!r}")
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
        raise BuildError(f"{what}: resolves outside packs/")


# --- the deletion namespace: .mercury/packs/** and the mercury-* skill entry directories ---

def managed_root_of(rel):
    """The root (as a tuple) that bounds deletion for this path, or None when the path is not in the namespace.

    Namespace: any file below .mercury/packs/, and any file below <skill root>/mercury-*/ where the
    skill roots are .agents/skills and .claude/skills. Compared exactly (case and dots included)."""
    parts = tuple(rel.split("/"))
    pk = tuple(PACKS_REL.split("/"))
    if parts[:2] == pk and len(parts) > 2:
        return pk
    for root in (tuple(v.split("/")) for v in HOSTS.values()):
        if parts[:2] == root and len(parts) > 3 and parts[2].startswith(STUB_PREFIX) and len(parts[2]) > len(STUB_PREFIX):
            return root
    return None


# --- skill frontmatter and entries ---

def frontmatter(path):
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    if not m:
        raise BuildError(f"{path.name}: no frontmatter")
    return m.group(1).split("\n")


def field_text(lines, key, what):
    """The value of a top-level frontmatter field as one line of text (plain, quoted and block scalars)."""
    idx = next((i for i, l in enumerate(lines) if l.startswith(key + ":")), None)
    if idx is None:
        raise BuildError(f"{what}: frontmatter needs {key}")
    first = lines[idx][len(key) + 1:].strip()
    cont = []
    for l in lines[idx + 1:]:
        if l[:1] in (" ", "\t") or not l.strip():
            cont.append(l.strip())
        else:
            break
    while cont and not cont[-1]:
        cont.pop()
    if re.match(r"^[|>][+-]?$", first):
        text = " ".join(c for c in cont if c)
    elif first[:1] == '"':
        if cont:
            raise BuildError(f"{what}: {key} must fit on one line when quoted")
        try:
            text = json.loads(first)
        except ValueError:
            raise BuildError(f"{what}: {key} is not a plain double-quoted string")
    elif first[:1] == "'":
        if cont or not first.endswith("'") or len(first) < 2:
            raise BuildError(f"{what}: {key} is not a one-line single-quoted string")
        text = first[1:-1].replace("''", "'")
    else:
        text = " ".join([first] + [c for c in cont if c])
    text = " ".join(text.split())
    if not text:
        raise BuildError(f"{what}: {key} is empty")
    return no_controls(text, f"{what}: {key}")


CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029]")


def no_controls(text, what):
    """C0/C1/DEL controls and the Unicode line separators would change how YAML reads a stub; refuse them."""
    if CONTROL_RE.search(text):
        raise BuildError(f"{what}: control character in frontmatter text")
    return text


def stub_name(skill):
    return skill if skill.startswith(STUB_PREFIX) else STUB_PREFIX + skill


def other_fields(lines):
    """Every top-level frontmatter field except name and description, as verbatim raw lines (continuations kept)."""
    out, keep = [], False
    for l in lines:
        m = re.match(r"^([A-Za-z0-9_-]+):", l)
        if m:
            keep = m.group(1) not in ("name", "description")
        elif l[:1] not in (" ", "\t") and l.strip():
            raise BuildError(f"unsupported frontmatter line: {l!r}")
        if keep:
            out.append(no_controls(l, "frontmatter"))
    return out


ARGS_TOKEN = "$" + "ARGUMENTS"


def uses_arguments(path):
    """True when the entry's body (after the frontmatter) contains the arguments placeholder."""
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n.*?\n---\n", text, flags=re.S)
    return ARGS_TOKEN in text[m.end():]


def stub_bytes(pack, skill, name, desc, extra, entry_file, args=False):
    skill_dir = f"{PACKS_REL}/{pack}/skills/{skill}/"
    fields = "".join(l + "\n" for l in extra)
    # Claude Code substitutes the placeholder only in the invoked SKILL.md, which is this stub, so the stub
    # carries the value over. The backslash keeps the second mention literal (the documented escape).
    carry = (f"Arguments: {ARGS_TOKEN}\n\nUse the value on the `Arguments:` line above wherever the entry "
             f"mentions `\\{ARGS_TOKEN}` (empty when none were given).\n\n") if args else ""
    body = (f"---\nname: {name}\ndescription: {json.dumps(desc, ensure_ascii=False)}\n{fields}---\n\n"
            f"# {name}\n\n{carry}"
            f"This skill is installed by Mercury (pack `{pack}`). The full skill lives in `{skill_dir}`.\n\n"
            f"Read `{skill_dir}{entry_file}` and follow it. Resolve every relative path it mentions "
            f"against `{skill_dir}`. This entry adds no permission and no separate workflow.\n")
    return body.encode("utf-8")


# --- sources: mercury.json, pack.json ---

def load_master(source):
    master, mhash = load_json(source / "mercury.json", "mercury.json")
    packs = master.get("packs") if isinstance(master, dict) and master.get("schema_version") == 1 else None
    if not isinstance(packs, dict) or not packs:
        raise BuildError("mercury.json: schema_version must be 1 and packs must be a non-empty object")
    for name, spec in packs.items():
        if not NAME_RE.match(name) or not isinstance(spec, dict) or not isinstance(spec.get("enabled"), bool) \
                or not isinstance(spec.get("required", False), bool):
            raise BuildError(f"mercury.json: pack {name!r} is malformed (needs a name and a boolean enabled)")
        if spec.get("required") and not spec["enabled"]:
            raise BuildError(f"mercury.json: required pack {name} cannot be disabled")
    return master, mhash


def load_config(target, master):
    cfg, chash = load_json(target / CONFIG_REL, CONFIG_REL)
    packs = cfg.get("packs") if isinstance(cfg, dict) else None
    if not isinstance(cfg, dict) or cfg.get("schema_version") != 1 or not isinstance(packs, list) \
            or not all(isinstance(p, str) for p in packs):
        raise BuildError(f"{CONFIG_REL}: expected {{\"schema_version\": 1, \"packs\": [names]}}")
    unknown = sorted(set(packs) - set(master["packs"]))
    if unknown:
        raise BuildError(f"{CONFIG_REL}: unknown pack(s): {', '.join(unknown)}")
    disabled = sorted(p for p in set(packs) if not master["packs"][p]["enabled"])
    if disabled:
        raise BuildError(f"{CONFIG_REL}: pack(s) disabled in mercury.json cannot be installed: {', '.join(disabled)}")
    required = {n for n, p in master["packs"].items() if p.get("required")}
    return sorted(set(packs) | required), chash


def load_pack(proot, pack):
    spec, _ = load_json(proot / "pack.json", f"pack {pack} pack.json")
    skills = spec.get("skills", {}) if isinstance(spec, dict) else None
    if not isinstance(spec, dict) or spec.get("schema_version") != 1 or spec.get("name") != pack or not isinstance(skills, dict):
        raise BuildError(f"pack {pack}: pack.json needs schema_version 1, name {pack!r} and a skills object")
    for skill, sspec in skills.items():
        if not NAME_RE.match(skill) or not isinstance(sspec, dict):
            raise BuildError(f"pack {pack}: skill {skill!r} is malformed")
        hosts = sspec.get("hosts")
        if not isinstance(hosts, list) or not hosts or not set(hosts) <= set(HOSTS):
            raise BuildError(f"pack {pack}: skill {skill}: hosts must be a non-empty subset of {sorted(HOSTS)}")
        entry = sspec.get("claude_entry")
        if entry is not None:
            plain_rel(entry, f"pack {pack}: skill {skill} claude_entry")
    return skills


def tracked_files(source, pack):
    """Files of packs/<pack> as recorded in the Git index of the Mercury checkout: {pack-relative path: mode}.

    The index, not the filesystem, decides what a pack contains, so untracked scratch files are never installed.
    Without a working git, or outside a repository, this is an error rather than a fallback to a directory walk."""
    prefix = f"packs/{pack}/"
    try:
        r = subprocess.run(["git", "-C", str(source), "ls-files", "-z", "-s", "--", prefix[:-1]], capture_output=True)
    except OSError as err:
        raise BuildError(f"pack {pack}: git is required to list the pack's files: {err}")
    if r.returncode != 0:
        raise BuildError(f"pack {pack}: cannot list files with git (is --source a Git checkout?): {r.stderr.decode('utf-8', 'replace').strip()}")
    out = {}
    for rec in r.stdout.decode("utf-8").split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        mode, _obj, stage = meta.split()
        if stage != "0" or not path.startswith(prefix):
            raise BuildError(f"pack {pack}: unmerged or unexpected index entry: {path!r}")
        if mode not in ("100644", "100755"):
            raise BuildError(f"pack {pack}: only regular files may be tracked (mode {mode}): {path!r}")
        out[path[len(prefix):]] = mode
    return out


def desired_files(source, master, selected):
    """Map installation path -> (bytes, executable) for the selected packs.

    The executable flag comes from the file's mode in the Git index (100755), on every platform."""
    out = {}
    proot_all = source / "packs"

    def add(rel, data, exe, owner):
        check_rel(rel, owner)
        if rel in out or any(fold(k) == fold(rel) for k in out):
            raise BuildError(f"{owner}: path installed twice: {rel}")
        out[rel] = (data, exe)

    for pack in selected:
        owner = f"pack {pack}"
        proot = proot_all / pack
        inside_source(proot_all, proot, owner)
        if is_link(proot) or not proot.is_dir():
            raise BuildError(f"{owner}: directory missing or a link: packs/{pack}")
        tracked = tracked_files(source, pack)
        if "pack.json" not in tracked:
            raise BuildError(f"{owner}: pack.json is not tracked by git")
        skills = load_pack(proot, pack)
        for rel, mode in sorted(tracked.items()):
            parts = rel.split("/")
            if parts[0] == "build" or SKIP_DIRS & set(parts):
                continue
            check_inside(source, f"packs/{pack}/{rel}", owner)
            f = proot / rel
            if is_link(f) or not f.is_file():
                raise BuildError(f"{owner}: tracked file missing or a link: {rel}")
            add(f"{PACKS_REL}/{pack}/{rel}", f.read_bytes(), mode == "100755", owner)
        for skill, sspec in sorted(skills.items()):
            sdir = proot / "skills" / skill
            if f"skills/{skill}/SKILL.md" not in tracked:
                raise BuildError(f"{owner}: skill {skill} has no tracked SKILL.md")
            entry = sspec.get("claude_entry")
            if entry is not None and f"skills/{skill}/{entry}" not in tracked:
                raise BuildError(f"{owner}: skill {skill}: claude_entry {entry} not found")
            name = stub_name(skill)
            for host in sspec["hosts"]:
                if host == "claude" and entry:
                    entry_file = entry
                else:
                    entry_file = "SKILL.md"
                fm = frontmatter(sdir / entry_file)
                desc = field_text(fm, "description", f"{owner}: {skill}/{entry_file}")
                add(f"{HOSTS[host]}/{name}/SKILL.md", stub_bytes(pack, skill, name, desc, other_fields(fm), entry_file,
                                                           uses_arguments(sdir / entry_file)), False, owner)
    return dict(sorted(out.items()))


def read_lock(target):
    path = target / LOCK_REL
    if not path.exists():
        return {}
    data, _ = load_json(path, "lock")
    files = data.get("files") if isinstance(data, dict) else None
    if data.get("schema_version") != 1 or not isinstance(files, dict):
        raise BuildError("lock: unexpected format")
    seen = {}
    for rel, h in files.items():
        check_rel(rel, "lock")
        # A tampered lock must not steer deletions outside the managed namespace.
        if not isinstance(h, str) or managed_root_of(rel) is None:
            raise BuildError(f"lock: entry outside the managed namespace or malformed: {rel!r}")
        # Windows sees two spellings that fold to one key as one file; this tool never writes such a lock.
        if fold(rel) in seen:
            raise BuildError(f"lock: entries {seen[fold(rel)]!r} and {rel!r} are the same path on Windows")
        seen[fold(rel)] = rel
    return files


def current_hash(target, rel):
    """None when absent, 'not-a-file' for anything that is not a plain file, else the digest."""
    p = target / rel
    if not os.path.lexists(p):
        return None
    if is_link(p) or not p.is_file():
        return "not-a-file"
    return digest(p.read_bytes())


EXE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
HAS_MODE_BITS = os.name != "nt"


def exe_differs(path, want_exe):
    """True when the file's owner-execute bit disagrees with the wanted state (only where the filesystem has mode bits)."""
    return HAS_MODE_BITS and bool(path.stat().st_mode & stat.S_IXUSR) != want_exe


def set_exe(path, exe):
    if HAS_MODE_BITS:
        mode = path.stat().st_mode
        path.chmod(mode | EXE_BITS if exe else mode & ~EXE_BITS)


def plan(source, target):
    master, mhash = load_master(source)
    selected, chash = load_config(target, master)
    want = desired_files(source, master, selected)
    lock = read_lock(target)
    check_inside(target, LOCK_REL, "lock")
    for rel in list(want) + list(lock):
        check_inside(target, rel, "target")
    # A lock spelling that folds to a wanted path but is spelled differently is a case-only rename.
    # On Windows both spellings are one file, so the old one must be removed before the new one is written.
    want_fold = {fold(r): r for r in want}
    renamed_from = {want_fold[fold(r)]: r for r in lock if r not in want and fold(r) in want_fold}
    writes, deletes, conflicts = [], [], []
    for rel, (data, exe) in want.items():
        d, cur = digest(data), current_hash(target, rel)
        old = renamed_from.get(rel)
        rec = lock.get(rel) if old is None else lock[old]
        if cur is None:
            writes.append((rel, "create"))
        elif cur == "not-a-file":
            conflicts.append(f"{rel}: exists but is not a regular file")
        elif cur == d:
            if old is not None:
                writes.append((rel, "rename"))  # same bytes, new spelling: still has to be rewritten after the delete
            elif exe_differs(target / rel, exe):
                writes.append((rel, "mode"))  # identical content, wrong executable bit
        elif rec is None:
            conflicts.append(f"{rel}: exists with different content and is not in the lock (not managed)")
        elif cur != rec:
            conflicts.append(f"{rel}: modified by hand since the last installation (lock hash differs)")
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
    lock_doc = {"schema_version": 1, "source": origin, "mercury_json_sha256": mhash, "config_sha256": chash,
                "files": {rel: digest(d) for rel, (d, _) in want.items()},
                "executable": sorted(rel for rel, (_, exe) in want.items() if exe)}
    lock_text = json.dumps(lock_doc, indent=2, sort_keys=True) + "\n"
    lp = target / LOCK_REL
    lock_same = lp.is_file() and lp.read_bytes().decode("utf-8").replace("\r\n", "\n") == lock_text
    return want, writes, deletes, conflicts, lock_text, lock_same


def prune_empty_dirs(target, rel):
    """Remove directories emptied by a deletion, up to but never including the managed root."""
    root = managed_root_of(rel)
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
        print("mercury: " + (f"{len(report)} difference(s)" if report else f"{len(want)} file(s) in sync"))
        return 1 if report else 0
    if conflicts:
        for line in report:
            print(line, file=sys.stderr)
        print("mercury: stopped, nothing written; resolve the conflicts above", file=sys.stderr)
        return 1
    # Deletes first: a case-only rename must remove the old spelling before the new one is written.
    for rel in deletes:
        (target / rel).unlink()
        prune_empty_dirs(target, rel)
    for rel, kind in writes:
        p = target / rel
        data, exe = want[rel]
        if kind != "mode":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        set_exe(p, exe)  # carry the executable bit where the filesystem has one (no effect on Windows)
    if not lock_same:
        lp = target / LOCK_REL
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_bytes(lock_text.encode("utf-8"))
    print(f"mercury: wrote {len(writes)}, deleted {len(deletes)}, {len(want)} file(s) managed")
    return 0


def main(argv):
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["check", "apply"])
    ap.add_argument("--target", required=True, help="project to install into")
    ap.add_argument("--source", default=str(SOURCE_ROOT), help="Mercury checkout holding packs/ (default: this one)")
    args = ap.parse_args(argv)
    target, source = Path(args.target).resolve(), Path(args.source).resolve()
    try:
        if not target.is_dir():
            raise BuildError(f"target is not a directory: {target}")
        return run(source, target, args.command == "apply")
    except BuildError as err:
        print(f"mercury: {err}", file=sys.stderr)
        return 2
    except (ValueError, AttributeError, TypeError, OSError) as err:
        print(f"mercury: invalid input or unreadable file: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
