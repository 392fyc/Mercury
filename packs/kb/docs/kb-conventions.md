# Knowledge-base conventions (pack `kb`)

Installed by Mercury (pack `kb`) at `.mercury/packs/kb/docs/kb-conventions.md`; do not edit the installed copy.
Mercury Issue #736.

A project knowledge base (KB) holds the project's current rules, its progress and its decisions, and the research
and specifications behind them. It fails in two ways that writing more cannot fix: an agent cannot find what is
current, and outdated text stays where it reads as current. These conventions give the KB a read path and a way out
for outdated content. `scripts/kb_check.py` checks the parts a script can decide; review checks the rest.

## Layout

- **One entry page**: the README at the KB root. It leads to three things first: the current rules, the current
  progress and the decision register. History is reached only through the archive's README.
- **The KB root holds only that README** and directories, so the entry page never has to list a document
  that later goes out of date.
- **An index in every directory**: each directory has a `README.md` that says what the directory is for, what to
  read first, and lists every document in it in a table with a status column (`Status` or `状态`). Tables
  without a status column, such as a "read first" list, are not indexes. Subdirectories are linked from it.
- **Numbered directories** (`00-`, `01-`, ...) keep their order in file browsers; a number is used once per level.
- **One archive**: a top-level directory named `NN-archive` (for example `90-archive/`). It holds history: documents
  about retired systems and superseded material moved out of the way. Indexes outside it link only its README, so a
  search can leave the whole directory out.

The project chooses its directory names, which documents are authoritative, and the rules themselves.

## Status

Each document's status lives in one place: the status column of its directory index.

| Status | 中文 | Meaning |
|---|---|---|
| `current` | 现行 | In force; the entry and indexes may link it as a basis for work. |
| `draft` | 草稿 | Being written in an open task; not a basis for work until merged as current. |
| `superseded` | 已取代 | Replaced. The index row names the replacement in a `Superseded by` (`取代者`) column. |
| `history` | 历史 | Kept as a record only. Usually lives in the archive. |

A superseded document also starts with one line naming its replacement, for readers who open it directly:

```markdown
> **Superseded**: the current rule is P3 in `docs/01-rules/principles.md` (2026-11-02, #123). The rest is history.
```

A status changes only in a pull request, together with the change that caused it.

## Rules, decisions and specifications

- **Current rules** say only how things work now, briefly, in one place. Each rule cites the decision it comes
  from. Reasons, discussion and quotations stay in the decision.
- **The decision register** has one row per decision: date, link, what was decided, status, and what it supersedes
  or is superseded by. The user's words are quoted in the decision document, not in the rule.
- **Specifications and milestone plans are change orders.** When one is merged, fold its conclusions into the current
  rules and the progress page, register its decisions, and set its own status to superseded (or history).
- Keep in the current rules what is expensive to rediscover or learned only after a mistake; leave out what the code
  shows. Trim them when they grow.

## Tasks

- A task starts from an Issue that states the goal, the acceptance criteria, what is out of scope and the KB
  documents that govern it (`templates/issue-task.md`).
- Before editing, read those documents. When the change alters a rule, a decision or progress, update the current
  document and mark what it replaces in the same pull request.
- The pull request names its Issue and has one line starting with `KB:` that lists the KB documents it updated, or
  says `KB: no impact` and why (`templates/pull_request_template.md`). With this pack selected, the git-safe pack's
  `guard.ps1 pre-merge` refuses a pull request without both.

## Checks

```
python -B .mercury/packs/kb/scripts/kb_check.py <kb-root>
```

It reports, one line each: a directory without `README.md`; a document other than the README at the KB root; a
document missing from its directory index; a status outside the four above; a superseded row without an existing
replacement; the entry page linking a superseded or history document; an index outside the archive linking into it;
a number repeated within a directory; a subdirectory its parent index does not link; an index row whose link target
is missing (letter case included); a README that is not UTF-8 text. Exit 0 means no problem, 1 problems, 2 an invalid
KB root. Run it in CI on every pull request once the KB conforms.
