/**
 * Callback Queue — SQLite-backed persistent queue for task completion callbacks.
 *
 * Callbacks are enqueued when a task completes and delivered to the Main Agent
 * session when available. If no session is active, callbacks remain pending and
 * are drained on the next Main Agent session creation (cold-start drain).
 *
 * Uses the same better-sqlite3 Database instance as TaskPersistenceSqlite.
 * The callback_queue table is created by migration v3.
 */

import type Database from "better-sqlite3";

/** Payload emitted by orchestrator.task.callback events. */
export interface TaskCallbackPayload {
  taskId: string;
  originatorSessionId?: string;
  verdict: string;
  findings?: string[];
  recommendations?: string[];
}

export interface CallbackQueueEntry {
  id: number;
  idempotency_key: string;
  task_id: string;
  verdict: string;
  payload_json: string;
  status: "pending" | "delivered" | "failed";
  created_at: number;
  delivered_at: number | null;
  delivery_attempts: number;
  last_error: string | null;
}

type Log = (msg: string) => void;

const DEFAULT_MAX_ATTEMPTS = 5;
const DEFAULT_RETENTION_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

export class CallbackQueue {
  private stmts: {
    enqueue: Database.Statement;
    getPending: Database.Statement;
    markDelivered: Database.Statement;
    incrementAttempt: Database.Statement;
    markFailed: Database.Statement;
    getById: Database.Statement;
    cleanup: Database.Statement;
  };
  private log: Log;

  constructor(db: Database.Database, log: Log = () => {}) {
    this.log = log;
    this.stmts = {
      enqueue: db.prepare(`
        INSERT OR IGNORE INTO callback_queue
          (idempotency_key, task_id, verdict, payload_json, status, created_at)
        VALUES (@idempotency_key, @task_id, @verdict, @payload_json, 'pending', @created_at)
      `),
      getPending: db.prepare(
        `SELECT * FROM callback_queue WHERE status = 'pending' ORDER BY created_at ASC`,
      ),
      markDelivered: db.prepare(
        `UPDATE callback_queue SET status = 'delivered', delivered_at = @now WHERE id = @id`,
      ),
      incrementAttempt: db.prepare(
        `UPDATE callback_queue SET delivery_attempts = delivery_attempts + 1, last_error = @error WHERE id = @id`,
      ),
      markFailed: db.prepare(
        `UPDATE callback_queue SET status = 'failed' WHERE id = @id`,
      ),
      getById: db.prepare(
        `SELECT * FROM callback_queue WHERE id = @id`,
      ),
      cleanup: db.prepare(
        `DELETE FROM callback_queue WHERE status = 'delivered' AND delivered_at < @cutoff`,
      ),
    };
  }

  /**
   * Generate idempotency key from payload.
   * Uses a 10-second time bucket to deduplicate rapid retries of the same verdict.
   */
  static makeIdempotencyKey(payload: TaskCallbackPayload): string {
    const bucket = Math.floor(Date.now() / 10_000);
    return `${payload.taskId}:${payload.verdict}:${bucket}`;
  }

  /**
   * Insert a callback into the queue.
   * Returns true if inserted, false if idempotency key already exists.
   */
  enqueue(payload: TaskCallbackPayload): boolean {
    const key = CallbackQueue.makeIdempotencyKey(payload);
    const result = this.stmts.enqueue.run({
      idempotency_key: key,
      task_id: payload.taskId,
      verdict: payload.verdict,
      payload_json: JSON.stringify(payload),
      created_at: Date.now(),
    });
    const inserted = result.changes > 0;
    if (inserted) {
      this.log(`[callback-queue] Enqueued callback for ${payload.taskId} (verdict: ${payload.verdict})`);
    }
    return inserted;
  }

  /** Return all pending entries ordered by created_at ASC. */
  getPending(): CallbackQueueEntry[] {
    return this.stmts.getPending.all() as CallbackQueueEntry[];
  }

  /** Mark a queued entry as successfully delivered. */
  markDelivered(id: number): void {
    this.stmts.markDelivered.run({ id, now: Date.now() });
  }

  /**
   * Increment attempt count and record error.
   * After maxAttempts, mark as "failed" to stop further retries.
   */
  markAttemptFailed(id: number, error: string, maxAttempts = DEFAULT_MAX_ATTEMPTS): void {
    this.stmts.incrementAttempt.run({ id, error });
    const entry = this.stmts.getById.get({ id }) as CallbackQueueEntry | undefined;
    if (entry && entry.delivery_attempts >= maxAttempts) {
      this.stmts.markFailed.run({ id });
      this.log(`[callback-queue] Callback ${id} failed after ${maxAttempts} attempts`);
    }
  }

  /**
   * Delete delivered entries older than retentionMs (default 7 days).
   * Returns count of deleted entries.
   */
  cleanup(retentionMs = DEFAULT_RETENTION_MS): number {
    const cutoff = Date.now() - retentionMs;
    const result = this.stmts.cleanup.run({ cutoff });
    return result.changes;
  }
}
