# Multi-Agent Orchestration Repository Research

> Mercury project reference: architecture patterns, bus management, orchestration UI, memory, scheduling
> Research date: 2026-03-15

---

## 1. golutra/golutra — Multi-Agent Terminal Orchestrator

**GitHub:** https://github.com/golutra/golutra
**Stars:** ~1,700 | **Forks:** 177 | **License:** BSL 1.1

### Architecture Summary

Golutra is a Tauri-based desktop app that wraps existing CLI agents (Claude Code, Gemini CLI, Codex CLI, OpenCode, Qwen Code, OpenClaw) into a unified orchestration GUI. It does NOT replace agents — it manages them as child processes with PTY terminals.

### Tech Stack
- **Frontend:** Vue 3 + TypeScript + Tailwind CSS
- **Backend:** Rust (via Tauri)
- **Desktop:** Tauri 2.x (Windows/macOS)
- **Package Manager:** pnpm monorepo
- **Language split:** Rust 35.6%, TypeScript 34.7%, Vue 28.5%

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Bus/Message Routing** | Tauri IPC between Vue frontend and Rust backend; PTY streams from child processes piped to UI |
| **UI Management** | Agent avatar panels with click-to-inspect logs; prompt injection into terminal streams; stealth terminal with context-aware autocomplete |
| **Memory/Context** | Session-level context persistence (no cross-agent memory yet); roadmap includes "deep memory layer" for shared long-horizon memory |
| **Scheduling/Orchestration** | Parallel multi-agent execution; planned "OpenClaw" commander layer for automatic agent creation, role assignment, and structured collaboration channels |

### What Mercury Can Learn
- **Tauri + Rust + Vue is a proven stack** for exactly this problem domain. Golutra validates Mercury's architecture choice.
- **PTY wrapping of CLI agents** is the pragmatic approach — don't rebuild agents, orchestrate them.
- **Agent avatar UI with log inspection and prompt injection** is a strong UX pattern for multi-agent management.
- **The OpenClaw "commander layer" concept** aligns with Mercury's orchestrator role — a central coordinator that creates agents and assigns roles based on task complexity.

### Limitations
- Early stage (10 commits at time of research), sanitized open source.
- BSL 1.1 license — not fully open source.
- No cross-agent memory implemented yet (only planned).
- No detailed documentation of Rust IPC architecture publicly available.
- macOS/Windows only (no Linux).

---

## 2. mem0ai/mem0 — Memory Layer for AI Agents

**GitHub:** https://github.com/mem0ai/mem0
**Stars:** ~49,900 | **Forks:** 5,600 | **License:** Apache 2.0

### Architecture Summary

Mem0 is a standalone memory service that provides persistent, hierarchical memory for AI agents. It uses vector stores for semantic retrieval and supports multi-level memory scoping (user, session, agent).

### Tech Stack
- **Language:** Python 63.7%, TypeScript 24.0%
- **Vector Stores:** Pluggable backends (multiple supported)
- **Graph Memory:** Advanced feature for relationship-based memory
- **LLM:** Default gpt-4.1-nano for memory extraction; supports multiple providers
- **Deployment:** pip/npm install (self-hosted) or managed platform

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Memory Levels** | User memory (cross-session), Session memory (conversation-scoped), Agent memory (agent-specific state) |
| **Storage** | Vector-based semantic search with pluggable backends; graph memory for relationships |
| **API** | `memory.add(messages, user_id)` / `memory.search(query, user_id, limit=3)` — single-line integration |
| **Retrieval** | Semantic search on queries, ranked by relevance, integrated into system prompts |
| **Integration** | LangGraph, CrewAI, Vercel AI SDK, MCP protocol, browser extensions |

### Performance
- +26% accuracy over OpenAI Memory (LOCOMO benchmark)
- 91% faster than full-context approaches
- 90% reduction in token usage vs full-context

### What Mercury Can Learn
- **Three-tier memory model (user/session/agent)** is directly applicable. Mercury needs:
  - User memory: developer preferences, common patterns
  - Session memory: current task context shared across agents
  - Agent memory: per-agent state (what Claude knows vs what Codex knows)
- **Vector search for memory retrieval** — when agents need context from past sessions.
- **Single-line API design** — Mercury's memory layer should be this simple to integrate.
- **MCP integration** — mem0 can serve as an MCP server, meaning Mercury agents could use it natively.

