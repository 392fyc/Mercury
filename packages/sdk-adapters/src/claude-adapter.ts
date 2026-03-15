/**
 * Claude Code SDK Adapter
 *
 * Uses @anthropic-ai/claude-agent-sdk to programmatically control Claude Code.
 *
 * Key capabilities:
 * - Start/resume sessions via query()
 * - Subagent spawning (for internal delegation)
 * - Session handoff on context overflow
 * - systemPrompt injection for project context
 */

import { randomUUID } from "node:crypto";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  SessionInfo,
} from "@mercury/core";

/** Extract text content from SDK message content blocks. */
function extractTextFromBlocks(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((block: Record<string, unknown>) => "text" in block && typeof block.text === "string")
    .map((block: Record<string, unknown>) => block.text as string)
    .join("\n");
}

export class ClaudeAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private sessionCwd = new Map<string, string>();
  private queryModule: typeof import("@anthropic-ai/claude-agent-sdk") | null =
    null;
  private systemPrompt?: string;

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

  /** Set the system prompt (e.g. project context from KB). */
  setSystemPrompt(prompt: string) {
    this.systemPrompt = prompt;
  }

  private async loadSdk() {
    if (!this.queryModule) {
      try {
        this.queryModule = await import("@anthropic-ai/claude-agent-sdk");
      } catch {
        throw new Error(
          "Claude Code SDK not available. Ensure @anthropic-ai/claude-agent-sdk is installed and 'claude' CLI is on PATH.",
        );
      }
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
    this.sessionCwd.set(sessionId, cwd);
    return info;
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
  ): AsyncGenerator<AgentMessage> {
    const sdk = await this.loadSdk();
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    const cwd = this.sessionCwd.get(sessionId) ?? process.cwd();
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
      permissionMode: "acceptEdits",
      maxTurns: 30,
      cwd,
    };

    if (this.systemPrompt) {
      options.systemPrompt = this.systemPrompt;
    }

    // If resuming from a previous SDK session, pass the resume id
    if (isResume && session.parentSessionId) {
      options.resume = session.parentSessionId;
    }

    let sdkSessionId: string | undefined;

    for await (const message of sdk.query({ prompt, options })) {
      session.lastActiveAt = Date.now();

      if (typeof message !== "object" || message === null || !("type" in message)) {
        continue;
      }

      const msg = message as Record<string, unknown>;

      // Capture SDK session ID from init message
      if (msg.type === "system" && msg.subtype === "init" && msg.session_id) {
        sdkSessionId = msg.session_id as string;
      }

      // Assistant messages — content is an array of blocks [{text: "..."}, {name: "ToolName"}]
      if (msg.type === "assistant") {
        const content = msg.message
          ? extractTextFromBlocks((msg.message as Record<string, unknown>).content)
          : extractTextFromBlocks(msg.content);

        if (content) {
          yield {
            role: "assistant",
            content,
            timestamp: Date.now(),
            metadata: { sdkSessionId },
          };
        }
      }

      // Result messages — final output
      if (msg.type === "result") {
        const resultText =
          typeof msg.result === "string"
            ? msg.result
            : extractTextFromBlocks(msg.result);
        if (resultText) {
          yield {
            role: "assistant",
            content: resultText,
            timestamp: Date.now(),
            metadata: {
              sdkSessionId,
              isResult: true,
              subtype: msg.subtype as string | undefined,
            },
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
