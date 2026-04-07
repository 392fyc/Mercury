# Phase 2-1 ADR — `gsd-build/get-shit-done` Evaluation

**Status**: REJECT (for Phase 2 Quality Gate purpose)
**Date**: 2026-04-07
**Issue**: #191
**Parent**: Phase 2 #181
**Decision authority**: Mercury main agent + user confirmation
**Research artifact**: `.research/reports/RESEARCH-get-shit-done-evaluation-2026-04-07.md` (3 rounds, 27 sources)

---

## Context

Phase 2 of Mercury's execution plan (`.mercury/docs/EXECUTION-PLAN.md:167-198`) requires mounting an
external project to provide a Quality Gate — specifically a hook that **blocks dev sub-agents from
stopping while tests are failing**. Acceptance criterion (`EXECUTION-PLAN.md:197`):

> Dev sub-agent 不能在 test 未通过时 stop

Four candidates were initially named in `DIRECTION.md`: GSD, Superpowers, OMC, OpenSpace.
This ADR covers the GSD evaluation. Per Mercury's mount-first principle (`CLAUDE.md`):
*"If an external project can solve the problem, mount it via submodule rather than reimplementing"*.

## Correction

`DIRECTION.md` originally named `gsd-build/gsd-2` as the candidate. Round 1 of the research
established that `gsd-2` is a **standalone CLI** (`npm gsd-pi@2.65.0`, built on the Anthropic
Pi SDK, Node ≥22, 15+ heavyweight runtime deps) that **replaces Claude Code as the agent harness**
rather than running under it. This is structurally incompatible with Mercury's "Claude Code
sub-agent" model.

The actually Claude-Code-hostable sibling under the same `gsd-build` org is `get-shit-done`
(48,667 stars, `npm get-shit-done-cc@1.34.2`, zero runtime deps, ships hooks/skills/agents into
`.claude/`). DIRECTION.md:153 and :365 are corrected in this same PR. Issue #191 title and body
were updated 2026-04-07.

## Decision

**REJECT** `gsd-build/get-shit-done` as a Phase 2 Quality Gate mount.
**REJECT** `gsd-build/gsd-2` as a Phase 2 Quality Gate mount.

## Rationale

### 1. No blocking Stop hook exists in get-shit-done

Mercury's acceptance criterion requires a Claude Code `Stop` or `SubagentStop` hook that returns
blocking output when test status is failing. Round 2-3 confirmed:

- All 9 hook scripts in `hooks/` (`gsd-workflow-guard.js`, `gsd-context-monitor.js`,
  `gsd-prompt-guard.js`, `gsd-read-guard.js`, `gsd-statusline.js`, `gsd-validate-commit.sh`,
  `gsd-session-state.sh`, `gsd-phase-boundary.sh`, `gsd-check-update.js`) are **PreToolUse**,
  **PostToolUse**, **SessionStart**, or **statusLine** events. **None is a Stop or SubagentStop hook.**
- `gsd-workflow-guard.js` header comment states literally: *"This is a SOFT guard — it advises,
  not blocks. The edit still proceeds."* All output is `additionalContext` strings, never
  blocking exit codes.
  Source: https://github.com/gsd-build/get-shit-done/blob/main/hooks/gsd-workflow-guard.js
- `gh search code --repo gsd-build/get-shit-done "SubagentStop"` returns zero results.
- "STOP"/"hard stop" occurrences in the repo (e.g. `get-shit-done/workflows/next.md`,
  `execute-plan.md`) are **English prose inside markdown workflow scripts** that the LLM is
  instructed to honor — not Claude Code hook event registrations.

### 2. Historical Stop hook existed but never for the Phase 2 use case