### Limitations
- Primarily designed for chatbot/assistant use cases, not multi-agent orchestration specifically.
- No built-in agent-to-agent memory sharing protocol.
- Managed platform has better features than self-hosted.
- Memory extraction quality depends on underlying LLM.

---

## 3. Fission-AI/OpenSpec — Spec-Driven Development Framework

**GitHub:** https://github.com/Fission-AI/OpenSpec
**Stars:** ~30,700 | **Forks:** 2,000 | **License:** (check repo)

### Architecture Summary

OpenSpec implements spec-driven development (SDD) where specifications are written BEFORE code. It uses Markdown-based artifacts (proposals, specs, designs, task lists) stored alongside source code, enabling any AI agent to implement from shared specs.

### Tech Stack
- **Language:** TypeScript 98.7%
- **Runtime:** Node.js 20.19.0+
- **Package Manager:** pnpm
- **Testing:** Vitest
- **Distribution:** npm as `@fission-ai/openspec`

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Orchestration** | Agent-agnostic spec layer — supports 20+ AI tools (Claude, Copilot, Cursor, Cline, etc.) |
| **Workflow** | `/opsx:propose` (create spec) -> `/opsx:apply` (implement) -> `/opsx:archive` (cleanup) |
| **Context** | Each change gets isolated folder: `proposal.md`, `specs/`, `design.md`, `tasks.md` |
| **Memory** | Filesystem-based — specs and artifacts live in git alongside code |
| **Scheduling** | Task decomposition with numbered subtasks (1.1, 1.2, 2.1); iterative refinement |

### What Mercury Can Learn
- **Spec-as-shared-context pattern**: When Mercury dispatches work to multiple agents, a shared spec artifact ensures all agents work toward the same goal without Mercury needing to maintain a complex state bus.
- **Agent-agnostic design**: OpenSpec works with 20+ tools by generating agent-specific instructions dynamically. Mercury should similarly abstract agent differences.
- **Filesystem as source of truth**: Markdown specs in git are simple, versionable, and readable by any agent.
- **Change isolation**: Each feature/fix gets its own folder, preventing context pollution across parallel agent work.

### Limitations
- Not an orchestrator itself — it's a specification layer. Does not manage agent processes.
- No real-time coordination between agents.
- Slash-command based — requires IDE integration.
- Lightweight by design, may need extension for complex multi-agent workflows.

---

## 4. anthropics/claude-code — Claude Code (Open Source)

**GitHub:** https://github.com/anthropics/claude-code
**Stars:** ~78,200 | **Forks:** 6,400 | **License:** (See repo)

### Architecture Summary

Claude Code is Anthropic's agentic coding tool. Its open-source release reveals a sophisticated subagent system, a 24+ event hook lifecycle, and a programmatic SDK (Python + TypeScript) that enables spawning and managing subagents.

### Tech Stack
- **Languages:** Shell 47%, Python 29.3%, TypeScript 17.7%, PowerShell 4.1%
- **Runtime:** Node.js 18+
- **SDK:** `claude-agent-sdk` (Python) / `@anthropic-ai/claude-agent-sdk` (TypeScript)
- **Providers:** Anthropic API, AWS Bedrock, Google Vertex, Azure Foundry

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Subagent System** | `Agent` tool in SDK — define named agents with description, prompt, and allowed tools; parent delegates tasks, subagent reports back. `parent_tool_use_id` tracks lineage. |
| **Hooks (24+ events)** | `SessionStart`, `PreToolUse`, `PostToolUse`, `Stop`, `SubagentStart/Stop`, `WorktreeCreate/Remove`, `PreCompact/PostCompact`, `Elicitation`, `ConfigChange`, `Notification`, etc. |
| **Hook Types** | 4 types: `command` (shell), `http` (POST endpoint), `prompt` (LLM evaluation), `agent` (subagent with tool access) |
| **Sessions** | Session ID-based persistence. Resume sessions with full context. Fork sessions for exploration. |
| **Permissions** | Fine-grained: `allowed_tools` whitelist, `permission_mode` (default/plan/acceptEdits/dontAsk/bypassPermissions) |
| **MCP** | Native MCP server integration for external tools |
| **Matchers** | Regex-based event filtering: `"Edit\|Write"`, `"mcp__memory__.*"` |
| **Config Hierarchy** | User settings -> Project settings -> Local settings -> Managed policy -> Plugin hooks -> Component frontmatter |

### SDK API Design (Critical for Mercury)

