# Mercury

Mercury is a **Claude Code harness framework** for keeping AI coding agents working continuously, autonomously, and at high quality. It is a repo you clone and point Claude Code at, not an application you install and launch.

Mercury solves the things Claude Code alone does not:

- Session continuity when context fills up (auto-handoff to a fresh session)
- Cross-session, cross-project long-term memory
- Quality gates for unattended long-running work
- Notification + minimal-human-intervention at decision points

See [`.mercury/docs/DIRECTION.md`](.mercury/docs/DIRECTION.md) for the full project charter and [`.mercury/docs/EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md) for the roadmap.

## What Mercury is NOT

The earlier README described a Tauri/Vue desktop application with a Node.js orchestrator sidecar. That architecture was archived in April 2026 as part of the direction pivot (see [DIRECTION.md §五](.mercury/docs/DIRECTION.md)).

- **Not** a desktop application — the GUI, Tauri shell, and `packages/gui/` are in `archive/packages/gui/`
- **Not** an orchestrator — `packages/orchestrator/` is in `archive/packages/orchestrator/`; Claude Code native sub-agents cover the dispatch role
- **Not** a CLI wrapper — Mercury does not wrap `claude` / `codex` / `opencode` binaries
- **Not** a closed system — every skill, hook, agent, and adapter is designed to be lifted out and used in another repo

## Architecture at a glance

```
Mercury (lightweight core — only builds what no external project provides)
├── .claude/
│   ├── agents/        sub-agent role definitions (main, dev, acceptance, critic, design, research, game-*)
│   ├── skills/        reusable workflow skills (pr-flow, autoresearch, dev-pipeline, dual-verify, ...)
│   ├── hooks/         SessionStart, PreToolUse, PostToolUse, PreCompact, Stop hooks
│   └── commands/      slash commands
├── .mercury/
│   ├── docs/          DIRECTION.md + EXECUTION-PLAN.md + guides/ + research/
│   ├── templates/     dispatch prompt templates
│   └── gates/         quality-gate configurations
├── adapters/          Mercury-owned hook/gate adapters (mercury-loop-detector, mercury-test-gate)
├── scripts/           maintenance scripts (worktree-reaper, mem0 hooks, codex guardrails, ...)
└── modules/           reserved for mounted external projects (currently empty — see External mounts)
```

Configuration lives at the repo root:

- `CLAUDE.md` — instructions for Claude Code sessions (MUST/DO NOT policies)
- `AGENTS.md` — instructions for Codex sessions
- `GEMINI.md`, `OPENCODE.md` — per-agent instruction files

## Getting started

### Prerequisites

- [Claude Code CLI](https://claude.com/claude-code) (primary runtime)
- [`gh`](https://cli.github.com/) — GitHub CLI for the PR flow
- `git` (worktree support recommended)
- Optional, per-agent:
  - [Codex CLI](https://developers.openai.com/codex/) — for `AGENTS.md`-driven sessions
  - [Gemini CLI](https://www.npmjs.com/package/@google/gemini-cli) — for `GEMINI.md`-driven sessions

Windows-specific Codex guardrails live in `.codex/config.toml`, `.codex/rules/default.rules`, and `scripts/codex/*.ps1` — see [AGENTS.md](AGENTS.md) for the enforcement path since Codex hooks are currently unavailable on Windows.

### Clone and enter

```bash
git clone https://github.com/392fyc/Mercury.git
cd Mercury
claude   # launch a Claude Code session at the repo root
```

On session start, Claude Code auto-loads every agent under `.claude/agents/`, every skill under `.claude/skills/`, and every hook under `.claude/hooks/`. No build step is required.

### Typical first-session checklist

1. Read `CLAUDE.md` (auto-surfaced by Claude Code) — enforces issue-first workflow, dual-verify before commit, PR-to-`develop` rule
2. Read `.mercury/docs/DIRECTION.md` — project charter and module definitions
3. Skim `.claude/skills/` — available workflows (`pr-flow`, `autoresearch`, `dev-pipeline`, `dual-verify`, `caveman-toggle`, `kb-lint`, ...)
4. Run your first task via the `dev-pipeline` skill: it dispatches a `dev` sub-agent, then an `acceptance` sub-agent, and returns a blind-review verdict

## Skills and sub-agents

The 12 skills under `.claude/skills/` and 9 sub-agents under `.claude/agents/` are **detachable** — each directory is self-contained and can be copied into another Claude Code project. Skill frontmatter lists the trigger phrases in English and Chinese.

Notable skills:

| Skill | Purpose |
|-------|---------|
| `dev-pipeline` | Main → Dev sub-agent → Acceptance sub-agent with blind review |
| `pr-flow` | End-to-end PR lifecycle: create → poll Argus → fix → merge |
| `autoresearch` | Multi-round web research with mechanical quality gate |
| `dual-verify` | Parallel Claude Code deep-review + Codex code-audit (mandatory pre-commit per CLAUDE.md) |
| `handoff` | Session-to-session handoff document + ready-to-paste starting prompt |
| `web-research` | Mandatory web verification protocol for any SDK/API/CLI claim |

Notable sub-agents: `main`, `dev`, `acceptance`, `critic`, `design`, `research`, plus three game-design agents (`game-researcher`, `game-analyst`, `game-critic`) cherry-picked from `msitarzewski/agency-agents`.

## Hooks

`.claude/hooks/` contains active hooks invoked by Claude Code at lifecycle events:

- `session-init.sh` — SessionStart context injection (date, KB index, memory snapshots)
- `pre-commit-guard.sh`, `pr-create-guard.sh`, `pr-merge-guard.sh`, `push-guard.sh` — enforce branch policies and dual-verify gate
- `scope-guard.sh`, `post-commit-reset.sh`, `post-review-flag.sh`, `post-web-research-flag.sh` — scope enforcement and state-flag lifecycle

`adapters/mercury-loop-detector/` and `adapters/mercury-test-gate/` implement mechanical Stop-hook enforcement via exit codes. Per DIRECTION.md §八-1, this is the only exit-code-based mechanical Stop-hook implementation known to us in the Claude Code ecosystem — an ecosystem gap identified during Phase 2-1 evaluation.

## External project mounts

Mercury's mount philosophy (DIRECTION.md §四): build the minimum in-house; mount external projects via git submodule under `modules/` with a thin `adapters/<name>/` translation layer (≤200 LOC). Phase 2-1 evaluated four candidates (GSD, Superpowers, OMC, OpenSpace) against a narrow Stop-hook acceptance criterion; all four were REJECT or DEFER on that criterion, so `modules/` is currently empty. Other value from those projects has been cherry-picked individually (see `.mercury/state/upstream-manifest.json` and `scripts/upstream-drift-check.sh`).

When files from an external project are cherry-picked into Mercury, the cherry-pick protocol in [`CLAUDE.md`](CLAUDE.md) applies (manifest entry in `.mercury/state/upstream-manifest.json`, SKILL.md frontmatter, attribution comments, license gate, SHA verification).

## Example files

Configuration templates ship as `.example` files — copy and customize before use; the targets are gitignored.

| Template | Target | Purpose |
|---|---|---|
| `.pr_agent.toml.example` | `.pr_agent.toml` | PR review bot instructions (Argus / Qodo Merge) |
| `CLAUDE.local.md.example` | `CLAUDE.local.md` | Claude Code local instructions (caveman concise mode) |

### Caveman mode

Persistent concise-output style based on [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (MIT). Activate via the `caveman-toggle` skill:

```
/caveman-on          # enable lite mode (default)
/caveman-on full     # enable full mode
/caveman-off         # disable
```

Or manually: `cp CLAUDE.local.md.example CLAUDE.local.md`.

### PR review bot

```bash
cp .pr_agent.toml.example .pr_agent.toml
# edit .pr_agent.toml with your review bot instructions
```

## Documentation index

| Topic | Path |
|---|---|
| Project charter and module definitions | [`.mercury/docs/DIRECTION.md`](.mercury/docs/DIRECTION.md) |
| Execution roadmap (Phase 0 → Phase 6) | [`.mercury/docs/EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md) |
| Claude Code session instructions | [`CLAUDE.md`](CLAUDE.md) |
| Codex session instructions | [`AGENTS.md`](AGENTS.md) |
| Git-flow conventions | [`.mercury/docs/guides/git-flow.md`](.mercury/docs/guides/git-flow.md) |
| Issue-first workflow | [`.mercury/docs/guides/issue-workflow.md`](.mercury/docs/guides/issue-workflow.md) |
| Architecture evaluation (PR #162) | [`.mercury/docs/research/issue-158-architecture-evaluation.md`](.mercury/docs/research/issue-158-architecture-evaluation.md) |

## Legacy / archived components

The following directories preserve the pre-pivot orchestrator/GUI architecture and are not part of the active runtime. They are kept in-tree for historical reference and potential cherry-pick; do not edit them in active PRs.

- `archive/packages/{gui,orchestrator,sdk-adapters,poc}/` — old Tauri/Vue/Node.js stack
- `archive/roles/*.yaml` — old role definitions (migrated to `.claude/agents/*.md`)
- `archive/agents/`, `archive/skills/`, `archive/docs/` — pre-pivot content

`packages/core/` still exists at the repo root for any shared types that may still be consumed. `mercury.config.json` / `mercury.config.example.json` remain as legacy config — only `obsidian.vaultName` / `obsidian.vaultPath` are still read (by `session-init.sh`), and removal is pending mem0 migration cleanup.

## License

MIT — see [LICENSE](LICENSE).
