/**
 * Mercury Orchestrator — core class managing agent sessions, prompts, and event flow.
 */

import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { copyFile, mkdir, readdir, writeFile, rename, unlink } from "node:fs/promises";
import { dirname, join, relative, resolve } from "node:path";
import { EventBus, isStreamingEvent, makeRoleSlotKey } from "@mercury/core";
import type {
  AgentConfig,
  AgentSendHooks,
  AgentRole,
  AgentStreamingEvent,
  ApprovalDecision,
  ApprovalMode,
  ApprovalRequest,
  ApprovalRequestStatus,
  ImageAttachment,
  MercuryConfig,
  AgentMessage,
  AcceptanceVerdict,
  ImplementationReceipt,
  ObsidianConfig,
  RoleSlotKey,
  SessionInfo,
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
  buildReferencePrompt,
  buildAcceptancePrompt,
  buildReworkPrompt,
  buildMainReviewPrompt,
} from "./task-manager.js";
import type { CreateTaskParams, CreateIssueParams } from "./task-manager.js";
import { TaskPersistenceKB } from "./task-persistence-kb.js";
import { TaskPersistenceSqlite } from "./task-persistence-sqlite.js";
import { TaskPersistenceDual } from "./task-persistence-dual.js";
import {
  buildRoleSystemPrompt,
  buildAcceptanceRolePrompt,
} from "./role-prompt-builder.js";
import { SessionPersistence } from "./session-persistence.js";
import type { PersistedSessionState } from "./session-persistence.js";
import { TranscriptPersistence } from "./transcript-persistence.js";
import type { TranscriptMessage } from "./transcript-persistence.js";
import { installRTKCommandWrapper, isRTKAvailable } from "./rtk-wrapper.js";

type NativeSessionBridge = {
  listNativeSessions?: (cwd?: string) => Promise<SessionInfo[]>;
  getNativeSessionInfo?: (sessionId: string, cwd?: string) => Promise<SessionInfo | null>;
  readNativeMessages?: (sessionId: string) => Promise<AgentMessage[]>;
  setSessionName?: (sessionId: string, name: string) => Promise<void>;
};

type StreamCompletion = {
  completed: boolean;
  lastAssistantMessage?: AgentMessage;
};

type TaskMainReview = NonNullable<TaskBundle["mainReview"]>;
type PreCheckResult = TaskMainReview["preChecks"][number];
type StructuredReviewResult = NonNullable<TaskMainReview["result"]>;
type ReviewFinding = StructuredReviewResult["findings"][number];
type ReviewConfig = NonNullable<TaskBundle["reviewConfig"]>;
type PreCheckConfig = NonNullable<ReviewConfig["preChecks"]>[number];
type ParsedMainReviewDecision = {
  decision: StructuredReviewResult["decision"];
  reason?: string;
  structured: StructuredReviewResult;
};

const ROLE_CONTEXT_ROLES = ["main", "dev", "acceptance"] as const;
type RoleContextKey = (typeof ROLE_CONTEXT_ROLES)[number];

export class Orchestrator {
  private static readonly APPROVAL_TIMEOUT_MS = 5 * 60 * 1000;
  private static readonly SESSION_HISTORY_TTL_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
  private static readonly DEFAULT_PRECHECK_TIMEOUT_MS = 5 * 60 * 1000;
  private static readonly DEFAULT_REVIEW_DIFF_MAX_CHARS = 12000;
  /** Token usage threshold (70%) at which context checkpoint + handoff is triggered. */
  private static readonly TOKEN_CHECKPOINT_THRESHOLD = 0.7;
  /** Base delay (ms) for exponential backoff on dispatch retries: min(300*2^attempt, 30000)+jitter. */
  private static readonly DISPATCH_RETRY_BASE_MS = 300;
  /** Max delay cap (ms) for dispatch retry backoff. */
  private static readonly DISPATCH_RETRY_MAX_MS = 30000;
  private bus: EventBus;
  private registry: AgentRegistry;
  private transport: RpcTransport;
  private sessions = new Map<string, SessionInfo>();
  private roleSessions = new Map<RoleSlotKey, string>(); // roleSlotKey → active sessionId
  private agentCwds = new Map<string, string>(); // agentId → working directory
  private approvalMode: ApprovalMode = "main_agent_review";
  private approvalRequests = new Map<string, ApprovalRequest>();
  private approvalWaiters = new Map<
    string,
    {
      resolve: (decision: ApprovalDecision) => void;
      timeout: ReturnType<typeof setTimeout>;
    }
  >();
  private kb: KnowledgeService | null = null;
  private sqliteDb: TaskPersistenceSqlite | null = null;
  private projectConfig: MercuryConfig | null = null;
  private configFilePath: string | null = null;
  private taskManager: TaskManager;
  private persistence: SessionPersistence | null = null;
  private transcripts: TranscriptPersistence | null = null;
  private persistDebounceTimer: ReturnType<typeof setTimeout> | null = null;
  /** Cached global context built from obsidian.contextFiles. */
  private sharedContext: string = "";
  /** Cached role-only context built from obsidian.roleContextFiles[role]. */
  private roleContexts: Partial<Record<RoleContextKey, string>> = {};

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