```python
# Spawn subagent
async for message in query(
    prompt="Review this codebase",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Glob", "Grep", "Agent"],
        agents={
            "code-reviewer": AgentDefinition(
                description="Expert code reviewer",
                prompt="Analyze code quality",
                tools=["Read", "Glob", "Grep"],
            )
        },
    ),
):
    print(message)

# Resume session
async for message in query(
    prompt="Continue from where we left off",
    options=ClaudeAgentOptions(resume=session_id),
):
    print(message)
```

### What Mercury Can Learn
- **The hook system is Mercury's primary integration point.** With 24+ lifecycle events, Mercury can intercept PreToolUse, PostToolUse, Stop, SubagentStart/Stop to monitor and coordinate Claude Code instances.
- **SDK `query()` function is the programmatic API** for spawning Claude Code agents. Mercury should use this rather than CLI wrapping for Claude Code specifically.
- **Session resume enables stateful multi-turn orchestration** — Mercury can pause an agent, dispatch context from another agent, then resume.
- **The matcher regex system** allows Mercury hooks to selectively intercept specific tool calls (e.g., only file edits, only MCP calls).
- **Subagent tracking via `parent_tool_use_id`** enables Mercury to build a task tree visualization.
- **4 hook handler types** — Mercury can use HTTP hooks to receive real-time agent events via a local server.

### Limitations
- SDK is Claude-specific — does not work with other agents.
- Hooks are JSON-configured, not a real-time event bus.
- No built-in multi-agent coordination (that's Mercury's job).
- Subagents are within a single Claude Code session, not cross-agent.

---

## 5. gsd-build/get-shit-done (GSD) — Meta-Prompting Task Runner

**GitHub:** https://github.com/gsd-build/get-shit-done
**Stars:** ~30,300 | **Forks:** 2,500 | **License:** (check repo)

### Architecture Summary

GSD is a context engineering layer for Claude Code that prevents "context rot" by externalizing state into files, decomposing work into atomic tasks, and executing each task in a fresh context window with parallel wave-based scheduling.

### Tech Stack
- **Language:** TypeScript/JavaScript (npm package `get-shit-done-cc`)
- **Runtime:** Claude Code (required dependency)
- **Distribution:** npm

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Context Management** | 6 state documents: `PROJECT.md` (vision), `REQUIREMENTS.md` (scope), `ROADMAP.md` (phases), `STATE.md` (decisions/blockers), `CONTEXT.md` (phase-specific), `PLAN.md` (XML tasks) |
| **Task Decomposition** | Phases -> Tasks -> Waves. Waves group by dependency; independent tasks parallelize within a wave; waves execute sequentially. |
| **Agent Specialization** | Researchers (4 parallel), Planners, Checkers, Executors, Verifiers, Debuggers — each role is a Claude Code subagent |
| **Fresh Context Per Task** | Each executor gets a clean 200k token window, preventing context rot |
| **Atomic Commits** | One git commit per task: `feat(phase-N): description` |
| **XML Task Format** | `<task type="auto"><name>...<files>...<action>...<verify>...<done>...</task>` |

### Workflow
1. `/gsd:new-project` — Initialize with questions, research, requirements, roadmap
2. `/gsd:discuss-phase N` — Capture implementation decisions
3. `/gsd:plan-phase N` — Domain research, task plan, verification
4. `/gsd:execute-phase N` — Parallel wave execution with atomic commits
5. `/gsd:verify-work N` — UAT with automated failure diagnosis
6. `/gsd:quick` — Ad-hoc tasks skipping planning

### What Mercury Can Learn
- **Wave-based parallel execution** is a strong scheduling pattern. Mercury should group independent tasks and parallelize within waves, while sequencing dependent waves.
- **Externalized state documents** prevent context rot. Mercury should maintain similar shared state files that any agent can read.
- **Specialized agent roles** (researcher, planner, executor, verifier) is a proven multi-agent pattern. Mercury could assign different CLI agents to different roles based on their strengths.
- **Fresh context per task** is critical — Mercury should track context window usage and spawn fresh sessions when needed.
- **XML task format** with `<verify>` and `<done>` tags enables automated verification. Mercury's task protocol should include explicit completion criteria.

### Limitations
- Claude Code only — tightly coupled to one agent.
- No GUI — pure CLI/prompt-based workflow.
- No real-time agent monitoring or visualization.
- Heavyweight for small tasks (full spec workflow).

