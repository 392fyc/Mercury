"""Tests for packs/core/hooks/wait_guard.py (Mercury #706).

Run: python -B -m unittest discover -s tests/core -p 'test_*.py'
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "packs" / "core" / "hooks"
sys.path.insert(0, str(HOOKS))
sys.dont_write_bytecode = True
from wait_guard import problem  # noqa: E402

# The loop that ran for about ten hours in a PR #705 review (its condition could never come true).
INCIDENT = ('f="tasks/b.output"; until grep -q "== test_mercury_deploy" "$f" && '
            '[ $(grep -c "^Ran\\|OK\\|FAILED\\|Terminated" "$f") -ge 8 ]; do sleep 5; done; cat "$f"')

BLOCKED = [
    INCIDENT,
    "until curl -sf localhost:8080/health; do sleep 1; done",
    "while true; do gh pr checks 12; sleep 30; done",
    "while ! test -f out/done; do sleep 2; done",
    "while true\ndo\n  sleep 5\ndone",
    "tail -f build.log",
    "tail -n 50 -F build.log | grep --line-buffered ERROR",
    "tail --follow=name app.log",
    "inotifywait -m -e close_write out/",
    "journalctl -fu app",
    "docker logs -f web",
    "kubectl logs --follow pod/x",
    "gh pr checks 12 --watch",
    "gh run watch 123",
    "watch -n 5 ls",
    "sleep infinity",
    "Wait-Job $job",
    "while (-not (Test-Path out/done)) { Start-Sleep 5 }",
    "do { Start-Sleep -Seconds 5 } until (Test-Path out/done)",
    "Get-Content build.log -Wait",
    "bash -c 'until test -f x; do sleep 1; done'",
    # A deadline elsewhere in the command does not bound the loop.
    "until test -f x; do sleep 1; done; timeout 5 cat x",
    "timeout 600 bash -c 'echo hi'; while true; do sleep 1; done",
    'echo "timeout 5 x"; until test -f x; do sleep 1; done',
    "while true; do date +%s; sleep 5; done",
    "until test -f x; do sleep 1; done # Get-Date",
    "until test -f x; do sleep 1; done; echo $SECONDS",
    "while sleep 5; do test -f x && break; done",  # the sleep is in the condition
    "timeout 0 bash -c 'while true; do sleep 1; done'",  # a duration of 0 means no limit
    "for ((;;)); do sleep 5; done",
    "for (;;) { Start-Sleep 5 }",
    "while ($n -lt 10) { Start-Sleep 5 }; $n++",  # the increment is outside the loop body
    "docker exec web tail -f /var/log/app.log",
    "kubectl exec -it pod -- tail -f /var/log/app.log",
    "docker exec web sleep infinity",
    "echo cat tail -f log",
    'while ($true) { Write-Host "}"; Start-Sleep 5 }',
    "while ($n -lt 10) { # don't stop yet\n Start-Sleep 5 }\n$n++",  # an apostrophe in a comment
    "tail /var/lib/docker/containers/abc/abc-json.log -f",
    "tail -n 100 /srv/gh/runner.log --follow",
    r"Get-Content C:\ProgramData\docker\logs\app.log -Wait",
    r"Get-Content C:\gh\runner\_diag\Runner.log -Wait",
    "journalctl -u gh -f",
    "kubectl logs sleep -f",
    "docker logs gh --follow",
    "gh pr checks cat --watch",
    "inotifywait /tmp/cat -m",
    "tail -n 5 cat -f",
    "/usr/bin/tail -f x",
    "./tail -f x",
    "tail.exe -f x",
    "/c/tools/tail.exe -f x",
    "kubectl.exe logs -f pod",
    "/bin/sleep infinity",
    "gh.exe pr checks 1 --watch",
    "gh.exe run watch 1",
]

ALLOWED = [
    "timeout 900 bash -c 'until test -f out/done; do sleep 5; done'",
    "timeout -k 10 600s bash -c \"while ! gh pr checks 12; do sleep 30; done\"",
    "timeout -s KILL 60 bash -c 'while true; do sleep 1; done'",
    "timeout --signal=KILL 60 bash -c 'while true; do sleep 1; done'",
    "gtimeout 60 bash -c 'while true; do sleep 1; done'",
    "timeout 300 tail -f build.log",
    "cd x && timeout 900 gh pr checks 12 --watch",
    "end=$((SECONDS+600)); while [ $SECONDS -lt $end ] && ! test -f x; do sleep 5; done",
    "end=$(( $(date +%s) + 600 )); until test -f x || [ $(date +%s) -ge $end ]; do sleep 5; done",
    "$end = (Get-Date).AddMinutes(15); while (-not (Test-Path x) -and (Get-Date) -lt $end) { Start-Sleep 5 }",
    "$sw = [Diagnostics.Stopwatch]::StartNew(); while ($sw.Elapsed.TotalMinutes -lt 10) { Start-Sleep 5 }",
    "n=0; while [ $n -lt 60 ] && ! test -f x; do n=$((n+1)); sleep 5; done",
    "$n = 0; do { Start-Sleep 5; $n++ } while ($n -lt 10)",
    "git ls-files | while read f; do sleep 0; echo $f; done",
    "while read -r line; do echo \"$line\"; done < list.txt",
    "for i in 1 2 3; do { sleep 1; }; done",
    "for i in 1 2 3; do sleep 1; done",
    "sleep 5",
    "tail -n 20 build.log",
    "git log --oneline -5",
    "grep -n 'until' docs/guide.md",
    'rg "until.*sleep" packs/',
    r'git diff --cached | grep -n "while\|sleep"',
    'Get-Content packs/core/hooks/wait_guard.py | Select-String "while|sleep"',
    'gh issue comment 706 --body "Waits until the job ends; no sleep loop"',
    'powershell -File scripts/codex/git-safe.ps1 commit -m "fix(#706): block while loops that sleep without a deadline"',
    "Get-Content build.log -Tail 20",
    "Wait-Job $job -Timeout 600",
    "python -m pytest -q --timeout 60",
    "while ($x) { Write-Host 1 }; Start-Sleep 5",  # the sleep is outside the loop body
    "npm run watch",
    "docker logs --tail 50 web",
    "Wait-Process -Name sleep -Timeout 30",
    "Wait-Job -Name gh -Timeout 5",
    "python tail.py -f",
    "git log --follow -- packs/core/hooks/tail.py",
    "cat file.log | grep x",
]

POWERSHELL_BLOCKED = [
    "timeout 60 tail -f x.log",  # in PowerShell, timeout is timeout.exe and bounds nothing
]


class WaitGuardTest(unittest.TestCase):
    def test_blocked(self):
        for command in BLOCKED:
            with self.subTest(command=command):
                self.assertIsNotNone(problem(command))

    def test_powershell_timeout_is_not_a_wrapper(self):
        for command in POWERSHELL_BLOCKED:
            with self.subTest(command=command):
                self.assertIsNotNone(problem(command, "PowerShell"))
                self.assertIsNone(problem(command, "Bash"))

    def test_allowed(self):
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assertIsNone(problem(command))

    def test_long_commands_stay_fast(self):
        import time
        for command in ("docker logs " * 1700, "while (" * 6000, "tail " * 4000, "x; do " * 4000,
                        "timeout 9 for ((;;)); " * 1800, "timeout 5 tail -f x; " * 2000, "docker exec tail " * 2500,
                        "tail -n 5 " + "/a/cat/b " * 4000, "gh x " * 8000):
            with self.subTest(command=command[:20]):
                start = time.monotonic()
                problem(command)
                self.assertLess(time.monotonic() - start, 5)

    def run_hook(self, payload, launcher=False):
        script = ["sh", str(HOOKS / "wait_guard.sh")] if launcher else [sys.executable, "-B", str(HOOKS / "wait_guard.py")]
        return subprocess.run(script, input=json.dumps(payload), capture_output=True, text=True, timeout=60)

    def test_hook_exit_codes(self):
        blocked = self.run_hook({"tool_name": "Bash", "tool_input": {"command": INCIDENT, "run_in_background": True}})
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("wait guard", blocked.stderr)
        self.assertEqual(self.run_hook({"tool_name": "PowerShell", "tool_input": {"command": "Get-Date"}}).returncode, 0)
        self.assertEqual(self.run_hook({"tool_name": "Read", "tool_input": {"file_path": "x"}}).returncode, 0)
        self.assertEqual(self.run_hook({"tool_name": "Bash"}).returncode, 0)
        self.assertEqual(self.run_hook({"tool_name": "Bash", "tool_input": "tail -f x"}).returncode, 0)

    def test_launcher(self):
        if subprocess.run(["sh", "-c", "exit 0"], capture_output=True).returncode != 0:
            self.skipTest("no sh")
        payload = {"tool_name": "Bash", "tool_input": {"command": "tail -f x.log"}}
        self.assertEqual(self.run_hook(payload, launcher=True).returncode, 2)


if __name__ == "__main__":
    unittest.main()
