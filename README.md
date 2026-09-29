# Mercury

Mercury is a **Claude Code harness framework** for keeping AI coding agents working continuously, autonomously, and at high quality. It is a repo you clone and point Claude Code at, not an application you install and launch.

Mercury solves the things Claude Code alone does not:

- Session continuity when context fills up (handoff to a fresh session)
- Cross-session, cross-project long-term memory
- Quality gates for unattended long-running work

See [`.mercury/docs/DIRECTION.md`](.mercury/docs/DIRECTION.md) for the full project charter and [`.mercury/docs/EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md) for the roadmap.

## What Mercury is NOT

The earlier README described a Tauri/Vue desktop application with a Node.js orchestrator sidecar. That architecture was archived in April 2026 as part of the direction pivot (see [DIRECTION.md §五](.mercury/docs/DIRECTION.md)).

- **Not** a CLI wrapper — Mercury does not wrap `claude` / `codex` / `opencode` binaries; it configures them
- **Not** a custom orchestrator — Claude Code native sub-agents cover the dispatch role; the old `packages/orchestrator/` lives in `archive/`
- **Not** a closed system — every skill, hook, agent, and adapter is designed to be lifted out and used in another repo
- **Not** "weak-model" software — features are designed for upward compatibility with stronger models, never around current limitations

The lone exception to "not an application" is an early **desktop GUI** MVP (`mercury-gui/`) — a Tauri 2 shell that observes Mercury's own runtime state. It is Mercury-internal tooling explored ahead of the on-demand Phase 6 trigger, not the resurrected pre-pivot product.

## Current status (snapshot — 2026-06)

Mercury is built phase by phase against [`EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md). This snapshot reflects accumulated progress; the plan is the authoritative source.

