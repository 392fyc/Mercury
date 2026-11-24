"""PreToolUse guard for read-only subagents (Mercury #628, packaged by #682).

Registered in the `hooks` frontmatter of the read-only `mercury-*` Claude roles (through readonly_guard.sh).
Subagent `permissionMode: plan` is ignored when the main conversation runs in bypassPermissions, acceptEdits
or auto mode, so this hook is the effective write guard. It blocks repository, GitHub and filesystem writes in
Bash and PowerShell commands by exiting 2. It is best-effort, not an OS sandbox: arbitrary interpreters
(python -c, node -e) can still write. A command inside a subshell, a command group, a substitution or a script
block (`(...)`, `{ ...; }`, `$(...)` also inside double quotes, backticks, `& { ... }`) is checked as well (#795).
Quotes, escapes and comments are read with the rules of the shell that runs the command (#798; see lex): the hook
payload's tool_name picks Bash or PowerShell, and a nested `bash -c`, `pwsh -Command` or `cmd /c` uses its own;
PowerShell here-strings are read too. Every command is read once with comments and once without, and refused if
either reading refuses (#799; see check_command). The body of a Bash here-document with a quoted delimiter
(`<<'EOF'`) is plain text to Bash and is left out of the check where the guard can tell where it ends (#800; see
cut_here_documents). Still best-effort: other here-documents (`<<EOF`, or one where any condition listed in
cut_here_documents fails, such as a `(`, `[` or `$` before it) are checked as if their body were commands, so an
odd quote in such a body can hide the next command from the guard, and `case` patterns are not read; in both, a `)` inside `"$(...)"` can end the substitution early for the guard. It catches a
write written plainly, not one written to evade it: a command after a keyword (`if git push; then`, `! git push`), a
redirection before the command name, `bash -lc`, piping into a shell, brace expansion, `eval` or `iex`, a variable as
command name, an expansion in place of a space (`git${IFS}push`), words after `pwsh -Command`'s first,
`-EncodedCommand`, PowerShell escapes that build a character (`` `n ``, `` `0 ``, `` `u{...} ``), and quotes that
Windows PowerShell 5.1 drops when it passes an argument to a program all pass it (as before #798).

A project adds write-type scripts of its own in `.mercury/config.json`:
    {"params": {"guard_write_scripts": ["deploy.sh"]}}
The list is read from $CLAUDE_PROJECT_DIR (or the hook payload's cwd). An unreadable or malformed list is
ignored; the built-in list always applies.

A script that has read-only and writing actions is named per action instead:
    {"params": {"guard_write_actions": {"deploy.ps1": ["Apply", "Remove"]}}}
Such a script is blocked only when its `-Action` value (PowerShell syntax, any case, `-Action X` or `-Action:X`) is in
the list, and also whenever the arguments cannot be read without doubt (fail closed): anything but plain ASCII words
after the script name (see ACTION_ARG_FORBIDDEN), a missing, repeated or non-plain -Action. A malformed action list
blocks every call of that script; a list of script names under this key blocks those scripts outright.
"""
from __future__ import annotations

import contextvars
import json
import os
import re
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

GIT_WRITE = {
    "add", "am", "apply", "checkout", "cherry-pick", "clean", "clone", "commit",
    "commit-tree", "gc", "init", "merge", "mv", "notes", "prune", "pull", "push",
    "rebase", "replace", "reset", "restore", "revert", "rm", "stash", "switch",
    "update-index", "update-ref",
}
GIT_SUBCOMMAND_WRITE = {
    "worktree": {"add", "move", "prune", "remove", "repair", "lock", "unlock"},
    "submodule": {"add", "update", "sync", "deinit", "init", "set-branch", "set-url"},
    "remote": {"add", "remove", "rm", "rename", "set-url", "set-branches", "prune"},
    "sparse-checkout": {"set", "add", "init", "disable", "reapply"},
}
GIT_BRANCH_READ = {
    "-l", "--list", "-a", "--all", "-r", "--remotes", "-v", "-vv", "--verbose",
    "--show-current", "--contains", "--no-contains", "--merged", "--no-merged",
    "--points-at", "--sort", "--format", "--column", "--no-column", "--color",
    "--no-color", "-i", "--ignore-case",
}
GH_WRITE = {
    "pr": {"create", "merge", "edit", "close", "reopen", "comment", "review", "ready", "lock", "unlock", "update-branch"},
    "issue": {"create", "edit", "close", "reopen", "comment", "delete", "transfer", "pin", "unpin", "lock", "unlock", "develop"},
    "release": {"create", "edit", "delete", "upload", "delete-asset"},
    "repo": {"create", "delete", "edit", "fork", "rename", "archive", "unarchive", "sync", "set-default"},
    "label": {"create", "edit", "delete", "clone"},
    "workflow": {"run", "enable", "disable"},
    "run": {"rerun", "cancel", "delete"},
    "secret": {"set", "delete"},
    "variable": {"set", "delete"},
    "gist": {"create", "edit", "delete", "clone"},
}
FILE_WRITERS = {
    "rm", "rmdir", "mv", "cp", "mkdir", "touch", "tee", "ln", "chmod", "chown",
    "truncate", "dd", "install", "rsync", "unlink", "shred", "patch",
    # PowerShell cmdlets and aliases
    "remove-item", "new-item", "set-content", "add-content", "out-file",
    "move-item", "copy-item", "rename-item", "clear-content", "set-item",
    "new-itemproperty", "set-itemproperty", "remove-itemproperty", "export-csv",
    "export-clixml", "ri", "del", "erase", "rd", "ni", "sc", "ac", "mi", "cpi",
    "rni", "md", "clc", "copy", "move", "ren",
}
WRITE_SCRIPTS = (
    "git-safe.ps1", "sync-downstream-pr.py", "lane-spawn.sh", "lane-close.sh", "lane-claim.sh", "lane-init.sh",
    "image_api.py", "image_check.py",
    # The lane bus writes its store on claim, release, send and read (reading marks read); release cannot be undone (#744).
    "lane-bus.sh", "lane-bus.py",
)
SHELLS = {"bash", "sh", "dash", "ash", "zsh", "powershell", "pwsh", "cmd"}
PYTHON_INTERPRETERS = {"python", "python3", "py"}
INTERPRETERS = {"pwsh", "powershell", "bash", "sh", "uv", "uvx", "node"}
PAID_PYTHON_HELPERS = ("image_api", "image_check")
# Options that take a value before the target program of these runners.
PROFILE_VALUE_OPTIONS = ("os", {"--outfile", "--sort"})
PYTHON_PROGRAM_RUNNERS = {
    "cprofile": PROFILE_VALUE_OPTIONS,
    "profile": PROFILE_VALUE_OPTIONS,
    "trace": ("fC", {"--file", "--coverdir", "--ignore-module", "--ignore-dir"}),
    "pdb": ("p", {"--pid"}),
    "coverage": ("", {"--concurrency", "--context", "--data-file", "--debug", "--include",
                     "--omit", "--rcfile", "--save-signal", "--source"}),
}
INLINE_FLAGS = {"-c", "-command", "/c", "-commandwithargs"}
# Per shell, spelled without quotes or escapes (Argus on ae13a5c). Checked in real shells: in Bash, `\$null`, `null`,
# `nul` (Git Bash too) and `$null` with a variable `null` set all write a file; in PowerShell `'$null'` and `null`
# write a file and `/dev/null` is `\dev\null` on the current drive (pwsh 7.6 too). `nul` is the null device only on
# Windows. Bash paths are case-sensitive; PowerShell and cmd names are not.
SAFE_REDIRECT_TARGETS = {"bash": {"/dev/null"}, "sh": {"/dev/null"},"powershell": {"$null"} | ({"nul"} if os.name == "nt" else set()),
                         "cmd": {"nul"}}


class Blocked(Exception):
    pass


class Rules(NamedTuple):
    """How one shell reads quotes, escapes, substitutions and comments (#798)."""
    single: str               # characters that open and close a literal string
    double: str               # characters that open and close a string with substitutions
    escape: str               # the escape character outside single quotes
    dq_escapes: str | None    # what it escapes inside double quotes (None: every character)
    substitutes: bool         # "$(...)" inside double quotes runs a command
    backticks: bool           # `...` runs a command
    ansi: bool                # $'...' strings, where a backslash escapes
    comments: bool            # '#' at the start of a word starts a comment that ends at the newline
    block_comments: bool      # <# ... #>
    here_strings: bool        # @' ... '@ and @" ... "@ here-strings
    braced_names: bool        # PowerShell: `${...}` is a variable name, text up to its `}`
    doubled: bool = False     # PowerShell: inside a string two quotes are one literal quote (`'it''s'`, `"a""b"`)


