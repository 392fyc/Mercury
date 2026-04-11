---
name: autoresearch
description: |
  Autonomous iterative research protocol with mechanical quality gates. Multi-round search loops with per-round verification -- the agent does NOT decide when to stop, only the gate does. Works standalone or under Mercury dispatch. Triggers: "autoresearch", "自动研究", "深度调研", "deep research", "comprehensive research", "多轮调研".
user-invocable: true
allowed-tools: WebSearch, WebFetch, Read, Write, Grep, Glob, Agent, Bash
---

# Autoresearch Protocol

## Purpose

Autonomous iterative research for comprehensive investigations. Inspired by Karpathy autoresearch philosophy: **the agent does NOT decide when research is complete -- only the mechanical quality gate does**. This is slightly more relaxed than the original NEVER STOP directive: the loop terminates when all gate metrics pass, but the agent may never self-declare completion or skip the gate.

## When This Applies

- `researchScope === "deep"` (Mercury dispatch)
- Research questions >= 3
- Cross-verification across >= 3 independent sources required
- Architectural decision analysis (comparing alternatives)
- User invokes `/autoresearch` or says "自动研究" / "深度调研"

For lighter research (1-2 questions, single-source verification), use the `web-research` skill instead.

## Iron Rules

1. **NO COMPLETION CLAIMS WITHOUT FRESH VERIFICATION EVIDENCE** -- confidence is not evidence.
2. **Every factual claim requires a source URL or an explicit UNVERIFIED tag** -- no exceptions.
3. **Agent self-reports are not evidence** -- use independent verification (subagent or checklist).
4. **"Should work" / "probably" / "I believe" are banned** -- use "verified at [URL]" or "UNVERIFIED".
5. **Never paste raw WebSearch/WebFetch output into the report or conversation** -- extract claim + URL + 1-sentence evidence only. Raw search dumps bloat context and have caused session stops (Issue #215). Use the Search Worker Protocol (below) to keep raw results out of the autoresearch agent's own context.

## Rationalization Prevention

| Excuse | Reality |
|--------|---------|
| "Should be done now" | Run the quality gate |
| "I'm confident in these findings" | Confidence != Evidence |
| "The search results confirmed it" | Show the URL and cited text |
| "I covered the main points" | Check question_answer_rate >= 0.9 |
| "Further research would be diminishing returns" | Only the quality gate decides that |

## Invocation & Bootstrap

### Argument Parsing

```text
/autoresearch <topic>
```

Optional directives (append to topic or set in dispatch prompt):
- `MAX_ROUNDS: N` -- hard cap on iterations (default: 10)
- `QUESTIONS: Q1; Q2; Q3` -- explicit research questions (otherwise auto-generated)

### Environment Detection (do this FIRST)

1. Check if `Mercury_KB/04-research/` exists in the workspace:
   - **YES -> Mercury mode**
     - Report: `Mercury_KB/04-research/RESEARCH-{TOPIC}-{ID}.md`
     - State: `Mercury_KB/04-research/.research-state/`
     - If a TaskBundle is in the dispatch prompt, read task metadata from it
     - RESULTS_FILE: `results-{ISSUE_NUM}.jsonl` (issue number from TaskBundle)
   - **NO -> Standalone mode**
     - Report: `.research/reports/RESEARCH-{TOPIC}-{DATE}.md`
     - State: `.research/state/`
     - RESULTS_FILE: `results.jsonl` (no issue number in standalone mode)
     - Create directories using Bash tool: `mkdir -p .research/reports .research/state`
       (Claude Code runs in bash shell on all platforms including Windows)

### Research Manifest

On Round 1, create `research-manifest.json` in the state directory:

```json
{
  "topic": "Your research topic",
  "questions": ["Q1: ...", "Q2: ...", "Q3: ..."],
  "max_rounds": 10,
  "started_at": "2026-04-05T12:00:00Z",
  "mode": "standalone"
}
```

If no `QUESTIONS` directive was provided, decompose the topic into 3-7 focused research sub-questions before starting.

Create the initial report file with the topic as H1 and questions as an H2 checklist.

## Research Loop

**You are in a loop. DO NOT declare completion. DO NOT summarize prematurely.**
**Only the mechanical quality gate (Step 5) can end this loop.**
**You may NOT judge "good enough" -- the gate decides.**

```text
Round N:
  1. RESTORE  -- Read research-manifest.json + {RESULTS_FILE} + report
                (for reports > 200 lines, read only the section for the current question)
  2. PLAN     -- Pick 1-3 unanswered or weakest questions for this round
  3. SEARCH   -- Dispatch one worker sub-agent per selected question via
                Agent() (see "Search Worker Protocol" below). Worker does
                WebSearch + WebFetch (minimum 3 searches, different angles)
                and returns a compressed summary under 500 tokens. If Agent()
                is unavailable (nested subagent / Codex mode), call WebSearch
                directly but extract only claim + URL + 1-sentence evidence;
                never leave raw search result text in conversation context.
  4. WRITE    -- Update report with findings, cite every claim with [URL]
                or mark UNVERIFIED. Document contradictions between sources.
  5. GATE     -- Run mechanical quality gate (see below)
  6. LOG      -- Append round JSON to {RESULTS_FILE}
  7. BRANCH   -- ALL gate metrics PASSED -> go to VERIFICATION
                ANY metric FAILED -> go to Round N+1
                Round N = max_rounds -> go to VERIFICATION with gaps flagged
```

## Search Worker Protocol

**Why**: WebSearch/WebFetch return 1-3K tokens per call. A typical research round runs 9-15 searches, injecting 15-45K tokens of raw HTML/snippet text into the autoresearch agent's own context window. Over 4+ rounds this causes context pressure and has triggered session stops (Issue #215, #101 Gap 4).

**Fix**: isolate search I/O inside a worker sub-agent whose only job is to search and return a compressed summary. Raw search output lives and dies inside the worker's isolated context; only the summary flows back to autoresearch.

**Dispatch pattern** (run once per question per round when `Agent()` is available):

```text
Agent(
  description: "autoresearch worker Q{n} round {r}",
  subagent_type: "general-purpose",
  prompt: |
    You are a search worker for autoresearch.
    Round: {r}
    Question: {full question text}
    Prior findings (if any): {one-line recap, max 100 tokens}

    Your ONLY job: perform 3-5 WebSearch/WebFetch calls using varied query
    angles and return a compressed summary.

    MANDATORY output format -- under 500 tokens total, nothing else:

    ## Findings for Q{n}
    - Claim 1: <one sentence> [source URL]
    - Claim 2: <one sentence> [source URL]
    - Claim 3: <one sentence> [source URL]
    - Contradiction (if any): <one sentence> [URL A] vs [URL B]
    - Unanswered aspect (if any): <one sentence>

    HARD RULES:
    - DO NOT paste raw search result snippets, titles, or metadata beyond the URL
    - DO NOT narrate your search process ("I searched for...", "I found...")
    - Every claim needs a source URL or an UNVERIFIED tag
    - Count your output tokens; if over 500, cut the weakest claims
    - If fewer than 3 substantive findings exist, return what you have and note the gap
)
```

The autoresearch agent ingests only the <500 token summary per worker call. Over 4 rounds × 3 questions × 500 tokens = 6K tokens of search state, versus 60-180K tokens under the old inline pattern.

**Fallback** (nested subagent mode, Codex mode, or `Agent()` not available): call WebSearch directly but immediately extract claim + URL + 1-sentence evidence into the report. Do NOT reference the raw search results again in later turns. Treat the raw output as write-once, read-once ephemeral data.

## Quality Gate -- Mechanical Counting

After updating the report, evaluate by **counting** (not self-assessment):

### Step-by-step counting procedure

1. Read `research-manifest.json` -> count `total_questions`
2. Read the report file. For each question, check:
   - Has >= 2 sentences of substantive answer (not just "mentioned")
   - Has at least 1 source URL in that section
   - If both -> count as `answered`
3. Count all declarative factual statements -> `total_claims`
4. Count claims with `[URL]` or inline source reference -> `cited_claims`
5. Count literal `UNVERIFIED` markers -> `unverified_count`

### Compute and check

| Metric | Formula | Threshold |
|--------|---------|-----------|
| `question_answer_rate` | answered / total_questions | **>= 0.9** |
| `citation_density` | cited_claims / total_claims | **>= 0.75** |
| `unverified_rate` | unverified_count / total_claims | **<= 0.1** |
| `iteration_depth` | current round number | **>= 4** |

**ALL FOUR must pass.** If any fails, the gate FAILS. Continue to next round.

### Recommended metrics (informational, not blocking)

| Metric | Target |
|--------|--------|
| `source_diversity` | >= 4 unique domains cited |

## Results JSONL

Each round, append one JSON line to `{RESULTS_FILE}` (determined during environment detection):

```json
{
  "round": 1,
  "timestamp": "2026-04-05T12:30:00Z",
  "questions_targeted": ["Q1", "Q3"],
  "sources_found": 5,
  "sources_verified": 4,
  "question_answer_rate": 0.6,
  "citation_density": 0.75,
  "unverified_rate": 0.1,
  "iteration_depth": 1,
  "gate_passed": false,
  "notes": "Q2 and Q5 need deeper investigation"
}
```

On the final round, add: `termination_reason`, `verification_verdict`, `verification_score`.

## Context Recovery

If the session is new or resumed mid-research:
1. Read `research-manifest.json` for topic and questions
2. Read `{RESULTS_FILE}` -- find the last round metrics
3. Identify the lowest-scoring dimensions
4. Focus the current round on those gaps

This eliminates dependency on conversation context window for continuity.

## Verification

When the gate passes (or max rounds reached), run verification:

### Step A: Mechanical Checklist (MANDATORY -- always runs)

Re-read the final report. For each research question, confirm:

- [ ] Question has a dedicated section in the report
- [ ] Section contains >= 2 unique source URLs from different domains
- [ ] No `UNVERIFIED` claims remain without justification for why verification was impossible
- [ ] Contradictions between sources are documented (not suppressed)

Write the checklist results to `verification-{TOPIC}.md` in the state directory.

### Step B: Adversarial Review (OPTIONAL -- attempted if Agent() is available)

**IF you are the top-level agent** (not running inside another subagent):

Spawn a verification subagent:

```text
Agent(
  description: "Verify autoresearch report quality",
  prompt: [see below]
)

Verification prompt:
  You are a Research Quality Verification Agent. You are READ-ONLY.
  Read the report at [report path].
  Read research-manifest.json for the original questions.
  Read results.jsonl for iteration history.

  Evaluate on a 1-5 scale:
  1. Question Coverage -- Are all research questions substantively answered?
  2. Citation Density -- Do factual claims cite sources?
  3. Actionability -- Can the findings be acted upon?
  4. Risk Honesty -- Are limitations and uncertainties clearly stated?

  Weights: coverage=0.3, citation=0.25, actionability=0.25, risk_honesty=0.2
  Pass threshold: weighted average >= 4.0

  Return: VERDICT (PASS/PARTIAL/FAIL) + per-dimension scores + gaps list.
  Do NOT modify any files.
```

**IF Agent() is NOT available** (subagent context, Codex, or fork mode):
Skip Step B. Log `"verification_mode": "mechanical_only"` in results.jsonl.
Mechanical verification from Step A is sufficient for standalone operation.

## Termination & Output

| Condition | Action |
|-----------|--------|
| Gate passed + verification PASS | Complete -- print summary |
| Gate passed + verification PARTIAL | Address gaps, re-verify |
| Gate passed + verification FAIL | Continue research rounds |
| Max rounds reached | Flag incomplete items + print summary |
| Human interruption | Save state + print current progress |

### Final Output

When research terminates, print a summary to the conversation:

```text
## Autoresearch Complete

- **Topic**: ...
- **Rounds**: N
- **Gate Metrics**: question_answer_rate=X, citation_density=X, unverified_rate=X
- **Verification**: PASS/PARTIAL/FAIL (or mechanical_only)
- **Report**: [file path]
- **Gaps**: [list any remaining gaps, or "None"]
```

## State Externalization

All research state lives in files, not in conversation memory:

| File | Purpose |
|------|---------|
| `research-manifest.json` | Topic, questions, config |
| `{RESULTS_FILE}` | Per-round metrics log (`results.jsonl` standalone, `results-{ISSUE_NUM}.jsonl` Mercury) |
| `RESEARCH-{TOPIC}-*.md` | The research report |
| `verification-{TOPIC}.md` | Verification checklist results |

This means:
- A new session can pick up where a previous one left off
- Context window exhaustion does not lose progress
- Multiple agents can read the same state

## Mercury Integration

When running under Mercury orchestrator (auto-detected via `Mercury_KB/04-research/` existence):

- Report and state files use Mercury KB paths instead of `.research/`
- TaskBundle fields (`researchScope`, `readScope`, `definitionOfDone`) are read from the dispatch prompt
- Results JSONL uses issue number: `results-{ISSUE_NUM}.jsonl`
- Receipt JSON format follows Mercury SoT workflow
- On completion, output a JSON receipt for the orchestrator record_receipt flow

The skill auto-detects this. No manual configuration needed.
