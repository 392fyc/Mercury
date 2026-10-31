"""Mechanical checks of a project knowledge base laid out by the kb pack conventions (Mercury #736).

Usage: python kb_check.py [KB_ROOT]   (default: docs)
Exit codes: 0 no problem, 1 problems found, 2 invalid input.

The conventions are in ../docs/kb-conventions.md. This script checks only what can be decided from the files:
every directory has a README index; the KB root holds no document besides that README; each document in a
directory is listed in that index (a table with a status column) with a valid status; a
superseded document names an existing replacement; the entry page (the root README) and the indexes outside the
archive link no superseded or history document and nothing inside the archive except its README; numbered names
(00-, 01-, ...) do not repeat within a directory. Whether a rule is right is left to review.
"""
import re
import sys
from pathlib import Path
from urllib.parse import unquote

STATUSES = {"current": "current", "现行": "current", "draft": "draft", "草稿": "draft",
            "superseded": "superseded", "已取代": "superseded", "history": "history", "历史": "history"}
STATUS_HEADERS = {"status", "状态"}
REPLACEMENT_HEADERS = {"superseded by", "replaced by", "replacement", "取代者"}
ARCHIVE = re.compile(r"^\d+-archive$")
NUMBERED = re.compile(r"^(\d+)-")
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


def links(text):
    """Local link targets of a Markdown text, outside fenced code blocks, without anchors."""
    out, fenced = [], False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced:
            for target in LINK.findall(line):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                    continue
                target = unquote(target.split("#", 1)[0])
                if target:
                    out.append(target)
    return out


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def plain(cell):
    return re.sub(r"[`*_]", "", cell).strip().lower()


def index_rows(text):
    """Rows of the index tables in a README (tables with a status column, outside fenced code blocks):
    (first-cell targets, status, replacement targets)."""
    rows, header, fenced = [], None, False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced, header = not fenced, None
            continue
        if fenced or not line.lstrip().startswith("|"):
            header = None
            continue
        row = cells(line)
        if header is None:
            header = [plain(c) for c in row]
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in row if c) or not STATUS_HEADERS & set(header):
            continue
        get = lambda names: next((row[i] for i, h in enumerate(header) if h in names and i < len(row)), None)
        rows.append((links(row[0]), plain(get(STATUS_HEADERS) or ""), links(get(REPLACEMENT_HEADERS) or "")))
    return rows


def exists_exactly(path):
    """Exists with the same letter case in its file name, as Linux and GitHub see it (Windows ignores case)."""
    # Compare the link as written: Path.resolve() on Windows already returns the case found on disk.
    return path.exists() and path.name in {c.name for c in path.parent.iterdir()}


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # file names may be Chinese; a GBK console must not fail
    root = Path(argv[1] if len(argv) > 1 else "docs")
    if not root.is_dir():
        print(f"kb-check: {root} is not a directory", file=sys.stderr)
        return 2
    root = root.resolve()
    problems, status_of, texts = [], {}, {}
    rel = lambda p: p.relative_to(root).as_posix() or "."

    def read(path):
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{rel(path)}: cannot be read as UTF-8 text ({type(error).__name__})")
            return None

    dirs = [root] + sorted(p for p in root.rglob("*") if p.is_dir()
                           and not any(part.startswith(".") for part in p.relative_to(root).parts))
    archives = [d for d in dirs if d.parent == root and ARCHIVE.match(d.name)]
    in_archive = lambda p: any(a == p or a in p.parents for a in archives)
    documents = 0
    for d in dirs:
        children = [c for c in d.iterdir() if not c.name.startswith(".")]
        seen = {}
        for c in children:
            m = NUMBERED.match(c.name)
            if m:
                seen.setdefault(int(m.group(1)), []).append(c.name)
        for number, names in sorted(seen.items()):
            if len(names) > 1:
                problems.append(f"{rel(d)}: numbered names repeat {number:02d}: {', '.join(sorted(names))}")
        readme = d / "README.md"
        if not readme.is_file():
            problems.append(f"{rel(d)}: no README.md index")
            continue
        text = read(readme)
        if text is None:
            continue
        texts[readme] = text
        listed, linked = set(), {(d / t).resolve() for t in links(text)}
        for targets, status, replacement in index_rows(text):
            for t in targets[:1]:
                path = (d / t).resolve()
                if not exists_exactly(d / t):
                    problems.append(f"{rel(readme)}: index links missing {t}")
                    continue
                if path.parent != d or path.suffix.lower() != ".md" or path == readme:
                    continue
                listed.add(path)
                if status not in STATUSES:
                    problems.append(f"{rel(readme)}: {path.name} has no valid status ({status or 'none'})")
                    continue
                status_of[path] = STATUSES[status]
                if STATUSES[status] == "superseded" and not any(exists_exactly(d / r) for r in replacement):
                    problems.append(f"{rel(readme)}: superseded {path.name} names no existing replacement")
        for c in children:
            if c.is_file() and c.suffix.lower() == ".md" and c.name != "README.md":
                documents += 1
                if d == root:
                    problems.append(f"{c.name}: the KB root holds only the entry README; move it into a directory")
                elif c.resolve() not in listed:
                    problems.append(f"{rel(readme)}: {c.name} is not listed in the index")
            elif c.is_dir() and not ({c.resolve(), (c / "README.md").resolve()} & linked):
                problems.append(f"{rel(readme)}: subdirectory {c.name}/ is not linked")
    for readme, text in texts.items():
        if in_archive(readme.parent):
            continue
        for t in links(text):
            path = (readme.parent / t).resolve()
            if in_archive(path) and not (path.name == "README.md" and path.parent in archives) and path not in archives:
                problems.append(f"{rel(readme)}: index outside the archive links into it: {t}")
            elif readme.parent == root and status_of.get(path) in ("superseded", "history"):
                problems.append(f"{rel(readme)}: entry page links {status_of[path]} document {t}")
    indexes = list(texts)
    for p in problems:
        print(p)
    if problems:
        print(f"kb-check: {len(problems)} problem(s)")
        return 1
    print(f"kb-check: OK ({documents} document(s) in {len(indexes)} director(ies))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