# PowerShell also reads the curly quotes as quotes (U+2018-U+201B single, U+201C-U+201E double); Bash and cmd do not.
RULES = {
    "bash": Rules("'", '"', "\\", "$`\"\\\n", True, True, True, True, False, False, False),
    "powershell": Rules("'‘’‚‛", '"“”„', "`", None, True, False, False, True, True, True, True, True),
    "cmd": Rules("", '"', "^", "", False, False, False, False, False, False, False),
}
RULES["sh"] = RULES["bash"]._replace(ansi=False)
# Whether lex reads comments; check_command reads every command both ways (see there).
READ_COMMENTS: contextvars.ContextVar[bool] = contextvars.ContextVar("READ_COMMENTS", default=True)
# When the check of one command must end (time.monotonic()); set by the outermost check_command.
DEADLINE: contextvars.ContextVar[float | None] = contextvars.ContextVar("DEADLINE", default=None)
CHECK_BUDGET_S = 20
# Line breaks that one shell may honour and the other not (PowerShell ends a line at a lone \r); refused outright.
ODD_BREAKS = re.compile("\r(?!\n)|[\v\f\x85  ]")
# "sh": a POSIX shell such as dash, which reads `$'...'` as `$` and a plain quote (#799: `sh -c "echo $'\' ; git push
# ; echo $'\'"` runs `git push` in dash); a nested `sh` is read both as Bash and as "sh".
SHELL_OF = {"bash": "bash", "sh": "sh", "dash": "sh", "ash": "sh", "zsh": "bash", "powershell": "powershell",
            "pwsh": "powershell", "cmd": "cmd"}
LIVE, SYNTAX, TEXT, COMMENT = 0, 1, 2, 3
# A '#' after one of these unescaped characters (or at the start) is read as a comment when comments are read. That is
# wider than either shell: Bash reads no comment after the `)` of `$(...)`, inside `${...}`, `(( ))` or a `=~` regex,
# and the second reading without comments covers each such place (#799).
COMMENT_AFTER = " \t\n;|&()<>{}"
# PowerShell reads `${...}` as a variable name: quotes and `#` in it are part of the name (`${a #}; Remove-Item x`).
# A PowerShell here-string starts only where a token can start (checked in both editions): at the start, after
# whitespace, one of these characters or a quote. After a word character (`x@'`, `$v@'`, `-@'`, `a:@'`) the `@` and
# the quote are a plain argument and a plain string.
HERE_AFTER = " \t\n=(,;|{&+)]}>@"
HERE_IN_WORD = "$-:./*[%!_?\\~^#"


def lex(text: str, shell: str) -> tuple[list[int], list[str]]:
    """The kind of every character of `text` as `shell` reads it, and the commands its substitutions run (#798).

    LIVE: shell syntax (separators, grouping, redirection, the words of a command). SYNTAX: a quote or escape character,
    which the shell removes. TEXT: quoted or escaped text, and the whole of a substitution inside double quotes or
    backticks. COMMENT: a comment. The second result lists each such substitution's command once (the outermost ones);
    a substitution nested inside one is found when that command is checked. An open quote at the end is refused.
    PowerShell here-strings (`@'` and `@"` at a line end, where a token starts) are read as PowerShell reads them:
    their body is text, with substitutions unless single-quoted, and a missing end line is refused. Bash here-documents
    are not read here: their body is lexed as commands, as before #798. _check_command first cuts out the bodies it can
    place exactly (cut_here_documents, #800).
    Not read either: `case` patterns and other syntax where a `)` or a quote means something else; there, and in a
    here-document, a substitution can end early, hiding what follows, or a read-only command is refused."""
    kinds = [LIVE] * len(text)
    subs: list[str] = []
    rules = RULES[shell] if READ_COMMENTS.get() else RULES[shell]._replace(comments=False, block_comments=False)
    _scan(text, 0, rules, kinds, subs, "top")
    return kinds, subs


def _scan(text: str, i: int, rules: Rules, kinds: list[int], subs: list[str], mode: str) -> int:
    """Mark kinds from i on. In mode "sub" (inside a `$(`) stop at the `)` that closes it and return its index."""
    n, depth, quote = len(text), 0, None
    while i < n:
        ch = text[i]
        if quote == "single":
            if rules.doubled and ch in rules.single and text[i + 1:i + 2] and text[i + 1] in rules.single:
                # `''` is one literal quote: tokenize keeps it, as PowerShell passes it on (#799: `bash -c 'echo
                # ''\''<LF>git push'` hands bash `echo '\'<LF>git push`, which runs `git push`)
                kinds[i], kinds[i + 1] = SYNTAX, TEXT
                i += 1
            else:
                kinds[i] = SYNTAX if ch in rules.single else TEXT
                quote = None if ch in rules.single else quote
        elif quote == "ansi":
            if ch == "\\" and i + 1 < n:
                kinds[i], kinds[i + 1] = SYNTAX, TEXT
                i += 1
            else:
                kinds[i] = SYNTAX if ch == "'" else TEXT
                quote = None if ch == "'" else quote
        elif quote == "double":
            if rules.doubled and ch in rules.double and text[i + 1:i + 2] and text[i + 1] in rules.double:
                kinds[i], kinds[i + 1] = SYNTAX, TEXT  # `""` is one literal quote, as `''` above
                i += 1
            elif ch in rules.double:
                kinds[i], quote = SYNTAX, None
            elif ch == rules.escape and i + 1 < n and (rules.dq_escapes is None or text[i + 1] in rules.dq_escapes):
                # A continuation in Bash; in a PowerShell string the backtick keeps the newline (Argus on ad7dcbb:
                # `pwsh -Command "Write-Output ok`<LF>git push"` runs two commands).
                kinds[i], kinds[i + 1] = SYNTAX, SYNTAX if text[i + 1] == "\n" and rules.dq_escapes else TEXT
                i += 1
            elif rules.substitutes and ch == "$" and text[i + 1:i + 2] == "(":
                i = _substitution(text, i, i + 2, rules, kinds, subs)
            elif rules.backticks and ch == "`":
                i = _substitution(text, i, i + 1, rules, kinds, subs)
            else:
                kinds[i] = TEXT
        elif ch == rules.escape and i + 1 < n:
            kinds[i], kinds[i + 1] = SYNTAX, SYNTAX if text[i + 1] == "\n" else TEXT  # escaped newline: continuation
            i += 1
        elif rules.here_strings and ch == "@" and text[i + 1:i + 2] and text[i + 1] in rules.single + rules.double \
                and re.match(r"[ \t]*\r?\n", text[i + 2:]) and _here_string_start(text, i, rules, kinds):
            i = _here_string(text, i, rules, kinds, subs)
            continue
        elif ch in rules.single:
            dollars = 0  # `$'` opens an ANSI string, but `$$` is the process id: an odd run of live `$` counts
            while i - dollars > 0 and text[i - dollars - 1] == "$" and kinds[i - dollars - 1] == LIVE:
                dollars += 1
            ansi = rules.ansi and dollars % 2 == 1
            kinds[i], quote = SYNTAX, "ansi" if ansi else "single"
        elif ch in rules.double:
            kinds[i], quote = SYNTAX, "double"
        elif (rules.comments and ch == "#" or rules.block_comments and text.startswith("<#", i)) \
                and (i == 0 or kinds[i - 1] == LIVE and text[i - 1] in COMMENT_AFTER):  # an escaped break is text
            end = text.find("#>", i + 2) + 2 if ch == "<" else text.find("\n", i)
            if ch == "<" and end < 2:
                raise Blocked("unterminated <# comment; command could not be checked")
            end = n if end < 0 else end
            kinds[i:end] = [COMMENT] * (end - i)
            i = end
            continue
        elif rules.backticks and ch == "`":
            i = _substitution(text, i, i + 1, rules, kinds, subs)
        elif rules.braced_names and ch == "$" and text[i + 1:i + 2] == "{":
            end = i + 2  # PowerShell: the name runs to the first `}` that no backtick escapes
            while end < n and text[end] != "}":
                end += 2 if text[end] == rules.escape else 1
            if end >= n:
                raise Blocked("unterminated ${ variable name; command could not be checked")
            kinds[i:end + 1] = [TEXT] * (end + 1 - i)
            i = end
        elif mode == "sub" and ch == "(":
            depth += 1
        elif mode == "sub" and ch == ")":
            if depth == 0:
                return i
            depth -= 1
        i += 1
    if quote:
        raise Blocked("unbalanced quotes; command could not be checked")
    return n


