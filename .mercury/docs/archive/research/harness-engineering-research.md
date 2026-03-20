# Harness Engineering Research

> Research compiled 2026-03-16 for Mercury project (updated).
> All content below was fetched and read from live URLs. Nothing is from training data.

---

## 1. OpenAI - "Harness Engineering: Leveraging Codex in an Agent-First World"

- **URL:** https://openai.com/index/harness-engineering/
- **Status:** 403 Forbidden (could not fetch full article; details sourced from secondary coverage)
- **Key facts from coverage:** OpenAI's Codex team built a production app with 1M+ lines of code, zero manually typed. ~1,500 PRs merged over 5 months by 3 engineers (3.5 PRs/engineer/day). The harness comprises context engineering, architectural constraints (linters, structural tests), and "garbage collection" agents.

---

## 2. Philipp Schmid - "The Importance of Agent Harness in 2026"

- **URL:** https://www.philschmid.de/agent-harness-2026
- **Author:** Philipp Schmid
- **Date:** January 5, 2026

### Key Concepts

**Computer Architecture Analogy:**
- Model = CPU (processing power)
- Context Window = RAM (limited working memory)
- Agent Harness = Operating System (context management, standard protocols)
- Agent = Application (user-specific logic)

**Three Critical Functions of a Harness:**
1. Enable real-world validation of model performance against actual use cases
2. Standardize developer experience through proven best practices
3. Create feedback loops converting agent trajectories into training data

**Core Principles:**
- **Start Simple:** Provide atomic tools and let models plan autonomously with guardrails
- **Build to Delete:** Design modular architectures anticipating logic replacement as models evolve
- **Data as Competitive Advantage:** Captured trajectories matter more than prompts
- **Context Durability** is the critical bottleneck; harnesses must detect when models drift after many steps

**The Bitter Lesson Applied:** Manus refactored 5x in 6 months; LangChain re-architected 3x/year; Vercel eliminated 80% of agent tooling. General computational methods outperform hand-coded solutions.

### Mercury Takeaways
- The harness IS the product; treat it as OS-level infrastructure
- Design for deletion -- every component should be replaceable
- Capture agent trajectories as first-class data

---

## 3. LangChain - "Improving Deep Agents with Harness Engineering"

- **URL:** https://blog.langchain.com/improving-deep-agents-with-harness-engineering/
- **Author:** LangChain team
- **Date:** February 17, 2026

### Key Achievement
Improved deepagents-cli from 52.8% to 66.5% on Terminal Bench 2.0 (13.7-point gain) with ZERO model changes -- only harness optimization.

### Specific Techniques

1. **Self-Verification Loop:** Four-stage approach (plan, build, verify, fix). `PreCompletionChecklistMiddleware` intercepts agents before exit to enforce verification.

2. **Context Engineering:**
   - `LocalContextMiddleware` maps directories and discovers tools at startup
   - Agents receive environment constraints (timeouts, testing requirements)
   - Explicit guidance on writing testable code

3. **Doom Loop Prevention:** `LoopDetectionMiddleware` tracks file edits and suggests reconsidering after repeated modifications.

4. **Reasoning Compute Allocation:** "Reasoning sandwich" -- xhigh reasoning for planning, high for execution, xhigh for verification.

5. **Automated Trace Analysis:** Trace Analyzer Skill fetches execution traces from LangSmith, spawns parallel analysis agents, synthesizes improvement recommendations.

### Design Patterns
- Proactive context assembly reduces agent search errors
- Aggressive verification prompting counteracts solution bias
- Traces are feedback signals for continuous debugging
- Short-term guardrails address current model limitations
- Harnesses require model-specific customization

### Mercury Takeaways
- Middleware-based architecture for harness concerns (loop detection, verification, context injection)
- Invest in trace analysis tooling -- automated failure mode identification
- The "reasoning sandwich" pattern for compute allocation across agent phases

---

## 4. Latent.Space (swyx) - "Is Harness Engineering Real?"

