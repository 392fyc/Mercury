/**
 * Codex CLI SDK Adapter
 *
 * Uses @openai/codex-sdk to programmatically control Codex CLI.
 * Role: Dev Sub Agent — receives tasks from Main Agent, returns results.
 */

import { randomUUID } from "node:crypto";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  SessionInfo,
  SlashCommand,
} from "@mercury/core";

export class CodexAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private sessionCwd = new Map<string, string>();
  private threads = new Map<string, unknown>();
  private codexInstance: unknown = null;

  constructor(config?: Partial<AgentConfig>) {
    this.config = {
      id: "codex-cli",
      displayName: "Codex CLI",
      cli: "codex",
      role: "dev",
      integration: "sdk",
      capabilities: ["code", "batch_json", "test"],
      restrictions: ["no_kb_write", "isolated_branch_only"],
      maxConcurrentSessions: 3,
      ...config,
    };
    this.agentId = this.config.id;
  }

  private async loadSdk() {
    if (!this.codexInstance) {
      try {
        const { Codex } = await import("@openai/codex-sdk");
        this.codexInstance = new Codex();
      } catch {
        throw new Error(
          "Codex SDK not available. Ensure @openai/codex-sdk is installed and 'codex' CLI is on PATH.",
        );
      }
    }
    return this.codexInstance as {
      startThread(opts?: { workingDirectory?: string }): unknown;
      resumeThread(id: string): unknown;
    };
  }

  async startSession(cwd: string): Promise<SessionInfo> {
    const codex = await this.loadSdk();
    const thread = codex.startThread({ workingDirectory: cwd });
    const sessionId = randomUUID();

    this.threads.set(sessionId, thread);
    this.sessionCwd.set(sessionId, cwd);
    const info: SessionInfo = {
      sessionId,
      agentId: this.agentId,
      startedAt: Date.now(),
      lastActiveAt: Date.now(),
      status: "active",
    };
    this.sessions.set(sessionId, info);
    return info;
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
  ): AsyncGenerator<AgentMessage> {
    const thread = this.threads.get(sessionId) as {
      run(prompt: string): Promise<{
        items: Array<{ id: string; type: string; text?: string }>;
        finalResponse: string;
        usage: { input_tokens: number; output_tokens: number };
      }>;
      runStreamed?(
        prompt: string,
      ): AsyncIterable<{
        type: string;
        text?: string;
        item?: { id: string; type: string; text?: string };
      }>;
    };

    if (!thread) throw new Error(`Thread ${sessionId} not found`);

    const session = this.sessions.get(sessionId);
    if (session) session.lastActiveAt = Date.now();

    // Prefer streaming if available
    if (thread.runStreamed) {
      try {
        for await (const event of thread.runStreamed(prompt)) {
          if (session) session.lastActiveAt = Date.now();

          if (event.type === "text" && event.text) {
            yield {
              role: "assistant" as const,
              content: event.text,
              timestamp: Date.now(),
            };
          } else if (event.type === "item" && event.item?.text) {
            yield {
              role: "assistant" as const,
              content: event.item.text,
              timestamp: Date.now(),
              metadata: {
                itemId: event.item.id,
                itemType: event.item.type,
              },
            };
          } else if (event.type === "result" && event.text) {
            yield {
              role: "assistant" as const,
              content: event.text,
              timestamp: Date.now(),
              metadata: { isResult: true },
            };
          }
        }
        return;
      } catch {
        // Fall through to non-streaming mode
      }
    }

    // Fallback: non-streaming run()
    const result = await thread.run(prompt);

    for (const item of result.items) {
      if (item.text) {
        yield {
          role: "assistant" as const,
          content: item.text,
          timestamp: Date.now(),
          metadata: { itemId: item.id, itemType: item.type },
        };
      }
    }

    if (result.finalResponse) {
      yield {
        role: "assistant" as const,
        content: result.finalResponse,
        timestamp: Date.now(),
        metadata: {
          isResult: true,
          usage: result.usage,
        },
      };
    }
  }

  async resumeSession(sessionId: string): Promise<SessionInfo> {
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);
    session.status = "active";
    return session;
  }

  async endSession(sessionId: string): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (session) session.status = "completed";
    this.threads.delete(sessionId);
  }

  async handoffSession(
    oldSessionId: string,
    _summary: string,
  ): Promise<SessionInfo> {
    const oldSession = this.sessions.get(oldSessionId);
    if (oldSession) oldSession.status = "overflow";

    const cwd = this.sessionCwd.get(oldSessionId) ?? process.cwd();
    const newSession = await this.startSession(cwd);
    newSession.parentSessionId = oldSessionId;
    return newSession;
  }

  getSlashCommands(): SlashCommand[] {
    return [
      { name: "/help", description: "Show available commands", category: "general" },
      { name: "/model", description: "Change the model", category: "config", args: [{ name: "model", description: "Model name", required: false, type: "string" }] },
      { name: "/approval", description: "Change approval mode (suggest, auto-edit, full-auto)", category: "config", args: [{ name: "mode", description: "suggest|auto-edit|full-auto", required: false, type: "string" }] },
      { name: "/undo", description: "Undo last file change", category: "code" },
      { name: "/diff", description: "Show pending changes", category: "code" },
      { name: "/clear", description: "Clear conversation context", category: "session" },
      { name: "/exit", description: "Exit the CLI", category: "general" },
    ];
  }
}
