/**
 * Task Manager — state machine, stores, and prompt builders for SoT-pattern task orchestration.
 *
 * State is in-memory (Map-based) with optional KB persistence via TaskPersistence.
 * The Orchestrator calls into TaskManager; TaskManager emits events via the injected EventBus.
 */

import { randomUUID } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import type { EventBus } from "@mercury/core";
import type {
  TaskBundle,
  TaskStatus,
  TaskAssignee,
  ImplementationReceipt,
  AcceptanceBundle,
  AcceptanceVerdict,
  IssueBundle,
  IssueType,
  AgentRole,
  AgentConfig,
  ReworkHistoryEntry,
  ScopeViolation,
} from "@mercury/core";
import type { TaskPersistence } from "./task-persistence-kb.js";

// ─── State Machine ───

const VALID_TRANSITIONS: Record<TaskStatus, TaskStatus[]> = {
  drafted: ["dispatched"],
  dispatched: ["in_progress"],
  in_progress: ["implementation_done", "failed", "blocked"],
  implementation_done: ["main_review"],
  main_review: ["acceptance", "in_progress"],
  acceptance: ["verified", "in_progress"],
  verified: ["closed"],
  blocked: ["in_progress", "failed"],
  failed: [],
  closed: [],
};

function shortId(): string {
  return randomUUID().slice(0, 8);
}

// ─── Task Creation Params ───

export interface CreateTaskParams {
  title: string;
  phaseId?: string;
  priority: TaskBundle["priority"];
  assignedTo: string;
  branch?: string;
  codeScope: TaskBundle["codeScope"];
  readScope: TaskBundle["readScope"];
  allowedWriteScope: TaskBundle["allowedWriteScope"];
  docsMustUpdate?: string[];
  docsMustNotTouch?: string[];
  definitionOfDone: string[];
  requiredEvidence?: string[];
  context: string;
  reviewConfig?: TaskBundle["reviewConfig"];
  handoffToAcceptance?: TaskBundle["handoffToAcceptance"];
  maxReworks?: number;
}

export interface CreateIssueParams {
  title: string;
  type: IssueType;
  priority: IssueBundle["priority"];
  source: { reporterType: AgentRole; reporterId: string };
  description: { summary: string; details: string; evidence: string[] };
  linkedTaskIds: string[];
}

// ─── TaskManager ───

export class TaskManager {
  private tasks = new Map<string, TaskBundle>();
  private acceptances = new Map<string, AcceptanceBundle>();
  private issues = new Map<string, IssueBundle>();

  // Session ↔ Task bindings
  private sessionToTask = new Map<string, string>();
  private taskToSessions = new Map<string, string[]>();

  private persistence: TaskPersistence | null = null;
  private agentConfigLookup: ((agentId: string) => AgentConfig | undefined) | null = null;

  constructor(private bus: EventBus) {}

  /** Inject persistence layer (optional — KB-backed). */
  setPersistence(persistence: TaskPersistence): void {
    this.persistence = persistence;
  }

  /** Inject agent config lookup for populating assignee.model. */
  setAgentConfigLookup(lookup: (agentId: string) => AgentConfig | undefined): void {
    this.agentConfigLookup = lookup;
  }

  /** Build a TaskAssignee struct from agentId, enriching with model from agent config if available. */
  private buildTaskAssignee(agentId: string): TaskAssignee {
    const model = this.agentConfigLookup?.(agentId)?.model;
    return model === undefined ? { agentId } : { agentId, model };
  }

  /** Rehydrate task state from persistence (call before RPC starts). */
  async init(): Promise<void> {
    if (!this.persistence) return;
    try {
      const { tasks, acceptances, issues } = await this.persistence.loadAll();
      for (const t of tasks) this.tasks.set(t.taskId, t);
      for (const a of acceptances) this.acceptances.set(a.acceptanceId, a);
      for (const i of issues) this.issues.set(i.issueId, i);
    } catch {
      // Persistence failure on init is non-fatal — start with empty state
    }
  }

  /** Fire-and-forget persist — never blocks state machine. */
  private persistTask(task: TaskBundle): void {
    this.persistence?.saveTask(task).catch(() => {});
  }

