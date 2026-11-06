"""Fifth rule set. Splits memory by source: the project's store is M, the host's own store is N.

Key labels: M project memory read, N host memory read, B cross-lane message read, S skill doc read, K KB read,
R rule file read, W external web, G GitHub.
Other labels: Mw, Kw, X writes; GR, GW git; T test; C search; F file read; P script; O other.
"""
import json
import re

I = re.I
SEP = r"[\\/]+"
MEM = re.compile(r"\.(sot|mercury)" + SEP + r"memory|\bMEMORY\.md|SESSION_INDEX|session-checkpoint\.md|gap-list\.md", I)
HOSTMEM = re.compile(r"\.codex" + SEP + r"memories|\.codex" + SEP + r"projects" + SEP + r".*memory", I)
EVIDENCE = re.compile(SEP + r"evidence(?:" + SEP + r"|$)", I)  # a task's working files kept under a lane folder, not recall
BUSDB = re.compile(r"[^\s'\"`]*lane-bus\.sqlite[^\s'\"`]*", I)
BUSCMD = re.compile(r"lane-bus\.(?:sh|py)['\"]?\s+read\b", I)
INBOX = re.compile(r"lanes" + SEP + r"inbox-|" + SEP + r"inbox-[\w.-]*\.md", I)
SKEND = r"skills(?:[\\/]|\s|$|['\"])"
SKILL = re.compile(r"(?:^|[\\/\s'\"])SKILL\.md|\.(?:agents|claude|codex)" + SEP + SKEND + r"|packs" + SEP + r"[\w-]+" + SEP + SKEND + r"|plugins" + SEP + r".*" + SKEND + r"|sot-skill\.md")
CODEEXT = re.compile(r"\.(py|ps1|sh|js|mjs|ts|json|toml|yaml|yml)['\"`,);]*$", I)
KB = re.compile(r"(^|[\s'\"=:/\\(])docs(" + SEP + r"|\s|$|['\"])|(?<![.\w])obsidian|审核请求", I)
KBLIT = re.compile(r"(^|[\s'\"=:/\\(])docs" + SEP + r"|审核请求", I)
RULE = re.compile(r"(AGENTS|CLAUDE)\.md")
READ = re.compile(r"^(cat|sed|head|tail|awk|less|more|type|gc|Get-Content|wc|cut|nl)\b", I)
GREP = re.compile(r"^(rg|grep|egrep|findstr|Select-String)\b", I)
LIST = re.compile(r"^(find|ls|dir|Get-ChildItem|gci|Test-Path|tree)\b|\s--files\b", I)
GITPRE = r"^git(\s+-C\s+\S+|\s+-c\s+\S+)*\s+"
GITSHOW = re.compile(GITPRE + r"(show|grep)\b", I)
GITR = re.compile(GITPRE + r"(log|show|diff|status|blame|grep|ls-files|rev-parse|ls-tree|rev-list|hash-object|branch\s+--show|remote\s+get-url|worktree\s+list|ls-remote|merge-base|cat-file)\b", I)
GITW = re.compile(GITPRE + r"(add|commit|push|fetch|pull|worktree\s+(add|remove)|branch\s+-|checkout|switch|merge|submodule|rm|mv)\b|git-safe|sot-publish|sot-worktree", I)
CLONE = re.compile(GITPRE + r"clone\b", I)
TEST = re.compile(r"^(npm\s+(run|test)|pnpm|npx\s+(vite|tsc|vitest|playwright)|vitest|pytest|cargo|go\s+test|tsc\b)|-m\s+(unittest|pytest)|node\s+.*--test|godot|run_tests|kb_check|mercury\.py\s+(check|apply)|guard\.ps1\s+pre-merge|TRIAL_URL=", I)
WRITE = re.compile(r"^(cp|mv|rm|mkdir|rmdir|touch|tee|Set-Content|Add-Content|Out-File|Copy-Item|Move-Item|Remove-Item|New-Item)\b|(^|[^0-9&2])>{1,2}\s*[^&\s]|\.write(_text|_bytes|File)?\(|writeFile|WriteAllText|open\([^)]*['\"][wax]b?['\"]", I)
NULLW = re.compile(r">{1,2}\s*(/dev/null|\$null|nul)\b", I)
PYRD = re.compile(r"open\(|read_text|readFile|read_bytes|json\.load", I)
NETCLI = re.compile(r"(?:^|\$\(|[\s(])(curl|wget|Invoke-WebRequest|Invoke-RestMethod|iwr|irm)\b", I)
NETPY = re.compile(r"urlopen\(|urllib\.request\.Request\(|requests\.(get|post)\(", I)
URL = re.compile(r"https?://([^/\s'\"`\\:)]+)", I)
PRIVATE = re.compile(r"^(localhost|127\.|0\.0\.0\.0|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|\[::1\])|\.localhost$|\$|\{")
SPLIT = re.compile(r"\\n|\n|⏎|&&|\|\||;")
VAR = re.compile(r"^(?:export\s+|const\s+|let\s+|var\s+)?\$?\{?([A-Za-z_]\w*)\}?\s*=\s*(.+)$")
PATCH = re.compile(r"\*\*\* Begin Patch.*?\*\*\* End Patch", re.S)
PATCHFILE = re.compile(r"\*\*\* (?:Update|Add|Delete) File: ([^\n]+?)(?:\\n|\n|$)")
NL = r"(?:\\n|\n)"
HEREDOC = re.compile(r"((?:cat|tee)\b[^\n;&|]*?)<<-?\s*['\"]?(\w+)['\"]?\s*" + r"(?:>{1,2}\s*\S+\s*)?" + NL + r".*?" + NL + r"\2\b", re.S)
PRE = re.compile(r"^(?:(?:timeout\s+(?:-\S+\s+)*\S+|sudo|time|env|nice|do|then|else|if|!|&)\s+|[A-Za-z_]\w*=\S*\s+)+")
GHCMD = re.compile(r"(?:^|\$\(|\{\s*|\|\s*|\(\)\s*)(?:(?:timeout\s+\S+|do|then|if)\s+|[A-Za-z_]\w*=\S*\s+)*gh\s+[a-z]", I)
CODE = re.compile(r"\bpython\d?\b|\bnode\b|nodeRepl|sqlite3|\bfunction\s|foreach|ForEach-Object|allSettled|@\(|read_text|read_bytes|readFile", I)
READCALL = re.compile(r"read_text|read_bytes|readFile|\.read\(|\bopen\(|json\.load|Get-Content|Show-Range|\brg\s|Select-String|sed\s+-n", I)
SCRIPTWRITE = re.compile(r"write_text|write_bytes|writeFile|WriteAllText|Set-Content|\.write\(|open\([^)]*['\"][wax]b?['\"]", I)
HASHONLY = re.compile(r"hashlib|sha256|Get-FileHash", I)
LITERAL = re.compile(r"'([^'\n]{4,260})'|\"([^\"\n]{4,260})\"|`([^`\n]{4,260})`")
TOK = re.compile(r"[^\s'\"`]+")


