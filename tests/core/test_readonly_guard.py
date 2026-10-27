"""Tests for packs/core/hooks/readonly_guard.py (ported from the user-level guard of Mercury #628; Mercury #682).

Run: python -B -m unittest discover -s tests/core -p 'test_*.py'
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "packs" / "core" / "hooks"
sys.path.insert(0, str(HOOKS))
sys.dont_write_bytecode = True
from readonly_guard import Blocked, check_command  # noqa: E402

ALLOWED = [
    "git status -sb",
    "git -C D:/repo log --oneline -5",
    "git diff HEAD~1 -- a.py 2>/dev/null",
    "git branch -a",
    "git branch --show-current",
    "git worktree list --porcelain",
    "git config --get user.name",
    "git fetch origin",
    "git show origin/develop:AGENTS.md | head -20",
    "gh pr view 12 --json state",
    "gh pr diff 12",
    "gh api repos/o/r/pulls/1/comments",
    "gh api -X GET repos/o/r",
    "gh api graphql -f query='query { repository(owner:\"o\", name:\"r\") { id } }'",
    "grep -rn 'git commit' scripts/",
    "rg \"Remove-Item\" -n",
    "python -m pytest -q tests 2>&1 | tail -5",
    "Get-ChildItem -Recurse | Select-Object -First 3",
    "powershell -NoProfile -Command \"Get-Content a.txt\"",
    "echo done > /dev/null",
    "Get-Content x > $null",
    "cat a.txt 2>&1",
    "FOO=1 git log",
    "grep -n sync-downstream-pr.py README.md",
    "cat scripts/codex/git-safe.ps1",
    "rg git-safe.ps1 docs",
    "pwsh -File scripts/other-tool.ps1 -Action List",
]

BLOCKED = [
    "git commit -m x",
    "git -C D:/repo add file",
    "git push origin HEAD",
    "git worktree add ../probe -b probe",
    "git branch probe",
    "git branch -D probe",
    "git tag v1",
    "git config user.name x",
    "git reset --hard",
    "git stash",
    "cd repo && git checkout -b x",
    "gh pr create --fill",
    "gh pr merge 3 --squash",
    "gh issue comment 5 --body hi",
    "gh api -X POST repos/o/r/issues",
    "gh api repos/o/r/issues -f title=x",
    "gh api graphql -f query='mutation { resolveReviewThread(input:{threadId:\"x\"}) { clientMutationId } }'",
    "rm -rf build",
    "mkdir probe",
    "echo hi > notes.txt",
    "echo hi >> notes.txt",
    "sed -i 's/a/b/' f",
    "Remove-Item -LiteralPath x",
    "New-Item -ItemType Directory x",
    "Set-Content a.txt hi",
    "'x' | Out-File a.txt",
    "powershell -File scripts/codex/git-safe.ps1 commit -m x",
    "powershell -File scripts/codex/guard.ps1 mark-review",
    "python scripts/codex/sync-project-template.py apply --target x",
    "bash -c 'git commit -m x'",
    "powershell -Command \"Remove-Item x\"",
    "ls; git add .",
    "git status && touch x",
    "echo 'unbalanced",
    "python -B mercury/scripts/codex/sync-downstream-pr.py --target t",
    "& scripts/codex/git-safe.ps1 push",
    "bash scripts/lane-spawn.sh x",
    "sh .mercury/packs/lanes/scripts/lane-close.sh",
    ".mercury/packs/lanes/scripts/lane-claim.sh --lane a",
    "bash .mercury/packs/lanes/scripts/lane-init.sh",
]

SCRIPT = HOOKS / "readonly_guard.py"
LAUNCHER = HOOKS / "readonly_guard.sh"


def run_hook(payload, project=None, launcher=False):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project)
    cmd = ["sh", str(LAUNCHER)] if launcher else [sys.executable, "-B", str(SCRIPT)]
    return subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, env=env)


class GuardCommands(unittest.TestCase):
    def test_allowed(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                check_command(command)

    def test_blocked(self):
        for command in BLOCKED:
            with self.subTest(command=command):
                with self.assertRaises(Blocked):
                    check_command(command)

    def test_no_user_machine_or_sot_names_are_left_in_the_guard(self):
        text = SCRIPT.read_text(encoding="utf-8").lower()
        for needle in ("sot-", "c:/users", "c:\\users", "python314", "ship of theseus"):
            self.assertNotIn(needle, text)

    def test_project_extension_names_are_blocked_only_when_executed(self):
        extra = frozenset({"deploy.sh", "publish.ps1"})
        for command in ("bash tools/deploy.sh prod", "pwsh -File x/Publish.ps1", "./deploy.sh", "& tools/publish.ps1 -DryRun"):
            with self.subTest(command=command):
                with self.assertRaises(Blocked):
                    check_command(command, extra=extra)
                check_command(command)  # without the extension the same command is allowed
        check_command("cat tools/deploy.sh", extra=extra)
        check_command("grep -n publish.ps1 README.md", extra=extra)


class GuardHook(unittest.TestCase):
    def test_hook_blocks_with_exit_2(self):
        result = run_hook({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}})
        self.assertEqual(result.returncode, 2)
        self.assertIn("read-only agent guard", result.stderr)

    def test_hook_allows_reads_and_other_tools(self):
        self.assertEqual(run_hook({"tool_name": "PowerShell", "tool_input": {"command": "git status"}}).returncode, 0)
        self.assertEqual(run_hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}).returncode, 0)
        self.assertEqual(run_hook({"tool_name": "Bash", "tool_input": {}}).returncode, 0)

    def test_hook_reads_project_extension_from_the_project_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".mercury").mkdir()
            payload = {"tool_name": "Bash", "tool_input": {"command": "bash tools/deploy.sh"}, "cwd": str(project)}
            self.assertEqual(run_hook(payload, project).returncode, 0)  # no config: not blocked
            cfg = project / ".mercury" / "config.json"
            cfg.write_text(json.dumps({"schema_version": 1, "packs": ["core"], "params": {"guard_write_scripts": ["Deploy.sh"]}}))
            self.assertEqual(run_hook(payload, project).returncode, 2)
            # without CLAUDE_PROJECT_DIR the payload's cwd is used
            self.assertEqual(run_hook(payload).returncode, 2)
            # built-in names still apply and a malformed list is ignored, not fatal
            for bad in ({"guard_write_scripts": "deploy.sh"}, {"guard_write_scripts": ["a/b.sh"]}, {"guard_write_scripts": [1]}, 5):
                cfg.write_text(json.dumps({"params": bad}))
                self.assertEqual(run_hook(payload, project).returncode, 0)
                git = {"tool_name": "Bash", "tool_input": {"command": "git push"}, "cwd": str(project)}
                self.assertEqual(run_hook(git, project).returncode, 2)
            cfg.write_text("not json")
            self.assertEqual(run_hook(payload, project).returncode, 0)


@unittest.skipUnless(shutil.which("sh"), "needs sh")
class GuardLauncher(unittest.TestCase):
    def test_launcher_runs_the_guard_and_passes_stdin(self):
        blocked = run_hook({"tool_name": "Bash", "tool_input": {"command": "git push"}}, launcher=True)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("read-only agent guard", blocked.stderr)
        self.assertEqual(run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}}, launcher=True).returncode, 0)

    def test_launcher_blocks_when_no_python_is_found(self):
        with tempfile.TemporaryDirectory() as tmp:
            # an empty PATH apart from the shell itself: nothing named python3, python or py can run
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            env = {"PATH": str(bindir)}
            sh = shutil.which("sh")
            result = subprocess.run([sh, str(LAUNCHER)], input="{}", capture_output=True, text=True, env=env)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("no working Python", result.stderr)

    def test_hook_command_of_the_generated_roles_matches_the_launcher_path(self):
        root = HOOKS.parents[2]
        sys.path.insert(0, str(root / "tools"))
        try:
            import mercury
        finally:
            sys.path.pop(0)
        self.assertEqual(mercury.GUARD_REL, ".mercury/packs/core/hooks/readonly_guard.sh")
        self.assertTrue((root / "packs" / "core" / "hooks" / "readonly_guard.sh").is_file())


if __name__ == "__main__":
    unittest.main()
