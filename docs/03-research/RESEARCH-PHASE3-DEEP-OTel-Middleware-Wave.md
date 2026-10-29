# Phase 3 Deep Research: OTel Tracing, Middleware Architecture, Wave-Based Parallel Scheduling

> **Task**: TASK-853f8e03 | **Date**: 2026-04-01
> **Analyst**: Claude Code (dev role)
> **Status**: COMPLETE

---

## 1. OpenTelemetry Integration for Mercury Orchestrator

### 1.1 Required npm Packages (Verified Versions)

| Package | Version | Purpose |
|---------|---------|---------|
| `@opentelemetry/api` | 1.9.1 (stable) | Core tracing API — singleton, app-wide |
| `@opentelemetry/sdk-node` | 0.214.0 | Full Node.js SDK (trace + metrics + logs) |
| `@opentelemetry/sdk-trace-node` | 1.30.1 | Node.js trace SDK with auto context propagation |
| `@opentelemetry/sdk-trace-base` | 1.30.1 | Base span processor + sampler abstractions |
| `@opentelemetry/exporter-trace-otlp-http` | latest | OTLP/HTTP exporter (Jaeger, Grafana Tempo, etc.) |
| `@opentelemetry/semantic-conventions` | latest | Attribute constants (ATTR_SERVICE_NAME, etc.) |
| `@opentelemetry/context-async-hooks` | latest | AsyncLocalStorage-based context propagation |
| `@langfuse/otel` | latest | LangfuseSpanProcessor for LLM observability |

