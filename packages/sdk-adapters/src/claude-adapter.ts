/**
 * Claude Code SDK Adapter
 *
 * Uses @anthropic-ai/claude-agent-sdk to programmatically control Claude Code.
 * This is the Main Agent adapter — user interaction is routed through here.
 *
 * Key capabilities:
 * - Start/resume sessions via query()
 * - Subagent spawning (for internal delegation)
 * - Session handoff on context overflow
 */

import { randomUUID } from "node:crypto";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  SessionInfo,
} from "@mercury/core";

export class ClaudeAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private queryModule: typeof import("@anthropic-ai/claude-agent-sdk") | null =
    null;

  constructor(config?: Partial<AgentConfig>) {
    this.config = {
      id: "claude-code",
      displayName: "Claude Code",
      cli: "claude",
      role: "main",
      integration: "sdk",
      capabilities: [
        "code",
        "review",
        "research",
        "orchestration",
        "subagent",
      ],
      restrictions: [],
      maxConcurrentSessions: 5,
      ...config,
    };
    this.agentId = this.config.id;
  }

  private async loadSdk() {
    if (!this.queryModule) {
      this.queryModule = await import("@anthropic-ai/claude-agent-sdk");
    }
    return this.queryModule;
  }

  async startSession(cwd: string): Promise<SessionInfo> {
    const sessionId = randomUUID();
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
    const sdk = await this.loadSdk();
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    const isResume = session.parentSessionId != null;

    const options: Record<string, unknown> = {
      allowedTools: [
        "Read",
        "Edit",
        "Write",
        "Bash",
        "Glob",
        "Grep",
        "Agent",
      ],
      maxTurns: 30,
    };

    // If resuming from a previous SDK session, pass the resume id
    if (isResume && session.parentSessionId) {
      options.resume = session.parentSessionId;
    }

    let sdkSessionId: string | undefined;

    for await (const message of sdk.query({ prompt, options })) {
      session.lastActiveAt = Date.now();

      // Capture SDK session ID from init message
      if (
        typeof message === "object" &&
        message !== null &&
        "type" in message
      ) {
        const msg = message as Record<string, unknown>;

        if (msg.type === "system" && msg.subtype === "init" && msg.session_id) {
          sdkSessionId = msg.session_id as string;
        }

        // Yield assistant messages
        if (msg.type === "assistant" && typeof msg.content === "string") {
          yield {
            role: "assistant",
            content: msg.content,
            timestamp: Date.now(),
            metadata: { sdkSessionId },
          };
        }

        // Yield result messages
        if ("result" in msg && typeof msg.result === "string") {
          yield {
            role: "assistant",
            content: msg.result,
            timestamp: Date.now(),
            metadata: { sdkSessionId, isResult: true },
          };
        }
      }
    }

    // Store SDK session ID for potential resume
    if (sdkSessionId) {
      session.parentSessionId = sdkSessionId;
    }
  }

  async resumeSession(sessionId: string): Promise<SessionInfo> {
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);
    session.status = "active";
    session.lastActiveAt = Date.now();
    return session;
  }

  async endSession(sessionId: string): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.status = "completed";
    }
  }

  /**
   * Session continuity: create new session that inherits context from old one.
   * Used when context window overflows.
   */
  async handoffSession(
    oldSessionId: string,
    summary: string,
  ): Promise<SessionInfo> {
    const oldSession = this.sessions.get(oldSessionId);
    if (oldSession) {
      oldSession.status = "overflow";
    }

    const newSessionId = randomUUID();
    const newSession: SessionInfo = {
      sessionId: newSessionId,
      agentId: this.agentId,
      startedAt: Date.now(),
      lastActiveAt: Date.now(),
      status: "active",
      parentSessionId: oldSessionId,
    };
    this.sessions.set(newSessionId, newSession);

    // The first prompt to the new session will include the summary
    // so the agent has context from the previous session
    return newSession;
  }
}
