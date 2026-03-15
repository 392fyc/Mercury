# OpenAI Multi-Agent Systems Research
> Collected 2026-03-16 | Mercury Project

---

## 1. Deep Research: Multi-Agent Architecture

**Sources:**
- https://developers.openai.com/cookbook/examples/deep_research_api/introduction_to_deep_research_api_agents/
- https://blog.promptlayer.com/how-deep-research-works/
- https://blog.bytebytego.com/p/how-openai-gemini-and-claude-use
- https://platform.openai.com/docs/guides/deep-research
- https://cdn.openai.com/deep-research-system-card.pdf

### Overview

Deep Research is OpenAI's agentic capability for conducting multi-step research on the internet. It is powered by specialized versions of the o3 model (o3-deep-research, o4-mini-deep-research) trained via end-to-end reinforcement learning on complex browsing and reasoning tasks. The model learned core browsing capabilities (searching, clicking, scrolling, interpreting files), Python tool use in a sandboxed setting, and how to reason through and synthesize large numbers of websites.

### Four-Agent Pipeline (API/SDK Implementation)

The Deep Research API cookbook demonstrates a four-agent pipeline built with the Agents SDK:

1. **Triage Agent** — Routes queries based on context completeness. Decides: does the user's query need clarification, or is it ready for research?
2. **Clarifying Agent** — Gathers missing information via structured questions (Pydantic `Clarifications` model). Waits for user responses before forwarding.
3. **Instruction Agent** — Transforms enriched input into precise research briefs for the research agent.
4. **Research Agent** — Executes empirical research using the `o3-deep-research-2025-06-26` model with WebSearchTool and HostedMCPTool.

Coordination uses handoff mechanisms:
```
Triage → (if needs clarification) → Clarifying Agent → Instruction Agent → Research Agent
Triage → (if clear) → Instruction Agent → Research Agent
```

Context flows sequentially. Each agent receives the original user query plus accumulated context from upstream agents, propagated through handoffs as message history maintained by the Runner.

### ReAct-Style Agent Loop

The core model follows a "Plan-Act-Observe" cycle:
1. Determine needed actions
2. Invoke tools (search, browser, code interpreter)
3. Observe results
4. Iterate — pursue multiple threads, backtrack from dead ends, refine queries

### Five-Phase Research Process

1. **Clarification** — Ask follow-up questions before researching
2. **Decomposition** — Break complex queries into subtopics/sub-questions with implicit prioritization
3. **Iterative Searching** — Progressive focusing and query refinement (documented: 21 sources across 28 minutes in one case)
4. **Content Analysis** — Read diverse formats (HTML, PDF, images); write and run code on the fly for calculations/visualizations
5. **Synthesis & Citation** — Structured reports with inline citations linking exact sources

### Core Tools

| Tool | Purpose |
|------|---------|
| Web Search | Real-time queries to search engines |
| Browser | Full page content fetching and navigation |
| Code Interpreter | Python execution for data analysis and visualization |
| File Parser | PDFs, images, non-HTML formats |
| MCP Servers | Internal knowledge stores via Model Context Protocol |

### Memory & Context Strategy

- The o3 model maintains lengthy reasoning chains (sometimes hundreds of steps) through specialized training that prevents divergence
- Sub-agents maintain short-term memory/context of discovered information, preventing redundant work
- Sub-agents return "well-structured packets" containing findings and source citations
- No explicit shared memory system — context propagates through handoffs as message history

### Stopping Mechanisms

Dual stopping logic:
- **Coverage-based**: Stops when sufficient sources address sub-questions, novelty exhausts, or confidence thresholds are met
- **Budget-driven**: Hard limits on wall-clock time (20-30 min), search calls (30-60), page fetches (120-150), reasoning iterations (150-200)

### Training Approach

End-to-end reinforcement learning in simulated research environments with tool access. Through this process the model learned to: plan and execute multi-step search trajectories, backtrack when paths are unfruitful, and pivot strategies based on new information.

---

## 2. OpenAI Agents SDK