> **Source**: [npm @opentelemetry/api](https://www.npmjs.com/package/@opentelemetry/api), [npm @opentelemetry/sdk-node](https://www.npmjs.com/package/@opentelemetry/sdk-node), [Langfuse OTEL docs](https://langfuse.com/integrations/native/opentelemetry)

**Minimum Requirements**: Node.js ≥18.19.0 || ≥20.6.0, TypeScript ≥5.0.4, compilation target ES2022.

### 1.2 Span Naming Convention

Mercury should adopt the [OTel GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/) for agent spans:

```
mercury.session.{role}             — root span per agent session
mercury.dispatch.{taskId}          — task dispatch lifecycle
mercury.stream.{sessionId}         — streamMessages() duration
mercury.tool.{toolName}            — individual tool invocations
mercury.review.{taskId}            — main review / critic review
invoke_agent {gen_ai.agent.name}   — OTel GenAI convention for agent invocations
```

**Required Attributes** (per OTel GenAI semconv):

| Attribute | Value | Notes |
|-----------|-------|-------|
| `gen_ai.system` | `"mercury"` | System identifier |
| `gen_ai.agent.name` | agentId | Agent identifier |
| `gen_ai.request.model` | model string | e.g. `"claude-opus-4-6"` |
| `gen_ai.usage.input_tokens` | number | From streaming events |
| `gen_ai.usage.output_tokens` | number | From streaming events |
| `service.name` | `"mercury-orchestrator"` | Standard OTel attribute |
| `mercury.task.id` | taskId | Custom attribute |
| `mercury.task.role` | role | dev/research/design |
| `mercury.session.id` | sessionId | Session tracking |

> **Source**: [OTel GenAI Agent Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/), [OTel GenAI Client Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)

### 1.3 Context Propagation Across Async Boundaries

**Recommended**: `AsyncLocalStorageContextManager` (default in `@opentelemetry/sdk-node`).

- `AsyncHooksContextManager` is **deprecated** and will be removed in OTel JS v3 (mid-2025 plan, slipped — still present but deprecated).
- `AsyncLocalStorage` is available since Node.js 14.8.0, simpler, faster (~7% overhead per recent benchmarks), and avoids DoS vulnerability in async_hooks.
- Node.js v24 delivers further `AsyncLocalStorage` performance improvements.
- Context propagation through `async/await` and Promises **just works** when the SDK is configured.

**Mercury-specific concern**: `streamMessages()` uses `for await...of` over adapter.sendPrompt() — context will propagate correctly within this async iterator as long as the root span is started before entering the loop.

```typescript
// Pseudocode: context propagation in streamMessages
const tracer = trace.getTracer('mercury-orchestrator');
await tracer.startActiveSpan(`mercury.stream.${sessionId}`, async (span) => {
  try {
    for await (const yielded of adapter.sendPrompt(...)) {
      // AsyncLocalStorage preserves span context here
      span.addEvent('message_received', { 'mercury.event_kind': yielded.eventKind });
    }
  } finally {
    span.end();
  }
});
```

> **Source**: [OTel JS Context docs](https://opentelemetry.io/docs/languages/js/context/), [@opentelemetry/context-async-hooks npm](https://www.npmjs.com/package/@opentelemetry/context-async-hooks)

### 1.4 Exporter Configuration

**Three export targets** (selectable via `MercuryConfig.otel`):

#### A) Jaeger via OTLP (Recommended Default)

```typescript
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';

const exporter = new OTLPTraceExporter({
  url: 'http://localhost:4318/v1/traces', // Jaeger OTLP HTTP port
});
```

Jaeger ≥1.35 natively supports OTLP on ports 4317 (gRPC) and 4318 (HTTP). The dedicated `@opentelemetry/exporter-jaeger` package is **deprecated** — use OTLP exporter instead.

> **Source**: [OTel JS Exporters](https://opentelemetry.io/docs/languages/js/exporters/), [OTel Jaeger Exporter guide](https://oneuptime.com/blog/post/2026-01-30-opentelemetry-jaeger-exporter/view)

#### B) OTel Collector (Production)

```typescript
const exporter = new OTLPTraceExporter({
  url: process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? 'http://localhost:4318/v1/traces',
});
```

#### C) Langfuse (LLM-Specific Observability)

```typescript
import { LangfuseSpanProcessor } from '@langfuse/otel';

const sdk = new NodeSDK({
  spanProcessors: [new LangfuseSpanProcessor()],
});
```

Langfuse supports OTLP over HTTP (JSON and protobuf). **gRPC is NOT supported**. Provides LLM-specific features: token usage tracking, cost estimation, prompt linking, scoring.

> **Source**: [Langfuse OTel Integration](https://langfuse.com/integrations/native/opentelemetry), [Langfuse existing OTel setup FAQ](https://langfuse.com/faq/all/existing-otel-setup)

### 1.5 Proposed MercuryConfig Extension

```typescript
interface OTelConfig {
  enabled: boolean;               // default: false — zero overhead when disabled
  exporterType: 'otlp' | 'langfuse' | 'console';
  otlpEndpoint?: string;          // default: http://localhost:4318/v1/traces
  langfusePublicKey?: string;
  langfuseSecretKey?: string;
  langfuseBaseUrl?: string;       // default: https://cloud.langfuse.com
  sampleRate?: number;            // 0.0-1.0, default: 1.0
  serviceName?: string;           // default: mercury-orchestrator
}
```

### 1.6 Integration Points in Orchestrator

| Lifecycle Event | OTel Action | Location |
|----------------|-------------|----------|
| `startRoleSession()` | Start root span `mercury.session.{role}` | orchestrator.ts:1407 |
| `dispatchBundleTask()` | Start child span `mercury.dispatch.{taskId}` | orchestrator.ts (dispatch) |
| `streamMessages()` | Start child span `mercury.stream.{sessionId}`, add events per message | orchestrator.ts:1560 |
| `stopSession()` | End root span, record final token count | orchestrator.ts (stop) |
| Tool loop detection | Add span event `mercury.loop_detected` | orchestrator.ts:1597 |
| Token checkpoint | Add span event `mercury.token_checkpoint` | orchestrator.ts (checkTokenThreshold) |
| Task completion/failure | Set span status OK/ERROR, end span | handleDevTaskStreamComplete |

---

## 2. Middleware Architecture Refactor

### 2.1 Design Goals

1. **Pluggable**: Add/remove middleware without touching orchestrator core
2. **Ordered**: Priority-based execution (lower number = earlier)
3. **Safe**: try/catch per middleware — failure logs but doesn't crash streamMessages
4. **Typed**: Full TypeScript interfaces with generic context
5. **Async**: All hooks return `Promise<void>` or `Promise<MiddlewareResult>`

### 2.2 TypeScript Interface Sketch

```typescript
// packages/orchestrator/src/middleware/types.ts

export interface MiddlewareContext {
  agentId: string;
  sessionId: string;
  role: AgentRole;
  taskId?: string;
  taskBundle?: TaskBundle;
  bus: EventBus;
  config: MercuryConfig;
}

export interface MessageContext extends MiddlewareContext {
  message: AgentMessage;
  streamEvent?: AgentStreamingEvent;
  /** Middleware can set this to inject additional context into the next prompt */
  additionalContext?: string;
}

export interface CompletionContext extends MiddlewareContext {
  lastAssistantMessage?: AgentMessage;
  receipt?: ImplementationReceipt;
}

export type MiddlewareResult = {
  action: 'continue' | 'skip' | 'abort';
  reason?: string;
  injectedContext?: string;
};

export interface Middleware {
  /** Unique identifier */
  name: string;
  /** Whether this middleware is active */
  enabled: boolean;
  /** Execution priority (lower = earlier). Default: 500 */
  priority: number;

  /** Called when a role session starts */
  onSessionStart?(ctx: MiddlewareContext): Promise<void>;

  /** Called when a role session ends */
  onSessionEnd?(ctx: MiddlewareContext): Promise<void>;

  /** Called for each message/streaming event in streamMessages() */
  onMessage?(ctx: MessageContext): Promise<MiddlewareResult>;

  /** Called when a task stream completes (before receipt parsing) */
  onCompletion?(ctx: CompletionContext): Promise<MiddlewareResult>;

  /** Called on errors during streaming */
  onError?(ctx: MiddlewareContext, error: Error): Promise<void>;
}
```

### 2.3 Pipeline Runner

```typescript
// packages/orchestrator/src/middleware/pipeline.ts

export class MiddlewarePipeline {
  private middlewares: Middleware[] = [];

  register(mw: Middleware): void {
    this.middlewares.push(mw);
    this.middlewares.sort((a, b) => a.priority - b.priority);
  }

  unregister(name: string): void {
    this.middlewares = this.middlewares.filter(m => m.name !== name);
  }

  async executeOnSessionStart(ctx: MiddlewareContext): Promise<void> {
    for (const mw of this.getEnabled()) {
      try {
        await mw.onSessionStart?.(ctx);
      } catch (err) {
        console.error(`[middleware:${mw.name}] onSessionStart error:`, err);
        // Log-only, do not propagate
      }
    }
  }

  async executeOnMessage(ctx: MessageContext): Promise<MiddlewareResult> {
    for (const mw of this.getEnabled()) {
      try {
        const result = await mw.onMessage?.(ctx);
        if (result?.action === 'abort') return result;
        if (result?.injectedContext) {
          ctx.additionalContext = (ctx.additionalContext ?? '') + '\n' + result.injectedContext;
        }
      } catch (err) {
        console.error(`[middleware:${mw.name}] onMessage error:`, err);
      }
    }
    return { action: 'continue' };
  }

  async executeOnCompletion(ctx: CompletionContext): Promise<MiddlewareResult> {
    for (const mw of this.getEnabled()) {
      try {
        const result = await mw.onCompletion?.(ctx);
        if (result?.action === 'abort') return result;
      } catch (err) {
        console.error(`[middleware:${mw.name}] onCompletion error:`, err);
      }
    }
    return { action: 'continue' };
  }

  private getEnabled(): Middleware[] {
    return this.middlewares.filter(m => m.enabled);
  }
}
```

### 2.4 Built-in Middleware Candidates

| Middleware | Priority | Purpose | Existing Code to Migrate |
|-----------|----------|---------|-------------------------|
| `OTelTracingMiddleware` | 50 | Span creation/events per message | New (Section 1) |
| `LoopDetectionMiddleware` | 100 | Detect repeated tool calls | orchestrator.ts:1597-1608 (inline) |
| `TokenCheckpointMiddleware` | 150 | Token usage tracking + auto-handoff | orchestrator.ts:1576-1581 (inline) |
| `TranscriptMiddleware` | 200 | Persist messages to transcript | orchestrator.ts:1619-1625 (inline) |
| `PreCompletionCheckMiddleware` | 300 | DoD verification before close | Phase 2.2 (planned) |
| `SecurityAnalysisMiddleware` | 400 | Scan for hardcoded secrets | New |
| `NotificationMiddleware` | 900 | Forward events to RPC transport | orchestrator.ts:1584-1595, 1627-1637 (inline) |

### 2.5 Retrofitting onto Existing Orchestrator

**Current state**: All message-level logic is inline in `streamMessages()` (orchestrator.ts:1560-1639) — loop detection, token tracking, transcript persistence, transport notification are all interleaved in the for-await loop.

**Migration strategy**:

1. Create `packages/orchestrator/src/middleware/` directory
2. Define interfaces in `middleware/types.ts`
3. Implement `MiddlewarePipeline` in `middleware/pipeline.ts`
4. Extract inline logic into individual middleware classes
5. Add `middlewarePipeline` as Orchestrator instance field
6. Replace inline logic in `streamMessages()` with pipeline calls:

```typescript
// Before (current):
if (yielded.toolName && yielded.eventKind === 'tool_start') {
  if (this.detectToolLoop(sessionId, yielded.toolName)) { ... }
}

// After (middleware):
const msgCtx: MessageContext = { ...baseCtx, streamEvent: yielded };
const result = await this.middlewarePipeline.executeOnMessage(msgCtx);
if (result.action === 'abort') break;
```

7. Register built-in middlewares in Orchestrator constructor
8. Expose `registerMiddleware()` API for external plugins

**Risk mitigation**:
- Each middleware wrapped in try/catch — failure is log-only
- Performance: each await adds ~0.1-1ms; 7 middlewares ≈ 1-7ms per message — negligible vs LLM latency (seconds)
- Backward compat: no API surface change; internal refactor only

### 2.6 Comparison with Claude Code Hook System

Claude Code (2026) hooks provide 21 lifecycle events with 4 handler types (command, http, prompt, agent):

| Claude Code Hook | Mercury Middleware Equivalent | Notes |
|-----------------|------------------------------|-------|
| `PreToolUse` | `onMessage` (filter `tool_start`) | Safety gate before tool execution |
| `PostToolUse` | `onMessage` (filter `tool_result`) | Quality gate after tool execution |
| `SessionStart` | `onSessionStart` | Session initialization |
| `Stop` | `onCompletion` | Pre-completion checks |

**Key difference**: Claude Code hooks are external (shell commands, HTTP calls) while Mercury middleware is in-process (TypeScript classes). Mercury's approach is lower latency and type-safe but less isolated.

> **Source**: [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks), [Claude Agent SDK Hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)

---

## 3. Wave-Based Parallel Scheduling

### 3.1 Problem Statement

Mercury currently dispatches tasks sequentially. When multiple tasks have no dependencies, they should run in parallel. When tasks have dependencies (e.g., "implement API" → "write tests"), they must be ordered correctly.

### 3.2 Dependency Graph Model

```typescript
// packages/orchestrator/src/task-scheduler.ts

export interface TaskNode {
  taskId: string;
  dependencies: string[];  // taskIds that must complete before this task
  status: TaskStatus;
  wave?: number;           // assigned wave (0 = first, highest parallelism)
}

export interface Wave {
  waveNumber: number;
  tasks: TaskNode[];
}

export interface ScheduleResult {
  waves: Wave[];
  hasCycle: boolean;
  cycleNodes?: string[];   // nodes involved in cycle, if detected
}
```

### 3.3 TaskBundle Extension

```typescript
// Extension to existing TaskBundle interface
interface TaskBundle {
  // ... existing fields ...

  /** Task scheduling metadata */
  scheduling?: {
    /** Tasks that must complete before this one starts */
    blockedBy?: string[];
    /** Soft ordering preference (not blocking) */
    mustRunAfter?: string[];
    /** Assigned wave number (set by scheduler) */
    wave?: number;
    /** Whether this task can run in parallel with others in same wave */
    parallel?: boolean;  // default: true
  };
}
```

### 3.4 Wave Detection Algorithm (Kahn's Algorithm)

Kahn's algorithm (BFS-based topological sort) naturally produces waves — each iteration processes all nodes with in-degree 0, which maps directly to a wave of parallelizable tasks.

```typescript
export function computeWaves(tasks: TaskNode[]): ScheduleResult {
  const taskMap = new Map(tasks.map(t => [t.taskId, t]));
  const inDegree = new Map<string, number>();
  const adjList = new Map<string, string[]>(); // parent -> children

  // Initialize
  for (const task of tasks) {
    inDegree.set(task.taskId, 0);
    adjList.set(task.taskId, []);
  }

  // Build graph
  for (const task of tasks) {
    for (const dep of task.dependencies) {
      if (!taskMap.has(dep)) continue; // skip unknown deps
      adjList.get(dep)!.push(task.taskId);
      inDegree.set(task.taskId, (inDegree.get(task.taskId) ?? 0) + 1);
    }
  }

  // Kahn's BFS — wave by wave
  const waves: Wave[] = [];
  let queue = tasks
    .filter(t => inDegree.get(t.taskId) === 0)
    .map(t => t.taskId);
  let processedCount = 0;

  while (queue.length > 0) {
    const waveNumber = waves.length;
    const waveTasks: TaskNode[] = [];

    const nextQueue: string[] = [];
    for (const taskId of queue) {
      const task = taskMap.get(taskId)!;
      task.wave = waveNumber;
      waveTasks.push(task);
      processedCount++;

      for (const child of adjList.get(taskId) ?? []) {
        const newDegree = (inDegree.get(child) ?? 1) - 1;
        inDegree.set(child, newDegree);
        if (newDegree === 0) {
          nextQueue.push(child);
        }
      }
    }

    waves.push({ waveNumber, tasks: waveTasks });
    queue = nextQueue;
  }

  // Cycle detection: if not all nodes processed, there's a cycle
  const hasCycle = processedCount < tasks.length;
  const cycleNodes = hasCycle
    ? tasks.filter(t => t.wave === undefined).map(t => t.taskId)
    : undefined;

  return { waves, hasCycle, cycleNodes };
}
```

**Complexity**: O(V + E) where V = tasks, E = dependency edges.

### 3.5 Wave Executor

```typescript
export class WaveScheduler {
  private maxConcurrency: number;

  constructor(maxConcurrency: number = 3) {
    this.maxConcurrency = maxConcurrency;
  }

  async executeWaves(
    schedule: ScheduleResult,
    dispatch: (taskId: string) => Promise<void>,
  ): Promise<WaveExecutionResult> {
    if (schedule.hasCycle) {
      throw new Error(
        `Dependency cycle detected in tasks: ${schedule.cycleNodes?.join(', ')}`
      );
    }

    const results: WaveResult[] = [];

    for (const wave of schedule.waves) {
      const tasksToRun = wave.tasks.filter(t =>
        t.status === 'pending' || t.status === 'ready'
      );

      // Chunk by maxConcurrency
      const chunks = this.chunk(tasksToRun, this.maxConcurrency);

      for (const chunk of chunks) {
        // Promise.allSettled — one failure doesn't block others in wave
        const settled = await Promise.allSettled(
          chunk.map(task => dispatch(task.taskId))
        );

        results.push({
          waveNumber: wave.waveNumber,
          results: settled.map((s, i) => ({
            taskId: chunk[i].taskId,
            status: s.status,
            error: s.status === 'rejected' ? String(s.reason) : undefined,
          })),
        });
      }

      // Check if any task in this wave failed — decide whether to continue
      const waveResults = results.filter(r => r.waveNumber === wave.waveNumber);
      const hasFailure = waveResults.some(r =>
        r.results.some(t => t.status === 'rejected')
      );

      if (hasFailure) {
        // Propagate failure: mark dependent tasks as blocked
        // Continue to next wave anyway — tasks without failed dependencies proceed
      }
    }

    return { results };
  }

  private chunk<T>(arr: T[], size: number): T[][] {
    const chunks: T[][] = [];
    for (let i = 0; i < arr.length; i += size) {
      chunks.push(arr.slice(i, i + size));
    }
    return chunks;
  }
}
```

### 3.6 Failure Propagation

When a task in Wave N fails:
1. Mark all tasks in Wave N+1..K that transitively depend on it as `blocked`
2. Tasks in later waves that do NOT depend on the failed task continue normally
3. Use `Promise.allSettled()` (not `Promise.all()`) to avoid early abort

### 3.7 Integration with TaskManager State Machine

Current task statuses: `pending → dispatched → in_progress → closed/failed`

**New statuses needed**:
- `ready` — dependencies satisfied, eligible for dispatch
- `blocked` — waiting on dependencies or upstream failure

**State transitions**:
```
pending → ready (when all blockedBy tasks reach "closed" or "verified")
ready → dispatched (by WaveScheduler)
dispatched → in_progress → closed/failed (existing flow)
pending → blocked (when a dependency fails)
```

**Integration point**: `TaskManager.transitionTask()` should check `scheduling.blockedBy` before allowing `pending → dispatched` transition.

### 3.8 Parallel Branch Push Concern

**Risk**: Multiple parallel tasks pushing to the same branch → git conflicts.

**Mitigation strategies**:
1. Each task operates on its own branch (`{agent}/{task-name}`) — already Mercury convention
2. Parallel tasks in the same wave must have separate branches
3. `WaveScheduler.validate()` should reject waves where multiple tasks share a branch
4. Git worktree isolation (already identified as a concern in RESEARCH-CALLBACK-001) prevents file-level conflicts

---

## 4. Everything-Claude-Code Patterns & Adaptation

### 4.1 Tool Discovery (Lazy/Deferred Loading)

**Claude Code pattern**: Tools are lazily loaded via `ToolSearch` — the agent starts with minimal tool definitions and discovers tools on-demand. This reduces initial context from 50,000+ tokens to a few hundred.

**Mercury adaptation**: Currently, Mercury injects all KB context into the role prompt upfront. Instead:
- Define a `ToolRegistry` that lists available tools/MCP endpoints
- Inject only tool names + descriptions (not full schemas) into the prompt
- Agent invokes `ToolSearch` equivalent to get full schemas when needed
- Reduces prompt bloat, especially for roles with many optional tools

**Recommendation**: Medium priority. Implement after middleware refactor, as middleware can intercept tool discovery requests.

### 4.2 Hook-Based Control Layer

**Claude Code pattern (2026)**: 21 lifecycle events, 4 handler types (command, http, prompt, agent). PreToolUse/PostToolUse create deterministic safety gates around tool calls. Without hooks, CLAUDE.md rules are advisory; with hooks, they become enforced.

**Mercury adaptation**: The middleware architecture (Section 2) directly adopts this pattern:
- `onMessage` with `tool_start` filter → equivalent to PreToolUse
- `onMessage` with `tool_result` filter → equivalent to PostToolUse
- `onCompletion` → equivalent to Stop hook

**Key insight**: Claude Code hooks are external processes (shell commands, HTTP) for isolation. Mercury middleware is in-process for performance. Consider hybrid: in-process for critical-path (loop detection, tracing), external for user-defined hooks (linters, validators).

> **Source**: [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks), [ClaudeLog Hooks Guide](https://claudelog.com/mechanics/hooks/)

### 4.3 Sub-Agent Architecture

**Claude Code pattern**: The `Agent` tool spawns sub-agents with isolated context. Each sub-agent gets a subset of tools (`allowedTools`), a scoped working directory, and can optionally run in a git worktree for isolation.

**Mercury adaptation**: Mercury already has:
- Role-based sessions with `startRoleSession()`
- Task-scoped tool restrictions via `allowedWriteScope`
- Agent registry with per-agent configuration

**Gap**: Mercury doesn't support git worktree isolation. Each dispatched task currently runs in the same working directory, causing branch collision (documented in `project_dispatch_architecture_findings.md`). Worktree isolation should be a Phase 3+ priority.

> **Source**: [Claude Code Sub-Agents](https://code.claude.com/docs/en/sub-agents)

### 4.4 Session Fork for Exploration

**Claude Code pattern**: `fork: true` creates a branch of the current session for speculative exploration. If the fork fails, the parent session continues unaffected.

**Mercury adaptation**: Mercury's `handoffSession()` inherits context but doesn't create a true fork. Recommendation:
- Add `forkSession()` API that snapshots current session state
- Main Agent can fork to explore alternative approaches
- Integrate with event sourcing for deterministic replay

**Recommendation**: Low priority. Useful for research tasks but not critical path.

### 4.5 Quality Loop Pattern

**Claude Code pattern**: PostToolUse hooks run validators (linters, type checkers) automatically. Results flow back as `additionalContext` into the agent's next turn, creating a self-correcting loop without human intervention.

**Mercury adaptation**: Implement as `QualityLoopMiddleware`:
```typescript
class QualityLoopMiddleware implements Middleware {
  name = 'quality-loop';
  enabled = true;
  priority = 250;

  async onMessage(ctx: MessageContext): Promise<MiddlewareResult> {
    if (ctx.streamEvent?.eventKind !== 'tool_result') return { action: 'continue' };
    if (ctx.streamEvent.toolName !== 'Write' && ctx.streamEvent.toolName !== 'Edit') {
      return { action: 'continue' };
    }

    // Run linter/type-checker on modified file
    const lintResult = await this.runLinter(ctx);
    if (lintResult.errors.length > 0) {
      return {
        action: 'continue',
        injectedContext: `[LINT] ${lintResult.errors.length} errors found:\n${lintResult.summary}`,
      };
    }
    return { action: 'continue' };
  }
}
```

> **Source**: [Claude Code Hooks Tutorial](https://blakecrosley.com/blog/claude-code-hooks-tutorial), [Claude Code Hooks Production Patterns](https://www.pixelmojo.io/blogs/claude-code-hooks-production-quality-ci-cd-patterns)

### 4.6 Adaptation Recommendations Summary

| Pattern | Priority | Effort | Mercury Gap |
|---------|----------|--------|-------------|
| Middleware/Hooks | P0 | 12-15h | Inline logic needs extraction |
| OTel Tracing | P1 | 6-8h | No observability currently |
| Wave Scheduling | P1 | 8-10h | Sequential dispatch only |
| Quality Loop | P2 | 4-6h | No auto-validation feedback |
| Tool Discovery (lazy) | P2 | 6-8h | Full prompt injection |
| Worktree Isolation | P2 | 8-10h | Branch collision risk |
| Session Fork | P3 | 10-12h | No speculative exploration |

---

## 5. Verification Matrix

| Claim | Verified? | Source |
|-------|-----------|--------|
| `@opentelemetry/api` 1.9.1 is latest stable | ✅ | [npm](https://www.npmjs.com/package/@opentelemetry/api) |
| `@opentelemetry/sdk-node` 0.214.0 is latest | ✅ | [npm](https://www.npmjs.com/package/@opentelemetry/sdk-node) |
| AsyncHooksContextManager deprecated in OTel JS | ✅ | [npm](https://www.npmjs.com/package/@opentelemetry/context-async-hooks) |
| Jaeger ≥1.35 supports OTLP natively | ✅ | [OTel Exporters docs](https://opentelemetry.io/docs/languages/js/exporters/) |
| `@opentelemetry/exporter-jaeger` deprecated | ✅ | [npm](https://www.npmjs.com/package/@opentelemetry/exporter-jaeger) |
| Langfuse supports OTLP HTTP (no gRPC) | ✅ | [Langfuse docs](https://langfuse.com/integrations/native/opentelemetry) |
| OTel GenAI semconv uses `invoke_agent` span name | ✅ | [OTel semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/) |
| Node.js ≥18.19.0 required for OTel JS SDK | ✅ | [npm sdk-node](https://www.npmjs.com/package/@opentelemetry/sdk-node) |
| TypeScript ≥5.0.4 required for OTel JS SDK | ✅ | [npm sdk-node](https://www.npmjs.com/package/@opentelemetry/sdk-node) |
| AsyncLocalStorage ~7% perf overhead | ✅ | [Platformatic blog](https://blog.platformatic.dev/the-hidden-cost-of-context) |
| Claude Code has 21 lifecycle hook events (2026) | ✅ | [Hooks reference](https://code.claude.com/docs/en/hooks) |
| Kahn's algorithm O(V+E) for topo sort | ✅ | [Wikipedia](https://en.wikipedia.org/wiki/Topological_sorting) |
| `LangfuseSpanProcessor` import from `@langfuse/otel` | ✅ | [Langfuse docs](https://langfuse.com/integrations/native/opentelemetry) |
| OTel JS SDK 2.0 announced | ✅ | [OTel blog](https://opentelemetry.io/blog/2025/otel-js-sdk-2-0/) |
| Mercury streamMessages uses for-await-of | ✅ | orchestrator.ts:1571 (codebase) |
| Mercury loop detection is inline in streamMessages | ✅ | orchestrator.ts:1597-1608 (codebase) |

---

## 6. Open Questions & Risks

1. **OTel SDK 2.0 migration**: The OTel JS SDK 2.0 was announced — should Mercury target 2.0 directly or start with current stable (0.214.x)? **Recommendation**: Start with current stable; SDK 2.0 API is backward-compatible for `@opentelemetry/api`.

2. **Middleware performance in hot path**: `streamMessages()` processes every token event. Middleware execution must be fast (<5ms total). Profile after implementation.

3. **Wave scheduling + git worktree**: Parallel tasks need branch isolation. Without worktree support, limit wave concurrency to 1 task per agent (already the case today).

4. **Langfuse vs pure OTLP**: Langfuse provides LLM-specific features (cost tracking, prompt linking) that raw OTLP doesn't. Consider Langfuse as the default for development, OTLP for production.

5. **UNVERIFIED**: Exact `@opentelemetry/sdk-trace-node` version 1.30.1 — inferred from SDK monorepo release cadence, not directly confirmed on npm page during this research session.
