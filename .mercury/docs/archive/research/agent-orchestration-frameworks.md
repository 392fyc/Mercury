# Agent Orchestration Frameworks & Protocols Research

> Mercury project reference: multi-agent orchestration patterns, communication protocols, and architectural lessons.
> Researched: 2026-03-15, updated 2026-03-16

---

## Summary Matrix

| Framework | Orchestration Model | Communication Pattern | State Management | Language |
|-----------|--------------------|-----------------------|------------------|----------|
| **CrewAI** | Role-based crews (sequential/hierarchical) | Delegation + context passing | Task output chaining | Python |
| **AutoGen** | Conversation-driven GroupChat | Pub-sub topic broadcast | Chat history per agent | Python |
| **LangGraph** | Graph-based state machine (DAG) | Shared state object | Immutable versioned state | Python |
| **Mastra** | Workflow + agent composition | Tool/MCP integration | Built-in memory | TypeScript |
| **Agency Swarm** | Organizational hierarchy | Directional agent-to-agent | OpenAI thread-based | Python |
| **smolagents** | Code-first agent loop | Direct tool invocation (code) | Agent step history | Python |
| **A2A Protocol** | Client-remote task delegation | JSON-RPC over HTTP/SSE | Task lifecycle states | Protocol spec |
| **MCP** | Host-client-server | JSON-RPC 2.0 | Stateful sessions | Protocol spec |

---

## 1. CrewAI

**Repository**: https://github.com/crewAIInc/crewAI

### Architecture
CrewAI organizes agents into "Crews" -- collaborative teams where each agent has a defined **role**, **goal**, and **backstory**. Agents are equipped with tools and can delegate work to each other.

Two execution layers exist:
- **Crews**: Autonomous teams where agents have true agency -- they decide when to delegate, when to ask questions, and how to approach tasks.
- **Flows**: Event-driven pipelines for production workloads needing more predictability.

### Process Models

**Sequential Process**: Tasks execute in predefined order. Output of one task serves as context for the next. Developers can customize which outputs pass to later tasks using the `context` parameter.

**Hierarchical Process**: A manager agent (auto-created or custom) coordinates work. Tasks are NOT pre-assigned; the manager allocates tasks to agents based on capabilities, reviews outputs, and assesses completion. The manager handles planning, delegation, and validation throughout execution.

**Consensus Process**: Planned but not yet implemented. Will enable collaborative decision-making among agents using democratic principles.

### Agent Properties
- Core identity: `role`, `goal`, `backstory`
- Model config: `llm` (default GPT-4), optional `function_calling_llm`
- Execution control: `max_iter` (20), `max_execution_time`, `max_rpm`, `max_retry_limit` (2)
- Capabilities: `tools`, `knowledge_sources`, `allow_delegation`, `memory`
- Advanced: `reasoning`, `inject_date`, `respect_context_window`, custom templates

### Communication Pattern
- Context passing: output of one task feeds into the next
- Delegation: agents can autonomously delegate sub-tasks to other agents
- No direct message bus -- communication is mediated through the crew framework
- Supports both sync `kickoff()` and async `kickoff_async()`

### Mercury Lessons
- The role/goal/backstory pattern is a clean way to define agent identity and scope
- Hierarchical process with a manager agent is a proven pattern for dynamic task allocation
- The distinction between autonomous Crews and predictable Flows maps well to dev-time vs production concerns

---

## 2. AutoGen (Microsoft)

**Repository**: https://github.com/microsoft/autogen

### Architecture
AutoGen uses a conversation-driven multi-agent framework. Agents communicate through structured dialogues. AutoGen 0.4 (Jan 2025) introduced a re-designed layered architecture with modular components for memory, custom agents, and diverse workflows.

### GroupChat Design Pattern
The GroupChat implements a **publish-subscribe pattern** with three message types:

1. **GroupChatMessage**: Carries actual content (text/images) between participants
2. **RequestToSpeak**: Signals an agent that it is their turn
3. **Topic-based routing**: Each agent subscribes to both a shared group topic and an individual topic

**Flow**: User publishes GroupChatMessage to common topic -> GroupChatManager selects next speaker -> sends RequestToSpeak -> agent publishes response as GroupChatMessage -> repeats until termination.

### Speaker Selection Strategies
- **LLM-based**: Manager uses an LLM to analyze conversation history and match roles to context
- **Round-robin**: Simple rotation through agents
- **Manual/Random**: For specific use cases
- **Stateful tracking**: Manager maintains `_previous_participant_topic_type` to avoid consecutive turns by the same agent

### State Management
- Each agent preserves `_chat_history` as a list of LLMMessage objects
- Agents track their source through message metadata
- GroupChatManager tracks previous speakers and conversation flow

### Communication Patterns
- Two-agent chats (direct)
- Group discussions (broadcast via topic)
- Sequential conversations (chained)
- Nested patterns (sub-conversations)

