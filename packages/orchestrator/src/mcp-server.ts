/**
 * Mercury Orchestrator — MCP Server Wrapper
 *
 * Exposes all orchestrator RPC methods as MCP tools using the high-level
 * McpServer API with Zod schemas for type-safe tool registration.
 *
 * Verified against installed @modelcontextprotocol/sdk v1.27.1:
 *   - McpServer: @modelcontextprotocol/sdk/server/mcp.js (high-level API)
 *   - McpServer.server: underlying Server instance for sendLoggingMessage()
 *   - StdioServerTransport: @modelcontextprotocol/sdk/server/stdio.js
 *   - registerTool(name, { description, inputSchema }, callback): RegisteredTool
 *   - inputSchema accepts ZodRawShapeCompat = Record<string, AnySchema>
 *   - callback receives (args: ShapeOutput<Schema>, extra) => CallToolResult
 *
 * Verified via:
 *   - npm: https://www.npmjs.com/package/@modelcontextprotocol/sdk (v1.27.1)
 *   - Docs: https://ts.sdk.modelcontextprotocol.io/
 *   - Local .d.ts: node_modules/@modelcontextprotocol/sdk/dist/esm/server/mcp.d.ts
 *
 * @see https://github.com/modelcontextprotocol/typescript-sdk
 */

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { z } from "zod";
import { randomUUID } from "node:crypto";
import type { IncomingMessage, ServerResponse } from "node:http";
import type { Orchestrator } from "./orchestrator.js";
import type { RpcTransport } from "./rpc-transport.js";
import type { NotificationBroadcaster } from "./notification-broadcaster.js";

// ─── Shared Schema Fragments ───

const agentId = z.string().describe("Agent identifier");
const sessionId = z.string().describe("Session identifier");
const taskId = z.string().describe("Task identifier");

// ─── Tool Registration Helper ───

/**
 * Register an MCP tool that delegates to orchestrator.handleRpc().
 * When inputSchema is omitted, the tool takes no arguments.
 */
function rpcTool(
  server: McpServer,
  orchestrator: Orchestrator,
  name: string,
  description: string,
  inputSchema?: Record<string, z.ZodTypeAny>,
) {
  const cb = async (args: Record<string, unknown>, extra?: { sessionId?: string }) => {
    // Inject MCP session ID for task creation so callback routing knows the originator
    if (name === "create_task" && extra?.sessionId) {
      args = { ...args, _mcpOriginatorSessionId: `mcp-http:${extra.sessionId}` };
    }
    const result = await orchestrator.handleRpc(name, args);
    return {
      content: [{ type: "text" as const, text: JSON.stringify(result, null, 2) }],
    };
  };

  if (inputSchema && Object.keys(inputSchema).length > 0) {
    server.registerTool(name, { description, inputSchema }, cb);
  } else {
    server.registerTool(name, { description }, cb as never);
  }
}

// ─── Shared Tool Registration ───

