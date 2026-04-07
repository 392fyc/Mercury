# Phase 2-1 ADR — `Yeachan-Heo/oh-my-claudecode` (OMC) Evaluation

**Status**: DEFER
**Date**: 2026-04-07
**Issue**: #194
**Parent**: Phase 2 #181
**Decision authority**: Mercury main agent + user confirmation
**Research artifact**: `.research/reports/RESEARCH-omc-evaluation-2026-04-07.md` (4 rounds, 21 sources)

---

## Context

Phase 2 of Mercury's execution plan (`.mercury/docs/EXECUTION-PLAN.md:197`) requires a Quality
Gate hook that **blocks dev sub-agents from stopping while tests are failing**:

> Dev sub-agent 不能在 test 未通过时 stop

GSD (phase2-1) was evaluated and REJECTED — it has no Stop/SubagentStop hook at all. OMC is
the second candidate. DIRECTION.md describes OMC as: *"Stop hook 拦截，活跃维护"* — directly
naming the acceptance criterion.

Per Mercury's mount-first principle (`CLAUDE.md`): *"If an external project can solve the
problem, mount it via submodule rather than reimplementing."*

---

## Decision

**DEFER** `Yeachan-Heo/oh-my-claudecode` for Phase 2 Quality Gate.

OMC is NOT rejected — it is the strongest candidate found so far and may be selected after
Superpowers and OpenSpace are evaluated. DEFER means: continue the evaluation sequence before
committing, because OMC's integration model and semantic gap may be resolved by a simpler candidate.

---

## Rationale

### 1. Stop hook DOES exist — confirmed BLOCKING

OMC registers a `Stop` event hook (`scripts/persistent-mode.cjs`) that outputs:
```json
{"decision": "block", "reason": "[RALPH LOOP - ITERATION N/MAX] Work is NOT done. Continue working."}
```

The `decision: "block"` format is the correct Claude Code Stop hook blocking mechanism (feeds
`reason` back to Claude so it continues working). This is confirmed from official docs:
> "For the Stop hook specifically, `decision: "block"` prevents the turn from ending and the
> `reason` is fed back to Claude."
Source: https://code.claude.com/docs/en/hooks

Source: `hooks/hooks.json`, `src/hooks/persistent-mode/index.ts`

### 2. UltraQA mode is closest to Mercury's requirement

OMC's **UltraQA** (`/oh-my-claudecode:ultraqa --tests`) is the specific mode that targets
Mercury's use case:
- Runs project test command and checks output each cycle
- Blocks Stop events while `active && !all_passing` (via Priority 7 in persistent-mode.cjs)
- Cycles: run tests → architect diagnosis → fix → repeat (max 5 cycles)
- Exit: goal met (tests pass) or max cycles reached

This is closer to "cannot stop while tests fail" than Ralph mode (which is a general loop).
Source: `skills/ultraqa/SKILL.md`, `src/hooks/ultraqa/index.ts`, `scripts/persistent-mode.cjs:995-1020`

### 3. Semantic gap with Mercury's acceptance criterion

Mercury's criterion is **mechanical**: "if `npm test` exit code != 0, block Stop."
UltraQA's gate is **LLM-level**: the agent runs tests, observes output, and decides whether
to mark the cycle complete. An architect/critic agent then verifies.

This gap means:
- The agent could claim tests pass incorrectly (hallucination risk)
- No harness-level enforcement: exit code is not checked by the hook itself
- Mercury activation requires the dev agent to invoke UltraQA mode explicitly per task

This is the primary reason for DEFER rather than MOUNT.

### 4. Integration model: plugin-only, no submodule path

REFERENCE.md states: **"Only the Claude Code Plugin method is supported."**
Installation: `/plugin install oh-my-claudecode` via Claude Code marketplace.

No documented git submodule mounting path exists. Cherry-picking individual scripts
(`persistent-mode.cjs`) is technically possible but:
- Requires tracking OMC's TypeScript build pipeline
- The CJS imports from `dist/hooks/` module tree at runtime
- Not a supported or stable extraction surface

This violates Mercury's mount-first principle, which specifically calls for submodule mounting.
Source: `docs/REFERENCE.md` Installation section

