# Agent Memory & Context Management Systems Research

> Mercury project reference: cross-agent memory sharing and context optimization

---

## 1. Mem0 (mem0ai/mem0)

**What it is:** Universal memory layer for AI agents. YC S24, $24M raised. Apache 2.0.

### Architecture

- **Hybrid datastore**: vector store + knowledge graph + key-value store
- **Memory pipeline**: conversations go through extraction -> deduplication -> consolidation -> storage
- **Memory scoping**: Multi-level memory with User, Session, and Agent state
- **LLM-powered extraction**: uses an LLM (default gpt-4.1-nano) to extract salient facts from conversations
- **Graph variant**: enhanced version uses graph-based memory representations to capture relational structures between conversational elements

### Core API

```python
memory = Memory()
memory.add(messages, user_id=user_id)           # Extract & store memories
memory.search(query=message, user_id=user_id)   # Semantic retrieval
memory.update(memory_id, data)                   # Update existing
memory.delete(memory_id)                         # Remove
```

### Performance (vs full-context baselines)

- +26% accuracy over OpenAI Memory on LOCOMO benchmark
- 91% lower p95 latency
- 90% fewer tokens

### Multi-agent integration

- LangGraph integration (customer bot guide)
- CrewAI integration
- AWS Agent SDK exclusive memory provider
- OpenMemory: team workspace-based shared memory

### Mercury relevance

- The `add`/`search` API pattern is clean and adoptable
- Multi-level scoping (user/session/agent) maps well to Mercury's multi-agent needs
- Graph-based memory variant useful for capturing relationships between agents' discoveries
- Could serve as a shared memory backend across Mercury's agent fleet

---

## 2. LangGraph / LangChain Agent Memory

**What it is:** State management and memory framework for multi-agent workflows.

### Architecture

- **State as shared memory**: centralized TypedDict state accessible to all nodes
- **Reducer pattern**: functions that merge existing state with node updates (e.g., `operator.add` for list concat, `add_messages` for message dedup by ID)
- **Immutable state versions**: updates create new versions rather than mutating
- **Checkpointing**: entire state persisted to external storage; workflows can pause/resume across environments

### State Flow

1. Node receives current state as input
2. Node performs logic, returns only the portion it wants to update
3. LangGraph applies reducer to merge update deterministically
4. Updated state flows to next node

### Memory Scopes

| Scope | Description |
|-------|-------------|
| **Short-term** | State within single execution/thread, resumable via checkpoints |
| **Long-term** | Cross-session persistence via external stores (databases, vector stores) |

### Multi-agent patterns

- All agents read/write to a shared state object (e.g., `IncidentState` with query, search_results, analysis, quality_score)
- Reducer-driven schemas prevent data loss during concurrent updates
- Typed state schemas using Python's `TypedDict` and `Annotated` types

### Mercury relevance

- The reducer pattern is valuable for Mercury's shared state across agents
- Checkpoint-based persistence enables pause/resume of multi-agent workflows
- Explicit typed state schemas prevent coordination bugs
- Immutable state versions provide audit trail

---

## 3. Letta (formerly MemGPT)

**What it is:** Platform for building stateful agents with self-managed memory. Open source.

### Memory Architecture (OS-inspired hierarchy)

| Layer | Analogy | Description |
|-------|---------|-------------|
| **Core Memory** | RAM | In-context memory blocks, always pinned in context window. Editable via APIs. Topics like "user preferences" or "agent persona" |
| **Recall Memory** | Cache | Complete interaction history, searchable, auto-persisted to disk |
| **Archival Memory** | Disk | Explicitly stored knowledge in external databases (vector/graph). Retrieved via specialized tools |
| **Message Buffer** | Registers | Recent conversation messages for immediate context |

### Key mechanisms

- **LLM-as-OS paradigm**: the model manages its own memory via function calls (read/write/search)
- **Memory tools**: agent autonomously moves data between in-context (core) and external (archival) storage
- **Message eviction & summarization**: when context capacity reached, ~70% removed via recursive summarization
- **Sleep-time compute**: asynchronous memory agents refine/consolidate during idle periods
- **Memory blocks**: structured storage with labels, descriptions, values, character limits

### Letta V1 changes (2025-2026)

- Eliminated `send_message` tool and `heartbeat` mechanisms from MemGPT
- Native reasoning tokens from frontier models (OpenAI, Anthropic, Gemini)
- Simplified agent loop: at each step, agent decides to call tool, send message, reason, or terminate
- Conversations API: shared memory across parallel user experiences

### Mercury relevance

- The OS-inspired memory hierarchy (core/recall/archival) is directly applicable
- Agent-managed memory (LLM decides what to store/retrieve) reduces orchestration complexity
- Sleep-time compute pattern useful for Mercury's background memory consolidation
- Memory blocks with structured metadata could standardize cross-agent memory format

---

## 4. Claude Code Memory System

**What it is:** File-based persistent memory for Claude Code sessions.

