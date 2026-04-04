---
name: autoresearch
description: |
  Autonomous iterative research protocol with mechanical quality gates. Multi-round search loops with per-round verification -- the agent does NOT decide when to stop, only the gate does. Works standalone or under Mercury dispatch. Triggers: "autoresearch", "自动研究", "深度调研", "deep research", "comprehensive research", "多轮调研".
---

# Autoresearch Protocol

## Purpose

Autonomous iterative research for comprehensive investigations. The agent does NOT decide when research is complete -- only the mechanical quality gate does. The loop terminates when all gate metrics pass, but the agent may never self-declare completion or skip the gate.

## When This Applies

- researchScope is deep (Mercury dispatch)
- Research questions >= 3
- Cross-verification across >= 3 independent sources
- Architectural decision analysis
- User invokes /autoresearch or says 自动研究 / 深度调研

For lighter research, use the web-research skill instead.

## Iron Rules

1. No completion claims without fresh verification evidence.
2. Every factual claim requires a source URL or an explicit UNVERIFIED tag.
3. Agent self-reports are not evidence.
4. "Should work", "probably", and "I believe" are banned.

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| Should be done now | Run the quality gate |
| I am confident in these findings | Confidence does not equal evidence |
| The search results confirmed it | Show the URL and cited text |
| I covered the main points | Check question_answer_rate >= 0.9 |
| Further research would be diminishing returns | Only the quality gate decides that |

## Invocation & Bootstrap

/autoresearch <topic>

Optional directives:
- MAX_ROUNDS: N -- hard cap on iterations (default: 10)
- QUESTIONS: Q1; Q2; Q3 -- explicit research questions

### Environment Detection (do this FIRST)

1. If Mercury_KB/04-research/ exists -> Mercury mode (use KB paths, RESULTS_FILE: results-{ISSUE_NUM}.jsonl)
2. Otherwise -> Standalone mode (use .research/reports/ and .research/state/, RESULTS_FILE: results.jsonl)

### Research Manifest

On Round 1, create research-manifest.json in the state directory with topic, questions, max_rounds, started_at, mode. If no QUESTIONS provided, decompose topic into 3-7 sub-questions.

## Research Loop

You are in a loop. DO NOT declare completion. DO NOT summarize prematurely.
Only the mechanical quality gate can end this loop.

Round N:
  1. RESTORE  -- Read manifest + {RESULTS_FILE} + report
  2. PLAN     -- Pick 1-3 unanswered or weakest questions
  3. SEARCH   -- WebSearch + WebFetch, min 3 searches per question
  4. WRITE    -- Update report with findings, cite every claim with [URL]
  5. GATE     -- Run mechanical quality gate
  6. LOG      -- Append round JSON to {RESULTS_FILE}
  7. BRANCH   -- ALL PASSED -> VERIFICATION; ANY FAILED -> Round N+1

## Quality Gate -- Mechanical Counting

Count (not self-assess):
1. total_questions from manifest
2. answered questions (>= 2 sentences + >= 1 URL each)
3. total_claims (declarative factual statements)
4. cited_claims (with [URL])
5. UNVERIFIED markers

| Metric | Threshold |
|--------|-----------|
| question_answer_rate | >= 0.9 |
| citation_density | >= 0.75 |
| unverified_rate | <= 0.1 |
| iteration_depth | >= 4 |

ALL FOUR must pass. If any fails, continue to next round.

## Results JSONL

Each round append to {RESULTS_FILE}: round, timestamp, questions_targeted, sources_found, sources_verified, question_answer_rate, citation_density, unverified_rate, iteration_depth, gate_passed, notes.

Final round adds: termination_reason, verification_verdict, verification_score.

## Context Recovery

1. Read research-manifest.json
2. Read {RESULTS_FILE} -- find last round metrics
3. Identify weakest dimensions
4. Focus current round on those gaps

## Verification

### Step A: Mechanical Checklist (MANDATORY)

For each question confirm:
- Has dedicated section
- Section has >= 2 unique source URLs
- No unjustified UNVERIFIED claims
- Contradictions documented

Write to verification-{TOPIC}.md in state directory.

### Step B: Codex Delegation (OPTIONAL)

Use native Codex delegation if available to run read-only verification:
- question coverage, citation density, actionability, risk honesty (1-5 each)
- Pass threshold: weighted average >= 4.0
- If delegation unavailable, mechanical verification is sufficient.
- Log verification_mode: mechanical_only

## Termination & Output

| Condition | Action |
|-----------|--------|
| Gate passed + verification PASS | Complete -- print summary |
| Gate passed + verification PARTIAL | Address gaps, re-verify |
| Gate passed + verification FAIL | Continue research rounds |
| Max rounds reached | Flag gaps + print summary |
| Human interruption | Save state + print progress |

## State Externalization

Files: research-manifest.json, {RESULTS_FILE}, RESEARCH-{TOPIC}-*.md, verification-{TOPIC}.md

## Mercury Integration

Auto-detected via Mercury_KB/04-research/ existence:
- Uses Mercury KB paths instead of .research/
- TaskBundle fields read from dispatch prompt
- Results JSONL uses issue number
- Receipt JSON follows SoT workflow
