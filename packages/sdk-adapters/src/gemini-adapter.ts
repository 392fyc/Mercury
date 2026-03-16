/**
 * Gemini CLI Adapter
 *
 * Uses Google's Gemini CLI (`gemini`) in headless mode for programmatic control.
 * Role: Dev Sub Agent — alternative to Codex/opencode.
 *
 * Integration: `gemini -p "prompt" --output-format json` for one-shot execution.
 * Session resume: `gemini --resume <UUID>` for session continuity.
 * Images: `@./path/to/image.png` syntax in prompt.
 * System prompt: `GEMINI_SYSTEM_MD` environment variable pointing to temp file.
 *
 * No official SDK exists yet (tracked internally by Google, issue #15539).
 * Ref: https://github.com/google-gemini/gemini-cli
 */

import { randomUUID } from "node:crypto";
import { writeFile, unlink, mkdtemp, rmdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import type {
  AgentAdapter,
  AgentConfig,
  AgentMessage,
  AgentSendHooks,
  ImageAttachment,
  SessionInfo,
  SlashCommand,
} from "@mercury/core";

/** Tracks a Gemini CLI session — maps to a --resume UUID. */
interface GeminiSession {
  info: SessionInfo;
  cwd: string;
  /** Gemini CLI session UUID (captured from first run output) */
  geminiSessionId?: string;
}

export class GeminiAdapter implements AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;
  private sessions = new Map<string, GeminiSession>();
  private sharedSystemPrompt?: string;

  constructor(config?: Partial<AgentConfig>) {
    this.config = {
      id: "gemini-cli",
      displayName: "Gemini CLI",
      cli: "gemini",
      roles: ["dev"],
      integration: "sdk", // closest match — uses CLI spawning
      capabilities: ["code", "multimodal", "research"],
      restrictions: ["no_kb_write", "isolated_branch_only"],
      maxConcurrentSessions: 3,
      ...config,
    };
    this.agentId = this.config.id;
  }

  /** Set shared context as system prompt via GEMINI_SYSTEM_MD env var. */
  setSystemPrompt(prompt: string) {
    this.sharedSystemPrompt = prompt;
  }

  /**
   * Ensure system prompt is written to a temp file.
   * Gemini CLI reads GEMINI_SYSTEM_MD env var for system prompt override.
   */
  private async ensureSystemPromptFile(prompt: string | undefined): Promise<string | undefined> {
    if (!prompt) return undefined;
    const dir = await mkdtemp(join(tmpdir(), "mercury-gemini-"));
    const filePath = join(dir, "SYSTEM.md");
    await writeFile(filePath, prompt, "utf-8");
    return filePath;
  }

  /** Best-effort removal of a single file and its parent directory. */
  private async cleanupFile(filePath: string): Promise<void> {
    try {
      await unlink(filePath);
      // Also remove the parent temp directory created by mkdtemp
      await rmdir(dirname(filePath));
    } catch {
      // Ignore cleanup errors
    }
  }

  async startSession(cwd: string): Promise<SessionInfo> {
    const sessionId = randomUUID();
    const info: SessionInfo = {
      sessionId,
      agentId: this.agentId,
      cwd,
      startedAt: Date.now(),
      lastActiveAt: Date.now(),
      status: "active",
    };
    this.sessions.set(sessionId, { info, cwd });
    return info;
  }

  /**
   * Intercept slash commands that can't be sent to Gemini CLI.
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

      case "new":
      case "clear": {
        const session = this.sessions.get(sessionId);
        if (session) {
          session.info.status = "completed";
          // Clear Gemini session ID so next prompt starts a fresh conversation
          session.geminiSessionId = undefined;
        }
        yield infoMsg("Session cleared. Send a new message to start a fresh conversation.");
        return;
      }

      case "exit":
      case "quit": {
        const session = this.sessions.get(sessionId);
        if (session) session.info.status = "completed";
        yield infoMsg("Session ended. Use the Start button to begin a new session.");
        return;
      }

      case "stats": {
        const session = this.sessions.get(sessionId);
        yield infoMsg(
          `## Session Status\n` +
          `- **Agent**: ${this.config.displayName} (${this.agentId})\n` +
          `- **Integration**: CLI headless mode\n` +
          `- **Session**: ${sessionId}\n` +
          `- **Gemini Session**: ${session?.geminiSessionId ?? "N/A"}\n` +
          `- **Status**: ${session?.info.status ?? "unknown"}\n` +
          `- **Started**: ${session ? new Date(session.info.startedAt).toLocaleString() : "N/A"}`,
        );
        return;
      }

      // CLI-only commands — rewritten as terminal guidance in Mercury GUI
      case "about":
      case "docs":
      case "privacy":
      case "bug":
      case "chat":
      case "resume":
      case "rewind":
      case "compress":
      case "copy":
      case "settings":
      case "model":
      case "theme":
      case "auth":
      case "editor":
      case "terminal-setup":
      case "init":
      case "memory":
      case "directory":
      case "dir":
      case "restore":
      case "tools":
      case "mcp":
      case "extensions":
      case "commands":
      case "agents":
      case "skills":
      case "hooks":
      case "plan":
      case "shells":
      case "bashes":
      case "ide":
      case "setup-github":
      case "permissions":
      case "policies":
      case "upgrade":
      case "vim": {
        yield infoMsg(
          `**/${cmd}** requires the Gemini CLI terminal.\n\n` +
          `Run in your terminal:\n\`\`\`\ngemini\n\`\`\`\nThen use \`/${cmd}\` inside the CLI.`,
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
   * Write base64-encoded images to temp files for Gemini CLI's @file syntax.
   * Returns temp file paths (caller must cleanup).
   */
  private async imagesToTempFiles(
    images: ImageAttachment[],
  ): Promise<{ paths: string[]; tempFiles: string[] }> {
    const paths: string[] = [];
    const tempFiles: string[] = [];

    const EXT_MAP: Record<string, string> = {
      jpeg: "jpg",
      "svg+xml": "svg",
      tiff: "tif",
    };

    for (const img of images) {
      const rawExt = img.mediaType.split("/")[1] || "png";
      const ext = EXT_MAP[rawExt] ?? rawExt;
      const tempPath = join(tmpdir(), `mercury-gemini-img-${randomUUID()}.${ext}`);
      await writeFile(tempPath, Buffer.from(img.data, "base64"));
      tempFiles.push(tempPath);
      paths.push(tempPath);
    }

    return { paths, tempFiles };
  }

  /** Best-effort removal of temp files. */
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
   * Build the prompt string with @file references for images.
   * Gemini CLI uses `@./path/to/image.png` syntax inline in the prompt.
   */
  private buildPromptWithImages(prompt: string, imagePaths?: string[]): string {
    if (!imagePaths || imagePaths.length === 0) return prompt;

    // Append @file references after the prompt text
    const fileRefs = imagePaths.map((p) => `@${p}`).join(" ");
    const effectivePrompt = prompt || "Please analyze these images.";
    return `${effectivePrompt} ${fileRefs}`;
  }

  async *sendPrompt(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
    _hooks?: AgentSendHooks,
  ): AsyncGenerator<AgentMessage> {
    // Intercept slash commands
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

    session.info.lastActiveAt = Date.now();

    // Prepare image temp files
    let imagePaths: string[] | undefined;
    const tempFiles: string[] = [];
    if (images && images.length > 0) {
      const result = await this.imagesToTempFiles(images);
      imagePaths = result.paths;
      tempFiles.push(...result.tempFiles);
    }

    try {
      // Add image @file references to the prompt
      const effectivePrompt = this.buildPromptWithImages(prompt, imagePaths);

      const result = await this.runGeminiCli(
        session,
        effectivePrompt,
        session.info.frozenSystemPrompt ?? this.sharedSystemPrompt,
      );

      yield {
        role: "assistant",
        content: result.response,
        timestamp: Date.now(),
        metadata: {
          isResult: true,
          ...(result.stats ? { stats: result.stats } : {}),
          ...(result.geminiSessionId
            ? { geminiSessionId: result.geminiSessionId }
            : {}),
        },
      };

      // Track Gemini session ID for future --resume
      if (result.geminiSessionId && !session.geminiSessionId) {
        session.geminiSessionId = result.geminiSessionId;
        session.info.resumeToken = result.geminiSessionId;
      }
    } finally {
      await this.cleanupTempFiles(tempFiles);
    }
  }

  /**
   * Execute Gemini CLI in headless mode.
   * Uses stdin piping (`echo prompt | gemini --output-format json -y`) to avoid
   * shell injection when shell: true is required on Windows for .cmd resolution.
   */
  private async runGeminiCli(
    session: GeminiSession,
    prompt: string,
    systemPrompt?: string,
  ): Promise<{
    response: string;
    stats?: Record<string, unknown>;
    geminiSessionId?: string;
  }> {
    // Pass prompt via stdin (not -p flag) to avoid shell injection when shell: true
    const args = ["--output-format", "json", "-y"];

    // Resume existing Gemini session if available
    if (session.geminiSessionId) {
      args.push("--resume", session.geminiSessionId);
    }

    // Build environment with system prompt file
    const env: Record<string, string> = { ...process.env } as Record<string, string>;
    const sysPromptFile = await this.ensureSystemPromptFile(systemPrompt);
    if (sysPromptFile) {
      env["GEMINI_SYSTEM_MD"] = sysPromptFile;
    }

    return new Promise((resolve, reject) => {
      // On Windows, npm-installed CLIs create .cmd wrappers that require
      // shell: true for spawn to find them. Prompt is passed via stdin
      // (not as CLI arg) to prevent shell injection.
      const isWindows = process.platform === "win32";
      const proc = spawn("gemini", args, {
        stdio: ["pipe", "pipe", "pipe"],
        cwd: session.cwd,
        env,
        shell: isWindows,
      });

      // Write prompt to stdin and close it to signal EOF
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
        if (sysPromptFile) {
          void this.cleanupFile(sysPromptFile);
        }
        if (code === 0 || stdout.length > 0) {
          try {
            const parsed = JSON.parse(stdout);
            resolve({
              response:
                parsed.response ||
                parsed.content ||
                parsed.text ||
                stdout,
              stats: parsed.stats,
              geminiSessionId: parsed.sessionId || parsed.session_id,
            });
          } catch {
            // JSON parse failed — return raw stdout
            resolve({ response: stdout || `(Gemini CLI exited with code ${code})` });
          }
        } else {
          reject(
            new Error(
              `Gemini CLI exited with code ${code}: ${stderr || "(no stderr)"}`,
            ),
          );
        }
      });

      proc.on("error", (err) => {
        if (sysPromptFile) {
          void this.cleanupFile(sysPromptFile);
        }
        reject(
          new Error(
            `Failed to run 'gemini': ${err.message}. Is Gemini CLI installed? (npm i -g @google/gemini-cli)`,
          ),
        );
      });
    });
  }

  async resumeSession(
    sessionId: string,
    persistedInfo?: SessionInfo,
    cwd?: string,
  ): Promise<SessionInfo> {
    let session = this.sessions.get(sessionId);
    if (!session) {
      if (!persistedInfo?.resumeToken) {
        throw new Error(`Session ${sessionId} not found`);
      }
      const restored: GeminiSession = {
        info: {
          ...persistedInfo,
          sessionId,
          agentId: this.agentId,
          cwd: persistedInfo.cwd ?? cwd ?? process.cwd(),
        },
        cwd: persistedInfo.cwd ?? cwd ?? process.cwd(),
        geminiSessionId: persistedInfo.resumeToken,
      };
      this.sessions.set(sessionId, restored);
      session = restored;
    }
    session.info.status = "active";
    session.info.lastActiveAt = Date.now();
    return session.info;
  }

  async endSession(sessionId: string): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (session) session.info.status = "completed";
  }

  async handoffSession(
    oldSessionId: string,
    _summary: string,
  ): Promise<SessionInfo> {
    const oldSession = this.sessions.get(oldSessionId);
    if (oldSession) oldSession.info.status = "overflow";

    const cwd = oldSession?.cwd ?? process.cwd();
    const newSession = await this.startSession(cwd);

    // Inherit Gemini session ID so --resume continues the conversation
    if (oldSession?.geminiSessionId) {
      const newEntry = this.sessions.get(newSession.sessionId);
      if (newEntry) {
        newEntry.geminiSessionId = oldSession.geminiSessionId;
      }
    }

    newSession.parentSessionId = oldSessionId;
    newSession.role = oldSession?.info.role;
    newSession.frozenRole = oldSession?.info.frozenRole;
    newSession.frozenSystemPrompt = oldSession?.info.frozenSystemPrompt;
    newSession.promptHash = oldSession?.info.promptHash;
    return newSession;
  }

  /**
   * Cleanup: remove system prompt temp file.
   */
  async shutdown(): Promise<void> {
    this.sessions.clear();
  }

  getSlashCommands(): SlashCommand[] {
    // Gemini CLI commands — ref: https://geminicli.com/docs/cli/commands/
    return [
      // ── Session ──
      { name: "/chat", description: "Manage conversations (save/resume/list/delete/share)", category: "session", args: [{ name: "action", description: "save, resume, list, delete, or share", required: false, type: "string" }] },
      { name: "/compress", description: "Summarize chat context to save tokens", category: "session" },
      { name: "/clear", description: "Clear terminal screen", category: "session" },
      { name: "/quit", description: "Exit Gemini CLI", category: "session" },
      // ── Code ──
      { name: "/restore", description: "Restore files to pre-tool-execution state", category: "code" },
      { name: "/copy", description: "Copy last output to clipboard", category: "code" },
      { name: "/paste", description: "Paste base64-encoded images as input", category: "code" },
      { name: "/editor", description: "Select supported editor", category: "code" },
      // ── Tools & Extensions ──
      { name: "/tools", description: "Display available tools", category: "tools" },
      { name: "/extensions", description: "List active extensions", category: "tools" },
      { name: "/mcp", description: "List MCP servers and schemas", category: "tools" },
      // ── Config ──
      { name: "/init", description: "Generate tailored GEMINI.md for current directory", category: "config" },
      { name: "/memory", description: "Manage instructional context (add/show/refresh/list)", category: "config", args: [{ name: "action", description: "add, show, refresh, or list", required: false, type: "string" }] },
      { name: "/directory", description: "Manage workspace directories (add/show)", category: "config", args: [{ name: "action", description: "add or show", required: false, type: "string" }] },
      { name: "/settings", description: "Open settings editor", category: "config" },
      { name: "/theme", description: "Change visual theme", category: "config" },
      { name: "/auth", description: "Change authentication method", category: "config" },
      // ── Info ──
      { name: "/help", description: "Show available commands", category: "info" },
      { name: "/stats", description: "Display session statistics and token usage", category: "info" },
      { name: "/about", description: "Show version info", category: "info" },
      { name: "/bug", description: "File an issue about Gemini CLI", category: "info" },
      { name: "/privacy", description: "Privacy notice and data consent", category: "info" },
      // ── Display ──
      { name: "/vim", description: "Toggle vim mode", category: "display" },
    ];
  }
}
