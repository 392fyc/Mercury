/**
 * Codex MCP adapter.
 *
 * Uses `codex mcp-server` over standard MCP protocol (stdio) instead of
 * the private app-server JSON-RPC protocol.
 *
 * Two MCP tools:
 *   - `codex(prompt, ...)` — start a new conversation
 *   - `codex-reply(threadId, prompt)` — continue an existing conversation
 *
 * Streaming via `notifications/codex/event` custom notifications.
 * Approval via elicitation server→client requests.
 *
 * Verified against:
 *   - @modelcontextprotocol/sdk v1.27.1 (npm)
 *   - codex mcp-server docs: https://developers.openai.com/codex/guides/agents-sdk
 *   - DeepWiki analysis: https://deepwiki.com/openai/codex/6.4-mcp-server-implementation-(codex-mcp-server)
 */

import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { join } from "node:path";
import type {
  AdapterYield,
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  AgentStreamingEvent,
  ImageAttachment,
  SessionInfo,
  SlashCommand,
} from "@mercury/core";
import {
  CodexMCPTransport,
  type CodexEventNotification,
  type CodexToolParams,
  type CodexToolResult,
} from "./codex-mcp-transport.js";

// ─── Constants ───

const CODEX_COMPACTION_NOTICE =
  "Context compaction triggered — role boundary instructions from earlier turns may have been summarized.";
const DEFAULT_APPROVAL_POLICY = "on-request";
const DEFAULT_SANDBOX_MODE = "workspace-write";
const END_OF_STREAM = Symbol("codex-mcp-turn-end");

// ─── Local types ───

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

interface MCPSession {
  info: SessionInfo;
  cwd: string;
  threadId: string | null; // null until first codex() call returns
  activeTurn: MessageStream | null;
}

// ─── Async message queue (same pattern as old adapter) ───

class MessageStream {
  private queue: Array<AdapterYield | Error | typeof END_OF_STREAM> = [];
  private waiters: Array<(value: AdapterYield | Error | typeof END_OF_STREAM) => void> = [];
  private ended = false;
  private compactionNotified = false;
  readonly hooks?: LocalAgentSendHooks;

  constructor(hooks?: LocalAgentSendHooks) {
    this.hooks = hooks;
  }

  push(message: AdapterYield): void {
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

  async *iterate(): AsyncGenerator<AdapterYield> {
    while (true) {
      const next = await this.next();
      if (next === END_OF_STREAM) return;
      if (next instanceof Error) throw next;
      yield next;
    }
  }

  private next(): Promise<AdapterYield | Error | typeof END_OF_STREAM> {
    const item = this.queue.shift();
    if (item !== undefined) return Promise.resolve(item);
    return new Promise((resolve) => { this.waiters.push(resolve); });
  }

  private deliver(item: AdapterYield | Error | typeof END_OF_STREAM): void {
    const waiter = this.waiters.shift();
    if (waiter) { waiter(item); return; }
    this.queue.push(item);
  }
}

// ─── Adapter ───

export class CodexMCPAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;

  private sessions = new Map<string, MCPSession>();
  private threadToSession = new Map<string, string>(); // threadId → sessionId
  /** Sessions awaiting their first threadId (first-turn notification routing). */
  private pendingSessionIds = new Set<string>();
  private sharedSystemPrompt?: string;
  private transport: CodexMCPTransport | null = null;

  constructor(config?: Partial<AgentConfig>) {
    this.config = {
      id: "codex-cli",
      displayName: "Codex CLI",
      cli: "codex",
      roles: ["dev"],
      integration: "mcp",
      capabilities: ["code", "batch_json", "test"],
      restrictions: ["no_kb_write", "isolated_branch_only"],
      maxConcurrentSessions: 3,
      ...config,
    };
    this.agentId = this.config.id;
  }

  // ─── System prompt ───

  setSystemPrompt(prompt: string): void {
    this.sharedSystemPrompt = prompt;
  }

  // ─── Transport lifecycle ───

  private ensureTransport(): CodexMCPTransport {
    if (this.transport) return this.transport;
    this.transport = new CodexMCPTransport({
      onEvent: (notification) => this.handleEvent(notification),
      onElicitation: (method, params) => this.handleElicitation(method, params),
      onError: (error) => {
        console.error(`[CodexMCPAdapter] transport error: ${error.message}`);
      },
    });
    return this.transport;
  }

