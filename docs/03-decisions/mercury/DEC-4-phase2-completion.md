# DEC-4: Phase 2 Stop Hook — Completion Record + Non-Selected Candidate Analysis

**Status**: Accepted
**Date**: 2026-04-09
**Parent Issue**: Phase 2 #181
**Implementation Issue**: #206
**Implementation PR**: #207 (merged `1d335fb`)
**Predecessor decisions**: DEC-2 TaskBundle Lightweight, DEC-3 Model Tiering

---

## Context

Phase 2 of Mercury's execution plan (`.mercury/docs/EXECUTION-PLAN.md` §2-3 *"Stop Hook 实现"*) defined a single mechanical acceptance criterion:

> **验收标准: Dev sub-agent 不能在 test 未通过时 stop**
> (Acceptance: dev sub-agent cannot stop while tests are failing)

The original reading — reinforced through Phase 2-1's 4 candidate ADRs — was **harness-level mechanical enforcement via Claude Code `SubagentStop` hook with exit-code gating**, not LLM-level prompt discipline.

Phase 2-1 ran an exhaustive evaluation of every candidate named in `DIRECTION.md`:

| Candidate | Verdict | Decisive reason |
|---|---|---|
| `gsd-build/get-shit-done` | REJECT (PR #193) | No Stop hook infrastructure |
| `Yeachan-Heo/oh-my-claudecode` (OMC) | DEFER (PR #195) | Stop hook exists but LLM-level (Ralph loop iteration counter), not mechanical |
| `obra/superpowers` | REJECT (PR #197) | No Stop hook scaffold, strictly weaker than OMC on this dimension |
| `HKUDS/OpenSpace` | REJECT (PR #204) | Zero Claude Code hook infrastructure + 15-day-old pre-1.0 churn |

The sequence exhausted all 4 candidates. PR #205 (design doc, merged `a3696f8`) framed a user-escalation decision between **Path α** (relax criterion to accept LLM-level enforcement) and **Path β** (mount OMC as companion orchestration + write a thin Mercury-owned mechanical adapter on the Stop hook layer).

The user chose **Path β** with 6 explicit answers to the design doc's open questions (Q1 test command resolution, Q2 fail-mode default, Q3 agent scope, Q5 hook language, Q14 re-entry policy, Q15 spec-invalid output replacement).

---

## Decision

**Phase 2 §2-3 is COMPLETE.** Mercury now mechanically blocks `dev` sub-agents from completing `SubagentStop` while the project's test command returns a non-zero exit code.

### Definition of Complete

"Complete" in this record means the following code-level criteria are all satisfied:

- **Code shipped to develop**: PR #207 merged at `1d335fb`; `adapters/mercury-test-gate/` is on the `develop` branch.
- **Unit tests passing**: 20/20 tests pass via `node --test` (10 hook integration cases + 10 resolve-command cases). The acceptance criterion is code-level test coverage, not runtime observation.
- **Adapter live and registered**: `.claude/settings.json` registers `mercury-test-gate/hook.cjs` on the `SubagentStop` event with matcher `dev`; the hook fires on every dev sub-agent stop attempt.

The following are **observation-period work, not blocking**:

- End-to-end integration test with a real failing pipeline (requires natural occurrence or purpose-built harness — deferred per user decision, session 26).
- Layer coexistence observation: confirming Mercury's mechanical gate and OMC's Ralph-loop LLM gate do not produce conflicting decisions in practice.

These deferred items are tracked in §"What Phase 2 does NOT yet provide" and §"Phase 3 Prerequisites" below. Their resolution does not retroactively change the COMPLETE status of Phase 2's code-level acceptance criterion.

### What was shipped (PR #207, merged `1d335fb`)

- **`adapters/mercury-test-gate/`** — 200 LOC Node.js `.cjs` adapter (at the ≤200 LOC cap defined in CLAUDE.md — 200 is the boundary and is compliant; this is the sanctioned bridge-adapter size for mount-first gaps)
  - `hook.cjs` (65 LOC) — `SubagentStop` entry: parses stdin JSON, dispatches through scope check → re-entry guard → test-command resolution → timed subprocess → exit-code interpretation → spec-compliant output
  - `lib/resolve-command.cjs` (43 LOC) — convention file `.mercury/config/test-gate.yaml` > auto-detect (`package.json`#scripts.test → `pyproject.toml [tool.pytest]` → `Makefile test:` target → `Cargo.toml` presence)
  - `lib/run-command.cjs` (31 LOC) — timeout-wrapped subprocess with cross-platform process-tree kill (POSIX detached + `kill(-pid)`, Windows `taskkill /F /T /PID`)
  - `lib/attempt-tracker.cjs` (61 LOC) — bounded retry state with advisory lockfile serialization (spin-wait, 2s max, 15ms poll)
- **`.claude/settings.json`** — registers the hook on `SubagentStop` with matcher `dev` (scope limited to dev sub-agents only, not `acceptance`/`critic`)
- **`.mercury/docs/EXECUTION-PLAN.md` §2-3** — marked implemented with reference to Issue #206 and the adapter path
- **20/20 unit tests pass** via `node --test` (10 hook integration cases, 10 resolve-command cases) including 2 persistence tests added post-dual-verify (1→2→3 block cycle + stale counter clear on green run)

### Acceptance criterion fulfillment

| Criterion | Fulfilled by |
|---|---|
| Dev sub-agent cannot stop while tests are failing | `hook.cjs:49-53` — on non-zero exit code, emits `{"decision": "block", "reason": "..."}` on stdout + exit 0 → Claude Code blocks the `SubagentStop` event per spec |
| Mechanical (not LLM-level) | The exit-code check is process-level — there is no LLM classification between the test runner and the block decision. `run-command.cjs` uses `child_process.spawn` with `shell: true` and inspects `exit_code` directly |
| Only applies to dev sub-agents | `hook.cjs:9` — `const GATED = ['dev']`; non-matching agents no-op exit 0 |
| Spec-compliant output | All output matches https://code.claude.com/docs/en/hooks: `decision: "block"` JSON on block path, no stdout + exit 0 on "no opinion" path, no use of `{"decision": null}` or other non-spec values (see Q15 resolution) |

### What Phase 2 does NOT yet provide (out of scope, tracked as follow-ups)

- **End-to-end integration test**: a real dev-pipeline run with an intentional failing test. Deferred because it requires either a purpose-built test harness (too heavy for post-merge validation) or a natural occurrence during normal Mercury self-development. The adapter's 20 unit tests cover hook logic comprehensively; integration testing is a soak-time observation, not a blocker for Phase 2 sign-off.
- **Extension to `acceptance` and `critic` sub-agents**: design subagent Q3 answer was "start with `dev` only; extend if needed". No evidence yet that those agents need the same gate.
- **Default test-command strict mode**: Q2 was resolved as fail-open default with `MERCURY_TEST_GATE_STRICT=1` opt-in. If empirical operation shows the fail-open default is too permissive, flip the default in a follow-up.

---

## Non-Selected Candidate Analysis — What Mercury Should Still Absorb

Phase 2-1's 4 REJECT/DEFER verdicts are scope-narrow: **they apply only to the Stop-hook mechanical-enforcement question**. Each candidate has substantial value in other dimensions that Mercury should selectively absorb. This section is the core of DEC-4's substantive mandate — it records the non-selection decisions without losing sight of what these projects DO offer.

### 1. `obra/superpowers` — skill library of record

**Phase 2-1 verdict**: REJECT (PR #197) — no Stop hook scaffold.
**What it IS**: an Anthropic-marketplace-vetted skill library with a "TDD red-green-refactor" + "verification-before-completion" + "subagent-driven-development" workflow methodology. Currently at v5.0.7 with 14+ skills shipped and active maintenance. `/plugin install superpowers@claude-plugins-official`.

The REJECT was narrow: Superpowers' `hooks.json` registers exactly one hook (`SessionStart`), so there's no `Stop`/`SubagentStop` scaffold for Mercury to layer on top of. But the REJECT does NOT mean the project lacks merit — it means it's the wrong shape for the Stop-hook question specifically.

**Why Mercury should look at Superpowers anyway** (per user mandate: *"官方插件，具有极高评价，肯定有可取之处"*):

| Superpowers capability | Mercury's current state | Cherry-pick potential |
|---|---|---|
| `verification-before-completion` skill — 5-step Gate Function workflow | Mercury has `dual-verify` + `auto-verify` skills but no verification-first discipline at the skill level | **HIGH**. Could be cherry-picked into `.claude/skills/` and composed with Mercury's existing verify skills. |
| `test-driven-development` skill — absolute "NO PRODUCTION CODE WITHOUT A FAILING TEST FIRST" rule | Mercury's dev-pipeline has acceptance review but not TDD discipline | **MEDIUM**. Would need Mercury dev-agent frontmatter adjustment; useful for self-development workstream where Mercury writes Mercury code. |
| `subagent-driven-development` skill — two-stage review loop via fresh subagents | Mercury has `dev-pipeline` (Main → Dev → Acceptance) but no spec→quality second pass | **MEDIUM-HIGH**. Natural extension of `dev-pipeline` — add a spec-review subagent before dev. |
| `systematic-debugging` skill — structured bug investigation workflow | Mercury has no formal debugging methodology | **MEDIUM**. Useful when `mercury-test-gate` flags a failing test Mercury itself must fix. |
| `brainstorming` skill — structured ideation | Mercury has design subagent but not a skill-level brainstorm framework | **LOW**. Design subagent covers most use cases. |
| `executing-plans` skill — plan decomposition and execution discipline | Overlap with Mercury's TaskBundle pattern (DEC-2) | **LOW**. Already have TaskBundle. |
| 14-skill library maintained at `obra/superpowers-skills` → re-merged into main repo | N/A — Mercury doesn't have a skill library curation workflow | **Cross-cutting**: Mercury could adopt Superpowers' "skill library under a single repo" pattern for its own `.claude/skills/` organization. |

**Follow-up Issue to file** (not as part of this DEC-4 merge): *"Selectively cherry-pick superpowers skills into `.claude/skills/` after Phase 2 soak period"*. Pin to Superpowers commit `917e5f5` (the SHA the Phase 2-1 ADR pinned) until a fresher audit. Target skills (in priority order): `verification-before-completion`, `subagent-driven-development`, `systematic-debugging`.

**Risks to track**:
- **Architecture churn**: Superpowers v1→v2→v5 saw a split into `obra/superpowers-skills` (now archived) and re-merge into the main repo within ~3 weeks. Any cherry-pick should be pinned to a specific SHA, not `main`.
- **Marketplace-native assumption**: skills assume a plugin runtime context (env vars like `CLAUDE_PLUGIN_ROOT`). Cherry-picking into `.claude/skills/` may break the env-var-dependent startup logic; would need a small adapter.
- **TDD discipline collision**: if Mercury adopts `test-driven-development` skill AND `mercury-test-gate` adapter, the two layers both enforce test-first discipline at different points. Compatible but needs explicit coordination.

### 2. `HKUDS/OpenSpace` — self-evolving harness

**Phase 2-1 verdict**: REJECT (PR #204) — no Stop hook + 15-day-old pre-1.0 churn + not published on PyPI under a usable name (name-squatted by Brandon Sexton's unrelated 2023 astrodynamics package).
**What it IS**: a self-evolving skill framework + MCP server + cloud-community skill sharing at `open-space.cloud`. Provides AUTO-FIX / AUTO-IMPROVE / AUTO-LEARN mechanisms that let skills improve themselves through task execution.

The REJECT was **compounded**: both category mismatch (skill evolution, not test gating) AND immaturity. The category-mismatch reason is permanent; the immaturity reason is time-limited — OpenSpace will mature.

**Why Mercury should look at OpenSpace anyway** (per user mandate: *"openspace 的 harness 自我进化也是重要课题"*):

The self-evolving-harness question is actually **important for Mercury's long-term roadmap**. Mercury's current design is static: skills, agents, and workflows are human-authored and hand-updated. OpenSpace asks a deeper question: *what if the harness updates itself?*

| OpenSpace capability | Relevance to Mercury | Absorption path |
|---|---|---|
| **AUTO-FIX**: skills self-repair when they encounter failures | Mercury has `dual-verify` for pre-merge, but no self-repair for skill-internal failures. If `dev-pipeline` phase 3 hits a skill bug, it surfaces as a full pipeline failure. | **HIGH long-term value**. Could be a Mercury-native "skill post-failure-fix" workflow where a dev subagent examines the skill, proposes a fix, and submits a PR — meta-development of Mercury by Mercury. |
| **AUTO-IMPROVE**: capture successful execution patterns, generalize into reusable skills | Mercury has no workflow to turn successful ad-hoc sequences into persistent skills. Every session re-invents the same solutions. | **CRITICAL long-term value**. This is the load-bearing capability for Mercury to get better over time without human curation. Feed: completed dev-pipeline runs → pattern extraction → skill proposal → human ratification → commit to `.claude/skills/`. |
| **AUTO-LEARN**: extract and store winning workflows post-task | Partial overlap with autoresearch's byproduct recycling (`feedback_research_byproduct_recycling` memory) but that is manual. | **HIGH value**. Extends autoresearch's recycling to the general dev-pipeline, not just research. |
| **Quality monitoring dimensions** (Skills / Tool Calls / Code Execution rates) | Mercury has no harness self-check metrics. DEC-3 Measurement Protocol is manual `/usage` snapshots for token tiering, not skill quality. | **MEDIUM value**. Could seed Mercury's future harness self-check workstream (#141). |
| **Cloud skill sharing via `open-space.cloud`** | Mercury is single-user; cloud sharing is orthogonal. | **NOT APPLICABLE** unless Mercury pivots to multi-user, which is not on DIRECTION.md. |
| **MCP server mode** (`openspace-mcp` entry point) | Mercury has MCP tools (codex, obsidian, etc.); could consume OpenSpace as another MCP tool for skill-evolution queries | **LOW value short-term** (adapter needed), **MEDIUM long-term** (after OpenSpace reaches v1.0+) |

**Where #141 fits**: Mercury already has Issue #141 open as a deferred-research tracker for OpenSpace's self-evolving skill engine. Session 26 posted a recycle comment (#141#issuecomment-4205801987) with:
- 6-row OpenSpace-feature → Mercury-integration table
- Deferred-research conditions: OpenSpace reaches v1.0 + 30 days production use + PyPI publication under non-collided name
- Integration shape sketches

DEC-4 cross-references #141 as the canonical tracking location. **Do not file another Issue**; Issue #141 is sufficient.

**Risks to track**:
- **Immaturity**: 15 days old at Phase 2-1 evaluation, pre-1.0, 8 runtime bugs patched in a single review round, MCP security leak patched 5 days before evaluation. Wait for v1.0 + production soak.
- **PyPI name collision**: cannot `pip install openspace`; only submodule or `git+` install works. If HKUDS doesn't publish under a unique name, the dependency story stays awkward.
- **"Self-evolution" is dual-use**: a skill library that modifies itself is both more adaptive AND more unpredictable. Mercury's principle (`CLAUDE.md`: "module is upward-compatible") suggests Mercury should absorb this pattern with strong guardrails — e.g., all AUTO-IMPROVE outputs go through `dev-pipeline` review before landing.
- **"Harness self-check" meta-question**: the deeper challenge is NOT "does Mercury use OpenSpace?", it's "does Mercury's harness look at its own behavior and improve?" That's a Mercury-native workstream #141 should track regardless of whether OpenSpace is the mount.

### 3. `gsd-build/get-shit-done` (GSD) — agent library

**Phase 2-1 verdict**: REJECT (PR #193) — 9 hooks, all `PreToolUse`/`PostToolUse`/`SessionStart`/statusLine, zero `Stop`.
**What it IS**: 48k+ stars, multi-year history, published as `get-shit-done-cc@1.34.2` on npm, broad agent library + workflow methodology.

**Why Mercury should look at GSD anyway** (already partially tracked in #192):

| GSD capability | Mercury state | Cherry-pick potential |
|---|---|---|
| Multi-role agent library (architect, reviewer, executor variants) | Mercury has 6 agents (main/dev/acceptance/critic/research/design) — smaller than GSD | **MEDIUM**. Individual agent definitions may be cherry-pickable if their role maps to a Mercury gap. |
| Curated command library for `/gsd` workflows | Mercury has slash-command skills (`/autoresearch`, `/dual-verify`, `/pr-flow`, `/dev-pipeline`) | **LOW**. Mercury's slash commands already cover the major workflows. |
| `PreToolUse`/`PostToolUse` hook patterns | Mercury already has its own (scope-guard, web-research-gate, pre-commit-guard) | **LOW**. Mercury's hooks are Mercury-specific; GSD's are differently focused. |
| SessionStart hook for workflow bootstrap | Mercury has `session-init.sh` hook | **LOW**. Different design; replacement would not improve Mercury. |

**Existing tracking**: Issue #192 already exists for "evaluate cherry-picking GSD agents after Phase 2". DEC-4 cross-references #192; no new Issue needed.

### 4. `Yeachan-Heo/oh-my-claudecode` (OMC) — Layer model companion (now installed)

**Phase 2-1 verdict**: DEFER (PR #195) — has Stop hook scaffold but gate is LLM-level, not mechanical.
**Path β resolution**: user installed OMC via `/plugin marketplace add` + `/plugin install oh-my-claudecode` in session 26 (2026-04-08). The chore commit `e8dab35` records the `enabledPlugins` flip. OMC is now present alongside `mercury-test-gate`.

**Layer model** (per design doc §3): Mercury and OMC register **independent** `SubagentStop` hooks. Both fire. Either can block.

| Layer | Hook source | Gate type | When it fires |
|---|---|---|---|
| Mechanical | `adapters/mercury-test-gate/hook.cjs` | Exit-code check on test command | Every `SubagentStop` from `dev` agents |
| LLM-level | OMC's `scripts/persistent-mode.cjs` (Ralph loop) | LLM judgement via iteration counter | Every `SubagentStop` during UltraQA cycles |

**Combined semantics**: a dev sub-agent stop is allowed only if BOTH layers permit it:
- Mercury layer permits: tests pass (or fail-open + non-strict)
- OMC layer permits: UltraQA Ralph loop agrees work is done (LLM-level)

This is **stronger than Path α** (which would accept only OMC's LLM layer) and **stronger than pure Path δ** (which would rely only on Mercury's mechanical layer without the LLM complement).

**OMC's other value** beyond the Stop hook:
- **UltraQA mode** — `/oh-my-claudecode:ultraqa` command provides structured QA cycling (test → verify → fix → repeat until goal)
- **Agent teams** — `team`, `ultrawork`, `ultrapilot` commands for parallel execution patterns Mercury doesn't have
- **Deep research pipeline** — `deep-dive` command (2-stage trace → deep-interview) complements Mercury's autoresearch skill
- **Skill catalogs** — `skill`, `learner`, `skillify`, `wiki` commands for skill lifecycle management
- **Installation now supported** — the chore commit `e8dab35` makes OMC part of Mercury's default environment; other developers cloning the repo will see the plugin flip and can `/reload-plugins`

**Follow-up actions** (not requiring new Issues):
- Document in `.mercury/docs/guides/` (if/when written) how Mercury's `mercury-test-gate` and OMC's Ralph-loop gate interact
- Observe during first month: does the combined layering cause any false-positive blocks? If so, Q2 fail-mode default may need tightening or loosening

---

## Consequences

### Workflow changes Mercury owner should know

1. **Dev sub-agents cannot complete `SubagentStop` while tests are failing.** After PR #207 merged, any dev-pipeline-dispatched work that leaves tests red will block at the stop event with a Mercury-branded `decision: "block"` message pointing at the failing test command output. The agent sees the block message and can attempt to fix.
2. **Bounded retry**: after 3 consecutive stop attempts with red tests, the gate lets the stop proceed + emits an AUDIT log to stderr. This prevents infinite loops AND prevents trivial "block once then retry" bypass. The `MAX_BLOCKS = 3` constant in `attempt-tracker.cjs:4` can be adjusted if 3 turns out to be wrong in practice.
3. **Default fail-open on unresolvable test command** (Q2): new projects or docs-only projects without `.mercury/config/test-gate.yaml`, `package.json`, `pyproject.toml`, `Makefile`, or `Cargo.toml` will fail open with a stderr WARNING. Opt-in strict mode via `MERCURY_TEST_GATE_STRICT=1` env var.
4. **OMC is now a Mercury plugin dependency**: the chore commit `e8dab35` adds `oh-my-claudecode@omc: true` to `.claude/settings.json`. Other developers need to run `/plugin marketplace add https://github.com/Yeachan-Heo/oh-my-claudecode` once, then `/reload-plugins`. The plugin is opt-in via `enabledPlugins` — if a developer objects, they can flip it back to `false` locally without affecting others.

### Positive outcomes

- **Real mechanical guarantee**: the core Phase 2 criterion is met. `grep -r 'decision.*block' adapters/mercury-test-gate/hook.cjs` surfaces the exact line, and `node --test adapters/mercury-test-gate/test/hook.test.cjs` reproduces the enforcement path.
- **Mount-first principle honored**: OMC is mounted untouched (via plugin). The only Mercury-owned code is the 200-LOC adapter that fills the load-bearing gap OMC doesn't ship. This is the correct application of the ≤200 LOC adapter cap rule from CLAUDE.md (200 is at-cap-compliant).
- **Layer model preserves both gates**: Mercury and OMC both fire on `SubagentStop`, giving Mercury belt-and-suspenders enforcement (mechanical + LLM-level) without redundant code.
- **All 4 Phase 2-1 candidates are now documented for selective future absorption**: none are wasted. Even the REJECTs have explicit follow-up paths (Superpowers cherry-pick, OpenSpace #141, GSD #192).

### Negative / trade-offs

- **Adapter is now Mercury-owned code to maintain**: 200 LOC of Node.js + tests (at the ≤200 LOC cap — compliant at the boundary) must be kept working across Node version upgrades, Claude Code spec changes, and platform differences. The `code.claude.com/docs/en/hooks` spec was pinned at 2026-04-08 verification; if Anthropic changes the Stop hook schema, Mercury must follow.
- **OMC dependency adds supply-chain surface**: `/plugin install oh-my-claudecode` pulls a third-party plugin. Mercury's security posture now depends on OMC's maintenance quality. Observable via: `/oh-my-claudecode:omc-doctor` diagnostic command.
- **Fail-open default is a weakened guarantee** (Q2): projects without a recognized test manifest default to letting stop proceed. A project maintainer who expects strict gating must set `MERCURY_TEST_GATE_STRICT=1` explicitly. This is a deliberate trade-off for UX (don't brick docs-only projects) but a reviewer reading this ADR should know it exists.
- **Bounded retry is N=3 by configuration, not enforcement** (Q14): a determined bypass could in theory trigger 3 blocks then a 4th successful stop with still-red tests. Mitigation: the 4th attempt logs a MAIN-visible AUDIT trail to stderr. A more-strict variant could be built as a follow-up if empirical operation shows abuse.

### Unverified items (kept for future empirical observation)

- **Real-world block trigger count per week**: unknown until observed. If it's zero for weeks, the gate is either working perfectly or dev agents never write failing tests — both are hard to distinguish without intentional failure injection.
- **OMC Ralph loop interaction under test-failure**: when both Mercury and OMC fire, does OMC's reason message get shown? Does the LLM see both? Unknown until observed.
- **Windows-specific process-tree kill edge cases**: `taskkill /F /T /PID` is well-documented but has edge cases with detached Git Bash subprocesses. Mercury dev primarily runs on Windows + Git Bash — any regression here should surface quickly.
- **≤200 LOC cap headroom**: the adapter is at exactly 200 LOC — at the cap boundary, compliant. Any future enhancement (more auto-detect patterns, more fail-mode nuance, per-agent scope expansion) needs either trimming elsewhere or a CLAUDE.md cap relaxation. The cap exists for mount-first purity; trimming is preferred.

---

## Phase 3 Prerequisites

Phase 3 of `.mercury/docs/EXECUTION-PLAN.md` will NOT start until the following are complete:

1. **Phase 2 soak period** (suggested 7-14 days normal operation) — surface any integration issues with the `mercury-test-gate` adapter before declaring Phase 2 fully mature.
2. **Optional: integration test** — at least one dev-pipeline run with an intentional failing test, documented as evidence the gate fires in practice. Deferred per user decision (session 26: *"故意失败可能有些困难，该 hook 需要时间验证，或者 test 工具齐备"*). Soak-time observation is an acceptable substitute.
3. **OMC-layer coexistence observation** — confirm Mercury's hook + OMC's Ralph loop don't produce conflicting decisions in normal operation.
4. **Follow-up Issues filed** (optional but recommended):
   - *Cherry-pick superpowers skills (verification-before-completion, subagent-driven-development, systematic-debugging)* — per §1 analysis above, after pinning to specific Superpowers SHA
   - *#141 re-evaluation checkpoint* — when OpenSpace reaches v1.0 (tracked passively; check every 30 days)
5. **DIRECTION.md update** — add a "Post-Phase-2 observations" section noting what the Phase 2-1 exhaustive evaluation taught about the Claude Code ecosystem (skill libraries, hook capabilities, publication channels). This is valuable methodology writeup material.

Phase 3 start criteria will be decided when these prerequisites are satisfied, NOT pre-committed in this DEC-4.

---

## References

- Parent: Phase 2 #181
- Implementation Issue: #206 (closed)
- Implementation PR: #207 (merged `1d335fb`, 2026-04-08)
- Chore commit: `e8dab35` (OMC plugin enable + `.omc/` gitignore, direct-develop under explicit user exception)
- Phase 2-1 ADRs (all merged):
  - GSD REJECT: `.mercury/docs/research/phase2-1-get-shit-done-evaluation.md` (PR #193)
  - OMC DEFER: `.mercury/docs/research/phase2-1-omc-evaluation.md` (PR #195)
  - Superpowers REJECT: `.mercury/docs/research/phase2-1-superpowers-evaluation.md` (PR #197)
  - OpenSpace REJECT: `.mercury/docs/research/phase2-1-openspace-evaluation.md` (PR #204)
- Design doc: `.mercury/docs/design/phase2-stop-hook-resolution.md` (PR #205, merged `a3696f8`)
- Prior DECs:
  - DEC-2 TaskBundle Lightweight Dispatch
  - DEC-3 Model Tiering for Sub-Agent Dispatch
- Tracking Issues for non-selected candidates:
  - #141 — OpenSpace self-evolving skill engine (deferred research, recycle comment `#141#issuecomment-4205801987`)
  - #192 — GSD agent cherry-pick evaluation (pre-existing tracker)
  - #209 — Superpowers skill cherry-pick (verification-before-completion, subagent-driven-development, systematic-debugging)
- Claude Code Stop hook spec: https://code.claude.com/docs/en/hooks (verified 2026-04-08 in design doc and implementation review)
- Mercury direction: `.mercury/docs/DIRECTION.md`
- Mount-first + adapter cap: `CLAUDE.md` MUST section