- **URL:** https://www.latent.space/p/ainews-is-harness-engineering-real
- **Author:** swyx (Latent.Space)
- **Date:** March 5, 2026

### The Debate: Big Model vs. Big Harness

**Arguments AGAINST harness value:**
- Anthropic's Claude Code team: "All the secret sauce is in the model" with "the thinnest possible wrapper"
- Noam Brown: reasoning models replaced previous "complex behavior" entirely; future models will render harnesses obsolete
- Scale AI's SWE-Atlas: harness choice creates "noise within the margin of error"

**Arguments FOR harness value:**
- Every deployed agent converges on the same core loop regardless of model
- Jerry Liu: "The Model Harness is Everything -- the biggest barrier to getting value from AI is your own ability to context and workflow engineer"
- An afternoon of harness optimization improved 15 different LLMs
- Cursor's $50B valuation is largely a harness engineering achievement

### Resolution
Reflects incentive misalignment -- model creators emphasize models, framework builders emphasize harnesses. Both have demonstrable value. The practical truth: harness engineering has real value AND models keep absorbing harness capabilities over time.

### Mercury Takeaways
- Design Mercury's harness to be thin but extensible -- don't over-engineer what models will absorb
- Focus harness effort on what models genuinely cannot do: durability, state management, tool orchestration, human-in-the-loop
- Track which harness features become unnecessary as models improve

---

## 5. Anthropic - "Demystifying Evals for AI Agents"

- **URL:** https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents
- **Authors:** Mikaela Grace, Jeremy Hadfield, Rodrigo Olivares, Jiri De Jonghe
- **Date:** January 9, 2026

### Definition

An eval is "a test for an AI system: give an AI an input, then apply grading logic to its output to measure success." The article focuses on automated evals that run during development without real users. Evals make problems visible before they affect users, and their value compounds over the agent lifecycle.

### Evaluation Types

- **Single-turn evals:** Traditional prompt-response-grading pattern
- **Multi-turn evals:** Agents execute multiple steps across turns
- **Agent evals:** Greatest complexity -- agents use tools across many turns, modifying environmental state, creating error propagation risk

### Key Terminology

- **Evaluation harness:** End-to-end infrastructure managing task execution and result aggregation
- **Agent harness/Scaffold:** System enabling model-as-agent functionality
- **Transcript/Trace/Trajectory:** Complete record of outputs, tool calls, reasoning, and interactions
- **pass@k:** Probability of at least one correct solution across k attempts
- **pass^k:** Probability that ALL k trials succeed -- essential for customer-facing reliability

### Three Grader Types

1. **Code-based graders:** String matching, binary tests, static analysis. Fast and reproducible but brittle.
2. **Model-based graders:** Rubric scoring, natural language assertions. Handle nuance but require calibration.
3. **Human graders:** Gold-standard quality but expensive and slow.

### Capability vs. Regression Evals

- **Capability evals** identify what agents struggle with (start at low pass rates)
- **Regression evals** protect against backsliding (maintain near-100% pass rates)
- Mature capability evals "graduate" to regression suites

### Agent-Type-Specific Evaluation Techniques

- **Coding agents:** Deterministic graders (unit tests + code quality rubrics). Benchmarks: SWE-bench Verified, Terminal-Bench.
- **Conversational agents:** Multidimensional metrics combining state verification, transcript constraints, LLM rubrics. Require user persona simulation.
- **Research agents:** Combine groundedness checks, coverage verification, source quality assessment. LLM rubrics need frequent expert calibration.
- **Computer use agents:** Real or sandboxed environments, screenshot/click interaction. Verify both UI state and backend outcomes.

### Implementation Roadmap

1. **Phase 1 - Task Collection:** Start with 20-50 simple tasks from real failures. Require unambiguous specs with reference solutions.
2. **Phase 2 - Harness & Grader Design:** Build stable, isolated environments. Favor outcome assessment over step verification to avoid penalizing valid alternative approaches. Implement partial credit.
3. **Phase 3 - Maintenance:** Read transcripts regularly. Monitor for eval saturation (100% pass = no signal). Establish dedicated ownership.