  // ─── Session lifecycle ───

  async startSession(cwd: string): Promise<SessionInfo> {
    const transport = this.ensureTransport();
    await transport.ensureStarted();

    const sessionId = randomUUID();
    const info: SessionInfo = {
      sessionId,
      agentId: this.agentId,
      cwd,
      startedAt: Date.now(),
      lastActiveAt: Date.now(),
      status: "active",
    };

    this.sessions.set(sessionId, {
      info,
      cwd,
      threadId: null,
      activeTurn: null,
    });

    return info;
  }

  async resumeSession(
    sessionId: string,
    persistedInfo?: SessionInfo,
    cwd?: string,
  ): Promise<SessionInfo> {
    const existing = this.sessions.get(sessionId);
    if (existing) {
      existing.info.lastActiveAt = Date.now();
      existing.info.status = "active";
      return existing.info;
    }

    const transport = this.ensureTransport();
    await transport.ensureStarted();

    const threadId = persistedInfo?.resumeToken ?? null;
    const resolvedCwd = cwd ?? persistedInfo?.cwd ?? process.cwd();
    const info: SessionInfo = {
      sessionId,
      agentId: this.agentId,
      cwd: resolvedCwd,
      startedAt: persistedInfo?.startedAt ?? Date.now(),
      lastActiveAt: Date.now(),
      status: "active",
      resumeToken: threadId ?? undefined,
      sessionName: persistedInfo?.sessionName,
      frozenSystemPrompt: persistedInfo?.frozenSystemPrompt,
    };

    const record: MCPSession = { info, cwd: resolvedCwd, threadId, activeTurn: null };
    this.sessions.set(sessionId, record);
    if (threadId) this.threadToSession.set(threadId, sessionId);
    return info;
  }

  async endSession(sessionId: string): Promise<void> {
    const record = this.sessions.get(sessionId);
    if (record) {
      record.info.status = "completed";
      record.activeTurn?.finish();
      if (record.threadId) this.threadToSession.delete(record.threadId);
      this.sessions.delete(sessionId);
    }
  }

  // ─── Send prompt (main interaction) ───

  async *sendPrompt(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
    hooks?: LocalAgentSendHooks,
  ): AsyncGenerator<AdapterYield> {
    // Handle slash commands
    const trimmed = prompt.trim();
    if (trimmed.startsWith("/")) {
      let handled = false;
      for await (const message of this.handleSlashCommand(sessionId, prompt)) {
        handled = true;
        yield message;
      }
      if (handled) return;
    }

    const record = this.sessions.get(sessionId);
    if (!record) throw new Error(`Unknown session: ${sessionId}`);
    if (record.activeTurn) throw new Error(`Session ${sessionId} already has an active turn`);

    record.info.lastActiveAt = Date.now();

    // Build effective prompt with role context
    const promptContext = record.info.frozenSystemPrompt ?? this.sharedSystemPrompt;
    const effectivePrompt = promptContext
      ? `[Mercury Role Context]\n${promptContext}\n\n[User Prompt]\n${prompt}`
      : prompt;

    const stream = new MessageStream(hooks);
    record.activeTurn = stream;

    const transport = this.ensureTransport();

    // Fix #3: For first-turn calls (no threadId yet), register a temporary
    // session mapping keyed by sessionId so that notifications arriving before
    // structuredContent.threadId can still be routed via pendingSessionIds.
    if (!record.threadId) {
      this.pendingSessionIds.add(sessionId);
    }

    // Fix #1: Decouple tool call from stream iteration. Launch the MCP tool
    // call in the background so yield* can start consuming events immediately
    // as they arrive via notifications, rather than buffering until callTool returns.
    const toolCallPromise = (async () => {
      try {
        let result: CodexToolResult;

        if (record.threadId) {
          result = await transport.callCodexReply({
            threadId: record.threadId,
            prompt: effectivePrompt,
          });
        } else {
          const toolParams: CodexToolParams = {
            prompt: effectivePrompt,
            cwd: record.cwd,
            sandbox: DEFAULT_SANDBOX_MODE,
            "approval-policy": hooks?.onApprovalRequest ? DEFAULT_APPROVAL_POLICY : "never",
          };
          if (this.config.model) toolParams.model = this.config.model;

          result = await transport.callCodex(toolParams);

          // Extract threadId and establish permanent mapping
          if (result.structuredContent?.threadId) {
            record.threadId = result.structuredContent.threadId;
            record.info.resumeToken = record.threadId;
            this.threadToSession.set(record.threadId, sessionId);
          }
          this.pendingSessionIds.delete(sessionId);
        }

        // Push final response message
        const text = this.extractTextFromResult(result);
        if (text) {
          stream.push({
            role: "assistant",
            content: text,
            timestamp: Date.now(),
          });
        }

        if (result.isError) {
          stream.fail(new Error(text || "Codex tool call returned an error"));
        } else {
          stream.finish();
        }
      } catch (err) {
        this.pendingSessionIds.delete(sessionId);
        stream.fail(err instanceof Error ? err : new Error(String(err)));
      } finally {
        record.activeTurn = null;
      }
    })();

    // Yield events as they arrive (streaming from notifications + final from tool result)
    yield* stream.iterate();

    // Ensure the background promise is settled (should already be, since stream.finish/fail was called)
    await toolCallPromise;
  }

