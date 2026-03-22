/**
 * Seed Script: Import all KB task/issue/acceptance data into SQLite.
 *
 * Handles field normalization for legacy KB JSON files:
 * - priority: Sev-X / sev-x → P0-P3
 * - assignedTo: object → string (extract agentId)
 * - status: normalize to DB-valid enum values
 * - issue type: normalize to DB-valid enum values
 * - Missing required fields → fill defaults
 *
 * Usage: npx tsx packages/orchestrator/src/scripts/seed-db-from-kb.ts
 */

import * as fs from "node:fs";
import * as path from "node:path";
import Database from "better-sqlite3";

// ─── Config ───

const DB_PATH = path.resolve(".mercury/mercury.db");
const KB_ROOT = path.resolve("D:/Mercury/Mercury_KB");
const TASKS_DIR = path.join(KB_ROOT, "10-tasks");
const ISSUES_DIR = path.join(KB_ROOT, "11-issues");
const ACC_DIR = path.join(KB_ROOT, "12-acceptances");

// ─── Priority Normalization ───

function normalizePriority(raw: string | undefined | null): string {
  if (!raw) return "P2";
  const s = raw.trim().toLowerCase();
  // Direct P0-P3
  if (/^p[0-3]$/i.test(s)) return s.toUpperCase();
  // Sev-X mapping
  const sevMatch = s.match(/sev[- ]?(\d)/i);
  if (sevMatch) {
    const n = parseInt(sevMatch[1], 10);
    if (n >= 0 && n <= 3) return `P${n}`;
  }
  return "P2"; // default
}

// ─── Status Normalization ───

const VALID_TASK_STATUSES = new Set([
  "drafted", "dispatched", "in_progress", "implementation_done",
  "main_review", "acceptance", "verified", "closed", "failed", "blocked",
]);

function normalizeTaskStatus(raw: string | undefined | null): string {
  if (!raw) return "drafted";
  const s = raw.trim().toLowerCase();
  if (VALID_TASK_STATUSES.has(s)) return s;
  // Map common legacy values
  if (s === "pending") return "drafted";
  if (s === "completed" || s === "done" || s === "merged") return "closed";
  if (s === "open" || s === "triaged") return "drafted";
  return "drafted";
}

const VALID_ISSUE_STATUSES = new Set(["open", "resolved", "deferred"]);

function normalizeIssueStatus(raw: string | undefined | null): string {
  if (!raw) return "open";
  const s = raw.trim().toLowerCase();
  if (VALID_ISSUE_STATUSES.has(s)) return s;
  if (s === "triaged" || s === "pending") return "open";
  if (s === "closed" || s === "wontfix" || s === "done") return "resolved";
  return "open";
}

const VALID_ISSUE_TYPES = new Set(["bug", "scope_creep", "blocker", "question"]);

function normalizeIssueType(raw: string | undefined | null): string {
  if (!raw) return "bug";
  const s = raw.trim().toLowerCase();
  if (VALID_ISSUE_TYPES.has(s)) return s;
  if (s.includes("bug")) return "bug";
  if (s.includes("enhancement") || s.includes("improvement") || s.includes("design_gap") || s.includes("process_gap")) return "scope_creep";
  if (s.includes("blocker")) return "blocker";
  if (s.includes("question")) return "question";
  return "bug";
}

// ─── AssignedTo Normalization ───

function normalizeAssignedTo(raw: unknown): string {
  if (!raw) return "";
  if (typeof raw === "string") return raw;
  if (typeof raw === "object" && raw !== null) {
    const obj = raw as Record<string, unknown>;
    if (typeof obj.agentId === "string") return obj.agentId;
    if (typeof obj.id === "string") return obj.id;
  }
  return "";
}

// ─── JSON File Reader ───

function readJsonFiles(dir: string): Array<{ filename: string; data: Record<string, unknown> }> {
  if (!fs.existsSync(dir)) {
    console.warn(`[seed] Directory not found: ${dir}`);
    return [];
  }
  const results: Array<{ filename: string; data: Record<string, unknown> }> = [];
  for (const file of fs.readdirSync(dir)) {
    if (!file.endsWith(".json")) continue;
    try {
      const raw = fs.readFileSync(path.join(dir, file), "utf-8");
      const data = JSON.parse(raw);
      results.push({ filename: file, data });
    } catch (err) {
      console.warn(`[seed] Failed to parse ${file}: ${err}`);
    }
  }
  return results;
}

// ─── Main Seed Logic ───

