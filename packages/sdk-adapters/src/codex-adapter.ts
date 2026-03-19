/**
 * Codex app-server adapter.
 *
 * Uses `codex app-server` over stdio JSON-RPC instead of the deprecated SDK
 * thread API.
 */

import { randomUUID } from "node:crypto";
import { readFile, unlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  AgentRole,
  ImageAttachment,
  SessionInfo,
  SlashCommand,
} from "@mercury/core";
import { CodexAppServerTransport } from "./codex-app-server-transport.js";
import type {
  AskForApproval,
  CodexAppServerNotificationMap,
  CodexAppServerNotificationMethod,
  CodexAppServerServerRequestMap,
  CodexAppServerServerRequestMethod,
  CommandAction,
  CommandExecutionApprovalDecision,
  CommandExecutionRequestApprovalParams,
  FileUpdateChange,
  FileChangeApprovalDecision,
  FileChangeRequestApprovalParams,
  SandboxMode,
  Thread,
  ThreadListResponse,
  ThreadItem,
  ThreadStatus,
  UserInput,
} from "./codex-app-server-types.js";

const CODEX_COMPACTION_NOTICE =
  "Context compaction triggered — role boundary instructions from earlier turns may have been summarized. Current turn still carries full role context via prepend.";
const DEFAULT_APPROVAL_POLICY: AskForApproval = "on-request";
const DEFAULT_SANDBOX_MODE = "workspace-write";
const END_OF_STREAM = Symbol("codex-turn-end");
const NOTIFICATION_OPTOUTS: string[] = [
  "item/agentMessage/delta",
  "item/reasoning/textDelta",
  "item/reasoning/summaryTextDelta",
  "item/reasoning/summaryPartAdded",
  "item/commandExecution/outputDelta",
  "item/fileChange/outputDelta",
];

type InputEntry = UserInput;
type LocalAgentApprovalRequest = {
  kind: "permission" | "tool_use" | "command_execution" | "file_change" | "user_input";
  toolName?: string;
  summary: string;
  rawRequest?: Record<string, unknown>;
};
type LocalApprovalDecision = {
  action: "approve" | "deny";
  reason?: string;
};
type LocalAgentSendHooks = {
  onApprovalRequest?: (request: LocalAgentApprovalRequest) => Promise<LocalApprovalDecision>;
};

interface NativeSession {
  info: SessionInfo;
  cwd: string;
  threadId: string;
  loaded: boolean;
  activeTurn: MessageStream | null;
}

class MessageStream {
  private queue: Array<AgentMessage | Error | typeof END_OF_STREAM> = [];
  private waiters: Array<(value: AgentMessage | Error | typeof END_OF_STREAM) => void> = [];
  private ended = false;
  private compactionNotified = false;
  readonly hooks?: LocalAgentSendHooks;
  turnId?: string;

  constructor(hooks?: LocalAgentSendHooks) {
    this.hooks = hooks;
  }

  setTurnId(turnId: string): void {
    this.turnId = turnId;
  }

  push(message: AgentMessage): void {
    if (this.ended) return;
    this.deliver(message);
  }

  pushCompactionNotice(buildNotice: () => AgentMessage): void {
    if (this.compactionNotified || this.ended) return;
    this.compactionNotified = true;
    this.push(buildNotice());
  }

  fail(error: Error): void {
    if (this.ended) return;
    this.ended = true;
    this.deliver(error);
  }

  finish(): void {
    if (this.ended) return;
    this.ended = true;
    this.deliver(END_OF_STREAM);
  }

  async *iterate(): AsyncGenerator<AgentMessage> {
    while (true) {
      const next = await this.next();
      if (next === END_OF_STREAM) return;
      if (next instanceof Error) throw next;
      yield next;
    }
  }

  private next(): Promise<AgentMessage | Error | typeof END_OF_STREAM> {
    const item = this.queue.shift();
    if (item !== undefined) {
      return Promise.resolve(item);
    }
    return new Promise((resolve) => {
      this.waiters.push(resolve);
    });
  }

  private deliver(item: AgentMessage | Error | typeof END_OF_STREAM): void {
    const waiter = this.waiters.shift();
    if (waiter) {
      waiter(item);
      return;
    }
    this.queue.push(item);
  }
}

