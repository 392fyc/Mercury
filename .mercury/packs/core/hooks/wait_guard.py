"""PreToolUse guard against waits without a deadline (Mercury #706).

Registered in the `hooks` frontmatter of every Claude role Mercury generates (through wait_guard.sh). A local Claude Code
session puts no time limit on background commands, and a foreground command that reaches its timeout moves to the
background instead of stopping, so a wait loop whose condition never comes true runs until a person notices.

The guard blocks (exit 2) a Bash or PowerShell command that contains a wait without a deadline:
- a loop that sleeps in its condition or body: sh `while|until <cond>; do ... sleep ... done`, PowerShell `while (<cond>) { ... }` and
  `do { ... } while|until (<cond>)` with Start-Sleep; or
- a command that follows or watches until stopped: `tail -f`, `inotifywait -m`, `Get-Content -Wait`, `journalctl -f`,
  `docker|kubectl logs -f`, `gh ... --watch`, `gh run watch`, `watch`, `sleep infinity`, `Wait-Job|Wait-Process`
  without -Timeout.
- an endless `for ((;;))` or `for (;;)` loop.
A wait has a deadline when its top-level command is `timeout <duration> ...` with a duration above 0 (Bash tool only: in PowerShell `timeout`
is Windows' timeout.exe, which waits instead of bounding a command), or, for a loop, when its condition reads the clock
($SECONDS, `date +%s`, Get-Date, Stopwatch .Elapsed, [datetime]::Now), counts up to a number, or reads input (`read`).
It reads text, not behavior: a deadline that is present but wrong passes, and an unusual bounded loop may be refused.
The accepted forms are listed in `.mercury/packs/core/docs/mercury-task-contract.md` (Waiting on long-running work).
"""
from __future__ import annotations

import json
import re
import sys

W = r"(?<![\w$.-])"  # start of a word that is not part of a name, variable, member or option
SLEEP = re.compile(W + r"(?:sleep|start-sleep)(?![\w-])|\[(?:system\.)?threading\.thread\]::sleep", re.IGNORECASE)
SH_LOOP = re.compile(W + r"(until|while)\b", re.IGNORECASE)  # then `<cond>; do <body> done`, found by SH_DO and SH_DONE
SH_DO = re.compile(r"[;\n]\s*do\b")
SH_DONE = re.compile(W + r"done\b")
PS_WHILE = re.compile(W + r"while\s*\(", re.IGNORECASE)  # the condition runs to the `)` before the next `{`
PS_DO = re.compile(W + r"do\s*\{(.*?)\}\s*(?:while|until)\s*\(((?:[^()]|\([^()]*\))*)\)", re.IGNORECASE | re.DOTALL)
# Follow and watch commands: (command, test on the text after it up to the end of its top-level command).
FOLLOW_RULES = [
    ("tail", lambda r: re.search(r"(?<!\S)(?:-[A-Za-z]*[fF][A-Za-z]*|--follow\S*)(?![\w-])", r)),
    ("inotifywait", lambda r: re.search(r"(?<!\S)(?:-[A-Za-z]*m[A-Za-z]*|--monitor)(?![\w-])", r)),
    ("get-content|gc|cat|type", lambda r: re.search(r"(?<!\S)-wait(?![\w-])", r, re.IGNORECASE)),
    ("journalctl", lambda r: re.search(r"(?<!\S)(?:-[A-Za-z]*f[A-Za-z]*|--follow)(?![\w-])", r)),
    ("docker|podman|kubectl", lambda r: (lambda m: m and re.search(r"(?<!\S)(?:-f|--follow)(?![\w-])", r[m.end():]))(
        re.search(r"(?<!\S)logs(?!\S)", r))),
    ("gh", lambda r: re.search(r"(?<!\S)--watch(?![\w-])", r) or re.match(r"\s*run\s+watch(?![\w-])", r)),
    ("sleep", lambda r: re.match(r"\s*(?:inf|infinity)(?![\w-])", r)),
    ("wait-job|wait-process", lambda r: not re.search(r"(?<!\S)-timeout(?![\w-])", r, re.IGNORECASE)),
]
# Each name is tested on the rest of its top-level command; the span is captured in a lookahead, so a later name in
# the same command (`docker exec web tail -f x`) is tested too. A name may carry a path and `.exe` (`/usr/bin/tail`,
# `tail.exe`); a name followed by a path character (`/var/lib/docker/x`, `tail.py`) is no name.
FOLLOW = re.compile(W + r"(" + "|".join(name for name, _ in FOLLOW_RULES) + r")(?:\.exe)?(?![\w./\\-])(?=([^|;&\n]*))",
                    re.IGNORECASE)
WATCH = re.compile(r"(?:^|(?<=[;&|(\n]))\s*watch\s")
FOR_EVER = re.compile(W + r"for\s*\(\(\s*;\s*;\s*\)\)|" + W + r"for\s*\(\s*;\s*;\s*\)", re.IGNORECASE)
# The top-level command `timeout [options] <duration> ...` (coreutils; gtimeout on macOS). Case-sensitive, as in sh.
TIMEOUT = re.compile(
    r"\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*g?timeout(?:\s+(?:-[sk]\s+\S+|--(?:signal|kill-after)\s+\S+|-\S+))*"
    r"\s+(?:0*[1-9]\d*(?:\.\d+)?|0*\.\d*[1-9]\d*)[smhd]?\s")  # a duration of 0 means no limit
