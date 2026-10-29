# Deep Research: Effective Harnesses for Long-Running Agents

> Task: TASK-3e79144f | Status: closed | Verdict: pass
> Completed: 2026-03-29T10:12:00Z

---

## 1. Session Persistence & Checkpoint/Resume

### Git-Based Checkpointing (Anthropic Pattern)
Anthropic's proven harness uses git commits as checkpoints after each feature completion. Each coding session starts with: `pwd` 鈫 →  read git logs 鈫 →  read progress files 鈫 →  review feature list 鈫 →  start dev server 鈫 →  run baseline tests 鈫 →  implement. This creates a reliable recovery chain where any session can reconstruct full state from artifacts alone.

Key artifacts:
- `init.sh` 鈥 →  rapid environment bootstrap script
- `claude-progress.txt` 鈥 →  cumulative work log
- `feature_list.json` 鈥 →  structured feature manifest (JSON preferred over Markdown because 'the model is less likely to inappropriately change or overwrite JSON files')
- Git history with descriptive commit messages

Source: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

### Context Resets > Compaction
Anthropic's second harness article reveals a critical insight: **context resets** (clearing the window and providing structured handoffs) outperform **compaction** (summarizing earlier messages). Compaction causes 'context anxiety' where models prematurely wrap up work as they approach perceived limits. Full resets with structured handoff documents eliminate this failure mode.

Source: https://www.anthropic.com/engineering/harness-design-long-running-apps

### Claude Agent SDK Session Resume
The SDK provides native session persistence via `session_id`. First query captures the ID, subsequent queries pass `resume=session_id` to continue with full context. Sessions can also be forked to explore different approaches.

Source: https://platform.claude.com/docs/en/agent-sdk/overview

### LangGraph Checkpoint Persistence
LangGraph saves graph state as checkpoints at every execution step, organized into threads. When a node fails mid-execution, it stores pending writes from successful nodes and resumes without re-running them. Backends include PostgreSQL, DynamoDB, Couchbase, and SQLite.

Source: https://docs.langchain.com/oss/python/langgraph/persistence

### Durable Workflow Orchestrators
Temporal, Inngest, and Trigger.dev treat agent tasks as durable workflows rather than ephemeral function calls. Checkpoint-resume cuts token costs during failures by 30-50% and eliminates wasted work from transient errors.

Source: https://eunomia.dev/blog/2025/05/11/checkpointrestore-systems-evolution-techniques-and-applications-in-ai-agents/

---

## 2. Self-Monitoring & Health Checks

### Verification Triad (Claude Agent SDK)
Anthropic recommends three verification mechanisms:
1. **Rules-Based Feedback** 鈥 →  Code linting provides granular, deterministic error reporting
2. **Visual Feedback** 鈥 →  Screenshots enable agents to verify UI/formatting correctness (via Playwright MCP or Puppeteer)
3. **LLM Judges** 鈥 →  Separate models evaluate fuzzy criteria (tone, quality, completeness)

Source: https://claude.com/blog/building-agents-with-the-claude-agent-sdk

### Separate Generation from Evaluation
Critical finding: 'When asked to evaluate work they've produced, agents tend to respond by confidently praising the work 鈥 →  even when, to a human observer, the quality is obviously mediocre.' The three-agent harness (Planner 鈫 →  Generator 鈫 →  Evaluator) addresses this by having a dedicated Evaluator agent that interacts with the running application independently.

Source: https://www.anthropic.com/engineering/harness-design-long-running-apps

### Session Initialization Health Check Protocol
Every new coding session begins with baseline tests before any new work:
1. Verify working directory (`pwd`)
2. Read git logs and progress files
3. Review feature list status
4. Start dev server
5. Run baseline tests to detect regressions
6. Only then begin new feature work

Source: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

### Observability Layer
Production harnesses require: logging every agent action, tracking token usage and costs, recording decision points, surfacing anomalies. Token consumption data in response headers enables well-behaved agents to self-throttle before hitting hard limits.

Source: https://dev.to/htekdev/agent-harnesses-why-2026-isnt-about-more-agents-its-about-controlling-them-1f24

---

## 3. Multi-Agent Coordination Harnesses

### Framework Comparison Matrix

| Feature | Claude Agent SDK | LangGraph | CrewAI | AutoGen/AG2 |
|---------|-----------------|-----------|--------|-------------|
| **Coordination Model** | Subagent delegation | Directed graph with state | Role-based teams | Conversational group chat |
| **State Management** | Session resume/fork | Built-in checkpointing with time travel | Sequential task output passing | In-memory conversation history |
| **Persistence** | Native session IDs | Postgres/DynamoDB/SQLite checkpointers | Limited built-in | Limited built-in |
| **Built-in Tools** | Read, Write, Edit, Bash, Glob, Grep, Web | None (bring your own) | None (bring your own) | None (bring your own) |
| **Model Lock-in** | Claude only | Model-agnostic | Model-agnostic | Model-agnostic |
| **Learning Curve** | Low (batteries-included) | Medium-High (graph concepts) | Low (~20 lines to start) | Medium |
| **Production Readiness** | High (powers Claude Code) | High (battle-tested) | Medium | Low (maintenance mode) |
| **Best For** | Code-centric autonomous agents | Stateful multi-step workflows | Quick team-based prototyping | Research/experimentation |

