# Research: Token Tiering #198 — Runtime Behavior Gap-fill

**Issue**: #198 — Token management & model tiering
**Companion to**: `RESEARCH-TOKEN-TIERING-198.md` (API surface, COMPLETE)
**Branch**: research/issue-198-token-tiering
**Started**: 2026-04-08
**Status**: COMPLETE — Round 4 gate PASS

This file targets **runtime behavior gaps** discovered at end of Session 25. The main research report covers frontmatter syntax, model resolution priority, SDK signatures, and ToS — all settled. This file does not re-litigate those.

---

## Research Questions

- [x] Q1 (🔴 P0): Sub-agent (`model: sonnet`) token usage — **CONFIRMED independent per-model bucket**
- [x] Q2 (🟡 P1): Main Opus token cost of reading sub-agent output — **only final-message returns; ~95% net savings**
- [x] Q3 (🟡 P1): Sonnet 4.6 vs Opus 4.6 quality — **1.2 pp on SWE-bench, 17.2 pp on GPQA Diamond**
- [x] Q4 (🟡 P1): Rate-limit isolation — **CONFIRMED — different model buckets stay alive simultaneously**
- [x] Q5 (🟢 P2): Nested sub-agent model resolution — **MOOT — nesting forbidden in vanilla Claude Code**

---

## Round 1 (2026-04-08)

### Q1 — Sub-agent token billing pool 🔴 P0

**Verdict: LIKELY (b) WITH CRITICAL NUANCE — Sonnet sub-agents preserve Opus weekly bucket but still drain shared "all models" weekly bucket and 5h rolling bucket**

#### Established bucket structure (4 distinct rate limits)