  private persistAcceptance(acc: AcceptanceBundle): void {
    this.persistence?.saveAcceptance(acc).catch(() => {});
  }

  private persistIssue(issue: IssueBundle): void {
    this.persistence?.saveIssue(issue).catch(() => {});
  }

  // ─── Task CRUD ───

  createTask(params: CreateTaskParams): TaskBundle {
    // ─── Input Validation + Normalization ───
    const errors: string[] = [];
    if (!params.title?.trim()) errors.push("title is required");
    if (!params.assignedTo?.trim()) errors.push("assignedTo is required");
    if (!params.context?.trim()) errors.push("context is required");
    if (!params.definitionOfDone?.length) errors.push("definitionOfDone must have at least 1 item");
    if (!params.codeScope) errors.push("codeScope is required");
    if (!params.readScope) errors.push("readScope is required");
    const validPriorities = ["sev-0", "sev-1", "sev-2", "sev-3"];
    if (!validPriorities.includes(params.priority)) {
      errors.push(`priority must be one of ${validPriorities.join(", ")}, got "${params.priority}"`);
    }
    // Filter empty/whitespace-only entries from write scope paths
    const normCodePaths = (params.allowedWriteScope?.codePaths ?? []).map((p) => p.trim()).filter(Boolean);
    const normKbPaths = (params.allowedWriteScope?.kbPaths ?? []).map((p) => p.trim()).filter(Boolean);
    if (normCodePaths.length === 0 && normKbPaths.length === 0) {
      errors.push("allowedWriteScope must have at least 1 codePath or kbPath");
    }
    if (errors.length > 0) {
      throw new Error(`createTask validation failed:\n  - ${errors.join("\n  - ")}`);
    }

    // Normalize string inputs
    params.title = params.title.trim();
    params.assignedTo = params.assignedTo.trim();
    params.context = params.context.trim();

    const taskId = `TASK-${shortId()}`;
    const task: TaskBundle = {
      taskId,
      title: params.title,
      phaseId: params.phaseId,
      priority: params.priority,
      status: "drafted",
      createdAt: new Date().toISOString(),
      closedAt: null,
      failedAt: null,
      assignedTo: params.assignedTo,
      branch: params.branch,
      codeScope: params.codeScope,
      readScope: params.readScope,
      allowedWriteScope: { codePaths: normCodePaths, kbPaths: normKbPaths },
      docsMustUpdate: params.docsMustUpdate ?? [],
      docsMustNotTouch: params.docsMustNotTouch ?? [],
      definitionOfDone: params.definitionOfDone,
      requiredEvidence: params.requiredEvidence ?? [],
      context: params.context,
      reviewConfig: params.reviewConfig,
      handoffToAcceptance: params.handoffToAcceptance,
      reworkCount: 0,
      maxReworks: params.maxReworks ?? 3,
      linkedIssueIds: [],
      reworkHistory: [],
    };

    // Agents First: populate structured assignee from agent config
    task.assignee = this.buildTaskAssignee(params.assignedTo);

    this.tasks.set(taskId, task);
    this.persistTask(task);

    this.bus.emit(
      "orchestrator.task.created",
      params.assignedTo,
      "orchestrator",
      { taskId, title: task.title, assignedTo: task.assignedTo, priority: task.priority },
    );

    return task;
  }

  /** Get task — in-memory only. Use getTaskAsync() for KB-fresh reads. */
  getTask(taskId: string): TaskBundle | undefined {
    return this.tasks.get(taskId);
  }

  updateTaskField<K extends keyof TaskBundle>(
    taskId: string,
    field: K,
    value: TaskBundle[K],
  ): TaskBundle {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    task[field] = value;
    this.persistTask(task);
    return task;
  }

  /** Async get task — KB first, in-memory fallback. Use when freshness matters (e.g., dashboard). */
  async getTaskAsync(taskId: string): Promise<TaskBundle | undefined> {
    if (this.persistence) {
      try {
        const fromKb = await this.persistence.loadTask(taskId);
        if (fromKb) {
          // Update write-through cache
          this.tasks.set(taskId, fromKb);
          return fromKb;
        }
      } catch {
        // KB read failed — fall through to in-memory
      }
    }
    return this.tasks.get(taskId);
  }

