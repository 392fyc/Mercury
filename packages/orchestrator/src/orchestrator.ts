/**
 * Mercury Orchestrator — core class managing agent sessions, prompts, and event flow.
 */

import { EventBus } from "@mercury/core";
import type {
  AgentConfig,
  SessionInfo,
  AgentMessage,
  ImageAttachment,
  MercuryConfig,
  AcceptanceVerdict,
  ImplementationReceipt,
  SlashCommand,
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
} from "./task-manager.js";
import type { CreateTaskParams, CreateIssueParams } from "./task-manager.js";
import { TaskPersistenceKB } from "./task-persistence-kb.js";

export class Orchestrator {
  private bus: EventBus;
  private registry: AgentRegistry;
  private transport: RpcTransport;
  private sessions = new Map<string, SessionInfo>();
  private agentSessions = new Map<string, string>(); // agentId → active sessionId
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

      // Inject into all registered adapters
      const agents = this.registry.listAgents();
      for (const agent of agents) {
        try {
          const adapter = this.registry.getAdapter(agent.id);
          adapter.setSystemPrompt(context);
        } catch {
          // Adapter instantiation may fail — non-critical
        }
      }

      this.transport.sendNotification("log", {
        message: `[context] Shared context injected into ${agents.length} agents (${context.length} chars from ${obsConfig.contextFiles.length} files)`,
      });

      return { injected: true, agentCount: agents.length, contextLength: context.length };
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
        return this.startSession(params.agentId as string);
      case "send_prompt":
        return this.sendPrompt(
          params.agentId as string,
          params.prompt as string,
          (params.images as ImageAttachment[] | null) ?? undefined,
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
        return this.taskManager.getTask(params.taskId as string) ?? null;
      case "list_tasks":
        return this.taskManager.listTasks(params as { status?: TaskStatus; assignedTo?: string });
      case "record_receipt":
        return this.taskManager.recordReceipt(
          params.taskId as string,
          params.receipt as ImplementationReceipt,
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

  private async startSession(agentId: string): Promise<SessionInfo> {
    const adapter = this.registry.getAdapter(agentId);
    const cwd = this.agentCwds.get(agentId) ?? process.cwd();
    const session = await adapter.startSession(cwd);

    this.sessions.set(session.sessionId, session);
    this.agentSessions.set(agentId, session.sessionId);

    this.bus.emit("agent.session.start", agentId, session.sessionId, {
      role: this.registry.getConfig(agentId).role,
    });

    return session;
  }

  private async sendPrompt(
    agentId: string,
    prompt: string,
    images?: ImageAttachment[],
  ): Promise<{ sessionId: string }> {
    const adapter = this.registry.getAdapter(agentId);

    // Auto-start session if none exists
    let sessionId = this.agentSessions.get(agentId);
    if (!sessionId) {
      const session = await this.startSession(agentId);
      sessionId = session.sessionId;
    }

    // Validate that the adapter still knows about this session and it's usable.
    // Sessions can become stale if the adapter was recreated (e.g. config update),
    // if a previous stream errored out, or if the user ran /clear or /exit.
    let needNewSession = false;
    try {
      const sessionInfo = await adapter.resumeSession(sessionId);
      // If session was ended (e.g. /clear, /exit), create a fresh one
      if (sessionInfo.status === "completed" || sessionInfo.status === "overflow") {
        needNewSession = true;
      }
    } catch {
      needNewSession = true;
    }
    if (needNewSession) {
      this.sessions.delete(sessionId);
      this.agentSessions.delete(agentId);
      const session = await this.startSession(agentId);
      sessionId = session.sessionId;
    }

    this.bus.emit("agent.message.send", agentId, sessionId, {
      prompt: prompt.slice(0, 200),
      hasImages: images ? images.length : 0,
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
      if (this.agentSessions.get(agentId) === sessionId) {
        this.agentSessions.delete(agentId);
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
    if (this.agentSessions.get(agentId) === sessionId) {
      this.agentSessions.delete(agentId);
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
    const fromSessionId = this.agentSessions.get(fromAgentId) ?? "orchestrator";
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

    // Start session for assigned agent
    const session = await this.startSession(task.assignedTo);
    this.taskManager.bindSession(taskId, session.sessionId);

    // Transition to in_progress (only if not already there)
    if (task.status === "dispatched") {
      this.taskManager.transitionTask(taskId, "in_progress", task.assignedTo);
    }

    // Send the prompt
    await this.sendPrompt(task.assignedTo, prompt);

    return { sessionId: session.sessionId, taskId };
  }

  private async createAcceptanceFlow(
    taskId: string,
    acceptorId: string,
  ): Promise<{ acceptanceId: string; sessionId: string }> {
    const task = this.taskManager.getTask(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    // Transition to acceptance if currently implementation_done
    if (task.status === "implementation_done") {
      this.taskManager.transitionTask(taskId, "acceptance", "orchestrator");
    }

    // Create acceptance bundle
    const acceptance = this.taskManager.createAcceptance(taskId, acceptorId);

    // Build acceptance prompt and dispatch to acceptor agent
    const prompt = buildAcceptancePrompt(task, acceptance);
    const session = await this.startSession(acceptorId);
    this.taskManager.bindSession(taskId, session.sessionId);

    await this.sendPrompt(acceptorId, prompt);

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
      return { verdict: "pass", reworkTriggered: false, newSession: false };
    }

    if (results.verdict === "fail" || results.verdict === "partial") {
      // Trigger rework
      const { newSession } = this.taskManager.triggerRework(
        task.taskId,
        results.findings.join("\n"),
      );

      if (!newSession) {
        // Send rework prompt to existing dev session
        const reworkPrompt = buildReworkPrompt(task, acceptance);
        await this.sendPrompt(task.assignedTo, reworkPrompt);
      }
      // If newSession is true, caller decides whether to start new session or switch agent

      return { verdict: results.verdict, reworkTriggered: true, newSession };
    }

    // verdict === "blocked" → create issue (caller handles specifics)
    if (results.verdict === "blocked") {
      this.taskManager.transitionTask(task.taskId, "blocked", acceptance.acceptor);
      return { verdict: "blocked", reworkTriggered: false, newSession: false };
    }

    return { verdict: results.verdict, reworkTriggered: false, newSession: false };
  }

  private async summarizeSession(
    agentId: string,
    summary: string,
  ): Promise<{ newSessionId: string }> {
    // End current session
    const currentSessionId = this.agentSessions.get(agentId);
    if (currentSessionId) {
      this.bus.emit("orchestrator.session.summarize", agentId, currentSessionId, { summary });
      await this.stopSession(agentId, currentSessionId);
    }

    // Start fresh session
    const newSession = await this.startSession(agentId);
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
      for (const agent of this.registry.listAgents()) {
        try {
          this.registry.getAdapter(agent.id).setSystemPrompt("");
        } catch { /* non-critical */ }
      }
    } else if (this.sharedContext) {
      // Re-apply existing shared context to fresh adapter instances
      for (const agent of this.registry.listAgents()) {
        try {
          this.registry.getAdapter(agent.id).setSystemPrompt(this.sharedContext);
        } catch { /* non-critical */ }
      }
    }

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