export class CodexAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;

  private sessions = new Map<string, NativeSession>();
  private threadSessions = new Map<string, string>();
  private sharedSystemPrompt?: string;
  private transport: CodexAppServerTransport | null = null;

  constructor(config?: Partial<AgentConfig>) {
    this.config = {
      id: "codex-cli",
      displayName: "Codex CLI",
      cli: "codex",
      roles: ["dev"],
      integration: "rpc",
      capabilities: ["code", "batch_json", "test"],
      restrictions: ["no_kb_write", "isolated_branch_only"],
      maxConcurrentSessions: 3,
      ...config,
    };
    this.agentId = this.config.id;
  }

  setSystemPrompt(prompt: string): void {
    this.sharedSystemPrompt = prompt;
  }

  async startSession(cwd: string): Promise<SessionInfo> {
    const transport = await this.ensureTransport();
    const response = await transport.request("thread/start", {
      model: this.config.model ?? null,
      cwd,
      approvalPolicy: DEFAULT_APPROVAL_POLICY,
      sandbox: DEFAULT_SANDBOX_MODE,
      experimentalRawEvents: false,
      persistExtendedHistory: true,
    });

    const sessionId = randomUUID();
    const info: SessionInfo = {
      sessionId,
      agentId: this.agentId,
      cwd: response.cwd ?? cwd,
      startedAt: response.thread.createdAt * 1000,
      lastActiveAt: Date.now(),
      status: "active",
      resumeToken: response.thread.id,
      sessionName: response.thread.name ?? undefined,
    };

    this.bindSession(sessionId, {
      info,
      cwd: info.cwd ?? cwd,
      threadId: response.thread.id,
      loaded: true,
      activeTurn: null,
    });

    return info;
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
    hooks?: LocalAgentSendHooks,
  ): AsyncGenerator<AgentMessage> {
    const trimmed = prompt.trim();
    if (trimmed.startsWith("/")) {
      let handled = false;
      for await (const message of this.handleSlashCommand(sessionId, prompt)) {
        handled = true;
        yield message;
      }
      if (handled) return;
    }

    const record = await this.ensureSession(sessionId, this.sessions.get(sessionId)?.info);
    if (record.activeTurn) {
      throw new Error(`Codex session ${sessionId} already has an active turn`);
    }

    record.info.lastActiveAt = Date.now();

    const promptContext = record.info.frozenSystemPrompt ?? this.sharedSystemPrompt;
    const effectivePrompt = promptContext
      ? `[Mercury Role Context]\n${promptContext}\n\n[User Prompt]\n${prompt}`
      : prompt;

    let input: InputEntry[] = [{ type: "text", text: effectivePrompt, text_elements: [] }];
    const tempFiles: string[] = [];
    if (images && images.length > 0) {
      const prepared = await this.imagesToTempFiles(images);
      input = input.concat(prepared.entries);
      tempFiles.push(...prepared.tempFiles);
    }

    const stream = new MessageStream(hooks);
    record.activeTurn = stream;

    try {
      const response = await (await this.ensureTransport()).request("turn/start", {
        threadId: record.threadId,
        input,
        cwd: record.cwd,
        approvalPolicy: DEFAULT_APPROVAL_POLICY,
        model: this.config.model ?? null,
      });
      stream.setTurnId(response.turn.id);

      if (response.turn.status === "failed" && response.turn.error) {
        stream.fail(new Error(response.turn.error.message));
      }
      if (response.turn.status === "completed" || response.turn.status === "interrupted") {
        stream.finish();
      }

      for await (const message of stream.iterate()) {
        yield message;
      }
    } finally {
      await this.cleanupTempFiles(tempFiles);
      record.info.lastActiveAt = Date.now();
      if (record.activeTurn === stream) {
        record.activeTurn = null;
      }
    }
  }

  async resumeSession(
    sessionId: string,
    persistedInfo?: SessionInfo,
    cwd?: string,
  ): Promise<SessionInfo> {
    const existing = this.sessions.get(sessionId);
    if (existing?.loaded) {
      existing.info.status = "active";
      existing.info.lastActiveAt = Date.now();
      return existing.info;
    }

    let info = existing?.info ?? persistedInfo;
    if (!info) {
      info = await this.getNativeSessionInfo(sessionId, cwd) ?? undefined;
    }
    if (!info?.resumeToken) {
      throw new Error(`Session ${sessionId} is missing a Codex thread id`);
    }

    const effectiveCwd = existing?.cwd ?? info.cwd ?? cwd ?? process.cwd();
    const response = await (await this.ensureTransport()).request("thread/resume", {
      threadId: info.resumeToken,
      model: this.config.model ?? null,
      cwd: effectiveCwd,
      approvalPolicy: DEFAULT_APPROVAL_POLICY,
      sandbox: DEFAULT_SANDBOX_MODE,
      persistExtendedHistory: true,
    });

    const mergedInfo: SessionInfo = {
      ...info,
      sessionId,
      agentId: this.agentId,
      cwd: response.cwd ?? effectiveCwd,
      startedAt: info.startedAt ?? response.thread.createdAt * 1000,
      lastActiveAt: Date.now(),
      status: "active",
      resumeToken: response.thread.id,
      sessionName: info.sessionName ?? response.thread.name ?? undefined,
    };

    this.bindSession(sessionId, {
      info: mergedInfo,
      cwd: mergedInfo.cwd ?? effectiveCwd,
      threadId: response.thread.id,
      loaded: true,
      activeTurn: existing?.activeTurn ?? null,
    });

    return mergedInfo;
  }

  async endSession(sessionId: string): Promise<void> {
    const record = this.sessions.get(sessionId);
    if (!record) return;

    record.info.status = "completed";
    record.info.lastActiveAt = Date.now();
    record.activeTurn?.fail(new Error("Codex session ended"));
    record.activeTurn = null;

    try {
      const transport = await this.ensureTransport();
      await transport.request("thread/unsubscribe", { threadId: record.threadId });
    } catch {
      // Best effort only.
    }

    this.threadSessions.delete(record.threadId);
    this.sessions.delete(sessionId);
  }

  async executeOneShot(
    prompt: string,
    cwd: string,
    options?: { model?: string; sandbox?: string; approvalPolicy?: unknown },
  ): Promise<{ messages: AgentMessage[]; finalMessage: string; threadId: string }> {
    const transport = await this.ensureTransport();
    const response = await transport.request("thread/start", {
      model: options?.model ?? this.config.model ?? null,
      cwd,
      approvalPolicy: (options?.approvalPolicy as AskForApproval | undefined) ?? DEFAULT_APPROVAL_POLICY,
      sandbox: (options?.sandbox as SandboxMode | undefined) ?? DEFAULT_SANDBOX_MODE,
      experimentalRawEvents: false,
      persistExtendedHistory: false,
    });

    const threadId = response.thread.id;
    const sessionId = threadId;
    const record: NativeSession = {
      info: {
        sessionId,
        agentId: this.agentId,
        cwd: response.cwd ?? cwd,
        startedAt: response.thread.createdAt * 1000,
        lastActiveAt: Date.now(),
        status: "active",
        resumeToken: threadId,
        sessionName: response.thread.name ?? undefined,
      },
      cwd: response.cwd ?? cwd,
      threadId,
      loaded: true,
      activeTurn: null,
    };
    this.bindSession(sessionId, record);

    const stream = new MessageStream();
    record.activeTurn = stream;
    const messages: AgentMessage[] = [];

    try {
      const turnResponse = await transport.request("turn/start", {
        threadId,
        input: [{ type: "text", text: prompt, text_elements: [] }],
        cwd: record.cwd,
        approvalPolicy: (options?.approvalPolicy as AskForApproval | undefined) ?? DEFAULT_APPROVAL_POLICY,
        model: options?.model ?? this.config.model ?? null,
      });
      stream.setTurnId(turnResponse.turn.id);

      if (turnResponse.turn.status === "failed" && turnResponse.turn.error) {
        stream.fail(new Error(turnResponse.turn.error.message));
      }
      if (
        turnResponse.turn.status === "completed" ||
        turnResponse.turn.status === "interrupted"
      ) {
        stream.finish();
      }

      for await (const message of stream.iterate()) {
        messages.push(message);
      }

      record.info.lastActiveAt = Date.now();
      record.info.status = "completed";

      return {
        messages,
        finalMessage: this.findFinalAssistantMessage(messages),
        threadId,
      };
    } finally {
      record.activeTurn = null;
      try {
        await transport.request("thread/unsubscribe", { threadId });
      } catch {
        // Best effort only.
      }
      this.threadSessions.delete(threadId);
      this.sessions.delete(sessionId);
    }
  }

  async handoffSession(oldSessionId: string, _summary: string): Promise<SessionInfo> {
    const oldSession = this.sessions.get(oldSessionId)?.info;
    if (oldSession) {
      oldSession.status = "overflow";
    }

    const cwd = this.sessions.get(oldSessionId)?.cwd ?? oldSession?.cwd ?? process.cwd();
    const nextSession = await this.startSession(cwd);
    nextSession.parentSessionId = oldSessionId;
    nextSession.role = oldSession?.role;
    nextSession.frozenRole = oldSession?.frozenRole;
    nextSession.frozenSystemPrompt = oldSession?.frozenSystemPrompt;
    nextSession.baseRolePromptHash = oldSession?.baseRolePromptHash;
    nextSession.promptHash = oldSession?.promptHash;
    return nextSession;
  }

  async listNativeSessions(cwd?: string): Promise<SessionInfo[]> {
    const transport = await this.ensureTransport();
    const sessions: SessionInfo[] = [];
    const seen = new Set<string>();
    let cursor: string | null = null;

    do {
      const response: ThreadListResponse = await transport.request("thread/list", {
        cursor,
        limit: 50,
        sourceKinds: ["appServer"],
        cwd: cwd ?? null,
        archived: false,
      });
      for (const thread of response.data) {
        if (seen.has(thread.id)) continue;
        seen.add(thread.id);
        sessions.push(this.buildSessionInfoFromThread(thread, thread.id));
      }
      cursor = response.nextCursor;
    } while (cursor);

    return sessions;
  }

  async getNativeSessionInfo(sessionId: string, cwd?: string): Promise<SessionInfo | null> {
    const threadId = this.resolveThreadId(sessionId);
    const response = await (await this.ensureTransport()).request("thread/read", {
      threadId,
      includeTurns: false,
    });

    if (cwd && response.thread.cwd !== cwd) {
      return null;
    }

    const session = this.buildSessionInfoFromThread(response.thread, sessionId);
    session.resumeToken = response.thread.id;
    return session;
  }

  async readNativeMessages(sessionId: string): Promise<AgentMessage[]> {
    const threadId = this.resolveThreadId(sessionId);
    const response = await (await this.ensureTransport()).request("thread/read", {
      threadId,
      includeTurns: true,
    });

    const messages: AgentMessage[] = [];
    let compactionNotified = false;
    for (const turn of response.thread.turns) {
      for (const item of turn.items) {
        if (item.type === "contextCompaction") {
          if (!compactionNotified) {
            messages.push(this.buildCompactionNotice({ itemId: item.id, itemType: item.type }));
            compactionNotified = true;
          }
          continue;
        }
        if (!compactionNotified && this.isCompactionSignal(this.getCompactionSignalText(item))) {
          messages.push(this.buildCompactionNotice());
          compactionNotified = true;
        }
        const message = this.mapThreadItemToMessage(item);
        if (message) {
          messages.push(message);
        }
      }
    }

    return messages;
  }

  async setSessionName(sessionId: string, name: string): Promise<void> {
    const threadId = this.resolveThreadId(sessionId);
    await (await this.ensureTransport()).request("thread/name/set", {
      threadId,
      name,
    });
    const record = this.sessions.get(sessionId);
    if (record) {
      record.info.sessionName = name;
    }
  }

  async listModels(): Promise<{ id: string; name: string }[]> {
    // Read the model cache that the Codex CLI maintains at ~/.codex/models_cache.json
    // This ensures the GUI shows exactly the same models as the CLI.
    try {
      const cachePath = join(homedir(), ".codex", "models_cache.json");
      const raw = await readFile(cachePath, "utf8");
      const data = JSON.parse(raw) as {
        models: { slug: string; display_name: string; visibility?: string }[];
      };
      return data.models
        .filter((m) => m.visibility === "list")
        .map((m) => ({ id: m.slug, name: m.display_name }));
    } catch {
      // Fallback if cache file is missing or unreadable
      return [
        { id: "gpt-5.4", name: "GPT-5.4" },
        { id: "gpt-5.4-mini", name: "GPT-5.4 Mini" },
        { id: "gpt-5.3-codex", name: "GPT-5.3 Codex" },
      ];
    }
  }

  setModel(model: string): void {
    this.config.model = model;
  }

  getSlashCommands(): SlashCommand[] {
    return [
      { name: "/new", description: "Start a new chat during a conversation", category: "session" },
      { name: "/resume", description: "Resume a saved chat", category: "session" },
      { name: "/fork", description: "Fork the current chat into a new thread", category: "session" },
      { name: "/clear", description: "Clear the terminal and start a new chat", category: "session" },
      { name: "/rename", description: "Rename the current thread", category: "session" },
      { name: "/compact", description: "Summarize conversation to prevent hitting context limit", category: "session" },
      { name: "/quit", description: "Exit Codex", category: "session" },
      { name: "/model", description: "Choose what model and reasoning effort to use", category: "model" },
      { name: "/fast", description: "Toggle Fast mode for fastest inference at 2X plan usage", category: "model" },
      { name: "/plan", description: "Switch to Plan mode", category: "model" },
      { name: "/collab", description: "Change collaboration mode (experimental)", category: "model" },
      { name: "/approvals", description: "Choose what Codex is allowed to do", category: "permissions" },
      { name: "/setup-default-sandbox", description: "Set up elevated agent sandbox", category: "permissions" },
      { name: "/sandbox-add-read-dir", description: "Let sandbox read a directory", category: "permissions", args: [{ name: "path", description: "Absolute directory path", required: true, type: "string" }] },
      { name: "/mcp", description: "List configured MCP tools", category: "integrations" },
      { name: "/apps", description: "Manage apps (connectors)", category: "integrations" },
      { name: "/skills", description: "Use skills to improve how Codex performs tasks", category: "integrations" },
      { name: "/mention", description: "Mention or attach a file", category: "integrations" },
      { name: "/agent", description: "Switch the active agent thread", category: "agents" },
      { name: "/review", description: "Review current changes and find issues", category: "code" },
      { name: "/diff", description: "Show git diff including untracked files", category: "code" },
      { name: "/copy", description: "Copy the latest Codex output to clipboard", category: "code" },
      { name: "/init", description: "Create an AGENTS.md file with instructions for Codex", category: "code" },
      { name: "/status", description: "Show current session configuration and token usage", category: "config" },
      { name: "/statusline", description: "Configure which items appear in the status line", category: "config" },
      { name: "/theme", description: "Choose a syntax highlighting theme", category: "config" },
      { name: "/personality", description: "Choose a communication style for Codex", category: "config" },
      { name: "/settings", description: "Configure realtime microphone and speaker", category: "config" },
      { name: "/experimental", description: "Toggle experimental features", category: "config" },
      { name: "/realtime", description: "Toggle realtime voice mode (experimental)", category: "experimental" },
      { name: "/ps", description: "List background terminals", category: "processes" },
      { name: "/clean", description: "Stop all background terminals", category: "processes" },
      { name: "/logout", description: "Log out of Codex", category: "account" },
      { name: "/feedback", description: "Send logs to maintainers", category: "account" },
    ];
  }

  private async ensureTransport(): Promise<CodexAppServerTransport> {
    if (!this.transport) {
      this.transport = new CodexAppServerTransport({
        clientInfo: {
          name: "mercury",
          title: "Mercury",
          version: "0.1.0",
        },
        capabilities: {
          experimentalApi: true,
          optOutNotificationMethods: NOTIFICATION_OPTOUTS,
        },
        onNotification: (method, params) => this.handleNotification(method, params),
        onServerRequest: (method, params) => this.handleServerRequest(method, params),
        onTransportError: (error) => this.handleTransportError(error),
      });
    }
    await this.transport.ensureStarted();
    return this.transport;
  }

  private bindSession(sessionId: string, record: NativeSession): void {
    this.sessions.set(sessionId, record);
    this.threadSessions.set(record.threadId, sessionId);
  }

  private resolveThreadId(sessionId: string): string {
    return this.sessions.get(sessionId)?.threadId ?? sessionId;
  }

  private async ensureSession(sessionId: string, persistedInfo?: SessionInfo): Promise<NativeSession> {
    await this.resumeSession(sessionId, persistedInfo, persistedInfo?.cwd);
    const record = this.sessions.get(sessionId);
    if (!record) {
      throw new Error(`Codex session ${sessionId} not found`);
    }
    return record;
  }

  private handleTransportError(error: Error): void {
    for (const session of this.sessions.values()) {
      session.loaded = false;
      session.activeTurn?.fail(error);
      session.activeTurn = null;
    }
    this.transport = null;
  }

  private async handleNotification(
    method: CodexAppServerNotificationMethod,
    params: CodexAppServerNotificationMap[CodexAppServerNotificationMethod],
  ): Promise<void> {
    switch (method) {
      case "thread/started": {
        const payload = params as CodexAppServerNotificationMap["thread/started"];
        const sessionId = this.threadSessions.get(payload.thread.id);
        if (!sessionId) return;
        const record = this.sessions.get(sessionId);
        if (!record) return;
        record.loaded = true;
        record.info.resumeToken = payload.thread.id;
        record.info.sessionName = payload.thread.name ?? record.info.sessionName;
        return;
      }
      case "thread/status/changed": {
        const payload = params as CodexAppServerNotificationMap["thread/status/changed"];
        const record = this.getSessionByThreadId(payload.threadId);
        if (!record) return;
        record.info.status = this.mapThreadStatus(payload.status);
        return;
      }
      case "thread/closed": {
        const payload = params as CodexAppServerNotificationMap["thread/closed"];
        const record = this.getSessionByThreadId(payload.threadId);
        if (!record) return;
        record.loaded = false;
        record.info.status = "completed";
        record.activeTurn?.finish();
        record.activeTurn = null;
        return;
      }
      case "thread/tokenUsage/updated": {
        const payload = params as CodexAppServerNotificationMap["thread/tokenUsage/updated"];
        const record = this.getSessionByThreadId(payload.threadId);
        if (!record) return;
        record.info.tokenUsage = payload.tokenUsage.total.totalTokens;
        record.info.tokenLimit = payload.tokenUsage.modelContextWindow ?? undefined;
        record.info.lastActiveAt = Date.now();
        return;
      }
      case "turn/started": {
        const payload = params as CodexAppServerNotificationMap["turn/started"];
        const turn = this.getTurnStream(payload.threadId, payload.turn.id);
        turn?.setTurnId(payload.turn.id);
        return;
      }
      case "item/completed": {
        const payload = params as CodexAppServerNotificationMap["item/completed"];
        const turn = this.getTurnStream(payload.threadId, payload.turnId);
        if (!turn) return;
        if (payload.item.type === "contextCompaction") {
          turn.pushCompactionNotice(() =>
            this.buildCompactionNotice({ itemId: payload.item.id, itemType: payload.item.type }),
          );
          return;
        }
        if (this.isCompactionSignal(this.getCompactionSignalText(payload.item))) {
          turn.pushCompactionNotice(() => this.buildCompactionNotice());
        }
        const message = this.mapThreadItemToMessage(payload.item);
        if (message) {
          turn.push(message);
        }
        return;
      }
      case "thread/compacted": {
        const payload = params as CodexAppServerNotificationMap["thread/compacted"];
        const turn = this.getTurnStream(payload.threadId, payload.turnId);
        turn?.pushCompactionNotice(() => this.buildCompactionNotice({ itemType: "contextCompaction" }));
        return;
      }
      case "turn/completed": {
        const payload = params as CodexAppServerNotificationMap["turn/completed"];
        const record = this.getSessionByThreadId(payload.threadId);
        if (record) {
          record.info.lastActiveAt = Date.now();
        }
        const turn = this.getTurnStream(payload.threadId, payload.turn.id);
        if (!turn) return;
        turn.setTurnId(payload.turn.id);
        if (payload.turn.status === "failed" && payload.turn.error) {
          turn.fail(new Error(payload.turn.error.message));
          return;
        }
        turn.finish();
        return;
      }
      case "error": {
        const payload = params as CodexAppServerNotificationMap["error"];
        if (payload.willRetry) return;
        const turn = this.getTurnStream(payload.threadId, payload.turnId);
        turn?.fail(new Error(payload.error.message));
        return;
      }
      case "item/started":
      case "turn/plan/updated":
      case "turn/diff/updated":
      case "serverRequest/resolved":
        return;
      default:
        return;
    }
  }

  private async handleServerRequest<M extends CodexAppServerServerRequestMethod>(
    method: M,
    params: CodexAppServerServerRequestMap[M]["params"],
  ): Promise<CodexAppServerServerRequestMap[M]["result"]> {
    switch (method) {
      case "item/commandExecution/requestApproval":
        return {
          decision: await this.resolveCommandApproval(params),
        } as CodexAppServerServerRequestMap[M]["result"];
      case "item/fileChange/requestApproval":
        return {
          decision: await this.resolveFileChangeApproval(params),
        } as CodexAppServerServerRequestMap[M]["result"];
      default:
        throw new Error(`Unsupported Codex server request: ${method}`);
    }
  }

  private async resolveCommandApproval(
    params: CommandExecutionRequestApprovalParams,
  ): Promise<CommandExecutionApprovalDecision> {
    const decision = await this.requestApproval(params.threadId, {
      kind: "command_execution",
      toolName: "codex.command_execution",
      summary: this.buildCommandApprovalSummary(params),
      rawRequest: params as unknown as Record<string, unknown>,
    });
    return decision.action === "approve" ? "accept" : "decline";
  }

  private async resolveFileChangeApproval(
    params: FileChangeRequestApprovalParams,
  ): Promise<FileChangeApprovalDecision> {
    const decision = await this.requestApproval(params.threadId, {
      kind: "file_change",
      toolName: "codex.file_change",
      summary: this.buildFileApprovalSummary(params),
      rawRequest: params as unknown as Record<string, unknown>,
    });
    return decision.action === "approve" ? "accept" : "decline";
  }

  private async requestApproval(
    threadId: string,
    request: LocalAgentApprovalRequest,
  ): Promise<{ action: "approve" | "deny" }> {
    const turn = this.getTurnStream(threadId);
    const callback = turn?.hooks?.onApprovalRequest;
    if (!callback) {
      return { action: "approve" };
    }

    try {
      return await callback(request);
    } catch {
      return { action: "deny" };
    }
  }

  private getSessionByThreadId(threadId: string): NativeSession | undefined {
    const sessionId = this.threadSessions.get(threadId);
    return sessionId ? this.sessions.get(sessionId) : undefined;
  }

  private getTurnStream(threadId: string, turnId?: string): MessageStream | undefined {
    const record = this.getSessionByThreadId(threadId);
    const stream = record?.activeTurn;
    if (!stream) return undefined;
    if (turnId && stream.turnId && stream.turnId !== turnId) {
      return undefined;
    }
    if (turnId && !stream.turnId) {
      stream.setTurnId(turnId);
    }
    return stream;
  }

  private buildSessionInfoFromThread(thread: Thread, sessionId: string): SessionInfo {
    const role = this.inferRoleFromThread(thread);
    return {
      sessionId,
      agentId: this.agentId,
      role,
      frozenRole: role,
      sessionName: thread.name ?? undefined,
      cwd: thread.cwd,
      startedAt: thread.createdAt * 1000,
      lastActiveAt: thread.updatedAt * 1000,
      tokenUsage: undefined,
      tokenLimit: undefined,
      status: this.mapThreadStatus(thread.status),
      resumeToken: thread.id,
    };
  }

  private inferRoleFromThread(thread: Thread): AgentRole | undefined {
    const candidate = thread.name?.split("-", 1)[0] ?? thread.agentRole ?? undefined;
    if (
      candidate === "main" ||
      candidate === "dev" ||
      candidate === "acceptance" ||
      candidate === "research" ||
      candidate === "design"
    ) {
      return candidate;
    }
    return undefined;
  }

  private mapThreadStatus(status: ThreadStatus): SessionInfo["status"] {
    switch (status.type) {
      case "active":
      case "idle":
        return "active";
      case "notLoaded":
        return "paused";
      case "systemError":
        return "completed";
      default:
        return "active";
    }
  }

  private mapThreadItemToMessage(item: ThreadItem): AgentMessage | null {
    switch (item.type) {
      case "userMessage": {
        const content = item.content
          .map((entry) => this.describeUserInput(entry))
          .filter((entry): entry is string => Boolean(entry))
          .join("\n")
          .trim();
        return this.createThreadItemMessage(
          item,
          "user",
          content || "User input received.",
          { inputTypes: item.content.map((entry) => entry.type) },
        );
      }
      case "agentMessage":
        return this.createThreadItemMessage(
          item,
          "assistant",
          item.text || "Assistant message received.",
          { phase: item.phase },
        );
      case "reasoning": {
        const content = item.content.join("\n").trim() || item.summary.join("\n").trim();
        return this.createThreadItemMessage(
          item,
          "assistant",
          content || "Reasoning update received.",
          {
            summary: item.summary,
            contentParts: item.content.length,
          },
        );
      }
      case "plan":
        return this.createThreadItemMessage(
          item,
          "system",
          item.text || "Plan updated.",
          { messageType: "plan_update" },
        );
      case "commandExecution": {
        const actions = this.describeCommandActions(item.commandActions);
        const lines = [
          `Command: ${item.command}`,
          `Status: ${item.status}`,
          `Exit code: ${item.exitCode ?? "pending"}`,
          `Working directory: ${item.cwd}`,
          item.processId ? `Process ID: ${item.processId}` : null,
          item.durationMs !== null ? `Duration: ${item.durationMs} ms` : null,
          actions ? `Actions: ${actions}` : null,
          this.formatLabeledBlock("Output", this.truncateText(item.aggregatedOutput ?? "", 2000)),
        ].filter((line): line is string => Boolean(line));
        return this.createThreadItemMessage(
          item,
          "system",
          lines.join("\n"),
          {
            status: item.status,
            command: item.command,
            cwd: item.cwd,
            processId: item.processId,
            exitCode: item.exitCode,
            durationMs: item.durationMs,
            commandActions: item.commandActions,
          },
        );
      }
      case "fileChange": {
        const changes = item.changes.map((change) => this.describeFileChange(change));
        const content =
          changes.length > 0
            ? changes.map((change) => `- ${change}`).join("\n")
            : "No file paths reported.";
        return this.createThreadItemMessage(
          item,
          "system",
          `File changes (${item.status})\n${content}`,
          {
            status: item.status,
            changeCount: item.changes.length,
            changes: item.changes.map((change) => this.buildFileChangeMetadata(change)),
          },
        );
      }
      case "mcpToolCall": {
        const lines = [
          `MCP tool: ${item.server}.${item.tool}`,
          `Status: ${item.status}`,
          item.durationMs !== null ? `Duration: ${item.durationMs} ms` : null,
          this.formatLabeledBlock("Arguments", this.serializeForMessage(item.arguments, 1000)),
          item.error !== null
            ? this.formatLabeledBlock("Error", this.serializeForMessage(item.error, 1000))
            : this.formatLabeledBlock("Result", this.serializeForMessage(item.result, 1000)),
        ].filter((line): line is string => Boolean(line));
        return this.createThreadItemMessage(
          item,
          "system",
          lines.join("\n"),
          {
            server: item.server,
            tool: item.tool,
            status: item.status,
            durationMs: item.durationMs,
            hasError: item.error !== null,
          },
        );
      }
      case "dynamicToolCall": {
        const lines = [
          `Dynamic tool: ${item.tool}`,
          `Status: ${item.status}`,
          item.success !== null ? `Success: ${item.success}` : null,
          item.durationMs !== null ? `Duration: ${item.durationMs} ms` : null,
          this.formatLabeledBlock("Arguments", this.serializeForMessage(item.arguments, 1000)),
          this.formatLabeledBlock("Content", this.serializeForMessage(item.contentItems, 1000)),
        ].filter((line): line is string => Boolean(line));
        return this.createThreadItemMessage(
          item,
          "system",
          lines.join("\n"),
          {
            tool: item.tool,
            status: item.status,
            success: item.success,
            durationMs: item.durationMs,
          },
        );
      }
      case "webSearch": {
        const lines = [
          `Web search: ${item.query}`,
          this.formatLabeledBlock("Action", this.serializeForMessage(item.action, 1000)),
        ].filter((line): line is string => Boolean(line));
        return this.createThreadItemMessage(item, "system", lines.join("\n"), {
          query: item.query,
        });
      }
      case "imageView":
        return this.createThreadItemMessage(
          item,
          "system",
          `Image viewed: ${item.path}`,
          { path: item.path },
        );
      case "imageGeneration": {
        const lines = [
          `Image generation status: ${item.status}`,
          item.revisedPrompt ? `Revised prompt: ${item.revisedPrompt}` : null,
          this.formatLabeledBlock("Result", this.serializeForMessage(item.result, 1000)),
        ].filter((line): line is string => Boolean(line));
        return this.createThreadItemMessage(
          item,
          "assistant",
          lines.join("\n"),
          {
            status: item.status,
            revisedPrompt: item.revisedPrompt,
            result: item.result,
          },
        );
      }
      case "enteredReviewMode":
        return this.createThreadItemMessage(
          item,
          "system",
          item.review ? `Entered review mode: ${item.review}` : "Entered review mode.",
          { review: item.review },
        );
      case "exitedReviewMode":
        return this.createThreadItemMessage(
          item,
          "system",
          item.review ? `Exited review mode: ${item.review}` : "Exited review mode.",
          { review: item.review },
        );
      case "contextCompaction": {
        const notice = this.buildCompactionNotice({ itemId: item.id, itemType: item.type });
        return {
          ...notice,
          metadata: {
            ...notice.metadata,
            itemId: item.id,
            itemType: item.type,
          },
        };
      }
      default:
        return null;
    }
  }

  private createThreadItemMessage(
    item: ThreadItem,
    role: AgentMessage["role"],
    content: string,
    metadata?: Record<string, unknown>,
  ): AgentMessage {
    return {
      role,
      content: content.trim() || `${item.type} event`,
      timestamp: Date.now(),
      metadata: {
        itemId: item.id,
        itemType: item.type,
        ...metadata,
      },
    };
  }

  private findFinalAssistantMessage(messages: AgentMessage[]): string {
    const finalPhaseMessage = [...messages]
      .reverse()
      .find(
        (message) =>
          message.role === "assistant" &&
          message.metadata?.phase === "final" &&
          typeof message.content === "string" &&
          message.content.trim().length > 0,
      );
    if (finalPhaseMessage) {
      return finalPhaseMessage.content;
    }

    const lastAssistantMessage = [...messages]
      .reverse()
      .find(
        (message) =>
          message.role === "assistant" &&
          typeof message.content === "string" &&
          message.content.trim().length > 0,
      );
    return lastAssistantMessage?.content ?? "";
  }

  private normalizeCompactionText(text: string): string {
    return text.toLowerCase().replace(/\s+/g, " ").trim();
  }

  private isCompactionSignal(text?: string): boolean {
    if (!text) return false;
    const normalized = this.normalizeCompactionText(text);
    const hasDirectCue =
      normalized.includes("compaction") ||
      normalized.includes("compact conversation") ||
      normalized.includes("context compact") ||
      normalized.includes("conversation compact");
    const hasTruncationCue =
      normalized.includes("truncat") &&
      (normalized.includes("context") || normalized.includes("conversation"));
    return hasDirectCue || hasTruncationCue;
  }

  private getCompactionSignalText(item: ThreadItem): string | undefined {
    switch (item.type) {
      case "agentMessage":
        return item.text;
      case "reasoning":
        return item.content.join("\n").trim() || item.summary.join("\n").trim();
      default:
        return undefined;
    }
  }

  private describeUserInput(entry: UserInput): string | null {
    switch (entry.type) {
      case "text":
        return entry.text.trim() || null;
      case "localImage":
        return `[Local image] ${entry.path}`;
      case "image":
        return `[Image] ${entry.url}`;
      case "skill":
        return `[Skill] ${entry.name} (${entry.path})`;
      case "mention":
        return `[Mention] ${entry.name} (${entry.path})`;
      default:
        return null;
    }
  }

  private describeFileChange(change: FileUpdateChange): string {
    switch (change.kind.type) {
      case "add":
        return `add ${change.path}`;
      case "delete":
        return `delete ${change.path}`;
      case "update":
        if (change.kind.move_path) {
          return `move ${change.path} -> ${change.kind.move_path}`;
        }
        return `update ${change.path}`;
      default:
        return `change ${change.path}`;
    }
  }

  private buildFileChangeMetadata(change: FileUpdateChange): Record<string, unknown> {
    return {
      path: change.path,
      actionType: change.kind.type,
      movePath: change.kind.type === "update" ? change.kind.move_path : null,
    };
  }

  private truncateText(text: string, maxLength = 1200): string {
    const normalized = text.trim();
    if (!normalized) return "";
    if (normalized.length <= maxLength) return normalized;
    return `${normalized.slice(0, maxLength - 16)}\n...[truncated]`;
  }

  private serializeForMessage(value: unknown, maxLength = 1200): string | undefined {
    if (value === null || value === undefined) return undefined;
    if (typeof value === "string") {
      return this.truncateText(value, maxLength);
    }
    try {
      return this.truncateText(JSON.stringify(value, null, 2), maxLength);
    } catch {
      return this.truncateText(String(value), maxLength);
    }
  }

  private formatLabeledBlock(label: string, value?: string): string | null {
    if (!value) return null;
    return value.includes("\n") ? `${label}:\n${value}` : `${label}: ${value}`;
  }

  private buildCompactionNotice(metadata?: Record<string, unknown>): AgentMessage {
    return {
      role: "system",
      content: CODEX_COMPACTION_NOTICE,
      timestamp: Date.now(),
      metadata: {
        itemType: "contextCompaction",
        messageType: "context_compaction_notice",
        adapter: this.agentId,
        ...metadata,
      },
    };
  }

  private buildCommandApprovalSummary(params: CommandExecutionRequestApprovalParams): string {
    if (params.networkApprovalContext) {
      return "Approve Codex network access request";
    }
    if (params.command) {
      return `Approve Codex command: ${params.command}`;
    }
    const action = this.describeCommandActions(params.commandActions ?? []);
    return action ? `Approve Codex command (${action})` : "Approve Codex command execution";
  }

  private buildFileApprovalSummary(params: FileChangeRequestApprovalParams): string {
    if (params.grantRoot) {
      return `Approve Codex file changes under ${params.grantRoot}`;
    }
    if (params.reason) {
      return `Approve Codex file changes: ${params.reason}`;
    }
    return "Approve Codex file changes";
  }

  private describeCommandActions(actions: CommandAction[]): string | undefined {
    if (actions.length === 0) return undefined;
    const labels = actions.map((action) => {
      switch (action.type) {
        case "read":
          return `read ${action.path}`;
        case "listFiles":
          return `list ${action.path ?? "."}`;
        case "search":
          return `search ${action.path ?? "."}`;
        default:
          return "shell";
      }
    });
    return labels.slice(0, 3).join(", ");
  }

  private async imagesToTempFiles(
    images: ImageAttachment[],
  ): Promise<{ entries: InputEntry[]; tempFiles: string[] }> {
    const entries: InputEntry[] = [];
    const tempFiles: string[] = [];

    for (const image of images) {
      const extMap: Record<string, string> = {
        jpeg: "jpg",
        "svg+xml": "svg",
        tiff: "tif",
      };
      const rawExt = image.mediaType.split("/")[1] || "png";
      const ext = extMap[rawExt] ?? rawExt;
      const tempPath = join(tmpdir(), `mercury-img-${randomUUID()}.${ext}`);
      await writeFile(tempPath, Buffer.from(image.data, "base64"));
      tempFiles.push(tempPath);
      entries.push({ type: "localImage", path: tempPath });
    }

    return { entries, tempFiles };
  }

  private async cleanupTempFiles(tempFiles: string[]): Promise<void> {
    for (const file of tempFiles) {
      try {
        await unlink(file);
      } catch {
        // Ignore cleanup failures.
      }
    }
  }

  private async *handleSlashCommand(
    sessionId: string,
    prompt: string,
  ): AsyncGenerator<AgentMessage> {
    const trimmed = prompt.trim();
    const match = trimmed.match(/^\/(\S+)(?:\s+(.*))?$/);
    if (!match) return;

    const cmd = match[1].toLowerCase();
    const ts = Date.now();

    const infoMsg = (content: string): AgentMessage => ({
      role: "assistant",
      content,
      timestamp: ts,
      metadata: { isSlashCommandResponse: true, command: `/${cmd}` },
    });

    switch (cmd) {
      case "help": {
        const commands = this.getSlashCommands();
        const grouped = new Map<string, typeof commands>();
        for (const command of commands) {
          const category = command.category ?? "other";
          if (!grouped.has(category)) grouped.set(category, []);
          grouped.get(category)!.push(command);
        }
        let text = "## Available Commands\n\n";
        for (const [category, commandsInCategory] of grouped) {
          text += `### ${category}\n`;
          for (const command of commandsInCategory) {
            text += `  **${command.name}**  ${command.description}\n`;
          }
          text += "\n";
        }
        yield infoMsg(text);
        return;
      }
      case "clear":
      case "new": {
        const session = this.sessions.get(sessionId)?.info;
        if (session) session.status = "completed";
        yield infoMsg("Session cleared. Send a new message to start a fresh conversation.");
        return;
      }
      case "status": {
        const session = this.sessions.get(sessionId)?.info;
        yield infoMsg(
          `## Session Status\n` +
            `- **Agent**: ${this.config.displayName} (${this.agentId})\n` +
            `- **Integration**: app-server JSON-RPC (stdio)\n` +
            `- **Session**: ${sessionId}\n` +
            `- **Thread**: ${session?.resumeToken ?? "unknown"}\n` +
            `- **Status**: ${session?.status ?? "unknown"}\n` +
            `- **Started**: ${session ? new Date(session.startedAt).toLocaleString() : "N/A"}`,
        );
        return;
      }
      case "exit":
      case "quit": {
        const session = this.sessions.get(sessionId)?.info;
        if (session) session.status = "completed";
        yield infoMsg("Session ended. Use the Start button to begin a new session.");
        return;
      }
      case "agent":
      case "apps":
      case "compact":
      case "copy":
      case "debug-config":
      case "diff":
      case "experimental":
      case "feedback":
      case "fork":
      case "init":
      case "logout":
      case "mcp":
      case "mention":
      case "model":
      case "permissions":
      case "approvals":
      case "personality":
      case "plan":
      case "ps":
      case "resume":
      case "review":
      case "sandbox-add-read-dir":
      case "skills":
      case "statusline": {
        yield infoMsg(
          `**/${cmd}** requires the Codex CLI terminal.\n\n` +
            `Run in your terminal:\n\`\`\`\ncodex /${cmd}\n\`\`\``,
        );
        return;
      }
      default:
        yield infoMsg(`Unknown command **/${cmd}**. Type **/help** to see available commands.`);
    }
  }
}