  // ─── One-shot execution ───

  async executeOneShot(
    prompt: string,
    cwd: string,
    options?: { model?: string; sandbox?: string; approvalPolicy?: unknown },
  ): Promise<{ messages: AgentMessage[]; finalMessage: string; threadId: string }> {
    const transport = this.ensureTransport();
    await transport.ensureStarted();

    const promptContext = this.sharedSystemPrompt;
    const effectivePrompt = promptContext
      ? `[Mercury Role Context]\n${promptContext}\n\n[User Prompt]\n${prompt}`
      : prompt;

    // Fix #4: Respect caller-provided model and sandbox overrides
    const toolParams: CodexToolParams = {
      prompt: effectivePrompt,
      cwd,
      sandbox: options?.sandbox ?? DEFAULT_SANDBOX_MODE,
      "approval-policy": (options?.approvalPolicy as string) ?? "never",
    };
    const model = options?.model ?? this.config.model;
    if (model) toolParams.model = model;

    const result = await transport.callCodex(toolParams);
    const text = this.extractTextFromResult(result);

    const threadId = result.structuredContent?.threadId ?? "";
    return {
      messages: text
        ? [{ role: "assistant", content: text, timestamp: Date.now() }]
        : [],
      finalMessage: text || "",
      threadId,
    };
  }

  // ─── Handoff (context overflow continuation) ───

  async handoffSession(oldSessionId: string, summary: string): Promise<SessionInfo> {
    const oldRecord = this.sessions.get(oldSessionId);
    const cwd = oldRecord?.cwd ?? process.cwd();

    await this.endSession(oldSessionId);

    const newInfo = await this.startSession(cwd);
    const newRecord = this.sessions.get(newInfo.sessionId)!;

    const existingPrompt = oldRecord?.info.frozenSystemPrompt ?? this.sharedSystemPrompt ?? "";
    newRecord.info.frozenSystemPrompt = `${existingPrompt}\n\n[Handoff Context]\n${summary}`;
    newRecord.info.parentSessionId = oldSessionId;

    return newRecord.info;
  }

  // ─── Model management ───

  async listModels(): Promise<{ id: string; name: string }[]> {
    try {
      const cachePath = join(homedir(), ".codex", "models_cache.json");
      const raw = await readFile(cachePath, "utf-8");
      const data = JSON.parse(raw);
      if (Array.isArray(data)) {
        return data.map((m: { id?: string; name?: string }) => ({
          id: m.id ?? "unknown",
          name: m.name ?? m.id ?? "unknown",
        }));
      }
    } catch {
      // Cache not available
    }
    return [
      { id: "o3", name: "o3" },
      { id: "o4-mini", name: "o4-mini" },
      { id: "codex-mini-latest", name: "Codex Mini" },
    ];
  }

  setModel(model: string): void {
    this.config.model = model;
  }

  // ─── Slash commands ───

