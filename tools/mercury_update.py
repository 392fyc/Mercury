#!/usr/bin/env python3
"""Deliver released Mercury commits to a downstream project, from inside that project's GitHub Actions job.

    python tools/mercury_update.py publish --target <checkout> --mercury <checkout> --repo owner/name --base <branch>
    python tools/mercury_update.py merge   --target <checkout> --mercury <checkout> --repo owner/name --base <branch>
                                           [--required-checks-json <json list>]
    python tools/mercury_update.py verify  --target <checkout> --mercury <checkout> --base <branch> --head-ref <branch>

`--target` is a checkout of the downstream project (full history; `publish` needs it at the tip of `--base` and clean),
`--mercury` a full checkout of Mercury's `master` (`origin/master` is the release). `gh` does every GitHub call, `git`
every Git call; both are reached through one seam each so tests can replace them.

publish  Reads the newest commit of Mercury's `origin/master`, and when the project's managed files differ from it,
         applies it with `tools/mercury.py apply` on a branch `mercury/update-<12 hex>` and opens the one update pull
         request. When an update pull request already exists and is behind, the new state goes on top of its branch
         with a regular push (see "Branch rule").
merge    Merges the one update pull request (squash, `--match-head-commit`) only when ALL of these hold on its exact head:
           1. it is from a same-repository `mercury/update-<12 hex>` branch and is the only such open pull request;
           2. every changed path (merge base to head) is Mercury-managed: in the old or the new lock, a v1 file being
              adopted or deleted, the lock itself, or AGENTS.md / CLAUDE.md where only the bytes between the Mercury
              markers changed; `.mercury/config.json` never changes;
           3. the new lock's `source_commit` is on Mercury's `origin/master` and descends from the old one's;
           4. `tools/mercury.py check` passes on a read-only checkout of that head (the files really are what that Mercury
              commit produces, so a hand-edited workflow cannot vouch for itself);
           5. every `params.required_checks` name and `Mercury verify` has a completed successful check run, or a success
              status, on that head (all pages read, or the run refuses);
           6. the pull request is open, not a draft and GitHub reports it mergeable.
verify   Read-only form of 2 and 3 for the `Mercury verify` job: with a head ref that is not an update branch it does nothing.

Branch rule: the tool never force-pushes and has no force option. A regular push cannot discard anyone's commits (the
remote refuses anything that is not a fast-forward). It only builds on an existing update branch that is same-repository,
named `mercury/update-<12 hex>`, whose every commit beyond the base was committed by the tool's own identity
(`mercury-update@users.noreply.github.com`); otherwise it stops and leaves the branch alone. That identity test is a
courtesy heuristic, not a security boundary: a committer email is free text, so anyone can forge it. What makes a forged
branch harmless is that a regular push never discards anything and that `merge` re-validates the head's content (paths,
provenance, `mercury.py check`) whoever wrote it.

Command line compatibility: a copy of the managed workflow that is older than this tool may still be the one that runs
(scheduled workflows run from the default branch), and it calls the latest tool from Mercury's `master`. So the arguments
only ever grow: a flag may be added when it is optional, an existing flag is never removed, renamed or made required, and
an existing exit code keeps its meaning. tests/tools/test_mercury_update.py pins the argument lists of every released
workflow.

Exit codes: 0 nothing to do, published or merged; 1 stopped (the reason is printed; a condition is not met, the queue is
not unique, a branch is not ours); 2 usage error or safety error (bad arguments, a cross-repository update branch,
incomplete pagination, unreadable or inconsistent data from GitHub or Git). Credentials never appear in the output.
"""
import argparse
import contextlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("mercury_installer", HERE / "mercury.py")
inst = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = inst
_spec.loader.exec_module(inst)

BRANCH_PREFIX = "mercury/update-"
BRANCH_RE = re.compile(r"^mercury/update-[0-9a-f]{12}\Z")
VERIFY_CHECK_NAME = "Mercury verify"  # the job name in packs/deploy/templates/mercury-verify.yml
VERIFY_APP_SLUG = "github-actions"  # the app that owns check runs of Actions jobs
BOT_NAME, BOT_EMAIL = "Mercury Update", "mercury-update@users.noreply.github.com"
RELEASE_REF = inst.RELEASE_REF
LOCK_REL, CONFIG_REL, V1_LOCK_REL = inst.LOCK_REL, inst.CONFIG_REL, inst.V1_LOCK_REL
ENTRY_FILES = tuple(inst.SECTION_FILES.values())  # AGENTS.md, CLAUDE.md
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+\Z")
PER_PAGE, MAX_PAGES = 100, 20


