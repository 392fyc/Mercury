"""PreToolUse guard for read-only subagents (Mercury #628, packaged by #682).

Registered in the `hooks` frontmatter of the read-only `mercury-*` Claude roles (through readonly_guard.sh).
Subagent `permissionMode: plan` is ignored when the main conversation runs in bypassPermissions, acceptEdits
or auto mode, so this hook is the effective write guard. It blocks repository, GitHub and filesystem writes in
Bash and PowerShell commands by exiting 2. It is best-effort, not an OS sandbox: arbitrary interpreters
(python -c, node -e) can still write.

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

import json
import os
import re
import sys
from pathlib import Path

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
)
SHELLS = {"bash", "sh", "zsh", "powershell", "pwsh", "cmd"}
INTERPRETERS = {"python", "python3", "py", "pwsh", "powershell", "bash", "sh", "uv", "uvx", "node"}
INLINE_FLAGS = {"-c", "-command", "/c", "-commandwithargs"}
SAFE_REDIRECT_TARGETS = {"/dev/null", "$null", "nul", "null"}


class Blocked(Exception):
    pass


def split_segments(command: str) -> list[str]:
    """Split on ; && || | & and newlines outside quotes."""
    segments, current, quote, i = [], [], None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"' and i + 1 < len(command):
                current.append(command[i + 1])
                i += 1
        elif ch in "'\"":
            quote = ch
            current.append(ch)
        elif ch in ";|\n" or (ch == "&" and not _is_redirect_amp(command, i)):
            segments.append("".join(current))
            current = []
            if command[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            current.append(ch)
        i += 1
    if quote:
        raise Blocked("unbalanced quotes; command could not be checked")
    segments.append("".join(current))
    return [s.strip() for s in segments if s.strip()]


def _is_redirect_amp(command: str, i: int) -> bool:
    return (i > 0 and command[i - 1] in "<>") or (i + 1 < len(command) and command[i + 1] == ">")


def tokenize(segment: str) -> list[str]:
    tokens, current, quote, has = [], [], None, False
    for ch in segment:
        if quote:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
        elif ch in "'\"":
            quote, has = ch, True
        elif ch.isspace():
            if current or has:
                tokens.append("".join(current))
            current, has = [], False
        else:
            current.append(ch)
    if current or has:
        tokens.append("".join(current))
    return tokens


def command_word(token: str) -> str:
    word = token.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return word[:-4] if word.endswith(".exe") else word


def check_redirects(segment: str) -> None:
    quote = None
    for i, ch in enumerate(segment):
        if quote:
            quote = None if ch == quote else quote
            continue
        if ch in "'\"":
            quote = ch
            continue
        if ch != ">":
            continue
        rest = segment[i + 1:].lstrip(">").lstrip()
        if rest.startswith("&"):
            continue
        target = tokenize(rest)[0].lower() if tokenize(rest) else ""
        if target not in SAFE_REDIRECT_TARGETS:
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


def check_segment(segment: str, depth: int = 0, extra: frozenset = frozenset(), actions: dict | None = None) -> None:
    actions = actions or {}
    if depth > 3:
        raise Blocked("nested shell depth exceeded")
    tokens = tokenize(segment)
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    while tokens and tokens[0] in ("&", ".", "env", "command", "exec", "time", "nice", "sudo"):
        tokens = tokens[1:]
    word, args = (command_word(tokens[0]), tokens[1:]) if tokens else ("", [])
    lowered = segment.lower()
    # Scripts count only when executed (directly or through an interpreter),
    # so reading or searching for them stays allowed.
    executed = [word] if word.endswith((".ps1", ".py", ".sh")) else []
    if word in INTERPRETERS:
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
    check_redirects(segment)
    if not tokens:
        return
    for script in executed:
        if script == "guard.ps1" and "mark-review" in lowered:
            raise Blocked("guard.ps1 mark-review")
        if script == "sync-project-template.py" and re.search(r"\bapply\b", lowered):
            raise Blocked("sync-project-template.py apply")
    if word in SHELLS:
        for j, a in enumerate(args):
            if a.lower() in INLINE_FLAGS and j + 1 < len(args):
                check_command(" ".join(args[j + 1:]) if word == "cmd" else args[j + 1], depth + 1, extra, actions)
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


def check_command(command: str, depth: int = 0, extra: frozenset = frozenset(), actions: dict | None = None) -> None:
    for segment in split_segments(command):
        check_segment(segment, depth, extra, actions)


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
    try:
        params = project_params(payload)
        check_command(command, extra=extra_scripts(payload, params), actions=write_actions(payload, params))
    except Blocked as exc:
        print(f"Blocked by the read-only agent guard (Mercury): {exc}. "
              "This agent must not write; report the needed change instead.", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
