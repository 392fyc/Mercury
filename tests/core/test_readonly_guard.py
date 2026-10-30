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
    "rg image_api.py docs",
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
    "python -B .mercury/packs/core/skills/image-api/scripts/image_api.py --config private.json --card card.json --execute",
    "py -3.12 packs/core/skills/image-api/scripts/image_api.py --config private.json --card card.json --execute",
    "uv run python .mercury/packs/core/skills/image-api/scripts/image_api.py --config private.json --card card.json --execute",
    "uv run --no-project --python 3.14 python .mercury/packs/core/skills/image-api/scripts/image_api.py --execute",
    "python -B -X utf8 .mercury/packs/core/skills/image-api/scripts/image_api.py --execute",
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


class GuardActions(unittest.TestCase):
    """#692: params.guard_write_actions blocks a script only for listed -Action values, and whenever the action is unclear."""
    ACTIONS = {"sot-worktree.ps1": frozenset({"create", "remove"})}

    def test_read_only_actions_pass_in_every_spelling(self):
        for command in ("pwsh -File scripts/sot-worktree.ps1 -Action List",
                        "powershell -NoProfile -ExecutionPolicy Bypass -File scripts\\sot-worktree.ps1 -Action Verify",
                        "scripts/sot-worktree.ps1 -action:list", "& ./sot-worktree.ps1 -ACTION list",
                        "./sot-worktree.ps1 -Act List -Name x", "pwsh -Command \"& ./sot-worktree.ps1 -Action List\"",
                        "cat scripts/sot-worktree.ps1", "rg -n Create scripts/sot-worktree.ps1"):
            with self.subTest(command=command):
                check_command(command, actions=self.ACTIONS)

    def test_listed_missing_or_unreadable_actions_are_blocked(self):
        for command in ("pwsh -File scripts/sot-worktree.ps1 -Action Create", "sot-worktree.ps1 -Action:REMOVE",
                        "sot-worktree.ps1 -A remove", "powershell -File sot-worktree.ps1 -Action \"Create\"",
                        "pwsh -Command \"& ./sot-worktree.ps1 -Action Remove\"", "bash -c 'pwsh -File sot-worktree.ps1 -Action Create'",
                        "sot-worktree.ps1", "sot-worktree.ps1 Create", "sot-worktree.ps1 @args", "sot-worktree.ps1 -Action",
                        "sot-worktree.ps1 -Action:", "sot-worktree.ps1 -Action $a", "sot-worktree.ps1 -Action (Get-X)",
                        "sot-worktree.ps1 -Action List,Create", "sot-worktree.ps1 -Action List -Action Create",
                        "sot-worktree.ps1 --% -Action List", "sot-worktree.ps1 --Action List",
                        "sot-worktree.ps1 –Action Create", "sot-worktree.ps1 —action:remove",
                        "sot-worktree.ps1 –Action List -Action Create", "sot-worktree.ps1 -Action \"Create\nList\""):
            with self.subTest(command=command):
                with self.assertRaises(Blocked):
                    check_command(command, actions=self.ACTIONS)

    def test_the_reviewed_bypasses_are_blocked(self):
        # B-F1..F3 of the #692 review: in each, pwsh bound Action=Create while the old tokenizer read -Action List.
        reproduced = {
            "comment decoy and splat": "$p=@{Action='Create';Task='x'}; ./sot-worktree.ps1 @p # -Action List",
            "curly quotes": "./sot-worktree.ps1 @p -Task \u201cx -Action List y\u201d",
            "curly quotes without a splat": "./sot-worktree.ps1 -Task \u201cx -Action List y\u201d -Action Create",
            "backtick-escaped quote": './sot-worktree.ps1 @p -Task "x`" -Action List `"y"',
            "block comment": "./sot-worktree.ps1 <# -Action List #> -Action Create",
        }
        for case, command in reproduced.items():
            with self.subTest(case=case):
                with self.assertRaises(Blocked) as ctx:
                    check_command(command, actions=self.ACTIONS)
                self.assertIn("in its arguments", str(ctx.exception))  # refused by the allowlist, not by luck

    def test_block_comment_is_refused_on_purpose_not_as_a_redirect(self):
        with self.assertRaises(Blocked) as ctx:
            check_command("./sot-worktree.ps1 <# -Action List #>", actions=self.ACTIONS)
        self.assertIn("'#<>'", str(ctx.exception))
        self.assertNotIn("redirection", str(ctx.exception))

    def test_other_ways_to_hide_a_value_are_blocked(self):
        for command in ("./sot-worktree.ps1 -Action List -Task x$(Write-Output y)", "./sot-worktree.ps1 -Action List -T {x}",
                        "./sot-worktree.ps1 -Action List -T (1)", "./sot-worktree.ps1 -Action List -T caf\u00e9",
                        "./sot-worktree.ps1 -Task x -- -Action List", "./sot-worktree.ps1 Create '-Action' List",
                        './sot-worktree.ps1 Create "-Action" List', "./sot-worktree.ps1 -Action List -T x\\\" ; x\"",
                        "./sot-worktree.ps1 -Action List \u2018x\u2019"):
            with self.subTest(command=command):
                with self.assertRaises(Blocked):
                    check_command(command, actions=self.ACTIONS)

    def test_plain_read_only_calls_stay_allowed(self):
        for command in ("./sot-worktree.ps1 -Action List", "pwsh -NoProfile -File scripts/sot-worktree.ps1 -Action Inspect -Task TASK-12",
                        "./sot-worktree.ps1 \u2013Action Verify -Name 'a b' -Path docs/x.md",
                        "& 'scripts/sot-worktree.ps1' -Action:verify -Root D:/Project/x"):
            with self.subTest(command=command):
                check_command(command, actions=self.ACTIONS)

    def test_whole_script_names_still_win_and_a_malformed_entry_blocks_everything(self):
        with self.assertRaises(Blocked):
            check_command("sot-worktree.ps1 -Action List", extra=frozenset({"sot-worktree.ps1"}), actions=self.ACTIONS)
        with self.assertRaises(Blocked):
            check_command("sot-worktree.ps1 -Action List", actions={"sot-worktree.ps1": None})
        check_command("other.ps1 -Action Create", actions=self.ACTIONS)


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

    def test_hook_reads_write_actions_from_the_project_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".mercury").mkdir()
            cfg = project / ".mercury" / "config.json"

            def code(command, params):
                cfg.write_text(json.dumps({"schema_version": 1, "packs": ["core"], "params": params}))
                return run_hook({"tool_name": "PowerShell", "tool_input": {"command": command}, "cwd": str(project)}, project).returncode

            listed = {"guard_write_actions": {"Sot-Memory.ps1": ["Write"]}}
            self.assertEqual(code("pwsh -File scripts/sot-memory.ps1 -Action Verify", listed), 0)
            self.assertEqual(code("pwsh -File scripts/sot-memory.ps1 -Action write", listed), 2)
            self.assertEqual(code("pwsh -File scripts/sot-memory.ps1", listed), 2)
            # a malformed list for a named script blocks that script; a value that is not an object is ignored
            for bad in ({"sot-memory.ps1": "Write"}, {"sot-memory.ps1": []}, {"sot-memory.ps1": ["$x"]}):
                self.assertEqual(code("pwsh -File sot-memory.ps1 -Action Verify", {"guard_write_actions": bad}), 2)
            # a list of script names under this key (the shape of guard_write_scripts) blocks those scripts outright;
            # any other value that is not an object is ignored
            self.assertEqual(code("pwsh -File sot-memory.ps1 -Action Verify", {"guard_write_actions": ["Sot-Memory.ps1"]}), 2)
            for ignored in (["a/b.ps1"], [1], "sot-memory.ps1", 5):
                self.assertEqual(code("pwsh -File sot-memory.ps1 -Action Write", {"guard_write_actions": ignored}), 0)
            # both params together: the whole-script list and the per-action list each apply
            both = {"guard_write_scripts": ["deploy.sh"], **listed}
            self.assertEqual(code("bash deploy.sh", both), 2)
            self.assertEqual(code("pwsh -File sot-memory.ps1 -Action Verify", both), 0)


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
