"""The `git-safe` pack (#723): installing it writes the controlled Git entry and its managed-section paragraph, and the
installed copy works from another checkout.

The pack under test is the real packs/git-safe, installed from a throw-away Mercury repository (a minimal core plus a
copy of the pack), so what is checked is exactly what a downstream project receives. No network."""
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
SCRIPTS = ".mercury/packs/git-safe/scripts"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8"))


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr
    return r.stdout


class GitSafeInstall(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.src, self.dst = Path(tmp.name) / "src", Path(tmp.name) / "dst"
        for repo in (self.src, self.dst):
            repo.mkdir(parents=True)
            git(repo, "init", "-q")
            git(repo, "config", "core.autocrlf", "false")
        git(self.src, "remote", "add", "origin", "https://github.com/test/mercury.git")
        write(self.src / "mercury.json", json.dumps({"schema_version": 1, "packs": {
            "core": {"enabled": True, "required": True}, "git-safe": {"enabled": True}}}))
        write(self.src / "packs/core/pack.json", json.dumps({"schema_version": 1, "name": "core", "skills": {}}))
        write(self.src / "packs/core/agents.md", "## Core\n\nCore text.\n")
        write(self.src / "packs/core/claude.md", "@AGENTS.md\n")
        shutil.copytree(ROOT / "packs" / "git-safe", self.src / "packs" / "git-safe")
        git(self.src, "add", "-A")
        git(self.src, "commit", "-q", "-m", "one")
        git(self.src, "update-ref", "refs/remotes/origin/master", "HEAD")
        write(self.dst / CONFIG, json.dumps({"schema_version": 1, "packs": ["core", "git-safe"],
                                             "params": {"integration_branch": "main"}}, indent=2) + "\n")

    def tool(self, mode="apply"):
        r = subprocess.run([sys.executable, "-B", str(TOOL), mode, "--source", str(self.src), "--target", str(self.dst)],
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode, r.stdout + r.stderr

    def test_install_writes_the_scripts_the_managed_text_and_the_lock(self):
        code, out = self.tool()
        self.assertEqual(code, 0, out)
        files = json.loads((self.dst / LOCK).read_text(encoding="utf-8"))["files"]
        for name in ("git-safe.ps1", "guard.ps1"):
            installed = self.dst / SCRIPTS / name
            self.assertEqual(installed.read_bytes(), (ROOT / "packs/git-safe/scripts" / name).read_bytes())
            self.assertIn(f"{SCRIPTS}/{name}", files)
        agents = (self.dst / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("## Controlled Git entry", agents)
        self.assertIn(".mercury/packs/git-safe/scripts/git-safe.ps1", agents)
        self.assertEqual(self.tool("check")[0], 0)

    @unittest.skipUnless(PWSH, "PowerShell is not available")
    def test_the_installed_copy_commits_in_its_own_project_from_another_checkout(self):
        self.assertEqual(self.tool()[0], 0)
        git(self.dst, "add", "-A")
        git(self.dst, "commit", "-q", "-m", "install")
        git(self.dst, "checkout", "-q", "-b", "docs/note-claude")
        write(self.dst / "note.md", "note\n")
        caller = self.src  # any other checkout; here the throw-away Mercury repository
        scripts = self.dst / SCRIPTS

        def run(script, *args):
            r = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(scripts / script), *args],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=caller, timeout=120)
            return r.returncode, r.stdout + r.stderr

        self.assertEqual(run("git-safe.ps1", "add", "note.md")[0], 0)
        self.assertEqual(run("guard.ps1", "mark-review")[0], 0)
        code, out = run("git-safe.ps1", "commit", "-Message", "docs(TASK-9): note")
        self.assertEqual(code, 0, out)
        self.assertEqual(git(self.dst, "log", "-1", "--format=%s").strip(), "docs(TASK-9): note")
        self.assertEqual(git(caller, "log", "-1", "--format=%s").strip(), "one")


if __name__ == "__main__":
    unittest.main()