  listTasks(filter?: { status?: TaskStatus; assignedTo?: string }): TaskBundle[] {
    let result = [...this.tasks.values()];
    if (filter?.status) {
      result = result.filter((t) => t.status === filter.status);
    }
    if (filter?.assignedTo) {
      result = result.filter((t) => t.assignedTo === filter.assignedTo);
    }
    return result;
  }

  /** Async list tasks — KB first, in-memory fallback. */
  async listTasksAsync(filter?: { status?: TaskStatus; assignedTo?: string }): Promise<TaskBundle[]> {
    if (this.persistence) {
      try {
        const fromKb = await this.persistence.loadTaskList(filter);
        if (fromKb.length > 0) {
          // Update write-through cache
          for (const t of fromKb) this.tasks.set(t.taskId, t);
          return fromKb;
        }
      } catch {
        // KB read failed — fall through to in-memory
      }
    }
    return this.listTasks(filter);
  }

  // ─── State Machine ───

  /** Git sync triggers on key state transitions. */
  private static readonly GIT_SYNC_STATES: Set<TaskStatus> = new Set([
    "dispatched",
    "implementation_done",
    "main_review",
    "acceptance",
    "verified",
    "closed",
  ]);

  transitionTask(taskId: string, newStatus: TaskStatus, agentId: string): TaskBundle {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    const allowed = VALID_TRANSITIONS[task.status];
    if (!allowed.includes(newStatus)) {
      throw new Error(
        `Invalid transition: ${task.status} → ${newStatus} (allowed: ${allowed.join(", ") || "none"})`,
      );
    }

    const from = task.status;
    task.status = newStatus;
    if (newStatus === "verified" || newStatus === "closed") {
      task.closedAt = new Date().toISOString();
    } else if (newStatus === "failed") {
      task.failedAt = new Date().toISOString();
    }

    this.bus.emit(
      "orchestrator.task.status_change",
      agentId,
      this.getLatestSession(taskId) ?? "orchestrator",
      { taskId, from, to: newStatus },
    );

    // Also fire legacy events for backward compat
    if (newStatus === "closed") {
      this.bus.emit("orchestrator.task.complete", agentId, "orchestrator", { taskId });
    } else if (newStatus === "failed") {
      this.bus.emit("orchestrator.task.fail", agentId, "orchestrator", { taskId });
    }

    this.persistTask(task);

    // Fire-and-forget git sync on key transitions
    if (TaskManager.GIT_SYNC_STATES.has(newStatus)) {
      this.persistence?.gitSync(`[mercury] ${taskId}: ${from} → ${newStatus}`).catch(() => {});
    }

    return task;
  }

  // ─── Receipt ───

  recordReceipt(taskId: string, receipt: ImplementationReceipt): TaskBundle {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    task.implementationReceipt = receipt;

    // Post-hoc scope validation (advisory — does not block state machine)
    const scopeResult = this.validateScope(task);
    if (!scopeResult.valid) {
      receipt.scopeViolations = scopeResult.violations;
      this.bus.emit(
        "orchestrator.scope.violation",
        receipt.implementer,
        this.getLatestSession(taskId) ?? "orchestrator",
        { taskId, violations: scopeResult.violations },
      );
    }

    this.persistTask(task);

    // Auto-transition to implementation_done if currently in_progress
    if (task.status === "in_progress") {
      return this.transitionTask(taskId, "implementation_done", receipt.implementer);
    }

    return task;
  }

  // ─── Scope Validation ───

  validateScope(task: TaskBundle): { valid: boolean; violations: ScopeViolation[] } {
    const violations: ScopeViolation[] = [];
    const receipt = task.implementationReceipt;
    if (!receipt) return { valid: true, violations };

    // Check changedFiles against allowedWriteScope.codePaths
    const codePaths = task.allowedWriteScope.codePaths;
    if (codePaths.length > 0) {
      for (const file of receipt.changedFiles) {
        const allowed = codePaths.some((prefix) => file.startsWith(prefix));
        if (!allowed) {
          violations.push({ file, reason: "Outside allowedWriteScope.codePaths" });
        }
      }
    }

    // Check docsUpdated against allowedWriteScope.kbPaths
    const kbPaths = task.allowedWriteScope.kbPaths ?? [];
    if (kbPaths.length > 0) {
      for (const doc of receipt.docsUpdated) {
        const allowed = kbPaths.some((prefix) => doc.startsWith(prefix));
        if (!allowed) {
          violations.push({ file: doc, reason: "Outside allowedWriteScope.kbPaths" });
        }
      }
    }

    // Check docsUpdated against docsMustNotTouch
    for (const doc of receipt.docsUpdated) {
      if (task.docsMustNotTouch.includes(doc)) {
        violations.push({ file: doc, reason: "Listed in docsMustNotTouch" });
      }
    }

    return { valid: violations.length === 0, violations };
  }

