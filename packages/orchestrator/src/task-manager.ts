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
} from "@mercury/core";
import type { TaskPersistence } from "./task-persistence-kb.js";

// ─── State Machine ───

const VALID_TRANSITIONS: Record<TaskStatus, TaskStatus[]> = {
  drafted: ["dispatched"],
  dispatched: ["in_progress"],
  in_progress: ["implementation_done", "failed", "blocked"],
  implementation_done: ["acceptance", "verified", "in_progress"],
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

  getTask(taskId: string): TaskBundle | undefined {
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

  // ─── State Machine ───

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
    return task;
  }

  // ─── Receipt ───

  recordReceipt(taskId: string, receipt: ImplementationReceipt): TaskBundle {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    task.implementationReceipt = receipt;
    this.persistTask(task);

    // Auto-transition to implementation_done if currently in_progress
    if (task.status === "in_progress") {
      return this.transitionTask(taskId, "implementation_done", receipt.implementer);
    }

    return task;
  }

  // ─── Rework ───

  triggerRework(
    taskId: string,
    _feedback: string,
  ): { reworked: boolean; newSession: boolean } {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    task.reworkCount += 1;
    const needsNewSession = task.reworkCount > task.maxReworks;

    // Transition back to in_progress via state machine
    if (task.status === "acceptance" || task.status === "implementation_done") {
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

    const validForAcceptance: TaskStatus[] = ["implementation_done", "acceptance"];
    if (!validForAcceptance.includes(task.status)) {
      throw new Error(
        `Cannot create acceptance for task in status "${task.status}" (must be implementation_done or acceptance)`,
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

  if (task.implementationReceipt) {
    lines.push("## Implementation Receipt");
    lines.push("```json");
    lines.push(JSON.stringify(task.implementationReceipt, null, 2));
    lines.push("```");
    lines.push("");
  }

  lines.push("## Instructions");
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
  lines.push("");

  // Agents First: structured rework metadata
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
  lines.push("## Rework Bundle (machine-readable)");
  lines.push("```json");
  lines.push(JSON.stringify(reworkMeta, null, 2));
  lines.push("```");
  lines.push("");

  lines.push("## Instructions");
  lines.push("Address the findings above within your current scope. Do not start from scratch.");
  lines.push("When complete, output an updated JSON receipt.");

  return lines.join("\n");
}
