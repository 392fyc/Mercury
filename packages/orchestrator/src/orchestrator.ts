/**
 * Mercury Orchestrator — core class managing agent sessions, prompts, and event flow.
 */

import { EventBus, makeRoleSlotKey } from "@mercury/core";
import type {
  AgentConfig,
  AgentRole,
  RoleSlotKey,
  SessionInfo,
  AgentMessage,
  ImageAttachment,
  MercuryConfig,
  AcceptanceVerdict,
  ImplementationReceipt,
  SlashCommand,
  TaskBundle,
  TaskStatus,
} from "@mercury/core";
import { AgentRegistry } from "./agent-registry.js";
import type { KnowledgeService } from "./knowledge-service.js";
import type { RpcTransport } from "./rpc-transport.js";
import {
  TaskManager,
  buildDevPrompt,
  buildAcceptancePrompt,
  buildReworkPrompt,
  buildMainReviewPrompt,
} from "./task-manager.js";
import type { CreateTaskParams, CreateIssueParams } from "./task-manager.js";
import { TaskPersistenceKB } from "./task-persistence-kb.js";
import { buildRoleSystemPrompt, buildAcceptanceRolePrompt } from "./role-prompt-builder.js";

export class Orchestrator {
  private bus: EventBus;
  private registry: AgentRegistry;
  private transport: RpcTransport;
  private sessions = new Map<string, SessionInfo>();
  private roleSessions = new Map<RoleSlotKey, string>(); // roleSlotKey → active sessionId
  private agentCwds = new Map<string, string>(); // agentId → working directory
  private kb: KnowledgeService | null = null;
  private projectConfig: MercuryConfig | null = null;
  private taskManager: TaskManager;
  /** Cached shared context (built from KB contextFiles). Injected into all adapters. */
  private sharedContext: string = "";

  constructor(
    registry: AgentRegistry,
    transport: RpcTransport,
    bus?: EventBus,
  ) {
    this.registry = registry;
    this.transport = transport;
    this.bus = bus ?? new EventBus();
    this.taskManager = new TaskManager(this.bus);

    // Forward all EventBus events as RPC notifications
    this.bus.on("*", (event) => {
      this.transport.sendNotification("mercury_event", {
        id: event.id,
        type: event.type,
        timestamp: event.timestamp,
        agentId: event.agentId,
        sessionId: event.sessionId,
        payload: event.payload as Record<string, unknown>,
        parentEventId: event.parentEventId,
      });
    });
  }

  /** Inject optional knowledge service + wire up task persistence. */
  setKnowledgeService(kb: KnowledgeService) {
    this.kb = kb;
    // Wire KB persistence into TaskManager
    const persistence = new TaskPersistenceKB(kb, (msg) =>
      this.transport.sendNotification("log", { message: msg }),
    );
    this.taskManager.setPersistence(persistence);
    // Wire agent config lookup for Agents First assignee.model
    this.taskManager.setAgentConfigLookup((agentId) =>
      this.registry.listAgents().find((a) => a.id === agentId),
    );
  }

  /** Wire agent config lookup for Agents First assignee.model (works with or without KB). */
  setAgentConfigLookup(): void {
    this.taskManager.setAgentConfigLookup((agentId) =>
      this.registry.listAgents().find((a) => a.id === agentId),
    );
  }

  /** Rehydrate task state from KB persistence + build shared context. */
  async init(): Promise<void> {
    await this.taskManager.init();
    // Build and inject shared context from KB if autoInjectContext is enabled
    await this.buildAndInjectContext();
  }

  /** Store project config for get_config/update_config RPC. */
  setProjectConfig(config: MercuryConfig) {
    this.projectConfig = config;
  }