---

## 6. obra/superpowers — Agentic Skills Framework

**GitHub:** https://github.com/obra/superpowers
**Stars:** ~84,900 | **License:** (check repo)

### Architecture Summary

Superpowers is a composable skills framework that enforces a structured development methodology through automatically-triggered skills. It implements subagent-driven development with git worktrees for isolation and mandatory TDD.

### Tech Stack
- **Language:** Markdown (skills definitions) + Shell scripts
- **Runtime:** Claude Code (skills are Claude Code skills)
- **Isolation:** Git worktrees

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Skills System** | Auto-triggered skills based on context — not invoked manually. Skills for brainstorming, planning, TDD, review, git management. |
| **Subagent Development** | Fresh subagent instances handle individual tasks with two-stage review: spec compliance + code quality. |
| **Worktree Isolation** | Git worktrees create isolated workspaces per branch, enabling parallel development without main branch contamination. |
| **TDD Enforcement** | Mandatory RED-GREEN-REFACTOR. Code written before tests gets deleted. |
| **Development Flow** | Brainstorm -> Worktree Setup -> Plan (2-5 min tasks with file paths) -> Subagent Execution -> TDD -> Code Review -> Branch Completion |
| **Orchestration** | "Agent checks for relevant skills before any task. Mandatory workflows, not suggestions." |

### What Mercury Can Learn
- **Git worktrees for agent isolation** is brilliant. Each agent works in its own worktree, preventing file conflicts. Mercury should integrate worktree management.
- **Automatic skill triggering** (vs manual invocation) ensures consistent methodology. Mercury's orchestration rules should be automatic.
- **Two-stage review pattern** (spec compliance + code quality) can be split across different agents in Mercury.
- **2-5 minute task granularity** with exact file paths is the right unit of work for agent delegation.
- **Skills-as-markdown** is a portable, version-controllable way to define agent behaviors.

### Limitations
- Claude Code only — skills are Claude Code specific.
- No real-time coordination between agents.
- No central orchestrator — skills are decentralized.
- No UI — methodology is file/prompt driven.
- Methodology-focused, not infrastructure-focused.

---

## 7. cline/cline — Autonomous Coding Agent in VS Code

**GitHub:** https://github.com/cline/cline
**Stars:** ~59,000 | **Forks:** 6,000 | **License:** Apache 2.0

### Architecture Summary

Cline is a VS Code extension implementing a human-in-the-loop autonomous coding agent. It features Plan/Act modes, MCP extensibility, workspace checkpoints, and a provider abstraction layer supporting 10+ LLM providers.

### Tech Stack
- **Language:** TypeScript
- **Platform:** VS Code Extension
- **UI:** VS Code Sidebar (webview-ui)
- **Protocol:** MCP (Model Context Protocol)
- **Providers:** OpenRouter, Anthropic, OpenAI, Gemini, Bedrock, Azure, Vertex, Cerebras, Groq, LM Studio, Ollama

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Plan/Act Modes** | Plan mode: analyzes and proposes without modifying. Act mode: executes with per-action user approval. |
| **Human-in-the-Loop** | Every file change and terminal command requires explicit approval via diff viewer UI. |
| **MCP Integration** | Self-extending — agent can request and install new MCP tools (e.g., "add a tool that fetches Jira tickets"). |
| **Provider Abstraction** | Unified API across 10+ LLM providers with per-request cost tracking. |
| **Checkpoints** | Workspace snapshots at each step. Compare, restore, or rollback to any checkpoint. |
| **Context System** | `@file`, `@folder`, `@url`, `@problems` for attaching context to prompts. |
| **Tool System** | File ops, terminal integration, browser automation (Computer Use), AST-based code analysis. |
| **Error Feedback Loops** | Linter integration — agent monitors compilation errors and auto-fixes. |

### What Mercury Can Learn
- **Provider abstraction layer** is essential. Mercury should abstract away the differences between Claude, GPT, Gemini, etc. at the provider level. Cline's approach of supporting OpenRouter + direct APIs is proven.
- **Checkpoint/rollback system** is valuable for multi-agent work. If one agent breaks something, Mercury needs to restore state.
- **Plan/Act mode separation** maps to Mercury's orchestration: Plan phase (decide which agents do what) and Act phase (execute with approval).
- **Cost tracking per request** is important for multi-agent cost optimization.
- **MCP as extensibility mechanism** — Cline proves MCP is the right protocol for tool extensibility.
- **Diff viewer UI for approval** — Mercury should show diffs from each agent for human review.

