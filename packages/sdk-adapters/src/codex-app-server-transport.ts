import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { createInterface } from "node:readline";
import type {
  CodexAppServerNotificationMap,
  CodexAppServerNotificationMethod,
  CodexAppServerRequestMap,
  CodexAppServerRequestMethod,
  CodexAppServerServerRequestMap,
  CodexAppServerServerRequestMethod,
  InitializeCapabilities,
  JsonRpcFailure,
  JsonRpcMessage,
  JsonRpcNotification,
  JsonRpcRequest,
  JsonRpcSuccess,
  RequestId,
} from "./codex-app-server-types.js";

interface PendingRequest {
  method: string;
  resolve: (value: unknown) => void;
  reject: (error: Error) => void;
}

interface CodexAppServerTransportOptions {
  clientInfo: {
    name: string;
    title: string | null;
    version: string;
  };
  capabilities: InitializeCapabilities;
  onNotification?: <M extends CodexAppServerNotificationMethod>(
    method: M,
    params: CodexAppServerNotificationMap[M],
  ) => void | Promise<void>;
  onServerRequest?: <M extends CodexAppServerServerRequestMethod>(
    method: M,
    params: CodexAppServerServerRequestMap[M]["params"],
  ) => Promise<CodexAppServerServerRequestMap[M]["result"]>;
  onTransportError?: (error: Error) => void;
}

export class CodexAppServerTransport {
  private readonly options: CodexAppServerTransportOptions;
  private child: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<RequestId, PendingRequest>();
  private startPromise: Promise<void> | null = null;
  private nextRequestId = 1;
  private stderrLines: string[] = [];
  private closed = false;

  constructor(options: CodexAppServerTransportOptions) {
    this.options = options;
  }

  async ensureStarted(): Promise<void> {
    if (!this.startPromise) {
      this.startPromise = this.start();
    }
    return this.startPromise;
  }

  async request<M extends CodexAppServerRequestMethod>(
    method: M,
    params: CodexAppServerRequestMap[M]["params"],
  ): Promise<CodexAppServerRequestMap[M]["result"]> {
    await this.ensureStarted();
    return this.requestRaw(method, params);
  }

  async notify(method: string, params?: unknown): Promise<void> {
    await this.ensureStarted();
    this.notifyRaw(method, params);
  }

  private requestRaw<M extends CodexAppServerRequestMethod>(
    method: M,
    params: CodexAppServerRequestMap[M]["params"],
  ): Promise<CodexAppServerRequestMap[M]["result"]> {
    const id = this.nextRequestId++;

    return new Promise<CodexAppServerRequestMap[M]["result"]>((resolve, reject) => {
      this.pending.set(id, {
        method,
        resolve: resolve as (value: unknown) => void,
        reject,
      });

      try {
        this.write({
          jsonrpc: "2.0",
          method,
          id,
          params,
        });
      } catch (error) {
        this.pending.delete(id);
        reject(this.normalizeError(error, `Failed to send ${method}`));
      }
    });
  }

  private notifyRaw(method: string, params?: unknown): void {
    this.write({
      jsonrpc: "2.0",
      method,
      ...(params === undefined ? {} : { params }),
    });
  }

  async close(): Promise<void> {
    this.closed = true;
    if (!this.child) {
      this.startPromise = null;
      return;
    }
    this.child.stdin.end();
    this.child.kill();
    this.child = null;
    this.startPromise = null;
  }

  private async start(): Promise<void> {
    this.closed = false;
    this.stderrLines = [];

    await new Promise<void>((resolve, reject) => {
      const child = spawn("codex", ["app-server"], {
        stdio: "pipe",
        detached: false,
        shell: process.platform === "win32",
      });
      this.child = child;

      let settled = false;

      child.once("error", (error) => {
        if (settled) return;
        settled = true;
        this.child = null;
        this.startPromise = null;
        reject(this.normalizeError(error, "Failed to spawn codex app-server"));
      });

      child.once("spawn", () => {
        if (settled) return;
        settled = true;
        resolve();
      });

      child.once("exit", (code, signal) => {
        if (!settled) {
          settled = true;
          reject(this.buildProcessError(code, signal));
        }
      });
    });

    const child = this.child;
    if (!child) {
      throw new Error("Codex app-server failed to start");
    }

    const stdout = createInterface({ input: child.stdout });
    stdout.on("line", (line) => {
      if (!line.trim()) return;
      void this.handleLine(line);
    });

    const stderr = createInterface({ input: child.stderr });
    stderr.on("line", (line) => {
      if (!line.trim()) return;
      this.stderrLines.push(line);
      if (this.stderrLines.length > 40) {
        this.stderrLines.shift();
      }
    });

    child.on("exit", (code, signal) => {
      const error = this.buildProcessError(code, signal);
      const pending = [...this.pending.values()];
      this.pending.clear();
      this.child = null;
      this.startPromise = null;

      for (const request of pending) {
        request.reject(error);
      }

      if (!this.closed) {
        this.options.onTransportError?.(error);
      }
    });

    await this.requestRaw("initialize", {
      clientInfo: this.options.clientInfo,
      capabilities: this.options.capabilities,
    });
    this.notifyRaw("initialized", {});
  }