  /**
   * Build shared context from KB contextFiles and inject into all adapters.
   * Uses systemPrompt — does NOT consume conversation context window.
   */
  private async buildAndInjectContext(): Promise<{ injected: boolean; agentCount: number; contextLength: number }> {
    const obsConfig = this.projectConfig?.obsidian;
    if (!obsConfig?.autoInjectContext || !obsConfig.contextFiles?.length) {
      return { injected: false, agentCount: 0, contextLength: 0 };
    }

    if (!this.kb?.isEnabled()) {
      this.transport.sendNotification("log", {
        message: "[context] autoInjectContext enabled but KB not available — skipping",
      });
      return { injected: false, agentCount: 0, contextLength: 0 };
    }

    try {
      const context = await this.kb.buildContext(obsConfig.contextFiles);
      if (!context) {
        return { injected: false, agentCount: 0, contextLength: 0 };
      }

      this.sharedContext = context;

      // Context is stored — role-specific prompts are injected per-session via startRoleSession()
      this.transport.sendNotification("log", {
        message: `[context] Shared context loaded (${context.length} chars from ${obsConfig.contextFiles.length} files). Will inject per-session with role constraints.`,
      });

      return { injected: true, agentCount: 0, contextLength: context.length };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.transport.sendNotification("log", {
        message: `[context] Failed to build shared context: ${msg}`,
      });
      return { injected: false, agentCount: 0, contextLength: 0 };
    }
  }

  async handleRpc(
    method: string,
    params: Record<string, unknown>,
  ): Promise<unknown> {
    switch (method) {
      case "get_agents":
        return this.getAgents();
      case "start_session":
        return this.startRoleSession(
          params.agentId as string,
          (params.role as AgentRole | null) ?? undefined,
          (params.taskName as string | null) ?? undefined,
        );
      case "send_prompt":
        return this.sendPrompt(
          params.agentId as string,
          params.prompt as string,
          (params.images as ImageAttachment[] | null) ?? undefined,
          (params.role as AgentRole | null) ?? undefined,
          (params.taskName as string | null) ?? undefined,
        );
      case "stop_session":
        return this.stopSession(
          params.agentId as string,
          params.sessionId as string,
        );
      case "configure_agent":
        return this.configureAgent(params.config as AgentConfig);
      case "dispatch_task":
        // Bundle-aware: if taskId present, use bundle flow
        if (params.taskId) {
          return this.dispatchBundleTask(params.taskId as string);
        }
        return this.dispatchTask(
          params.fromAgentId as string,
          params.toAgentId as string,
          params.prompt as string,
        );
      case "create_task":
        return this.taskManager.createTask(params as unknown as CreateTaskParams);
      case "get_task":
        return (await this.taskManager.getTaskAsync(params.taskId as string)) ?? null;
      case "list_tasks":
        return this.taskManager.listTasksAsync(params as { status?: TaskStatus; assignedTo?: string });
      case "record_receipt":
        return this.recordReceiptAndTriggerReview(
          params.taskId as string,
          params.receipt as ImplementationReceipt,
        );
      case "main_review_result":
        return this.handleMainReviewResult(
          params.taskId as string,
          params.decision as string,
          (params.reason as string | null) ?? undefined,
          (params.acceptorId as string | null) ?? undefined,
        );
      case "create_acceptance":
        return this.createAcceptanceFlow(
          params.taskId as string,
          params.acceptorId as string,
        );
      case "record_acceptance_result":
        return this.recordAcceptanceFlow(
          params.acceptanceId as string,
          params.results as { verdict: AcceptanceVerdict; findings: string[]; recommendations: string[] },
        );
      case "create_issue":
        return this.taskManager.createIssue(params as unknown as CreateIssueParams);
      case "resolve_issue":
        return this.taskManager.resolveIssue(
          params.issueId as string,
          params.resolution as { resolvedBy: string; summary: string; resolvedAt: number },
        );
      case "summarize_session":
        return this.summarizeSession(
          params.agentId as string,
          params.summary as string,
        );
      case "get_config":
        return this.getConfig();
      case "update_config":
        return this.updateConfig(params.config as MercuryConfig);
      case "kb_read":
        return this.kbRead(params.file as string);
      case "kb_search":
        return this.kbSearch(params.query as string);
      case "kb_list":
        return this.kbList(params.folder as string | undefined);
      case "kb_write":
        return this.kbWrite(params.name as string, params.content as string);
      case "kb_append":
        return this.kbAppend(params.file as string, params.content as string);
      case "get_slash_commands":
        return this.getSlashCommands(params.agentId as string);
      case "set_agent_cwd":
        return this.setAgentCwd(params.agentId as string, params.cwd as string);
      case "refresh_context":
        return this.buildAndInjectContext();
      case "get_context_status":
        return {
          hasContext: this.sharedContext.length > 0,
          contextLength: this.sharedContext.length,
          autoInject: this.projectConfig?.obsidian?.autoInjectContext ?? false,
          contextFiles: this.projectConfig?.obsidian?.contextFiles ?? [],
        };
      case "ping":
        return { pong: true, timestamp: Date.now() };
      default:
        throw new Error(`Unknown method: ${method}`);
    }
  }