def _substitution(text: str, start: int, inner: int, rules: Rules, kinds: list[int], subs: list[str]) -> int:
    """Record the command of the substitution that starts at `start` and return the index of its closing character.
    Inside `$(...)` the shell reads a new command with its own quotes; inside backticks Bash looks only for the next
    backtick that no backslash escapes."""
    if text[start] == "`":
        end = inner
        while end < len(text) and text[end] != "`":
            end += 2 if text[end] == "\\" else 1
        end = min(end, len(text))
        # Inside backticks Bash turns \` \\ \$ back into ` \ $ before it runs the command, so `echo \`git push\``
        # nests a second substitution.
        subs.append(re.sub(r"\\([`\\$])", r"\1", text[inner:end]))
    else:
        end = _scan(text, inner, rules, kinds, [], "sub")
        subs.append(text[inner:end])
    kinds[start:end + 1] = [TEXT] * len(kinds[start:end + 1])
    return end


def _body(text: str, a: int, b: int, rules: Rules, kinds: list[int], subs: list[str], escapes: str | None) -> None:
    """Mark text[a:b], the body of a double-quoted here-string: text, apart from escapes and the substitutions in it,
    which must end inside the body."""
    i = a
    while i < b:
        ch = text[i]
        if ch == rules.escape and i + 1 < b and (escapes is None or text[i + 1] in escapes):
            kinds[i], kinds[i + 1] = SYNTAX, TEXT
            i += 2
            continue
        if ch == "$" and text[i + 1:i + 2] == "(" or rules.backticks and ch == "`":
            i = _substitution(text, i, i + (1 if ch == "`" else 2), rules, kinds, subs)
            if i >= b:
                raise Blocked("substitution runs past the end of a here-string; command could not be checked")
        else:
            kinds[i] = TEXT
        i += 1


def _here_string_start(text: str, i: int, rules: Rules, kinds: list[int]) -> bool:
    """Whether the `@` at i, followed by a quote at a line end, starts a here-string (see HERE_AFTER). A character
    before it that neither list covers is refused: reading it wrongly either way can hide a command."""
    if i == 0:
        return True
    prev, kind = text[i - 1], kinds[i - 1]
    if kind == LIVE and prev in HERE_AFTER or kind == SYNTAX and prev in rules.single + rules.double:
        return True
    if kind == TEXT or kind == LIVE and (prev.isalnum() or prev in HERE_IN_WORD):
        return False
    raise Blocked(f"cannot tell whether {prev!r}@ starts a here-string; command could not be checked")


def _here_string(text: str, i: int, rules: Rules, kinds: list[int], subs: list[str]) -> int:
    """PowerShell `@'` or `@"` at the end of a line: the body runs to a line that starts with the closing quote and `@`
    (at the very start of the line: an indented one is body). Return the index after that `@`."""
    single = text[i + 1] in rules.single
    body = text.index("\n", i) + 1
    close = re.compile(r"(?m)^[" + re.escape(rules.single if single else rules.double) + "]@").search(text, body)
    if not close:
        raise Blocked("here-string without its end line; command could not be checked")
    kinds[i:body] = [SYNTAX] * (body - i)
    if single:
        kinds[body:close.start()] = [TEXT] * (close.start() - body)
    else:
        _body(text, body, close.start(), rules, kinds, subs, None)
    kinds[close.start():close.end()] = [SYNTAX, SYNTAX]
    return close.end()


def split_segments(command: str, shell: str = "bash") -> list[str]:
    """Split on ; && || | & and newlines that the shell reads as syntax (not quoted, escaped or in a comment)."""
    kinds, _ = lex(command, shell)
    segments, start, i = [], 0, 0
    while i < len(command):
        ch = command[i]
        if kinds[i] == LIVE and (ch in ";|\n" or (ch == "&" and not _is_redirect_amp(command, i, kinds, shell))):
            segments.append(command[start:i])
            if command[i:i + 2] in ("&&", "||") and kinds[i + 1] == LIVE:
                i += 1
            start = i + 1
        i += 1
    segments.append(command[start:])
    return [s.strip() for s in segments if s.strip()]


def _is_redirect_amp(command: str, i: int, kinds: list[int], shell: str = "bash") -> bool:
    # Only a live `<`/`>` makes `&` part of a redirection: after an escaped one (`` `>& `` in PowerShell, `\>&` in
    # Bash) the `&` is the background operator, and the command after it runs (#798). PowerShell has only `n>&1`
    # and `*>&1`: its `<` is no redirection, so `a<&git push` runs `a<` in the background and then `git push`.
    if shell == "powershell":
        return i > 0 and command[i - 1] == ">" and kinds[i - 1] == LIVE
    return ((i > 0 and command[i - 1] in "<>" and kinds[i - 1] == LIVE)
            or (i + 1 < len(command) and command[i + 1] == ">" and kinds[i + 1] == LIVE))


_MEMBER = r"(?:\.|::)[A-Za-z_][A-Za-z0-9_]*\Z"
# `$x.Foo`, `${env:X}.a.Foo`, `'a'.Foo`: the whole word is one variable or one quoted string and its member chain.
# `$dir/a.txt` or `"a"b.Foo` is a plain argument in PowerShell, and the `(` after it runs a command.
# A variable may carry indexes first (`$lines[0].StartsWith`); an index holds no space, quote or parenthesis (#798).
VALUE_MEMBER = re.compile(r"""(?:\$(?:\{[^}]*\}|[A-Za-z_]\w*(?::\w+)?)(?:\[[^\]\s()'"]*\])*|'[^']*'|"[^"]*")"""
                          r"""(?:(?:\.|::)[A-Za-z_]\w*)+""")
TYPE_MEMBER = re.compile(r"\[\S*" + _MEMBER)  # `[math]::Max`: an expression only as the first word of its piece
MEMBER_CHAIN = re.compile(r"(?:(?:\.|::)[A-Za-z_]\w*)+")  # `.a.Trim`: after the `)` of a method call, at `chain`
CHAIN_MEMBER = re.compile(r"\.[A-Za-z_][A-Za-z0-9_]*\Z")  # `.Trim`: only right after a `)`


SPACES = re.compile(r"\s*")


def _method_call(text: str, start: int, end: int, after_close: bool, chain: int | None, last_space: int) -> bool:
    """True when `text[start:end]`, the piece so far, ends in the name of a method, so that the `(` after it opens method
    arguments (expressions) and not a command group. Checked against both PowerShell editions with a harmless command
    in the parentheses: these forms do not run it, while `Write-Output [math]::Max(cmd)`, `Write-Output a$x.Foo(cmd)`,
    `Get-Content $dir/a.txt(cmd)` and `git.exe(cmd)` do, because there the word before `(` is a plain argument or a
    command name. Words are split where the shell splits them, so a quoted string with a space is one word (#798).
    Bash reads none of these forms as a call; there a word followed by `(` is a syntax error and runs nothing.
    split_groups passes positions in the whole segment instead of copies, so that a long command costs linear time
    (#798): `chain` is where a member chain may start (after the `)` of a method call), `last_space` the last
    unquoted, unescaped whitespace before `end`."""
    if chain is not None and MEMBER_CHAIN.fullmatch(text, chain, end):
        return True  # `$_.Name.ToLower().Contains(`: members directly after the `)` of a method call
    if after_close:
        return bool(CHAIN_MEMBER.fullmatch(text, start, end))
    word = max(start, last_space + 1)  # the last word of the piece
    if word == end:
        return False  # an empty piece, or one that ends in a break
    first_word = bool(SPACES.fullmatch(text, start, word))
    return bool(VALUE_MEMBER.fullmatch(text, word, end) or (first_word and TYPE_MEMBER.fullmatch(text, word, end)))


