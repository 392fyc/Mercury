# Mercury harness

The harness is the source of truth for the skills and files Mercury ships to agent hosts.
Nothing here is read by an agent directly: `scripts/harness/build.py` projects it into the
places the hosts look at (`.agents/skills/`, `.claude/skills/`, repository root files).
Mercury projects into itself the same way a downstream repository does.

## Layout

```
harness/
  manifest.json                 schema 2: modules, skills, hosts, file projections
  core/skills/<skill>/          always projected (module "core", required)
  modules/<module>/skills/<skill>/
  modules/<module>/files/...    plain files projected to a declared repository path
```

Modules: `core` (required: web-research), `plan` (html-plan-zh), `subagent-dev`
(mercury-subagent-driven-development), `caveman` (caveman-toggle, `CLAUDE.local.md.example`),
`dot-link` (dot-link). The html-plan-zh, subagent-dev and caveman contents are generated into
`harness/` by `adapters/*/generate.py`; do not edit those files by hand
(`html-plan-zh/claude-entry.md` is the one hand-written file among them).

## Manifest (schema 2)

```json
{"schema_version": 2, "modules": {
  "plan": {"path": "modules/plan",
           "skills": {"html-plan-zh": {"hosts": ["codex", "claude"]}}},
  "caveman": {"path": "modules/caveman",
              "skills": {"caveman-toggle": {"hosts": ["codex"]}},
              "files": [{"source": "files/CLAUDE.local.md.example", "target": "CLAUDE.local.md.example"}]}}}
```

- `path` is the module directory under `harness/`; `required: true` forces the module on.
- Skill hosts: `codex` projects the whole skill directory to `.agents/skills/<name>/`
  (a top-level `claude-entry.md` is excluded). `claude` writes `.claude/skills/<name>/SKILL.md`:
  the skill's `claude-entry.md` verbatim if present, otherwise an entry generated from the
  `name` and `description` frontmatter lines of its `SKILL.md` plus a pointer to
  `.agents/skills/<name>/SKILL.md`.
- `files` copies `<module>/<source>` to the repository-relative `target`.

## Selecting modules

The target repository declares its choice in `mercury.project.json` at its root:

```json
{"schema_version": 1, "modules": ["core", "plan"]}
```

Unknown module names are an error; required modules are always included.

## Builder

```
python scripts/harness/build.py --target <repo> --check   # compare only; exit 1 on any difference
python scripts/harness/build.py --target <repo> --apply   # write files and the lock
```

`--source <mercury checkout>` selects the harness to project from (default: the checkout that
holds the script). Exit codes: 0 in sync, 1 differences or conflicts, 2 invalid input.

Rules:

- Only manifest-declared paths are written. Every path segment is compared the way Windows
  sees it (trailing dots and spaces dropped, case ignored). A path must be relative, free of
  `..`, drive letters, backslashes, control characters, `:`, `~` (8.3 short names) and Windows
  device names (`CON`, `NUL`, `COM1`...), and must lie outside `.git/` and `harness/`.
  `mercury.project.json` and the lock are reserved.
- Existing ancestors of every target (and of the lock) must be real directories that resolve
  inside the repository: symlinks, junctions and a file where a directory is needed are errors.
  All of this is checked before the first byte is written.
- Deletion is limited to the projection namespace: files under `.agents/skills/` or
  `.claude/skills/`, and single-file targets declared in the manifest. A lock entry anywhere else
  is an error (exit 2), so a tampered lock cannot delete other repository files. Emptied
  directories are removed only below those skill roots; the roots themselves stay. Removing
  a single-file projection from the manifest entirely therefore needs the file deleted by hand.
- A generated Claude entry (no `claude-entry.md`) needs host `codex` as well and single-line
  `name` and `description` frontmatter; otherwise the build fails. Malformed input of any kind
  exits 2 rather than crashing.
- A managed file whose content differs from the hash in the lock was edited by hand: the run
  stops, reports it, and writes nothing. Without a lock entry, an existing file is adopted only
  when its content already equals the projection; otherwise the run stops.
- Files that the manifest or the module selection no longer declares are deleted if unmodified
  (and directories left empty are removed); if modified, the run stops.
- Content is copied byte for byte (line endings are not rewritten). Hashes and comparisons fold
  CRLF to LF in text files, so a Windows checkout with `core.autocrlf` and a Linux checkout agree.
- Output is deterministic: sorted paths, sorted JSON keys.

## Lock

`<repo>/.mercury/harness.lock` is committed. Example (shortened):

```json
{
  "files": {".agents/skills/web-research/SKILL.md": "<sha256>"},
  "manifest_sha256": "<sha256>",
  "project_config_sha256": "<sha256>",
  "schema_version": 1,
  "source": "self"
}
```

`source` is `self` when Mercury projects into itself, otherwise `mercury@<commit>`.

## Changing a skill

Edit the file under `harness/` (or the adapter overlay for generated skills), then run
`--apply`. Editing a projected file directly makes the next `--apply` stop on a conflict.