  private getAgents(): AgentConfig[] {
    return this.registry.listAgents();
  }

  private setAgentCwd(agentId: string, cwd: string): { ok: true } {
    this.agentCwds.set(agentId, cwd);
    return { ok: true };
  }

  private async startRoleSession(
    agentId: string,
    role?: AgentRole,
    taskName?: string,
    systemPrompt?: string,
  ): Promise<SessionInfo> {
    const adapter = this.registry.getAdapter(agentId);
    const config = this.registry.getConfig(agentId);
    const effectiveRole = role ?? config.roles[0];
    const cwd = this.agentCwds.get(agentId) ?? process.cwd();
    const session = await adapter.startSession(cwd);

    // Enrich session with role and naming convention
    session.role = effectiveRole;
    session.sessionName = `${effectiveRole}-${config.cli}-${taskName ?? "default"}`;

    this.sessions.set(session.sessionId, session);
    const slotKey = makeRoleSlotKey(effectiveRole, agentId);
    this.roleSessions.set(slotKey, session.sessionId);

    // Inject role-specific system prompt (includes shared KB context)
    const rolePrompt =
      systemPrompt ??
      buildRoleSystemPrompt(
        effectiveRole,
        undefined,
        this.sharedContext || undefined,
      );
    try {
      adapter.setSystemPrompt(rolePrompt);
    } catch {
      // Non-critical — some adapters may not support system prompts
    }

    this.bus.emit("agent.session.start", agentId, session.sessionId, {
      role: effectiveRole,
      sessionName: session.sessionName,
    });

    return session;
  }

  private async sendPrompt(
    agentId: string,
    prompt: string,
    images?: ImageAttachment[],
    role?: AgentRole,
    taskName?: string,
  ): Promise<{ sessionId: string }> {
    const adapter = this.registry.getAdapter(agentId);
    const config = this.registry.getConfig(agentId);
    const effectiveRole = role ?? config.roles[0];
    const slotKey = makeRoleSlotKey(effectiveRole, agentId);

    // Auto-start session if none exists for this role slot
    let sessionId = this.roleSessions.get(slotKey);
    if (!sessionId) {
      const session = await this.startRoleSession(agentId, effectiveRole, taskName);
      sessionId = session.sessionId;
    }

    // Validate that the adapter still knows about this session and it's usable.
    let needNewSession = false;
    try {
      const sessionInfo = await adapter.resumeSession(sessionId);
      if (sessionInfo.status === "completed" || sessionInfo.status === "overflow") {
        needNewSession = true;
      }
    } catch {
      needNewSession = true;
    }
    if (needNewSession) {
      this.sessions.delete(sessionId);
      this.roleSessions.delete(slotKey);
      const session = await this.startRoleSession(agentId, effectiveRole, taskName);
      sessionId = session.sessionId;
    }

    this.bus.emit("agent.message.send", agentId, sessionId, {
      prompt: prompt.slice(0, 200),
      hasImages: images ? images.length : 0,
      role: effectiveRole,
    });

    // Stream messages asynchronously
    this.streamMessages(adapter, agentId, sessionId, prompt, images);

    return { sessionId };
  }

