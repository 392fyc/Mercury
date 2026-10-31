"""Mechanical checks of a project knowledge base laid out by the kb pack conventions (Mercury #736).

Usage: python kb_check.py [KB_ROOT]   (default: docs)
Exit codes: 0 no problem, 1 problems found, 2 invalid input.

The conventions are in ../docs/kb-conventions.md. This script checks only what can be decided from the files:
every directory has a README index; the KB root holds no document besides that README; each document in a
directory is listed in that index (a table with a status column) as current or draft; there is no archive
directory; numbered names (00-, 01-, ...) do not repeat within a directory; every link in every KB document,
Markdown or wikilink, reaches an existing file. Whether a rule is right is left to review.
"""
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote

STATUSES = {"current": "current", "现行": "current", "draft": "draft", "草稿": "draft"}
STATUS_HEADERS = {"status", "状态"}
ARCHIVE = re.compile(r"^(\d+[-_]|_)?(archive|archived|history)$", re.I)
FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
NUMBERED = re.compile(r"^(\d+)-")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
WIKILINK = re.compile(r"!?\[\[([^\]|#^]*)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")


def prose_lines(text):
    """Lines outside fenced code blocks and HTML comments, with inline code removed."""
    text = re.sub(r"(?s)<!--.*?-->", "", text)
    fence = None  # the opening fence: a block closes only on the same character, at least as long, and nothing after
    for line in text.splitlines():
        m = FENCE.match(line)
        if fence is None and m:
            fence = m.group(1)
            continue
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence) and not line[m.end():].strip():
                fence = None
            continue
        yield re.sub(r"(`+).*?\1", "", line)


def links(text):
    """Local Markdown link targets, without anchors."""
    out = []
    for line in prose_lines(text):
        for target in LINK.findall(line):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I) or target.startswith("#"):
                continue
            target = unquote(re.split(r"[#?]", target, maxsplit=1)[0])
            if target:
                out.append(target)
    return out


def wikilinks(text):
    """Wikilink targets ([[target#heading|alias]], ![[embed]]), without heading or alias.
    In a table the alias bar is escaped ([[note\\|alias]]), which leaves a trailing backslash on the target."""
    return [t.strip().rstrip("\\").strip() for line in prose_lines(text) for t in WIKILINK.findall(line)
            if t.strip().rstrip("\\").strip()]


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def plain(cell):
    return re.sub(r"[`*_]", "", cell).strip().lower()


def index_rows(text):
    """Rows of the index tables in a README (tables with a status column): (first-cell targets, status)."""
    rows, header = [], None
    for line in prose_lines(text):
        if not line.lstrip().startswith("|"):
            header = None
            continue
        row = cells(line)
        if header is None:
            header = [plain(c) for c in row]
            continue
        if all(re.fullmatch(r":?-+:?", c) for c in row if c) or not STATUS_HEADERS & set(header):
            continue
        status = next((row[i] for i, h in enumerate(header) if h in STATUS_HEADERS and i < len(row)), "")
        rows.append((links(row[0]), plain(status)))
    return rows


def exists_exactly(path):
    """Exists with the same letter case in its file name, as Linux and GitHub see it (Windows ignores case).
    Only the last component's case is compared; on Linux exists() already compares every component."""
    # Compare the link as written: Path.resolve() on Windows already returns the case found on disk.
    path = Path(os.path.normpath(path))
    if path.name in ("", ".", ".."):
        return path.exists()
    return path.exists() and path.name in {c.name for c in path.parent.iterdir()}


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # file names may be Chinese; a GBK console must not fail
    root = Path(argv[1] if len(argv) > 1 else "docs")
    if not root.is_dir():
        print(f"kb-check: {root} is not a directory", file=sys.stderr)
        return 2
    root = root.resolve()
    problems, texts = [], {}
    rel = lambda p: p.relative_to(root).as_posix() or "."
    hidden = lambda p: any(part.startswith(".") for part in p.relative_to(root).parts)

    def read(path):
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{rel(path)}: cannot be read as UTF-8 text ({type(error).__name__})")
            return None

    dirs = [root] + sorted(p for p in root.rglob("*") if p.is_dir() and not hidden(p))
    files = [p for p in root.rglob("*") if p.is_file() and not hidden(p)]
    by_name = {}
    for f in files:
        by_name.setdefault(f.name.lower(), []).append(f)
        if f.suffix.lower() == ".md":
            by_name.setdefault(f.stem.lower(), []).append(f)
    documents = 0
    for d in dirs:
        if ARCHIVE.match(d.name):
            problems.append(f"{rel(d)}: archive directory; delete outdated material instead (Git history keeps it)")
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
        for targets, status in index_rows(text):
            for t in targets[:1]:
                path = (d / t).resolve()
                if not exists_exactly(d / t) or path.parent != d or path.suffix.lower() != ".md" or path == readme:
                    continue
                listed.add(path)
                if status not in STATUSES:
                    problems.append(f"{rel(readme)}: {path.name} has status {status or 'none'}; "
                                    "use current or draft, and delete what is outdated")
        for c in children:
            if c.is_file() and c.suffix.lower() == ".md" and c.name != "README.md":
                documents += 1
                if d == root:
                    problems.append(f"{c.name}: the KB root holds only the entry README; move it into a directory")
                elif c.resolve() not in listed:
                    problems.append(f"{rel(readme)}: {c.name} is not listed in the index")
                body = read(c)
                if body is not None:
                    texts[c] = body
            elif c.is_dir() and not ({c.resolve(), (c / "README.md").resolve()} & linked):
                problems.append(f"{rel(readme)}: subdirectory {c.name}/ is not linked")
    for doc, text in texts.items():
        for t in links(text):
            if t.startswith("/"):
                problems.append(f"{rel(doc)}: link {t} starts with /; write it relative to the document")
            elif not exists_exactly(doc.parent / t):
                problems.append(f"{rel(doc)}: link to missing {t}")
        for t in wikilinks(text):
            # Obsidian: a path from the vault root, else from the note's folder; a bare name matches a file of that
            # name anywhere (letter case ignored; with several, Obsidian picks one, so any match passes).
            name = t if Path(t).suffix else t + ".md"
            found = any(exists_exactly(p) for p in (root / name, doc.parent / name))
            if not found and "/" not in t and not by_name.get(t.lower()):
                problems.append(f"{rel(doc)}: wikilink to missing [[{t}]]")
            elif not found and "/" in t:
                problems.append(f"{rel(doc)}: wikilink to missing [[{t}]]")
    for p in problems:
        print(p)
    if problems:
        print(f"kb-check: {len(problems)} problem(s)")
        return 1
    print(f"kb-check: OK ({documents} document(s) in {len(dirs)} director(ies))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
