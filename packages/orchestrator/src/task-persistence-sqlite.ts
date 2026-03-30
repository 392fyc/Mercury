/**
 * Task Persistence — SQLite storage via better-sqlite3.
 *
 * Implements the same TaskPersistence interface as the KB variant.
 * SQLite is the primary data store; reads are indexed, writes are synchronous.
 *
 * Schema enforces CHECK constraints on status/priority/type enums.
 * Each entity also stores a raw_json column as a complete JSON backup for
 * forward-compatible fields not yet modelled as columns.
 */

import Database from "better-sqlite3";
import { normalizePriority } from "@mercury/core";
import type {
  TaskBundle,
  AcceptanceBundle,
  IssueBundle,
  TaskStatus,
  TaskPriority,
} from "@mercury/core";
import type { TaskPersistence } from "./task-persistence-kb.js";

type Log = (msg: string) => void;

// ─── Schema & Migrations ───

const MIGRATIONS = [
  {
    version: 1,
    description: "initial_schema",
    sql: `
      CREATE TABLE IF NOT EXISTS tasks (
        task_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('drafted','dispatched','in_progress','implementation_done','main_review','acceptance','verified','closed','failed','blocked')),
        priority TEXT NOT NULL CHECK(priority IN ('P0','P1','P2','P3')),
        phase_id TEXT,
        created_at TEXT,
        closed_at TEXT,
        failed_at TEXT,
        assigned_to TEXT,
        branch TEXT,
        context TEXT,
        dispatch_attempts INTEGER DEFAULT 0,
        max_dispatch_attempts INTEGER DEFAULT 5,
        last_dispatch_error TEXT,
        rework_count INTEGER DEFAULT 0,
        max_reworks INTEGER DEFAULT 2,
        originator_session_id TEXT,
        raw_json TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS issues (
        issue_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('open','resolved','deferred')),
        type TEXT NOT NULL CHECK(type IN ('bug','scope_creep','blocker','question')),
        priority TEXT NOT NULL CHECK(priority IN ('P0','P1','P2','P3')),
        raw_json TEXT NOT NULL
      );

      CREATE TABLE IF NOT EXISTS acceptances (
        acceptance_id TEXT PRIMARY KEY,
        linked_task_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('pending','in_progress','completed')),
        acceptor TEXT,
        completed_at INTEGER,
        raw_json TEXT NOT NULL
      );

      CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
      CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
      CREATE INDEX IF NOT EXISTS idx_tasks_assigned ON tasks(assigned_to);
      CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);

      CREATE TABLE IF NOT EXISTS _migrations (
        version INTEGER PRIMARY KEY,
        description TEXT,
        applied_at TEXT DEFAULT (datetime('now'))
      );
    `,
  },
  {
    version: 2,
    description: "add_task_role_column",
    sql: `ALTER TABLE tasks ADD COLUMN role TEXT DEFAULT 'dev';`,
  },
  {
    version: 3,
    description: "add_callback_queue_table",
    sql: `
      CREATE TABLE IF NOT EXISTS callback_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        idempotency_key TEXT NOT NULL UNIQUE,
        task_id TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK(verdict IN ('pass','partial','fail','blocked')),
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','delivered','failed')),
        created_at INTEGER NOT NULL,
        delivered_at INTEGER,
        delivery_attempts INTEGER NOT NULL DEFAULT 0,
        last_error TEXT
      );
      CREATE INDEX IF NOT EXISTS idx_cbq_status ON callback_queue(status);
      CREATE INDEX IF NOT EXISTS idx_cbq_task ON callback_queue(task_id);
      CREATE INDEX IF NOT EXISTS idx_cbq_created ON callback_queue(created_at);
    `,
  },
];

function applyMigrations(db: Database.Database, log: Log): void {
  db.exec("CREATE TABLE IF NOT EXISTS _migrations (version INTEGER PRIMARY KEY, description TEXT, applied_at TEXT DEFAULT (datetime('now')))");
  const applied = new Set(
    db.prepare("SELECT version FROM _migrations").all().map((r: unknown) => (r as { version: number }).version),
  );

  for (const m of MIGRATIONS) {
    if (applied.has(m.version)) continue;
    log(`[sqlite] Applying migration v${m.version}: ${m.description}`);
    db.exec(m.sql);
    db.prepare("INSERT INTO _migrations (version, description) VALUES (?, ?)").run(m.version, m.description);
  }
}

// ─── Persistence Implementation ───

/** SQLite-backed task persistence using better-sqlite3 with indexed columns and CHECK constraints. */
export class TaskPersistenceSqlite implements TaskPersistence {
  private db: Database.Database;
  private log: Log;

  // Prepared statements (lazy-init after DB open)
  private stmts!: {
    upsertTask: Database.Statement;
    upsertIssue: Database.Statement;
    upsertAcceptance: Database.Statement;
    selectAllTasks: Database.Statement;
    selectAllIssues: Database.Statement;
    selectAllAcceptances: Database.Statement;
    selectTask: Database.Statement;
    selectTasksByStatus: Database.Statement;
    selectTasksByAssigned: Database.Statement;
    selectTasksByBoth: Database.Statement;
    selectAllTasksList: Database.Statement;
  };

