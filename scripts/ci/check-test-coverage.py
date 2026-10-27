#!/usr/bin/env python3
"""Fail when a tracked test file is not run by a workflow on every pull request (Mercury #686).

What counts as a test file (decided by the file NAME, for every tracked file, and every new file that is not
ignored, outside `sources/`):

    Python      test_*.py   test-*.py   *_test.py
    Shell       test_*.sh   test-*.sh   *-test.sh   *.test.sh
    PowerShell  test_*.ps1  test-*.ps1  *.test.ps1  *.Tests.ps1
    Node        *.test.js   *.test.cjs  *.test.mjs  *.test.ts

A helper that lives next to tests but is not named like one (`tests/dot/pack_paths.py`) is not a test.

A workflow step COUNTS only if all of these hold (each is checked, none is assumed):

  1. The workflow has a `pull_request` trigger. `push`, `schedule` and `workflow_dispatch` alone do not count.
  2. That trigger has no `paths` / `paths-ignore` filter, its `types` (if any) include `synchronize`, and its
     `branches` (if any) include `master` (the branch this repository merges into). A workflow whose
     trigger is filtered runs only for some pull requests: its steps count only for the tests listed under
     "path_filtered" in the config (with a reason), and the matrix marks them with `*`.
  3. Neither the job nor the step is switched off: `if: false` / `${{ false }}` on either means never. An `if:` this
     script does not understand (anything but nothing, true, success(), always(), !cancelled(), `runner.os ==
     'X'`, `matrix.os == 'label'`, and the `!=` forms) does not count: those steps count only for the tests listed
     under "conditional" (with a reason), marked `~`. `continue-on-error: true` on the job or step means a failure
     cannot fail the check, so it never counts.
  4. The command can fail the step. `cmd || true`, `|| :`, `|| exit 0`, `|| echo ...` and a trailing `; true`
     swallow the failure and do not count. Where only the LAST command decides a step's result (the Windows
     default shell `pwsh`, or a block after `set +e`), only the last statement of the step counts.
  5. The command runs the test. A command counts when its first word is an executor, or the test path itself:
     `bash`/`sh` (the script, or the `run-isolated.sh` wrapper and its argument), `python`/`python3`/`py` (a script,
     `-m unittest ...`, `-m pytest ...`), `pwsh`/`powershell` (`-File x` or a script), `node` (path or glob
     arguments), `run-isolated.sh` (the test, or the command after `--`). `cat tests/x.sh`, `echo tests/x.sh`,
     `ls`, `git` and the like never count. In `for VAR in ITEMS; do BODY; done`, each item (a path, or a glob that
     is matched against the test files) is substituted for `$VAR` in BODY and BODY's commands are judged by this
     same rule, so `for t in tests/*.sh; do echo $t; done` counts nothing.
  6. `python -m unittest discover -s DIR -p PATTERN` counts the file only if it sits directly in DIR, matches
     PATTERN (default `test*.py`) and has an importable name (unittest cannot import `test-x.py`); discovery does
     not descend into a directory without `__init__.py`, so neither does this check.

Every run is recorded with the platform(s) of its job (from `runs-on` and `matrix.os`, narrowed by `runner.os` /
`matrix.os` conditions).

Four explicit lists live in `scripts/ci/test-coverage.json`; every entry needs a reason:

    "exempt"         tests no workflow runs.
    "path_filtered"  tests that are run only by a workflow whose pull_request trigger is path-filtered (rule 2).
    "conditional"    tests that are run only by a step whose `if:` / `continue-on-error` is not understood (rule 3).
    "not_on"         {"windows": {path: reason}, "linux": {...}}: tests that run on one platform but, on purpose,
                     not on the other. A test that runs on Linux but not on Windows MUST be listed under "windows"
                     (and vice versa), so a platform gap is always a written decision.

Stale entries are errors, so the lists cannot rot: a path that is not a test file; an `exempt` test that does run;
a `path_filtered` / `conditional` entry that adds no platform beyond what unfiltered runs already cover; a
`not_on` entry that does run there. A run that is both filtered and conditional is judged as conditional.

Not modelled: reusable workflows (`workflow_call`), `needs`, other `if:` operators, and any way of swallowing a
failure other than those in rule 4.

Exit status: 0 all covered, 1 a coverage problem, 2 usage or parse error.
`--matrix` prints which tests run on which platform; `*` = only via a path-filtered workflow, `~` = only via a
conditional step.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_REL = "scripts/ci/test-coverage.json"
WORKFLOW_DIR = ".github/workflows"
PLATFORMS = ("linux", "windows", "macos")

TEST_NAME_PATTERNS = (
    "test_*.py", "test-*.py", "*_test.py",
    "test_*.sh", "test-*.sh", "*-test.sh", "*.test.sh",
    "test_*.ps1", "test-*.ps1", "*.test.ps1", "*.Tests.ps1",
    "*.test.js", "*.test.cjs", "*.test.mjs", "*.test.ts",
)
IGNORED_PREFIXES = ("sources/",)


class CoverageError(Exception):
    """The input (workflow, config) cannot be understood: exit status 2."""


# ---------------------------------------------------------------- tracked test files

def is_test_name(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(base, p) for p in TEST_NAME_PATTERNS)


def tracked_files(root: Path) -> list[str]:
    # Tracked files plus new files that are not ignored: on CI that is exactly the checkout, and on a developer
    # machine a test file that has not been `git add`ed yet is still checked.
    r = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       capture_output=True)
    if r.returncode != 0:
        raise CoverageError("git ls-files failed: " + r.stderr.decode("utf-8", "replace").strip())
    return [p for p in r.stdout.decode("utf-8").split("\0") if p]


def test_files(paths: list[str]) -> list[str]:
    return sorted(p for p in paths if is_test_name(p) and not p.startswith(IGNORED_PREFIXES))


# ---------------------------------------------------------------- a small YAML reader
# Only what workflows use: block mappings and sequences, plain/quoted scalars, flow lists, `|` and `>` block
# scalars, comments. Anything else raises CoverageError instead of being guessed at.

def _strip_comment(line: str) -> str:
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"" and (i == 0 or line[i - 1] in " \t[{,:"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _scalar(text: str):
    text = text.strip()
    if text == "":
        return ""
    if text[0] in "'\"":
        if len(text) < 2 or text[-1] != text[0]:
            raise CoverageError(f"unterminated quoted scalar: {text!r}")
        inner = text[1:-1]
        return inner.replace("''", "'") if text[0] == "'" else inner.replace('\\"', '"')
    if text[0] == "[":
        if not text.endswith("]"):
            raise CoverageError(f"unsupported flow sequence: {text!r}")
        inner = text[1:-1].strip()
        return [_scalar(x) for x in _split_flow(inner)] if inner else []
    if text[0] == "{":
        raise CoverageError(f"flow mappings are not supported: {text!r}")
    return text


def _split_flow(text: str) -> list[str]:
    out, cur, quote, depth = [], "", None, 0
    for ch in text:
        if quote:
            cur += ch
            quote = None if ch == quote else quote
        elif ch in "'\"":
            quote = ch
            cur += ch
        elif ch == "[":
            depth += 1
            cur += ch
        elif ch == "]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _key_split(text: str):
    """('key', 'rest') for `key: rest` / `key:`, else None. Handles quoted keys."""
    m = re.match(r"""^("([^"]*)"|'([^']*)'|[^\s'"\[{#][^:]*?)\s*:(?:\s+(.*)|)$""", text)
    if not m:
        return None
    key = m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(1)
    return key.strip(), (m.group(4) or "")


def parse_yaml(text: str):
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines = []  # (indent, content, original index)
    i = 0
    while i < len(raw):
        line = raw[i]
        if "\t" in line[: len(line) - len(line.lstrip())]:
            raise CoverageError(f"line {i + 1}: tabs are not allowed for indentation")
        stripped = _strip_comment(line)
        if stripped.strip() in ("", "---"):
            i += 1
            continue
        lines.append((len(stripped) - len(stripped.lstrip(" ")), stripped.strip(), i))
        i += 1
    pos = [0]

    def block_scalar(parent_indent: int, header: str, start_index: int):
        """Collect the lines of a `|` or `>` block scalar that follow raw line start_index."""
        fold = header.startswith(">")
        j = start_index + 1
        body = []
        base = None
        while j < len(raw):
            ln = raw[j]
            if ln.strip() == "":
                body.append("")
                j += 1
                continue
            ind = len(ln) - len(ln.lstrip(" "))
            if ind <= parent_indent:
                break
            base = ind if base is None else base
            body.append(ln[min(base, ind):])
            j += 1
        while body and body[-1] == "":
            body.pop()
        # skip the structured lines that fall inside the scalar
        while pos[0] < len(lines) and lines[pos[0]][2] < j:
            pos[0] += 1
        return (" ".join(body) if fold else "\n".join(body)) + "\n"

    def parse_block(indent: int):
        if pos[0] >= len(lines):
            return None
        if lines[pos[0]][1].startswith("- ") or lines[pos[0]][1] == "-":
            return parse_seq(lines[pos[0]][0])
        return parse_map(lines[pos[0]][0])

    def parse_value(parent_indent: int, rest: str, index: int):
        if re.match(r"^[|>][+-]?\d*$", rest):
            return block_scalar(parent_indent, rest, index)
        if rest != "":
            return _scalar(rest)
        if pos[0] < len(lines) and (lines[pos[0]][0] > parent_indent
                                    or (lines[pos[0]][0] == parent_indent and lines[pos[0]][1].startswith("- "))):
            return parse_block(lines[pos[0]][0])
        return ""

    def parse_map(indent: int):
        out = {}
        while pos[0] < len(lines) and lines[pos[0]][0] == indent and not lines[pos[0]][1].startswith("- "):
            ind, content, index = lines[pos[0]]
            kv = _key_split(content)
            if kv is None:
                raise CoverageError(f"line {index + 1}: cannot read {content!r} as `key: value`")
            pos[0] += 1
            out[kv[0]] = parse_value(indent, kv[1], index)
        if pos[0] < len(lines) and lines[pos[0]][0] > indent:
            raise CoverageError(f"line {lines[pos[0]][2] + 1}: unexpected indentation")
        return out

    def parse_seq(indent: int):
        out = []
        while pos[0] < len(lines) and lines[pos[0]][0] == indent and (lines[pos[0]][1].startswith("- ") or lines[pos[0]][1] == "-"):
            ind, content, index = lines[pos[0]]
            item = content[1:].strip()
            if item == "":
                pos[0] += 1
                out.append(parse_block(lines[pos[0]][0]) if pos[0] < len(lines) and lines[pos[0]][0] > indent else "")
                continue
            kv = _key_split(item)
            if kv is None:
                pos[0] += 1
                out.append(_scalar(item))
                continue
            # `- key: value` opens a mapping whose further keys sit at indent + 2 (the column of `key`)
            child_indent = indent + (len(content) - len(content[1:].lstrip()) )
            lines[pos[0]] = (child_indent, item, index)
            out.append(parse_map(child_indent))
        return out

    result = parse_block(lines[0][0]) if lines else {}
    if pos[0] < len(lines):
        raise CoverageError(f"line {lines[pos[0]][2] + 1}: could not parse the rest of the file")
    return result


# ---------------------------------------------------------------- workflows -> runs

class Run:
    """One `run:` step: its commands text and where, and how reliably, it executes."""

    def __init__(self, workflow, job, step, text, platforms, working_directory, shell, trigger, conditional):
        self.workflow, self.job, self.step, self.text = workflow, job, step, text
        self.platforms, self.working_directory, self.shell = platforms, working_directory, shell
        self.trigger = trigger          # "always" or "filtered" (rule 2)
        self.conditional = conditional  # an `if:` / `continue-on-error` this script does not understand (rule 3)

    def where(self):
        return f"{self.workflow}:{self.job}:{self.step}"


def _os_of(label: str):
    label = label.lower()
    for key, name in (("ubuntu", "linux"), ("windows", "windows"), ("macos", "macos")):
        if label.startswith(key):
            return name
    return None


def job_platforms(job: dict) -> list[str]:
    runs_on = job.get("runs-on", "")
    if isinstance(runs_on, list):
        runs_on = " ".join(map(str, runs_on))
    if "matrix." in str(runs_on):
        matrix = (job.get("strategy") or {}).get("matrix") or {}
        key = re.search(r"matrix\.(\w+)", str(runs_on)).group(1)
        values = matrix.get(key, [])
        labels = list(values) if isinstance(values, list) else [values]
    else:
        labels = [runs_on]
    return [p for p in (_os_of(str(label)) for label in labels) if p]


_RUNS = {"", "true", "${{ true }}", "${{true}}", "success()", "${{ success() }}", "always()", "${{ always() }}",
         "!cancelled()", "${{ !cancelled() }}"}
_NEVER = {"false", "${{ false }}", "${{false}}"}


def condition(expr, platforms: list[str]):
    """Evaluate an `if:` of a job or step. Returns (state, platforms); state is 'run', 'never' or 'unknown'."""
    text = "" if expr is None else str(expr).strip()
    low = text.lower()
    if low in _RUNS:
        return "run", platforms
    if low in _NEVER:
        return "never", platforms
    inner = text[3:-2].strip() if text.startswith("${{") and text.endswith("}}") else text
    for pattern, convert in ((r"runner\.os\s*(==|!=)\s*'(\w+)'", str.lower), (r"matrix\.os\s*(==|!=)\s*'([^']+)'", _os_of)):
        m = re.fullmatch(pattern, inner)
        if m:
            target = convert(m.group(2))
            kept = [p for p in platforms if (p == target) == (m.group(1) == "==")]
            return ("run", kept) if kept else ("never", kept)
    return "unknown", platforms


def _continue_on_error(value):
    """'never' (a literal true: failures cannot fail the check), 'unknown' (an expression) or 'fine'."""
    if value is None:
        return "fine"
    text = str(value).strip().lower()
    if text in ("true", "${{ true }}", "${{true}}"):
        return "never"
    if text in ("", "false", "${{ false }}", "${{false}}"):
        return "fine"
    return "unknown"


def pull_request_trigger(doc) -> str:
    """'none' (no pull_request trigger), 'filtered' (paths / types / branches narrow it) or 'always'."""
    on = (doc or {}).get("on")
    if isinstance(on, str):
        return "always" if on == "pull_request" else "none"
    if isinstance(on, list):
        return "always" if "pull_request" in on else "none"
    if not isinstance(on, dict) or "pull_request" not in on:
        return "none"
    cfg = on["pull_request"]
    if not isinstance(cfg, dict):
        return "always"
    if cfg.get("paths") or cfg.get("paths-ignore"):
        return "filtered"
    types = cfg.get("types")
    if types and "synchronize" not in (types if isinstance(types, list) else [types]):
        return "filtered"
    branches = cfg.get("branches")
    if branches:
        wanted = branches if isinstance(branches, list) else [branches]
        if not any(fnmatch.fnmatchcase("master", str(b)) for b in wanted):
            return "filtered"
    ignored = cfg.get("branches-ignore")
    if ignored:
        banned = ignored if isinstance(ignored, list) else [ignored]
        if any(fnmatch.fnmatchcase("master", str(b)) for b in banned):
            return "filtered"
    return "always"


def collect_runs(root: Path) -> list[Run]:
    runs = []
    wf_dir = root / WORKFLOW_DIR
    for path in sorted(list(wf_dir.glob("*.yml")) + list(wf_dir.glob("*.yaml"))):
        try:
            doc = parse_yaml(path.read_text(encoding="utf-8"))
        except CoverageError as err:
            raise CoverageError(f"{path.name}: {err}")
        jobs = (doc or {}).get("jobs")
        if not isinstance(jobs, dict):
            raise CoverageError(f"{path.name}: no `jobs` mapping")
        trigger = pull_request_trigger(doc)
        if trigger == "none":
            continue  # rule 1: nothing in this workflow runs for a pull request
        for job_id, job in jobs.items():
            if not isinstance(job, dict):
                continue
            state, platforms = condition(job.get("if"), job_platforms(job))
            job_coe = _continue_on_error(job.get("continue-on-error"))
            if state == "never" or job_coe == "never":
                continue
            unknown_job = state == "unknown" or job_coe == "unknown"
            defaults = (job.get("defaults") or {}).get("run") or {}
            default_wd, default_shell = defaults.get("working-directory", ""), defaults.get("shell")
            for n, step in enumerate(job.get("steps") or [], 1):
                if not isinstance(step, dict) or "run" not in step:
                    continue
                step_state, step_platforms = condition(step.get("if"), platforms)
                step_coe = _continue_on_error(step.get("continue-on-error"))
                if step_state == "never" or step_coe == "never" or not step_platforms:
                    continue
                runs.append(Run(path.name, job_id, step.get("name") or f"step {n}", str(step["run"]), step_platforms,
                                str(step.get("working-directory", default_wd) or ""), step.get("shell", default_shell), trigger,
                                unknown_job or step_state == "unknown" or step_coe == "unknown"))
    return runs


# ---------------------------------------------------------------- which files does a run step execute?

def _logical_lines(text: str) -> list[str]:
    out, cur = [], ""
    for line in text.split("\n"):
        stripped = line.rstrip()
        if stripped.endswith("\\") or stripped.endswith("`"):
            cur += stripped[:-1] + " "
            continue
        out.append(cur + stripped)
        cur = ""
    if cur:
        out.append(cur)
    return [ln for ln in out if ln.strip() and not ln.strip().startswith("#")]


def _commands(line: str) -> list[dict]:
    """Split a logical line into commands: [{'words': [...], 'sep': the operator that follows, or ''}]."""
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    try:
        words = list(lex)
    except ValueError:
        words = line.split()
    out, cur = [], []
    for w in words:
        if w and set(w) <= set(";&|()"):
            if cur:
                out.append({"words": cur, "sep": w})
            elif out:
                out[-1]["sep"] += w
            cur = []
        else:
            cur.append(w)
    if cur:
        out.append({"words": cur, "sep": ""})
    return out


def _swallowed(commands: list[dict], i: int) -> bool:
    """True when a failure of commands[i] cannot fail the step (rule 4)."""
    sep = commands[i]["sep"]
    nxt = commands[i + 1]["words"] if i + 1 < len(commands) else []
    if sep == "||":
        return nxt[:1] in (["true"], [":"], ["echo"], ["printf"]) or nxt[:2] == ["exit", "0"]
    return sep == ";" and nxt in (["true"], [":"]) and i + 2 >= len(commands)


def _norm(token: str, wd: str) -> str:
    t = token.replace("\\", "/")
    while t.startswith("./"):
        t = t[2:]
    if wd and wd not in (".", "./"):
        t = f"{wd.strip('/').replace(chr(92), '/')}/{t}"
    return t


def glob_regex(pattern: str):
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern[i:i + 3] == "**/":
            out += "(?:.*/)?"
            i += 3
            continue
        if c == "*" and pattern[i:i + 2] == "**":
            out += ".*"
            i += 2
            continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    return re.compile(out + r"\Z")


def _has_glob(token: str) -> bool:
    return any(ch in token for ch in "*?[")


def _discover_matches(tokens: list[str], tests: list[str], wd: str) -> set[str]:
    start, pattern = ".", "test*.py"
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("-s", "--start-directory") and i + 1 < len(tokens):
            start = tokens[i + 1]
            i += 2
            continue
        if t in ("-p", "--pattern") and i + 1 < len(tokens):
            pattern = tokens[i + 1]
            i += 2
            continue
        if t.startswith("-s") and len(t) > 2 and not t.startswith("--"):
            start = t[2:]
        elif t.startswith("-p") and len(t) > 2 and not t.startswith("--"):
            pattern = t[2:]
        i += 1
    base = _norm(start, wd).rstrip("/")
    out = set()
    for rel in tests:
        d, _, name = rel.rpartition("/")
        stem = name[:-3] if name.endswith(".py") else None
        if d == (base if base != "." else "") and stem is not None and stem.isidentifier() \
                and fnmatch.fnmatchcase(name, pattern):
            out.add(rel)
    return out


WRAPPER = "run-isolated.sh"
_PWSH_VALUE_OPTIONS = {"-executionpolicy", "-workingdirectory", "-wd", "-configurationname", "-inputformat",
                       "-outputformat", "-version", "-settingsfile", "-windowstyle"}
_NODE_VALUE_OPTIONS = {"-r", "--require", "--import", "--test-reporter", "--test-reporter-destination",
                       "--test-name-pattern", "--test-concurrency", "--env-file"}


def _base(word: str) -> str:
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def command_hits(words: list[str], tests: list[str], wd: str) -> set[str]:
    """The test files ONE command executes (rule 5)."""
    words = list(words)
    while words and (words[0] in ("{", "(", "!") or re.match(r"^\w+=", words[0])):
        words.pop(0)
    if not words:
        return set()
    test_set = set(tests)
    first = _norm(words[0], wd)
    if first in test_set:
        return {first}
    exe, args = _base(words[0]), words[1:]

    def wrapper(rest):
        if rest and rest[0] == "--":
            return command_hits(rest[1:], tests, wd)
        n = _norm(rest[0], wd) if rest else ""
        return {n} if n in test_set else set()

    if exe == WRAPPER:
        return wrapper(args)
    if exe in ("bash", "sh"):
        i = 0
        while i < len(args) and args[i].startswith("-"):
            if args[i] == "-c":
                return set()
            i += 2 if args[i] == "-o" else 1
        if i >= len(args):
            return set()
        if _base(args[i]) == WRAPPER:
            return wrapper(args[i + 1:])
        n = _norm(args[i], wd)
        return {n} if n in test_set else set()
    if exe in ("python", "python3", "py"):
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-c":
                return set()
            if a == "-m":
                module, rest = (args[i + 1] if i + 1 < len(args) else ""), args[i + 2:]
                if module == "unittest":
                    if "discover" in rest:
                        return _discover_matches(rest, tests, wd)
                    named = (_norm(w.replace(".", "/") + ".py", wd) for w in rest if not w.startswith("-"))
                    return {c for c in named if c in test_set}
                if module == "pytest":
                    return {c for c in (_norm(w, wd) for w in rest if not w.startswith("-")) if c in test_set}
                return set()
            if a in ("-W", "-X"):
                i += 2
                continue
            if a.startswith("-"):
                i += 1
                continue
            n = _norm(a, wd)
            return {n} if n in test_set else set()
        return set()
    if exe in ("pwsh", "powershell"):
        lowered = [a.lower() for a in args]
        if "-file" in lowered and lowered.index("-file") + 1 < len(args):
            n = _norm(args[lowered.index("-file") + 1], wd)
            return {n} if n in test_set else set()
        i = 0
        while i < len(args):
            if lowered[i] in ("-command", "-c", "-encodedcommand"):
                return set()
            if lowered[i] in _PWSH_VALUE_OPTIONS:
                i += 2
                continue
            if lowered[i].startswith("-"):
                i += 1
                continue
            n = _norm(args[i], wd)
            return {n} if n in test_set else set()
        return set()
    if exe == "node":
        out, i = set(), 0
        while i < len(args):
            a = args[i]
            if a in _NODE_VALUE_OPTIONS:
                i += 2
                continue
            if not a.startswith("-"):
                n = _norm(a, wd)
                if _has_glob(n):
                    rx = glob_regex(n)
                    out.update(t for t in tests if rx.match(t))
                elif n in test_set:
                    out.add(n)
            i += 1
        return out
    return set()


def _statements(text: str) -> list[str]:
    """A `for ... done` loop is one statement, every other logical line is one."""
    lines = _logical_lines(text)
    out, i = [], 0
    while i < len(lines):
        line = lines[i]
        if re.match(r"^\s*for\s+\w+\s+in\b", line) and not re.search(r"\bdone\b", line):
            block = [line]
            i += 1
            while i < len(lines):
                block.append(lines[i])
                finished = re.match(r"^\s*done\b", lines[i])
                i += 1
                if finished:
                    break
            out.append("\n".join(block))
        else:
            out.append(line)
            i += 1
    return out


def _statement_hits(stmt: str, tests: list[str], wd: str) -> set[str]:
    hits: set[str] = set()
    loop = re.match(r"^\s*for\s+(\w+)\s+in\s+(.+?)\s*(?:;|\n)\s*do\b(.*?)\bdone\b", stmt, re.S)
    if loop:
        var, items, body = loop.group(1), loop.group(2), loop.group(3)
        try:
            item_words = shlex.split(items, comments=False, posix=True)
        except ValueError:
            item_words = items.split()
        concrete, known = [], set(tests)
        for item in item_words:
            norm = _norm(item, wd)
            if _has_glob(norm):
                rx = glob_regex(norm)
                concrete += [t for t in tests if rx.match(t)]
            elif norm in known:
                concrete.append(norm)
        # `do cmd; cmd2; done` on one line: the body's own `;` separate commands inside _commands
        for t in dict.fromkeys(concrete):
            for line in _logical_lines(body):
                cmds = _commands(line)
                for i, cmd in enumerate(cmds):
                    if not _swallowed(cmds, i):
                        words = [w.replace("${" + var + "}", t).replace("$" + var, t) for w in cmd["words"]]
                        hits |= command_hits(words, tests, wd)
        return hits
    cmds = _commands(stmt)
    for i, cmd in enumerate(cmds):
        if not _swallowed(cmds, i):
            hits |= command_hits(cmd["words"], tests, wd)
    return hits


def executed_by(run: Run, tests: list[str]) -> tuple[set[str], set[str]]:
    """(tests run by any command that can fail the step, tests run by the LAST statement of the step)."""
    per = [_statement_hits(s, tests, run.working_directory) for s in _statements(run.text)]
    return (set().union(*per) if per else set()), (per[-1] if per else set())


def hits_on(run: Run, tests: list[str], platform: str) -> set[str]:
    """What the step runs on one platform: every command under bash, only the last statement where rule 4 says so."""
    every, last = executed_by(run, tests)
    shell = str(run.shell or ("pwsh" if platform == "windows" else "bash")).split()[0].lower()
    set_plus_e = bool(re.search(r"^\s*set\s+\+e\b", run.text, re.M))
    return last if shell in ("pwsh", "powershell", "cmd") or set_plus_e else every


# ---------------------------------------------------------------- the check

CATEGORIES = ("exempt", "path_filtered", "conditional")


def load_config(root: Path) -> dict:
    path = root / CONFIG_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        raise CoverageError(f"{CONFIG_REL}: {err}")
    if not isinstance(data, dict) or set(data) - {"_comment", "not_on", *CATEGORIES}:
        raise CoverageError(f"{CONFIG_REL}: only the keys '_comment', 'not_on', 'exempt', 'path_filtered' and 'conditional' are allowed")
    config = {name: data.get(name, {}) for name in CATEGORIES}
    config["not_on"] = data.get("not_on", {})
    if not isinstance(config["not_on"], dict) or set(config["not_on"]) - set(PLATFORMS):
        raise CoverageError(f"{CONFIG_REL}: 'not_on' is {{platform: {{path: reason}}}} with platform in {PLATFORMS}")
    sections = [(name, config[name]) for name in CATEGORIES] + [(f"not_on.{k}", v) for k, v in config["not_on"].items()]
    for where, entries in sections:
        if not isinstance(entries, dict):
            raise CoverageError(f"{CONFIG_REL}: {where} must be an object {{path: reason}}")
        for entry, reason in entries.items():
            if not isinstance(reason, str) or len(reason.strip()) < 10:
                raise CoverageError(f"{CONFIG_REL}: {where}[{entry!r}] needs a reason (at least 10 characters)")
    return config


def analyse(root: Path, tracked: list[str] | None = None):
    """Returns (problems, matrix). The matrix maps every test file to its platforms, for example ['linux', 'windows*']:
    `*` marks a platform covered only through a path-filtered workflow, `~` only through a conditional step."""
    tracked = tracked if tracked is not None else tracked_files(root)
    tests = test_files(tracked)
    config = load_config(root)
    cov = {t: {"always": set(), "filtered": set(), "conditional": set()} for t in tests}
    for run in collect_runs(root):
        kind = "conditional" if run.conditional else "filtered" if run.trigger == "filtered" else "always"
        for platform in run.platforms or ["unknown"]:
            for t in hits_on(run, tests, platform):
                cov[t][kind].add(platform)
    listed = {"filtered": config["path_filtered"], "conditional": config["conditional"]}
    label = {"filtered": "path_filtered", "conditional": "conditional"}
    effective, marks, problems = {}, {}, []
    for t in tests:
        eff, marks[t] = set(cov[t]["always"]), {}
        for kind in ("filtered", "conditional"):
            if t in listed[kind]:
                eff |= cov[t][kind]
                for p in cov[t][kind] - cov[t]["always"]:
                    marks[t].setdefault(p, "*" if kind == "filtered" else "~")
        effective[t] = eff
    for entry in sorted(config["exempt"]):
        if entry not in cov:
            problems.append(f"exempt entry is not a tracked test file: {entry}")
        elif any(cov[entry].values()):
            problems.append(f"exempt entry is run by a workflow; remove it: {entry}")
    for kind in ("filtered", "conditional"):
        for entry in sorted(listed[kind]):
            if entry not in cov:
                problems.append(f"{label[kind]} entry is not a tracked test file: {entry}")
            elif not cov[entry][kind] - cov[entry]["always"]:
                problems.append(f"{label[kind]} entry adds nothing (the test is covered without it, or no such run exists); "
                                f"remove it: {entry}")
    for platform, entries in sorted(config["not_on"].items()):
        for entry in sorted(entries):
            if entry not in cov:
                problems.append(f"not_on.{platform} entry is not a tracked test file: {entry}")
            elif not effective[entry]:
                problems.append(f"not_on.{platform} entry is not run anywhere (use 'exempt'): {entry}")
            elif platform in effective[entry]:
                problems.append(f"not_on.{platform} entry does run on {platform}; remove it: {entry}")
    for t in tests:
        if t in config["exempt"]:
            continue
        for kind in ("filtered", "conditional"):
            extra = cov[t][kind] - cov[t]["always"]
            if extra and t not in listed[kind]:
                what = "a path-filtered workflow" if kind == "filtered" else "a step whose condition this check cannot evaluate"
                problems.append(f"{t} is run on {', '.join(sorted(extra))} only by {what}; run it unconditionally or list it "
                                f"under '{label[kind]}' with a reason")
        if not effective[t]:
            if not any(cov[t].values()):
                problems.append(f"no workflow runs {t} on every pull request (add a step, or list it under 'exempt' with a reason)")
            continue
        for platform, other in (("windows", "linux"), ("linux", "windows")):
            if other in effective[t] and platform not in effective[t] and t not in config["not_on"].get(platform, {}):
                problems.append(f"{t} runs on {other} but not on {platform}, and 'not_on.{platform}' gives no reason")
    matrix = {t: sorted(p + marks[t].get(p, "") for p in effective[t]) for t in tests}
    return problems, matrix


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=str(ROOT), help="repository root (default: this repository)")
    parser.add_argument("--matrix", action="store_true", help="print which tests run on which platform")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    try:
        problems, matrix = analyse(root)
    except CoverageError as err:
        print(f"check-test-coverage: {err}", file=sys.stderr)
        return 2
    if args.matrix:
        width = max((len(t) for t in matrix), default=0)
        for t, plats in matrix.items():
            print(f"{t.ljust(width)}  {','.join(plats) if plats else '-'}")
        print("(* = only through a path-filtered workflow listed under path_filtered; "
              "~ = only through a step whose condition is listed under conditional)")
    covered = sum(1 for plats in matrix.values() if plats)
    print(f"check-test-coverage: {len(matrix)} test files, {covered} run by a workflow, "
          f"{len(matrix) - covered} exempt or unrun, {len(problems)} problem(s)")
    for p in problems:
        print(f"  PROBLEM: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
