---
name: deep-research
description: |
  Mercury's multi-round deep research protocol for comprehensive investigations requiring 3+ research questions, cross-source verification, or architectural decision analysis. Activates when TaskBundle.researchScope is "deep", when explicitly invoked, or when the research agent starts a session. Enforces iterative search loops with per-round quality gates and independent verification. Prevents premature completion via the "evidence over claims" iron rule. Triggers on: "深度调研", "深度研究", "deep research", "comprehensive research", "多轮调研", "research protocol", "调研协议".
user-invocable: true
allowed-tools: WebSearch, WebFetch, Read, Write, Grep, Glob, Agent, TodoWrite
---

# Mercury Deep Research Protocol

## Purpose

This skill governs **large-scale research tasks** that require multiple rounds of web search, cross-source verification, and structured synthesis. It prevents the most common agent failure mode: **declaring research complete based on confidence rather than evidence**.

## When This Protocol Applies

- TaskBundle `researchScope` is `"deep"`
- Research questions ≥ 3
- Cross-verification across ≥ 3 independent sources required
- Architectural decision analysis (comparing alternatives)
- Any research task explicitly routed to the Research Agent
- User invokes `/deep-research` or says "深度调研"

For lighter research (1-2 questions, single-source verification), use the `web-research` skill instead.

## Iron Rules

1. **NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE** — confidence is not evidence.
2. **Every factual claim requires a source URL or an explicit UNVERIFIED tag** — no exceptions.
3. **Agent self-reports are not evidence** — use independent verification (subagent or checklist).
4. **"Should work" / "probably" / "I believe" are banned** — use "verified at [URL]" or "UNVERIFIED".

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should be done now" | Run the quality gate |
| "I'm confident" | Confidence ≠ Evidence |
| "Covered the main points" | Check question_answer_rate ≥ 0.8 |
| "The search results confirmed it" | Show the URL and cited text |
| "Further research = diminishing returns" | Only the gate decides that |

## Iterative Research Loop

### Round Structure

```text
Round N:
  1. CONTEXT  — Read results.jsonl + existing KB report to restore state
  2. PLAN     — Identify which sub-questions to tackle this round
  3. SEARCH   — WebSearch/WebFetch (multiple calls per round)
  4. SYNTHESIZE — Integrate findings into KB 04-research/ report
  5. VERIFY   — Run quality gate checklist
  6. LOG      — Append round summary to results.jsonl
  7. DECIDE   — Gate passed? → trigger verification
                 Gate failed? → continue to Round N+1
                 Max iterations (10) → mark gaps + submit
```

### Quality Gate (per round)

| Metric | Threshold | How to Measure |
|--------|-----------|----------------|
| `question_answer_rate` | ≥ 0.8 | answered / total questions |
| `citation_density` | ≥ 0.6 | claims with URL / total claims |
| `unverified_rate` | ≤ 0.2 | UNVERIFIED / total claims |

All metrics are mechanically countable — no LLM self-evaluation for mandatory gates.

### State Files

```text
Mercury_KB/04-research/RESEARCH-{TOPIC}-{ISSUE}.md     — report
Mercury_KB/04-research/.research-state/results-{ISSUE}.jsonl — iteration log
```

Each `results.jsonl` line (schema matches `.claude/` version — see `.mercury/gates/research-quality.yaml`):
```json
{
  "round": 1,
  "timestamp": "2026-03-25T10:30:00Z",
  "questions_targeted": ["Q1", "Q3"],
  "sources_found": 5,
  "sources_verified": 4,
  "question_answer_rate": 0.6,
  "citation_density": 0.75,
  "unverified_rate": 0.1,
  "gate_passed": false,
  "notes": "Q2 and Q5 need deeper investigation"
}
```

## Verification

After gate passes, spawn independent verification agent to review:
- Question coverage, citation density, actionability, risk honesty
- Returns VERDICT: PASS / PARTIAL / FAIL
- FAIL → continue research; PASS → submit for human review

## Codex Compatibility

- Logic is agent-agnostic: file I/O + web tools
- Replace Claude subagent spawning with Codex native delegation
- Same `results.jsonl` format regardless of agent CLI
- See `openai.yaml` for Codex-specific policy
