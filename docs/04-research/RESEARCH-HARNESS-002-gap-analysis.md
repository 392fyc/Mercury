# Mercury Gap Analysis vs Harness Best Practices

> Task: TASK-ef7f6301 | Status: closed | Verdict: pass
> Completed: 2026-03-29T10:22:27.470Z

---

=== EXECUTIVE SUMMARY: TOP 3 CRITICAL GAPS ===

---



---

GAP #1 鈥 ->  HARNESS MIDDLEWARE PIPELINE (R4, R8): Mercury has no composable middleware stack for cross-cutting agent concerns. LangChain demonstrated +13.7% on TerminalBench with zero model changes using LoopDetectionMiddleware, PreCompletionChecklistMiddleware, and LocalContextMiddleware. Mercury's orchestrator is monolithic 鈥 ->  harness concerns are scattered across orchestrator.ts (3000+ lines) rather than isolated as pluggable middleware.

---



---

GAP #2 鈥 ->  OBSERVABILITY & TRACING (R9): Mercury has zero OpenTelemetry integration. The EventBus emits typed events but there is no trace hierarchy (session鈫抰race鈫抯pan), no trace export, no Langfuse/OTEL collector. Multiple industry sources (Langfuse, Anthropic, Cursor) converge on OTEL as the standard for agent telemetry. Mercury cannot debug non-deterministic agent behavior without glass-box tracing.

---



---

GAP #3 鈥 ->  EVAL ENGINE (R10): Mercury has no automated evaluation framework. No pass@k/pass^k metrics, no code-based or model-based graders, no capability鈫抮egression eval graduation pipeline. Anthropic's eval taxonomy is the backbone of harness engineering quality 鈥 ->  without it, Mercury cannot systematically measure or improve agent effectiveness.

---



---

=== R1-R10 DETAILED ASSESSMENT ===

---



---

--- R1: Session Handoff Protocol for Long-Running Tasks ---

---

Status: PARTIALLY IMPLEMENTED

---

Evidence:

---

  - ClaudeAdapter.handoffSession() (claude-adapter.ts:686-713) creates new sessions inheriting context from old ones on overflow

---

  - SessionInfo tracks parentSessionId, frozenSystemPrompt, frozenRole for continuity

---

  - Orchestrator.checkTokenThreshold() (orchestrator.ts:1661-1691) detects 70% context usage and emits orchestrator.context.compact event

---

  - TaskBundle.implementationReceipt preserves work state across sessions (sot-workflow.md)

---

  - SessionPersistence (session-persistence.ts) saves state atomically to disk via tmp+rename

---

Gap: No automatic session handoff execution 鈥 ->  the 70% threshold only emits an event; no code actually triggers handoffSession() or injects a summary into the new session. The protocol exists but is not wired end-to-end. No Anthropic-style 'verification-first session start' (read git log + progress before implementing). No structured progress file (claude-progress.txt pattern).

---

Priority: CRITICAL

---

Effort: MEDIUM 鈥 ->  wiring exists; need to connect checkpoint event 鈫 ->  handoff trigger 鈫 ->  summary injection

---



---

--- R2: Structured Delegation with Explicit Boundaries ---

---

Status: FULLY IMPLEMENTED

---

Evidence:

---

  - TaskBundle type (core package) contains: codeScope, readScope, allowedWriteScope, definitionOfDone, requiredEvidence, context, modelRecommendation

---

  - Dev role YAML (.mercury/roles/dev.yaml) enforces: allowedWriteScope file restrictions, forbidden actions (no scope expansion, no branch switching)

---

  - TaskManager.validateScope() (task-manager.ts:513+) checks file-level scope violations and emits orchestrator.scope.violation events

---

  - Dispatch prompt templates (.mercury/templates/) include explicit task boundaries

---

  - G9 auto-triage (task-manager.ts:126-220) scores agents by capabilities/model/complexity

---

Gap: None significant 鈥 ->  this is Mercury's strongest feature. Minor enhancement: add explicit tool guidance and scaling hints per-task as Anthropic recommends.

---