### Key Insights
- **LangGraph** wins for production stateful systems with its graph-based state machine, conditional edges, and checkpoint time-travel debugging
- **CrewAI** deploys multi-agent teams 40% faster than LangGraph for standard business workflows
- **AutoGen** is in maintenance mode; Microsoft shifted focus to broader Agent Framework
- **Claude Agent SDK** is uniquely batteries-included with built-in tool execution, hooks, and session management
- Same model + different harness architecture = 17-issue score difference on 731 problems (Feb 2026 benchmarks)

Sources: https://www.datacamp.com/tutorial/crewai-vs-langgraph-vs-autogen, https://platform.claude.com/docs/en/agent-sdk/overview

---

## 4. Resource Management (Token Budgets, Cost Controls, Rate Limiting)

### Three-Tier Cost Control (Industry Standard 2026)
1. **Per-request**: `max_tokens` parameter on each API call
2. **Per-task**: Token budget allocated to each agent task/session
3. **Per-period**: Daily/monthly spending caps with soft alerts at 50%/80% and hard cutoff at 100%

### Concrete Cost Data
Anthropic's harness comparison reveals the cost-quality tradeoff:
- Solo agent generation: 20 minutes, $9 鈥 →  broken functionality
- Full 3-agent harness: 6 hours, $200 鈥 →  working core features

The harness is 22x more expensive but produces substantially superior results.

Source: https://www.anthropic.com/engineering/harness-design-long-running-apps

### Token Optimization Strategies
- **Context compaction** 鈥 →  SDK auto-summarizes earlier messages near token limits
- **Context resets** 鈥 →  Clear window with structured handoff (preferred for long sessions)
- **Subagent isolation** 鈥 →  Each subagent gets its own context; returns only relevant excerpts
- **Incremental single-feature focus** 鈥 →  Prevents mid-context exhaustion
- **Tool reduction** 鈥 →  Vercel eliminated 80% of agent tools, improving speed and token efficiency

Source: https://www.philschmid.de/agent-harness-2026

### Rate Limiting Best Practices
- Combine short-term (tokens/minute) with long-term (tokens/month) limits
- Assign cost multipliers to different models for unified dollar-denominated budgets
- Return token consumption in response headers for agent self-throttling
- Circuit breakers for graceful degradation vs. hard failure

Source: https://zuplo.com/learning-center/token-based-rate-limiting-ai-agents

---

## 5. Error Recovery & Resilience

### Compounding Failure Problem
When each step in a 20-step pipeline succeeds 95%, end-to-end completion drops to **36%** (0.95^20). Harnesses must add verification loops, retry policies, and checkpoint-resume to push this back toward acceptable levels.

### Recovery Patterns

| Pattern | Description | When to Use |
|---------|-------------|-------------|
| **Git rollback** | Revert to last good commit | Feature implementation breaks existing functionality |
| **Checkpoint resume** | Restart from last successful step | Transient API/network failures |
| **Exponential backoff retry** | Increasing delay between retries | Rate limits, temporary service outages |
| **Circuit breaker** | Stop calling failing service after N failures | Persistent external service issues |
| **Fallback response** | Graceful degradation with partial results | Non-critical feature failures |
| **Context reset with handoff** | Clear context window, provide structured state | Context exhaustion or compaction anxiety |
| **Evaluator feedback loop** | Separate agent tests and reports issues | Quality degradation over long sessions |

### Premature Completion Mitigation
Anthropic identified 'declaring victory prematurely' as a key failure mode. Solutions:
- Maintain comprehensive feature list with explicit pass/fail status
- Mark features complete only after automated testing
- Use browser automation (Playwright/Puppeteer) for E2E verification
- JSON-format checklists over Markdown (models less likely to inappropriately modify)

Source: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents

### Isolated Sandboxes
Cloud sandboxes provide full execution permissions within strict boundaries. Mistakes stay contained 鈥 →  agents can run commands, modify files, execute tests without approval for every action, but cannot touch production.

Source: https://www.builder.io/blog/best-ai-background-agents-for-developers-2026

---

## 6. Real-World Case Studies

