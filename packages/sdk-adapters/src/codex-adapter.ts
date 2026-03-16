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
      // ── Session & Navigation ──
      { name: "/new", description: "Start a new chat during a conversation", category: "session" },
      { name: "/resume", description: "Resume a saved chat", category: "session" },
      { name: "/fork", description: "Fork the current chat into a new thread", category: "session" },
      { name: "/clear", description: "Clear the terminal and start a new chat", category: "session" },
      { name: "/rename", description: "Rename the current thread", category: "session" },
      { name: "/compact", description: "Summarize conversation to prevent hitting context limit", category: "session" },
      { name: "/quit", description: "Exit Codex", category: "session" },
      // ── Model & Mode ──
      { name: "/model", description: "Choose what model and reasoning effort to use", category: "model" },
      { name: "/fast", description: "Toggle Fast mode for fastest inference at 2X plan usage", category: "model" },
      { name: "/plan", description: "Switch to Plan mode", category: "model" },
      { name: "/collab", description: "Change collaboration mode (experimental)", category: "model" },
      // ── Permissions & Sandbox ──
      { name: "/approvals", description: "Choose what Codex is allowed to do", category: "permissions" },
      { name: "/setup-default-sandbox", description: "Set up elevated agent sandbox", category: "permissions" },
      { name: "/sandbox-add-read-dir", description: "Let sandbox read a directory", category: "permissions", args: [{ name: "path", description: "Absolute directory path", required: true, type: "string" }] },
      // ── Tools & Integrations ──
      { name: "/mcp", description: "List configured MCP tools", category: "integrations" },
      { name: "/apps", description: "Manage apps (connectors)", category: "integrations" },
      { name: "/skills", description: "Use skills to improve how Codex performs tasks", category: "integrations" },
      { name: "/mention", description: "Mention/attach a file", category: "integrations" },
      // ── Agent & Threads ──
      { name: "/agent", description: "Switch the active agent thread", category: "agents" },
      // ── Code & Review ──
      { name: "/review", description: "Review current changes and find issues", category: "code" },
      { name: "/diff", description: "Show git diff including untracked files", category: "code" },
      { name: "/copy", description: "Copy the latest Codex output to clipboard", category: "code" },
      { name: "/init", description: "Create an AGENTS.md file with instructions for Codex", category: "code" },
      // ── Config & Display ──
      { name: "/status", description: "Show current session configuration and token usage", category: "config" },
      { name: "/statusline", description: "Configure which items appear in the status line", category: "config" },
      { name: "/theme", description: "Choose a syntax highlighting theme", category: "config" },
      { name: "/personality", description: "Choose a communication style for Codex", category: "config" },
      { name: "/settings", description: "Configure realtime microphone/speaker", category: "config" },
      { name: "/experimental", description: "Toggle experimental features", category: "config" },
      // ── Experimental ──
      { name: "/realtime", description: "Toggle realtime voice mode (experimental)", category: "experimental" },
      // ── Background Processes ──
      { name: "/ps", description: "List background terminals", category: "processes" },
      { name: "/clean", description: "Stop all background terminals", category: "processes" },
      // ── Account ──
      { name: "/logout", description: "Log out of Codex", category: "account" },
      { name: "/feedback", description: "Send logs to maintainers", category: "account" },
    ];
  }
}
