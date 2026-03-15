/**
 * Task Manager — state machine, stores, and prompt builders for SoT-pattern task orchestration.
 *
 * All task state is in-memory (Map-based). No persistence — acceptable for prototype.
 * The Orchestrator calls into TaskManager; TaskManager emits events via the injected EventBus.
 */

import { randomUUID } from "node:crypto";
import type { EventBus } from "@mercury/core";
import type {
  TaskBundle,
  TaskStatus,
  ImplementationReceipt,
  AcceptanceBundle,
  AcceptanceVerdict,
  IssueBundle,
  IssueType,
  AgentRole,
} from "@mercury/core";

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

  constructor(private bus: EventBus) {}

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
    this.tasks.set(taskId, task);

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

    return task;
  }

  // ─── Receipt ───

  recordReceipt(taskId: string, receipt: ImplementationReceipt): TaskBundle {
    const task = this.tasks.get(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    task.implementationReceipt = receipt;

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

    return issue;
  }

  // ─── Session ↔ Task Binding ───

  bindSession(taskId: string, sessionId: string): void {
    this.sessionToTask.set(sessionId, taskId);
    const sessions = this.taskToSessions.get(taskId) ?? [];
    sessions.push(sessionId);
    this.taskToSessions.set(taskId, sessions);
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
  lines.push(`Priority: ${task.priority} | Branch: ${task.branch ?? "create new branch"}`);
  lines.push("");

  if (task.readScope.requiredDocs.length > 0) {
    lines.push("## Required Reading");
    for (const doc of task.readScope.requiredDocs) {
      lines.push(`- ${doc}`);
    }
    lines.push("");
  }

  lines.push("## Code Scope");
  lines.push(`Include: ${task.codeScope.include.join(", ") || "(all)"}`);
  if (task.codeScope.exclude.length > 0) {
    lines.push(`Exclude: ${task.codeScope.exclude.join(", ")}`);
  }
  lines.push("");

  if (task.allowedWriteScope.codePaths.length > 0) {
    lines.push("## Allowed Write Paths");
    lines.push(`Code: ${task.allowedWriteScope.codePaths.join(", ")}`);
    if (task.allowedWriteScope.kbPaths.length > 0) {
      lines.push(`KB: ${task.allowedWriteScope.kbPaths.join(", ")}`);
    }
    lines.push("");
  }

  if (task.docsMustNotTouch.length > 0) {
    lines.push("## Do NOT Touch");
    for (const doc of task.docsMustNotTouch) {
      lines.push(`- ${doc}`);
    }
    lines.push("");
  }

  lines.push("## Definition of Done");
  task.definitionOfDone.forEach((item, i) => {
    lines.push(`${i + 1}. ${item}`);
  });
  lines.push("");

  if (task.requiredEvidence.length > 0) {
    lines.push("## Required Evidence");
    for (const ev of task.requiredEvidence) {
      lines.push(`- ${ev}`);
    }
    lines.push("");
  }

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
  lines.push(`Linked Task: ${task.taskId}`);
  lines.push("");

  if (task.implementationReceipt) {
    lines.push("## Implementation Receipt");
    lines.push("```json");
    lines.push(JSON.stringify(task.implementationReceipt, null, 2));
    lines.push("```");
    lines.push("");
  }

  lines.push("## Review Scope");
  if (acceptance.scope.filesToReview.length > 0) {
    lines.push(`Files: ${acceptance.scope.filesToReview.join(", ")}`);
  }
  if (acceptance.scope.docsToCheck.length > 0) {
    lines.push(`Docs: ${acceptance.scope.docsToCheck.join(", ")}`);
  }
  if (acceptance.scope.runtimeChecks.length > 0) {
    lines.push(`Runtime: ${acceptance.scope.runtimeChecks.join(", ")}`);
  }
  lines.push("");

  lines.push("## Definition of Done (verify these)");
  task.definitionOfDone.forEach((item, i) => {
    lines.push(`${i + 1}. ${item}`);
  });
  lines.push("");

  lines.push("## Blind Input Policy");
  lines.push(`Allowed: ${acceptance.blindInputPolicy.allowed.join(", ")}`);
  lines.push(`Forbidden: ${acceptance.blindInputPolicy.forbidden.join(", ")}`);
  lines.push("");

  lines.push("## Instructions");
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

  lines.push(`# Rework Required [${task.taskId}] (attempt ${task.reworkCount}/${task.maxReworks})`);
  lines.push("");

  if (acceptance.results) {
    lines.push("## Acceptance Findings");
    for (const finding of acceptance.results.findings) {
      lines.push(`- ${finding}`);
    }
    lines.push("");

    if (acceptance.results.recommendations.length > 0) {
      lines.push("## Recommendations");
      for (const rec of acceptance.results.recommendations) {
        lines.push(`- ${rec}`);
      }
      lines.push("");
    }
  }

  lines.push("## Instructions");
  lines.push("Address the findings above within your current scope. Do not start from scratch.");
  lines.push("When complete, output an updated JSON receipt.");

  return lines.join("\n");
}
