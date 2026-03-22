/**
 * Task Persistence — Dual-write layer.
 *
 * Writes to SQLite (primary) first, then asynchronously syncs to KB (secondary).
 * Reads always come from SQLite for speed and indexed queries.
 * Git sync is delegated to the KB layer.
 */

import type { TaskBundle, AcceptanceBundle, IssueBundle, TaskStatus } from "@mercury/core";
import type { TaskPersistence } from "./task-persistence-kb.js";

type Log = (msg: string) => void;

/** Dual-write persistence: writes to primary (SQLite) first, then async-syncs to secondary (KB). Reads from primary only. */
export class TaskPersistenceDual implements TaskPersistence {
  constructor(
    private primary: TaskPersistence,
    private secondary: TaskPersistence,
    private log: Log = () => {},
  ) {}

  /** Save task to primary (SQLite), then fire-and-forget to secondary (KB). */
  async saveTask(task: TaskBundle): Promise<void> {
    await this.primary.saveTask(task);
    // Fire-and-forget to secondary (KB)
    this.secondary.saveTask(task).catch((err) => {
      this.log(`[dual] Secondary saveTask failed: ${err instanceof Error ? err.message : err}`);
    });
  }

  /** Save acceptance to primary, then fire-and-forget to secondary. */
  async saveAcceptance(acc: AcceptanceBundle): Promise<void> {
    await this.primary.saveAcceptance(acc);
    this.secondary.saveAcceptance(acc).catch((err) => {
      this.log(`[dual] Secondary saveAcceptance failed: ${err instanceof Error ? err.message : err}`);
    });
  }

  /** Save issue to primary, then fire-and-forget to secondary. */
  async saveIssue(issue: IssueBundle): Promise<void> {
    await this.primary.saveIssue(issue);
    this.secondary.saveIssue(issue).catch((err) => {
      this.log(`[dual] Secondary saveIssue failed: ${err instanceof Error ? err.message : err}`);
    });
  }

  /** Load all entities from primary (SQLite). */
  async loadAll(): Promise<{
    tasks: TaskBundle[];
    acceptances: AcceptanceBundle[];
    issues: IssueBundle[];
  }> {
    return this.primary.loadAll();
  }

  /** Load a single task from primary. */
  async loadTask(taskId: string): Promise<TaskBundle | null> {
    return this.primary.loadTask(taskId);
  }

  /** Load tasks with optional filter from primary. */
  async loadTaskList(filter?: { status?: TaskStatus; assignedTo?: string }): Promise<TaskBundle[]> {
    return this.primary.loadTaskList(filter);
  }

  /** Delegate git sync to KB (secondary) layer. */
  async gitSync(message: string): Promise<void> {
    return this.secondary.gitSync(message);
  }
}
