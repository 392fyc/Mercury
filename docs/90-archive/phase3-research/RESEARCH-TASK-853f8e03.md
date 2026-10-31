# Phase 3 Deep Research: OTel Tracing, Middleware Architecture, Wave-Based Parallel Scheduling

**Task ID:** TASK-853f8e03
**Researcher:** claude-code (claude-opus-4-6)
**Date:** 2026-04-01
**Status:** Complete

---

## Table of Contents

1. [OTel Integration for Mercury Orchestrator](#1-otel-integration)
2. [Middleware Architecture Refactor](#2-middleware-architecture-refactor)
3. [Wave-Based Parallel Scheduling](#3-wave-based-parallel-scheduling)
4. [everything-claude-code Adaptation Patterns](#4-everything-claude-code-adaptation)
5. [Cross-Cutting Concerns](#5-cross-cutting-concerns)
6. [Sources](#6-sources)

---

## 1. OTel Integration

### 1.1 NPM Packages (Verified Versions)

| Package | Version | Purpose |
|---------|---------|---------|
| `@opentelemetry/api` | 1.9.1 | Stable API surface — import `trace`, `context`, `propagation` |
| `@opentelemetry/sdk-node` | 0.214.0 | All-in-one SDK for Node.js (bundles trace + metrics + context manager) |
| `@opentelemetry/sdk-trace-node` | 1.x (bundled) | Trace SDK with `NodeTracerProvider` — used internally by sdk-node |
| `@opentelemetry/exporter-trace-otlp-http` | latest | OTLP/HTTP exporter (default: `http://localhost:4318/v1/traces`) |
| `@opentelemetry/exporter-trace-otlp-grpc` | latest | OTLP/gRPC exporter (default: `http://localhost:4317`) — Node.js only |
| `@opentelemetry/sdk-metrics` | latest | Metrics SDK (if we want counters for dispatch latency, etc.) |
| `@opentelemetry/resources` | latest | Resource descriptors (service.name, service.version) |
| `@opentelemetry/semantic-conventions` | latest | Standard attribute keys (http.method, rpc.system, etc.) |
| `@opentelemetry/context-async-hooks` | latest | AsyncLocalStorage-based context manager (default in sdk-node) |
| `@opentelemetry/instrumentation-http` | latest | Auto-instrument Node HTTP server (Mercury's HTTP transport) |

**Source:** [npmjs.com/@opentelemetry/sdk-node](https://www.npmjs.com/package/@opentelemetry/sdk-node), [npmjs.com/@opentelemetry/api](https://www.npmjs.com/package/@opentelemetry/api)

**Install command:**
```bash
pnpm add @opentelemetry/api @opentelemetry/sdk-node \
  @opentelemetry/exporter-trace-otlp-http \
  @opentelemetry/resources @opentelemetry/semantic-conventions \
  @opentelemetry/instrumentation-http
```

### 1.2 Span Naming Convention for Mercury

Mercury is an orchestrator, not an HTTP service — span names should reflect **orchestrator semantics**:

| Operation | Span Name | Kind |
|-----------|-----------|------|
| RPC handler entry | `mercury.rpc/{method}` | SERVER |
| Task dispatch | `mercury.task.dispatch/{taskId}` | INTERNAL |
| Task execution (full lifecycle) | `mercury.task.execute/{taskId}` | INTERNAL |
| Agent session start | `mercury.session.start/{agentId}` | INTERNAL |
| Agent prompt send | `mercury.agent.send/{agentId}` | CLIENT |
| Agent streaming | `mercury.agent.stream/{sessionId}` | CLIENT |
| KB read | `mercury.kb.read/{docPath}` | CLIENT |
| KB write | `mercury.kb.write/{docPath}` | CLIENT |
| Acceptance verification | `mercury.acceptance/{taskId}` | INTERNAL |
| Callback delivery | `mercury.callback.deliver/{taskId}` | INTERNAL |

**Convention:** `mercury.{domain}.{action}/{identifier}`

This follows [OTel semantic conventions for RPC](https://opentelemetry.io/docs/specs/semconv/http/http-spans/) adapted for Mercury's domain.

### 1.3 Context Propagation Across Async Boundaries

Mercury has several async boundary patterns that need careful context propagation:

#### Pattern A: EventBus → Handler (fire-and-forget)

The `EventBus.emit()` dispatches synchronously to handlers, but handlers often trigger async work (`void this.deliverTaskCallback(payload)`). Context is preserved within the synchronous emit, but **lost** in fire-and-forget `void` calls.

**Fix:** Capture context before fire-and-forget:
```typescript
// In constructor event handler:
this.bus.on("orchestrator.task.callback", (event) => {
  const ctx = context.active(); // capture OTel context
  const payload = event.payload as TaskCallbackPayload;
  void context.with(ctx, () => this.deliverTaskCallback(payload));
});
```

#### Pattern B: Dispatch Retry Loop (exponential backoff)

`dispatchBundleTask()` has a retry loop with `setTimeout` for backoff. `setTimeout` breaks async context in Node.js unless `AsyncLocalStorage` is used.

**Fix:** `@opentelemetry/context-async-hooks` (default in sdk-node) uses `AsyncLocalStorage` which **automatically propagates** through `setTimeout` and `Promise` chains. No manual fix needed — just ensure sdk-node is initialized before any Mercury code runs.

#### Pattern C: Cross-Process (Agent spawn)

Mercury spawns child processes (claude, codex). OTel context does **not** automatically propagate across process boundaries.

**Fix:** Inject W3C `traceparent` header into the agent's environment or prompt metadata:
```typescript
import { propagation, context } from "@opentelemetry/api";

const headers: Record<string, string> = {};
propagation.inject(context.active(), headers);
// Pass headers.traceparent as env or metadata to child process
```

### 1.4 Exporter Configuration

**Recommended setup:**

```typescript
// instrumentation.ts — loaded via node --import ./instrumentation.js
import { NodeSDK } from "@opentelemetry/sdk-node";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { Resource } from "@opentelemetry/resources";
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from "@opentelemetry/semantic-conventions";
import { HttpInstrumentation } from "@opentelemetry/instrumentation-http";

const sdk = new NodeSDK({
  resource: new Resource({
    [ATTR_SERVICE_NAME]: "mercury-orchestrator",
    [ATTR_SERVICE_VERSION]: "0.0.1",
  }),
  traceExporter: new OTLPTraceExporter({
    url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT || "http://localhost:4318/v1/traces",
  }),
  instrumentations: [new HttpInstrumentation()],
});

sdk.start();
process.on("SIGTERM", () => sdk.shutdown());
```

**Environment variable overrides** (OTel standard):
- `OTEL_EXPORTER_OTLP_ENDPOINT` — base URL
- `OTEL_EXPORTER_OTLP_PROTOCOL` — `http/protobuf` (default), `grpc`, `http/json`
- `OTEL_SERVICE_NAME` — overrides resource attribute
- `OTEL_TRACES_SAMPLER` — `always_on`, `traceidratio`, `parentbased_traceidratio`

**Source:** [OpenTelemetry JS Exporters](https://opentelemetry.io/docs/languages/js/exporters/), [OTLP Exporter Configuration](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)

### 1.5 Integration Points with Mercury Architecture

The instrumentation file **must be loaded before** Mercury's `index.ts` entry point. In the Tauri sidecar launch:

```
node --import ./instrumentation.js ./dist/index.js
```

Key integration points:
1. **`Orchestrator.handleRpc()`** — wrap with root span per RPC call
2. **`dispatchBundleTask()`** — child span under RPC span
3. **`streamMessages()`** — long-lived span with events for each streamed chunk
4. **`EventBus.emit()`** — optional: add span events for key lifecycle events
5. **`KnowledgeService` calls** — child spans for KB read/write latency

---

## 2. Middleware Architecture Refactor

### 2.1 Current Architecture Analysis

Mercury's current dispatch pipeline is **monolithic** — embedded in `Orchestrator` class methods:

```
handleRpc() → dispatchBundleTask() → prepareBundleTaskExecution() → startRoleSession() → sendPrompt()
```

Cross-cutting concerns (logging, validation, context building, retry, token budget) are interleaved within these methods. There is no extension point for:
- Pre-dispatch hooks (e.g., rate limiting, approval gates)
- Post-dispatch hooks (e.g., telemetry, notification)
- Pre-send hooks (e.g., prompt injection, context enrichment)
- Post-completion hooks (e.g., artifact extraction, cost tracking)

### 2.2 Proposed Middleware Pipeline Design

**Pattern:** Koa-style `next()` middleware chain with typed context.

```typescript
// ─── Core Types ───

/** Context object threaded through the middleware chain. */
interface DispatchContext {
  /** The task being dispatched. Middleware can read/mutate. */
  task: TaskBundle;
  /** Built prompt — starts empty, populated by prompt-builder middleware. */
  prompt: string;
  /** Role-specific system prompt. */
  rolePrompt: string;
  /** Session created for this dispatch (populated after session middleware). */
  session?: SessionInfo;
  /** Agent adapter resolved for the assignee. */
  adapter?: AgentAdapter;
  /** KB context fetched for readScope (populated by kb-context middleware). */
  kbContext?: string;
  /** Dispatch attempt number (for retry middleware). */
  attempt: number;
  /** Arbitrary metadata bag for middleware communication. */
  metadata: Record<string, unknown>;
  /** OTel span for this dispatch (if tracing enabled). */
  span?: Span;
}

/** A single middleware function. Call next() to continue the chain. */
type DispatchMiddleware = (
  ctx: DispatchContext,
  next: () => Promise<void>,
) => Promise<void>;

/** Pipeline that composes middlewares and executes them in order. */
class DispatchPipeline {
  private middlewares: DispatchMiddleware[] = [];

  /** Register middleware. Order matters — first registered = outermost. */
  use(mw: DispatchMiddleware): this {
    this.middlewares.push(mw);
    return this;
  }

  /** Execute the pipeline with the given context. */
  async execute(ctx: DispatchContext): Promise<void> {
    let index = 0;
    const dispatch = async (): Promise<void> => {
      if (index >= this.middlewares.length) return;
      const mw = this.middlewares[index++];
      await mw(ctx, dispatch);
    };
    await dispatch();
  }
}
```

### 2.3 Standard Middleware Stack

```typescript
const pipeline = new DispatchPipeline();

// 1. Telemetry (outermost — captures full latency)
pipeline.use(tracingMiddleware);

// 2. Validation (reject invalid tasks early)
pipeline.use(validationMiddleware);

// 3. Rate limiting / concurrency gate
pipeline.use(concurrencyMiddleware);

// 4. KB context enrichment
pipeline.use(kbContextMiddleware);

// 5. Prompt building (role-appropriate)
pipeline.use(promptBuilderMiddleware);

// 6. Session creation
pipeline.use(sessionMiddleware);

// 7. Dispatch (innermost — actually sends to agent)
pipeline.use(dispatchMiddleware);
```

### 2.4 Example Middleware Implementations

```typescript
/** Tracing middleware — wraps dispatch in OTel span. */
const tracingMiddleware: DispatchMiddleware = async (ctx, next) => {
  const tracer = trace.getTracer("mercury-orchestrator");
  await tracer.startActiveSpan(
    `mercury.task.dispatch/${ctx.task.taskId}`,
    async (span) => {
      ctx.span = span;
      span.setAttribute("mercury.task.id", ctx.task.taskId);
      span.setAttribute("mercury.task.role", ctx.task.role ?? "dev");
      span.setAttribute("mercury.task.assignee", ctx.task.assignedTo);
      try {
        await next();
        span.setStatus({ code: SpanStatusCode.OK });
      } catch (err) {
        span.setStatus({ code: SpanStatusCode.ERROR, message: String(err) });
        span.recordException(err as Error);
        throw err;
      } finally {
        span.end();
      }
    },
  );
};

/** Validation middleware — checks task state and dispatch budget. */
const validationMiddleware: DispatchMiddleware = async (ctx, next) => {
  const error = taskManager.validateDispatch(ctx.task.taskId);
  if (error) throw new Error(error);
  await next();
};

/** KB context middleware — fetches required docs from Obsidian. */
const kbContextMiddleware: DispatchMiddleware = async (ctx, next) => {
  if (kb?.isEnabled() && (ctx.task.readScope.requiredDocs?.length ?? 0) > 0) {
    try {
      ctx.kbContext = await kb.buildContext(ctx.task.readScope.requiredDocs);
    } catch {
      // KB context is best-effort — continue without it
    }
  }
  await next();
};
```

### 2.5 Retrofitting onto Existing Orchestrator

**Migration strategy:** Incremental, not big-bang.

1. **Phase 1:** Extract `DispatchPipeline` class and `DispatchContext` type into `packages/orchestrator/src/dispatch-pipeline.ts`
2. **Phase 2:** Refactor `dispatchBundleTask()` to build a `DispatchContext` and call `pipeline.execute(ctx)` instead of inline logic
3. **Phase 3:** Extract existing inline logic into named middlewares (validation, kb-context, prompt-builder, session, dispatch)
4. **Phase 4:** Add new middlewares (tracing, concurrency, cost-tracking) as plugins
5. **Phase 5:** Expose `pipeline.use()` in config for user-defined middleware

**Key constraint:** The `Orchestrator` class currently holds state (sessions, roleSessions, approvalRequests). Middlewares should receive these via the context or via injected services, NOT by capturing `this` directly. This enables future testability.

### 2.6 Integration with EventBus

The EventBus remains the **notification** layer (fan-out to GUI/SSE/MCP). The middleware pipeline is the **dispatch control flow** layer. They are complementary:

- Middleware controls **whether** and **how** a dispatch happens
- EventBus announces **that** it happened (post-facto)

Middleware can emit events at any point: `ctx.metadata.bus.emit(...)`.

---

## 3. Wave-Based Parallel Scheduling

### 3.1 Problem Statement

Mercury currently dispatches tasks **serially** — one at a time via `dispatchBundleTask()`. For multi-task plans (e.g., Session 6's A∥R→C→B pattern), there's no built-in mechanism to:
- Express dependencies between tasks
- Run independent tasks in parallel
- Respect a maximum concurrency limit
- Propagate failures correctly

### 3.2 Dependency Graph Model

```typescript
/** A node in the task dependency graph. */
interface TaskNode {
  taskId: string;
  /** Tasks that must complete before this one can start. */
  dependsOn: string[];
  /** Current execution status. */
  status: "pending" | "running" | "completed" | "failed" | "skipped";
}

/** The full execution plan — a DAG of task nodes. */
interface ExecutionPlan {
  planId: string;
  nodes: Map<string, TaskNode>;
  /** Maximum concurrent tasks per wave. */
  maxConcurrency: number;
  /** Strategy when a task fails: "fail-fast" | "continue" | "skip-dependents" */
  failureStrategy: "fail-fast" | "continue" | "skip-dependents";
}
```

### 3.3 Wave Detection Algorithm (Kahn's Algorithm Variant)

The wave scheduler uses a modified Kahn's algorithm for topological sorting that groups nodes into **waves** (levels) of independent tasks:

```typescript
/**
 * Compute execution waves from a DAG.
 * Each wave contains tasks whose dependencies are all in previous waves.
 * Tasks within a wave can execute in parallel.
 *
 * Time: O(V + E), Space: O(V)
 */
function computeWaves(plan: ExecutionPlan): string[][] {
  const inDegree = new Map<string, number>();
  const adjList = new Map<string, string[]>(); // dependsOn → dependents

  // Initialize
  for (const [id, node] of plan.nodes) {
    inDegree.set(id, node.dependsOn.length);
    for (const dep of node.dependsOn) {
      const list = adjList.get(dep) ?? [];
      list.push(id);
      adjList.set(dep, list);
    }
  }

  const waves: string[][] = [];
  let currentWave = [...plan.nodes.keys()].filter(
    (id) => (inDegree.get(id) ?? 0) === 0,
  );

  while (currentWave.length > 0) {
    waves.push(currentWave);
    const nextWave: string[] = [];

    for (const id of currentWave) {
      for (const dependent of adjList.get(id) ?? []) {
        const newDegree = (inDegree.get(dependent) ?? 1) - 1;
        inDegree.set(dependent, newDegree);
        if (newDegree === 0) {
          nextWave.push(dependent);
        }
      }
    }

    currentWave = nextWave;
  }

  // Cycle detection: if any node not in any wave, graph has a cycle
  const scheduled = new Set(waves.flat());
  if (scheduled.size < plan.nodes.size) {
    const unscheduled = [...plan.nodes.keys()].filter((id) => !scheduled.has(id));
    throw new Error(`Dependency cycle detected involving: ${unscheduled.join(", ")}`);
  }

  return waves;
}
```

### 3.4 Wave Executor

```typescript
class WaveScheduler {
  private plan: ExecutionPlan;
  private dispatcher: (taskId: string) => Promise<void>;
  private bus: EventBus;

  constructor(plan: ExecutionPlan, dispatcher: (taskId: string) => Promise<void>, bus: EventBus) {
    this.plan = plan;
    this.dispatcher = dispatcher;
    this.bus = bus;
  }

  async execute(): Promise<Map<string, "completed" | "failed" | "skipped">> {
    const results = new Map<string, "completed" | "failed" | "skipped">();
    const waves = computeWaves(this.plan);

    this.bus.emit("orchestrator.plan.start", "orchestrator", "orchestrator", {
      planId: this.plan.planId,
      waveCount: waves.length,
      taskCount: this.plan.nodes.size,
    });

    for (let waveIdx = 0; waveIdx < waves.length; waveIdx++) {
      const wave = waves[waveIdx];

      // Filter out tasks whose dependencies failed (if skip-dependents strategy)
      const executable = wave.filter((taskId) => {
        const node = this.plan.nodes.get(taskId)!;
        if (this.plan.failureStrategy === "skip-dependents") {
          const hasFailedDep = node.dependsOn.some(
            (dep) => results.get(dep) === "failed" || results.get(dep) === "skipped",
          );
          if (hasFailedDep) {
            results.set(taskId, "skipped");
            return false;
          }
        }
        return true;
      });

      this.bus.emit("orchestrator.wave.start", "orchestrator", "orchestrator", {
        planId: this.plan.planId,
        waveIndex: waveIdx,
        tasks: executable,
      });

      // Execute wave with concurrency limit
      const semaphore = new Semaphore(this.plan.maxConcurrency);
      const waveResults = await Promise.allSettled(
        executable.map(async (taskId) => {
          await semaphore.acquire();
          try {
            this.plan.nodes.get(taskId)!.status = "running";
            await this.dispatcher(taskId);
            this.plan.nodes.get(taskId)!.status = "completed";
            results.set(taskId, "completed");
          } catch (err) {
            this.plan.nodes.get(taskId)!.status = "failed";
            results.set(taskId, "failed");
            if (this.plan.failureStrategy === "fail-fast") {
              throw err; // Propagate to abort remaining
            }
          } finally {
            semaphore.release();
          }
        }),
      );

      // Check for fail-fast abort
      if (this.plan.failureStrategy === "fail-fast") {
        const hasFailed = waveResults.some((r) => r.status === "rejected");
        if (hasFailed) {
          // Mark all remaining tasks as skipped
          for (let i = waveIdx + 1; i < waves.length; i++) {
            for (const taskId of waves[i]) {
              results.set(taskId, "skipped");
            }
          }
          break;
        }
      }

      this.bus.emit("orchestrator.wave.end", "orchestrator", "orchestrator", {
        planId: this.plan.planId,
        waveIndex: waveIdx,
        results: Object.fromEntries(
          executable.map((id) => [id, results.get(id)]),
        ),
      });
    }

    return results;
  }
}

/** Simple counting semaphore for concurrency limiting. */
class Semaphore {
  private count: number;
  private queue: (() => void)[] = [];

  constructor(max: number) {
    this.count = max;
  }

  async acquire(): Promise<void> {
    if (this.count > 0) {
      this.count--;
      return;
    }
    return new Promise<void>((resolve) => this.queue.push(resolve));
  }

  release(): void {
    const next = this.queue.shift();
    if (next) {
      next();
    } else {
      this.count++;
    }
  }
}
```

### 3.5 Failure Propagation Strategies

| Strategy | Behavior | Use Case |
|----------|----------|----------|
| `fail-fast` | Abort all remaining waves on first failure | Critical path tasks, CI/CD |
| `continue` | Continue executing all waves regardless of failures | Independent tasks, best-effort |
| `skip-dependents` | Skip tasks whose dependencies failed, continue others | Default for Mercury — balanced |

### 3.6 Integration with TaskManager State Machine

The wave scheduler integrates with the existing state machine:

1. **Plan creation:** Main Agent creates an `ExecutionPlan` with task IDs and dependencies
2. **Task creation:** Each task in the plan is created via `TaskManager.createTask()` with status `drafted`
3. **Wave execution:** Scheduler calls `dispatchBundleTask(taskId)` which handles `drafted → dispatched → in_progress` transitions
4. **Completion detection:** Scheduler listens to `orchestrator.task.callback` events to know when a task finishes
5. **Wave advancement:** When all tasks in wave N complete/fail/skip, wave N+1 begins

**New EventBus events needed:**
- `orchestrator.plan.start` — plan execution begins
- `orchestrator.plan.end` — plan execution completes
- `orchestrator.wave.start` — wave N begins
- `orchestrator.wave.end` — wave N completes

### 3.7 Example: Session 6 Plan as Waves

```
Plan: A∥R → C → B

Wave 0: [TASK-A (streaming+resume+cleanup), TASK-R (multi-agent research)]  ← parallel
Wave 1: [TASK-C (KB cleanup)]                                               ← after A∥R
Wave 2: [TASK-B (UX polish)]                                                ← after C

Dependencies:
  TASK-C.dependsOn = [TASK-A, TASK-R]
  TASK-B.dependsOn = [TASK-C]
```

---

## 4. everything-claude-code Adaptation

### 4.1 Project Overview

[everything-claude-code](https://github.com/affaan-m/everything-claude-code) (by Affaan Mustafa) is a production-ready agent harness providing:
- **30 specialized agents** (code-reviewer, planner, tdd-guide, doc-updater, etc.)
- **136 skills** (analyze-repo, skill-creator, pattern-detection, etc.)
- **60 commands** (slash commands for common workflows)
- **Automated hook workflows** (pre-commit, post-build, etc.)

Built at Claude Code Hackathon (Cerebral Valley × Anthropic, Feb 2026).

**Source:** [github.com/affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code)

### 4.2 Tool Discovery Patterns

**Pattern 1: YAML-Based Agent Registry**
```yaml
# agents/code-reviewer.md frontmatter
name: code-reviewer
description: Reviews code for quality, security, and best practices
triggers:
  - keywords: [review, code quality, security audit]
  - file_patterns: ["**/*.ts", "**/*.js"]
  - domains: [code-review, security]
```

Agents are discovered via structured metadata — Claude dynamically selects the right agent based on task keywords, file paths, and domain tags.

**Adaptation for Mercury:** Mercury's `AgentRegistry` already maps agent IDs to capabilities. We can extend `AgentConfig` with:
```typescript
interface AgentConfig {
  // ... existing fields
  triggers?: {
    keywords?: string[];
    filePatterns?: string[];
    domains?: string[];
  };
}
```

This enables the auto-triage G9 algorithm to use richer signals beyond `capabilities` and `model`.

**Pattern 2: Skill Composition**
Skills in everything-claude-code are self-contained markdown files that describe a capability. They're loaded dynamically via file system scanning (`.claude/skills/*.md`).

**Adaptation for Mercury:** Mercury's role cards (`role-loader.ts`) already load role-specific prompts. We can extend this to support skill cards — composable prompt fragments that can be mixed into any role:
```
Mercury_KB/05-skills/
  ├── git-safety.md        ← loaded for dev role
  ├── kb-writing.md        ← loaded for research role
  └── test-runner.md       ← loaded for dev + acceptance
```

**Pattern 3: Hook Workflows**
everything-claude-code uses pre/post hooks for common lifecycle events (pre-commit, post-build). These are declarative and composable.

**Adaptation for Mercury:** This maps directly to the middleware pipeline (§2). Pre-dispatch and post-dispatch hooks can be defined declaratively in config:
```json
{
  "hooks": {
    "pre-dispatch": ["validate-scope", "check-budget"],
    "post-completion": ["extract-artifacts", "update-kb"]
  }
}
```

### 4.3 Composition Patterns

**Pattern: Parallel Agent Execution**
everything-claude-code spawns sub-agents for independent tasks. Key insight: agents should be launched in parallel when tasks are independent, with a fan-out/fan-in pattern.

**Already present in Mercury:** The wave scheduler (§3) provides this. The missing piece is the **fan-in** — collecting results from parallel agents and synthesizing them. The `WaveScheduler.execute()` return value provides this via the results map.

**Pattern: Research-First Development**
everything-claude-code emphasizes research before implementation — gather context, analyze patterns, then execute.

**Already present in Mercury:** The `research` role in `task-manager.ts` with `buildResearchPrompt()` supports this. Can be enhanced with the skill card system.

### 4.4 Recommendations for Mercury

| Pattern | Priority | Effort | Value |
|---------|----------|--------|-------|
| Extended trigger metadata in AgentConfig | P2 | Low | Better auto-triage |
| Skill card system (composable prompt fragments) | P2 | Medium | Flexible role augmentation |
| Declarative hook config | P1 | Medium | User extensibility |
| Parallel sub-agent fan-out/fan-in | P1 | High | Wave scheduler (§3) |
| File pattern-based agent routing | P3 | Low | Niche use case |

---

## 5. Cross-Cutting Concerns

### 5.1 OTel + Middleware Integration

The tracing middleware (§2.4) and OTel instrumentation (§1) are designed to compose. The `tracingMiddleware` sits at the outermost position in the pipeline, wrapping the entire dispatch in a span. Inner middlewares can add attributes and events to `ctx.span`.

### 5.2 Wave Scheduler + Middleware Integration

Each task dispatched by the wave scheduler goes through the full middleware pipeline. The scheduler itself should be wrapped in a parent span:
```
mercury.plan.execute/{planId}
  ├── mercury.wave/{waveIndex}
  │   ├── mercury.task.dispatch/{taskA}  ← middleware pipeline
  │   └── mercury.task.dispatch/{taskR}  ← middleware pipeline
  ├── mercury.wave/1
  │   └── mercury.task.dispatch/{taskC}
  └── mercury.wave/2
      └── mercury.task.dispatch/{taskB}
```

### 5.3 Implementation Priority

| Component | Priority | Dependencies | Estimated Effort |
|-----------|----------|--------------|-----------------|
| OTel instrumentation file | P1 | None | Small |
| DispatchPipeline + DispatchContext types | P1 | None | Small |
| Refactor dispatchBundleTask → pipeline | P1 | Pipeline types | Medium |
| Wave scheduler (computeWaves + executor) | P1 | Pipeline | Medium |
| Tracing middleware | P2 | OTel + Pipeline | Small |
| AgentConfig trigger extension | P3 | None | Small |
| Skill card system | P3 | role-loader | Medium |

---

## 6. Sources

### OTel Integration
- [OpenTelemetry Node.js Getting Started](https://opentelemetry.io/docs/languages/js/getting-started/nodejs/)
- [OpenTelemetry JS Instrumentation](https://opentelemetry.io/docs/languages/js/instrumentation/)
- [OpenTelemetry Context Propagation](https://opentelemetry.io/docs/languages/js/propagation/)
- [OpenTelemetry Context (JS)](https://opentelemetry.io/docs/languages/js/context/)
- [@opentelemetry/sdk-node on npm](https://www.npmjs.com/package/@opentelemetry/sdk-node) — v0.214.0
- [@opentelemetry/api on npm](https://www.npmjs.com/package/@opentelemetry/api) — v1.9.1
- [@opentelemetry/context-async-hooks on npm](https://www.npmjs.com/package/@opentelemetry/context-async-hooks)
- [OTLP Exporter Configuration](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/)
- [OpenTelemetry JS Exporters](https://opentelemetry.io/docs/languages/js/exporters/)
- [Semantic Conventions for HTTP Spans](https://opentelemetry.io/docs/specs/semconv/http/http-spans/)
- [How to Name Spans in OpenTelemetry](https://oneuptime.com/blog/post/2024-11-04-how-to-name-spans-in-opentelemetry/view)
- [OTel Context Propagation Best Practices](https://oneuptime.com/blog/post/2026-02-02-opentelemetry-context-propagation/view)
- [Propagate Trace Context Across Async Boundaries](https://oneuptime.com/blog/post/2026-02-06-propagate-trace-context-async-boundaries/view)
- [OpenTelemetry Best Practices (Better Stack)](https://betterstack.com/community/guides/observability/opentelemetry-best-practices/)

### Middleware Architecture
- [A Generic Middleware Pattern in TypeScript](https://evertpot.com/generic-middleware/)
- [Make A TypeScript Middleware Engine (DEV Community)](https://dev.to/lcanady/make-a-typescript-middleware-engine-21kb)
- [The Pipeline Pattern (DEV Community)](https://dev.to/wallacefreitas/the-pipeline-pattern-streamlining-data-processing-in-software-architecture-44hn)
- [Understanding TypeScript Middleware Pattern](https://www.webdevtutor.net/blog/typescript-middleware-pattern)
- [Plug-in Architecture (Medium)](https://medium.com/omarelgabrys-blog/plug-in-architecture-dec207291800)

### Wave-Based Scheduling
- [Parallelizing Tasks with Dependencies (DZone)](https://dzone.com/articles/parallelizing-tasks-with-dependencies-design-your)
- [Parallelizing Tasks with Dependencies (Medium)](https://medium.com/@TRikace/parallelizing-tasks-with-dependencies-design-your-code-to-optimize-performance-d0948549f1eb)
- [Task Planning and DAG Architecture (DeepWiki)](https://deepwiki.com/github/awesome-copilot/9.3-task-planning-and-dag-architecture)
- [Coffman-Graham Algorithm (Medium)](https://medium.com/@bolerio/scheduling-tasks-and-drawing-graphs-the-coffman-graham-algorithm-3c85eb975ab)
- [parallel-task-graph (GitHub)](https://github.com/intzeros/parallel-task-graph)

### everything-claude-code
- [everything-claude-code (GitHub)](https://github.com/affaan-m/everything-claude-code)
- [AGENTS.md](https://github.com/affaan-m/everything-claude-code/blob/main/AGENTS.md)
- [Claude Code Sub-Agents Docs](https://code.claude.com/docs/en/sub-agents)
- [Multi-Agent Workflows (Mintlify)](https://www.mintlify.com/VineeTagarwaL-code/claude-code/guides/multi-agent)
- [Agent Patterns Claude Code Skill](https://mcpmarket.com/es/tools/skills/agent-patterns-for-claude-code)
