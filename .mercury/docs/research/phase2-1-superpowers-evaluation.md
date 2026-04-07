# Phase 2-1 ADR — `obra/superpowers` Evaluation

**Status**: REJECT (for Phase 2 Quality Gate purpose)
**Date**: 2026-04-07
**Issue**: #196
**Parent**: Phase 2 #181
**Decision authority**: Mercury main agent + user confirmation
**Research method**: 4-round `autoresearch` skill invocation (8 unique source domains, mechanical quality gate passed). All load-bearing findings are reproduced inline in Sections 1–7 with permalinked vendor sources — this ADR is the canonical, self-contained audit artifact.
**Upstream pin**: All `obra/superpowers` source URLs in this ADR are permalinked to commit `917e5f53b16b115b70a3a355ed5f4993b9f8b73d` (2026-04-06, latest on `main` at the time of evaluation). The few external non-vendor URLs (claude.com marketplace listing, registry.npmjs.org name lookup) point to live pages and serve only as **secondary corroboration** — they confirm marketplace presence and the npm name-squatter, but every load-bearing claim is independently witnessed by the permalinked vendor sources in Sections 1–7. If post-merge audit needs to reproduce the live-page state as of 2026-04-07, use the Wayback Machine snapshot pattern `https://web.archive.org/web/20260407*/[URL]`.

---

## Context

Phase 2 of Mercury's execution plan (`.mercury/docs/EXECUTION-PLAN.md §2-3 "Stop Hook 实现"`) requires a Quality
Gate hook that **blocks dev sub-agents from stopping while tests are failing**:

> Dev sub-agent 不能在 test 未通过时 stop