  // ─── Rework ───

  triggerRework(
    taskId: string,
    _feedback: string,
    acceptanceId?: string,
    findings?: string[],
  ): { reworked: boolean; newSession: boolean } {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    const canTriggerRework =
      task.status === "in_progress" ||
      task.status === "main_review" ||
      task.status === "acceptance";
    if (!canTriggerRework) {
      throw new Error(
        `Cannot trigger rework for task in status "${task.status}" (must be in_progress, main_review, or acceptance)`,
      );
    }

    // Push current receipt + acceptance findings into rework history
    if (task.implementationReceipt) {
      task.reworkHistory.push({
        attempt: task.reworkCount + 1,
        receipt: { ...task.implementationReceipt },
        acceptanceId: acceptanceId ?? "",
        findings: findings ?? [],
        timestamp: Date.now(),
      });
    }

    task.reworkCount += 1;
    const needsNewSession = task.reworkCount > task.maxReworks;

    // Transition back to in_progress via state machine
    if (task.status === "acceptance" || task.status === "main_review") {
      this.transitionTask(taskId, "in_progress", task.assignedTo);
    }

    this.bus.emit(
      "orchestrator.task.rework",
      task.assignedTo,
      this.getLatestSession(taskId) ?? "orchestrator",
      {
        taskId,
        reworkCount: task.reworkCount,
        maxReworks: task.maxReworks,
        newSession: needsNewSession,
      },
    );

    this.persistTask(task);
    return { reworked: true, newSession: needsNewSession };
  }

  // ─── Acceptance ───

  createAcceptance(taskId: string, acceptorId: string): AcceptanceBundle {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    const validForAcceptance: TaskStatus[] = ["implementation_done", "main_review", "acceptance"];
    if (!validForAcceptance.includes(task.status)) {
      throw new Error(
        `Cannot create acceptance for task in status "${task.status}" (must be implementation_done, main_review, or acceptance)`,
      );
    }

    const acceptanceId = `ACC-${shortId()}`;
    const acceptance: AcceptanceBundle = {
      acceptanceId,
      linkedTaskId: taskId,
      status: "pending",
      acceptor: acceptorId,
      scope: {
        filesToReview: task.implementationReceipt?.changedFiles ?? [],
        docsToCheck: task.readScope.requiredDocs,
        runtimeChecks: task.requiredEvidence,
      },
      blindInputPolicy: task.handoffToAcceptance?.blindInputPolicy ?? {
        allowed: ["Task Bundle input fields", "Codebase", "Runtime environment"],
        forbidden: ["Dev-agent reasoning outside bundle"],
      },
    };
    this.acceptances.set(acceptanceId, acceptance);
    this.persistAcceptance(acceptance);

    // Link acceptance to task
    if (task.handoffToAcceptance) {
      task.handoffToAcceptance.acceptanceBundleId = acceptanceId;
    } else {
      task.handoffToAcceptance = {
        acceptanceBundleId: acceptanceId,
        blindInputPolicy: acceptance.blindInputPolicy,
        acceptanceFocus: task.definitionOfDone,
      };
    }

    this.bus.emit(
      "orchestrator.acceptance.created",
      acceptorId,
      "orchestrator",
      { acceptanceId, linkedTaskId: taskId, acceptor: acceptorId },
    );

    this.persistTask(task);
    return acceptance;
  }

  getAcceptance(acceptanceId: string): AcceptanceBundle | undefined {
    return this.acceptances.get(acceptanceId);
  }

