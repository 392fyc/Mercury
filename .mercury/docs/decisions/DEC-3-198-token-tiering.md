# DEC-3: Model Tiering for Sub-Agent Dispatch (#198)

**Status**: Accepted
**Date**: 2026-04-08
**Issue**: #198
**Branch**: feat/issue-198-tiering

## Context

Mercury currently runs every sub-agent (dev, acceptance, critic, research, design) with `model: inherit`, which resolves to the main session's model — Opus 4.6 on Max plans. Long autonomous runs drain the Opus weekly bucket far faster than necessary because routine sub-agent work (code edits, test running, web summarization) does not benefit from Opus-level reasoning.

Two rounds of research established the facts:

- **Round 1-4 (`Mercury_KB/04-research/RESEARCH-TOKEN-TIERING-198.md`)** — API surface: frontmatter `model:` field is officially supported, accepts `sonnet`/`opus`/`haiku`/`inherit`/full IDs. Per [code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents), the resolution priority is: (1) `CLAUDE_CODE_SUBAGENT_MODEL` env var, (2) per-invocation `model` parameter, (3) subagent frontmatter `model:`, (4) main conversation model. No `minModel:` field exists. Frontmatter is not a hard lock — orchestrator may override at call time.

- **Round 5 (`Mercury_KB/04-research/RESEARCH-TOKEN-TIERING-198-RUNTIME.md`)** — Runtime behavior: per-model rate-limit buckets are independent ([Anthropic API rate-limits doc](https://platform.claude.com/docs/en/api/rate-limits): *"Rate limits are applied separately for each model; therefore you can use different models up to their respective limits simultaneously."*). Sub-agents dispatched with `model: sonnet` consume the Sonnet bucket, NOT the parent Opus bucket. Net Opus quota savings ≈ 95% on routed traffic, since Task tool only returns the sub-agent's final summary message ([anthropics/claude-code#10164](https://github.com/anthropics/claude-code/issues/10164)).

## Decision

Adopt a three-tier model assignment for Mercury sub-agents:

| Tier | Model | Mechanism | Used by |
|---|---|---|---|
| T1 | Opus 4.6 | Main session (`inherit`) + explicit `model: opus` | main, design, critic |
| T2 | Sonnet 4.6 | Explicit `model: sonnet` frontmatter | dev, acceptance, research |
| T3 | Codex gpt-5.4 | `mcp__codex__codex` tool call from any tier | mechanical edits, audit, rescue |

### Per-agent assignment

| Agent | `model:` | Rationale |
|---|---|---|
| `main.md` | `inherit` | Documentation only — `main.md` is not dispatched as a sub-agent. The main role IS the session, which runs at user-tier default (Opus on Max). |
| `dev.md` | `sonnet` | SWE-bench Verified gap is only 1.2 pp (Opus 80.8% vs Sonnet 79.6%) — within noise for code-edit workloads. |
| `acceptance.md` | `sonnet` | Acceptance is test-runner + diff inspection, not deep reasoning. Same code-task class as dev. |
| `critic.md` | `opus` | Existing critic spec mandates *"SHOULD run on a different model than the dev agent to avoid self-congratulation bias"*. With dev now on Sonnet, critic moves to Opus to preserve the bias separation. Critic also benefits from Opus's stronger adversarial reasoning. |
| `research.md` | `sonnet` | Single-shot web search + summarize is well within Sonnet's capability. |
| `design.md` | `opus` | Architectural proposals + trade-off analyses are GPQA-Diamond-class work — Sonnet shows a 17.2 pp gap on that benchmark ([NxCode comparison](https://www.nxcode.io/resources/news/claude-sonnet-4-6-vs-opus-4-6-complete-comparison-2026)). |

### Skill model exception: autoresearch / deep-research

Skills are prompt expansions executed in the **invoking** session's context — they inherit whatever model the caller is on. autoresearch's synthesis phase is GPQA-Diamond-class deep reasoning and must run on Opus.

**Rule**: autoresearch (and any future deep-research class skill) MUST be invoked directly from Mercury Main (`/autoresearch ...`), NOT delegated through the `research` sub-agent. This keeps the skill execution in Main's Opus context.

The `research` sub-agent (Sonnet) remains the right tool for one-shot web lookups + summarization. The two roles are deliberately split.

### Why NO `CLAUDE_CODE_SUBAGENT_MODEL` env var

Initial draft planned to set `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` in `.claude/settings.json` as a belt-and-suspenders default. **This was wrong and has been removed.**

Per the [official sub-agents doc](https://code.claude.com/docs/en/sub-agents) (verified 2026-04-08), the resolution priority is:

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable, if set
2. Per-invocation `model` parameter
3. Subagent definition's `model` frontmatter
4. Main conversation's model

The env var is **priority 1 — a HARD override of frontmatter (priority 3)**. Setting it globally to `sonnet` would clobber `critic.md` and `design.md`'s explicit `model: opus`, forcing them to Sonnet and breaking both the critic bias-separation and the design deep-reasoning intent.

**Mercury uses explicit frontmatter only.** No env-var default. This is safer because:
- Adding a new agent requires explicit `model:` in the frontmatter — no silent defaulting.
- Changing tier assignment requires a visible code change in the agent file.
- Reviewers can audit tier assignments by grepping `^model:` across `.claude/agents/`.

## Implementation Guardrails

1. **Explicit, not inherited** — every sub-agent file that is actually dispatched (`dev`, `acceptance`, `critic`, `research`, `design`) gets `model: sonnet|opus` written out. Do NOT rely on `inherit` (anthropics/claude-code#5456 historical bug; #19174 documentation ambiguity through 2026-02). **Exception**: `main.md` keeps `model: inherit` because it documents the top-level orchestrator role and is never dispatched as a sub-agent — it runs as the user's session model.
2. **No `CLAUDE_CODE_SUBAGENT_MODEL` env var** — it would hard-override explicit frontmatter for critic/design. Explicit frontmatter is the single source of truth.
3. **autoresearch/deep-research stays on Main** — never dispatched through research sub-agent.
4. **Opus-required tasks** — main session, design proposals, critic adversarial review, dual-verify orchestration, ADR drafting.
5. **Watch all-models weekly cap** — tiering preserves Opus weekly but does NOT relieve the all-models weekly aggregate. Quantify after first week of operation. Empirically measured caps documented in [anthropics/claude-code#12487](https://github.com/anthropics/claude-code/issues/12487).

## Consequences

### Positive

- Opus weekly bucket consumption reduced by routing dev/acceptance/research traffic to Sonnet — preserves headroom for the binding constraint on Mercury autonomous runs.
- Sonnet sub-agents continue functioning when Main Opus is rate-limited (per-model bucket independence) — improves dev-pipeline robustness on long runs.
- Critic moves to Opus, satisfying the existing "different model than dev" bias-separation requirement that was previously implicit.
- **Bonus: Opus tier runs at 1M context on Max / Team / Enterprise plans automatically** *(verified 2026-04-08; subject to Anthropic plan-policy changes — re-check on any future plan update)*. Post-merge runtime verification showed `critic` and `design` sub-agents self-report as `claude-opus-4-6[1m]` — the 1M-token context window. Verification records (runtime self-report quotes + empirical test table) are in [`Mercury_KB/04-research/MEASURE-198-baseline.md`](../../../Mercury_KB/04-research/MEASURE-198-baseline.md) under the "RESOLVED 2026-04-08" section. Per the [Claude Code model-config doc](https://code.claude.com/docs/en/model-config) and [Anthropic 1M GA announcement](https://claude.com/blog/1m-context-ga) (both accessed 2026-04-08): *"On Max, Team, and Enterprise plans, Opus is automatically upgraded to 1M context with no additional configuration."* This gives critic and design the ability to ingest large PR diffs or multi-document architectural contexts in a single pass without truncation — an upside not anticipated when the ADR was drafted.
  - Sonnet 4.6 on Max can ALSO reach 1M context *(as of 2026-04-08)*, but only via `/extra-usage` opt-in (Sonnet 1M is NOT included in the Max subscription by default). Mercury's Sonnet-tier sub-agents (`dev`/`acceptance`/`research`) operate at the standard 200k context window in practice, which is sufficient for their workloads (code edits, test runs, one-shot web lookups). No action required.

### Negative / Caveats

- 5h rolling session limit is unchanged — all sub-agent traffic still drains it. For burst protection, see deferred [#199](https://github.com/392fyc/Mercury/issues/199).
- All-models weekly aggregate is unchanged — tiering shifts cost between buckets, doesn't reduce total token volume.
- Sonnet weekly sub-cap is now reachable. If Mercury workload skews dev-heavy, Sonnet weekly may bind before Opus weekly. Monitor `/usage`.
- Critic on Opus costs more per critic invocation than on Sonnet — accepted because critic runs at end-of-cycle, not in inner loops.

### Unverified items (kept for future empirical observation)

- Exact byte cost of Task tool result return path (Anthropic does not publish).
- Whether Pro/Max plan weekly buckets follow the same per-model partitioning as the API doc rate-limits — only inferred via empirical reports, no Anthropic-confirmed statement.
- Sonnet weekly sub-cap vs all-models cap relative consumption rate under typical Mercury workload — requires post-implementation measurement.

## Measurement Protocol (post-merge validation)

The ADR's quantitative claims (≈95% net Opus savings on routed traffic; Opus weekly headroom restoration) are theoretical derivations from the research reports. A one-week empirical measurement is required to validate them against real Mercury workload.

### Baseline (pre-merge snapshot)

Capture once immediately BEFORE merging this PR:

```text
T-0: /usage output (main session)
  - Opus weekly: X% of limit
  - Sonnet weekly: Y% of limit
  - All-models weekly: Z% of limit
  - Current 5h rolling: W% of limit
Record in: Mercury_KB/04-research/MEASURE-198-baseline.md
```

### Measurement window

7 days of normal Mercury operation after merge. No special workload; measure whatever the owner naturally runs through dev-pipeline, pr-flow, autoresearch, etc.

### Metrics to capture (daily samples)

1. **Opus weekly consumption rate** — samples at day-end for 7 days. Expected: lower than equivalent pre-tiering baseline.
2. **Sonnet weekly consumption rate** — samples at day-end. Expected: non-zero (was near-zero pre-tiering).
3. **All-models weekly** — samples at day-end. Expected: similar total to pre-tiering (volume didn't change, only routing).
4. **Rate-limit incidents** — count of `/usage` 100% hits on any bucket during the week. Expected: fewer than pre-tiering on Opus; watch for new Sonnet-bucket hits.
5. **Critic-on-Opus quality check** — spot-check 2-3 critic reviews manually to confirm adversarial reasoning quality did not degrade vs pre-tiering.

### Recording location

```text
Mercury_KB/04-research/MEASURE-198-week1.md
  - T-0 baseline copy
  - 7 daily samples with timestamps
  - Final comparison: routing effectiveness %, bucket pressure change
  - Go / no-go decision: keep current tiering | rebalance (push more to Codex T3) | revert
```

### Decision criteria

- **Keep** — Opus weekly headroom improved ≥20% vs baseline AND no new Sonnet weekly hit
- **Rebalance** — Sonnet weekly sub-cap hit before all-models cap → route more to Codex T3 via tool call
- **Revert** — Measurable quality degradation on critic or dev output (should not happen if guardrails held)

This protocol is lightweight by design — no automation, just `/usage` snapshots + manual logging. Mercury owner runs it.

## References

- Issue #198 — Token management & model tiering
- `Mercury_KB/04-research/RESEARCH-TOKEN-TIERING-198.md` — API surface (Rounds 1-4, gate PASS)
- `Mercury_KB/04-research/RESEARCH-TOKEN-TIERING-198-RUNTIME.md` — Runtime behavior (Round 5+, gate PASS)
- [Anthropic API rate limits](https://platform.claude.com/docs/en/api/rate-limits) — per-model independence (canonical)
- [code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents) — frontmatter spec
- [code.claude.com/docs/en/model-config](https://code.claude.com/docs/en/model-config) — `CLAUDE_CODE_SUBAGENT_MODEL` env var
- [NxCode Sonnet 4.6 vs Opus 4.6 benchmark](https://www.nxcode.io/resources/news/claude-sonnet-4-6-vs-opus-4-6-complete-comparison-2026) — quality gap data
- anthropics/claude-code#12487 — empirical bucket sharing observations
- anthropics/claude-code#10164 — Task tool return path
- anthropics/claude-code#27665 — sub-agent model resolver code
- anthropics/claude-code#5456 — historical inherit bug
- anthropics/claude-code#19174 — documentation ambiguity (resolved 2026-02)
- anthropics/claude-code#4182 — sub-agent nesting prohibition
- DEC-2 — TaskBundle Lightweight Dispatch (related: dispatch-layer optimization)
- Issue #199 — burst-mode dual-process fallback (deferred)
- Issue #200 — hybrid autoresearch Codex-early/Opus-late (deferred)
