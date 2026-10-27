"""Installer for external projects and the `source` commands (#684): commit pinning, ignore rules, v1 adoption, dry run.

No test touches the network: `source add/update` run against a fake `Ext` that answers `gh api` from a table and redirects
`git fetch origin` to a local repository."""
import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mercury.py"
LOCK, CONFIG = ".mercury/lock.json", ".mercury/config.json"
V1_LOCK = ".codex/mercury-template.lock"
ORIGIN = "https://github.com/test/mercury.git"
DEV_ROLE = '---\ndescription: "Builds things."\nclaude:\n  model: sonnet\ncodex:\n  sandbox_mode: workspace-write\n---\n\n# Role: builder\n'


def load_tool():
    spec = importlib.util.spec_from_file_location("mercury_tool_install", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args, check=True):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True, encoding="utf-8")
    if check:
        assert r.returncode == 0, r.stderr
    return r.stdout


def init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "core.autocrlf", "false")
    git(path, "config", "core.fileMode", "false")


class ExternalTarget(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src, self.dst = Path(tmp.name) / "src", Path(tmp.name) / "dst"
        init_repo(self.src)
        git(self.src, "remote", "add", "origin", ORIGIN)
        p = self.src / "packs"
        write(self.src / "mercury.json", json.dumps({"schema_version": 1, "packs": {
            "core": {"enabled": True, "required": True}, "extra": {"enabled": True}, "off": {"enabled": False}}}))
        write(p / "core/pack.json", json.dumps({"schema_version": 1, "name": "core", "skills": {"alpha": {"hosts": ["claude"]}}}))
        write(p / "core/skills/alpha/SKILL.md", "---\nname: alpha\ndescription: Alpha.\n---\n\nalpha body\n")
        write(p / "core/agents.md", "## Core\n\nCore text.\n")
        write(p / "core/claude.md", "@AGENTS.md\n\nClaude text.\n")
        write(p / "core/roles/dev.md", DEV_ROLE)
        write(p / "core/rules/mercury-git-safety.rules", "# rules\n")
        write(p / "extra/pack.json", json.dumps({"schema_version": 1, "name": "extra", "skills": {}}))
        write(p / "extra/note.txt", "note\n")
        write(p / "extra/run.sh", "#!/bin/sh\n")
        self.commit_all("one", exec_paths=["packs/extra/run.sh"])
        init_repo(self.dst)

    def commit_all(self, msg, exec_paths=()):
        git(self.src, "add", "-A")
        for rel in exec_paths:
            git(self.src, "update-index", "--chmod=+x", rel)
        git(self.src, "commit", "-q", "-m", msg)
        head = git(self.src, "rev-parse", "HEAD").strip()
        git(self.src, "update-ref", "refs/remotes/origin/main", head)  # as if pushed and fetched
        return head

    def head(self):
        return git(self.src, "rev-parse", "HEAD").strip()

    def tool(self, *args):
        r = subprocess.run([sys.executable, "-B", str(TOOL), *args], capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    def install(self, *extra, mode="install"):
        return self.tool(mode, "--source", str(self.src), "--target", str(self.dst), *extra)

    def snapshot(self):
        return {p.relative_to(self.dst).as_posix(): p.read_bytes() for p in sorted(self.dst.rglob("*"))
                if p.is_file() and ".git" not in p.relative_to(self.dst).parts}

    # --- first installation, pinning, lock ---

    def test_first_install_creates_config_and_records_the_source(self):
        code, out = self.install()
        self.assertEqual(code, 0, out)
        s = self.snapshot()
        self.assertEqual(json.loads(s[CONFIG]), {"schema_version": 1, "packs": ["core"]})
        lock = json.loads(s[LOCK])
        self.assertEqual((lock["source_repo"], lock["source_commit"]), ("test/mercury", self.head()))
        self.assertIn(".codex/agents/mercury-dev.toml", s)
        self.assertIn(".codex/rules/mercury-git-safety.rules", s)
        self.assertNotIn(".mercury/packs/extra/note.txt", s)  # core only by default
        self.assertTrue(s["AGENTS.md"].startswith(b"<!-- MERCURY:BEGIN"))
        before = self.snapshot()
        code, out = self.install()
        self.assertEqual((code, self.snapshot()), (0, before))
        self.assertIn("wrote 0, deleted 0", out)
        self.assertEqual(self.install(mode="check")[0], 0)

    def test_packs_option_creates_the_config_and_a_mismatch_with_an_existing_one_is_an_error(self):
        self.assertEqual(self.install("--packs", "core,extra")[0], 0)
        s = self.snapshot()
        self.assertEqual(json.loads(s[CONFIG])["packs"], ["core", "extra"])
        self.assertEqual(s[".mercury/packs/extra/note.txt"], b"note\n")
        self.assertEqual(json.loads(s[LOCK])["executable"], [".mercury/packs/extra/run.sh"])  # mode comes from the tree
        code, out = self.install("--packs", "core")
        self.assertEqual(code, 2, out)
        self.assertIn("does not match", out)
        self.assertEqual(self.snapshot(), s)
        self.assertEqual(self.install("--packs", "extra,core")[0], 0)  # same set, any order
        self.assertEqual(self.snapshot()[CONFIG], s[CONFIG])  # the project's config is never rewritten
        self.assertEqual(self.install("--packs", "off")[0], 2)

    def test_content_comes_from_the_commit_not_the_working_tree(self):
        self.assertEqual(self.install("--packs", "core,extra")[0], 0)
        first = self.head()
        write(self.src / "packs/extra/note.txt", "edited in the working tree only\n")
        write(self.src / "packs/extra/untracked.txt", "x\n")
        write(self.src / "mercury.json", json.dumps({"schema_version": 1, "packs": {"core": {"enabled": True, "required": True}, "extra": {"enabled": False}}}))
        self.assertEqual(self.install(mode="check")[0], 0)  # still in sync with the recorded commit
        code, out = self.install(mode="apply")
        self.assertEqual(code, 0, out)
        s = self.snapshot()
        self.assertEqual(s[".mercury/packs/extra/note.txt"], b"note\n")
        self.assertNotIn(".mercury/packs/extra/untracked.txt", s)
        # a new commit is picked up by `install` (default HEAD) and by --source-commit, never by check/apply alone
        git(self.src, "checkout", "-q", "--", "packs", "mercury.json")
        write(self.src / "packs/extra/note.txt", "second\n")
        second = self.commit_all("two")
        self.assertEqual(self.install(mode="check")[0], 0)
        self.assertEqual(json.loads(self.snapshot()[LOCK])["source_commit"], first)
        code, out = self.install("--source-commit", second, mode="check")
        self.assertEqual(code, 1, out)
        self.assertIn("update: .mercury/packs/extra/note.txt", out)
        self.assertEqual(self.install()[0], 0)
        self.assertEqual(self.snapshot()[".mercury/packs/extra/note.txt"], b"second\n")
        self.assertEqual(json.loads(self.snapshot()[LOCK])["source_commit"], second)
        self.assertEqual(self.install("--source-commit", first, mode="apply")[0], 0)  # and back
        self.assertEqual(self.snapshot()[".mercury/packs/extra/note.txt"], b"note\n")

    def test_the_master_switch_is_read_from_the_commit(self):
        write(self.src / "mercury.json", json.dumps({"schema_version": 1, "packs": {"core": {"enabled": True, "required": True}, "extra": {"enabled": False}}}))
        self.commit_all("disable extra")
        code, out = self.install("--packs", "extra")
        self.assertEqual(code, 2, out)
        self.assertIn("disabled", out)
        git(self.src, "checkout", "-q", "HEAD~1", "--", "mercury.json")  # working tree says enabled; the commit still says disabled
        self.assertEqual(self.install("--packs", "extra")[0], 2)

    def test_links_and_odd_tree_entries_are_refused(self):
        r = subprocess.run(["git", "-C", str(self.src), "hash-object", "-w", "--stdin"], input="note.txt", capture_output=True, text=True)
        git(self.src, "update-index", "--add", "--cacheinfo", f"120000,{r.stdout.strip()},packs/core/link.txt")
        git(self.src, "commit", "-q", "-m", "link")
        git(self.src, "update-ref", "refs/remotes/origin/main", "HEAD")
        code, out = self.install()
        self.assertEqual(code, 2, out)
        self.assertIn("only regular files", out)
        self.assertEqual(self.snapshot(), {})

    def test_the_source_commit_must_exist_and_needs_an_origin(self):
        self.assertEqual(self.install("--source-commit", "0" * 40)[0], 2)
        self.assertEqual(self.install("--source-commit", "--help")[0], 2)
        git(self.src, "remote", "remove", "origin")
        code, out = self.install()
        self.assertEqual(code, 2, out)
        self.assertIn("origin", out)
        self.assertEqual(self.snapshot(), {})

    def test_target_must_be_a_git_work_tree_root(self):
        plain = self.dst.parent / "plain"
        plain.mkdir()
        code, out = self.tool("install", "--source", str(self.src), "--target", str(plain))
        self.assertEqual(code, 2, out)
        self.assertEqual(list(plain.iterdir()), [])
        sub = self.dst / "inner"
        sub.mkdir()
        self.assertEqual(self.tool("install", "--source", str(self.src), "--target", str(sub))[0], 2)

    # --- the source commit must be on origin; the commit in use is printed (A-F2) ---

    def test_a_commit_that_exists_only_locally_cannot_be_installed_but_can_be_previewed(self):
        write(self.src / "packs/extra/note.txt", "local only\n")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "unpushed")
        local = self.head()
        code, out = self.install()
        self.assertEqual(code, 2, out)
        self.assertIn("not reachable from any remote-tracking branch of origin", out)
        self.assertEqual(self.snapshot(), {})
        for mode in ("apply",):
            self.assertEqual(self.install(mode=mode)[0], 2)
        code, out = self.install("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn(f"source commit {local} (NOT on origin", out)
        self.assertEqual(self.snapshot(), {})
        git(self.src, "update-ref", "refs/remotes/origin/main", local)  # pushed and fetched
        code, out = self.install()
        self.assertEqual(code, 0, out)
        self.assertIn(f"mercury: source commit {local}\n", out)
        self.assertEqual(json.loads(self.snapshot()[LOCK])["source_commit"], local)

    def test_a_commit_on_another_branch_of_origin_counts(self):
        git(self.src, "update-ref", "-d", "refs/remotes/origin/main")
        git(self.src, "update-ref", "refs/remotes/origin/feature/x", "HEAD")
        self.assertEqual(self.install()[0], 0)

    def test_check_and_install_print_the_commit_in_use(self):
        self.assertEqual(self.install()[0], 0)
        code, out = self.install(mode="check")
        self.assertEqual(code, 0, out)
        self.assertIn(f"mercury: source commit {self.head()}", out)

    # --- the origin url is never echoed (A-F3) ---

    def test_an_unreadable_origin_url_is_not_echoed(self):
        git(self.src, "remote", "set-url", "origin", "https://user:s3cret-token@example.com/onlyone")
        code, out = self.install()
        self.assertEqual(code, 2, out)
        self.assertNotIn("s3cret-token", out)
        self.assertNotIn("example.com", out)
        self.assertEqual(self.snapshot(), {})

    def test_credentials_in_a_readable_origin_url_do_not_reach_the_lock(self):
        git(self.src, "remote", "set-url", "origin", "https://user:s3cret-token@github.com/test/mercury.git")
        code, out = self.install()
        self.assertEqual(code, 0, out)
        lock = self.snapshot()[LOCK].decode()
        self.assertNotIn("s3cret-token", lock + out)
        self.assertEqual(json.loads(lock)["source_repo"], "test/mercury")

    # --- ignore rules ---

    def test_ignored_managed_paths_stop_the_run_before_any_write(self):
        for rule in (".mercury/", ".codex/rules/", "AGENTS.md", ".mercury/lock.json", ".mercury/config.json"):
            with self.subTest(rule=rule):
                write(self.dst / ".gitignore", rule + "\n")
                code, out = self.install()
                self.assertEqual(code, 2, out)
                self.assertIn("ignore rules", out)
                self.assertEqual({k for k in self.snapshot() if k != ".gitignore"}, set())
        write(self.dst / ".gitignore", "scratch/\n")
        self.assertEqual(self.install()[0], 0)

    # --- dry run ---

    def test_dry_run_writes_nothing_and_says_what_it_would_do(self):
        write(self.dst / "AGENTS.md", "# Mine\n\nline\n")
        write(self.dst / "docs/a.md", "see .codex/project/mercury-lane-contract.md for details\n")
        write(self.dst / ".github/workflows/sync.yml", "name: s\nrun: python scripts/codex/sync-project-template.py check\n")
        write(self.dst / ".github/workflows/ci.yml", "name: ci\n")
        git(self.dst, "add", "docs/a.md")
        contract = b"v1 contract\n"
        write(self.dst / ".codex/project/mercury-lane-contract.md", contract)
        write(self.dst / V1_LOCK, json.dumps({"schema_version": 1, "source_repo": "x/y", "source_commit": "a" * 40, "manifest_sha256": "b" * 64,
                                              "files": {"project/mercury-lane-contract.md": sha(contract)}}))
        before = self.snapshot()
        code, out = self.install("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.dst / ".mercury").exists())
        for expect in (f"@ {self.head()}", f"{CONFIG} (packs: core)", "create (", "delete (", ".codex/project/mercury-lane-contract.md (v1 file, no longer shipped)",
                       f"{V1_LOCK} (the v1 lock)", "AGENTS.md: insert: managed section goes to the top (line 1), above the existing 3 line(s)",
                       "CLAUDE.md: create", "docs/a.md:1: .codex/project/mercury-lane-contract.md", ".github/workflows/sync.yml:2:"):
            self.assertIn(expect, out)
        self.assertNotIn("ci.yml", out)

    def test_dry_run_reports_conflicts_with_exit_1(self):
        write(self.dst / "AGENTS.md", "# Mine\n")
        self.assertEqual(self.install()[0], 0)
        agents = (self.dst / "AGENTS.md").read_bytes()
        (self.dst / "AGENTS.md").write_bytes(agents.replace(b"Core text.", b"hand edit"))
        code, out = self.install("--dry-run")
        self.assertEqual(code, 1, out)
        self.assertIn("conflict:", out)

    # --- v1 adoption ---

    def v1_files(self, project=b"v1 contract\n", dev=b"old v1 dev\n", rules=b"# rules\n"):
        write(self.dst / ".codex/agents/mercury-dev.toml", dev)
        write(self.dst / ".codex/rules/mercury-git-safety.rules", rules)
        write(self.dst / ".codex/project/mercury-lane-contract.md", project)
        files = {"agents/mercury-dev.toml": b"old v1 dev\n", "rules/mercury-git-safety.rules": b"# rules\n",
                 "project/mercury-lane-contract.md": b"v1 contract\n"}
        write(self.dst / V1_LOCK, json.dumps({"schema_version": 1, "source_repo": "392fyc/Mercury", "source_commit": "a" * 40,
                                              "manifest_sha256": "b" * 64, "files": {k: sha(v) for k, v in files.items()}}))

    def test_v1_files_with_the_recorded_hash_are_adopted(self):
        self.v1_files()
        code, out = self.install("--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn(".codex/agents/mercury-dev.toml (replaces the v1 file)", out)
        self.assertIn(".codex/rules/mercury-git-safety.rules (v1 file, content already identical", out)
        code, out = self.install()
        self.assertEqual(code, 0, out)
        s = self.snapshot()
        self.assertNotIn(V1_LOCK, s)
        self.assertNotIn(".codex/project/mercury-lane-contract.md", s)
        self.assertFalse((self.dst / ".codex/project").exists())  # emptied directory goes with its last file
        self.assertTrue((self.dst / ".codex/agents").is_dir())
        self.assertIn(b"# Role: builder", s[".codex/agents/mercury-dev.toml"])
        lock = json.loads(s[LOCK])
        self.assertIn(".codex/agents/mercury-dev.toml", lock["files"])
        self.assertIn(".codex/rules/mercury-git-safety.rules", lock["files"])
        before = self.snapshot()
        code, out = self.install()
        self.assertEqual((code, self.snapshot()), (0, before))
        self.assertIn("wrote 0, deleted 0", out)

    def test_a_hand_edited_v1_file_stops_with_zero_writes(self):
        self.v1_files()
        write(self.dst / ".codex/project/mercury-lane-contract.md", b"edited by the project\n")
        write(self.dst / CONFIG, json.dumps({"schema_version": 1, "packs": ["core"]}))
        before = self.snapshot()
        for mode, extra in (("install", ()), ("install", ("--dry-run",)), ("apply", ()), ("check", ())):
            with self.subTest(mode=mode, extra=extra):
                code, out = self.install(*extra, mode=mode)
                self.assertEqual(code, 1, out)
                self.assertIn(".codex/project/mercury-lane-contract.md: modified since the v1 installation", out)
                self.assertEqual(self.snapshot(), before)
        write(self.dst / ".codex/project/mercury-lane-contract.md", b"v1 contract\n")
        write(self.dst / ".codex/agents/mercury-dev.toml", b"edited dev\n")
        code, out = self.install()
        self.assertEqual(code, 1, out)
        self.assertIn("mercury-dev.toml: modified since the v1 installation", out)
        self.assertFalse((self.dst / LOCK).exists())

    def test_a_crlf_only_difference_from_a_windows_checkout_does_not_block_adoption(self):
        self.v1_files()  # the lock hashes the LF bytes; a core.autocrlf checkout has CRLF in the work tree
        write(self.dst / ".codex/project/mercury-lane-contract.md", bytes([118]) + b"1 contract" + bytes([13, 10]))
        write(self.dst / ".codex/agents/mercury-dev.toml", b"old v1 dev" + bytes([13, 10]))
        code, out = self.install()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.dst / ".codex/project").exists())
        self.assertIn(b"# Role: builder", (self.dst / ".codex/agents/mercury-dev.toml").read_bytes())

    def test_any_other_change_to_a_v1_file_is_a_modification(self):
        self.v1_files()
        write(self.dst / ".codex/project/mercury-lane-contract.md", b"v1 contract and more" + bytes([13, 10]))
        code, out = self.install()
        self.assertEqual(code, 1, out)
        self.assertIn("modified since the v1 installation", out)

    def test_v1_lock_entries_outside_the_template_namespace_are_refused(self):
        write(self.dst / "keep.txt", b"keep\n")
        for rel in ("../keep.txt", "keep.txt", "agents/other.toml", "agents/sub/mercury-x.toml", "project/mercury-x.toml"):
            with self.subTest(rel=rel):
                write(self.dst / V1_LOCK, json.dumps({"schema_version": 1, "files": {rel: sha(b"keep\n")}}))
                code, out = self.install()
                self.assertEqual(code, 2, out)
                self.assertEqual((self.dst / "keep.txt").read_bytes(), b"keep\n")
                self.assertFalse((self.dst / ".mercury").exists())

    def test_a_v1_spelling_that_differs_only_in_case_from_a_wanted_path_is_never_deleted(self):
        scratch = self.dst.parent / "scratch"
        init_repo(scratch)
        self.assertEqual(self.tool("install", "--source", str(self.src), "--target", str(scratch))[0], 0)
        new = (scratch / ".codex/agents/mercury-dev.toml").read_bytes()  # content equal to the new installation
        write(self.dst / ".codex/agents/mercury-Dev.toml", new)
        write(self.dst / V1_LOCK, json.dumps({"schema_version": 1, "files": {"agents/mercury-Dev.toml": sha(new)}}))
        write(self.dst / CONFIG, json.dumps({"schema_version": 1, "packs": ["core"]}))
        before = self.snapshot()
        for extra, mode in (((), "install"), (("--dry-run",), "install"), ((), "apply"), ((), "check")):
            with self.subTest(mode=mode, extra=extra):
                code, out = self.install(*extra, mode=mode)
                self.assertEqual(code, 1, out)
                self.assertIn("spells this path differently", out)
                self.assertEqual(self.snapshot(), before)  # zero writes; the file is still there under its spelling
        self.assertTrue((self.dst / ".codex/agents/mercury-Dev.toml").exists())
        # platform independent: the plan itself, not the file system, decides
        tool = load_tool()
        src = tool.CommitSource(self.src, self.head())
        master, src.master_hash = tool.load_master(src)
        selected, chash, extras, _ = tool.prepare_config(src, self.dst, master, [], True)
        P = tool.plan(src, self.dst, selected, chash, extras, True, False)
        self.assertEqual(P.v1_deletes, [])
        self.assertEqual(len([c for c in P.conflicts if "mercury-Dev.toml" in c]), 1)
        self.assertNotIn(".codex/agents/mercury-Dev.toml", [r for r, _ in P.writes])

    def test_the_plan_alone_refuses_a_v1_spelling_that_differs_only_in_case(self):
        """#686: assert plan() directly. The install-level test above leans on the file system; this one does not.

        The v1 lock spells the file `mercury-Dev.toml`, the new installation wants `mercury-dev.toml`. On a
        case-insensitive file system those are one file, on a case-sensitive one they are two. Either way the
        plan must keep the old spelling (never delete it) and report one conflict, and must not write the wanted path.
        """
        scratch = self.dst.parent / "scratch"
        init_repo(scratch)
        self.assertEqual(self.tool("install", "--source", str(self.src), "--target", str(scratch))[0], 0)
        new = (scratch / ".codex/agents/mercury-dev.toml").read_bytes()
        write(self.dst / ".codex/agents/mercury-Dev.toml", new)
        write(self.dst / V1_LOCK, json.dumps({"schema_version": 1, "files": {"agents/mercury-Dev.toml": sha(new)}}))
        write(self.dst / CONFIG, json.dumps({"schema_version": 1, "packs": ["core"]}))
        tool = load_tool()
        src = tool.CommitSource(self.src, self.head())
        master, src.master_hash = tool.load_master(src)
        selected, chash, extras, _ = tool.prepare_config(src, self.dst, master, [], True)
        P = tool.plan(src, self.dst, selected, chash, extras, True, False)
        self.assertEqual(P.v1_deletes, [])
        conflicts = [c for c in P.conflicts if "mercury-Dev.toml" in c]
        self.assertEqual(len(conflicts), 1, P.conflicts)
        self.assertIn("spells this path differently", conflicts[0])
        written = [r for r, _ in P.writes]
        self.assertNotIn(".codex/agents/mercury-dev.toml", written)
        self.assertNotIn(".codex/agents/mercury-Dev.toml", written)

    def test_a_v1_lock_alone_is_just_removed(self):
        write(self.dst / V1_LOCK, json.dumps({"schema_version": 1, "files": {"agents/mercury-gone.toml": "0" * 64}}))
        self.assertEqual(self.install()[0], 0)
        self.assertFalse((self.dst / V1_LOCK).exists())


class FakeExt:
    """Answers `gh api` from a table, sends `git fetch ... origin` to a local repository, runs every other command for real."""

    def __init__(self, tool, upstream, api, fail_fetch=False):
        self.real, self.upstream, self.api, self.calls, self.fail_fetch = tool.Ext(), upstream, api, [], fail_fetch

    def run(self, argv, cwd=None):
        self.calls.append(list(argv))
        if argv[0] == "gh":
            path = argv[2]
            if path not in self.api:
                return 1, b"", f"no fake answer for {path}".encode()
            return 0, json.dumps(self.api[path]).encode(), b""
        if argv[0] == "git" and "fetch" in argv:
            if self.fail_fetch:
                return 1, b"", b"fatal: simulated network failure"
            argv = [self.upstream.as_uri() if a == "origin" else a for a in argv]
        return self.real.run(argv, cwd)

    gh_api = lambda self, path: type(self.real).gh_api(self, path)  # noqa: E731  (uses this fake's run)
    git = lambda self, cwd, *args: type(self.real).git(self, cwd, *args)  # noqa: E731


class SourceCommands(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        self.up = base / "upstream"
        init_repo(self.up)
        write(self.up / "LICENSE", "MIT\n")
        write(self.up / "f.txt", "one\n")
        git(self.up, "add", "-A")
        git(self.up, "commit", "-q", "-m", "first")
        self.sha1 = git(self.up, "rev-parse", "HEAD").strip()
        write(self.up / "f.txt", "two\n")
        git(self.up, "commit", "-qam", "second: change f")
        self.sha2 = git(self.up, "rev-parse", "HEAD").strip()
        self.root = base / "mercury"
        init_repo(self.root)
        write(self.root / "mercury.json", json.dumps({"schema_version": 1, "packs": {"core": {"enabled": True, "required": True}}}, indent=2) + "\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "base")
        self.api = {}

    def set_api(self, spdx="MIT", sha=None, ref="HEAD"):
        sha = sha or self.sha1
        self.api[f"repos/o/r/commits/{ref}"] = {"sha": sha}
        self.api[f"repos/o/r/license?ref={sha}"] = {"license": {"spdx_id": spdx}}

    def run_main(self, ext, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.tool.main(["source", *args, "--root", str(self.root)], ext=ext)
        return code, out.getvalue() + err.getvalue()

    def add(self, ext=None, name="up", url="https://github.com/o/r", extra=()):
        ext = ext or FakeExt(self.tool, self.up, self.api)
        return self.run_main(ext, "add", name, url, "--rationale", "Needed for tests.", *extra), ext

    def staged(self):
        return git(self.root, "diff", "--cached", "--name-only").strip()

    def test_add_checks_out_the_commit_records_it_and_never_touches_the_index(self):
        self.set_api()
        (code, out), ext = self.add()
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.root / "sources/up", "rev-parse", "HEAD").strip(), self.sha1)
        self.assertEqual((self.root / "sources/up/f.txt").read_text().strip(), "one")
        gm = (self.root / ".gitmodules").read_text()
        self.assertIn('[submodule "sources/up"]', gm)
        self.assertIn("url = https://github.com/o/r.git", gm)
        self.assertIn("shallow = true", gm)
        rec = json.loads((self.root / "mercury.json").read_text())["sources"]
        self.assertEqual(len(rec), 1)
        self.assertEqual({k: rec[0][k] for k in ("name", "url", "commit", "license", "rationale")},
                         {"name": "up", "url": "https://github.com/o/r.git", "commit": self.sha1, "license": "MIT", "rationale": "Needed for tests."})
        self.assertRegex(rec[0]["added"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertIn("git-safe.ps1 add .gitmodules mercury.json sources/up", out)
        self.assertEqual(self.staged(), "")  # the tool never writes the index
        self.assertEqual(git(self.root, "config", "--get", "submodule.sources/up.url").strip(), "https://github.com/o/r.git")
        # once staged it is a gitlink with a .gitmodules entry, and the work tree keeps its Git directory under modules/
        git(self.root, "add", ".gitmodules", "mercury.json", "sources/up")
        self.assertIn(f"160000 {self.sha1}", git(self.root, "ls-files", "-s", "sources/up"))
        self.assertRegex((self.root / "sources/up/.git").read_text(), r"^gitdir: .*modules[/\\]sources[/\\]up")
        self.assertEqual(self.tool.load_master(self.root)[0]["sources"][0]["name"], "up")

    def test_the_layout_is_the_one_git_submodule_add_makes(self):
        self.set_api()
        self.assertEqual(self.add()[0][0], 0)
        dest = self.root / "sources/up"
        text = (dest / ".git").read_text()
        self.assertTrue(text.startswith("gitdir: "), text)
        link = text[len("gitdir: "):].strip()
        self.assertFalse(Path(link).is_absolute(), link)
        self.assertTrue(link.startswith(".."), link)
        gitdir = (dest / link).resolve()
        self.assertTrue((gitdir / "HEAD").is_file())
        # git submodule add keeps the Git directory at <repo>/.git/modules/<submodule name>; the name of this one is its path
        self.assertEqual(gitdir, (self.root / ".git" / "modules" / "sources" / "up").resolve())
        worktree = git(dest, "config", "--file", str(gitdir / "config"), "--get", "core.worktree").strip()
        self.assertFalse(Path(worktree).is_absolute(), worktree)
        self.assertEqual((gitdir / worktree).resolve(), dest.resolve())
        self.assertEqual(Path(git(dest, "rev-parse", "--show-toplevel").strip()).resolve(), dest.resolve())

    @unittest.skipUnless(shutil.which("pwsh") or shutil.which("powershell"), "PowerShell is not available")
    def test_the_result_of_add_is_accepted_by_git_safe_and_staged_as_a_gitlink(self):
        # Everything happens in the scratch repository self.root, never in the Mercury index.
        self.set_api()
        self.assertEqual(self.add()[0][0], 0)
        scripts = ROOT / "scripts/codex"
        (self.root / "scripts/codex").mkdir(parents=True)
        for name in ("git-safe.ps1", "guard.ps1"):
            shutil.copyfile(scripts / name, self.root / "scripts/codex" / name)
        git(self.root, "checkout", "-q", "-b", "feature/TASK-1")
        pwsh = shutil.which("pwsh") or shutil.which("powershell")
        r = subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(self.root / "scripts/codex/git-safe.ps1"),
                            "add", ".gitmodules", "mercury.json", "sources/up"], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=self.root)
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
        self.assertEqual(git(self.root, "ls-files", "-s", "sources/up").split()[:3], ["160000", self.sha1, "0"])
        self.assertEqual(sorted(git(self.root, "diff", "--cached", "--name-only").split()), [".gitmodules", "mercury.json", "sources/up"])
        self.assertEqual(git(self.root, "submodule", "status").split()[0], self.sha1)  # an initialised, active submodule

    def test_add_refuses_a_license_that_is_not_permissive(self):
        for spdx in ("GPL-3.0", "NOASSERTION", None):
            with self.subTest(spdx=spdx):
                self.set_api(spdx=spdx)
                (code, out), _ = self.add()
                self.assertEqual(code, 2, out)
                self.assertIn("permissive", out)
                self.assertFalse((self.root / "sources").exists())
                self.assertFalse((self.root / ".gitmodules").exists())

    def test_every_permissive_license_is_accepted(self):
        for i, spdx in enumerate(("MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "0BSD", "Unlicense")):
            with self.subTest(spdx=spdx):
                self.set_api(spdx=spdx)
                (code, out), _ = self.add(name=f"s{i}")
                self.assertEqual(code, 0, out)

    def test_add_verifies_the_commit_id_and_the_source(self):
        self.api["repos/o/r/commits/HEAD"] = {"sha": "not-a-sha"}
        self.assertEqual(self.add()[0][0], 2)
        self.set_api(sha=self.sha2, ref=self.sha1)  # asked for sha1, GitHub answers sha2
        self.assertEqual(self.add(extra=("--ref", self.sha1))[0][0], 2)
        self.assertEqual(self.add(url="https://gitlab.com/o/r")[0][0], 2)
        self.assertEqual(self.add(url="git@github.com:o/r.git")[0][0], 2)
        self.assertEqual(self.add(extra=("--ref=--evil",))[0][0], 2)
        self.assertEqual(self.add(name="../x")[0][0], 2)
        self.assertFalse((self.root / "sources").exists())
        self.assertEqual(git(self.root, "status", "--porcelain").strip(), "")

    def test_a_failed_add_names_both_leftovers(self):
        self.set_api()
        ext = FakeExt(self.tool, self.up, self.api, fail_fetch=True)
        (code, out), _ = self.add(ext)
        self.assertEqual(code, 2, out)
        self.assertIn("simulated network failure", out)
        self.assertIn("sources", out)
        self.assertIn(str(self.root / "sources/up"), out)
        self.assertIn("modules", out)
        self.assertIn("submodule.sources/up.url", out)
        self.assertFalse((self.root / ".gitmodules").exists())  # recorded nothing

    def test_github_urls_and_names_are_parsed_strictly(self):
        for ok in ("https://github.com/o/r", "https://github.com/o/r.git", "https://github.com/o/r/", "https://github.com/o/.github"):
            with self.subTest(ok=ok):
                self.assertIsNotNone(self.tool.parse_github(ok))
        for bad in ("https://github.com/o/.", "https://github.com/o/..", "https://github.com/o/.git", "https://github.com/o/x.git.git",
                    "https://github.com/o/r" + chr(10), "https://github.com/o/r" + chr(10) + "x", "http://github.com/o/r", "https://github.com/o"):
            with self.subTest(bad=bad):
                self.assertIsNone(self.tool.parse_github(bad))
                self.assertEqual(self.add(url=bad)[0][0], 2)
        self.assertIsNone(self.tool.NAME_RE.match("up" + chr(10)))
        self.assertEqual(self.add(name="up" + chr(10))[0][0], 2)
        self.assertEqual(self.add(name=".")[0][0], 2)
        self.assertFalse((self.root / "sources").exists())

    def test_add_refuses_an_existing_name_or_directory(self):
        self.set_api()
        self.assertEqual(self.add()[0][0], 0)
        self.assertEqual(self.add()[0][0], 2)  # recorded
        write(self.root / "sources/other/x", "x")
        self.assertEqual(self.add(name="other")[0][0], 2)  # directory exists

    def record(self, name="up", commit=None):
        self.set_api()
        (code, out), _ = self.add(name=name)
        self.assertEqual(code, 0, out)

    def test_update_summarises_moves_the_checkout_and_updates_the_record(self):
        self.record()
        self.set_api(sha=self.sha2, ref="HEAD")
        self.api[f"repos/o/r/compare/{self.sha1}...{self.sha2}"] = {
            "status": "ahead", "ahead_by": 1, "files": [{"status": "modified", "filename": "f.txt", "additions": 1, "deletions": 1}],
            "commits": [{"sha": self.sha2, "commit": {"message": "second: change f\n\nbody"}}]}
        ext = FakeExt(self.tool, self.up, self.api)
        code, out = self.run_main(ext, "update", "up")
        self.assertEqual(code, 0, out)
        self.assertIn(f"upstream changes {self.sha1[:7]}..{self.sha2[:7]}: ahead, 1 commit(s) ahead, 1 file(s) changed", out)
        self.assertIn("second: change f", out)
        self.assertIn("modified: f.txt (+1 -1)", out)
        self.assertEqual(git(self.root / "sources/up", "rev-parse", "HEAD").strip(), self.sha2)
        self.assertEqual(json.loads((self.root / "mercury.json").read_text())["sources"][0]["commit"], self.sha2)
        self.assertEqual(self.staged(), "")

    def test_update_refuses_local_modifications_in_the_checkout(self):
        self.record()
        self.set_api(sha=self.sha2, ref="HEAD")
        ext = FakeExt(self.tool, self.up, self.api)
        for change in ("tracked", "untracked"):
            with self.subTest(change=change):
                path = self.root / ("sources/up/f.txt" if change == "tracked" else "sources/up/new.txt")
                write(path, "local change\n")
                code, out = self.run_main(ext, "update", "up")
                self.assertEqual(code, 2, out)
                self.assertIn("local modifications", out)
                self.assertEqual(git(self.root / "sources/up", "rev-parse", "HEAD").strip(), self.sha1)
                self.assertEqual(path.read_text(), "local change\n")
                self.assertEqual(json.loads((self.root / "mercury.json").read_text())["sources"][0]["commit"], self.sha1)
                if change == "tracked":
                    git(self.root / "sources/up", "checkout", "-q", "--", "f.txt")
                else:
                    path.unlink()

    def test_update_to_the_same_commit_is_a_no_op(self):
        self.record()
        before = (self.root / "mercury.json").read_bytes()
        code, out = self.run_main(FakeExt(self.tool, self.up, self.api), "update", "up", "--to", self.sha1)
        self.assertEqual(code, 2, out)  # no fake answer for that ref yet
        self.api[f"repos/o/r/commits/{self.sha1}"] = {"sha": self.sha1}
        code, out = self.run_main(FakeExt(self.tool, self.up, self.api), "update", "up", "--to", self.sha1)
        self.assertEqual(code, 0, out)
        self.assertIn("nothing to move", out)
        self.assertEqual((self.root / "mercury.json").read_bytes(), before)

    def test_update_refuses_a_rewind_an_unknown_source_and_a_checkout_off_the_record(self):
        self.record()
        self.set_api(sha=self.sha2, ref="HEAD")
        self.api[f"repos/o/r/compare/{self.sha1}...{self.sha2}"] = {"status": "diverged", "commits": [], "files": []}
        ext = FakeExt(self.tool, self.up, self.api)
        self.assertEqual(self.run_main(ext, "update", "up")[0], 2)
        self.assertEqual(git(self.root / "sources/up", "rev-parse", "HEAD").strip(), self.sha1)
        self.assertEqual(self.run_main(ext, "update", "nope")[0], 2)
        git(self.root / "sources/up", "-c", "protocol.file.allow=always", "fetch", "-q", self.up.as_uri(), self.sha2)
        git(self.root / "sources/up", "checkout", "-q", "--detach", self.sha2)
        self.assertEqual(self.run_main(ext, "update", "up")[0], 2)

    def test_update_runs_the_generator_check_of_packs_that_use_the_source(self):
        self.record()
        gen = self.root / "packs/demo/build/generate.py"
        write(gen, "import pathlib, sys\nsys.exit(int(pathlib.Path(__file__).with_name('rc').read_text()))\n# uses sources/up\n")
        write(self.root / "packs/demo/build/rc", "1")
        write(self.root / "packs/other/build/generate.py", "import sys\nsys.exit(1)\n# no mention of the source\n")
        self.set_api(sha=self.sha2, ref="HEAD")
        self.api[f"repos/o/r/compare/{self.sha1}...{self.sha2}"] = {"status": "ahead", "ahead_by": 1, "commits": [], "files": []}
        ext = FakeExt(self.tool, self.up, self.api)
        code, out = self.run_main(ext, "update", "up")
        self.assertEqual(code, 1, out)
        self.assertIn("demo generator --check: FAILS", out)
        self.assertNotIn("other", out)
        self.assertEqual(json.loads((self.root / "mercury.json").read_text())["sources"][0]["commit"], self.sha2)  # moved and recorded
        self.assertEqual([c for c in ext.calls if c[0] == sys.executable and "--check" in c][0][-2].replace("\\", "/")[-28:], "packs/demo/build/generate.py")
        self.assertEqual(len([c for c in ext.calls if c[0] == sys.executable]), 1)
        write(self.root / "packs/demo/build/rc", "0")
        self.api[f"repos/o/r/commits/{self.sha2}"] = {"sha": self.sha2}
        code, out = self.run_main(ext, "update", "up", "--to", self.sha2)
        self.assertEqual(code, 0, out)
        self.assertIn("demo generator --check: passes", out)

    def test_the_sources_record_is_validated(self):
        tool = self.tool
        good = {"name": "a", "url": "https://github.com/o/r.git", "commit": "a" * 40, "license": "MIT", "added": "2026-01-01", "rationale": "x"}
        self.assertEqual(tool.load_sources([good]), [good])
        for bad in ({**good, "license": "GPL-3.0"}, {**good, "url": "https://gitlab.com/o/r.git"}, {**good, "commit": "abc"},
                    {**good, "added": "yesterday"}, {**good, "rationale": "two\nlines"}, {**good, "extra": 1}, {"name": "a"}):
            with self.subTest(bad=bad), self.assertRaises(tool.BuildError):
                tool.load_sources([bad])
        with self.assertRaises(tool.BuildError):
            tool.load_sources([good, {**good, "name": "A"}])

    def test_the_repository_records_its_two_submodules(self):
        master, _ = self.tool.load_master(ROOT)
        got = {e["name"]: e["commit"] for e in master["sources"]}
        self.assertEqual(got, {"claude-plugins-community": "88003be1129c09381d27100b37a8fdc21b3214bf",
                               "superpowers": "d884ae04edebef577e82ff7c4e143debd0bbec99"})


if __name__ == "__main__":
    unittest.main()
