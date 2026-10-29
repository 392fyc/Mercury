# Phase 3 Deep Research: OTel Tracing, Middleware Architecture, Wave-Based Parallel Scheduling

**Task**: TASK-853f8e03
**Author**: claude-code (dev)
**Date**: 2026-04-01
**Status**: Complete

---

## Table of Contents

1. [OTel Integration for Mercury Orchestrator](#1-otel-integration-for-mercury-orchestrator)
2. [Middleware Architecture Refactor](#2-middleware-architecture-refactor)
3. [Wave-Based Parallel Scheduling](#3-wave-based-parallel-scheduling)
4. [Everything-Claude-Code Adaptation](#4-everything-claude-code-adaptation)

---

## 1. OTel Integration for Mercury Orchestrator

### 1.1 Recommended npm Packages

Mercury orchestrator is TypeScript/Node.js (ESM, `"type": "module"`). The OTel JS SDK 2.0 was released in late February 2025 with significant improvements. Recommended packages:

| Package | Purpose | Version (as of 2026-04) | Stability |
|---------|---------|------------------------|-----------|
| `@opentelemetry/api` | Vendor-neutral tracing API | ^1.9.x | Stable (independent versioning) |
| `@opentelemetry/sdk-node` | All-in-one Node.js SDK (traces + metrics + logs) | ^0.214.x | Unstable (0.200+ = SDK 2.x series) |
| `@opentelemetry/sdk-trace-node` | Trace SDK with Node.js context manager | ^2.x | Stable |
| `@opentelemetry/exporter-trace-otlp-http` | OTLP/HTTP JSON trace exporter | ^0.214.x | Unstable |
| `@opentelemetry/resources` | Resource detection (service name, version) | ^2.x | Stable |
| `@opentelemetry/semantic-conventions` | Standard attribute names | ^1.x | Stable |

**Source**: [npm: @opentelemetry/sdk-node](https://www.npmjs.com/package/@opentelemetry/sdk-node), [OTel JS SDK 2.0 announcement](https://opentelemetry.io/blog/2025/otel-js-sdk-2-0/)

#### SDK 2.0 Breaking Changes (important for Mercury)

- **Minimum Node.js**: `^18.19.0 || >=20.6.0` — Mercury already targets Node 20+, so compatible.
- **Minimum TypeScript**: 5.0.4 — Mercury uses TS 5.x, compatible.
- **Compilation target**: ES2022 (from ES2017) — Mercury targets ESNext/ES2022, compatible.
- **Removed classes/namespaces** for better tree-shaking.
- **`@opentelemetry/api` unchanged** — no breaking changes in the API package.

**Source**: [Upgrade to OTel JS SDK 2.x](https://github.com/open-telemetry/opentelemetry-js/blob/main/doc/upgrade-to-2.x.md)

### 1.2 Span Naming Convention for Mercury

Following OTel's official span naming best practices, Mercury should adopt the `{verb} {object}` pattern:

| Operation | Span Name | Attributes |
|-----------|-----------|------------|
| Task dispatch | `dispatch task` | `mercury.task.id`, `mercury.task.role`, `mercury.agent.id` |
| Agent session start | `start session` | `mercury.session.id`, `mercury.agent.id`, `mercury.role` |
| Message send to agent | `send message` | `mercury.session.id`, `mercury.message.type` |
| Message receive from agent | `receive message` | `mercury.session.id`, `mercury.message.role` |
| RPC method handling | `handle {method}` | `rpc.method`, `rpc.jsonrpc.version` |
| Task state transition | `transition task` | `mercury.task.id`, `mercury.from_status`, `mercury.to_status` |
| KB persistence write | `persist task` | `mercury.task.id`, `mercury.persistence.layer` |
| Approval request | `request approval` | `mercury.approval.id`, `mercury.agent.id` |
| Main review | `review task` | `mercury.task.id`, `mercury.review.decision` |
| Acceptance verification | `verify acceptance` | `mercury.task.id`, `mercury.acceptance.verdict` |

**Attribute namespace**: All custom attributes should use the `mercury.*` prefix per OTel semantic convention naming rules.

**Source**: [How to Name Your Spans (OTel blog)](https://opentelemetry.io/blog/2025/how-to-name-your-spans/), [OTel Naming Conventions](https://opentelemetry.io/docs/specs/semconv/general/naming/)

### 1.3 Context Propagation Across Async Boundaries

Mercury orchestrator is heavily async (agent sessions, streaming messages, approval waits, callbacks). Key considerations:

#### AsyncLocalStorage (recommended)

- `@opentelemetry/sdk-node` automatically configures `AsyncLocalStorageContextManager` on Node.js 14.8+.
- The older `AsyncHooksContextManager` is **deprecated** and will be removed in SDK v3 (planned mid-2025).
- Performance cost: ~7% overhead, acceptable for orchestrator workloads.
- Node.js 24+ includes critical AsyncLocalStorage performance improvements (~5% better throughput).

**Source**: [npm: @opentelemetry/context-async-hooks](https://www.npmjs.com/package/@opentelemetry/context-async-hooks), [OTel Context docs](https://opentelemetry.io/docs/languages/js/context/)

#### Mercury-Specific Context Propagation Challenges

1. **`spawn()` boundaries**: Mercury spawns agent CLI processes via `child_process.spawn()`. Context does NOT automatically propagate across process boundaries. Solution: inject `traceparent` header as environment variable (`TRACEPARENT`) into spawned processes.

2. **EventBus async listeners**: The `bus.on("*", ...)` wildcard listener fires asynchronously. The OTel context is preserved within the same async execution chain but may be lost if events are queued/batched. Solution: capture `context.active()` at emit time and use `context.with()` in listeners.

3. **Approval waiter Promises**: `approvalWaiters` Map stores resolve functions with timeouts. The span context at request time must be propagated to the resolution callback. Solution: wrap the resolver with `context.bind()`.

4. **Fire-and-forget patterns**: `void this.deliverTaskCallback(payload)` — spans for these must be linked (not parented) to the originating span to avoid orphaned traces.

#### Recommended Setup Code Sketch

```typescript
// instrumentation.ts — loaded via --import flag
import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { Resource } from "@opentelemetry/resources";
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from "@opentelemetry/semantic-conventions";

const sdk = new NodeSDK({
  resource: new Resource({
    [ATTR_SERVICE_NAME]: "mercury-orchestrator",
    [ATTR_SERVICE_VERSION]: "0.1.0",
  }),
  traceExporter: new OTLPTraceExporter({
    url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? "http://localhost:4318/v1/traces",
  }),
});

sdk.start();
process.on("SIGTERM", () => sdk.shutdown());
```

### 1.4 Exporter Configuration

| Exporter | Use Case | Config |
|----------|----------|--------|
| `OTLPTraceExporter` (HTTP/JSON) | Production — send to Jaeger/Tempo/etc. | `OTEL_EXPORTER_OTLP_ENDPOINT` env var |
| `ConsoleSpanExporter` | Development/debugging | Built into `@opentelemetry/sdk-trace-node` |
| `InMemorySpanExporter` | Testing | Built into `@opentelemetry/sdk-trace-node` |

Mercury should support toggling via config:
```jsonc
// mercury.config.json
{
  "telemetry": {
    "enabled": true,
    "exporter": "otlp",  // "otlp" | "console" | "none"
    "endpoint": "http://localhost:4318"
  }
}
```

**Source**: [OTel Exporters docs](https://opentelemetry.io/docs/languages/js/exporters/)

---

## 2. Middleware Architecture Refactor

### 2.1 Current Architecture Analysis

The Mercury Orchestrator currently has a **monolithic dispatch flow**:

1. `handleRpc()` receives method call → big `switch` statement (line ~752)
2. `dispatch_task` case → `dispatchBundleTask(taskId)`
3. `dispatchBundleTask()` → pre-dispatch validation → retry loop → session creation → `streamMessages()`
4. Events emitted via `this.bus.emit()` at various points

**Problems with current approach**:
- Cross-cutting concerns (logging, tracing, validation, scope checking) are inline
- No extension point for pre/post dispatch hooks
- Retry logic is embedded in `dispatchBundleTask()`
- Scope validation (`ScopeViolation` checks) cannot be added/removed independently

### 2.2 Proposed Middleware Pipeline Design

The middleware pattern follows the **Chain of Responsibility** / **onion model** used by Express, Koa, and Redux:

```typescript
// ─── Core Types ───

/** Context object passed through the middleware pipeline */
interface DispatchContext {
  /** The task being dispatched */
  task: TaskBundle;
  /** The agent config selected for dispatch */
  agent: AgentConfig;
  /** The session ID (populated after session creation middleware) */
  sessionId?: string;
  /** Metadata bag for middleware to communicate */
  metadata: Map<string, unknown>;
  /** Trace span for this dispatch (populated by tracing middleware) */
  span?: Span;
  /** Abort signal — any middleware can abort the pipeline */
  aborted: boolean;
  abortReason?: string;
}

/** Next function — call to invoke the next middleware in the chain */
type NextFn = () => Promise<void>;

/** A middleware function */
type Middleware = (ctx: DispatchContext, next: NextFn) => Promise<void>;

/** Middleware pipeline manager */
interface MiddlewarePipeline {
  /** Register a middleware (appended to the chain) */
  use(middleware: Middleware): void;
  /** Register a middleware at a specific position */
  useAt(index: number, middleware: Middleware): void;
  /** Execute the full pipeline with a given context */
  execute(ctx: DispatchContext): Promise<void>;
}
```

### 2.3 Pipeline Implementation

```typescript
class TaskDispatchPipeline implements MiddlewarePipeline {
  private middlewares: Middleware[] = [];

  use(middleware: Middleware): void {
    this.middlewares.push(middleware);
  }

  useAt(index: number, middleware: Middleware): void {
    this.middlewares.splice(index, 0, middleware);
  }

  async execute(ctx: DispatchContext): Promise<void> {
    let index = 0;
    const dispatch = async (): Promise<void> => {
      if (ctx.aborted || index >= this.middlewares.length) return;
      const middleware = this.middlewares[index++];
      await middleware(ctx, dispatch);
    };
    await dispatch();
  }
}
```

### 2.4 Standard Middleware Stack

The recommended ordering for Mercury's dispatch pipeline:

```
┌─────────────────────────────────────────────┐
│  1. TracingMiddleware (create span)          │ ← outermost
│  ┌─────────────────────────────────────────┐│
│  │  2. LoggingMiddleware (log entry/exit)  ││
│  │  ┌─────────────────────────────────────┐││
│  │  │  3. ValidationMiddleware            │││
│  │  │     (scope checks, status checks)   │││
│  │  │  ┌─────────────────────────────────┐│││
│  │  │  │  4. RetryMiddleware             ││││
│  │  │  │     (exponential backoff)       ││││
│  │  │  │  ┌─────────────────────────────┐││││
│  │  │  │  │  5. SessionCreationMW       │││││
│  │  │  │  │  ┌─────────────────────────┐│││││
│  │  │  │  │  │  6. PromptBuildMW       ││││││
│  │  │  │  │  │  ┌─────────────────────┐│││││││
│  │  │  │  │  │  │ 7. StreamMessagesMW ││││││││ ← innermost
│  │  │  │  │  │  └─────────────────────┘│││││││
│  │  │  │  │  └─────────────────────────┘│││││
│  │  │  │  └─────────────────────────────┘││││
│  │  │  └─────────────────────────────────┘│││
│  │  └─────────────────────────────────────┘││
│  └─────────────────────────────────────────┘│
└─────────────────────────────────────────────┘
```

### 2.5 Retrofitting onto Existing Code

**Current**: `Orchestrator.dispatchBundleTask()` is a ~200-line method with inline validation, retry logic, session creation, and streaming.

**Migration strategy** (incremental, not big-bang):

1. **Phase A**: Extract each concern into a standalone middleware function, keeping `dispatchBundleTask()` as the integration point that assembles and runs the pipeline.
2. **Phase B**: Move pipeline assembly to constructor/init, making it configurable.
3. **Phase C**: Expose `use()` via config or plugin API for external middlewares.

**Key integration points with EventBus**:
- `TracingMiddleware` wraps the entire dispatch in a span, emitting span context as event metadata.
- `LoggingMiddleware` replaces scattered `this.transport.sendNotification("log", ...)` calls.
- Existing `bus.emit("orchestrator.task.dispatch", ...)` moves into the `StreamMessagesMiddleware` (or a dedicated `EventEmitMiddleware`).

### 2.6 Interface for Plugin Middlewares

```typescript
/** Plugin middleware registration — for mercury.config.json extensions */
interface MiddlewarePlugin {
  name: string;
  /** Position hint: "before:validation" | "after:retry" | "first" | "last" */
  position?: string;
  /** The middleware function */
  handler: Middleware;
}
```

---

## 3. Wave-Based Parallel Scheduling

### 3.1 Problem Statement

Currently, Mercury dispatches tasks one at a time via `dispatch_task` RPC. For Phase 3 milestones, the orchestrator needs to dispatch multiple independent tasks in parallel while respecting dependency ordering.

### 3.2 Dependency Graph Model

```typescript
/** A node in the task dependency DAG */
interface TaskNode {
  taskId: string;
  /** Tasks that must complete before this one can start */
  dependsOn: string[];
  /** Current execution status */
  status: "pending" | "running" | "completed" | "failed";
  /** The TaskBundle for dispatch */
  bundle: TaskBundle;
}

/** The full dependency graph */
interface TaskGraph {
  nodes: Map<string, TaskNode>;
  /** Reverse adjacency: taskId → tasks that depend on it */
  dependents: Map<string, Set<string>>;
}
```

### 3.3 Wave Detection Algorithm

The wave algorithm is based on **Kahn's algorithm** for topological sorting, adapted for parallel execution:

```typescript
/**
 * Compute execution waves from a task dependency DAG.
 * Each wave contains tasks whose dependencies are all satisfied.
 * Tasks within a wave can execute in parallel.
 *
 * Algorithm: Modified Kahn's (BFS-based topological sort)
 * Time: O(V + E) where V = tasks, E = dependency edges
 * Space: O(V)
 */
function computeWaves(graph: TaskGraph): TaskNode[][] {
  // 1. Compute in-degree for each node
  const inDegree = new Map<string, number>();
  for (const [id, node] of graph.nodes) {
    inDegree.set(id, node.dependsOn.length);
  }

  // 2. Seed wave 0 with all zero-indegree nodes
  const waves: TaskNode[][] = [];
  let currentWave: TaskNode[] = [];

  for (const [id, degree] of inDegree) {
    if (degree === 0) {
      currentWave.push(graph.nodes.get(id)!);
    }
  }

  // 3. Process waves until all nodes are scheduled
  while (currentWave.length > 0) {
    waves.push(currentWave);
    const nextWave: TaskNode[] = [];

    for (const node of currentWave) {
      // For each dependent of the completed node
      const deps = graph.dependents.get(node.taskId) ?? new Set();
      for (const depId of deps) {
        const newDegree = (inDegree.get(depId) ?? 1) - 1;
        inDegree.set(depId, newDegree);
        if (newDegree === 0) {
          nextWave.push(graph.nodes.get(depId)!);
        }
      }
    }

    currentWave = nextWave;
  }

  // 4. Cycle detection: if any nodes remain unscheduled, there's a cycle
  const scheduled = waves.flat().length;
  if (scheduled < graph.nodes.size) {
    const unscheduled = [...graph.nodes.keys()].filter(
      id => !waves.flat().some(n => n.taskId === id)
    );
    throw new Error(
      `Dependency cycle detected — unschedulable tasks: ${unscheduled.join(", ")}`
    );
  }

  return waves;
}
```

### 3.4 Wave Executor with Concurrency Control

```typescript
interface WaveSchedulerOptions {
  /** Max tasks running simultaneously within a wave */
  maxConcurrency: number;
  /** Strategy when a task in a wave fails */
  failureStrategy: "fail-fast" | "continue-wave" | "skip-dependents";
}

class WaveScheduler {
  constructor(
    private graph: TaskGraph,
    private options: WaveSchedulerOptions,
    private dispatcher: (task: TaskBundle) => Promise<void>,
  ) {}

  async execute(): Promise<WaveExecutionResult> {
    const waves = computeWaves(this.graph);
    const results: WaveResult[] = [];

    for (const [waveIndex, wave] of waves.entries()) {
      // Respect maxConcurrency within each wave
      const waveResult = await this.executeWave(waveIndex, wave);
      results.push(waveResult);

      // Handle failures per strategy
      if (waveResult.failed.length > 0) {
        if (this.options.failureStrategy === "fail-fast") {
          break; // Stop all subsequent waves
        }
        if (this.options.failureStrategy === "skip-dependents") {
          this.markDependentsSkipped(waveResult.failed);
        }
      }
    }

    return { waves: results };
  }

  private async executeWave(index: number, tasks: TaskNode[]): Promise<WaveResult> {
    // Semaphore-based concurrency: process up to maxConcurrency at a time
    const results: TaskOutcome[] = [];
    const running = new Set<Promise<void>>();

    for (const task of tasks) {
      if (task.status === "failed") {
        results.push({ taskId: task.taskId, outcome: "skipped" });
        continue;
      }

      const promise = this.dispatcher(task.bundle)
        .then(() => {
          task.status = "completed";
          results.push({ taskId: task.taskId, outcome: "completed" });
        })
        .catch((err) => {
          task.status = "failed";
          results.push({ taskId: task.taskId, outcome: "failed", error: err });
        })
        .finally(() => running.delete(promise));

      running.add(promise);

      if (running.size >= this.options.maxConcurrency) {
        await Promise.race(running);
      }
    }

    // Wait for remaining tasks in this wave
    await Promise.all(running);

    return {
      waveIndex: index,
      completed: results.filter(r => r.outcome === "completed"),
      failed: results.filter(r => r.outcome === "failed"),
      skipped: results.filter(r => r.outcome === "skipped"),
    };
  }

  private markDependentsSkipped(failed: TaskOutcome[]): void {
    for (const f of failed) {
      const deps = this.graph.dependents.get(f.taskId) ?? new Set();
      for (const depId of deps) {
        const node = this.graph.nodes.get(depId);
        if (node) node.status = "failed"; // Mark as failed so executeWave skips them
      }
    }
  }
}
```

### 3.5 Integration with TaskManager State Machine

Current `TaskManager` state machine: `drafted → dispatched → in_progress → implementation_done → main_review → acceptance → verified → closed`

**Required additions for wave scheduling**:

1. **New field on TaskBundle**: `dependsOn: string[]` — list of taskIds this task depends on.
2. **New status** (optional): `"waiting"` — task is drafted but blocked by unfinished dependencies. Alternative: keep using `"drafted"` and let the wave scheduler control dispatch timing externally.
3. **TaskManager integration**: The wave scheduler sits **above** TaskManager — it calls `dispatchBundleTask()` for each task in a wave, and TaskManager handles the per-task state machine as today.
4. **New event**: `orchestrator.wave.start` / `orchestrator.wave.complete` emitted by the scheduler.

**Recommended approach**: The wave scheduler is a **new class** (`WaveScheduler`) that the Orchestrator instantiates when a `dispatch_wave` RPC is called. It does NOT modify the TaskManager state machine — it orchestrates multiple existing `dispatchBundleTask()` calls.

### 3.6 Failure Propagation Strategies

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `fail-fast` | Stop all remaining waves, cancel running tasks | Critical dependencies (all-or-nothing) |
| `continue-wave` | Finish current wave, stop subsequent waves | Best-effort within a phase |
| `skip-dependents` | Continue all waves, but skip tasks that depend on failed ones | Maximum parallelism with graceful degradation |

**Recommendation**: Default to `skip-dependents` for Mercury's multi-agent workload, as independent task branches should not block each other.

---

## 4. Everything-Claude-Code Adaptation

### 4.1 Project Overview

[everything-claude-code](https://github.com/affaan-m/everything-claude-code) (v1.9.0, March 2026) is an agent harness optimization system with:
- 28+ specialized agents
- 119+ skills (auto-loaded workflows)
- 60+ slash commands
- Hook-based automations
- Rules (always-follow guidelines per language)
- MCP server configurations

**Source**: [GitHub: everything-claude-code](https://github.com/affaan-m/everything-claude-code)

### 4.2 Key Patterns for Mercury Adoption

#### Pattern 1: Tiered Tool/Skill Discovery

everything-claude-code uses a 3-level lazy loading architecture:
- **Level 1 — Metadata**: Skill name + description pre-loaded (low cost)
- **Level 2 — Core Instructions**: Full SKILL.md loaded when Claude determines the skill applies
- **Level 3 — Nested Resources**: Additional files loaded dynamically only as needed

**Mercury adaptation**: Mercury's `RoleLoader` and `RolePromptBuilder` currently load role cards eagerly. Adopt tiered loading:
1. Role cards (Level 1) are always included in the system prompt.
2. Task-specific context (Level 2) is injected only when a task is dispatched to the role.
3. Reference docs / KB content (Level 3) loaded on-demand via `readScope.optionalDocs`.

#### Pattern 2: Skills as Auto-Loaded Modules

everything-claude-code skills are stored in `.agents/skills/` with a standard structure:
```
skills/
  {skill-name}/
    SKILL.md        # Instructions + workflow
    agents/
      openai.yaml   # Codex interface metadata
```

**Mercury adaptation**: Mercury could adopt a similar pattern for **reusable task templates**:
```
Mercury_KB/04-skills/
  code-review/
    SKILL.md          # Review checklist, guidelines
    task-template.json # Pre-filled CreateTaskParams
  research/
    SKILL.md
    task-template.json
```

This would allow Main Agent to instantiate tasks from templates rather than constructing `CreateTaskParams` from scratch each time.

#### Pattern 3: Hook-Based Automations

everything-claude-code defines hooks as JSON with matcher conditions:
```json
{
  "event": "file:save",
  "match": "*.test.ts",
  "action": "run-tests"
}
```

**Mercury adaptation**: Mercury's `EventBus` already supports wildcard listeners. Extend with a **declarative hook registry**:
```typescript
interface MercuryHook {
  event: string;           // EventBus event pattern
  match?: Record<string, unknown>; // Payload matching conditions
  action: string;          // RPC method to invoke
  params?: Record<string, unknown>;
}
```

This would allow users to configure automated responses to events (e.g., "when a task fails, auto-create an issue") via `mercury.config.json`.

#### Pattern 4: Agent Specialization via Role Cards

everything-claude-code's 28 agents each have a dedicated markdown file (e.g., `agents/architect.md`, `agents/security.md`) defining:
- Role and responsibility
- Available tools
- Constraints and forbidden actions
- Output format

**Mercury adaptation**: Mercury already has `role-loader.ts` and role cards in `Mercury_KB/01-roles/`. The pattern aligns well. Recommended enhancements:
1. Add **tool restrictions** to role cards (Mercury currently trusts agents to self-restrict).
2. Add **output format specifications** (Mercury's `buildDevPrompt()` etc. partially do this but could be more explicit).

#### Pattern 5: Selective Install Architecture

everything-claude-code v1.9.0 introduced selective installation — users pick only the agents/skills they need instead of installing everything.

**Mercury adaptation**: Mercury's `mercury.config.json` already supports per-agent configuration. Extend to support optional **skill packs** that can be enabled/disabled:
```jsonc
{
  "skills": {
    "code-review": { "enabled": true },
    "research": { "enabled": true },
    "security-audit": { "enabled": false }
  }
}
```

### 4.3 Patterns NOT Recommended for Mercury

| Pattern | Reason to Skip |
|---------|---------------|
| 60+ slash commands | Mercury uses RPC methods, not CLI slash commands. The RPC approach is more structured. |
| `.codex/AGENTS.md` format | Mercury has its own CLAUDE.md/AGENTS.md convention; mixing formats would cause confusion. |
| Cross-agent-platform support (Cursor, Codex, etc.) | Mercury is purpose-built for its own orchestration; multi-platform compatibility adds unnecessary abstraction. |

### 4.4 Summary of Adaptation Recommendations

| Priority | Pattern | Effort | Impact |
|----------|---------|--------|--------|
| P1 | Tiered skill/context loading | Medium | Reduces token usage in agent prompts |
| P1 | Hook-based automation via EventBus | Medium | Enables user-defined automation workflows |
| P2 | Task templates from skill modules | Low | Speeds up task creation for Main Agent |
| P2 | Output format specs in role cards | Low | Improves receipt/verdict parsing reliability |
| P3 | Selective skill pack installation | Low | Better UX for config management |

---

## Appendix: Source URLs

### OTel Integration
- [Node.js Getting Started | OpenTelemetry](https://opentelemetry.io/docs/languages/js/getting-started/nodejs/)
- [npm: @opentelemetry/sdk-node](https://www.npmjs.com/package/@opentelemetry/sdk-node)
- [npm: @opentelemetry/api](https://www.npmjs.com/package/@opentelemetry/api)
- [OTel JS SDK 2.0 Announcement](https://opentelemetry.io/blog/2025/otel-js-sdk-2-0/)
- [Upgrade to OTel JS SDK 2.x Guide](https://github.com/open-telemetry/opentelemetry-js/blob/main/doc/upgrade-to-2.x.md)
- [OTel Span Naming Blog](https://opentelemetry.io/blog/2025/how-to-name-your-spans/)
- [OTel Semantic Conventions — Naming](https://opentelemetry.io/docs/specs/semconv/general/naming/)
- [OTel Context Propagation (JS)](https://opentelemetry.io/docs/languages/js/context/)
- [npm: @opentelemetry/context-async-hooks](https://www.npmjs.com/package/@opentelemetry/context-async-hooks)
- [OTel Exporters (JS)](https://opentelemetry.io/docs/languages/js/exporters/)
- [OTel JS DOS Mitigation Statement](https://opentelemetry.io/blog/2026/oteljs-nodejs-dos-mitigation/)

### Middleware Architecture
- [TypeScript Middleware Engine (DEV Community)](https://dev.to/lcanady/make-a-typescript-middleware-engine-21kb)
- [Generic Middleware Pattern in TypeScript (Evert Pot)](https://evertpot.com/generic-middleware/)
- [Building Extensible Pipelines with Middleware (Medium)](https://medium.com/ingeniouslysimple/building-extensible-pipelines-with-middleware-4f64428cab39)

### Wave-Based Scheduling
- [Scheduling Tasks with Topological Sorting (Bruno Scheufler)](https://brunoscheufler.com/blog/2021-11-27-scheduling-tasks-with-topological-sorting)
- [Parallelizing Tasks with Dependencies (DZone)](https://dzone.com/articles/parallelizing-tasks-with-dependencies-design-your)
- [Topological Sorting — Wikipedia](https://en.wikipedia.org/wiki/Topological_sorting)

### Everything-Claude-Code
- [GitHub: everything-claude-code](https://github.com/affaan-m/everything-claude-code)
- [GitHub: everything-claude-code AGENTS.md](https://github.com/affaan-m/everything-claude-code/blob/main/AGENTS.md)
- [GitHub: everything-claude-code CLAUDE.md](https://github.com/affaan-m/everything-claude-code/blob/main/CLAUDE.md)
- [Awesome Claude Code (curated list)](https://github.com/hesreallyhim/awesome-claude-code)