def split_groups(segment: str, shell: str = "bash") -> list[str]:
    """The commands that grouping hides inside `segment`: split on ( ) { } outside quotes (#795).

    A subshell `(git push)`, a command group `{ git push; }`, a substitution `$(git push)` and a PowerShell script
    block `& { git push }` all run the command inside, but the segment's first word is then `(git`, `{` or the outer
    command. The `{` of `${name}` opens a variable name, not a command, so it does not split; `${ cmd; }` and
    `${| cmd; }` (Bash 5.3) run a command and do. The pieces are checked in addition to the whole segment, never
    instead of it, so this can only block more. Substitutions inside quotes are found by substitutions().

    Two pairs are not grouping and stay inside their word, with what they hold: the braces of `${name}`, and the
    parentheses of a method call (`$_.Contains('Remove-Item')`, `[math]::Max(1, 2)`, `(Get-Date).ToString('yyyy')`;
    see _method_call), whose arguments are expressions, not commands. A group or a script block inside either pair
    still splits (`$x.Where({ Remove-Item y })`, `$x.Foo((git push))`). `open` pairs each closer with its opener."""
    kinds, _ = lex(segment, shell)
    # The current piece is segment[start:i]: a character either stays in it or, as a splitting ( ) { }, ends it.
    pieces, start, open, after_close, chain, last_space = [], 0, [], False, None, -1
    for i, ch in enumerate(segment):
        if kinds[i] != LIVE:
            continue
        if ch.isspace():
            last_space = i
        elif ch in "({":
            if ch == "{":
                # An escaped `$` (`` `${git push}.Invoke() ``) is plain text: the braces then hold a script block.
                inert = (i > 0 and segment[i - 1] == "$" and kinds[i - 1] == LIVE and i + 1 < len(segment)
                         and not segment[i + 1].isspace() and segment[i + 1] != "|")
            else:
                inert = _method_call(segment, start, i, after_close, chain, last_space)
            open.append((ch, inert))
            if not inert:
                pieces.append(segment[start:i])
                start, after_close, chain = i + 1, False, None
        elif ch in ")}":
            # A closer that does not match the innermost opener is unbalanced text: it splits and leaves `open` alone.
            inert = bool(open) and open[-1][0] == "({"[")}".index(ch)] and open.pop()[1]
            if inert:
                chain = i + 1 if ch == ")" else chain  # a member chain may go on from here
            else:
                pieces.append(segment[start:i])
                start, chain = i + 1, None
                after_close = ch == ")"  # `(Get-Date).ToString(`: the piece that starts here may be `.name`
    pieces.append(segment[start:])
    return [p.strip() for p in pieces if p.strip()]


def substitutions(command: str, shell: str = "bash") -> list[str]:
    """The commands that `command` runs through a substitution a quote would hide (#795): `$(...)` inside double
    quotes in both shells, and in Bash `...` in backticks outside single quotes. The shell's own escapes decide
    (#798): in Bash `\\$(` and `\\`` start nothing and `\\'` outside quotes is a plain character; in PowerShell a
    backslash is plain text, the backtick is the escape character and backticks run nothing. Inside `$(...)` quotes
    are read as the shell reads them, so a quoted `)` does not end it. Each result is checked as a command of its
    own, so a substitution nested inside it is found on that pass."""
    return lex(command, shell)[1]


ANSI_ESCAPE = re.compile(r"\\(?:([abeEfnrtv\\'\"?])|([0-7]{1,3})|x([0-9A-Fa-f]{1,2})|u([0-9A-Fa-f]{1,4})"
                         r"|U([0-9A-Fa-f]{1,8})|c(.))", re.S)
ANSI_SIMPLE = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v"}


def _ansi_decode(body: str) -> str:
    """The text of a Bash `$'...'` string: its backslash escapes decoded as Bash decodes them."""
    def one(m: re.Match) -> str:
        simple, octal, hex2, u4, u8, ctrl = m.groups()
        if simple:
            return ANSI_SIMPLE.get(simple, simple)
        if ctrl:
            return chr(ord(ctrl) & 31)
        try:
            return chr(int(octal, 8) & 255 if octal else int(hex2 or u4 or u8, 16))
        except (ValueError, OverflowError):
            raise Blocked("an escape in a $'...' string could not be read; command could not be checked") from None
    # Bash ends the string at its first NUL (`\0`, `\c@`, `\x00`, `\u0000`, `\400`): `git $'push\0x'` runs `git push`.
    return ANSI_ESCAPE.sub(one, body).split("\x00", 1)[0]


def tokenize(segment: str, shell: str = "bash", *, input_files: list[str] | None = None) -> list[str]:
    """The words of `segment` as the shell passes them on: quotes and escape characters removed, comments dropped.
    In Bash the `$` of `$'...'` and `$"..."` goes too, and the escapes of `$'...'` are decoded (#799): otherwise
    `bash -c $'git push'` would hand `$git push` to the check of the inner command.
    When input_files is given, omit shell redirections from the arguments and collect their input file names."""
    kinds, _ = lex(segment, shell)
    rules = RULES[shell]
    redirect_chars = ">" if shell == "powershell" else "<>"
    tokens, current, has, i, n = [], [], False, 0, len(segment)
    while i < n:
        ch, kind = segment[i], kinds[i]
        if input_files is not None and kind == LIVE and ch in redirect_chars:
            # A live descriptor belongs to the redirection. An adjacent ordinary word is an argument.
            prefix = "".join(current)
            special_descriptor = "*" if shell == "powershell" else "&"
            descriptor = prefix == special_descriptor or (prefix.isascii() and prefix.isdigit())
            if shell in ("bash", "sh") and re.fullmatch(r"\{[A-Za-z_][A-Za-z0-9_]*\}", prefix):
                descriptor = True
            if (current or has) and not (descriptor and not has and all(k == LIVE for k in kinds[i - len(prefix):i])):
                tokens.append(prefix)
            current, has = [], False
            operator_start = i
            i += 1
            while i < n and kinds[i] == LIVE and segment[i] in redirect_chars + "&|":
                i += 1
            if i < n and kinds[i] == LIVE and segment[i] == "-" and segment[i - 2:i] == "<<":
                i += 1
            operator = segment[operator_start:i]
            while i < n and kinds[i] == LIVE and segment[i].isspace():
                i += 1
            target_start = i
            while i < n and not (kinds[i] == LIVE and (segment[i].isspace() or segment[i] in redirect_chars)):
                i += 1
            if operator in ("<", "<>"):
                input_files.extend(tokenize(segment[target_start:i], shell))
            continue
        if rules.ansi and kind == SYNTAX and ch in "'\"" and current and current[-1] == "$" \
                and kinds[i - 1] == LIVE and _odd_dollars(segment, i, kinds):
            current.pop()
            if ch == "'":
                end = i + 1
                while end < n and not (kinds[end] == SYNTAX and segment[end] == "'"):
                    end += 1
                current.append(_ansi_decode(segment[i + 1:end]))
                has, i = True, end + 1
                continue
        if kind == COMMENT:
            pass
        elif kind == SYNTAX:
            has = has or ch != rules.escape and ch != "\n"
        elif kind == LIVE and ch.isspace():
            if current or has:
                tokens.append("".join(current))
            current, has = [], False
        else:
            current.append(ch)
        i += 1
    if current or has:
        tokens.append("".join(current))
    return tokens


def _odd_dollars(text: str, i: int, kinds: list[int]) -> bool:
    """An odd run of live `$` right before i: `$'` and `$"` start a string, `$$'` is the process id and a quote."""
    dollars = 0
    while i - dollars > 0 and text[i - dollars - 1] == "$" and kinds[i - dollars - 1] == LIVE:
        dollars += 1
    return dollars % 2 == 1


def command_word(token: str) -> str:
    # PowerShell drops escaped whitespace before a command name: `` ` Remove-Item x`` runs Remove-Item (#799)
    word = token.strip().replace("\\", "/").rsplit("/", 1)[-1].lower()
    return word[:-4] if word.endswith(".exe") else word


def check_redirects(segment: str, shell: str = "bash") -> None:
    kinds, _ = lex(segment, shell)
    for i, ch in enumerate(segment):
        if ch != ">" or kinds[i] != LIVE:
            continue
        rest = segment[i + 1:].lstrip(">").lstrip(" \t")
        if rest.startswith("&"):
            # `>&2` and `>&-` duplicate or close a descriptor; `>& file` and `>&file` write stdout and stderr to a
            # file, so only a number or `-` is let through (#772). ASCII only: Bash reads neither other Unicode digits
            # nor other Unicode spaces that way, so `>&١` writes a file named ١.
            rest = rest[1:].lstrip(" \t")
            if re.match(r"([0-9]+|-)(?=$|[ \t\n;|&)<>])", rest):
                continue
        # Only the first word is the target: lex up to the first unquoted whitespace, not the whole rest of the segment
        # at every `>` (quadratic: 8,000 redirections took two minutes).
        start = len(segment) - len(rest)
        end = start
        while end < len(segment) and not (kinds[end] == LIVE and segment[end] in " \t\n"):
            end += 1
        try:
            words = tokenize(segment[start:end], shell)
        except Blocked:
            words = []
        fold = (lambda s: s) if shell in ("bash", "sh") else str.lower
        target = fold(words[0]) if words else ""
        spelled = fold(re.match(r"[^ \t\n;|&()<>]*", rest).group(0))
        if target not in SAFE_REDIRECT_TARGETS[shell] or spelled != target:
            raise Blocked(f"output redirection to a file ({target or 'unknown'})")