  getSlashCommands(): SlashCommand[] {
    return [
      { name: "/new", description: "Start a new conversation thread", category: "session" },
      { name: "/model", description: "Change the AI model", category: "config", args: [{ name: "model", description: "Model name", required: false, type: "string" }] },
      { name: "/help", description: "Show available commands", category: "help" },
      { name: "/mcp", description: "List configured MCP tools", category: "tools" },
      { name: "/clear", description: "Clear conversation context", category: "session" },
    ];
  }

  // ─── Event handling (notifications/codex/event → AgentStreamingEvent) ───

  private handleEvent(notification: CodexEventNotification): void {
    const threadId = notification._meta?.threadId;

    // Try threadId → sessionId lookup first
    let sessionId = threadId ? this.threadToSession.get(threadId) : undefined;

    // Fix #3: During the first turn, threadId may not be mapped yet.
    // Fall back to the pending session (there should be at most one active).
    if (!sessionId && this.pendingSessionIds.size > 0) {
      const pendingId = this.pendingSessionIds.values().next().value as string;
      const pending = this.sessions.get(pendingId);
      if (pending?.activeTurn) {
        sessionId = pendingId;
        // Establish the mapping now if threadId is available
        if (threadId && !pending.threadId) {
          pending.threadId = threadId;
          pending.info.resumeToken = threadId;
          this.threadToSession.set(threadId, pendingId);
          this.pendingSessionIds.delete(pendingId);
        }
      }
    }

    if (!sessionId) return;

    const record = this.sessions.get(sessionId);
    if (!record?.activeTurn) return;

    const event = notification.event;
    const eventType = event.type as string | undefined;
    if (!eventType) return;

    switch (eventType) {
      case "agent_message_delta":
      case "agentMessage/delta": {
        const delta = (event.delta ?? event.text ?? "") as string;
        if (delta) {
          record.activeTurn.push({
            type: "streaming",
            eventKind: "text_delta",
            content: delta,
            timestamp: Date.now(),
          } as AgentStreamingEvent);
        }
        break;
      }

      case "command_execution_started":
      case "item/started": {
        const itemType = (event.item as Record<string, unknown>)?.type as string | undefined;
        if (itemType === "commandExecution" || itemType === "fileChange") {
          record.activeTurn.push({
            type: "streaming",
            eventKind: "tool_start",
            toolName: itemType,
            timestamp: Date.now(),
          } as AgentStreamingEvent);
        }
        break;
      }

      case "command_execution_completed":
      case "item/completed": {
        const item = event.item as Record<string, unknown> | undefined;
        if (item) {
          const msg = this.mapItemToMessage(item);
          if (msg) record.activeTurn.push(msg);
        }
        break;
      }

      case "turn/completed":
        record.activeTurn.finish();
        break;

      case "context_compaction":
      case "thread/compacted":
        record.activeTurn.pushCompactionNotice(() => ({
          role: "system",
          content: CODEX_COMPACTION_NOTICE,
          timestamp: Date.now(),
        }));
        break;
    }
  }

  // ─── Elicitation → Approval bridge ───

  private async handleElicitation(
    method: string,
    params: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const threadId = params.threadId as string | undefined;
    const sessionId = threadId ? this.threadToSession.get(threadId) : undefined;
    const record = sessionId ? this.sessions.get(sessionId) : undefined;
    const hooks = record?.activeTurn?.hooks;

    if (!hooks?.onApprovalRequest) {
      // Fix #2: Fail-closed — decline when no approval handler is registered.
      // This matches the "on-request" semantics: no handler = no one to approve.
      return { decision: "decline" };
    }

    if (method === "elicitation/exec_approval") {
      const request: LocalAgentApprovalRequest = {
        kind: "command_execution",
        summary: (params.command as string) ?? (params.reason as string) ?? "Command execution approval requested",
        rawRequest: params,
      };
      const decision = await hooks.onApprovalRequest(request);
      return { decision: decision.action === "approve" ? "accept" : "decline" };
    }

    if (method === "elicitation/patch_approval") {
      const request: LocalAgentApprovalRequest = {
        kind: "file_change",
        summary: (params.reason as string) ?? "File change approval requested",
        rawRequest: params,
      };
      const decision = await hooks.onApprovalRequest(request);
      return { decision: decision.action === "approve" ? "accept" : "decline" };
    }

    // Unknown elicitation method — decline by default (fail-closed)
    return { decision: "decline" };
  }