### Mercury Lessons
- Pub-sub with topic-based routing is a scalable communication primitive
- The GroupChatManager as an LLM-powered orchestrator is powerful but creates a bottleneck
- Speaker selection strategies (LLM-based, round-robin, manual) offer a good taxonomy of agent scheduling approaches
- The event-driven runtime model enables flexible agent composition

---

## 3. LangGraph

**Repository**: Part of LangChain ecosystem -- https://www.langchain.com/langgraph

### Architecture
LangGraph models workflows as **directed graphs** where:
- **Nodes** = agents, functions, or decision points
- **Edges** = data flow paths between nodes
- A centralized **StateGraph** maintains workflow context

Before execution, graphs undergo compilation that validates connections, identifies cycles, and optimizes paths. Once compiled, the graph becomes immutable.

### State Management
Central to LangGraph. Uses **reducer-driven state schemas** with Python TypedDict and Annotated types. Instead of direct messaging, agents interact through a **centralized state object**. Updates create new immutable state versions rather than modifying existing ones (prevents race conditions, increases memory use).

### Orchestration Patterns
- **Conditional edges**: Route execution based on state evaluation
- **Scatter-gather**: Distribute tasks to multiple agents, consolidate results
- **Pipeline parallelism**: Process sequential stages concurrently
- **Subgraphs**: Group related agents into reusable components
- **Human-in-the-loop**: Workflow pauses for manual review via interrupt mechanisms

### Communication Model
Agents exchange information through **structured state updates** with typed schemas. No direct agent-to-agent messaging -- all coordination flows through shared state. This creates data consistency but can bottleneck when multiple agents simultaneously update.

### Key Strengths
- Durable execution: agents persist through failures and resume automatically
- Precise control over execution flow
- Comprehensive memory systems
- Runtime graph mutation for dynamic workflows

### Mercury Lessons
- Graph-based orchestration gives precise control over agent coordination
- Immutable state with reducers is a robust pattern for concurrent agent updates
- Subgraphs as reusable components map well to composable agent teams
- The compile-then-execute model provides safety guarantees
- Scatter-gather pattern is essential for parallel agent work

---

## 4. Mastra

**Repository**: https://github.com/mastra-ai/mastra

### Architecture
TypeScript-first framework from the team behind Gatsby. Key architectural components:

- **Agents**: Autonomous entities using LLMs and tools to solve open-ended tasks
- **Workflows**: Distinct from agents -- composition-based orchestration of agent actions
- **Tools**: Pluggable capabilities with MCP integration
- **Memory**: Built-in state persistence and context management
- **RAG**: Integrated retrieval-augmented generation for knowledge access
- **Evals**: Model-graded, rule-based, and statistical quality measurement

### Orchestration Model
- Agents and workflows are separate concepts that compose together
- Supports deployment as APIs or embedded within applications
- Framework-agnostic: works with Next.js, Express, Hono
- Serverless deployment: Vercel, Cloudflare, Netlify, or standalone Node.js

### Key Differentiators
- TypeScript native (most alternatives are Python)
- Model routing across 40+ providers through one interface
- Built-in observability and tracing
- Versioned datasets with JSON Schema validation
- MCP integration for standardized tool interfaces

### Mercury Lessons
- TypeScript-first approach is directly relevant to Mercury's stack
- Clean separation of agents vs workflows is a good architectural boundary
- MCP integration as a first-class citizen for tool communication
- The eval system (model-graded + rule-based + statistical) is worth studying for quality assurance

---

## 5. Agency Swarm

**Repository**: https://github.com/VRSEN/agency-swarm

### Architecture
Models multi-agent systems as real-world organizational structures. Built on top of OpenAI Agents SDK + Responses API with an additional orchestration layer.

### Organizational Model
- **Agencies**: Collections of agents working toward a common goal
- **Communication flows are directional**: agent on the left can initiate conversations with agent on the right (defined in agency chart)
- Customizable roles: CEO, Virtual Assistant, Developer, etc.
- Each agent has tailored instructions, tools, and capabilities

### Tool System
- Tools defined using `@function_tool` decorator (recommended) or by extending `BaseTool`
- Model support via LiteLLM: OpenAI, Anthropic, Google, Grok, Azure, OpenRouter

### Mercury Lessons
- The directional communication flow model (agency chart) is a simple but powerful way to define agent topology
- Mapping agent structures to real-world org charts is intuitive for enterprise use cases
- Extending an existing SDK (OpenAI Agents SDK) rather than building from scratch is pragmatic

---

## 6. smolagents (Hugging Face)

**Repository**: https://github.com/huggingface/smolagents

### Architecture
Minimalist design -- core agent logic fits in ~1,000 lines of code.

### Agent Types
- **CodeAgent**: Writes actions as Python code (function nesting, loops, conditionals). Code is executed directly without JSON-to-tool-call translation. Sandboxed via Modal, Blaxel, E2B, Docker, or Pyodide+Deno.
- **ToolCallingAgent**: Standard JSON/text-based tool calling for traditional scenarios.

