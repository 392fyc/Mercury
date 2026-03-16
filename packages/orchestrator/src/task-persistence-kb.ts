/**
 * Task Persistence — JSON file storage via KnowledgeService (Obsidian KB).
 *
 * File convention:
 *   tasks/TASK-xxx.json
 *   acceptances/ACC-xxx.json
 *   issues/ISS-xxx.json
 *
 * Each file is a complete JSON snapshot of the entity.
 * Writes are fire-and-forget — errors are logged, never block the state machine.
 *
 * KB is the authoritative source. TaskManager reads from KB first,
 * falls back to in-memory Map as write-through cache.
 */

import type { TaskBundle, AcceptanceBundle, IssueBundle, TaskStatus } from "@mercury/core";
import type { KnowledgeService } from "./knowledge-service.js";

export interface TaskPersistence {
  saveTask(task: TaskBundle): Promise<void>;
  saveAcceptance(acc: AcceptanceBundle): Promise<void>;
  saveIssue(issue: IssueBundle): Promise<void>;
  loadAll(): Promise<{
    tasks: TaskBundle[];
    acceptances: AcceptanceBundle[];
    issues: IssueBundle[];
  }>;
  loadTask(taskId: string): Promise<TaskBundle | null>;
  loadTaskList(filter?: { status?: TaskStatus; assignedTo?: string }): Promise<TaskBundle[]>;
  gitSync(message: string): Promise<void>;
}

export class TaskPersistenceKB implements TaskPersistence {
  constructor(
    private kb: KnowledgeService,
    private log: (msg: string) => void = () => {},
  ) {}

  async saveTask(task: TaskBundle): Promise<void> {
    await this.writeJson(`tasks/${task.taskId}`, task);
  }

  async saveAcceptance(acc: AcceptanceBundle): Promise<void> {
    await this.writeJson(`acceptances/${acc.acceptanceId}`, acc);
  }

  async saveIssue(issue: IssueBundle): Promise<void> {
    await this.writeJson(`issues/${issue.issueId}`, issue);
  }

  async loadAll(): Promise<{
    tasks: TaskBundle[];
    acceptances: AcceptanceBundle[];
    issues: IssueBundle[];
  }> {
    const [tasks, acceptances, issues] = await Promise.all([
      this.loadFolder<TaskBundle>("tasks"),
      this.loadFolder<AcceptanceBundle>("acceptances"),
      this.loadFolder<IssueBundle>("issues"),
    ]);

    this.log(
      `Rehydrated from KB: ${tasks.length} tasks, ${acceptances.length} acceptances, ${issues.length} issues`,
    );

    return { tasks, acceptances, issues };
  }

  /** Load a single task from KB by ID. */
  async loadTask(taskId: string): Promise<TaskBundle | null> {
    try {
      const raw = await this.kb.read(`tasks/${taskId}`);
      return JSON.parse(raw) as TaskBundle;
    } catch {
      return null;
    }
  }

  /** Load task list from KB with optional filtering. */
  async loadTaskList(filter?: { status?: TaskStatus; assignedTo?: string }): Promise<TaskBundle[]> {
    let tasks = await this.loadFolder<TaskBundle>("tasks");
    if (filter?.status) {
      tasks = tasks.filter((t) => t.status === filter.status);
    }
    if (filter?.assignedTo) {
      tasks = tasks.filter((t) => t.assignedTo === filter.assignedTo);
    }
    return tasks;
  }

  /** Delegate git sync to KnowledgeService. Fire-and-forget. */
  async gitSync(message: string): Promise<void> {
    try {
      await this.kb.gitSync(message);
    } catch {
      this.log(`[persistence] Git sync failed: ${message}`);
    }
  }

  private async writeJson(name: string, data: unknown): Promise<void> {
    try {
      await this.kb.write(name, JSON.stringify(data, null, 2));
    } catch (err) {
      this.log(`[persistence] Failed to write ${name}: ${err instanceof Error ? err.message : err}`);
    }
  }

  private async loadFolder<T>(folder: string): Promise<T[]> {
    const results: T[] = [];
    try {
      const files = await this.kb.list(folder);
      for (const file of files) {
        try {
          const raw = await this.kb.read(file.path);
          results.push(JSON.parse(raw) as T);
        } catch {
          this.log(`[persistence] Skipping malformed file: ${file.path}`);
        }
      }
    } catch {
      // Folder may not exist yet — that's fine
    }
    return results;
  }
}
