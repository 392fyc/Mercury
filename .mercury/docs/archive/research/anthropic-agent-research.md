# Anthropic Agent Architecture Research

> Research compiled for Mercury multi-agent orchestrator project.
> Sources: Anthropic engineering blog articles, fetched and analyzed 2026-03-16.
> Only contains information actually read from the source articles.

---

## Table of Contents

1. [Building Effective Agents](#1-building-effective-agents)
2. [How We Built Our Multi-Agent Research System](#2-how-we-built-our-multi-agent-research-system)
3. [Building Agents with the Claude Agent SDK](#3-building-agents-with-the-claude-agent-sdk)
4. [Effective Context Engineering for AI Agents](#4-effective-context-engineering-for-ai-agents)
5. [Effective Harnesses for Long-Running Agents](#5-effective-harnesses-for-long-running-agents)
6. [Cross-Cutting Themes and Mercury Recommendations](#6-cross-cutting-themes-and-mercury-recommendations)

---

## 1. Building Effective Agents

- **URL**: https://www.anthropic.com/research/building-effective-agents
- **Authors**: Erik Schluntz and Barry Zhang

### Core Philosophy

Anthropic's overarching recommendation: pursue **simple, composable patterns** rather than complex frameworks. "Success in the LLM space isn't about building the most sophisticated system. It's about building the right system for your needs."

### Architectural Taxonomy

Two categories of agentic systems:

- **Workflows**: LLMs and tools orchestrated through **predefined code paths**. Predictable, consistent, well-suited for well-defined tasks.
- **Agents**: LLMs **dynamically direct their own processes** and tool usage, maintaining control over how they accomplish tasks. Best for open-ended problems where the number of steps cannot be predicted.

### Decision Framework (When to Use What)

1. Start with optimized single LLM calls using retrieval and in-context examples
2. Add workflows only when predictability/consistency are required for well-defined tasks
3. Deploy agents only for open-ended problems where steps cannot be predicted in advance

Agentic systems "often trade latency and cost for better task performance" -- evaluate this tradeoff explicitly.

### Workflow Patterns

**Prompt Chaining**: Sequential LLM steps where each processes previous output. Includes programmatic checkpoints ("gates") to validate progress. Use cases: generation + translation, outline + validation + composition.

**Routing**: Classify inputs and direct to specialized processes. Enables "separation of concerns, and building more specialized prompts." Use cases: customer service categorization, model selection by complexity (simple -> Haiku, complex -> Sonnet).

**Parallelization** (two variants):
- **Sectioning**: Independent subtasks run in parallel. Examples: guardrails (separate instances for processing and content screening), multi-aspect evaluations.
- **Voting**: Same task run multiple times for diverse outputs. Examples: code vulnerability reviews, content moderation with threshold-based decisions.

**Orchestrator-Workers**: Central LLM dynamically breaks down tasks, delegates to worker LLMs, synthesizes results. Key distinction from parallelization: subtasks are **not pre-defined** but determined by the orchestrator based on specific input. Use cases: multi-file coding changes, information gathering from multiple sources.

**Evaluator-Optimizer**: One LLM generates, another evaluates/provides feedback in a loop. Most effective when responses can be demonstrably improved with articulated feedback. Use cases: literary translation, complex multi-cycle research.

### Agent Design Requirements

- Clear initial commands or interactive user discussion
- Independent planning and operation capability
- Environmental feedback at each step (tool results, code execution)
- Checkpoints for human feedback or blocker resolution
- Stopping conditions (maximum iterations, completion criteria)

### Tool Design Principles ("Agent-Computer Interface")

Anthropic emphasizes investing as much effort in agent-computer interfaces (ACI) as in human-computer interfaces (HCI).

**Tool format optimization:**
1. Provide enough tokens for the model to think before writing itself into a corner
2. Keep formats close to what the model has seen in internet text
3. Eliminate formatting overhead (no line counts, complex escaping)

**Tool documentation standards:**
- Clear parameter names and descriptions
- Example usage patterns
- Edge case documentation
- Explicit boundaries distinguishing tools
- "Poka-yoke" design (make mistakes harder)

The SWE-bench team spent more time optimizing tools than the overall prompt. Switching to absolute filepaths eliminated relative path mistakes.

### Framework Guidance

Frameworks "make it easy to get started" but "often create extra layers of abstraction that can obscure the underlying prompts and responses, making them harder to debug." Recommendation: start with LLM APIs directly; many patterns need only a few lines of code. If using frameworks, "ensure you understand the underlying code."

---

## 2. How We Built Our Multi-Agent Research System

- **URL**: https://www.anthropic.com/engineering/multi-agent-research-system

### Architecture Overview

Orchestrator-worker pattern: a lead agent coordinates specialized subagents operating in parallel.

**Lead Agent**: Analyzes queries, develops strategy, spawns subagents for parallel exploration. Saves research plan to memory before context truncation occurs (context windows can exceed 200K tokens).

**Subagents**: Receive detailed task descriptions (objectives, output formats, tool guidance, boundaries). Operate independently with own context windows. Conduct iterative web searches with interleaved thinking to evaluate results.

**CitationAgent**: Receives all findings, identifies specific source attributions, ensures all claims are properly referenced.

### Performance

- Multi-agent system (Opus 4 lead + Sonnet 4 subagents) outperformed single-agent Opus 4 by **90.2%** on internal research eval
- Three factors explain 95% of variance on BrowseComp: token usage (80%), tool calls, model choice
- Upgrading model quality (e.g., Sonnet 3.7 -> Sonnet 4) provides a **larger performance gain than doubling token budget**

### Token Economics

- Agents use ~4x more tokens than chat interactions
- Multi-agent systems use ~15x more tokens than chats
- System succeeds because it "helps spend enough tokens to solve the problem"

### Delegation Framework

Initially, simple instructions like "research the semiconductor shortage" caused duplicated work and misaligned searches. The solution:

- **Clear objectives** with specific output formats
- **Explicit tool and source guidance**
- **Defined task boundaries** preventing overlap between subagents
- **Scaling rules embedded in prompts**: simple fact-finding = 1 agent with 3-10 tool calls; complex research = 10+ subagents with "clearly divided responsibilities"

### Eight Prompt Engineering Strategies

1. **Simulation-Based Iteration**: Watch agents step-by-step in Anthropic Console to discover failure modes (continuing despite sufficient results, verbose queries, wrong tool selection).

2. **Delegation Teaching**: Orchestrators need detailed instructions on decomposing queries. Without specificity, "subagents misinterpreted the task or performed the exact same searches as other agents."

3. **Complexity Scaling**: Embed explicit guidelines to prevent overinvestment in simple queries.

4. **Tool Selection Discipline**: "Bad tool descriptions can send agents down completely wrong paths." When encountering MCP servers with varying quality descriptions, use explicit heuristics -- examine available tools first, match usage to intent, prefer specialized over generic.

5. **Self-Improvement**: Claude 4 models can prompt-engineer themselves. A tool-testing agent rewrites flawed descriptions based on dozens of test iterations, achieving **40% decrease in task completion time**.

6. **Search Strategy**: Mirror expert humans -- start with "short, broad queries, evaluate what's available, then progressively narrow focus."

7. **Extended Thinking Guidance**: Serves as controllable scratchpad. Lead agents use for planning, assessing tool fit, determining complexity, defining subagent roles. Subagents use interleaved thinking after tool results to evaluate quality and refine queries.

8. **Parallelization**: Spin up 3-5 subagents in parallel; have subagents use 3+ tools in parallel. These changes "cut research time by up to 90% for complex queries."

### Context and Memory Management

**Persistent Context Storage**: Lead researcher saves plans to external memory before truncation. Agents "retrieve stored context like the research plan from their memory rather than losing previous work."

**Long-Horizon Patterns**: For conversations spanning hundreds of turns, agents "summarize completed work phases and store essential information in external memory before proceeding to new tasks." Fresh subagents can be spawned with clean contexts while maintaining continuity through careful handoffs.

**Artifact Bypass Pattern**: Rather than routing all subagent outputs through the lead agent, specialized agents create outputs that persist independently. Subagents store work in external systems and pass lightweight references back, reducing token overhead and preventing information loss.

### Production Engineering

**Error Compounding**: "Minor system failures can be catastrophic for agents." Systems must "resume from where the agent was when the errors occurred" rather than restarting. Combine model intelligence with deterministic safeguards (retry logic, checkpoints).

**Observability**: Full production tracing for non-deterministic behavior. Monitor "agent decision patterns and interaction structures" without monitoring conversation contents (user privacy).

**Deployment**: "Rainbow deployments" gradually shift traffic between versions, preventing disruption to running agents mid-process.

**Synchronous Bottleneck**: Current architecture executes subagents synchronously (lead waits for completion). Simplifies coordination but prevents mid-process steering. Asynchronous execution would enable more parallelism but introduces coordination and error propagation challenges.

### Evaluation Methodology

- Start with ~20 queries representing real usage for early testing ("with effect sizes this large, you can spot changes with just a few test cases")
- LLM-as-judge evaluates against rubric: factual accuracy, citation accuracy, completeness, source quality, tool efficiency (0.0-1.0 scores)
- Human evaluation catches edge cases evals miss: hallucinations, system failures, source selection biases (agents consistently chose SEO-optimized content over authoritative academic sources)
- Multi-agent systems exhibit **emergent behaviors** -- small lead agent changes unpredictably affect subagent behavior

### Key Insight

"The gap between prototype and production proves wider than anticipated. The compound nature of errors in agentic systems means that minor issues for traditional software can derail agents entirely."

---

## 3. Building Agents with the Claude Agent SDK

- **URL**: https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk (redirects to https://claude.com/blog/building-agents-with-the-claude-agent-sdk)

### The Agent Feedback Loop (Four Stages)

**1. Gather Context**
- Agentic search via bash commands (grep, tail) for targeted file system retrieval
- Semantic search as secondary option when speed trumps transparency
- Subagents with isolated context windows returning only relevant excerpts
- Context compaction: "automatically summarizes previous messages when context limit approaches"

**2. Take Action**
- **Tools**: Primary execution mechanisms, prominently displayed in context
- **Bash/Scripts**: General-purpose computer operations
- **Code Generation**: Precise, composable outputs (especially effective for file creation, spreadsheets)
- **MCPs**: Standardized integrations handling authentication automatically (Slack, GitHub, Asana)

**3. Verify Work**
- Rules-based feedback (linting provides multiple validation layers)
- Visual feedback (screenshots for UI tasks)
- LLM-as-judge subagents for fuzzy rule evaluation

**4. Iterate**
- Continuous refinement based on output analysis and failure patterns

### Subagent Orchestration

Enables parallelization while managing context through isolation. Information flows bidirectionally -- subagents receive delegated tasks and return synthesized results.

### Context Engineering Insight

File and folder structure becomes a searchable knowledge base. Bash commands serve as dynamic retrieval mechanisms rather than static embedding vectors.

### Tool-First Design

Tools occupy prominent context real estate, making them the primary decision point for action selection. Tool design directly impacts context efficiency.

### Diagnostic Framework for Underperformance

When agents underperform, ask:
1. Does the agent understand the task, or is critical information inaccessible?
2. Can formal rules identify and prevent recurring failures?
3. Would alternative tools enable different problem-solving approaches?

### Best Practices

- Analyze failures to determine missing information architecture
- Add formal rules to tools when agents repeatedly fail
- Expand tool portfolios when agents cannot self-correct
- Build representative test sets for programmatic evaluation
- Start with agentic search; add semantic search only if justified
- Structure APIs to facilitate discovery
- Use subagents to filter large datasets before returning to orchestrator

---

## 4. Effective Context Engineering for AI Agents

- **URL**: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

### Core Concept

Context engineering goes beyond prompt engineering. It encompasses "strategies for curating and maintaining the optimal set of tokens (information) during LLM inference" -- including system instructions, tools, MCP integrations, external data, and message history.

### The Attention Budget Problem

LLMs face n-squared pairwise relationships in transformer attention. As context grows, models experience "context rot" -- diminishing ability to accurately recall information. Not a hard cliff but a performance gradient.

### Anatomy of Effective Context

**System Prompts**: Find the "right altitude" between too-specific (brittle) and too-vague (no shared understanding). Organize into distinct sections (background, instructions, tool guidance, output descriptions) using XML tags or markdown headers. Goal: "minimal set of information that fully outlines expected behavior."

**Tools**: Self-contained functionality with minimal overlap. Clear input parameters playing to model strengths. Bloated tool sets are a common failure mode -- "if human engineers cannot definitively choose between tools, neither can AI agents."

**Examples**: Curate diverse canonical examples rather than exhaustive edge cases. "Examples are the 'pictures' worth a thousand words."

### Context Retrieval Strategies

**Just-In-Time**: Maintain lightweight identifiers (file paths, queries, links) and dynamically retrieve using tools. Claude Code demonstrates this -- complex data analysis over large databases by writing targeted queries without loading full objects into context.

**Progressive Disclosure**: Agents incrementally discover context through exploration. File sizes suggest complexity; naming conventions hint at purpose; timestamps indicate relevance. Agents "maintain only what's necessary in working memory."

**Hybrid Approaches**: Combine pre-retrieved data for speed with autonomous exploration. Claude Code uses CLAUDE.md files dropped into context initially + glob/grep primitives for just-in-time retrieval, bypassing stale indexing.

### Long-Horizon Task Strategies

**Compaction**: When approaching context limits, summarize and reinitiate with compressed content. High-fidelity compaction preserves architectural decisions, unresolved bugs, implementation details while discarding redundant tool outputs. Tool result clearing is the safest, lightest-touch method.

**Structured Note-Taking (Agentic Memory)**: Agents regularly write notes persisted outside the context window, pulled back later. Provides "persistent memory with minimal overhead." Demonstrated in Claude playing Pokemon -- maintaining precise tallies across thousands of steps.

**Sub-Agent Architectures**: Specialized sub-agents handle focused tasks with clean context windows. Explore extensively (tens of thousands of tokens) but return condensed summaries (1,000-2,000 tokens). "Clear separation of concerns" with substantial performance improvements on complex research tasks.

### Overarching Design Principle

Treat "context as a precious, finite resource." Good context engineering means discovering the "smallest set of high-signal tokens that maximize the likelihood of some desired outcome." Even as models improve, "do the simplest thing that works."

---

## 5. Effective Harnesses for Long-Running Agents

- **URL**: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

### Core Problem

Long-running agents operate in discrete sessions with context windows that reset. Like "engineers working in shifts, where each new engineer arrives with no memory of what happened on the previous shift."

### Two-Agent Architecture

**Initializer Agent (first session)**:
- Sets up foundational environment structure
- Creates `init.sh` script for running development servers
- Establishes `claude-progress.txt` for session logging
- Initializes git repository with baseline commit
- Generates comprehensive feature requirements list (200+ features in JSON format)

**Coding Agent (subsequent sessions)**:
- Single-feature incremental progress
- Maintains clean, mergeable code state
- Updates progress documentation
- Commits changes with descriptive messages
- Performs end-to-end testing

### Session Handoff Protocol

Each coding session follows:
1. `pwd` to confirm working directory
2. Read git logs and progress files for recent activity
3. Review feature list for highest-priority incomplete work
4. Start dev server and verify basic functionality
5. Begin feature implementation only after baseline verification

### Documented Failure Patterns and Solutions

| Failure | Solution |
|---------|----------|
| Premature victory declaration | Maintain exhaustive feature checklist visible throughout |
| Undocumented progress with bugs | Mandatory git commits + progress file updates at session end |
| Incomplete testing | Explicit prompting to use browser automation (Puppeteer MCP) for e2e testing |
| Infrastructure discovery overhead | Pre-written `init.sh` with documented startup procedures |

### Key Design Decisions

- **JSON over Markdown** for feature tracking: "the model is less likely to inappropriately change or overwrite JSON files compared to Markdown files"
- **One feature at a time**: Constraining agents prevents half-implemented features and context exhaustion
- **Git-based recovery**: Revert bad changes, recover working states, track incremental progress
- **Verification-first sessions**: Test existing functionality before implementing new features
- **"Clean state" definition**: Code appropriate for merging -- no major bugs, well-documented, next developer can start new feature without cleanup

### Core Insight

Context window limits are not primarily technical constraints but **design problems requiring behavioral architecture**. Explicit session handoff protocols, comprehensive state documentation, and progress tracking systems enable effective operation despite memory discontinuity.

---

## 6. Cross-Cutting Themes and Mercury Recommendations

### Pattern: Start Simple, Add Complexity Only When Justified

Every Anthropic article reinforces this. Mercury should:
- Implement the orchestrator-workers pattern as the primary multi-agent architecture
- Support simpler patterns (prompt chaining, routing, parallelization) as building blocks
- Make it easy to start with a single agent and scale to multi-agent only when needed
- Provide clear decision criteria for when to escalate complexity

### Pattern: Orchestrator-Workers is the Proven Multi-Agent Architecture

Anthropic's production system validates this pattern:
- Lead agent decomposes, delegates, synthesizes
- Subagents operate with isolated context windows
- Only condensed results flow back to orchestrator (1,000-2,000 tokens from tens of thousands)
- Parallelization cuts research time by up to 90%

**Mercury should adopt**: First-class support for orchestrator-workers with configurable delegation strategies, parallel subagent execution, and result synthesis.

### Pattern: Context is the Critical Resource

Across all articles, context management emerges as the make-or-break factor:
- **Compaction**: Automatic summarization when context limits approach
- **External memory**: Persistent storage outside context window (plans, progress, notes)
- **Subagent isolation**: Each agent gets clean context, returns only relevant information
- **Just-in-time retrieval**: Dynamic tool-based context loading over static pre-loading
- **Progressive disclosure**: Explore incrementally rather than loading everything upfront

**Mercury should adopt**: Built-in context management layer with compaction, external memory persistence, and subagent context isolation.

### Pattern: Delegation Requires Precision

Vague delegation causes duplicated work and misaligned outputs. Effective delegation requires:
- Clear objectives with specific output formats
- Explicit tool and source guidance
- Defined task boundaries preventing overlap
- Scaling rules (simple = 1 agent/few tools, complex = many agents/divided responsibilities)

**Mercury should adopt**: Structured delegation protocol with required fields (objective, output format, tool guidance, boundaries, scaling hints).

### Pattern: Tool Design is More Important Than Prompt Design

Anthropic spent more time on tools than prompts for SWE-bench. Tool quality directly determines agent effectiveness.

**Mercury should adopt**: Tool validation/documentation standards, ACI design guidelines, MCP integration as first-class concern.

### Pattern: Evaluation Must Be Multi-Layered

- Small-sample early testing (~20 cases) catches large effects
- LLM-as-judge for automated scoring on rubrics
- Human evaluation catches edge cases (hallucinations, source bias, emergent behaviors)
- End-state evaluation over intermediate step validation for stateful agents

**Mercury should adopt**: Built-in evaluation framework supporting all three tiers.

### Pattern: Production Multi-Agent Systems Need Robust Engineering

- Error compounding is catastrophic -- need retry logic, checkpoints, resume-from-failure
- Non-deterministic behavior requires comprehensive observability/tracing
- Rainbow deployments for safe rollouts of agent changes
- Emergent behaviors from agent interactions require system-level monitoring

**Mercury should adopt**: Production hardening layer with checkpointing, tracing, graceful degradation, and deployment strategies.

### Pattern: Session Continuity for Long-Running Tasks

- External progress files and memory persistence
- Git-based state tracking and recovery
- Verification-first session starts
- One-task-at-a-time incremental progress
- JSON over Markdown for structured state (models less likely to corrupt JSON)

**Mercury should adopt**: Session management system with handoff protocols, progress persistence, and state recovery.

### Pattern: Feedback Loops Drive Agent Quality

The four-stage loop (Gather Context -> Take Action -> Verify Work -> Iterate) is fundamental:
- Rules-based verification (linting, tests)
- Visual verification (screenshots)
- LLM-as-judge for fuzzy evaluation
- Human-in-the-loop at checkpoints

**Mercury should adopt**: Configurable verification pipeline with pluggable feedback mechanisms.

---

## Articles Not Found

Two articles from the original research request could not be located:

1. **"Building multi-agent systems: when and how to use them"** -- No standalone article with this title exists on anthropic.com. The content appears distributed across "Building Effective Agents" (workflow/agent patterns) and "How we built our multi-agent research system" (production multi-agent specifics), both covered above.

2. **"Lessons from building Claude Code: Seeing like an Agent"** -- No article with this title exists on anthropic.com/engineering. The closest related articles are "Effective context engineering for AI agents" and "Building agents with the Claude Agent SDK," both of which contain substantial Claude Code lessons and are covered above.

---

## Source URLs

| Article | URL |
|---------|-----|
| Building Effective Agents | https://www.anthropic.com/research/building-effective-agents |
| How We Built Our Multi-Agent Research System | https://www.anthropic.com/engineering/multi-agent-research-system |
| Building Agents with the Claude Agent SDK | https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk |
| Effective Context Engineering for AI Agents | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents |
| Effective Harnesses for Long-Running Agents | https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents |
