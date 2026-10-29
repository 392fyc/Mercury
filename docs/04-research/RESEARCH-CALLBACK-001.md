# Deep Research: Callback Chain Architecture (Issue #99)

> Task: TASK-1dd8330b | Verdict: pass
> Completed: 2026-03-29T12:21:43.391Z

## Findings

FINDING-1: PLATFORM PATTERN SURVEY 鈥 ->  Callback mechanisms across 7 platforms fall into 3 categories: (A) State-machine callbacks (LangGraph, CrewAI): callbacks are synchronous, inline with task lifecycle. CrewAI has explicit task_callback and step_callback hooks receiving TaskOutput objects. LangGraph uses graph-node state transitions with no explicit callback API 鈥 ->  monitoring is via LangSmith tracing. (B) Durable message-passing (Temporal, Inngest): Temporal uses Signals (async, fire-and-forget) and Updates (sync, tracked) for workflow-to-workflow notification. Inngest uses event-driven step invocation with inngest/function.finished events and automatic retry. Both persist state across crashes. (C) Protocol-level notification (Google A2A, AutoGen): A2A defines webhook push notifications for task updates via HTTP POST with StreamResponse payloads, persisting until task completion. AutoGen uses request/response patterns with @response_handler decorators.

FINDING-2: MERCURY CURRENT STATE 鈥 ->  deliverTaskCallback (orchestrator.ts:2728-2794) is purely best-effort, fire-and-forget. Three critical gaps: (1) If no active Main Agent session exists, callback is silently dropped with only a log message. (2) External MCP clients rely solely on SSE broadcaster which is connection-scoped 鈥 ->  if SSE disconnects, events are lost. (3) EventBus (event-bus.ts) is in-memory only with no persistence 鈥 ->  process restart loses all pending callbacks. The NotificationBroadcaster has no message buffering or replay capability.

FINDING-3: PERSISTENT QUEUE ARCHITECTURE 鈥 ->  Mercury already uses better-sqlite3 with WAL mode for task persistence (task-persistence-sqlite.ts). Adding a callback_queue table to the existing SQLite database is the lowest-friction option. No need for Redis (single-process sidecar architecture). SQLite WAL handles 100k+ ops/sec which far exceeds Mercury's callback volume. A separate queue table with status tracking (pending/delivered/failed/expired) enables crash recovery and retry.

FINDING-4: EXTERNAL NOTIFICATION DESIGN 鈥 ->  Current NotificationBroadcaster supports SSE and stdio channels but lacks: (a) message buffering for disconnected clients, (b) webhook outbound delivery, (c) Last-Event-ID replay per SSE spec. The A2A protocol's webhook pattern is the most applicable model: register a webhook URL per task, POST callback payloads on state change, with retry and exponential backoff. For Mercury, a hybrid SSE+webhook model covers both real-time GUI updates and external integrations.

FINDING-5: CONTEXT-AWARE COLD-START DELIVERY 鈥 ->  When Main Agent session is absent, Temporal's Signal pattern is most applicable: queue the callback persistently, and on next Main Agent session creation, drain pending callbacks with full context injection. The callback prompt should include not just verdict but also: task title, original dispatch context, elapsed time, and summary of acceptance findings 鈥 ->  enabling Main Agent to act without re-reading the task.

FINDING-6: IDEMPOTENCY AND DEDUP 鈥 ->  Per Martin Fowler's Idempotent Receiver pattern and microservices.io's Idempotent Consumer pattern, each callback needs a unique idempotency key (taskId + verdict + timestamp hash). The callback_queue table should store this key with a UNIQUE constraint. On delivery attempt, check if already delivered before processing. This prevents duplicate notifications on retry or process restart.

FINDING-7: MERCURY CONSTRAINTS 鈥 ->  Node.js sidecar architecture means: single-process (no distributed queue needed), SQLite is already the persistence layer, EventBus is in-process. The solution must not introduce external dependencies (no Redis, no message broker). All persistence goes through better-sqlite3. The callback queue should be a new table in the existing mercury.db file.

## Recommendations

REC-1: ADD callback_queue TABLE TO EXISTING SQLITE 鈥 ->  Schema: id (UUID PK), task_id (TEXT), callback_type (TEXT: 'task_completion'|'task_update'|'external_webhook'), payload_json (TEXT), status (TEXT: 'pending'|'delivering'|'delivered'|'failed'|'expired'), idempotency_key (TEXT UNIQUE), created_at (INTEGER), delivered_at (INTEGER), retry_count (INTEGER DEFAULT 0), max_retries (INTEGER DEFAULT 3), next_retry_at (INTEGER). Add to existing migration chain in task-persistence-sqlite.ts.

REC-2: IMPLEMENT CallbackQueue CLASS 鈥 ->  New file: callback-queue.ts. Methods: enqueue(payload) 鈫 ->  persists to SQLite with 'pending' status; dequeue(filter?) 鈫 ->  returns oldest pending callbacks; markDelivered(id); markFailed(id, error); drainPending(targetType) 鈫 ->  returns all pending for a target type. Integrates with existing EventBus via bus.on('orchestrator.task.callback').

REC-3: REFACTOR deliverTaskCallback TO USE QUEUE 鈥 ->  Instead of direct sendPrompt, flow becomes: (1) enqueue callback to SQLite, (2) attempt immediate delivery if Main Agent session active, (3) on success mark delivered, (4) on failure leave as pending for cold-start drain. This eliminates silent drops.

REC-4: ADD COLD-START DRAIN TO SESSION CREATION 鈥 ->  When a new Main Agent session is created (in roleSessions.set), drain all pending callbacks from the queue. Inject them as a batch context prompt: '[Queued Callbacks] N task callbacks were pending while you were offline: ...' This follows Temporal's Signal-drain-on-wake pattern.

REC-5: ADD SSE REPLAY WITH Last-Event-ID 鈥 ->  Extend NotificationBroadcaster to support SSE Last-Event-ID header. On reconnection, client sends last received event ID; broadcaster replays missed events from the callback_queue. This follows the SSE specification and eliminates the current gap where SSE disconnection loses events.

REC-6: ADD OPTIONAL WEBHOOK CHANNEL 鈥 ->  New NotificationChannel implementation: WebhookChannel. Registered per-task or globally. On callback, POST JSON payload to registered URL with HMAC signature, retry with exponential backoff (300ms base, 30s cap 鈥 ->  matching existing DISPATCH_RETRY constants). Follows A2A protocol's push notification pattern.

REC-7: IDEMPOTENCY KEY FORMAT 鈥 ->  Use `${taskId}:${verdict}:${acceptanceBundleId}` as the idempotency key. This ensures the same acceptance result is never delivered twice, even across process restarts. The UNIQUE constraint on idempotency_key in SQLite handles dedup at the storage level.