  constructor(dbPath: string, log: Log = () => {}) {
    this.log = log;
    this.db = new Database(dbPath);
    // WAL mode for better concurrent read performance
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("foreign_keys = ON");

    applyMigrations(this.db, log);
    this.prepareStatements();
    log(`[sqlite] Database opened: ${dbPath}`);
  }

  private prepareStatements(): void {
    this.stmts = {
      upsertTask: this.db.prepare(`
        INSERT OR REPLACE INTO tasks (
          task_id, title, status, priority, phase_id,
          created_at, closed_at, failed_at, assigned_to, branch,
          context, dispatch_attempts, max_dispatch_attempts,
          last_dispatch_error, rework_count, max_reworks,
          originator_session_id, role, raw_json
        ) VALUES (
          @task_id, @title, @status, @priority, @phase_id,
          @created_at, @closed_at, @failed_at, @assigned_to, @branch,
          @context, @dispatch_attempts, @max_dispatch_attempts,
          @last_dispatch_error, @rework_count, @max_reworks,
          @originator_session_id, @role, @raw_json
        )
      `),

      upsertIssue: this.db.prepare(`
        INSERT OR REPLACE INTO issues (
          issue_id, title, status, type, priority, raw_json
        ) VALUES (
          @issue_id, @title, @status, @type, @priority, @raw_json
        )
      `),

      upsertAcceptance: this.db.prepare(`
        INSERT OR REPLACE INTO acceptances (
          acceptance_id, linked_task_id, status, acceptor, completed_at, raw_json
        ) VALUES (
          @acceptance_id, @linked_task_id, @status, @acceptor, @completed_at, @raw_json
        )
      `),

      selectAllTasks: this.db.prepare("SELECT raw_json, role FROM tasks"),
      selectAllIssues: this.db.prepare("SELECT raw_json FROM issues"),
      selectAllAcceptances: this.db.prepare("SELECT raw_json FROM acceptances"),
      selectTask: this.db.prepare("SELECT raw_json, role FROM tasks WHERE task_id = ?"),
      selectTasksByStatus: this.db.prepare("SELECT raw_json, role FROM tasks WHERE status = ?"),
      selectTasksByAssigned: this.db.prepare("SELECT raw_json, role FROM tasks WHERE assigned_to = ?"),
      selectTasksByBoth: this.db.prepare("SELECT raw_json, role FROM tasks WHERE status = ? AND assigned_to = ?"),
      selectAllTasksList: this.db.prepare("SELECT raw_json, role FROM tasks"),
    };
  }

  // ─── TaskPersistence interface ───