CLOCK = re.compile(r"\$\{?SECONDS\b|" + W + r"date\s+\+%s|get-date|stopwatch|\.elapsed|\[datetime\]::(?:utc)?now",
                   re.IGNORECASE)
COUNT = re.compile(r"(?:-lt|-le|-gt|-ge|-ne|<=?|>=?)\s*\d+|\d+\s*(?:-lt|-le|-gt|-ge|<=?|>=?)", re.IGNORECASE)
INCREMENT = re.compile(r"\+\+|\+=\s*1\b|\$\(\(\s*\$?\w+\s*\+\s*1\s*\)\)|\b\w+\s*=\s*\$?\w+\s*\+\s*1\b")
READ = re.compile(W + r"read\b")


def segments(command: str) -> list[tuple[int, int]]:
    """(start, end) of the top-level commands of `command`: split on ; & | && || and newlines outside quotes."""
    out, start, quote, i = [], 0, None, 0
    while i < len(command):
        ch = command[i]
        if quote:
            if ch == quote:
                quote = None
            elif ch == "\\" and quote == '"':
                i += 1
        elif ch in "'\"":
            quote = ch
        elif ch == "\\":
            i += 1
        elif ch in ";|&\n" and not (ch == "&" and i > 0 and command[i - 1] in "<>"):
            out.append((start, i))
            start = i + (2 if command[i:i + 2] in ("&&", "||") else 1)
            i = start
            continue
        i += 1
    out.append((start, len(command)))
    return out


def wrapped(command: str, pos: int, shell: str, segs: list[tuple[int, int]]) -> bool:
    """True when the top-level command (of `segs`) that contains `pos` is `timeout <duration> ...` (Bash tool only)."""
    if shell != "Bash":
        return False
    for start, end in segs:
        if start <= pos < end:
            return bool(TIMEOUT.match(command, start))
    return False


def bounded(cond: str, body: str, sh: bool) -> bool:
    if CLOCK.search(cond) or (sh and READ.search(cond)):
        return True
    return bool(COUNT.search(cond) and (INCREMENT.search(body) or INCREMENT.search(cond)))


def closing_braces(command: str) -> dict[int, int]:
    """{index of a `{`: index of its matching `}`}, found in one pass; braces inside '...', "..." or a # comment do
    not count, and unclosed ones are absent."""
    pairs, stack, quote = {}, [], None
    for i, ch in enumerate(command):
        if quote:
            if ch == quote or (quote == "#" and ch == "\n"):
                quote = None
        elif ch == "#" and (i == 0 or command[i - 1].isspace()):
            quote = "#"
        elif ch in "'\"":
            quote = ch
        elif ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            pairs[stack.pop()] = i
    return pairs


def waits(command: str):
    """(position, description, has its own bound) of every wait in `command`. Each search moves forward once, so a long
    command is checked in about linear time."""
    for m in SH_LOOP.finditer(command):
        do = SH_DO.search(command, m.end())
        if not do:
            break  # no later loop has a `do` either
        done = SH_DONE.search(command, do.end())
        if not done:
            break
        cond, body = command[m.end():do.start()], command[do.end():done.start()]
        if SLEEP.search(cond) or SLEEP.search(body):
            yield m.start(), f"wait loop `{m.group(1)} ...; do ... sleep ... done`", bounded(cond, body, True)
    for m in PS_DO.finditer(command):
        if SLEEP.search(m.group(1)):
            yield m.start(), "wait loop `do { ... Start-Sleep ... } while/until (...)`", bounded(m.group(2), m.group(1), False)
    pairs = None
    for m in PS_WHILE.finditer(command):
        brace = command.find("{", m.end())
        if brace < 0:
            break  # no block follows this or any later `while (`
        head = command[m.end():brace].rstrip()
        if not head.endswith(")"):
            continue
        pairs = closing_braces(command) if pairs is None else pairs
        cond, body = head[:-1], command[brace + 1:pairs.get(brace, len(command))]
        if SLEEP.search(cond) or SLEEP.search(body):
            yield m.start(), "wait loop `while (...) { ... Start-Sleep ... }`", bounded(cond, body, False)
    for m in FOR_EVER.finditer(command):
        yield m.start(), f"endless loop `{m.group(0)}`", False
    for m in FOLLOW.finditer(command):
        name = m.group(1).lower()
        test = next(t for n, t in FOLLOW_RULES if name in n.split("|"))
        if test(m.group(2)):
            yield m.start(), f"follow or watch command `{m.group(0).strip()[:60]}`", False
    for m in WATCH.finditer(command):
        yield m.start(), "watch command", False


def problem(command: str, shell: str = "Bash") -> str | None:
    """Why `command` is a wait without a deadline, or None."""
    segs = None
    for pos, what, own_bound in sorted(waits(command)):
        segs = segments(command) if segs is None else segs
        if not own_bound and not wrapped(command, pos, shell, segs):
            return what
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("tool_name") not in ("Bash", "PowerShell"):
        return 0
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str):
        return 0
    why = problem(command, payload["tool_name"])
    if why is None:
        return 0
    print(f"Blocked by the wait guard (Mercury #706): {why} without a deadline. Local sessions do not stop background "
          "commands, so this could run forever. In Bash wrap it as `timeout <seconds> bash -c '...'`; bound a loop "
          "condition with $SECONDS, Get-Date or a counter; use Monitor with timeout_ms; and to learn when a background "
          "command ends, wait for its completion notice instead of polling its output. If this is not a wait (for "
          "example a search pattern or a message that mentions these words), rephrase it or pass the text in a file.",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
