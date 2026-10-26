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
        r = subprocess.run([sys.executable, "-B", str(TOOL), mode, "--source", str(self.src), "--target", str(self.dst)],
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
        self.assertEqual(set(lock["files"]), set(s) - {LOCK, CONFIG})
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

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_in_target_is_refused(self):
        import _winapi
        outside = self.dst.parent / "outside"
        outside.mkdir()
        (self.dst / ".agents").mkdir()
        _winapi.CreateJunction(str(outside), str(self.dst / ".agents" / "skills"))
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.check()[0], 2)

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_for_packs_directory_is_refused(self):
        import _winapi
        outside = self.dst.parent / "outside"
        outside.mkdir()
        _winapi.CreateJunction(str(outside), str(self.dst / ".mercury" / "packs"))
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertEqual(list(outside.iterdir()), [])

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_for_lock_directory_is_refused(self):
        import _winapi
        outside = self.dst.parent / "outside"
        write(outside / "config.json", json.dumps({"schema_version": 1, "packs": ["core"]}))
        (self.dst / CONFIG).unlink()
        (self.dst / ".mercury").rmdir()
        _winapi.CreateJunction(str(outside), str(self.dst / ".mercury"))
        code, out = self.apply()
        self.assertEqual(code, 2, out)
        self.assertEqual([p.name for p in outside.iterdir()], ["config.json"])

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

    def test_repository_installation_is_in_sync(self):
        r = subprocess.run([sys.executable, "-B", str(TOOL), "check", "--target", str(ROOT)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
