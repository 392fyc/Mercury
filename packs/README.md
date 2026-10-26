# Mercury packs

A pack is a directory of content that Mercury ships to agent hosts and to downstream projects: skills,
scripts and guides. `packs/` is the source; nothing here is read by an agent directly. `tools/mercury.py`
installs the packs a project selects into that project, and Mercury installs itself the same way a
downstream project does.

```
mercury.json                    master switch: which packs exist and which are enabled
packs/
  README.md                     this file
  <pack>/
    pack.json                   declares the pack's skills (hosts, optional Claude entry)
    skills/<skill>/SKILL.md     full skill content (plus references, runtime, examples ...)
    scripts/ docs/ ...          plain files, laid out as they should look inside the installed pack
    build/                      Mercury-side generator inputs; never installed
sources/<project>/              external projects exactly as upstream (git submodules, pinned)
tools/mercury.py                the installer (`check`, `apply`)
```

Packs: `core` (required: web-research), `plan` (html-plan-zh), `subagent-dev`
(mercury-subagent-driven-development), `lanes` (lane-handoff skill, the lane scripts and the lane guides),
`dot-link` (dot-link skill, the agents event gateway and dispatcher scripts, the dot-link installer),
`playwright-mcp` (disabled; see below).

Pack-relative paths are the paths inside the installed pack: `packs/lanes/scripts/lane-claim.sh`
is installed as `.mercury/packs/lanes/scripts/lane-claim.sh`, and
`packs/lanes/docs/guides/lane-claim.md` as `.mercury/packs/lanes/docs/guides/lane-claim.md`. Files in a
pack refer to each other with paths relative to the pack (for example `scripts/lib/lane-paths.sh` is
found through `$(dirname "$0")`) and to project files with paths relative to the project root.

## mercury.json

```json
{"schema_version": 1, "packs": {
  "core": {"enabled": true, "required": true},
  "plan": {"enabled": true},
  "playwright-mcp": {"enabled": false}}}
```

Every directory under `packs/` is listed here. A pack with `"enabled": false` cannot be installed by any
project: `tools/mercury.py` refuses a config that selects it. `required: true` forces the pack on for
every project and cannot be combined with `enabled: false`.

## pack.json

```json
{"schema_version": 1, "name": "plan", "skills": {
  "html-plan-zh": {"hosts": ["codex", "claude"], "claude_entry": "claude-entry.md"}}}
```

- `name` must equal the directory name. `skills` maps a skill directory name under `skills/` to its settings.
- `hosts`: `codex` writes a stub to `.agents/skills/`, `claude` writes a stub to `.claude/skills/`.
- `claude_entry` (optional, path inside the skill directory): a Claude-specific entry file. The Claude stub
  takes its description from that file and tells the agent to read it instead of `SKILL.md`.
- A pack without skills has `"skills": {}`.

## Installation

```
python tools/mercury.py check --target <project>   # compare only; exit 1 on any difference
python tools/mercury.py apply --target <project>   # write the installation and the lock
```

`--source <mercury checkout>` selects the packs to install from (default: the checkout that holds the tool).
Exit codes: 0 in sync, 1 differences or conflicts, 2 invalid input.

The project selects packs in `<project>/.mercury/config.json`:

```json
{"schema_version": 1, "packs": ["core", "plan"]}
```

Unknown or disabled packs are errors; required packs are always included.

What is written:

- Every file that Git tracks in `packs/<pack>/` (listed with `git ls-files` in the Mercury checkout, so untracked
  files are never installed; no git, or a source that is not a repository, is an error) except its top-level
  `build/` directory is copied byte for byte to `<project>/.mercury/packs/<pack>/<relative path>`, `pack.json`
  included.
- Each declared skill gets a thin entry per host: `.claude/skills/mercury-<skill>/SKILL.md` and/or
  `.agents/skills/mercury-<skill>/SKILL.md`. A skill whose name already starts with `mercury-` is not
  prefixed twice. The entry's `name` is the entry name; its `description` is the real skill's description
  (block and quoted scalars are folded to one line, control characters are refused) and every other frontmatter
  field of the real entry is copied unchanged (`user-invocable`, `argument-hint`, `allowed-tools` ...); its body says that Mercury installed the skill, names
  the skill directory `.mercury/packs/<pack>/skills/<skill>/`, and tells the agent to read and follow the
  real `SKILL.md` (or the Claude entry), resolving relative paths against that directory. Claude Code substitutes
  the arguments placeholder only in the invoked `SKILL.md`, which is the entry; so when the real entry's body uses
  it, the entry gets an `Arguments:` line that carries the value and says to use it wherever the real entry
  mentions the placeholder.
- `<project>/.mercury/lock.json` records the sha256 of every installed file and the list of files that are executable.

Rules:

- Only these paths are written. Every path segment is compared the way Windows sees it (trailing dots and
  spaces dropped, case ignored), so a segment that ends in a dot or space is refused outright (`mercury-./x`
  would alias `mercury-/x`). A path must be relative, free of `..`, drive letters, backslashes, control
  characters, `:`, `~` (8.3 short names) and Windows device names (`CON`, `NUL`, `COM1`...), and must lie
  outside `.git/`. `.mercury/config.json` and `.mercury/lock.json` are reserved.
- Existing ancestors of every target (and of the lock) must be real directories that resolve inside the
  project: symlinks, junctions and a file where a directory is needed are errors. A link inside a pack source
  is an error too. All checks run before the first byte is written.
- Deletion is limited to two places: files under `.mercury/packs/`, and files inside
  `.claude/skills/mercury-*/` and `.agents/skills/mercury-*/`. A lock entry anywhere else is an error (exit 2),
  so a tampered lock cannot delete other project files. Emptied directories are removed only below those
  roots; the roots (`.mercury/packs`, `.claude/skills`, `.agents/skills`) stay.
- A managed file whose content differs from the hash in the lock was edited by hand: the run stops, reports
  it, and writes nothing. Without a lock entry, an existing file is adopted only when its content already
  equals the installation; otherwise the run stops.
- Files that the pack selection no longer produces are deleted if unmodified; if modified, the run stops.
  Deletes run before writes. A lock entry that differs from a wanted path only by case is a case-only rename:
  the old spelling is deleted and the new one written, so the file is never lost and the lock ends up with the
  new spelling. A lock holding two entries that are one path on Windows is refused.
- Content is copied byte for byte (line endings are not rewritten). Hashes and comparisons fold CRLF to LF in
  text files, so a Windows checkout with `core.autocrlf` and a Linux checkout agree.
- A file is executable when its mode in the Git index of the pack source is 100755, on every platform; that
  one source works the same on Windows, where files have no mode bits. The lock lists the executable files, so
  a change of mode in the index is a difference everywhere. Where the filesystem has mode bits (POSIX),
  `check` also reports `mode: <path>` when an installed file's executable bit disagrees, and `apply` repairs
  it without rewriting the content. On Windows the bit cannot be set or read, so only the lock is compared;
  stage the bit with `git update-index --chmod=+x` for both the pack source and the installed copy.
- Output is deterministic: sorted paths, sorted JSON keys.

## Changing a pack

Edit the file under `packs/<pack>/` (or the generator input under `packs/<pack>/build/` for generated skills),
then run `python tools/mercury.py apply --target .`. Never edit `.mercury/packs/` or the stubs by hand: the next
`apply` stops on the conflict. Tests of pack scripts live in `tests/lanes/` and `tests/dot/` and run against the
installed copy in `.mercury/packs/`, so run `apply` before them.

## Generated skills

`packs/plan/build/generate.py` writes `packs/plan/skills/html-plan-zh/` and
`packs/subagent-dev/build/generate.py` writes `packs/subagent-dev/skills/mercury-subagent-driven-development/`.
Both check that the pinned submodule under `sources/` is at the recorded commit, and `--check` compares the
committed files with the generated ones (line endings normalised). Only `claude-entry.md` in the plan pack is
written by hand. Provenance, pins and update steps are in each `build/UPSTREAM.md`.

## External integrations

Each external integration is a thin translation layer: it converts an interface and holds no business logic.

- Every external integration stays under 200 lines of code, in any language. That covers the translation layers
  in `packs/<pack>/build/` (generators, patches, overlays for pinned upstream submodules; never installed) and
  runtime integration wrappers such as `packs/playwright-mcp/` (JavaScript). Internal `scripts/` and `tools/`
  tooling is exempt.
- Mount modes (see `.mercury/docs/DIRECTION.md`, section "挂载方式（三类并列）"):
  1. **Git submodule (default)**: pinned under `sources/`; Mercury's changes live only in the pack's `build/`
     (patches, `overlay/`, `generate.py`); the generator output is compared by `--check` in CI.
  2. **`uvx git+SHA`, runtime only**: referenced as `uvx --from git+<repo>@<SHA>` (limited exception from the
     Phase 2 ADR `pixel-animation-workflow` section 7.2.1; the first user, `gpt-image-2`, was removed in #586).
  3. **npm-version-pinned MCP server, runtime only**: mounted by exact npm version (never `@latest`). The first
     user is `packs/playwright-mcp/` (Issue #154).
- Modes 2 and 3 need a permissive license, provenance (an `.mercury/state/upstream-manifest.json` entry and a
  `UPSTREAM.md`) and drift monitoring (`scripts/upstream-drift-check.sh`).

`playwright-mcp` is a config-gate wrapper around microsoft/playwright-mcp (Apache-2.0) that enforces the ADR
section 4.2 security red lines. It is disabled in `mercury.json`: no project can install it. Its content is the
former `adapters/playwright-mcp/`, unchanged apart from path references.