  /** Persist a task bundle to SQLite — INSERT OR REPLACE with indexed columns + raw_json backup. */
  async saveTask(task: TaskBundle): Promise<void> {
    try {
      this.saveTaskSync(task);
    } catch (err) {
      this.log(`[sqlite] Failed to save task ${task.taskId}: ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }

  /** Persist an acceptance bundle to SQLite. */
  async saveAcceptance(acc: AcceptanceBundle): Promise<void> {
    try {
      this.saveAcceptanceSync(acc);
    } catch (err) {
      this.log(`[sqlite] Failed to save acceptance ${acc.acceptanceId}: ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }

  /** Persist an issue bundle to SQLite. */
  async saveIssue(issue: IssueBundle): Promise<void> {
    try {
      this.saveIssueSync(issue);
    } catch (err) {
      this.log(`[sqlite] Failed to save issue ${issue.issueId}: ${err instanceof Error ? err.message : err}`);
      throw err;
    }
  }

  // ─── Sync helpers (used by importFromKB transaction) ───

  private saveTaskSync(task: TaskBundle): void {
    const priority = normalizePriority(task.priority);
    this.stmts.upsertTask.run({
      task_id: task.taskId,
      title: task.title,
      status: task.status,
      priority,
      phase_id: task.phaseId ?? null,
      created_at: task.createdAt ?? null,
      closed_at: task.closedAt ?? null,
      failed_at: task.failedAt ?? null,
      assigned_to: task.assignedTo ?? null,
      branch: task.branch ?? null,
      context: task.context ?? null,
      dispatch_attempts: task.dispatchAttempts ?? 0,
      max_dispatch_attempts: task.maxDispatchAttempts ?? 5,
      last_dispatch_error: task.lastDispatchError ?? null,
      rework_count: task.reworkCount ?? 0,
      max_reworks: task.maxReworks ?? 2,
      originator_session_id: task.originatorSessionId ?? null,
      role: task.role ?? "dev",
      raw_json: JSON.stringify({ ...task, priority, role: task.role ?? "dev" }),
    });
  }

  private saveAcceptanceSync(acc: AcceptanceBundle): void {
    this.stmts.upsertAcceptance.run({
      acceptance_id: acc.acceptanceId,
      linked_task_id: acc.linkedTaskId,
      status: acc.status,
      acceptor: acc.acceptor ?? null,
      completed_at: acc.completedAt ?? null,
      raw_json: JSON.stringify(acc),
    });
  }

  private saveIssueSync(issue: IssueBundle): void {
    const priority = normalizePriority(issue.priority);
    this.stmts.upsertIssue.run({
      issue_id: issue.issueId,
      title: issue.title,
      status: issue.status,
      type: issue.type,
      priority,
      raw_json: JSON.stringify({ ...issue, priority }),
    });
  }

  /** Parse a task row, backfilling role from the column if missing in raw_json. */
  private parseTaskRow(row: unknown): TaskBundle | null {
    const { raw_json, role } = row as { raw_json: string; role?: string };
    const task = this.parseJson<TaskBundle>(raw_json);
    if (task && !task.role) {
      task.role = (role as TaskBundle["role"]) ?? "dev";
    }
    return task;
  }

  /** Load all tasks, acceptances, and issues from SQLite. */
  async loadAll(): Promise<{
    tasks: TaskBundle[];
    acceptances: AcceptanceBundle[];
    issues: IssueBundle[];
  }> {
    const tasks = this.stmts.selectAllTasks.all()
      .map((r: unknown) => this.parseTaskRow(r))
      .filter((t): t is TaskBundle => t !== null);

    const acceptances = this.stmts.selectAllAcceptances.all()
      .map((r: unknown) => this.parseJson<AcceptanceBundle>((r as { raw_json: string }).raw_json))
      .filter((a): a is AcceptanceBundle => a !== null);

    const issues = this.stmts.selectAllIssues.all()
      .map((r: unknown) => this.parseJson<IssueBundle>((r as { raw_json: string }).raw_json))
      .filter((i): i is IssueBundle => i !== null);

    this.log(`[sqlite] Loaded: ${tasks.length} tasks, ${acceptances.length} acceptances, ${issues.length} issues`);
    return { tasks, acceptances, issues };
  }

  /** Load a single task by ID from SQLite. */
  async loadTask(taskId: string): Promise<TaskBundle | null> {
    const row = this.stmts.selectTask.get(taskId) as { raw_json: string; role?: string } | undefined;
    if (!row) return null;
    return this.parseTaskRow(row);
  }

  /** Load tasks with optional status/assignedTo filter — uses indexed queries. */
  async loadTaskList(filter?: { status?: TaskStatus; assignedTo?: string }): Promise<TaskBundle[]> {
    let rows: unknown[];
    if (filter?.status && filter?.assignedTo) {
      rows = this.stmts.selectTasksByBoth.all(filter.status, filter.assignedTo);
    } else if (filter?.status) {
      rows = this.stmts.selectTasksByStatus.all(filter.status);
    } else if (filter?.assignedTo) {
      rows = this.stmts.selectTasksByAssigned.all(filter.assignedTo);
    } else {
      rows = this.stmts.selectAllTasksList.all();
    }
    return rows
      .map((r: unknown) => this.parseTaskRow(r))
      .filter((t): t is TaskBundle => t !== null);
  }

  /** SQLite does not need git sync — no-op. */
  async gitSync(_message: string): Promise<void> {
    // no-op: DB changes are persisted immediately
  }

  // ─── Migration helper: bulk import from KB data ───

  /** Bulk import all entities from KB data in a single transaction. */
  importFromKB(data: {
    tasks: TaskBundle[];
    acceptances: AcceptanceBundle[];
    issues: IssueBundle[];
  }): void {
    // Use sync helpers directly — better-sqlite3 transactions are synchronous,
    // so we must avoid the async TaskPersistence interface methods here.
    const importAll = this.db.transaction(() => {
      for (const task of data.tasks) {
        this.saveTaskSync(task);
      }
      for (const acc of data.acceptances) {
        this.saveAcceptanceSync(acc);
      }
      for (const issue of data.issues) {
        this.saveIssueSync(issue);
      }
    });

    importAll();
    this.log(`[sqlite] Imported from KB: ${data.tasks.length} tasks, ${data.acceptances.length} acceptances, ${data.issues.length} issues`);
  }

  /** Check if DB has any data across all entity tables (used to detect first-run migration need). */
  isEmpty(): boolean {
    const row = this.db.prepare(
      "SELECT (SELECT COUNT(*) FROM tasks) + (SELECT COUNT(*) FROM issues) + (SELECT COUNT(*) FROM acceptances) as total"
    ).get() as { total: number };
    return row.total === 0;
  }

  /** Expose underlying database for shared-table modules (e.g. CallbackQueue). */
  getDatabase(): Database.Database {
    return this.db;
  }

  /** Close the database connection. */
  close(): void {
    this.db.close();
  }

  // ─── Internal ───

  private parseJson<T>(raw: string): T | null {
    try {
      return JSON.parse(raw) as T;
    } catch {
      this.log(`[sqlite] Failed to parse JSON record`);
      return null;
    }
  }
}