| Phase | Scope | Status |
|---|---|---|
| **Phase 0** | Cleanup + scaffolding — archive pre-pivot stack, migrate roles to `.claude/agents/*.md` | ✅ Complete |
| **Phase 1** | Dev pipeline — Main → Dev → Acceptance blind-review chain + `pr-flow` | ✅ Complete |
| **Phase 2** | Quality gates — mechanical Stop-hook enforcement (`adapters/mercury-test-gate/`, `mercury-loop-detector/`) | ✅ Complete |
| **Phase 3** | Memory layer — mem0 + Qdrant cross-session/cross-project memory (user-level) | ✅ Complete |
| **Phase 4** | Session continuity — `claude-handoff` session-chain, worktree-per-task, compact-prevention, stall detection | ✅ Complete |
| ~~**Phase 5**~~ | ~~Notify hub — Telegram channel~~ — **abandoned & removed** ([#512](https://github.com/392fyc/Mercury/issues/512)): the Telegram/Channels approach is gated by Anthropic's server-side `tengu_harbor` rollout flag (unavailable on personal accounts), so the subsystem was stripped | ❌ Removed |
| **Phase 6** | Desktop GUI — evaluated on-demand after Phase 1-4 are stable | ⚪ On-demand |

Per [`EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md), Phase 6 is explicitly **on-demand and not in the committed roadmap**. An early `mercury-gui/` MVP (Tauri 2 + React) nonetheless exists in-tree as exploratory work ahead of any formal trigger.

Recent additions on top of the core phases:

- **Multi-lane development** — multiple isolated work lanes (own worktree + branch + handoff) running in parallel, driven by Claude Code or Codex, with no lane-count cap (see [Multi-lane development](#multi-lane-development))
- **Codex native project layer** — `AGENTS.md`, `.codex/rules/`, and guarded PowerShell wrappers carry branch, review, and task policy (see [Multi-agent runtimes](#multi-agent-runtimes))

## Architecture at a glance

```
Mercury (lightweight core — only builds what no external project provides)
├── .claude/
│   ├── agents/        sub-agent role definitions (dev, acceptance, critic, design, research, game-*)
│   ├── skills/        opt-in workflow skills (pr-flow, autoresearch, dev-pipeline, dual-verify, ...)
│   └── settings.json  permission rules only; no project hooks are registered
├── .codex/            Codex CLI config + agents + rules (project-level hook registrations retired)
├── .mercury/
│   ├── docs/          DIRECTION.md + EXECUTION-PLAN.md + guides/ + research/
│   ├── templates/     dispatch prompt templates
│   └── gates/         quality-gate configurations
├── adapters/          Mercury-owned hook/gate/integration adapters (≤200 LOC each for external mounts)
├── scripts/           maintenance scripts (lane-*, worktree-reaper, mem0 hooks, codex guardrails, ...)
├── mercury-gui/       early desktop GUI MVP — Tauri 2 + React (Phase 6 is on-demand)
└── modules/           reserved for mounted external projects (currently empty — see External project mounts)
```

`adapters/` currently holds three adapters: `mercury-loop-detector` and `mercury-test-gate` (mechanical Stop-hook gates) and `playwright-mcp` (browser automation mount).

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
  - [Codex CLI](https://developers.openai.com/codex/) — for `AGENTS.md`-driven sessions (see below)
  - [Gemini CLI](https://www.npmjs.com/package/@google/gemini-cli) — for `GEMINI.md`-driven sessions

### Clone and enter

```bash
git clone https://github.com/392fyc/Mercury.git
cd Mercury
claude   # launch a Claude Code session at the repo root
```

On session start, Claude Code auto-discovers every agent under `.claude/agents/` and every skill under `.claude/skills/`. Mercury registers no project-level Claude Code hooks; `.claude/settings.json` only carries permission rules. Codex reads `AGENTS.md`, `.codex/config.toml`, `.codex/agents/`, and `.codex/rules/`; Mercury does not register project-level Codex hooks. No build step is required.

### Typical first-session checklist

1. Read `CLAUDE.md` (auto-surfaced by Claude Code; it imports `AGENTS.md`) — issue-first workflow, guarded Git writes via `scripts/codex/git-safe.ps1`, PR-to-`develop` rule
2. Read `.mercury/docs/DIRECTION.md` — project charter and module definitions
3. Skim `.claude/skills/` — opt-in workflows (`pr-flow`, `autoresearch`, `dev-pipeline`, `dual-verify`, ...); each runs only when you ask for it or an agreed plan requires it
4. Run your first task via the `dev-pipeline` skill: it dispatches a `dev` sub-agent, then an `acceptance` sub-agent, and returns a blind-review verdict

## Skills and sub-agents

The skills under `.claude/skills/` and sub-agents under `.claude/agents/` are **detachable** — each directory is self-contained and can be copied into another Claude Code project. Skill frontmatter lists the trigger phrases in English and Chinese. Treat the directory contents as the authoritative list; the snapshot below is current as of this writing and intentionally not a pinned count.

Skills (4 at time of writing), all opt-in:

| Skill | Purpose |
|-------|---------|
| `dev-pipeline` | One bounded implementation followed by independent acceptance |
| `pr-flow` | A single requested PR stage: check a PR, handle Argus comments, or run one step |
| `dual-verify` | Optional review in two independent contexts, not a default gate |
| `autoresearch` | Bounded multi-round research with coverage, sources and open questions |

`web-research` and `handoff` are provided as user-level skills rather than from this repository.

Sub-agents (8): `dev`, `acceptance`, `critic`, `design`, `research`, plus three game-design agents (`game-researcher`, `game-analyst`, `game-critic`) cherry-picked from `msitarzewski/agency-agents`. The main session acts as the Mercury main agent; there is no separate `main` sub-agent.

## Hooks

Mercury registers no project-level hooks for Claude Code or Codex. Git safety is enforced by the permission rules in `.claude/settings.json`, `.codex/rules/`, and the guarded wrappers `scripts/codex/git-safe.ps1` and `scripts/codex/guard.ps1`. Memory location is supplied by a user-level `SessionStart` hook that only returns paths; see [Ecosystem](#ecosystem).

`adapters/mercury-loop-detector/` and `adapters/mercury-test-gate/` remain available as standalone adapters that implement mechanical Stop-hook enforcement via exit codes; they are not registered by default. Per DIRECTION.md §八-1, this is the only exit-code-based mechanical Stop-hook implementation known to us in the Claude Code ecosystem — an ecosystem gap identified during Phase 2-1 evaluation.

## Multi-lane development

Mercury runs multiple **lanes** in parallel — independent work streams that don't step on each other. Each lane owns a git worktree, a branch namespace, and a handoff document, so concurrent sessions (e.g. an architecture lane and a bug-fix lane) stay isolated.

- **Branch prefix**: `lane/<short>/<N>-<slug>` (≤40 chars; a legacy `feature/lane-<lane>/...` form is still accepted)
- **No lane-count cap**: open as many lanes as you need; the former Δ7 hard cap was removed in [#605](https://github.com/392fyc/Mercury/issues/605). A lane is driven by Claude Code or Codex (`--harness`) and works solo by default or pairs with another lane (see the [cross-harness lane ADR](.mercury/docs/research/issue-599-cross-harness-lane-isolation-2026-09.md))
- **First-time setup**: run `scripts/lane-init.sh` once per machine to create the lane registry (`LANES.md`)
- **Tooling**: `scripts/lane-*.sh` (init / spawn / claim / close / sweep) + `lane-assertion.sh` enforce the protocol mechanically; `lane-cap-check.sh` reports the active-lane count
- **Lane guides**: [`lane-spawn.md`](.mercury/docs/guides/lane-spawn.md), [`lane-claim.md`](.mercury/docs/guides/lane-claim.md), [`lane-close.md`](.mercury/docs/guides/lane-close.md), [`lane-sweep.md`](.mercury/docs/guides/lane-sweep.md), [`lane-emergency-escalation.md`](.mercury/docs/guides/lane-emergency-escalation.md)

## Multi-agent runtimes

Mercury is primarily a Claude Code harness, but the same policies are mirrored for other agent CLIs so a task can be handed across runtimes without losing its guardrails.

- **Claude Code** — primary runtime; reads `CLAUDE.md` (which imports `AGENTS.md`), auto-discovers `.claude/{agents,skills}`, and applies the permission rules in `.claude/settings.json`
- **Codex CLI** — reads `AGENTS.md` and the native `.codex/` project layer. Mercury has retired project-level Codex hook registrations; `.codex/rules/` is the verified command-enforcement layer, `scripts/codex/*.ps1` provides guarded operations, and instructions cover hosted tools that command-prefix rules cannot reach
- **Gemini / OpenCode** — `GEMINI.md` / `OPENCODE.md` carry the equivalent instruction set

## Ecosystem

Some Mercury capabilities run as separate, independently-deployable layers rather than in-repo code:

| Layer | Where | Role |
|---|---|---|
| **claude-handoff** | Local plugin ([392fyc/claude-handoff](https://github.com/392fyc/claude-handoff)) | Session handoff / continuation + `session_chain` SQLite — backs Phase 4 |
| **Memory layer** | User-level `~/.claude/hooks/` + `~/.claude/scripts/` | mem0 + Qdrant adapter, session-start/end/pre-compact hooks, cost tracker — backs Phase 3 |
| **Argus** | Self-hosted PR review bot | Automated PR review on GitHub; pairs with `dual-verify` and the `pr-flow` skill |
| **oh-my-claudecode (OMC)** | Optional Claude Code plugin — not enabled in the committed `.claude/settings.json` | Multi-agent orchestration companion: UltraQA cycling, agent teams, deep-research, skill lifecycle. Adopted as a plugin (DEC-4 "Path β"); its LLM-level `SubagentStop` gate complements Mercury's mechanical `mercury-test-gate` adapter. **Opt-in & reversible** — after cloning, run `/plugin marketplace add https://github.com/Yeachan-Heo/oh-my-claudecode`, then `/plugin install oh-my-claudecode@omc`, then `/reload-plugins` (marketplace-add only registers the catalog — the explicit install step is what fetches the plugin); it is not enabled in the committed settings, so enable it only in your gitignored `.claude/settings.local.json`; Mercury runs without it and the shared config is never touched. |

User-level changes (anything under `~/.claude/`) are governed separately from project PRs — see "Git and local safety" in [`AGENTS.md`](AGENTS.md) for the backup, rollback and issue-tracking discipline.

> **OMC is a plugin, not a `modules/` mount.** Phase 2-1 evaluated OMC (alongside GSD / Superpowers / OpenSpace) against a mechanical Stop-hook criterion and deferred it (PR #195) for two reasons: OMC's gate is LLM-level rather than a mechanical exit-code check, and OMC ships **plugin-only with no git-submodule path** — which did not fit the then-strict "mount as a submodule under `modules/`" reading of the mount-first principle. That is why `modules/` stays empty. OMC was later adopted on its *supported* axis — a Claude Code **plugin** (DEC-4 "Path β"); the committed `.claude/settings.json` no longer enables it. So Mercury *does* use OMC as a plugin companion; it simply is not (and cannot be) vendored as a submodule. The plugin stays **opt-in** — enable it in your gitignored `.claude/settings.local.json` if you want it; Mercury runs unchanged without it: a convenience companion, not a hard dependency. See [External project mounts](#external-project-mounts) for the submodule philosophy.

## External project mounts

Mercury's mount philosophy (DIRECTION.md §四): build the minimum in-house; mount external projects via git submodule under `modules/` with a thin `adapters/<name>/` translation layer (≤200 LOC). Phase 2-1 evaluated four candidates (GSD, Superpowers, OMC, OpenSpace) against a narrow Stop-hook acceptance criterion; all four were REJECT or DEFER on that criterion, so `modules/` is currently empty. Other value from those projects has been cherry-picked individually (see `.mercury/state/upstream-manifest.json` and `scripts/upstream-drift-check.sh`).

When files from an external project are cherry-picked into Mercury, the upstream import protocol in [`AGENTS.md`](AGENTS.md) is the canonical source for the required attribution / manifest / drift discipline. Two adjacent cases (one-shot CLI scaffolding and registry-based per-item imports) have a narrower carve-out — AGENTS.md keeps a summary; the authoritative full rules live in [`.mercury/docs/guides/cherry-pick-carve-out.md`](.mercury/docs/guides/cherry-pick-carve-out.md). This README does not restate the rules; consult those docs for current details.

## Example files

Two `.example` files ship at the repo root. They serve two different tracking models:

| Template | Target | Tracking model |
|---|---|---|
| `CLAUDE.local.md.example` | `CLAUDE.local.md` | Target is **gitignored** — personal instructions per developer |
| `.pr_agent.toml.example` | `.pr_agent.toml` | Target is **committed** (project-scope config for Argus); the `.example` is reference material when setting up the file elsewhere |

### Caveman mode (local, gitignored)

Persistent concise-output style based on [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) (MIT). Activate it by copying the example: `cp CLAUDE.local.md.example CLAUDE.local.md`.

### PR review bot (committed)

`.pr_agent.toml` is already committed; edit it in place rather than copying from the `.example`. The `.example` exists for bootstrapping the file in another repo or regenerating from scratch.

## Documentation index

| Topic | Path |
|---|---|
| Project charter and module definitions | [`.mercury/docs/DIRECTION.md`](.mercury/docs/DIRECTION.md) |
| Execution roadmap (Phase 0 → Phase 6) | [`.mercury/docs/EXECUTION-PLAN.md`](.mercury/docs/EXECUTION-PLAN.md) |
| Claude Code session instructions | [`CLAUDE.md`](CLAUDE.md) |
| Codex session instructions | [`AGENTS.md`](AGENTS.md) |
| Git-flow conventions | [`.mercury/docs/guides/git-flow.md`](.mercury/docs/guides/git-flow.md) |
| Issue-first workflow | [`.mercury/docs/guides/issue-workflow.md`](.mercury/docs/guides/issue-workflow.md) |
| Lane naming + concurrency cap | [`.mercury/docs/guides/lane-naming.md`](.mercury/docs/guides/lane-naming.md) |
| Architecture evaluation (PR #162) | [`.mercury/docs/research/issue-158-architecture-evaluation.md`](.mercury/docs/research/issue-158-architecture-evaluation.md) |

## Legacy / archived components

The following directories preserve the pre-pivot orchestrator/GUI architecture and are not part of the active runtime. They are kept in-tree for historical reference and potential cherry-pick; do not edit them in active PRs.

- `archive/packages/{gui,orchestrator,sdk-adapters,poc}/` — old Tauri/Vue/Node.js stack
- `archive/roles/*.yaml` — old role definitions (migrated to `.claude/agents/*.md`)
- `archive/agents/`, `archive/skills/`, `archive/docs/` — pre-pivot content

`packages/core/` still exists at the repo root for any shared types that may still be consumed. `mercury.config.json` / `mercury.config.example.json` remain as legacy config — only `obsidian.vaultName` / `obsidian.vaultPath` remain for reference; the `session-init.sh` hook that read them was retired in #579.

(Note: the early `mercury-gui/` GUI MVP at the repo root is distinct from the archived pre-pivot `archive/packages/gui/`.)

## License

MIT — see [LICENSE](LICENSE).