### Design Philosophy
- Reasoning + Acting (ReAct) framework: LLM reasons about task -> selects tools -> executes actions -> observes results -> iterates
- Model-agnostic: HF Inference, OpenAI, Anthropic, LiteLLM, local Transformers/Ollama
- Modality-agnostic: text, vision, video, audio
- Tool-agnostic: MCP servers, LangChain tools, Hub Spaces

### Multi-Agent Support
Supports Multi-Agent Systems (MAS) where multiple specialized agents interact and collaborate. Agents can be shared/loaded via Hugging Face Hub.

### Mercury Lessons
- Code-as-action (CodeAgent) is more composable than JSON tool calls -- natural loops, conditionals, function nesting
- The ~1000-line core proves that agent orchestration does not need to be complex
- MCP server integration as a tool source is a clean abstraction
- Sandboxed execution is essential for code-generating agents

---

## 7. A2A Protocol (Google Agent2Agent)

**Specification**: https://github.com/google/A2A | https://a2aprotocol.ai/

### Architecture
Open standard for inter-agent communication. Launched April 2025 by Google, now under Linux Foundation governance. 150+ supporting organizations.

### Core Concepts

**Agent Cards**: JSON metadata files containing agent name, description, version, service endpoint URL, supported modalities, authentication requirements. Function as capability advertisements enabling discovery.

**Task Lifecycle**: Five states: `submitted` -> `working` -> `input-required` -> `completed` / `failed`. Each task has a unique identifier and supports multi-turn processing.

**Message Parts**: Hierarchical structure:
- `TextPart`: Textual content
- `FilePart`: File objects
- `DataPart`: Structured JSON data
- Artifacts consist of parts and can be incrementally streamed

### Communication Architecture
1. **Discovery**: Client identifies remote agents via Agent Cards
2. **Authentication**: OpenAPI-aligned schemes (OAuth 2.0, etc.)
3. **Communication**: HTTPS + JSON-RPC 2.0

**Async patterns**: Push notifications via secure client-supplied webhooks for long-running tasks. Real-time streaming via Server-Sent Events (SSE).

### Protocol Details
- Built on HTTP, SSE, JSON-RPC (established standards)
- Enterprise-grade security with auth parity to OpenAPI
- Version 0.3 added gRPC support and signed security cards
- Complementary to MCP: A2A handles agent-to-agent, MCP handles agent-to-tool

### Mercury Lessons
- Agent Cards as JSON capability manifests is a clean discovery mechanism
- The 5-state task lifecycle (submitted/working/input-required/completed/failed) is a practical state machine for async agent work
- Building on HTTP + JSON-RPC + SSE means no custom transport needed
- A2A (agent-to-agent) + MCP (agent-to-tool) is the emerging standard stack
- Push notifications + SSE for long-running tasks is production-ready async

---

## 8. MCP (Model Context Protocol)

**Specification**: https://modelcontextprotocol.io/specification/2025-11-25

### Architecture
Open protocol by Anthropic (Nov 2024), now under Linux Foundation (Agentic AI Foundation). Three roles:

- **Hosts**: LLM applications that initiate connections
- **Clients**: Connectors within the host application
- **Servers**: Services that provide context and capabilities

### Protocol Details
- **Transport**: JSON-RPC 2.0 messages over stateful connections
- **Capability negotiation**: Server and client negotiate features at connection time

### Server Features (exposed to clients)
- **Resources**: Context and data for user or AI model
- **Prompts**: Templated messages and workflows
- **Tools**: Functions for the AI model to execute

### Client Features (exposed to servers)
- **Sampling**: Server-initiated agentic behaviors and recursive LLM interactions
- **Roots**: Server-initiated inquiries into URI/filesystem boundaries
- **Elicitation**: Server-initiated requests for additional user information

### Additional Utilities
- Configuration, progress tracking, cancellation, error reporting, logging

### Security Principles
- Explicit user consent for all data access and operations
- User control over data sharing and actions
- Tools treated as arbitrary code execution (untrusted descriptions)
- LLM sampling requires explicit user approval

### 2026 Roadmap
- **Tasks primitive** (SEP-1686): Shipped experimentally, adding retry semantics and expiry policies
- **Transport scalability**: Evolving Streamable HTTP for stateless horizontal scaling, `.well-known` metadata for capability discovery without live connections
- **Agent-to-agent**: MCP Servers evolving to act as agents themselves (e.g., "Travel Agent" server negotiating with "Booking Agent" server)
- **Governance**: Contributor ladder, Working Group-driven SEP review
- **Enterprise**: Audit trails, SSO integration, configuration portability

### Mercury Lessons
- MCP's host/client/server model is the de facto standard for agent-tool communication
- The capability negotiation pattern at connection time is elegant and extensible
- Sampling (server-initiated LLM calls) enables recursive agentic patterns
- The 2026 roadmap toward agent-to-agent communication means MCP will converge with A2A territory
- Building on MCP now provides future-proof agent-tool integration

