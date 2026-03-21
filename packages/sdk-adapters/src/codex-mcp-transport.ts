/**
 * MCP transport layer for Codex CLI.
 *
 * Spawns `codex mcp-server` as a child process and communicates via
 * the standard MCP protocol using @modelcontextprotocol/sdk.
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import type { JSONRPCMessage } from "@modelcontextprotocol/sdk/types.js";

/** Parameters forwarded to the `codex()` MCP tool. */
export interface CodexToolParams {
  prompt: string;
  model?: string;
  sandbox?: string;
  "approval-policy"?: string;
  cwd?: string;
  "base-instructions"?: string;
  "developer-instructions"?: string;
  config?: Record<string, unknown>;
  profile?: string;
}

/** Parameters for the `codex-reply()` MCP tool. */
export interface CodexReplyToolParams {
  threadId: string;
  prompt: string;
}

/** Structured content returned by codex/codex-reply tools. */
export interface CodexToolResult {
  content: Array<{ type: string; text?: string; [k: string]: unknown }>;
  structuredContent?: { threadId: string; [k: string]: unknown };
  isError?: boolean;
}

/** A codex/event notification forwarded from the MCP server. */
export interface CodexEventNotification {
  event: Record<string, unknown>;
  _meta?: { requestId?: string; threadId?: string };
}

/** Callback types for event handling. */
export type CodexEventHandler = (notification: CodexEventNotification) => void;
export type ElicitationHandler = (
  method: string,
  params: Record<string, unknown>,
) => Promise<Record<string, unknown>>;

export class CodexMCPTransport {
  private client: Client | null = null;
  private transport: StdioClientTransport | null = null;
  private startPromise: Promise<void> | null = null;
  private closed = false;

  private onEvent?: CodexEventHandler;
  private onElicitation?: ElicitationHandler;
  private onError?: (error: Error) => void;

  constructor(options?: {
    onEvent?: CodexEventHandler;
    onElicitation?: ElicitationHandler;
    onError?: (error: Error) => void;
  }) {
    this.onEvent = options?.onEvent;
    this.onElicitation = options?.onElicitation;
    this.onError = options?.onError;
  }

  /** Ensure the MCP client is connected. Idempotent — only spawns once. */
  async ensureStarted(): Promise<void> {
    if (this.client && !this.closed) return;
    if (this.startPromise) return this.startPromise;
    this.startPromise = this.start();
    return this.startPromise;
  }

  private async start(): Promise<void> {
    this.closed = false;

    this.transport = new StdioClientTransport({
      command: process.platform === "win32" ? "codex.cmd" : "codex",
      args: ["mcp-server"],
      stderr: "pipe",
    });

    this.client = new Client(
      { name: "mercury-orchestrator", version: "0.1.0" },
      {
        capabilities: {
          // Declare elicitation support so the server can send approval requests
          elicitation: {},
        },
      },
    );

    // Register custom notification handler for codex/event streaming
    // The MCP SDK setNotificationHandler requires a Zod schema; for custom
    // notifications we hook into the low-level transport message handler.
    const origOnMessage = this.transport.onmessage;
    this.transport.onmessage = (message: JSONRPCMessage) => {
      this.interceptNotification(message);
      origOnMessage?.call(this.transport, message);
    };

    this.transport.onerror = (error) => {
      this.onError?.(error);
    };

    this.transport.onclose = () => {
      this.client = null;
      this.transport = null;
      this.startPromise = null;
    };

    await this.client.connect(this.transport);

    // Register elicitation request handler if callback provided
    // Codex MCP server sends elicitation/exec_approval and elicitation/patch_approval
    // as server→client requests. We handle them generically.
    if (this.onElicitation) {
      this.setupElicitationHandlers();
    }
  }

  /**
   * Intercept raw JSON-RPC messages to capture `notifications/codex/event`
   * which are custom Codex-specific notifications not in the MCP spec.
   */
  private interceptNotification(message: JSONRPCMessage): void {
    if (!this.onEvent) return;
    const msg = message as Record<string, unknown>;
    if (msg.method === "notifications/codex/event" && msg.params) {
      try {
        this.onEvent(msg.params as CodexEventNotification);
      } catch {
        // Event handler errors are non-fatal
      }
    }
  }

  /**
   * Register handlers for elicitation requests from the Codex MCP server.
   * These are server→client requests for command/file approval.
   */
  private setupElicitationHandlers(): void {
    if (!this.client || !this.onElicitation) return;

    // The Codex MCP server may send elicitation requests with various method
    // names. We use the low-level transport message interception since these
    // aren't standard MCP elicitation/create requests — they're Codex-specific.
    //
    // We override the transport.onmessage to intercept JSON-RPC requests
    // (messages with 'id' and 'method' fields that expect a response).
    const prevOnMessage = this.transport!.onmessage;
    const handler = this.onElicitation;

    this.transport!.onmessage = (message: JSONRPCMessage) => {
      const msg = message as Record<string, unknown>;
      const isRequest = typeof msg.id !== "undefined" && typeof msg.method === "string" && !("result" in msg) && !("error" in msg);

      if (isRequest && typeof msg.method === "string" && msg.method.startsWith("elicitation/")) {
        // Handle asynchronously, respond via transport.send()
        void (async () => {
          try {
            const result = await handler(msg.method as string, (msg.params ?? {}) as Record<string, unknown>);
            await this.transport!.send({
              jsonrpc: "2.0",
              id: msg.id,
              result,
            } as JSONRPCMessage);
          } catch (err) {
            await this.transport!.send({
              jsonrpc: "2.0",
              id: msg.id,
              error: { code: -32000, message: err instanceof Error ? err.message : String(err) },
            } as JSONRPCMessage);
          }
        })();
        return; // Don't pass to the SDK's own handler
      }

      // Pass non-elicitation messages to the SDK
      prevOnMessage?.call(this.transport, message);
    };
  }

  /** Call the `codex` tool (start new session). */
  async callCodex(params: CodexToolParams): Promise<CodexToolResult> {
    await this.ensureStarted();
    const result = await this.client!.callTool(
      { name: "codex", arguments: params as unknown as Record<string, unknown> },
      undefined,
      { timeout: 600_000 }, // 10 min for long tasks
    );
    return result as unknown as CodexToolResult;
  }

  /** Call the `codex-reply` tool (continue existing session). */
  async callCodexReply(params: CodexReplyToolParams): Promise<CodexToolResult> {
    await this.ensureStarted();
    const result = await this.client!.callTool(
      { name: "codex-reply", arguments: params as unknown as Record<string, unknown> },
      undefined,
      { timeout: 600_000 },
    );
    return result as unknown as CodexToolResult;
  }

  /** List available MCP tools (for diagnostics). */
  async listTools(): Promise<Array<{ name: string; description?: string }>> {
    await this.ensureStarted();
    const { tools } = await this.client!.listTools();
    return tools.map((t) => ({ name: t.name, description: t.description }));
  }

  /** Gracefully close the MCP connection and kill the codex process. */
  async close(): Promise<void> {
    this.closed = true;
    if (this.client) {
      try {
        await this.client.close();
      } catch {
        // Best-effort cleanup
      }
    }
    this.client = null;
    this.transport = null;
    this.startPromise = null;
  }

  /** Whether the transport is currently connected. */
  get isConnected(): boolean {
    return this.client !== null && !this.closed;
  }
}