Priority: Nice-to-have (already strong)

---

Effort: LOW

---



---

--- R3: Error Recovery & Crash Resilience ---

---

Status: PARTIALLY IMPLEMENTED

---

Evidence:

---

  - Orchestrator.recoverOrphanedTasks() (orchestrator.ts:584-633) detects tasks in dispatched/in_progress with dead sessions and re-dispatches

---

  - G2 crash recovery in streamMessages catch handler (orchestrator.ts:1629-1650) auto-re-dispatches on session crash with retry budget

---

  - dispatchBundleTask() (orchestrator.ts:2639+) implements exponential backoff retry: min(300*2^attempt, 30000)+jitter

---

  - Task.maxDispatchAttempts (default 5) + dispatchAttempts counter track retry budget

---

  - ClaudeAdapter catches transport crash errors (claude-adapter.ts:621-637), preserves resumeToken for recovery

---

  - isTransportCrashError() utility detects SDK ProcessTransport disconnects

---

Gap: No checkpoint/resume within a single session (only full-session re-dispatch). No partial work preservation 鈥 ->  if agent crashes mid-implementation, all in-session work is lost unless committed to git. No deterministic replay from event log.

---

Priority: IMPORTANT

---

Effort: HIGH 鈥 ->  requires event-sourcing for in-session state reconstruction

---



---

--- R4: Context Management (Compaction, Pruning, Budgets) ---

---

Status: PARTIALLY IMPLEMENTED

---

Evidence:

---

  - TOKEN_CHECKPOINT_THRESHOLD = 0.7 (orchestrator.ts:103) detects context exhaustion

---

  - checkTokenThreshold() emits orchestrator.context.compact event + RPC notification

---

  - ClaudeAdapter tracks tokenUsage/tokenLimit per session via streaming events (claude-adapter.ts:519-533)

---

  - Sub-agent isolation via separate sessions with frozen system prompts

---

  - KB context injection is role-scoped (buildAndInjectContext, getRoleSpecificContext)

---

Gap: No actual compaction logic 鈥 ->  the event is emitted but nothing summarizes/compresses context. No two-tier pruning (soft-trim head/tail, hard-clear). No context budget warnings to agents. No 'reasoning sandwich' pattern (xhigh reasoning for planning, high for execution, xhigh for verification). No Inngest-style step-level atomicity.

---

Priority: CRITICAL

---

Effort: MEDIUM 鈥 ->  detection exists; need compaction strategy implementation and budget injection into agent prompts

---



---

--- R5: Git-Based State Tracking & Recovery ---

---

Status: FULLY IMPLEMENTED

---

Evidence:

---

  - Dev role convention: branch naming {agent-cli-name}/{task-name}, commit format {type}({task_id}): {summary} (dev.yaml)

---

  - TaskBundle tracks branch name, git state is the canonical handoff mechanism (sot-workflow.md:82-89)

---

  - Session handoff uses TaskBundle.context + implementationReceipt + git log for continuity

---

  - Atomic session persistence via tmp+rename (session-persistence.ts:28-31)

---