/** Register all Mercury RPC methods as MCP tools on the given McpServer instance. */
function registerMcpTools(server: McpServer, orchestrator: Orchestrator): void {
  // ─── Agent Management ───

  rpcTool(server, orchestrator, "get_agents",
    "List all configured agents with their capabilities and roles");

  rpcTool(server, orchestrator, "configure_agent",
    "Update an agent's configuration", {
      config: z.record(z.string(), z.unknown()).describe("Full AgentConfig object"),
    });

  rpcTool(server, orchestrator, "list_models",
    "List available models for an agent", { agentId });

  rpcTool(server, orchestrator, "set_model",
    "Set the active model for an agent", {
      agentId,
      model: z.string().describe("Model identifier to activate"),
    });

  rpcTool(server, orchestrator, "set_agent_cwd",
    "Set the working directory for an agent", {
      agentId,
      cwd: z.string().describe("Absolute path to working directory"),
    });

  rpcTool(server, orchestrator, "get_slash_commands",
    "Get slash commands supported by an agent's CLI", { agentId });

  // ─── Session Management ───
  // Signatures verified against orchestrator.ts handleRpc() cases

  rpcTool(server, orchestrator, "start_session",
    "Start a new agent session with optional role", {
      agentId,
      role: z.string().optional().describe("Agent role (main/dev/acceptance/research/design)"),
      taskName: z.string().optional().describe("Task name for session context"),
    });

  rpcTool(server, orchestrator, "send_prompt",
    "Send a prompt to an active session", {
      agentId,
      prompt: z.string().describe("The prompt text"),
      images: z.array(z.record(z.string(), z.unknown())).optional().describe("Image attachments"),
      role: z.string().optional().describe("Agent role context"),
      taskName: z.string().optional().describe("Task name context"),
    });

  rpcTool(server, orchestrator, "stop_session",
    "Stop an active session", {
      agentId,
      sessionId,
    });

  rpcTool(server, orchestrator, "delete_session",
    "Delete a session entirely (stops if active, removes from persistence)", {
      agentId,
      sessionId,
    });

  rpcTool(server, orchestrator, "list_sessions",
    "List all sessions, optionally filtered by agent and role", {
      agentId: agentId.optional(),
      role: z.string().optional().describe("Filter by role"),
      includeTerminal: z.boolean().optional().describe("Include completed/overflow sessions"),
    });

  rpcTool(server, orchestrator, "resume_session",
    "Resume a paused or completed session", {
      agentId,
      sessionId,
      expectedRole: z.string().optional().describe("Expected role for validation"),
    });

  rpcTool(server, orchestrator, "get_session_messages",
    "Get message history for a session (filtered by agent+role when provided)", {
      sessionId,
      offset: z.number().optional().describe("Start offset"),
      limit: z.number().optional().describe("Max messages to return"),
      agentId: agentId.optional().describe("Filter: only return messages if session belongs to this agent"),
      role: z.string().optional().describe("Filter: only return messages if session has this role"),
    });

  rpcTool(server, orchestrator, "summarize_session",
    "Record a summary for a session", {
      agentId,
      summary: z.string().describe("Session summary text"),
    });

  // ─── Task Management ───

  rpcTool(server, orchestrator, "create_task",
    "Create a new TaskBundle (assignedTo is auto-selected via G9 if omitted)", {
      taskId: taskId.optional().describe("Custom task ID (auto-generated if omitted)"),
      title: z.string().describe("Short task title"),
      priority: z.enum(["P0", "P1", "P2", "P3"]).optional().describe("Task priority level"),
      assignedTo: agentId.optional().describe("Agent to assign (auto-selected if omitted)"),
      role: z.enum(["dev", "research", "design"]).optional().describe("Task dispatch role (default: dev)"),
      description: z.string().optional().describe("Detailed task description"),
      context: z.string().describe("Task context for dev agent"),
      codeScope: z.object({
        include: z.array(z.string()).describe("Glob patterns to include"),
        exclude: z.array(z.string()).optional().default([]).describe("Glob patterns to exclude"),
      }).describe("Code scope boundaries (include/exclude globs)"),
      readScope: z.object({
        requiredDocs: z.array(z.string()).optional().default([]).describe("Docs the agent must read"),
        optionalDocs: z.array(z.string()).optional().default([]).describe("Docs the agent may read"),
      }).describe("Required/optional docs to read"),
      allowedWriteScope: z.object({
        codePaths: z.array(z.string()).optional().default([]).describe("Allowed code file paths"),
        kbPaths: z.array(z.string()).optional().default([]).describe("Allowed KB file paths"),
      }).describe("Allowed write paths for code and KB"),
      definitionOfDone: z.array(z.string()).optional().describe("DoD checklist items"),
      branch: z.string().optional().describe("Git branch name"),
      modelRecommendation: z.object({
        complexity: z.enum(["low", "medium", "high"]).describe("Task complexity"),
        requiredCapabilities: z.array(z.string()).optional().describe("Required agent capabilities"),
        preferredModel: z.string().optional().describe("Preferred model hint (e.g. claude-opus-4-6)"),
        reason: z.string().optional().describe("Reason for recommendation"),
      }).optional().describe("G9: model recommendation for auto-routing"),
    });

  rpcTool(server, orchestrator, "get_main_agent_token_usage",
    "Get current token usage for the Main Agent session (usage, limit, remaining, ratio). Returns undefined fields when no active Main session exists.", {});

  rpcTool(server, orchestrator, "get_task",
    "Get a task by ID", { taskId });

  rpcTool(server, orchestrator, "get_task_result",
    "Get task result with acceptance verdict and findings (for polling task completion)", { taskId });

  rpcTool(server, orchestrator, "list_tasks",
    "List all tasks, optionally filtered by status", {
      status: z.string().optional().describe("Filter by task status"),
    });

  rpcTool(server, orchestrator, "dispatch_task",
    "Dispatch a drafted task to its assigned dev agent", { taskId });

  rpcTool(server, orchestrator, "execute_task",
    "Execute an existing task (dispatch by taskId)", {
      taskId,
      oneShot: z.boolean().optional().describe("If true, create+dispatch in one step"),
    });

  // ─── Review Flow ───

  rpcTool(server, orchestrator, "record_receipt",
    "Record an implementation receipt from dev agent", {
      taskId,
      receipt: z.record(z.string(), z.unknown()).describe("Implementation receipt object"),
    });

  rpcTool(server, orchestrator, "main_review_result",
    "Record the main agent's review decision", {
      taskId,
      decision: z.string().describe("APPROVE_FOR_ACCEPTANCE | SEND_BACK"),
      reason: z.string().optional().describe("Review reason"),
      acceptorId: agentId.optional().describe("Agent for acceptance (if approved)"),
    });

  rpcTool(server, orchestrator, "build_reference_prompt",
    "Build a reference prompt for a task's required docs", {
      taskId,
      taskFilePath: z.string().describe("Path to task JSON file"),
      handoffFilePath: z.string().optional().describe("Path to handoff file"),
    });

  // ─── Acceptance ───

  rpcTool(server, orchestrator, "create_acceptance",
    "Create an acceptance review for a task", {
      taskId,
      acceptorId: agentId.describe("Agent to run acceptance review"),
    });

  rpcTool(server, orchestrator, "record_acceptance_result",
    "Record acceptance review results", {
      acceptanceId: z.string().describe("Acceptance bundle ID"),
      results: z.object({
        verdict: z.string().describe("pass | fail | partial"),
        findings: z.array(z.string()),
        recommendations: z.array(z.string()),
      }).describe("Acceptance results object"),
    });

  // ─── Critic Verification ───

  rpcTool(server, orchestrator, "get_critic_prompt",
    "Generate the critic verification prompt for a task (spec-driven DoD validation)", {
      taskId,
    });

  rpcTool(server, orchestrator, "record_critic_result",
    "Record the critic agent's verification result on a task", {
      taskId,
      result: z.object({
        overallVerdict: z.enum(["pass", "partial", "fail"]).describe("Overall critic verdict"),
        completeness: z.number().describe("0.0 – 1.0"),
        items: z.array(z.object({
          dodItem: z.string(),
          verdict: z.enum(["pass", "fail", "partial", "skip"]).describe("Per-item verdict"),
          evidence: z.string(),
          detail: z.string(),
        })),
        blockers: z.array(z.string()),
        suggestions: z.array(z.string()),
      }).describe("Critic verification result"),
      criticAgentId: z.string().optional().describe("Agent ID of the critic that performed verification"),
    });

  // ─── Issue Management ───

  rpcTool(server, orchestrator, "create_issue",
    "Create a new issue (params passed to CreateIssueParams)", {
      title: z.string().describe("Issue title"),
      type: z.string().describe("bug | enhancement | task"),
      priority: z.enum(["P0", "P1", "P2", "P3"]).describe("Issue priority level"),
      description: z.string().describe("Issue description"),
      source: z.string().optional().describe("Source context"),
      linkedTaskIds: z.array(z.string()).optional().describe("Related task IDs"),
    });

  rpcTool(server, orchestrator, "resolve_issue",
    "Resolve an open issue", {
      issueId: z.string().describe("Issue ID to resolve"),
      resolution: z.object({
        resolvedBy: z.string().describe("Agent/user who resolved"),
        summary: z.string().describe("Resolution summary"),
        resolvedAt: z.number().describe("Timestamp (epoch ms)"),
      }).describe("Resolution details"),
    });

  // ─── Configuration ───

  rpcTool(server, orchestrator, "get_config",
    "Get current Mercury configuration");

  rpcTool(server, orchestrator, "update_config",
    "Replace the full Mercury configuration (not a partial patch)", {
      config: z.record(z.string(), z.unknown()).describe("Complete MercuryConfig object"),
    });

  // ─── Knowledge Base ───

  rpcTool(server, orchestrator, "kb_read",
    "Read a file from the knowledge base", {
      file: z.string().describe("File path within the vault"),
    });

  rpcTool(server, orchestrator, "kb_search",
    "Search the knowledge base", {
      query: z.string().describe("Search query text"),
    });

  rpcTool(server, orchestrator, "kb_list",
    "List files in a knowledge base directory", {
      folder: z.string().optional().describe("Directory path (root if omitted)"),
    });

  rpcTool(server, orchestrator, "kb_write",
    "Write content to a knowledge base file", {
      name: z.string().describe("File name/path within the vault"),
      content: z.string().describe("File content"),
    });

  rpcTool(server, orchestrator, "kb_append",
    "Append content to a knowledge base file", {
      file: z.string().describe("File path within the vault"),
      content: z.string().describe("Content to append"),
    });

  // ─── Approval Control ───

  rpcTool(server, orchestrator, "get_approval_mode",
    "Get the current approval mode for an agent", {
      agentId: agentId.optional(),
    });

  rpcTool(server, orchestrator, "set_approval_mode",
    "Set approval mode for an agent", {
      agentId,
      mode: z.string().describe("Approval mode to set"),
    });

  rpcTool(server, orchestrator, "list_approval_requests",
    "List pending approval requests", {
      status: z.string().optional().describe("Filter by status (pending/approved/denied)"),
    });

  rpcTool(server, orchestrator, "approve_request",
    "Approve a pending request", {
      requestId: z.string().describe("Approval request ID"),
      reason: z.string().optional().describe("Approval reason"),
    });

  rpcTool(server, orchestrator, "deny_request",
    "Deny a pending request", {
      requestId: z.string().describe("Approval request ID"),
      reason: z.string().optional().describe("Denial reason"),
    });

  // ─── Context Management ───

  rpcTool(server, orchestrator, "refresh_context",
    "Refresh orchestrator context from filesystem/KB");

  rpcTool(server, orchestrator, "get_context_status",
    "Get current context status including loaded configs and KB state", {
      verbose: z.boolean().optional().describe("Include detailed breakdown"),
    });

  // ─── Utility ───

  rpcTool(server, orchestrator, "ping",
    "Health check — returns pong with timestamp");
}