  private write(message: JsonRpcRequest | JsonRpcNotification): void {
    if (!this.child?.stdin.writable) {
      throw new Error("Codex app-server stdin is not writable");
    }
    this.child.stdin.write(`${JSON.stringify(message)}\n`);
  }

  private async handleLine(line: string): Promise<void> {
    let message: JsonRpcMessage;
    try {
      message = JSON.parse(line) as JsonRpcMessage;
    } catch (error) {
      this.options.onTransportError?.(
        this.normalizeError(error, "Failed to parse codex app-server message"),
      );
      return;
    }

    if (this.isFailure(message)) {
      const pendingId = message.id;
      if (pendingId === null) return;
      const pending = this.pending.get(pendingId);
      if (!pending) return;
      this.pending.delete(pendingId);
      pending.reject(new Error(`${pending.method} failed: ${message.error.message}`));
      return;
    }

    if (this.isSuccess(message)) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      pending.resolve(message.result);
      return;
    }

    if ("id" in message && typeof message.method === "string") {
      await this.handleServerRequest(
        message as JsonRpcRequest<CodexAppServerServerRequestMethod, unknown>,
      );
      return;
    }

    if (typeof message.method === "string") {
      const notification = message as JsonRpcNotification<CodexAppServerNotificationMethod, unknown>;
      try {
        await this.options.onNotification?.(
          notification.method,
          notification.params as never,
        );
      } catch (error) {
        this.options.onTransportError?.(
          this.normalizeError(error, `Failed to handle ${notification.method} notification`),
        );
      }
    }
  }

  private async handleServerRequest(
    request: JsonRpcRequest<CodexAppServerServerRequestMethod, unknown>,
  ): Promise<void> {
    const { onServerRequest } = this.options;
    if (!onServerRequest) {
      this.sendError(request.id, -32000, `Unhandled server request: ${request.method}`);
      return;
    }

    try {
      const result = await onServerRequest(request.method, request.params as never);
      this.sendResult(request.id, result);
    } catch (error) {
      const normalized = this.normalizeError(
        error,
        `Failed to handle server request ${request.method}`,
      );
      this.sendError(request.id, -32000, normalized.message);
      this.options.onTransportError?.(normalized);
    }
  }

  private sendError(id: RequestId, code: number, message: string): void {
    if (!this.child?.stdin.writable) return;
    this.child.stdin.write(
      `${JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } })}\n`,
    );
  }

  private sendResult(id: RequestId, result: unknown): void {
    if (!this.child?.stdin.writable) return;
    this.child.stdin.write(
      `${JSON.stringify({ jsonrpc: "2.0", id, result })}\n`,
    );
  }

  private isSuccess(message: JsonRpcMessage): message is JsonRpcSuccess {
    return "id" in message && "result" in message;
  }

  private isFailure(message: JsonRpcMessage): message is JsonRpcFailure {
    return "error" in message;
  }

  private buildProcessError(code: number | null, signal: NodeJS.Signals | null): Error {
    const detail =
      signal !== null
        ? `signal ${signal}`
        : `exit code ${code ?? "unknown"}`;
    const stderr =
      this.stderrLines.length > 0
        ? `\n${this.stderrLines.slice(-10).join("\n")}`
        : "";
    return new Error(`Codex app-server exited with ${detail}${stderr}`);
  }

  private normalizeError(error: unknown, fallback: string): Error {
    if (error instanceof Error) return error;
    return new Error(`${fallback}: ${String(error)}`);
  }
}