---

## 9. "Memory in the Age of AI Agents" (Survey Paper)

**URL**: https://arxiv.org/abs/2512.13564
**Authors**: Yuyang Hu, Shichun Liu, Yanwei Yue, Guibin Zhang, Boyang Liu, Fangyi Zhu, et al. (47 authors)
**Published**: December 2025

### Key Concepts

This comprehensive survey establishes a unified taxonomy for agent memory research, arguing that traditional short-term/long-term memory distinctions are insufficient for modern agent systems. Memory is positioned as "a first-class primitive" for designing advanced agentic intelligence.

### Three-Dimensional Taxonomy

**Forms of Agent Memory:**
- **Token-level memory**: Information stored within the context window as tokens
- **Parametric memory**: Knowledge encoded in model weights through fine-tuning
- **Latent memory**: Compressed representations in latent space (e.g., memory tokens, hidden states)

**Functions of Agent Memory:**
- **Factual memory**: Storage and retrieval of knowledge and facts
- **Experiential memory**: Learning from past interactions, successes, and failures
- **Working memory**: Active processing buffer for current task execution

**Dynamics of Agent Memory:**
- **Formation**: How memories are created and encoded
- **Evolution**: How memories change, consolidate, or decay over time
- **Retrieval**: How memories are accessed when needed

### Emerging Research Frontiers
- Memory automation (self-managing memory systems)
- Reinforcement learning integration for memory optimization
- Multimodal memory (beyond text to images, audio, video)
- Multi-agent shared memory and coordination
- Trustworthiness and privacy in memory systems

### Relevance to Multi-Agent Orchestration
The paper distinguishes agent memory from RAG and context engineering. For multi-agent systems, the key insight is that agents need shared experiential memory -- learning from each other's successes and failures -- not just shared factual knowledge. Multi-agent memory coordination is identified as an open frontier.

### What Mercury Should Adopt
- **Three-layer memory architecture**: factual (knowledge base), experiential (learning from runs), working (active context)
- **Memory as a first-class primitive**: not an afterthought bolted onto agents
- **Experiential memory across agents**: allow agents to learn from each other's trajectories
- **Memory lifecycle management**: explicit formation, evolution, and retrieval policies

---

## 10. "Dynamic Context Discovery" (Cursor Blog)

**URL**: https://cursor.com/blog/dynamic-context-discovery
**Author**: Jediah Katz (Cursor)
**Published**: January 6, 2026

### Key Concepts

The fundamental principle: provide agents with fewer details up front, enabling them to discover relevant context autonomously rather than including everything statically in prompts. This directly counters the intuition that "more context = better results."

### Five Implementation Techniques

1. **Long tool responses as files**: Instead of truncating extensive command outputs, write results to files. Agents use `tail` and selective reads to access needed sections. Prevents data loss while managing context.

2. **Chat history during summarization**: When context windows fill, the system triggers summarization. The agent can reference historical chat files to recover lost details, improving summary quality beyond simple compression.

3. **Agent Skills open standard**: Skills (domain-specific capability files) include names and descriptions as static context. Agents dynamically discover relevant skills using grep and semantic search tools at runtime.

4. **MCP tool descriptions in folders**: Rather than always including verbose MCP tool descriptions in the prompt, sync them to directories. Agents receive minimal static context (tool names only), then retrieve full descriptions on demand.

5. **Integrated terminal sessions as files**: Terminal outputs sync to the filesystem. Agents can grep specific outputs, particularly useful for long-running server logs.

### Performance Metrics
- A/B test for MCP tool integration: **reduced total agent tokens by 46.9%** (statistically significant)
- Variance depends on number of installed MCPs -- more MCPs = greater savings

### Core Architectural Insight
Files serve as a "simple and powerful primitive" for LLM-based agent tools. They offer a safer, more flexible design than adding new abstractions. The filesystem is the universal interface between agent and context.

### Relevance to Multi-Agent Orchestration
Dynamic context discovery applies directly to multi-agent systems where each agent has different context needs. Rather than broadcasting all context to all agents, let each agent pull what it needs. This reduces token waste and improves reasoning quality by reducing noise.

### What Mercury Should Adopt
- **Lazy context loading**: agents discover context at runtime rather than receiving everything up front
- **Filesystem as context bus**: use files as the interchange format between agents and tools
- **Minimal static context**: only include names/summaries; agents fetch full details on demand
- **Context compaction during summarization**: maintain access to history files during compression
- **MCP tool description foldering**: sync tool schemas to directories for on-demand discovery

---

## 11. "Building for Trillions of Agents" (Aaron Levie / Exponential View)

**URLs**:
- https://x.com/levie/status/2030714592238956960 (Aaron Levie, X post)
- https://www.latent.space/p/box (Latent Space podcast: "Every Agent Needs a Box")
- https://www.exponentialview.co/p/entering-the-trillion-agent-economy (Azeem Azhar, Rohit Krishnan, Chantal Smith)

