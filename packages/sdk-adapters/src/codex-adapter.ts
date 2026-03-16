/**
 * Codex CLI SDK Adapter
 *
 * Uses @openai/codex-sdk to programmatically control Codex CLI.
 * Role: Dev Sub Agent — receives tasks from Main Agent, returns results.
 */

import { randomUUID } from "node:crypto";
import { writeFile, unlink } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  ImageAttachment,
  SessionInfo,
  SlashCommand,
} from "@mercury/core";

/** SDK InputEntry union — text or local_image */
type InputEntry =
  | { type: "text"; text: string }
  | { type: "local_image"; path: string };

type InputArg = string | InputEntry[];

export class CodexAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private sessionCwd = new Map<string, string>();
  private threads = new Map<string, unknown>();
  private codexInstance: unknown = null;
  private systemPrompt?: string;
  private systemPromptSentSessions = new Set<string>();

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

  /** Set shared context as system prompt (injected by Orchestrator). */
  setSystemPrompt(prompt: string) {
    this.systemPrompt = prompt;
    // Reset tracking so context is re-injected on next message in each session
    this.systemPromptSentSessions.clear();
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

  /**
   * Write base64-encoded images to temp files and return InputEntry[] with
   * local_image entries. Caller is responsible for cleanup via cleanupTempFiles().
   */
  private async imagesToTempFiles(
    images: ImageAttachment[],
  ): Promise<{ entries: InputEntry[]; tempFiles: string[] }> {
    const entries: InputEntry[] = [];
    const tempFiles: string[] = [];

    for (const img of images) {
      const EXT_MAP: Record<string, string> = { jpeg: "jpg", "svg+xml": "svg", tiff: "tif" };
      const rawExt = img.mediaType.split("/")[1] || "png";
      const ext = EXT_MAP[rawExt] ?? rawExt;
      const tempPath = join(tmpdir(), `mercury-img-${randomUUID()}.${ext}`);
      await writeFile(tempPath, Buffer.from(img.data, "base64"));
      tempFiles.push(tempPath);
      entries.push({ type: "local_image", path: tempPath });
    }

    return { entries, tempFiles };
  }

  /** Best-effort removal of temp image files. */
  private async cleanupTempFiles(tempFiles: string[]): Promise<void> {
    for (const f of tempFiles) {
      try {
        await unlink(f);
      } catch {
        // Ignore cleanup errors
      }
    }
  }

  /**
   * Build the input argument for thread.run / thread.runStreamed.
   * When images are present, returns an InputEntry[] with text + local_image entries.
   * Otherwise returns a plain string (preserving the original code path).
   */
  private buildInput(
    effectivePrompt: string,
    imageEntries?: InputEntry[],
  ): InputArg {
    if (!imageEntries || imageEntries.length === 0) {
      return effectivePrompt;
    }
    const entries: InputEntry[] = [
      { type: "text", text: effectivePrompt },
      ...imageEntries,
    ];
    return entries;
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

  /**
   * Intercept slash commands that can't be sent raw to the Codex SDK.
   * Returns AgentMessage(s) if handled, or yields nothing to fall through.
   */
  private async *handleSlashCommand(
    sessionId: string,
    prompt: string,
  ): AsyncGenerator<AgentMessage> {
    const trimmed = prompt.trim();
    const match = trimmed.match(/^\/(\S+)(?:\s+(.*))?$/);
    if (!match) return;

    const cmd = match[1].toLowerCase();
    const ts = Date.now();

    const infoMsg = (content: string): AgentMessage => ({
      role: "assistant",
      content,
      timestamp: ts,
      metadata: { isSlashCommandResponse: true, command: `/${cmd}` },
    });

    // Only intercept commands we can actually implement. Everything else passes through.
    switch (cmd) {
      case "help": {
        const cmds = this.getSlashCommands();
        const grouped = new Map<string, typeof cmds>();
        for (const c of cmds) {
          const cat = c.category ?? "other";
          if (!grouped.has(cat)) grouped.set(cat, []);
          grouped.get(cat)!.push(c);
        }
        let text = "## Available Commands\n\n";
        for (const [cat, list] of grouped) {
          text += `### ${cat}\n`;
          for (const c of list) text += `  **${c.name}**  ${c.description}\n`;
          text += "\n";
        }
        yield infoMsg(text);
        return;
      }

      case "clear":
      case "new": {
        const session = this.sessions.get(sessionId);
        if (session) session.status = "completed";
        yield infoMsg("Session cleared. Send a new message to start a fresh conversation.");
        return;
      }

      case "status": {
        const session = this.sessions.get(sessionId);
        yield infoMsg(
          `## Session Status\n` +
          `- **Agent**: ${this.config.displayName} (${this.agentId})\n` +
          `- **Integration**: SDK mode\n` +
          `- **Session**: ${sessionId}\n` +
          `- **Status**: ${session?.status ?? "unknown"}\n` +
          `- **Started**: ${session ? new Date(session.startedAt).toLocaleString() : "N/A"}`,
        );
        return;
      }

      case "exit":
      case "quit": {
        const session = this.sessions.get(sessionId);
        if (session) session.status = "completed";
        yield infoMsg("Session ended. Use the Start button to begin a new session.");
        return;
      }

      default:
        // All other commands: pass through to SDK/model
        return;
    }
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
  ): AsyncGenerator<AgentMessage> {
    // Intercept slash commands before sending to SDK
    const trimmed = prompt.trim();
    if (trimmed.startsWith("/")) {
      let handled = false;
      for await (const msg of this.handleSlashCommand(sessionId, prompt)) {
        handled = true;
        yield msg;
      }
      if (handled) return;
    }

    const thread = this.threads.get(sessionId) as {
      run(
        input: string | Array<{ type: string; text?: string; path?: string }>,
        options?: { outputSchema?: unknown; workingDirectory?: string; skipGitRepoCheck?: boolean },
      ): Promise<{
        items: Array<{ id: string; type: string; text?: string }>;
        finalResponse: string;
        usage: { input_tokens: number; output_tokens: number };
      }>;
      runStreamed?(
        input: string | Array<{ type: string; text?: string; path?: string }>,
      ): AsyncIterable<{
        type: string;
        text?: string;
        item?: { id: string; type: string; text?: string };
      }>;
    };

    if (!thread) throw new Error(`Thread ${sessionId} not found`);

    const session = this.sessions.get(sessionId);
    if (session) session.lastActiveAt = Date.now();

    // Prepend shared context only on the first message of each session (avoids token waste)
    let effectivePrompt = prompt;
    if (this.systemPrompt && !this.systemPromptSentSessions.has(sessionId)) {
      effectivePrompt = `[System Context]\n${this.systemPrompt}\n\n[User Prompt]\n${prompt}`;
      this.systemPromptSentSessions.add(sessionId);
    }

    // Prepare image temp files if images are provided
    let imageEntries: InputEntry[] | undefined;
    const tempFiles: string[] = [];
    if (images && images.length > 0) {
      const result = await this.imagesToTempFiles(images);
      imageEntries = result.entries;
      tempFiles.push(...result.tempFiles);
    }

    const input = this.buildInput(effectivePrompt, imageEntries);

    try {
      // Prefer streaming if available
      if (thread.runStreamed) {
        let yieldedAny = false;
        try {
          for await (const event of thread.runStreamed(input)) {
            if (session) session.lastActiveAt = Date.now();

            if (event.type === "text" && event.text) {
              yieldedAny = true;
              yield {
                role: "assistant" as const,
                content: event.text,
                timestamp: Date.now(),
              };
            } else if (event.type === "item" && event.item?.text) {
              yieldedAny = true;
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
              yieldedAny = true;
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
          // Only fall through to non-streaming if no events were yielded yet.
          // If we already yielded partial output, re-throw to avoid duplicate messages.
          if (yieldedAny) return;
        }
      }

      // Fallback: non-streaming run()
      const result = await thread.run(input);

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
    } finally {
      // Clean up temp image files regardless of success or failure
      await this.cleanupTempFiles(tempFiles);
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