def check_git(args: list[str]) -> None:
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") else 1
    if i >= len(args):
        return
    sub, rest = args[i].lower(), args[i + 1:]
    if sub in GIT_WRITE:
        raise Blocked(f"git {sub}")
    if sub in GIT_SUBCOMMAND_WRITE and rest and rest[0].lower() in GIT_SUBCOMMAND_WRITE[sub]:
        raise Blocked(f"git {sub} {rest[0]}")
    if sub == "branch" and any(not (a in GIT_BRANCH_READ or a.split("=")[0] in GIT_BRANCH_READ) for a in rest if a.startswith("-")):
        raise Blocked("git branch with a modifying option")
    if sub == "branch" and rest and not any(a.startswith("-") for a in rest):
        raise Blocked("git branch <name> creates a branch")
    if sub == "tag" and rest and not any(a in ("-l", "--list", "-n", "--contains", "--points-at", "--sort", "--format") for a in rest):
        raise Blocked("git tag creates or deletes a tag")
    if sub == "config" and not any(a in ("--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin", "--show-scope") for a in rest):
        if len([a for a in rest if not a.startswith("-")]) >= 2 or any(a in ("--unset", "--unset-all", "--add", "--replace-all", "--remove-section", "--rename-section", "-e", "--edit") for a in rest):
            raise Blocked("git config write")
    if sub == "symbolic-ref" and len([a for a in rest if not a.startswith("-")]) >= 2:
        raise Blocked("git symbolic-ref write")


def check_gh(args: list[str]) -> None:
    words = [a.lower() for a in args if not a.startswith("-")]
    if len(words) >= 2 and words[0] in GH_WRITE and words[1] in GH_WRITE[words[0]]:
        raise Blocked(f"gh {words[0]} {words[1]}")
    if words and words[0] == "api":
        lowered = [a.lower() for a in args]
        if len(words) >= 2 and words[1] == "graphql":
            # GraphQL always POSTs; only a mutation writes.
            if any(re.search(r"\bmutation\b", a, re.IGNORECASE) for a in args):
                raise Blocked("gh api graphql mutation")
            return
        for j, a in enumerate(lowered):
            method = None
            if a in ("-x", "--method") and j + 1 < len(lowered):
                method = lowered[j + 1]
            elif a.startswith("--method="):
                method = a.split("=", 1)[1]
            elif a.startswith("-x") and len(a) > 2:
                method = a[2:]
            if method and method != "get":
                raise Blocked(f"gh api {method.upper()}")
        if any(a in ("-f", "-F", "--field", "--raw-field", "--input") or a.startswith(("--field=", "--raw-field=", "--input=")) for a in args) and not any(a in ("-X", "--method") for a in args):
            raise Blocked("gh api with fields defaults to POST")


ACTION_VALUE = re.compile(r"^[A-Za-z][A-Za-z0-9]*\Z")
DASHES = "–—―"  # en dash, em dash, horizontal bar: PowerShell also takes these as the leading dash of a parameter name


# After the name of a script in guard_write_actions only these characters may appear (plain ASCII text, space and tab).
# Everything PowerShell gives a meaning beyond "a word" is refused: comments (#, <# #>), the escape character (`),
# splatting and variables (@, $), subexpressions and script blocks ((), {}), statement and pipeline separators (; | &),
# redirection (< >), and every non-ASCII character (PowerShell also reads the curly quotes U+2018-U+201E as quotes). The
# leading dashes that PowerShell accepts besides "-" (DASHES) are folded to "-" first.
ACTION_ARG_FORBIDDEN = set("#`@$(){};|&<>")


def raw_tokens(text: str) -> list[str]:
    """Whitespace-separated tokens of text with their quotes kept. Inside '...' and "..." nothing is special; a doubled
    quote closes and reopens, which keeps one token, as PowerShell's '' and "" do. An unclosed quote is refused."""
    tokens, current, quote = [], [], None
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            current.append(ch)
        elif ch in " \t":
            if current:
                tokens.append("".join(current))
            current = []
        else:
            current.append(ch)
    if quote:
        raise Blocked("unbalanced quotes; command could not be checked")
    if current:
        tokens.append("".join(current))
    return tokens


def unquote(token: str) -> str:
    return token.replace("'", "").replace('"', "")


def check_action(script: str, segment: str, blocked: frozenset) -> None:
    """Block `script` (named in guard_write_actions) unless its arguments are plain and its -Action is readable and not
    one of `blocked` (lower case). An allowlist, not a PowerShell parser: when the arguments cannot be read without
    doubt, the call is blocked.

    1. Every character after the script's own token must be plain ASCII and not in ACTION_ARG_FORBIDDEN. That leaves only
       words, quotes, spaces and ordinary punctuation, which this tokenizer and PowerShell split the same way.
    2. A parameter name is a token that starts with a dash and holds no quote (PowerShell reads a quoted "-Action" as a
       value, not as a name), so a token that starts with a dash and contains a quote is refused.
    3. PowerShell binds a named parameter ignoring case, as `-Action X` or `-Action:X`, and accepts a unique prefix
       (`-Act X`); a prefix of -action counts as -Action. Exactly one is needed: none (the value could come by position
       or splatting), two, or a value that is not a plain name blocks. With -Action bound by name, a positional argument
       cannot bind to it any more."""
    text = "".join("-" if ch in DASHES else ch for ch in segment)
    toks = raw_tokens(text)
    pos, idx = 0, None
    for i, tok in enumerate(toks):
        pos = text.index(tok, pos) + len(tok)
        if command_word(unquote(tok)) == script:
            idx = i
            break
    if idx is None:
        raise Blocked(f"{script} (its arguments could not be located)")
    rest = text[pos:]
    bad = sorted({ch for ch in rest if ch in ACTION_ARG_FORBIDDEN or not (ch in "\t" or " " <= ch <= "~")})
    if bad:
        raise Blocked(f"{script} with {''.join(bad)!r} in its arguments (they cannot be checked; use plain words)")
    args = toks[idx + 1:]
    values = []
    for i, tok in enumerate(args):
        if tok in ("--%", "--"):
            raise Blocked(f"{script} with {tok} (arguments cannot be checked)")
        if not unquote(tok).startswith("-"):
            continue
        if "'" in tok or '"' in tok:
            raise Blocked(f"{script} with the quoted dash token {tok} (a name or a value? it cannot be told)")
        name, sep, value = tok.partition(":")
        name = name.lower()
        if len(name) < 2 or not "-action".startswith(name):
            continue
        if sep:
            values.append(unquote(value))
        else:
            values.append(unquote(args[i + 1]) if i + 1 < len(args) else "")
    if len(values) != 1:
        raise Blocked(f"{script} without exactly one readable -Action (a write action cannot be ruled out)")
    if not ACTION_VALUE.match(values[0]):
        raise Blocked(f"{script} -Action {values[0] or '(missing)'} is not a plain action name")
    if values[0].lower() in blocked:
        raise Blocked(f"{script} -Action {values[0]}")


# Commands that run the command after them, with the options that take a separate value (#804). `timeout` also takes
# a duration; `env` also takes NAME=value words. Without this, `timeout 20 git push` or `nice -n 5 git push` was read
# as the command `timeout` or `-n` and passed.
WRAPPERS = {
    "&": set(), ".": set(), "command": set(), "nohup": set(),
    "env": {"-u", "--unset", "-C", "--chdir"},
    "exec": {"-a"},
    "time": {"-f", "--format", "-o", "--output"},
    "nice": {"-n", "--adjustment"},
    "sudo": {"-u", "--user", "-g", "--group", "-h", "--host", "-p", "--prompt", "-C", "--close-from", "-D",
             "--chdir", "-r", "--role", "-t", "--type", "-U", "--other-user", "-T", "--command-timeout"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-a", "--arg-file", "-d", "--delimiter", "-E", "-I", "-L", "-n", "--max-args", "-P", "--max-procs",
              "-s", "--max-chars"},
}
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def strip_wrappers(tokens: list[str]) -> list[str]:
    """Drop leading NAME=value words and wrapper commands with their options, so that the command they run is the one
    checked: `timeout -k 5 20 git push`, `env -i A=1 git push` and `sudo -u x git push` all check `git push`."""
    i = 0
    while i < len(tokens):
        if ASSIGNMENT.match(tokens[i]):
            i += 1
            continue
        wrapper = command_word(tokens[i]) if tokens[i] not in ("&", ".") else tokens[i]
        if wrapper not in WRAPPERS:
            break
        i += 1
        while i < len(tokens) and tokens[i].startswith("-") and len(tokens[i]) > 1:
            option = tokens[i]
            i += 1
            if option == "--":
                break
            if wrapper == "command" and option in ("-v", "-V") or wrapper == "sudo" and option in ("-l", "--list"):
                return []  # `command -v rm` says where rm is, `sudo -l rm` whether it may run: neither runs it
            if option in WRAPPERS[wrapper]:  # a separate value follows (`-n 5`); `-n5` and `--signal=KILL` hold it
                i += 1
        if wrapper == "timeout" and i < len(tokens):
            i += 1  # the duration
    return tokens[i:]


