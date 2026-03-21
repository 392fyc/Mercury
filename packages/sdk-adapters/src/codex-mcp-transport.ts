/**
 * MCP transport layer for Codex CLI.
 *
 * Spawns `codex mcp-server` as a child process and communicates via
 * the standard MCP protocol using @modelcontextprotocol/sdk.
 *
 * Verified against:
 *   - @modelcontextprotocol/sdk v1.27.1 — https://www.npmjs.com/package/@modelcontextprotocol/sdk
 *   - codex mcp-server — https://developers.openai.com/codex/guides/agents-sdk
 *   - MCP TS SDK source — https://github.com/modelcontextprotocol/typescript-sdk
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

/**
 * MCP client wrapper for `codex mcp-server`.
 *
 * Manages the lifecycle of a single `codex mcp-server` child process,
 * exposes the `codex()` and `codex-reply()` MCP tools, and routes
 * Codex-specific notifications and elicitation requests to callbacks.
 *
 * @see https://developers.openai.com/codex/guides/agents-sdk
 * @see https://deepwiki.com/openai/codex/6.4-mcp-server-implementation-(codex-mcp-server)
 */
export class CodexMCPTransport {
  private client: Client | null = null;
  private transport: StdioClientTransport | null = null;
  private startPromise: Promise<void> | null = null;
  private closed = false;

  private onEvent?: CodexEventHandler;
  private onElicitation?: ElicitationHandler;
  private onError?: (error: Error) => void;

  /**
   * @param options.onEvent       Called for each `notifications/codex/event` notification (streaming).
   * @param options.onElicitation  Called for `elicitation/*` server-to-client requests (approval bridge).
   * @param options.onError       Called on transport-level errors.
   */
  constructor(options?: {
    onEvent?: CodexEventHandler;
    onElicitation?: ElicitationHandler;
    onError?: (error: Error) => void;
  }) {
    this.onEvent = options?.onEvent;
    this.onElicitation = options?.onElicitation;
    this.onError = options?.onError;
  }

  /**
   * Ensure the MCP client is connected. Idempotent — only spawns once.
   *
   * Checks startPromise BEFORE client to avoid race where concurrent
   * callers see a non-null client before connect() completes. On failure,
   * clears startPromise so the next call can retry.
   */
  async ensureStarted(): Promise<void> {
    if (this.startPromise) return this.startPromise;
    if (this.client && !this.closed) return;
    this.startPromise = this.start().catch((error) => {
      this.client = null;
      this.transport = null;
      this.startPromise = null;
      throw error;
    });
    return this.startPromise;
  }

  private async start(): Promise<void> {
    this.closed = false;

    const stdioTransport = new StdioClientTransport({
      command: process.platform === "win32" ? "codex.cmd" : "codex",
      args: ["mcp-server"],
      stderr: "pipe",
    });

    const client = new Client(
      { name: "mercury-orchestrator", version: "0.1.0" },
      {
        capabilities: {
          elicitation: {},
        },
      },
    );

    stdioTransport.onerror = (error) => {
      this.onError?.(error);
    };

    stdioTransport.onclose = () => {
      this.client = null;
      this.transport = null;
      this.startPromise = null;
    };

    // Connect first, THEN register custom message handlers.
    // The SDK's Protocol.connect() overwrites transport.onmessage with a
    // chain-wrapper (captures existing callback, wraps it). We wrap AFTER
    // connect so our interceptors sit in front of the SDK's handler.
    //
    // RISK: this replaces the SDK's onmessage wrapper. Codex-specific
    // notifications (notifications/codex/event) and elicitation requests
    // are intercepted here; all other messages are forwarded to the SDK's
    // original handler (sdkOnMessage). If the SDK's internal chain changes
    // in a future version, this interception may break — verify after
    // upgrading @modelcontextprotocol/sdk.
    await client.connect(stdioTransport);

    // Store references only after successful connect
    this.client = client;
    this.transport = stdioTransport;

    // Wrap the SDK's onmessage to intercept codex-specific notifications
    // and elicitation server→client requests.
    const sdkOnMessage = stdioTransport.onmessage;
    const elicitationHandler = this.onElicitation;

    stdioTransport.onmessage = (message: JSONRPCMessage) => {
      const msg = message as Record<string, unknown>;

      // Intercept codex/event notifications for streaming
      if (msg.method === "notifications/codex/event" && msg.params && this.onEvent) {
        try {
          this.onEvent(msg.params as CodexEventNotification);
        } catch {
          // Event handler errors are non-fatal
        }
      }

      // Intercept elicitation server→client requests for approval bridge
      if (elicitationHandler) {
        const isRequest = typeof msg.id !== "undefined"
          && typeof msg.method === "string"
          && !("result" in msg)
          && !("error" in msg);

        if (isRequest && (msg.method as string).startsWith("elicitation/")) {
          void (async () => {
            try {
              const result = await elicitationHandler(
                msg.method as string,
                (msg.params ?? {}) as Record<string, unknown>,
              );
              await stdioTransport.send({
                jsonrpc: "2.0",
                id: msg.id,
                result,
              } as JSONRPCMessage).catch((sendErr) => {
                this.onError?.(sendErr instanceof Error ? sendErr : new Error(`Elicitation response send failed: ${sendErr}`));
              });
            } catch (err) {
              await stdioTransport.send({
                jsonrpc: "2.0",
                id: msg.id,
                error: { code: -32000, message: err instanceof Error ? err.message : String(err) },
              } as JSONRPCMessage).catch((sendErr) => {
                this.onError?.(sendErr instanceof Error ? sendErr : new Error(`Elicitation error response send failed: ${sendErr}`));
              });
            }
          })();
          return; // Don't pass elicitation requests to SDK
        }
      }

      // All other messages go to SDK's handler chain
      sdkOnMessage?.call(stdioTransport, message);
    };
  }

  /** Call the `codex` tool (start new session). */
  async callCodex(params: CodexToolParams): Promise<CodexToolResult> {
    await this.ensureStarted();
    const result = await this.client!.callTool(
      { name: "codex", arguments: params as unknown as Record<string, unknown> },
      undefined,
      { timeout: 600_000 },
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