  recordAcceptanceResult(
    acceptanceId: string,
    results: { verdict: AcceptanceVerdict; findings: string[]; recommendations: string[] },
  ): AcceptanceBundle {
    const acceptance = this.acceptances.get(acceptanceId);
    if (!acceptance) throw new Error(`Acceptance not found: ${acceptanceId}`);

    acceptance.results = results;
    acceptance.status = "completed";
    acceptance.completedAt = Date.now();

    this.bus.emit(
      "orchestrator.acceptance.completed",
      acceptance.acceptor,
      "orchestrator",
      { acceptanceId, verdict: results.verdict, linkedTaskId: acceptance.linkedTaskId },
    );

    this.persistAcceptance(acceptance);
    return acceptance;
  }

  // ─── Issues ───

  createIssue(params: CreateIssueParams): IssueBundle {
    const issueId = `ISS-${shortId()}`;
    const issue: IssueBundle = {
      issueId,
      title: params.title,
      status: "open",
      type: params.type,
      priority: params.priority,
      source: params.source,
      description: params.description,
      linkedTaskIds: params.linkedTaskIds,
    };
    this.issues.set(issueId, issue);
    this.persistIssue(issue);

    // Link issues to tasks
    for (const taskId of params.linkedTaskIds) {
      const task = this.tasks.get(taskId);
      if (task && !task.linkedIssueIds.includes(issueId)) {
        task.linkedIssueIds.push(issueId);
      }
    }

    this.bus.emit(
      "orchestrator.issue.created",
      params.source.reporterId,
      "orchestrator",
      { issueId, title: issue.title, linkedTaskIds: params.linkedTaskIds },
    );

    return issue;
  }

  getIssue(issueId: string): IssueBundle | undefined {
    return this.issues.get(issueId);
  }

  resolveIssue(
    issueId: string,
    resolution: { resolvedBy: string; summary: string; resolvedAt: number },
  ): IssueBundle {
    const issue = this.issues.get(issueId);
    if (!issue) throw new Error(`Issue not found: ${issueId}`);

    issue.status = "resolved";
    issue.resolution = resolution;

    this.bus.emit(
      "orchestrator.issue.resolved",
      resolution.resolvedBy,
      "orchestrator",
      { issueId },
    );

    this.persistIssue(issue);
    return issue;
  }

  // ─── Session ↔ Task Binding ───

  bindSession(taskId: string, sessionId: string): void {
    this.sessionToTask.set(sessionId, taskId);
    const sessions = this.taskToSessions.get(taskId) ?? [];
    if (!sessions.includes(sessionId)) {
      sessions.push(sessionId);
    }
    this.taskToSessions.set(taskId, sessions);

    // Agents First: update assignee.sessionId
    const task = this.tasks.get(taskId);
    if (task?.assignee) {
      task.assignee.sessionId = sessionId;
      this.persistTask(task);
    }
  }

  getTaskForSession(sessionId: string): string | undefined {
    return this.sessionToTask.get(sessionId);
  }

  getSessionsForTask(taskId: string): string[] {
    return this.taskToSessions.get(taskId) ?? [];
  }

  private getLatestSession(taskId: string): string | undefined {
    const sessions = this.taskToSessions.get(taskId);
    return sessions?.[sessions.length - 1];
  }
}

// ─── Prompt Builders ───

/** Format allowedWriteScope for human-readable display (codePaths + kbPaths). */
function formatWriteScope(scope: TaskBundle["allowedWriteScope"]): string {
  const parts: string[] = [];
  if (scope.codePaths.length) parts.push(`code: ${scope.codePaths.join(", ")}`);
  if (scope.kbPaths?.length) parts.push(`kb: ${scope.kbPaths.join(", ")}`);
  return parts.length > 0 ? parts.join(" | ") : "无限制";
}

/**
 * Build the dispatch prompt sent to the Dev Agent for implementation.
 * NOTE: Template is loaded synchronously per call. Future optimization:
 * preload + cache at TaskManager.init() with mtime-based refresh (tracked in TASK-WF-001).
 */