def python_entry(options: Iterator[str]) -> tuple[str, str]:
    """Read the interpreter options up to the script, module, inline code or stdin."""
    for arg in options:
        if arg == "--":
            return "script", next(options, "")
        if arg == "-" or not arg.startswith("-"):
            return "script", arg
        if arg.startswith("--"):
            if arg == "--check-hash-based-pycs":
                next(options, None)
            continue
        # In a short option group, these options take the rest of the word or the next argument.
        for i, flag in enumerate(arg[1:], start=1):
            if flag in "cmWX":
                value = arg[i + 1:] or next(options, "")
                if flag in "cm":
                    return flag, value
                break
    return "", ""


def check_python_inline(code: str) -> None:
    code = code.lower()
    for name in PAID_PYTHON_HELPERS:
        if name in code:
            raise Blocked(name + ".py")


def pdb_target_options(options: Iterator[str]) -> Iterator[str]:
    """Pdb claims its debugger commands even when they follow the debug target."""
    target_options = []
    for arg in options:
        if arg == "--":
            target_options.append(arg)
            target_options.extend(options)
            break
        name, equals, value = arg.partition("=")
        # Older pdb uses getopt, which also consumes unique prefixes of --command.
        if arg == "-c" or (name.startswith("--") and "--command".startswith(name)):
            check_python_inline(value if equals else next(options, ""))
        elif arg.startswith("-c"):
            check_python_inline(arg[2:])
        else:
            target_options.append(arg)
    return iter(target_options)


def python_runner_entry(runner: str, options: Iterator[str]) -> tuple[str, str]:
    """Read a runner up to its own target, leaving the target's arguments alone."""
    if runner == "coverage" and next(options, "") != "run":
        return "", ""
    short_values, long_values = PYTHON_PROGRAM_RUNNERS[runner]
    kind = "script"
    for arg in options:
        if arg == "--":
            return kind, next(options, "")
        if arg == "-" or not arg.startswith("-"):
            return kind, arg
        if arg.startswith("--"):
            option, equals, _ = arg.partition("=")
            takes_value = option in long_values or (runner != "pdb" and
                                                     any(flag.startswith(option) for flag in long_values))
            if takes_value:
                if not equals:
                    next(options, "")
                if option == "--pid":
                    return "", ""
            elif runner in ("trace", "coverage") and "--module".startswith(arg):
                kind = "m"
            continue
        for i, flag in enumerate(arg[1:], start=1):
            if flag == "m" and runner != "trace":
                if runner == "pdb":
                    return "m", arg[i + 1:] or next(options, "")
                kind = "m"
            if flag in short_values:
                if i == len(arg) - 1:
                    next(options, "")
                if flag == "p":
                    return "", ""
                break
    return "", ""


def check_paid_helper_arguments(args: list[str], *, scan_inline: bool) -> None:
    """Conservatively check the arguments of a wrapper that can run another program."""
    paths = [command_word(a) for a in args]
    modules = [command_word(m) for flag, m in zip(args, args[1:]) if flag in ("-m", "--module")]
    modules += [command_word(a[2:]) for a in args if a.startswith("-m") and len(a) > 2]
    inline = [code.lower() for flag, code in zip(args, args[1:]) if scan_inline
              and flag.startswith("-") and not flag.startswith("--") and flag.endswith("c")]
    for name in PAID_PYTHON_HELPERS:
        if name + ".py" in paths or name in modules or any(name in c for c in inline):
            raise Blocked(name + ".py")


def check_segment(segment: str, depth: int = 0, extra: frozenset = frozenset(), actions: dict | None = None,
                  shell: str = "bash") -> None:
    actions = actions or {}
    if depth > 3:
        raise Blocked("nested shell depth exceeded")
    tokens = strip_wrappers(tokenize(segment, shell))
    word, args = (command_word(tokens[0]), tokens[1:]) if tokens else ("", [])
    lowered = segment.lower()
    # Scripts count only when executed (directly or through an interpreter),
    # so reading or searching for them stays allowed.
    executed = [word] if word.endswith((".ps1", ".py", ".sh")) else []
    if word in PYTHON_INTERPRETERS:
        # Preserve the separate exact-name check for paid scripts, including targets passed to an unknown wrapper.
        paths = {command_word(arg) for arg in args}
        # A redirection can appear before the program; it is consumed by the shell, not by Python.
        input_files: list[str] = []
        if "<" in segment or ">" in segment:
            args = strip_wrappers(tokenize(segment, shell, input_files=input_files))[1:]
        # Keep refusing paid helpers named as input files: Python can execute code from stdin.
        paths.update(map(command_word, input_files))
        for name in PAID_PYTHON_HELPERS:
            if name + ".py" in paths:
                raise Blocked(name + ".py")
        if word == "py":
            if args and args[0].lower() == "exec":  # Python install manager subcommand
                args = args[1:]
            if args and args[0].startswith("-V:"):
                args = args[1:]
        options = iter(args)
        kind, entry = python_entry(options)
        while kind == "m" and command_word(entry) in PYTHON_PROGRAM_RUNNERS:
            runner = command_word(entry)
            if runner == "pdb":
                options = pdb_target_options(options)
            kind, entry = python_runner_entry(runner, options)
        if kind == "script":
            executed.append(command_word(entry))
        elif kind == "m" and command_word(entry) in PAID_PYTHON_HELPERS:
            raise Blocked(command_word(entry) + ".py")
        elif kind == "c":
            check_python_inline(entry)
    elif word in INTERPRETERS:
        # Keep checking wrappers such as uv run, whose options or wrapped interpreter can precede a paid helper.
        # Shell -c bodies are checked recursively below.
        check_paid_helper_arguments(args, scan_inline=word in ("uv", "uvx"))
        executed += [command_word(a) for a in args if not a.startswith("-")][:1]
        executed += [command_word(args[i + 1]) for i, a in enumerate(args[:-1])
                     if a.lower() in ("-file", "-f")]
    # Before the redirect check, so that a comment such as <# ... #> is refused for what it is.
    for script in executed:
        if script in WRITE_SCRIPTS or script in extra:
            raise Blocked(script)
        if script in actions:
            if actions[script] is None:
                raise Blocked(f"{script} (blocked outright by guard_write_actions)")
            check_action(script, segment, actions[script])
    check_redirects(segment, shell)
    if not tokens:
        return
    # The raw text and the words as the shell passes them: `mark-rev''iew` and `$'appl\x79'` hide from the first.
    words = [a.lower() for a in args]
    for script in executed:
        if script == "guard.ps1" and ("mark-review" in lowered or any("mark-review" in a for a in words)):
            raise Blocked("guard.ps1 mark-review")
        if script == "sync-project-template.py" and (re.search(r"\bapply\b", lowered) or "apply" in words):
            raise Blocked("sync-project-template.py apply")
    if word in SHELLS:
        for j, a in enumerate(args):
            if a.lower() in INLINE_FLAGS and j + 1 < len(args):
                inner = " ".join(args[j + 1:]) if word == "cmd" else args[j + 1]
                # `sh` is Bash in Git Bash and macOS but dash (no `$'...'`) on Debian: read it both ways.
                # The two differ only where `$'` or `$"` appears; otherwise one reading is enough (time).
                both = SHELL_OF[word] == "sh" and re.search(r"\$['\"]", inner)
                for each in ("bash", "sh") if both else ("bash",) if SHELL_OF[word] == "sh" else (SHELL_OF[word],):
                    check_command(inner, depth + 1, extra, actions, each)
                return
        return
    if word == "git":
        check_git(args)
    elif word == "gh":
        check_gh(args)
    elif word in FILE_WRITERS:
        raise Blocked(word)
    elif word in ("sed", "perl") and any(a == "-i" or a.startswith(("-i", "--in-place")) for a in args):
        raise Blocked(f"{word} in-place edit")


