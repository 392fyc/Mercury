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
  private serverProcess: ChildProcess | null = null;
  private port: number;
  private systemPrompt?: string;
  private systemPromptSentSessions = new Set<string>();

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

    this.serverProcess = spawn("opencode", ["serve", "--port", String(this.port)], {
      stdio: "pipe",
      detached: false,
      shell: true,
    });

    // Wait for server to be ready
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("opencode server startup timeout")), 15000);
      const check = async () => {
        try {
          const resp = await fetch(`http://localhost:${this.port}/health`);
          if (resp.ok) {
            clearTimeout(timeout);
            resolve();
            return;
          }
        } catch {
          // Not ready yet
        }
        setTimeout(check, 500);
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

      default:
        // All other commands: pass through to CLI/model
        return;
    }
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
    _images?: ImageAttachment[],
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

    // Prepend shared context only on the first message of each session (avoids token waste)
    let effectivePrompt = prompt;
    if (this.systemPrompt && !this.systemPromptSentSessions.has(sessionId)) {
      effectivePrompt = `[System Context]\n${this.systemPrompt}\n\n[User Prompt]\n${prompt}`;
      this.systemPromptSentSessions.add(sessionId);
    }

    // Use one-shot mode as the simpler integration path
    const result = await this.runOneShot(effectivePrompt);

    yield {
      role: "assistant",
      content: result,
      timestamp: Date.now(),
      metadata: { isResult: true },
    };
  }

  /**
   * One-shot execution: opencode run --format json "prompt"
   */
  private async runOneShot(prompt: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const proc = spawn("opencode", ["run", "--format", "json", prompt], {
        stdio: ["pipe", "pipe", "pipe"],
        shell: true,
      });

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
  }

  async handoffSession(
    oldSessionId: string,
    summary: string,
  ): Promise<SessionInfo> {
    const oldSession = this.sessions.get(oldSessionId);
    if (oldSession) oldSession.status = "overflow";
    return this.startSession(process.cwd());
  }

  /**
   * Cleanup: stop the HTTP server.
   */
  async shutdown(): Promise<void> {
    if (this.serverProcess) {
      this.serverProcess.kill();
      this.serverProcess = null;
    }
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
