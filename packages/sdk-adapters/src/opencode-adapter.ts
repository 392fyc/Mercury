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
  SessionInfo,
  SlashCommand,
} from "@mercury/core";

export class OpencodeAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, SessionInfo>();
  private serverProcess: ChildProcess | null = null;
  private port: number;

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

  async *sendPrompt(
    sessionId: string,
    prompt: string,
  ): AsyncGenerator<AgentMessage> {
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    session.lastActiveAt = Date.now();

    // Use one-shot mode as the simpler integration path
    const result = await this.runOneShot(prompt);

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