**Sources:**
- https://openai.github.io/openai-agents-python/
- https://openai.github.io/openai-agents-python/handoffs/
- https://openai.github.io/openai-agents-python/guardrails/
- https://openai.github.io/openai-agents-python/agents/
- https://openai.github.io/openai-agents-python/running_agents/

### Architecture & Design Principles

The SDK (launched March 2025, evolved from the experimental Swarm project) is built around four core primitives: **Agents, Handoffs, Guardrails, and Tracing**. Design philosophy: "enough features to be worth using, but few enough primitives to make it quick to learn."

- **Python-first**: Uses native language constructs rather than domain-specific abstractions
- **Agent Loop**: Automatically handles tool invocation, result transmission, and continuation logic
- **Code-first orchestration**: Workflow logic via familiar programming constructs, no pre-defined graphs

### Core Primitives

**Agent** — An LLM configured with `name`, `instructions` (static string or dynamic async callback), `tools`, `handoffs`, `model_settings`, `output_type`, and lifecycle hooks.

```python
from agents import Agent, Runner

agent = Agent(name="Assistant", instructions="You are a helpful assistant")
result = Runner.run_sync(agent, "Write a haiku about recursion...")
print(result.final_output)
```

**Handoffs** — Agent-to-agent delegation exposed as tools (naming: `transfer_to_<agent_name>`). The `handoff()` function accepts:
- `agent`: target agent
- `tool_name_override` / `tool_description_override`: custom naming
- `on_handoff`: callback executed during handoff
- `input_type`: schema for handoff metadata (e.g., `{"reason": "duplicate_charge", "priority": "high"}`)
- `input_filter`: modifies conversation history received by next agent (pre-built filters in `agents.extensions.handoff_filters`)
- `is_enabled`: boolean or function controlling runtime availability
- `nest_handoff_history`: collapses prior transcripts into summaries (beta)

Key behaviors:
- Handoffs stay within a single run
- Receiving agent sees full conversation history unless filtered
- Input guardrails apply only to first agent; output guardrails only to final agent

**Guardrails** — Three types:
- **Input Guardrails**: Validate user input. Run in parallel (default) or blocking mode. Blocking mode prevents token consumption if tripwire triggers.
- **Output Guardrails**: Validate final agent output. Always run after agent completion.
- **Tool Guardrails**: Wrap function tools to validate before/after execution.
- **Tripwires**: Signal validation failure, immediately raise exception and halt execution.

**Tracing** — Built-in observability for visualization, debugging, evaluation, and fine-tuning. Supports OpenAI's evaluation and distillation tooling.

### Agent Loop Execution

The Runner executes agents through a structured loop:
1. Call the LLM for the current agent with the current input
2. LLM produces one of three outcomes:
   - **Final output** → loop terminates
   - **Handoff** → agent switches, loop continues
   - **Tool calls** → executed, results appended, loop repeats
3. Exceeding `max_turns` raises `MaxTurnsExceeded`

Three execution modes:
- `Runner.run()` — Asynchronous, returns `RunResult`
- `Runner.run_sync()` — Synchronous wrapper
- `Runner.run_streamed()` — Async with streaming events, returns `RunResultStreaming`

Input accepts strings, OpenAI Responses API item lists, or `RunState` objects for resuming interrupted runs.

### Agent-as-Tool Pattern vs. Handoffs

| Pattern | Control | Use Case |
|---------|---------|----------|
| **Agent-as-Tool** (`agent.as_tool()`) | Central agent retains control, invokes sub-agents as tools | Manager pattern; maintain conversation context |
| **Handoffs** (`agent.handoffs=[...]`) | Control transfers to peer agent | Triage/routing; specialized delegation |

### Conversation History & State Management

Four persistence strategies:

| Strategy | Storage | Best Use |
|----------|---------|----------|
| `to_input_list()` | Application memory | Manual control, small loops |
| **Sessions** | Client + storage (SQLAlchemy, SQLite, Redis, Dapr) | Persistent, resumable state |
| `conversation_id` | OpenAI server | Named conversations across services |
| `previous_response_id` | OpenAI server | Lightweight chaining between turns |

Session-based and server-managed approaches cannot be mixed within a single run. When agents handoff, the runner updates the current agent and input then re-executes the loop. Optional `nest_handoff_history` collapses prior transcripts into a single assistant message.