**Authors**: Aaron Levie (CEO, Box), Azeem Azhar (Exponential View), Rohit Krishnan (Strange Loop Canon)

### Key Concepts

**Scale inevitability**: At current trajectories, a trillion agents emerges rapidly. Each person may use 200+ personal agents; with 8 billion humans, scale becomes enormous. Enterprises will have 100x-1000x more agents than people.

**Agents as primary software users**: Software must be designed and built specifically for agents. Everything must become API-first. If a feature cannot be exposed through an API, CLI, or MCP server, it effectively does not exist.

**Agent behavioral economics ("Homo agenticus")**: Agents display surprising personality traits -- risk-averse about spending, prefer building over purchasing, reluctant to transact. Systems must be designed around these behavioral tendencies rather than assuming frictionless rational actors.

**Three economic invariants for agent economies**:
1. **Medium of exchange** -- price signals enabling frictionless transactions
2. **Identity** -- knowing transaction partners
3. **Verifiability** -- record-keeping of agreements

**Sandbox-first design ("Every Agent Needs a Box")**:
- Persistent storage for agent memory, specifications, and work artifacts
- Scoped data access preventing uncontrolled information exposure
- Collaborative boundaries enabling human oversight of agent workspaces

**Context engineering as the real bottleneck**: With ~60,000 usable tokens against millions of pages of enterprise documentation, retrieval precision is existential. Search systems must identify precisely relevant documents. Models must recognize when searching becomes futile rather than guessing.

**Read vs. write asymmetry**: Reading enterprise data (navigating permissions, finding correct information) is harder than generation. Write workflows mostly synthesize new content (where models excel).

**Why coding agents succeeded first**: Engineers get broad codebase access from day one; text-in/text-out aligns with model capabilities; lab researchers are daily users creating rapid feedback loops.

### Relevance to Multi-Agent Orchestration
The trillion-agent framing highlights infrastructure requirements: identity management, sandboxed execution, API-first design, and economic primitives for agent-to-agent transactions. These are foundational concerns for any orchestration system operating at scale.

### What Mercury Should Adopt
- **API-first / MCP-first design**: every capability exposed programmatically
- **Agent identity and access control**: scoped permissions, creator liability model
- **Sandboxed workspaces per agent**: isolated persistent storage with collaborative boundaries
- **Context precision over context volume**: retrieval quality matters more than context window size
- **Design for agent-as-user**: interfaces optimized for programmatic consumption, not just human UX

---

## 12. "How Coding Agents Are Reshaping Engineering, Product and Design" (LangChain Blog)

**URL**: https://blog.langchain.com/how-coding-agents-are-reshaping-engineering-product-and-design/
**Author**: Harrison Chase (CEO, LangChain)
**Published**: March 10, 2026

### Key Concepts

**Death of the traditional PRD process**: The sequential workflow (idea -> PRD -> design mock -> code implementation) becomes obsolete. Coding agents enable rapid prototyping, eliminating the waterfall. However, written product requirement documentation remains essential alongside prototypes for communicating intent during review phases.

**Shift from implementation to review**: "The cost of creating some initial version of the code is so cheap" that prototype volume increases dramatically. Engineering, Product, and Design (EPD) professionals transition from builders to gatekeepers, ensuring generated code meets three standards: architectural soundness, product-market fit, and usability.

**Rise of generalists**: Professionals combining product thinking, design intuition, and technical capability gain disproportionate advantage. One person doing all of product, design, and engineering moves faster than a team of three because they eliminate communication overhead.

**Prompts as evolved PRDs**: Structured, versioned prompts serve as the new specification format, accompanying prototypes as documentation of intent.

### Specific Recommendations
- Engineers: develop exceptional system design skills OR strengthen product/design capabilities
- PMs and designers: adopt coding agents as mandatory professional development
- All roles: cultivate "product sense" -- the discernment to direct agents toward valuable solutions

### Relevance to Multi-Agent Orchestration
This article frames the human side of agent adoption. For Mercury, it reinforces that orchestration systems must support rapid prototyping workflows, versioned prompt management, and review-centric interfaces rather than implementation-centric ones.

### What Mercury Should Adopt
- **Review-oriented workflows**: optimize for reviewing and iterating on agent output, not just generating it
- **Prompt versioning**: treat prompts as first-class artifacts with version history
- **Generalist-friendly interfaces**: reduce the barrier for non-specialists to direct agent work
- **Prototype-first development**: agents generate working prototypes; humans review and refine

---

## 13. Langfuse (Agent Observability Platform)

**URL**: https://langfuse.com
**Repository**: https://github.com/langfuse/langfuse
**Type**: Open source LLM engineering platform (YC W23)

### Architecture & Approach

Langfuse implements **application tracing** as its primary observability mechanism, built on **OpenTelemetry (OTEL)** as its foundation. This aligns with industry convergence toward OTEL and prevents vendor lock-in.

### Tracing Data Model