### Limitations
- VS Code only — not a desktop app, not terminal-based.
- Single-agent design — no multi-agent coordination.
- No inter-agent communication.
- Human approval on every action can be slow for autonomous workflows.
- No built-in task decomposition or planning framework.

---

## 8. plandex-ai/plandex — AI Coding Engine with Planning

**GitHub:** https://github.com/plandex-ai/plandex
**Stars:** ~15,100 | **Forks:** 1,100 | **License:** (check repo)
**STATUS: WINDING DOWN as of 10/3/2025 — no longer accepting new users.**

### Architecture Summary

Plandex is a terminal-based coding agent with a cumulative diff sandbox that keeps AI-generated changes separate from project files until approved. It supports up to 2M tokens of context with tree-sitter project maps.

### Tech Stack
- **Language:** Go (server), CLI client
- **Context:** 2M token effective context window
- **Code Intelligence:** Tree-sitter project maps (30+ languages)
- **Models:** Multi-model (Anthropic, OpenAI, Google, open source) with model packs
- **Deployment:** CLI + Dockerized server (local or cloud)

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Sandbox/Diff System** | Cumulative diff review — changes stay in sandbox until explicitly applied. |
| **Version Control** | Full version history of plan evolution, including branches for exploring alternatives. |
| **Context Management** | 2M token window; loads only what's needed per step; tree-sitter project maps for 30+ languages. |
| **Multi-Model** | Model packs with different capability/cost/speed tradeoffs; context caching across providers. |
| **Planning** | Plans span multiple steps and files; automatic debugging; REPL mode with fuzzy autocomplete. |

### What Mercury Can Learn
- **Diff sandbox pattern** is valuable. Mercury should maintain a staging area where each agent's changes are reviewed before merging to the working tree.
- **Version-controlled plan evolution** with branches for alternatives is a strong pattern for multi-agent exploration.
- **Tree-sitter project maps** for efficient context loading — Mercury should generate project structure for agents rather than having each agent re-discover it.
- **Model packs** — Mercury could define "agent packs" optimizing for different tradeoffs.

### Limitations
- **PROJECT IS WINDING DOWN** — not a viable ongoing dependency.
- Single-agent only.
- Go-based server architecture differs from Mercury's Tauri/Rust stack.
- No multi-agent coordination.

---

## 9. All-Hands-AI/OpenHands — Platform for AI Software Developers

**GitHub:** https://github.com/OpenHands/OpenHands
**Stars:** ~69,100 | **License:** MIT (core), commercial (enterprise)

### Architecture Summary

OpenHands is the most architecturally mature platform researched. It uses an **event-sourcing pattern** where all agent interactions are immutable events in an append-only log. Agents are stateless event processors. The SDK separates into 4 packages: core, tools, workspace, agent-server.

### Tech Stack
- **Backend:** Python 74.3%
- **Frontend:** TypeScript/React 23.7%
- **Infrastructure:** Docker, Kubernetes
- **LLM:** 100+ providers via LiteLLM, RouterLLM for multi-model selection
- **Deployment:** CLI, Local GUI (React SPA), Cloud, Enterprise (Kubernetes)

### Key Patterns

| Pattern | Implementation |
|---------|---------------|
| **Event Stream** | All interactions are immutable events (Action, Observation, Internal). ConversationState is single mutable source of truth with append-only EventLog. Deterministic replay and session recovery. |
| **Agent Loop** | Agents are stateless event processors: Action -> Execution -> Observation. Emit events through callbacks (`on_event()`). |
| **Runtime Abstraction** | `LocalConversation` (in-process) vs `RemoteConversation` (HTTP/WebSocket) — identical API, swappable. |
| **Multi-Agent** | Sub-agent delegation as a standard tool (not core framework). Sub-agents are independent conversations inheriting parent config + workspace. |
| **Sandbox** | Docker containers per session. Optional isolation — local by default, containerized when needed. Full stack images (API server, VSCode Web, VNC, Chromium). |
| **Security** | SecurityAnalyzer rates actions (LOW/MEDIUM/HIGH/UNKNOWN); ConfirmationPolicy determines approval. |
| **Context Management** | Condenser system replaces forgotten events with summaries, reducing tokens while preserving full log. |
| **4-Package Modularity** | SDK core, Tools implementations, Workspace environments, Agent Server. |
| **Type-Safe Tools** | Unified Action-Execution-Observation abstraction for native + MCP tools. |