  private async streamMessages(
    adapter: ReturnType<AgentRegistry["getAdapter"]>,
    agentId: string,
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
  ): Promise<void> {
    try {
      for await (const message of adapter.sendPrompt(sessionId, prompt, images)) {
        this.bus.emit("agent.message.receive", agentId, sessionId, {
          contentPreview: message.content.slice(0, 200),
        });

        this.transport.sendNotification("agent_message", {
          agentId,
          sessionId,
          message: {
            role: message.role,
            content: message.content,
            timestamp: message.timestamp,
            images: message.images,
            metadata: message.metadata,
          },
        });
      }

      // Signal stream complete
      this.transport.sendNotification("agent_stream_end", {
        agentId,
        sessionId,
      });
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.bus.emit("agent.error", agentId, sessionId, { error: errorMsg });
      this.transport.sendNotification("agent_error", {
        agentId,
        sessionId,
        error: errorMsg,
      });

      // Clean up failed session so next sendPrompt auto-creates a fresh one
      this.sessions.delete(sessionId);
      for (const [key, sid] of this.roleSessions) {
        if (sid === sessionId) {
          this.roleSessions.delete(key);
          break;
        }
      }
      // Also end it in the adapter to avoid stale references
      try { await adapter.endSession(sessionId); } catch { /* best-effort */ }
    }
  }

  private async stopSession(
    agentId: string,
    sessionId: string,
  ): Promise<void> {
    const adapter = this.registry.getAdapter(agentId);
    await adapter.endSession(sessionId);

    this.sessions.delete(sessionId);
    for (const [key, sid] of this.roleSessions) {
      if (sid === sessionId) {
        this.roleSessions.delete(key);
        break;
      }
    }

    this.bus.emit("agent.session.end", agentId, sessionId, {});
  }

  private configureAgent(config: AgentConfig): { ok: true } {
    this.registry.register(config);
    return { ok: true };
  }

  private async dispatchTask(
    fromAgentId: string,
    toAgentId: string,
    prompt: string,
  ): Promise<{ sessionId: string; taskId: string }> {
    // Find any active session for fromAgent
    let fromSessionId = "orchestrator";
    for (const [key, sid] of this.roleSessions) {
      if (key.endsWith(`:${fromAgentId}`)) {
        fromSessionId = sid;
        break;
      }
    }
    const taskId = `TASK-${Date.now()}`;

    this.bus.emit(
      "orchestrator.task.dispatch",
      fromAgentId,
      fromSessionId,
      {
        taskId,
        assignedTo: toAgentId,
        prompt: prompt.slice(0, 200),
      },
    );

    // Start sub-agent session and send prompt
    const result = await this.sendPrompt(toAgentId, prompt);

    return { sessionId: result.sessionId, taskId };
  }

  // ─── Bundle-Aware Task Dispatch ───