Three-level hierarchy:
- **Sessions**: Group related traces (e.g., multi-turn conversations)
- **Traces**: Single request/operation containers (e.g., one user question -> bot response)
- **Observations**: Individual steps within traces (LLM calls, tool executions, retrieval steps)

Observations can be nested, creating tree-like structures for modeling complex agent workflows. Each observation captures timing, inputs, outputs, and metadata. Traces propagate context attributes (`user_id`, `session_id`, `tags`, `metadata`, environment, release version) to all contained observations.

### Agent-Specific Observability

Langfuse captures the complete execution trajectory of agents:
- **Planning decisions**: how agents decompose tasks into steps
- **Tool invocations**: external API calls, retrieval operations, code execution
- **Reasoning chains**: intermediate LLM outputs and decisions
- **State transitions**: movement through multi-agent workflows

For multi-agent architectures, Langfuse tracks task routing through different states and interactions between specialized agents.

### Three-Tier Evaluation Framework

1. **Black-Box (Final Response)**: Assesses only input and final output, ignoring intermediate steps
2. **Glass-Box (Trajectory)**: Evaluates complete sequences of tool calls and reasoning paths against expected behaviors
3. **White-Box (Single Step)**: Examines individual operations for granular feedback

These progress through three phases:
- **Development**: Manual tracing and inspection
- **Online**: User feedback collection and LLM-as-a-Judge scoring on live traffic
- **Offline**: Benchmark datasets for regression testing

### Critical Metrics for Agents
- **Cost tracking**: per-trace attribution (agents chain multiple LLM/API calls unpredictably)
- **Latency**: response time across multi-step executions
- **Error rates**: failure detection in intermediate steps
- **Accuracy**: output quality versus expected results

### Integration
- OpenTelemetry native
- SDKs for Python and JavaScript
- Integrations with LangGraph, Pydantic AI, CrewAI, OpenAI Agents SDK
- Async data sending (local queuing + batch flushing) to prevent latency impact
- Self-hostable

### Relevance to Multi-Agent Orchestration
Langfuse provides the observability layer that any production multi-agent system needs. Its nested observation model maps naturally to agent workflows. The three-tier evaluation framework (black-box, glass-box, white-box) gives different granularity levels for debugging and quality assurance.

### What Mercury Should Adopt
- **OpenTelemetry-based tracing**: align with the industry standard for observability
- **Nested trace model**: traces -> observations (nested) maps to orchestrator -> agents -> tools
- **Three-tier evaluation**: black-box for end-to-end quality, glass-box for trajectory debugging, white-box for step-level analysis
- **Cost tracking per trace**: essential when agents make unpredictable numbers of LLM calls
- **Async telemetry**: background data sending to avoid impacting agent latency
- **LLM-as-a-Judge**: automated evaluation of agent outputs alongside human review

---

## 14. Cursor Technical Architecture (Agent Engineering)

**URLs**:
- https://cursor.com/blog/agent-best-practices (Lee Robinson, January 9, 2026)
- https://cursor.com/blog/dynamic-context-discovery (Jediah Katz, January 6, 2026)
- https://blog.bytebytego.com/p/how-cursor-shipped-its-coding-agent (ByteByteGo analysis)

### System Architecture

Cursor's production coding agent is composed of interconnected components:
- **Router**: Dynamically analyzes request complexity to select the optimal model ("Auto" mode)
- **LLM (Agentic Coding Model)**: Specialized model trained on action trajectories showing "how and when to use available tools"
- **Tools**: 10+ tools covering codebase searching, file operations, terminal commands
- **Context Retrieval**: Pulls relevant code snippets without overflowing context windows
- **Orchestrator**: Control loop executing tool calls and rebuilding working context iteratively
- **Sandbox**: Isolated execution environment with strict guardrails preventing destructive commands

### Agent Harness Design

The harness orchestrates three elements:
1. **Instructions** (system prompts) -- tuned per frontier model based on internal evaluations and benchmarks
2. **Tools** (file editing, search, terminal execution) -- different models respond differently to the same tool definitions
3. **User messages** (prompts and follow-ups)

Key insight: "Different models respond differently to the same prompts." Cursor tunes instructions and tools specifically for each frontier model.

### Composer Model (Custom MoE)

Cursor created **Composer**, a Mixture of Experts (MoE) model trained specifically for agentic behavior:
- Trained on "trajectories, sequences of actions" (not just text data or static code)
- Trained within a dynamic IDE that mirrors production conditions
- Edit operations emphasized heavily using edit trajectory triples: `(original_code, edit_command, final_code)`
- Custom MXFP8 quantization kernels achieve 3.5x speedup for MoE layers
- Trained on "tens of thousands of GPUs"

### Three Critical Production Challenges

**1. The "Diff Problem"**: General models struggle converting logical edits into valid code patches.
- Solution: Training on edit trajectory data with high-volume examples
- "Prompting alone is not enough for reliable tool calling inside long loops"

