/**
 * Task Manager — state machine, stores, and prompt builders for SoT-pattern task orchestration.
 *
 * State is in-memory (Map-based) with optional KB persistence via TaskPersistence.
 * The Orchestrator calls into TaskManager; TaskManager emits events via the injected EventBus.
 */

import { randomUUID } from "node:crypto";
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
    const taskId = `TASK-${shortId()}`;
    const task: TaskBundle = {
      taskId,
      title: params.title,
      phaseId: params.phaseId,
      priority: params.priority,
      status: "drafted",
      assignedTo: params.assignedTo,
      branch: params.branch,
      codeScope: params.codeScope,
      readScope: params.readScope,
      allowedWriteScope: params.allowedWriteScope,
      docsMustUpdate: params.docsMustUpdate ?? [],
      docsMustNotTouch: params.docsMustNotTouch ?? [],
      definitionOfDone: params.definitionOfDone,
      requiredEvidence: params.requiredEvidence ?? [],
      context: params.context,
      handoffToAcceptance: params.handoffToAcceptance,
      reworkCount: 0,
      maxReworks: params.maxReworks ?? 3,
      linkedIssueIds: [],
      reworkHistory: [],
    };

    // Agents First: populate structured assignee from agent config
    const agentCfg = this.agentConfigLookup?.(params.assignedTo);
    task.assignee = {
      agentId: params.assignedTo,
      model: agentCfg?.model,
    };

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

  /** Get task — in-memory first, KB fallback. */
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
    sessions.push(sessionId);
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

export function buildDevPrompt(task: TaskBundle, kbContext?: string): string {
  const lines: string[] = [];

  lines.push(`# Task: ${task.title} [${task.taskId}]`);
  lines.push("");

  // Agents First: structured JSON block for machine-readable task metadata
  const bundleMeta = {
    taskId: task.taskId,
    assignee: task.assignee ?? { agentId: task.assignedTo },
    priority: task.priority,
    branch: task.branch ?? null,
    codeScope: task.codeScope,
    readScope: task.readScope,
    allowedWriteScope: task.allowedWriteScope,
    docsMustUpdate: task.docsMustUpdate,
    docsMustNotTouch: task.docsMustNotTouch,
    definitionOfDone: task.definitionOfDone,
    requiredEvidence: task.requiredEvidence,
    reworkCount: task.reworkCount,
    maxReworks: task.maxReworks,
  };
  lines.push("## Task Bundle (machine-readable)");
  lines.push("```json");
  lines.push(JSON.stringify(bundleMeta, null, 2));
  lines.push("```");
  lines.push("");

  lines.push("## Context");
  lines.push(task.context);
  lines.push("");

  if (kbContext) {
    lines.push("## Project Knowledge Base Context");
    lines.push(kbContext);
    lines.push("");
  }

  lines.push("## Completion Instructions");
  lines.push("When complete, output a JSON receipt as your final message:");
  lines.push("```json");
  lines.push(JSON.stringify(
    { branch: "", summary: "", changedFiles: [], evidence: [], docsUpdated: [], residualRisks: [] },
    null,
    2,
  ));
  lines.push("```");

  return lines.join("\n");
}

export function buildAcceptancePrompt(
  task: TaskBundle,
  acceptance: AcceptanceBundle,
): string {
  const lines: string[] = [];

  lines.push(`# Acceptance Review: ${task.title} [${acceptance.acceptanceId}]`);
  lines.push("");

  // Agents First: structured acceptance metadata
  const acceptanceMeta = {
    acceptanceId: acceptance.acceptanceId,
    linkedTaskId: acceptance.linkedTaskId,
    acceptor: acceptance.acceptor,
    scope: acceptance.scope,
    blindInputPolicy: acceptance.blindInputPolicy,
    definitionOfDone: task.definitionOfDone,
  };
  lines.push("## Acceptance Bundle (machine-readable)");
  lines.push("```json");
  lines.push(JSON.stringify(acceptanceMeta, null, 2));
  lines.push("```");
  lines.push("");

  // Blind review: only expose changedFiles and branch — NOT summary/evidence/residualRisks
  if (task.implementationReceipt) {
    const blindReceipt = {
      branch: task.implementationReceipt.branch,
      changedFiles: task.implementationReceipt.changedFiles,
      docsUpdated: task.implementationReceipt.docsUpdated,
    };
    lines.push("## Implementation (blind — changed files only)");
    lines.push("```json");
    lines.push(JSON.stringify(blindReceipt, null, 2));
    lines.push("```");
    lines.push("");
  }

  lines.push("## Instructions");
  lines.push("BLIND REVIEW: You are FORBIDDEN from referencing the developer's self-assessment,");
  lines.push("evidence descriptions, or risk evaluations. Evaluate ONLY from code, tests, and runtime output.");
  lines.push("");
  lines.push("Review the implementation against the definition of done and scope above.");
  lines.push("Provide your review as JSON:");
  lines.push("```json");
  lines.push(JSON.stringify(
    { verdict: "pass|partial|fail|blocked", findings: [], recommendations: [] },
    null,
    2,
  ));
  lines.push("```");

  return lines.join("\n");
}

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

export function buildMainReviewPrompt(task: TaskBundle): string {
  const lines: string[] = [];

  lines.push(`# Main Agent Review: ${task.title} [${task.taskId}]`);
  lines.push("");

  // Full receipt (Main Agent is the originator — not blind)
  if (task.implementationReceipt) {
    lines.push("## Implementation Receipt");
    lines.push("```json");
    lines.push(JSON.stringify(task.implementationReceipt, null, 2));
    lines.push("```");
    lines.push("");
  }

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
  lines.push("Quick review: Does this implementation satisfy the definition of done?");
  lines.push("Reply with one of:");
  lines.push("- `APPROVE_FOR_ACCEPTANCE` — proceed to blind acceptance review");
  lines.push("- `SEND_BACK` followed by reasons — return to dev agent for rework");

  return lines.join("\n");
}