export function buildDevPrompt(
  task: TaskBundle,
  kbContext?: string,
  basePath = process.cwd(),
): string {
  const templatePath = resolve(basePath, ".mercury", "templates", "dispatch-prompt.template.md");
  let template: string;
  try {
    template = readFileSync(templatePath, "utf-8");
  } catch (err) {
    // Fallback: inline template if file not found — log warning for debugging
    console.warn(
      `[TaskManager] dispatch template not found at ${templatePath}: ${err instanceof Error ? err.message : String(err)}. Using fallback.`,
    );
    template = fallbackDevTemplate();
  }

  // Lightweight dispatch meta: only execution-relevant fields.
  // Empty arrays are omitted to save ~50 tokens per dispatch (DEC-2).
  const bundleMeta: Record<string, unknown> = {
    taskId: task.taskId,
    assignee: task.assignee ?? { agentId: task.assignedTo },
    priority: task.priority,
    branch: task.branch ?? null,
    codeScope: task.codeScope,
    readScope: task.readScope,
    allowedWriteScope: task.allowedWriteScope,
    definitionOfDone: task.definitionOfDone,
    reworkCount: task.reworkCount,
    maxReworks: task.maxReworks,
  };
  if (task.docsMustUpdate.length > 0) bundleMeta.docsMustUpdate = task.docsMustUpdate;
  if (task.docsMustNotTouch.length > 0) bundleMeta.docsMustNotTouch = task.docsMustNotTouch;
  if (task.requiredEvidence.length > 0) bundleMeta.requiredEvidence = task.requiredEvidence;

  const receiptTemplate = JSON.stringify(
    { implementer: "", branch: "", summary: "", changedFiles: [], evidence: [], docsUpdated: [], residualRisks: [], completedAt: "" },
    null,
    2,
  );

  const scopeDisplay = formatWriteScope(task.allowedWriteScope);

  // Single-pass template substitution to prevent cross-replacement
  const placeholders: Record<string, string> = {
    "{{taskId}}": `${task.title} [${task.taskId}]`,
    "{{context}}": task.context,
    "{{taskFilePath}}": `{Project}_KB/10-tasks/${task.taskId}.json`,
    "{{allowedWriteScope}}": scopeDisplay,
    "{{docsMustNotTouch}}": task.docsMustNotTouch.join(", ") || "无",
    "{{bundleJson}}": JSON.stringify(bundleMeta, null, 2),
    "{{receiptTemplate}}": receiptTemplate,
  };
  const placeholderPattern = new RegExp(
    Object.keys(placeholders).map((k) => k.replace(/[{}]/g, "\\$&")).join("|"),
    "g",
  );
  let result = template.replace(placeholderPattern, (match) => placeholders[match] ?? match);

  if (kbContext) {
    result += `\n\n## Project Knowledge Base Context\n${kbContext}`;
  }

  return result;
}

function fallbackDevTemplate(): string {
  return [
    "# Dispatch: {{taskId}}",
    "",
    "{{context}}",
    "",
    "## Task Bundle (machine-readable)",
    "```json",
    "{{bundleJson}}",
    "```",
    "",
    "## Completion Instructions",
    "When complete, output a JSON receipt as your final message:",
    "```json",
    "{{receiptTemplate}}",
    "```",
  ].join("\n");
}

/**
 * Build a lightweight dispatch prompt using file references instead of embedded data.
 * Used for manual handoff or when the sub-agent can read local files directly.
 *
 * Follows Mercury's prompt+reference protocol:
 *   - Natural language intent + action
 *   - Pointers to structured files (TaskBundle, Handoff)
 *   - Return format specification
 */
export function buildReferencePrompt(
  task: TaskBundle,
  taskFilePath: string,
  handoffFilePath?: string,
): string {
  const lines: string[] = [];

  lines.push(`实现任务 ${task.taskId}: ${task.title}`);
  lines.push("");
  lines.push(`任务详情: 读取 ${taskFilePath}`);
  if (handoffFilePath) {
    lines.push(`项目上下文: 读取 ${handoffFilePath}`);
  }
  lines.push("");
  lines.push("完成后:");
  lines.push(`1. 将 implementation receipt 写入 ${taskFilePath} 的 implementationReceipt 字段`);
  lines.push('2. 返回一句话总结 + receipt 文件路径');
  lines.push("");
  lines.push(`允许修改的文件: ${formatWriteScope(task.allowedWriteScope)}`);
  lines.push(`禁止修改: ${task.docsMustNotTouch.join(", ") || "无"}`);

  return lines.join("\n");
}

