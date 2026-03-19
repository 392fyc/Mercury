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
  AgentApprovalRequest,
  AgentConfig,
  AgentMessage,
  AgentSendHooks,
  ImageAttachment,
  SessionInfo,
  SlashCommand,
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
  private sharedSystemPrompt?: string;
  private activeQueries = new Map<string, AsyncGenerator<unknown, void> & { supportedModels?(): Promise<unknown[]>; setModel?(model?: string): Promise<void> }>();
  private modelsCache: { id: string; name: string }[] | null = null;

  constructor(config?: Partial<AgentConfig>) {
    this.config = {
      id: "claude-code",
      displayName: "Claude Code",
      cli: "claude",
      roles: ["main"],
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
    this.sharedSystemPrompt = prompt;
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
      cwd,
      startedAt: Date.now(),
      lastActiveAt: Date.now(),
      status: "active",
    };
    this.sessions.set(sessionId, info);
    this.sessionCwd.set(sessionId, cwd);
    return info;
  }

  /**
   * Intercept slash commands that can't be sent raw to sdk.query().
   * Returns an AgentMessage generator if handled, or null to pass through.
   */
  private async *handleSlashCommand(
    sessionId: string,
    prompt: string,
  ): AsyncGenerator<AgentMessage> {
    const trimmed = prompt.trim();
    const match = trimmed.match(/^\/(\S+)(?:\s+(.*))?$/);
    if (!match) return; // not a slash command — caller should pass through

    const cmd = match[1].toLowerCase();
    const _args = match[2]?.trim() ?? "";
    const ts = Date.now();

    const infoMsg = (content: string): AgentMessage => ({
      role: "assistant",
      content,
      timestamp: ts,
      metadata: { isSlashCommandResponse: true, command: `/${cmd}` },
    });

    // Strategy: commands Mercury can natively implement are handled here.
    // CLI-only commands are rewritten as guidance messages pointing users
    // to the original CLI. Mercury is a CLI-to-GUI bridge, not a replacement.
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
          for (const c of list) {
            text += `  **${c.name}**  ${c.description}\n`;
          }
          text += "\n";
        }
        yield infoMsg(text);
        return;
      }

      case "clear": {
        // Actually clear the session — mark completed so orchestrator creates new one
        const session = this.sessions.get(sessionId);
        if (session) session.status = "completed";
        yield infoMsg("Session cleared. Send a new message to start a fresh conversation.");
        return;
      }

      case "status": {
        // Actually show real session data we have
        const session = this.sessions.get(sessionId);
        yield infoMsg(
          `## Session Status\n` +
          `- **Agent**: ${this.config.displayName} (${this.agentId})\n` +
          `- **Integration**: SDK mode\n` +
          `- **Session**: ${sessionId}\n` +
          `- **Status**: ${session?.status ?? "unknown"}\n` +
          `- **Started**: ${session ? new Date(session.startedAt).toLocaleString() : "N/A"}\n` +
          `- **SDK Session**: ${session?.parentSessionId ?? "pending"}`,
        );
        return;
      }

      case "exit":
      case "quit": {
        // Actually end the session
        const session = this.sessions.get(sessionId);
        if (session) session.status = "completed";
        yield infoMsg("Session ended. Use the Start button to begin a new session.");
        return;
      }

      // CLI-only commands — rewritten as terminal guidance in Mercury GUI
      case "login":
      case "logout":
      case "doctor":
      case "config":
      case "settings":
      case "terminal-setup":
      case "permissions":
      case "allowed-tools":
      case "sandbox":
      case "vim":
      case "theme":
      case "color":
      case "keybindings":
      case "statusline":
      case "privacy-settings":
      case "upgrade":
      case "install-github-app":
      case "install-slack-app":
      case "remote-control":
      case "rc":
      case "remote-env":
      case "desktop":
      case "app":
      case "mobile":
      case "ios":
      case "android":
      case "stickers":
      case "passes":
      case "skills":
      case "plugin":
      case "reload-plugins":
      case "agents":
      case "tasks":
      case "compact":
      case "model":
      case "effort":
      case "fast":
      case "cost":
      case "usage":
      case "extra-usage":
      case "stats":
      case "context":
      case "diff":
      case "copy":
      case "export":
      case "fork":
      case "rename":
      case "rewind":
      case "checkpoint":
      case "resume":
      case "continue":
      case "review":
      case "security-review":
      case "plan":
      case "add-dir":
      case "init":
      case "memory":
      case "hooks":
      case "mcp":
      case "ide":
      case "chrome":
      case "pr-comments":
      case "btw":
      case "feedback":
      case "bug":
      case "release-notes":
      case "insights": {
        const cliCmd = cmd === "login" || cmd === "logout" ? `auth ${cmd}` : cmd;
        yield infoMsg(
          `**/${cmd}** requires the Claude Code CLI terminal.\n\n` +
          `Run in your terminal:\n\`\`\`\nclaude ${cliCmd}\n\`\`\``,
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
      // Not handled — fall through to SDK query
    }

    const sdk = await this.loadSdk();
    const session = this.sessions.get(sessionId);
    if (!session) throw new Error(`Session ${sessionId} not found`);

    const cwd = this.sessionCwd.get(sessionId) ?? process.cwd();
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

    if (this.config.model) {
      options.model = this.config.model;
    }

    const onApprovalRequest = hooks?.onApprovalRequest;
    if (onApprovalRequest) {
      options.canUseTool = async (
        toolName: string,
        input: Record<string, unknown>,
        requestOptions: {
          blockedPath?: string;
          decisionReason?: string;
          toolUseID: string;
          agentID?: string;
        },
      ) => {
        const summaryParts = [
          toolName,
          requestOptions.decisionReason,
          requestOptions.blockedPath,
        ].filter((part): part is string => Boolean(part && part.trim()));

        const approvalRequest: AgentApprovalRequest = {
          kind: "tool_use",
          toolName,
          summary: summaryParts.join(" — ") || `Approve ${toolName}`,
          rawRequest: {
            input,
            blockedPath: requestOptions.blockedPath,
            decisionReason: requestOptions.decisionReason,
            toolUseID: requestOptions.toolUseID,
            agentID: requestOptions.agentID,
          },
        };

        const decision = await onApprovalRequest(approvalRequest);
        if (decision.action === "approve") {
          return {
            behavior: "allow",
            updatedInput: decision.updatedInput,
            toolUseID: requestOptions.toolUseID,
          };
        }
        return {
          behavior: "deny",
          message: decision.reason ?? `Mercury denied ${toolName}`,
          interrupt: true,
          toolUseID: requestOptions.toolUseID,
        };
      };
    }

    const effectiveSystemPrompt = session.frozenSystemPrompt ?? this.sharedSystemPrompt;
    if (effectiveSystemPrompt) {
      options.systemPrompt = effectiveSystemPrompt;
    }

    if (session.resumeToken) {
      options.resume = session.resumeToken;
    }

    // Build the effective prompt — with or without images.
    // When images are present, use native multimodal via AsyncIterable<SDKUserMessage>
    // which passes content arrays (ImageBlockParam + TextBlockParam) directly to the SDK.
    // Ref: https://platform.claude.com/docs/en/agent-sdk/typescript
    // SDKUserMessage.message is MessageParam from Anthropic SDK — supports content arrays.
    //
    // query() signature: prompt: string | AsyncIterable<SDKUserMessage>
    // We use `unknown` to avoid importing SDK types, then pass to query() which accepts both.
    let queryArgs: { prompt: unknown; options: Record<string, unknown> };

    if (images && images.length > 0) {
      const contentBlocks: unknown[] = [];
      for (const img of images) {
        contentBlocks.push({
          type: "image",
          source: {
            type: "base64",
            media_type: img.mediaType,
            data: img.data,
          },
        });
      }
      contentBlocks.push({
        type: "text",
        text: prompt || "Please analyze these images.",
      });

      // Yield a single SDKUserMessage with the multimodal content array.
      // session_id is filled by the SDK at runtime — we provide a placeholder UUID.
      const placeholderId = randomUUID();
      async function* makeUserMessageStream() {
        yield {
          type: "user" as const,
          session_id: placeholderId,
          message: {
            role: "user" as const,
            content: contentBlocks,
          },
          parent_tool_use_id: null,
        };
      }
      queryArgs = { prompt: makeUserMessageStream() as unknown, options };
    } else {
      queryArgs = { prompt, options };
    }

    let sdkSessionId: string | undefined;
    let hasYieldedAssistant = false;

    // sdk.query() accepts string | AsyncIterable<SDKUserMessage> — both paths converge here
    const queryIter = sdk.query(queryArgs as { prompt: string; options: Record<string, unknown> });
    this.activeQueries.set(sessionId, queryIter as Parameters<typeof this.activeQueries.set>[1]);
    for await (const message of queryIter) {
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
          hasYieldedAssistant = true;
          yield {
            role: "assistant",
            content,
            timestamp: Date.now(),
            metadata: { sdkSessionId },
          };
        }
      }

      // Result messages — only yield if no assistant messages were emitted.
      // SDK streams content via assistant messages (possibly chunked), then emits
      // a result with the full concatenated text. Showing both causes duplication.
      if (msg.type === "result" && !hasYieldedAssistant) {
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

    this.activeQueries.delete(sessionId);

    // Store SDK session ID for future resume after restart.
    if (sdkSessionId) {
      session.resumeToken = sdkSessionId;
    }
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
      session = {
        ...persistedInfo,
        sessionId,
        agentId: this.agentId,
        cwd: persistedInfo.cwd ?? cwd ?? process.cwd(),
      };
      this.sessions.set(sessionId, session);
      this.sessionCwd.set(sessionId, session.cwd ?? process.cwd());
    }
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
      role: oldSession?.role,
      frozenRole: oldSession?.frozenRole,
      frozenSystemPrompt: oldSession?.frozenSystemPrompt,
      promptHash: oldSession?.promptHash,
    };
    this.sessions.set(newSessionId, newSession);

    // The first prompt to the new session will include the summary
    // so the agent has context from the previous session
    return newSession;
  }

  async listModels(): Promise<{ id: string; name: string }[]> {
    if (this.modelsCache) return this.modelsCache;

    const mapModels = (models: { value: string; displayName: string; description?: string }[]) =>
      models
        .filter((m) => m.value)
        .map((m) => {
          // description format: "Opus 4.6 with 1M context [NEW] · Most capable..."
          // extract "Name X.Y" from the part before "·"
          const beforeDot = m.description?.split("·")[0]?.trim() ?? "";
          const versionMatch = beforeDot.match(/^([\w][\w\s]*?\s\d+\.\d+)/);
          const name = versionMatch ? versionMatch[1] : (m.displayName || m.value);
          return { id: m.value, name };
        });

    // If there's an active query, use its supportedModels() method
    const firstActive = this.activeQueries.values().next().value;
    if (firstActive?.supportedModels) {
      try {
        const models = await firstActive.supportedModels();
        this.modelsCache = mapModels(models as { value: string; displayName: string }[]);
        return this.modelsCache;
      } catch {
        // Fall through to SDK query approach
      }
    }

    // No active query — create a lightweight one to retrieve models
    const sdk = await this.loadSdk();
    const q = sdk.query({
      prompt: "Return immediately.",
      options: { maxTurns: 1, permissionMode: "plan" },
    }) as AsyncGenerator<unknown, void> & { supportedModels(): Promise<{ value: string; displayName: string }[]> };

    try {
      const models = await q.supportedModels();
      this.modelsCache = mapModels(models);
    } catch {
      this.modelsCache = [];
    } finally {
      // Drain the query so the session ends cleanly
      try {
        const interrupt = (q as unknown as { interrupt?(): Promise<void> }).interrupt;
        if (interrupt) await interrupt.call(q);
      } catch { /* ignore */ }
    }

    return this.modelsCache;
  }

  setModel(model: string): void {
    this.config.model = model;
    // Clear cache so next listModels reflects new state
    this.modelsCache = null;
    // Switch model on all active queries
    for (const q of this.activeQueries.values()) {
      if (q.setModel) {
        q.setModel(model).catch(() => { /* best-effort */ });
      }
    }
  }

  getSlashCommands(): SlashCommand[] {
    return [
      // ── Help & Info ──
      { name: "/help", description: "Show help and available commands", category: "help" },
      { name: "/doctor", description: "Diagnose and verify installation", category: "help" },
      { name: "/release-notes", description: "View the full changelog", category: "help" },
      { name: "/insights", description: "Generate a report analyzing your sessions", category: "help" },
      // ── Session ──
      { name: "/clear", description: "Clear conversation history and free up context", category: "session" },
      { name: "/compact", description: "Compact conversation with optional focus instructions", category: "session", args: [{ name: "instructions", description: "Optional focus instructions", required: false, type: "string" }] },
      { name: "/resume", description: "Resume a conversation by ID or name", category: "session", args: [{ name: "session", description: "Session ID or name", required: false, type: "string" }] },
      { name: "/fork", description: "Create a fork of the current conversation", category: "session", args: [{ name: "name", description: "Fork name", required: false, type: "string" }] },
      { name: "/rename", description: "Rename the current session", category: "session", args: [{ name: "name", description: "New name", required: false, type: "string" }] },
      { name: "/rewind", description: "Rewind conversation and/or code to a previous point", category: "session" },
      { name: "/export", description: "Export the current conversation as plain text", category: "session", args: [{ name: "filename", description: "Output filename", required: false, type: "string" }] },
      { name: "/exit", description: "Exit the CLI", category: "session" },
      // ── Code & Files ──
      { name: "/diff", description: "Open interactive diff viewer for uncommitted changes", category: "code" },
      { name: "/review", description: "Review code changes (deprecated, use plugin)", category: "code" },
      { name: "/security-review", description: "Analyze pending changes for security vulnerabilities", category: "code" },
      { name: "/plan", description: "Enter plan mode directly from prompt", category: "code" },
      { name: "/add-dir", description: "Add a new working directory", category: "code", args: [{ name: "path", description: "Directory path", required: true, type: "string" }] },
      { name: "/copy", description: "Copy last assistant response to clipboard", category: "code" },
      // ── Config & Preferences ──
      { name: "/init", description: "Initialize project with CLAUDE.md guide", category: "config" },
      { name: "/config", description: "Open the Settings interface", category: "config" },
      { name: "/model", description: "Select or change the AI model", category: "config", args: [{ name: "model", description: "Model name", required: false, type: "string" }] },
      { name: "/permissions", description: "View or update permissions", category: "config" },
      { name: "/memory", description: "Edit CLAUDE.md memory files", category: "config" },
      { name: "/hooks", description: "View hook configurations for tool events", category: "config" },
      { name: "/keybindings", description: "Open or create keybindings configuration", category: "config" },
      { name: "/theme", description: "Change the color theme", category: "config" },
      { name: "/color", description: "Set the prompt bar color", category: "config", args: [{ name: "color", description: "Color name or 'default'", required: false, type: "string" }] },
      { name: "/vim", description: "Toggle between Vim and Normal editing modes", category: "config" },
      { name: "/statusline", description: "Configure the status line display", category: "config" },
      { name: "/terminal-setup", description: "Configure terminal keybindings", category: "config" },
      { name: "/sandbox", description: "Toggle sandbox mode", category: "config" },
      { name: "/effort", description: "Set the model effort level", category: "config", args: [{ name: "level", description: "low|medium|high|max|auto", required: false, type: "string" }] },
      { name: "/fast", description: "Toggle fast mode on or off", category: "config", args: [{ name: "toggle", description: "on|off", required: false, type: "string" }] },
      { name: "/privacy-settings", description: "View and update privacy settings", category: "config" },
      // ── Account ──
      { name: "/login", description: "Sign in to your Anthropic account", category: "account" },
      { name: "/logout", description: "Sign out from your Anthropic account", category: "account" },
      { name: "/status", description: "Show version, model, account, and connectivity", category: "account" },
      { name: "/usage", description: "Show plan usage limits and rate limit status", category: "account" },
      { name: "/cost", description: "Show token usage statistics", category: "account" },
      { name: "/stats", description: "Visualize daily usage, session history, and streaks", category: "account" },
      { name: "/extra-usage", description: "Configure extra usage when rate limits are hit", category: "account" },
      { name: "/upgrade", description: "Open the upgrade page for higher plan tier", category: "account" },
      // ── Integrations ──
      { name: "/mcp", description: "Manage MCP server connections and OAuth", category: "integrations" },
      { name: "/ide", description: "Manage IDE integrations and show status", category: "integrations" },
      { name: "/chrome", description: "Configure Claude in Chrome settings", category: "integrations" },
      { name: "/install-github-app", description: "Set up the Claude GitHub Actions app", category: "integrations" },
      { name: "/install-slack-app", description: "Install the Claude Slack app", category: "integrations" },
      { name: "/pr-comments", description: "Fetch and display comments from a GitHub PR", category: "integrations", args: [{ name: "PR", description: "PR number or URL", required: false, type: "string" }] },
      // ── Plugins & Skills ──
      { name: "/plugin", description: "Manage Claude Code plugins", category: "plugins" },
      { name: "/reload-plugins", description: "Reload all active plugins", category: "plugins" },
      { name: "/skills", description: "List available skills", category: "plugins" },
      // ── Agents & Tasks ──
      { name: "/agents", description: "Manage agent (subagent) configurations", category: "agents" },
      { name: "/tasks", description: "List and manage background tasks", category: "agents" },
      { name: "/btw", description: "Ask a quick side question without adding to history", category: "agents", args: [{ name: "question", description: "Side question", required: true, type: "string" }] },
      // ── Remote & Mobile ──
      { name: "/remote-control", description: "Make session available for remote control from claude.ai", category: "remote" },
      { name: "/remote-env", description: "Configure default remote environment for web sessions", category: "remote" },
      { name: "/desktop", description: "Continue session in Claude Code Desktop app", category: "remote" },
      { name: "/mobile", description: "Show QR code to download the Claude mobile app", category: "remote" },
      // ── Feedback ──
      { name: "/feedback", description: "Submit feedback about Claude Code", category: "feedback", args: [{ name: "report", description: "Feedback text", required: false, type: "string" }] },
      { name: "/context", description: "Visualize current context usage as a colored grid", category: "help" },
      { name: "/stickers", description: "Order Claude Code stickers", category: "feedback" },
      { name: "/passes", description: "Share a free week of Claude Code with friends", category: "feedback" },
    ];
  }
}
