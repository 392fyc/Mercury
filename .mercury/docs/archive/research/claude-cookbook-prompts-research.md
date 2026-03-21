# Claude Cookbook & Agent Prompt Patterns Research

> Research compiled 2026-03-16. Sources: Anthropic Cookbook, Claude Code system prompts, Anthropic engineering blog, Claude Agent SDK docs.

---

## Table of Contents

1. [Anthropic Cookbook: Agent Pattern Notebooks](#1-anthropic-cookbook-agent-pattern-notebooks)
2. [Orchestrator-Workers Prompt Pattern](#2-orchestrator-workers-prompt-pattern)
3. [Deep Research Multi-Agent Prompts](#3-deep-research-multi-agent-prompts)
4. [Claude Code Internal System Prompt Architecture](#4-claude-code-internal-system-prompt-architecture)
5. [Claude Code Subagent Architecture](#5-claude-code-subagent-architecture)
6. [Context Engineering for Agents](#6-context-engineering-for-agents)
7. [Long-Running Agent Harness Patterns](#7-long-running-agent-harness-patterns)
8. [Claude Agent SDK Patterns](#8-claude-agent-sdk-patterns)
9. [Key Takeaways for Mercury](#9-key-takeaways-for-mercury)

---

## 1. Anthropic Cookbook: Agent Pattern Notebooks

**Source**: [anthropics/anthropic-cookbook/patterns/agents](https://github.com/anthropics/anthropic-cookbook/tree/main/patterns/agents)

The cookbook implements the patterns from Anthropic's [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) research paper. The agent patterns directory contains:

| Notebook | Pattern | Description |
|----------|---------|-------------|
| `basic_workflows.ipynb` | Prompt Chaining, Routing, Parallelization | Foundational agent workflow patterns |
| `orchestrator_workers.ipynb` | Orchestrator-Workers | Central LLM decomposes tasks, delegates to workers |
| `evaluator_optimizer.ipynb` | Evaluator-Optimizer | Generator + evaluator in iterative feedback loop |
| `prompts/research_lead_agent.md` | Research orchestrator | Full orchestrator prompt for deep research |
| `prompts/research_subagent.md` | Research worker | Subagent prompt for delegated research tasks |
| `prompts/citations_agent.md` | Citation specialist | Post-processing agent for adding citations |

### Core Architectural Patterns (from Anthropic's Research)

1. **Prompt Chaining**: Sequential LLM calls, each processing prior output. Includes programmatic validation gates between steps.
2. **Routing**: Classify inputs to specialized downstream handlers with focused prompts.
3. **Parallelization**: Either "sectioning" (independent subtasks run simultaneously) or "voting" (same task repeated for diverse outputs).
4. **Orchestrator-Workers**: Central LLM dynamically decomposes and delegates. Workers handle specialized subtasks; orchestrator synthesizes results.
5. **Evaluator-Optimizer**: Generator produces output; evaluator provides iterative feedback until quality threshold met.
6. **Autonomous Agents**: LLMs maintain self-directed process control based on environmental feedback.

### Three Core Implementation Principles

1. **Simplicity** — reduce abstraction layers; start with direct API usage
2. **Transparency** — make planning steps visible and explicit
3. **Tool Documentation & Testing** — treat agent-computer interfaces (ACI) with same rigor as human-computer interfaces

---

## 2. Orchestrator-Workers Prompt Pattern

**Source**: [orchestrator_workers.ipynb](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb)

### Orchestrator Prompt Template

```
Analyze this task and break it down into 2-3 distinct approaches:

Task: {task}

Return your response in this format:

<analysis>
Explain your understanding of the task and which variations would be valuable.
Focus on how each approach serves different aspects of the task.
</analysis>

<tasks>
    <task>
    <type>formal</type>
    <description>Write a precise, technical version that emphasizes specifications</description>
    </task>
    <task>
    <type>conversational</type>
    <description>Write an engaging, friendly version that connects with readers</description>
    </task>
</tasks>
```

### Worker Prompt Template

```
Generate content based on:
Task: {original_task}
Style: {task_type}
Guidelines: {task_description}

Return your response in this format:

<response>
Your content here, maintaining the specified style and fully addressing requirements.
</response>
```

### Key Design Patterns

- **XML-based structured output** for reliable parsing between orchestrator and worker
- **Template variables** (`{task}`, `{original_task}`, `{task_type}`, `{task_description}`) injected at runtime
- **Workers receive both the original task AND their specific instructions** for full context
- **FlexibleOrchestrator class** with `_format_prompt()` method for template substitution
- Uses `parse_tasks()` helper to extract XML task structures into Python dicts
- Two-phase workflow: analysis phase -> parallel worker execution

---

## 3. Deep Research Multi-Agent Prompts

**Source**: [anthropic-cookbook/patterns/agents/prompts/](https://github.com/anthropics/anthropic-cookbook/tree/main/patterns/agents/prompts)

### 3.1 Research Lead Agent (Orchestrator)

**File**: `research_lead_agent.md` — Full orchestrator prompt for a multi-agent deep research system.

**Role definition**:
> "You are an expert research lead, focused on high-level research strategy, planning, efficient delegation to subagents, and final report writing."

**Four-Stage Research Process**:

1. **Assessment and Breakdown**: Analyze the user's prompt — identify main concepts, key entities, relationships, specific facts needed, temporal constraints, and what form the answer should take.

2. **Query Type Determination** — classifies queries into three categories:
   - **Depth-first**: Multiple perspectives on a single issue. "Going deep." Example: "What caused the 2008 financial crisis?" (economic, regulatory, behavioral, historical perspectives)
   - **Breadth-first**: Distinct independent sub-questions. "Going wide." Example: "Compare economic systems of three Nordic countries" (parallel research streams)
   - **Straightforward**: Focused fact-finding. Example: "What is the current population of Tokyo?"

3. **Detailed Research Plan Development** — different strategies per query type:
   - Depth-first: 3-5 methodological approaches/perspectives, plan synthesis
   - Breadth-first: Enumerate independent sub-questions, define clear boundaries, plan aggregation
   - Straightforward: Most direct efficient path, basic verification

4. **Methodical Plan Execution** — deploy subagents, synthesize findings

**Subagent Count Guidelines**:
| Complexity | Subagent Count | Example |
|-----------|---------------|---------|
| Simple/Straightforward | 1 | "When is the tax deadline?" |
| Standard | 2-3 | "Compare top 3 cloud providers" |
| Medium | 3-5 | "Analyze AI impact on healthcare" |
| High | 5-10 (max 20) | "Fortune 500 CEOs birthplaces and ages" |

**Critical Orchestrator Rules**:
- Deploy subagents immediately after finalizing plan
- Use parallel tool calls for maximum efficiency
- Orchestrator coordinates and synthesizes — does NOT conduct primary research
- NEVER delegate final report writing to subagents
- Stop research when diminishing returns appear
- Adapt strategy based on subagent results (Bayesian updating)

**Delegation Instructions** (key excerpt):
> "Make sure that IF all the subagents followed their instructions very well, the results in aggregate would allow you to give an EXCELLENT answer to the user's question."

Each subagent task description must include:
- Specific research objectives (1 core objective per subagent)
- Expected output format
- Background context about the user's question
- Key questions to answer
- Suggested starting points and sources
- Specific tools to use
- Scope boundaries to prevent research drift

### 3.2 Research Subagent (Worker)

**File**: `research_subagent.md` — Worker agent for delegated research tasks.

**Role definition**:
> "You are a research subagent working as part of a team."

**Three-Phase Process**:

1. **Planning**: Think through task, develop research budget:
   - Simple tasks: under 5 tool calls
   - Medium tasks: ~5 tool calls
   - Hard tasks: ~10 tool calls
   - Very difficult: up to 15 tool calls

2. **Tool Selection**: Prioritize internal tools first (Google Drive, Gmail, Calendar), then web_search and web_fetch.

3. **Research Loop**: OODA methodology (Observe, Orient, Decide, Act) with 5-10 minimum tool calls.

**Key Worker Guidelines**:
- Moderate query breadth (queries under 5 words)
- Track findings with source attribution
- Flag speculation vs. established facts
- Maximum 20 tool calls, ~100 sources absolute limit
- Parallelize independent operations
- Use `web_fetch` for complete webpage retrieval after search
- Maintain epistemic honesty — flag source quality issues
- Complete task immediately once sufficient info gathered via `complete_task` tool

### 3.3 Citations Agent (Post-Processor)

**File**: `citations_agent.md` — Specialized agent for adding citations to completed research reports.

**Role**: Takes synthesized text and source documents, adds citations without modifying content.

**Rules**:
- Do NOT modify the synthesized text — only add citations
- Preserve exact whitespace
- Only cite where sources directly support claims
- Cite meaningful semantic units (complete thoughts, not fragments)
- Minimize sentence fragmentation from multiple citations
- No redundant citations to same source within a sentence
- Output within `<exact_text_with_citation>` tags

### Design Pattern: Separation of Concerns

The research system uses three distinct agent roles:
1. **Orchestrator** (research_lead) — plans, delegates, synthesizes
2. **Workers** (research_subagent) — conduct actual research
3. **Post-processor** (citations_agent) — quality/formatting pass on final output

This separation ensures each agent has focused context and clear responsibilities.

---

## 4. Claude Code Internal System Prompt Architecture

**Source**: [Piebald-AI/claude-code-system-prompts](https://github.com/Piebald-AI/claude-code-system-prompts) — Documents Claude Code v2.1.76 (March 13, 2026)

### Architecture Overview

Claude Code does NOT use a single monolithic system prompt. Instead, it employs **~110+ conditional string fragments** distributed across its codebase:

- **Conditional components** adjusted by environment and configuration
- **Tool descriptions** for 18+ builtin utilities
- **Separate sub-agent prompts** for specialized tasks
- **Utility function prompts** for conversation summarization, CLAUDE.md generation, session management
- **40+ system reminders** injected contextually during sessions

### Main System Prompt Structure (~70+ individual files)

**Behavioral Guidelines** (13 files):
- Task execution philosophy (scope, engineering focus, compatibility, abstractions)
- Tool usage policies (11 files directing when to use Glob, Grep, Write, Edit, Bash, Task tools)
- Tone/style conventions (concise output, code references as `file_path:line_number`)

**Execution & Safety**:
- Executing Actions with Care (541 tokens) — careful action methodology
- Fork Usage Guidelines (339 tokens) — sub-agent delegation rules, output integrity
- Auto Mode (188 tokens) — continuous background task execution
- Security Monitor (5641 tokens total) — two-part system evaluating autonomous actions

**Context Management**:
- Context Compaction (3 variants): full conversation (182 tokens), minimal (157 tokens), recent messages only (178 tokens)
- Session Continuation (37 tokens) — multi-machine session resumption
- Memory Instructions (337 tokens) — session memory file updates

### Built-in Sub-Agent Prompts

| Agent | Tokens | Purpose |
|-------|--------|---------|
| Explore Agent | 517 | Codebase exploration, search strategy |
| Plan Mode Agents | 685 | 5-phase planning with parallel exploration |
| Explore Strengths | 185 | Behavioral guidelines for thoroughness |
| Agent Creation Architect | 1110 | Designs custom AI agents |
| CLAUDE.md Creator | 384 | Analyzes codebases, generates documentation |
| Status Line Setup | 1641 | Configures status line display |
| Verification Specialist | 2453 | Adversarially tests implementations |
| Bash Command Writer | 207 | Generates clear command descriptions |
| Conversation Summarization | 956 | Detailed summaries for context compaction |
| Session Memory Manager | 756 | Updates memory files during conversations |

### Slash Command Agent Prompts

| Command | Tokens | Purpose |
|---------|--------|---------|
| `/batch` | 1136 | Orchestrates large parallelizable codebase changes |
| `/review-pr` | 211 | GitHub PR analysis with code examination |
| `/security-review` | 2607 | Comprehensive vulnerability assessment |
| `/pr-comments` | 402 | Retrieves and displays GitHub PR comments |

### System Reminders (40+ injected dynamically)

- File operations: truncation warnings (74 tokens), offset exceeded (59 tokens), external modifications (97 tokens)
- Hook system: success messages (29 tokens), blocking errors (52 tokens)
- Plan mode: 5-phase active (1297 tokens), iterative variant (919 tokens), sub-agent variant (307 tokens)
- Team coordination (250 tokens): multi-agent synchronization and shutdown protocols
- Token & budget tracking: usage statistics and USD cost monitoring

### Reference Data Sections (30+ documents)

- API references: Python/TypeScript/Java/Go/C#/PHP/Ruby/cURL (696-4703 tokens each)
- Tool Use Concepts (3932 tokens)
- Tool Use References: Python (5106 tokens), TypeScript (5033 tokens)
- Agent SDK patterns/references for Python and TypeScript
- Model Catalog (1558 tokens): current and legacy Claude models with context windows and pricing
- Live Documentation Sources (2336 tokens): WebFetch URLs for current API docs
- HTTP Error Codes (1922 tokens): API error handling strategies

---

## 5. Claude Code Subagent Architecture

**Source**: [Claude Code Docs — Create custom subagents](https://code.claude.com/docs/en/sub-agents)

### Built-in Subagents

| Agent | Model | Tools | Purpose |
|-------|-------|-------|---------|
| **Explore** | Haiku (fast) | Read-only | File discovery, code search, codebase exploration |
| **Plan** | Inherits | Read-only | Codebase research for plan mode |
| **General-purpose** | Inherits | All tools | Complex multi-step operations |
| **Bash** | Inherits | Terminal | Running commands in separate context |
| **statusline-setup** | Sonnet | Specific | Configure status line display |
| **Claude Code Guide** | Haiku | Specific | Answer questions about Claude Code features |

### Subagent File Format

Subagents are defined as Markdown files with YAML frontmatter:

```markdown
---
name: code-reviewer
description: Reviews code for quality and best practices
tools: Read, Glob, Grep
model: sonnet
---

You are a code reviewer. When invoked, analyze the code and provide
specific, actionable feedback on quality, security, and best practices.
```

**Key**: "Subagents receive only this system prompt (plus basic environment details like working directory), not the full Claude Code system prompt."

### Configuration Options

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique identifier (lowercase + hyphens) |
| `description` | Yes | When Claude should delegate to this subagent |
| `tools` | No | Allowlist of tools (inherits all if omitted) |
| `disallowedTools` | No | Denylist of tools |
| `model` | No | `sonnet`, `opus`, `haiku`, full ID, or `inherit` |
| `permissionMode` | No | `default`, `acceptEdits`, `dontAsk`, `bypassPermissions`, `plan` |
| `maxTurns` | No | Maximum agentic turns |
| `skills` | No | Skills to preload into context |
| `mcpServers` | No | MCP servers available to subagent |
| `hooks` | No | Lifecycle hooks scoped to subagent |
| `memory` | No | Persistent memory scope: `user`, `project`, `local` |
| `background` | No | Run as background task |
| `isolation` | No | `worktree` for git worktree isolation |

### Subagent Scope & Priority

| Location | Scope | Priority |
|----------|-------|----------|
| `--agents` CLI flag | Current session | 1 (highest) |
| `.claude/agents/` | Current project | 2 |
| `~/.claude/agents/` | All projects | 3 |
| Plugin's `agents/` | Where plugin enabled | 4 (lowest) |

### Orchestrator-to-Subagent Restriction

Orchestrator agents can restrict which subagents they spawn:
```yaml
---
name: coordinator
description: Coordinates work across specialized agents
tools: Agent(worker, researcher), Read, Bash
---
```

**Critical constraint**: Subagents cannot spawn other subagents. This prevents infinite nesting.

### Persistent Memory for Subagents

When `memory` is enabled:
- Subagent's system prompt includes instructions for reading/writing to memory directory
- First 200 lines of `MEMORY.md` are included in system prompt
- Read, Write, Edit tools are auto-enabled for memory management
- Memory survives across sessions, building knowledge over time

Memory scopes:
| Scope | Location | Use case |
|-------|----------|----------|
| `user` | `~/.claude/agent-memory/<name>/` | Cross-project learnings |
| `project` | `.claude/agent-memory/<name>/` | Project-specific, shareable |
| `local` | `.claude/agent-memory-local/<name>/` | Project-specific, not in VCS |

### Key Patterns from Subagent Docs

1. **Isolate high-volume operations**: Delegate tests, docs fetching, log processing to subagents to keep verbose output out of main context
2. **Parallel research**: Spawn multiple subagents for independent investigations
3. **Chained subagents**: Multi-step workflows with sequential delegation
4. **Foreground vs background**: Blocking vs concurrent execution with pre-approved permissions
5. **Hook-based validation**: `PreToolUse` hooks for conditional tool control (e.g., read-only DB queries)

---

## 6. Context Engineering for Agents

**Source**: [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)

### Core Principles

**Context as finite resource**: "Context must be treated as a finite resource with diminishing marginal returns." Models degrade at scale due to n-squared pairwise token relationships. Design for "smallest possible set of high-signal tokens."

**Altitude Calibration**: Avoid two failure modes:
1. Hardcoded, brittle if-else logic (too rigid)
2. Vague guidance assuming shared understanding (too loose)

Optimal: "specific enough to guide behavior effectively, yet flexible enough to provide the model with strong heuristics"

### System Prompt Structure

Recommended sections using XML tags or Markdown headers:
- `<background_information>`
- `<instructions>`
- `## Tool guidance`
- `## Output description`

### Tool Design Principles

- Minimize functional overlap between tools
- Return token-efficient information
- Use descriptive, unambiguous input parameters
- Avoid bloated tool sets that create ambiguous decision points

### Retrieval Strategies

**Just-In-Time Context**:
- Maintain lightweight identifiers (file paths, URLs, queries)
- Load data dynamically at runtime using tools
- Leverage folder hierarchies, naming conventions, timestamps as navigation signals
- Progressive disclosure — incrementally discover relevant context through exploration

**Hybrid Strategy**: Combine pre-loaded data (documentation) with autonomous retrieval tools (glob, grep).

### Long-Horizon Task Management

**Compaction**: Summarize conversations approaching context limits. Preserve architectural decisions, unresolved issues. Tool result clearing is lowest-risk compaction form.

**Structured Note-Taking (Agentic Memory)**:
- Agent maintains persistent notes outside context window
- Tracks progress, critical dependencies, strategic insights
- Example: agent maintains tallies, maps, achievement logs across thousands of steps

**Sub-Agent Architecture**:
- Specialized agents handle focused tasks with clean context windows
- Main agent coordinates high-level planning
- Sub-agents return condensed summaries (1,000-2,000 tokens) from extensive exploration (potentially tens of thousands of tokens)
- **Separates detailed search context from synthesis/analysis**

### Few-Shot Prompting for Agents

- Use diverse, canonical examples rather than exhaustive edge case lists
- "Examples are the 'pictures' worth a thousand words for LLMs"
- Start with minimal examples; add based on observed failure modes

---

## 7. Long-Running Agent Harness Patterns

**Source**: [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)

### Two-Part Agent Structure

1. **Initializer Agent**: First session sets up complete environment with feature lists, progress tracking, initial git commits
2. **Coding Agent**: Subsequent sessions make incremental progress with structured artifact updates

### Per-Session Startup Routine

1. Verify working directory with basic commands
2. Review progress files and git history
3. Consult feature requirements to identify next priority
4. Run basic end-to-end tests before new work
5. Implement incrementally — one feature at a time

### Progress Artifacts

- Maintain `claude-progress.txt` documenting agent work alongside git history
- "Allows agents to quickly understand the state of work when starting with a fresh context window"
- Code should be production-ready at session end (no major bugs, proper documentation, committed)

### Feature Status Tracking

Use JSON feature files with boolean `passes` fields:
- Instruct agents never to delete or edit entries, only update status flags
- Prevents missing functionality across sessions

### Recovery Patterns

- Prompt agents to commit frequently with descriptive messages
- Git enables reverting bad changes and recovering working states
- Use browser automation (Puppeteer) for end-to-end testing — "test as a human user would"

---

## 8. Claude Agent SDK Patterns

**Source**: [Claude Agent SDK Overview](https://platform.claude.com/docs/en/agent-sdk/overview)

### SDK Architecture

The Agent SDK provides the same tools, agent loop, and context management that power Claude Code, programmable in Python and TypeScript. Key difference from Client SDK: Agent SDK includes built-in tool execution (no manual tool loop needed).

### Subagent Definition via SDK

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition

async for message in query(
    prompt="Use the code-reviewer agent to review this codebase",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Grep", "Agent"],
        agents={
            "code-reviewer": AgentDefinition(
                description="Expert code reviewer for quality and security reviews.",
                prompt="Analyze code quality and suggest improvements.",
                tools=["Read", "Glob", "Grep"],
            )
        },
    ),
):
    ...
```

### CLI-Defined Subagents (JSON)

```bash
claude --agents '{
  "code-reviewer": {
    "description": "Expert code reviewer. Use proactively after code changes.",
    "prompt": "You are a senior code reviewer. Focus on code quality, security, and best practices.",
    "tools": ["Read", "Grep", "Glob", "Bash"],
    "model": "sonnet"
  },
  "debugger": {
    "description": "Debugging specialist for errors and test failures.",
    "prompt": "You are an expert debugger. Analyze errors, identify root causes, and provide fixes."
  }
}'
```

### Key SDK Design Choices

- SDK no longer loads Claude Code's system prompt by default — agents get minimal prompt unless `claude_code` preset or custom prompt provided
- Subagents get their own isolated context windows
- Messages from subagents include `parent_tool_use_id` for tracking
- Sessions can be resumed with full context via session IDs
- Hooks available: `PreToolUse`, `PostToolUse`, `Stop`, `SessionStart`, `SessionEnd`, `UserPromptSubmit`
- Setting sources configurable: `setting_sources=["project"]` to use filesystem-based config

---

## 9. Key Takeaways for Mercury

### Prompt Architecture Decisions

1. **Modular, conditional prompt assembly** — Follow Claude Code's pattern of ~110 conditional fragments rather than monolithic prompts. Compose system prompts from focused, testable components.

2. **XML-structured communication** — Use XML tags for structured data flow between orchestrator and workers. This is the pattern both the cookbook and Claude Code use internally.

3. **Markdown+YAML frontmatter for agent definitions** — Clean, version-controllable format for agent configurations. Body = system prompt, frontmatter = metadata/tools/model.

4. **Query type classification** — The research lead agent's depth-first / breadth-first / straightforward taxonomy is a proven pattern for dynamically choosing research strategy.

### Orchestrator vs. Worker Prompt Design

| Aspect | Orchestrator | Worker |
|--------|-------------|--------|
| **Focus** | Strategy, delegation, synthesis | Execution, research, specific tasks |
| **Tools** | Primarily subagent spawning | Domain-specific tools (search, read, etc.) |
| **Context** | Full task + synthesized results | Original task + specific sub-task instructions |
| **Output** | Final synthesized report | Raw findings with source attribution |
| **Budget** | Controls overall resource allocation | Has per-task tool call budget |
| **Rules** | Never do primary research; never delegate final synthesis | Never spawn sub-subagents; complete ASAP |

### Context Injection Patterns Mercury Should Adopt

1. **System reminders** — Dynamic contextual injections mid-conversation (Claude Code uses 40+ of these)
2. **Just-in-time context loading** — Maintain lightweight identifiers, load data dynamically via tools
3. **Compaction with preservation** — Summarize conversations near limits while preserving architectural decisions and unresolved issues
4. **Persistent agent memory** — Cross-session knowledge via memory directories (MEMORY.md pattern)
5. **Progressive disclosure** — Incrementally discover context through exploration rather than front-loading

### Tool Use Patterns

1. **Minimal tool overlap** — Each tool should have a clear, distinct purpose
2. **Token-efficient tool results** — Tools should return concise, high-signal information
3. **Poka-yoke tool design** — Restructure arguments to prevent misuse
4. **Descriptive parameter names** with comprehensive docstrings
5. **Tool restriction per agent** — Workers get only the tools they need (principle of least privilege)

### Multi-Agent Best Practices

1. **Separation of concerns**: Orchestrator (plan/delegate/synthesize) + Workers (research/execute) + Post-processors (citations/formatting)
2. **No sub-subagent spawning** — Prevents infinite nesting; keeps architecture flat
3. **Parallel execution** for independent tasks; sequential for dependent ones
4. **Budget-aware agents** — Each worker has a tool call budget scaled to task complexity
5. **Epistemic honesty** — Workers flag speculation vs. facts, source quality issues
6. **Diminishing returns detection** — Stop research when further work yields diminishing returns
7. **Feature status tracking** with JSON for long-running multi-session work
8. **Git-based checkpointing** — Frequent commits enable recovery from bad states

### Specific Prompt Engineering Techniques

1. **Role + goal + process** structure: Define who the agent is, what it achieves, and how it works
2. **Explicit section headers** with XML or Markdown for different instruction categories
3. **Examples over rules** — "Examples are worth a thousand words for LLMs"
4. **Budget calibration** — Explicitly specify expected effort levels per task complexity
5. **Output format specification** — XML tags or structured formats for reliable parsing
6. **Anti-patterns in prompt** — Explicitly state what NOT to do (e.g., "NEVER delegate final report writing")
7. **Bayesian reasoning instruction** — Tell agents to update priors based on new information
8. **OODA loop methodology** — Observe, Orient, Decide, Act as explicit research methodology

---

## Sources

- [Anthropic Cookbook — Agent Patterns](https://github.com/anthropics/anthropic-cookbook/tree/main/patterns/agents)
- [Building Effective Agents — Anthropic Research](https://www.anthropic.com/research/building-effective-agents)
- [Effective Context Engineering for AI Agents — Anthropic Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Effective Harnesses for Long-Running Agents — Anthropic Engineering](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Claude Code System Prompts — Piebald-AI](https://github.com/Piebald-AI/claude-code-system-prompts)
- [Create Custom Subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents)
- [Claude Agent SDK Overview](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Building Agents with the Claude Agent SDK — Anthropic Engineering](https://claude.com/blog/building-agents-with-the-claude-agent-sdk)
- [Orchestrator-Workers Notebook](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/orchestrator_workers.ipynb)
- [Research Lead Agent Prompt](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/prompts/research_lead_agent.md)
- [Research Subagent Prompt](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/prompts/research_subagent.md)
- [Citations Agent Prompt](https://github.com/anthropics/anthropic-cookbook/blob/main/patterns/agents/prompts/citations_agent.md)
