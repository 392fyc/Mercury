#!/usr/bin/env python3
"""Install Mercury packs into a target project.

    python tools/mercury.py check --target <dir>    compare only; any difference exits 1
    python tools/mercury.py apply --target <dir>    write the installation and the lock
    python tools/mercury.py install --target <dir> [--packs a,b] [--source-commit <sha>] [--dry-run]
    python tools/mercury.py source add <name> <github-url> --rationale <text> [--ref <ref>]
    python tools/mercury.py source update <name> [--to <ref>]

The target selects packs in `<target>/.mercury/config.json`. Every file of a selected pack except its
`build/` directory is copied verbatim to `<target>/.mercury/packs/<pack>/`. Each skill the pack declares
also gets a thin entry (`.claude/skills/mercury-<skill>/SKILL.md`, `.agents/skills/mercury-<skill>/SKILL.md`)
that points at the full skill under `.mercury/packs/`. Role sources (`roles/<role>.md`) become
`.claude/agents/mercury-<role>.md` and `.codex/agents/mercury-<role>.toml`, rule files (`rules/mercury-*.rules`) go to
`.codex/rules/`, and the pack fragments `agents.md` / `claude.md` fill a managed section at the top of the target's
AGENTS.md / CLAUDE.md (bytes outside the markers are never touched). Every installed file's sha256 and every
section's hash is recorded in `<target>/.mercury/lock.json`. Hand edits to a managed file are never overwritten or
deleted. See packs/README.md for the rules.

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
import urllib.parse
from datetime import date
from pathlib import Path

sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1]
CONFIG_REL = ".mercury/config.json"
LOCK_REL = ".mercury/lock.json"
PACKS_REL = ".mercury/packs"
HOSTS = {"codex": ".agents/skills", "claude": ".claude/skills"}
STUB_PREFIX = "mercury-"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SKIP_DIRS = {"__pycache__"}
# Single-file deletion roots: <dir>/mercury-<name><ext> may be deleted, nothing else in <dir>.
FILE_ROOTS = {(".claude", "agents"): ".md", (".codex", "agents"): ".toml", (".codex", "rules"): ".rules"}
SECTION_FILES = {"agents": "AGENTS.md", "claude": "CLAUDE.md"}  # fragment name -> entry file that carries the section
GUARD_REL = f"{PACKS_REL}/core/hooks/readonly_guard.sh"


class BuildError(Exception):
    """Invalid input or configuration (exit code 2)."""


def digest(data):
    """sha256 of the content; CRLF is folded to LF for text so git's autocrlf cannot change it."""
    if b"\0" not in data:
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


def parse_json(raw, what, name):
    try:
        return json.loads(raw.decode("utf-8")), digest(raw)
    except ValueError as err:
        raise BuildError(f"cannot read {what} ({name}): {err}")


def load_json(path, what):
    try:
        raw = path.read_bytes()
    except OSError as err:
        raise BuildError(f"cannot read {what} ({path.name}): {err}")
    return parse_json(raw, what, path.name)


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

    Namespace: any file below .mercury/packs/, any file below <skill root>/mercury-*/ where the skill roots
    are .agents/skills and .claude/skills, and the single files .claude/agents/mercury-*.md,
    .codex/agents/mercury-*.toml and .codex/rules/mercury-*.rules. Compared exactly (case and dots included)."""
    parts = tuple(rel.split("/"))
    pk = tuple(PACKS_REL.split("/"))
    if parts[:2] == pk and len(parts) > 2:
        return pk
    ext = FILE_ROOTS.get(parts[:2])
    if ext and len(parts) == 3 and parts[2].startswith(STUB_PREFIX) and parts[2].endswith(ext)             and len(parts[2]) > len(STUB_PREFIX) + len(ext):
        return parts[:2]
    for root in (tuple(v.split("/")) for v in HOSTS.values()):
        if parts[:2] == root and len(parts) > 3 and parts[2].startswith(STUB_PREFIX) and len(parts[2]) > len(STUB_PREFIX):
            return root
    return None


# --- skill frontmatter and entries ---

def frontmatter(raw, name):
    text = raw.decode("utf-8").replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.S)
    if not m:
        raise BuildError(f"{name}: no frontmatter")
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


HIDDEN = r"\ud800-\udfff\u202a-\u202e\u2066-\u2069\ufeff"  # lone surrogates, bidi controls, byte order mark
CONTROL_RE = re.compile(r"[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029" + HIDDEN + "]")


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


def uses_arguments(raw):
    """True when the entry's body (after the frontmatter) contains the arguments placeholder."""
    text = raw.decode("utf-8").replace("\r\n", "\n")
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

SOURCE_FIELDS = ("name", "url", "commit", "license", "added", "rationale")
PERMISSIVE = ("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense")
GITHUB_URL_RE = re.compile(r"^https://github\.com/([A-Za-z0-9][A-Za-z0-9-]*)/([A-Za-z0-9._-]+?)(?:\.git)?/?\Z")
SHA_RE = re.compile(r"^[0-9a-f]{40}\Z")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")


def parse_github(url):
    """(owner, repo) from https://github.com/<owner>/<repo>[.git], or None. The repo name may not be ., .. or .git."""
    m = GITHUB_URL_RE.match(url) if isinstance(url, str) else None
    if not m or m.group(2) in (".", "..", ".git") or m.group(2).lower().endswith(".git"):
        return None
    return m.group(1), m.group(2)


def plain_line(value, what):
    """One short line of plain text without control or hidden characters."""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 400:
        raise BuildError(f"{what}: must be a non-empty single-line string of at most 400 characters without edge spaces")
    no_controls(value, what)
    return value


def load_sources(items):
    """The `sources` record of mercury.json: one entry per pinned external project under sources/."""
    if not isinstance(items, list):
        raise BuildError("mercury.json: sources must be a list")
    out, seen = [], set()
    for i, item in enumerate(items):
        what = f"mercury.json: sources[{i}]"
        if not isinstance(item, dict) or set(item) != set(SOURCE_FIELDS):
            raise BuildError(f"{what}: needs exactly the fields {', '.join(SOURCE_FIELDS)}")
        if not isinstance(item["name"], str) or not NAME_RE.match(item["name"]) or item["name"].lower() in seen:
            raise BuildError(f"{what}.name: must be a unique plain name")
        seen.add(item["name"].lower())
        parsed = parse_github(item["url"])
        if not parsed or item["url"] != f"https://github.com/{parsed[0]}/{parsed[1]}.git":
            raise BuildError(f"{what}.url: must be https://github.com/<owner>/<repo>.git")
        if not isinstance(item["commit"], str) or not SHA_RE.match(item["commit"]):
            raise BuildError(f"{what}.commit: must be a full lowercase 40-digit commit id")
        if item["license"] not in PERMISSIVE:
            raise BuildError(f"{what}.license: must be one of {', '.join(PERMISSIVE)}")
        if not isinstance(item["added"], str) or not DATE_RE.match(item["added"]):
            raise BuildError(f"{what}.added: must be a YYYY-MM-DD date")
        plain_line(item["rationale"], f"{what}.rationale")
        out.append(item)
    return out