function main() {
  console.log("[seed] Starting DB seed from KB data...");
  console.log(`[seed] DB path: ${DB_PATH}`);
  console.log(`[seed] KB root: ${KB_ROOT}`);

  // Verify DB exists
  if (!fs.existsSync(DB_PATH)) {
    console.error(`[seed] DB file not found at ${DB_PATH}. Run orchestrator first to create schema.`);
    process.exit(1);
  }

  const db = new Database(DB_PATH);
  db.pragma("journal_mode = WAL");
  db.pragma("foreign_keys = ON");

  // ─── Prepare Statements ───

  const upsertTask = db.prepare(`
    INSERT OR REPLACE INTO tasks (
      task_id, title, status, priority, phase_id,
      created_at, closed_at, failed_at, assigned_to, branch,
      context, dispatch_attempts, max_dispatch_attempts,
      last_dispatch_error, rework_count, max_reworks,
      originator_session_id, raw_json
    ) VALUES (
      @task_id, @title, @status, @priority, @phase_id,
      @created_at, @closed_at, @failed_at, @assigned_to, @branch,
      @context, @dispatch_attempts, @max_dispatch_attempts,
      @last_dispatch_error, @rework_count, @max_reworks,
      @originator_session_id, @raw_json
    )
  `);

  const upsertIssue = db.prepare(`
    INSERT OR REPLACE INTO issues (
      issue_id, title, status, type, priority, raw_json
    ) VALUES (
      @issue_id, @title, @status, @type, @priority, @raw_json
    )
  `);

  const upsertAcceptance = db.prepare(`
    INSERT OR REPLACE INTO acceptances (
      acceptance_id, linked_task_id, status, acceptor, completed_at, raw_json
    ) VALUES (
      @acceptance_id, @linked_task_id, @status, @acceptor, @completed_at, @raw_json
    )
  `);

  // ─── Seed Tasks ───

  const taskFiles = readJsonFiles(TASKS_DIR);
  let taskCount = 0;
  let taskErrors = 0;

  const seedTasks = db.transaction(() => {
    for (const { filename, data } of taskFiles) {
      try {
        const taskId = (data.taskId as string) || filename.replace(".json", "");
        const status = normalizeTaskStatus(data.status as string);
        const priority = normalizePriority(data.priority as string);
        const assignedTo = normalizeAssignedTo(data.assignedTo);

        // Normalize the raw_json with corrected fields
        const normalized = {
          ...data,
          taskId,
          status,
          priority,
          assignedTo,
          closedAt: data.closedAt ?? data.completedAt ?? null,
          failedAt: data.failedAt ?? null,
          context: data.context ?? data.description ?? "",
          codeScope: data.codeScope ?? { include: [], exclude: [] },
          readScope: data.readScope ?? { requiredDocs: [], optionalDocs: [] },
          allowedWriteScope: data.allowedWriteScope ?? { codePaths: [], kbPaths: [] },
          docsMustUpdate: data.docsMustUpdate ?? [],
          docsMustNotTouch: data.docsMustNotTouch ?? [],
          definitionOfDone: data.definitionOfDone ?? [],
          requiredEvidence: data.requiredEvidence ?? [],
          dispatchAttempts: data.dispatchAttempts ?? 0,
          maxDispatchAttempts: data.maxDispatchAttempts ?? 5,
          reworkCount: data.reworkCount ?? 0,
          maxReworks: data.maxReworks ?? 2,
          linkedIssueIds: data.linkedIssueIds ?? [],
        };

        upsertTask.run({
          task_id: taskId,
          title: (data.title as string) || taskId,
          status,
          priority,
          phase_id: (data.phaseId as string) ?? (data.phase as string) ?? null,
          created_at: (data.createdAt as string) ?? null,
          closed_at: normalized.closedAt,
          failed_at: normalized.failedAt,
          assigned_to: assignedTo || null,
          branch: (data.branch as string) ?? null,
          context: typeof normalized.context === "string"
            ? normalized.context
            : JSON.stringify(normalized.context),
          dispatch_attempts: normalized.dispatchAttempts,
          max_dispatch_attempts: normalized.maxDispatchAttempts,
          last_dispatch_error: (data.lastDispatchError as string) ?? null,
          rework_count: normalized.reworkCount,
          max_reworks: normalized.maxReworks,
          originator_session_id: (data.originatorSessionId as string) ?? null,
          raw_json: JSON.stringify(normalized),
        });

        taskCount++;
      } catch (err) {
        console.error(`[seed] Task ${filename}: ${err}`);
        taskErrors++;
      }
    }
  });

  seedTasks();
  console.log(`[seed] Tasks: ${taskCount} imported, ${taskErrors} errors`);

  // ─── Seed Issues ───

  const issueFiles = readJsonFiles(ISSUES_DIR);
  let issueCount = 0;
  let issueErrors = 0;

  const seedIssues = db.transaction(() => {
    for (const { filename, data } of issueFiles) {
      try {
        const issueId = (data.issueId as string) ?? (data.id as string) ?? filename.replace(".json", "");
        const status = normalizeIssueStatus(data.status as string);
        const priority = normalizePriority(
          (data.priority as string) ?? (data.severity as string),
        );
        const type = normalizeIssueType(data.type as string);
        const title = (data.title as string) || issueId;

        const normalized = {
          ...data,
          issueId,
          title,
          status,
          priority,
          type,
        };

        upsertIssue.run({
          issue_id: issueId,
          title,
          status,
          type,
          priority,
          raw_json: JSON.stringify(normalized),
        });

        issueCount++;
      } catch (err) {
        console.error(`[seed] Issue ${filename}: ${err}`);
        issueErrors++;
      }
    }
  });

  seedIssues();
  console.log(`[seed] Issues: ${issueCount} imported, ${issueErrors} errors`);

  // ─── Seed Acceptances ───

  const accFiles = readJsonFiles(ACC_DIR);
  let accCount = 0;
  let accErrors = 0;

  const seedAcceptances = db.transaction(() => {
    for (const { filename, data } of accFiles) {
      try {
        const acceptanceId = (data.acceptanceId as string) || filename.replace(".json", "");
        const linkedTaskId = (data.linkedTaskId as string) || "";
        const status = (data.status as string) || "pending";
        const acceptor = typeof data.acceptor === "string"
          ? data.acceptor
          : typeof data.acceptor === "object" && data.acceptor !== null
            ? (data.acceptor as Record<string, unknown>).agentId as string ?? ""
            : "";

        const completedAt = data.completedAt
          ?? (data.acceptanceReceipt as Record<string, unknown>)?.executedAt
          ?? null;

        const normalized = {
          ...data,
          acceptanceId,
          linkedTaskId,
          status,
          acceptor,
          completedAt,
        };

        upsertAcceptance.run({
          acceptance_id: acceptanceId,
          linked_task_id: linkedTaskId,
          status,
          acceptor: acceptor || null,
          completed_at: completedAt,
          raw_json: JSON.stringify(normalized),
        });

        accCount++;
      } catch (err) {
        console.error(`[seed] Acceptance ${filename}: ${err}`);
        accErrors++;
      }
    }
  });

  seedAcceptances();
  console.log(`[seed] Acceptances: ${accCount} imported, ${accErrors} errors`);

  // ─── Summary ───

  const taskTotal = (db.prepare("SELECT COUNT(*) as c FROM tasks").get() as { c: number }).c;
  const issueTotal = (db.prepare("SELECT COUNT(*) as c FROM issues").get() as { c: number }).c;
  const accTotal = (db.prepare("SELECT COUNT(*) as c FROM acceptances").get() as { c: number }).c;

  console.log("\n[seed] ═══════════════════════════════════");
  console.log(`[seed] DB totals: ${taskTotal} tasks, ${issueTotal} issues, ${accTotal} acceptances`);
  console.log("[seed] ═══════════════════════════════════");

  // ─── Verification: sample Chinese content ───
  const sampleCjk = db.prepare("SELECT task_id, title FROM tasks WHERE title LIKE '%中文%' OR title LIKE '%修复%' OR title LIKE '%流式%' LIMIT 5").all() as Array<{ task_id: string; title: string }>;
  if (sampleCjk.length > 0) {
    console.log("\n[seed] CJK verification (sample):");
    for (const r of sampleCjk) {
      console.log(`  ${r.task_id}: ${r.title}`);
    }
  }

  // Verify priority distribution
  const prioDistrib = db.prepare("SELECT priority, COUNT(*) as c FROM tasks GROUP BY priority ORDER BY priority").all() as Array<{ priority: string; c: number }>;
  console.log("\n[seed] Priority distribution:");
  for (const r of prioDistrib) {
    console.log(`  ${r.priority}: ${r.c} tasks`);
  }

  // Verify status distribution
  const statusDistrib = db.prepare("SELECT status, COUNT(*) as c FROM tasks GROUP BY status ORDER BY c DESC").all() as Array<{ status: string; c: number }>;
  console.log("\n[seed] Status distribution:");
  for (const r of statusDistrib) {
    console.log(`  ${r.status}: ${r.c}`);
  }

  db.close();
  console.log("\n[seed] Done. DB closed.");
}

main();