### Lifecycle Hooks

- **RunHooks**: Monitor entire `Runner.run()` including cross-agent handoffs
- **AgentHooks**: Per-agent instance via `agent.hooks`
- Events: `on_agent_start`, `on_llm_end`, `on_tool_end`, `on_handoff`

### RunConfig

Global run settings without modifying agent definitions:
- Model/provider overrides
- Guardrails and input filtering
- Tracing and observability
- Tool approval behavior via `tool_error_formatter`
- `call_model_input_filter` hook for pre-call input modification

### Additional Features

- **MCP Integration**: Native Model Context Protocol server support; MCP tools operate identically to function tools
- **Human-in-the-Loop**: Integrated mechanisms for human involvement
- **Realtime Agents**: Voice capabilities with interruption detection, low-latency speech pipeline
- **Structured Output**: Pydantic models, dataclasses, or TypedDict via `output_type`
- **tool_use_behavior**: `"run_llm_again"` (default), `"stop_on_first_tool"`, or custom function

---

## 3. "A Practical Guide to Building Agents" (OpenAI)

**Sources:**
- https://openai.com/business/guides-and-resources/a-practical-guide-to-building-ai-agents/
- https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf
- https://developers.openai.com/tracks/building-agents/

### Definition

"An agent is an AI system that has instructions (what it should do), guardrails (what it should not do), and access to tools (what it can do) to take action on the user's behalf."

Key distinction: chatbots answer questions; agents execute tasks through external system connections.

### Four Composable Primitives

1. **Models** — Reasoning (o1, o3, o4-mini with adjustable reasoning_effort) vs. non-reasoning (gpt-4.1, gpt-5-mini, gpt-5-nano). Start with flagship, optimize based on evaluation. Different prompt strategies apply — don't simply swap model names.
2. **Tools** — Custom (function calling) and built-in (Web Search, File Search/RAG, Code Interpreter, Computer Use, Image Generation, MCP). Use built-in first; function calling for custom logic.
3. **State/Memory** — Conversation history management. Multi-agent systems share memory via `conversation_id`.
4. **Orchestration** — Multi-step workflow management.

### Two API Approaches

| | Responses API | Agents SDK |
|--|---------------|------------|
| **Level** | Low-level, flexible | High-level framework |
| **State** | Stateful by default | Automatic management |
| **Orchestration** | Manual logic | Automatic (loops, handoffs, guardrails) |
| **Trade-off** | More control | Faster development |

### Multi-Agent Networks

When to use multiple agents:
- Separate non-overlapping tasks
- Complex, lengthy instructions per task
- Task-specific tool sets that would confuse a single agent
- Tasks requiring different model capabilities

Pattern: Routing agent delegates to specialists; shared memory via `conversation_id`. Agent-as-tool approach where one agent becomes callable by another.

Benefits: Separation of concerns, parallelism, focused evaluation per agent.

### Model Selection Strategy

- **Reasoning models** (high reasoning_effort): Complex planning, math, code generation — trade latency/cost for reliability
- **Non-reasoning models**: Conversational interfaces, simpler tasks — prioritize speed
- **Pairing**: Fast model for chat, powerful model for complex reasoning
- **Practical approach**: Start with flagship models (gpt-4.1/gpt-5) with minimal reasoning effort, scale based on complexity

### Guardrails Best Practices

- **Input**: Simple (prompt constraints) to complex (multi-step filtering based on risk tolerance)
- **Output**: Structured outputs to enforce strict JSON schemas for programmatic responses
- **Production**: Monitor via tracing; Agents SDK provides automatic visibility into tool calls, agent selection, guardrail triggers

### Key Recommendation

Start with single-agent architectures. Migrate to multi-agent only when complexity warrants it. Plan for human intervention during early deployment. Agents should escalate to humans when encountering errors or high-risk decisions.

---

## 4. AgentKit

**Sources:**
- https://openai.com/index/introducing-agentkit/
- https://developers.openai.com/cookbook/examples/agentkit/agentkit_walkthrough
- https://codeconductor.ai/blog/openai-agentkit-how-to-build-and-ship-ai-agents/