**2. Compounded Latency**: Agent loops amplify delays across multiple iterations. Three mitigations:
- **Mixture of Experts**: Routes each token to specialized experts, activating only a few per token
- **Speculative Decoding**: Smaller draft model proposes tokens; larger model verifies quickly. Code's predictable structure makes this effective.
- **Context Compaction**: Summarizes working state, retaining "stable signals like failing test names, error types, and key stack frames" rather than full logs. Improves both latency and quality.

**3. Sandboxing at Scale**: Two challenges:
- Sandbox creation exceeded model inference time (provisioning bottleneck)
- Training required hundreds of thousands of concurrent sandboxed environments
- Solution: Rewrote VM scheduler for bursty demand; treat sandboxes as core serving infrastructure

### Multi-Model Approach
Cursor supports running multiple models simultaneously against the same prompt, comparing results side-by-side. This multi-model ensemble leverages unique strengths of different AI systems (Composer's speed, Claude's reasoning, GPT's versatility).

### Agent Best Practices (from Cursor's blog)

- **Plan before coding**: Use Plan Mode to research codebases and create implementation plans before writing code
- **Dynamic context management**: Agent uses search tools to locate context rather than manual file tagging
- **Rules and Skills**: Rules provide persistent project-specific instructions; Skills enable dynamic capabilities
- **New conversations per task**: Start fresh when switching tasks; continue when iterating on same feature
- **Test-driven development**: Write tests, then let agents iterate toward passing

### Relevance to Multi-Agent Orchestration
Cursor demonstrates that production agent systems require purpose-built infrastructure: custom models trained on trajectories, per-model prompt tuning, latency optimization through MoE and speculative decoding, and sandbox infrastructure treated as core serving capacity. These are the real engineering challenges of shipping agents, not the orchestration logic itself.

### What Mercury Should Adopt
- **Per-model harness tuning**: different instructions and tool definitions for different LLMs
- **Context compaction**: summarize working state to stable signals, not full logs
- **Speculative decoding pattern**: use fast models for predictable steps, frontier models for complex reasoning
- **Sandbox as infrastructure**: treat isolated execution environments as core capacity, not an afterthought
- **Dynamic context discovery**: lazy loading over static context packing (see section 10)
- **Edit trajectory training**: if training custom models, use action trajectories not just text data
- **Speed as product feature**: "Latency shapes daily usage" -- route simpler steps to fast models

---

## Cross-Cutting Patterns for Mercury

### 1. Orchestration Models (pick based on use case)
| Pattern | Best For | Example |
|---------|----------|---------|
| Sequential pipeline | Predictable multi-step workflows | CrewAI Sequential, LangGraph linear |
| Hierarchical manager | Dynamic task allocation | CrewAI Hierarchical, Agency Swarm CEO |
| Graph state machine | Complex branching/looping | LangGraph DAG |
| Conversation-driven | Collaborative reasoning | AutoGen GroupChat |
| Code-as-action | Composable tool use | smolagents CodeAgent |

### 2. Communication Patterns
| Pattern | Frameworks | Trade-offs |
|---------|-----------|------------|
| Shared state object | LangGraph | Consistent but bottleneck risk |
| Pub-sub / topic broadcast | AutoGen | Scalable but needs ordering |
| Direct delegation | CrewAI | Simple but tightly coupled |
| Directional flows | Agency Swarm | Explicit topology, less flexible |
| Protocol-based (HTTP/JSON-RPC) | A2A, MCP | Decoupled, cross-framework |

### 3. State Management Approaches
| Approach | Used By | Characteristics |
|----------|---------|-----------------|
| Task output chaining | CrewAI | Simple, linear |
| Chat history per agent | AutoGen | Natural for conversations |
| Immutable versioned state | LangGraph | Safe concurrency, memory-heavy |
| Task lifecycle FSM | A2A | Clean async model |
| Stateful sessions | MCP | Connection-scoped |

### 4. Memory Architecture (from survey + Cursor)
| Layer | Function | Implementation |
|-------|----------|----------------|
| Factual memory | Knowledge storage | RAG, knowledge bases |
| Experiential memory | Learning from runs | Trajectory logging, success/failure patterns |
| Working memory | Active context | Dynamic context discovery, context compaction |

### 5. Observability Stack (from Langfuse + Cursor)
| Layer | What to Track | Approach |
|-------|--------------|----------|
| Traces | End-to-end request flow | OpenTelemetry, nested observations |
| Evaluations | Output quality | Three-tier: black-box, glass-box, white-box |
| Cost | Token/API spend per trace | Per-trace attribution with alerting |
| Latency | Multi-step execution time | Per-step timing with compaction metrics |

### 6. Key Architectural Decisions for Mercury

1. **Protocol layer**: Adopt MCP for agent-tool communication and A2A for agent-to-agent as the emerging standard stack
2. **Orchestration**: Support multiple models (sequential, hierarchical, graph) since different tasks need different patterns
3. **Communication**: Use a shared state bus with topic routing (combines LangGraph's state consistency with AutoGen's pub-sub flexibility)
4. **Agent identity**: CrewAI's role/goal/backstory pattern is proven and intuitive
5. **Code-as-action**: smolagents proves that code generation is more composable than JSON tool calls
6. **TypeScript-first**: Mastra validates that a TS-native agent framework is viable and growing
7. **Memory as first-class primitive**: Three-layer architecture (factual, experiential, working) per the survey paper
8. **Dynamic context discovery**: Lazy context loading over static packing (Cursor's 46.9% token reduction)
9. **Per-model harness tuning**: Different models need different prompts and tool definitions (Cursor)
10. **OpenTelemetry observability**: Nested traces with three-tier evaluation (Langfuse)
11. **API-first / agent-as-user design**: Every capability exposed programmatically (Levie)
12. **Review-oriented workflows**: Optimize for reviewing agent output, not just generating it (Chase)
13. **Sandbox infrastructure**: Treat isolated execution as core serving capacity (Cursor)
14. **Cost tracking per trace**: Essential for unpredictable multi-step agent execution (Langfuse)

---

## Sources

### Frameworks & Protocols
- [CrewAI Agents Documentation](https://docs.crewai.com/en/concepts/agents)
- [CrewAI Processes Documentation](https://docs.crewai.com/en/concepts/processes)
- [CrewAI GitHub](https://github.com/crewAIInc/crewAI)
- [CrewAI on AWS Prescriptive Guidance](https://docs.aws.amazon.com/prescriptive-guidance/latest/agentic-ai-frameworks/crewai.html)
- [AutoGen GitHub](https://github.com/microsoft/autogen)
- [AutoGen GroupChat Design Pattern](https://microsoft.github.io/autogen/stable//user-guide/core-user-guide/design-patterns/group-chat.html)
- [AutoGen Multi-Agent Conversation Framework](https://microsoft.github.io/autogen/0.2/docs/Use-Cases/agent_chat/)
- [LangGraph Multi-Agent Orchestration Guide](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langgraph-multi-agent-orchestration/langgraph-multi-agent-orchestration-complete-framework-guide-architecture-analysis-2025)
- [LangGraph Official Site](https://www.langchain.com/langgraph)
- [Mastra Official Site](https://mastra.ai/)
- [Mastra GitHub](https://github.com/mastra-ai/mastra)
- [Agency Swarm GitHub](https://github.com/VRSEN/agency-swarm)
- [smolagents Documentation](https://huggingface.co/docs/smolagents/en/index)
- [smolagents GitHub](https://github.com/huggingface/smolagents)
- [Google A2A Protocol Announcement](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/)
- [A2A Protocol (IBM Overview)](https://www.ibm.com/think/topics/agent2agent-protocol)
- [A2A Protocol Site](https://a2aprotocol.ai/)
- [Linux Foundation A2A Project](https://www.linuxfoundation.org/press/linux-foundation-launches-the-agent2agent-protocol-project-to-enable-secure-intelligent-communication-between-ai-agents)
- [MCP Specification (2025-11-25)](https://modelcontextprotocol.io/specification/2025-11-25)
- [2026 MCP Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/)
- [Open Source AI Agent Frameworks Compared (2026)](https://openagents.org/blog/posts/2026-02-23-open-source-ai-agent-frameworks-compared)

### Articles & Research (added 2026-03-16)
- [Memory in the Age of AI Agents (arXiv)](https://arxiv.org/abs/2512.13564)
- [Agent Memory Paper List (GitHub)](https://github.com/Shichun-Liu/Agent-Memory-Paper-List)
- [Dynamic Context Discovery (Cursor Blog)](https://cursor.com/blog/dynamic-context-discovery)
- [Best Practices for Coding with Agents (Cursor Blog)](https://cursor.com/blog/agent-best-practices)
- [How Cursor Shipped its Coding Agent (ByteByteGo)](https://blog.bytebytego.com/p/how-cursor-shipped-its-coding-agent)
- [Building for Trillions of Agents (Aaron Levie, X)](https://x.com/levie/status/2030714592238956960)
- [Every Agent Needs a Box (Latent Space / Aaron Levie)](https://www.latent.space/p/box)
- [Entering the Trillion-Agent Economy (Exponential View)](https://www.exponentialview.co/p/entering-the-trillion-agent-economy)
- [How Coding Agents Are Reshaping Engineering, Product and Design (LangChain Blog)](https://blog.langchain.com/how-coding-agents-are-reshaping-engineering-product-and-design/)
- [Langfuse: Open Source LLM Engineering Platform](https://langfuse.com/)
- [Langfuse Agent Observability Blog](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)
- [Langfuse Observability Overview](https://langfuse.com/docs/observability/overview)
- [Langfuse Tracing Data Model](https://langfuse.com/docs/observability/data-model)
- [Langfuse Evaluation Overview](https://langfuse.com/docs/evaluation/overview)
- [Langfuse GitHub](https://github.com/langfuse/langfuse)
