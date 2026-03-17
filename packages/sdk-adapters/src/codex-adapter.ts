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
  RunResult as CodexRunResult,
  Thread as CodexThread,
  ThreadEvent as CodexThreadEvent,
  ThreadItem as CodexThreadItem,
} from "@openai/codex-sdk";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  AgentSendHooks,
  ImageAttachment,
  SessionInfo,
  SlashCommand,
} from "@mercury/core";

/** SDK InputEntry union — text or local_image */
type InputEntry =
  | { type: "text"; text: string }
  | { type: "local_image"; path: string };

type InputArg = string | InputEntry[];

const CODEX_COMPACTION_NOTICE =
  "Context compaction triggered — role boundary instructions from earlier turns may have been summarized. Current turn still carries full role context via prepend.";

export class CodexAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private sessionCwd = new Map<string, string>();
  private threads = new Map<string, unknown>();
  private codexInstance: unknown = null;
  private sharedSystemPrompt?: string;

  constructor(config?: Partial<AgentConfig>) {
    this.config = {
      id: "codex-cli",
      displayName: "Codex CLI",
      cli: "codex",
      roles: ["dev"],
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
    this.sharedSystemPrompt = prompt;
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
      startThread(opts?: { workingDirectory?: string }): CodexThread;
      resumeThread(id: string, opts?: { workingDirectory?: string }): CodexThread;
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

  private normalizeCompactionText(text: string): string {
    return text.toLowerCase().replace(/\s+/g, " ").trim();
  }

  private isCompactionSignal(text?: string): boolean {
    if (!text) return false;
    const normalized = this.normalizeCompactionText(text);
    const hasDirectCue =
      normalized.includes("compaction") ||
      normalized.includes("compact conversation") ||
      normalized.includes("context compact") ||
      normalized.includes("conversation compact");
    const hasTruncationCue =
      normalized.includes("truncat") &&
      (normalized.includes("context") || normalized.includes("conversation"));
    return hasDirectCue || hasTruncationCue;
  }

  private buildCompactionNotice(): AgentMessage {
    return {
      role: "system",
      content: CODEX_COMPACTION_NOTICE,
      timestamp: Date.now(),
      metadata: { messageType: "context_compaction_notice", adapter: this.agentId },
    };
  }

  private getItemText(item: CodexThreadItem): string | undefined {
    switch (item.type) {
      case "agent_message":
      case "reasoning":
        return item.text;
      case "error":
        return item.message;
      case "command_execution":
        return item.aggregated_output;
      default:
        return undefined;
    }
  }

  private getEventCompactionText(event: CodexThreadEvent): string | undefined {
    switch (event.type) {
      case "item.started":
      case "item.updated":
      case "item.completed":
        return this.getItemText(event.item);
      case "turn.failed":
        return event.error.message;
      case "error":
        return event.message;
      default:
        return undefined;
    }
  }

  private mapCompletedItemToMessage(item: CodexThreadItem): AgentMessage | null {
    switch (item.type) {
      case "agent_message":
      case "reasoning":
        return {
          role: "assistant",
          content: item.text,
          timestamp: Date.now(),
          metadata: {
            itemId: item.id,
            itemType: item.type,
          },
        };
      case "error":
        return {
          role: "system",
          content: item.message,
          timestamp: Date.now(),
          metadata: {
            itemId: item.id,
            itemType: item.type,
          },
        };
      default:
        return null;
    }
  }

  private resultHasCompactionSignal(result: CodexRunResult): boolean {
    return result.items.some((item) => this.isCompactionSignal(this.getItemText(item))) ||
      this.isCompactionSignal(result.finalResponse);
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
      cwd,
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

    // Commands Mercury can natively implement are handled here.
    // CLI-only commands are rewritten as guidance pointing users to the original CLI.
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

      // CLI-only commands — rewritten as terminal guidance in Mercury GUI
      case "agent":
      case "apps":
      case "compact":
      case "copy":
      case "debug-config":
      case "diff":
      case "experimental":
      case "feedback":
      case "fork":
      case "init":
      case "logout":
      case "mcp":
      case "mention":
      case "model":
      case "permissions":
      case "approvals":
      case "personality":
      case "plan":
      case "ps":
      case "resume":
      case "review":
      case "sandbox-add-read-dir":
      case "skills":
      case "statusline": {
        yield infoMsg(
          `**/${cmd}** requires the Codex CLI terminal.\n\n` +
          `Run in your terminal:\n\`\`\`\ncodex /${cmd}\n\`\`\``,
        );
        return;
      }

      default:
        // Unknown command — inform user rather than sending as prompt
        yield infoMsg(
          `Unknown command **/${cmd}**. Type **/help** to see available commands.`,
        );
        return;
    }
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
    hooks?: AgentSendHooks,
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

    const thread = this.threads.get(sessionId) as CodexThread | undefined;

    if (!thread) throw new Error(`Thread ${sessionId} not found`);

    const session = this.sessions.get(sessionId);
    if (session) session.lastActiveAt = Date.now();

    if (hooks?.onApprovalRequest) {
      const decision = await hooks.onApprovalRequest({
        kind: "permission",
        toolName: "codex_turn",
        summary: "Approve Codex turn execution",
        rawRequest: {
          promptPreview: prompt.slice(0, 200),
          sessionId,
        },
      });
      if (decision.action !== "approve") {
        yield {
          role: "system",
          content: decision.reason ?? "Mercury denied this Codex execution request.",
          timestamp: Date.now(),
          metadata: { deniedByMercury: true },
        };
        return;
      }
    }

    const promptContext = session?.frozenSystemPrompt ?? this.sharedSystemPrompt;
    const effectivePrompt = promptContext
      ? `[Mercury Role Context]\n${promptContext}\n\n[User Prompt]\n${prompt}`
      : prompt;

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
        let compactionNotified = false;
        try {
          const { events } = await thread.runStreamed(input);
          for await (const event of events) {
            if (session) session.lastActiveAt = Date.now();

            const compactionText = this.getEventCompactionText(event);
            if (!compactionNotified && this.isCompactionSignal(compactionText)) {
              compactionNotified = true;
              yieldedAny = true;
              yield this.buildCompactionNotice();
            }

            if (event.type === "item.completed") {
              const message = this.mapCompletedItemToMessage(event.item);
              if (!message) continue;
              yieldedAny = true;
              yield message;
              continue;
            }

            if (event.type === "turn.failed") {
              throw new Error(event.error.message);
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
      let yieldedFinalResponse = false;

      if (this.resultHasCompactionSignal(result)) {
        yield this.buildCompactionNotice();
      }

      for (const item of result.items) {
        const message = this.mapCompletedItemToMessage(item);
        if (!message) continue;
        if (item.type === "agent_message" && item.text === result.finalResponse) {
          yieldedFinalResponse = true;
        }
        yield message;
      }

      if (result.finalResponse && !yieldedFinalResponse) {
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
      if (thread.id) {
        if (session) {
          session.resumeToken = thread.id;
        }
      }
      await this.cleanupTempFiles(tempFiles);
    }
  }

  async resumeSession(
    sessionId: string,
    persistedInfo?: SessionInfo,
    cwd?: string,
  ): Promise<SessionInfo> {
    let session = this.sessions.get(sessionId);
    const effectiveCwd = session?.cwd ?? persistedInfo?.cwd ?? cwd ?? process.cwd();

    if (!session) {
      if (!persistedInfo?.resumeToken) {
        throw new Error(`Session ${sessionId} not found`);
      }
      session = {
        ...persistedInfo,
        sessionId,
        agentId: this.agentId,
        cwd: effectiveCwd,
      };
      this.sessions.set(sessionId, session);
      this.sessionCwd.set(sessionId, effectiveCwd);
    }

    if (!this.threads.has(sessionId)) {
      if (!session.resumeToken) {
        throw new Error(`Session ${sessionId} is missing a Codex resume token`);
      }
      const codex = await this.loadSdk();
      const thread = codex.resumeThread(session.resumeToken, { workingDirectory: effectiveCwd });
      this.threads.set(sessionId, thread);
      this.sessionCwd.set(sessionId, effectiveCwd);
    }

    session.status = "active";
    session.lastActiveAt = Date.now();
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
    newSession.role = oldSession?.role;
    newSession.frozenRole = oldSession?.frozenRole;
    newSession.frozenSystemPrompt = oldSession?.frozenSystemPrompt;
    newSession.baseRolePromptHash = oldSession?.baseRolePromptHash;
    newSession.promptHash = oldSession?.promptHash;
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
