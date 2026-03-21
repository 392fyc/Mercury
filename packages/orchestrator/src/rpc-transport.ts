/**
 * JSON-RPC 2.0 transport over stdin/stdout.
 * Line-delimited: one JSON object per line.
 * All logging goes to stderr (stdout is reserved for the protocol).
 */

import { createInterface } from "node:readline";

export interface RpcRequest {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
  id?: number | string;
}

export interface RpcResponse {
  jsonrpc: "2.0";
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
  id: number | string;
}

export interface RpcNotification {
  jsonrpc: "2.0";
  method: string;
  params?: Record<string, unknown>;
}

export type RpcHandler = (
  method: string,
  params: Record<string, unknown>,
) => Promise<unknown>;

export class RpcTransport {
  private handler: RpcHandler | null = null;
  /** When true, stdin EOF logs a warning but does NOT call process.exit(). */
  private stdinOptional = false;

  /**
   * Mark stdin as optional so that its closure does not kill the process.
   * Call this BEFORE start() when running in headless / HTTP-only mode.
   */
  setStdinOptional(optional = true): void {
    this.stdinOptional = optional;
  }

  start(handler: RpcHandler): void {
    this.handler = handler;

    const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });

    rl.on("line", (line) => {
      const trimmed = line.trim();
      if (!trimmed) return;

      try {
        const msg = JSON.parse(trimmed) as RpcRequest;
        this.handleMessage(msg);
      } catch (err) {
        this.log(`Failed to parse: ${trimmed}`);
      }
    });

    rl.on("close", () => {
      if (this.stdinOptional) {
        this.log("stdin closed (non-fatal in headless mode, HTTP server still active)");
        return;
      }
      this.log("stdin closed, shutting down");
      process.exit(0);
    });

    this.log("RPC transport started");
  }

  private async handleMessage(msg: RpcRequest): Promise<void> {
    if (!this.handler) return;

    if (msg.id !== undefined) {
      // Request — expects response
      try {
        const result = await this.handler(msg.method, msg.params ?? {});
        this.sendResponse({ jsonrpc: "2.0", result, id: msg.id });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        this.sendResponse({
          jsonrpc: "2.0",
          error: { code: -32000, message },
          id: msg.id,
        });
      }
    } else {
      // Notification — no response expected
      try {
        await this.handler(msg.method, msg.params ?? {});
      } catch (err) {
        this.log(`Notification handler error: ${err}`);
      }
    }
  }

  sendNotification(method: string, params: Record<string, unknown>): void {
    const msg: RpcNotification = { jsonrpc: "2.0", method, params };
    process.stdout.write(JSON.stringify(msg) + "\n");
  }

  private sendResponse(response: RpcResponse): void {
    process.stdout.write(JSON.stringify(response) + "\n");
  }

  log(message: string): void {
    process.stderr.write(`[orchestrator] ${message}\n`);
  }
}