### Memory Hierarchy (highest to lowest priority)

| Level | Location | Scope |
|-------|----------|-------|
| **Managed Policy** | `/Library/Application Support/ClaudeCode/CLAUDE.md` (macOS) | Organization-wide, cannot be excluded |
| **Project Instructions** | `./CLAUDE.md` or `./.claude/CLAUDE.md` | Team-shared via version control |
| **Project Rules** | `.claude/rules/*.md` | Path-scoped, modular |
| **User Instructions** | `~/.claude/CLAUDE.md` | Personal, all projects |
| **Auto Memory** | `~/.claude/projects/<project>/memory/MEMORY.md` | Per-project, machine-local |

### Auto Memory system

- Claude writes notes for itself: build commands, debugging insights, architecture, preferences
- Storage: `~/.claude/projects/<project>/memory/` directory with `MEMORY.md` index + topic files
- First 200 lines of `MEMORY.md` loaded at session start; topic files loaded on demand
- Plain markdown, human-editable
- All worktrees/subdirectories in same git repo share one auto memory directory
- Subagents can maintain their own auto memory

### Path-scoped rules

```yaml
---
paths:
  - "src/api/**/*.ts"
---
# API Development Rules
- All API endpoints must include input validation
```

### Key design decisions

- Memory files are context (user message), not enforced configuration
- Under 200 lines per CLAUDE.md for >92% rule adherence (vs 71% beyond 400 lines)
- ~4,000 tokens per 100-line file; total memory budget ~10,000 tokens
- `@path/to/import` syntax for file imports (max 5 hops)
- CLAUDE.md survives `/compact` (re-read from disk)

### Mercury relevance

- File-based memory is simple, auditable, version-controllable
- Hierarchical scoping (org > project > rules > user > auto) is a proven pattern
- Path-scoped rules reduce noise -- only load relevant context
- Auto memory pattern (agent writes its own notes) reduces manual maintenance
- Token budgeting (~10k tokens for memory) is a practical constraint to adopt

---

## 5. Zep

**What it is:** Temporal knowledge graph architecture for agent memory. YC-backed.

### Architecture: Three integrated subgraphs

1. **Episode Subgraph**: raw unstructured data (messages, text, JSON) as non-lossy source material
2. **Semantic Entity Subgraph**: extracted and resolved entities + facts from episodes
3. **Community Subgraph**: groups strongly-connected entities with high-level summaries

### Temporal modeling (bi-temporal)

- **Timeline T**: chronological event ordering (when things happened)
- **Timeline T'**: data ingestion ordering (when system learned about it)
- Facts have `t_valid`/`t_invalid` (event timeline) and `t'_created`/`t'_expired` (transaction timeline)
- Contradicting facts are invalidated, not deleted -- preserves history

### Entity resolution

- LLM extracts entities from current + preceding messages
- Resolution against existing nodes via embedding similarity + full-text search
- Each entity embedded in 1024-dimensional space

### Retrieval pipeline (3-stage)

1. **Search (phi)**: parallel cosine similarity + BM25 full-text + breadth-first graph traversal
2. **Reranking (rho)**: RRF, MMR, or cross-encoder models
3. **Construction (chi)**: formats selected facts with temporal ranges + entity summaries

### Performance

- 94.8% accuracy on DMR benchmark (vs 93.4% for MemGPT)
- Up to 18.5% accuracy improvement on LongMemEval
- 90% latency reduction vs full-context baselines

### Core library: Graphiti

- Open source temporal knowledge graph engine
- Handles both unstructured conversational data and structured business data
- Dynamic updates without reprocessing entire conversations

### Mercury relevance

- Temporal awareness is critical for Mercury -- facts change over time
- Bi-temporal modeling enables "what was true at time X" queries
- Graph-based retrieval captures relationships that vector-only approaches miss
- Non-lossy architecture (episodes preserved for citation) supports auditability
- The 3-stage retrieval pipeline (search -> rerank -> construct) is a proven pattern

---

## 6. Context Window Management Patterns

### Anthropic's context engineering principles

- Context engineering = "designing dynamic systems that provide the right information and tools, in the right format, at the right time"
- Find "the smallest set of high-signal tokens that maximize desired outcome"
- Context has diminishing marginal returns; more != better

### Key patterns

| Pattern | Description | When to use |
|---------|-------------|-------------|
| **Hierarchical summarization** | Compress older conversation segments; recent stays verbatim | Long conversations |
| **Prompt compression** | Remove redundant info, compress patterns, strip unnecessary formatting | Token-constrained scenarios |
| **Micro-agent pattern** | Small agents (3-10 steps each) instead of monolithic agents | Complex multi-step workflows |
| **On-demand retrieval (ReAct)** | Agent retrieves context as needed via tools | Unknown information needs |
| **Sub-agent architecture** | Specialized sub-agents with clean context windows; return condensed summaries (1-2k tokens) | Complex research tasks |
| **Structured note-taking** | Agent writes externally-persisted notes outside context window | Long-horizon tasks |