  // ─── Helpers ───

  private extractTextFromResult(result: CodexToolResult): string {
    return result.content
      .filter((c) => c.type === "text" && c.text)
      .map((c) => c.text!)
      .join("\n");
  }

  private mapItemToMessage(item: Record<string, unknown>): AgentMessage | null {
    const type = item.type as string;
    const ts = Date.now();

    switch (type) {
      case "agentMessage":
        return {
          role: "assistant",
          content: (item.text ?? item.content ?? "") as string,
          timestamp: ts,
        };

      case "commandExecution": {
        const cmd = (item.command ?? "") as string;
        const exitCode = item.exitCode as number | undefined;
        const output = (item.output ?? "") as string;
        return {
          role: "assistant",
          content: `\`\`\`\n$ ${cmd}\n${output}\n\`\`\`${exitCode !== undefined && exitCode !== 0 ? `\n(exit code: ${exitCode})` : ""}`,
          timestamp: ts,
          metadata: { itemType: "commandExecution", command: cmd, exitCode },
        };
      }

      case "fileChange":
        return {
          role: "assistant",
          content: `File changed: ${(item.filePath ?? item.path ?? "") as string}`,
          timestamp: ts,
          metadata: { itemType: "fileChange", filePath: item.filePath ?? item.path },
        };

      case "mcpToolCall":
        return {
          role: "assistant",
          content: `MCP tool: ${item.server}/${item.tool}`,
          timestamp: ts,
          metadata: { itemType: "mcpToolCall", tool: item.tool, server: item.server },
        };

      case "webSearch":
        return {
          role: "assistant",
          content: `Web search: ${item.query}`,
          timestamp: ts,
          metadata: { itemType: "webSearch", query: item.query },
        };

      default:
        return null;
    }
  }

  private async *handleSlashCommand(
    sessionId: string,
    prompt: string,
  ): AsyncGenerator<AdapterYield> {
    const trimmed = prompt.trim();
    const match = trimmed.match(/^(\S+)(?:\s+(.*))?$/);
    if (!match) return;

    const cmd = match[1].toLowerCase();
    const ts = Date.now();
    const infoMsg = (content: string): AgentMessage => ({ role: "assistant", content, timestamp: ts });

    switch (cmd) {
      case "/new": {
        const record = this.sessions.get(sessionId);
        if (record) {
          // Fix #5: Clean up old threadToSession mapping and resumeToken
          if (record.threadId) this.threadToSession.delete(record.threadId);
          record.threadId = null;
          record.info.resumeToken = undefined;
        }
        yield infoMsg("Started new conversation thread. Next message will create a fresh session.");
        return;
      }

      case "/model": {
        const arg = match[2]?.trim();
        if (arg) {
          this.setModel(arg);
          yield infoMsg(`Model set to: ${arg}`);
        } else {
          const models = await this.listModels();
          yield infoMsg(`Available models:\n${models.map((m) => `  - ${m.id} (${m.name})`).join("\n")}\n\nCurrent: ${this.config.model ?? "default"}`);
        }
        return;
      }

      case "/help":
        yield infoMsg(this.getSlashCommands().map((c) => `${c.name} — ${c.description}`).join("\n"));
        return;

      case "/mcp": {
        try {
          const tools = await this.ensureTransport().listTools();
          yield infoMsg(`MCP tools:\n${tools.map((t) => `  - ${t.name}: ${t.description ?? "(no description)"}`).join("\n")}`);
        } catch (err) {
          yield infoMsg(`Failed to list MCP tools: ${err instanceof Error ? err.message : String(err)}`);
        }
        return;
      }

      case "/clear": {
        const record = this.sessions.get(sessionId);
        if (record) {
          // Fix #5: Clean up old threadToSession mapping and resumeToken
          if (record.threadId) this.threadToSession.delete(record.threadId);
          record.threadId = null;
          record.info.resumeToken = undefined;
          record.info.frozenSystemPrompt = undefined;
        }
        yield infoMsg("Cleared conversation context.");
        return;
      }

      default:
        return; // Not a known command — fall through to sendPrompt
    }
  }
}