def check_command(command: str, depth: int = 0, extra: frozenset = frozenset(), actions: dict | None = None,
                  shell: str | None = None) -> None:
    """Check `command` as `shell` ("bash", "powershell" or "cmd") reads it. Without a shell (a caller that does not
    know which tool ran the command) it is checked as both Bash and PowerShell and refused if either refuses.

    Each command is also read twice, with comments and without, and refused if either reading refuses (#799). Where a
    `#` starts a comment depends on far more of the shell's grammar than a lexer models (`$(true)#`, `${x:- #}`,
    `(( 1 #))`, a `=~` regex, extglob switched on earlier), and a wrong guess either way lets a quote or the comment
    itself hide a command. This covers a command whose `#` are all of one kind; it does not cover a real comment and
    a misread `#` together (`echo hi # '` then `echo ${#x};git push # '` on the next line passes both readings, as it
    passed before #798). The reading without comments is
    how the guard read before #798, so an odd quote in a real comment (`echo hi # it's`) is refused, as it was then,
    and so are PowerShell comments."""
    if DEADLINE.get() is None:
        # The readings multiply with nesting (with and without comments, sh); a huge nested command could outlast the
        # hook's timeout, whose outcome is not a block. Past the budget the command is refused instead.
        budget = DEADLINE.set(time.monotonic() + CHECK_BUDGET_S)
        try:
            return check_command(command, depth, extra, actions, shell)
        finally:
            DEADLINE.reset(budget)
    if shell is None:
        for each in ("bash", "powershell"):
            check_command(command, depth, extra, actions, each)
        return
    # At every depth: a `bash -c` inside a comment-free outer command may need the reading with comments, and the other
    # way round (at most 2 ** 4 readings, as nesting stops at depth 3).
    for comments in (True, False):
        token = READ_COMMENTS.set(comments)
        try:
            _check_command(command, depth, extra, actions, shell)
        finally:
            READ_COMMENTS.reset(token)


# The delimiter word of a here-document whose body Bash reads as plain text: quoted in part or whole (`'EOF'`, `"EOF"`,
# `\EOF`, `'E'OF`), with only plain characters besides, and no `$`, backtick or backslash inside double quotes (#800).
HERE_DOC_WORD = re.compile(r"""(?:'[^']*'|"[^"$`\\]*"|\\[A-Za-z0-9_.+-]|[A-Za-z0-9_.+-])+""")
HERE_DOC_PIECE = re.compile(r"""'([^']*)'|"([^"]*)"|\\(.)|(.)""")
# Commands that read a here-document body only as data. A body is cut only when every command on the operator's line
# is one of these (after wrappers such as `timeout`): any other command may run its input, as `bash -s`, `xargs`,
# `env -S 'bash -s'`, `parallel`, `at` or `make -f -` do (Argus and review on #810), and no list of those is complete.
# The interpreters run the body as their own language, which the guard leaves alone anyway (`python -c` can write).
HERE_DOC_READERS = {"cat", "tac", "grep", "egrep", "fgrep", "rg", "wc", "head", "tail", "sort", "uniq", "cut", "tr", "nl",
                    "column", "diff", "cmp", "comm", "jq", "base64", "md5sum", "sha1sum", "sha256sum", "od", "xxd",
                    "python", "python3", "py", "node"}
# Every command before the body, back to the start, must be one of these: any other command could give `cat` another
# meaning first (`eval 'cat(){ bash; }'`, `function cat if ...`, `declare 'BASH_ALIASES[cat]=bash'`, review on #810).
HERE_DOC_PLAIN = HERE_DOC_READERS | {"cd", "echo", "pwd", "true", ":", "git"}
# Options that make a reader run a program: `env -S'bash -s'` (strip_wrappers drops it), `sort --compress-program`.
# GNU tools accept any unambiguous prefix of a long option (`env --sp=`, `sort --compress-prog=`) and a short option
# inside a cluster (`env -iS`); review on #810.
HERE_DOC_RUNS = ("--split-string", "--compress-program")
HERE_DOC_RUNS_SHORT = re.compile(r"-[A-Za-z]*S")


def _runs_program(word: str) -> bool:
    """True if `word` is, or may abbreviate, an option in HERE_DOC_RUNS, or is a short cluster holding `-S`."""
    if word.startswith("--"):
        name = word.split("=", 1)[0]
        return len(name) > 2 and any(option.startswith(name) for option in HERE_DOC_RUNS)
    return bool(HERE_DOC_RUNS_SHORT.match(word))
# Wrappers whose options strip_wrappers cannot all tell apart (`xargs --process-slot-var cat sh -c`, `sudo -R cat
# bash`) or that hand the body on: with one of them the body is not cut.
HERE_DOC_NO_WRAPPERS = {"xargs", "sudo", "doas", "exec", "."}
# In the body of an unquoted here-document: a backtick, a backslash, or a `$` not followed by a name character.
HERE_DOC_EXPANDS = re.compile(r"[`\\]|\$(?![A-Za-z0-9_])")


def _live_pieces(command: str, kinds: list[int], a: int, b: int, seps: str) -> list[tuple[int, int]]:
    """The non-blank pieces of command[a:b] between separators in `seps` that are shell syntax (a `&` of a redirection
    such as `>&2` does not separate)."""
    pieces, start = [], a
    for p in range(a, b + 1):
        if p == b or kinds[p] == LIVE and command[p] in seps and not (command[p] == "&" and _is_redirect_amp(
                command, p, kinds, "bash")):
            if command[start:p].strip():
                pieces.append((start, p))
            start = p + 1
    return pieces


