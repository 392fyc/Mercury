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
import { z } from "zod";
import type { Orchestrator } from "./orchestrator.js";
import type { RpcTransport } from "./rpc-transport.js";

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
  const cb = async (args: Record<string, unknown>) => {
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

// ─── Factory ───

export function createMcpServer(orchestrator: Orchestrator, transport: RpcTransport) {
  const server = new McpServer(
    { name: "mercury-orchestrator", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

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

  rpcTool(server, orchestrator, "start_session",
    "Start a new agent session", {
      agentId,
      prompt: z.string().optional().describe("Initial prompt"),
      cwd: z.string().optional().describe("Working directory override"),
    });

  rpcTool(server, orchestrator, "send_prompt",
    "Send a prompt to an active session", {
      agentId,
      prompt: z.string().describe("The prompt text"),
      sessionId: sessionId.optional().describe("Target session (latest if omitted)"),
    });

  rpcTool(server, orchestrator, "stop_session",
    "Stop an active session", {
      agentId,
      sessionId: sessionId.optional().describe("Session to stop (latest if omitted)"),
    });

  rpcTool(server, orchestrator, "list_sessions",
    "List all sessions, optionally filtered by agent", {
      agentId: agentId.optional(),
    });

  rpcTool(server, orchestrator, "resume_session",
    "Resume a paused or completed session", {
      agentId,
      sessionId,
      prompt: z.string().optional().describe("Resume prompt"),
    });

  rpcTool(server, orchestrator, "get_session_messages",
    "Get message history for a session", { agentId, sessionId });

  rpcTool(server, orchestrator, "summarize_session",
    "Generate a summary of a session's conversation", { agentId, sessionId });

  // ─── Task Management ───

  rpcTool(server, orchestrator, "create_task",
    "Create a new TaskBundle", {
      taskId: taskId.optional().describe("Custom task ID (auto-generated if omitted)"),
      title: z.string().describe("Short task title"),
      priority: z.string().optional().describe("sev-0 | sev-1 | sev-2 | sev-3"),
      assignedTo: agentId.describe("Agent to assign the task to"),
      description: z.string().optional().describe("Detailed task description"),
      context: z.string().describe("Task context for dev agent"),
      codeScope: z.record(z.string(), z.unknown()).optional().describe("Code scope boundaries"),
      readScope: z.record(z.string(), z.unknown()).describe("Required/optional docs to read"),
      allowedWriteScope: z.record(z.string(), z.unknown()).describe("Allowed write paths"),
      definitionOfDone: z.array(z.string()).optional().describe("DoD checklist items"),
      branch: z.string().optional().describe("Git branch name"),
    });

  rpcTool(server, orchestrator, "get_task",
    "Get a task by ID", { taskId });

  rpcTool(server, orchestrator, "list_tasks",
    "List all tasks, optionally filtered by status", {
      status: z.string().optional().describe("Filter by task status"),
    });

  rpcTool(server, orchestrator, "dispatch_task",
    "Dispatch a drafted task to its assigned dev agent", { taskId });

  rpcTool(server, orchestrator, "execute_task",
    "Execute a task directly (create + dispatch in one step)", {
      taskId: taskId.optional(),
      title: z.string().describe("Task title"),
      assignedTo: agentId.describe("Agent to execute"),
      context: z.string().describe("Task context"),
      definitionOfDone: z.array(z.string()).optional(),
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
      summary: z.string().optional().describe("Review summary"),
      findings: z.array(z.record(z.string(), z.unknown())).optional(),
    });

  rpcTool(server, orchestrator, "build_reference_prompt",
    "Build a reference prompt for a task's required docs", {
      taskId,
      maxTokens: z.number().optional().describe("Max tokens for reference content"),
    });

  // ─── Acceptance ───

  rpcTool(server, orchestrator, "create_acceptance",
    "Create an acceptance review for a task", {
      taskId,
      acceptor: agentId.optional().describe("Agent to run acceptance"),
    });

  rpcTool(server, orchestrator, "record_acceptance_result",
    "Record acceptance review results", {
      acceptanceId: z.string().describe("Acceptance bundle ID"),
      verdict: z.string().describe("pass | fail | partial"),
      findings: z.array(z.string()).optional(),
      recommendations: z.array(z.string()).optional(),
    });

  // ─── Issue Management ───

  rpcTool(server, orchestrator, "create_issue",
    "Create a new issue in the knowledge base", {
      title: z.string().describe("Issue title"),
      description: z.string().describe("Issue description"),
      severity: z.string().optional().describe("sev-0 | sev-1 | sev-2 | sev-3"),
      source: z.string().optional().describe("Source context"),
      reportedBy: z.string().optional().describe("Reporter agent ID"),
    });

  rpcTool(server, orchestrator, "resolve_issue",
    "Resolve an open issue", {
      issueId: z.string().describe("Issue ID to resolve"),
      resolution: z.string().describe("Resolution description"),
      linkedTaskId: taskId.optional().describe("Task that fixed this issue"),
    });

  // ─── Configuration ───

  rpcTool(server, orchestrator, "get_config",
    "Get current Mercury configuration");

  rpcTool(server, orchestrator, "update_config",
    "Update Mercury configuration", {
      config: z.record(z.string(), z.unknown()).describe("Configuration fields to update"),
    });

  // ─── Knowledge Base ───

  rpcTool(server, orchestrator, "kb_read",
    "Read a file from the knowledge base", {
      path: z.string().describe("File path within the vault"),
    });

  rpcTool(server, orchestrator, "kb_search",
    "Search the knowledge base", {
      query: z.string().describe("Search query text"),
    });

  rpcTool(server, orchestrator, "kb_list",
    "List files in a knowledge base directory", {
      path: z.string().optional().describe("Directory path (root if omitted)"),
    });

  rpcTool(server, orchestrator, "kb_write",
    "Write content to a knowledge base file", {
      name: z.string().describe("File name/path within the vault"),
      content: z.string().describe("File content"),
    });

  rpcTool(server, orchestrator, "kb_append",
    "Append content to a knowledge base file", {
      path: z.string().describe("File path within the vault"),
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
      agentId: agentId.optional(),
      sessionId: sessionId.optional(),
    });

  rpcTool(server, orchestrator, "approve_request",
    "Approve a pending request", {
      requestId: z.string().describe("Approval request ID"),
      agentId,
    });

  rpcTool(server, orchestrator, "deny_request",
    "Deny a pending request", {
      requestId: z.string().describe("Approval request ID"),
      agentId,
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

  // ─── Start Function ───

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