Gap: No git-based revert on agent failure (Anthropic's pattern). No automatic baseline commit before agent starts work. JSON feature tracking pattern not adopted (Anthropic recommends JSON over Markdown for model-facing state).

---

Priority: Nice-to-have

---

Effort: LOW

---



---

--- R6: One-Feature-at-a-Time Incremental Progress ---

---

Status: FULLY IMPLEMENTED

---

Evidence:

---

  - SoT task flow enforces single-task dispatch per session (sot-workflow.md)

---

  - TaskBundle is atomic: one task, one branch, one agent, one receipt

---

  - Dev role forbidden from picking up additional work after completion (dev.yaml:37)

---

  - Task state machine prevents parallel task execution on same session (VALID_TRANSITIONS in task-manager.ts:31-42)

---

Gap: None.

---

Priority: N/A (complete)

---

Effort: N/A

---



---

--- R7: Verification Pipeline (Self-Verification Loop) ---

---

Status: PARTIALLY IMPLEMENTED

---

Evidence:

---

  - Two-stage verification: Main Review (orchestrator.ts:2969+) + Acceptance blind review (sot-workflow.md)

---

  - Critic verification agent (task-manager.ts:1378+) provides spec-driven DoD validation via buildCriticPrompt()

---

  - MCP tools: get_critic_prompt and record_critic_result (mcp-server.ts:247+)

---

  - Pre-checks with configurable timeout (DEFAULT_PRECHECK_TIMEOUT_MS = 5min, orchestrator.ts:100)

---

Gap: No LangChain-style PreCompletionChecklistMiddleware intercepting agent before exit. No rules-based verification integrated into agent loop (linting). No visual verification (screenshot-based). Verification is post-hoc (after implementation_done), not inline during execution. No self-verification loop (plan鈫抌uild鈫抳erify鈫抐ix).

---

Priority: IMPORTANT

---

Effort: MEDIUM

---



---

--- R8: Loop Detection & Doom Loop Prevention ---

---

Status: NOT IMPLEMENTED

---

Evidence:

---

  - No LoopDetectionMiddleware equivalent found in codebase

---

  - maxTurns: 30 in ClaudeAdapter (claude-adapter.ts:349) is a blunt ceiling, not behavioral detection

---

  - No file-edit tracking to detect repeated modifications to same files

---

Gap: Complete gap. LangChain's LoopDetectionMiddleware tracks file edits and suggests reconsidering after repeated modifications. Mercury has no mechanism to detect when an agent is spinning without progress.

---

Priority: IMPORTANT

---

Effort: MEDIUM 鈥 ->  could be implemented as an EventBus listener counting tool_use patterns

---



---

--- R9: Observability & Production Tracing ---

---

Status: NOT IMPLEMENTED (basic events only)

---

Evidence:

---

  - EventBus exists (core package) and forwards all events as RPC notifications (orchestrator.ts:147-157)

---

  - SSE heartbeat for connection health (index.ts:443-446)

---

  - /health endpoint (index.ts:482, mcp-server.ts:376)

---

  - TranscriptPersistence saves conversation transcripts to disk

---

Gap: No OpenTelemetry integration. No trace hierarchy (session鈫抰race鈫抩bservation). No span-level timing/cost attribution. No trace export to external systems (Langfuse, Jaeger). No agent decision pattern monitoring. No production tracing for non-deterministic behavior debugging. EventBus events are flat, not hierarchical traces.

---

Priority: CRITICAL

---

Effort: HIGH 鈥 ->  requires new infrastructure layer, trace correlation IDs, span management

---



---

--- R10: Eval-Driven Development ---

---

Status: NOT IMPLEMENTED

---

Evidence:

---

  - No eval harness, no test suites for agent behavior, no graders

---

  - Acceptance testing is human/agent-driven (acceptance role in SoT) but not automated eval

---

  - No pass@k or pass^k metrics

---

Gap: Complete gap. No evaluation framework for systematically measuring agent effectiveness. No capability-to-regression eval graduation. No code-based or model-based graders. Anthropic recommends starting with ~20 representative tasks.

---

Priority: CRITICAL (for long-term quality)

---

Effort: HIGH 鈥 ->  new subsystem required

---



---

=== RESEARCH AREA COMPARISON MATRIX ===

---



---

--- Area 1: Session Persistence & Checkpoint/Resume ---

---

Mercury Current State: Session state persisted to .mercury/sessions.json via atomic write. TokenCheckpoint at 70% emits event but doesn't auto-handoff. ClaudeAdapter stores resumeToken (SDK session ID) for resume after restart. TaskBundle + git branch serve as cross-session handoff. Transcript persistence saves conversation history.

---

Industry Best Practice: Anthropic 鈥 ->  structured progress files (claude-progress.txt), verification-first session starts, JSON feature checklists. Inngest 鈥 ->  step-level atomicity with independently retryable steps. OpenHands 鈥 ->  event-sourced deterministic replay.

---

Gap Level: MEDIUM 鈥 ->  foundation solid, auto-handoff execution and structured progress files missing

---



---

--- Area 2: Self-Monitoring & Health Checks ---

---

Mercury Current State: /health endpoint returns pong (mcp-server.ts:376). SSE heartbeat at 30s intervals (index.ts:443). Token usage tracking per session. No agent-level health monitoring.

---

Industry Best Practice: Cursor 鈥 ->  router-level health/latency monitoring. LangChain 鈥 ->  middleware stack with loop detection, stuck-agent detection. Langfuse 鈥 ->  three-phase evaluation progression with online monitoring.

---

Gap Level: HIGH 鈥 ->  only basic infrastructure health; no agent behavioral health monitoring

---



---

--- Area 3: Multi-Agent Coordination ---

---

Mercury Current State: Orchestrator-Workers pattern (Main dispatches to dev/research/acceptance/design). TaskBundle state machine with 11 states and validated transitions. Role-scoped sessions with frozen system prompts. G9 auto-triage for agent selection. Task-to-session binding. Single-agent-per-task constraint.

---

vs LangGraph: LangGraph uses graph-based state machines with conditional edges. Mercury's state machine is linear with limited branching (rework loops). LangGraph has built-in checkpointing; Mercury's is custom.

---

vs CrewAI: CrewAI has role-based agents with memory sharing. Mercury's role system is similar but more structured (YAML definitions, input/output boundaries). CrewAI supports inter-agent delegation; Mercury's dev agents cannot delegate.

---

vs Claude Agent SDK patterns: Mercury wraps Claude Agent SDK's query() API correctly. Sub-agent spawning delegates to Claude Code's built-in Agent tool. Mercury adds TaskBundle structure, scope validation, and multi-agent routing on top of SDK primitives.

---

Gap Level: LOW-MEDIUM 鈥 ->  Mercury's coordination is strong; missing wave-based parallel scheduling (GSD pattern) and A2A protocol support

---



---

--- Area 4: Resource Management ---

---

Mercury Current State: tokenUsage/tokenLimit tracked per session via streaming events (claude-adapter.ts:519-533). maxTurns=30 as execution ceiling. maxConcurrentSessions=5 per adapter. Token checkpoint at 70%. No cost tracking, no spending limits, no per-task token budgets.

---

Industry Best Practice: Anthropic 鈥 ->  scaling rules (simple: 3-10 tool calls, complex: 50+). Cursor 鈥 ->  context compaction with -46.9% token savings. Inngest 鈥 ->  context budget warnings + two-tier pruning. Multiple sources 鈥 ->  per-agent and per-task cost attribution.

---

Gap Level: MEDIUM-HIGH 鈥 ->  basic tracking exists but no budgets, no cost controls, no per-task resource allocation

---



---

--- Area 5: Error Recovery & Resilience ---

---

Mercury Current State: Strong. Orphaned task recovery on startup (orchestrator.ts:584-633). G2 crash recovery with automatic re-dispatch (orchestrator.ts:1629-1650). Exponential backoff retry with jitter (orchestrator.ts:2630-2637). Transport crash detection with resume token preservation (claude-adapter.ts:621-637). maxDispatchAttempts budget per task. Atomic session persistence.

---

Industry Best Practice: Anthropic 鈥 ->  resume from failure point, deterministic checkpoints. Inngest 鈥 ->  step-level retryability. OpenHands 鈥 ->  event-sourced replay for recovery.

---

Gap Level: LOW 鈥 ->  Mercury's error recovery is production-grade; only lacking mid-session checkpoint/resume and event-sourced replay

---



---

--- Area 6: Real-World Alignment ---

---

Mercury sits between Cursor (deeply integrated IDE) and Devin (fully autonomous cloud agent). Mercury's unique position: desktop GUI orchestrating multiple heterogeneous CLI agents (Claude, Codex, Gemini, opencode). This is distinct from all existing tools.

---

vs Devin: Devin runs fully autonomous in cloud sandboxes. Mercury runs locally with human-in-the-loop approval. Mercury lacks Devin's persistent cloud execution environment.

---

vs Cursor: Cursor is editor-embedded with deep code intelligence. Mercury is standalone orchestrator without code editor. Cursor has model routing, context compaction, sandbox infra that Mercury lacks.

---

vs Claude Code: Mercury wraps Claude Code via SDK. Adds multi-agent orchestration, task management, GUI, approval workflows. Claude Code is 'thin harness' philosophy; Mercury adds the thick orchestration layer.

---

Gap Level: MEDIUM 鈥 ->  Mercury has unique multi-agent GUI niche but lacks production infrastructure that Cursor/Devin have built (observability, eval, context management)

---



---

=== PRIORITIZED ACTION PLAN ===

---



---

Phase 1 鈥 ->  CRITICAL (implement first, highest ROI):

---

  1. Wire auto-handoff on context exhaustion: connect checkTokenThreshold event 鈫 ->  handoffSession() 鈫 ->  summary injection 鈫 ->  new session start. Files: orchestrator.ts, claude-adapter.ts. Effort: MEDIUM.

---

  2. Implement context compaction strategy: when 70% threshold hit, summarize conversation preserving key decisions and inject into new session. Add compaction middleware. Effort: MEDIUM.

---

  3. Add loop detection: EventBus listener tracking repeated tool_use on same files, emit warning after N iterations, suggest reconsidering approach. Effort: MEDIUM.

---



---

Phase 2 鈥 ->  IMPORTANT (next priority):

---

  4. Add verification-first session protocol: new sessions read git log + task receipt before starting implementation. Add to dispatch template. Effort: LOW.

---

  5. Pre-completion verification: before agent declares implementation_done, enforce a verification step (run tests, check DoD). Effort: MEDIUM.

---

  6. Token budget injection: add per-task token budget to TaskBundle, inject into agent prompt, emit warnings at thresholds. Effort: LOW-MEDIUM.

---



---

Phase 3 鈥 ->  STRATEGIC (longer-term investment):

---

  7. OpenTelemetry integration: add trace IDs to EventBus events, create span hierarchy, export to OTEL collector. Effort: HIGH.

---

  8. Basic eval framework: start with 20 representative tasks, code-based graders (test pass/fail, scope compliance), LLM-as-judge for quality. Effort: HIGH.

---

  9. Middleware architecture refactor: extract harness concerns from monolithic orchestrator.ts into composable middleware (LoopDetection, ContextCompaction, PreCompletion, SecurityAnalysis). Effort: HIGH.

---

  10. Wave-based parallel scheduling: support dispatching independent tasks in parallel with dependency-aware serialization (GSD pattern). Effort: MEDIUM-HIGH.

---

IMMEDIATE: Wire the existing 70% token checkpoint event to actually trigger session handoff 鈥 ->  the detection exists but the response doesn't. This is the single highest-ROI fix.

IMMEDIATE: Add loop detection as an EventBus listener 鈥 ->  low effort, prevents expensive doom loops that waste tokens and time.

SHORT-TERM: Inject token budgets and scaling hints into task dispatch prompts 鈥 ->  Mercury's TaskBundle already has the structure, just needs budget fields.

SHORT-TERM: Add verification-first session start to dispatch templates 鈥 ->  read git log, check existing work before starting.

MEDIUM-TERM: Refactor orchestrator.ts (3000+ lines) into middleware architecture 鈥 ->  current monolithic structure makes it hard to add new harness concerns.

MEDIUM-TERM: Start OpenTelemetry integration 鈥 ->  even basic trace correlation IDs in EventBus events would dramatically improve debugging.

LONG-TERM: Build eval framework starting with 20 representative tasks from real usage 鈥 ->  this is the foundation for systematic quality improvement.

ARCHITECTURAL: Mercury's TaskBundle + SoT state machine is genuinely best-in-class for structured delegation (R2). Preserve this as a differentiator.

ARCHITECTURAL: Mercury's error recovery (G2 crash recovery, exponential backoff, orphaned task detection) is production-grade. This is ahead of most frameworks.

WATCH: 'Design for deletion' 鈥 ->  as Claude models improve, some harness features (e.g., loop detection) may become unnecessary. Keep components modular and removable.