class Stop(Exception):
    """A condition is not met; nothing unsafe happened (exit 1)."""


class Unsafe(Exception):
    """The tool cannot trust what it sees, or was misused (exit 2)."""


# --- diagnostics never carry credentials (ported from scripts/codex/sync-downstream-pr.py) ---

_SECRET = re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}"
                     r"|(?<=://)[^\s/@]+@|(?i:authorization:)\s*\S+(?:\s+\S+)?")


def redact(text, limit):
    text = _SECRET.sub("<redacted>", text.replace("\r", "")).strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def describe(args):
    """Name the failing command without long values such as PR bodies. Each argument is redacted before it is cut,
    so a cut can never expose part of a secret."""
    shown = [Path(args[0]).name]
    for arg in args[1:8]:
        arg = _SECRET.sub("<redacted>", arg).replace("x-access-token:", "<redacted>")
        shown.append(arg if len(arg) <= 80 else arg[:77] + "...")
    return redact(" ".join(shown) + (" ..." if len(args) > 8 else ""), 300)


def execute(args, cwd=None, timeout=120):
    """(return code, stdout bytes, stderr bytes). A command that cannot start or times out is an Unsafe."""
    try:
        r = subprocess.run(args, cwd=None if cwd is None else str(cwd), capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as err:
        raise Unsafe(f"cannot complete command: {describe(args)} ({type(err).__name__})") from None
    return r.returncode, r.stdout, r.stderr


def run(args, cwd=None, timeout=120):
    rc, out, err = execute(args, cwd, timeout)
    if rc:
        detail = redact(err.decode("utf-8", "replace").strip() or out.decode("utf-8", "replace"), 600)
        raise Unsafe(f"command failed (exit {rc}): {describe(args)}" + (f"\n{detail}" if detail else ""))
    return out.decode("utf-8", "replace").strip()


def git(root, *args):
    return run(["git", "-C", str(root), "-c", "core.fsmonitor=false", *args])


def gh_real(args):
    """The seam to GitHub: `gh <args>`, stdout as text. Tests pass a function with the same shape instead."""
    return run(["gh", *args])


def show(root, rev, path):
    """Bytes of `path` at `rev`, or None when the path does not exist there; any other failure is an Unsafe."""
    rc, out, err = execute(["git", "-C", str(root), "cat-file", "blob", f"{rev}:{path}"])
    if rc == 0:
        return out
    listing = run(["git", "-C", str(root), "ls-tree", "-z", rev, "--", path])
    if listing:
        raise Unsafe(f"cannot read {path} at {rev[:12]}: {redact(err.decode('utf-8', 'replace'), 300)}")
    return None


# --- GitHub reads: every page, or a refusal ---

def paged(gh, path, key=None):
    """All items of a list endpoint. With `key` the answer is an object holding the list and `total_count`.
    Running out of pages while the answer is still full, or a count that does not match, is an Unsafe."""
    sep = "&" if "?" in path else "?"
    items, total = [], None
    for page in range(1, MAX_PAGES + 1):
        data = json.loads(gh(["api", f"{path}{sep}per_page={PER_PAGE}&page={page}"]))
        if key:
            if not isinstance(data, dict) or not isinstance(data.get(key), list):
                raise Unsafe(f"unexpected answer from GitHub for {path.split('?')[0]}")
            chunk, total = data[key], data.get("total_count")
            if not isinstance(total, int):
                raise Unsafe(f"GitHub gave no total_count for {path.split('?')[0]}")
        elif isinstance(data, list):
            chunk = data
        else:
            raise Unsafe(f"unexpected answer from GitHub for {path.split('?')[0]}")
        items += chunk
        if total is not None and len(items) >= total:
            break
        if len(chunk) < PER_PAGE:
            break
    else:
        raise Unsafe(f"{path.split('?')[0]} has more than {MAX_PAGES * PER_PAGE} entries; refusing to decide on incomplete data")
    if total is not None and len(items) != total:
        raise Unsafe(f"incomplete data for {path.split('?')[0]}: read {len(items)} of {total}; refusing to decide")
    return items


def is_number(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def update_queue(gh, repo, base):
    """The one open update pull request (a list item), or None. Another repository's branch, an odd branch name or more
    than one candidate stops the run."""
    prs = paged(gh, f"repos/{repo}/pulls?state=open&base={urllib.parse.quote(base, safe='')}")
    found = []
    for pr in prs:
        head = pr.get("head") if isinstance(pr, dict) else None
        ref = head.get("ref") if isinstance(head, dict) else None
        if not isinstance(ref, str) or not ref.startswith(BRANCH_PREFIX):
            continue
        owner = (head.get("repo") or {}).get("full_name")
        if not isinstance(owner, str) or owner.lower() != repo.lower():
            raise Unsafe(f"pull request #{pr.get('number')} has the update branch name but comes from another repository; refusing")
        if not BRANCH_RE.match(ref):
            raise Unsafe(f"pull request #{pr.get('number')}: unexpected update branch name {ref!r}")
        if not is_number(pr.get("number")):
            raise Unsafe("an update pull request has no valid number")
        found.append(pr)
    if len(found) > 1:
        raise Stop("the update queue is not unique: open update pull requests " + ", ".join(f"#{p.get('number')}" for p in found)
                   + "; close all but one")
    return found[0] if found else None


def check_problems(gh, repo, sha, names):
    """Reasons why a required name has no success on exactly this commit (empty list: all good).

    Two kinds of name:
    * `Mercury verify` is Mercury's own check. Anyone with write access can post a commit status or a check run with any
      name (a status costs one API call), so it counts only as a check run created by GitHub Actions (app slug
      `github-actions`), which is what the managed workflow's job is. A status, or a check run of another app, with that
      name is ignored.
    * The project's `params.required_checks` are the project's own names. They may be satisfied by a check run of any app
      or by a commit status, because external CI (another app, a status API) is a legitimate source and the project chose
      the names. The project is responsible for those names not being claimable by an untrusted writer."""
    runs = paged(gh, f"repos/{repo}/commits/{sha}/check-runs?filter=latest", key="check_runs")
    statuses = paged(gh, f"repos/{repo}/commits/{sha}/statuses")
    seen, own = {}, []
    for r in runs:
        done = r.get("status") == "completed"
        item = (done and r.get("conclusion") == "success",
                f"check run is {r.get('status')}" + (f" ({r.get('conclusion')})" if done else ""))
        seen.setdefault(r.get("name"), []).append(item)
        if r.get("name") == VERIFY_CHECK_NAME and ((r.get("app") or {}).get("slug") == VERIFY_APP_SLUG):
            own.append(item)
    latest = {}
    for s in statuses:
        ctx = s.get("context")
        if ctx not in latest or s.get("id", 0) > latest[ctx].get("id", 0):
            latest[ctx] = s
    for ctx, s in latest.items():
        seen.setdefault(ctx, []).append((s.get("state") == "success", f"status is {s.get('state')}"))
    problems = []
    for name in names:
        states = own if name == VERIFY_CHECK_NAME else seen.get(name)
        if not states:
            problems.append(f"check {name!r} has not been reported on this commit"
                            + (f" by the {VERIFY_APP_SLUG} app" if name == VERIFY_CHECK_NAME else ""))
        for ok, why in states or ():
            if not ok:
                problems.append(f"check {name!r}: {why}")
    return problems


# --- the project and the Mercury checkout ---

def checkout_root(path, what):
    root = Path(path).resolve()
    if not root.is_dir():
        raise Unsafe(f"{what} is not a directory: {path}")
    top = git(root, "rev-parse", "--show-toplevel")
    if not os.path.samefile(top, root):
        raise Unsafe(f"{what} must be the root of its Git work tree")
    return root


def mercury_master(mercury):
    out = git(mercury, "rev-parse", "--verify", "--quiet", RELEASE_REF + "^{commit}")
    if not inst.SHA_RE.match(out):
        raise Unsafe("the Mercury checkout has no origin/master")
    return out


def params_at_base(target, base, required_json):
    """The project's validated params from the base branch (the trusted copy), cross-checked against the workflow's."""
    raw = show(target, f"origin/{base}", CONFIG_REL)
    if raw is None:
        raise Unsafe(f"origin/{base} has no {CONFIG_REL}; install first")
    try:
        cfg, _ = inst.parse_json(raw, CONFIG_REL, "config.json")
        params = inst.load_params(cfg.get("params", {}) if isinstance(cfg, dict) else None)
    except inst.BuildError as err:
        raise Unsafe(f"{CONFIG_REL} at origin/{base}: {err}") from None
    if params.get("integration_branch") != base:
        raise Unsafe(f"params.integration_branch in {CONFIG_REL} ({params.get('integration_branch')!r}) is not the branch this "
                     f"workflow runs for ({base!r}); re-apply the deploy pack")
    if "required_checks" not in params:
        raise Unsafe(f"params.required_checks is missing from {CONFIG_REL}")
    if required_json is not None:
        try:
            given = inst.check_names(json.loads(required_json), "--required-checks-json")
        except (ValueError, inst.BuildError) as err:
            raise Unsafe(f"--required-checks-json: {err}") from None
        if given != params["required_checks"]:
            raise Unsafe("the required checks rendered into the workflow differ from params.required_checks; re-apply the deploy pack")
    return params


def lock_of(raw, what):
    """(lock document, files, sections) from lock bytes, or raise a Stop that names the problem."""
    try:
        doc, _ = inst.parse_json(raw, what, "lock.json")
        files, sections = inst.parse_lock(doc)
    except inst.BuildError as err:
        raise Stop(f"{what}: {err}") from None
    return doc, files, sections


def raw_changes(target, old, new):
    """[(path, new mode)] for every path that differs between two commits; mode '000000' means deleted."""
    parts = git(target, "diff", "--raw", "-z", "--no-renames", "--no-abbrev", old, new).split("\0")
    out, i = [], 0
    while i < len(parts):
        if parts[i].startswith(":"):
            fields = parts[i][1:].split()
            out.append((parts[i + 1], fields[1]))
            i += 2
        else:
            i += 1
    return out


def entry_problem(name, old, new, sections):
    """Why a change to AGENTS.md / CLAUDE.md is not a managed-section-only change, or None. Everything outside the
    markers must be byte for byte what it was (a new insertion adds exactly the separator the installer writes)."""
    if new is None:
        return f"{name}: deleted"
    try:
        new_text = new.decode("utf-8")
        old_text = "" if old is None else old.decode("utf-8")
        span = inst.find_block(new_text, name)
        old_span = inst.find_block(old_text, name) if old_text else None
    except UnicodeDecodeError:
        return f"{name}: not valid UTF-8"
    except inst.BuildError as err:
        return str(err)
    if new_text.startswith("﻿") or old_text.startswith("﻿"):
        return f"{name}: byte order mark"
    if span is None:
        return f"{name}: no Mercury section after the change"
    if inst.digest(new_text[span[0]:span[1]].encode("utf-8")) != sections.get(name):
        return f"{name}: the Mercury section is not the one recorded in the lock"
    if old_span is None:
        eol = inst.nl_of(old_text)
        if span[0] != 0 or new_text[span[1]:] != (eol + eol + old_text if old_text else eol):
            return f"{name}: bytes outside the Mercury section changed (the section may only be added at the top)"
    elif new_text[:span[0]] != old_text[:old_span[0]] or new_text[span[1]:] != old_text[old_span[1]:]:
        return f"{name}: bytes outside the Mercury section changed"
    return None


def ancestor(mercury, older, newer):
    rc, _, err = execute(["git", "-C", str(mercury), "merge-base", "--is-ancestor", older, newer])
    if rc in (0, 1):
        return rc == 0
    raise Stop(f"commit {older[:12]} or {newer[:12]} is not known to the Mercury checkout")


def validate_update(target, mercury, base, head):
    """Managed paths only, and provenance. Returns the new lock document; raises Stop listing every problem found."""
    mb = git(target, "merge-base", f"origin/{base}", head)
    new_raw = show(target, head, LOCK_REL)
    if new_raw is None:
        raise Stop(f"the proposed head has no {LOCK_REL}")
    new_doc, new_files, new_sections = lock_of(new_raw, "the new lock")
    old_raw = show(target, mb, LOCK_REL)
    old_doc, old_files = (None, {})
    if old_raw is not None:
        old_doc, old_files, _ = lock_of(old_raw, "the old lock")
    v1 = set()
    v1_raw = show(target, mb, V1_LOCK_REL)
    if v1_raw is not None:
        try:
            v1 = set(inst.parse_v1(inst.parse_json(v1_raw, "v1 lock", "mercury-template.lock")[0])) | {V1_LOCK_REL}
        except inst.BuildError as err:
            raise Stop(f"the v1 template lock: {err}") from None
    # What may change, and how. A path that the NEW lock lists is verified byte for byte by `mercury.py check` on the head, so
    # it may be created or modified. A path that only the old lock or the v1 lock lists is something this release stops
    # shipping: it may only be deleted. Nothing re-checks the content of such a path (the new lock no longer names it), so
    # "modified instead of deleted" would otherwise carry arbitrary content through.
    allowed = set(new_files) | set(old_files) | v1 | {LOCK_REL}
    changes = raw_changes(target, mb, head)
    problems = []
    if not changes:
        raise Stop("the proposed head changes nothing")
    for path, mode in changes:
        if path == CONFIG_REL:
            problems.append(f"{CONFIG_REL} must not change in an update pull request")
        elif path in ENTRY_FILES:
            why = entry_problem(path, show(target, mb, path), show(target, head, path), new_sections)
            if why:
                problems.append(why)
        elif path not in allowed:
            problems.append(f"{path}: not a Mercury-managed path")
        elif path not in new_files and path != LOCK_REL and mode != "000000":
            problems.append(f"{path}: is no longer shipped (not in the new lock), so it may only be deleted, not changed")
        if mode not in ("000000", "100644", "100755"):
            problems.append(f"{path}: not a regular file (mode {mode})")
    commit = new_doc.get("source_commit")
    if not isinstance(commit, str) or not inst.SHA_RE.match(commit):
        problems.append("the new lock has no valid source_commit")
    else:
        try:
            if not ancestor(mercury, commit, RELEASE_REF):
                problems.append(f"source commit {commit[:12]} is not on Mercury's origin/master")
            if old_doc is not None:
                old_commit = old_doc.get("source_commit")
                if not isinstance(old_commit, str) or not inst.SHA_RE.match(old_commit):
                    problems.append("the old lock has no valid source_commit")
                elif not ancestor(mercury, old_commit, commit):
                    problems.append(f"source commit {commit[:12]} does not descend from the old one {old_commit[:12]} (rollback)")
                if new_doc.get("source_repo") != old_doc.get("source_repo"):
                    problems.append("source_repo changed")
        except Stop as err:
            problems.append(str(err))
    if problems:
        raise Stop("the update fails validation:\n  " + "\n  ".join(problems))
    return new_doc


def installer(mode, tree, mercury, commit):
    """(exit code, text) of `tools/mercury.py <mode>` from this Mercury checkout, against `tree`, at `commit`."""
    rc, out, err = execute([sys.executable, "-B", str(HERE / "mercury.py"), mode, "--target", str(tree), "--source", str(mercury),
                            "--source-commit", commit], timeout=300)
    return rc, redact(out.decode("utf-8", "replace") + "\n" + err.decode("utf-8", "replace"), 1500)


def require_installed(tree, mercury, commit):
    rc, text = installer("check", tree, mercury, commit)
    if rc == 2:
        raise Unsafe(f"mercury check could not run: {text}")
    if rc != 0:
        raise Stop(f"the proposed files are not what Mercury commit {commit[:12]} produces:\n{text}")


@contextlib.contextmanager
def detached_tree(target, rev):
    """A read-only checkout of `rev` outside the repository, removed afterwards."""
    tmp = tempfile.mkdtemp(prefix="mercury-update-")
    tree = Path(tmp) / "tree"
    try:
        git(target, "worktree", "add", "--detach", str(tree), rev)
        yield tree
    finally:
        with contextlib.suppress(Unsafe):
            git(target, "worktree", "remove", "--force", str(tree))
        shutil.rmtree(tmp, ignore_errors=True)


def set_output(name, value):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


# --- publish ---

def clean_line(text, limit=100):
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text).replace("@", "@​")[:limit]


def pr_text(target, mercury, base, new, old):
    """(title, body) of the update pull request: the source, what changed upstream, which files change here."""
    title = f"chore(harness): update Mercury to {new[:12]}"
    doc = json.loads(show(target, "HEAD", LOCK_REL))
    repo = doc.get("source_repo")
    link = f"https://github.com/{repo}/commit/{new}" if isinstance(repo, str) and REPO_RE.match(repo) else new
    lines = [f"Takes Mercury commit {link} (on Mercury's `master`) into this project.", ""]
    if old:
        log = git(mercury, "log", "--format=%h %s", "--max-count=30", f"{old}..{new}").splitlines()
        lines += [f"Upstream changes since {old[:12]}:", ""] + [f"- {clean_line(l)}" for l in log] + [""]
    else:
        lines += ["First installation from Mercury; no earlier source commit is recorded.", ""]
    v1_raw = show(target, f"origin/{base}", V1_LOCK_REL)
    v1 = set(inst.parse_v1(json.loads(v1_raw))) | {V1_LOCK_REL} if v1_raw else set()
    names = {"A": "Added", "M": "Updated", "D": "Deleted", "T": "Type changed"}
    changes = git(target, "diff", "--name-status", "--no-renames", f"origin/{base}", "HEAD").splitlines()
    lines.append(f"Files changed in this project ({len(changes)}):")
    lines.append("")
    for row in changes[:100]:
        status, _, path = row.partition("\t")
        lines.append(f"- {names.get(status[:1], status)}: `{path}`" + (" (v1 template file, taken over)" if path in v1 else ""))
    if len(changes) > 100:
        lines.append(f"- ... and {len(changes) - 100} more")
    lines += ["", "This pull request is managed by `.github/workflows/mercury-update.yml`. Do not push to this branch. It is merged "
              "automatically only when the source is released, only Mercury-managed paths changed and the required checks "
              "pass on its head; otherwise the update job reports the reason."]
    return title, "\n".join(lines) + "\n"


def commit_update(target, mercury, base, new, old):
    """Commit what `apply` wrote, validate the result, and return it as (title, body). Nothing is pushed here."""
    git(target, "add", "-A")
    if not git(target, "status", "--porcelain"):
        return None
    git(target, "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "-c", "commit.gpgsign=false", "commit", "-q",
        "-m", f"chore(harness): update Mercury to {new[:12]}", "-m", f"Mercury-Source: {new}")
    validate_update(target, mercury, base, "HEAD")
    require_installed(target, mercury, new)
    return pr_text(target, mercury, base, new, old)


def push_branch(target, branch):
    # No force option exists anywhere in this tool: a rejected (non fast-forward) push is an error, never retried harder.
    git(target, "push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")


def write_body(body):
    tmp = tempfile.mkdtemp(prefix="mercury-update-body-")
    path = Path(tmp) / "body.md"
    path.write_text(body, encoding="utf-8")
    return tmp, path


def apply_update(target, mercury, new):
    rc, text = installer("apply", target, mercury, new)
    if rc == 2:
        raise Unsafe(f"mercury apply could not run: {text}")
    if rc != 0:
        raise Stop(f"mercury apply stopped with conflicts; nothing was committed:\n{text}")


def our_branch(target, base, pr):
    """Fetch the PR's branch and prove it is one this tool made: the head matches the PR, every commit beyond the base has
    the tool's committer identity. Returns the head commit."""
    branch = pr["head"]["ref"]
    git(target, "fetch", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    head = git(target, "rev-parse", f"refs/remotes/origin/{branch}")
    if head != pr["head"].get("sha"):
        raise Stop(f"{branch} moved while the tool was reading it; try again")
    who = git(target, "log", "--format=%ce", f"origin/{base}..{head}").split()
    if not who or any(e != BOT_EMAIL for e in who):
        raise Stop(f"{branch} holds commits that this tool did not make; it will not build on it (nothing was pushed). "
                   "Close that pull request and delete the branch, or ask its owner")
    return head


def cmd_publish(a, gh):
    target, mercury = checkout_root(a.target, "--target"), checkout_root(a.mercury, "--mercury")
    git(target, "fetch", "--no-tags", "origin", f"+refs/heads/{a.base}:refs/remotes/origin/{a.base}")
    if git(target, "status", "--porcelain"):
        raise Stop("the target checkout must be clean")
    if git(target, "rev-parse", "HEAD") != git(target, "rev-parse", f"origin/{a.base}"):
        raise Stop(f"the target checkout must be at the tip of origin/{a.base}")
    params_at_base(target, a.base, None)
    new = mercury_master(mercury)
    pr = update_queue(gh, a.repo, a.base)
    if pr is None:
        rc, _ = installer("check", target, mercury, new)
        if rc == 0:
            print(f"nothing to do: the project already matches Mercury {new[:12]}")
            return 0
        branch = BRANCH_PREFIX + new[:12]
        if git(target, "ls-remote", "--heads", "origin", f"refs/heads/{branch}"):
            raise Stop(f"{branch} exists on the remote without an open pull request; delete it (the tool never overwrites a branch)")
        old_raw = show(target, f"origin/{a.base}", LOCK_REL)
        old = lock_commit(old_raw)
        git(target, "switch", "--no-track", "-c", branch)
        apply_update(target, mercury, new)
        made = commit_update(target, mercury, a.base, new, old)
        if made is None:
            print(f"nothing to do: applying Mercury {new[:12]} changed nothing")
            return 0
        push_branch(target, branch)
        tmp, body = write_body(made[1])
        try:
            print(gh(["pr", "create", "--repo", a.repo, "--base", a.base, "--head", branch, "--title", made[0],
                      "--body-file", str(body)]).strip())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        set_output("published", "true")
        print(f"published update pull request for Mercury {new[:12]}")
        return 0
    branch = pr["head"]["ref"]
    head = our_branch(target, a.base, pr)
    raw = show(target, head, LOCK_REL)
    if raw is None:
        raise Stop(f"{branch} has no {LOCK_REL}")
    here = lock_commit(raw)
    if here is None:
        raise Stop(f"{branch}: its lock has no valid source_commit")
    if here == new:
        print(f"nothing to publish: pull request #{pr['number']} already carries Mercury {new[:12]}")
        return 0
    if not ancestor(mercury, here, new):
        raise Stop(f"pull request #{pr['number']} carries {here[:12]}, which is not an ancestor of Mercury master {new[:12]}; "
                   "not touching it")
    git(target, "switch", "--no-track", "-C", branch, f"refs/remotes/origin/{branch}")
    apply_update(target, mercury, new)
    old = lock_commit(show(target, f"origin/{a.base}", LOCK_REL))
    made = commit_update(target, mercury, a.base, new, old)
    if made is None:
        print(f"nothing to do: pull request #{pr['number']} already has the content of Mercury {new[:12]}")
        return 0
    push_branch(target, branch)  # regular push on top of the branch: a fast-forward or nothing
    tmp, body = write_body(made[1])
    try:
        gh(["pr", "edit", str(pr["number"]), "--repo", a.repo, "--title", made[0], "--body-file", str(body)])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    set_output("published", "true")
    print(f"updated pull request #{pr['number']} to Mercury {new[:12]}")
    return 0


def lock_commit(raw):
    """source_commit of lock bytes, or None (absent lock, unreadable lock or no commit)."""
    if raw is None:
        return None
    try:
        c = json.loads(raw.decode("utf-8")).get("source_commit")
    except (ValueError, AttributeError):
        return None
    return c if isinstance(c, str) and inst.SHA_RE.match(c) else None


# --- merge and verify ---

def cmd_merge(a, gh):
    target, mercury = checkout_root(a.target, "--target"), checkout_root(a.mercury, "--mercury")
    git(target, "fetch", "--no-tags", "origin", f"+refs/heads/{a.base}:refs/remotes/origin/{a.base}")
    params = params_at_base(target, a.base, a.required_checks_json)
    item = update_queue(gh, a.repo, a.base)
    if item is None:
        print("nothing to do: no open update pull request")
        return 0
    n = item["number"]
    pr = json.loads(gh(["api", f"repos/{a.repo}/pulls/{n}"]))
    if not isinstance(pr, dict) or pr.get("number") != n:
        raise Unsafe(f"pull request #{n}: unexpected data from GitHub")
    head_sha = ((pr.get("head") or {}).get("sha")) if isinstance(pr, dict) else None
    if not isinstance(head_sha, str) or not inst.SHA_RE.match(head_sha) or pr.get("head", {}).get("ref") != item["head"]["ref"] \
            or (pr.get("base") or {}).get("ref") != a.base or (((pr.get("head") or {}).get("repo")) or {}).get("full_name", "").lower() != a.repo.lower():
        raise Unsafe(f"pull request #{n}: unexpected data from GitHub")
    if pr.get("state") != "open" or pr.get("draft"):
        raise Stop(f"pull request #{n} is not open or is a draft")
    branch = item["head"]["ref"]
    git(target, "fetch", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}")
    if git(target, "rev-parse", f"refs/remotes/origin/{branch}") != head_sha:
        raise Stop(f"the head of pull request #{n} moved while it was being verified; try again")
    new = validate_update(target, mercury, a.base, head_sha)["source_commit"]
    with detached_tree(target, head_sha) as tree:
        require_installed(tree, mercury, new)
    wanted = params["required_checks"] + [VERIFY_CHECK_NAME]
    problems = check_problems(gh, a.repo, head_sha, wanted)
    if problems:
        raise Stop(f"pull request #{n} is waiting for its checks on {head_sha[:12]}:\n  " + "\n  ".join(problems))
    if pr.get("mergeable") is False:
        raise Stop(f"pull request #{n} conflicts with {a.base} and cannot be merged. Close it and delete its branch "
                   f"{branch}; the next publish opens a fresh update from the current base")
    if pr.get("mergeable") is not True:
        raise Stop(f"pull request #{n}: GitHub has not computed whether it is mergeable yet; the next run will look again")
    # The server compares the head again; no admin or bypass flag is used.
    gh(["pr", "merge", str(n), "--repo", a.repo, "--squash", "--match-head-commit", head_sha])
    print(f"merged update pull request #{n} (Mercury {new[:12]})")
    return 0


def cmd_verify(a, gh):
    target, mercury = checkout_root(a.target, "--target"), checkout_root(a.mercury, "--mercury")
    if not a.head_ref.startswith(BRANCH_PREFIX):
        print("not an update branch: nothing to verify here")
        return 0
    if not BRANCH_RE.match(a.head_ref):
        raise Stop(f"unexpected update branch name {a.head_ref!r}")
    git(target, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{a.base}^{{commit}}")
    validate_update(target, mercury, a.base, git(target, "rev-parse", "HEAD"))
    print("update verified: Mercury-managed paths only, released source")
    return 0


# --- command line ---

def build_parser():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("publish", "merge", "verify"):
        p = sub.add_parser(name)
        p.add_argument("--target", required=True)
        p.add_argument("--mercury", required=True)
        p.add_argument("--base", required=True, help="the project's integration branch")
        if name != "verify":
            p.add_argument("--repo", required=True, help="owner/name of the project")
        if name == "merge":
            p.add_argument("--required-checks-json", help="the list rendered into the workflow; must equal params.required_checks")
        if name == "verify":
            p.add_argument("--head-ref", required=True, help="head branch of the pull request being verified")
    return ap


def main(argv, gh=None):
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")
    ap = build_parser()
    a = ap.parse_args(argv)
    try:
        try:
            inst.branch_name(a.base, "--base")
        except inst.BuildError as err:
            raise Unsafe(str(err)) from None
        if getattr(a, "repo", None) is not None and not REPO_RE.match(a.repo):
            raise Unsafe("--repo must look like owner/name")
        handler = {"publish": cmd_publish, "merge": cmd_merge, "verify": cmd_verify}[a.command]
        return handler(a, gh or gh_real)
    except Stop as err:
        print(f"mercury-update: stopped: {redact(str(err), 4000)}", file=sys.stderr)
        return 1
    except (Unsafe, inst.BuildError) as err:
        print(f"mercury-update: refused: {redact(str(err), 4000)}", file=sys.stderr)
        return 2
    except Exception as err:  # the type only: the message may hold data from a command
        print(f"mercury-update: refused: unexpected {type(err).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
