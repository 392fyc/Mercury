"""Tests for packs/core/hooks/readonly_guard.py (ported from the user-level guard of Mercury #628; Mercury #682).

Run: python -B -m unittest discover -s tests/core -p 'test_*.py'
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[2] / "packs" / "core" / "hooks"
sys.path.insert(0, str(HOOKS))
sys.dont_write_bytecode = True
from readonly_guard import Blocked, check_command  # noqa: E402

ALLOWED = [
    "git status -sb",
    # #821: tests and reads that name the paid helpers stay allowed; only running them is refused
    "python -B -m pytest -q tests/core/test_image_check.py", "python -m pytest tests/core -k image_api",
    "python -B -m pytest -rc tests/core/test_image_check.py", "python -m pytest tests -m image_check",
    "python tests/core/test_image_check.py -m image_check",
    "python -c 'print(1)' -m image_check",
    "python 2>&1 -m pytest tests -m image_check",
    "py exec -m pytest tests -m image_check",
    "python -m cProfile -m pytest tests -m image_check",
    "py -3.11 -m pdb -m pytest tests -m image_check",
    "py -3.12 -m pdb -m pytest -c quit tests -m image_check",
    "py -3.14 -m pdb -m pytest -c quit tests -m image_check",
    "py -3.14 -m pdb -m pytest tests -mimage_check",
    "py -3.11 -m pdb -m pytest tests -mimage_check",
    "py -V:PythonCore/3.14 -m pdb -m pytest tests -m image_check",
    "python -m pdb -m -m pytest tests -m image_check",
    "python -m pdb -mc quit pytest tests -m image_check",
    "python -m coverage run -m pytest tests -m image_check",
    "bash -c 'grep -n image_check packs/core/hooks/readonly_guard.py'",
    # wrapper commands run the command after them (#804): read-only ones stay allowed
    "timeout 20 git log -1", "timeout -k 5 20s git status", "nice -n 5 git log", "env -i A=1 git status",
    "nohup git fetch --dry-run", "xargs -n 1 grep -l push", "command -v git", "stdbuf -o0 git log", "env",
    "timeout 20 bash -c 'git status'", "time -p git log -1", "command -v rm", "command -V tee",
    "sudo -l rm", "sudo --list git push", "sudo -u x -l rm",
    "git -C D:/repo log --oneline -5",
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
    "cat a.txt 2>&1",
    "cat a.txt >&2",
    "cat a.txt 1>&2; echo ok",
    "cat a.txt >& 2",
    "cat a.txt 2>&-",
    "FOO=1 git log",
    "grep -n sync-downstream-pr.py README.md",
    "cat scripts/codex/git-safe.ps1",
    "rg git-safe.ps1 docs",
    "rg image_api.py docs",
    "cat .mercury/packs/lanes/scripts/lane-bus.py",
    "pwsh -File scripts/other-tool.ps1 -Action List",
    # #795: grouping around read-only commands stays allowed
    "(git status)",
    "(cd 'app' && git log --oneline -3)",
    "cd 'app' && { git status; }",
    "echo $(git rev-parse HEAD)",
    "echo ${HOME} ${rm}",
    "git show HEAD@{1}",
    "git log --format='%h (%s)' -3",
    "rg \"(git push)\" docs",
    "Get-ChildItem | Where-Object { $_.Name -eq 'x' } | ForEach-Object { $_.FullName }",
    "(Get-Content a.txt).Count",
    "find . -name '*.py' -exec grep -l x {} +",
    "Get-Content a.txt | Where-Object { $_.Contains('Remove-Item') }",
    "[math]::Max(1, 2)",
    "(Get-Content a.txt).Replace('rm', 'del').Trim()",
    "rg \\`git\\ push\\` docs",
    "(Get-Date).ToString('Remove-Item')",
    "Write-Output $x.y.Replace('rm', 'del')",
    "1..3 | ForEach-Object { $_.ToString('Remove-Item') }",
    "'a,b'.Split('Remove-Item')",
    "Get-ChildItem | Where-Object { $_.Name.ToLower().Contains('rm') }",
    "(Get-Date).ToString().Trim('rm')",
    "Write-Output ${env:TEMP}.Replace('rm', 'del') $env:TEMP.Contains('rm')",
    "cat ${repo}/scripts/codex/git-safe.ps1",
    "Get-Content ${env:TEMP}\\git-safe.ps1",
    "echo \"head: $(git rev-parse HEAD)\"",
    "rg '$(git push)' docs",
    "rg '`git push`' docs",
    "Write-Output \"a`nb`tc\"",
]

BLOCKED = [
    # #804: a write behind a wrapper command and its options
    "timeout 20 git push", "timeout -k 5 20 rm -f x", "timeout --signal=KILL 20 git push",
    "timeout 20 bash -c \"echo \\' ; rm -f x ; echo \\'\"", "nohup git push", "xargs git push",
    "xargs -0 -I {} rm {}", "stdbuf -o0 git push", "stdbuf -o 0 git push", "nice -n 5 git push", "nice -n5 git push",
    "sudo -u x git push", "sudo -- git push", "env -i git push", "env A=1 git push", "env -u A git push",
    "time -p git push", "command -p git push", "exec -a x git push", "/usr/bin/timeout 20 git push",
    "A=1 timeout 20 B=2 git push", "timeout 20 nice -n 5 git push", "stdbuf --output L git push",
    "echo x >& result.txt",
    "echo x >&١",
    "echo x >& out.txt",
    "echo x >&result.txt",
    "echo x 2>&1 >&out.log",
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
    "python -B .mercury/packs/core/skills/image-api/scripts/image_check.py --config c.json --card k.json --image i.png --execute",
    "uv run --no-project python packs/core/skills/image-api/scripts/image_check.py --execute",
    "python -m image_check --config c.json --card k.json --image i.png --execute",
    "cd packs/core/skills/image-api/scripts && python -B -m image_api --execute",
    "PYTHONPATH=packs/core/skills/image-api/scripts python -m image_check --execute",
    "python -c 'import image_check; image_check.main()'",
    "python -mimage_check --execute", "python -Bc 'import image_api'", "uv run --module image_check",
    "python -X utf8 -W ignore -Bmimage_check --help", "python -- image_check.py --help",
    "python -Bc'import image_api'",
    "python 2>&1 -m image_check --help", "python -m 2>&1 image_check --help",
    "py exec -B -m image_check --help", "py exec -V:PythonCore/3.14 image_api.py --help",
    "python - --help < packs/core/skills/image-api/scripts/image_check.py",
    "python < packs/core/skills/image-api/scripts/image_api.py",
    "python -m cProfile -s time packs/core/skills/image-api/scripts/image_check.py --help",
    "python -m profile -m image_api --help", "python -m pdb -m image_check --help",
    "python -m trace --trace --module image_api --help",
    "python -m cProfile --so time packs/core/skills/image-api/scripts/image_check.py --help",
    "python -m pdb example.py -c 'import image_check'",
    "python -m pdb --comm continue -m image_check --help",
    "python -m pdb -mc 'import image_check' example",
    "python {fd}>/dev/null -m image_check --help",
    "python -m coverage run packs/core/skills/image-api/scripts/image_check.py --help",
    "python -m coverage run --source packs/core -m image_check --help",
    "python -m coverage run --module image_api --help",
    "python -m debugpy -m image_check --help",
    "python -m IPython -c 'import image_api'",
    "python -m pdb -m -m image_check --help",
    "python -m pdb -mm -- image_check --help",
    "python -m pdb -mimage_check --help",
    "python /stdlib/cProfile.py -m image_check --help",
    "py -3.13 -B -m pdb -c quit -mimage_check",
    "py -3.11 -m pdb -m trace -c --module image_check --help",
    "python -m pdb -m trace -c --module image_check --help",
    "py -3.13 -m pdb -m pytest -c quit tests -m image_check",
    "py -3.13 -m pdb -m pytest tests -mimage_check",
    "py -3.14 /stdlib313/pdb.py -m pytest -c quit tests -m image_check",
    "python -m pdb -m pytest tests -m image_check",
    # #744: the lane bus writes its store, and release cannot be undone
    "bash .mercury/packs/lanes/scripts/lane-bus.sh release --harness claude --lane main",
    "bash .mercury/packs/lanes/scripts/lane-bus.sh read --harness claude",
    "python -B .mercury/packs/lanes/scripts/lane-bus.py claim --harness claude --session x",
    # #795: a write inside a subshell, a command group, a substitution or a script block
    "(git push)",
    "(cd 'app' && git push)",
    "cd 'app' && { git push; }",
    "{ rm x; }",
    "echo $(git commit -m x)",
    "((git push))",
    "bash -c \"(git push)\"",
    "(gh pr merge 3 --squash)",
    "(powershell -File scripts/codex/git-safe.ps1 commit -m x)",
    "& { Remove-Item x }",
    "Get-ChildItem | ForEach-Object { Remove-Item $_ }",
    "if (Test-Path x) { Set-Content x hi }",
    "echo \"$(git push)\"",
    "Write-Output \"$(Remove-Item x)\"",
    "echo \"head: $(cd app && git commit -m x) done\"",
    "echo \"$(echo \"$(git push)\")\"",
    "echo `git push`",
    "echo \"now `git push`\"",
    "x=${ git push; }",
    "$x.Where({ Remove-Item y })",
    "$x.Foo((git push))",
    "$x.Foo('a') ; (git push)",
    "Write-Output(Remove-Item x)",
    "echo \"\\\\`git push`\"",
    "echo \\\\`git push`",
    "echo \\\\\\\\`git push`",
    "echo \"\\\\\\\\`git push`\"",
    "git.exe(Remove-Item x)",
    "Get-Content a.txt(Remove-Item x)",
    "Write-Output [math]::Max(Remove-Item x)",
    "Write-Output a$x.Contains(Remove-Item x)",
    "Write-Output .Foo(Remove-Item x)",
    "Write-Output $x.ToString()a.Foo(Remove-Item x)",
    "Write-Output $x.ToString() .Foo(Remove-Item x)",
    "echo \"$(printf ')'; git push)\"",
    "echo \"$(echo \\'$( true ; echo \\') ; git push )\"",
    "Get-Content $dir/a.txt(Remove-Item x)",
    "Write-Output $x/a.Foo(Remove-Item x)",
    "Write-Output $x+a.Foo(Remove-Item x)",
    "Write-Output $x..Foo(Remove-Item x)",
    "Write-Output \"a\"b.Foo(Remove-Item x)",
    "Write-Output 'a'+$b.Foo(Remove-Item x)",
    "./tool.exe(Remove-Item x)",
    "echo ${x}; { git push; }",
    "echo ${x:-$(git push)}",
]

# #798: each shell lexes with its own quote, escape and comment rules. A command checked without a shell
# (shell=None) is read both ways and blocked if either reading blocks it, so these pass only under their shell.
SHELL_ALLOWED = [
    # the null device of each shell, spelled without quotes or escapes
    ("bash", "git diff HEAD~1 -- a.py 2>/dev/null"),
    ("bash", "echo done > /dev/null"),
    ("powershell", "Get-Content x > $null"),
    ("powershell", "Get-Content x 2>$NULL"),
    ("cmd", "type a.txt > nul"),
    ("powershell", "Write-Output 'it''s' \"say \"\"hi\"\"\""),
    ("powershell", "bash -c 'echo ''it is'' fine'"),
    ("powershell", 'git log --format="%h`t%s"'),
    ("powershell", 'bash -c "printf a`tb"'),
    ("powershell", "Write-Output 'a`u{27}b'"),
    ("powershell", 'Write-Output "a`0b"'),
    ("powershell", "git log --format='\"%h\"'"),
    ("powershell", "Select-String -Pattern '\"key\"' a.json"),
    # Bash drops a backslash-newline inside double quotes: one word `gitpush` is no write
    ("bash", 'bash -c "echo a; git\\\npush"'),
] + ([("powershell", "Get-Content x > nul")] if os.name == "nt" else []) + [
    ("bash", r'rg "\`git push origin\`" docs'),
    ("bash", 'echo "on `git branch --show-current`"'),
    ("bash", r"""echo "$(printf ')')" """),
    ("bash", r'''echo "\$(git push)" '''),  # an escaped $ in double quotes is text in bash
    ("bash", "echo hi # a comment"),
    ("bash", r"bash -c $'echo a\tb\x21'"),
    ("bash", "echo $'it\\'s' $$"),
    ("bash", r"echo $'\' ; git push ; echo \''"),  # one ANSI-C string: \' is an escaped quote
    ("powershell", r'Write-Output "file: `"$path`""'),  # the backtick escapes the quote
    ("powershell", r"$lines[0].StartsWith('Remove-Item')"),
    ("powershell", r"'a b'.Contains('Remove-Item')"),
    # The body of a here-document with a quoted delimiter is input, not commands (#800); PowerShell here-string bodies
    # are input too
    ("bash", "x=1; (( x += 2 )); echo $x $((1+2)) \"$((3-1))\""),
    ("bash", "cat <<\"END-1\" | python -\nprint(\"it's\")\nEND-1"),
    ("bash", "python - <<'EOF'\nxs = [1, 2]\nif len(xs) >= 2:\n    print(xs)\nEOF"),  # the downstream report on #800
    ("bash", "python - <<'EOF'\nxs = [1, 2]\nif len(xs) > 1:\n    print(xs)\nEOF"),
    ("bash", "cat <<'EOF'\nit's\nEOF"),
    ("bash", "cat <<'EOF'\ngit push > f\nEOF\necho ok"),
    ("bash", "cat <<\"EOF\" <<\\END\na > b\nEOF\nit's\nEND\necho ok"),
    ("bash", "cat <<-'EOF'\n\ta > b\n\tEOF\necho ok"),
    ("bash", "cat <<'EOF'\na > b\nEOF \ngit push\nEOF"),  # `EOF ` does not end the body
    ("bash", "cat <<'EOF'\na > b"),  # no end line: the body runs to the end
    ("bash", "cat <<'EOF' | python -\nimport os; print(1) # it's\nEOF\necho done"),  # refused before #800
    ("bash", "timeout 20 grep -c x <<'EOF' >&2 # sh\na > b\nEOF"),  # a wrapper, `>&2` and a comment
    ("bash", "cd x && wc -l <<'EOF'\na > b\nEOF"),
    # an unquoted delimiter: quotes and `>` in the body are text when it has no `$(`, backtick or backslash
    ("bash", "python3 - <<EOF\nxs = [1, 2]\nif len(xs) > 1:\n    print(xs)\nEOF"),
    ("bash", "cat <<EOF\nit's $HOME/$1 > not a file\nEOF"),
    ("bash", "cat <<-EOF\n\ta > b\n\tEOF\necho ok"),
    ("bash", "cat <<EOF\nit's\n"),  # no end line: the body runs to the end (refused before)
    ("bash", "git log --oneline | python - <<'EOF'\nimport sys\nprint(sum(1 for _ in sys.stdin) > 1)\nEOF"),
    ("bash", "echo $$"),
    # nested ${...} is Bash only: PowerShell's name ends at the first `}`
    ("bash", "echo ${prefix}rm ${a:-${b}}/lane-bus.sh"),
    ("bash", "echo ${x:-a # b} ${#x} ${x#y}"),
    ("powershell", "Write-Output ${a b} ${env:X}"),
    ("powershell", "$v = @'\nit's\n'@"),
    ("powershell", "Write-Output @'\nit's $(Remove-Item x)\n'@"),
    ("powershell", "Write-Output @\"\nit's `$(Remove-Item x)\n\"@"),
]

SHELL_BLOCKED = [
    # after an escaped `>` the `&` is PowerShell's background operator, not part of a redirection: git push runs
    ("powershell", "`>& git push"),
    ("powershell", "echo a`>& git push"),
    ("bash", "echo a \\>& git push"),
    ("powershell", "Write-Output `>a<&git push"),  # PowerShell has no `<&`: background `a<`, then git push
    ("powershell", "Write-Output a<&git push"),  # master too
    ("powershell", "Write-Output a&>git push"),
    ("powershell", "Write-Output `${git push}.Invoke()"),  # an escaped `$`: the braces are a script block (master too)
    # PowerShell `''` / `""` in a string is one literal quote, handed on to the inner shell
    ("powershell", "bash -c 'echo ''\\''\ngit push'"),
    ("powershell", "sh -c 'echo $''\\''\ngit push\necho $''\\'''"),
    ("powershell", 'bash -c "echo ""\\""\ngit push"'),
    # a nested `sh` may be dash, where `$'\'` is `$` and a plain quote: `git push` runs there
    ("bash", "sh -c \"echo \\$'\\\\' ; git push ; echo \\$'\\\\'\""),
    ("bash", "dash -c 'git push'"),
    # Argus on ad7dcbb: in a PowerShell string a backtick before a newline keeps it (two commands in real pwsh)
    ("powershell", 'pwsh -Command "Write-Output ok`\ngit push"'),
    ("powershell", 'powershell -Command "Write-Output ok`\nRemove-Item x"'),
    ("bash", 'pwsh -Command "Write-Output ok`\ngit push"'),
    # Argus on ae13a5c: each of these writes a file in the real shell
    ("bash", r"echo x > \$null"),
    ("bash", "echo x > '$null'"),
    ("bash", "echo x > $null"),  # writes the file named by a variable `null`
    ("bash", "echo x > null"),
    ("bash", "echo x > nul"),  # a real file in Git Bash too
    ("bash", "echo x > '/dev/null'x"),
    ("bash", "echo x > /DEV/NULL"),  # Bash paths are case-sensitive
    ("powershell", "echo x > '$null'"),
    ("powershell", "echo x > `$null"),
    ("powershell", "echo x > null"),
    ("powershell", "echo x > /dev/null"),
    ("cmd", "echo x > null"),
    ("bash", r"echo \' $(git push) \'"),  # \' is an escaped quote, so $(...) is live
    ("bash", r"echo \' ; git push ; echo \'"),
    ("bash", "echo a # it's\ngit push\necho 'b'"),  # the quote in the comment opens nothing
    ("bash", r"gi\t push"),
    ("bash", r"""echo "$(echo \'$( true ; echo \') ; git push )" """),
    ("bash", r"echo ${#x}; git push"),  # '#' after '{' is not a comment
    ("powershell", "Write-Output ‘a\"b’ ; Remove-Item x ; Write-Output ‘c\"d’"),  # curly quotes
    ("powershell", r"g`it push"),
    ("powershell", r"Remove-It`em x"),  # a backtick escape is read as the plain character (Remove-Item in 5.1)
    ("powershell", r'''Write-Output "\$(git push)" '''),  # '\' is no escape in PowerShell
    ("powershell", "<# it's #> Remove-Item x"),
    ("powershell", "Write-Output a#b; Remove-Item x"),  # '#' inside a word is not a comment
    (None, r"echo \' $(git push) \'"),
    (None, r'Write-Output "file: `"$path`""'),  # bash would read an open quote
    # an escaped space, ';' or newline is text, so the '#' after it starts no comment
    ("bash", r"echo a\ #; git push"),
    ("bash", r"echo \;#; git push"),
    ("bash", "echo a\\\n#; git push"),
    ("bash", r"bash -c 'echo a\ #; git push'"),
    ("powershell", r"Write-Output a` #; Remove-Item x"),
    ("powershell", r"Write-Output `;#; Remove-Item x"),
    ("powershell", "Write-Output a`\n#; Remove-Item x"),
    ("bash", r"echo $$'\'; git push #'"),  # `$$` is the process id, the quote after it a plain one
    ("bash", r"echo `echo \`git push\``"),  # \` inside backticks nests a substitution
    ("bash", r'echo "`echo \`git push\``"'),
    # Bash here-documents: commands after the body, or expanded in it, are still seen (a quote in the body of one
    # the guard does not cut that hides the next command is a known limitation, see the module docstring)
    ("bash", "cat <<A <<'B'\nit's\nA\nb's\nB\ngit push"),
    ("bash", "cat <<'EOF'\nit's\nEOF\ngit push # '"),  # #800: the quote in the body no longer hides the push
    ("bash", "cat <<EOF\nit's\nEOF\ngit push # '"),
    # an unquoted body that runs a command, or whose end a backslash-newline moves, is still read as commands
    ("bash", "cat <<EOF\n$(git push)\nEOF"),
    ("bash", "cat <<EOF\n`git push`\nEOF"),
    ("bash", "cat <<EOF\nx ${y:-$(git push)}\nEOF"),
    ("bash", "cat <<EOF\na\\\nEOF\ngit push\nEOF"),
    # a `$(` built in a variable and run by prompt expansion (review on #800, real Bash runs the push)
    ("bash", "cat <<EOF\n${d:=$}${y:=${d}(git push)}${y@P}\nEOF"),
    ("bash", "cat <<EOF\n${d:=$}${y:=a[${d}(git push)]}${y@P}\nEOF"),
    ("bash", "git push | python - <<'EOF'\nx\nEOF"),
    ("bash", "cat <<'A' <<'B'\nx\nA\nit's\nB\ngit push # '"),
    ("bash", "cat <<-'EOF'\n\tit's\n\tEOF\ngit push # '"),
    ("bash", "cat <<'E'\"O\"F\nit's\nEOF\ngit push # '"),
    ("bash", "cat <<'EOF' | git push\nx\nEOF"),
    ("bash", "cat > f <<'EOF'\nx\nEOF"),
    ("bash", "cat <<'EOF' # it's\nx\nEOF\ngit push"),
    ("bash", "cat <<'EOF'\nx\nEOF\r\ngit push"),  # Git Bash ends the body at EOF\r: a \r stops the cutting
    ("bash", "x=$(cat <<'EOF'\nhi\nEOF\n); git push"),
    ("bash", "echo \"$(cat <<'EOF'\nit's\nEOF\n)\"\ngit push"),
    ("bash", "echo $((1<<'2'))\ngit push\n2"),
    ("bash", "x=1; echo ${x:0<<'1'}\ngit push\n1"),
    ("bash", "{ cat <<'EOF'\nx\nEOF\n}; git push"),
    ("bash", "case a in a) cat <<'EOF'\nx\nEOF\n;; esac\ngit push"),
    # Bash reads the body only after a `$(`, `<(` or `${` opened on the operator's line has ended (#800 review)
    ("bash", "cat <<'EOF' <(\ngit push\n)\nx\nEOF"),
    ("bash", "cat <<'EOF' $(\ngit push\n)\nx\nEOF"),
    ("bash", "cat <<'EOF' ${x:-$(\ngit push\n)}\nx\nEOF"),
    ("bash", "cat <<'EOF' $(case a in a)\ngit push;; esac)\nx\nEOF"),
    # a lone `]` or `}` is a word character, so it does not close an earlier `$((` (#800 review)
    ("bash", "echo ] ]; echo $((1<<'2'))\ngit push\n2"),
    ("bash", "echo } }; echo $((1<<'2'))\ngit push\n2"),
    ("bash", "echo ] ]; (( x=1<<'2' ))\ngit push\n2"),
    ("bash", "echo ] ]; for (( i=0; i<<'2'; )); do :; done\ngit push\n2"),
    # a body that a shell or a later command runs stays checked as commands (Argus on #810)
    ("bash", "bash -s <<'EOF'\ngit push\nEOF"),
    ("bash", "bash <<'EOF'\ngit push\nEOF"),
    ("bash", "/usr/bin/bash <<'EOF'\ngit push\nEOF"),
    ("bash", "sh <<'EOF'\ngit push\nEOF"),
    ("bash", "sudo -s <<'EOF'\ngit push\nEOF"),
    ("bash", ". /dev/stdin <<'EOF'\ngit push\nEOF"),
    ("bash", "cat <<'EOF' | bash\ngit push\nEOF"),
    ("bash", "cat <<'EOF' |\ngit push\nEOF\nbash"),
    ("bash", "cat <<'EOF' | # x\ngit push\nEOF\nbash"),
    ("bash", "exec 3<<'EOF'\ngit push\nEOF\nbash <&3"),
    ("bash", "exec <<'EOF'\ngit push\nEOF\nbash"),
    ("bash", "while read -r l; do\n$l\ndone <<'EOF'\ngit push\nEOF"),
    # only commands that read the body as data get it cut (review on #810)
    ("bash", "xargs -L1 env <<'EOF'\ntouch made\nEOF"),
    ("bash", "xargs -L1 timeout 5 <<'EOF'\ntouch made\nEOF"),
    ("bash", "parallel <<'EOF'\ngit push\nEOF"),
    ("bash", "at now <<'EOF'\ngit push\nEOF"),
    ("bash", "make -f - <<'EOF'\nall:\n\tgit push\nEOF"),
    ("bash", "env -S 'bash -s' <<'EOF'\ngit push\nEOF"),
    ("bash", "<<'EOF' bash\ngit push\nEOF"),
    ("bash", "hash -p /usr/bin/bash cat\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "shopt -s expand_aliases\nalias cat=bash\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "eval 'cat(){ bash; }'\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "eval 'cat(){ bash; }'; cat <<'EOF'\ngit push\nEOF"),
    ("bash", "source /dev/stdin <<<'cat(){ bash; }'\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", ". /dev/stdin <<< 'cat(){ bash; }'; cat <<'EOF'\ngit push\nEOF"),
    ("bash", "function cat if true; then bash; fi\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "function cat for x in 1; do bash; done\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "shopt -s expand_aliases\neval 'alias cat=bash'\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "shopt -s expand_aliases\ndeclare 'BASH_ALIASES[cat]=bash'\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "shopt -s expand_aliases\nprintf -v 'BASH_ALIASES[cat]' bash\ncat <<'EOF'\ngit push\nEOF"),
    ("bash", "env -S'bash -s' cat <<'EOF'\ngit push\nEOF"),
    # GNU long-option prefixes and short clusters (Argus on #810, 0fed288): Bash runs the body.
    ("bash", "env --sp='bash -s' cat <<'EOF'\ngit push\nEOF"),
    ("bash", "env -iS'bash -s' cat <<'EOF'\ngit push\nEOF"),
    ("bash", "sort --buffer-size=1K --compress-prog=sh <<'EOF'\ngit push\nEOF"),
    ("bash", "xargs -d '\\n' --process-slot-var cat sh -c <<'EOF'\ngit push\nEOF"),
    ("bash", "sudo -R cat bash <<'EOF'\ngit push\nEOF"),
    ("bash", "cat <<EOF\nx $(git push) y\nEOF"),  # an unquoted delimiter expands the body
    ("bash", "cat <<EOF\nx `git push` y\nEOF"),
    ("bash", "echo $((1<<2))\ngit push\n2"),  # a shift, so the next line is a command
    ("bash", "echo $[1<<X]\ngit push\nX]"),
    ("bash", "cat <<EOF\r\nEOF\r\ngit push #\r\nEOF"),  # the delimiter is EOF\r, so the third line runs
    ("bash", "cat <<EOF\v\nEOF\v\ngit push #\nEOF"),
    ("bash", "x=abc\necho ${x:0<<1}\ngit push\n1}"),
    ("bash", "echo ${x:-<<EOF}\ngit push\nEOF}"),
    ("bash", "a[1<<2]=x\ngit push\n2]=x"),
    ("bash", "cat <<$'EOF'\nb\nEOF\ngit push\n$EOF"),
    ("bash", "cat <<$\"EOF\"\nb\nEOF\ngit push\n$EOF"),
    ("bash", "cat <<EOF; echo $(\ngit push\n)\nEOF"),
    ("bash", "cat <<E\"O\"F\nx\nEOF\ngit push"),
    # Every command is also read without comments (#799), so a comment that holds an odd quote or a write is refused,
    # as before #798, even where the shell skips it
    ("bash", "echo a;#x; git push"),
    ("bash", "(echo a)# it's"),
    ("powershell", "Write-Output (1)# it's"),
    # both readings at every depth: the outer command needs one, the `bash -c` inside it the other
    ("bash", "echo a # x'\nbash -c \"(( 1 #)); git push\"\necho \\'"),
    ("bash", "echo a # x'\nbash -c \"shopt -s extglob\necho @(a|#)x; git push\"\necho \\'"),
    # Argus on 6568661: `$'...'` and `$"..."` handed on as words lose their `$`, and `$'...'` escapes are decoded
    ("bash", "bash -c $'git push'"),
    ("bash", r"bash -c $'git\x20push'"),
    ("bash", r"bash -c $'echo a\ngit push'"),
    ("bash", r"bash -c $'\x67it push'"),
    ("bash", r"bash -c $'git\040push'"),
    ("bash", r"sh -c $'rm\x20-rf x'"),
    ("bash", "git $'push'"),
    ("bash", "bash -c $\"git push\""),
    ("bash", r"bash -c $'\U110000'"),  # beyond Unicode: refused
    # Bash ends a `$'...'` string at its first NUL
    ("bash", r"git $'push\0junk'"),
    ("bash", r"git $'push\c@x'"),
    ("bash", r"git $'push\x00x'"),
    ("bash", r"git $'push\400x'"),
    ("bash", r"bash -c $'git push\0x'"),
    # mark-review and apply spelled with quotes or escapes
    ("bash", r"pwsh -File guard.ps1 $'mark\x2dreview'"),
    ("bash", "pwsh -File guard.ps1 mark-rev''iew"),
    ("bash", r"python sync-project-template.py $'appl\x79'"),
    # PowerShell drops escaped whitespace before a command name
    ("powershell", "` Remove-Item v"),
    ("powershell", "Write-Output a; ` Remove-Item v"),
    ("powershell", "& ` Remove-Item v"),
    ("powershell", "`\tRemove-Item v"),
    ("powershell", "` git push"),
    # fifth review: Bash reads no comment in `(( ))`, `for (( ))` or an extglob pattern
    ("bash", "(( 1 #)); git push"),
    ("bash", "for (( i=0; i<1; i++ #)); do :; done; git push"),
    ("bash", "shopt -s extglob\necho @(a|#)x; git push"),
    ("bash", "shopt -s extglob\necho @(#)x; git push"),
    # Argus on #799: after the `)` of `$(...)`, `$((...))`, `<(...)`, `a=(...)` the word goes on, so `#` is no comment
    ("bash", "echo $(true)#; git push"),
    ("bash", "echo $((1))#; git push"),
    ("bash", "cat <(true)#; git push"),
    ("bash", "a=(1 2)#; git push"),
    ("bash", "echo $( (true) )#; git push"),
    ("bash", "x=$(true)#; git push"),
    # review of the fix: after the `()` of a function definition `#` is a comment, and its quote opens nothing
    ("bash", "f()#'\n{ git push; }\nf\necho \\'"),
    ("bash", "function f()#'\n{ git push; }\nf\necho \\'"),
    # review of the fix: no comment inside ${...}; inside [[ ]] only after whitespace; PowerShell ${...} is a name
    ("bash", "echo ${x:- #}; git push"),
    ("bash", "echo ${x:-(a)#}; git push"),
    ("bash", "echo ${x:-a;#}; git push"),
    ("bash", "echo ${x:+a b #c}; git push"),
    ("bash", "echo ${x:-${y:- #}}; git push"),
    ("bash", "[[ a =~ (a)# ]]; git push"),
    ("bash", "[[ a =~ a|# ]]; git push"),
    ("bash", "[[ a == a # it's\n ]] && git push #'"),
    ("bash", "[[ (a)#'\n]]\ngit push\necho \\'"),  # a grouping `)` inside [[ ]]: ambiguous, refused
    ("bash", "[[ a == a && (a)#'\n]]\ngit push\necho \\'"),
    ("bash", "[[ ( a )#'\n]]\ngit push\necho \\'"),
    ("bash", "echo $${x #'\ngit push\necho \\'"),  # `$$` is the process id, `{x` plain text, `#'` a comment
    ("bash", "echo a$${b c #'\ngit push\necho \\'"),  # after whitespace it is a comment, so the quote opens nothing
    ("powershell", "Write-Output ${a #}; Remove-Item x"),
    ("powershell", "Write-Output ${a # it's}\nRemove-Item x"),
    ("powershell", "Write-Output ${a"),
    # Argus on #799: `@'` inside a word is no here-string (a plain argument and a plain string)
    ("powershell", "Write-Output x@'\n'; Remove-Item victim <#\n'@\n#>"),
    ("powershell", "Write-Output $v@\"\n\"; Remove-Item victim <#\n\"@\n#>"),
    ("powershell", "Write-Output -@'\n'; Remove-Item victim <#\n'@\n#>"),
    ("powershell", "Write-Output a`x@'\n'; Remove-Item victim <#\n'@\n#>"),
    ("powershell", "Write-Output 1<@'\nx\n'@"),  # neither list: refused
    # a quote inside a PowerShell here-string body opens nothing
    ("powershell", "$x = @'\nfoo\r'@\nRemove-Item x\n'@'\n#'"),  # PowerShell ends a line at a lone \r
    ("powershell", "$x = @\"\nfoo\r\"@\nRemove-Item x\n\"@\"\n#\""),
    ("powershell", "Write-Output @'\nit's\n'@; Remove-Item x # '"),
    ("powershell", "Write-Output @\"\nit\"s\n\"@; Remove-Item x # \""),
    ("powershell", "Write-Output @'\nit's\n  '@\n'@; Remove-Item x # '"),  # an indented end is body
    ("powershell", "Write-Output @\"\nx $(Remove-Item x) y\n\"@"),
    ("powershell", "Write-Output @'\nit's\n"),  # no end line
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

    def test_shell_specific_lexing(self):
        for shell, command in SHELL_ALLOWED:
            with self.subTest(shell=shell, command=command):
                check_command(command, shell=shell)
        for shell, command in SHELL_BLOCKED:
            with self.subTest(shell=shell, command=command):
                with self.assertRaises(Blocked):
                    check_command(command, shell=shell)

    def test_without_a_shell_both_readings_must_pass(self):
        for shell, command in SHELL_ALLOWED:
            other = "powershell" if shell == "bash" else "bash"
            with self.subTest(command=command):
                try:
                    check_command(command, shell=other)
                except Blocked:
                    with self.assertRaises(Blocked):
                        check_command(command)
                else:
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

    def test_opaque_python_entry_file_option_checks_project_scripts(self):
        extra = frozenset({"task_file.py"})
        for entry in ("-m locust", "/venv/bin/locust", "-m pytest", "tools/trace.py"):
            for option in ("-f", "-File"):
                with self.subTest(entry=entry, option=option):
                    with self.assertRaisesRegex(Blocked, "task_file.py"):
                        check_command(f"python {entry} {option} tasks/task_file.py", extra=extra)
        check_command("python -m pytest -f tests/test_main.py", extra=extra)

    def test_ipython_file_launcher_checks_code_arguments(self):
        launchers = ("/venv/bin/ipython", "/venv/bin/ipython3", "/venv/site-packages/IPython/__main__.py",
                     "IPython/__main__.py")
        for launcher in launchers:
            with self.subTest(launcher=launcher):
                with self.assertRaisesRegex(Blocked, "image_check.py"):
                    check_command(f'python {launcher} -c "print(\'image_check\')"')
        check_command('python tools/reader.py -c "print(\'image_check\')"')
        check_command('python tools/__main__.py -c "print(\'image_check\')"')

    def test_python_checks_project_scripts_executed_from_stdin(self):
        extra = frozenset({"task_file.py"})
        for prefix in ("python - <", "python <", "python -B - 0<"):
            with self.subTest(prefix=prefix):
                with self.assertRaisesRegex(Blocked, "task_file.py"):
                    check_command(prefix + "tasks/task_file.py", extra=extra, shell="bash")
        check_command("python tests/test_main.py <tasks/task_file.py", extra=extra, shell="bash")
        check_command("python - 3<tasks/task_file.py", extra=extra, shell="bash")

    def test_unittest_checks_explicit_project_script_targets(self):
        extra = frozenset({"task_file.py"})
        for options in ("-f", "-f -v", "--failfast", "-f --"):
            with self.subTest(options=options):
                with self.assertRaisesRegex(Blocked, "task_file.py"):
                    check_command(f"python -m unittest {options} tasks/task_file.py", extra=extra)
        check_command("python -m unittest -f -k task_file.py tests/test_main.py", extra=extra)
        check_command("python -m unittest -fk task_file.py tests/test_main.py", extra=extra)


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
                    check_command(command, actions=self.ACTIONS, shell="powershell")
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

    def test_hook_lexes_with_the_shell_of_the_tool(self):
        command = 'Write-Output "file: `"$path`""'
        self.assertEqual(run_hook({"tool_name": "PowerShell", "tool_input": {"command": command}}).returncode, 0)
        self.assertEqual(run_hook({"tool_name": "Bash", "tool_input": {"command": command}}).returncode, 2)
        command = r"echo \' $(git push) \'"
        self.assertEqual(run_hook({"tool_name": "Bash", "tool_input": {"command": command}}).returncode, 2)

    def test_hook_blocks_when_the_check_itself_fails(self):
        # third review B9: 600 nested "$(...)" overflow the recursion; the hook must block (2), not crash (1)
        command = 'echo "$(' * 600 + "git push" + ')"' * 600
        result = run_hook({"tool_name": "Bash", "tool_input": {"command": command}})
        self.assertEqual(result.returncode, 2, result.stderr[-300:])

    def test_long_method_chain_is_checked_in_time(self):
        # fourth review R1: each `(` lexed its piece again, so 16k characters took seconds and 64k over a minute
        # fifth review: rebuilding the piece at every `(` and `)` of an inert member chain was quadratic as well
        for command in ("Write-Output " + "a.b(" * 16000 + ")" * 16000, "$a.b()" + ".c()" * 32000,
                        "Write-Output " + "$a.b( " * 16000 + ")" * 16000):
            with self.subTest(length=len(command), start=command[:16]):
                started = time.monotonic()
                check_command(command, shell="powershell")
                self.assertLess(time.monotonic() - started, 10)

    def test_check_past_its_time_budget_is_refused(self):
        # review of #799: three nested quoted `powershell -Command` of 400k characters took 76 s, past the hook timeout
        import readonly_guard
        saved = readonly_guard.CHECK_BUDGET_S
        readonly_guard.CHECK_BUDGET_S = -1  # already past: Windows' monotonic clock may not move between two reads
        try:
            with self.assertRaises(Blocked):
                check_command("pwsh -Command 'git status'", shell="powershell")
        finally:
            readonly_guard.CHECK_BUDGET_S = saved
        check_command("pwsh -Command 'git status'", shell="powershell")

    def test_hook_watchdog_blocks_a_check_stuck_in_one_step(self):
        # review of #799: the budget was checked only between commands, so one slow step (a quadratic redirect scan)
        # kept the real hook busy for two minutes; the hook's watchdog ends it and blocks
        script = ("import io, json, sys, time; sys.path.insert(0, sys.argv[1]); import readonly_guard as g; "
                  "g.CHECK_BUDGET_S = -4; g.check_command = lambda *a, **k: time.sleep(30); "
                  "sys.stdin = io.StringIO(json.dumps({'tool_name': 'Bash', 'tool_input': {'command': 'ls'}})); "
                  "sys.exit(g.main())")
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-B", "-c", script, str(HOOKS)], capture_output=True, text=True,
                                timeout=60)
        self.assertEqual(result.returncode, 2, result.stderr[-300:])
        self.assertLess(time.monotonic() - started, 20)

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