### Overview

Launched October 2025 at OpenAI DevDay. A complete set of tools for developers and enterprises to build, deploy, and optimize agents. Builds on top of the Responses API and Agents SDK.

### Three Core Components

1. **Agent Builder** — Visual drag-and-drop canvas for composing multi-agent workflows. Includes branching logic, inline testing, version control, guardrail configuration, and preview runs. Workflows can be exported as Agents SDK code (Python or TypeScript).

2. **Connector Registry** — Centralized hub for integrating third-party APIs and tools (Google Drive, Slack, etc.). Makes workflows modular and extensible. Works alongside MCP servers for external context and actions.

3. **ChatKit** — Pre-built UI widgets for embedding agent interactions directly into applications. Supports custom widget rendering and JSON schema enforcement.

### Workflow Composition

- Agents access upstream node outputs via context variables: `{{workflow.input_as_text}}` and `{{input.output_text}}`
- Sequential agent composition with data flow through multi-step processes
- PII protection nodes can be inserted between agents as guardrails
- Supports multi-agent handoffs within visual workflows

### State Management

- **Conversations API**: Durable threads and replayable state
- **Connectors and MCP servers**: External context incorporation

### Evals Integration

Built-in evaluation for prompt optimization and trace grading for end-to-end workflow assessment.

### Production Considerations

AgentKit excels at prototyping but has noted gaps for production: deployment pipelines, rollback mechanisms, comprehensive observability, concurrency handling, cost optimization, and security validation require complementary engineering.

---

## 5. Codex CLI Multi-Agent Architecture

**Sources:**
- https://developers.openai.com/codex/concepts/multi-agents/
- https://developers.openai.com/codex/guides/agents-sdk/

### Core Architecture

Codex spawns specialized sub-agents in parallel while keeping the main conversation thread clean.

**Why multi-agent:**
- Separating concerns: noisy intermediate work (logs, tests, exploration) off the main thread
- Reducing context pollution: useful information not buried under intermediate details
- Returning summaries: sub-agents report distilled results, not raw output

**Agent threads**: Each spawned agent runs in its own CLI thread, inspectable via `/agent` command.

### Model Selection Strategy

- `gpt-5.3-codex` (primary reasoning): Code review, security analysis, multi-step implementation, ambiguous requirements
- `gpt-5.3-codex-spark` (speed-optimized): Exploration, read-heavy scanning, quick summarization

**Reasoning effort levels**: high (complex logic), medium (balanced default), low (straightforward/speed)

### Codex as MCP Server

Codex runs as an MCP server (`codex mcp-server`), exposing two tools:

1. **`codex`** — Initiates session. Params: `prompt`, `approval-policy`, `sandbox`, `model`, `config`, `cwd`
2. **`codex-reply`** — Continues session. Params: `prompt`, `threadId` (from previous response)

```python
async with MCPServerStdio(
    name="Codex CLI",
    params={"command": "npx", "args": ["-y", "codex", "mcp-server"]},
    client_session_timeout_seconds=360000,
) as codex_mcp_server:
    developer_agent = Agent(
        name="Developer",
        instructions="Expert in building games...",
        mcp_servers=[codex_mcp_server],
    )
```

### Orchestration Patterns

- **Sequential hand-offs**: PM verifies deliverables before advancing
- **Parallel execution**: Multiple agents work simultaneously on independent components
- **File gating**: Workflow progression requires confirmation of expected artifacts
- **Trace audit**: All prompts, tool calls, and hand-offs recorded

**Best practice**: Start with read-heavy parallel tasks. Exercise caution with write-heavy workflows (conflict risk).

---

## 6. Multi-Agent Portfolio Collaboration (Cookbook Example)

**Source:** https://developers.openai.com/cookbook/examples/agents_sdk/multi-agent-portfolio-collaboration/multi_agent_portfolio_collaboration

### Hub-and-Spoke Architecture

Central Portfolio Manager (PM) agent coordinates specialist agents. PM retains orchestration authority — specialists are invoked as callable tools, not handoffs.

