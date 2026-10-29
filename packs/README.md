# Mercury packs

A pack is a directory of content that Mercury ships to agent hosts and to downstream projects: skills,
scripts, guides, roles, rules and fragments of the AGENTS.md / CLAUDE.md managed section. `packs/` is the source; nothing here is read by an agent directly. `tools/mercury.py`
installs the packs a project selects into that project, and Mercury installs itself the same way a
downstream project does.

```
mercury.json                    master switch: which packs exist and which are enabled
packs/
  README.md                     this file
  <pack>/
    pack.json                   declares the pack's skills (hosts, optional Claude entry)
    skills/<skill>/SKILL.md     full skill content (plus references, runtime, examples ...)
    roles/<role>.md             role source, generated into both host formats (core)
    rules/mercury-*.rules       Codex rules, installed to .codex/rules/ (core)
    agents.md  claude.md        fragments of the managed section of AGENTS.md / CLAUDE.md (core must have both)
    scripts/ docs/ ...          plain files, laid out as they should look inside the installed pack
    build/                      Mercury-side generator inputs; never installed
sources/<project>/              external projects exactly as upstream (git submodules, pinned)
tools/mercury.py                the installer (`check`, `apply`, `install`) and the `source` commands
```

Packs: `core` (required: web-research, test-file-delivery, the five roles, Git safety rules, task and ownership contracts, read-only guard, wait guard), `plan` (html-plan-zh), `subagent-dev`
(mercury-subagent-driven-development), `lanes` (lane-handoff skill, the lane scripts, the lane guides, the lane contract and the lane-bus prototype with its
Claude Code plugin, `docs/guides/lane-bus.md`),
`dot-link` (dot-link skill, the agents event gateway and dispatcher scripts, the dot-link installer),
`deploy` (the two workflows that deliver released Mercury commits to a downstream project; see "The deploy pack"),
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
  "playwright-mcp": {"enabled": false}},
 "sources": [{"name": "superpowers", "url": "https://github.com/obra/superpowers.git", "commit": "<40 hex>",
              "license": "MIT", "added": "2026-10-26", "rationale": "..."}]}
```

`sources` records every pinned external project under `sources/` (see "Sources" below). It is bookkeeping: the
`mercury_json_sha256` value in an installation lock is computed without it, so `source add` and `source update` never
make `check` fail.

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
python tools/mercury.py install --target <project> [--packs core,lanes] [--source-commit <sha>] [--dry-run]
python tools/mercury.py check   --target <project> [--source-commit <sha>]   # compare only; exit 1 on any difference
python tools/mercury.py apply   --target <project> [--source-commit <sha>]   # write the installation and the lock
```

`--source <mercury checkout>` selects the repository to install from (default: the checkout that holds the tool).
`install` and `apply` also take `--adopt-local` (see "The project area").
Exit codes: 0 in sync, 1 differences or conflicts, 2 invalid input.

Two ways to read the packs:

- **Mercury installing itself** (`--target` is the source checkout): the file list comes from the Git index and the
  content from the working tree, as before. `--source-commit` is an error here.
- **Any other project**: the packs, their modes and `mercury.json` (the master switch) are read from one commit of the
  source repository (`git ls-tree` and `git cat-file`), never from its working tree, so local edits and untracked files
  cannot leak in. `install` uses `--source-commit` or else the source `HEAD`; `check` and `apply` use
  `--source-commit` or else the commit recorded in the target lock (`source_commit`; `HEAD` when there is no lock).
  The lock records `source_repo` (`owner/name`, from the `origin` remote of the source; the URL itself is never printed
  or stored) and `source_commit`. **Release gate:** writing (`install`, `apply`) requires the commit to be reachable
  from `origin/master` of the source (`git merge-base --is-ancestor <commit> refs/remotes/origin/master`, as of the last
  fetch). Mercury's `master` is its single trunk, where every merged pull request is a release, so a commit that is only on
  a feature branch, the frozen `develop` or nowhere on origin is refused. `--dry-run` and `check` accept it and annotate the line
  `source commit <sha> (NOT on origin/master ...)`. There is no override flag: the only reason to use an unreleased
  commit is to preview it, and `--dry-run` does that. Mercury installing itself is not gated.
  Every run prints `mercury: source commit <sha>`. A tree entry that is not a regular file (link, submodule) is an
  error. The target must be the root of a Git work tree.

`install` is `apply` for a project that may not have a configuration yet. Without `.mercury/config.json` it creates one
from `--packs` (default: only `core`; `core` is always included); afterwards the file belongs to the project and the
tool only reads it. With an existing configuration, a `--packs` list that selects a different set is an error.

