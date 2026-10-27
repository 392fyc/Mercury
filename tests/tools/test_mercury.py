"""Pack installer (#674, #680): installation, stubs, idempotence, hand-edit protection, deletion scope, path safety."""
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "mercury.py"
ENTRY = "---\nname: alpha\ndescription: Alpha skill (Claude entry).\n---\n\nhand-written entry\n"
LOCK = ".mercury/lock.json"
CONFIG = ".mercury/config.json"


# Runs the tool in its legacy mode that reads the packs from the source's working tree and index (the mode Mercury uses on
# itself). The command line has no such switch; external targets always read a commit (see tests/tools/test_mercury_install.py).
WORKTREE_RUNNER = ("import importlib.util, sys; spec = importlib.util.spec_from_file_location('m', sys.argv[1]); "
                   "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); sys.exit(m.main(sys.argv[2:], worktree=True))")


def load_tool():
    spec = importlib.util.spec_from_file_location("mercury_tool", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def skill_md(name, desc):
    return f"---\nname: {name}\ndescription: {desc}\n---\n\nbody of {name}\n"


class MercuryToolTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src, self.dst = Path(tmp.name) / "src", Path(tmp.name) / "dst"
        self.dst.mkdir()
        p = self.src / "packs"
        write(p / "core/skills/alpha/SKILL.md", skill_md("alpha", "Alpha skill."))
        write(p / "core/skills/alpha/claude-entry.md", ENTRY)
        write(p / "core/skills/alpha/ref/a.md", "ref\r\n")
        write(p / "core/build/generate.py", "# Mercury-side generator, never installed\n")
        write(p / "core/agents.md", "## Core agents\n\nCore fragment for AGENTS.\n")
        write(p / "core/claude.md", "@AGENTS.md\n\nCore fragment for CLAUDE.\n")
        write(p / "extra/skills/beta/SKILL.md", skill_md("beta", "Beta skill."))
        write(p / "extra/skills/mercury-gamma/SKILL.md", "---\nname: mercury-gamma\ndescription: >-\n  Gamma\n  skill.\n---\n\ngamma\n")
        write(p / "extra/note.txt", "note\n")
        write(p / "off/note.txt", "off\n")
        self.master = {"schema_version": 1, "packs": {
            "core": {"enabled": True, "required": True}, "extra": {"enabled": True}, "off": {"enabled": False}}}
        self.packs = {
            "core": {"alpha": {"hosts": ["codex", "claude"], "claude_entry": "claude-entry.md"}},
            "extra": {"beta": {"hosts": ["codex", "claude"]}, "mercury-gamma": {"hosts": ["codex"]}},
            "off": {}}
        self.save_sources()
        self.select(["core", "extra"])
        # The tool lists a pack's files from the Git index, so the source tree is a throwaway repository.
        self.auto_index = True
        self.git("init", "-q")
        self.git("config", "core.autocrlf", "false")
        # Modes in the index come only from explicit `update-index --chmod`; on POSIX `add -A` would otherwise reset them.
        self.git("config", "core.fileMode", "false")

    def git(self, *args):
        r = subprocess.run(["git", "-C", str(self.src), *args], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def index(self):
        self.git("add", "-A")

    def save_sources(self):
        write(self.src / "mercury.json", json.dumps(self.master))
        for name, skills in self.packs.items():
            write(self.src / "packs" / name / "pack.json", json.dumps({"schema_version": 1, "name": name, "skills": skills}))

    def select(self, packs):
        write(self.dst / CONFIG, json.dumps({"schema_version": 1, "packs": packs}))

    def run_tool(self, mode):
        if self.auto_index:
            self.index()  # tests edit the source freely; the index follows unless a test turns this off
        r = subprocess.run([sys.executable, "-B", "-c", WORKTREE_RUNNER, str(TOOL), mode, "--source", str(self.src), "--target", str(self.dst)],
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    def apply(self):
        return self.run_tool("apply")

    def check(self):
        return self.run_tool("check")

    def snapshot(self):
        return {p.relative_to(self.dst).as_posix(): p.read_bytes() for p in sorted(self.dst.rglob("*")) if p.is_file()}

    # --- installation and stubs ---

    def test_first_installation(self):
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        s = self.snapshot()
        self.assertEqual(s[".mercury/packs/core/skills/alpha/SKILL.md"], (self.src / "packs/core/skills/alpha/SKILL.md").read_bytes())
        self.assertEqual(s[".mercury/packs/core/skills/alpha/ref/a.md"], b"ref\r\n")  # line endings untouched
        self.assertEqual(s[".mercury/packs/core/skills/alpha/claude-entry.md"], ENTRY.encode())  # the whole skill dir is installed
        self.assertEqual(s[".mercury/packs/extra/note.txt"], b"note\n")
        self.assertIn(".mercury/packs/core/pack.json", s)  # pack.json is installed too
        self.assertFalse(any("/build/" in k for k in s), "build/ must not be installed")
        self.assertFalse(any(k.startswith(".mercury/packs/off") for k in s), "a disabled pack that nobody selected stays out")
        lock = json.loads(s[LOCK])
        self.assertEqual(lock["schema_version"], 1)
        self.assertEqual(set(lock["files"]), set(s) - {LOCK, CONFIG, "AGENTS.md", "CLAUDE.md"})
        self.assertEqual(self.check()[0], 0)

    def test_stubs(self):
        self.apply()
        s = self.snapshot()
        claude_alpha = s[".claude/skills/mercury-alpha/SKILL.md"].decode()
        self.assertTrue(claude_alpha.startswith('---\nname: mercury-alpha\ndescription: "Alpha skill (Claude entry)."\n---\n'))
        self.assertIn(".mercury/packs/core/skills/alpha/claude-entry.md", claude_alpha)  # Claude stub points at the Claude entry
        self.assertIn("pack `core`", claude_alpha)
        codex_alpha = s[".agents/skills/mercury-alpha/SKILL.md"].decode()
        self.assertIn('description: "Alpha skill."', codex_alpha)  # Codex stub takes the real skill's description
        self.assertIn(".mercury/packs/core/skills/alpha/SKILL.md", codex_alpha)
        self.assertNotIn("claude-entry", codex_alpha)
        self.assertIn("name: mercury-beta\n", s[".claude/skills/mercury-beta/SKILL.md"].decode())
        self.assertIn(".mercury/packs/extra/skills/beta/SKILL.md", s[".claude/skills/mercury-beta/SKILL.md"].decode())
        # a skill whose name already starts with mercury- gets no second prefix; a folded description becomes one line
        gamma = s[".agents/skills/mercury-gamma/SKILL.md"].decode()
        self.assertIn("name: mercury-gamma\n", gamma)
        self.assertIn('description: "Gamma skill."', gamma)
        self.assertNotIn(".agents/skills/mercury-mercury-gamma/SKILL.md", s)
        self.assertNotIn(".claude/skills/mercury-gamma/SKILL.md", s)  # hosts: codex only
        self.assertFalse(any(k.startswith(".claude/skills/alpha") or k.startswith(".agents/skills/alpha") for k in s))

    def test_stub_copies_every_other_frontmatter_field(self):
        write(self.src / "packs/extra/skills/beta/SKILL.md",
              "---\nname: beta\ndescription: Beta skill.\nuser-invocable: true\nargument-hint: \"[auto] [text]\"\n"
              "allowed-tools: Read, Write, Bash\nmetadata:\n  owner: me\n  tags: [a, b]\nupstream_sha: \"abc\"\n---\n\nbody\n")
        write(self.src / "packs/core/skills/alpha/claude-entry.md",
              "---\nname: alpha\ndescription: Alpha skill (Claude entry).\nargument-hint: \"[plan]\"\n---\n\nentry\n")
        self.assertEqual(self.apply()[0], 0)
        beta = (self.dst / ".agents/skills/mercury-beta/SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(beta.startswith(
            "---\nname: mercury-beta\ndescription: \"Beta skill.\"\nuser-invocable: true\nargument-hint: \"[auto] [text]\"\n"
            "allowed-tools: Read, Write, Bash\nmetadata:\n  owner: me\n  tags: [a, b]\nupstream_sha: \"abc\"\n---\n"), beta)
        self.assertNotIn("name: beta", beta)  # the real name is not copied; only the stub name
        # for Claude the fields come from the Claude entry, not from SKILL.md
        claude = (self.dst / ".claude/skills/mercury-alpha/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("argument-hint: \"[plan]\"\n", claude)
        self.assertNotIn("user-invocable", claude)
        codex = (self.dst / ".agents/skills/mercury-alpha/SKILL.md").read_text(encoding="utf-8")
        self.assertNotIn("argument-hint", codex)  # the Codex stub follows SKILL.md, which has no such field

    def test_stub_carries_arguments_when_the_entry_uses_them(self):
        # Claude Code substitutes the placeholder only in the invoked SKILL.md (the stub), so the stub must pass it on.
        write(self.src / "packs/core/skills/alpha/claude-entry.md", ENTRY + "Task: $ARGUMENTS\n")  # Claude entry uses it
        write(self.src / "packs/extra/skills/beta/SKILL.md",
              "---\nname: beta\ndescription: Beta skill.\nargument-hint: \"$ARGUMENTS in frontmatter only\"\n---\n\nno placeholder in the body\n")
        write(self.src / "packs/extra/skills/mercury-gamma/SKILL.md",
              "---\nname: mercury-gamma\ndescription: Gamma.\n---\n\nUse $ARGUMENTS[0] first.\n")  # codex-only skill uses it
        self.assertEqual(self.apply()[0], 0)
        claude_alpha = (self.dst / ".claude/skills/mercury-alpha/SKILL.md").read_text(encoding="utf-8")
        self.assertIn("\nArguments: $ARGUMENTS\n", claude_alpha)
        self.assertIn("wherever the entry mentions `\\$ARGUMENTS`", claude_alpha)  # the second mention is escaped
        self.assertEqual(claude_alpha.count("$ARGUMENTS"), 2)
        self.assertLess(claude_alpha.index("Arguments:"), claude_alpha.index("Read `"))
        # the Codex stub of alpha follows SKILL.md, whose body has no placeholder: nothing is carried
        self.assertNotIn("Arguments:", (self.dst / ".agents/skills/mercury-alpha/SKILL.md").read_text(encoding="utf-8"))
        # a placeholder only in the frontmatter does not count; the indexed form does
        self.assertNotIn("Arguments:", (self.dst / ".claude/skills/mercury-beta/SKILL.md").read_text(encoding="utf-8"))
        gamma = (self.dst / ".agents/skills/mercury-gamma/SKILL.md").read_text(encoding="utf-8")  # no double prefix
        self.assertIn("\nArguments: $ARGUMENTS\n", gamma)

    def test_unsupported_frontmatter_line_exit_2(self):
        write(self.src / "packs/extra/skills/beta/SKILL.md", "---\nname: beta\ndescription: B.\nnot a field line\n---\n\nb\n")
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertFalse((self.dst / ".mercury/packs").exists())

    def test_description_forms(self):
        for fm, want in (('description: "quoted: yes # ok"', "quoted: yes # ok"), ("description: 'it''s'", "it's"),
                         ("description: |\n  line one\n  line two", "line one line two"),
                         ("description: plain: text", "plain: text")):
            with self.subTest(fm=fm):
                write(self.src / "packs/extra/skills/beta/SKILL.md", f"---\nname: beta\n{fm}\n---\n\nb\n")
                self.assertEqual(self.apply()[0], 0)
                text = (self.dst / ".agents/skills/mercury-beta/SKILL.md").read_text(encoding="utf-8")
                self.assertIn("description: " + json.dumps(want, ensure_ascii=False) + "\n", text)
                # the stub's frontmatter is valid JSON-style YAML: the description round-trips
                line = [l for l in text.split("\n") if l.startswith("description: ")][0]
                self.assertEqual(json.loads(line[len("description: "):]), want)

    def test_bad_descriptions_exit_2(self):
        for body in ("---\nname: beta\n---\n\nb\n", "---\nname: beta\ndescription:\n---\n\nb\n", "no frontmatter\n",
                     '---\nname: beta\ndescription: "unterminated\n---\n'):
            with self.subTest(body=body):
                write(self.src / "packs/extra/skills/beta/SKILL.md", body)
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertFalse((self.dst / ".mercury/packs").exists())  # nothing written

    def test_control_characters_in_frontmatter_are_refused(self):
        # C0, DEL and C1 controls could change how YAML reads a stub; refuse them in the description and in copied fields.
        for ch in (chr(1), chr(0x7f), chr(0x9f), chr(0)):
            for body in (f"---\nname: beta\ndescription: bad{ch}text\n---\n\nb\n",
                         f"---\nname: beta\ndescription: ok\nargument-hint: x{ch}y\n---\n\nb\n"):
                with self.subTest(code=ord(ch), body=body):
                    write(self.src / "packs/extra/skills/beta/SKILL.md", body)
                    code, out = self.apply()
                    self.assertEqual(code, 2, out)
                    self.assertIn("control character", out)
                    self.assertFalse((self.dst / ".mercury/packs").exists())

    def test_repeat_is_noop(self):
        self.apply()
        before = self.snapshot()
        mtimes = {p: p.stat().st_mtime_ns for p in self.dst.rglob("*") if p.is_file()}
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        self.assertIn("wrote 0, deleted 0", out)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.dst.rglob("*") if p.is_file()}, mtimes)

    def test_executable_flag_comes_from_the_git_index_and_is_locked(self):
        write(self.src / "packs/extra/run.sh", "#!/bin/sh\necho hi\n")
        self.index()
        self.git("update-index", "--chmod=+x", "packs/extra/run.sh")
        self.assertEqual(self.apply()[0], 0)
        lock = json.loads((self.dst / LOCK).read_text())
        self.assertEqual(lock["executable"], [".mercury/packs/extra/run.sh"])  # the same list on every platform
        self.git("update-index", "--chmod=-x", "packs/extra/run.sh")
        self.assertEqual(self.check()[0], 1)  # the lock records the flag, so a mode change in the index is a difference
        self.assertEqual(self.apply()[0], 0)
        self.assertEqual(json.loads((self.dst / LOCK).read_text())["executable"], [])

    @unittest.skipIf(sys.platform == "win32", "executable bits do not exist on Windows")
    def test_executable_bit_is_carried_and_drift_is_reported(self):
        write(self.src / "packs/extra/run.sh", "#!/bin/sh\necho hi\n")
        self.index()
        self.git("update-index", "--chmod=+x", "packs/extra/run.sh")
        self.assertEqual(self.apply()[0], 0)
        run, note = self.dst / ".mercury/packs/extra/run.sh", self.dst / ".mercury/packs/extra/note.txt"
        self.assertTrue(os.access(run, os.X_OK))
        self.assertFalse(os.access(note, os.X_OK))
        run.chmod(0o644)  # same bytes, wrong mode: a difference, repaired without touching the content
        code, out = self.check()
        self.assertEqual(code, 1, out)
        self.assertIn("mode: .mercury/packs/extra/run.sh", out)
        self.assertEqual(self.apply()[0], 0)
        self.assertTrue(os.access(run, os.X_OK))
        note.chmod(0o755)  # and a file that should not be executable
        self.assertEqual(self.check()[0], 1)
        self.assertEqual(self.apply()[0], 0)
        self.assertFalse(os.access(note, os.X_OK))

    # --- what counts as a pack file: the Git index ---

    def test_untracked_files_are_not_installed(self):
        self.index()
        self.auto_index = False
        write(self.src / "packs/extra/junk.txt", "junk\n")
        write(self.src / "packs/extra/skills/beta/junk.md", "junk\n")
        write(self.src / "packs/core/__pycache__/x.pyc", "junk\n")
        self.assertEqual(self.apply()[0], 0)
        s = self.snapshot()
        self.assertFalse(any("junk" in k or "pyc" in k for k in s), sorted(s))
        self.assertIn(".mercury/packs/extra/note.txt", s)

    def test_pack_files_need_git(self):
        self.index()
        self.auto_index = False

        def unlock(func, path, _exc):  # git object files are read-only on Windows
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(self.src / ".git", onerror=unlock)
        code, out = self.apply()
        self.assertEqual(code, 2, out)  # an error, not a directory walk
        self.assertIn("git", out)
        self.assertFalse((self.dst / ".mercury/packs").exists())

    def test_tracked_file_missing_from_disk_exit_2(self):
        self.index()
        self.auto_index = False
        (self.src / "packs/extra/note.txt").unlink()
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("note.txt", out)
        self.assertFalse((self.dst / ".mercury/packs").exists())

    def test_untracked_pack_json_or_skill_is_an_error(self):
        self.index()
        self.auto_index = False
        self.git("rm", "-q", "--cached", "packs/extra/skills/beta/SKILL.md")
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("SKILL.md", out)
        self.index()
        self.git("rm", "-q", "--cached", "packs/extra/pack.json")
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("pack.json", out)
        self.assertFalse((self.dst / ".mercury/packs").exists())

    # --- case folding and trailing dots (Windows sees one file where Linux sees two) ---

    def pack_dir_names(self, rel):
        return sorted(os.listdir(self.dst / rel))

    def set_lock_entry(self, old, new):
        lock = self.dst / LOCK
        data = json.loads(lock.read_text())
        data["files"][new] = data["files"].pop(old)
        lock.write_text(json.dumps(data))

    def test_lock_spelling_that_folds_to_a_wanted_path_does_not_delete_it(self):
        self.apply()
        self.set_lock_entry(".mercury/packs/core/pack.json", ".mercury/packs/core/PACK.JSON")
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        self.assertIn("pack.json", self.pack_dir_names(".mercury/packs/core"))  # present, with the wanted spelling
        self.assertNotIn("PACK.JSON", self.pack_dir_names(".mercury/packs/core"))
        files = json.loads((self.dst / LOCK).read_text())["files"]
        self.assertIn(".mercury/packs/core/pack.json", files)
        self.assertNotIn(".mercury/packs/core/PACK.JSON", files)
        self.assertEqual(self.check()[0], 0)

    def test_lock_entry_with_a_trailing_dot_is_refused_and_deletes_nothing(self):
        self.apply()
        self.set_lock_entry(".mercury/packs/core/pack.json", ".mercury/packs/core/pack.json.")
        before = self.snapshot()
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("pack.json", self.pack_dir_names(".mercury/packs/core"))
        self.assertEqual(self.snapshot(), before)

    def test_lock_entries_that_collide_after_folding_are_refused(self):
        self.apply()
        lock = self.dst / LOCK
        data = json.loads(lock.read_text())
        data["files"][".mercury/packs/core/PACK.JSON"] = data["files"][".mercury/packs/core/pack.json"]
        lock.write_text(json.dumps(data))
        before = self.snapshot()
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("same path", out)
        self.assertEqual(self.snapshot(), before)  # zero writes

    def test_case_only_rename_inside_a_pack_keeps_the_file(self):
        self.apply()
        os.rename(self.src / "packs/extra/note.txt", self.src / "packs/extra/Note.txt")
        self.git("rm", "-q", "--cached", "packs/extra/note.txt")  # git on a case-insensitive disk would not notice the rename
        self.auto_index = False
        self.index()
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        names = self.pack_dir_names(".mercury/packs/extra")
        self.assertIn("Note.txt", names)
        self.assertNotIn("note.txt", names)
        self.assertEqual((self.dst / ".mercury/packs/extra/Note.txt").read_bytes(), b"note\n")
        files = json.loads((self.dst / LOCK).read_text())["files"]
        self.assertIn(".mercury/packs/extra/Note.txt", files)
        self.assertNotIn(".mercury/packs/extra/note.txt", files)
        self.assertEqual(self.check()[0], 0)
        self.assertIn("wrote 0, deleted 0", self.apply()[1])

    @staticmethod
    def is_case_insensitive(directory):
        """Create CaseProbe.tmp and ask for caseprobe.tmp: does this file system fold case?"""
        probe = directory / "CaseProbe.tmp"
        probe.write_bytes(b"")
        try:
            return (directory / "caseprobe.tmp").exists()
        finally:
            probe.unlink()

    def test_the_plan_of_a_case_only_rename_does_not_depend_on_the_file_system(self):
        """#686: assert plan() itself. The apply-level test above passes on Linux whichever way the rename is planned.

        The installed note.txt is in the lock; the source now spells it Note.txt. On a case-insensitive file system
        (Windows, default macOS) that is one file, so the plan rewrites it ("rename"); on a case-sensitive one (Linux) it
        is two, so the plan creates the new spelling ("create"). Which one applies is probed on the target directory
        itself, not guessed from sys.platform. On both the old spelling must be deleted, the new one written exactly
        once, and nothing reported as a conflict.
        """
        self.apply()
        os.rename(self.src / "packs/extra/note.txt", self.src / "packs/extra/Note.txt")
        self.git("rm", "-q", "--cached", "packs/extra/note.txt")
        self.auto_index = False
        self.index()
        tool = load_tool()
        src = tool.WorkTreeSource(self.src)
        master, src.master_hash = tool.load_master(src)
        selected, chash, extras, _ = tool.prepare_config(src, self.dst, master, None, False)
        P = tool.plan(src, self.dst, selected, chash, extras, False, False)
        old, new = ".mercury/packs/extra/note.txt", ".mercury/packs/extra/Note.txt"
        self.assertEqual(P.conflicts, [])
        self.assertEqual(P.deletes, [old])
        writes = [(rel, kind) for rel, kind in P.writes if rel in (old, new)]
        self.assertEqual(len(writes), 1, P.writes)
        self.assertEqual(writes[0][0], new)
        self.assertIn(writes[0][1], ("create", "rename"))
        self.assertEqual(writes[0][1], "rename" if self.is_case_insensitive(self.dst) else "create")

    def test_case_only_rename_with_a_hand_edit_stops(self):
        self.apply()
        (self.dst / ".mercury/packs/extra/note.txt").write_bytes(b"mine\n")
        os.rename(self.src / "packs/extra/note.txt", self.src / "packs/extra/Note.txt")
        self.git("rm", "-q", "--cached", "packs/extra/note.txt")
        self.auto_index = False
        self.index()
        before = self.snapshot()
        code, out = self.apply()
        self.assertEqual(code, 1, out)
        self.assertEqual(self.snapshot(), before)

    # --- disabled and unknown packs ---

    def test_disabled_pack_is_refused(self):
        self.select(["core", "off"])
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("off", out)
        self.assertIn("disabled", out)
        self.assertFalse((self.dst / ".mercury/packs").exists())
        self.assertEqual(self.check()[0], 2)

    def test_disabling_after_installation_stops_the_pack(self):
        self.apply()
        self.master["packs"]["extra"]["enabled"] = False
        self.save_sources()
        code, out = self.apply()
        self.assertEqual(code, 2, out)  # the config still selects it: refused, nothing deleted
        self.assertTrue((self.dst / ".mercury/packs/extra/note.txt").exists())

    def test_required_pack_cannot_be_disabled_and_is_always_installed(self):
        self.select(["extra"])  # core not listed: still installed
        self.assertEqual(self.apply()[0], 0)
        self.assertTrue((self.dst / ".mercury/packs/core/pack.json").exists())
        self.master["packs"]["core"]["enabled"] = False
        self.save_sources()
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("required", out)

    def test_unknown_pack_is_error(self):
        self.select(["core", "nope"])
        code, out = self.apply()
        self.assertEqual(code, 2)
        self.assertIn("nope", out)

    # --- hand edits, check mode, adoption ---

    def test_hand_edit_stops_apply(self):
        self.apply()
        (self.dst / ".mercury/packs/core/skills/alpha/SKILL.md").write_bytes(b"edited by hand\n")
        write(self.src / "packs/core/skills/alpha/SKILL.md", skill_md("alpha", "Alpha skill.") + "new\n")
        write(self.src / "packs/extra/note.txt", "note v2\n")  # would also change an untouched file
        before = self.snapshot()
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("modified by hand", out)
        self.assertEqual(self.snapshot(), before)  # nothing written, not even the unrelated update

    def test_hand_edited_stub_stops_apply(self):
        self.apply()
        (self.dst / ".claude/skills/mercury-alpha/SKILL.md").write_bytes(b"mine\n")
        write(self.src / "packs/core/skills/alpha/claude-entry.md", ENTRY.replace("Alpha skill (Claude entry).", "Changed."))
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("mercury-alpha", out)
        self.assertEqual((self.dst / ".claude/skills/mercury-alpha/SKILL.md").read_bytes(), b"mine\n")

    def test_check_reports_difference(self):
        self.assertEqual(self.check()[0], 1)  # nothing installed yet
        self.assertFalse((self.dst / ".mercury/packs").exists())
        self.apply()
        (self.dst / ".mercury/packs/extra/note.txt").write_bytes(b"x\n")
        code, out = self.check()
        self.assertEqual(code, 1)
        self.assertIn("note.txt", out)
        self.assertEqual((self.dst / ".mercury/packs/extra/note.txt").read_bytes(), b"x\n")  # check never writes

    def test_unmanaged_file_adopted_only_when_identical(self):
        write(self.dst / ".mercury/packs/extra/note.txt", "note\n")  # identical to the installation: adopted
        self.assertEqual(self.apply()[0], 0)
        self.assertIn(".mercury/packs/extra/note.txt", json.loads((self.dst / LOCK).read_text())["files"])
        self.select(["core"])
        self.apply()
        write(self.dst / ".agents/skills/mercury-mercury-gamma/SKILL.md", "x\n")  # unrelated path: not touched
        write(self.dst / ".claude/skills/mercury-beta/SKILL.md", "mine\n")  # unmanaged and different
        self.select(["core", "extra"])
        code, out = self.apply()
        self.assertEqual(code, 1, out)
        self.assertEqual((self.dst / ".claude/skills/mercury-beta/SKILL.md").read_bytes(), b"mine\n")

    # --- deselection and the deletion scope ---

    def test_deselected_pack_cleanup(self):
        self.apply()
        self.select(["core"])
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        s = self.snapshot()
        self.assertFalse(any("extra" in k or "beta" in k or "gamma" in k for k in s))
        self.assertFalse((self.dst / ".mercury/packs/extra").exists())  # emptied directories removed
        self.assertFalse((self.dst / ".claude/skills/mercury-beta").exists())
        self.assertIn(".mercury/packs/core/skills/alpha/SKILL.md", s)
        self.assertNotIn(".mercury/packs/extra/note.txt", json.loads(s[LOCK])["files"])

    def test_deselected_pack_keeps_hand_edited_file(self):
        self.apply()
        (self.dst / ".mercury/packs/extra/note.txt").write_bytes(b"mine\n")
        self.select(["core"])
        code, out = self.apply()
        self.assertEqual(code, 1)
        self.assertIn("note.txt", out)
        self.assertEqual((self.dst / ".mercury/packs/extra/note.txt").read_bytes(), b"mine\n")
        self.assertTrue((self.dst / ".claude/skills/mercury-beta/SKILL.md").exists())  # nothing partially applied

    def test_prune_stops_at_managed_roots(self):
        self.apply()
        self.select(["core"])
        self.assertEqual(self.apply()[0], 0)
        self.assertTrue((self.dst / ".claude/skills").is_dir())  # the skill root itself stays
        self.assertTrue((self.dst / ".agents/skills").is_dir())
        self.assertTrue((self.dst / ".mercury/packs").is_dir())  # and so does .mercury/packs
        self.assertTrue((self.dst / ".mercury").is_dir())

    def test_deletion_scope_reaches_packs_and_mercury_prefixed_skill_dirs(self):
        self.apply()
        lock = self.dst / LOCK
        for rel in (".claude/skills/mercury-old/SKILL.md", ".agents/skills/mercury-old/deep/x.md", ".mercury/packs/gone/a.txt"):
            write(self.dst / rel, "old\n")
        data = json.loads(lock.read_text())
        for rel in (".claude/skills/mercury-old/SKILL.md", ".agents/skills/mercury-old/deep/x.md", ".mercury/packs/gone/a.txt"):
            data["files"][rel] = hashlib.sha256(b"old\n").hexdigest()
        lock.write_text(json.dumps(data))
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.dst / ".claude/skills/mercury-old").exists())
        self.assertFalse((self.dst / ".agents/skills/mercury-old").exists())
        self.assertFalse((self.dst / ".mercury/packs/gone").exists())

    def test_lock_entries_outside_the_deletion_scope_are_refused(self):
        self.apply()
        write(self.dst / "README.md", "readme\n")
        write(self.dst / ".mercury/state/s.json", "{}\n")
        write(self.dst / ".claude/skills/own-skill/SKILL.md", "mine\n")  # a skill dir without the mercury- prefix
        write(self.dst / ".agents/skills/mercury-loose", "x\n")  # a file directly in the skill root
        write(self.dst / ".claude/settings.json", "{}\n")
        lock = self.dst / LOCK
        for victim in ("README.md", CONFIG, LOCK, ".mercury/state/s.json", ".claude/skills/own-skill/SKILL.md",
                       ".agents/skills/mercury-loose", ".claude/settings.json", ".mercury/packs", "mercury.json",
                       ".agents/skills/mercury-"):
            with self.subTest(victim=victim):
                data = json.loads(lock.read_text())
                data["files"][victim] = "0" * 64
                lock.write_text(json.dumps(data))
                before = self.snapshot()
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertEqual({k: v for k, v in self.snapshot().items() if k != LOCK}, {k: v for k, v in before.items() if k != LOCK})
                del data["files"][victim]
                lock.write_text(json.dumps(data))

    def test_tampered_lock_cannot_delete_outside(self):
        self.apply()
        victim = self.dst.parent / "keep.txt"
        victim.write_bytes(b"keep\n")
        lock = self.dst / LOCK
        data = json.loads(lock.read_text())
        data["files"]["../keep.txt"] = "0" * 64
        lock.write_text(json.dumps(data))
        before = self.snapshot()
        code, _ = self.apply()
        self.assertEqual(code, 2)
        self.assertTrue(victim.exists())
        self.assertEqual(self.snapshot(), before)  # a refused lock means zero writes

    # --- path safety ---

    def test_windows_path_bypasses_rejected_by_the_path_rules(self):
        tool = load_tool()
        for bad in ("../escape.txt", ".git/config", "/abs.txt", "a/../../b.txt", "sub/.git/hooks", ".git./config", ".git /config",
                    "GIT~1/config", ".mercury/lock.json.", ".MERCURY/lock.json", ".mercury/lock.json ", ".mercury/config.json",
                    "Mercury/../x", "CON", "aux.txt", "x/NUL.txt", "a:b", "x/...", "a\\b", "c:/x.txt", "ctl\x01.txt", "a//b", "",
                    ".claude/skills/mercury-./x", ".claude/skills/mercury- /x", "a /b", "a./b", "x/file.", "x/file "):
            with self.subTest(path=bad):
                with self.assertRaises(tool.BuildError):
                    tool.check_rel(bad, "test")
        for good in (".mercury/packs/core/skills/alpha/SKILL.md", ".claude/skills/mercury-x/SKILL.md", "docs/guides/a b.md"):
            self.assertEqual(tool.check_rel(good, "test"), good)

    def test_windows_path_bypasses_rejected_in_lock(self):
        self.apply()
        git = self.dst / ".git"
        write(git / "config", "[core]\n")
        lock = self.dst / LOCK
        for bad in (".git./config", ".git /config", "GIT~1/config", ".MERCURY/lock.json", ".mercury/packs./x", ".mercury/packs/../config.json"):
            with self.subTest(entry=bad):
                data = json.loads(lock.read_text())
                data["files"][bad] = "0" * 64
                lock.write_text(json.dumps(data))
                code, _ = self.apply()
                self.assertEqual(code, 2)
                self.assertEqual((git / "config").read_bytes(), b"[core]\n")

    def test_unsafe_names_in_pack_json_exit_2(self):
        for skills in ({"../evil": {"hosts": ["codex"]}}, {"beta": {"hosts": ["codex", "claude"], "claude_entry": "../SKILL.md"}},
                       {"beta": {"hosts": ["codex", "claude"], "claude_entry": "C:/x.md"}}, {"beta": {"hosts": ["codex", "claude"], "claude_entry": "missing.md"}},
                       {"beta": {"hosts": ["vim"]}}, {"beta": {"hosts": []}}, {"beta": "x"}):
            with self.subTest(skills=skills):
                self.packs["extra"] = skills
                self.save_sources()
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertFalse((self.dst / ".mercury/packs").exists())
                self.assertFalse((self.dst / ".claude").exists())

    def test_two_skills_cannot_share_a_stub(self):
        write(self.src / "packs/extra/skills/gamma/SKILL.md", skill_md("gamma", "Gamma."))
        self.packs["extra"]["gamma"] = {"hosts": ["codex"]}  # stub mercury-gamma collides with the skill mercury-gamma
        self.save_sources()
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("twice", out)

    def test_lock_directory_cannot_be_a_file(self):
        write(self.dst / ".agents", "i am a file\n")
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertFalse((self.dst / ".claude").exists())  # nothing written before the failure
        self.assertFalse((self.dst / ".mercury/packs").exists())

    # A directory link is refused whether it is a Windows junction or a POSIX directory symlink. The
    # bodies are shared; one test per link kind (#686). Junctions run on Windows only. Directory symlinks
    # run wherever the OS lets the user create one (always on Linux, in CI); where it does not (an
    # unprivileged Windows account) the test skips and the junction test still covers Windows.

    def make_junction(self, link, target):
        import _winapi
        _winapi.CreateJunction(str(target), str(link))

    def make_dir_symlink(self, link, target):
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlinks not available")

    def link_in_target_is_refused(self, make_link):
        outside = self.dst.parent / "outside"
        outside.mkdir()
        (self.dst / ".agents").mkdir()
        make_link(self.dst / ".agents" / "skills", outside)
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.check()[0], 2)

    def link_for_packs_directory_is_refused(self, make_link):
        outside = self.dst.parent / "outside"
        outside.mkdir()
        make_link(self.dst / ".mercury" / "packs", outside)
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertEqual(list(outside.iterdir()), [])

    def link_for_lock_directory_is_refused(self, make_link):
        outside = self.dst.parent / "outside"
        write(outside / "config.json", json.dumps({"schema_version": 1, "packs": ["core"]}))
        (self.dst / CONFIG).unlink()
        (self.dst / ".mercury").rmdir()
        make_link(self.dst / ".mercury", outside)
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertEqual([p.name for p in outside.iterdir()], ["config.json"])

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_in_target_is_refused(self):
        self.link_in_target_is_refused(self.make_junction)

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_for_packs_directory_is_refused(self):
        self.link_for_packs_directory_is_refused(self.make_junction)

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_for_lock_directory_is_refused(self):
        self.link_for_lock_directory_is_refused(self.make_junction)

    def test_directory_symlink_in_target_is_refused(self):
        self.link_in_target_is_refused(self.make_dir_symlink)

    def test_directory_symlink_for_packs_directory_is_refused(self):
        self.link_for_packs_directory_is_refused(self.make_dir_symlink)

    def test_directory_symlink_for_lock_directory_is_refused(self):
        self.link_for_lock_directory_is_refused(self.make_dir_symlink)

    def test_link_inside_a_pack_source_is_refused(self):
        target = self.src / "packs/extra/note.txt"
        link = self.src / "packs/extra/link.txt"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not available")
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertFalse((self.dst / ".mercury/packs").exists())

    # --- malformed input ---

    def test_bad_inputs_exit_2_without_partial_writes(self):
        (self.src / "packs/extra/skills/beta/SKILL.md").write_bytes(b"---\nname: beta\ndescription: \xff\xfe\n---\n")
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertFalse((self.dst / ".agents").exists())
        self.assertFalse((self.dst / ".mercury/packs").exists())

    def test_malformed_sources_exit_2(self):
        good_master = json.loads(json.dumps(self.master))
        for bad in ({"schema_version": 2, "packs": good_master["packs"]}, {"schema_version": 1, "packs": {}},
                    {"schema_version": 1, "packs": {"core": "x"}}, {"schema_version": 1, "packs": {"core": {"enabled": "yes"}}},
                    {"schema_version": 1, "packs": {"core": {"enabled": False, "required": True}}}, []):
            with self.subTest(master=bad):
                write(self.src / "mercury.json", json.dumps(bad))
                self.assertEqual(self.apply()[0], 2)
        write(self.src / "mercury.json", json.dumps(good_master))
        for bad in ('{"schema_version": 1, "name": "wrong", "skills": {}}', '{"schema_version": 1, "name": "extra", "skills": []}', "not json", "[]"):
            with self.subTest(pack_json=bad):
                (self.src / "packs/extra/pack.json").write_text(bad)
                self.assertEqual(self.apply()[0], 2)
        self.save_sources()
        (self.src / "packs/extra/pack.json").unlink()
        self.assertEqual(self.apply()[0], 2)
        self.save_sources()
        for bad in ("not json", '{"schema_version": 1, "packs": "core"}', '{"schema_version": 2, "packs": []}', "[]"):
            with self.subTest(config=bad):
                (self.dst / CONFIG).write_text(bad)
                self.assertEqual(self.apply()[0], 2)
        self.assertFalse((self.dst / ".mercury/packs").exists())

    def test_missing_skill_files_exit_2(self):
        (self.src / "packs/extra/skills/beta/SKILL.md").unlink()
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("SKILL.md", out)

    # --- the real repository ---

    def test_repository_packs_are_consistent(self):
        tool = load_tool()
        master, _ = tool.load_master(ROOT)
        on_disk = {p.name for p in (ROOT / "packs").iterdir() if p.is_dir()}
        self.assertEqual(set(master["packs"]), on_disk)
        for name in on_disk:
            tool.load_pack(ROOT / "packs" / name, name)
        self.assertTrue(master["packs"]["core"]["required"])
        self.assertFalse(master["packs"]["playwright-mcp"]["enabled"])

    # --- roles, rules, managed sections, linked repositories (#682) ---

    ROLE = ('---\ndescription: "Reviews things."\nclaude:\n  model: opus\n  tools: "Read, Grep"\n  disallowedTools: "Edit, Write"\n'
            '  permissionMode: plan\n  readonly_guard: true\ncodex:\n  model: gpt-6-luna\n  model_reasoning_effort: max\n'
            '  sandbox_mode: read-only\n  web_search: live\n---\n\n# Role: checker\n\nBody text.\n')
    DEV_ROLE = ('---\ndescription: "Builds things."\nclaude:\n  model: sonnet\ncodex:\n  sandbox_mode: workspace-write\n---\n\n# Role: builder\n')

    def add_role(self, name, text):
        write(self.src / "packs/core/roles" / f"{name}.md", text)

    def section(self, name="AGENTS.md"):
        text = (self.dst / name).read_bytes().decode("utf-8")
        return text

    def frontmatter_of(self, rel):
        text = (self.dst / rel).read_bytes().decode("utf-8")
        head = text.split("\n---\n", 1)[0]
        self.assertTrue(head.startswith("---\n"))
        return head[4:].split("\n")

    def test_roles_are_generated_in_both_formats(self):
        import tomllib
        self.add_role("reviewer", self.ROLE)
        self.add_role("dev", self.DEV_ROLE)
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        fm = self.frontmatter_of(".claude/agents/mercury-reviewer.md")
        self.assertEqual(fm[:7], ['name: "mercury-reviewer"', 'description: "Reviews things."', 'model: "opus"',
                                  'tools: "Read, Grep"', 'disallowedTools: "Edit, Write"', 'permissionMode: "plan"', "hooks:"])
        self.assertIn('    - matcher: "Bash|PowerShell"', fm)
        self.assertIn("          shell: bash", fm)
        self.assertIn("          timeout: 60", fm)
        self.assertIn('          onFailure: "block"', fm)
        cmd = json.loads(next(l for l in fm if l.startswith("          command: "))[len("          command: "):])
        self.assertEqual(cmd, 'sh "${CLAUDE_PROJECT_DIR}/.mercury/packs/core/hooks/readonly_guard.sh" || exit 2')
        self.assertNotIn("hooks:", "\n".join(self.frontmatter_of(".claude/agents/mercury-dev.md")))  # no guard without readonly_guard
        self.assertEqual(self.frontmatter_of(".claude/agents/mercury-dev.md"), ['name: "mercury-dev"', 'description: "Builds things."', 'model: "sonnet"'])
        toml = tomllib.loads((self.dst / ".codex/agents/mercury-reviewer.toml").read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in toml.items() if k != "developer_instructions"},
                         {"name": "mercury-reviewer", "description": "Reviews things.", "model": "gpt-6-luna",
                          "model_reasoning_effort": "max", "sandbox_mode": "read-only", "web_search": "live"})
        self.assertEqual(toml["developer_instructions"], "# Role: checker\n\nBody text.\n")
        dev = tomllib.loads((self.dst / ".codex/agents/mercury-dev.toml").read_text(encoding="utf-8"))
        self.assertEqual((dev["name"], dev["sandbox_mode"], "model" in dev), ("mercury-dev", "workspace-write", False))
        lock = json.loads((self.dst / LOCK).read_text())
        self.assertIn(".claude/agents/mercury-reviewer.md", lock["files"])
        self.assertIn(".codex/agents/mercury-dev.toml", lock["files"])
        self.assertTrue(self.apply()[1].startswith("mercury: wrote 0, deleted 0"))

    def test_role_text_is_escaped_for_yaml_and_toml(self):
        import tomllib
        desc = 'Quote " backslash \\ colon: hash # dash --- unicode \u00e9\u4e2d'
        body = '# Role\n\nTriple """ quotes, a backslash \\ at end \\\n``` fenced "json": {"a": 1}\n--- not frontmatter\nit\'s fine\n'
        text = ('---\ndescription: ' + json.dumps(desc, ensure_ascii=False) + '\nclaude:\n  model: opus\n  readonly_guard: true\n'
                'codex:\n  sandbox_mode: read-only\n---\n\n' + body)
        self.add_role("esc", text)
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        fm = self.frontmatter_of(".claude/agents/mercury-esc.md")
        self.assertEqual(json.loads(fm[1][len("description: "):]), desc)  # a JSON string is a YAML double-quoted scalar
        toml = tomllib.loads((self.dst / ".codex/agents/mercury-esc.toml").read_text(encoding="utf-8"))
        self.assertEqual((toml["description"], toml["developer_instructions"]), (desc, body))
        self.assertTrue((self.dst / ".claude/agents/mercury-esc.md").read_bytes().endswith(body.encode("utf-8")))

    def test_roles_that_cannot_be_written_safely_are_refused(self):
        bad = {
            "triple": self.ROLE.replace("Body text.", "has ''' inside"),
            "control-body": self.ROLE.replace("Body text.", "bell \x07 here"),
            "control-desc": self.ROLE.replace("Reviews things.", "bell \\u0007 here"),
            "newline-desc": self.ROLE.replace("Reviews things.", "two\\nlines"),
            "unknown-key": self.ROLE.replace("  web_search: live\n", "  web_search: live\n  evil: x\n"),
            "unknown-top": self.ROLE.replace("claude:\n", "extra: x\nclaude:\n", 1),
            "no-model": self.ROLE.replace("  model: opus\n", ""),
            "bad-enum": self.ROLE.replace("sandbox_mode: read-only", "sandbox_mode: danger-full-access"),
            "bad-value": self.ROLE.replace("model: opus", "model: [a, b]"),
            "no-desc": self.ROLE.replace('description: "Reviews things."\n', ""),
            "no-host": '---\ndescription: "x"\n---\n\nbody\n',
            "no-frontmatter": "# just a body\n",
            "empty-body": self.ROLE.split("---\n\n")[0] + "---\n\n",
            "twice": self.ROLE.replace("codex:\n", "claude:\n  model: opus\ncodex:\n", 1),
            "surrogate-desc": self.ROLE.replace("Reviews things.", "lone \\ud800 surrogate"),
            "bidi-desc": self.ROLE.replace("Reviews things.", "bidi \\u202e text"),
            "bom-desc": self.ROLE.replace("Reviews things.", "bom \\ufeff here"),
            "bidi-body": self.ROLE.replace("Body text.", "bidi \u2067 body"),
            "bom-body": self.ROLE.replace("Body text.", "bom \ufeff body"),
        }
        self.apply()
        before = self.snapshot()
        for name, text in bad.items():
            with self.subTest(case=name):
                self.add_role("r", text)
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertEqual(self.snapshot(), before)
        self.add_role("r", self.ROLE)
        (self.src / "packs/core/roles/r.md").rename(self.src / "packs/core/roles/Bad_Name.md")
        self.assertEqual(self.apply()[0], 2)
        self.assertEqual(self.snapshot(), before)

    def test_removed_role_is_deleted_and_hand_edit_stops(self):
        self.add_role("r", self.ROLE)
        self.apply()
        (self.src / "packs/core/roles/r.md").unlink()
        # a hand-edited generated role is never deleted or overwritten
        (self.dst / ".claude/agents/mercury-r.md").write_bytes(b"mine\n")
        before = self.snapshot()
        code, out = self.apply()
        self.assertEqual(code, 1, out)
        self.assertEqual(self.snapshot(), before)
        write(self.dst / ".claude/agents/mercury-r.md", self.generated_of("claude"))
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        self.assertFalse((self.dst / ".claude/agents/mercury-r.md").exists())
        self.assertFalse((self.dst / ".codex/agents/mercury-r.toml").exists())
        self.assertTrue((self.dst / ".claude/agents").is_dir())  # the root directory stays

    def generated_of(self, host):
        # the original bytes of the generated role, taken from the lock-consistent installation of a fresh copy
        tool = load_tool()
        role = tool.parse_role(self.ROLE.encode("utf-8"), "test")
        return (tool.role_claude("mercury-r", "core/roles/r.md", role) if host == "claude"
                else tool.role_codex("mercury-r", "core/roles/r.md", role)).decode("utf-8")

    def test_rules_are_installed_verbatim(self):
        raw = b"# rule\r\nprefix_rule(pattern = [\"git\", \"push\"], decision = \"forbidden\")\r\n"
        (self.src / "packs/core/rules").mkdir(parents=True)
        (self.src / "packs/core/rules/mercury-git-safety.rules").write_bytes(raw)
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        self.assertEqual((self.dst / ".codex/rules/mercury-git-safety.rules").read_bytes(), raw)
        for rel in ("rules/other.rules", "rules/mercury-x.txt", "rules/sub/mercury-x.rules", "rules/mercury-.rules"):
            with self.subTest(rel=rel):
                write(self.src / "packs/core" / rel, "x\n")
                self.assertEqual(self.apply()[0], 2)
                (self.src / "packs/core" / rel).unlink()
                self.index()
        self.assertEqual(self.apply()[0], 0)

    # managed section

    def test_section_is_created_when_the_file_is_missing(self):
        self.apply()
        a, c = self.section("AGENTS.md"), self.section("CLAUDE.md")
        self.assertTrue(a.startswith("<!-- MERCURY:BEGIN ") and a.endswith("\n<!-- MERCURY:END -->\n"))
        self.assertIn("Core fragment for AGENTS.", a)
        self.assertIn("@AGENTS.md\n\nCore fragment for CLAUDE.", c)
        self.assertEqual(a.count("MERCURY:"), 2)
        lock = json.loads((self.dst / LOCK).read_text())
        self.assertEqual(set(lock["sections"]), {"AGENTS.md", "CLAUDE.md"})
        tool = load_tool()
        self.assertEqual(lock["sections"]["AGENTS.md"], tool.digest(a.rstrip("\n").encode("utf-8")))
        self.assertEqual(self.check()[0], 0)

    def test_section_is_inserted_above_existing_content_byte_for_byte(self):
        for original in (b"# Mine\nline\n", b"# Mine\r\nline\r\n", b"no newline at all", b"\n\n  odd  \n\xe4\xb8\xad\n"):
            with self.subTest(original=original):
                for name in ("AGENTS.md", "CLAUDE.md", LOCK):
                    (self.dst / name).unlink(missing_ok=True)
                (self.dst / "AGENTS.md").write_bytes(original)
                code, out = self.apply()
                self.assertEqual(code, 0, out)
                data = (self.dst / "AGENTS.md").read_bytes()
                self.assertTrue(data.startswith(b"<!-- MERCURY:BEGIN "))
                self.assertTrue(data.endswith(original), data)
                eol = b"\r\n" if original.split(b"\n")[0].endswith(b"\r") and b"\n" in original else b"\n"
                self.assertTrue(data[:-len(original)].endswith(b"<!-- MERCURY:END -->" + eol + eol))
                if eol == b"\r\n":  # one consistent line ending in the section
                    self.assertEqual(data[:-len(original)].count(b"\n"), data[:-len(original)].count(b"\r\n"))
                self.assertEqual(self.check()[0], 0)

    def test_section_update_leaves_bytes_outside_the_markers_alone(self):
        before_text, after_text = b"# Above (mine)\r\n\r\ntext \xe4\xb8\xad\r\n", b"\r\n# Below\r\nmore\r\n\r\n\r\n"
        self.apply()
        a = (self.dst / "AGENTS.md").read_bytes().replace(b"\r\n", b"\n")
        block = a.rstrip(b"\n")
        (self.dst / "AGENTS.md").write_bytes(before_text + block.replace(b"\n", b"\r\n") + after_text)
        write(self.src / "packs/core/agents.md", "## Core agents\n\nChanged fragment.\n")
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        data = (self.dst / "AGENTS.md").read_bytes()
        self.assertTrue(data.startswith(before_text + b"<!-- MERCURY:BEGIN "))
        self.assertTrue(data.endswith(b"<!-- MERCURY:END -->" + after_text))
        self.assertIn(b"Changed fragment.", data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))  # CRLF kept everywhere
        self.assertEqual(self.check()[0], 0)
        self.assertEqual(self.apply()[1].split(",")[0], "mercury: wrote 0")
        # an LF file stays LF, with the markers in the middle of other text
        (self.dst / "CLAUDE.md").write_bytes(b"top\n" + (self.dst / "CLAUDE.md").read_bytes().rstrip(b"\n") + b"\nbottom")
        write(self.src / "packs/core/claude.md", "@AGENTS.md\n\nNew claude fragment.\n")
        self.assertEqual(self.apply()[0], 0)
        c = (self.dst / "CLAUDE.md").read_bytes()
        self.assertTrue(c.startswith(b"top\n<!-- MERCURY:BEGIN ") and c.endswith(b"<!-- MERCURY:END -->\nbottom"))
        self.assertNotIn(b"\r", c)

    def test_hand_edited_section_stops_the_run_with_zero_writes(self):
        self.apply()
        p = self.dst / "AGENTS.md"
        p.write_bytes(p.read_bytes().replace(b"Core fragment for AGENTS.", b"Core fragment for AGENTS. EDITED"))
        write(self.src / "packs/core/claude.md", "@AGENTS.md\n\nNew claude fragment.\n")  # would otherwise update CLAUDE.md
        write(self.src / "packs/extra/note.txt", "changed\n")  # and a file
        before = self.snapshot()
        for mode in ("check", "apply"):
            code, out = self.run_tool(mode)
            self.assertEqual(code, 1, out)
            self.assertIn("AGENTS.md: the Mercury section was modified by hand", out)
        self.assertEqual(self.snapshot(), before)

    def test_removed_markers_and_foreign_sections_stop_the_run(self):
        self.apply()
        p = self.dst / "AGENTS.md"
        good = p.read_bytes()
        p.write_bytes(b"# only mine\n")  # markers and section gone although the lock has them
        code, out = self.apply()
        self.assertEqual(code, 1, out)
        self.assertIn("markers are gone", out)
        self.assertEqual(p.read_bytes(), b"# only mine\n")
        # a section that is not in the lock and differs is not adopted; an identical one is
        p.write_bytes(good.replace(b"Core fragment", b"Someone else"))
        (self.dst / LOCK).unlink()
        code, out = self.apply()
        self.assertEqual(code, 1, out)
        self.assertIn("not in the lock", out)
        p.write_bytes(good)
        self.assertEqual(self.apply()[0], 0)

    def test_unbalanced_or_duplicate_markers_are_errors(self):
        self.apply()
        good = (self.dst / "AGENTS.md").read_bytes().decode("utf-8")
        begin = good.split("\n")[0]
        cases = {
            "no-end": good.replace("<!-- MERCURY:END -->", ""),
            "no-begin": good.replace(begin, "", 1),
            "twice": good + good,
            "end-first": "<!-- MERCURY:END -->\n" + good.replace("<!-- MERCURY:END -->", ""),
            "begin-twice": begin + "\n" + good,
            "prose-mention": good + "\nthe MERCURY:END marker\n",
            "inline": "text " + good,
            "end-with-text": good.replace("<!-- MERCURY:END -->", "<!-- MERCURY:END --> trailing"),
            "begin-not-comment": good.replace("<!-- MERCURY:BEGIN", "MERCURY:BEGIN", 1),
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                (self.dst / "AGENTS.md").write_bytes(text.encode("utf-8"))
                before = self.snapshot()
                for mode in ("check", "apply"):
                    code, out = self.run_tool(mode)
                    self.assertEqual(code, 2, out)
                    self.assertIn("AGENTS.md", out)
                self.assertEqual(self.snapshot(), before)

    def test_entry_file_problems_are_refused(self):
        (self.dst / "AGENTS.md").write_bytes(b"\xef\xbb\xbf# bom\n")
        self.assertEqual(self.apply()[0], 2)
        (self.dst / "AGENTS.md").write_bytes(b"\xff\xfe bad utf8")
        self.assertEqual(self.apply()[0], 2)
        (self.dst / "AGENTS.md").unlink()
        (self.dst / "AGENTS.md").mkdir()
        self.assertEqual(self.apply()[0], 2)
        self.assertEqual(sorted(p.name for p in self.dst.iterdir() if p.name.endswith("tmp")), [])

    def test_fragments_must_exist_for_core_and_stay_free_of_markers(self):
        (self.src / "packs/core/claude.md").unlink()
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("core must provide claude.md", out)
        write(self.src / "packs/core/claude.md", "@AGENTS.md\n\n<!-- MERCURY:END -->\n")
        self.assertEqual(self.apply()[0], 2)
        write(self.src / "packs/core/claude.md", "  \n\n")
        self.assertEqual(self.apply()[0], 2)

    def test_fragments_follow_core_first_then_config_order(self):
        write(self.src / "packs/extra/agents.md", "## Extra agents\n")
        write(self.src / "packs/zeta/agents.md", "## Zeta agents\n")
        write(self.src / "packs/zeta/note.txt", "z\n")
        self.master["packs"]["zeta"] = {"enabled": True}
        self.packs["zeta"] = {}
        self.save_sources()
        self.select(["zeta", "core", "extra"])
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        a = self.section("AGENTS.md")
        self.assertTrue(a.index("Core fragment for AGENTS.") < a.index("## Zeta agents") < a.index("## Extra agents"))
        self.assertNotIn("Extra", self.section("CLAUDE.md"))  # a pack without claude.md adds nothing there
        self.select(["extra", "zeta"])
        self.assertEqual(self.apply()[0], 0)
        a = self.section("AGENTS.md")
        self.assertTrue(a.index("Core fragment for AGENTS.") < a.index("## Extra agents") < a.index("## Zeta agents"))

    # linked repositories and params

    LINK = {"name": "Notes_KB", "purpose": "Knowledge base.", "locate": "Read the `kb_dir` key of `.handoff-config`.",
            "access": "Read freely; writes need their own authorized scope."}

    def set_config(self, **extra):
        write(self.dst / CONFIG, json.dumps({"schema_version": 1, "packs": ["core", "extra"], **extra}))

    def test_linked_repos_are_rendered_into_the_agents_section_only(self):
        self.set_config(linked_repos=[self.LINK, {**self.LINK, "name": "Design.lib", "purpose": "Design data."}])
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        a = self.section("AGENTS.md")
        self.assertIn("## Linked repositories", a)
        self.assertIn("### Notes_KB\n\n- Purpose: Knowledge base.\n- Locate: Read the `kb_dir` key of `.handoff-config`.\n"
                      "- Access: Read freely; writes need their own authorized scope.\n", a)
        self.assertTrue(a.index("### Notes_KB") < a.index("### Design.lib") < a.index("<!-- MERCURY:END -->"))
        self.assertNotIn("Linked repositories", self.section("CLAUDE.md"))
        self.set_config()  # removing the declaration removes the section
        self.assertEqual(self.apply()[0], 0)
        self.assertNotIn("Linked repositories", self.section("AGENTS.md"))

    def test_linked_repos_validation(self):
        L = self.LINK
        bad = {
            "not-a-list": {"name": "x"},
            "missing-field": {k: v for k, v in L.items() if k != "access"},
            "extra-field": {**L, "path": "x"},
            "unix-absolute": {**L, "locate": "/home/me/kb"},
            "unix-in-text": {**L, "locate": "Look in (/srv/data) first"},
            "windows-drive": {**L, "locate": "D:\\Project\\KB"},
            "windows-drive-slash": {**L, "locate": "key=C:/kb"},
            "bold-drive": {**L, "locate": "**C:/v**"},
            "brace-drive": {**L, "locate": "{C:/vault}"},
            "label-drive": {**L, "locate": "path:C:\\vault"},
            "pipe-drive": {**L, "locate": "x|C:/v"},
            "drive-relative": {**L, "locate": "D:vault"},
            "percent-var": {**L, "locate": "%USERPROFILE%\\vault"},
            "dollar-home": {**L, "locate": "$HOME/vault"},
            "brace-home": {**L, "locate": "${HOME}/vault"},
            "userprofile": {**L, "locate": "$USERPROFILE\\vault"},
            "tilde-slash": {**L, "locate": "see ~/vault"},
            "tilde-backslash": {**L, "locate": "see ~\\vault"},
            "slash-after-punct": {**L, "locate": "x|/etc/kb"},
            "slash-after-space": {**L, "locate": "in /srv/kb"},
            "backslash-after-punct": {**L, "locate": "x=\\share\\kb"},
            "generic-var": {**L, "locate": "$KB_ROOT/vault"},
            "file-url-in-text": {**L, "locate": "open file:///kb now"},
            "hidden-bidi": {**L, "purpose": "x\u202ey"},
            "hidden-bom": {**L, "purpose": "x\ufeffy"},
            "hidden-surrogate": {**L, "purpose": "x\ud800y"},
            "unc": {**L, "locate": "\\\\server\\share"},
            "home": {**L, "locate": "~/kb"},
            "file-url": {**L, "locate": "file:///kb"},
            "newline": {**L, "purpose": "two\nlines"},
            "carriage-return": {**L, "purpose": "two\rlines"},
            "line-separator": {**L, "purpose": "two\u2028lines"},
            "control": {**L, "access": "bell \x07"},
            "nul": {**L, "access": "a\x00b"},
            "comment-open": {**L, "purpose": "x <!-- y"},
            "comment-close": {**L, "purpose": "x --> y"},
            "marker": {**L, "purpose": "MERCURY:END"},
            "empty": {**L, "purpose": ""},
            "edge-space": {**L, "purpose": " x"},
            "not-string": {**L, "purpose": 5},
            "long": {**L, "purpose": "x" * 401},
            "bad-name": {**L, "name": "has space"},
            "name-dup": None,
        }
        before = None
        for name, entry in bad.items():
            with self.subTest(case=name):
                if name == "not-a-list":
                    repos = entry
                elif name == "name-dup":
                    repos = [L, {**L, "name": "NOTES_kb"}]
                else:
                    repos = [entry]
                self.set_config(linked_repos=repos)
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertIn("linked_repos", out)
                self.assertFalse((self.dst / "AGENTS.md").exists())
        self.set_config(linked_repos=[{**L, "locate": "Use the `kb_dir` key; see docs/guides/kb.md and https://example.com/a/b (and/or read/write)."}])
        self.assertEqual(self.apply()[0], 0)

    def test_params_and_unknown_config_keys(self):
        for bad in ({"guard_write_scripts": "x.sh"}, {"guard_write_scripts": ["dir/x.sh"]}, {"guard_write_scripts": ["x"]},
                    {"guard_write_scripts": ["..\\x.sh"]}, {"guard_write_scripts": ["run.cmd"]},
                    {"guard_write_scripts": ["run.bat"]}, {"other": 1}, ["x"]):
            with self.subTest(bad=bad):
                self.set_config(params=bad)
                self.assertEqual(self.apply()[0], 2)
        self.set_config(typo_repos=[])
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertIn("unknown key", out)
        self.set_config(params={"guard_write_scripts": ["deploy.sh", "Push.ps1"]})
        self.assertEqual(self.apply()[0], 0)

    # deletion scope for roles and rules

    def test_deletion_scope_reaches_mercury_roles_and_rules_only(self):
        self.apply()
        lock = self.dst / LOCK
        mine = [".claude/agents/mercury-old.md", ".codex/agents/mercury-old.toml", ".codex/rules/mercury-old.rules"]
        for rel in mine + [".codex/agents/main.toml", ".codex/rules/default.rules", ".claude/agents/own.md"]:
            write(self.dst / rel, "old\n")
        data = json.loads(lock.read_text())
        for rel in mine:
            data["files"][rel] = hashlib.sha256(b"old\n").hexdigest()
        lock.write_text(json.dumps(data))
        code, out = self.apply()
        self.assertEqual(code, 0, out)
        for rel in mine:
            self.assertFalse((self.dst / rel).exists(), rel)
        for rel in (".codex/agents/main.toml", ".codex/rules/default.rules", ".claude/agents/own.md"):
            self.assertTrue((self.dst / rel).exists(), rel)
        self.assertTrue((self.dst / ".codex/agents").is_dir())

    def test_lock_entries_for_foreign_agent_and_rule_files_are_refused(self):
        self.apply()
        victims = (".claude/agents/own.md", ".codex/agents/main.toml", ".codex/rules/default.rules", ".claude/agents/mercury-x.toml",
                   ".codex/agents/mercury-x.md", ".codex/rules/mercury-x.toml", ".claude/agents/mercury-.md", ".codex/agents/mercury-.toml",
                   ".codex/rules/mercury-.rules", ".claude/agents/sub/mercury-x.md", ".codex/agents/Mercury-x.toml", ".claude/agents",
                   ".codex/rules/mercury-x.rules/inner", "AGENTS.md", "CLAUDE.md")
        lock = self.dst / LOCK
        for victim in victims:
            with self.subTest(victim=victim):
                if victim not in ("AGENTS.md", "CLAUDE.md", ".claude/agents"):
                    write(self.dst / victim, "keep\n")
                data = json.loads(lock.read_text())
                data["files"][victim] = "0" * 64
                lock.write_text(json.dumps(data))
                before = {k: v for k, v in self.snapshot().items() if k != LOCK}
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertEqual({k: v for k, v in self.snapshot().items() if k != LOCK}, before)
                del data["files"][victim]
                lock.write_text(json.dumps(data))

    def test_lock_with_a_malformed_sections_entry_is_refused(self):
        self.apply()
        lock = self.dst / LOCK
        for bad in ({"README.md": "0" * 64}, {"AGENTS.md": 5}, []):
            with self.subTest(bad=bad):
                data = json.loads(lock.read_text())
                data["sections"] = bad
                lock.write_text(json.dumps(data))
                before = self.snapshot()
                self.assertEqual(self.apply()[0], 2)
                self.assertEqual(self.snapshot(), before)
                del data["sections"]
                lock.write_text(json.dumps(data))

    def test_temporary_file_names_are_never_followed_or_overwritten(self):
        self.apply()
        write(self.src / "packs/core/agents.md", "## Core agents\n\nChanged.\n")
        for kind in ("file", "link"):
            with self.subTest(kind=kind):
                tmp = self.dst / "AGENTS.md.mercury-tmp"
                victim = self.dst / "victim.txt"
                write(victim, "keep\n")
                if kind == "file":
                    write(tmp, "planted\n")
                else:
                    try:
                        os.symlink(victim, tmp)
                    except (OSError, NotImplementedError):
                        continue  # no symlink privilege here (Windows); the file case above still ran
                before = self.snapshot()
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertIn("temporary name", out)
                self.assertEqual(victim.read_bytes(), b"keep\n")
                self.assertEqual(self.snapshot(), before)
                tmp.unlink()
        self.assertEqual(self.apply()[0], 0)
        self.assertFalse((self.dst / "AGENTS.md.mercury-tmp").exists())

    def test_exclusive_temporary_file_and_cleanup(self):
        tool = load_tool()
        target = self.dst
        write(target / "AGENTS.md", "old\n")
        write(target / "AGENTS.md.mercury-tmp", "planted\n")  # the writer itself refuses too, not only the plan
        with self.assertRaises(tool.BuildError):
            tool.atomic_write(target, "AGENTS.md", b"new\n")
        self.assertEqual((target / "AGENTS.md.mercury-tmp").read_bytes(), b"planted\n")
        (target / "AGENTS.md.mercury-tmp").unlink()
        tool.atomic_write(target, "AGENTS.md", b"new\n")
        self.assertEqual((target / "AGENTS.md").read_bytes(), b"new\n")
        self.assertFalse((target / "AGENTS.md.mercury-tmp").exists())
        real_replace = os.replace
        os.replace = lambda *a: (_ for _ in ()).throw(OSError("boom"))
        try:
            with self.assertRaises(OSError):
                tool.atomic_write(target, "AGENTS.md", b"newer\n")
        finally:
            os.replace = real_replace
        self.assertFalse((target / "AGENTS.md.mercury-tmp").exists())  # removed in finally
        self.assertEqual((target / "AGENTS.md").read_bytes(), b"new\n")

    def test_hidden_characters_in_fragments_are_refused(self):
        for bad in ("bidi \u202e text", "bom \ufeff text", "nul \x00 text"):
            with self.subTest(bad=bad):
                write(self.src / "packs/core/agents.md", f"## Core\n\n{bad}\n")
                code, out = self.apply()
                self.assertEqual(code, 2, out)
                self.assertFalse((self.dst / "AGENTS.md").exists())

    def test_consecutive_bullet_fragments_join_without_a_blank_line(self):
        write(self.src / "packs/core/claude.md", "@AGENTS.md\n\n- first bullet\n")
        write(self.src / "packs/extra/claude.md", "- second bullet\n")
        write(self.src / "packs/extra/agents.md", "## Extra\n")
        self.assertEqual(self.apply()[0], 0)
        self.assertIn("- first bullet\n- second bullet\n", self.section("CLAUDE.md"))
        write(self.src / "packs/extra/claude.md", "Paragraph after a list.\n")
        self.assertEqual(self.apply()[0], 0)
        self.assertIn("- first bullet\n\nParagraph after a list.\n", self.section("CLAUDE.md"))

    def test_linked_repos_that_are_fine_stay_accepted(self):
        L = self.LINK
        for locate in ("Use the `kb_dir` key; see docs/guides/kb.md and https://example.com/a/b (and/or read/write).",
                       "Key `kb_dir` in .handoff-config; see ./docs/x.md, ../y.md and my-dir/sub_dir/z.md.",
                       "Step A: read it; 50% done; about ~5 minutes; ask in the KB channel."):
            with self.subTest(locate=locate):
                self.set_config(linked_repos=[{**L, "locate": locate}])
                code, out = self.apply()
                self.assertEqual(code, 0, out)

    def test_repository_installation_is_in_sync(self):
        r = subprocess.run([sys.executable, "-B", str(TOOL), "check", "--target", str(ROOT)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
