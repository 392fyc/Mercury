# Phase 3 Deep Research: OTel Tracing, Middleware Architecture, Wave-Based Parallel Scheduling

**Task**: TASK-853f8e03
**Date**: 2026-04-01
**Author**: claude-code (dev)
**Status**: Complete

---

## Table of Contents

1. [OTel Integration for Mercury Orchestrator](#1-otel-integration-for-mercury-orchestrator)
2. [Middleware Architecture Refactor](#2-middleware-architecture-refactor)
3. [Wave-Based Parallel Scheduling](#3-wave-based-parallel-scheduling)
4. [everything-claude-code Adaptation](#4-everything-claude-code-adaptation)

---

## 1. OTel Integration for Mercury Orchestrator

### 1.1 Required npm Packages (Verified Versions)

| Package | Version | Purpose |
|---------|---------|---------|
| `@opentelemetry/api` | 1.9.1 | Stable tracing API (decoupled from SDK) |
| `@opentelemetry/sdk-node` | 0.214.0 | All-in-one Node.js SDK (includes trace + metrics + context) |
| `@opentelemetry/context-async-hooks` | (bundled with sdk-node) | AsyncLocalStorage-based context manager (default) |
| `@opentelemetry/exporter-trace-otlp-proto` | latest (follows sdk-node) | OTLP/HTTP+protobuf trace exporter |
| `@opentelemetry/resources` | (bundled with sdk-node) | Service name/version resource attributes |
| `@opentelemetry/semantic-conventions` | latest | Standard attribute names including GenAI conventions |

**Source**: [npm @opentelemetry/sdk-node](https://www.npmjs.com/package/@opentelemetry/sdk-node), [npm @opentelemetry/api](https://www.npmjs.com/package/@opentelemetry/api)

> **Note**: `@opentelemetry/sdk-node` v0.214.0 bundles `sdk-trace-node`, `context-async-hooks`, and `resources`. No need to install them separately unless overriding defaults.

### 1.2 Span Naming Convention for Mercury

Following the OTel `{verb} {object}` convention ([How to Name Your Spans | OpenTelemetry](https://opentelemetry.io/blog/2025/how-to-name-your-spans/)) and the GenAI agent semantic conventions ([GenAI Agent Spans | OpenTelemetry](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)):

```
mercury.orchestrator.init                  # Orchestrator bootstrap
mercury.orchestrator.shutdown              # Graceful shutdown

mercury.task.create         {taskId}       # Task creation + auto-triage
mercury.task.dispatch       {taskId}       # Bundle dispatch to agent
mercury.task.execute        {taskId}       # Full task execution lifecycle
mercury.task.complete       {taskId}       # Completion handler (role-specific)

mercury.session.start       {sessionId}    # Agent session lifecycle
mercury.session.stream      {sessionId}    # streamMessages() loop
mercury.session.handoff     {sessionId}    # Token-threshold handoff
mercury.session.end         {sessionId}    # Session termination

mercury.rpc.handle          {method}       # RPC method dispatch
mercury.kb.read             {path}         # Knowledge base operations
mercury.kb.write            {path}         # Knowledge base mutations

mercury.agent.prompt        {agentId}      # sendPrompt() to agent
mercury.agent.tool_call     {toolName}     # Tool invocation within agent
mercury.agent.approval      {requestId}    # Approval request/resolution

mercury.critic.evaluate     {taskId}       # DoD verification
mercury.acceptance.run      {taskId}       # Acceptance testing
```

**Attribute naming** follows OTel conventions ([Naming | OpenTelemetry](https://opentelemetry.io/docs/specs/semconv/general/naming/)):
- `mercury.task.id` — Task identifier
- `mercury.session.id` — Session identifier
- `mercury.agent.id` — Agent performing work
- `mercury.agent.role` — Role (dev/main/reviewer/researcher)
- `mercury.task.status` — Current task state machine status
- `mercury.rework.count` — Number of rework iterations
- `gen_ai.request.model` — LLM model used (per GenAI semconv)
- `gen_ai.usage.input_tokens` — Token usage (per GenAI semconv)
- `gen_ai.usage.output_tokens` — Token usage (per GenAI semconv)

### 1.3 Context Propagation Across Async Boundaries

**Problem**: Mercury's `streamMessages()` is an async generator that yields messages across event loop ticks. Trace context must survive these boundaries.

**Solution**: `AsyncLocalStorageContextManager` (default in `@opentelemetry/sdk-node`).

```typescript
// instrumentation.ts — loaded before app via --import
import { NodeSDK } from '@opentelemetry/sdk-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-proto';
import { Resource } from '@opentelemetry/resources';

const sdk = new NodeSDK({
  resource: new Resource({
    'service.name': 'mercury-orchestrator',
    'service.version': '1.0.0',
  }),
  traceExporter: new OTLPTraceExporter({
    url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || 'http://localhost:4318/v1/traces',
  }),
  // AsyncLocalStorageContextManager is the default — no explicit config needed
});

sdk.start();

process.on('SIGTERM', () => sdk.shutdown());
```

**Key propagation patterns for Mercury**:

1. **Task dispatch → agent session**: Parent span `mercury.task.dispatch` creates child span `mercury.session.start`. Context propagated via `context.with(trace.setSpan(ctx, parentSpan), callback)`.

2. **Async generator (`streamMessages()`)**: AsyncLocalStorage automatically propagates context through `for await...of` loops. No manual intervention needed.

3. **Event bus handlers**: EventBus `emit()` → handlers run in the same async context. For fire-and-forget handlers (e.g., callback delivery), explicitly capture and restore context:
   ```typescript
   const currentCtx = context.active();
   setImmediate(() => {
     context.with(currentCtx, () => deliverTaskCallback(taskId));
   });
   ```

4. **Cross-session handoff**: When `handoffSession()` creates a new session, the new session span should be a child of the task span (not the old session span). Extract `taskId` → look up task span → use as parent.

**Source**: [Context | OpenTelemetry JS](https://opentelemetry.io/docs/languages/js/context/), [AsyncLocalStorage Context Manager](https://www.npmjs.com/package/@opentelemetry/context-async-hooks)

> **Performance note**: AsyncLocalStorage imposes ~7% overhead in benchmarks. Node.js v24+ delivers further improvements. Given Mercury orchestrator is I/O-bound (waiting on LLM responses), this overhead is negligible. Source: [The Hidden Cost of Async Context in Node.js](https://blog.platformatic.dev/the-hidden-cost-of-context)

### 1.4 Exporter Configuration

**Recommended**: OTLP/HTTP+protobuf (not gRPC) for simplicity. No native gRPC dependency needed.

```typescript
// Environment-variable based config (OTel standard)
// OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
// OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
// OTEL_SERVICE_NAME=mercury-orchestrator
```

**Compatible backends**: Jaeger (native OTLP), Grafana Tempo, Honeycomb, Datadog (v1.37+ supports GenAI semconv natively), any OTEL Collector.

**Source**: [Exporters | OpenTelemetry JS](https://opentelemetry.io/docs/languages/js/exporters/), [OTLP Exporter Configuration](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)

### 1.5 GenAI Semantic Conventions for LLM Calls

Mercury should adopt the OTel GenAI semantic conventions for agent↔LLM interactions:

| Attribute | Example | Source |
|-----------|---------|--------|
| `gen_ai.system` | `anthropic` | [GenAI Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/) |
| `gen_ai.request.model` | `claude-opus-4-6` | GenAI Spans |
| `gen_ai.operation.name` | `chat` | GenAI Spans |
| `gen_ai.usage.input_tokens` | `4500` | GenAI Spans |
| `gen_ai.usage.output_tokens` | `1200` | GenAI Spans |
| `gen_ai.agent.name` | `mercury-dev` | [GenAI Agent Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/) |

**Span hierarchy for a task dispatch**:
```
mercury.task.execute (root)
  └─ mercury.task.dispatch
       └─ mercury.session.start
            └─ mercury.session.stream
                 ├─ mercury.agent.prompt (gen_ai.operation.name=chat)
                 ├─ mercury.agent.tool_call (tool: "Read")
                 ├─ mercury.agent.tool_call (tool: "Edit")
                 └─ mercury.agent.prompt (gen_ai.operation.name=chat)
            └─ mercury.task.complete
```

### 1.6 Implementation Insertion Points (Mercury Codebase)

| Location | What to Instrument |
|----------|--------------------|
| `orchestrator.ts` constructor | Initialize NodeSDK, create root tracer |
| `orchestrator.ts:handleRpc()` (~L752) | Wrap each RPC method in `mercury.rpc.handle` span |
| `orchestrator.ts:dispatchBundleTask()` (~L2992) | `mercury.task.dispatch` span with retry attributes |
| `orchestrator.ts:streamMessages()` (~L1568) | `mercury.session.stream` span per message batch |
| `orchestrator.ts:handleDevTaskStreamComplete()` (~L1911) | `mercury.task.complete` span |
| `orchestrator.ts:triggerSessionHandoff()` (~L1760) | `mercury.session.handoff` span |
| `knowledge-service.ts` read/write methods | `mercury.kb.read`/`mercury.kb.write` spans |
| `callback-queue.ts:enqueue()/deliver()` | `mercury.callback.enqueue`/`mercury.callback.deliver` spans |

**Estimated effort**: 6-8 hours for basic instrumentation; 2-3 hours additional for GenAI attributes.

---

## 2. Middleware Architecture Refactor

### 2.1 Problem Statement

Mercury's `orchestrator.ts` is 3793 lines with cross-cutting concerns scattered throughout:
- Loop detection at L1818-1842 (hardcoded in `streamMessages()`)
- Token checkpoint at L1721-1754 (inline in message processing)
- Context injection at L305 (in `buildSystemRolePrompt()`)
- Approval handling via hooks (partial middleware pattern)
- Role-specific completion handlers (switch-case at L1854-2349)

This makes it difficult to add new cross-cutting concerns (OTel tracing, rate limiting, audit logging) without further bloating the orchestrator.

### 2.2 Proposed Middleware Interface

```typescript
/**
 * Middleware lifecycle hooks for the dispatch pipeline.
 * Each middleware runs in priority order (lower = earlier).
 * Async hooks support I/O operations (KB reads, external calls).
 */
interface Middleware {
  /** Unique middleware name for logging/config */
  readonly name: string;

  /** Enable/disable without removing from pipeline */
  enabled: boolean;

  /** Execution order: lower runs first. Suggested ranges:
   *  0-99: Security & validation
   *  100-199: Observability (tracing, metrics)
   *  200-299: Context enrichment
   *  300-399: Flow control (loop detection, rate limiting)
   *  400-499: Completion checks
   */
  readonly priority: number;

  /** Called when a new agent session starts */
  onSessionStart?(ctx: MiddlewareContext): Promise<void>;

  /** Called before each message is sent to the agent */
  onBeforePrompt?(ctx: MiddlewareContext, prompt: string): Promise<string>;

  /** Called after each message is received from the agent */
  onAfterMessage?(ctx: MiddlewareContext, message: AgentMessage): Promise<AgentMessage>;

  /** Called on each tool call within a session */
  onToolCall?(ctx: MiddlewareContext, toolCall: ToolCallInfo): Promise<ToolCallInfo | null>;

  /** Called when a session ends (success or failure) */
  onSessionEnd?(ctx: MiddlewareContext, result: SessionResult): Promise<void>;

  /** Called before task completion handler runs */
  onBeforeComplete?(ctx: MiddlewareContext, receipt: ImplementationReceipt): Promise<ImplementationReceipt>;

  /** Called on error — can transform or suppress errors */
  onError?(ctx: MiddlewareContext, error: Error): Promise<Error | null>;
}

interface MiddlewareContext {
  sessionId: string;
  taskId?: string;
  agentId: string;
  role: string;
  /** Shared key-value store for middleware to communicate */
  state: Map<string, unknown>;
  /** Access to EventBus for emitting events */
  bus: EventBus;
  /** OTel span for this context (if tracing middleware is active) */
  span?: Span;
}

interface ToolCallInfo {
  toolName: string;
  arguments: Record<string, unknown>;
  filePath?: string;
  timestamp: number;
}
```

### 2.3 Pipeline Executor Design

```typescript
class MiddlewarePipeline {
  private middlewares: Middleware[] = [];

  register(middleware: Middleware): void {
    this.middlewares.push(middleware);
    this.middlewares.sort((a, b) => a.priority - b.priority);
  }

  unregister(name: string): void {
    this.middlewares = this.middlewares.filter(m => m.name !== name);
  }

  /** Execute a specific hook across all enabled middleware */
  async execute<K extends keyof Middleware>(
    hook: K,
    ctx: MiddlewareContext,
    ...args: Parameters<NonNullable<Middleware[K]>>
  ): Promise<ReturnType<NonNullable<Middleware[K]>>> {
    let result = args[0]; // passthrough pattern
    for (const mw of this.middlewares) {
      if (!mw.enabled) continue;
      const fn = mw[hook];
      if (typeof fn === 'function') {
        try {
          result = await (fn as Function).call(mw, ctx, result);
        } catch (err) {
          // Middleware errors are isolated — log and continue
          console.error(`[Middleware:${mw.name}] ${hook} error:`, err);
        }
      }
    }
    return result;
  }
}
```

### 2.4 Built-In Middleware Implementations

#### 2.4.1 LoopDetectionMiddleware (priority: 300)

Extract existing logic from `orchestrator.ts` L1818-1842:

```typescript
class LoopDetectionMiddleware implements Middleware {
  readonly name = 'loop-detection';
  enabled = true;
  readonly priority = 300;

  private windowSize = 6;
  private threshold = 3;
  private recentCalls: Map<string, ToolCallInfo[]> = new Map();

  async onToolCall(ctx: MiddlewareContext, toolCall: ToolCallInfo): Promise<ToolCallInfo | null> {
    const key = ctx.sessionId;
    const calls = this.recentCalls.get(key) ?? [];
    calls.push(toolCall);
    if (calls.length > this.windowSize) calls.shift();
    this.recentCalls.set(key, calls);

    // Check for repeated identical tool calls
    const recent = calls.slice(-this.threshold);
    if (recent.length === this.threshold) {
      const allSame = recent.every(c =>
        c.toolName === recent[0].toolName &&
        c.filePath === recent[0].filePath
      );
      if (allSame) {
        ctx.bus.emit('orchestrator.loop.detected', ctx.agentId, ctx.sessionId, {
          toolName: toolCall.toolName,
          count: this.threshold,
        });
        return null; // suppress the looping call
      }
    }
    return toolCall;
  }

  async onSessionEnd(ctx: MiddlewareContext): Promise<void> {
    this.recentCalls.delete(ctx.sessionId);
  }
}
```

#### 2.4.2 OTelTracingMiddleware (priority: 100)

```typescript
class OTelTracingMiddleware implements Middleware {
  readonly name = 'otel-tracing';
  enabled = true;
  readonly priority = 100;

  private tracer: Tracer;

  constructor(tracer: Tracer) {
    this.tracer = tracer;
  }

  async onSessionStart(ctx: MiddlewareContext): Promise<void> {
    const span = this.tracer.startSpan('mercury.session.stream', {
      attributes: {
        'mercury.session.id': ctx.sessionId,
        'mercury.agent.id': ctx.agentId,
        'mercury.agent.role': ctx.role,
        'mercury.task.id': ctx.taskId ?? '',
      },
    });
    ctx.span = span;
    ctx.state.set('otel.span', span);
  }

  async onToolCall(ctx: MiddlewareContext, toolCall: ToolCallInfo): Promise<ToolCallInfo> {
    const parentSpan = ctx.state.get('otel.span') as Span | undefined;
    if (parentSpan) {
      const childSpan = this.tracer.startSpan('mercury.agent.tool_call', {
        attributes: { 'mercury.tool.name': toolCall.toolName },
      });
      childSpan.end();
    }
    return toolCall;
  }

  async onSessionEnd(ctx: MiddlewareContext): Promise<void> {
    const span = ctx.state.get('otel.span') as Span | undefined;
    span?.end();
  }
}
```

#### 2.4.3 TokenCheckpointMiddleware (priority: 200)

Extract from `orchestrator.ts` L1721-1754:

```typescript
class TokenCheckpointMiddleware implements Middleware {
  readonly name = 'token-checkpoint';
  enabled = true;
  readonly priority = 200;

  private threshold = 0.7; // 70%

  async onAfterMessage(ctx: MiddlewareContext, message: AgentMessage): Promise<AgentMessage> {
    const tokenUsage = message.usage;
    if (tokenUsage && tokenUsage.total > 0) {
      const ratio = tokenUsage.used / tokenUsage.total;
      if (ratio >= this.threshold) {
        ctx.bus.emit('orchestrator.context.compact', ctx.agentId, ctx.sessionId, {
          usedTokens: tokenUsage.used,
          totalTokens: tokenUsage.total,
          ratio,
        });
      }
    }
    return message;
  }
}
```

#### 2.4.4 PreCompletionCheckMiddleware (priority: 400)

```typescript
class PreCompletionCheckMiddleware implements Middleware {
  readonly name = 'pre-completion-check';
  enabled = true;
  readonly priority = 400;

  async onBeforeComplete(
    ctx: MiddlewareContext,
    receipt: ImplementationReceipt
  ): Promise<ImplementationReceipt> {
    // Validate receipt has required fields
    const missing: string[] = [];
    if (!receipt.summary) missing.push('summary');
    if (!receipt.changedFiles?.length) missing.push('changedFiles');
    if (!receipt.completedAt) missing.push('completedAt');

    if (missing.length > 0) {
      ctx.bus.emit('orchestrator.receipt.incomplete', ctx.agentId, ctx.sessionId, {
        taskId: ctx.taskId,
        missingFields: missing,
      });
    }
    return receipt;
  }
}
```

### 2.5 Integration with Existing Orchestrator

**Retrofitting strategy** — minimize disruption:

1. **Create `middleware-pipeline.ts`** with `Middleware`, `MiddlewareContext`, `MiddlewarePipeline` types.
2. **Add `pipeline` field to Orchestrator** constructor.
3. **Wrap `streamMessages()`** body with pipeline hooks:
   ```typescript
   // Before (current)
   for await (const msg of adapter.streamMessages(sessionId)) {
     // ... inline loop detection, token check, etc.
   }

   // After (middleware-aware)
   await this.pipeline.execute('onSessionStart', ctx);
   for await (const msg of adapter.streamMessages(sessionId)) {
     const processed = await this.pipeline.execute('onAfterMessage', ctx, msg);
     // ... only core routing logic remains
   }
   await this.pipeline.execute('onSessionEnd', ctx, result);
   ```
4. **Extract existing inline logic** into middleware classes (loop detection, token checkpoint).
5. **Register middleware in constructor** or via config:
   ```typescript
   this.pipeline.register(new OTelTracingMiddleware(tracer));
   this.pipeline.register(new LoopDetectionMiddleware());
   this.pipeline.register(new TokenCheckpointMiddleware());
   this.pipeline.register(new PreCompletionCheckMiddleware());
   ```

### 2.6 Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Async hooks (not sync) | Middleware may need I/O (KB reads, OTel export) |
| Error isolation per middleware | One middleware failure must not break the pipeline |
| Priority-ordered execution | Tracing must start before other middleware can use spans |
| Passthrough pattern (not chain-of-responsibility) | Each middleware transforms the value, not decides to continue |
| `MiddlewareContext.state` map | Middleware communicate without coupling (e.g., tracing → others) |

**Estimated effort**: 12-15 hours total. 4 hours for interface + pipeline. 3 hours per extracted middleware. 2 hours for integration testing.

---

## 3. Wave-Based Parallel Scheduling

### 3.1 Problem Statement

Mercury currently dispatches tasks sequentially — one agent per task, one task at a time. For complex milestones with multiple independent sub-tasks, this creates unnecessary serialization. A wave-based scheduler enables parallel execution of independent tasks while respecting dependency ordering.

### 3.2 Dependency Graph Data Model

Extend `TaskBundle` with dependency metadata:

```typescript
interface TaskDependencies {
  /** Tasks that must complete before this task can start */
  blockedBy: string[];      // taskId[]

  /** Soft ordering: prefer to run after these, but don't block */
  mustRunAfter?: string[];   // taskId[]
}

interface TaskScheduling {
  /** Computed wave number (0 = first wave, no dependencies) */
  wave: number;

  /** Whether this task can run in parallel with others in its wave */
  parallel: boolean;

  /** Maximum concurrent tasks in this wave (0 = unlimited) */
  maxConcurrency?: number;
}

// Extended TaskBundle
interface TaskBundle {
  // ... existing fields ...
  dependencies?: TaskDependencies;
  scheduling?: TaskScheduling;
}
```

### 3.3 Wave Detection Algorithm

Based on Kahn's algorithm for topological sorting ([Topological Sorting | Wikipedia](https://en.wikipedia.org/wiki/Topological_sorting)):

```typescript
interface WaveAssignment {
  wave: number;
  tasks: string[];  // taskIds in this wave
}

function computeWaves(tasks: Map<string, TaskBundle>): WaveAssignment[] {
  // 1. Build adjacency list and in-degree map
  const inDegree = new Map<string, number>();
  const dependents = new Map<string, string[]>(); // task → tasks that depend on it

  for (const [id, task] of tasks) {
    inDegree.set(id, 0);
    dependents.set(id, []);
  }

  for (const [id, task] of tasks) {
    const deps = task.dependencies?.blockedBy ?? [];
    inDegree.set(id, deps.length);
    for (const dep of deps) {
      dependents.get(dep)?.push(id);
    }
  }

  // 2. Cycle detection — if any node never reaches in-degree 0, there's a cycle
  const visited = new Set<string>();
  const waves: WaveAssignment[] = [];

  // 3. BFS by waves (modified Kahn's)
  let currentWave: string[] = [];

  // Wave 0: all tasks with no dependencies
  for (const [id, degree] of inDegree) {
    if (degree === 0) currentWave.push(id);
  }

  let waveNum = 0;
  while (currentWave.length > 0) {
    waves.push({ wave: waveNum, tasks: [...currentWave] });
    currentWave.forEach(id => visited.add(id));

    const nextWave: string[] = [];
    for (const id of currentWave) {
      for (const dependent of (dependents.get(id) ?? [])) {
        const newDegree = (inDegree.get(dependent) ?? 1) - 1;
        inDegree.set(dependent, newDegree);
        if (newDegree === 0 && !visited.has(dependent)) {
          nextWave.push(dependent);
        }
      }
    }

    currentWave = nextWave;
    waveNum++;
  }

  // 4. Cycle check
  if (visited.size < tasks.size) {
    const cycleNodes = [...tasks.keys()].filter(id => !visited.has(id));
    throw new Error(`Dependency cycle detected: ${cycleNodes.join(', ')}`);
  }

  return waves;
}
```

### 3.4 Wave Executor

```typescript
class WaveScheduler {
  private maxGlobalConcurrency: number;
  private taskManager: TaskManager;

  constructor(taskManager: TaskManager, maxConcurrency = 3) {
    this.taskManager = taskManager;
    this.maxGlobalConcurrency = maxConcurrency;
  }

  async executeWaves(waves: WaveAssignment[]): Promise<Map<string, TaskResult>> {
    const results = new Map<string, TaskResult>();

    for (const wave of waves) {
      // Wave 0 can be serial (bootstrap/setup tasks)
      if (wave.wave === 0 && wave.tasks.length === 1) {
        const result = await this.executeTask(wave.tasks[0]);
        results.set(wave.tasks[0], result);
        continue;
      }

      // Parallel execution with concurrency limit
      const semaphore = new Semaphore(this.maxGlobalConcurrency);
      const waveResults = await Promise.allSettled(
        wave.tasks.map(async (taskId) => {
          await semaphore.acquire();
          try {
            return { taskId, result: await this.executeTask(taskId) };
          } finally {
            semaphore.release();
          }
        })
      );

      // Process results and handle failures
      for (const settled of waveResults) {
        if (settled.status === 'fulfilled') {
          results.set(settled.value.taskId, settled.value.result);
        } else {
          // Failure propagation: mark dependent tasks as blocked
          const failedTaskId = (settled.reason as any)?.taskId;
          if (failedTaskId) {
            results.set(failedTaskId, { status: 'failed', error: settled.reason });
            this.propagateFailure(failedTaskId, waves);
          }
        }
      }

      // Check if any failure should abort remaining waves
      const failedTasks = [...results.values()].filter(r => r.status === 'failed');
      if (failedTasks.length > 0) {
        // Strategy: continue with non-dependent tasks, skip blocked ones
        // (don't abort entire pipeline for one branch failure)
      }
    }

    return results;
  }

  private propagateFailure(failedTaskId: string, waves: WaveAssignment[]): void {
    // Find all tasks transitively dependent on the failed task
    // and transition them to 'blocked' status
    const task = this.taskManager.getTask(failedTaskId);
    // ... traverse dependency graph and block downstream tasks
  }
}

/** Simple counting semaphore for concurrency limiting */
class Semaphore {
  private permits: number;
  private waiting: (() => void)[] = [];

  constructor(permits: number) {
    this.permits = permits;
  }

  async acquire(): Promise<void> {
    if (this.permits > 0) {
      this.permits--;
      return;
    }
    return new Promise(resolve => this.waiting.push(resolve));
  }

  release(): void {
    if (this.waiting.length > 0) {
      this.waiting.shift()!();
    } else {
      this.permits++;
    }
  }
}
```

### 3.5 Failure Propagation Strategy

| Scenario | Behavior |
|----------|----------|
| Task fails, has dependents | Dependents transition to `blocked`. Other branches continue. |
| Task fails, no dependents | Wave continues. Failure recorded. |
| All tasks in wave fail | Next wave skipped entirely. Error report generated. |
| Task times out | Treated as failure. Retry via existing `maxDispatchAttempts`. |
| Dependency cycle detected | `computeWaves()` throws. No tasks dispatched. |

### 3.6 Integration with TaskManager State Machine

**New transitions needed**:

```
drafted → scheduled     # Wave assignment computed
scheduled → dispatched  # Wave executor dispatches task
blocked → scheduled     # Blocker resolved, re-enter scheduling
```

**TaskManager extensions**:
```typescript
// New methods
taskManager.setDependencies(taskId, deps: TaskDependencies): void;
taskManager.getBlockedTasks(failedTaskId: string): string[];
taskManager.getReadyTasks(): TaskBundle[];  // in-degree 0, status=scheduled
```

### 3.7 Example Wave Execution

```
Milestone: "Add authentication system"

Wave 0 (serial):
  TASK-001: Design auth schema (research, no deps)

Wave 1 (parallel, max 3):
  TASK-002: Implement JWT middleware (blockedBy: [001])
  TASK-003: Create user model (blockedBy: [001])
  TASK-004: Write auth API tests (blockedBy: [001])

Wave 2 (parallel, max 2):
  TASK-005: Integration test (blockedBy: [002, 003, 004])
  TASK-006: Documentation (blockedBy: [002, 003])
```

**Estimated effort**: 8-10 hours. 3 hours for graph algorithm + data model. 3 hours for executor + semaphore. 2 hours for TaskManager integration. 2 hours for failure propagation testing.

---

## 4. everything-claude-code Adaptation

### 4.1 Project Overview

[everything-claude-code](https://github.com/affaan-m/everything-claude-code) (100K+ GitHub stars) is a community-built agent harness optimization system providing:

- **30 specialized agents** (subagents for planning, reviewing, security, etc.)
- **136 workflow skills** (reusable SKILL.md-based knowledge)
- **60 slash commands** (quick-access workflows)
- **Hooks** (trigger-based automations — deterministic, always run)
- **Rules** (always-follow guidelines)
- **ecc.tools** (git history → auto-generated skills)

Built at Claude Code Hackathon (Cerebral Valley x Anthropic, Feb 2026). v1.9.0 (March 2026) added selective-install architecture and 12 language ecosystem support.

**Source**: [GitHub](https://github.com/affaan-m/everything-claude-code), [Augment Code Analysis](https://www.augmentcode.com/learn/everything-claude-code-github)

### 4.2 Key Patterns for Mercury Adoption

#### 4.2.1 Skills as Meta-Tools

**Pattern**: Skills inject specialized instructions into conversation history rather than performing actions directly. A `Skill` tool acts as container/dispatcher.

**Architecture**:
```
SKILL.md = YAML frontmatter (trigger conditions) + Markdown body (process steps)
Reference files = Context, examples, edge cases (separate from process)
```

**Mercury adaptation**:
- Create `.claude/skills/mercury-task-flow/SKILL.md` — standard task dispatch workflow
- Create `.claude/skills/mercury-research/SKILL.md` — research agent behavior
- Create `.claude/skills/mercury-review/SKILL.md` — review/acceptance workflow
- Skills replace redundant inline instructions in dispatch prompts
- **Benefit**: Reduce TaskBundle prompt size by 40-60% (move process to skills, keep only task-specific context in bundle)

**Source**: [Claude Code Skills Docs](https://code.claude.com/docs/en/skills), [Skills Architecture](https://www.mindstudio.ai/blog/claude-code-skills-architecture-skill-md-reference-files)

#### 4.2.2 Deterministic vs Probabilistic Split

**Pattern**:
- **CLAUDE.md + Hooks** = deterministic (always run, every time)
- **Skills + Agents** = probabilistic (Claude uses judgment on when/how)

**Mercury adaptation**:
- Move Mercury's mandatory behaviors (branch naming, commit format, role boundaries) to CLAUDE.md rules (deterministic)
- Move situational workflows (research loop, PR review) to skills (probabilistic)
- TaskBundle `contextInjection` field specifies which skills to load for each task

#### 4.2.3 Agent Specialization Architecture

**Pattern**: everything-claude-code defines 30 subagents in `agents/*.md`, each with:
- Specific role and capabilities
- Allowed/forbidden actions
- Delegation rules
- Output format requirements

**Mercury adaptation**:
- Mercury already has role-based agents (dev, main, reviewer, researcher, designer)
- **Enhancement**: Create agent-specific `.claude/agents/{role}.md` files that define behavior independently of the orchestrator
- This decouples agent behavior from orchestrator code — new agent types added via config, not code changes
- `AgentRegistry` loads agent definitions from filesystem, not hardcoded

#### 4.2.4 ecc.tools: Auto-Generated Skills from Git History

**Pattern**: `ecc.tools` analyzes git commit history to discover recurring patterns and wraps them into SKILL.md files automatically.

**Mercury adaptation**:
- **Short-term**: Manual skill creation for Mercury's known patterns (task dispatch, KB operations, acceptance flow)
- **Long-term**: Build similar tooling that analyzes Mercury's EventBus logs to auto-generate middleware or skills for frequently-observed agent patterns
- **Benefit**: Captures institutional knowledge that would otherwise be lost across sessions

#### 4.2.5 Security Scanner (ecc-agentshield)

**Pattern**: Scans CLAUDE.md, settings.json, MCP configs, hooks, agent definitions, and skills for:
- Secrets detection (14 patterns)
- Permission auditing
- Hook injection analysis
- MCP server risk profiling
- Agent config review

**Mercury adaptation**:
- Add pre-dispatch security validation middleware
- Scan TaskBundle prompts for potential prompt injection
- Validate agent write scope before execution
- Mercury already has `allowedWriteScope` in TaskBundle — extend with runtime enforcement

### 4.3 Specific Recommendations for Mercury

| Pattern | Priority | Effort | Impact |
|---------|----------|--------|--------|
| Skills for dispatch prompts | HIGH | 4-6h | Reduces prompt bloat, improves consistency |
| Agent definition files | MEDIUM | 3-4h | Decouples agent behavior from orchestrator |
| Deterministic/probabilistic split | HIGH | 2-3h | Clearer CLAUDE.md, fewer misfires |
| Security validation middleware | MEDIUM | 4-6h | Hardens task dispatch pipeline |
| Auto-skill generation from EventBus | LOW | 10-15h | Long-term knowledge preservation |

### 4.4 What NOT to Adopt

| Pattern | Reason |
|---------|--------|
| 136 workflow skills | Mercury's domain is narrower; 10-15 skills sufficient |
| 60 slash commands | Mercury uses RPC dispatch, not interactive slash commands |
| ecc.tools full suite | Over-engineered for Mercury's current scale |
| Language ecosystem plugins | Mercury is TypeScript-only; no need for polyglot support |

---

## Summary & Implementation Priority

| Topic | Key Deliverable | Effort | Dependencies |
|-------|----------------|--------|--------------|
| OTel Tracing | `instrumentation.ts` + span wrappers | 8-11h | None |
| Middleware Architecture | `middleware-pipeline.ts` + 4 built-in | 12-15h | None |
| Wave Scheduler | `wave-scheduler.ts` + TaskBundle extensions | 8-10h | TaskManager type changes |
| ECC Adaptation | Skills files + agent definition decoupling | 6-10h | None (incremental) |

**Recommended implementation order**:
1. **Middleware Architecture** first — creates the hook points for everything else
2. **OTel Tracing** second — implemented as a middleware, validates the pipeline
3. **Wave Scheduler** third — depends on TaskManager extensions
4. **ECC Patterns** fourth — incremental, can be done in parallel with any of the above

---

## Sources

### OTel Integration
- [OpenTelemetry Node.js Getting Started](https://opentelemetry.io/docs/languages/js/getting-started/nodejs/)
- [npm @opentelemetry/sdk-node v0.214.0](https://www.npmjs.com/package/@opentelemetry/sdk-node)
- [npm @opentelemetry/api v1.9.1](https://www.npmjs.com/package/@opentelemetry/api)
- [How to Name Your Spans | OpenTelemetry](https://opentelemetry.io/blog/2025/how-to-name-your-spans/)
- [How to Name Your Span Attributes | OpenTelemetry](https://opentelemetry.io/blog/2025/how-to-name-your-span-attributes/)
- [Naming Conventions | OpenTelemetry](https://opentelemetry.io/docs/specs/semconv/general/naming/)
- [Semantic Conventions | OpenTelemetry](https://opentelemetry.io/docs/concepts/semantic-conventions/)
- [GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [GenAI Agent Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- [GenAI Client Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
- [Context Propagation | OpenTelemetry JS](https://opentelemetry.io/docs/languages/js/context/)
- [AsyncLocalStorage Context Manager](https://www.npmjs.com/package/@opentelemetry/context-async-hooks)
- [The Hidden Cost of Async Context in Node.js](https://blog.platformatic.dev/the-hidden-cost-of-context)
- [OTLP Exporter Configuration](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)
- [Exporters | OpenTelemetry JS](https://opentelemetry.io/docs/languages/js/exporters/)
- [Datadog GenAI OTel Support](https://www.datadoghq.com/blog/llm-otel-semantic-convention/)
- [OTel JS DOS Mitigation](https://opentelemetry.io/blog/2026/oteljs-nodejs-dos-mitigation/)
- [Propagate Trace Context Across Async Boundaries](https://oneuptime.com/blog/post/2026-02-06-propagate-trace-context-async-boundaries/view)

### Middleware Architecture
- [Pipeline Pattern | DEV Community](https://dev.to/wallacefreitas/the-pipeline-pattern-streamlining-data-processing-in-software-architecture-44hn)
- [TypeScript Middleware Engine | DEV Community](https://dev.to/lcanady/make-a-typescript-middleware-engine-21kb)
- [Basic Middleware Pattern in JavaScript](https://muniftanjim.dev/blog/basic-middleware-pattern-in-javascript/)
- [Pipelines in TypeScript with Generics](https://dev.to/tommus/pipelines-in-typescript-with-generics-jad)
- [pipescript-ts: Pipeline Design Pattern](https://github.com/Bariskau/pipescript-ts)

### Wave-Based Scheduling
- [Topological Sorting | Wikipedia](https://en.wikipedia.org/wiki/Topological_sorting)
- [Scheduling Tasks with Topological Sorting | Bruno Scheufler](https://brunoscheufler.com/blog/2021-11-27-scheduling-tasks-with-topological-sorting)
- [Parallelizing Tasks with Dependencies | DZone](https://dzone.com/articles/parallelizing-tasks-with-dependencies-design-your)
- [Coffman-Graham Algorithm | Medium](https://medium.com/@bolerio/scheduling-tasks-and-drawing-graphs-the-coffman-graham-algorithm-3c85eb975ab)
- [Topological Sorting Explained | GeeksforGeeks](https://www.geeksforgeeks.org/dsa/topological-sorting/)

### everything-claude-code
- [GitHub: everything-claude-code](https://github.com/affaan-m/everything-claude-code)
- [everything-claude-code AGENTS.md](https://github.com/affaan-m/everything-claude-code/blob/main/AGENTS.md)
- [Augment Code: everything-claude-code Analysis](https://www.augmentcode.com/learn/everything-claude-code-github)
- [Claude Code Skills Docs](https://code.claude.com/docs/en/skills)
- [Claude Code Skills Architecture](https://www.mindstudio.ai/blog/claude-code-skills-architecture-skill-md-reference-files)
- [Claude Agent Skills Deep Dive](https://leehanchung.github.io/blogs/2025/10/26/claude-skills-deep-dive/)
- [Agent SDK Skills](https://platform.claude.com/docs/en/agent-sdk/skills)
- [Plugin Architecture | DeepWiki](https://deepwiki.com/affaan-m/everything-claude-code/3.1-planning-and-architecture-agents)
