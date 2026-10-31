# Phase 3 Deep Research: OTel Tracing, Middleware Architecture, Wave-Based Parallel Scheduling

**Task ID**: TASK-853f8e03
**Date**: 2026-04-01
**Status**: Complete

---

## 1. OpenTelemetry Integration for Mercury Orchestrator

### 1.1 NPM Packages (Verified)

Mercury orchestrator runs as a Node.js/TypeScript process. The following packages form the OTel stack:

| Package | Purpose | Source |
|---------|---------|--------|
| `@opentelemetry/api` | Stable tracing/metrics API surface (vendor-neutral) | [npm](https://www.npmjs.com/package/@opentelemetry/api) |
| `@opentelemetry/sdk-node` | All-in-one NodeSDK that wires tracer, meter, context manager | [npm](https://www.npmjs.com/package/@opentelemetry/sdk-node) |
| `@opentelemetry/sdk-trace-node` | Node-specific TracerProvider + ConsoleSpanExporter | [npm](https://www.npmjs.com/package/@opentelemetry/sdk-trace-node) |
| `@opentelemetry/exporter-trace-otlp-http` | OTLP/HTTP exporter (sends spans to any OTLP collector) | [npm](https://www.npmjs.com/package/@opentelemetry/exporter-trace-otlp-http) |
| `@opentelemetry/sdk-metrics` | Metrics SDK (PeriodicExportingMetricReader) | [npm](https://www.npmjs.com/package/@opentelemetry/sdk-metrics) |
| `@opentelemetry/resources` | Service resource identification (`service.name`, `service.version`) | Bundled with sdk-node |
| `@opentelemetry/semantic-conventions` | Standard attribute keys (e.g., `SEMRESATTRS_SERVICE_NAME`) | [npm](https://www.npmjs.com/package/@opentelemetry/semantic-conventions) |

**Note**: `@opentelemetry/auto-instrumentations-node` is available for auto-instrumenting HTTP/Express, but Mercury uses raw `node:http` and JSON-RPC, so **manual instrumentation is preferred** for meaningful span names.

> Source: [Node.js Getting Started — OpenTelemetry](https://opentelemetry.io/docs/languages/js/getting-started/nodejs/)

### 1.2 Span Naming Convention

Following the official OTel recommendation of `{verb} {object}` pattern for low-cardinality names:

| Mercury Operation | Span Name | Attributes |
|-------------------|-----------|------------|
| Handle RPC call | `handle rpc` | `rpc.method`, `rpc.jsonrpc.id` |
| Dispatch task to agent | `dispatch task` | `task.id`, `task.role`, `agent.id` |
| Execute task bundle | `execute task` | `task.id`, `task.title`, `task.priority` |
| Build role prompt | `build prompt` | `task.role`, `prompt.length` |
| Start agent session | `start session` | `agent.id`, `session.role`, `session.id` |
| Send prompt to agent | `send prompt` | `agent.id`, `prompt.length` |
| Stream agent response | `stream response` | `agent.id`, `session.id`, `stream.events` |
| Persist task to KB | `persist task` | `task.id`, `persistence.target` |
| Review task (main) | `review task` | `task.id`, `review.decision` |
| Accept task | `accept task` | `task.id`, `acceptance.verdict` |
| Recover orphaned task | `recover task` | `task.id`, `recovery.attempt` |
| KB context fetch | `fetch context` | `docs.count`, `docs.total_chars` |
| Callback delivery | `deliver callback` | `task.id`, `callback.target_session` |

**Anti-patterns to avoid**:
- ❌ `dispatch_task_TASK-853f8e03_to_claude-code` (high cardinality)
- ❌ `handleRpc_create_session` (method name in span name)
- ✅ `dispatch task` with attributes `{task.id: "TASK-853f8e03", agent.id: "claude-code"}`

> Source: [How to Name Your Spans — OpenTelemetry Blog](https://opentelemetry.io/blog/2025/how-to-name-your-spans/)

### 1.3 Context Propagation Across Async Boundaries

Mercury orchestrator is heavily async (agent sessions, streaming, KB operations). Key findings:

1. **Default context manager**: `AsyncLocalStorageContextManager` is the default in `@opentelemetry/sdk-node`. It uses Node.js `AsyncLocalStorage` (available since Node 14.8.0) to propagate trace context across `await` boundaries automatically.

2. **Deprecated**: `AsyncHooksContextManager` is deprecated and scheduled for removal in OTel JS v3. Mercury should use only `AsyncLocalStorage`.

3. **Automatic propagation**: When using `tracer.startActiveSpan("dispatch task", async (span) => { ... })`, any nested `await` calls within the callback inherit the active span context — child spans created inside will automatically become children.

4. **Cross-process propagation**: Mercury spawns agent CLIs as child processes. To propagate trace context across process boundaries, inject W3C `traceparent` header via environment variables or stdin protocol:
   ```typescript
   // Inject traceparent into spawned process env
   const activeContext = api.context.active();
   const spanContext = api.trace.getSpan(activeContext)?.spanContext();
   if (spanContext) {
     env.TRACEPARENT = `00-${spanContext.traceId}-${spanContext.spanId}-01`;
   }
   ```

5. **Security note**: Node.js 20.20.0+ fixes a DoS vulnerability related to `async_hooks`. Mercury should require Node.js ≥ 20.20.0 in engines field.

> Sources:
> - [Context — OpenTelemetry JS](https://opentelemetry.io/docs/languages/js/context/)
> - [@opentelemetry/context-async-hooks — npm](https://www.npmjs.com/package/@opentelemetry/context-async-hooks)
> - [OTel JS DoS Mitigation Statement](https://opentelemetry.io/blog/2026/oteljs-nodejs-dos-mitigation/)

### 1.4 Exporter Configuration

```typescript
// instrumentation.ts — must load before any application code
import { NodeSDK } from '@opentelemetry/sdk-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { Resource } from '@opentelemetry/resources';
import { SEMRESATTRS_SERVICE_NAME, SEMRESATTRS_SERVICE_VERSION } from '@opentelemetry/semantic-conventions';

const sdk = new NodeSDK({
  resource: new Resource({
    [SEMRESATTRS_SERVICE_NAME]: 'mercury-orchestrator',
    [SEMRESATTRS_SERVICE_VERSION]: '0.1.0',
  }),
  traceExporter: new OTLPTraceExporter({
    url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? 'http://localhost:4318/v1/traces',
    headers: {},
    timeoutMillis: 10000,
  }),
});

sdk.start();
process.on('SIGTERM', () => sdk.shutdown());
```

**Environment variables** (standard OTel config):
- `OTEL_EXPORTER_OTLP_ENDPOINT` — collector base URL (default: `http://localhost:4318`)
- `OTEL_EXPORTER_OTLP_HEADERS` — comma-separated key=value headers
- `OTEL_SERVICE_NAME` — overrides resource service name
- `OTEL_TRACES_SAMPLER` — sampling strategy (`always_on`, `traceidratio`, `parentbased_always_on`)

> Source: [OTLP Exporter Configuration — OpenTelemetry](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)

### 1.5 Mercury-Specific Integration Points

Where to add spans in the current codebase:

| File | Method/Area | Span Scope |
|------|-------------|------------|
| `orchestrator.ts` | `handleRpc()` | Root span per RPC call |
| `orchestrator.ts` | `dispatchBundleTask()` | Parent span for entire task lifecycle |
| `orchestrator.ts` | `executeTask()` | Child span for session execution |
| `orchestrator.ts` | `sendPrompt()` | Child span for agent communication |
| `orchestrator.ts` | `deliverTaskCallback()` | Child span for callback routing |
| `task-manager.ts` | `createTask()` | Span for task creation + persistence |
| `task-manager.ts` | `transitionTask()` | Span with old/new status attributes |
| `task-persistence-kb.ts` | `save()`/`load()` | Span for KB I/O |
| `knowledge-service.ts` | `buildContext()` | Span for doc fetching |

---

## 2. Middleware Pipeline Architecture

### 2.1 Design Goals

Retrofit a pluggable pre/post-dispatch middleware pipeline onto the existing `Orchestrator` class without breaking the current `handleRpc()` → `dispatchBundleTask()` → `executeTask()` flow.

### 2.2 TypeScript Interface Sketch

```typescript
// ─── Middleware Types ───

/** Context object passed through the middleware chain */
interface DispatchContext {
  /** The task being dispatched */
  task: TaskBundle;
  /** Built prompt (mutable — middleware can modify) */
  prompt: string;
  /** Role prompt for the agent */
  rolePrompt: string;
  /** Target agent ID (mutable — middleware can re-route) */
  targetAgentId: string;
  /** Target role */
  role: AgentRole;
  /** Metadata bag for middleware to pass data downstream */
  metadata: Record<string, unknown>;
  /** OTel span for this dispatch (if tracing enabled) */
  span?: Span;
  /** Abort dispatch (set by middleware to skip execution) */
  aborted?: { reason: string };
}

/** Result returned after dispatch completes */
interface DispatchResult {
  sessionId: string;
  taskId: string;
  /** Duration in ms */
  durationMs: number;
  /** Whether task completed or failed */
  outcome: 'completed' | 'failed' | 'aborted';
}

/** Next function — call to proceed to next middleware */
type NextFn = () => Promise<DispatchResult>;

/** A single middleware function */
type DispatchMiddleware = (
  ctx: DispatchContext,
  next: NextFn,
) => Promise<DispatchResult>;

/** Middleware registration and execution engine */
interface MiddlewarePipeline {
  /** Register middleware (order matters — first registered = outermost) */
  use(middleware: DispatchMiddleware): void;

  /** Execute the pipeline with a terminal handler */
  execute(
    ctx: DispatchContext,
    handler: (ctx: DispatchContext) => Promise<DispatchResult>,
  ): Promise<DispatchResult>;
}
```

### 2.3 Pipeline Implementation Pattern

```typescript
class DispatchPipeline implements MiddlewarePipeline {
  private middlewares: DispatchMiddleware[] = [];

  use(mw: DispatchMiddleware): void {
    this.middlewares.push(mw);
  }

  async execute(
    ctx: DispatchContext,
    handler: (ctx: DispatchContext) => Promise<DispatchResult>,
  ): Promise<DispatchResult> {
    // Build chain from right to left (onion model)
    let next: NextFn = () => handler(ctx);

    for (let i = this.middlewares.length - 1; i >= 0; i--) {
      const mw = this.middlewares[i];
      const currentNext = next;
      next = () => mw(ctx, currentNext);
    }

    return next();
  }
}
```

### 2.4 Example Middleware Implementations

```typescript
// ─── Tracing Middleware (OTel) ───
const tracingMiddleware: DispatchMiddleware = async (ctx, next) => {
  const tracer = api.trace.getTracer('mercury-orchestrator');
  return tracer.startActiveSpan('dispatch task', async (span) => {
    span.setAttribute('task.id', ctx.task.taskId);
    span.setAttribute('agent.id', ctx.targetAgentId);
    span.setAttribute('task.role', ctx.role);
    ctx.span = span;
    try {
      const result = await next();
      span.setAttribute('dispatch.outcome', result.outcome);
      span.setStatus({ code: SpanStatusCode.OK });
      return result;
    } catch (err) {
      span.setStatus({ code: SpanStatusCode.ERROR, message: String(err) });
      throw err;
    } finally {
      span.end();
    }
  });
};

// ─── Scope Validation Middleware ───
const scopeValidationMiddleware: DispatchMiddleware = async (ctx, next) => {
  if (!ctx.task.allowedWriteScope?.codePaths?.length &&
      !ctx.task.allowedWriteScope?.kbPaths?.length) {
    ctx.aborted = { reason: 'No write scope defined — refusing dispatch' };
    return { sessionId: '', taskId: ctx.task.taskId, durationMs: 0, outcome: 'aborted' };
  }
  return next();
};

// ─── Rate Limiting Middleware ───
const rateLimitMiddleware: DispatchMiddleware = async (ctx, next) => {
  const activeCount = getActiveSessionCount(ctx.targetAgentId);
  const maxConcurrent = getAgentMaxConcurrent(ctx.targetAgentId);
  if (activeCount >= maxConcurrent) {
    await waitForSlot(ctx.targetAgentId);
  }
  return next();
};

// ─── Logging/Audit Middleware ───
const auditMiddleware: DispatchMiddleware = async (ctx, next) => {
  const start = Date.now();
  bus.emit('orchestrator.dispatch.pre', 'orchestrator', 'orchestrator', {
    taskId: ctx.task.taskId, targetAgent: ctx.targetAgentId,
  });
  const result = await next();
  bus.emit('orchestrator.dispatch.post', 'orchestrator', 'orchestrator', {
    taskId: ctx.task.taskId, outcome: result.outcome, durationMs: Date.now() - start,
  });
  return result;
};
```

### 2.5 Retrofitting onto Orchestrator

Current flow in `orchestrator.ts`:
```
handleRpc("dispatch_task") → dispatchBundleTask(taskId) → prepareBundleTaskExecution() → executeTask()
```

Proposed integration point:
```
handleRpc("dispatch_task")
  → dispatchBundleTask(taskId)
    → prepareBundleTaskExecution()  // builds DispatchContext
    → pipeline.execute(ctx, (ctx) => executeTask())  // wraps executeTask in pipeline
```

Changes required:
1. Add `DispatchPipeline` as a field on `Orchestrator`
2. Expose `orchestrator.useDispatchMiddleware(mw)` for registration
3. Modify `dispatchBundleTask()` to build `DispatchContext` and call `pipeline.execute()`
4. The terminal handler inside `execute()` calls the existing `executeTask()` logic

**Key advantage**: Existing code in `executeTask()` remains untouched. All cross-cutting concerns (tracing, validation, rate limiting, auditing) are composed externally.

> Source: [A Generic Middleware Pattern in TypeScript — Evert Pot](https://evertpot.com/generic-middleware/)

---

## 3. Wave-Based Parallel Scheduling

### 3.1 Problem Statement

Mercury currently dispatches tasks sequentially. When multiple tasks have dependency relationships (e.g., "research" must complete before "implement", but "implement-A" and "implement-B" are independent), we need a scheduler that:
1. Respects dependency ordering
2. Maximizes parallelism within each "wave"
3. Handles failure propagation
4. Integrates with TaskManager's state machine

### 3.2 Dependency Graph Model

```typescript
/** A node in the task dependency graph */
interface TaskNode {
  taskId: string;
  /** Task IDs this node depends on (must complete before this node starts) */
  dependsOn: string[];
  /** Current status from TaskManager */
  status: TaskStatus;
  /** Assigned wave (computed by scheduler) */
  wave?: number;
}

/** The full dependency graph — a DAG (Directed Acyclic Graph) */
interface TaskGraph {
  nodes: Map<string, TaskNode>;
  /** Adjacency list: taskId → set of dependents (tasks that depend on it) */
  dependents: Map<string, Set<string>>;
}
```

### 3.3 Wave Detection Algorithm

The algorithm uses a modified Kahn's topological sort to group independent tasks into waves:

```typescript
/**
 * Compute waves from a DAG of task dependencies.
 * Each wave contains tasks whose dependencies are all in earlier waves.
 * Tasks within a wave are independent and can execute in parallel.
 *
 * Time complexity: O(V + E) where V = tasks, E = dependency edges
 */
function computeWaves(graph: TaskGraph): TaskNode[][] {
  const inDegree = new Map<string, number>();
  const waves: TaskNode[][] = [];

  // Initialize in-degree counts
  for (const [id, node] of graph.nodes) {
    inDegree.set(id, node.dependsOn.length);
  }

  // Iteratively extract nodes with zero in-degree
  let remaining = new Set(graph.nodes.keys());

  while (remaining.size > 0) {
    // Find all nodes with in-degree 0 (no unresolved dependencies)
    const wave: TaskNode[] = [];
    for (const id of remaining) {
      if ((inDegree.get(id) ?? 0) === 0) {
        wave.push(graph.nodes.get(id)!);
      }
    }

    if (wave.length === 0) {
      // Cycle detected — should not happen in a valid DAG
      throw new Error(
        `Cycle detected in task graph: ${[...remaining].join(', ')}`,
      );
    }

    waves.push(wave);

    // Remove this wave's nodes and decrement dependents' in-degree
    for (const node of wave) {
      remaining.delete(node.taskId);
      const deps = graph.dependents.get(node.taskId);
      if (deps) {
        for (const depId of deps) {
          inDegree.set(depId, (inDegree.get(depId) ?? 1) - 1);
        }
      }
    }
  }

  return waves;
}
```

### 3.4 Wave Executor with Concurrency Control

```typescript
interface WaveSchedulerConfig {
  /** Max tasks executing concurrently within a single wave */
  maxConcurrency: number;
  /** How to handle a failed task within a wave */
  failureStrategy: 'fail-fast' | 'continue-wave' | 'skip-dependents';
}

interface WaveExecutionResult {
  completedTasks: string[];
  failedTasks: string[];
  skippedTasks: string[];
  /** Total wall-clock time */
  totalDurationMs: number;
}

class WaveScheduler {
  constructor(
    private config: WaveSchedulerConfig,
    private taskManager: TaskManager,
    private dispatchFn: (taskId: string) => Promise<DispatchResult>,
  ) {}

  async executeGraph(graph: TaskGraph): Promise<WaveExecutionResult> {
    const waves = computeWaves(graph);
    const completed: string[] = [];
    const failed: string[] = [];
    const skipped: string[] = [];
    const start = Date.now();

    for (let waveIdx = 0; waveIdx < waves.length; waveIdx++) {
      const wave = waves[waveIdx];

      // Filter out tasks whose dependencies failed (skip-dependents mode)
      const executable = wave.filter((node) => {
        if (this.config.failureStrategy === 'skip-dependents') {
          const hasFailedDep = node.dependsOn.some((dep) => failed.includes(dep));
          if (hasFailedDep) {
            skipped.push(node.taskId);
            return false;
          }
        }
        return true;
      });

      // Execute wave with concurrency limit
      const results = await this.executeWave(executable);

      for (const [taskId, result] of results) {
        if (result.outcome === 'completed') {
          completed.push(taskId);
        } else {
          failed.push(taskId);

          if (this.config.failureStrategy === 'fail-fast') {
            // Mark all remaining tasks as skipped
            for (let j = waveIdx + 1; j < waves.length; j++) {
              skipped.push(...waves[j].map((n) => n.taskId));
            }
            return { completedTasks: completed, failedTasks: failed, skippedTasks: skipped, totalDurationMs: Date.now() - start };
          }
        }
      }
    }

    return { completedTasks: completed, failedTasks: failed, skippedTasks: skipped, totalDurationMs: Date.now() - start };
  }

  private async executeWave(
    tasks: TaskNode[],
  ): Promise<Map<string, DispatchResult>> {
    const results = new Map<string, DispatchResult>();
    const semaphore = new Semaphore(this.config.maxConcurrency);

    await Promise.all(
      tasks.map(async (node) => {
        await semaphore.acquire();
        try {
          const result = await this.dispatchFn(node.taskId);
          results.set(node.taskId, result);
        } catch (err) {
          results.set(node.taskId, {
            sessionId: '',
            taskId: node.taskId,
            durationMs: 0,
            outcome: 'failed',
          });
        } finally {
          semaphore.release();
        }
      }),
    );

    return results;
  }
}
```

### 3.5 Failure Propagation Strategies

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `fail-fast` | Abort entire graph on first failure | Critical path — no point continuing |
| `continue-wave` | Finish current wave, continue to next | Best-effort parallel execution |
| `skip-dependents` | Skip tasks whose dependencies failed, continue others | Most practical for Mercury — preserves independent branches |

### 3.6 Integration with TaskManager State Machine

Current valid transitions in `task-manager.ts`:
```
drafted → dispatched → in_progress → implementation_done → main_review → acceptance → verified → closed
```

Wave scheduler integration:
1. **Task creation**: New `dependsOn: string[]` field on `TaskBundle` (list of task IDs)
2. **Graph building**: When `executeGraph()` is called, build the DAG from all tasks' `dependsOn` fields
3. **State transitions**: Each task follows its existing state machine independently; the wave scheduler only controls *when* `dispatch` is called
4. **Cycle detection**: Validate DAG at graph construction time; reject cycles with clear error

> Sources:
> - [Parallelizing Tasks with Dependencies — DZone](https://dzone.com/articles/parallelizing-tasks-with-dependencies-design-your)
> - [Topological Sorting — Wikipedia](https://en.wikipedia.org/wiki/Topological_sorting)
> - [Coffman-Graham Algorithm — Medium](https://medium.com/@bolerio/scheduling-tasks-and-drawing-graphs-the-coffman-graham-algorithm-3c85eb975ab)

---

## 4. Everything-Claude-Code Patterns & Adaptation

### 4.1 Overview

[Everything-Claude-Code (ECC)](https://github.com/affaan-m/everything-claude-code) is a harness optimization system for AI coding agents. Key stats: 36 specialized subagents, 142+ skills, 68+ slash commands.

### 4.2 Patterns Relevant to Mercury

#### 4.2.1 Layered Tool Discovery

ECC uses three layers for tool/capability discovery:
1. **Skills** (142 workflow definitions) — domain knowledge injected into agent context on demand
2. **Commands** (68 slash commands) — user-facing workflows that compose multiple skills
3. **Hooks** (lifecycle automations) — trigger-based actions at PreToolUse, PostToolUse, Stop

**Mercury adaptation**: Mercury already has `readScope.requiredDocs` and `readScope.optionalDocs` on TaskBundle, which serve a similar purpose to ECC skills — injecting domain context. Consider extending this with:
- A `skills` registry in orchestrator config that maps skill names to doc paths
- Automatic skill injection based on task `role` and `codeScope`

#### 4.2.2 Agent Specialization via Delegation

ECC defines 36 subagents with clear domain boundaries:
- `planner` for architecture
- `code-reviewer` for quality
- `tdd-guide` for test-driven development
- `security-reviewer` for security
- Language-specific reviewers (TypeScript, Python, Go, etc.)

**Mercury adaptation**: Mercury already has role-based dispatch (`dev`, `research`, `design`, `acceptance`, `critic`). ECC suggests finer granularity. Consider:
- Agent capabilities in config already support this (`capabilities: ["code", "review", "orchestration"]`)
- The `modelRecommendation.requiredCapabilities` auto-triage mechanism already scores agents by capability match
- Enhancement: Add more granular capability tags (e.g., `typescript-review`, `security-audit`) to existing config

#### 4.2.3 Instinct-Based Learning

ECC implements a continuous learning loop:
1. Extract patterns mid-session via `/learn` command
2. Score learned behaviors with confidence metrics
3. Cluster instincts into reusable skills
4. Import/export across projects

**Mercury adaptation**: Mercury's KB already serves as persistent memory. Consider:
- Adding a `Mercury_KB/05-patterns/` folder for extracted coding patterns
- Auto-extracting patterns from successful task completions (post-acceptance)
- Including pattern references in role prompts for future tasks

#### 4.2.4 Context Window Management

ECC reserves the final 20% of context for complex operations and implements strategic compaction.

**Mercury adaptation**: Mercury already has:
- `TOKEN_CHECKPOINT_THRESHOLD = 0.7` for auto-handoff (Orchestrator line 100)
- `resourceBudget.tokenBudget` on TaskBundle
- The `orchestrator.context.compact` event triggers session handoff

These align well with ECC's approach. No changes needed.

#### 4.2.5 Hook System Architecture

ECC hooks fire at lifecycle points: `PreToolUse`, `PostToolUse`, `Stop`, with configurable profiles.

**Mercury adaptation**: The proposed middleware pipeline (Section 2) directly maps to ECC's hook concept:
- `PreToolUse` → pre-dispatch middleware
- `PostToolUse` → post-dispatch middleware (in the `next()` return path)
- `Stop` → session cleanup handlers (already exist via EventBus `session.end` events)

### 4.3 Adaptation Recommendations Summary

| ECC Pattern | Mercury Current State | Recommendation | Priority |
|-------------|----------------------|----------------|----------|
| Layered tool discovery | Basic doc injection via readScope | Add skills registry to config | P2 |
| Agent specialization | 5 roles + capability scoring | Add finer capability tags | P3 |
| Instinct learning | KB persistence, no pattern extraction | Add post-acceptance pattern capture | P3 |
| Context management | 70% threshold + token budget | Already aligned — no changes | — |
| Hook system | No middleware pipeline | Implement DispatchPipeline (Section 2) | P1 |
| Package manager detection | Not applicable (Node.js only) | N/A | — |
| Multi-harness compat | Mercury is its own harness | N/A | — |

> Source: [everything-claude-code — GitHub](https://github.com/affaan-m/everything-claude-code)

---

## 5. Cross-Topic Integration Notes

### 5.1 OTel + Middleware Pipeline

The tracing middleware (Section 2.4) is the natural integration point for OTel spans. By placing it as the outermost middleware, every dispatch gets traced without touching `executeTask()`.

### 5.2 OTel + Wave Scheduler

Each wave should create a parent span (`execute wave`) with child spans for individual task dispatches. The trace view then shows:

```
execute graph (root)
  ├── execute wave [0]
  │   ├── dispatch task [TASK-A]
  │   └── dispatch task [TASK-B]
  └── execute wave [1]
      └── dispatch task [TASK-C]  (depended on A and B)
```

### 5.3 Middleware + Wave Scheduler

The wave scheduler calls `dispatchFn(taskId)` for each task. This function should route through the middleware pipeline, so all middleware (tracing, validation, rate limiting) applies uniformly regardless of whether dispatch is single-shot or wave-scheduled.

---

## Appendix: Verification Status

| Claim | Verified? | Source |
|-------|-----------|--------|
| `@opentelemetry/sdk-node` is the recommended all-in-one package | ✅ Verified | [OTel docs](https://opentelemetry.io/docs/languages/js/getting-started/nodejs/) |
| `AsyncLocalStorageContextManager` is default context manager | ✅ Verified | [OTel context docs](https://opentelemetry.io/docs/languages/js/context/) |
| `AsyncHooksContextManager` deprecated for v3 | ✅ Verified | [npm page](https://www.npmjs.com/package/@opentelemetry/context-async-hooks) |
| Node.js 20.20.0 fixes async_hooks DoS | ✅ Verified | [OTel blog](https://opentelemetry.io/blog/2026/oteljs-nodejs-dos-mitigation/) |
| `{verb} {object}` span naming is official recommendation | ✅ Verified | [OTel blog](https://opentelemetry.io/blog/2025/how-to-name-your-spans/) |
| OTLP/HTTP default endpoint is `localhost:4318` | ✅ Verified | [OTel exporter docs](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/) |
| Kahn's algorithm O(V+E) for topological sort | ✅ Verified | [Wikipedia](https://en.wikipedia.org/wiki/Topological_sorting) |
| ECC has 36 subagents, 142 skills, 68 commands | ✅ Verified | [GitHub README](https://github.com/affaan-m/everything-claude-code) |
| Middleware onion model pattern in TypeScript | ✅ Verified | [Evert Pot blog](https://evertpot.com/generic-middleware/) |
| Wave scheduler maps to level-based parallel topological sort | ✅ Verified | [DZone article](https://dzone.com/articles/parallelizing-tasks-with-dependencies-design-your) |
| `W3C traceparent` header format for cross-process propagation | UNVERIFIED | Based on W3C Trace Context spec (not directly verified in this session) |
| Semaphore-based concurrency control in wave executor | UNVERIFIED | Standard pattern; no specific source consulted |