  /** Inject optional knowledge service + wire up task persistence (with optional SQLite dual-write). */
  setKnowledgeService(kb: KnowledgeService) {
    this.kb = kb;
    const logFn = (msg: string) => this.transport.sendNotification("log", { message: msg });

    // KB persistence (always available as secondary / fallback)
    const kbPersistence = new TaskPersistenceKB(kb, logFn);

    // Try to initialize SQLite as primary persistence with dual-write to KB
    try {
      const mercuryDir = join(this.getProjectRoot(), ".mercury");
      try { mkdirSync(mercuryDir, { recursive: true }); } catch { /* already exists */ }
      const dbPath = join(mercuryDir, "mercury.db");
      this.sqliteDb = new TaskPersistenceSqlite(dbPath, logFn);
      const dualPersistence = new TaskPersistenceDual(this.sqliteDb, kbPersistence, logFn);
      this.taskManager.setPersistence(dualPersistence);
      logFn("[orchestrator] Persistence: SQLite (primary) + KB (sync)");
    } catch (err) {
      // Fallback to KB-only if SQLite fails to initialize
      logFn(`[orchestrator] SQLite init failed, falling back to KB-only: ${err instanceof Error ? err.message : err}`);
      this.taskManager.setPersistence(kbPersistence);
    }

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

  /** Rehydrate task state from KB persistence + build shared context + restore sessions. */
  async init(): Promise<void> {
    this.syncRTKCommandWrapper();
    await this.validateRTKConfiguration();

    // If SQLite is empty and KB has data, do one-time migration
    if (this.sqliteDb?.isEmpty() && this.kb) {
      try {
        const logFn = (msg: string) => this.transport.sendNotification("log", { message: msg });
        const kbOnly = new TaskPersistenceKB(this.kb, logFn);
        const kbData = await kbOnly.loadAll();
        if (kbData.tasks.length > 0 || kbData.issues.length > 0 || kbData.acceptances.length > 0) {
          logFn(`[orchestrator] Migrating KB data to SQLite (${kbData.tasks.length} tasks, ${kbData.issues.length} issues, ${kbData.acceptances.length} acceptances)`);
          this.sqliteDb.importFromKB(kbData);
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.transport.sendNotification("log", { message: `[orchestrator] KB→SQLite migration failed: ${msg}` });
      }
    }

    await this.taskManager.init();
    await this.restoreSessions();
    // Build and inject shared context from KB if autoInjectContext is enabled
    await this.buildAndInjectContext();
  }

  /** Store project config for get_config/update_config RPC. */
  setProjectConfig(config: MercuryConfig, configFilePath?: string | null) {
    this.projectConfig = config;
    this.syncRTKCommandWrapper(config);
    if (configFilePath !== undefined) {
      this.configFilePath = configFilePath ?? null;
    }
  }

  /** Enable session persistence to disk. */
  setPersistencePath(basePath: string): void {
    this.persistence = new SessionPersistence(basePath);
    this.transcripts = new TranscriptPersistence(basePath);
  }

  private hashPrompt(prompt: string): string {
    return createHash("sha256").update(prompt).digest("hex");
  }

  private getProjectRoot(): string {
    if (this.configFilePath) {
      return dirname(this.configFilePath);
    }
    if (this.projectConfig?.workDir) {
      return resolve(this.projectConfig.workDir);
    }
    return process.cwd();
  }

  private buildSystemRolePrompt(role: AgentRole, task?: TaskBundle): string {
    return buildRoleSystemPrompt(
      role,
      task,
      this.sharedContext || undefined,
      this.getRoleSpecificContext(role),
      undefined, // instructions loaded from YAML via basePath
      this.getProjectRoot(),
    );
  }

  private clearContextCache(): void {
    this.sharedContext = "";
    this.roleContexts = {};
  }

  private getRoleSpecificFiles(
    role: RoleContextKey,
    obsConfig: ObsidianConfig | null | undefined = this.projectConfig?.obsidian,
  ): string[] {
    return obsConfig?.roleContextFiles?.[role] ?? [];
  }

  private getRoleSpecificContext(role: AgentRole): string | undefined {
    if (ROLE_CONTEXT_ROLES.includes(role as RoleContextKey)) {
      const roleSpecificContext = this.roleContexts[role as RoleContextKey];
      if (roleSpecificContext) {
        return roleSpecificContext;
      }
    }
    return undefined;
  }

  private getContextCacheLength(): number {
    return this.sharedContext.length +
      Object.values(this.roleContexts).reduce((total, context) => total + (context?.length ?? 0), 0);
  }

  private getDefaultRole(agentId: string | undefined): AgentRole | undefined {
    if (!agentId) return undefined;
    try {
      return this.registry.getConfig(agentId).roles[0];
    } catch {
      return undefined;
    }
  }

  private getSessionRole(
    info: SessionInfo | undefined,
    fallbackAgentId?: string,
  ): AgentRole | undefined {
    return info?.frozenRole ?? info?.role ?? this.getDefaultRole(info?.agentId ?? fallbackAgentId);
  }

  private normalizeCwd(cwd: string): string {
    const normalized = resolve(cwd).replace(/[\\/]+$/, "");
    return process.platform === "win32" ? normalized.toLowerCase() : normalized;
  }

  private isRelatedCwd(sessionCwd: string | undefined, expectedCwd: string): boolean {
    if (!sessionCwd) return false;
    const normalizedSession = this.normalizeCwd(sessionCwd);
    const normalizedExpected = this.normalizeCwd(expectedCwd);
    if (normalizedSession === normalizedExpected) {
      return true;
    }
    const sessionRelative = relative(normalizedExpected, normalizedSession);
    if (sessionRelative && !sessionRelative.startsWith("..") && !sessionRelative.includes(":")) {
      return true;
    }
    const expectedRelative = relative(normalizedSession, normalizedExpected);
    return (
      Boolean(expectedRelative) &&
      !expectedRelative.startsWith("..") &&
      !expectedRelative.includes(":")
    );
  }

  private async listNativeSessionsWithFallback(
    agentId: string,
    nativeAdapter: NativeSessionBridge,
  ): Promise<SessionInfo[]> {
    if (!nativeAdapter.listNativeSessions) {
      return [];
    }
    const agentCwd = this.agentCwds.get(agentId);
    const scopedSessions = await nativeAdapter.listNativeSessions(agentCwd);
    if (scopedSessions.length > 0 || !agentCwd) {
      return scopedSessions;
    }
    const allSessions = await nativeAdapter.listNativeSessions();
    return allSessions.filter((info) => this.isRelatedCwd(info.cwd, agentCwd));
  }

  private isTaskScopedPrompt(info: SessionInfo): boolean {
    const prompt = info.frozenSystemPrompt ?? "";
    return prompt.includes("## Task Scope") || prompt.includes("## Review Scope");
  }

  private getComparableRolePromptHash(info: SessionInfo): string | undefined {
    if (info.baseRolePromptHash) {
      return info.baseRolePromptHash;
    }
    if (this.isTaskScopedPrompt(info)) {
      return undefined;
    }
    return info.promptHash;
  }

  private getCurrentPromptHash(info: SessionInfo): string | undefined {
    if (!info.frozenRole || !this.getComparableRolePromptHash(info)) {
      return undefined;
    }
    const currentPrompt = this.buildSystemRolePrompt(info.frozenRole);
    return this.hashPrompt(currentPrompt);
  }

  private getLegacyRoleConfigState(
    info: SessionInfo,
  ): { currentPromptHash?: string; legacyRoleConfig: boolean } {
    const comparablePromptHash = this.getComparableRolePromptHash(info);
    const currentPromptHash = this.getCurrentPromptHash(info);
    return {
      currentPromptHash,
      legacyRoleConfig:
        Boolean(comparablePromptHash) &&
        Boolean(currentPromptHash) &&
        comparablePromptHash !== currentPromptHash,
    };
  }

  private appendTranscriptMessage(sessionId: string, message: TranscriptMessage): void {
    if (!this.transcripts) return;
    try {
      this.transcripts.append(sessionId, message);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      this.transport.sendNotification("log", {
        message: `[transcript] append failed for ${sessionId}: ${msg}`,
      });
    }
  }

  private saveStateToDisk(): void {
    if (!this.persistence) return;

    // Merge: preserve completed sessions from disk that may not be in memory
    const SESSION_HISTORY_TTL_MS = Orchestrator.SESSION_HISTORY_TTL_MS;
    const now = Date.now();
    const mergedSessions: Record<string, SessionInfo> = {};

    // First, load existing persisted completed sessions
    const existing = this.persistence.load();
    if (existing) {
      for (const [sid, info] of Object.entries(existing.sessions)) {
        if (
          (info.status === "completed" || info.status === "overflow") &&
          now - info.lastActiveAt < SESSION_HISTORY_TTL_MS
        ) {
          mergedSessions[sid] = info;
        }
      }
    }

    // Then overlay current in-memory sessions (overwrite if same sid)
    for (const [sid, info] of this.sessions) {
      if (
        (info.status === "completed" || info.status === "overflow") &&
        now - info.lastActiveAt >= SESSION_HISTORY_TTL_MS
      ) {
        continue; // TTL expired — drop
      }
      mergedSessions[sid] = info;
    }

    const state: PersistedSessionState = {
      roleSessions: Object.fromEntries(this.roleSessions),
      sessions: mergedSessions,
      agentCwds: Object.fromEntries(this.agentCwds),
      approvalMode: this.approvalMode,
      approvalRequests: Object.fromEntries(this.approvalRequests),
      savedAt: now,
    };
    this.persistence.save(state);
  }

  /** Debounced save of session state to disk (500ms trailing). */
  private persistState(immediate = false): void {
    if (!this.persistence) return;
    if (this.persistDebounceTimer) clearTimeout(this.persistDebounceTimer);
    if (immediate) {
      try {
        this.saveStateToDisk();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.transport.sendNotification("log", { message: `[persist] save failed: ${msg}` });
      }
      return;
    }

    this.persistDebounceTimer = setTimeout(() => {
      try {
        this.saveStateToDisk();
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        this.transport.sendNotification("log", { message: `[persist] save failed: ${msg}` });
      }
    }, 500);
  }

  /** Restore sessions from disk on startup. */
  private async restoreSessions(): Promise<void> {
    if (!this.persistence) return;
    const state = this.persistence.load();
    if (!state) return;

    this.approvalMode = state.approvalMode ?? "main_agent_review";

    let approvalsChanged = false;
    for (const [requestId, request] of Object.entries(state.approvalRequests ?? {})) {
      if (request.status === "pending") {
        this.approvalRequests.set(requestId, {
          ...request,
          status: "cancelled",
          resolvedAt: Date.now(),
          decisionBy: "system",
          decisionReason: "Mercury restarted while waiting for approval",
        });
        approvalsChanged = true;
        continue;
      }
      this.approvalRequests.set(requestId, request);
    }

    // Restore agentCwds
    for (const [agentId, cwd] of Object.entries(state.agentCwds)) {
      this.agentCwds.set(agentId, cwd);
    }

    // Restore sessions: keep completed/overflow for history, resume the rest
    const SESSION_HISTORY_TTL_MS = Orchestrator.SESSION_HISTORY_TTL_MS;
    const now = Date.now();
    for (const [sessionId, info] of Object.entries(state.sessions)) {
      if (info.status === "completed" || info.status === "overflow") {
        // Preserve terminal sessions for history (with TTL cleanup)
        if (now - info.lastActiveAt < SESSION_HISTORY_TTL_MS) {
          this.sessions.set(sessionId, info);
        }
        continue;
      }
      if (!info.frozenRole) {
        continue;
      }
      try {
        const adapter = this.registry.getAdapter(info.agentId);
        const resumed = await adapter.resumeSession(
          sessionId,
          info,
          info.cwd ?? state.agentCwds[info.agentId],
        );
        if (resumed.status === "completed" || resumed.status === "overflow") {
          // Still preserve for history
          this.sessions.set(sessionId, resumed);
          continue;
        }
        this.sessions.set(sessionId, resumed);
      } catch {
        // Session no longer valid — skip silently
        continue;
      }
    }

    // Restore roleSessions (only for sessions that survived resume)
    for (const [key, sessionId] of Object.entries(state.roleSessions)) {
      if (this.sessions.has(sessionId)) {
        this.roleSessions.set(key as RoleSlotKey, sessionId);
      }
    }

    const restored = this.sessions.size;
    if (restored > 0) {
      this.transport.sendNotification("log", {
        message: `[persist] Restored ${restored} session(s) from disk`,
      });
    }
    if (approvalsChanged) {
      this.persistState(true);
    }
  }

  /**
   * Build cached KB context from global + role-specific config.
   * Prompts consume this per-session via startRoleSession().
   */
  private async buildAndInjectContext(): Promise<{ injected: boolean; agentCount: number; contextLength: number }> {
    const obsConfig = this.projectConfig?.obsidian;
    const hasRoleSpecificFiles = ROLE_CONTEXT_ROLES.some(
      (role) => this.getRoleSpecificFiles(role, obsConfig).length > 0,
    );

    if (!obsConfig?.autoInjectContext) {
      this.clearContextCache();
      return { injected: false, agentCount: 0, contextLength: 0 };
    }

    if (!obsConfig.contextFiles?.length && !hasRoleSpecificFiles) {
      this.clearContextCache();
      return { injected: false, agentCount: 0, contextLength: 0 };
    }

    if (!this.kb?.isEnabled()) {
      this.clearContextCache();
      this.transport.sendNotification("log", {
        message: "[context] autoInjectContext enabled but KB not available — skipping",
      });
      return { injected: false, agentCount: 0, contextLength: 0 };
    }

    try {
      const nextSharedContext = obsConfig.contextFiles?.length
        ? await this.kb.buildContext([...new Set(obsConfig.contextFiles)])
        : "";
      const nextRoleContexts: Partial<Record<RoleContextKey, string>> = {};

      for (const role of ROLE_CONTEXT_ROLES) {
        const roleSpecificFiles = this.getRoleSpecificFiles(role, obsConfig);
        if (roleSpecificFiles.length === 0) {
          continue;
        }

        const context = await this.kb.buildContext([...new Set(roleSpecificFiles)]);
        if (context) {
          nextRoleContexts[role] = context;
        }
      }

      const totalContextLength = nextSharedContext.length +
        Object.values(nextRoleContexts).reduce((total, context) => total + (context?.length ?? 0), 0);
      if (totalContextLength === 0) {
        this.clearContextCache();
        return { injected: false, agentCount: 0, contextLength: 0 };
      }

      this.sharedContext = nextSharedContext;
      this.roleContexts = nextRoleContexts;

      const roleSummaries = ROLE_CONTEXT_ROLES
        .filter((role) => Boolean(nextRoleContexts[role]))
        .map((role) => `${role}=${nextRoleContexts[role]!.length} chars`)
        .join(", ");

      this.transport.sendNotification("log", {
        message: `[context] Cached prompt context: global=${nextSharedContext.length} chars from ${(obsConfig.contextFiles ?? []).length} files${roleSummaries ? `; role-specific ${roleSummaries}` : ""}.`,
      });

      return { injected: true, agentCount: 0, contextLength: totalContextLength };
    } catch (err) {
      this.clearContextCache();
      const msg = err instanceof Error ? err.message : String(err);
      this.transport.sendNotification("log", {
        message: `[context] Failed to build prompt context: ${msg}`,
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
      case "execute_task":
        return this.executeTask(
          params.taskId as string,
          (params.oneShot as boolean | null) ?? undefined,
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
      case "list_models": {
        const adapter = this.registry.getAdapter(params.agentId as string);
        return adapter.listModels();
      }
      case "set_model": {
        const adapter = this.registry.getAdapter(params.agentId as string);
        adapter.setModel(params.model as string);
        // Update persisted config with immutable replacement
        const agents = this.projectConfig?.agents;
        if (agents) {
          const idx = agents.findIndex(
            (a: AgentConfig) => a.id === (params.agentId as string),
          );
          if (idx >= 0) {
            agents[idx] = { ...agents[idx], model: params.model as string };
            await this.persistConfigToDisk();
          }
        }
        return { ok: true };
      }
      case "get_slash_commands":
        return this.getSlashCommands(params.agentId as string);
      case "set_agent_cwd":
        return this.setAgentCwd(params.agentId as string, params.cwd as string);
      case "list_sessions":
        return this.listSessions(
          params.agentId as string | undefined,
          (params.role as AgentRole | null) ?? undefined,
          (params.includeTerminal as boolean | null) ?? undefined,
        );
      case "resume_session":
        return this.resumeExistingSession(
          params.agentId as string,
          params.sessionId as string,
          (params.expectedRole as AgentRole | null) ?? undefined,
        );
      case "get_session_messages":
        return this.getSessionMessages(
          params.sessionId as string,
          (params.offset as number | null) ?? undefined,
          (params.limit as number | null) ?? undefined,
        );
      case "get_approval_mode":
        return { mode: this.approvalMode };
      case "set_approval_mode":
        return this.setApprovalMode(params.mode as ApprovalMode);
      case "list_approval_requests":
        return this.listApprovalRequests(params.status as ApprovalRequestStatus | undefined);
      case "approve_request":
        return this.resolveApprovalRequest(
          params.requestId as string,
          "approve",
          "main_agent",
          (params.reason as string | null) ?? undefined,
        );
      case "deny_request":
        return this.resolveApprovalRequest(
          params.requestId as string,
          "deny",
          "main_agent",
          (params.reason as string | null) ?? undefined,
        );
      case "refresh_context":
        return this.buildAndInjectContext();
      case "get_context_status":
        return {
          hasContext: this.getContextCacheLength() > 0,
          contextLength: this.getContextCacheLength(),
          autoInject: this.projectConfig?.obsidian?.autoInjectContext ?? false,
          contextFiles: this.projectConfig?.obsidian?.contextFiles ?? [],
          roleContextFiles: this.projectConfig?.obsidian?.roleContextFiles ?? {},
        };
      case "build_reference_prompt": {
        const refTask = this.taskManager.getTask(params.taskId as string);
        if (!refTask) throw new Error(`Task not found: ${params.taskId}`);
        return {
          prompt: buildReferencePrompt(
            refTask,
            params.taskFilePath as string,
            (params.handoffFilePath as string | null) ?? undefined,
          ),
        };
      }
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
    this.persistState(true);
    return { ok: true };
  }

  private setApprovalMode(mode: ApprovalMode): { mode: ApprovalMode } {
    this.approvalMode = mode;
    this.persistState(true);
    return { mode };
  }

  private listApprovalRequests(status?: ApprovalRequestStatus): ApprovalRequest[] {
    const result = [...this.approvalRequests.values()];
    const filtered = status ? result.filter((request) => request.status === status) : result;
    filtered.sort((a, b) => b.createdAt - a.createdAt);
    return filtered;
  }

  private buildApprovalCardSummary(request: ApprovalRequest): string {
    const parts = [
      `Approval needed for ${request.role ?? "sub"}:${request.agentId}`,
      request.toolName ? `tool ${request.toolName}` : request.kind,
      request.summary,
    ].filter(Boolean);
    return parts.join(" — ");
  }

  private notifyApprovalRequest(request: ApprovalRequest): void {
    const mainAgentId = this.findMainAgentId();
    if (!mainAgentId) return;

    const mainSessionId =
      this.roleSessions.get(makeRoleSlotKey("main", mainAgentId)) ?? "orchestrator";
    const content = this.buildApprovalCardSummary(request);

    this.appendTranscriptMessage(mainSessionId, {
      role: "system",
      content,
      timestamp: Date.now(),
      metadata: {
        messageType: "approval_request",
        approvalRequestId: request.id,
      },
    });

    this.transport.sendNotification("agent_message", {
      agentId: mainAgentId,
      sessionId: mainSessionId,
      message: {
        role: "system",
        content,
        timestamp: Date.now(),
        metadata: {
          messageType: "approval_request",
          approvalRequestId: request.id,
        },
      },
    });
  }

  private async requestApproval(
    agentId: string,
    sessionId: string,
    adapter: string,
    request: {
      kind: ApprovalRequest["kind"];
      toolName?: string;
      summary: string;
      rawRequest?: Record<string, unknown>;
    },
  ): Promise<ApprovalDecision> {
    const session = this.sessions.get(sessionId);
    const approval: ApprovalRequest = {
      id: randomUUID(),
      agentId,
      sessionId,
      role: this.getSessionRole(session),
      adapter,
      kind: request.kind,
      toolName: request.toolName,
      summary: request.summary,
      rawRequest: request.rawRequest,
      cwd: session?.cwd ?? this.agentCwds.get(agentId),
      createdAt: Date.now(),
      status: "pending",
    };

    this.approvalRequests.set(approval.id, approval);
    this.bus.emit("agent.approval.requested", agentId, sessionId, approval);
    this.notifyApprovalRequest(approval);

    if (this.approvalMode === "auto_accept" || approval.role === "main") {
      return this.resolveApprovalRequest(
        approval.id,
        "approve",
        "system",
        this.approvalMode === "auto_accept" ? "Auto Accept enabled" : "Main agent bypass",
      );
    }

    this.persistState(true);

    return new Promise<ApprovalDecision>((resolve) => {
      const timeout = setTimeout(() => {
        void this.resolveApprovalRequest(
          approval.id,
          "deny",
          "system",
          "Approval request timed out",
          "timed_out",
        );
      }, Orchestrator.APPROVAL_TIMEOUT_MS);

      this.approvalWaiters.set(approval.id, { resolve, timeout });
    });
  }

  private resolveApprovalRequest(
    requestId: string,
    action: ApprovalDecision["action"],
    decisionBy: ApprovalRequest["decisionBy"],
    reason?: string,
    terminalStatus?: Extract<ApprovalRequestStatus, "approved" | "denied" | "timed_out" | "cancelled">,
  ): ApprovalDecision {
    const request = this.approvalRequests.get(requestId);
    if (!request) {
      throw new Error(`Approval request not found: ${requestId}`);
    }

    const resolvedStatus =
      terminalStatus ?? (action === "approve" ? "approved" : "denied");
    const updated: ApprovalRequest = {
      ...request,
      status: resolvedStatus,
      resolvedAt: Date.now(),
      decisionBy,
      decisionReason: reason,
    };
    this.approvalRequests.set(requestId, updated);
    this.bus.emit("agent.approval.resolved", updated.agentId, updated.sessionId, updated);

    const waiter = this.approvalWaiters.get(requestId);
    if (waiter) {
      clearTimeout(waiter.timeout);
      this.approvalWaiters.delete(requestId);
      waiter.resolve(
        action === "approve"
          ? { action: "approve", reason }
          : { action: "deny", reason },
      );
    }

    this.persistState(true);
    return action === "approve"
      ? { action: "approve", reason }
      : { action: "deny", reason };
  }

  private cancelPendingApprovalsForSession(sessionId: string, reason: string): void {
    for (const request of this.approvalRequests.values()) {
      if (request.sessionId !== sessionId || request.status !== "pending") continue;
      this.resolveApprovalRequest(request.id, "deny", "system", reason, "cancelled");
    }
  }

  private asNativeSessionBridge(agentId: string): NativeSessionBridge {
    return this.registry.getAdapter(agentId) as NativeSessionBridge;
  }

  private async getSessionMessages(
    sessionId: string,
    offset?: number,
    limit?: number,
  ): Promise<{ messages: TranscriptMessage[]; total: number }> {
    const safeOffset = Math.max(0, offset ?? 0);

    if (this.transcripts) {
      const transcript = this.transcripts.read(sessionId, safeOffset, limit);
      if (transcript.total > 0) {
        return transcript;
      }
    }

    for (const agent of this.registry.listAgents()) {
      const nativeAdapter = this.asNativeSessionBridge(agent.id);
      if (!nativeAdapter.readNativeMessages) continue;
      try {
        const messages = await nativeAdapter.readNativeMessages(sessionId);
        const paged = typeof limit === "number"
          ? messages.slice(safeOffset, safeOffset + Math.max(0, limit))
          : messages.slice(safeOffset);
        return { messages: paged, total: messages.length };
      } catch {
        continue;
      }
    }

    return { messages: [], total: 0 };
  }

  private async listSessions(
    agentId?: string,
    role?: AgentRole,
    includeTerminal = false,
  ): Promise<Array<SessionInfo & { active: boolean; currentPromptHash?: string; legacyRoleConfig?: boolean }>> {
    const result: Array<
      SessionInfo & { active: boolean; currentPromptHash?: string; legacyRoleConfig?: boolean }
    > = [];
    const activeSessionIds = new Set(this.roleSessions.values());
    const seenResumeTokens = new Set<string>();

    // In-memory sessions
    for (const [, info] of this.sessions) {
      if (agentId && info.agentId !== agentId) continue;
      const sessionRole = this.getSessionRole(info, info.agentId);
      if (role && sessionRole !== role) continue;
      if (!includeTerminal && !sessionRole) continue;
      if (!includeTerminal && (info.status === "completed" || info.status === "overflow")) continue;
      const promptState = this.getLegacyRoleConfigState(info);
      result.push({
        ...info,
        role: sessionRole,
        active: activeSessionIds.has(info.sessionId),
        ...promptState,
      });
      if (info.resumeToken) {
        seenResumeTokens.add(info.resumeToken);
      }
    }

    // Also include persisted-but-not-in-memory sessions
    if (this.persistence) {
      const persisted = this.persistence.load();
      if (persisted) {
        for (const [sessionId, info] of Object.entries(persisted.sessions)) {
          if (this.sessions.has(sessionId)) continue; // Already included
          if (agentId && info.agentId !== agentId) continue;
          const sessionRole = this.getSessionRole(info, info.agentId);
          if (role && sessionRole !== role) continue;
          if (!includeTerminal && !sessionRole) continue;
          if (!includeTerminal && (info.status === "completed" || info.status === "overflow")) continue;
          const promptState = this.getLegacyRoleConfigState(info);
          result.push({
            ...info,
            role: sessionRole,
            active: false,
            ...promptState,
          });
          if (info.resumeToken) {
            seenResumeTokens.add(info.resumeToken);
          }
        }
      }
    }

    if (agentId) {
      const nativeAdapter = this.asNativeSessionBridge(agentId);
      if (nativeAdapter.listNativeSessions) {
        try {
          const nativeSessions = await this.listNativeSessionsWithFallback(agentId, nativeAdapter);
          for (const info of nativeSessions) {
            if (info.resumeToken && seenResumeTokens.has(info.resumeToken)) continue;
            const sessionRole = this.getSessionRole(info, agentId);
            if (role && sessionRole !== role) continue;
            if (!includeTerminal && !sessionRole) continue;
            if (!includeTerminal && (info.status === "completed" || info.status === "overflow")) continue;
            const promptState = this.getLegacyRoleConfigState(info);
            result.push({
              ...info,
              role: sessionRole,
              active: activeSessionIds.has(info.sessionId),
              ...promptState,
            });
          }
        } catch {
          // Native session discovery is best effort.
        }
      }
    }

    result.sort((a, b) => b.lastActiveAt - a.lastActiveAt);
    return result;
  }

  private async resumeExistingSession(
    agentId: string,
    sessionId: string,
    expectedRole?: AgentRole,
  ): Promise<SessionInfo> {
    // Check in-memory first
    let info = this.sessions.get(sessionId);

    // Try persisted sessions
    if (!info && this.persistence) {
      const persisted = this.persistence.load();
      if (persisted?.sessions[sessionId]) {
        info = persisted.sessions[sessionId];
      }
    }

    if (!info) {
      const nativeAdapter = this.asNativeSessionBridge(agentId);
      if (nativeAdapter.getNativeSessionInfo) {
        info = await nativeAdapter.getNativeSessionInfo(
          sessionId,
          this.agentCwds.get(agentId),
        ) ?? undefined;
      }
    }

    if (!info) throw new Error(`Session not found: ${sessionId}`);
    if (info.agentId !== agentId) {
      throw new Error(`Session ${sessionId} belongs to agent ${info.agentId}, not ${agentId}`);
    }
    const sessionRole = this.getSessionRole(info);
    if (!sessionRole) {
      throw new Error(`Session ${sessionId} is legacy and can only be viewed in History`);
    }
    if (expectedRole && expectedRole !== sessionRole) {
      throw new Error(
        `Role mismatch: session ${sessionId} belongs to role "${sessionRole}", not "${expectedRole}"`,
      );
    }
    // Completed sessions can only be resumed if they have a native resumeToken
    if (info.status === "completed" || info.status === "overflow") {
      if (!info.resumeToken) {
        throw new Error(`Session ${sessionId} is ${info.status} and has no resume token — use History to view`);
      }
    }

    // Attempt to resume in the adapter
    const adapter = this.registry.getAdapter(agentId);
    const resumed = await adapter.resumeSession(
      sessionId,
      info,
      info.cwd ?? this.agentCwds.get(agentId),
    );
    if (resumed.status === "completed" || resumed.status === "overflow") {
      throw new Error(`Session ${sessionId} is ${resumed.status} and cannot be resumed`);
    }

    // Restore into active state
    resumed.role = sessionRole;
    resumed.frozenRole = sessionRole;
    this.sessions.set(sessionId, resumed);
    const promptState = this.getLegacyRoleConfigState(resumed);
    const resumedWithPromptState = resumed as SessionInfo & {
      currentPromptHash?: string;
      legacyRoleConfig?: boolean;
    };
    resumedWithPromptState.currentPromptHash = promptState.currentPromptHash;
    resumedWithPromptState.legacyRoleConfig = promptState.legacyRoleConfig;

    const slotKey = makeRoleSlotKey(sessionRole, agentId);
    this.roleSessions.set(slotKey, sessionId);

    this.bus.emit("agent.session.start", agentId, sessionId, {
      role: sessionRole,
      sessionName: resumed.sessionName,
      resumed: true,
      promptHash: resumed.promptHash,
      currentPromptHash: promptState.currentPromptHash,
      legacyRoleConfig: promptState.legacyRoleConfig,
    });

    this.persistState(true);
    return resumedWithPromptState;
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
    session.frozenRole = effectiveRole;
    session.sessionName = this.buildRoleSessionName(agentId, effectiveRole, taskName);
    session.cwd = cwd;

    // Inject role-specific system prompt (global + per-role KB context)
    const baseRolePrompt = this.buildSystemRolePrompt(effectiveRole);
    const rolePrompt =
      systemPrompt ??
      baseRolePrompt;
    session.baseRolePromptHash = this.hashPrompt(baseRolePrompt);
    session.frozenSystemPrompt = rolePrompt;
    session.promptHash = this.hashPrompt(rolePrompt);
    const promptState = this.getLegacyRoleConfigState(session);

    this.sessions.set(session.sessionId, session);
    const slotKey = makeRoleSlotKey(effectiveRole, agentId);
    this.roleSessions.set(slotKey, session.sessionId);
    const nativeAdapter = this.asNativeSessionBridge(agentId);
    try {
      await nativeAdapter.setSessionName?.(session.sessionId, session.sessionName);
    } catch {
      // Naming native sessions is best effort.
    }

    this.bus.emit("agent.session.start", agentId, session.sessionId, {
      role: effectiveRole,
      sessionName: session.sessionName,
      promptHash: session.promptHash,
      currentPromptHash: promptState.currentPromptHash,
      legacyRoleConfig: promptState.legacyRoleConfig,
    });

    this.persistState(true);
    return session;
  }

  private async sendPrompt(
    agentId: string,
    prompt: string,
    images?: ImageAttachment[],
    role?: AgentRole,
    taskName?: string,
    systemPrompt?: string,
    taskId?: string,
  ): Promise<{ sessionId: string; role?: AgentRole; sessionName?: string; status?: SessionInfo["status"] }> {
    const adapter = this.registry.getAdapter(agentId);
    const config = this.registry.getConfig(agentId);
    const effectiveRole = role ?? config.roles[0];
    const slotKey = makeRoleSlotKey(effectiveRole, agentId);

    // Auto-start session if none exists for this role slot
    let sessionId = this.roleSessions.get(slotKey);
    if (!sessionId) {
      const session = await this.startRoleSession(agentId, effectiveRole, taskName, systemPrompt);
      sessionId = session.sessionId;
    }

    // Validate that the adapter still knows about this session and it's usable.
    let needNewSession = false;
    try {
      const currentInfo = this.sessions.get(sessionId);
      const sessionInfo = await adapter.resumeSession(
        sessionId,
        currentInfo,
        currentInfo?.cwd ?? this.agentCwds.get(agentId),
      );
      if (sessionInfo.status === "completed" || sessionInfo.status === "overflow") {
        needNewSession = true;
      }
    } catch {
      needNewSession = true;
    }
    if (needNewSession) {
      const staleSession = this.sessions.get(sessionId);
      if (staleSession) {
        staleSession.status = "completed";
        staleSession.lastActiveAt = Date.now();
      }
      this.roleSessions.delete(slotKey);
      const session = await this.startRoleSession(agentId, effectiveRole, taskName, systemPrompt);
      sessionId = session.sessionId;
    }
    if (taskId && effectiveRole !== "main") {
      this.taskManager.bindSession(taskId, sessionId);
    }

    this.bus.emit("agent.message.send", agentId, sessionId, {
      prompt: prompt.slice(0, 200),
      hasImages: images ? images.length : 0,
      role: effectiveRole,
    });
    this.transport.sendNotification("agent_working", {
      agentId,
      sessionId,
      role: effectiveRole,
      startedAt: Date.now(),
    });
    this.appendTranscriptMessage(sessionId, {
      role: "user",
      content: prompt,
      timestamp: Date.now(),
      images,
    });

    // Stream messages asynchronously
    const hooks: AgentSendHooks | undefined =
      effectiveRole === "main"
        ? undefined
        : {
            onApprovalRequest: (request) =>
              this.requestApproval(agentId, sessionId, adapter.config.cli, request),
          };
    void this.streamMessages(adapter, agentId, sessionId, prompt, images, hooks)
      .then((result) => this.handleStreamCompletion(agentId, sessionId, effectiveRole, taskId, result))
      .catch((err) => {
        const message = err instanceof Error ? err.message : String(err);
        this.transport.sendNotification("log", {
          message: `[stream] completion handler failed for ${sessionId}: ${message}`,
        });
      });

    const session = this.sessions.get(sessionId);
    return {
      sessionId,
      role: session?.role,
      sessionName: session?.sessionName,
      status: session?.status,
    };
  }

  private async streamMessages(
    adapter: ReturnType<AgentRegistry["getAdapter"]>,
    agentId: string,
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
    hooks?: AgentSendHooks,
  ): Promise<StreamCompletion> {
    let lastAssistantMessage: AgentMessage | undefined;

    try {
      for await (const yielded of adapter.sendPrompt(sessionId, prompt, images, hooks)) {
        // Streaming events are forwarded as lightweight notifications without
        // persisting to transcript — they represent incremental token content.
        if (isStreamingEvent(yielded)) {
          // Track token usage from streaming events for context checkpoint
          if (yielded.tokenCount !== undefined) {
            const session = this.sessions.get(sessionId);
            if (session) {
              session.tokenUsage = yielded.tokenCount;
              this.checkTokenThreshold(session);
            }
          }

          this.transport.sendNotification("agent_streaming", {
            agentId,
            sessionId,
            event: {
              eventKind: yielded.eventKind,
              content: yielded.content,
              toolName: yielded.toolName,
              toolInput: yielded.toolInput,
              tokenCount: yielded.tokenCount,
              timestamp: yielded.timestamp,
            },
          });
          continue;
        }

        const message = yielded as AgentMessage;
        if (message.role === "assistant" && message.content.trim().length > 0) {
          lastAssistantMessage = message;
        }
        this.bus.emit("agent.message.receive", agentId, sessionId, {
          contentPreview: message.content.slice(0, 200),
        });
        this.appendTranscriptMessage(sessionId, {
          role: message.role,
          content: message.content,
          timestamp: message.timestamp,
          images: message.images,
          metadata: message.metadata,
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
        this.persistState();
      }

      // Signal stream complete
      this.transport.sendNotification("agent_stream_end", {
        agentId,
        sessionId,
      });
      const session = this.sessions.get(sessionId);
      if (session) {
        session.lastActiveAt = Date.now();
      }
      this.persistState();
      return { completed: true, lastAssistantMessage };
    } catch (err) {
      const errorMsg = err instanceof Error ? err.message : String(err);
      this.cancelPendingApprovalsForSession(sessionId, "Session failed while approval was pending");
      this.bus.emit("agent.error", agentId, sessionId, { error: errorMsg });
      // Ensure frontend streaming state is always closed, even on errors
      this.transport.sendNotification("agent_stream_end", {
        agentId,
        sessionId,
      });
      this.transport.sendNotification("agent_error", {
        agentId,
        sessionId,
        error: errorMsg,
      });

      // Keep failed sessions in history, but detach them from active role routing.
      const session = this.sessions.get(sessionId);
      if (session) {
        session.status = "completed";
        session.lastActiveAt = Date.now();
      }
      for (const [key, sid] of this.roleSessions) {
        if (sid === sessionId) {
          this.roleSessions.delete(key);
          break;
        }
      }
      try { await adapter.endSession(sessionId); } catch { /* best-effort */ }
      this.persistState(true);
      return { completed: false };
    }
  }

  /**
   * Check if a session has crossed the 70% token usage threshold.
   * When crossed for the first time, records a checkpoint timestamp and
   * emits an event so the orchestrator can plan a session handoff.
   */
  private checkTokenThreshold(session: SessionInfo): void {
    if (session.tokenCheckpointAt) return; // already checkpointed
    if (!session.tokenUsage || !session.tokenLimit) return;

    const ratio = session.tokenUsage / session.tokenLimit;
    if (ratio >= Orchestrator.TOKEN_CHECKPOINT_THRESHOLD) {
      session.tokenCheckpointAt = new Date().toISOString();
      this.bus.emit(
        "orchestrator.context.compact",
        session.agentId,
        session.sessionId,
        {
          tokenUsage: session.tokenUsage,
          tokenLimit: session.tokenLimit,
          ratio,
          checkpointAt: session.tokenCheckpointAt,
        },
      );
      this.transport.sendNotification("orchestrator.context.checkpoint", {
        agentId: session.agentId,
        sessionId: session.sessionId,
        tokenUsage: session.tokenUsage,
        tokenLimit: session.tokenLimit,
        ratio,
        checkpointAt: session.tokenCheckpointAt,
      });

      // Persist session state so checkpoint survives a crash
      this.persistSessionState(session);
    }
  }

  /** Persist session info to session-persistence (fire-and-forget). */
  private persistSessionState(session: SessionInfo): void {
    // Session persistence is handled by session-persistence.ts if available;
    // this is a best-effort write to ensure checkpoint data survives crashes.
    this.sessions.set(session.sessionId, session);
  }

  private async handleStreamCompletion(
    agentId: string,
    sessionId: string,
    role: AgentRole,
    taskId: string | undefined,
    result: StreamCompletion,
  ): Promise<void> {
    const completedAt = Date.now();
    const effectiveTaskId = taskId ?? this.taskManager.getTaskForSession(sessionId);
    this.transport.sendNotification("orchestrator.session.stream_complete", {
      agentId,
      sessionId,
      role,
      taskId: effectiveTaskId ?? null,
      completed: result.completed,
      completedAt,
    });

    if (!result.completed || !effectiveTaskId) {
      return;
    }

    const task = this.taskManager.getTask(effectiveTaskId);
    if (!task) {
      return;
    }

    const finalMessage = result.lastAssistantMessage?.content ?? "";
    if (role === "dev") {
      await this.handleDevTaskStreamComplete(task, agentId, finalMessage, completedAt);
      return;
    }
    if (role === "main") {
      await this.handleMainReviewStreamComplete(task, finalMessage);
      return;
    }
    if (role === "acceptance") {
      await this.handleAcceptanceStreamComplete(task, finalMessage);
    }
  }

  private async handleDevTaskStreamComplete(
    task: TaskBundle,
    agentId: string,
    finalMessage: string,
    completedAt: number,
  ): Promise<void> {
    if (task.status !== "in_progress") {
      return;
    }
    const receipt = this.parseImplementationReceipt(task, agentId, finalMessage, completedAt);
    await this.recordReceiptAndTriggerReview(task.taskId, receipt);
  }

  private async handleMainReviewStreamComplete(
    task: TaskBundle,
    finalMessage: string,
  ): Promise<void> {
    if (task.status !== "main_review") {
      return;
    }
    const review = this.parseMainReviewDecision(finalMessage);
    if (!review) {
      this.transport.sendNotification("log", {
        message: `[task] Unable to parse main review result for ${task.taskId}`,
      });
      return;
    }
    this.taskManager.updateTaskField(task.taskId, "mainReview", {
      preChecks: task.mainReview?.preChecks ?? [],
      gitDiff: task.mainReview?.gitDiff ?? "",
      result: review.structured,
      reviewedAt: Date.now(),
    });
    await this.handleMainReviewResult(
      task.taskId,
      review.decision,
      review.reason,
      undefined,
      review.structured,
    );
  }

  private async handleAcceptanceStreamComplete(
    task: TaskBundle,
    finalMessage: string,
  ): Promise<void> {
    if (task.status !== "acceptance") {
      return;
    }
    const acceptanceId = task.handoffToAcceptance?.acceptanceBundleId;
    if (!acceptanceId) {
      this.transport.sendNotification("log", {
        message: `[task] Missing acceptance bundle for ${task.taskId}`,
      });
      return;
    }
    const results = this.parseAcceptanceResult(finalMessage);
    if (!results) {
      this.transport.sendNotification("log", {
        message: `[task] Unable to parse acceptance result for ${task.taskId}`,
      });
      return;
    }
    await this.recordAcceptanceFlow(acceptanceId, results);
  }

  private parseImplementationReceipt(
    task: TaskBundle,
    agentId: string,
    finalMessage: string,
    completedAt: number,
  ): ImplementationReceipt {
    const parsed = this.tryParseJsonRecord(finalMessage);
    const agentConfig = this.registry.getConfig(agentId);
    const implementerModel = agentConfig.model ?? agentConfig.cli;

    return {
      implementer: `${agentId} (${implementerModel})`,
      branch: this.readString(parsed?.branch) ?? task.branch ?? "",
      summary: this.readString(parsed?.summary) ?? finalMessage.trim(),
      changedFiles: this.readStringArray(parsed?.changedFiles),
      evidence: this.readStringArray(parsed?.evidence),
      docsUpdated: this.readStringArray(parsed?.docsUpdated),
      residualRisks: this.readStringArray(parsed?.residualRisks),
      completedAt,
    };
  }

  private parseMainReviewDecision(
    content: string,
  ): ParsedMainReviewDecision | null {
    const trimmed = content.trim();
    if (!trimmed) {
      return null;
    }
    const parsed = this.tryParseJsonRecord(content);
    const structured = parsed
      ? this.parseStructuredReviewResult(parsed, trimmed)
      : null;
    if (structured) {
      return {
        decision: structured.decision,
        reason: structured.reason,
        structured,
      };
    }
    if (trimmed.includes("APPROVE_FOR_ACCEPTANCE")) {
      return {
        decision: "APPROVE_FOR_ACCEPTANCE",
        structured: {
          decision: "APPROVE_FOR_ACCEPTANCE",
          summary: trimmed,
          findings: [],
        },
      };
    }
    const sendBackIndex = trimmed.indexOf("SEND_BACK");
    if (sendBackIndex === -1) {
      return null;
    }
    const reason = trimmed.slice(sendBackIndex + "SEND_BACK".length).trim()
      .replace(/^[:\-\s]+/, "");
    return {
      decision: "SEND_BACK",
      reason: reason || undefined,
      structured: {
        decision: "SEND_BACK",
        summary: reason || "Main review sent the task back for rework.",
        reason: reason || undefined,
        findings: [],
      },
    };
  }

  private parseAcceptanceResult(
    content: string,
  ): { verdict: AcceptanceVerdict; findings: string[]; recommendations: string[] } | null {
    const parsed = this.tryParseJsonRecord(content);
    const verdict = this.readString(parsed?.verdict);
    if (
      verdict !== "pass" &&
      verdict !== "partial" &&
      verdict !== "fail" &&
      verdict !== "blocked"
    ) {
      return null;
    }
    return {
      verdict,
      findings: this.readStringArray(parsed?.findings),
      recommendations: this.readStringArray(parsed?.recommendations),
    };
  }

  private tryParseJsonRecord(content: string): Record<string, unknown> | null {
    const trimmed = content.trim();
    const candidates = [
      ...trimmed.matchAll(/```(?:json)?\s*([\s\S]*?)```/gi),
    ]
      .map((match) => match[1]?.trim())
      .filter((candidate): candidate is string => Boolean(candidate));

    if (trimmed.startsWith("{") && trimmed.endsWith("}")) {
      candidates.unshift(trimmed);
    }

    const inlineObject = trimmed.match(/\{[\s\S]*\}/);
    if (inlineObject?.[0]) {
      candidates.push(inlineObject[0]);
    }

    for (const candidate of candidates) {
      try {
        const parsed = JSON.parse(candidate) as unknown;
        if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
          return parsed as Record<string, unknown>;
        }
      } catch {
        continue;
      }
    }

    return null;
  }

  private readString(value: unknown): string | undefined {
    return typeof value === "string" ? value : undefined;
  }

  private readNumber(value: unknown): number | undefined {
    return typeof value === "number" && Number.isFinite(value) ? value : undefined;
  }

  private readStringArray(value: unknown): string[] {
    return Array.isArray(value)
      ? value.filter((entry): entry is string => typeof entry === "string")
      : [];
  }

  private readReviewFindings(value: unknown): ReviewFinding[] {
    if (!Array.isArray(value)) {
      return [];
    }

    const findings: ReviewFinding[] = [];
    for (const entry of value) {
      if (typeof entry === "string") {
        findings.push({
          severity: "major",
          title: entry,
          detail: entry,
        });
        continue;
      }
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
        continue;
      }

      const record = entry as Record<string, unknown>;
      const severity = this.readString(record.severity);
      const title = this.readString(record.title) ?? this.readString(record.summary);
      const detail = this.readString(record.detail) ?? this.readString(record.reason) ?? title;

      if (
        !title ||
        !detail ||
        (severity !== "critical" && severity !== "major" && severity !== "minor" && severity !== "info")
      ) {
        continue;
      }

      findings.push({
        severity,
        title,
        detail,
        file: this.readString(record.file),
        line: this.readNumber(record.line),
      });
    }

    return findings;
  }

  private parseStructuredReviewResult(
    parsed: Record<string, unknown>,
    fallbackSummary: string,
  ): StructuredReviewResult | null {
    const rawDecision = this.readString(parsed.decision);
    const findings = this.readReviewFindings(parsed.findings);
    const summary = this.readString(parsed.summary) ?? this.readString(parsed.reason) ?? fallbackSummary;
    let decision: StructuredReviewResult["decision"] | undefined;
    if (rawDecision === "APPROVE_FOR_ACCEPTANCE" || rawDecision === "SEND_BACK") {
      decision = rawDecision;
    }

    const criticalFindings = findings.filter((finding) => finding.severity === "critical");
    if (criticalFindings.length > 0) {
      decision = "SEND_BACK";
    }

    if (!decision) {
      return null;
    }

    return {
      decision,
      summary,
      reason: this.readString(parsed.reason) ?? this.formatCriticalFindingReason(criticalFindings),
      findings,
    };
  }

  private formatCriticalFindingReason(findings: ReviewFinding[]): string | undefined {
    if (findings.length === 0) {
      return undefined;
    }
    return findings
      .map((finding) => {
        const location = finding.file
          ? `${finding.file}${finding.line !== undefined ? `:${finding.line}` : ""}`
          : undefined;
        return location ? `${finding.title} (${location})` : finding.title;
      })
      .join("; ");
  }

  private getReviewConfig(task: TaskBundle): ReviewConfig {
    return task.reviewConfig ?? {};
  }

  private async runPreChecks(task: TaskBundle): Promise<PreCheckResult[]> {
    const configs = this.getReviewConfig(task).preChecks ?? [];
    const results: PreCheckResult[] = [];

    for (const config of configs) {
      results.push(await this.runPreCheck(config));
    }

    return results;
  }

  private async runPreCheck(config: PreCheckConfig): Promise<PreCheckResult> {
    const cwd = resolve(this.getProjectRoot(), config.cwd ?? ".");
    const args = config.args ?? [];
    const command = [config.command, ...args].join(" ").trim();
    const result = await this.runCommand(config.command, args, {
      cwd,
      shell: config.shell ?? false,
      timeoutMs: config.timeoutMs ?? Orchestrator.DEFAULT_PRECHECK_TIMEOUT_MS,
    });

    return {
      name: config.name,
      command,
      cwd,
      success: result.exitCode === 0 && !result.timedOut,
      exitCode: result.exitCode,
      timedOut: result.timedOut,
      durationMs: result.durationMs,
      stdout: result.stdout,
      stderr: result.stderr,
    };
  }

  private async getGitDiff(task: TaskBundle): Promise<string> {
    const reviewConfig = this.getReviewConfig(task);
    const diffBaseRef = reviewConfig.diffBaseRef ?? "develop...HEAD";
    const maxChars = reviewConfig.diffMaxChars ?? Orchestrator.DEFAULT_REVIEW_DIFF_MAX_CHARS;
    const result = await this.runCommand("git", ["diff", "--no-ext-diff", diffBaseRef], {
      cwd: this.getProjectRoot(),
      timeoutMs: Orchestrator.DEFAULT_PRECHECK_TIMEOUT_MS,
    });

    let diff = result.stdout;
    if (result.exitCode !== 0) {
      const errorText = result.stderr || `git diff exited with code ${result.exitCode ?? "null"}`;
      diff = `# git diff ${diffBaseRef} failed\n${errorText}`;
    }
    if (!diff.trim()) {
      diff = "# No diff output";
    }
    if (diff.length <= maxChars) {
      return diff;
    }

    return `${diff.slice(0, maxChars)}\n\n# Diff truncated at ${maxChars} characters`;
  }

  private async runCommand(
    command: string,
    args: string[],
    options: { cwd: string; timeoutMs: number; shell?: boolean },
  ): Promise<{
    stdout: string;
    stderr: string;
    exitCode: number | null;
    timedOut: boolean;
    durationMs: number;
  }> {
    return await new Promise((resolveResult) => {
      const startedAt = Date.now();
      let stdout = "";
      let stderr = "";
      let timedOut = false;
      let settled = false;
      let timeoutId: ReturnType<typeof setTimeout> | undefined;

      const finalize = (result: {
        stdout: string;
        stderr: string;
        exitCode: number | null;
        timedOut: boolean;
      }) => {
        if (settled) {
          return;
        }
        settled = true;
        if (timeoutId) {
          clearTimeout(timeoutId);
        }
        resolveResult({
          ...result,
          stdout: result.stdout.trim(),
          stderr: result.stderr.trim(),
          durationMs: Date.now() - startedAt,
        });
      };

      let child: ReturnType<typeof spawn>;
      try {
        child = spawn(command, args, {
          cwd: options.cwd,
          shell: options.shell ?? false,
          windowsHide: true,
          env: process.env,
        });
      } catch (err) {
        finalize({
          stdout,
          stderr: err instanceof Error ? err.message : String(err),
          exitCode: null,
          timedOut: false,
        });
        return;
      }

      timeoutId = setTimeout(() => {
        timedOut = true;
        child.kill("SIGTERM");
        // Escalate to SIGKILL if process survives SIGTERM
        setTimeout(() => {
          try { child.kill("SIGKILL"); } catch { /* already exited */ }
        }, 500);
      }, options.timeoutMs);

      child.stdout?.setEncoding("utf8");
      child.stdout?.on("data", (chunk: string) => {
        stdout += chunk;
      });

      child.stderr?.setEncoding("utf8");
      child.stderr?.on("data", (chunk: string) => {
        stderr += chunk;
      });

      child.on("error", (err) => {
        finalize({
          stdout,
          stderr: [stderr, err.message].filter(Boolean).join("\n"),
          exitCode: null,
          timedOut,
        });
      });

      child.on("close", (code) => {
        finalize({
          stdout,
          stderr,
          exitCode: code,
          timedOut,
        });
      });
    });
  }

  private async stopSession(
    agentId: string,
    sessionId: string,
  ): Promise<void> {
    this.cancelPendingApprovalsForSession(sessionId, "Session stopped while approval was pending");
    const adapter = this.registry.getAdapter(agentId);
    await adapter.endSession(sessionId);

    const session = this.sessions.get(sessionId);
    if (session) {
      session.status = "completed";
      session.lastActiveAt = Date.now();
    }
    for (const [key, sid] of this.roleSessions) {
      if (sid === sessionId) {
        this.roleSessions.delete(key);
        break;
      }
    }

    this.bus.emit("agent.session.end", agentId, sessionId, {});
    this.persistState(true);
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

  private buildRoleSessionName(agentId: string, role: AgentRole, taskName?: string): string {
    const config = this.registry.getConfig(agentId);
    return `${role}-${config.cli}-${taskName ?? "default"}`;
  }

  private wrapPromptWithRoleContext(prompt: string, promptContext?: string): string {
    if (!promptContext) {
      return prompt;
    }
    return `[Mercury Role Context]\n${promptContext}\n\n[User Prompt]\n${prompt}`;
  }

  private async prepareBundleTaskExecution(taskId: string): Promise<{
    task: TaskBundle;
    prompt: string;
    devRolePrompt: string;
    baseRolePrompt: string;
  }> {
    const task = await this.taskManager.getTaskAsync(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    // Normalize assignedTo: KB bundles may use {agentId, model, sessionId} object
    if (typeof task.assignedTo === "object" && task.assignedTo !== null) {
      task.assignedTo = (task.assignedTo as unknown as { agentId: string }).agentId;
    }

    const mainAgentId = this.findMainAgentId();
    if (mainAgentId) {
      const mainSlot = makeRoleSlotKey("main", mainAgentId);
      this.taskManager.updateTaskField(
        taskId,
        "originatorSessionId",
        this.roleSessions.get(mainSlot),
      );
    }

    let kbContext: string | undefined;
    if (this.kb?.isEnabled() && task.readScope.requiredDocs.length > 0) {
      try {
        kbContext = await this.kb.buildContext(task.readScope.requiredDocs);
      } catch {
        // KB context is best-effort
      }
    }

    return {
      task,
      prompt: buildDevPrompt(task, kbContext, this.getProjectRoot()),
      devRolePrompt: this.buildSystemRolePrompt("dev", task),
      baseRolePrompt: this.buildSystemRolePrompt("dev"),
    };
  }

  private async executeTask(
    taskId: string,
    oneShot = false,
  ): Promise<{
    taskId: string;
    sessionId: string;
    threadId?: string;
    messages: AgentMessage[];
    finalMessage?: string;
  }> {
    if (!oneShot) {
      const result = await this.dispatchBundleTask(taskId);
      const session = this.sessions.get(result.sessionId);
      return {
        ...result,
        threadId: session?.resumeToken,
        messages: [],
      };
    }

    const { task, prompt, devRolePrompt, baseRolePrompt } =
      await this.prepareBundleTaskExecution(taskId);
    const agentId = task.assignedTo;
    const adapter = this.registry.getAdapter(agentId);
    if (!adapter.executeOneShot) {
      throw new Error(`Agent ${agentId} does not support executeOneShot()`);
    }

    if (task.status === "drafted") {
      this.taskManager.transitionTask(taskId, "dispatched", "orchestrator");
    }
    if (task.status === "dispatched") {
      this.taskManager.transitionTask(taskId, "in_progress", agentId);
    }

    const cwd = this.agentCwds.get(agentId) ?? process.cwd();
    const startedAt = Date.now();
    const sessionName = this.buildRoleSessionName(agentId, "dev", task.title);
    const result = await adapter.executeOneShot(
      this.wrapPromptWithRoleContext(prompt, devRolePrompt),
      cwd,
    );

    const nativeAdapter = this.asNativeSessionBridge(agentId);
    try {
      await nativeAdapter.setSessionName?.(result.threadId, sessionName);
    } catch {
      // Naming native sessions is best effort.
    }

    const nativeInfo = await nativeAdapter.getNativeSessionInfo?.(result.threadId, cwd) ?? null;
    const sessionId = result.threadId;
    const session: SessionInfo = {
      sessionId,
      agentId,
      role: "dev",
      frozenRole: "dev",
      sessionName,
      cwd: nativeInfo?.cwd ?? cwd,
      startedAt: nativeInfo?.startedAt ?? startedAt,
      lastActiveAt: Date.now(),
      tokenUsage: nativeInfo?.tokenUsage,
      tokenLimit: nativeInfo?.tokenLimit,
      status: "completed",
      resumeToken: result.threadId,
      frozenSystemPrompt: devRolePrompt,
      baseRolePromptHash: this.hashPrompt(baseRolePrompt),
      promptHash: this.hashPrompt(devRolePrompt),
    };

    this.sessions.set(sessionId, session);
    this.taskManager.bindSession(taskId, sessionId);

    this.bus.emit("agent.session.start", agentId, sessionId, {
      role: "dev",
      sessionName,
      promptHash: session.promptHash,
      currentPromptHash: undefined,
      legacyRoleConfig: false,
      oneShot: true,
    });
    this.bus.emit("agent.message.send", agentId, sessionId, {
      prompt: prompt.slice(0, 200),
      hasImages: 0,
      role: "dev",
      oneShot: true,
    });
    this.transport.sendNotification("agent_working", {
      agentId,
      sessionId,
      role: "dev",
      startedAt,
      oneShot: true,
    });

    this.appendTranscriptMessage(sessionId, {
      role: "user",
      content: prompt,
      timestamp: startedAt,
    });

    for (const message of result.messages) {
      this.bus.emit("agent.message.receive", agentId, sessionId, {
        contentPreview: message.content.slice(0, 200),
        oneShot: true,
      });
      this.appendTranscriptMessage(sessionId, {
        role: message.role,
        content: message.content,
        timestamp: message.timestamp,
        images: message.images,
        metadata: message.metadata,
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

    this.transport.sendNotification("agent_stream_end", {
      agentId,
      sessionId,
    });
    this.bus.emit("agent.session.end", agentId, sessionId, { oneShot: true, taskId });
    this.persistState(true);

    return {
      taskId,
      sessionId,
      threadId: result.threadId,
      messages: result.messages,
      finalMessage: result.finalMessage,
    };
  }

  /**
   * Compute exponential backoff delay: min(300 * 2^attempt, 30000) + jitter.
   * Jitter is a random value in [0, baseDelay) to prevent thundering herd.
   */
  private computeDispatchBackoff(attempt: number): number {
    const base = Math.min(
      Orchestrator.DISPATCH_RETRY_BASE_MS * Math.pow(2, attempt),
      Orchestrator.DISPATCH_RETRY_MAX_MS,
    );
    const jitter = Math.floor(Math.random() * base);
    return base + jitter;
  }

  private async dispatchBundleTask(
    taskId: string,
  ): Promise<{ sessionId: string; taskId: string }> {
    // Pre-dispatch validation
    const validationError = this.taskManager.validateDispatch(taskId);
    if (validationError) {
      throw new Error(validationError);
    }

    const task = this.taskManager.getTask(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);

    // Retry loop with exponential backoff
    let lastError: Error | undefined;
    const maxAttempts = task.maxDispatchAttempts;

    for (let attempt = task.dispatchAttempts; attempt < maxAttempts; attempt++) {
      // Apply backoff delay for retries (not the first attempt)
      if (attempt > 0) {
        const delay = this.computeDispatchBackoff(attempt);
        this.transport.sendNotification("log", {
          message: `[dispatch] retry attempt ${attempt + 1}/${maxAttempts} for ${taskId}, backoff ${delay}ms`,
        });
        await new Promise<void>((resolve) => setTimeout(resolve, delay));
      }

      try {
        // NOTE: sendPrompt() internally fire-and-forgets streamMessages().
        // This try/catch only catches synchronous failures (session creation,
        // state transitions). Streaming failures are handled by the adapter's
        // error events + session overflow detection, not by this retry loop.
        // Full streaming retry requires G2 (crash recovery) — see Issue #33/#34.
        const { task: freshTask, prompt, devRolePrompt } = await this.prepareBundleTaskExecution(taskId);

        // Transition: drafted → dispatched → in_progress
        if (freshTask.status === "drafted") {
          this.taskManager.transitionTask(taskId, "dispatched", "orchestrator");
        }

        // Start role-scoped session for assigned agent
        const session = await this.startRoleSession(freshTask.assignedTo, "dev", freshTask.title, devRolePrompt);
        this.taskManager.bindSession(taskId, session.sessionId);

        // Transition to in_progress (only if not already there)
        if (freshTask.status === "dispatched") {
          this.taskManager.transitionTask(taskId, "in_progress", freshTask.assignedTo);
        }

        // Send the prompt with role context
        await this.sendPrompt(
          freshTask.assignedTo,
          prompt,
          undefined,
          "dev",
          freshTask.title,
          devRolePrompt,
          freshTask.taskId,
        );

        // Record successful dispatch attempt
        this.taskManager.recordDispatchAttempt(taskId);
        return { sessionId: session.sessionId, taskId };
      } catch (err) {
        lastError = err instanceof Error ? err : new Error(String(err));
        // Record failed dispatch attempt with error
        this.taskManager.recordDispatchAttempt(taskId, lastError.message);
        this.transport.sendNotification("log", {
          message: `[dispatch] attempt ${attempt + 1}/${maxAttempts} failed for ${taskId}: ${lastError.message}`,
        });
      }
    }

    // All attempts exhausted
    throw new Error(
      `Failed to dispatch task ${taskId} after ${maxAttempts} attempts: ${lastError?.message ?? "unknown error"}`,
    );
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
    const projectRoot = this.getProjectRoot();
    const prompt = buildAcceptancePrompt(task, acceptance, projectRoot);
    const acceptanceRolePrompt = buildAcceptanceRolePrompt(
      task,
      acceptance,
      this.sharedContext || undefined,
      this.getRoleSpecificContext("acceptance"),
      projectRoot,
    );
    const session = await this.startRoleSession(
      acceptorId,
      "acceptance",
      task.title,
      acceptanceRolePrompt,
    );
    this.taskManager.bindSession(taskId, session.sessionId);

    await this.sendPrompt(
      acceptorId,
      prompt,
      undefined,
      "acceptance",
      task.title,
      acceptanceRolePrompt,
      task.taskId,
    );

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
        await this.sendPrompt(
          task.assignedTo,
          reworkPrompt,
          undefined,
          "dev",
          task.title,
          this.buildSystemRolePrompt("dev", task),
          task.taskId,
        );
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
    const preChecks = await this.runPreChecks(task);
    const gitDiff = await this.getGitDiff(task);

    this.taskManager.updateTaskField(taskId, "mainReview", {
      preChecks,
      gitDiff,
    });

    // Auto-trigger main review step
    await this.mainReviewStep(taskId);

    return this.taskManager.getTask(taskId) ?? task;
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
    await this.sendPrompt(mainAgentId, reviewPrompt, undefined, "main", task.title, undefined, taskId);
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
    structured?: StructuredReviewResult,
  ): Promise<{ decision: string; nextAction: string }> {
    const task = this.taskManager.getTask(taskId);
    if (!task) throw new Error(`Task not found: ${taskId}`);
    const hasCriticalFinding = structured?.findings.some((finding) => finding.severity === "critical");
    const effectiveDecision = hasCriticalFinding ? "SEND_BACK" : decision;
    const effectiveReason = hasCriticalFinding
      ? reason ?? this.formatCriticalFindingReason(structured?.findings ?? [])
      : reason;

    if (effectiveDecision === "APPROVE_FOR_ACCEPTANCE") {
      // Find or use provided acceptor
      const effectiveAcceptorId = acceptorId ?? this.findAcceptorAgentId();
      if (!effectiveAcceptorId) {
        throw new Error("No acceptance agent available. Configure an agent with acceptance role.");
      }
      await this.createAcceptanceFlow(taskId, effectiveAcceptorId);
      return { decision: effectiveDecision, nextAction: "acceptance_created" };
    }

    if (effectiveDecision === "SEND_BACK") {
      const { newSession } = this.taskManager.triggerRework(
        taskId,
        effectiveReason ?? "Main Agent review: sent back for rework",
      );

      // Send rework directive to dev agent
      const reworkPrompt = `# Rework Required [${task.taskId}]\n\nMain Agent review returned this task for rework.\n\n**Reason:** ${effectiveReason ?? "Unspecified"}\n\nPlease address and resubmit.`;
      if (newSession) {
        const devRolePrompt = this.buildSystemRolePrompt("dev", task);
        const session = await this.startRoleSession(
          task.assignedTo,
          "dev",
          task.title,
          devRolePrompt,
        );
        this.taskManager.bindSession(taskId, session.sessionId);
      }
      await this.sendPrompt(
        task.assignedTo,
        reworkPrompt,
        undefined,
        "dev",
        task.title,
        this.buildSystemRolePrompt("dev", task),
        task.taskId,
      );

      return { decision: effectiveDecision, nextAction: "rework_triggered" };
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

  private syncRTKCommandWrapper(config: MercuryConfig | null = this.projectConfig): void {
    installRTKCommandWrapper(config?.rtk);
  }

  private async validateRTKConfiguration(
    config: MercuryConfig | null = this.projectConfig,
  ): Promise<void> {
    const rtkConfig = config?.rtk;
    if (!rtkConfig?.enabled) {
      return;
    }

    const available = await isRTKAvailable(rtkConfig);
    if (!available) {
      const binary = rtkConfig.binaryPath?.trim() || "rtk";
      throw new Error(`RTK is enabled but unavailable: ${binary}`);
    }

    this.transport.sendNotification("log", {
      message: `[rtk] Enabled for commands: ${rtkConfig.commands.join(", ") || "(none)"}`,
    });
  }

  /**
   * Persist the current projectConfig to disk using atomic write (write tmp + rename).
   * Errors are logged but never thrown — config persistence is best-effort.
   */
  private async persistConfigToDisk(): Promise<void> {
    if (!this.configFilePath || !this.projectConfig) return;

    const tmpPath = join(dirname(this.configFilePath), `.mercury.config.tmp.${Date.now()}.json`);
    try {
      const backupDir = join(dirname(this.configFilePath), ".mercury", "backups");
      await mkdir(backupDir, { recursive: true });
      const backupStamp = new Date().toISOString().replace(/[:.]/g, "-");
      const backupPath = join(backupDir, `mercury.config.${backupStamp}.json`);
      try {
        await copyFile(this.configFilePath, backupPath);
      } catch {
        // Missing source config is expected on first save.
      }

      // Retain only the 10 most recent backups
      const MAX_BACKUPS = 10;
      try {
        const files = await readdir(backupDir);
        const backups = files
          .filter((f: string) => f.startsWith("mercury.config."))
          .sort()
          .reverse();
        for (const old of backups.slice(MAX_BACKUPS)) {
          await unlink(join(backupDir, old)).catch(() => {});
        }
      } catch { /* cleanup failure is non-fatal */ }

      const json = JSON.stringify(this.projectConfig, null, 2) + "\n";
      await writeFile(tmpPath, json, "utf-8");
      await rename(tmpPath, this.configFilePath);
    } catch (err) {
      // Clean up tmp file on failure
      try { await unlink(tmpPath); } catch { /* ignore */ }
      const msg = err instanceof Error ? err.message : String(err);
      this.transport.sendNotification("log", {
        message: `[config] Warning: failed to persist config to ${this.configFilePath}: ${msg}`,
      });
    }
  }

  private async updateConfig(config: MercuryConfig): Promise<{ ok: true }> {
    await this.validateRTKConfiguration(config);

    const prevAutoInject = this.projectConfig?.obsidian?.autoInjectContext;
    const prevContextFiles = this.projectConfig?.obsidian?.contextFiles?.join(",");
    const prevRoleContextFiles = JSON.stringify(this.projectConfig?.obsidian?.roleContextFiles ?? {});

    this.projectConfig = config;
    this.syncRTKCommandWrapper(config);

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
    const newRoleContextFiles = JSON.stringify(config.obsidian?.roleContextFiles ?? {});
    const obsidianChanged =
      newAutoInject !== prevAutoInject ||
      newContextFiles !== prevContextFiles ||
      newRoleContextFiles !== prevRoleContextFiles;

    if (obsidianChanged && !newAutoInject && this.getContextCacheLength() > 0) {
      this.clearContextCache();
    }
    // Note: role-specific prompts are injected per-session via startRoleSession(),
    // so no need to re-inject into all adapters here.

    // Rebuild context from KB if obsidian settings changed and autoInject is on
    if (obsidianChanged && newAutoInject) {
      await this.buildAndInjectContext();
    }

    // Persist updated config to disk so changes survive restarts
    await this.persistConfigToDisk();

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