def cut_here_documents(command: str, shell: str) -> str:
    """`command` without the bodies of its Bash here-documents that run no command (#800).

    Bash reads the body of a here-document whose delimiter is quoted as plain text; with an unquoted delimiter
    (`<<EOF`) it expands `$`, `$(...)`, backticks and backslashes but still treats quotes, `>` and `;` as text. So
    checking a body as commands refused read-only commands (`python - <<'EOF'` with `if a >= b:` in the body was read
    as a redirection) and let an odd quote in it hide the next command. An unquoted body is cut only when every `$` in
    it starts a plain name and it has no backtick or backslash (HERE_DOC_EXPANDS), so nothing in it runs and no
    backslash-newline moves its end. A body is cut
    only where the guard can tell exactly where Bash ends it: the `<<` or `<<-` is shell syntax, no `(`, `[` or `{`
    that is shell syntax comes before it (so it is not in `$((...))`, `a[...]`, `${...}`, `$(...)`, a group or a
    `case` branch, even when closers seem to balance them), the command up to the end of its line has no substitution,
    the line has no `$`, backtick, `(`, `[` or `{` that is shell syntax (Bash reads the body only after a `$(` or `<(`
    opened there has ended), its delimiter word matches HERE_DOC_WORD, and the line ends at a newline that is shell
    syntax. The body must also stay data: the command that holds the operator (after wrappers such as `timeout`) is
    in HERE_DOC_READERS and every other command from the start of `command` to the end of the line in HERE_DOC_PLAIN,
    so `bash -s`, `xargs`, `done` (the end of a loop that reads it), a pipe into a shell, or an `eval`, `function` or
    `declare` that gives `cat` another meaning keep the body checked as commands; no word is or abbreviates one of
    HERE_DOC_RUNS or is a short option cluster with `S` (`_runs_program`), or is one of HERE_DOC_NO_WRAPPERS (`xargs ... cat`, `sudo ... cat`); no file descriptor number comes before the `<<` (`exec 3<<'EOF'` keeps it for a later
    `bash <&3`); and the line does not end in `|` or `|&` (which pipes it into the line after the body). The body
    then runs to the first line equal to the delimiter (after leading tabs for `<<-`; no other whitespace is
    stripped, as in Bash), or to the end. A command with a `\\r` is not cut at all: Git Bash ends a body at `EOF\\r`,
    Linux Bash does not. At the first `<<` that fails any of these the cutting stops, and the rest is read as before
    #800."""
    if "\r" in command:
        return command
    resume, n = 0, len(command)
    while True:
        j = command.find("<<", resume)
        newline = command.find("\n", j)
        if j < 0 or newline < 0:
            return command
        # Lex only up to the end of the operator's line: the body below it is not shell syntax (an odd quote in it
        # would make the whole command look unbalanced). Text before a position lexes the same in a prefix.
        try:
            kinds, subs = lex(command[:newline + 1], shell)
        except Blocked:
            return command
        if not (kinds[j] == kinds[j + 1] == LIVE and command[j + 2:j + 3] != "<" and (j == 0 or command[j - 1] != "<")):
            resume = j + 2  # quoted, in a comment, or a `<<<` here-string
            continue
        if time.monotonic() > DEADLINE.get():
            raise Blocked(f"checking took over {CHECK_BUDGET_S} s; command could not be checked")
        # No construct may be open around the operator or on its line, not even one that looks closed again: a lone
        # `]` or `}` is a plain word character to Bash (`echo ] ]; echo $((1<<'2'))` is a shift), and Bash reads the
        # body only after a `$(`, `<(` or `${` opened on the operator's line has ended (#800 review).
        line_start = command.rfind("\n", 0, j) + 1
        if subs or any(kind == LIVE and ch in "([{" for ch, kind in zip(command[:j], kinds)) \
                or any(kinds[p] == LIVE and command[p] in "([{$`" for p in range(line_start, newline)):
            return command
        k = j + 2
        strip_tabs = command[k:k + 1] == "-"
        k += strip_tabs
        while k < n and command[k] in " \t":
            k += 1
        word = HERE_DOC_WORD.match(command, k)
        if not word or word.end() < n and command[word.end()] not in " \t\n;&|<>)":
            return command
        expands = not any(c in "'\"\\" for c in word.group(0))
        delimiter = "".join(next(p for p in m.groups() if p is not None) for m in HERE_DOC_PIECE.finditer(word.group(0)))
        if not delimiter or kinds[newline] != LIVE:
            return command
        # The body is input, but stays data only if what reads it is a HERE_DOC_READERS command: a `3<<` keeps it for a
        # later `bash <&3`, and a line ending in `|` pipes it into the line after the body.
        tail = "".join(command[p] for p in range(line_start, newline) if kinds[p] != COMMENT).rstrip(" \t")
        if j > 0 and command[j - 1].isdigit() or tail.endswith(("|", "|&")):
            return command
        # Every command from the start to the end of this line, split at `;`, `&`, `|` and newlines: those in the
        # pipeline that reads the body must be HERE_DOC_READERS, the others at least HERE_DOC_PLAIN.
        try:
            pieces = [("".join(command[p] if kinds[p] != COMMENT else " " for p in range(a, b)), a <= j < b)
                      for a, b in _live_pieces(command, kinds, 0, newline, ";|&\n")]
            words = [(tokenize(re.sub(r"<<-?", " ", text), shell), holds) for text, holds in pieces]
        except Blocked:
            return command
        commands = [(strip_wrappers(tokens), holds) for tokens, holds in words]
        if any(_runs_program(w) or command_word(w) in HERE_DOC_NO_WRAPPERS for tokens, _ in words
               for w in tokens) or not any(h for _, h in commands) \
                or any(not c or command_word(c[0]) not in (HERE_DOC_READERS if h else HERE_DOC_PLAIN)
                       for c, h in commands):
            return command
        end, line = n, newline + 1
        while line <= n:
            stop = command.find("\n", line)
            stop = n if stop < 0 else stop
            text = command[line:stop]
            if (text.lstrip("\t") if strip_tabs else text) == delimiter:
                end = min(stop + 1, n)
                break
            line = stop + 1
        # With an unquoted delimiter Bash expands the body: `$(...)` and backticks run commands, `${y@P}` or an
        # arithmetic context can run a `$(...)` stored in a variable (review on #800), and a backslash before a newline
        # joins two lines (so the delimiter line may not end the body). Quotes, `>` and `;` stay text. Such a body is
        # cut only when its `$` signs all start a plain name (`$HOME`, `$1`) and it has no backtick or backslash;
        # otherwise it is read as commands, as before.
        if expands and (HERE_DOC_EXPANDS.search(command, newline + 1, end)):
            return command
        command = command[:newline + 1] + command[end:]
        resume, n = word.end(), len(command)


def _check_command(command: str, depth: int, extra: frozenset, actions: dict | None, shell: str) -> None:
    if time.monotonic() > DEADLINE.get():
        raise Blocked(f"checking took over {CHECK_BUDGET_S} s; command could not be checked")
    if ODD_BREAKS.search(command):
        raise Blocked("a lone carriage return or another unusual line break; command could not be checked")
    if shell in ("bash", "sh"):
        command = cut_here_documents(command, shell)
    _, subs = lex(command, shell)
    for segment in split_segments(command, shell):
        check_segment(segment, depth, extra, actions, shell)
        pieces = split_groups(segment, shell)
        if pieces != [segment]:
            for piece in pieces:
                check_segment(piece, depth, extra, actions, shell)
    for inner in subs:
        if depth >= 3:
            raise Blocked("nested shell depth exceeded")
        check_command(inner, depth + 1, extra, actions, shell)


def project_params(payload: dict) -> dict:
    """params of the project's .mercury/config.json ($CLAUDE_PROJECT_DIR, else the payload's cwd); {} when unreadable."""
    base = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    try:
        params = json.loads((Path(base) / ".mercury" / "config.json").read_text(encoding="utf-8"))["params"]
        return params if isinstance(params, dict) else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


SCRIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:sh|ps1|py)\Z")  # as SCRIPT_RE in tools/mercury.py


def plain_name(n) -> bool:
    return isinstance(n, str) and bool(n) and "/" not in n and "\\" not in n


def extra_scripts(payload: dict, params: dict | None = None) -> frozenset:
    """Lower-cased script names from params.guard_write_scripts of the project's .mercury/config.json."""
    names = (project_params(payload) if params is None else params).get("guard_write_scripts")
    if isinstance(names, list) and all(plain_name(n) for n in names):
        return frozenset(n.lower() for n in names)
    return frozenset()


def write_actions(payload: dict, params: dict | None = None) -> dict:
    """{script: frozenset of blocked actions}, lower case, from params.guard_write_actions. A script whose action list is
    malformed maps to None, which blocks every call of it (fail closed). A list of script names (the shape of
    guard_write_scripts, written under the wrong key) blocks those scripts outright too. Any other value that is not an
    object is ignored, like a malformed guard_write_scripts (tools/mercury.py refuses both on every check and apply)."""
    value = (project_params(payload) if params is None else params).get("guard_write_actions")
    out = {}
    if isinstance(value, list) and value and all(isinstance(n, str) and SCRIPT_NAME.match(n) for n in value):
        return {n.lower(): None for n in value}
    if not isinstance(value, dict):
        return out
    for script, acts in value.items():
        if not plain_name(script):
            continue
        ok = isinstance(acts, list) and acts and all(isinstance(a, str) and ACTION_VALUE.match(a) for a in acts)
        out[script.lower()] = frozenset(a.lower() for a in acts) if ok else None
    return out


TOOL_SHELL = {"Bash": "bash", "PowerShell": "powershell"}  # the hook payload's tool_name decides the shell (#798)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    command = (payload.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0
    outcome: list[BaseException | None] = []

    def run() -> None:
        try:
            params = project_params(payload)
            check_command(command, extra=extra_scripts(payload, params), actions=write_actions(payload, params),
                          shell=TOOL_SHELL[payload["tool_name"]])
            outcome.append(None)
        except BaseException as exc:  # noqa: BLE001 - handed to the main thread below
            outcome.append(exc)

    # A watchdog, so that no slow step anywhere in the check outlasts the hook's own timeout (a timed-out hook is
    # not known to block): past the budget the hook blocks. A big stack keeps deep nesting a RecursionError.
    try:
        threading.stack_size(64 * 1024 * 1024)
        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(CHECK_BUDGET_S + 5)
    except BaseException as exc:  # noqa: BLE001 - no thread (memory limits): block, do not exit 1
        outcome[:] = [exc]
        worker = None
    if worker is not None and worker.is_alive():
        print(f"Blocked by the read-only agent guard (Mercury): checking took over {CHECK_BUDGET_S + 5} s; "
              "the command could not be checked. This agent must not write; report the needed change instead.",
              file=sys.stderr, flush=True)
        os._exit(2)
    try:
        if outcome[0] is not None:
            raise outcome[0]
    except Blocked as exc:
        print(f"Blocked by the read-only agent guard (Mercury): {exc}. "
              "This agent must not write; report the needed change instead.", file=sys.stderr)
        return 2
    except BaseException as exc:  # noqa: BLE001 - any failure to read the command must block, not let it run (exit 1 does)
        print(f"Blocked by the read-only agent guard (Mercury): the command could not be checked "
              f"({type(exc).__name__}). This agent must not write; report the needed change instead.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
