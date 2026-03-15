/**
 * Codex CLI SDK Adapter
 *
 * Uses @openai/codex-sdk to programmatically control Codex CLI.
 * Role: Dev Sub Agent — receives tasks from Main Agent, returns results.
 *
 * Two integration modes:
 * 1. SDK mode: direct Codex.startThread() / thread.run()
 * 2. MCP mode: codex mcp-server (for Main Agent to call as tool)
 */

import { randomUUID } from "node:crypto";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  SessionInfo,
} from "@mercury/core";

export class CodexAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private threads = new Map<string, unknown>(); // thread instances
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
      const { Codex } = await import("@openai/codex-sdk");
      this.codexInstance = new Codex();
    }
    return this.codexInstance as {
      startThread(): unknown;
      resumeThread(id: string): unknown;
    };
  }

  async startSession(cwd: string): Promise<SessionInfo> {
    const codex = await this.loadSdk();
    const thread = codex.startThread();
    const sessionId = randomUUID();

    this.threads.set(sessionId, thread);
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
      run(prompt: string): Promise<{ text?: string; content?: string }>;
      runStreamed(
        prompt: string,
      ): Promise<{
        events: AsyncIterable<{ type: string; text?: string }>;
      }>;
    };

    if (!thread) throw new Error(`Thread ${sessionId} not found`);

    const session = this.sessions.get(sessionId);
    if (session) session.lastActiveAt = Date.now();

    // Codex SDK returns { items, finalResponse, usage }
    const result = await thread.run(prompt) as {
      items: Array<{ id: string; type: string; text?: string }>;
      finalResponse: string;
      usage: { input_tokens: number; output_tokens: number };
    };

    // Yield individual items
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

    // Yield final response
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
    summary: string,
  ): Promise<SessionInfo> {
    const oldSession = this.sessions.get(oldSessionId);
    if (oldSession) oldSession.status = "overflow";

    // Start a fresh thread for the new session
    const newSession = await this.startSession(process.cwd());
    newSession.parentSessionId = oldSessionId;
    return newSession;
  }
}
