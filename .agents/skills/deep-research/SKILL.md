---
name: deep-research
description: |
  Mercury's multi-round deep research protocol for comprehensive investigations requiring 3+ research questions, cross-source verification, or architectural decision analysis. Activates when TaskBundle.researchScope is "deep", when explicitly invoked, or when the research agent starts a session. Enforces iterative search loops with per-round quality gates and independent verification. Prevents premature completion via the "evidence over claims" iron rule. Triggers on: "深度调研", "深度研究", "deep research", "comprehensive research", "多轮调研", "research protocol", "调研协议".
---

# Mercury Deep Research Protocol

## Purpose

This skill governs large-scale research tasks that require multiple rounds of web search, cross-source verification, and structured synthesis. It prevents the most common agent failure mode: declaring research complete based on confidence rather than evidence.

## When This Protocol Applies

- TaskBundle `researchScope` is `"deep"`
- research questions >= 3
- cross-verification across >= 3 independent sources required
- architectural decision analysis
- any research task explicitly routed to the Research Agent
- user invokes `/deep-research` or says `深度调研`

For lighter research, use the `web-research` skill instead.

## Iron Rules

1. No completion claims without fresh verification evidence.
2. Every factual claim requires a source URL or an explicit `UNVERIFIED` tag.
3. Agent self-reports are not evidence.
4. `Should work`, `probably`, and `I believe` are banned.

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| `Should be done now` | Run the quality gate |
| `I'm confident in these findings` | Confidence does not equal evidence |
| `The search results confirmed it` | Show the URL and cited text |
| `I covered the main points` | Check `question_answer_rate >= 0.8` |
| `Further research would be diminishing returns` | Only the quality gate decides that |

## Iterative Research Loop

### Round Structure

```text
Round N:
  1. CONTEXT    - Read results.jsonl + existing KB report to restore state
  2. PLAN       - Identify which sub-questions to tackle this round
  3. SEARCH     - WebSearch/WebFetch with multiple calls
  4. SYNTHESIZE - Integrate findings into KB 04-research report
  5. VERIFY     - Run quality gate checklist
  6. LOG        - Append round summary to results.jsonl
  7. DECIDE     - Gate passed? -> trigger verification
                  Gate failed? -> continue to Round N+1
                  Max iterations reached? -> mark gaps + trigger verification
```

### Round 1 Bootstrap

On the first round, create the state files:

```text
Mercury_KB/04-research/RESEARCH-{TOPIC}-{ISSUE_NUM}.md
Mercury_KB/04-research/.research-state/results-{ISSUE_NUM}.jsonl
```

Each line in `results.jsonl` is a JSON object:

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

### Context Recovery Between Rounds

If the session is new or resumed:
1. Read the existing report from KB
2. Read `results.jsonl`
3. Identify the weakest dimensions from the last round
4. Focus the current round on those gaps

## Quality Gate

### Mandatory Metrics

| Metric | Threshold | Measurement |
|--------|-----------|-------------|
| `question_answer_rate` | >= 0.8 | answered questions / total questions |
| `citation_density` | >= 0.6 | claims with source URL / total factual claims |
| `unverified_rate` | <= 0.2 | `UNVERIFIED` tags / total factual claims |

### Recommended Metrics

| Metric | Target | Measurement |
|--------|--------|-------------|
| `iteration_depth` | >= 3 | completed rounds |
| `source_diversity` | >= 3 | unique domains cited |

### Gate Evaluation

After each round:
1. count research questions
2. count which were substantively answered
3. count factual claims, URLs, and `UNVERIFIED` markers
4. compare against thresholds
5. record the result in `results.jsonl`

## Verification

When the quality gate passes, or max iterations are reached, run an independent verification pass.

Codex adaptation:
- replace Claude `Agent()` examples with native Codex delegation when available
- keep the verifier read-only
- the verifier should not rewrite the research report

Verifier checklist:
- question coverage
- citation density
- actionability
- risk honesty

Return `PASS`, `PARTIAL`, or `FAIL` plus dimension scores and gaps.

## Iteration Limit

If the task prompt contains a `MAX_ITERATIONS: N` directive, treat it as a hard upper bound that overrides the default gate config. Stop the loop when that round count is reached, mark outstanding gaps, then proceed to KB write and submission.

`MAX_ITERATIONS: 0` means no limit (default behaviour).

## Termination Conditions

| Condition | Action |
|-----------|--------|
| Quality gate passed + verification PASS | Submit for human review |
| Quality gate passed + verification PARTIAL | Address gaps, re-verify |
| Quality gate passed + verification FAIL | Continue research rounds |
| Max iterations reached (config or injected limit) | Mark incomplete items and submit |
| Human interruption | Save state and submit current progress |

## State Externalization

All research state lives in files:
- report: `Mercury_KB/04-research/RESEARCH-*.md`
- iteration log: `Mercury_KB/04-research/.research-state/results-*.jsonl`
- KB references: supporting docs read during research

## Integration with Mercury SoT

| SoT Transition | Gate | Implementation |
|----------------|------|----------------|
| `drafted -> dispatched` | Research scope classification | Orchestrator sets `researchScope` |
| `dispatched -> in_progress` | Skill activation | Session start or dispatch prompt injects this skill |
| `in_progress` | Per-round quality gate | This skill's iterative loop |
| `in_progress -> implementation_done` | Evidence requirement | Receipt must include the verification verdict |
