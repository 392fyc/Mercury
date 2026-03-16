/**
 * opencode HTTP Server Adapter
 *
 * Uses opencode's HTTP serve mode for programmatic control.
 * Role: Dev Sub Agent — alternative to Codex.
 *
 * Integration: opencode serve --port 4096, then HTTP requests to control.
 * Fallback: opencode run --format json for one-shot execution.
 */

import { randomUUID } from "node:crypto";
import { spawn, type ChildProcess } from "node:child_process";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  ImageAttachment,
  SessionInfo,
  SlashCommand,
} from "@mercury/core";

export class OpencodeAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private sessionCwd = new Map<string, string>();
  private serverProcess: ChildProcess | null = null;
  private port: number;
  private systemPrompt?: string;
  private systemPromptSentSessions = new Set<string>();
  /** Maps Mercury sessionId → opencode server session ID */
  private serverSessions = new Map<string, string>();
  /** Tracks whether the HTTP server is confirmed available (with TTL) */
  private httpServerAvailableUntil = 0; // timestamp — 0 means not cached

  constructor(port = 4096, config?: Partial<AgentConfig>) {
    this.port = port;
    this.config = {
      id: "opencode",
      displayName: "opencode",
      cli: "opencode",
      role: "dev",
      integration: "http",
      capabilities: ["code", "parallel", "design_to_code"],
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

  /**
   * Start the opencode HTTP server if not running.
   */
  private async ensureServer(): Promise<void> {
    if (this.serverProcess) return;

    // Check if server is already running
    try {
      const resp = await fetch(`http://localhost:${this.port}/health`);
      if (resp.ok) return;
    } catch {
      // Not running, start it
    }

    // shell: true only on Windows for .cmd wrapper resolution (no user input in args here)
    this.serverProcess = spawn("opencode", ["serve", "--port", String(this.port)], {
      stdio: "pipe",
      detached: false,
      shell: process.platform === "win32",
    });

    // Wait for server to be ready (with proper cleanup of polling timer)
    await new Promise<void>((resolve, reject) => {
      let settled = false;
      let pollTimer: ReturnType<typeof setTimeout> | null = null;
      const timeout = setTimeout(() => {
        settled = true;
        if (pollTimer) clearTimeout(pollTimer);
        reject(new Error("opencode server startup timeout"));
      }, 15000);
      const check = async () => {
        if (settled) return;
        try {
          const resp = await fetch(`http://localhost:${this.port}/health`);
          if (resp.ok) {
            settled = true;
            clearTimeout(timeout);
            resolve();
            return;
          }
        } catch {
          // Not ready yet
        }
        if (!settled) {
          pollTimer = setTimeout(check, 500);
        }
      };
      check();
    });
  }

  async startSession(cwd: string): Promise<SessionInfo> {
    await this.ensureServer();

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

  /**
   * Intercept slash commands that can't be sent to opencode CLI.
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

      case "new": {
        const session = this.sessions.get(sessionId);
        if (session) session.status = "completed";
        yield infoMsg("Session cleared. Send a new message to start a fresh conversation.");
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
      case "clear":
      case "compact":
      case "summarize":
      case "connect":
      case "details":
      case "editor":
      case "export":
      case "init":
      case "models":
      case "redo":
      case "sessions":
      case "resume":
      case "continue":
      case "share":
      case "themes":
      case "thinking":
      case "undo":
      case "unshare":
      case "upgrade": {
        yield infoMsg(
          `**/${cmd}** requires the opencode terminal.\n\n` +
          `Run in your terminal:\n\`\`\`\nopencode\n\`\`\`\nThen use \`/${cmd}\` inside the TUI.`,
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

  /**
   * Check if the HTTP server is reachable.
   */
  private async isHttpServerAvailable(): Promise<boolean> {
    // Use cached result if within TTL (30 seconds)
    if (Date.now() < this.httpServerAvailableUntil) return true;
    try {
      const resp = await fetch(`http://localhost:${this.port}/health`);
      if (resp.ok) {
        this.httpServerAvailableUntil = Date.now() + 30_000;
        return true;
      }
    } catch {
      // Server not reachable
    }
    this.httpServerAvailableUntil = 0;
    return false;
  }

  /**
   * Send prompt via opencode HTTP server API.
   * Uses native system prompt field and FilePartInput for images.
   */
  private async sendPromptHttp(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
  ): Promise<string> {
    // Get or create an opencode server session for this Mercury session
    let ocSessionId = this.serverSessions.get(sessionId);
    if (!ocSessionId) {
      const resp = await fetch(`http://localhost:${this.port}/session`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: `Mercury-${sessionId.slice(0, 8)}` }),
      });
      if (!resp.ok) {
        throw new Error(`Failed to create opencode session: ${resp.status} ${resp.statusText}`);
      }
      const data = await resp.json();
      ocSessionId = data.id as string;
      this.serverSessions.set(sessionId, ocSessionId);
    }

    // Build parts array (TextPartInput + FilePartInput for images)
    const parts: Array<{ type: string; [key: string]: unknown }> = [];
    parts.push({ type: "text", text: prompt });

    if (images) {
      for (const img of images) {
        parts.push({
          type: "file",
          mime: img.mediaType,
          filename: img.filename || "image.png",
          url: `data:${img.mediaType};base64,${img.data}`,
        });
      }
    }

    // Send message with native system prompt — only on first message per session
    // to avoid wasting tokens on repeated system context injection
    const includeSystemPrompt =
      this.systemPrompt && !this.systemPromptSentSessions.has(sessionId);
    const resp = await fetch(
      `http://localhost:${this.port}/session/${ocSessionId}/message`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system: includeSystemPrompt ? this.systemPrompt : undefined,
          parts,
        }),
      },
    );
    if (includeSystemPrompt) {
      this.systemPromptSentSessions.add(sessionId);
    }

    if (!resp.ok) {
      throw new Error(`opencode HTTP message failed: ${resp.status} ${resp.statusText}`);
    }

    const data = await resp.json();
    // Extract text content from response parts
    if (data.parts && Array.isArray(data.parts)) {
      const textParts = data.parts
        .filter((p: { type: string }) => p.type === "text")
        .map((p: { text: string }) => p.text);
      if (textParts.length > 0) return textParts.join("\n");
    }
    // Fallback: return raw response or content field
    return data.response || data.content || JSON.stringify(data);
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
  ): AsyncGenerator<AgentMessage> {
    // Intercept slash commands before sending to CLI
    const trimmed = prompt.trim();
    if (trimmed.startsWith("/")) {
      let handled = false;
      for await (const msg of this.handleSlashCommand(sessionId, prompt)) {
        handled = true;
        yield msg;
      }
      if (handled) return;
    }

    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    session.lastActiveAt = Date.now();

    // Try HTTP server path first (supports native system prompt + images)
    let result: string | null = null;
    if (await this.isHttpServerAvailable()) {
      try {
        // HTTP mode: system prompt is sent natively via the `system` field,
        // and images are sent as FilePartInput with data: URIs.
        // No need for prompt prepending workaround.
        result = await this.sendPromptHttp(sessionId, prompt, images);
        // systemPromptSentSessions is tracked inside sendPromptHttp()
      } catch {
        // HTTP path failed — fall back to CLI one-shot mode
        this.httpServerAvailableUntil = 0;
        result = null;
      }
    }

    if (result === null) {
      // Fallback: CLI one-shot mode (no image support, uses prompt prepending for system context)
      let effectivePrompt = prompt;
      if (this.systemPrompt && !this.systemPromptSentSessions.has(sessionId)) {
        effectivePrompt = `[System Context]\n${this.systemPrompt}\n\n[User Prompt]\n${prompt}`;
        this.systemPromptSentSessions.add(sessionId);
      }
      result = await this.runOneShot(effectivePrompt);
    }

    yield {
      role: "assistant",
      content: result,
      timestamp: Date.now(),
      metadata: { isResult: true },
    };
  }

  /**
   * One-shot execution via stdin piping to avoid shell injection.
   * On Windows, shell: true is needed for .cmd wrapper resolution;
   * prompt is piped via stdin (not as a CLI arg) for safety.
   */
  private async runOneShot(prompt: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const isWindows = process.platform === "win32";
      const proc = spawn("opencode", ["run", "--format", "json"], {
        stdio: ["pipe", "pipe", "pipe"],
        shell: isWindows,
      });

      // Pipe prompt via stdin to prevent shell metacharacter injection
      proc.stdin?.write(prompt);
      proc.stdin?.end();

      let stdout = "";
      let stderr = "";

      proc.stdout?.on("data", (data: Buffer) => {
        stdout += data.toString();
      });
      proc.stderr?.on("data", (data: Buffer) => {
        stderr += data.toString();
      });

      proc.on("close", (code) => {
        if (code === 0) {
          try {
            const parsed = JSON.parse(stdout);
            resolve(parsed.response || parsed.content || stdout);
          } catch {
            resolve(stdout);
          }
        } else {
          reject(new Error(`opencode exited with code ${code}: ${stderr}`));
        }
      });

      proc.on("error", (err) => {
        reject(new Error(`Failed to run 'opencode': ${err.message}. Is opencode installed and on PATH?`));
      });
    });
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
    this.serverSessions.delete(sessionId);
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

  /**
   * Cleanup: stop the HTTP server.
   */
  async shutdown(): Promise<void> {
    if (this.serverProcess) {
      this.serverProcess.kill();
      this.serverProcess = null;
    }
    this.serverSessions.clear();
    this.httpServerAvailableUntil = 0;
  }

  getSlashCommands(): SlashCommand[] {
    // opencode uses keyboard shortcuts (leader key ctrl+x) rather than
    // traditional "/" slash commands. These are mapped here as slash commands
    // for Mercury GUI consistency. Source: opencode.ai/docs/keybinds/
    return [
      // ── Session ──
      { name: "/new", description: "Start a new session", category: "session" },
      { name: "/sessions", description: "List and switch sessions", category: "session" },
      { name: "/compact", description: "Summarize session and create a new one with summary", category: "session" },
      { name: "/timeline", description: "Show session timeline", category: "session" },
      { name: "/export", description: "Export session data", category: "session" },
      { name: "/quit", description: "Exit opencode", category: "session" },
      // ── Code ──
      { name: "/undo", description: "Undo last change", category: "code" },
      { name: "/redo", description: "Redo last undone change", category: "code" },
      { name: "/copy", description: "Copy messages to clipboard", category: "code" },
      { name: "/editor", description: "Open external editor", category: "code" },
      // ── Model & Agent ──
      { name: "/model", description: "List and select AI model", category: "model" },
      { name: "/agent", description: "List and select agent", category: "model" },
      // ── Config & Display ──
      { name: "/init", description: "Create/update OpenCode.md memory file", category: "config" },
      { name: "/theme", description: "Choose a color theme", category: "config" },
      { name: "/help", description: "Show keyboard shortcuts", category: "config" },
      { name: "/conceal", description: "Toggle tool output visibility", category: "config" },
      { name: "/sidebar", description: "Toggle sidebar visibility", category: "config" },
      // ── Sharing ──
      { name: "/share", description: "Create a shareable link for the session", category: "sharing" },
    ];
  }
}