def memory(target):
    """The memory labels a target earns, judged path by path so that one path cannot lend its label to another."""
    labels = set()
    for token in TOK.findall(target):
        if EVIDENCE.search(token):
            continue
        if HOSTMEM.search(token):
            labels.add("N")
        elif MEM.search(token):
            labels.add("M")
    return labels


def skill_doc(target):
    return any(SKILL.search(t) and not CODEEXT.search(t) for t in TOK.findall(target))


def without_skill(target):
    return " ".join(t for t in TOK.findall(target) if not SKILL.search(t))


def external(text):
    return any(not PRIVATE.search(h) and "." in h for h in URL.findall(text))


def hit(target, stage_is_listing, labels):
    """Add the read labels a target earns; return whether any was added."""
    if stage_is_listing:
        return False
    target = BUSDB.sub(" ", target)
    read = memory(target)
    labels |= read
    found = bool(read)
    for ok, lab in ((skill_doc(target), "S"), (RULE.search(target), "R"), (KB.search(without_skill(target)), "K")):
        if ok:
            labels.add(lab)
            found = True
    return found


def native(name, text, path):
    d = {}
    try:
        d = json.loads(text or "{}")
    except ValueError:
        pass
    if not isinstance(d, dict):
        d = {}
    if name == "Glob":
        return {"C"}
    if name == "Grep":
        target = " ".join(str(d.get(k) or "") for k in ("path", "glob")) or path
        labels = set()
        if BUSDB.search(target) or INBOX.search(target):
            labels.add("B")
        return labels if hit(target, False, labels) or labels else {"C"}
    if name == "WebSearch":
        return {"W"}
    if name == "WebFetch":
        return {"W"} if external(text or "") else {"O"}
    if name == "Skill":
        return {"S"}
    return None