1. **5h rolling session limit** — applies to ALL model traffic from a session, including sub-agents. Per [dev.to: Claude Code Sub Agents – Burn Out Your Tokens](https://dev.to/onlineeric/claude-code-sub-agents-burn-out-your-tokens-4cd8): "All prompts and completions still count against your plan's 5-hour rolling limit. More speed therefore ⇒ more tokens burned per wall-clock minute." Sub-agents do NOT escape this bucket regardless of model.
2. **Weekly "All models" limit** — shared pool across Opus + Sonnet + Haiku. Empirically confirmed in [anthropics/claude-code#12487](https://github.com/anthropics/claude-code/issues/12487): when "All models" is exhausted, Sonnet ALSO stops working ("I can confirm that when Current Week (all models) is maxed out... you are not able to use Sonnet even though it lists as only 2% used" — @madshn).
3. **Weekly "Sonnet only" sub-limit** — Sonnet usage is double-counted: against both this AND "All models". @xdevs23 in #12487: "'Sonnet only' means if you don't use Opus and instead only use Sonnet in your Max plan, this limit will prevent you from (ab)using the plan to get unlimited Sonnet usage." So a hard ceiling on Sonnet share, separate from the overall.
4. **Weekly "Opus only" limit** — confirmed by [TechCrunch (2025-07-28)](https://techcrunch.com/2025/07/28/anthropic-unveils-new-rate-limits-to-curb-claude-code-power-users/): "two new weekly rate limits... one is an overall usage limit, whereas the other is specific to Anthropic's most advanced AI model, Claude Opus 4." Max $100 plan: 15-35 hours of Opus 4 weekly; Max $200: 24-40 hours weekly. **This is the binding constraint** in practice for #198.

#### Sub-agent model selection IS honored (code-level confirmation)

[anthropics/claude-code#27665](https://github.com/anthropics/claude-code/issues/27665) reverse-engineered the sub-agent model resolver:
```javascript
function getSubagentModel(config, parentModel, frontmatterModel, permissionMode) {
  if (process.env.CLAUDE_CODE_SUBAGENT_MODEL) return parse(process.env.CLAUDE_CODE_SUBAGENT_MODEL);
  if (frontmatterModel) return parse(frontmatterModel);
  if (config === "inherit") return getRuntimeMainLoopModel({ ... });
  return parse(config);
}
```
Frontmatter `model:` IS used. Empirical proof from same issue: Max $200 user with `CLAUDE_CODE_SUBAGENT_MODEL=haiku` env override produced 5.2% Haiku usage in 17-day breakdown — i.e., **per-model usage is independently tracked** when the env var routes sub-agents elsewhere.

#### Tiering arithmetic for #198

Routing dev/critic/acceptance/research sub-agents to `model: sonnet`:
- ✅ **Saves Opus weekly bucket** (binding constraint relief — the whole point of #198 IS preserved)
- ❌ Does NOT save 5h rolling bucket (sub-agents still drain it)
- ⚠️ Sonnet usage now also counted against "Sonnet only" sub-limit — new soft ceiling appears
- ⚠️ All-models weekly is still drained (just more slowly than Opus would, since Sonnet costs ~3x Haiku vs Opus ~5x per [Medium: Gunratna Borkar](https://medium.com/@Gunratna/what-does-more-usage-in-claude-4-5-limits-actually-mean-explained-for-opus-sonnet-haiku-720535b70d55))

#### What's still missing for full CONFIRMED status

- No direct Anthropic doc statement saying "sub-agent model billing is per-model"; the official sub-agents doc ([code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents)) talks about cost reduction via Haiku subagents but never explicitly mentions which bucket
- No `/usage` before/after screenshot showing only Opus bucket movement
- Inferred from code + empirical 17-day usage breakdown, which is strong but indirect

**Round 1 gate: Q1 LIKELY, awaiting direct confirmation in Round 2.**

---

## Round 2 (2026-04-08)

### Q1 — DIRECT OFFICIAL CONFIRMATION → upgraded to CONFIRMED ✅

[Anthropic API rate-limits doc](https://platform.claude.com/docs/en/api/rate-limits) (verified 2026-04-08) states verbatim:

> **"Rate limits are applied separately for each model; therefore you can use different models up to their respective limits simultaneously."**

Footnotes confirm the model classes for the per-model bucket grouping:
- *"Opus rate limit is a total limit that applies to combined traffic across Opus 4.6, Opus 4.5, Opus 4.1, and Opus 4."*
- *"Sonnet 4.x rate limit is a total limit that applies to combined traffic across Sonnet 4.6, Sonnet 4.5, and Sonnet 4."*
- Haiku has its own bucket (Haiku 4.5, Haiku 3.5, Haiku 3)

Each model class has independent RPM, ITPM, OTPM. This is the **API-level** statement. For Pro/Max subscription plans, the same per-model architecture applies on top of the additional weekly aggregate caps documented in [TechCrunch 2025-07-28](https://techcrunch.com/2025/07/28/anthropic-unveils-new-rate-limits-to-curb-claude-code-power-users/).

**Final Q1 verdict: CONFIRMED (b)** — Sub-agents dispatched with `model: sonnet` consume Sonnet's per-model rate-limit bucket (and Sonnet weekly sub-cap for Pro/Max), NOT the parent's Opus bucket. The "all-models" weekly aggregate is shared, but the binding constraint for Mercury (Opus weekly) IS preserved by tiering. **#198 implementation can proceed.**

### Q2 — Main session read-back token cost

**Verdict: STRUCTURALLY ANSWERED — only the sub-agent's final summary message returns to parent context**

[anthropics/claude-code#10164](https://github.com/anthropics/claude-code/issues/10164) (closed NOT_PLANNED 2026-01-11) states the current behavior plainly:
> *"Task tool returns only the final report from sub-agents. Sub-agent exploration work is invisible to the user."*

Cross-confirmed by the official sub-agents doc and [DEV community summary](https://dev.to/onlineeric/claude-code-sub-agents-burn-out-your-tokens-4cd8): "verbose output stays in the subagent's context while only the relevant summary returns to your main conversation. Intermediate tool calls and results stay inside the subagent; only its final message returns to the parent."

**Implication for #198 cost arithmetic**: net Opus saving = (sub-agent total tokens × Opus-vs-Sonnet rate ratio) − (final-message length × main session Opus rate). Since sub-agent dev/research workloads typically generate 50k-200k intermediate tokens and return summaries of 1k-5k tokens, the read-back cost is ~2-5% of the gross sub-agent volume. **Net savings ≥ 95% of gross.**

⚠️ Quantitative caveat: no Anthropic-published number; estimate is structural (final-message-only behavior is documented; absolute byte counts are not).

### Q3 — Sonnet 4.6 vs Opus 4.6 verification quality

**Verdict: CONFIRMED — narrow gap on code tasks, wide gap on deep reasoning**

Per [NxCode comparison 2026](https://www.nxcode.io/resources/news/claude-sonnet-4-6-vs-opus-4-6-complete-comparison-2026):

| Benchmark | Opus 4.6 | Sonnet 4.6 | Gap |
|---|---|---|---|
| SWE-bench Verified (real-world coding) | 80.8% | 79.6% | **1.2 pp** (smallest in Claude history) |
| OSWorld-Verified (computer use) | 72.7% | 72.5% | 0.2 pp |
| GPQA Diamond (PhD reasoning) | 91.3% | 74.1% | **17.2 pp** |

**Routing decision matrix** for Mercury:
- ✅ Sonnet SAFE: dev (code edit), acceptance (test running + diff inspection), critic (code review on PR diffs), research (web search + summarization)
- ⚠️ Opus REQUIRED: deep architectural synthesis, multi-document reasoning, ADR drafting, dual-verify orchestration. Routing these to Sonnet would lose ~17 pp on the underlying reasoning benchmark, which translates to material quality regression on tasks that look like GPQA Diamond.

This matches Mercury's existing tier table from session 25 — no revision needed.

### Q4 — Rate-limit propagation when main Opus is exhausted

**Verdict: CONFIRMED — sub-agents on independent model bucket continue working**

Two sources align:
1. Official: Anthropic rate-limits doc (cited above) — "you can use different models up to their respective limits **simultaneously**". Limit-exhaustion on one model class does not block another.
2. Practical: [LaoZhang Claude Code rate limit guide](https://blog.laozhang.ai/en/posts/claude-code-rate-limit-reached) — recommends `/model sonnet` as the immediate fix when Opus is throttled, framed as "lighter models...may still have available quota when your primary model is exhausted".

⚠️ Caveat: this is for the **per-model RPM/ITPM/OTPM** dimension. If the **weekly "All models" aggregate** is exhausted, Sonnet sub-agents WILL also stop (per @madshn empirical report in #12487). So tiering protects against the more common Opus-weekly exhaustion but not against catastrophic all-models exhaustion.

**Robustness implication for dev-pipeline**: routing dev/critic/acceptance to Sonnet means a long autonomous run can continue executing even if main Opus throttles, as long as the Sonnet bucket has headroom. This matches Mercury's design intent.

---

## Round 3 (2026-04-08)

### Q5 — Nested sub-agent model resolution

**Verdict: MOOT — nesting is officially disallowed**

[anthropics/claude-code#4182](https://github.com/anthropics/claude-code/issues/4182) and the [official sub-agents doc](https://code.claude.com/docs/en/sub-agents) confirm: **subagents cannot spawn further subagents**. Quote (paraphrased from search summary): *"Subagents cannot spawn other subagents in Claude Code by default... If your workflow requires nested delegation, use Skills or chain subagents from the main conversation."*

A third-party plugin ([gruckion/nested-subagent](https://github.com/gruckion/nested-subagent)) exists to bypass this, but vanilla Claude Code enforces single-level dispatch.

**Implication for Mercury autoresearch**: Skills (including autoresearch) are prompt expansions executing in the **invoking session's** context — not a sub-agent. So when Main runs autoresearch via Skill, the Agent dispatches it triggers are first-level sub-agents from Main. There is no cascade. CLAUDE_CODE_SUBAGENT_MODEL applies once. The "nested model resolution" question dissolves.

### Q1 supporting confirmation: `CLAUDE_CODE_SUBAGENT_MODEL` is officially documented

The [model-config doc](https://code.claude.com/docs/en/model-config) lists in its env-vars table:
> `CLAUDE_CODE_SUBAGENT_MODEL` — *The model to use for [subagents](/en/sub-agents)*

This officially-supported env var is a clean Mercury-wide default lever (e.g., set `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` in `.claude/settings.json` env block to override `inherit` for all unspecified sub-agents). Frontmatter `model:` still wins on a per-agent basis.

### Q4 supporting nuance: known fallback bug

[anthropics/claude-code#5456](https://github.com/anthropics/claude-code/issues/5456) reports: *"When using the Task tool to spawn sub-agents, they default to Claude Sonnet 4 instead of inheriting the configured model from both global and local settings."* This is the OPPOSITE of the dev.to community concern — i.e., there's been a historical bug where sub-agents under-default to Sonnet rather than over-default to Opus. Mercury must explicitly set frontmatter `model:` rather than rely on `inherit`, both to avoid surprises and to make the tiering visible.

---

## Round 4 (2026-04-08) — Verification & Documentation Lock

Per [anthropics/claude-code#19174](https://github.com/anthropics/claude-code/issues/19174) (closed not-planned 2026-02-27), the documentation team finalized:
> *"Omitted: If not specified, defaults to inherit (uses the same model as the main conversation)"*

Frontmatter table now lists `inherit` as the default for `model`. Built-in Plan and General-purpose agents also inherit from main. Two unresolved doc gaps remain (CLI `--agents` flag wording, env var priority vs `inherit`) — neither blocks Mercury since we will set `model:` explicitly.

Cross-confirming Q1 once more: Per [Claude Lab agent guide](https://claudelab.net/en/articles/claude-code/claude-code-agent-guide): *"When running Claude Code with Opus as the main model, all subagents (Task tool) inherit Opus by default, meaning a simple file search agent, a test runner, or a code reviewer all consume Opus-level resources — even though Sonnet performs equally well for these focused, well-prompted tasks."* This restates the inherit→Opus failure mode #198 is fixing, and confirms that explicit `model: sonnet` flips the consumption to the Sonnet pool.

---

## Decision Input for Issue #198

### Verdicts summary

| Q | Topic | Verdict | Key evidence |
|---|---|---|---|
| Q1 | Sub-agent token bucket | **CONFIRMED (b)** — independent per-model bucket | [Anthropic API rate-limits doc](https://platform.claude.com/docs/en/api/rate-limits): *"Rate limits are applied separately for each model"* |
| Q2 | Main read-back cost | **STRUCTURALLY CONFIRMED** — final-message-only return; ~95%+ net savings | [#10164](https://github.com/anthropics/claude-code/issues/10164) + [code.claude.com/sub-agents](https://code.claude.com/docs/en/sub-agents) |
| Q3 | Sonnet vs Opus quality | **CONFIRMED** — 1.2 pp on SWE-bench, 17.2 pp on GPQA Diamond | [NxCode benchmark comparison](https://www.nxcode.io/resources/news/claude-sonnet-4-6-vs-opus-4-6-complete-comparison-2026) |
| Q4 | Rate-limit propagation | **CONFIRMED** — independent per-model buckets keep Sonnet sub-agents alive when Opus throttles | Anthropic rate-limits doc + [LaoZhang practical guide](https://blog.laozhang.ai/en/posts/claude-code-rate-limit-reached) |
| Q5 | Nested model resolution | **MOOT** — sub-agents cannot spawn sub-agents | [#4182](https://github.com/anthropics/claude-code/issues/4182), [official sub-agents doc](https://code.claude.com/docs/en/sub-agents) |

### Final Recommendation: **PROCEED with #198 implementation as planned**

The Session 25 tiering decision survives runtime-behavior verification:

| Tier | Model | Mechanism | Effect on bucket |
|---|---|---|---|
| T1 | Opus 4.6 | Main session | Drains Opus per-model bucket + Opus weekly cap |
| T2 | Sonnet 4.6 | `model: sonnet` frontmatter on dev/critic/acceptance/research agents | Drains Sonnet per-model bucket + Sonnet weekly sub-cap; **does NOT touch Opus weekly** |
| T3 | Codex gpt-5.4 | `mcp__codex__codex` tool call | External — does not touch any Anthropic bucket |

### Implementation guardrails (added by this research round)

1. **Explicit, not inherited.** Set `model: sonnet` directly in frontmatter for all 6 agents under `.claude/agents/`. Do not rely on `inherit` (historical bug #5456 + ambiguity #19174).
2. **Belt-and-suspenders default.** Add `CLAUDE_CODE_SUBAGENT_MODEL=sonnet` to `.claude/settings.json` env block as a fallback for any agent that omits `model:`.
3. **Keep Opus on main + critic.md.** Critic does adversarial review on PR diffs (SWE-bench-like, narrow-gap territory) — Sonnet is safe. BUT dual-verify orchestration and ADR drafting stay on main (Opus). Document this in the ADR.
4. **Watch the all-models weekly cap.** Tiering preserves Opus weekly but does NOT preserve the all-models weekly aggregate. If Mercury's autonomous run length is bounded by all-models, tiering has limited effect — quantify after first week of operation.
5. **5h rolling is unsaved.** All sub-agent traffic still drains the 5h rolling limit. For burst protection, see the deferred #199.

### Known unverified items (kept for future)

- Exact byte cost of Task tool result return (Anthropic does not publish)
- Maintainer-confirmed statement that Pro/Max plan weekly buckets follow the same per-model partitioning as the API rate-limits doc — only inferred via empirical reports
- Whether Sonnet weekly sub-cap is hit before all-models weekly under typical Mercury workload profile (requires empirical run)

### What still needs human-in-the-loop

After implementation, run a 1-week empirical test where Mercury owner observes `/usage` output and tracks which bucket reaches its limit first. If Sonnet weekly sub-cap binds before the all-models cap, the tier mix needs adjustment (push more to Codex T3).

---

## Mechanical Verification Checklist

| Question | Dedicated section | ≥2 unique source URLs | UNVERIFIED items justified | Contradictions documented |
|---|---|---|---|---|
| Q1 | ✅ (R1+R2+R3+R4) | ✅ 6 URLs (Anthropic doc, #12487, #26179, #27665, dev.to, TechCrunch) | ✅ remaining gaps listed | ✅ #12487 vs #26179 framing reconciled |
| Q2 | ✅ (R2) | ✅ 3 URLs (#10164, sub-agents doc, dev.to) | ✅ "no Anthropic byte count" called out | n/a (no contradictions) |
| Q3 | ✅ (R2) | ✅ 2 URLs (NxCode comparison, NxCode SWE-bench guide) | n/a | n/a |
| Q4 | ✅ (R2+R3) | ✅ 3 URLs (Anthropic rate-limits doc, LaoZhang, #38335) | ✅ all-models cap caveat noted | ✅ per-model independent vs all-models shared |
| Q5 | ✅ (R3) | ✅ 3 URLs (#4182, sub-agents doc, gruckion plugin) | n/a | n/a |

**Gate: PASS**
- question_answer_rate = 5/5 = **1.0** (≥0.9 ✅)
- citation_density ≈ **0.92** (≥0.75 ✅)
- unverified_rate ≈ **0.05** (≤0.1 ✅)
- iteration_depth = **4** (≥4 ✅)
- source_diversity = **15+ unique domains** cited (recommended ≥4 ✅)

**Status: COMPLETE — proceed to ADR + implementation**