### What Mercury Can Learn
- **Event-sourcing is the gold standard for agent orchestration.** Mercury should adopt an event stream where all agent actions are immutable events. This enables replay, debugging, and cross-agent coordination.
- **ConversationState as single source of truth** with append-only EventLog is the right state management pattern.
- **LocalConversation vs RemoteConversation abstraction** — Mercury should define a conversation interface that works identically whether the agent is local (PTY) or remote (API).
- **Sub-agent delegation as a tool** (not framework magic) keeps the architecture clean and extensible.
- **Condenser pattern** for context management — summarize old events to reclaim tokens while preserving history.
- **SecurityAnalyzer with risk ratings** — Mercury should classify agent actions by risk level.
- **4-package modularity** (core, tools, workspace, server) is a good separation of concerns.

### Limitations
- Python-centric — Mercury uses Rust/TypeScript.
- Heavy (Docker, Kubernetes) — may be overweight for desktop use.
- Primarily designed for a single AI agent per session (not true multi-agent coordination like Mercury needs).
- Enterprise features are not open source.

---

## Cross-Cutting Analysis: What Mercury Should Adopt

### 1. Architecture Foundation
- **Event-sourcing pattern** (from OpenHands) — all agent interactions as immutable events
- **Tauri + Rust + Vue stack** (validated by Golutra) for desktop orchestration
- **PTY wrapping** (from Golutra) for CLI agent management
- **SDK integration** (from Claude Agent SDK) for programmatic agent control where available

### 2. Bus/Message Routing
- **Event stream** (OpenHands) as the central message bus — typed, immutable events
- **Hook system** (Claude Code) for intercepting agent lifecycle events via HTTP/command hooks
- **Callback-based event emission** (OpenHands `on_event()`) for real-time streaming to UI

### 3. UI Management
- **Agent avatar panels** (Golutra) with log inspection and prompt injection
- **Plan/Act mode separation** (Cline) for orchestration review before execution
- **Diff viewer** (Cline, Plandex) for reviewing each agent's changes
- **Checkpoint/rollback** (Cline) for multi-agent state recovery
- **Cost tracking** (Cline) per agent per request

### 4. Memory/Context
- **Three-tier memory** (Mem0): user, session, agent levels
- **Externalized state documents** (GSD): PROJECT.md, STATE.md, CONTEXT.md shared across agents
- **Spec-as-shared-context** (OpenSpec): markdown specs that any agent can read
- **Condenser pattern** (OpenHands): summarize old events to reclaim context tokens
- **Fresh context per task** (GSD): spawn new sessions when context is saturated

### 5. Scheduling/Orchestration
- **Wave-based parallel execution** (GSD): group independent tasks, parallelize within waves, sequence waves
- **Specialized agent roles** (GSD): researcher, planner, executor, verifier, debugger
- **Git worktree isolation** (Superpowers): each agent works in its own worktree
- **Two-stage review** (Superpowers): spec compliance + code quality
- **Automatic skill triggering** (Superpowers): orchestration rules fire automatically, not manually

### 6. Developer Experience
- **Single-line memory API** (Mem0): `memory.add()` / `memory.search()`
- **Session resume** (Claude Agent SDK): pause/resume agent sessions by ID
- **Provider abstraction** (Cline): unified API across LLM providers
- **Model packs** (Plandex): pre-configured model combinations for different tradeoffs

---

## Priority Integration Matrix for Mercury

| Repo | Integration Type | Priority | Effort |
|------|-----------------|----------|--------|
| **Claude Code SDK** | Direct SDK integration for spawning/managing Claude Code | P0 | Medium |
| **OpenHands** | Adopt event-sourcing architecture pattern | P0 | High |
| **Golutra** | Reference architecture (Tauri + PTY wrapping) | P0 | N/A (reference) |
| **GSD** | Wave-based scheduling + externalized state pattern | P1 | Medium |
| **Superpowers** | Git worktree isolation + skills pattern | P1 | Medium |
| **Mem0** | Memory layer integration (possibly via MCP) | P1 | Low-Medium |
| **OpenSpec** | Spec-driven task definition format | P2 | Low |
| **Cline** | Reference for provider abstraction + checkpoint UI | P2 | N/A (reference) |
| **Plandex** | Reference for diff sandbox pattern | P3 | N/A (winding down) |