Before any write (and in `check`), every managed path, `.mercury/config.json`, the lock, `AGENTS.md` and `CLAUDE.md` is
passed through `git check-ignore` of the target. An ignored path stops the run (exit 2) because the file would never be
committed. `--dry-run` prints the plan and writes nothing: the files to create, update, adopt and delete; where the
managed section goes in `AGENTS.md` and `CLAUDE.md`; the tracked text files of the target that mention a path about to be
removed; the workflows under `.github/workflows/` that still refer to the v1 template (`mercury-template`, `mercury-sync`,
`sync-project-template`, `sync-downstream-pr`, `verify-project-template`; this tool never edits them); and any managed
path that the ignore rules cover. It exits 1 when there is a conflict or an ignored path.

### The project area

A project keeps roles and skills of its own next to Mercury's, in an area that Mercury updates never touch but whose
outputs the same tool generates, checks and retires. The project owns `.mercury/local.json`:

```json
{"schema_version": 1,
 "roles":  {"game-analyst": ".mercury/local/roles/game-analyst.md"},
 "skills": {"sot-designlib": {"path": ".agents/skills/sot-designlib", "hosts": ["claude", "codex"]}}}
```

- Names: lowercase letters, digits and `-`, starting with a letter, at most 64 characters, and never starting with
  `mercury-` (that prefix is Mercury's namespace, so a project name can never collide with a pack output).
- `roles`: the source is exactly `.mercury/local/roles/<role>.md`, in the role source format described under "What is
  written" (the same parser and the same renderer as the pack roles, so the two cannot diverge). It becomes
  `.claude/agents/<role>.md` and/or `.codex/agents/<role>.toml`, without a prefix, depending on the host sections it
  declares. The optional `attribution` (a block `attribution: |` with lines indented by two spaces, or a double-quoted
  string; at most 20 lines and 2000 characters) is written verbatim as a comment at the top of both outputs: `# ` lines
  at the top of the TOML file, and one `<!-- -->` block right after the frontmatter of the Markdown file (the frontmatter
  must stay first). It is refused, not escaped, when it contains a control, hidden or line-separator character or one of
  `<!--`, `-->`, `--!>`, so it can never end its comment; a TOML comment ends only at a line break, which every line gets
  its own `#` for. Both outputs are read back: the TOML must parse to exactly the declared keys.
- `skills`: the full skill stays in the declared `path`, a directory with a `SKILL.md` whose `name` equals the manifest
  key. Each host in `hosts` gets the same thin entry a pack skill gets (`.claude/skills/<skill>/SKILL.md`,
  `.agents/skills/<skill>/SKILL.md`; description and other frontmatter fields carried over, the arguments line when the
  skill uses it), pointing at `path` and saying that the skill belongs to the project. A project skill writes its paths
  relative to the repository root unless it says otherwise (the other host reads the same file in place; a Markdown
  link stays relative to its own file), and its entry says so; a pack skill's entry resolves paths against the pack
  skill's directory instead. A host whose entry location is the
  skill directory itself (for example Codex with `path` `.agents/skills/<skill>`) gets no entry. A directory directly
  under `.claude/skills/` or `.agents/skills/` must carry the skill's own name.
- Sources are project-owned input: in both source modes (Mercury on itself, and an external target read from a Mercury
  commit) they are read from the target's working tree, not from the Mercury commit, which supplies only the tool and
  the renderers. A source must be a regular file inside the target reached through real directories (no symbolic link or
  junction), outside `.git/` and outside Mercury's namespace (case and trailing dots folded as Windows does). For an
  external target the outputs and sources also go through the ignore check.
- The lock records the outputs and the sources (`.mercury/local.json` included) with their hashes in a separate
  `local` section, apart from the pack files. `check` (and therefore `Mercury verify` and the update merge) compares the
  outputs with what the sources produce. For an external target it renders with the tool of the commit it compares with
  (`--source-commit`, else the recorded one): when that commit's `tools/mercury.py` differs from the running one, `check`
  runs that commit's copy (the commit must be in the history of `--source` HEAD), so a release that changes a renderer
  does not make existing installations look modified before they update (#708). The checking logic is then that
  commit's too: a lock that names an older released commit is checked by that older tool, and only the update merge
  (condition 3) refuses going back.
- Lifecycle, all through `apply` (or `install`): create = add the manifest entry and the source; update = change the
  source; retire = remove the entry (or the whole manifest). Retiring deletes only outputs that the `local` section
  recorded, at the four output locations (`.claude/agents/<name>.md`, `.codex/agents/<name>.toml`,
  `.claude/skills/<name>/SKILL.md`, `.agents/skills/<name>/SKILL.md`; an emptied `<name>/` entry directory goes too), and
  never a path that is a source now. A lock whose `local` section names anything else is refused (exit 2).
- A hand-edited output stops the run (exit 1, nothing written), for an update as for a retire.
- `--adopt-local` (only `install` and `apply`): the first time an output path already holds a different file that the
  lock does not record (for example the project's hand-written `.codex/agents/<role>.toml`), the run stops with a
  conflict and writes nothing; with the flag that file is replaced by the generated one. The flag never takes over a link,
  a directory, a Mercury-managed path or a recorded output that was edited by hand. A file that already equals the
  output is taken into the lock without the flag.
- Update pull requests of the deploy pack never change the manifest, the sources or the set of outputs. When a release
  renders the unchanged sources differently, they carry the regenerated outputs and their hashes in the `local` section
  (see "What the update job does").

### Adopting a v1 template installation

A project with `.codex/mercury-template.lock` (schema 1) was installed by the old template synchroniser. `install` takes
its files over. A file listed in the v1 lock (only `agents/mercury-*.toml`, `rules/mercury-*.rules` and
`project/mercury-*.md` are accepted) is adopted while its content still hashes to the v1 lock value: sha256 of the raw
bytes, as the retired v1 synchroniser computed it. A difference that is only CRLF against LF, as `core.autocrlf` makes,
also counts as unmodified. An adopted file that the new installation also ships is replaced; one it no longer ships
(`.codex/project/mercury-*.md`) is deleted, and its directory is removed if that empties it. The v1 lock is deleted
last, so an interrupted run can be repeated. Any other content stops the run with nothing written. A v1 lock entry whose
spelling differs from a path the new installation ships only in case (one file on Windows) is a conflict, never a
deletion; fix the spelling first.

## The deploy pack

`deploy` is optional. It makes a project take every released Mercury commit on its own, through a pull request, with no
human in the loop when (and only when) the conditions below hold. It installs two managed workflow files:

- `.github/workflows/mercury-update.yml` (jobs `dispatch` and `Mercury update`): runs on `schedule`
  (`params.update_schedule`) and on `workflow_dispatch`; one run at a time (`concurrency`); top-level
  `permissions: contents: read`. The `update` job sets up Python, checks out Mercury at `master` without credentials, creates
  a GitHub App token (as late as possible, right before the first step that needs it), checks out the project at the
  integration branch with it, then runs `tools/mercury_update.py publish` and, unless that just published, `merge`. The
  `dispatch` job is explained under "Scheduled runs and the default branch".
- `.github/workflows/mercury-verify.yml` (job `Mercury verify`): runs on `pull_request` to the integration branch with
  a read-only token, checks out the proposed head and Mercury `master` without credentials, runs `tools/mercury.py check`
  and `tools/mercury_update.py verify`. There is no `pull_request_target`, and no pull-request-controlled text is written
  into a command line (the head branch name goes through an environment variable).

Both are *rendered* files: the pack holds templates (`templates/*.yml`, with `@@NAME@@` placeholders) and `pack.json`
maps each destination to its template in a `rendered` object. A destination must be `.github/workflows/mercury-<name>.yml`.
`tools/mercury.py` fills the placeholders from the project's params, hashes the result into the lock like any other file,
and `check` renders again to compare. Values are written as JSON strings (valid YAML double-quoted scalars), so no param can
end its scalar or add a line; non-ASCII is written as escapes.

### Params (in `.mercury/config.json`)

```json
{"schema_version": 1, "packs": ["core", "deploy"],
 "params": {"integration_branch": "develop", "required_checks": ["Godot headless", "Repository contract"],
            "update_schedule": "17 3 * * *"}}
```

- `integration_branch` (required with `deploy`): the branch update pull requests target. Letters, digits, `.`, `_`, `/`,
  `-`; at most 100 characters; git branch-name rules (no `..`, no empty or dot-leading segment, no `.lock` suffix; a test
  checks every accepted name against `git check-ref-format --branch`); not a pseudo ref (`HEAD`, `FETCH_HEAD`, `ORIG_HEAD`,
  `MERGE_HEAD`, `CHERRY_PICK_HEAD`), not under `refs/`, and neither `mercury` nor under `mercury/` (reserved for update
  branches: a ref cannot be both a file and a directory). These reserved names are compared ignoring case, because on a
  case-insensitive file system `Mercury` collides with `mercury/` and `head` with `HEAD`.
- `required_checks` (required with `deploy`, may be `[]`): the project's own check names that must be green on the head
  before a merge. At most 20 distinct names of 1-100 characters on one line, no `${{`, and no character that draws nothing or
  counts as a space or control. That is decided by Unicode general category, so no list can miss one: Cc, Cf (zero-width and
  direction marks, the tag block, the Arabic number signs, ...), Cs, Co, Cn (unassigned), Zl, Zp, Me and every Zs except the
  ordinary space are refused, plus a few fillers and selectors that Unicode files as letters or marks (U+034F, U+115F, U+1160,
  U+17B4, U+17B5, U+180B-U+180F, U+2800, U+3164, U+FE00-U+FE0F, U+FFA0, U+E0100-U+E01EF). A test walks every code point.
  **Look-alike letters are not detected:** a Cyrillic capital I (U+0406) is an ordinary letter that looks like the Latin I, so a
  name that uses it passes. A name can be claimed by a commit status (see merge condition 5), so a project must review every
  change to `required_checks` like code. (The answer for a character that only a very new Unicode version assigns can differ
  between Python versions, because unassigned code points are refused.)
- `update_schedule` (optional, default `17 3 * * *`, UTC): five space-separated cron fields of numbers, `*`, ranges, `/n`
  steps and commas, each number in range (day of week 0-6).

Because `.mercury/config.json` may not change inside an update pull request, changing a param is a normal project pull
request followed by `python tools/mercury.py apply` from a released Mercury commit.

### The GitHub App (set up once by the project owner)

Events caused by the workflow's own `GITHUB_TOKEN` do not create workflow runs, except `workflow_dispatch` and
`repository_dispatch` (GitHub docs, "Triggering a workflow"; the source text is `data/reusables/actions/actions-do-not-trigger-workflows.md`
in github/docs). A pull request opened with `GITHUB_TOKEN` therefore either starts no `pull_request` workflows or, in the
newest docs, starts them waiting for a person to approve them; either way the checks would not run by themselves. The workflow
therefore uses a GitHub App installation token (`actions/create-github-app-token@v3`; input names verified in its `action.yml`
and README at v3).

- Create the App on the owner account (webhook off, installable on that account only) with these repository permissions:
  Contents read and write, Pull requests read and write, Workflows read and write (the managed files live under
  `.github/workflows/`), Checks read-only, Commit statuses read-only; Metadata read-only is added automatically.
- Install it on the downstream repositories only (choose "Only select repositories").
- In each downstream repository: variable `MERCURY_APP_CLIENT_ID` (the App's Client ID, not the App ID) and secret
  `MERCURY_APP_PRIVATE_KEY` (the whole `.pem` file). The key is never committed or sent anywhere else.
- The workflow narrows the token to the current repository and to `permission-contents: write`,
  `permission-pull-requests: write`, `permission-workflows: write`, `permission-checks: read` and
  `permission-statuses: read`, even if the App holds more. Mercury itself is public, so its checkout needs no token.

### Scheduled runs and the default branch

GitHub runs `schedule` workflows only from the latest commit of the repository's default branch ("Events that trigger
workflows", docs.github.com). A project may integrate somewhere else (SoT's default branch is `main`, it integrates on
`develop`). So the `dispatch` job, which runs only when `github.event_name == 'schedule'` and `github.ref_name` is not
the integration branch, does one thing: `gh workflow run mercury-update.yml --ref <integration_branch>` with the workflow's
`GITHUB_TOKEN`, then ends. A `workflow_dispatch` created with `GITHUB_TOKEN` does start a run (same docs). Creating the
event needs the repository permission Actions: write (github/docs `src/github-apps/data/fpt-2026-03-10/server-to-server-permissions.json`,
entry `create-a-workflow-dispatch-event`), which is granted to that job only (`permissions: actions: write`; the top-level
`contents: read` is unchanged). The dispatched run uses the integration branch's own copy of the file, so after the managed
files have reached the integration branch every update runs the version that branch holds. No App token, variable or secret
is referenced in the `dispatch` job. The two job conditions are exact complements, so exactly one job runs: a scheduled run
on the integration branch, and any manual dispatch, run `update`.

Two properties of this design that are worth knowing:

- Anyone with write access to the repository can start `workflow_dispatch` on any branch (the Actions tab, or `gh workflow run
  --ref <branch>`). That runs the `update` job with the App token, using the workflow file of the branch they picked. This is
  inside the threat model that already trusts write access (a writer can push a workflow file and run it anyway); it is
  mentioned so nobody assumes the token is reachable only from the integration branch.
- The `dispatch` run and the run it starts share the one `concurrency` group (`mercury-update`, `cancel-in-progress: false`).
  GitHub keeps at most one running and one pending run per group, and a newer pending run replaces an older pending one. A
  scheduled tick that arrives while an update is running, or while another is already pending, can therefore be replaced
  and skipped. Nothing is lost: the update is idempotent and the next schedule tick catches up.

Because the scheduled copy on the default branch can be older than the tool, **the command line of `tools/mercury_update.py`
is a compatibility surface.** An older workflow copy calls the latest tool from Mercury's `master`. Flags are only ever added,
and only as optional; an existing flag is never removed, renamed or made required, and an exit code keeps its meaning.
`tests/tools/test_mercury_update.py` pins the argument lists of the released workflows and the set of required options.

### What the update job does

`publish` reads the newest commit of Mercury's `origin/master` and runs `tools/mercury.py apply` on a branch
`mercury/update-<12 hex>`. If the project already matches, nothing happens. Otherwise it commits as
`mercury-update@users.noreply.github.com`, pushes, and opens the one update pull request (source commit link, upstream
change summary, files taken over or deleted). If an update pull request already exists and is behind Mercury `master`,
the new state is committed on top of its branch with a regular push; the tool has no force option, so a regular push can
never discard anyone's commits. It builds only on a branch that is same-repository, named `mercury/update-<12 hex>`, and
whose every commit beyond the base carries the tool's committer identity; any other branch is left alone and reported. That
identity test is a courtesy heuristic and not a security boundary (a committer email is free text and can be forged). What makes
a forged branch harmless is that a regular push never discards anything and that `merge` re-validates the content of the head
whoever wrote it.

Credentials: `actions/checkout@v7` keeps the token in a separate file under `$RUNNER_TEMP` and wires it through git
`includeIf.gitdir:` entries for the repository's `.git` and for `.git/worktrees/*` (checkout's `src/git-auth-helper.ts`,
`includeIf.gitdir:${gitDir}` and `${gitDir}/worktrees/*`). `publish` fetches and pushes from the main checkout of `--target`
(`git -C target`), which the first entry covers, so the push path needs nothing more; the only worktree the tool creates is the
read-only one that `merge` uses for `mercury.py check`, and it never talks to the remote.

`merge` squash-merges that pull request with `gh pr merge --match-head-commit <head>` only when all of these hold:

1. It is the only open pull request from a `mercury/update-*` branch, and that branch is in the same repository.
2. Every path changed between the merge base and the head is Mercury-managed, and a path may only change in the way its
   kind allows:
   - a path the **new** lock lists may be created or modified (its content is verified by condition 4);
   - a path that only the **old** lock or the **v1** lock lists (a file this release stops shipping, a v1 file being adopted
     away, the v1 lock itself) may only be **deleted**: nothing re-reads its content, so keeping it with other bytes would carry
     anything through;
   - `.mercury/lock.json` may change (condition 4 checks it);
   - `AGENTS.md` / `CLAUDE.md` may change only between the markers (compared outside the markers byte for byte; the section
     must hash to the value in the lock; deleting either file is refused);
   - `.mercury/config.json` must not change, and no path outside these kinds may change;
   - nothing of the project area may change: `.mercury/local.json`, anything under `.mercury/local/`, any path that the old
     or the new lock records in its `local` section (outputs and sources), and the `local` section itself. One exception
     (#708): when both locks record the same sources with the same hashes and the same outputs, an output may be
     **modified** (never created or deleted), together with its hash in the `local` section. That is a Mercury change to
     the role or entry renderer reaching the outputs of unchanged sources; condition 4 proves the bytes. Anything else in
     the project area (a changed source, a new or retired output) still needs the project's own `apply` in an ordinary
     pull request.
   Every changed path other than a deletion must be a regular file (mode 100644 or 100755), so a symlink or submodule
   in place of a managed path is refused.
3. The new lock's `source_commit` is on Mercury's `origin/master` and descends from the old lock's commit (or there is no
   old lock, on a first installation), with the same `source_repo`.
4. `tools/mercury.py check` passes on a read-only checkout of that head, so the file contents are what that Mercury
   commit produces (a pull request that edits `mercury-verify.yml` to always pass cannot vouch for itself).
5. Every name in `params.required_checks` and `Mercury verify` has a completed successful result on the exact head.
   Missing, pending, failed, skipped or cancelled means stop. Every page is read; incomplete pagination is a refusal.
   Who may satisfy a name:
   - `Mercury verify` only by a **check run created by GitHub Actions** (app slug `github-actions`), which is what the managed
     job is. A commit status with that name, or a check run of another app, does not count, because anyone with write access
     can post those in one call.
   - The project's own `required_checks` may be satisfied by a check run of any app or by a commit status (external CI is a
     legitimate source and the project chose the names). The project is responsible for those names not being claimable by
     an untrusted writer. When one name is reported by several sources, all of them must be green.
6. The pull request is open, not a draft and GitHub reports it mergeable. `mergeable: false` (a conflict with the base) stops
   with the instruction to close the pull request and delete its branch, after which the next `publish` opens a fresh one;
   `mergeable: null` (not computed yet) stops and says the next run will look again.

Exit codes of `tools/mercury_update.py`: 0 nothing to do, published or merged; 1 stopped (the reason is printed: a condition
is not met, the queue is not unique, a branch is not the tool's); 2 usage or safety error (bad arguments, a cross-repository
update branch, incomplete pagination, unreadable data). Credentials are redacted from all output.

## Sources

`sources/<name>/` holds one pinned external GitHub project as a shallow Git submodule.

```
python tools/mercury.py source add <name> <https://github.com/owner/repo> --rationale "<why>" [--ref <ref>]
python tools/mercury.py source update <name> [--to <ref>]
```

- `add` asks `gh api repos/{owner}/{repo}/commits/{ref}` for the full commit id (default ref `HEAD`) and
  `gh api repos/{owner}/{repo}/license?ref=<sha>` for the SPDX license at that commit. Only MIT, Apache-2.0,
  BSD-2-Clause, BSD-3-Clause, ISC, 0BSD and Unlicense are accepted. It fetches that one commit (`--depth 1`) into
  `sources/<name>/` with the layout `git submodule add` produces: the Git directory under the `modules/` directory of
  the superproject (per work tree), a `.git` file with a relative `gitdir:` and a relative `core.worktree` in the module
  config. It appends the `.gitmodules` entry (with `shallow = true`), writes the `submodule.<path>.url` and `.active`
  values that `git submodule init` writes into the shared Git config, and records `name`, `url`, `commit`, `license`,
  `added` and `rationale` in `mercury.json`. It prints the paths to stage and **never writes the Git index**; stage with
  `git-safe.ps1 add .gitmodules mercury.json sources/<name>`. Only GitHub repositories are supported (the repository
  name may not be `.`, `..` or end in `.git`). A step that fails after the directories exist reports both leftover paths,
  `sources/<name>` and its Git directory, for you to inspect and remove. If an add is abandoned, also remove the two shared
  config keys by hand: `git config --unset submodule.sources/<name>.url` and `git config --unset submodule.sources/<name>.active`
  (and the `.gitmodules` entry and `mercury.json` record if they were written).
- `update` requires the checkout to be at the recorded commit and free of local modifications (`git status --porcelain`
  empty, untracked files included), resolves the new commit (license checked again), refuses
  a commit that is not a descendant of the recorded one, prints the upstream change summary (`gh api compare`), moves
  the checkout, updates `mercury.json`, and runs `build/generate.py --check` of every pack whose `build/` mentions the
  source. A mismatch is reported and stops the command with exit 1: the generators pin the commit themselves, so update
  the pin and regenerate by hand. It never regenerates or stages anything.
- All network and Git calls of these two commands go through one small class, `Ext`, so tests can replace it.

The project selects packs in `<project>/.mercury/config.json`:

```json
{"schema_version": 1, "packs": ["core", "plan"],
 "linked_repos": [{"name": "Notes_KB", "purpose": "...", "locate": "...", "use": "...", "rules": "..."}],
 "params": {"guard_write_scripts": ["deploy.sh"], "guard_write_actions": {"worktree.ps1": ["Create", "Remove"]}}}
```

Unknown or disabled packs are errors; required packs are always included. Unknown top-level keys are errors.

- `linked_repos` (optional): other repositories the project works with. Each entry has exactly `name` (plain, unique
  ignoring case), `purpose` (what the repository is responsible for), `locate` (how to find it: a config key or a
  description, never a machine path), `use` (how to use it quickly: skills, scripts, command entry points) and `rules`
  (what work in it must follow, for example read and write rights). The older field `access` is gone; move its text to
  `rules`. Every text is one line of at most 400 characters without control or hidden characters (bidi controls, byte
  order mark, lone surrogates), comment markers or `MERCURY:`, and without a machine path anywhere in it: `file:`, a
  drive letter (`C:/x`, `D:x`, also after punctuation), `%VAR%`, `$HOME`-style variables, `~`, or a slash or backslash
  that starts a path (URLs and words such as `and/or` or `docs/a.md` are fine). The list is rendered into the managed
  section of AGENTS.md as "Linked repositories".
- `params.guard_write_scripts` (optional): file names ending in `.sh`, `.ps1` or `.py` (no directories; the guard
  recognises no other kind) of write-type scripts that the read-only guard
  blocks in addition to its built-in list.
- `params.guard_write_actions` (optional): `{"<script>": ["<Action>", ...]}` for a script that has read-only and writing
  actions. The guard blocks such a script only when its `-Action` value is one of the listed names, compared ignoring case
  as PowerShell does (`-Action X`, `-Action:X`, or a prefix of the parameter name such as `-Act X`), whether the script is
  run directly or through `pwsh`/`powershell -File`. The guard is not a PowerShell parser, so it accepts only arguments
  it reads the same way PowerShell does and blocks everything else: after the script name only plain ASCII may follow,
  without `#` (comments, also `<# #>`), the backtick escape, `@` (splatting), `$`, `(`, `)`, `{`, `}`, `;`, `|`, `&`, `<` or
  `>` (PowerShell also reads the curly quotes as quotes); a quoted token that starts with a dash and the `--` and `--%`
  tokens are refused (PowerShell does not read them as parameter names); and exactly one `-Action` with a plain name is
  required (none, which leaves a positional or splatted value possible, or two, block). The en dash, em dash and
  horizontal bar that PowerShell accepts in front of a parameter name count as `-`. Script names follow the rule of
  `guard_write_scripts`; action names are a letter followed by letters or digits; at most 20 scripts with 1-20 distinct
  actions each; a script may be neither in `guard_write_scripts` nor on the guard's built-in list (both block it
  outright). The guard reads both params from the project's `.mercury/config.json` when the hook runs: a malformed action
  list for a named script blocks every call of that script, a list of script names under `guard_write_actions` blocks
  those scripts outright, and any other malformed value is ignored (the installer refuses it on every check and apply).
  What this costs a read-only role, so its instructions can say it up front: a script whose default action is read-only
  still needs an explicit `-Action` (for example `-Action List`), because a call without one is blocked; a redirect such as
  `2>&1`, `2>$null` or `> $null` after such a script is blocked although it is allowed after other commands; and a path
  that contains parentheses, a variable or non-ASCII characters cannot be passed to it. Write the call as
  `<script> -Action <Name> -Param plain-value`.
