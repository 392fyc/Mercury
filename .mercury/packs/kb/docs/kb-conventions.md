# Knowledge-base conventions (pack `kb`)

Installed by Mercury (pack `kb`) at `.mercury/packs/kb/docs/kb-conventions.md`; do not edit the installed copy.
Mercury Issue #736.

A project knowledge base (KB) holds the project's current rules, its progress and its decisions, and the research
and specifications behind them. It fails in two ways that writing more cannot fix: an agent cannot find what is
current, and outdated text stays where it reads as current. Agents find KB text by search and by following links;
neither sees a status label, so an outdated document that stays in the working tree is found and read like a current
one. These conventions therefore give the KB one read path and keep only what is in force: outdated or wrong content
is deleted. Git history, Issues and pull requests are the record of what came before. `scripts/kb_check.py` checks
the parts a script can decide; review checks the rest.

## Layout

- **One entry page**: the README at the KB root. It leads to three things first: the current rules, the current
  progress and the decision register.
- **The KB root holds only that README** and directories, so the entry page never has to list a document
  that later goes out of date.
- **An index in every directory**: each directory has a `README.md` that says what the directory is for, what to
  read first, and lists every document in it in a table with a status column (`Status` or `状态`). Tables
  without a status column, such as a "read first" list, are not indexes. Subdirectories are linked from it.
- **Numbered directories** (`00-`, `01-`, ...) keep their order in file browsers; a number is used once per level.
- **No archive.** A directory of old material is found by search like any other, so the KB has none (no
  `NN-archive`, `_archive`, `archive` or `history` directory). Material that must stay citable without being a rule, such as a frozen review copy with its
  checksum list, is cited by a permanent link to a fixed commit (`https://github.com/<owner>/<repo>/blob/<sha>/<path>`)
  or attached to its Issue.

The project chooses its directory names, which documents are authoritative, and the rules themselves.

## Status

Each document's status lives in one place: the status column of its directory index.

| Status | 中文 | Meaning |
|---|---|---|
| `current` | 现行 | In force; a basis for work. |
| `draft` | 草稿 | Being written in an open task; not a basis for work until merged as current. |

There is no third status. A document, rule or register row that is outdated or wrong is deleted in the pull request
that makes it so, together with every link to it.

## Rules, decisions and specifications

- **Current rules** say only how things work now, briefly, in one place. Each rule cites the decision it comes
  from. Reasons, discussion and quotations stay in the decision's source.
- **The decision register** lists only decisions in force, one row each: number, date, what was decided, and its
  source. When a new decision overturns one, delete the old row; the new row does not name the old number, which no
  longer exists in the working tree. Numbers are never reused: a deleted row leaves its number unused.
- **The user's words are kept** in the decision's source: the Issue or pull request where the decision was made
  (quote them in its body), or a document that stays current. When the document that quoted them is deleted, point
  the source at a permanent link to a fixed commit that still contains it.
- **Specifications and milestone plans are change orders.** When one is merged, fold its conclusions into the current
  rules and the progress page, register its decisions, and delete the specification.
- Keep in the current rules what is expensive to rediscover or learned only after a mistake; leave out what the code
  shows. Trim them when they grow.

## Links

- Link KB documents with relative Markdown links (`[text](../01-rules/README.md)`), never from `/`. They work for
  agents, on GitHub and in Obsidian. Obsidian writes `[[wikilinks]]` by default; turn off **Use [[Wikilinks]]** under Settings → Files
  and links to make it write Markdown links.
- A link to a deleted document is an error. `kb_check.py` checks every link in every KB document, wikilinks included.

## Tasks

- A task starts from an Issue that states the goal, the acceptance criteria, what is out of scope and the KB
  documents that govern it (`templates/issue-task.md`).
- Before editing, read those documents. When the change alters a rule, a decision or progress, update the current
  document and delete what it makes outdated, with the links to it, in the same pull request.
- The pull request names its Issue and has one line starting with `KB:` that lists the KB documents it updated or
  deleted, or says `KB: no impact` and why (`templates/pull_request_template.md`). With this pack selected, the
  git-safe pack's `guard.ps1 pre-merge` refuses a pull request without both.

## Checks

```
python -B .mercury/packs/kb/scripts/kb_check.py <kb-root>
```

It reports, one line each: a directory without `README.md`; a document other than the README at the KB root; a
document missing from its directory index; a status other than the two above; an archive directory (`NN-archive`,
`_archive`, `archive`, `history`); a number repeated within a directory; a subdirectory its parent index does not
link; a link in any KB document, Markdown or wikilink, whose target is missing (letter case of the file name
included) or that starts with `/`; a KB document that is not UTF-8 text. Links inside code and HTML comments are not
checked; a wikilink resolves from the KB root, then from its own directory, then by file name anywhere in the KB, as
Obsidian finds it. Exit 0 means no problem, 1 problems, 2 an invalid KB root. Run it in CI on every pull request once
the KB conforms.

**Upgrading from the first version (#737)**, which allowed `superseded` and `history` and one `NN-archive`: delete the
documents with those statuses and the archive directory, point what cited them at fixed-commit links, and register
nothing in their place. `kb_check.py` fails until that is done.