/** Build the blind-review prompt sent to the Acceptance Agent. */
export function buildAcceptancePrompt(
  task: TaskBundle,
  acceptance: AcceptanceBundle,
  basePath = process.cwd(),
): string {
  const templatePath = resolve(basePath, ".mercury", "templates", "acceptance-prompt.template.md");
  let template: string;
  try {
    template = readFileSync(templatePath, "utf-8");
  } catch (err) {
    console.warn(
      `[TaskManager] acceptance template not found at ${templatePath}: ${err instanceof Error ? err.message : String(err)}. Using fallback.`,
    );
    template = fallbackAcceptanceTemplate();
  }

  const acceptanceMeta = {
    acceptanceId: acceptance.acceptanceId,
    linkedTaskId: acceptance.linkedTaskId,
    acceptor: acceptance.acceptor,
    scope: acceptance.scope,
    definitionOfDone: task.definitionOfDone,
  };

  const blindReceipt = task.implementationReceipt
    ? {
        branch: task.implementationReceipt.branch,
        changedFiles: task.implementationReceipt.changedFiles,
        docsUpdated: task.implementationReceipt.docsUpdated,
      }
    : { branch: "", changedFiles: [], docsUpdated: [] };

  const verdictTemplate = JSON.stringify(
    { verdict: "pass|partial|fail|blocked", findings: [], recommendations: [] },
    null,
    2,
  );

  // Single-pass template substitution
  const accPlaceholders: Record<string, string> = {
    "{{taskTitle}}": task.title,
    "{{acceptanceId}}": acceptance.acceptanceId,
    "{{acceptanceJson}}": JSON.stringify(acceptanceMeta, null, 2),
    "{{blindReceiptJson}}": JSON.stringify(blindReceipt, null, 2),
    "{{verdictTemplate}}": verdictTemplate,
  };
  const accPattern = new RegExp(
    Object.keys(accPlaceholders).map((k) => k.replace(/[{}]/g, "\\$&")).join("|"),
    "g",
  );
  return template.replace(accPattern, (match) => accPlaceholders[match] ?? match);
}

function fallbackAcceptanceTemplate(): string {
  return [
    "# Acceptance Review: {{taskTitle}} [{{acceptanceId}}]",
    "",
    "## Acceptance Bundle (machine-readable)",
    "```json",
    "{{acceptanceJson}}",
    "```",
    "",
    "## Implementation (blind — changed files only)",
    "```json",
    "{{blindReceiptJson}}",
    "```",
    "",
    "## Instructions",
    "BLIND REVIEW: Evaluate ONLY from code, tests, and runtime output.",
    "Provide your review as JSON:",
    "```json",
    "{{verdictTemplate}}",
    "```",
  ].join("\n");
}

/** Build the rework prompt sent to the Dev Agent after acceptance failure. */
export function buildReworkPrompt(
  task: TaskBundle,
  acceptance: AcceptanceBundle,
): string {
  const lines: string[] = [];

  lines.push(`# Rework Required [${task.taskId}]`);
  lines.push(`This is attempt **${task.reworkCount}/${task.maxReworks}**.`);
  lines.push("");

  // Current failure context
  const reworkMeta = {
    taskId: task.taskId,
    assignee: task.assignee ?? { agentId: task.assignedTo },
    reworkCount: task.reworkCount,
    maxReworks: task.maxReworks,
    acceptanceId: acceptance.acceptanceId,
    verdict: acceptance.results?.verdict,
    findings: acceptance.results?.findings ?? [],
    recommendations: acceptance.results?.recommendations ?? [],
  };
  lines.push("## Current Failure (machine-readable)");
  lines.push("```json");
  lines.push(JSON.stringify(reworkMeta, null, 2));
  lines.push("```");
  lines.push("");

  // Previous receipt (what was rejected)
  if (task.implementationReceipt) {
    lines.push("## Rejected Implementation Receipt");
    lines.push("```json");
    lines.push(JSON.stringify(task.implementationReceipt, null, 2));
    lines.push("```");
    lines.push("");
  }

  // Full rework history for iterative learning
  if (task.reworkHistory.length > 0) {
    lines.push("## Rework History");
    for (const entry of task.reworkHistory) {
      lines.push(`### Attempt ${entry.attempt}`);
      lines.push(`- Acceptance: ${entry.acceptanceId}`);
      lines.push(`- Findings: ${entry.findings.join("; ") || "none"}`);
      lines.push(`- Timestamp: ${new Date(entry.timestamp).toISOString()}`);
    }
    lines.push("");
  }

  lines.push("## Instructions");
  lines.push("Address the findings above within your current scope. Do not start from scratch.");
  lines.push("Learn from previous attempts listed in the rework history — do not repeat the same mistakes.");
  lines.push("When complete, output an updated JSON receipt.");

  return lines.join("\n");
}

