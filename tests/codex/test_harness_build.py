"""Harness builder (#674): projection, idempotence, hand-edit protection, cleanup, path safety."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[2] / "scripts" / "harness" / "build.py"
ENTRY = "---\nname: alpha\ndescription: Alpha skill.\n---\n\nhand-written entry\n"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


class HarnessBuildTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src, self.dst = Path(tmp.name) / "src", Path(tmp.name) / "dst"
        self.dst.mkdir()
        h = self.src / "harness"
        write(h / "core/skills/alpha/SKILL.md", "---\nname: alpha\ndescription: Alpha skill.\n---\n\nbody\n")
        write(h / "core/skills/alpha/claude-entry.md", ENTRY)
        write(h / "core/skills/alpha/ref/a.md", "ref\r\n")
        write(h / "modules/extra/skills/beta/SKILL.md", "---\nname: beta\ndescription: Beta skill.\n---\n\nbeta\n")
        write(h / "modules/extra/files/note.txt", "note\n")
        self.manifest = {"schema_version": 2, "modules": {
            "core": {"path": "core", "required": True, "skills": {"alpha": {"hosts": ["codex", "claude"]}}},
            "extra": {"path": "modules/extra", "skills": {"beta": {"hosts": ["codex", "claude"]}},
                      "files": [{"source": "files/note.txt", "target": "NOTE.txt"}]}}}
        self.save_manifest()
        self.select(["core", "extra"])

    def save_manifest(self):
        write(self.src / "harness/manifest.json", json.dumps(self.manifest))

    def select(self, mods):
        write(self.dst / "mercury.project.json", json.dumps({"schema_version": 1, "modules": mods}))

    def build(self, mode):
        r = subprocess.run([sys.executable, "-B", str(BUILD), "--source", str(self.src), "--target", str(self.dst), mode],
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    def snapshot(self):
        return {p.relative_to(self.dst).as_posix(): p.read_bytes() for p in sorted(self.dst.rglob("*")) if p.is_file()}

    def test_first_projection(self):
        code, out = self.build("--apply")
        self.assertEqual(code, 0, out)
        s = self.snapshot()
        self.assertEqual(s[".agents/skills/alpha/SKILL.md"], (self.src / "harness/core/skills/alpha/SKILL.md").read_bytes())
        self.assertEqual(s[".agents/skills/alpha/ref/a.md"], b"ref\r\n")  # line endings untouched
        self.assertNotIn(".agents/skills/alpha/claude-entry.md", s)
        self.assertEqual(s["NOTE.txt"], b"note\n")
        lock = json.loads(s[".mercury/harness.lock"])
        self.assertEqual(lock["schema_version"], 1)
        self.assertEqual(set(lock["files"]), set(s) - {".mercury/harness.lock", "mercury.project.json"})
        self.assertEqual(self.build("--check")[0], 0)

    def test_claude_entry_verbatim_and_generated(self):
        self.build("--apply")
        s = self.snapshot()
        self.assertEqual(s[".claude/skills/alpha/SKILL.md"], ENTRY.encode())
        gen = s[".claude/skills/beta/SKILL.md"].decode()
        self.assertIn("name: beta\ndescription: Beta skill.\n", gen)
        self.assertIn(".agents/skills/beta/SKILL.md", gen)

    def test_repeat_is_noop(self):
        self.build("--apply")
        before = self.snapshot()
        mtimes = {p: p.stat().st_mtime_ns for p in self.dst.rglob("*") if p.is_file()}
        code, out = self.build("--apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual({p: p.stat().st_mtime_ns for p in self.dst.rglob("*") if p.is_file()}, mtimes)

    def test_hand_edit_stops_apply(self):
        self.build("--apply")
        (self.dst / ".agents/skills/alpha/SKILL.md").write_bytes(b"edited by hand\n")
        write(self.src / "harness/core/skills/alpha/SKILL.md", "---\nname: alpha\ndescription: Alpha skill.\n---\n\nnew\n")
        write(self.src / "harness/modules/extra/files/note.txt", "note v2\n")  # would also change an untouched file
        before = self.snapshot()
        code, out = self.build("--apply")
        self.assertEqual(code, 1)
        self.assertIn("modified by hand", out)
        self.assertEqual(self.snapshot(), before)  # nothing written, not even the unrelated update

    def test_check_reports_difference(self):
        self.assertEqual(self.build("--check")[0], 1)  # nothing projected yet
        self.assertFalse((self.dst / ".mercury").exists())
        self.build("--apply")
        (self.dst / "NOTE.txt").write_bytes(b"x\n")
        code, out = self.build("--check")
        self.assertEqual(code, 1)
        self.assertIn("NOTE.txt", out)
        self.assertEqual((self.dst / "NOTE.txt").read_bytes(), b"x\n")  # --check never writes

    def test_unmanaged_file_adopted_only_when_identical(self):
        write(self.dst / "NOTE.txt", "note\n")  # identical to the projection: adopted
        self.assertEqual(self.build("--apply")[0], 0)
        self.assertIn("NOTE.txt", json.loads((self.dst / ".mercury/harness.lock").read_text())["files"])
        self.select(["core"])
        self.build("--apply")
        write(self.dst / ".agents/skills/beta/SKILL.md", "mine\n")  # unmanaged and different
        self.select(["core", "extra"])
        code, out = self.build("--apply")
        self.assertEqual(code, 1, out)
        self.assertEqual((self.dst / ".agents/skills/beta/SKILL.md").read_bytes(), b"mine\n")

    def test_deselected_module_cleanup(self):
        self.build("--apply")
        self.select(["core"])
        code, out = self.build("--apply")
        self.assertEqual(code, 0, out)
        s = self.snapshot()
        self.assertNotIn("NOTE.txt", s)
        self.assertFalse(any("beta" in k for k in s))
        self.assertFalse((self.dst / ".agents/skills/beta").exists())  # emptied directory removed
        self.assertIn(".agents/skills/alpha/SKILL.md", s)
        self.assertNotIn("NOTE.txt", json.loads(s[".mercury/harness.lock"])["files"])

    def test_deselected_module_keeps_hand_edited_file(self):
        self.build("--apply")
        (self.dst / "NOTE.txt").write_bytes(b"mine\n")
        self.select(["core"])
        code, out = self.build("--apply")
        self.assertEqual(code, 1)
        self.assertIn("NOTE.txt", out)
        self.assertEqual((self.dst / "NOTE.txt").read_bytes(), b"mine\n")
        self.assertTrue((self.dst / ".agents/skills/beta/SKILL.md").exists())  # nothing partially applied

    def test_rejects_unsafe_targets(self):
        for bad in ("../escape.txt", ".git/config", "harness/x.txt", "/abs.txt", "a/../../b.txt", "sub/.git/hooks"):
            with self.subTest(target=bad):
                self.manifest["modules"]["extra"]["files"] = [{"source": "files/note.txt", "target": bad}]
                self.save_manifest()
                code, out = self.build("--apply")
                self.assertEqual(code, 2, out)
                self.assertFalse((self.dst.parent / "escape.txt").exists())
                self.assertFalse((self.dst / ".git").exists())

    def test_tampered_lock_cannot_delete_outside(self):
        self.build("--apply")
        victim = self.dst.parent / "keep.txt"
        victim.write_bytes(b"keep\n")
        lock = self.dst / ".mercury/harness.lock"
        data = json.loads(lock.read_text())
        data["files"]["../keep.txt"] = "0" * 64
        lock.write_text(json.dumps(data))
        code, _ = self.build("--apply")
        self.assertEqual(code, 2)
        self.assertTrue(victim.exists())

    def set_extra_files(self, target):
        self.manifest["modules"]["extra"]["files"] = [{"source": "files/note.txt", "target": target}]
        self.save_manifest()

    def test_windows_path_bypasses_rejected_in_manifest(self):
        for bad in (".git./config", ".git /config", "harness./x", "HARNESS/x", "GIT~1/config", ".mercury/harness.lock.",
                    ".MERCURY/harness.lock", ".mercury/harness.lock ", "mercury.project.json", "Mercury.Project.JSON.",
                    "CON", "aux.txt", "x/NUL.txt", "a:b", "x/...", "a\\b", "c:/x.txt", "ctl\x01.txt"):
            with self.subTest(target=bad):
                self.set_extra_files(bad)
                code, out = self.build("--apply")
                self.assertEqual(code, 2, out)
                self.assertFalse((self.dst / ".mercury").exists())  # nothing was written

    def test_windows_path_bypasses_rejected_in_lock(self):
        self.build("--apply")
        git = self.dst / ".git"
        write(git / "config", "[core]\n")
        lock = self.dst / ".mercury/harness.lock"
        for bad in (".git./config", ".git /config", "GIT~1/config", ".MERCURY/harness.lock", "harness./x"):
            with self.subTest(entry=bad):
                data = json.loads(lock.read_text())
                data["files"][bad] = "0" * 64
                lock.write_text(json.dumps(data))
                code, _ = self.build("--apply")
                self.assertEqual(code, 2)
                self.assertEqual((git / "config").read_bytes(), b"[core]\n")

    def test_lock_entries_outside_projection_namespace_are_refused(self):
        self.build("--apply")
        write(self.dst / "README.md", "readme\n")
        write(self.dst / ".mercury/state/s.json", "{}\n")
        lock = self.dst / ".mercury/harness.lock"
        for victim in ("README.md", "mercury.project.json", ".mercury/state/s.json"):
            with self.subTest(victim=victim):
                data = json.loads(lock.read_text())
                digest = __import__("hashlib").sha256((self.dst / victim).read_bytes()).hexdigest()
                data["files"][victim] = digest
                lock.write_text(json.dumps(data))
                code, out = self.build("--apply")
                self.assertEqual(code, 2, out)
                self.assertTrue((self.dst / victim).exists())
                self.assertTrue((self.dst / ".mercury/state").exists() or victim != ".mercury/state/s.json")
                del data["files"][victim]
                lock.write_text(json.dumps(data))

    def test_prune_stops_at_skill_root(self):
        self.manifest["modules"]["core"]["skills"]["alpha"]["hosts"] = ["codex"]
        self.save_manifest()
        self.build("--apply")
        self.assertTrue((self.dst / ".claude/skills/beta/SKILL.md").exists())
        self.select(["core"])
        self.assertEqual(self.build("--apply")[0], 0)
        self.assertFalse((self.dst / ".claude/skills/beta").exists())
        self.assertTrue((self.dst / ".claude/skills").is_dir())  # the projection root itself stays
        self.assertTrue((self.dst / ".claude").is_dir())

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_in_target_is_refused(self):
        import _winapi
        outside = self.dst.parent / "outside"
        outside.mkdir()
        (self.dst / ".agents").mkdir()
        _winapi.CreateJunction(str(outside), str(self.dst / ".agents" / "skills"))
        code, out = self.build("--apply")
        self.assertEqual(code, 2, out)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.build("--check")[0], 2)

    @unittest.skipUnless(sys.platform == "win32", "directory junctions exist on Windows only")
    def test_junction_for_lock_directory_is_refused(self):
        import _winapi
        outside = self.dst.parent / "outside"
        outside.mkdir()
        _winapi.CreateJunction(str(outside), str(self.dst / ".mercury"))
        code, out = self.build("--apply")
        self.assertEqual(code, 2, out)
        self.assertEqual(list(outside.iterdir()), [])

    def test_bad_inputs_exit_2_without_partial_writes(self):
        (self.src / "harness/modules/extra/skills/beta/SKILL.md").write_bytes(b"---\nname: beta\ndescription: \xff\xfe\n---\n")
        code, out = self.build("--apply")
        self.assertEqual(code, 2, out)
        self.assertFalse((self.dst / ".agents").exists())

    def test_malformed_manifest_entries_exit_2(self):
        for key, bad in (("skills", {"beta": "x"}), ("files", ["note.txt"]), ("files", {"a": 1}), ("skills", [])):
            with self.subTest(key=key):
                self.manifest["modules"]["extra"][key] = bad
                self.save_manifest()
                self.assertEqual(self.build("--apply")[0], 2)
        self.manifest["modules"]["extra"] = "oops"
        self.save_manifest()
        self.assertEqual(self.build("--apply")[0], 2)

    def test_parent_path_that_is_a_file_is_a_preflight_error(self):
        write(self.dst / ".agents", "i am a file\n")
        code, out = self.build("--apply")
        self.assertEqual(code, 2, out)
        self.assertFalse((self.dst / ".claude").exists())  # nothing written before the failure
        self.assertFalse((self.dst / "NOTE.txt").exists())

    def test_manifest_paths_must_be_plain_relative(self):
        for field, bad in (("path", "C:/x"), ("path", "/abs"), ("path", "../x"), ("path", "a\\b")):
            with self.subTest(field=field, bad=bad):
                self.setUp()
                self.manifest["modules"]["extra"][field] = bad
                self.save_manifest()
                self.assertEqual(self.build("--apply")[0], 2)
        for bad in ("C:/x.txt", "/abs.txt", "../note.txt", "files\\note.txt"):
            with self.subTest(source=bad):
                self.manifest["modules"]["extra"]["path"] = "modules/extra"
                self.manifest["modules"]["extra"]["files"] = [{"source": bad, "target": "NOTE.txt"}]
                self.save_manifest()
                self.assertEqual(self.build("--apply")[0], 2)

    def test_generated_entry_requires_codex_host_and_single_line_frontmatter(self):
        self.manifest["modules"]["extra"]["skills"]["beta"]["hosts"] = ["claude"]
        self.save_manifest()
        code, out = self.build("--apply")
        self.assertEqual(code, 2, out)
        self.assertIn("codex", out)
        self.manifest["modules"]["extra"]["skills"]["beta"]["hosts"] = ["codex", "claude"]
        self.save_manifest()
        for desc in ("description: >\n  folded text\n", "description:\n  next line\n", "description: first\n  second\n"):
            with self.subTest(desc=desc):
                write(self.src / "harness/modules/extra/skills/beta/SKILL.md", "---\nname: beta\n" + desc + "---\n\nbeta\n")
                code, out = self.build("--apply")
                self.assertEqual(code, 2, out)
                self.assertIn("single-line", out)

    def test_unknown_module_is_error(self):
        self.select(["core", "nope"])
        code, out = self.build("--apply")
        self.assertEqual(code, 2)
        self.assertIn("nope", out)


if __name__ == "__main__":
    unittest.main()