  private async dispatchBundleTask(
    taskId: string,
  ): Promise<{ sessionId: string; taskId: string }> {
    const task = this.taskManager.getTask(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    // Store originator session for callback routing
    const mainAgentId = this.findMainAgentId();
    if (mainAgentId) {
      const mainSlot = makeRoleSlotKey("main", mainAgentId);
      this.taskManager.updateTaskField(
        taskId,
        "originatorSessionId",
        this.roleSessions.get(mainSlot),
      );
    }

    // Build dev prompt from bundle (optionally include KB context)
    let kbContext: string | undefined;
    if (this.kb?.isEnabled() && task.readScope.requiredDocs.length > 0) {
      try {
        kbContext = await this.kb.buildContext(task.readScope.requiredDocs);
      } catch {
        // KB context is best-effort
      }
    }

    const prompt = buildDevPrompt(task, kbContext);

    // Transition: drafted → dispatched → in_progress
    if (task.status === "drafted") {
      this.taskManager.transitionTask(taskId, "dispatched", "orchestrator");
    }

    // Start role-scoped session for assigned agent
    const devRolePrompt = buildRoleSystemPrompt("dev", task, this.sharedContext || undefined);
    const session = await this.startRoleSession(task.assignedTo, "dev", task.title, devRolePrompt);
    this.taskManager.bindSession(taskId, session.sessionId);

    // Transition to in_progress (only if not already there)
    if (task.status === "dispatched") {
      this.taskManager.transitionTask(taskId, "in_progress", task.assignedTo);
    }

    // Send the prompt with role context
    await this.sendPrompt(task.assignedTo, prompt, undefined, "dev", task.title);

    return { sessionId: session.sessionId, taskId };
  }

  /** Find the agent currently assigned to the "main" role. */
  private findMainAgentId(): string | undefined {
    for (const [key] of this.roleSessions) {
      if (key.startsWith("main:")) {
        return key.slice(5);
      }
    }
    // Fallback: find first agent with main role in config
    return this.registry.listAgents().find((a) => a.roles.includes("main"))?.id;
  }

  private async createAcceptanceFlow(
    taskId: string,
    acceptorId: string,
  ): Promise<{ acceptanceId: string; sessionId: string }> {
    const task = this.taskManager.getTask(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    // Transition to acceptance (must come from main_review now)
    if (task.status === "main_review") {
      this.taskManager.transitionTask(taskId, "acceptance", "orchestrator");
    }

    // Create acceptance bundle
    const acceptance = this.taskManager.createAcceptance(taskId, acceptorId);

    // Build blind acceptance prompt (filtered — no dev narrative)
    const prompt = buildAcceptancePrompt(task, acceptance);
    const acceptanceRolePrompt = buildAcceptanceRolePrompt(
      task,
      acceptance,
      this.sharedContext || undefined,
    );
    const session = await this.startRoleSession(
      acceptorId,
      "acceptance",
      task.title,
      acceptanceRolePrompt,
    );
    this.taskManager.bindSession(taskId, session.sessionId);

    await this.sendPrompt(acceptorId, prompt, undefined, "acceptance", task.title);

    return { acceptanceId: acceptance.acceptanceId, sessionId: session.sessionId };
  }

  private async recordAcceptanceFlow(
    acceptanceId: string,
    results: { verdict: AcceptanceVerdict; findings: string[]; recommendations: string[] },
  ): Promise<{ verdict: AcceptanceVerdict; reworkTriggered: boolean; newSession: boolean }> {
    const acceptance = this.taskManager.recordAcceptanceResult(acceptanceId, results);
    const task = this.taskManager.getTask(acceptance.linkedTaskId);
    if (!task) throw new Error(`Linked task not found: ${acceptance.linkedTaskId}`);

    if (results.verdict === "pass") {
      // Acceptance passed → verified → closed
      this.taskManager.transitionTask(task.taskId, "verified", acceptance.acceptor);
      this.taskManager.transitionTask(task.taskId, "closed", "orchestrator");

      // Callback to Main Agent
      this.bus.emit(
        "orchestrator.task.callback",
        acceptance.acceptor,
        "orchestrator",
        {
          taskId: task.taskId,
          originatorSessionId: task.originatorSessionId,
          verdict: "pass",
        },
      );

      return { verdict: "pass", reworkTriggered: false, newSession: false };
    }

    if (results.verdict === "fail" || results.verdict === "partial") {
      // Trigger rework with full context
      const { newSession } = this.taskManager.triggerRework(
        task.taskId,
        results.findings.join("\n"),
        acceptanceId,
        results.findings,
      );

      if (!newSession) {
        // Send rework prompt to existing dev session
        const reworkPrompt = buildReworkPrompt(task, acceptance);
        await this.sendPrompt(task.assignedTo, reworkPrompt);
      }

      return { verdict: results.verdict, reworkTriggered: true, newSession };
    }

    // verdict === "blocked" → create issue (caller handles specifics)
    if (results.verdict === "blocked") {
      this.taskManager.transitionTask(task.taskId, "blocked", acceptance.acceptor);
      return { verdict: "blocked", reworkTriggered: false, newSession: false };
    }

    return { verdict: results.verdict, reworkTriggered: false, newSession: false };
  }

  // ─── Two-Stage Verification ───

  /**
   * Record receipt → scope validation → auto-trigger Main Agent review.
   */
  private async recordReceiptAndTriggerReview(
    taskId: string,
    receipt: ImplementationReceipt,
  ): Promise<TaskBundle> {
    const task = this.taskManager.recordReceipt(taskId, receipt);

    // Auto-trigger main review step
    await this.mainReviewStep(taskId);

    return task;
  }

  /**
   * Main Agent quick review step: implementation_done → main_review.
   * Sends review prompt to Main Agent session (originator or new).
   */
  private async mainReviewStep(taskId: string): Promise<void> {
    const task = this.taskManager.getTask(taskId);
    if (!task || task.status !== "implementation_done") return;

    // Transition to main_review
    this.taskManager.transitionTask(taskId, "main_review", "orchestrator");

    this.bus.emit(
      "orchestrator.task.main_review",
      "orchestrator",
      task.originatorSessionId ?? "orchestrator",
      { taskId, title: task.title },
    );

    // Send review prompt to Main Agent
    const mainAgentId = this.findMainAgentId();
    if (!mainAgentId) return;

    const reviewPrompt = buildMainReviewPrompt(task);
    await this.sendPrompt(mainAgentId, reviewPrompt, undefined, "main", task.title);
  }

  /**
   * Handle Main Agent's review decision.
   * APPROVE_FOR_ACCEPTANCE → create acceptance flow.
   * SEND_BACK → trigger rework.
   */
  private async handleMainReviewResult(
    taskId: string,
    decision: string,
    reason?: string,
    acceptorId?: string,
  ): Promise<{ decision: string; nextAction: string }> {
    const task = this.taskManager.getTask(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    if (decision === "APPROVE_FOR_ACCEPTANCE") {
      // Find or use provided acceptor
      const effectiveAcceptorId = acceptorId ?? this.findAcceptorAgentId();
      if (!effectiveAcceptorId) {
        throw new Error("No acceptance agent available. Configure an agent with acceptance role.");
      }
      await this.createAcceptanceFlow(taskId, effectiveAcceptorId);
      return { decision, nextAction: "acceptance_created" };
    }

    if (decision === "SEND_BACK") {
      const { newSession } = this.taskManager.triggerRework(
        taskId,
        reason ?? "Main Agent review: sent back for rework",
      );

      // Send rework directive to dev agent
      const reworkPrompt = `# Rework Required [${task.taskId}]\n\nMain Agent review returned this task for rework.\n\n**Reason:** ${reason ?? "Unspecified"}\n\nPlease address and resubmit.`;
      if (newSession) {
        const devRolePrompt = buildRoleSystemPrompt("dev", task, this.sharedContext || undefined);
        const session = await this.startRoleSession(
          task.assignedTo,
          "dev",
          task.title,
          devRolePrompt,
        );
        this.taskManager.bindSession(taskId, session.sessionId);
      }
      await this.sendPrompt(task.assignedTo, reworkPrompt, undefined, "dev", task.title);

      return { decision, nextAction: "rework_triggered" };
    }

    throw new Error(`Invalid review decision: "${decision}". Expected APPROVE_FOR_ACCEPTANCE or SEND_BACK.`);
  }

  /** Find an agent with the "acceptance" role. */
  private findAcceptorAgentId(): string | undefined {
    return this.registry.listAgents().find((a) => a.roles.includes("acceptance"))?.id;
  }

  private async summarizeSession(
    agentId: string,
    summary: string,
  ): Promise<{ newSessionId: string }> {
    // Find any active session for this agent and preserve its role
    let currentSessionId: string | undefined;
    let currentRole: AgentRole | undefined;
    for (const [key, sid] of this.roleSessions) {
      if (key.endsWith(`:${agentId}`)) {
        currentSessionId = sid;
        const colonIdx = key.indexOf(":");
        currentRole = key.slice(0, colonIdx) as AgentRole;
        break;
      }
    }
    if (currentSessionId) {
      this.bus.emit("orchestrator.session.summarize", agentId, currentSessionId, { summary });
      await this.stopSession(agentId, currentSessionId);
    }

    // Start fresh session with same role
    const newSession = await this.startRoleSession(agentId, currentRole);
    return { newSessionId: newSession.sessionId };
  }

  // ─── Slash Commands ───

  private getSlashCommands(agentId: string): SlashCommand[] {
    const adapter = this.registry.getAdapter(agentId);
    return adapter.getSlashCommands();
  }

  // ─── Config RPC ───

  private getConfig(): MercuryConfig | null {
    return this.projectConfig;
  }

  private async updateConfig(config: MercuryConfig): Promise<{ ok: true }> {
    const prevAutoInject = this.projectConfig?.obsidian?.autoInjectContext;
    const prevContextFiles = this.projectConfig?.obsidian?.contextFiles?.join(",");

    this.projectConfig = config;

    // Hot-reload agents from new config
    const currentIds = new Set(this.registry.listAgents().map((a) => a.id));
    const newIds = new Set(config.agents.map((a) => a.id));

    // Remove agents no longer in config
    for (const id of currentIds) {
      if (!newIds.has(id)) this.registry.unregister(id);
    }

    // Add/update agents
    for (const agentConfig of config.agents) {
      this.registry.register(agentConfig);
    }

    // Re-apply shared context to all (potentially new) adapters.
    // Adapters are recreated on register(), so they lose systemPrompt.
    // Always re-inject if we have context, regardless of obsidian config changes.
    const newAutoInject = config.obsidian?.autoInjectContext;
    const newContextFiles = config.obsidian?.contextFiles?.join(",");
    const obsidianChanged = newAutoInject !== prevAutoInject || newContextFiles !== prevContextFiles;

    if (obsidianChanged && !newAutoInject && this.sharedContext) {
      // autoInject was disabled — clear shared context
      this.sharedContext = "";
    }
    // Note: role-specific prompts are injected per-session via startRoleSession(),
    // so no need to re-inject into all adapters here.

    // Rebuild context from KB if obsidian settings changed and autoInject is on
    if (obsidianChanged && newAutoInject) {
      await this.buildAndInjectContext();
    }

    return { ok: true };
  }

  // ─── Knowledge Base RPC (optional) ───

  private requireKb(): NonNullable<typeof this.kb> {
    if (!this.kb || !this.kb.isEnabled()) {
      throw new Error("Knowledge service is not enabled. Configure obsidian in mercury.config.json.");
    }
    return this.kb;
  }

  private async kbRead(file: string): Promise<{ content: string }> {
    const content = await this.requireKb().read(file);
    return { content };
  }

  private async kbSearch(query: string) {
    return this.requireKb().search(query);
  }

  private async kbList(folder?: string) {
    return this.requireKb().list(folder);
  }

  private async kbWrite(name: string, content: string): Promise<{ ok: true }> {
    await this.requireKb().write(name, content);
    return { ok: true };
  }

  private async kbAppend(file: string, content: string): Promise<{ ok: true }> {
    await this.requireKb().append(file, content);
    return { ok: true };
  }
}