GSD (Issue #191, PR #193) was **REJECTED** — it has no Stop/SubagentStop hook at all.
OMC (Issue #194, PR #195) was **DEFERRED** — it has a Stop hook, but the gate is LLM-level
rather than mechanical exit-code, and integration is plugin-only.

`obra/superpowers` is the third candidate. DIRECTION.md describes it as:
> *"inline checklist + TDD red-green-refactor，已进入 Anthropic marketplace"*

Per Mercury's mount-first principle (`CLAUDE.md`): *"If an external project can solve the
problem, mount it via submodule rather than reimplementing."*

---

## Decision

**REJECT** `obra/superpowers` as a Phase 2 Quality Gate mount.

Superpowers is **strictly weaker** than OMC on the dimension Mercury cares about most: it
ships **zero** `Stop` or `SubagentStop` hooks. Its TDD red-green-refactor enforcement is
purely instructional (skill-prompt discipline), not harness-level. There is nothing for
Mercury to mount that satisfies the EXECUTION-PLAN.md §2-3 "Stop Hook 实现" acceptance criterion.

This is REJECT rather than DEFER because, unlike OMC (which has a Stop hook that could in
principle be patched with an exit-code interposer), Superpowers would require Mercury to
**write the hook from scratch** — exactly the self-research outcome the mount-first
principle forbids.

---

## Rationale

### 1. No Stop/SubagentStop hook exists in superpowers

`obra/superpowers/hooks/hooks.json` defines exactly **one** hook, and it is `SessionStart`:

```json
{
  "SessionStart": [
    {
      "matcher": "startup|clear|compact",
      "hooks": [
        {
          "type": "command",
          "command": "${CLAUDE_PLUGIN_ROOT}/hooks/run-hook.cmd session-start",
          "async": false
        }
      ]
    }
  ]
}
```

There is **no** `Stop`, `SubagentStop`, `PreToolUse`, or `PostToolUse` hook. Mercury's
acceptance criterion ("dev sub-agent 不能在 test 未通过时 stop") requires a Stop event
hook that returns blocking output when test status is failing. Superpowers does not provide
this — at any layer.

Source (permalinked at commit `917e5f5`, verified 2026-04-07): https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/hooks/hooks.json

### 2. TDD enforcement is prompt discipline, not mechanical

Superpowers' TDD model lives in three skills:

- `test-driven-development` SKILL.md — absolute rules ("NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST"), agent-discipline only, no hooks/tools/exit codes.
- `verification-before-completion` SKILL.md — 5-step Gate Function workflow that *mentions* exit codes and `Test command output: 0 failures` as evidence, but enforcement is intrinsic prompt discipline. No tool integration.
- `subagent-driven-development` SKILL.md — two-stage review loop (spec → quality) via fresh subagents, controller-orchestrated, no harness-level blocking.

Quoting the TDD skill analysis verbatim:
> *"This TDD framework operates as a working agreement and skill guide rather than an
> enforced system. Compliance depends on developers adhering to the principles and code
> reviewers catching violations — there's no automated mechanism preventing someone from
> committing untested code."*

This is the **same semantic gap** as OMC's UltraQA, but **strictly weaker** because OMC at
least ships a Stop hook scaffold (`persistent-mode.cjs` Priority 7). Superpowers ships
nothing at the hook layer to interpose on.

Sources:
- https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/skills/test-driven-development/SKILL.md
- https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/skills/verification-before-completion/SKILL.md
- https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/skills/subagent-driven-development/SKILL.md

### 3. Integration model: plugin/marketplace only

Six documented installation methods, **none** of them git submodule:

| Host | Command |
|---|---|
| Claude Code official marketplace | `/plugin install superpowers@claude-plugins-official` |
| Custom marketplace | `/plugin install superpowers@superpowers-marketplace` |
| Cursor | `/add-plugin superpowers` |
| Codex | fetch `.codex/INSTALL.md` |
| OpenCode | fetch `.opencode/INSTALL.md` |
| Gemini CLI | `gemini extensions install https://github.com/obra/superpowers` |

**Not published to npm.** The npm name `superpowers` is squatted by an unrelated package
(publisher `01studio`, version `0.0.2`). The repo's `package.json` declares
`"name": "superpowers"`, `"version": "5.0.7"`, but is not published.

Sources:
- https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/README.md
- https://registry.npmjs.org/superpowers
- https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/package.json

### 4. Architecture has churned recently

The skills layout has split and re-merged within ~3 weeks:

| Phase | Window | Skills location |
|---|---|---|
| v1.x | pre-2025-10-09 | inline `obra/superpowers/skills/` |
| v2.0 | 2025-10-09 to 2025-10-27 | split into separate `obra/superpowers-skills` repo |
| current (v5.x) | 2025-10-27 → present | re-merged inline; `obra/superpowers-skills` **archived** |

A `git submodule add obra/superpowers .external/superpowers` is now technically possible
post-re-merge, but:
- The `session-start` hook detects plugin host via env vars (`CLAUDE_PLUGIN_ROOT`,
  `CURSOR_PLUGIN_ROOT`, `COPILOT_CLI`) and assumes a plugin runtime context.
- Skills are designed for Claude Code's plugin auto-discovery — Mercury would need to copy
  them into `.claude/skills/`, defeating the submodule's update story.
- Recent split→re-merge churn is a stability red flag for any vendor mounting strategy.

Sources (all upstream/first-party):
- https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/RELEASE-NOTES.md (canonical version history with v2.0 split + v5.x re-merge timeline)
- https://github.com/obra/superpowers-skills (the archived split repo itself — banner shows "This repository has been archived by the owner on Oct 27, 2025")

### 5. Windows support: stronger than GSD, comparable to OMC

**Positive findings**:
- `hooks/run-hook.cmd` is a polyglot batch+bash wrapper: on Windows, `cmd.exe` runs the
  `.cmd` portion which locates Git for Windows' `bash.exe`; on Unix, the `:` no-op falls
  through to bash. Hook scripts use extensionless filenames to avoid Claude Code's Windows
  `.sh` auto-prepending quirk.
- No tmux dependency in the SessionStart hook (only `cd`, `pwd`, `cat`, parameter substitution).
- Historical Windows freeze (Issue #31, Dec 2025, Claude Code 2.0.15) was **fixed in v3.6+**
  via the polyglot wrapper. Issue is closed.

**Caveats**:
- Bash on Windows is required (Git for Windows ships it; Mercury already uses MSYS2/Git Bash).
- This is a strength **for the skills library**, not for Mercury's Quality Gate goal —
  there is no Quality Gate to support on any platform.

Sources:
- https://raw.githubusercontent.com/obra/superpowers/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/hooks/run-hook.cmd
- https://github.com/obra/superpowers/blob/917e5f53b16b115b70a3a355ed5f4993b9f8b73d/hooks/session-start
- https://github.com/obra/superpowers/issues/31

### 6. Adapter LOC scenarios

| Scenario | Description | Adapter LOC | Phase 2 outcome |
|---|---|---|---|
| A | `/plugin install superpowers@claude-plugins-official` (docs only) | ~5 | Not a submodule mount |
| B | `git submodule add` + symlink/copy SKILL.md into `.claude/skills/` + env-var shim | ~50–100 | Defeats submodule update story |
| C | Cherry-pick `verification-before-completion` + `test-driven-development` SKILL.md only | ~0 | Doesn't satisfy Stop-hook criterion |
| D | None — REJECT | 0 | Phase 2-1 advances to OpenSpace |

Mercury's <200 LOC cap is technically satisfiable, but **none of A–C deliver a mechanical
exit-code Stop hook**, because Superpowers does not contain one. The LOC math is irrelevant
when the underlying mechanism does not exist.

### 7. Three-way comparison vs. GSD and OMC

| Dimension | GSD | OMC | Superpowers |
|---|---|---|---|
| Stop/SubagentStop hook | None | `persistent-mode.cjs` Priority 7 | **None** |
| Stop gate type | N/A | LLM-level (Ralph loop) | N/A |
| Mercury criterion fit | FAIL | PARTIAL | **FAIL — strictly weaker than OMC** |
| Submodule mountable | Possible/undocumented | Plugin-only | Possible/undocumented + plugin-runtime assumptions |
| Anthropic marketplace | No | No | Yes |
| npm publication | `get-shit-done-cc@1.34.2` | `oh-my-claude-sisyphus` | Not published |
| Windows native | Bash + issues | Patched + tested | Polyglot wrapper, freeze fixed v3.6+ |
| License | MIT | MIT | MIT |
| Activeness | Active, 48k stars | Active | Very active (421 commits since Oct 2025, v5.0.7) |
| Decision | **REJECT** | **DEFER** | **REJECT** |

---

## Consequences

- **Phase 2-1 advances to next candidate**: **OpenSpace** (`HKUDS/OpenSpace`), the final
  candidate named in DIRECTION.md.
- **REJECT is not equivalent to GSD's REJECT**: Superpowers and GSD share the same primary
  failure (no Stop hook), but Superpowers has stronger marketplace presence, more polished
  Windows support, and a higher-quality skills library. The REJECT applies **only** to the
  Phase 2 Quality Gate purpose.
- **OMC remains the fallback**: if OpenSpace also fails, return to OMC and accept the
  LLM-level semantic gap, since OMC is the only candidate that ships an actual Stop hook.
- **Mount-first principle preserved**: REJECT is justified by absence of mechanism, not
  preference for self-research.
- **Post-Phase-2 follow-up**: superpowers' 14-skill library (`verification-before-completion`,
  `subagent-driven-development`, `systematic-debugging`, `brainstorming`,
  `executing-plans`, etc.) is high quality and Anthropic-marketplace-vetted. Recommend
  filing a separate Issue (analogous to #192 for GSD agents) to evaluate cherry-picking
  individual skills into Mercury's `.claude/skills/` after Phase 2 completes. Pin to a
  specific git tag because of the recent split→re-merge churn.

---

## Verification

**Evidence pattern**: This ADR is the canonical, self-contained audit artifact. All load-bearing findings (verbatim `hooks.json` body, verbatim TDD-skill analysis quote, 6-method install table, 7-question dimension table, 3-way comparison table) are reproduced **inline** in Sections 1–7 above with vendor-source permalinks pinned to commit `917e5f5`. A reviewer can independently re-verify every load-bearing claim by clicking the source URLs in Sections 1–7 — no Mercury-local file is required. The autoresearch process generated supplementary scratch files in the operating session; per Mercury convention (also applied to the merged GSD ADR PR #193 and OMC ADR PR #195), those are not committed because the ADR Rationale sections fully reproduce the load-bearing content.

Final autoresearch gate metrics:

| Metric | Value | Threshold | Status |
|---|---|---|---|
| `question_answer_rate` | 7/7 = 1.00 | ≥ 0.9 | PASS |
| `citation_density` | ~0.92 | ≥ 0.75 | PASS |
| `unverified_rate` | 0.0 | ≤ 0.1 | PASS |
| `iteration_depth` | 4 | ≥ 4 | PASS |

Verification: PASS — mechanical checklist completed during the operating session. Per the *Evidence pattern* note above, the load-bearing reproduction lives **in this ADR's Sections 1–7** (with permalinked vendor sources), not in the local-scratch transcript files.

---

## References

- Phase 2 parent Issue: #181
- This evaluation Issue: #196
- GSD evaluation (REJECTED): `.mercury/docs/research/phase2-1-get-shit-done-evaluation.md`
- OMC evaluation (DEFERRED): `.mercury/docs/research/phase2-1-omc-evaluation.md`
- Mercury direction: `.mercury/docs/DIRECTION.md`
- Phase 2 acceptance criterion: `.mercury/docs/EXECUTION-PLAN.md` §2-3 *Stop Hook 实现* (search the section heading for stable anchoring)
- Mount-first principle: `CLAUDE.md` MUST section
- Superpowers repo: https://github.com/obra/superpowers
- Anthropic marketplace listing: https://claude.com/plugins/superpowers
- Stop hook docs: https://code.claude.com/docs/en/hooks