def master_hash(master):
    """Hash of the pack switch alone: the `sources` bookkeeping must not change what an installation records."""
    core = {k: v for k, v in master.items() if k != "sources"}
    return hashlib.sha256(json.dumps(core, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def load_master(source):
    """The master switch from a source object (working tree or commit) or from a checkout directory."""
    if hasattr(source, "master_raw"):
        raw = source.master_raw()
    else:
        try:
            raw = (Path(source) / "mercury.json").read_bytes()
        except OSError as err:
            raise BuildError(f"cannot read mercury.json (mercury.json): {err}")
    master, _ = parse_json(raw, "mercury.json", "mercury.json")
    packs = master.get("packs") if isinstance(master, dict) and master.get("schema_version") == 1 else None
    if not isinstance(packs, dict) or not packs:
        raise BuildError("mercury.json: schema_version must be 1 and packs must be a non-empty object")
    for name, spec in packs.items():
        if not NAME_RE.match(name) or not isinstance(spec, dict) or not isinstance(spec.get("enabled"), bool) \
                or not isinstance(spec.get("required", False), bool):
            raise BuildError(f"mercury.json: pack {name!r} is malformed (needs a name and a boolean enabled)")
        if spec.get("required") and not spec["enabled"]:
            raise BuildError(f"mercury.json: required pack {name} cannot be disabled")
    load_sources(master.get("sources", []))
    return master, master_hash(master)


LINK_FIELDS = ("name", "purpose", "locate", "access")
LINK_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
URL_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s`'\"()<>\[\]]*")
# Machine-specific locations, searched in the whole text: file: URLs, a drive letter not glued to a word, %VAR%,
# $HOME-like variables, ~ as a home directory, and a slash or backslash that starts a path (not "and/or", not "./x").
LINK_PATHS = [re.compile(p, re.I) for p in (
    r"\bfile:", r"(?<![A-Za-z0-9])[A-Za-z]:(?=\S)", r"%[A-Za-z_][A-Za-z0-9_ ]*%",
    r"\$\{?(?:HOME|USERPROFILE|HOMEPATH|HOMEDRIVE|APPDATA|LOCALAPPDATA|TEMP|TMP)\b", r"\$\{?[A-Za-z_][A-Za-z0-9_]*\}?[/\\]",
    r"(?<![A-Za-z0-9])~(?=[/\\]|$|\s)", r"(?<![A-Za-z0-9._-])[/\\]", r"(?<![A-Za-z0-9])\\", r"(?<![A-Za-z0-9])\.{3,}[/\\]",
)]
CONFIG_KEYS = {"schema_version", "packs", "linked_repos", "params"}
PARAM_KEYS = {"guard_write_scripts"}
SCRIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:sh|ps1|py)$")


def link_text(value, what):
    """One line of plain text for the managed section: no control or line-separator characters, no markup that could
    close the section, and no machine path (absolute, drive, home or file URL) in any word."""
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 400:
        raise BuildError(f"{what}: must be a non-empty single-line string of at most 400 characters without edge spaces")
    no_controls(value, what)
    if "<!--" in value or "-->" in value or "MERCURY:" in value:
        raise BuildError(f"{what}: must not contain a comment marker or a Mercury marker")
    probe = URL_RE.sub("url", value) if not re.search(r"\bfile:", value, re.I) else value
    for pattern in LINK_PATHS:
        m = pattern.search(probe)
        if m:
            raise BuildError(f"{what}: machine paths are not allowed (name a config key or describe the location): {probe[max(0, m.start() - 8):m.end() + 12]!r}")
    return value


def load_linked(items):
    if not isinstance(items, list) or len(items) > 20:
        raise BuildError(f"{CONFIG_REL}: linked_repos must be a list of at most 20 entries")
    out, seen = [], set()
    for i, item in enumerate(items):
        what = f"{CONFIG_REL}: linked_repos[{i}]"
        if not isinstance(item, dict) or set(item) != set(LINK_FIELDS):
            raise BuildError(f"{what}: needs exactly the fields {', '.join(LINK_FIELDS)}")
        for field in LINK_FIELDS:
            link_text(item[field], f"{what}.{field}")
        if not LINK_NAME_RE.match(item["name"]) or item["name"].lower() in seen:
            raise BuildError(f"{what}.name: must be a unique plain name (letters, digits, . _ -)")
        seen.add(item["name"].lower())
        out.append({f: item[f] for f in LINK_FIELDS})
    return out


def load_params(params):
    if not isinstance(params, dict) or not set(params) <= PARAM_KEYS:
        raise BuildError(f"{CONFIG_REL}: params may only hold: {', '.join(sorted(PARAM_KEYS))}")
    scripts = params.get("guard_write_scripts", [])
    if not isinstance(scripts, list) or not all(isinstance(x, str) and SCRIPT_RE.match(x) for x in scripts):
        raise BuildError(f"{CONFIG_REL}: params.guard_write_scripts must be a list of script file names (no directories)")
    return {"guard_write_scripts": scripts} if "guard_write_scripts" in params else {}


def load_config(target, master):
    """(packs in install order, config hash, {linked_repos, params}) from <target>/.mercury/config.json."""
    try:
        raw = (target / CONFIG_REL).read_bytes()
    except OSError as err:
        raise BuildError(f"cannot read {CONFIG_REL} ({Path(CONFIG_REL).name}): {err}")
    return parse_config(raw, master)


def parse_config(raw, master):
    """The same from the config's bytes. Core comes first, then the config's order."""
    cfg, chash = parse_json(raw, CONFIG_REL, Path(CONFIG_REL).name)
    packs = cfg.get("packs") if isinstance(cfg, dict) else None
    if not isinstance(cfg, dict) or cfg.get("schema_version") != 1 or not isinstance(packs, list) \
            or not all(isinstance(p, str) for p in packs):
        raise BuildError(f"{CONFIG_REL}: expected {{\"schema_version\": 1, \"packs\": [names]}}")
    if set(cfg) - CONFIG_KEYS:
        raise BuildError(f"{CONFIG_REL}: unknown key(s): {', '.join(sorted(set(cfg) - CONFIG_KEYS))}")
    unknown = sorted(set(packs) - set(master["packs"]))
    if unknown:
        raise BuildError(f"{CONFIG_REL}: unknown pack(s): {', '.join(unknown)}")
    disabled = sorted(p for p in set(packs) if not master["packs"][p]["enabled"])
    if disabled:
        raise BuildError(f"{CONFIG_REL}: pack(s) disabled in mercury.json cannot be installed: {', '.join(disabled)}")
    required = [n for n, p in master["packs"].items() if p.get("required")]
    ordered = []
    for name in ["core"] + packs + sorted(required):
        if name not in ordered and (name in packs or name in required):
            ordered.append(name)
    extras = {"linked_repos": load_linked(cfg.get("linked_repos", [])), "params": load_params(cfg.get("params", {}))}
    return ordered, chash, extras


def load_pack(proot, pack):
    """The skills object of pack.json; proot is a pack directory or a function returning pack.json's bytes."""
    if callable(proot):
        spec, _ = parse_json(proot(), f"pack {pack} pack.json", "pack.json")
    else:
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


# --- where the pack content comes from: the working tree (Mercury itself) or one commit (everything else) ---

def git_bytes(root, args, what, stdin=None):
    """stdout of `git -C root <args>`; any failure is a BuildError."""
    try:
        r = subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True)
    except OSError as err:
        raise BuildError(f"{what}: git is required: {err}")
    if r.returncode != 0:
        raise BuildError(f"{what}: {r.stderr.decode('utf-8', 'replace').strip() or 'git failed'}")
    return r.stdout


def resolve_commit(root, spec):
    """The full commit id that `spec` names in the Mercury repository at root (an id, a branch, HEAD ...)."""
    if not isinstance(spec, str) or not spec or spec.startswith("-") or any(c.isspace() or ord(c) < 32 for c in spec):
        raise BuildError(f"not a usable commit name: {spec!r}")
    out = git_bytes(root, ["rev-parse", "--verify", "--quiet", "--end-of-options", spec + "^{commit}"],
                    f"source commit {spec}").decode("utf-8").strip()
    if not SHA_RE.match(out):
        raise BuildError(f"source commit {spec}: cannot resolve to a full commit id (is it fetched in --source?)")
    return out


REMOTE_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*://(?:[^/@]*@)?[^/]+/|[^/@\s]+@[^/:\s]+:)([^/\s]+)/([^/\s]+?)(?:\.git)?/?\Z")


def normalise_repo(url):
    """owner/name from a Git remote URL (https, ssh:// or scp-like); anything else is an error."""
    m = REMOTE_RE.match(url.strip())
    if not m or not all(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*\Z", g) for g in m.groups()):
        # The URL is not echoed: it may carry a user name, password or token.
        raise BuildError("cannot read owner/name from the url of the `origin` remote of the source (url not shown, it may hold credentials)")
    return f"{m.group(1)}/{m.group(2)}"


class WorkTreeSource:
    """Mercury's own checkout: the file list comes from the Git index and the content from the working tree."""
    kind = "worktree"

    def __init__(self, root):
        self.root = root
        self.packs = root / "packs"

    def master_raw(self):
        try:
            return (self.root / "mercury.json").read_bytes()
        except OSError as err:
            raise BuildError(f"cannot read mercury.json (mercury.json): {err}")

    def tracked(self, pack):
        proot = self.packs / pack
        inside_source(self.packs, proot, f"pack {pack}")
        if is_link(proot) or not proot.is_dir():
            raise BuildError(f"pack {pack}: directory missing or a link: packs/{pack}")
        return tracked_files(self.root, pack)

    def read(self, pack, rel):
        check_inside(self.root, f"packs/{pack}/{rel}", f"pack {pack}")
        f = self.packs / pack / rel
        if is_link(f) or not f.is_file():
            raise BuildError(f"pack {pack}: tracked file missing or a link: {rel}")
        return f.read_bytes()

    def origin(self):
        r = subprocess.run(["git", "-C", str(self.root), "rev-parse", "HEAD"], capture_output=True, text=True)
        head = r.stdout.strip() if r.returncode == 0 else ""
        return {"source": f"mercury@{head}" if head else "mercury"}


class CommitSource:
    """One Mercury commit: file list, modes and bytes all come from its Git tree, never from the working tree."""
    kind = "commit"

    def __init__(self, root, commit):
        self.root, self.commit = root, commit
        self.oids, self.blobs = {}, {}

    def master_raw(self):
        return git_bytes(self.root, ["cat-file", "blob", f"{self.commit}:mercury.json"], f"mercury.json at {self.commit[:12]}")

    def tracked(self, pack):
        prefix = f"packs/{pack}/"
        raw = git_bytes(self.root, ["ls-tree", "-r", "-z", "--full-tree", self.commit, "--", prefix],
                        f"pack {pack}: cannot list the commit tree")
        out = {}
        for rec in raw.decode("utf-8").split("\0"):
            if not rec:
                continue
            meta, _, path = rec.partition("\t")
            mode, kind, oid = meta.split()
            if not path.startswith(prefix):
                raise BuildError(f"pack {pack}: unexpected tree entry: {path!r}")
            if kind != "blob" or mode not in ("100644", "100755"):
                raise BuildError(f"pack {pack}: only regular files may be tracked (mode {mode}): {path!r}")
            out[path[len(prefix):]] = mode
            self.oids[(pack, path[len(prefix):])] = oid
        if not out:
            raise BuildError(f"pack {pack}: packs/{pack} is not in commit {self.commit[:12]}")
        wanted = sorted({self.oids[(pack, rel)] for rel in out if rel.split("/")[0] != "build"} - set(self.blobs))
        if wanted:
            self.fetch(wanted)
        return out

    def fetch(self, oids):
        """Read many blobs with one `git cat-file --batch` process."""
        raw = git_bytes(self.root, ["cat-file", "--batch"], "reading the commit tree", stdin="".join(o + "\n" for o in oids).encode())
        pos = 0
        for oid in oids:
            nl = raw.index(b"\n", pos)
            head = raw[pos:nl].decode().split()
            if len(head) != 3 or head[0] != oid or head[1] != "blob":
                raise BuildError(f"reading the commit tree: unexpected answer for {oid}: {raw[pos:nl]!r}")
            size = int(head[2])
            self.blobs[oid] = raw[nl + 1:nl + 1 + size]
            pos = nl + 1 + size + 1

    def read(self, pack, rel):
        oid = self.oids.get((pack, rel))
        if oid is None:
            raise BuildError(f"pack {pack}: {rel} is not in commit {self.commit[:12]}")
        if oid not in self.blobs:
            self.fetch([oid])
        return self.blobs[oid]

    def is_published(self):
        """True when the commit is reachable from a remote-tracking branch of origin (as last fetched)."""
        out = git_bytes(self.root, ["for-each-ref", "--format=%(refname)", "--count=1", "--contains", self.commit,
                                    "refs/remotes/origin/"], "checking that the source commit is on origin")
        return bool(out.strip())

    def origin(self):
        r = subprocess.run(["git", "-C", str(self.root), "remote", "get-url", "origin"], capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            raise BuildError("the Mercury source has no `origin` remote, so source_repo cannot be recorded")
        return {"source": f"mercury@{self.commit}", "source_repo": normalise_repo(r.stdout), "source_commit": self.commit}


# --- roles: one source, two host formats ---

ROLE_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ROLE_SECTIONS = {
    "claude": {"model": "token", "tools": "text", "disallowedTools": "text", "permissionMode": "enum", "readonly_guard": "bool"},
    "codex": {"model": "token", "model_reasoning_effort": "enum", "sandbox_mode": "enum", "web_search": "enum"},
}
ROLE_ENUMS = {
    "permissionMode": {"default", "acceptEdits", "auto", "dontAsk", "plan"},
    "model_reasoning_effort": {"minimal", "low", "medium", "high", "xhigh", "max"},
    "sandbox_mode": {"read-only", "workspace-write"},
    "web_search": {"live", "cached", "disabled"},
}
ROLE_REQUIRED = {"claude": {"model"}, "codex": {"sandbox_mode"}}
BODY_BAD = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029" + HIDDEN + "]")


def role_value(raw, what):
    """One frontmatter value: true/false, a JSON string, or a bare token. Anything else is refused."""
    if raw in ("true", "false"):
        return raw == "true"
    if raw.startswith('"'):
        try:
            val = json.loads(raw)
        except ValueError:
            raise BuildError(f"{what}: not a valid double-quoted string")
        if not isinstance(val, str):
            raise BuildError(f"{what}: not a string")
        return no_controls(val, what)
    if TOKEN_RE.match(raw):
        return raw
    raise BuildError(f"{what}: unsupported value {raw!r} (use true/false, a double-quoted string or a plain token)")


def parse_role(data, what):
    """{'description', 'claude': {...}, 'codex': {...}, 'body'} from a role source; unknown keys and odd syntax are errors."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise BuildError(f"{what}: not valid UTF-8")
    text = text.replace("\r\n", "\n")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, flags=re.S)
    if not m:
        raise BuildError(f"{what}: no frontmatter")
    role, section = {}, None
    for line in m.group(1).split("\n"):
        if not line.strip():
            continue
        top = re.match(r"^([A-Za-z_]+):(?: (.*))?$", line)
        sub = re.match(r"^  ([A-Za-z_]+): (.+)$", line)
        if top and not line.startswith(" "):
            key, val = top.group(1), top.group(2)
            if key in ROLE_SECTIONS and val is None:
                if key in role:
                    raise BuildError(f"{what}: {key} declared twice")
                role[key], section = {}, key
            elif key == "description" and val is not None and key not in role:
                role[key], section = role_value(val, f"{what}: description"), None
            else:
                raise BuildError(f"{what}: unexpected or repeated frontmatter key {key!r}")
        elif sub and section:
            key = sub.group(1)
            kind = ROLE_SECTIONS[section].get(key)
            if kind is None or key in role[section]:
                raise BuildError(f"{what}: unknown or repeated {section} field {key!r}")
            val = role_value(sub.group(2), f"{what}: {section}.{key}")
            if (kind == "bool") != isinstance(val, bool) or (kind == "token" and not TOKEN_RE.match(val)) \
                    or (kind == "enum" and val not in ROLE_ENUMS[key]) or (kind == "text" and not val.strip()):
                raise BuildError(f"{what}: bad value for {section}.{key}")
            role[section][key] = val
        else:
            raise BuildError(f"{what}: unsupported frontmatter line: {line!r}")
    if not isinstance(role.get("description"), str) or not role["description"].strip():
        raise BuildError(f"{what}: description is required")
    hosts = [h for h in ROLE_SECTIONS if h in role]
    if not hosts:
        raise BuildError(f"{what}: declare at least one of: {', '.join(ROLE_SECTIONS)}")
    for h in hosts:
        missing = ROLE_REQUIRED[h] - set(role[h])
        if missing:
            raise BuildError(f"{what}: {h} section needs: {', '.join(sorted(missing))}")
    body = m.group(2).strip("\n")
    if not body.strip():
        raise BuildError(f"{what}: empty body")
    if BODY_BAD.search(body):
        raise BuildError(f"{what}: control character in the body")
    role["body"] = body + "\n"
    return role


def role_claude(name, src, role):
    c = role["claude"]
    q = lambda v: json.dumps(v, ensure_ascii=False)
    fm = [f"name: {q(name)}", f"description: {q(role['description'])}", f"model: {q(c['model'])}"]
    for key in ("tools", "disallowedTools", "permissionMode"):
        if key in c:
            fm.append(f"{key}: {q(c[key])}")
    if c.get("readonly_guard"):
        # Shell form under bash: ${CLAUDE_PROJECT_DIR} is substituted by Claude Code (documented, verified), `sh` needs
        # no executable bit. `|| exit 2` turns a launcher that cannot run or exits non-zero into a block. A timeout or a
        # shell that cannot start is not an exit code: only `onFailure: "block"` (documented for Claude Code 2.1.295 and
        # later; older versions ignore the unknown field, verified on 2.1.291) blocks the call in those cases.
        cmd = f'sh "${{CLAUDE_PROJECT_DIR}}/{GUARD_REL}" || exit 2'
        fm += ["hooks:", "  PreToolUse:", '    - matcher: "Bash|PowerShell"', "      hooks:", "        - type: command",
               "          shell: bash", f"          command: {q(cmd)}", "          timeout: 60", '          onFailure: "block"']
    note = f"<!-- Generated by tools/mercury.py from {PACKS_REL}/{src}. Do not edit; change the source and run `python tools/mercury.py apply`. -->"
    return ("---\n" + "\n".join(fm) + f"\n---\n\n{note}\n\n" + role["body"]).encode("utf-8")


def role_codex(name, src, role):
    c = role["codex"]
    q = lambda v: json.dumps(v, ensure_ascii=False)
    if "'''" in role["body"]:
        raise BuildError(f"{src}: the body contains ''' and cannot be written as a TOML literal string")
    lines = [f"# Generated by tools/mercury.py from {PACKS_REL}/{src}. Do not edit; change the source and run `python tools/mercury.py apply`.",
             f"name = {q(name)}", f"description = {q(role['description'])}"]
    for key in ("model", "model_reasoning_effort", "sandbox_mode", "web_search"):
        if key in c:
            lines.append(f"{key} = {q(c[key])}")
    out = "\n".join(lines) + "\n\ndeveloper_instructions = '''\n" + role["body"] + "'''\n"
    try:
        import tomllib
        got = tomllib.loads(out)
        if got.get("developer_instructions") != role["body"] or got.get("name") != name or got.get("description") != role["description"]:
            raise BuildError(f"{src}: generated TOML does not read back to the source")
    except ImportError:
        pass
    except ValueError as err:
        raise BuildError(f"{src}: generated TOML is invalid: {err}")
    return out.encode("utf-8")


def role_files(src, pack, tracked):
    """{installation path: bytes} for every roles/<role>.md of the pack; both host formats."""
    out = {}
    for rel in sorted(tracked):
        parts = rel.split("/")
        if parts[0] != "roles":
            continue
        if len(parts) != 2 or not parts[1].endswith(".md") or not ROLE_NAME_RE.match(parts[1][:-3]):
            raise BuildError(f"pack {pack}: roles/ holds only <role>.md files with a lowercase name: {rel}")
        role_name = parts[1][:-3]
        what = f"pack {pack}: {rel}"
        role = parse_role(src.read(pack, rel), what)
        name = stub_name(role_name)
        if "claude" in role:
            out[f".claude/agents/{name}.md"] = role_claude(name, f"{pack}/{rel}", role)
        if "codex" in role:
            out[f".codex/agents/{name}.toml"] = role_codex(name, f"{pack}/{rel}", role)
    return out


# --- managed sections in AGENTS.md / CLAUDE.md ---

MARK_RE = re.compile(r"MERCURY:(BEGIN|END)")
BEGIN_TEXT = "<!-- MERCURY:BEGIN managed by tools/mercury.py from the packs in .mercury/config.json; edits here stop the installer, change the pack and run apply -->"
END_TEXT = "<!-- MERCURY:END -->"


def fragment(src, pack, rel):
    """A pack's fragment as LF text with no trailing blank lines; markers inside it are refused."""
    try:
        text = src.read(pack, rel).decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise BuildError(f"{rel}: not valid UTF-8")
    if BODY_BAD.search(text.replace("\n", "")) or MARK_RE.search(text) or not text.strip():
        raise BuildError(f"{rel}: empty, contains a control or hidden character, or contains a Mercury marker")
    return text.strip("\n")


def render_linked(repos):
    if not repos:
        return ""
    parts = ["## Linked repositories", "",
             "Declared in `linked_repos` in `.mercury/config.json`; Mercury renders this section from it. "
             "Access beyond what a repository's entry allows needs its own authorized scope.", ""]
    for r in repos:
        parts += [f"### {r['name']}", "", f"- Purpose: {r['purpose']}", f"- Locate: {r['locate']}", f"- Access: {r['access']}", ""]
    return "\n".join(parts).rstrip("\n")


def build_sections(frags, repos):
    """{entry file: managed section text (LF, markers included, no trailing newline)} from {pack: {'agents'|'claude': text}} in install order."""
    out = {}
    for kind, entry in SECTION_FILES.items():
        body = [f[kind] for f in frags.values() if kind in f]
        if kind == "agents" and repos:
            body.append(render_linked(repos))
        text = body[0] if body else ""
        for part in body[1:]:
            both_bullets = text.rsplit("\n", 1)[-1].startswith("- ") and part.startswith("- ")
            text += ("\n" if both_bullets else "\n\n") + part
        out[entry] = "\n".join([BEGIN_TEXT, text, END_TEXT])
    return out


def find_block(text, name):
    """(start, end) of the managed block in text, or None when there are no markers. Anything unbalanced is an error."""
    marks = [(m.start(), m.group(1)) for m in MARK_RE.finditer(text)]
    if not marks:
        return None
    if [k for _, k in marks] != ["BEGIN", "END"]:
        raise BuildError(f"{name}: Mercury markers are unbalanced or appear more than once")
    b, e = marks[0][0], marks[1][0]
    bl = text.rfind("\n", 0, b) + 1
    be = text.find("\n", b)
    begin_line = text[bl:be if be != -1 else len(text)].rstrip("\r")
    el = text.rfind("\n", 0, e) + 1
    ee = text.find("\n", e)
    end_line = text[el:ee if ee != -1 else len(text)].rstrip("\r")
    if not (begin_line.startswith("<!-- MERCURY:BEGIN") and begin_line.endswith("-->")) or end_line != END_TEXT:
        raise BuildError(f"{name}: Mercury markers must each sit alone on a line in the documented form")
    return bl, el + len(END_TEXT)


def nl_of(text):
    """The line ending of the first line of text: CRLF if that line ends with CRLF, else LF."""
    i = text.find("\n")
    return "\r\n" if i > 0 and text[i - 1] == "\r" else "\n"


def read_entry(path, name):
    if is_link(path) or not path.is_file():
        raise BuildError(f"{name}: exists but is not a regular file")
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise BuildError(f"{name}: starts with a byte order mark; remove it so the section can sit above the content")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BuildError(f"{name}: not valid UTF-8")


def plan_sections(target, want, lock_sections):
    """([(entry, kind, new bytes, where)], [conflicts]); reads only. kind: create | insert | update; where says it in words."""
    actions, conflicts = [], []
    for name, block in want.items():
        if os.path.lexists(tmp_name(target / name)):
            raise BuildError(f"{name}: the temporary name {tmp_name(target / name).name} already exists; remove it and run again")
        d, rec, p = digest(block.encode("utf-8")), lock_sections.get(name), target / name
        if not os.path.lexists(p):
            actions.append((name, "create", (block + "\n").encode("utf-8"), "new file that holds only the managed section"))
            continue
        text = read_entry(p, name)
        span = find_block(text, name)
        if span is None:
            if rec is not None:
                conflicts.append(f"{name}: the Mercury section markers are gone (edited by hand); restore them or the section")
            else:
                eol = nl_of(text)
                head = block.replace("\n", eol)
                new = head + eol + (eol + text if text else "")
                n = len(text.splitlines())
                where = (f"managed section goes to the top (line 1), above the existing {n} line(s), which stay byte for byte"
                         if n else "managed section goes into the empty file")
                actions.append((name, "insert", new.encode("utf-8"), where))
            continue
        cur = digest(text[span[0]:span[1]].encode("utf-8"))
        if cur == d:
            continue
        if rec is None:
            conflicts.append(f"{name}: has a Mercury section that is not in the lock (not managed)")
        elif cur != rec:
            conflicts.append(f"{name}: the Mercury section was modified by hand since the last installation (lock hash differs)")
        else:
            eol = nl_of(text[span[0]:])
            new = text[:span[0]] + block.replace("\n", eol) + text[span[1]:]
            first = text.count("\n", 0, span[0]) + 1
            last = text.count("\n", 0, span[1]) + 1
            actions.append((name, "update", new.encode("utf-8"),
                            f"managed section replaced in place (lines {first}-{last}); bytes outside it untouched"))
    return actions, conflicts


def tmp_name(path):
    return path.with_name(path.name + ".mercury-tmp")


def atomic_write(target, name, data):
    """Replace target/name without ever leaving it half written. The temporary file is created exclusively (an existing
    file or link of that name is refused, never followed or overwritten) and removed again if anything fails."""
    path, tmp = target / name, tmp_name(target / name)
    check_inside(target, tmp.name, f"{name} temporary file")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(tmp, flags, 0o666)
    except FileExistsError:
        raise BuildError(f"{name}: the temporary name {tmp.name} already exists; remove it and run again")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        if path.exists():
            os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)


def desired_files(src, selected):
    """(installation path -> (bytes, executable), {pack: {'agents'|'claude': fragment text}}) for the selected packs.

    The executable flag comes from the file's mode in the Git index (100755), on every platform."""
    out, frags = {}, {}

    def add(rel, data, exe, owner):
        check_rel(rel, owner)
        if rel in out or any(fold(k) == fold(rel) for k in out):
            raise BuildError(f"{owner}: path installed twice: {rel}")
        out[rel] = (data, exe)

    for pack in selected:
        owner = f"pack {pack}"
        tracked = src.tracked(pack)
        if "pack.json" not in tracked:
            raise BuildError(f"{owner}: pack.json is not tracked by git")
        skills = load_pack(lambda: src.read(pack, "pack.json"), pack)
        for kind in SECTION_FILES:
            if f"{kind}.md" in tracked:
                frags.setdefault(pack, {})[kind] = fragment(src, pack, f"{kind}.md")
            elif pack == "core":
                raise BuildError(f"{owner}: core must provide {kind}.md (the fragment of the managed section)")
        for dest, data in role_files(src, pack, tracked).items():
            add(dest, data, False, owner)
        for rel in sorted(tracked):
            if rel.startswith("rules/"):
                rule = rel[len("rules/"):]
                if "/" in rule or not rule.startswith(STUB_PREFIX) or not rule.endswith(".rules") or len(rule) <= len(STUB_PREFIX) + 6:
                    raise BuildError(f"{owner}: rules/ holds only mercury-<name>.rules files: {rel}")
                add(f".codex/rules/{rule}", src.read(pack, rel), False, owner)
        for rel, mode in sorted(tracked.items()):
            parts = rel.split("/")
            if parts[0] == "build" or SKIP_DIRS & set(parts):
                continue
            add(f"{PACKS_REL}/{pack}/{rel}", src.read(pack, rel), mode == "100755", owner)
        for skill, sspec in sorted(skills.items()):
            sdir = f"skills/{skill}/"
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
                raw = src.read(pack, sdir + entry_file)
                fm = frontmatter(raw, entry_file)
                desc = field_text(fm, "description", f"{owner}: {skill}/{entry_file}")
                add(f"{HOSTS[host]}/{name}/SKILL.md", stub_bytes(pack, skill, name, desc, other_fields(fm), entry_file,
                                                           uses_arguments(raw)), False, owner)
    return dict(sorted(out.items())), frags


def read_lock(target):
    path = target / LOCK_REL
    if not path.exists():
        return {}, {}
    data, _ = load_json(path, "lock")
    files = data.get("files") if isinstance(data, dict) else None
    sections = data.get("sections", {}) if isinstance(data, dict) else None
    if data.get("schema_version") != 1 or not isinstance(files, dict) or not isinstance(sections, dict)             or not set(sections) <= set(SECTION_FILES.values()) or not all(isinstance(h, str) for h in sections.values()):
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
    return files, sections


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




# --- v1 template adoption (.codex/mercury-template.lock) ---

V1_LOCK_REL = ".codex/mercury-template.lock"
V1_GROUPS = {"agents": ".toml", "rules": ".rules", "project": ".md"}
V1_KEEP_DIRS = {(".codex", "agents"), (".codex", "rules")}
HEX64_RE = re.compile(r"^[0-9a-f]{64}\Z")


def read_v1(target):
    """{project-relative path: raw sha256} for the files a v1 template lock lists, or None when there is no v1 lock.

    v1 hashes the raw bytes (sync-project-template.py `_sha256`); adoption also accepts a CRLF-only difference, see v1_hashes."""
    p = target / V1_LOCK_REL
    if not os.path.lexists(p):
        return None
    check_inside(target, V1_LOCK_REL, "v1 lock")
    if is_link(p) or not p.is_file():
        raise BuildError(f"{V1_LOCK_REL}: exists but is not a regular file")
    data, _ = load_json(p, "v1 template lock")
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or data.get("schema_version") != 1:
        raise BuildError(f"{V1_LOCK_REL}: not a schema 1 template lock")
    out, seen = {}, set()
    for rel, h in files.items():
        plain_rel(rel, "v1 lock")
        parts = rel.split("/")
        ext = V1_GROUPS.get(parts[0])
        name = parts[-1]
        if ext is None or len(parts) != 2 or not name.startswith(STUB_PREFIX) or not name.endswith(ext) \
                or len(name) <= len(STUB_PREFIX) + len(ext) or not isinstance(h, str) or not HEX64_RE.match(h):
            raise BuildError(f"{V1_LOCK_REL}: entry outside the v1 template namespace or malformed: {rel!r}")
        full = f".codex/{rel}"
        if fold(full) in seen:
            raise BuildError(f"{V1_LOCK_REL}: entries that are the same path on Windows: {rel!r}")
        seen.add(fold(full))
        out[full] = h
    return out


def v1_hashes(target, rel):
    """None when absent, 'not-a-file' for anything that is not a plain file, else the set of sha256 values that count as
    "unmodified since v1": the raw bytes (what v1 hashes) and the same bytes with CRLF folded to LF. A Windows checkout with
    core.autocrlf rewrites line endings of a committed file, which changes no content, so it must not block adoption."""
    p = target / rel
    if not os.path.lexists(p):
        return None
    if is_link(p) or not p.is_file():
        return "not-a-file"
    data = p.read_bytes()
    out = {hashlib.sha256(data).hexdigest()}
    if b"\0" not in data:
        out.add(hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest())
    return out


class Plan:
    """What one run would do. Built by plan(); reading only."""


def plan(src, target, selected, chash, extras, external, same):
    want, frags = desired_files(src, selected)
    lock, lock_sections = read_lock(target)
    v1 = read_v1(target) if external else None
    check_inside(target, LOCK_REL, "lock")
    check_inside(target, CONFIG_REL, "config")
    for entry in SECTION_FILES.values():
        check_inside(target, entry, "target")
    sections = build_sections(frags, extras["linked_repos"])
    section_actions, section_conflicts = plan_sections(target, sections, lock_sections)
    for rel in list(want) + list(lock) + list(v1 or {}):
        check_inside(target, rel, "target")
    # A lock spelling that folds to a wanted path but is spelled differently is a case-only rename.
    # On Windows both spellings are one file, so the old one must be removed before the new one is written.
    want_fold = {fold(r): r for r in want}
    renamed_from = {want_fold[fold(r)]: r for r in lock if r not in want and fold(r) in want_fold}
    writes, deletes, conflicts = [], [], list(section_conflicts)
    # v1 files: a file the v1 lock owns is taken over only while it still has the exact bytes the v1 lock recorded.
    lock_folds = {fold(r) for r in lock}
    adopt, adopt_same, v1_deletes = {}, [], []
    for rel, h in sorted((v1 or {}).items()):
        if rel not in want and fold(rel) in want_fold:
            # The v1 lock spells a path differently from the new installation (case or trailing dot): on Windows that is the
            # same file. Adopting it under the new spelling would silently rename it, and leaving it as a v1 deletion would
            # delete the file the new installation needs. sync-project-template.py refuses the same case-only rename, so
            # this refuses too, with nothing written; fix the spelling in the project first.
            conflicts.append(f"{rel}: the v1 lock spells this path differently from the new installation ({want_fold[fold(rel)]}); "
                             "the two are one file on Windows, so it is not adopted or deleted; make the spelling match first")
            continue
        if fold(rel) in lock_folds:
            continue  # this installer already owns it; the ordinary rules apply
        raw = v1_hashes(target, rel)
        if raw is None:
            continue
        if raw == "not-a-file":
            conflicts.append(f"{rel}: listed in the v1 lock but is not a regular file")
        elif rel in want and digest(want[rel][0]) == current_hash(target, rel):
            adopt_same.append(rel)
        elif h not in raw:
            conflicts.append(f"{rel}: modified since the v1 installation (its hash differs from the v1 lock); not adopted")
        elif rel in want:
            adopt[rel] = h
        else:
            v1_deletes.append(rel)
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
        elif rel in adopt:
            writes.append((rel, "adopt"))
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
    origin = {"source": "self"} if same else src.origin()
    lock_doc = {"schema_version": 1, **origin, "mercury_json_sha256": src.master_hash, "config_sha256": chash,
                "files": {rel: digest(d) for rel, (d, _) in want.items()},
                "sections": {name: digest(text.encode("utf-8")) for name, text in sections.items()},
                "executable": sorted(rel for rel, (_, exe) in want.items() if exe)}
    lock_text = json.dumps(lock_doc, indent=2, sort_keys=True) + "\n"
    lp = target / LOCK_REL
    lock_same = lp.is_file() and lp.read_bytes().decode("utf-8").replace("\r\n", "\n") == lock_text
    P = Plan()
    P.want, P.writes, P.deletes, P.conflicts = want, writes, deletes, conflicts
    P.lock_text, P.lock_same, P.section_actions = lock_text, lock_same, section_actions
    P.adopt_same, P.v1_deletes, P.origin = adopt_same, v1_deletes, origin
    P.v1_lock = V1_LOCK_REL if v1 is not None else None
    P.config_create = None
    P.selected = selected
    return P


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


def prune_v1_dirs(target, rel):
    """After a v1 file is deleted: remove the directories it emptied below .codex (never .codex, .codex/agents or .codex/rules)."""
    stop = target / ".codex"
    parent = (target / rel).parent
    while parent != stop and stop in parent.parents and parent.is_dir() and not any(parent.iterdir()):
        if tuple(parent.relative_to(target).parts) in V1_KEEP_DIRS:
            break
        parent.rmdir()
        parent = parent.parent


# --- checks against the target repository ---

def target_git_root(target):
    out = git_bytes(target, ["rev-parse", "--show-toplevel"], "target is not a Git work tree").decode("utf-8").strip()
    if not out or not os.path.samefile(out, target):
        raise BuildError(f"target must be the root of its Git work tree (found {out})")


def ignored_paths(target, rels):
    """The managed paths that `git check-ignore` says are ignored: such a file is silently left out of commits."""
    try:
        r = subprocess.run(["git", "-C", str(target), "check-ignore", "--stdin", "-z"],
                           input=("\0".join(rels) + "\0").encode("utf-8"), capture_output=True)
    except OSError as err:
        raise BuildError(f"git is required to check the ignore rules: {err}")
    if r.returncode == 1:
        return []
    if r.returncode != 0:
        raise BuildError(f"git check-ignore failed: {r.stderr.decode('utf-8', 'replace').strip()}")
    return sorted(x for x in r.stdout.decode("utf-8").split("\0") if x)


def ignore_message(hit):
    return ("the target's Git ignore rules cover managed paths (the files would never be committed); "
            "change the ignore rules first:\n  " + "\n  ".join(hit))


WORKFLOW_RE = re.compile(r"mercury-template|mercury-sync|sync-project-template|sync-downstream-pr|verify-project-template")


def v1_workflows(target):
    """[(file, [(line number, line)])] for .github/workflows files that mention the v1 template, its sync or verify scripts."""
    out = []
    wf = target / ".github" / "workflows"
    if not wf.is_dir() or is_link(wf):
        return out
    for p in sorted(wf.iterdir()):
        if p.suffix not in (".yml", ".yaml") or is_link(p) or not p.is_file():
            continue
        text = p.read_bytes().decode("utf-8", "replace")
        hits = [(i, l.strip()) for i, l in enumerate(text.splitlines(), 1) if WORKFLOW_RE.search(l)]
        if hits or WORKFLOW_RE.search(p.name):
            out.append((f".github/workflows/{p.name}", hits or [(0, "(the file name matches)")]))
    return out


def find_references(target, paths, skip):
    """[(file, line number, removed path)] for tracked text files of the target that mention a path about to be removed."""
    if not paths:
        return []
    names = git_bytes(target, ["ls-files", "-z"], "listing the target's tracked files").decode("utf-8", "replace").split("\0")
    out = []
    for name in sorted(n for n in names if n and n not in skip):
        p = target / name
        if is_link(p) or not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        data = p.read_bytes()
        if b"\0" in data[:8192]:
            continue
        for i, line in enumerate(data.decode("utf-8", "replace").splitlines(), 1):
            for path in paths:
                if path in line:
                    out.append((name, i, path))
    return out


# --- run ---

def prepare_config(src, target, master, packs_arg, may_create):
    """(selected, config hash, extras, bytes to create or None). An existing config belongs to the project: only read."""
    path = target / CONFIG_REL
    if os.path.lexists(path):
        check_inside(target, CONFIG_REL, "config")
        if is_link(path) or not path.is_file():
            raise BuildError(f"{CONFIG_REL}: exists but is not a regular file")
        selected, chash, extras = load_config(target, master)
        if packs_arg is not None:
            want, _, _ = parse_config(config_bytes(packs_arg), master)
            if set(want) != set(selected):
                raise BuildError(f"--packs ({', '.join(want)}) does not match {CONFIG_REL} ({', '.join(selected)}); "
                                 "the config belongs to the project, edit it there")
        return selected, chash, extras, None
    if not may_create:
        raise BuildError(f"cannot read {CONFIG_REL} ({Path(CONFIG_REL).name}): no such file; run `install` first")
    raw = config_bytes(packs_arg or [])
    selected, chash, extras = parse_config(raw, master)
    return selected, chash, extras, raw


def config_bytes(packs):
    names = ["core"] + [p for p in packs if p != "core"]
    return (json.dumps({"schema_version": 1, "packs": list(dict.fromkeys(names))}, indent=2) + "\n").encode("utf-8")


def managed_paths(P):
    return sorted(set(P.want) | {CONFIG_REL, LOCK_REL} | set(SECTION_FILES.values()))


def report_lines(P):
    report = [f"conflict: {c}" for c in P.conflicts]
    report += [f"{kind}: {rel}" for rel, kind in P.writes] + [f"delete: {rel}" for rel in P.deletes]
    report += [f"adopt: {rel} (v1 file, already identical)" for rel in P.adopt_same]
    report += [f"delete: {rel} (v1 file)" for rel in P.v1_deletes]
    report += [f"{kind}: {name} (managed section)" for name, kind, _, _ in P.section_actions]
    if P.config_create is not None:
        report.append(f"create: {CONFIG_REL}")
    if not P.lock_same:
        report.append(f"update: {LOCK_REL}")
    if P.v1_lock:
        report.append(f"delete: {P.v1_lock} (v1 lock)")
    return report


def dry_report(P, target, hit):
    """Human-readable plan for `install --dry-run`; stdout."""
    o = P.origin
    groups = {"create": [], "update": [], "adopt": [], "delete": []}
    for rel, kind in P.writes:
        groups["adopt" if kind == "adopt" else "create" if kind == "create" else "update"].append(
            rel + (" (replaces the v1 file)" if kind == "adopt" else " (executable bit)" if kind == "mode" else ""))
    groups["adopt"] += [f"{r} (v1 file, content already identical; only the lock takes it over)" for r in P.adopt_same]
    groups["delete"] += P.deletes + [f"{r} (v1 file, no longer shipped)" for r in P.v1_deletes]
    if P.v1_lock:
        groups["delete"].append(f"{P.v1_lock} (the v1 lock)")
    if P.config_create is not None:
        groups["create"].insert(0, f"{CONFIG_REL} (packs: {', '.join(P.selected)})")
    if not P.lock_same:
        groups["update" if (target / LOCK_REL).exists() else "create"].append(LOCK_REL)
    print("mercury install --dry-run: nothing is written")
    print(f"source: {o.get('source_repo', o['source'])}" + (f" @ {o['source_commit']}" if "source_commit" in o else ""))
    print(f"target: {target}")
    print(f"packs: {', '.join(P.selected)}")
    for kind in ("create", "update", "adopt", "delete"):
        print(f"{kind} ({len(groups[kind])}):")
        for item in groups[kind]:
            print(f"  {item}")
    print("entry files:")
    if not P.section_actions:
        print("  managed sections are already up to date")
    for name, kind, _, where in P.section_actions:
        print(f"  {name}: {kind}: {where}")
    removed = sorted(set(P.deletes) | set(P.v1_deletes) | ({P.v1_lock} if P.v1_lock else set()))
    skip = set(removed) | {LOCK_REL}
    refs = find_references(target, [r for r in removed if r != P.v1_lock], skip)
    print(f"tracked files that mention a path about to be removed ({len(refs)}):")
    for name, line, path in refs[:200]:
        print(f"  {name}:{line}: {path}")
    flows = v1_workflows(target)
    print(f"v1 workflows that need handling by the project; this tool does not change them ({len(flows)}):")
    for name, hits in flows:
        for line, text in hits[:3]:
            print(f"  {name}:{line}: {text}")
    print(f"ignored by the target's Git ignore rules ({len(hit)}); an install stops while this is not empty:")
    for item in hit:
        print(f"  {item}")
    for line in (f"conflict: {c}" for c in P.conflicts):
        print(line)
    problems = len(P.conflicts) + len(hit)
    print("mercury: dry run, " + (f"{problems} problem(s), an install would stop with nothing written" if problems else "no conflicts"))


def run(src, target, apply, *, external=False, same=False, dry=False, packs_arg=None, may_create=False):
    master, mhash = load_master(src)
    src.master_hash = mhash
    if src.kind == "commit":
        published = src.is_published()
        if apply and not dry and not published:
            raise BuildError(f"source commit {src.commit} is not reachable from any remote-tracking branch of origin, but the lock "
                             "would record it as a commit of origin; push it (and fetch) first, or preview with --dry-run")
        print(f"mercury: source commit {src.commit}" + ("" if published else " (NOT on origin: preview only, nothing can be written)"))
    selected, chash, extras, cfg_raw = prepare_config(src, target, master, packs_arg, may_create)
    P = plan(src, target, selected, chash, extras, external, same)
    P.config_create = cfg_raw
    hit = ignored_paths(target, managed_paths(P)) if external else []
    if hit and not dry:
        raise BuildError(ignore_message(hit))
    report = report_lines(P)
    if dry:
        dry_report(P, target, hit)
        return 1 if P.conflicts or hit else 0
    if not apply:
        for line in report:
            print(line, file=sys.stderr)
        print("mercury: " + (f"{len(report)} difference(s)" if report else f"{len(P.want)} file(s) in sync"))
        return 1 if report else 0
    if P.conflicts:
        for line in report:
            print(line, file=sys.stderr)
        print("mercury: stopped, nothing written; resolve the conflicts above", file=sys.stderr)
        return 1
    # Deletes first: a case-only rename must remove the old spelling before the new one is written.
    for rel in P.deletes:
        (target / rel).unlink()
        prune_empty_dirs(target, rel)
    for rel in P.v1_deletes:
        (target / rel).unlink()
        prune_v1_dirs(target, rel)
    for rel, kind in P.writes:
        p = target / rel
        data, exe = P.want[rel]
        if kind != "mode":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        set_exe(p, exe)  # carry the executable bit where the filesystem has one (no effect on Windows)
    for name, _kind, data, _where in P.section_actions:
        atomic_write(target, name, data)
    if P.config_create is not None:
        (target / CONFIG_REL).parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, CONFIG_REL, P.config_create)
    if not P.lock_same:
        lp = target / LOCK_REL
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_bytes(P.lock_text.encode("utf-8"))
    if P.v1_lock:
        (target / P.v1_lock).unlink()  # last: until the new lock is written a rerun can still adopt the v1 files
    n_del = len(P.deletes) + len(P.v1_deletes) + (1 if P.v1_lock else 0)
    print(f"mercury: wrote {len(P.writes) + len(P.section_actions)}, deleted {n_del}, {len(P.want)} file(s) managed")
    if external:
        for name, hits in v1_workflows(target):
            print(f"mercury: note: v1 workflow {name} still refers to the v1 template; the project must retire or change it")
    return 0


# --- source add / source update: external upstream projects pinned under sources/ ---

class Ext:
    """The only door to the network and to Git for the `source` commands; tests replace it with a fake."""

    def run(self, argv, cwd=None):
        try:
            r = subprocess.run(argv, cwd=None if cwd is None else str(cwd), capture_output=True)
        except OSError as err:
            raise BuildError(f"cannot run {argv[0]}: {err}")
        return r.returncode, r.stdout, r.stderr

    def gh_api(self, path):
        rc, out, err = self.run(["gh", "api", path])
        if rc != 0:
            raise BuildError(f"gh api {path} failed: {err.decode('utf-8', 'replace').strip()}")
        try:
            return json.loads(out.decode("utf-8"))
        except ValueError:
            raise BuildError(f"gh api {path}: the answer is not JSON")

    def git(self, cwd, *args):
        rc, out, err = self.run(["git", *args], cwd=cwd)
        if rc != 0:
            raise BuildError(f"git {' '.join(args[:2])} failed: {err.decode('utf-8', 'replace').strip()}")
        return out.decode("utf-8", "replace")


def check_ref(ref):
    if not isinstance(ref, str) or not ref or ref.startswith("-") or ".." in ref or "\\" in ref \
            or any(c.isspace() or ord(c) < 32 or c in "~^:?*[" for c in ref):
        raise BuildError(f"not a usable ref: {ref!r}")
    return ref


def github_repo(url):
    parsed = parse_github(url)
    if not parsed:
        raise BuildError(f"only GitHub repositories are supported (https://github.com/<owner>/<repo>): {url!r}")
    return parsed


def verified_commit(ext, owner, repo, ref):
    """(full commit id, SPDX license at that commit). Refuses a license that is not on the permissive list."""
    info = ext.gh_api(f"repos/{owner}/{repo}/commits/{urllib.parse.quote(check_ref(ref), safe='/')}")
    sha = info.get("sha") if isinstance(info, dict) else None
    if not isinstance(sha, str) or not SHA_RE.match(sha):
        raise BuildError(f"{owner}/{repo}: GitHub returned no usable commit id for {ref!r}")
    if SHA_RE.match(ref) and ref != sha:
        raise BuildError(f"{owner}/{repo}: {ref} resolved to a different commit {sha}")
    lic = ext.gh_api(f"repos/{owner}/{repo}/license?ref={sha}")
    spdx = ((lic.get("license") or {}).get("spdx_id")) if isinstance(lic, dict) else None
    if spdx not in PERMISSIVE:
        raise BuildError(f"{owner}/{repo}: license {spdx!r} is not on the permissive list ({', '.join(PERMISSIVE)})")
    return sha, spdx


def read_master_file(root):
    """(master dict, raw bytes) of <root>/mercury.json; the file must be a plain file."""
    p = root / "mercury.json"
    if is_link(p) or not p.is_file():
        raise BuildError("mercury.json is missing or not a regular file")
    raw = p.read_bytes()
    master, _ = parse_json(raw, "mercury.json", "mercury.json")
    load_master(root)
    return master, raw


def write_master(root, master, raw):
    """Write mercury.json back with the original line ending; key order is kept."""
    eol = nl_of(raw.decode("utf-8"))
    text = json.dumps(master, indent=2, ensure_ascii=False) + "\n"
    atomic_write(root, "mercury.json", text.replace("\n", eol).encode("utf-8"))


def relink_module(ext, dest, gitdir):
    """Turn the absolute links that `git init --separate-git-dir` writes into the relative ones of `git submodule add`."""
    (dest / ".git").unlink()  # Windows marks it hidden, which blocks overwriting in place
    (dest / ".git").write_bytes(f"gitdir: {Path(os.path.relpath(gitdir, dest)).as_posix()}\n".encode("utf-8"))
    ext.git(dest, "config", "--file", str(gitdir / "config"), "core.worktree", Path(os.path.relpath(dest, gitdir)).as_posix())
    top = ext.git(dest, "rev-parse", "--show-toplevel").strip()
    if not os.path.samefile(top, dest):
        raise BuildError(f"the relinked work tree resolves to {top}, not {dest}")


def source_add(root, ext, name, url, ref, rationale):
    if not NAME_RE.match(name) or name.endswith("."):
        raise BuildError(f"not a plain source name: {name!r}")
    owner, repo = github_repo(url)
    canon = f"https://github.com/{owner}/{repo}.git"
    plain_line(rationale, "--rationale")
    master, raw = read_master_file(root)
    if name.lower() in {e["name"].lower() for e in master.get("sources", [])}:
        raise BuildError(f"source {name} is already recorded in mercury.json")
    rel = f"sources/{name}"
    dest = root / rel
    check_inside(root, rel, "source")
    if os.path.lexists(dest):
        raise BuildError(f"{rel} already exists; remove it first if it is a leftover")
    gm = root / ".gitmodules"
    gm_text = gm.read_bytes().decode("utf-8") if gm.is_file() and not is_link(gm) else ""
    if os.path.lexists(gm) and not gm.is_file() or is_link(gm):
        raise BuildError(".gitmodules is not a regular file")
    if f'"{rel}"' in gm_text or re.search(r"^\s*path\s*=\s*" + re.escape(rel) + r"\s*$", gm_text, re.M):
        raise BuildError(f"{rel} is already registered in .gitmodules")
    if ext.git(root, "ls-files", "-z", "--", rel).strip("\0"):
        raise BuildError(f"{rel} is already tracked in the index")
    sha, spdx = verified_commit(ext, owner, repo, ref or "HEAD")
    gitdir = Path(ext.git(root, "rev-parse", "--git-path", f"modules/{rel}").strip())
    gitdir = gitdir if gitdir.is_absolute() else root / gitdir
    if os.path.lexists(gitdir):
        raise BuildError(f"the Git directory {gitdir} already exists; remove the leftover first")
    try:
        gitdir.parent.mkdir(parents=True, exist_ok=True)
        # The layout `git submodule add` produces: the Git directory lives under the superproject's modules/, the work
        # tree has a `.git` file with a relative `gitdir:` and the module config has a relative `core.worktree`.
        # Staging the path later then records a gitlink. The index is never touched here.
        ext.git(root, "init", "-q", "--separate-git-dir", str(gitdir), str(dest))
        relink_module(ext, dest, gitdir)
        ext.git(dest, "remote", "add", "origin", canon)
        ext.git(dest, "fetch", "-q", "--depth", "1", "origin", sha)
        ext.git(dest, "checkout", "-q", "--detach", sha)
        if ext.git(dest, "rev-parse", "HEAD").strip() != sha:
            raise BuildError(f"the checkout is not at {sha}")
        eol = nl_of(gm_text) if gm_text else "\n"
        entry = eol.join([f'[submodule "{rel}"]', f"\tpath = {rel}", f"\turl = {canon}", "\tshallow = true"]) + eol
        gm_new = gm_text + ("" if not gm_text or gm_text.endswith("\n") else eol) + entry
        atomic_write(root, ".gitmodules", gm_new.encode("utf-8"))
        # What `git submodule init` records, so that the staged gitlink is an active submodule.
        ext.git(root, "config", f"submodule.{rel}.url", canon)
        ext.git(root, "config", f"submodule.{rel}.active", "true")
        master.setdefault("sources", []).append({"name": name, "url": canon, "commit": sha, "license": spdx,
                                                 "added": date.today().isoformat(), "rationale": rationale})
        master["sources"].sort(key=lambda e: e["name"].lower())
        write_master(root, master, raw)
    except (BuildError, OSError) as err:
        raise BuildError(f"source add failed: {err}\n  leftovers to inspect and remove by hand: {dest} and {gitdir}; "
                         f".gitmodules, mercury.json and the shared config keys submodule.{rel}.url / .active "
                         "may also have been written")
    print(f"mercury: added source {name} at {sha} ({spdx})")
    print(f"stage with: powershell -File scripts/codex/git-safe.ps1 add .gitmodules mercury.json {rel}")
    return 0


def pack_generators(root, name):
    """[(pack, generator path or None)] for packs whose build/ directory mentions sources/<name>."""
    pat = re.compile(r"sources[\"']?\s*[/,]\s*[\"']?" + re.escape(name) + r"(?![A-Za-z0-9._-])")
    out = []
    for build in sorted((root / "packs").glob("*/build")):
        if not build.is_dir() or is_link(build):
            continue
        if any(pat.search(f.read_bytes().decode("utf-8", "replace")) for f in sorted(build.rglob("*"))
               if f.is_file() and not is_link(f) and f.stat().st_size < 2_000_000):
            gen = build / "generate.py"
            out.append((build.parent.name, gen if gen.is_file() else None))
    return out


def summarise_compare(cmp, old, new):
    lines = [f"upstream changes {old[:7]}..{new[:7]}: {cmp.get('status')}, {cmp.get('ahead_by', 0)} commit(s) ahead, "
             f"{len(cmp.get('files') or [])} file(s) changed" + (" (GitHub lists at most 300)" if len(cmp.get("files") or []) >= 300 else "")]
    commits = cmp.get("commits") or []
    for c in commits[:15]:
        msg = ((c.get("commit") or {}).get("message") or "").split("\n")[0][:100]
        lines.append(f"  {str(c.get('sha'))[:7]} {msg}")
    if len(commits) > 15:
        lines.append(f"  ... and {len(commits) - 15} more commit(s)")
    for f in (cmp.get("files") or [])[:30]:
        lines.append(f"  {f.get('status')}: {f.get('filename')} (+{f.get('additions')} -{f.get('deletions')})")
    return lines


def source_update(root, ext, name, to):
    master, raw = read_master_file(root)
    entry = next((e for e in master.get("sources", []) if e["name"] == name), None)
    if entry is None:
        raise BuildError(f"source {name} is not recorded in mercury.json")
    rel = f"sources/{name}"
    dest = root / rel
    check_inside(root, rel, "source")
    if not (dest / ".git").exists():
        raise BuildError(f"{rel} is not checked out (git submodule update --init -- {rel})")
    old = entry["commit"]
    here = ext.git(dest, "rev-parse", "HEAD").strip()
    if here != old:
        raise BuildError(f"{rel} is at {here}, not at the recorded {old}; resolve that before updating")
    dirty = ext.git(dest, "status", "--porcelain").strip()
    if dirty:
        raise BuildError(f"{rel} has local modifications; not moving the checkout over them:\n{dirty}")
    owner, repo = github_repo(entry["url"])
    new, spdx = verified_commit(ext, owner, repo, to or "HEAD")
    if new == old:
        print(f"mercury: {name} is already at {old}; nothing to move")
    else:
        cmp = ext.gh_api(f"repos/{owner}/{repo}/compare/{old}...{new}")
        if not isinstance(cmp, dict) or cmp.get("status") not in ("ahead", "identical"):
            raise BuildError(f"{name}: {new} is not a descendant of {old} (status {cmp.get('status') if isinstance(cmp, dict) else '?'}); not moving")
        print("\n".join(summarise_compare(cmp, old, new)))
        ext.git(dest, "fetch", "-q", "--depth", "1", "origin", new)
        ext.git(dest, "checkout", "-q", "--detach", new)
        if ext.git(dest, "rev-parse", "HEAD").strip() != new:
            raise BuildError(f"{rel}: the checkout is not at {new} after the update")
        entry["commit"], entry["license"] = new, spdx
        write_master(root, master, raw)
    failed = []
    for pack, gen in pack_generators(root, name):
        if gen is None:
            print(f"mercury: pack {pack} mentions {rel} but has no build/generate.py to check")
            continue
        rc, out, err = ext.run([sys.executable, "-B", str(gen), "--check"], cwd=root)
        print(f"mercury: {pack} generator --check: " + ("passes" if rc == 0 else "FAILS"))
        if rc != 0:
            failed.append(pack)
            print((out + err).decode("utf-8", "replace").rstrip())
    print(f"stage with: powershell -File scripts/codex/git-safe.ps1 add mercury.json {rel}")
    if failed:
        print(f"mercury: stopped: the generator check of {', '.join(failed)} does not match the new checkout; "
              "update the pin in its build/generate.py, regenerate, and review", file=sys.stderr)
        return 1
    return 0


# --- command line ---

def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("check", "apply", "install"):
        p = sub.add_parser(name)
        p.add_argument("--target", required=True, help="project to install into")
        p.add_argument("--source", default=str(SOURCE_ROOT), help="Mercury checkout holding packs/ (default: this one)")
        p.add_argument("--source-commit", help="Mercury commit to read the packs from (external targets only)")
        if name == "install":
            p.add_argument("--packs", help="comma separated packs for a new .mercury/config.json (core is always included)")
            p.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    sp = sub.add_parser("source")
    ss = sp.add_subparsers(dest="action", required=True)
    add = ss.add_parser("add")
    add.add_argument("name")
    add.add_argument("url")
    add.add_argument("--ref")
    add.add_argument("--rationale", required=True)
    upd = ss.add_parser("update")
    upd.add_argument("name")
    upd.add_argument("--to")
    for p in (add, upd):
        p.add_argument("--root", default=str(SOURCE_ROOT), help="Mercury checkout (default: this one)")
    return ap


def lock_commit(target):
    """The source commit recorded in the target's lock, or None."""
    p = target / LOCK_REL
    if not p.is_file():
        return None
    data, _ = load_json(p, "lock")
    c = data.get("source_commit") if isinstance(data, dict) else None
    return c if isinstance(c, str) and SHA_RE.match(c) else None


def main(argv, worktree=False, ext=None):
    """worktree=True (tests only, never a command line flag) reads the packs from the source's working tree and index."""
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "source":
            root, ext = Path(args.root).resolve(), ext or Ext()
            if args.action == "add":
                return source_add(root, ext, args.name, args.url, args.ref, args.rationale)
            return source_update(root, ext, args.name, args.to)
        target, source = Path(args.target).resolve(), Path(args.source).resolve()
        if not target.is_dir():
            raise BuildError(f"target is not a directory: {target}")
        same = source == target
        external = not (worktree or same)
        if external:
            target_git_root(target)
            git_bytes(source, ["rev-parse", "--git-dir"], "--source is not a Git checkout")
            spec = args.source_commit or (None if args.command == "install" else lock_commit(target)) or "HEAD"
            src = CommitSource(source, resolve_commit(source, spec))
        else:
            if args.source_commit:
                raise BuildError("--source-commit applies to external targets; Mercury installs itself from its working tree")
            src = WorkTreeSource(source)
        install = args.command == "install"
        packs_arg = None
        if install and args.packs is not None:
            packs_arg = [p.strip() for p in args.packs.split(",") if p.strip()]
        return run(src, target, args.command != "check", external=external, same=same,
                   dry=install and args.dry_run, packs_arg=packs_arg, may_create=install)
    except BuildError as err:
        print(f"mercury: {err}", file=sys.stderr)
        return 2
    except (ValueError, AttributeError, TypeError, OSError) as err:
        print(f"mercury: invalid input or unreadable file: {type(err).__name__}: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