### Complementary Methods

Automated evals + production monitoring + A/B testing + user feedback + manual transcript review + systematic human studies.

### Key Insight

"Frontier models can also find creative solutions that surpass the limits of static evals" -- exemplified by Opus 4.5 discovering policy loopholes in tau-Bench. High eval pass rates alone don't guarantee capability advancement.

### Mercury Takeaways
- Start eval-driven development early -- define planned capabilities before agents fulfill them
- Favor outcome-based grading over step-sequence checking (don't penalize creative solutions)
- Build evaluation harness infrastructure as a first-class system component
- Use pass^k (all-must-pass) metric for reliability-critical agent paths
- Combine code-based + model-based graders matching task complexity
- Plan for capability eval -> regression eval graduation pipeline

---

## 6. Exponential View - "Entering the Trillion-Agent Economy"

- **URL:** https://www.exponentialview.co/p/entering-the-trillion-agent-economy
- **Authors:** Azeem Azhar, Rohit Krishnan, Chantal Smith
- **Date:** February 20, 2026

### Key Concepts

**Scale:** The LLM API market processes ~50 trillion tokens/day (1.5 quadrillion/month). Individual power users consume 100M tokens/day.

**Agent Behavioral Traits ("Homo Agenticus"):**
- Reluctance to spend money
- Preference for building over buying
- Resistance to transactions
- These become structural economic features at trillion-agent scale

**Three Economic Invariants for Agent Systems:**
1. **Medium of exchange** (price signals)
2. **Identity** (knowing counterparties)
3. **Verifiability** (transaction records)

**Infrastructure Requirements:**
- VPS ($7-15/month) for isolated agent operation
- Email security against "context poisoning"
- Memory management for simultaneous sub-agents
- Persistent context and tool-use frameworks

**Scaling projection:** Personal agents go from 20 today to 200 within years as costs drop from thousands to dollars/day by 2028.

### Mercury Takeaways
- Design for agent-to-agent communication, not just human-to-agent
- API-first everything -- "if you don't have an API for a feature, it might as well not exist"
- Identity and verifiability are infrastructure requirements, not nice-to-haves
- Plan for 10-100x agent count scaling per user

---

## 7. Harrison Chase (LangChain) - "How Coding Agents Are Reshaping Engineering, Product and Design"

- **URL:** https://blog.langchain.com/how-coding-agents-are-reshaping-engineering-product-and-design/
- **Author:** Harrison Chase
- **Date:** March 10, 2026

### Key Transformations

**Process shift:** PRD-to-mock-to-code waterfall dissolves. Teams prototype functional software rapidly, then focus on review/refinement rather than creation.

**Bottleneck shift:** From implementation to review quality. Anyone can write code; not everyone produces well-architected solutions.

**Two Emerging Archetypes:**
1. **Builders:** Product intuition + agent proficiency + design sensibility; can move features concept-to-production independently
2. **Reviewers:** Systems thinking + fast-paced critique; ensure architectural, product, and design quality

**Generalists gain leverage:** One person doing product+design+engineering moves faster than a team of three due to communication overhead elimination.

**Documentation evolves:** Traditional PRDs become obsolete; replaced by "structured, versioned prompts."

### Mercury Takeaways
- Mercury should support the "builder" archetype -- single person orchestrating multiple agents
- Review tooling is as important as generation tooling
- Structured prompt management (versioned, templated) is a core infrastructure need

---

## 8. Cursor - Agent Architecture and Best Practices

### 8a. ByteByteGo - "How Cursor Shipped Its Coding Agent to Production"

- **URL:** https://blog.bytebytego.com/p/how-cursor-shipped-its-coding-agent
- **Author:** ByteByteGo (with Lee Robinson at Cursor)
- **Date:** January 26, 2026

**Five Core Components:**
1. **Router:** Dynamically selects optimal model per request complexity
2. **LLM:** Agentic coding model trained on edit trajectories
3. **Tools:** 10+ tools for search, read/write, edit, terminal
4. **Context Retrieval:** Pulls relevant code without exceeding token limits
5. **Orchestrator:** Controls iterative think-act loop
6. **Sandbox:** Isolated execution with strict guardrails

**Three Production Challenges:**

1. **The Diff Problem:** Models hallucinate line numbers, drift on indentation. Solution: train on edit trajectories + oversampled search-and-replace in training data.

2. **Latency Compounding:** In agent loops, latency multiplies. Solutions:
   - Mixture of Experts (MoE) for selective token routing
   - Speculative decoding with small draft models
   - Context compaction (summarize working state, drop logs, deduplicate)

3. **Sandboxing at Scale:** Provisioning time can outlast inference. Solutions:
   - Rebuilt VM schedulers for bursty demand
   - Sandboxes as core serving infrastructure with fast provisioning + aggressive recycling
   - Restricted sandbox mode: no network, filesystem limited to workspace + /tmp

**Key Insight:** "Production coding agents are systems engineering challenges disguised as model problems."

### 8b. Cursor Blog - "Best Practices for Coding with Agents"

- **URL:** https://cursor.com/blog/agent-best-practices
- **Author:** Lee Robinson
- **Date:** January 9, 2026

**Orchestration Patterns:**
- **TDD with agents:** Write tests first, confirm failure, implement iteratively until tests pass
- **Parallel execution:** Multiple models on worktrees simultaneously; compare and select
- **Cloud agents:** Delegate background tasks to remote sandboxes asynchronously
- **Debug mode:** Generate hypotheses, instrument logging, analyze runtime data

**Customization layers:**
- **Rules:** Static context in `.cursor/rules/` for persistent instructions
- **Skills:** Dynamic capabilities in `SKILL.md` files

### Mercury Takeaways
- Router pattern for model selection is essential at scale
- Context compaction is a critical infrastructure concern
- Sandbox provisioning is a bottleneck -- treat as core serving infrastructure
- Support parallel agent execution on isolated worktrees
- Rules + Skills pattern for customizing agent behavior

---

## 9. Langfuse - "AI Agent Observability, Tracing & Evaluation"

- **URL:** https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse
- **Author:** Jannik Maierhofer
- **Date:** March 16, 2025 (updated February 20, 2026)

### Five-Part Agent Design
1. Coordinator (LLM with general capabilities)
2. Planning module (task decomposition)
3. Action module (tool utilization)
4. Memory module (interaction storage)
5. Profile module (behavior specification)

### Three Evaluation Strategies
- **Black-Box:** Input-to-output only
- **Glass-Box:** Full trajectory of tool calls and reasoning
- **White-Box:** Granular step-level assessment

### Three-Phase Evaluation Progression
1. Manual trace inspection during development
2. Online evaluation with user feedback + LLM-as-Judge
3. Offline evaluation using benchmark datasets pre-release

### Industry Trend
Convergence on **OpenTelemetry (OTEL)** as standard for agent telemetry. Frameworks (Pydantic AI, smolagents, Strands Agents) now emit traces via OTEL natively.

### Mercury Takeaways
- Build on OpenTelemetry from day one -- it's the emerging standard
- Three-phase evaluation progression is a practical roadmap
- Glass-box tracing (full trajectory) is essential for debugging multi-step agents
- Langfuse integration should be considered for observability layer

---

## 10. Inngest - "Your Agent Needs a Harness, Not a Framework"

- **URL:** https://www.inngest.com/blog/your-agent-needs-a-harness-not-a-framework
- **Author:** Dan Farrelly
- **Date:** March 3, 2026

### Core Argument
Agents need durable, event-driven infrastructure (retry logic, state persistence, job queues, event routing) -- not application frameworks that reinvent these capabilities.

### Utah Architecture (Universally Triggered Agent Harness)
- Think-act-observe loop where each LLM call and tool execution is an independently retryable step
- Decoupled triggers (webhooks, crons, sub-agent invocations) from execution logic
- Step-level observability and traces
- Singleton concurrency (one agent run per chat, cancel-on-new-message)

### Practical Patterns
- **Sub-agent delegation:** `step.invoke()` spawns isolated agent runs with separate context windows
- **Context pruning:** Two-tier (soft-trim head/tail, hard-clear beyond thresholds) + session compaction + budget warnings
- **Multi-provider:** Provider-agnostic abstractions enable switching between Anthropic/OpenAI/Google via config

### Mercury Takeaways
- Step-level atomicity provides natural durability boundaries
- Decoupling triggers from logic enables universal activation patterns
- Context management (pruning, compaction, budgets) is a first-class infrastructure concern
- Singleton concurrency patterns prevent message collisions

---

## 11. Birgitta Bockeler (Thoughtworks/Martin Fowler) - "Harness Engineering"

- **URL:** https://martinfowler.com/articles/exploring-gen-ai/harness-engineering.html
- **Author:** Birgitta Bockeler
- **Date:** February 17, 2026

### OpenAI's Harness Components
1. **Context Engineering:** Knowledge bases enhanced continuously with dynamic observability data
2. **Architectural Constraints:** Deterministic linters + structural tests + LLM-based monitoring
3. **Garbage Collection:** Periodic agents detecting documentation inconsistencies and constraint violations

### Key Insights
- When agents struggle, identify missing tools/documentation; use AI to implement fixes (meta-harness pattern)
- Constraint-driven design: constrain solution spaces rather than enabling unlimited generation
- Fewer tech stacks and standardized topologies may emerge as orgs prioritize "AI-friendliness"
- Gap identified: "What I am missing is verification of functionality and behaviour"

### Mercury Takeaways
- "Garbage collection" agents are a novel pattern -- periodic cleanup and consistency checking
- Constraint-driven design > unlimited flexibility
- Verification of behavior remains an unsolved gap worth focusing on

---

## Cross-Cutting Themes for Mercury

### 1. The Harness IS the Product
The competitive moat is not the model -- it's the infrastructure around it. Every article converges on this point.

### 2. Middleware Architecture
LangChain's middleware pattern (LoopDetectionMiddleware, PreCompletionChecklistMiddleware, LocalContextMiddleware) is the most practical pattern for implementing harness concerns as composable, pluggable components.

### 3. Context Management is the #1 Technical Challenge
Every source identifies context durability, compaction, and drift as the critical bottleneck. Solutions include:
- State offloading to storage
- Task isolation via sub-agents
- Two-tier pruning (soft/hard)
- Context compaction (summarize, drop logs, deduplicate)

### 4. Observability via OpenTelemetry
OTEL is the emerging standard. Build on it from day one. Glass-box tracing (full agent trajectory) is essential.

### 5. Design for Deletion
Models keep absorbing harness capabilities. Every harness component should be modular and replaceable. "Build to delete."

### 6. Eval-Driven Development
Anthropic's eval framework provides the measurement backbone for harness engineering. Key patterns: outcome-based grading (not step-sequence), pass^k for reliability, capability-to-regression eval graduation, and combined code+model+human graders. Evals should be built as first-class infrastructure alongside the harness itself.

### 7. Verification Gap
Multiple sources identify verification of agent behavior as unsolved. This is a high-value area for Mercury to differentiate. Anthropic's eval taxonomy (coding, conversational, research, computer-use agents) provides a starting framework.

### 8. Agent-to-Agent Communication
The trillion-agent economy requires API-first design, identity, verifiability, and price signals between agents.

### 9. Sandbox Infrastructure
Cursor's experience shows sandbox provisioning is a production bottleneck. Treat it as core serving infrastructure with fast provisioning and aggressive recycling.