- `params.integration_branch`: the branch task branches start from and merge into. The `deploy` pack renders it into
  its workflows (see below), and the `lanes` scripts use it as the base for new lane branches, after the
  `MERCURY_BASE_BRANCH` environment variable and before origin's default branch (Issue #703).
- `params.required_checks`, `params.update_schedule`: used by the `deploy` pack only (see below).
- `params.skip_skills` (optional): a list of pack skill names whose entry files (`.claude/skills/mercury-<skill>/`,
  `.agents/skills/mercury-<skill>/`) are not installed, for a project whose own skill does the same job, for example
  `["lane-handoff"]` where the project keeps its own handoff skill. The skill's source still installs under
  `.mercury/packs/`; an unknown name is an error, and removing it from the list installs the entries again. If a later
  Mercury release renames or removes a skipped skill, every check, apply and update pull request stops on that error
  until the project removes the name.
- These values are validated whenever they are present, so a bad value stops every run, not just the one that renders
  them.

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
- `<project>/.mercury/lock.json` records the sha256 of every installed file, the list of files that are executable and
  the hash of each managed section; a project area adds its own `local` section (see "The project area").
- Roles: every `roles/<role>.md` of a selected pack (lowercase name) becomes `.claude/agents/mercury-<role>.md` and
  `.codex/agents/mercury-<role>.toml`. The source has a frontmatter with a one-line `description` and per-host sections
  (`claude:` with `model` (required), `tools`, `disallowedTools`, `permissionMode`, `readonly_guard`; `codex:` with
  `sandbox_mode` (required for a pack role; a project-area role may leave it out. Current Codex documentation says a
  subagent always inherits the parent's current sandbox and approval settings (including changes made during the
  session) and that a custom agent file cannot override them, so the key changes nothing at run time either way; see
  #665) and the optional `model`, `model_reasoning_effort` and `web_search`) and an optional
  `attribution` (see "The project area"; it works the same for pack roles) whose values are `true`/`false`, a double-quoted string or a
  plain token; anything else, an unknown key, a control character, or a body that cannot be written safely (a TOML
  literal string cannot hold `'''`) is an error and nothing is written. The body is shared and in English. Strings are
  written as JSON strings (valid YAML and TOML); the generated TOML is parsed back and compared when `tomllib` exists.
  `readonly_guard: true` adds a `PreToolUse` hook to the Claude file: shell form under `bash`, command
  `sh "${CLAUDE_PROJECT_DIR}/.mercury/packs/core/hooks/readonly_guard.sh" || exit 2`. Claude Code documents that
  `${CLAUDE_PROJECT_DIR}` is substituted in hook commands and that the default Windows shell is Git Bash;
  the launcher picks the first working `python3`, `python` or `py` (the Windows Store stub is skipped), and a launcher
  that cannot run or exits non-zero blocks the call (`|| exit 2`). The hook has `timeout: 60` and `onFailure: "block"`:
  per the hooks documentation a PreToolUse timeout or a shell that cannot start is otherwise non-blocking, and
  `onFailure` is documented for Claude Code 2.1.295 and later (an older version, checked on 2.1.291, ignores the
  unknown field, and `|| exit 2` still applies). Every generated Claude file (with or without `readonly_guard`) also
  gets the wait guard (#706) under the same matcher: command `sh "${CLAUDE_PROJECT_DIR}/.mercury/packs/core/hooks/wait_guard.sh"`,
  `timeout: 30`, no `|| exit 2` and no `onFailure`. It blocks a wait loop or follow command without a deadline (exit 2)
  and fails open when no Python 3.8+ is found or the check cannot run; what it blocks and accepts is in the task contract
  (Waiting on long-running work). Project-level frontmatter hooks run only after the workspace trust dialog is accepted.
- Rules: `rules/mercury-<name>.rules` of a selected pack is copied byte for byte to `.codex/rules/`.
- Managed section: the pack fragments `agents.md` (AGENTS.md) and `claude.md` (CLAUDE.md), core first and then the
  config order, plus the linked repositories (AGENTS.md only), are written between a `MERCURY:BEGIN` comment line and a
  `MERCURY:END` comment line. A missing file is created with only the section; a file without markers gets the section
  above its content, which stays byte for byte below it (separated by one blank line); a file with markers has only the
  bytes from the begin line to the end of the end line replaced (wherever they sit). Bytes outside are never modified.
  The line ending of the section follows the file (CRLF if its first line, or the begin line, ends with CRLF; LF
  otherwise); the section hash folds CRLF like every other hash. Markers that are unbalanced, repeated, mentioned in
  prose or not alone on their line are an error (exit 2); a file with a byte order mark, invalid UTF-8 or that is
  not a regular file is an error. A section that differs from the lock hash was edited by hand, and a missing section
  with a lock entry means the markers were removed by hand: both stop the run (exit 1, nothing written), as does a
  section that is not in the lock and not identical to the wanted one. The file is replaced atomically through an exclusively created temporary file `<name>.mercury-tmp`; an existing file
  or link of that name is refused (exit 2). A project's own text that contains the string `MERCURY:BEGIN` or
  `MERCURY:END` outside the section stops the installation too (exit 2), so write those words differently.

Rules:

- Only these paths are written. Every path segment is compared the way Windows sees it (trailing dots and
  spaces dropped, case ignored), so a segment that ends in a dot or space is refused outright (`mercury-./x`
  would alias `mercury-/x`). A path must be relative, free of `..`, drive letters, backslashes, control
  characters, `:`, `~` (8.3 short names) and Windows device names (`CON`, `NUL`, `COM1`...), and must lie
  outside `.git/`. `.mercury/config.json`, `.mercury/lock.json` and `.mercury/local.json` are reserved.
- Existing ancestors of every target (and of the lock) must be real directories that resolve inside the
  project: symlinks, junctions and a file where a directory is needed are errors. A link inside a pack source
  is an error too. All checks run before the first byte is written.
- Deletion is limited to these places: files under `.mercury/packs/`, files inside
  `.claude/skills/mercury-*/` and `.agents/skills/mercury-*/`, and the single files `.claude/agents/mercury-*.md`,
  `.codex/agents/mercury-*.toml`, `.codex/rules/mercury-*.rules` and `.github/workflows/mercury-*.yml`. A lock entry anywhere else is an error (exit 2),
  so a tampered lock cannot delete other project files. Emptied directories are removed only below those
  roots; the roots (`.mercury/packs`, `.claude/skills`, `.agents/skills`, `.claude/agents`, `.codex/agents`,
  `.codex/rules`) stay. AGENTS.md and CLAUDE.md are never deleted. The project area has its own, narrower deletion
  scope (the outputs its lock section recorded; see "The project area").
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