### 5. Windows support: stronger than GSD, still risks

**Positive findings (Round 3-4)**:
- Ralph/UltraQA Stop hook (`persistent-mode.cjs`) is pure Node.js — does NOT require tmux
- `better-sqlite3` v12.8.0: prebuilt binaries for `win32-x64` and `win32-arm64` (confirmed from GitHub releases)
- `@ast-grep/napi`: ships `@ast-grep/napi-win32-x64-msvc` and `@ast-grep/napi-win32-arm64-msvc` optional packages
- Active Windows patching: `patchHooksJsonForWindows()` rewrites hook scripts for MSYS2/Git Bash compatibility (Issue #899, Claude Code UI bug #17088)
- Windows-specific test coverage: `dist/__tests__/cli-win32-warning.test.js`, `hud-windows.test.js`, `windows-patch.test.js`

**Remaining risks**:
- CI matrix: Ubuntu only — no Windows runners
- Team/multi-agent features require tmux (unavailable on native Windows; psmux is unofficial alternative)
- CRLF issues being filed: Issue #2282 (2026-04-07) "parseFrontmatter fails silently on CRLF"
- Official documentation: "Native Windows (win32) support is experimental"

Source: `docs/REFERENCE.md` Platform Support table; `.github/workflows/ci.yml`

### 6. Adapter line-count scenarios

| Scenario | Adapter LOC | Phase 2 outcome |
|---|---|---|
| A — Plugin install instruction | ~10 | Not a submodule; global npm dep |
| B — Cherry-pick persistent-mode.cjs | ~80-120 | Fragile, unsupported extraction |
| C — None (DEFER) | 0 | Revisit after remaining candidates |

Mercury's 200-line cap is technically satisfiable (Scenario A), but Scenario A is not the
submodule mount Mercury's design intent requires.

---

## Consequences

- **Phase 2-1 advances to next candidate**: **Superpowers** (`obra/superpowers`), described in
  DIRECTION.md as *"inline checklist + TDD red-green-refactor, 已进入 Anthropic marketplace"*.
- **DEFER is not REJECT**: if Superpowers and OpenSpace also fail to provide a mechanical
  exit-code Stop hook, return to OMC's UltraQA as the best-available option and accept the
  LLM-level semantic gap.
- **Mercury's mount-first principle is preserved**: DEFER is based on integration model
  mismatch (plugin-only, not submodule), not on preference for self-research.
- **Post-Phase-2 follow-up**: OMC's 29 agent definitions and 32 skills library (architect,
  critic, code-reviewer, debugger, security-auditor, qa-tester, executor, etc.) is potentially
  valuable for Mercury's agent role library — separate evaluation, similar to GSD Issue #192.

---

## Verification

Research artifact `.research/reports/RESEARCH-omc-evaluation-2026-04-07.md` covers all 7
evaluation dimensions across 4 autoresearch rounds with 21 source URLs.

Final autoresearch gate metrics:
- `question_answer_rate` 1.00 (PASS, threshold ≥0.9)
- `citation_density` ~0.97 (PASS, threshold ≥0.75)
- `unverified_rate` ~0.02 (PASS, threshold ≤0.1)
- `iteration_depth` 4 (PASS, threshold ≥4)

Verification: PASS (mechanical checklist in `.research/state/verification-omc-evaluation.md`)

---

## References

- Phase 2 parent Issue: #181
- This evaluation Issue: #194
- GSD evaluation (REJECTED): `.mercury/docs/research/phase2-1-get-shit-done-evaluation.md`
- Mercury direction: `.mercury/docs/DIRECTION.md`
- Phase 2 acceptance criterion: `.mercury/docs/EXECUTION-PLAN.md:197`
- Mount-first principle: `CLAUDE.md` MUST section
- Full research transcript: `.research/reports/RESEARCH-omc-evaluation-2026-04-07.md`
- OMC repo: https://github.com/Yeachan-Heo/oh-my-claudecode
- npm package: https://www.npmjs.com/package/oh-my-claude-sisyphus
- Stop hook docs: https://code.claude.com/docs/en/hooks