// ─── Factory: stdio MCP Server (--mcp flag) ───

export function createMcpServer(orchestrator: Orchestrator, transport: RpcTransport) {
  const server = new McpServer(
    { name: "mercury-orchestrator", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  registerMcpTools(server, orchestrator);

  async function start(): Promise<void> {
    const stdioTransport = new StdioServerTransport();
    transport.log("Starting MCP server on stdio...");

    await server.connect(stdioTransport);
    transport.log("MCP server connected and ready");

    // In MCP mode, stdout is owned by the MCP protocol.
    // Redirect orchestrator event notifications to MCP logging messages.
    transport.sendNotification = (method: string, params: Record<string, unknown>) => {
      const level = method === "log" ? "info" as const : "debug" as const;
      server.server.sendLoggingMessage({
        level,
        logger: "mercury",
        data: { method, ...params },
      }).catch(() => {
        // Non-fatal: logging notification delivery is best-effort
      });
    };

    // Signal ready
    await server.server.sendLoggingMessage({
      level: "info",
      logger: "mercury",
      data: { method: "ready", timestamp: Date.now() },
    });
  }

  return { start, server };
}

// ─── Factory: HTTP MCP Sessions (shared HTTP server) ───
// Uses StreamableHTTPServerTransport from @modelcontextprotocol/sdk (^1.27.1, verified npm 2026-03-28)

export interface McpHttpSession {
  server: McpServer;
  transport: StreamableHTTPServerTransport;
}

/**
 * Manages per-session MCP servers over Streamable HTTP.
 * Each MCP client gets its own McpServer + StreamableHTTPServerTransport pair.
 */
export class McpHttpSessionManager {
  private sessions = new Map<string, McpHttpSession>();
  private sessionCreatedAt = new Map<string, number>();
  private log: (msg: string) => void;
  private maxSessions: number;
  private closing = false;
  /** Sessions older than this (by creation time) are eligible for eviction (30 minutes). */
  private static readonly STALE_SESSION_MS = 30 * 60 * 1000;

  constructor(
    private orchestrator: Orchestrator,
    private broadcaster: NotificationBroadcaster | null,
    logger: (msg: string) => void,
    maxSessions = 10,
  ) {
    this.log = logger;
    this.maxSessions = maxSessions;
  }

  private cleanupSession(sid: string): void {
    if (this.closing) return;
    this.sessions.delete(sid);
    this.sessionCreatedAt.delete(sid);
    if (this.broadcaster) {
      this.broadcaster.removeChannel(`mcp-http:${sid}`);
    }
    this.log(`MCP HTTP session closed: ${sid}`);
  }

  /**
   * Evict MCP HTTP sessions older than STALE_SESSION_MS (by creation time).
   * Called before rejecting new connections with 503 to reclaim ghost sessions
   * whose HTTP connections were broken without a clean close event.
   *
   * Note: uses creation time, not last-activity time, because MCP HTTP
   * transports don't track per-request timestamps. This is safe given
   * maxSessions=10 and typical GUI usage of 1-2 concurrent sessions.
   */
  private evictStaleSessions(): void {
    const now = Date.now();
    // Collect stale IDs first to avoid mutating the Map during iteration
    const toEvict: string[] = [];
    for (const [sid] of this.sessions) {
      if (this.sessions.size - toEvict.length < this.maxSessions) break;
      const createdAt = this.sessionCreatedAt.get(sid) ?? 0;
      if (now - createdAt > McpHttpSessionManager.STALE_SESSION_MS) {
        toEvict.push(sid);
      }
    }
    for (const sid of toEvict) {
      const session = this.sessions.get(sid);
      const createdAt = this.sessionCreatedAt.get(sid) ?? 0;
      if (session) {
        session.transport.close().catch(() => {});
      }
      this.cleanupSession(sid);
      this.log(`Evicted stale MCP HTTP session: ${sid} (age: ${Math.round((now - createdAt) / 60000)}min)`);
    }
  }

  get sessionCount(): number {
    return this.sessions.size;
  }

  /**
   * Handle an incoming HTTP request on the /mcp route.
   * POST without session: creates new session (MCP initialize).
   * POST/GET/DELETE with session: routes to existing session transport.
   */
  async handleRequest(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const sessionId = req.headers["mcp-session-id"] as string | undefined;

    // Existing session: route to its transport
    if (sessionId && this.sessions.has(sessionId)) {
      const session = this.sessions.get(sessionId)!;
      await session.transport.handleRequest(req, res);
      return;
    }

    // New session: only via POST without session header (MCP initialize)
    if (req.method === "POST" && !sessionId) {
      if (this.sessions.size >= this.maxSessions) {
        // Lazy eviction: only on demand, not via background timer. Acceptable
        // because GUI typically uses 1-2 concurrent sessions and rarely hits
        // maxSessions=10; avoids background overhead for the common case.
        this.evictStaleSessions();
        if (this.sessions.size >= this.maxSessions) {
          res.statusCode = 503;
          res.end(JSON.stringify({ error: "Too many MCP sessions" }));
          return;
        }
      }
      await this.createSession(req, res);
      return;
    }

    // POST with invalid/expired session ID → 404
    if (sessionId) {
      res.statusCode = 404;
      res.end(JSON.stringify({ error: "Session not found" }));
      return;
    }

    // GET/DELETE without session header
    res.statusCode = 400;
    res.end(JSON.stringify({ error: "Missing MCP-Session-Id header" }));
  }

  private async createSession(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const server = new McpServer(
      { name: "mercury-orchestrator", version: "0.1.0" },
      { capabilities: { tools: {} } },
    );

    registerMcpTools(server, this.orchestrator);

    // sessionId is generated inside handleRequest() during MCP initialize,
    // so we register the session in onsessioninitialized — the SDK hook
    // designed for multi-session tracking (verified: MCP TS SDK docs).
    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: () => randomUUID(),
      onsessioninitialized: (sid) => {
        // Double-check limit (guard against TOCTOU race)
        if (this.sessions.size >= this.maxSessions) {
          this.log(`MCP HTTP session rejected (over limit): ${sid}`);
          transport.close();
          return;
        }
        this.log(`MCP HTTP session initialized: ${sid}`);
        this.sessions.set(sid, { server, transport });
        this.sessionCreatedAt.set(sid, Date.now());

        // Register as broadcaster channel for event fan-out
        if (this.broadcaster) {
          this.broadcaster.addChannel({
            name: `mcp-http:${sid}`,
            send: (method: string, params: Record<string, unknown>) => {
              server.server.sendLoggingMessage({
                level: method === "log" ? "info" : "debug",
                logger: "mercury",
                data: { method, ...params },
              }).catch(() => {});
            },
            close: () => {},
          });
        }
      },
    });

    // Wire session lifecycle cleanup (guarded against closeAll race)
    transport.onclose = () => {
      const sid = transport.sessionId;
      if (sid) {
        this.cleanupSession(sid);
      }
    };

    await server.connect(transport);

    // Handle the initial request — this triggers sessionId generation
    await transport.handleRequest(req, res);
  }

  async closeAll(): Promise<void> {
    this.closing = true;
    for (const [sid, session] of this.sessions) {
      try {
        await session.transport.close();
      } catch {
        // best-effort
      }
      if (this.broadcaster) {
        this.broadcaster.removeChannel(`mcp-http:${sid}`);
      }
    }
    this.sessions.clear();
    this.sessionCreatedAt.clear();
    this.closing = false;
  }
}
