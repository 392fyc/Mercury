/**
 * Task Manager — state machine, stores, and prompt builders for SoT-pattern task orchestration.
 *
 * State is in-memory (Map-based) with optional KB persistence via TaskPersistence.
 * The Orchestrator calls into TaskManager; TaskManager emits events via the injected EventBus.
 */

import { randomUUID } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { basename, resolve, sep } from "node:path";
import type { EventBus } from "@mercury/core";
import { normalizePriority } from "@mercury/core";
import type { SkillInjection } from "./skill-registry.js";
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
  in_progress: ["implementation_done", "failed", "blocked", "dispatched"], // dispatched: G2 crash recovery re-dispatch
  implementation_done: ["main_review"],
  main_review: ["acceptance", "in_progress", "failed"],
  acceptance: ["verified", "in_progress", "failed"],
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
  assignedTo?: string; // Optional: auto-assigned via G9 modelRecommendation if omitted
  role?: string; // Task dispatch role: dev, research, design (default: dev)
  researchScope?: "deep" | "quick"; // Research depth mode: 'deep' activates the deep-research skill protocol
  branch?: string;
  codeScope?: TaskBundle["codeScope"];
  readScope: TaskBundle["readScope"];
  allowedWriteScope: TaskBundle["allowedWriteScope"];
  docsMustUpdate?: string[];
  docsMustNotTouch?: string[];
  definitionOfDone: string[];
  requiredEvidence?: string[];
  context: string;
  reviewConfig?: TaskBundle["reviewConfig"];
  handoffToAcceptance?: TaskBundle["handoffToAcceptance"];
  modelRecommendation?: TaskBundle["modelRecommendation"];
  maxReworks?: number;
  maxDispatchAttempts?: number;
  resourceBudget?: TaskBundle["resourceBudget"];
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
  private agentListLookup: (() => AgentConfig[]) | null = null;

  constructor(private bus: EventBus) {}

  /** Inject persistence layer (optional — KB-backed). */
  setPersistence(persistence: TaskPersistence): void {
    this.persistence = persistence;
  }

  /** Inject agent config lookup for populating assignee.model. */
  setAgentConfigLookup(lookup: (agentId: string) => AgentConfig | undefined): void {
    this.agentConfigLookup = lookup;
  }

  /** Inject agent list lookup for G9 auto-triage agent selection. */
  setAgentListLookup(lookup: () => AgentConfig[]): void {
    this.agentListLookup = lookup;
  }

  /** Build a TaskAssignee struct from agentId, enriching with model from agent config if available. */
  private buildTaskAssignee(agentId: string): TaskAssignee {
    const model = this.agentConfigLookup?.(agentId)?.model;
    return model === undefined ? { agentId } : { agentId, model };
  }

  /**
   * G9 Auto-triage: select the best agent for a task based on:
   * 1. modelRecommendation.preferredModel → match agent.model
   * 2. modelRecommendation.requiredCapabilities → match agent.capabilities
   * 3. modelRecommendation.complexity → prefer more capable agents for high complexity
   * 4. Fallback: first agent with "dev" role
   */
  private autoSelectAgent(params: CreateTaskParams, taskId: string): string {
    if (!this.agentListLookup) {
      throw new Error("agentListLookup not injected — Orchestrator wiring incomplete");
    }
    const agents = this.agentListLookup();
    // Filter agents by the requested role (default: dev)
    const targetRole = params.role ?? "dev";
    const roleAgents = agents.filter((a) => a.roles.includes(targetRole as AgentRole));
    if (roleAgents.length === 0) {
      throw new Error(
        `No agents with '${targetRole}' role in registry (${agents.length} total agents) — cannot auto-assign task`,
      );
    }

    // Helper: emit debug event and return selected agent
    const selectAndEmit = (
      agentId: string,
      reason: string,
      candidates?: { agentId: string; score: number }[],
    ): string => {
      this.bus.emit("orchestrator.routing.debug", "orchestrator", "orchestrator", {
        taskId,
        taskTitle: params.title,
        reason,
        candidates: candidates ?? [{ agentId, score: 0 }],
        selected: agentId,
      });
      return agentId;
    };

    if (roleAgents.length === 1) {
      return selectAndEmit(roleAgents[0].id, `single-${targetRole}-agent`);
    }

    const rawRec = params.modelRecommendation;
    // Normalize: treat recommendation with only whitespace values as absent
    const hasModel = !!rawRec?.preferredModel?.trim();
    const hasCaps = !!rawRec?.requiredCapabilities?.some((c) => c.trim().length > 0);
    const hasComplexity = !!rawRec?.complexity;
    if (!rawRec || (!hasModel && !hasCaps && !hasComplexity)) {
      return selectAndEmit(roleAgents[0].id, "no-recommendation-fallback");
    }
    const rec = rawRec;

    // Score each candidate agent
    const scored = roleAgents.map((agent) => {
      let score = 0;

      // Preferred model match: trim + lowercase to handle whitespace in MCP/JSON inputs
      if (rec.preferredModel && agent.model) {
        const preferred = rec.preferredModel.trim().toLowerCase();
        const agentModel = agent.model.trim().toLowerCase();
        if (preferred && agentModel) {
          if (agentModel === preferred) {
            score += 100; // Exact match
          } else if (agentModel.startsWith(preferred + "-") || preferred.startsWith(agentModel + "-")) {
            score += 50; // Family match (e.g. "claude-opus-4-6" matches "claude-opus-4-6-xxx")
          }
        }
      }

      // Capability matching (deduplicated, trimmed to prevent whitespace/repeated scoring)
      if (rec.requiredCapabilities?.length) {
        const uniqueCaps = [
          ...new Set(
            rec.requiredCapabilities
              .map((c) => c.trim().toLowerCase())
              .filter((c) => c.length > 0),
          ),
        ];
        const matched = uniqueCaps.filter((cap) =>
          agent.capabilities.some((ac) => ac.trim().toLowerCase() === cap),
        ).length;
        score += matched * 20;
      }

      // Complexity-based preference:
      // - high: prefer agents with more capabilities (stronger models)
      // - medium/low: no capability bias (any dev agent is suitable)
      if (rec.complexity === "high") {
        score += agent.capabilities.length * 5;
      }

      return { agent, score };
    });

    // Sort by score descending; tiebreak by agent ID for deterministic selection
    scored.sort((a, b) => b.score - a.score || a.agent.id.localeCompare(b.agent.id));

    return selectAndEmit(
      scored[0].agent.id,
      "scored-selection",
      scored.slice(0, 3).map((s) => ({ agentId: s.agent.id, score: s.score })),
    );
  }

  /** Rehydrate task state from persistence (call before RPC starts). */
  async init(): Promise<void> {
    if (!this.persistence) return;
    try {
      const { tasks, acceptances, issues } = await this.persistence.loadAll();
      for (const t of tasks) {
        // Backfill dispatch retry fields for legacy tasks (pre-RESILIENCE-001).
        // Note: legacy tasks always get maxDispatchAttempts = 5 regardless of role
        // for backward compatibility. New tasks created after this change receive
        // role-aware defaults via createTask() (research/design → 1, dev → 5).
        if (t.dispatchAttempts === undefined) t.dispatchAttempts = 0;
        if (t.maxDispatchAttempts === undefined) t.maxDispatchAttempts = 5;
        this.tasks.set(t.taskId, t);
      }
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
    if (!params.context?.trim()) errors.push("context is required");
    if (!params.definitionOfDone?.length) errors.push("definitionOfDone must have at least 1 item");
    if (!params.readScope) errors.push("readScope is required");
    if (params.researchScope !== undefined) {
      if (params.researchScope !== "deep" && params.researchScope !== "quick") {
        errors.push(`researchScope must be "deep" or "quick", got "${params.researchScope}"`);
      } else if ((params.role ?? "dev") !== "research") {
        errors.push(`researchScope is only valid for research tasks (role: "${params.role ?? "dev"}")`);
      }
    }
    const validPriorities = ["P0", "P1", "P2", "P3", "sev-0", "sev-1", "sev-2", "sev-3"];
    if (!validPriorities.includes(params.priority)) {
      errors.push(`priority must be one of P0, P1, P2, P3, got "${params.priority}"`);
    }
    if (params.maxDispatchAttempts !== undefined &&
        (!Number.isInteger(params.maxDispatchAttempts) || params.maxDispatchAttempts < 1)) {
      errors.push(`maxDispatchAttempts must be a positive integer, got ${params.maxDispatchAttempts}`);
    }
    // Filter empty/whitespace-only entries from write scope paths
    const normCodePaths = (params.allowedWriteScope?.codePaths ?? []).map((p) => p.trim()).filter(Boolean);
    const normKbPaths = (params.allowedWriteScope?.kbPaths ?? []).map((p) => p.trim()).filter(Boolean);
    if (normCodePaths.length === 0 && normKbPaths.length === 0) {
      errors.push("allowedWriteScope must have at least 1 codePath or kbPath");
    }
    // Research/design tasks must have kbPath for report output (fail-fast)
    const taskRole = params.role ?? "dev";
    if ((taskRole === "research" || taskRole === "design") && normKbPaths.length === 0) {
      errors.push(`${taskRole} tasks require at least one non-empty allowedWriteScope.kbPaths entry for report output`);
    }
    if (errors.length > 0) {
      throw new Error(`createTask validation failed:\n  - ${errors.join("\n  - ")}`);
    }

    // Normalize string inputs
    params.title = params.title.trim();
    params.context = params.context.trim();

    // Generate taskId early so it can be included in routing debug events
    const taskId = `TASK-${shortId()}`;

    // G9: Auto-assign agent if not provided — use modelRecommendation routing
    const explicitAssignedTo = params.assignedTo?.trim();
    const assignedTo = explicitAssignedTo || this.autoSelectAgent(params, taskId);

    // Validate role is a known dispatch role
    const VALID_DISPATCH_ROLES: readonly string[] = ["dev", "research", "design"];
    const rawRole = params.role ?? "dev";
    if (!VALID_DISPATCH_ROLES.includes(rawRole)) {
      throw new Error(`createTask validation failed: invalid role "${rawRole}" (must be one of: ${VALID_DISPATCH_ROLES.join(", ")})`);
    }
    const requiredRole = rawRole as AgentRole;
    if (this.agentConfigLookup) {
      const agentConfig = this.agentConfigLookup(assignedTo);
      if (!agentConfig) {
        throw new Error(`createTask validation failed: agent "${assignedTo}" is not registered`);
      }
      if (!agentConfig.roles.includes(requiredRole)) {
        throw new Error(`createTask validation failed: agent "${assignedTo}" does not have '${requiredRole}' role`);
      }
    }

    const task: TaskBundle = {
      taskId,
      title: params.title,
      phaseId: params.phaseId,
      priority: normalizePriority(params.priority),
      status: "drafted",
      createdAt: new Date().toISOString(),
      closedAt: null,
      failedAt: null,
      assignedTo,
      role: requiredRole,
      researchScope: params.researchScope,
      branch: params.branch,
      codeScope: params.codeScope ?? { include: [], exclude: [] },
      readScope: {
        requiredDocs: (params.readScope as Record<string, unknown>)?.requiredDocs as string[] ?? [],
        optionalDocs: (params.readScope as Record<string, unknown>)?.optionalDocs as string[] ?? [],
      },
      allowedWriteScope: { codePaths: normCodePaths, kbPaths: normKbPaths },
      docsMustUpdate: params.docsMustUpdate ?? [],
      docsMustNotTouch: params.docsMustNotTouch ?? [],
      definitionOfDone: params.definitionOfDone,
      requiredEvidence: params.requiredEvidence ?? [],
      context: params.context,
      reviewConfig: params.reviewConfig,
      handoffToAcceptance: params.handoffToAcceptance,
      modelRecommendation: params.modelRecommendation,
      dispatchAttempts: 0,
      // Research and design roles run long single-pass sessions; context exhaustion
      // is not a retriable error — re-dispatching from scratch wastes resources and
      // produces no output. Default to 1 for these roles. Dev tasks keep 5 so that
      // transient failures (session crash, network blip) are automatically retried.
      maxDispatchAttempts: params.maxDispatchAttempts ?? (requiredRole === "research" || requiredRole === "design" ? 1 : 5),
      reworkCount: 0,
      maxReworks: params.maxReworks ?? 3,
      linkedIssueIds: [],
      reworkHistory: [],
      resourceBudget: params.resourceBudget,
    };

    // Agents First: populate structured assignee from agent config
    task.assignee = this.buildTaskAssignee(assignedTo);

    this.tasks.set(taskId, task);
    this.persistTask(task);

    this.bus.emit(
      "orchestrator.task.created",
      assignedTo,
      "orchestrator",
      {
        taskId,
        title: task.title,
        assignedTo: task.assignedTo,
        priority: task.priority,
        modelRecommendation: task.modelRecommendation,
        routingMethod: explicitAssignedTo ? "explicit" : "auto-triage",
      },
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

    // Check docsUpdated against docsMustNotTouch (supports prefix matching for directories)
    for (const doc of receipt.docsUpdated) {
      const blocked = task.docsMustNotTouch.some(
        (entry) => doc === entry || (entry.endsWith("/") && doc.startsWith(entry))
      );
      if (blocked) {
        violations.push({ file: doc, reason: "Listed in docsMustNotTouch" });
      }
    }

    return { valid: violations.length === 0, violations };
  }

  // ─── Rework ───

  triggerRework(
    taskId: string,
    feedback: string,
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
        findings: findings?.length ? findings : (feedback.trim() ? [feedback.trim()] : []),
        timestamp: Date.now(),
      });
    }

    task.reworkCount += 1;

    if (task.reworkCount > task.maxReworks) {
      this.transitionTask(taskId, "failed", task.assignedTo ?? "orchestrator");
      return { reworked: false, newSession: false };
    }

    const needsNewSession = task.reworkCount >= task.maxReworks;

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

  /** Persist a synthetic acceptance for fast-tracked tasks (research/design) so getTaskResult() returns a verdict. */
  persistSyntheticAcceptance(
    taskId: string,
    acceptorId: string,
    results: { verdict: AcceptanceVerdict; findings: string[]; recommendations: string[] },
  ): void {
    const acceptanceId = `ACC-${shortId()}`;
    const acceptance: AcceptanceBundle = {
      acceptanceId,
      linkedTaskId: taskId,
      status: "completed",
      acceptor: acceptorId,
      scope: { filesToReview: [], docsToCheck: [], runtimeChecks: [] },
      blindInputPolicy: { allowed: [], forbidden: [] },
      results,
      completedAt: Date.now(),
    };
    this.acceptances.set(acceptanceId, acceptance);
    this.persistAcceptance(acceptance);

    // Link to task
    const task = this.tasks.get(taskId);
    if (task) {
      if (!task.handoffToAcceptance) {
        task.handoffToAcceptance = {
          acceptanceBundleId: acceptanceId,
          blindInputPolicy: acceptance.blindInputPolicy,
          acceptanceFocus: task.definitionOfDone,
        };
      } else {
        task.handoffToAcceptance.acceptanceBundleId = acceptanceId;
      }
      this.persistTask(task);
    }
  }

  /** Find the latest acceptance for a given task ID. */
  getAcceptanceByTaskId(taskId: string): AcceptanceBundle | undefined {
    let latest: AcceptanceBundle | undefined;
    for (const a of this.acceptances.values()) {
      if (a.linkedTaskId === taskId) {
        if (!latest || (a.completedAt ?? 0) > (latest.completedAt ?? 0)) {
          latest = a;
        }
      }
    }
    return latest;
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

  // ─── Dispatch Retry Tracking ───

  /**
   * Validate whether a task can be dispatched (pre-dispatch check).
   * Returns null if OK, or an error message string if dispatch should be blocked.
   */
  validateDispatch(taskId: string): string | null {
    const task = this.tasks.get(taskId);
    if (!task) return `Task not found: ${taskId}`;
    if (task.status !== "drafted" && task.status !== "dispatched") {
      return `Task ${taskId} is in status "${task.status}" — only drafted/dispatched tasks can be dispatched`;
    }
    if (task.dispatchAttempts >= task.maxDispatchAttempts) {
      return `Task ${taskId} exceeded max dispatch attempts (${task.dispatchAttempts}/${task.maxDispatchAttempts})`;
    }
    // Research/design tasks must have at least one non-empty kbPath for report output
    const VALID_ROLES = ["dev", "research", "design"] as const;
    if (task.role !== undefined && task.role !== null && !(VALID_ROLES as readonly string[]).includes(task.role)) {
      return `Task ${taskId} has invalid role "${task.role}"; expected one of: ${VALID_ROLES.join(", ")}`;
    }
    if (task.role === undefined || task.role === null) {
      console.warn(`[TaskManager] Task ${taskId} has no role set; defaulting to "dev" for dispatch`);
    }
    const role = task.role ?? "dev";
    if (role === "research" || role === "design") {
      const validKbPaths = (task.allowedWriteScope?.kbPaths ?? []).filter((p) => p.trim().length > 0);
      if (validKbPaths.length === 0) {
        return `Task ${taskId} (${role}) requires at least one non-empty allowedWriteScope.kbPaths entry for report output`;
      }
    }
    return null;
  }

  /**
   * Record a dispatch attempt (success or failure).
   * Call this before each dispatch attempt to track retries.
   */
  recordDispatchAttempt(taskId: string, error?: string): void {
    const task = this.tasks.get(taskId);
    if (!task) return;
    task.dispatchAttempts += 1;
    task.lastDispatchAt = new Date().toISOString();
    if (error) {
      task.lastDispatchError = error;
    } else {
      task.lastDispatchError = undefined;
    }
    this.persistTask(task);
  }
}