### Manus lessons (production context engineering)

- **KV-cache as primary metric**: stable prompt prefixes, append-only context, avoid cache-breaking changes. Cached tokens cost 10x less.
- **Action space masking**: mask token logits during decoding instead of removing tools (preserves KV-cache)
- **File system as ultimate context**: treat filesystem as reversible compressed storage. Drop content if paths/URLs remain available.
- **Attention recitation via todo.md**: force objectives into recent attention span by updating todo files during tasks
- **Error preservation**: leave wrong turns in context so model can update beliefs from seeing failed actions
- **Context diversity**: vary action-observation serialization to prevent repetitive pattern drift

### Google ADK multi-agent patterns

- **Session**: definitive state with structured Event records (messages, tool calls, results, control signals)
- **Working Context**: ephemeral view rebuilt per invocation from underlying state
- **Memory**: long-lived, searchable knowledge outlining individual sessions
- **Artifacts**: large data externalized as named, versioned objects with lightweight references
- **Compaction**: async LLM summarization of older events when threshold reached
- **Scoped handoffs**: `include_contents` controls how much ancestral context flows to sub-agents

### Mercury relevance

- Sub-agent architecture with condensed summaries maps directly to Mercury's multi-agent design
- KV-cache optimization is critical for cost control at scale
- File-as-context pattern (Manus) aligns with Mercury's potential workspace model
- Todo.md attention recitation pattern combats lost-in-the-middle for long tasks
- Scoped handoffs control context propagation between Mercury's agents

---

## Cross-cutting recommendations for Mercury

### 1. Memory architecture

Adopt a **three-tier memory model** inspired by Letta + Claude Code:
- **Core memory**: always in context (like CLAUDE.md / Letta core memory). Project config, agent personas, active task state.
- **Working memory**: session-scoped, shared state between agents (like LangGraph state). Reducer-based merging for concurrent updates.
- **Archival memory**: persistent, searchable store (like Mem0 / Zep). Graph + vector hybrid for relational and semantic queries.

### 2. Cross-agent memory sharing

- **Shared state object** (LangGraph pattern): typed schema with reducers for safe concurrent updates
- **Scoped memory access** (Claude Code pattern): agents only see memory relevant to their current task/file scope
- **Memory bus**: agents publish discoveries; other agents subscribe to relevant topics (inspired by Google ADK events)

### 3. Context window optimization

- **Token budgeting**: allocate fixed budgets per memory tier (Claude Code: ~10k for memory files)
- **Hierarchical summarization**: compress old context, keep recent verbatim
- **On-demand retrieval**: lightweight references in context; full content fetched via tools
- **Sub-agent isolation**: each agent gets clean context; returns condensed summary to coordinator
- **KV-cache awareness**: stable prefixes, append-only context growth

### 4. Temporal awareness

Adopt Zep's bi-temporal model for Mercury's knowledge base:
- Track when facts were true (event time) and when they were learned (ingestion time)
- Invalidate rather than delete contradicted facts
- Enable point-in-time queries for debugging and audit

### 5. Agent-managed memory

Let agents manage their own memory (Letta pattern):
- Provide memory tools (read/write/search) as first-class agent capabilities
- Auto-memory (Claude Code pattern): agents decide what is worth remembering
- Sleep-time compute (Letta pattern): background consolidation during idle periods

---

## Sources

- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [Mem0 Research Paper](https://arxiv.org/abs/2504.19413)
- [Mem0 Documentation](https://docs.mem0.ai/introduction)
- [LangGraph Agent Memory Architecture (DEV)](https://dev.to/sreeni5018/the-architecture-of-agent-memory-how-langgraph-really-works-59ne)
- [LangGraph Multi-Agent Orchestration Guide](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langgraph-multi-agent-orchestration/langgraph-multi-agent-orchestration-complete-framework-guide-architecture-analysis-2025)
- [Letta GitHub](https://github.com/letta-ai/letta)
- [Letta Agent Memory Blog](https://www.letta.com/blog/agent-memory)
- [Letta V1 Architecture](https://www.letta.com/blog/letta-v1-agent)
- [Letta MemGPT Concepts](https://docs.letta.com/concepts/memgpt/)
- [Claude Code Memory Docs](https://code.claude.com/docs/en/memory)
- [Zep Temporal Knowledge Graph Paper](https://arxiv.org/html/2501.13956v1)
- [Zep Product Page](https://www.getzep.com/product/agent-memory/)
- [Graphiti GitHub](https://github.com/getzep/graphiti)
- [Anthropic: Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Manus: Context Engineering Lessons](https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus)
- [Google ADK Multi-Agent Context](https://developers.googleblog.com/architecting-efficient-context-aware-multi-agent-framework-for-production/)
- [Context Window Management Strategies (Maxim)](https://www.getmaxim.ai/articles/context-window-management-strategies-for-long-context-ai-agents-and-chatbots/)
