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
| "I'm confident in these findings" | Confidence ≠ Evidence |
| "The search results confirmed it" | Show the URL and cited text |
| "I covered the main points" | Check question_answer_rate ≥ 0.9 |
| "Further research would be diminishing returns" | Only the quality gate decides that |

## Iterative Research Loop

### Round Structure

```text
Round N:
  1. CONTEXT  — Read results.jsonl + existing KB report to restore state
  2. PLAN     — Identify which sub-questions to tackle this round
  3. SEARCH   — WebSearch/WebFetch with dynamic filtering (multiple calls)
  4. SYNTHESIZE — Integrate findings into KB 04-research/ report
  5. VERIFY   — Run quality gate checklist (see below)
  6. LOG      — Append round summary to results.jsonl
  7. DECIDE   — Gate passed? → trigger verification subagent
                 Gate failed? → continue to Round N+1
                 Max iterations reached? → mark gaps + trigger verification
```

### Round 1 Bootstrap

On the first round, create the state files:

```text
Mercury_KB/04-research/RESEARCH-{TOPIC}-{ISSUE_NUM}.md  — the report
Mercury_KB/04-research/.research-state/results-{ISSUE_NUM}.jsonl — iteration log
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
2. Read `results.jsonl` to understand iteration history
3. Identify the lowest-scoring dimensions from the last round
4. Focus the current round on those gaps

This eliminates dependency on conversation context window for continuity.

## Quality Gate

### Mandatory Metrics (must all pass)

| Metric | Threshold | Measurement |
|--------|-----------|-------------|
| `question_answer_rate` | ≥ 0.9 | Count of answered questions / total questions |
| `citation_density` | ≥ 0.75 | Claims with source URL / total factual claims |
| `unverified_rate` | ≤ 0.1 | UNVERIFIED tags / total factual claims |

### Recommended Metrics (informational)

| Metric | Target | Measurement |
|--------|--------|-------------|
| `iteration_depth` | ≥ 4 | Number of completed rounds |
| `source_diversity` | ≥ 4 | Unique domains cited |

### Gate Evaluation

After each round, evaluate mechanically:
1. Count research questions in TaskBundle or dispatch prompt
2. Count which have substantive answers (not just "mentioned")
3. Count factual claims, source URLs, and UNVERIFIED markers
4. Compare against thresholds
5. Record result in `results.jsonl`

No LLM self-evaluation for mandatory metrics — they are mechanically countable.

## Verification Subagent

When the quality gate passes (or max iterations reached), spawn a verification subagent:

```text
Agent(
  description: "Verify research quality",
  prompt: """
    You are a Research Quality Verification Agent.
    Read the report at [KB path].
    Read the original research questions from [source].
    Read results.jsonl for iteration history.

    Evaluate:
    1. Question Coverage (1-5): Are all research questions answered?
    2. Citation Density (1-5): Do claims cite sources?
    3. Actionability (1-5): Can the findings be implemented?
    4. Risk Honesty (1-5): Are limitations clearly stated?

    Return: VERDICT (PASS/PARTIAL/FAIL) + dimension scores + gaps list.
    Do NOT modify any files.
  """
)
```

**Rules for verification subagent**:
- Cannot modify the research report
- Cannot access the research agent's internal reasoning
- FAIL verdict → research agent addresses gaps in next round
- PASS verdict → report submitted for human review

## Termination Conditions

| Condition | Action |
|-----------|--------|
| Quality gate passed + verification PASS | Submit for human review |
| Quality gate passed + verification PARTIAL | Address gaps, re-verify |
| Quality gate passed + verification FAIL | Continue research rounds |
| Max iterations reached (default: 10) | Mark incomplete items + submit |
| Human interruption | Save state + submit current progress |

## State Externalization

All research state lives in files, not in conversation memory:
- **Report**: `Mercury_KB/04-research/RESEARCH-*.md`
- **Iteration log**: `Mercury_KB/04-research/.research-state/results-*.jsonl`
- **KB references**: Any supporting docs read during research

This means:
- A new session can pick up where a previous one left off
- Context window exhaustion doesn't lose progress
- Multiple agents can read the same state

## Integration with Mercury SoT

| SoT Transition | Gate | Implementation |
|----------------|------|----------------|
| `drafted → dispatched` | Research scope classification | Orchestrator sets `researchScope` in TaskBundle |
| `dispatched → in_progress` | Skill auto-activation | SessionStart or dispatch prompt injects this skill |
| `in_progress` (internal) | Per-round quality gate | This skill's iterative loop |
| `in_progress → implementation_done` | Evidence requirement | Receipt must include verification VERDICT |

## Max Plan Usage

For deep research routed through the Anthropic API (Agent SDK path):
- Use `web_search_20260209` with `code_execution` for dynamic filtering
- Set `max_uses: 20` per API request for deep research tasks
- Use `pause_turn` handling for long-running research
- Enable prompt caching for multi-turn research conversations
- Budget: ~$6/task (Opus 4.6) or ~$3.6/task (Sonnet 4.6)

## Codex Compatibility

When running under Codex (via MCP adapter):
- This skill's logic is agent-agnostic — it uses standard file I/O and web tools
- Codex agents follow the same iterative loop and quality gate
- Replace `Agent()` subagent spawning with Codex's native delegation mechanism
- `results.jsonl` format is the same regardless of which agent CLI executes
- `openai.yaml` policy: `allow_implicit_invocation: true` (reference/guardrail skill)