def analyse(name, text, path=""):
    labels = set()
    if name in ("Glob", "Grep", "WebSearch", "WebFetch", "Skill"):
        return native(name, text, path)
    if name == "Read":
        target = path or text
        if BUSDB.search(target) or INBOX.search(target):
            return {"B"}
        hit(target, False, labels)
        return labels or {"F"}
    text = text or ""
    tools = set(re.findall(r"\btools\.([A-Za-z_]\w*)", text))
    if "web__run" in tools:
        labels.add("W")
    if any("github" in t for t in tools):
        labels.add("G")
    if any(t.startswith("mcp__codex_app__") or t in ("write_stdin", "view_image", "clock__curr_time") for t in tools):
        labels.add("O")
    for p in PATCHFILE.findall(text):
        labels.add("Mw" if memory(p) else "Kw" if KB.search(p) else "X")
    if "apply_patch" in tools and not labels & {"Mw", "Kw", "X"}:
        labels.add("X")
    text = PATCH.sub(" ", text)
    text = HEREDOC.sub(lambda m: m.group(1), text)
    # Unwrap JS forms so that a command string starts at its verb.
    body = re.sub(r"\[\"[\w.\- ]+\",\s*[\"`]", "\n", text)
    body = re.sub(r"(?:tools\.\w+\(\{\s*)?\bcmd\s*:\s*(String\.raw)?[`'\"]", "\n", body)
    body = re.sub(r"bash\w*\([\"'`]|String\.raw`|@'|'@", "\n", body)
    ctx, variables = "", {}
    for seg in SPLIT.split(body):
        if GHCMD.search(seg.strip()):
            labels.add("G")
        if BUSCMD.search(seg):
            labels.add("B")
        stage = seg.split(" | ")[0].strip().strip("(").strip()
        if not stage:
            continue
        for k in sorted(variables, key=len, reverse=True):
            stage = re.sub(r"\$\{?" + k + r"\b\}?", lambda m, v=variables[k]: v, stage)
        mf = re.match(r"for\s+(\w+)\s+in\s+(.+)$", stage)
        if mf:
            variables[mf.group(1)] = mf.group(2).strip()[:600]
        mv = VAR.match(stage)
        if mv:
            variables[mv.group(1)] = mv.group(2).strip().strip("'\"`")[:300]
        stage = PRE.sub("", stage)
        m = re.match(r"(cd|Set-Location|Push-Location)\s+['\"]?([^'\"]+)", stage, I)
        if m:
            ctx = m.group(2)
            continue
        verb = re.sub(r"^\$\w+\s*=\s*[@$]?\(*\s*", "", stage)
        grep = bool(GREP.match(verb))
        listing = bool(LIST.search(verb))
        red = bool(READ.match(verb)) or bool(GITSHOW.match(verb)) or bool(PYRD.search(stage))
        wr = bool(WRITE.search(stage)) and not NULLW.search(stage)
        mm = re.search(r"sot-memory\.ps1.*-Action\s+(\w+)", stage, I)
        if mm:
            a = mm.group(1).lower()
            labels.add("Mw" if a == "write" else "M" if a in ("read", "list", "search") else "O")
            continue
        target = verb + " " + ctx
        if grep:
            # The search pattern is not a target: drop the first argument that is not a flag.
            if re.search(r"-Pattern\b", verb, I):
                target = re.sub(r"""-Pattern\s+(?:"[^"]*"|'[^']*'|\S+)""", " ", verb, flags=I) + " " + ctx
            elif not re.match(r"Select-String", verb, I):
                target = re.sub(r"""^(\S+(?:\s+-\S+)*\s+)(?:"[^"]*"|'[^']*'|\S+)""", r"\1", verb) + " " + ctx
        if red or grep or listing:
            if BUSDB.search(target) or INBOX.search(target):
                labels.add("B")
            found = hit(target, listing, labels)
            if not found and (grep or listing or READ.match(verb)):
                labels.add("C" if grep or listing else "F")
        if GITR.match(verb):
            labels.add("GR")
        if GITW.search(verb):
            labels.add("GW")
        if TEST.search(verb):
            labels.add("T")
        if NETCLI.search(stage) or CLONE.match(verb):
            # A quoted \n or ; can move the URL into another piece of the command.
            if external(stage) or (not URL.search(stage) and external(body)):
                labels.add("W")
        if wr:
            labels.add("Mw" if memory(target) else "Kw" if KB.search(target) else "X")
        if re.match(r"(python\d?|node|pwsh|powershell|bash)\b", verb, I):
            labels.add("P")
        if "lane-bus" in stage:
            labels.add("O")
    if NETPY.search(body) and external(body):
        labels.add("W")
    if BUSDB.search(body) and re.search(r"sqlite3|select\s", body, I):
        labels.add("B")
    # A script can name a path on one line and read it on another, so check its path literals as a whole.
    if CODE.search(body) and READCALL.search(body):
        hashing = HASHONLY.search(body) and not re.search(r"read_text|Get-Content|\.read\(\)", body)
        writes = SCRIPTWRITE.search(body)
        kept = "\n".join(x for x in SPLIT.split(body) if not re.search(r"\bgit\s+(diff|add|status|commit)\b|Test-Path", x))
        for groups in LITERAL.findall(kept):
            lit = next(g for g in groups if g)
            if any(x in lit for x in ("|", "\\.", "://", "[", "(")) or len(TOK.findall(lit)) > 2 or BUSDB.search(lit):
                continue
            if memory(lit) and not hashing:
                labels |= {"Mw"} if writes else memory(lit)
            if skill_doc(lit) and not hashing:
                labels.add("S")
            if RULE.search(lit) and not hashing and not writes:
                labels.add("R")
            if KBLIT.search(without_skill(lit)) and not hashing and not writes:
                labels.add("K")
    # Reading a memory file and writing it back in one call is an edit, not recall.
    if "Mw" in labels:
        labels -= {"M", "N"}
    return labels or {"O"}