Three specialist agents:
- Fundamental Analysis Agent — company financials and market position
- Macro Environment Agent — economic conditions and policy impacts
- Quantitative Analysis Agent — statistical patterns and risk metrics

### Agent-as-Tool Implementation

```python
def make_agent_tool(agent, name, description):
    @function_tool(name_override=name, description_override=description)
    async def agent_tool(input):
        return await specialist_analysis_func(agent, input)
    return agent_tool

Agent(
    name="Head Portfolio Manager Agent",
    instructions=load_prompt("pm_base.md"),
    model="gpt-4.1",
    tools=[fundamental_tool, macro_tool, quant_tool, memo_edit_tool],
    model_settings=ModelSettings(parallel_tool_calls=True)
)
```

### Communication Patterns

- **Parallel execution**: PM invokes all specialists simultaneously via `parallel_tool_calls=True`
- **Sequential integration**: PM synthesizes findings via memo editor tool
- **Centralized control**: PM retains authority for consistency and auditability

---

## Cross-Cutting Patterns for Mercury

### Orchestration Patterns (Ranked by Complexity)

1. **Single Agent** — One agent with tools. Start here.
2. **Agent-as-Tool (Hub-and-Spoke)** — Central agent invokes specialists as tools. Retains control. Good for structured workflows.
3. **Handoff (Decentralized)** — Agents transfer control to peers. Good for triage/routing. One-way delegation.
4. **Four-Agent Pipeline** — Triage → Clarification → Instruction → Execution. Good for research/complex queries.
5. **Multi-Agent Network** — Routing agent + specialists with shared memory via conversation_id.
6. **Visual Composition (AgentKit)** — Drag-and-drop workflow builder with code export.

### Memory Architecture

| Layer | Mechanism | Scope |
|-------|-----------|-------|
| Working | Message history in context | Single run |
| Sessions | SQLAlchemy/SQLite/Redis/Dapr | Cross-run persistence |
| conversation_id | OpenAI server-side | Cross-agent, cross-service |
| previous_response_id | OpenAI server-side | Lightweight turn chaining |
| nest_handoff_history | Transcript collapse | Cross-agent within run |
| Extended reasoning | RL-trained long chains (o3) | Within single agent |

### Agent-to-Agent Communication

| Mechanism | Control Flow | Context Sharing |
|-----------|-------------|-----------------|
| Handoffs | Transfers to peer | Full history (filterable) |
| Agent-as-Tool | Central retains control | Tool input/output only |
| MCP Server | External tool interface | Via tool params + threadId |
| Shared Session | Independent agents | Via conversation_id |
| Pipeline | Sequential enrichment | Accumulated context per stage |

### Guardrail Architecture

- **Input guardrails**: Run parallel (default) or blocking. Fail fast. Apply to first agent only.
- **Output guardrails**: Post-completion validation. Apply to final agent only.
- **Tool guardrails**: Per-invocation validation.
- **Tripwires**: Immediate halt on failure.
- **PII protection nodes**: Insertable between agents in AgentKit workflows.
- **Structured outputs**: JSON schema enforcement for programmatic reliability.

### Deep Research Patterns Worth Adopting

1. **Clarification before execution** — Don't start complex work without confirming intent
2. **Decomposition into subtasks** — Break complex queries into sub-questions
3. **Iterative refinement** — Progressive focusing, backtracking from dead ends
4. **Dual stopping logic** — Coverage-based (sufficiency) + budget-driven (hard limits)
5. **Citation/provenance** — Every factual claim linked to source
6. **Sub-agent summaries** — Return distilled results, not raw output

### Key Design Principles for Mercury

1. Start with a single agent; add complexity only when needed
2. Use agent-as-tool for centralized control; handoffs for delegation/triage
3. Memory is a first-class primitive — choose the right layer for each use case
4. Trace everything for debugging and evaluation
5. Match model capability to task complexity (reasoning vs. speed)
6. Separate read-heavy parallel work from write-heavy sequential work
7. Plan for human intervention during early deployment
8. Use structured outputs for programmatic reliability
9. Context should flow through pipelines, not be loaded all at once
10. AgentKit is good for prototyping; production needs additional infrastructure