// ─── Prompt Builders ───

/** Format allowedWriteScope for human-readable display (codePaths + kbPaths). */
/**
 * Resolve the Obsidian vault name from mercury.config.json.
 * Priority: obsidian.vaultName > basename(obsidian.vaultPath) > null.
 * Returns null when config is missing or has no obsidian section.
 */
function resolveVaultName(basePath: string): string | null {
  try {
    const configPath = resolve(basePath, "mercury.config.json");
    const config = JSON.parse(readFileSync(configPath, "utf-8"));
    const obs = config.obsidian;
    if (!obs) return null;
    if (obs.vaultName) return obs.vaultName;
    if (obs.vaultPath) {
      // Derive vault name from the last segment of the configured path
      const segments = obs.vaultPath.replace(/[\\/]+$/, "").split(/[\\/]/);
      return segments[segments.length - 1] || null;
    }
    return null;
  } catch {
    return null;
  }
}

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
/**
 * Build the "## Accumulated Skills" block injected into dev/research prompts.
 * Each SkillInjection carries a `body` field (SKILL.md text after frontmatter).
 * Token budget: max 3 skills × ~300 tokens each = ~900 tokens (~1% context).
 */
function buildSkillsBlock(skills: SkillInjection[]): string {
  if (skills.length === 0) return "";
  const sections = ["\n\n## Accumulated Skills (Mercury Skill Library)"];
  sections.push("These reusable patterns were accumulated through prior tasks. Apply when relevant:");
  for (const skill of skills) {
    sections.push(`\n### ${skill.name} [${skill.category}]`);
    sections.push(`_${skill.description}_`);
    if (skill.body) sections.push(skill.body.replace(/```/g, "` ` `"));
  }
  return sections.join("\n");
}

/**
 * Build a git log section for dev dispatch prompts.
 * Returns a markdown block with the last 20 commits on the task branch (best-effort).
 */
function buildGitLogSection(branch: string | undefined, basePath: string): string {
  if (!branch) return "";
  try {
    const log = execFileSync("git", ["-C", basePath, "log", "--max-count=20", "--oneline", branch], {
      timeout: 5000,
      encoding: "utf-8",
    }).replace(/```/g, "` ` `").trim();
    if (!log) return "";
    return `

## Recent Commits on Branch (${branch})
\`\`\`
${log}
\`\`\``;
  } catch {
    return "";
  }
}

export function buildDevPrompt(
  task: TaskBundle,
  kbContext?: string,
  basePath = process.cwd(),
  relevantSkills?: SkillInjection[],
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
  // Empty arrays within scope objects are stripped; if all sub-fields are empty
  // the scope key itself is also omitted to avoid serializing empty {} (DEC-2).
  /** Strip empty string[] fields from a scope object to save dispatch tokens. */
  const compactScope = <T extends Record<string, string[]>>(scope: T): Partial<T> | undefined => {
    const out: Partial<T> = {};
    let hasContent = false;
    for (const key of Object.keys(scope) as (keyof T)[]) {
      const val = scope[key];
      if (Array.isArray(val) && val.length > 0) { out[key] = val; hasContent = true; }
    }
    return hasContent ? out : undefined;
  };
  const bundleMeta: Record<string, unknown> = {
    taskId: task.taskId,
    assignee: task.assignee ?? { agentId: task.assignedTo },
    priority: task.priority,
    branch: task.branch ?? null,
    definitionOfDone: task.definitionOfDone,
    reworkCount: task.reworkCount,
    maxReworks: task.maxReworks,
  };
  // Scope fields: omit entirely when all sub-fields are empty (defensive for legacy KB entries)
  if (task.codeScope) { const cs = compactScope(task.codeScope); if (cs) bundleMeta.codeScope = cs; }
  if (task.readScope) { const rs = compactScope(task.readScope); if (rs) bundleMeta.readScope = rs; }
  if (task.allowedWriteScope) { const ws = compactScope(task.allowedWriteScope); if (ws) bundleMeta.allowedWriteScope = ws; }
  if ((task.docsMustUpdate ?? []).length > 0) bundleMeta.docsMustUpdate = task.docsMustUpdate;
  if ((task.docsMustNotTouch ?? []).length > 0) bundleMeta.docsMustNotTouch = task.docsMustNotTouch;
  if ((task.requiredEvidence ?? []).length > 0) bundleMeta.requiredEvidence = task.requiredEvidence;

  const receiptTemplate = JSON.stringify(
    { implementer: "", branch: "", summary: "", changedFiles: [], evidence: [], docsUpdated: [], residualRisks: [], completedAt: "" },
    null,
    2,
  );

  const scopeDisplay = formatWriteScope(task.allowedWriteScope);

  // Resolve vault name: prefer vaultName, derive from vaultPath basename, or null
  const vaultName = resolveVaultName(basePath);

  // Single-pass template substitution to prevent cross-replacement
  const placeholders: Record<string, string> = {
    "{{taskId}}": `${task.title} [${task.taskId}]`,
    "{{context}}": task.context,
    "{{taskFilePath}}": `10-tasks/${task.taskId}.json`,
    "{{vaultName}}": vaultName ?? "Mercury_KB",
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

  result += buildGitLogSection(task.branch, basePath);

  if (relevantSkills && relevantSkills.length > 0) {
    result += buildSkillsBlock(relevantSkills);
  }

  if (kbContext) {
    result += `

## Project Knowledge Base Context
${kbContext}`;
  }

  return result;
}

function fallbackDevTemplate(): string {
  return [
    "# Dispatch: {{taskId}}",
    "",
    "{{context}}",
    "",
    "## KB Access",
    "KB vault: `{{vaultName}}` — use Obsidian MCP (`mcp__obsidian__*`) or CLI (`obsidian vault=\"{{vaultName}}\"`).",
    "Task bundle: `{{taskFilePath}}` (vault-relative path).",
    "Never construct `Mercury_KB/...` paths from the project CWD.",
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
 * Derive deterministic KB write instructions from allowedWriteScope.kbPaths.
 * If a kbPath ends with ".md", use it as-is; otherwise treat it as a prefix
 * and append `${taskId}.md` to produce an explicit filename.
 *
 * Note: validateDispatch() ensures research/design tasks always have at least
 * one kbPath, so the empty-array branch is only reachable for dev tasks.
 */
function deriveKbWriteInstructions(task: TaskBundle, vaultPath?: string): string[] {
  const kbPaths = task.allowedWriteScope?.kbPaths ?? [];
  if (kbPaths.length === 0) {
    return ["No KB output path configured for this task."];
  }
  const rawTarget = kbPaths[0].endsWith(".md")
    ? kbPaths[0]
    : `${kbPaths[0].replace(/\/+$/, "")}/${task.taskId}.md`;

  // Strip leading vault-name prefix to prevent double-path segments
  // (e.g. "Mercury_KB/04-research/foo.md" → "04-research/foo.md")
  let normalizedTarget = rawTarget.replace(/\\/g, "/");
  if (vaultPath) {
    const vaultBaseName = basename(vaultPath);
    const prefix = `${vaultBaseName}/`;
    if (normalizedTarget.startsWith(prefix)) {
      normalizedTarget = normalizedTarget.slice(prefix.length);
    }
  }

  // Derive safe absolute path with traversal guard (mirrors KnowledgeService.resolveVaultFilePath)
  let safePath: string | null = null;
  if (vaultPath) {
    const segments = normalizedTarget.split(/[\\/]+/).filter(Boolean);
    const resolved = resolve(vaultPath, ...segments);
    const normalizedVault = resolve(vaultPath);
    if (resolved.startsWith(normalizedVault + sep) || resolved === normalizedVault) {
      safePath = resolved; // preserve platform-native separators
    } else {
      console.warn(`[task-manager] KB path traversal blocked for task ${task.taskId}: ${normalizedTarget}`);
    }
  }

  return [
    "## KB Write Instructions (3-tier priority)",
    "**Tier 1 — MCP (preferred)**: call `mcp__mercury-orchestrator__kb_write`:",
    `  - \`name\`: \`${normalizedTarget}\``,
    "  - `content`: your complete Markdown report",
    ...(safePath ? [`  - Absolute path: \`${safePath}\``] : []),
    ...(safePath
      ? [
          "**Tier 2 — direct file write (if MCP unavailable)**: write the report directly to:",
          `  \`${safePath}\``,
          "  Create parent directories as needed.",
        ]
      : ["**Tier 2 — unavailable**: no safe vault path configured; use Tier 3 if Tier 1 fails."]),
    "**Tier 3 — embed in Step 1 JSON**: if both Tier 1 and Tier 2 fail, include the full report text",
    "  inside the `findings` array of your Step 1 JSON output — as part of that same output, NOT as a",
    "  subsequent assistant message. Do NOT send additional messages after Step 1 to patch or augment",
    "  the findings; the complete report must be present within the single Step 1 JSON response.",
    "Do NOT stop silently — always persist the report by one of these three methods.",
  ];
}

/** Minimum context window tokens before skipping research and going straight to JSON output. */
const RESEARCH_CONTEXT_BUDGET_THRESHOLD = 20_000;

/**
 * Token budget level at which the research agent should delegate heavy file scanning to Codex.
 * Must be above RESEARCH_CONTEXT_BUDGET_THRESHOLD to leave room for synthesis.
 */
const CODEX_DELEGATION_THRESHOLD = 40_000;

if (CODEX_DELEGATION_THRESHOLD <= RESEARCH_CONTEXT_BUDGET_THRESHOLD) {
  throw new Error(
    `CODEX_DELEGATION_THRESHOLD (${CODEX_DELEGATION_THRESHOLD}) must be greater than RESEARCH_CONTEXT_BUDGET_THRESHOLD (${RESEARCH_CONTEXT_BUDGET_THRESHOLD})`,
  );
}

/**
 * Claude Code sub-agent type for Codex delegation.
 * Provided by the official codex@openai-codex plugin (openai/codex-plugin-cc).
 * Install: /plugin marketplace add openai/codex-plugin-cc then /plugin install codex@openai-codex
 * Distinct from Mercury's own codex-cli adapter.
 */
const CODEX_SUBAGENT_ID = "codex:codex-rescue";

/**
 * Compact Codex sub-agent hint for non-research prompts (design, rework, send-back).
 * Omits the full research protocol to minimise context consumption at session start.
 * Only injected when the target agent is a Claude Code instance (cli === "claude").
 */
function buildCodexHint(): string[] {
  return [
    "## Codex Sub-Agent Delegation",
    `If \`codex@openai-codex\` is installed: delegate to \`${CODEX_SUBAGENT_ID}\` via the Agent tool when your token budget drops below ${CODEX_DELEGATION_THRESHOLD.toLocaleString()} tokens or a task requires scanning 5+ files sequentially.`,
    "",
  ];
}

/**
 * Build a research-role dispatch prompt.
 * Research tasks produce findings/reports, not code commits.
 */
export function buildResearchPrompt(
  task: TaskBundle,
  kbContext?: string,
  maxIterations?: number,
  tokenBudgetHint?: number,
  vaultPath?: string,
  codexEnabled?: boolean,
  relevantSkills?: SkillInjection[],
): string {
  const lines = [
    `# Research Task: ${task.title} [${task.taskId}]`,
    "",
    task.context,
    "",
    "## Task Bundle",
    "```json",
    JSON.stringify({
      taskId: task.taskId,
      priority: task.priority,
      definitionOfDone: task.definitionOfDone,
      readScope: task.readScope,
      allowedWriteScope: task.allowedWriteScope,
    }, null, 2),
    "```",
    "",
    "## Research Instructions",
    "- Conduct deep research on the topic described above.",
    "- Use web search, codebase analysis, and KB resources as needed.",
    "- Produce structured findings with evidence and recommendations.",
    "- No code commits expected.",
    ...(task.researchScope === "deep" ? [
      "",
      "## Deep Research Protocol",
      "This task requires the `/deep-research` protocol. Type `/deep-research` at the start of your response to activate the Mercury Deep Research Protocol with full multi-round verification.",
    ] : []),
    ...(maxIterations && maxIterations > 0
      ? [
        `- **MAX_ITERATIONS: ${maxIterations}** — you MUST stop the research loop and submit your findings after at most ${maxIterations} round(s). Do not continue past this limit under any circumstances.`,
        ]
      : []),
    ...(tokenBudgetHint !== undefined && tokenBudgetHint > 0
      ? [`- **TOKEN_BUDGET: ~${tokenBudgetHint.toLocaleString()} tokens remaining** — wrap up and submit findings before reaching this limit.`]
      : []),
    ...(codexEnabled ? [
      "",
      "## Codex Sub-Agent Delegation",
      `If the \`codex@openai-codex\` plugin is installed and enabled, use \`${CODEX_SUBAGENT_ID}\` via the Agent tool for token-intensive file scanning.`,
      "**Delegate when (plugin enabled):**",
      `- Your token budget drops below ${CODEX_DELEGATION_THRESHOLD.toLocaleString()} tokens AND you still need file scanning`,
      "- A task requires scanning 5+ files sequentially with Grep/Read",
      "- You need broad codebase pattern analysis across many directories",
      "",
      "**How to delegate (Agent tool call):**",
      "```",
      `Agent tool: subagent_type="${CODEX_SUBAGENT_ID}"`,
      "prompt: \"[RESEARCH PROTOCOL] Evidence-over-claims: report only what you find, not assumptions.",
      "Search iteratively with multiple patterns before concluding 'not found'.",
      "Return file:line references for all findings.",
      "Task: <focused scanning task description>\"",
      "```",
      "The sub-agent's usage counts toward your shared Codex budget. Plan delegation conservatively and monitor actual token consumption via runtime feedback.",
      "Use its output as raw evidence in your Step 1 JSON; you write the final synthesis.",
      "If the plugin is not installed or the Agent tool call fails, continue with local Grep/Read and narrow scope early to protect remaining budget.",
    ] : []),
    "",
    "## CRITICAL: Context Budget Check",
    `Before starting research, check your remaining context window. If it is below ${RESEARCH_CONTEXT_BUDGET_THRESHOLD.toLocaleString()} tokens,`,
    "SKIP all research and go directly to Step 1 to output the JSON summary with what you know.",
    "",
    "## Step 1 (ALWAYS FIRST): Output JSON Summary",
    "Your FINAL MESSAGE must be the JSON summary below. Output it BEFORE writing to KB.",
    "This guarantees the receipt has content even if KB write fails or context exhausts.",
    "Keep each finding/recommendation as a cohesive paragraph (not split by line):",
    "```json",
    JSON.stringify({
      researcher: "",
      summary: "",
      findings: ["<finding 1 as one complete paragraph>", "<finding 2 ...>"],
      recommendations: ["<recommendation 1 as one complete paragraph>"],
      evidence: ["<source URL or file reference>"],
      completedAt: "",
    }, null, 2),
    "```",
    "",
    "## Step 2 (After JSON output): Write Report to KB",
    "After outputting the JSON summary above, write the full report to KB.",
    ...deriveKbWriteInstructions(task, vaultPath),
    "Do NOT send any additional messages after Step 1 JSON — the orchestrator reads only your last message.",
  ];

  if (relevantSkills && relevantSkills.length > 0) {
    lines.push(...buildSkillsBlock(relevantSkills).split("\n"));
  }

  if (kbContext) {
    lines.push("", "## Project Knowledge Base Context", kbContext);
  }

  return lines.join("\n");
}

/**
 * Build a design-role dispatch prompt.
 * Design tasks produce design artifacts, not code commits.
 */
export function buildDesignPrompt(
  task: TaskBundle,
  kbContext?: string,
  tokenBudgetHint?: number,
  vaultPath?: string,
  codexEnabled?: boolean,
): string {
  const lines = [
    `# Design Task: ${task.title} [${task.taskId}]`,
    "",
    task.context,
    "",
    "## Task Bundle",
    "```json",
    JSON.stringify({
      taskId: task.taskId,
      priority: task.priority,
      definitionOfDone: task.definitionOfDone,
      readScope: task.readScope,
      allowedWriteScope: task.allowedWriteScope,
    }, null, 2),
    "```",
    "",
    "## Design Instructions",
    "- Produce design artifacts (specs, diagrams, architecture docs) for the topic above.",
    "- Reference existing code patterns and KB docs as needed.",
    "- No code implementation expected.",
    ...(tokenBudgetHint !== undefined && tokenBudgetHint > 0
      ? [`- **TOKEN_BUDGET: ~${tokenBudgetHint.toLocaleString()} tokens remaining** — wrap up and submit design before reaching this limit.`]
      : []),
    ...(codexEnabled ? buildCodexHint() : []),
    "## CRITICAL: Context Budget Check",
    `Before starting design work, check your remaining context window. If it is below ${RESEARCH_CONTEXT_BUDGET_THRESHOLD.toLocaleString()} tokens,`,
    "SKIP all design work and go directly to Step 1 to output the JSON summary with what you know.",
    "",
    "## Step 1 (ALWAYS FIRST): Output JSON Summary",
    "Your FINAL MESSAGE must be the JSON design report below. Output it BEFORE writing to KB.",
    "This guarantees the receipt has content even if KB write fails or context exhausts.",
    "```json",
    JSON.stringify({
      designer: "",
      summary: "",
      findings: [""],
      designDoc: "",
      artifacts: [""],
      completedAt: "",
    }, null, 2),
    "```",
    "",
    "## Step 2 (After JSON output): Write Report to KB",
    "After outputting the JSON summary above, write the full design report to KB.",
    ...deriveKbWriteInstructions(task, vaultPath),
    "Do NOT send any additional messages after Step 1 JSON — the orchestrator reads only your last message.",
  ];

  if (kbContext) {
    lines.push("", "## Project Knowledge Base Context", kbContext);
  }

  return lines.join("\n");
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
  // Defensive: warn if path looks like absolute or has Mercury_KB prefix
  const isNotVaultRelative = (p: string) => p.includes("Mercury_KB") || /^[A-Z]:[/\\]|^\//.test(p);
  if (isNotVaultRelative(taskFilePath)) {
    console.warn(`[buildReferencePrompt] taskFilePath should be vault-relative, got: ${taskFilePath}`);
  }
  if (handoffFilePath && isNotVaultRelative(handoffFilePath)) {
    console.warn(`[buildReferencePrompt] handoffFilePath should be vault-relative, got: ${handoffFilePath}`);
  }
  const lines: string[] = [];

  lines.push(`实现任务 ${task.taskId}: ${task.title}`);
  lines.push("");
  lines.push(`任务详情: 使用 Obsidian MCP 读取 ${taskFilePath} (vault-relative 路径)`);
  if (handoffFilePath) {
    lines.push(`项目上下文: 使用 Obsidian MCP 读取 ${handoffFilePath}`);
  }
  lines.push("");
  lines.push("KB 访问: 使用 mcp__obsidian__obsidian_get_file_contents，禁止从项目 CWD 拼接 Mercury_KB/ 路径");
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
  codexEnabled?: boolean,
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

  if (codexEnabled) {
    lines.push(...buildCodexHint());
  }
  lines.push("## Instructions");
  lines.push("Address the findings above within your current scope. Do not start from scratch.");
  lines.push("Learn from previous attempts listed in the rework history — do not repeat the same mistakes.");
  lines.push("When complete, output an updated JSON receipt.");

  return lines.join("\n");
}

/** Build the rework prompt sent to the Dev Agent after Main Agent SEND_BACK decision. */
export function buildSendBackPrompt(task: TaskBundle, reason: string, codexEnabled?: boolean): string {
  const lines: string[] = [];

  lines.push(`# Rework Required [${task.taskId}]`);
  lines.push(`This is attempt **${task.reworkCount}/${task.maxReworks}** — returned by Main Agent review.`);
  lines.push("");

  lines.push("## Send-Back Context");
  const meta = {
    taskId: task.taskId,
    reworkCount: task.reworkCount,
    maxReworks: task.maxReworks,
    reason,
  };
  lines.push("```json");
  lines.push(sanitizeFenceContent(JSON.stringify(meta, null, 2)));
  lines.push("```");
  lines.push("");

  lines.push("## Task Specification");
  const spec = {
    taskId: task.taskId,
    title: task.title,
    context: task.context,
    definitionOfDone: task.definitionOfDone,
    allowedWriteScope: task.allowedWriteScope,
    readScope: task.readScope,
  };
  lines.push("```json");
  lines.push(sanitizeFenceContent(JSON.stringify(spec, null, 2)));
  lines.push("```");
  lines.push("");

  if (task.implementationReceipt) {
    lines.push("## Previous Implementation Receipt");
    lines.push("```json");
    lines.push(truncate(sanitizeFenceContent(JSON.stringify(task.implementationReceipt, null, 2)), MAX_PRECHECKS_CHARS));
    lines.push("```");
    lines.push("");
  }

  if (task.reworkHistory.length > 0) {
    lines.push("## Rework History");
    const historyEntries = task.reworkHistory.slice(-5);
    for (const entry of historyEntries) {
      lines.push(`### Attempt ${entry.attempt}`);
      lines.push(`- Findings: ${entry.findings.join("; ") || "none"}`);
      lines.push(`- Timestamp: ${new Date(entry.timestamp).toISOString()}`);
    }
    lines.push("");
  }

  if (codexEnabled) {
    lines.push(...buildCodexHint());
  }
  lines.push("## Instructions");
  lines.push("Address the reason stated above. Do not start from scratch.");
  lines.push("Review the task specification and previous receipt to understand what needs to change.");
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
export function buildMainReviewPrompt(task: TaskBundle, diffBaseRef = "develop...HEAD"): string {
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
  lines.push(`## Git Diff (\`${diffBaseRef}\`)`);
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

/** Build the Critic Agent verification prompt — spec-driven DoD validation. */
export function buildCriticPrompt(task: TaskBundle, _projectRoot?: string): string {
  const lines: string[] = [];

  lines.push(`# Critic Verification: ${task.title} [${task.taskId}]`);
  lines.push("");

  // Definition of Done — the "spec" to verify against
  lines.push("## Definition of Done (verify each item)");
  for (const [i, item] of task.definitionOfDone.entries()) {
    lines.push(`${i + 1}. ${item}`);
  }
  lines.push("");

  // Task context
  lines.push("## Task Context");
  lines.push(task.context || "(no context provided)");
  lines.push("");

  // Code scope
  lines.push("## Code Scope");
  lines.push("```json");
  lines.push(JSON.stringify(task.codeScope ?? { include: [], exclude: [] }, null, 2));
  lines.push("```");
  lines.push("");

  // Pre-check results (if available)
  if (task.mainReview?.preChecks?.length) {
    lines.push("## Pre-Check Results");
    for (const pc of task.mainReview.preChecks) {
      const icon = pc.success ? "PASS" : "FAIL";
      lines.push(`- [${icon}] ${pc.name}: exit=${pc.exitCode}, ${pc.durationMs}ms`);
      if (!pc.success && pc.stdout) {
        lines.push("  ```");
        lines.push("  " + truncate(pc.stdout, 500));
        lines.push("  ```");
      }
    }
    lines.push("");
  }

  // Git diff (if available)
  if (task.mainReview?.gitDiff) {
    lines.push("## Git Diff");
    lines.push("```diff");
    lines.push(sanitizeFenceContent(truncate(task.mainReview.gitDiff, MAX_DIFF_CHARS)));
    lines.push("```");
    lines.push("");
  }

  // Changed files list from receipt
  if (task.implementationReceipt?.changedFiles?.length) {
    lines.push("## Changed Files");
    for (const f of task.implementationReceipt.changedFiles) {
      lines.push(`- ${f}`);
    }
    lines.push("");
  }

  // Instructions
  lines.push("## Instructions");
  lines.push("");
  lines.push("For EACH Definition of Done item above:");
  lines.push("1. Locate the relevant code changes in the diff");
  lines.push("2. Verify the implementation satisfies the requirement");
  lines.push("3. Cite specific evidence (file:line or test output)");
  lines.push("4. Assign a verdict: pass / fail / partial / skip");
  lines.push("");
  lines.push("Return your result as JSON:");
  lines.push("```json");
  lines.push(JSON.stringify({
    overallVerdict: "pass|partial|fail",
    completeness: 0.85,
    items: [
      {
        dodItem: "the DoD checklist item text",
        verdict: "pass|fail|partial|skip",
        evidence: "file:line or test output citation",
        detail: "explanation of verification result",
      },
    ],
    blockers: ["critical issues that must be fixed"],
    suggestions: ["optional improvements"],
  }, null, 2));
  lines.push("```");
  lines.push("");
  lines.push("Any item with verdict `fail` should appear in `blockers`.");
  lines.push("Set `overallVerdict` to `fail` if any blocker exists, `partial` if any item is partial/skip, `pass` if all pass.");

  return lines.join("\n");
}