### Claude Code / Claude Agent SDK (Anthropic)
- **Architecture**: Two-agent system (Initializer + Coding Agent) or three-agent (Planner 鈫 →  Generator 鈫 →  Evaluator)
- **Persistence**: Git commits + progress files + session IDs with resume/fork
- **Key Innovation**: Context resets over compaction; JSON feature lists over Markdown
- **SDK Features**: Built-in tools (Read/Write/Edit/Bash/Glob/Grep/Web), hooks lifecycle (PreToolUse, PostToolUse, Stop, SessionStart, SessionEnd), subagent delegation, MCP integrations
- **Result**: SDK powers Claude Code itself 鈥 →  proven at scale

### Devin (Cognition)
- **Use Case**: 'Tier 3' tasks 鈥 →  clearing tech debt, upgrading legacy dependencies, framework migrations
- **Capabilities**: Browses web for updated API docs, debugs own environment, 'interviews' APIs for integration requirements
- **Architecture**: Cloud sandbox with full execution permissions, isolated from production

### Cursor Background Agents
- **Architecture**: Single-agent, cloud-based repo cloning, test execution, PR creation
- **Limitation**: No parallel agent coordination (single-agent architecture)
- **Pattern**: Connects to issue tracker, monitors Slack, analyzes codebase, sets up preview environments

### Open SWE (LangChain)
- **Architecture**: LangGraph-based autonomous coding agent
- **Key Feature**: Graph-based state machine with checkpoint time-travel for debugging
- **Persistence**: DynamoDB/Postgres-backed checkpointers

### Manus
- **Notable**: Refactored harnesses 5 times in 6 months 鈥 →  exemplifies 'build to delete' principle
- **Lesson**: Each model release requires different structural approaches; lightweight modular harnesses are essential

### Industry Benchmarks (Feb 2026)
- Same model, different harness = 17-issue score difference on 731 problems
- Every major tool shipped multi-agent capabilities in same 2-week window: Grok Build (8 agents), Windsurf (5 parallel), Claude Code Agent Teams, Codex CLI, Devin

Sources: https://www.morphllm.com/ai-coding-agent, https://unanimoustech.com/unanimous-guide-ai-tools-for-developers-2026/

---

## Mercury Architecture Recommendations

### R1: Adopt Context Reset over Compaction for Long Sessions
Mercury's multi-agent sessions should use structured handoff documents (JSON format) between context windows rather than relying on compaction. This prevents 'context anxiety' and premature task completion. Mercury already uses HANDOFF-SESSION-*.json 鈥 →  formalize this as the primary persistence mechanism.

### R2: Implement Three-Tier Checkpointing
1. **Git commits** after each discrete unit of work (already in Mercury workflow)
2. **Progress JSON files** (feature_list.json + progress.txt pattern) for intra-session state
3. **Session IDs** via Claude Agent SDK for resume/fork capability

This layered approach provides recovery at any granularity.

### R3: Separate Generation from Evaluation
Mercury's dispatch architecture should never allow an agent to self-evaluate. Use dedicated evaluator agents (or at minimum, separate evaluation sessions with fresh context) for quality assurance. This directly addresses the 'confidently praising mediocre work' failure mode.

### R4: Build on Claude Agent SDK for Core Harness
The SDK provides exactly what Mercury needs: built-in tools, session resume/fork, hooks lifecycle, subagent delegation, MCP integrations. It's batteries-included and proven at scale (powers Claude Code itself). Mercury should use the SDK as its harness foundation rather than building custom orchestration.

### R5: Implement Resource Guardrails
- Per-task token budgets with soft warnings at 80%
- Per-session cost caps with circuit breakers
- Tool allowlists per agent role (research agents get Read/Glob/Grep/Web only; dev agents get full tool access)
- Return token consumption in agent results for self-monitoring

### R6: Session Initialization Health Check Protocol
Every Mercury agent session should begin with the 5-step health check: verify directory 鈫 →  read state artifacts 鈫 →  review task status 鈫 →  run baseline tests 鈫 →  only then begin work. This catches regressions before they compound.

### R7: Use JSON for All Agent-Facing State Files
JSON over Markdown for feature lists, task bundles, and progress tracking. Models are less likely to inappropriately modify structured JSON. Mercury_KB already uses JSON for handoffs 鈥 →  extend this to all agent-consumed state.

### R8: Design Modular, Deletable Components
Follow Philipp Schmid's 'build to delete' principle. Manus refactored 5 times in 6 months. Each model generation may invalidate current harness logic. Keep harness components loosely coupled so individual pieces can be replaced without cascading changes.

### R9: Capture Trajectories as Training Data
'Competitive advantage is no longer the prompt. It is the trajectories your Harness captures.' Mercury should log complete agent execution traces (tool calls, decisions, outcomes) as structured data for future optimization and debugging.

### R10: Adopt Isolated Worktrees for Parallel Agents
Mercury already identified branch collision as a problem (project_dispatch_architecture_findings.md). Use git worktrees (Claude Agent SDK supports `isolation: 'worktree'`) to give each agent an isolated copy of the repository. This eliminates the branch collision issue entirely.