// ─── Main Review Prompt (Phase 4: two-stage verification) ───

const MAX_DIFF_CHARS = 30_000;
const MAX_PRECHECKS_CHARS = 10_000;

/** Escape triple-backtick sequences to prevent Markdown fence breakout */
function sanitizeFenceContent(content: string): string {
  return content.replace(/```/g, "` ` `");
}

/** Truncate content with a marker if it exceeds maxLen */
function truncate(content: string, maxLen: number): string {
  if (content.length <= maxLen) return content;
  return content.slice(0, maxLen) + "\n...[truncated]";
}

/** Build the Main Agent review prompt with sanitized pre-checks and diff. */
export function buildMainReviewPrompt(task: TaskBundle): string {
  const lines: string[] = [];

  lines.push(`# Main Agent Review: ${task.title} [${task.taskId}]`);
  lines.push("");

  // Full receipt (Main Agent is the originator — not blind)
  if (task.implementationReceipt) {
    const receiptJson = sanitizeFenceContent(
      JSON.stringify(task.implementationReceipt, null, 2)
    );
    lines.push("## Implementation Receipt");
    lines.push("```json");
    lines.push(receiptJson);
    lines.push("```");
    lines.push("");
  }

  const preChecksRaw = JSON.stringify(task.mainReview?.preChecks ?? [], null, 2);
  const preChecksSafe = truncate(sanitizeFenceContent(preChecksRaw), MAX_PRECHECKS_CHARS);
  lines.push("## Pre-check Results");
  lines.push("```json");
  lines.push(preChecksSafe);
  lines.push("```");
  lines.push("");

  const diffRaw = task.mainReview?.gitDiff?.trim() || "# No diff captured";
  const diffSafe = truncate(sanitizeFenceContent(diffRaw), MAX_DIFF_CHARS);
  lines.push("## Git Diff (`develop...HEAD`)");
  lines.push("```diff");
  lines.push(diffSafe);
  lines.push("```");
  lines.push("");

  // Scope violations (if any)
  if (task.implementationReceipt?.scopeViolations?.length) {
    lines.push("## ⚠ Scope Violations Detected");
    for (const v of task.implementationReceipt.scopeViolations) {
      lines.push(`- **${v.file}**: ${v.reason}`);
    }
    lines.push("");
  }

  // Definition of done checklist
  lines.push("## Definition of Done");
  for (const item of task.definitionOfDone) {
    lines.push(`- [ ] ${item}`);
  }
  lines.push("");

  lines.push("## Instructions");
  lines.push("Quick review: verify the implementation receipt, pre-check results, git diff, and definition of done.");
  lines.push("Return JSON in this format:");
  lines.push("```json");
  lines.push(JSON.stringify({
    decision: "APPROVE_FOR_ACCEPTANCE|SEND_BACK",
    summary: "short summary",
    reason: "required when sending back or when findings exist",
    findings: [
      {
        severity: "critical|major|minor|info",
        title: "issue title",
        detail: "what is wrong or what was verified",
        file: "optional/path.ts",
        line: 123,
      },
    ],
  }, null, 2));
  lines.push("```");
  lines.push("Any `critical` finding must result in `SEND_BACK`.");
  lines.push("Legacy fallback is still accepted: `APPROVE_FOR_ACCEPTANCE` or `SEND_BACK: <reason>`.");

  return lines.join("\n");
}