`gsd-intel-prune.js` was registered as a Claude Code Stop event hook in older versions
(confirmed by Issues #116 and #203 error traces showing `Ran 1 stop hook` for this file path).
Its function was to **prune `.planning/intel/` data on session stop** — janitorial cleanup,
not test-failure blocking. It was removed in v1.9.2 (per code comment in `bin/install.js`:
`'gsd-intel-prune.js',  // Removed in v1.9.2`).

Conclusion: even when get-shit-done used Claude Code Stop hooks, the use case was unrelated to
Mercury's Phase 2 acceptance criterion.

Sources:
- https://github.com/gsd-build/get-shit-done/issues/203
- https://github.com/gsd-build/get-shit-done/issues/116
- `gh search code --repo gsd-build/get-shit-done "intel-prune"`

### 3. Project trajectory is moving AWAY from harness hooks

CHANGELOG v1.33.0+ shows the project replacing hook-based intel management with **agent-based**
intel (`gsd-intel-updater` agent, queryable `.planning/intel/` store, #1688). The intel hooks
were not replaced with new Stop hooks — they were replaced with agents.

CHANGELOG v1.34.0 added a "Gates taxonomy" — 4 canonical gate types (pre-flight, revision,
escalation, abort) — but they are wired into `plan-checker` and `verifier` **agents**, not into
Claude Code hook events. The gating happens at the agent prompt layer.

Source: https://github.com/gsd-build/get-shit-done/blob/main/CHANGELOG.md (v1.33.0, v1.34.0)

This is the opposite of what Mercury Phase 2 needs. Mercury needs harness-level enforcement;
get-shit-done has decided that harness-level hooks are fragile (especially on Windows) and is
explicitly migrating to agent-prompt-level discipline.

### 4. Architectural mismatch — installer-pattern, not library

`bin/install.js` is 226,749 bytes and is the intended integration surface. Distribution is
`npx get-shit-done-cc@latest`, an interactive installer that writes into `~/.claude/` or
project-local `.claude/`. The installer registers hooks in `settings.json`, drops skills under
`skills/gsd-*/`, slash commands under `commands/gsd/`, workflow assets under `get-shit-done/`,
and 20 sub-agent role definitions under `agents/gsd-*.md`.

Mercury already has its own curated `.claude/agents/`, `.claude/skills/`, and `.claude/settings.json`.
Treating get-shit-done as a git submodule with manual extraction is semantically a fork-and-extract
pattern, not a clean mount. This violates Mercury's design intent for `modules/` mounting.

Source: https://raw.githubusercontent.com/gsd-build/get-shit-done/main/README.md

### 5. Windows is officially second-class upstream

CHANGELOG v1.33.0 (#1676): *"CI matrix — Drop Windows runner, add static hardcoded-path detection"*.
Windows is no longer in the upstream CI matrix. Historic Windows-specific issues:
- #466 (Feb 2026): SessionStart hook freezes Claude Code input on Windows (CLOSED, fix `detached: true`)
- #1343 (Mar 2026): Bash hook errors, stdin hangs, project_root detection failures (CLOSED)
- #114 (Jan 2026): "OUCH! Windows hooks don't work after GSD update" (CLOSED)
- #116 (Jan 2026): WSL `execvpe` failure on Stop hook (CLOSED)
- #129 (Feb 2026): `gsd-notify.sh` errors after update

Mercury runs on Windows 11. A direct dependency on a project that has dropped Windows from CI
is a real carrying cost.

Sources: `gh issue list --repo gsd-build/get-shit-done`, `gh issue view 466 / 1343 / 116 / 203`

### 6. Adapter line-count is not the bottleneck

Three plausible mounting scenarios were estimated; all fit under the 200-line adapter cap.
None of the three delivers a blocking Stop hook (because the source repo does not contain one):

| Scenario | Adapter LOC | Phase 2 outcome |
|---|---|---|
| A — wrap full installer | 150-300 | No stop hook delivered |
| B — cherry-pick hook scripts (e.g. context-monitor) | ~30 | Context warning only, no stop hook |
| C — mount agent role library only | 50-100 | Agent role library, unrelated to Phase 2 |

Feature mismatch is the rejection driver, not adapter complexity.

## Consequences

- **Phase 2-1 advances to next candidate**. Per `DIRECTION.md:155`, **OMC (Yeachan-Heo/oh-my-claudecode)**
  is described as *"Ralph mode stop hook 拦截"* — which targets the acceptance criterion directly.
  OMC is the most promising next candidate.
- **DIRECTION.md updated** in this same PR to reflect both the gsd-2 → get-shit-done correction and
  the REJECT outcome.
- **Follow-up Issue filed** (linked from this PR) for the orthogonal observation: get-shit-done's
  20 pre-built sub-agent role definitions (`agents/gsd-*.md`: executor, planner, code-reviewer,
  debugger, security-auditor, doc-writer, nyquist-auditor, roadmapper, ui-auditor, etc.) are
  potentially valuable as a sub-agent role library mount **after Phase 2 completes** — separate
  use case from Quality Gate.
- **Mercury's mount-first principle is preserved**: this REJECT is based on the source repo not
  containing the needed feature, not on a preference for self-research.

## Verification

Research artifact `.research/reports/RESEARCH-get-shit-done-evaluation-2026-04-07.md` covers all
7 evaluation dimensions across 3 autoresearch rounds with 27 source URLs cross-verified across
GitHub repo metadata, npm registry, source code reads, and issue tracker.

Final autoresearch gate metrics:
- `question_answer_rate` 1.00 (PASS, threshold ≥0.9)
- `citation_density` ~0.90 (PASS, threshold ≥0.75)
- `unverified_rate` ~0.02 (PASS, threshold ≤0.1)
- `iteration_depth` 3 (FAIL, threshold ≥4) — terminated with user approval after Round 3 since
  content was exhaustively complete and 2 of 3 prior UNVERIFIED items were resolved in Round 3.

## References

- Phase 2 parent Issue: #181
- This evaluation Issue: #191
- Mercury direction: `.mercury/docs/DIRECTION.md` (corrected in this PR)
- Phase 2 acceptance criterion: `.mercury/docs/EXECUTION-PLAN.md:197`
- Mount-first principle: `CLAUDE.md` MUST section
- Full research transcript: `.research/reports/RESEARCH-get-shit-done-evaluation-2026-04-07.md`
- get-shit-done repo: https://github.com/gsd-build/get-shit-done
- gsd-2 sibling repo: https://github.com/gsd-build/gsd-2
- npm registry (get-shit-done-cc): https://registry.npmjs.org/get-shit-done-cc/latest
- npm registry (gsd-pi): https://registry.npmjs.org/gsd-pi/latest
