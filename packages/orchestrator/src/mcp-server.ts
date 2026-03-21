/**
 * Mercury Orchestrator — MCP Server Wrapper
 *
 * Exposes all orchestrator RPC methods as MCP tools, allowing any MCP client
 * (Claude Code, Cursor, etc.) to interact with Mercury via the standard
 * Model Context Protocol.
 *
 * Verified against @modelcontextprotocol/sdk v1.27.1:
 *   - Server: @modelcontextprotocol/sdk/server/index.js
 *   - StdioServerTransport: @modelcontextprotocol/sdk/server/stdio.js
 *   - Schemas: @modelcontextprotocol/sdk/types.js
 *
 * @see https://github.com/modelcontextprotocol/typescript-sdk
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  ListToolsRequestSchema,
  CallToolRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";
import type { Orchestrator } from "./orchestrator.js";
import type { RpcTransport } from "./rpc-transport.js";

// ─── Tool Definitions ───

interface McpToolDef {
  name: string;
  description: string;
  inputSchema: {
    type: "object";
    properties: Record<string, unknown>;
    required?: string[];
  };
}

/**
 * All orchestrator RPC methods mapped to MCP tool definitions.
 * Grouped by domain: agent, session, task, review, acceptance, issue,
 * config, KB, approval, context, and utility.
 */
const TOOL_DEFINITIONS: McpToolDef[] = [
  // ─── Agent Management ───
  {
    name: "get_agents",
    description: "List all configured agents with their capabilities and roles",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "configure_agent",
    description: "Update an agent's configuration",
    inputSchema: {
      type: "object",
      properties: {
        config: {
          type: "object",
          description: "Full AgentConfig object to apply",
        },
      },
      required: ["config"],
    },
  },
  {
    name: "list_models",
    description: "List available models for an agent",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
      },
      required: ["agentId"],
    },
  },
  {
    name: "set_model",
    description: "Set the active model for an agent",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
        model: { type: "string", description: "Model identifier to activate" },
      },
      required: ["agentId", "model"],
    },
  },
  {
    name: "set_agent_cwd",
    description: "Set the working directory for an agent",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
        cwd: { type: "string", description: "Absolute path to working directory" },
      },
      required: ["agentId", "cwd"],
    },
  },
  {
    name: "get_slash_commands",
    description: "Get slash commands supported by an agent's CLI",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
      },
      required: ["agentId"],
    },
  },

  // ─── Session Management ───
  {
    name: "start_session",
    description: "Start a new agent session with optional role and task binding",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
        role: { type: "string", description: "Role to assign (main, dev, acceptance, research, design)" },
        taskName: { type: "string", description: "Optional task name for session naming" },
      },
      required: ["agentId"],
    },
  },
  {
    name: "send_prompt",
    description: "Send a prompt to an agent session and receive the response",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
        prompt: { type: "string", description: "The prompt text to send" },
        images: {
          type: "array",
          description: "Optional image attachments (base64-encoded)",
          items: { type: "object" },
        },
        role: { type: "string", description: "Role context for the prompt" },
        taskName: { type: "string", description: "Task name context" },
      },
      required: ["agentId", "prompt"],
    },
  },
  {
    name: "stop_session",
    description: "End an active agent session",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
        sessionId: { type: "string", description: "Session to stop" },
      },
      required: ["agentId", "sessionId"],
    },
  },
  {
    name: "list_sessions",
    description: "List active sessions, optionally filtered by agent or role",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Filter by agent identifier" },
        role: { type: "string", description: "Filter by role" },
        includeTerminal: { type: "boolean", description: "Include completed/overflow sessions" },
      },
    },
  },
  {
    name: "resume_session",
    description: "Resume a previously persisted session",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
        sessionId: { type: "string", description: "Session to resume" },
        expectedRole: { type: "string", description: "Expected role for validation" },
      },
      required: ["agentId", "sessionId"],
    },
  },
  {
    name: "get_session_messages",
    description: "Retrieve message history for a session",
    inputSchema: {
      type: "object",
      properties: {
        sessionId: { type: "string", description: "Session identifier" },
        offset: { type: "number", description: "Start index for pagination" },
        limit: { type: "number", description: "Maximum messages to return" },
      },
      required: ["sessionId"],
    },
  },
  {
    name: "summarize_session",
    description: "Record a session summary for context handoff",
    inputSchema: {
      type: "object",
      properties: {
        agentId: { type: "string", description: "Agent identifier" },
        summary: { type: "string", description: "Summary text" },
      },
      required: ["agentId", "summary"],
    },
  },

  // ─── Task Orchestration ───
  {
    name: "create_task",
    description: "Create a new task bundle in the SoT system",
    inputSchema: {
      type: "object",
      properties: {
        title: { type: "string", description: "Task title" },
        context: { type: "string", description: "Task description/context" },
        assignedTo: { type: "string", description: "Agent to assign the task to" },
        priority: { type: "string", description: "Priority level: sev-0, sev-1, sev-2, sev-3" },
        definitionOfDone: {
          type: "array",
          items: { type: "string" },
          description: "List of completion criteria",
        },
        codeScope: { type: "object", description: "Code scope include/exclude paths" },
        readScope: { type: "object", description: "Read scope docs" },
        allowedWriteScope: { type: "object", description: "Allowed write paths" },
        reviewConfig: { type: "object", description: "Review configuration" },
      },
      required: ["title", "context", "assignedTo"],
    },
  },
  {
    name: "get_task",
    description: "Retrieve a task bundle by ID",
    inputSchema: {
      type: "object",
      properties: {
        taskId: { type: "string", description: "Task identifier" },
      },
      required: ["taskId"],
    },
  },
  {
    name: "list_tasks",
    description: "List tasks with optional status and assignee filters",
    inputSchema: {
      type: "object",
      properties: {
        status: { type: "string", description: "Filter by task status" },
        assignedTo: { type: "string", description: "Filter by assigned agent" },
      },
    },
  },
  {
    name: "dispatch_task",
    description: "Dispatch a task to an agent (by taskId for bundle flow, or by agent+prompt for ad-hoc)",
    inputSchema: {
      type: "object",
      properties: {
        taskId: { type: "string", description: "Task ID for bundle-aware dispatch" },
        fromAgentId: { type: "string", description: "Source agent (ad-hoc mode)" },
        toAgentId: { type: "string", description: "Target agent (ad-hoc mode)" },
        prompt: { type: "string", description: "Prompt for ad-hoc dispatch" },
      },
    },
  },
  {
    name: "execute_task",
    description: "Execute a task by starting a session and running the task prompt",
    inputSchema: {
      type: "object",
      properties: {
        taskId: { type: "string", description: "Task identifier" },
        oneShot: { type: "boolean", description: "Use one-shot execution mode" },
      },
      required: ["taskId"],
    },
  },
  {
    name: "record_receipt",
    description: "Record an implementation receipt for a task and trigger main review",
    inputSchema: {
      type: "object",
      properties: {
        taskId: { type: "string", description: "Task identifier" },
        receipt: { type: "object", description: "ImplementationReceipt object" },
      },
      required: ["taskId", "receipt"],
    },
  },

  // ─── Review ───
  {
    name: "main_review_result",
    description: "Record the main agent's review decision for a task",
    inputSchema: {
      type: "object",
      properties: {
        taskId: { type: "string", description: "Task identifier" },
        decision: { type: "string", description: "APPROVE_FOR_ACCEPTANCE or SEND_BACK" },
        reason: { type: "string", description: "Reason for the decision" },
        acceptorId: { type: "string", description: "Agent ID for acceptance testing" },
      },
      required: ["taskId", "decision"],
    },
  },
  {
    name: "build_reference_prompt",
    description: "Build a reference prompt for a task (for dev agent context)",
    inputSchema: {
      type: "object",
      properties: {
        taskId: { type: "string", description: "Task identifier" },
        taskFilePath: { type: "string", description: "Path to task file" },
        handoffFilePath: { type: "string", description: "Optional path to handoff file" },
      },
      required: ["taskId", "taskFilePath"],
    },
  },

  // ─── Acceptance ───
  {
    name: "create_acceptance",
    description: "Create an acceptance testing flow for a completed task",
    inputSchema: {
      type: "object",
      properties: {
        taskId: { type: "string", description: "Task identifier" },
        acceptorId: { type: "string", description: "Agent to perform acceptance testing" },
      },
      required: ["taskId", "acceptorId"],
    },
  },
  {
    name: "record_acceptance_result",
    description: "Record the result of acceptance testing",
    inputSchema: {
      type: "object",
      properties: {
        acceptanceId: { type: "string", description: "Acceptance bundle identifier" },
        results: {
          type: "object",
          description: "Results with verdict (pass/partial/fail/blocked), findings, recommendations",
        },
      },
      required: ["acceptanceId", "results"],
    },
  },

  // ─── Issues ───
  {
    name: "create_issue",
    description: "Create a new issue (bug, blocker, scope_creep, question)",
    inputSchema: {
      type: "object",
      properties: {
        title: { type: "string", description: "Issue title" },
        type: { type: "string", description: "Issue type: bug, scope_creep, blocker, question" },
        priority: { type: "string", description: "Priority: sev-0 through sev-3" },
        summary: { type: "string", description: "Brief summary" },
        details: { type: "string", description: "Detailed description" },
        evidence: { type: "array", items: { type: "string" }, description: "Evidence list" },
        reporterType: { type: "string", description: "Reporter role" },
        reporterId: { type: "string", description: "Reporter agent ID" },
        linkedTaskIds: { type: "array", items: { type: "string" }, description: "Linked task IDs" },
      },
      required: ["title", "type", "summary", "reporterId"],
    },
  },
  {
    name: "resolve_issue",
    description: "Resolve an open issue",
    inputSchema: {
      type: "object",
      properties: {
        issueId: { type: "string", description: "Issue identifier" },
        resolution: {
          type: "object",
          description: "Resolution details: resolvedBy, summary, resolvedAt",
        },
      },
      required: ["issueId", "resolution"],
    },
  },

  // ─── Configuration ───
  {
    name: "get_config",
    description: "Get the current Mercury project configuration",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "update_config",
    description: "Update the Mercury project configuration",
    inputSchema: {
      type: "object",
      properties: {
        config: { type: "object", description: "Partial MercuryConfig to merge" },
      },
      required: ["config"],
    },
  },

  // ─── Knowledge Base ───
  {
    name: "kb_read",
    description: "Read a file from the Obsidian knowledge base",
    inputSchema: {
      type: "object",
      properties: {
        file: { type: "string", description: "File path within the vault" },
      },
      required: ["file"],
    },
  },
  {
    name: "kb_search",
    description: "Search the knowledge base by query",
    inputSchema: {
      type: "object",
      properties: {
        query: { type: "string", description: "Search query" },
      },
      required: ["query"],
    },
  },
  {
    name: "kb_list",
    description: "List files in a knowledge base folder",
    inputSchema: {
      type: "object",
      properties: {
        folder: { type: "string", description: "Folder path within the vault" },
      },
    },
  },
  {
    name: "kb_write",
    description: "Write a file to the knowledge base",
    inputSchema: {
      type: "object",
      properties: {
        name: { type: "string", description: "File name/path within the vault" },
        content: { type: "string", description: "File content to write" },
      },
      required: ["name", "content"],
    },
  },
  {
    name: "kb_append",
    description: "Append content to an existing knowledge base file",
    inputSchema: {
      type: "object",
      properties: {
        file: { type: "string", description: "File path within the vault" },
        content: { type: "string", description: "Content to append" },
      },
      required: ["file", "content"],
    },
  },

  // ─── Approval Control Plane ───
  {
    name: "get_approval_mode",
    description: "Get the current approval mode (main_agent_review or auto_accept)",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "set_approval_mode",
    description: "Set the approval mode",
    inputSchema: {
      type: "object",
      properties: {
        mode: { type: "string", description: "Approval mode: main_agent_review or auto_accept" },
      },
      required: ["mode"],
    },
  },
  {
    name: "list_approval_requests",
    description: "List approval requests, optionally filtered by status",
    inputSchema: {
      type: "object",
      properties: {
        status: { type: "string", description: "Filter by status: pending, approved, denied, timed_out, cancelled" },
      },
    },
  },
  {
    name: "approve_request",
    description: "Approve a pending approval request",
    inputSchema: {
      type: "object",
      properties: {
        requestId: { type: "string", description: "Approval request identifier" },
        reason: { type: "string", description: "Optional reason for approval" },
      },
      required: ["requestId"],
    },
  },
  {
    name: "deny_request",
    description: "Deny a pending approval request",
    inputSchema: {
      type: "object",
      properties: {
        requestId: { type: "string", description: "Approval request identifier" },
        reason: { type: "string", description: "Optional reason for denial" },
      },
      required: ["requestId"],
    },
  },

  // ─── Context Management ───
  {
    name: "refresh_context",
    description: "Rebuild and re-inject shared context from KB into all active sessions",
    inputSchema: { type: "object", properties: {} },
  },
  {
    name: "get_context_status",
    description: "Get the current context injection status and configuration",
    inputSchema: { type: "object", properties: {} },
  },

  // ─── Utility ───
  {
    name: "ping",
    description: "Health check — returns pong with timestamp",
    inputSchema: { type: "object", properties: {} },
  },
];

// ─── MCP Server Factory ───

/**
 * Create and configure an MCP Server that wraps all orchestrator RPC methods
 * as MCP tools. The server communicates over stdio using the standard MCP protocol.
 *
 * Streaming events from the orchestrator's EventBus are forwarded as MCP
 * logging notifications so MCP clients can observe real-time activity.
 *
 * @param orchestrator  The Orchestrator instance to wrap
 * @param transport     The RpcTransport used for logging (stderr)
 * @returns An object with start() to begin serving
 */
export function createMcpServer(
  orchestrator: Orchestrator,
  transport: RpcTransport,
): { start: () => Promise<void>; server: Server } {
  const server = new Server(
    { name: "mercury-orchestrator", version: "0.1.0" },
    { capabilities: { tools: {} } },
  );

  // ─── List Tools Handler ───

  server.setRequestHandler(ListToolsRequestSchema, async () => {
    return { tools: TOOL_DEFINITIONS };
  });

  // ─── Call Tool Handler ───

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const toolName = request.params.name;
    const args = (request.params.arguments ?? {}) as Record<string, unknown>;

    try {
      const result = await orchestrator.handleRpc(toolName, args);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify(result, null, 2),
          },
        ],
      };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return {
        content: [
          {
            type: "text" as const,
            text: JSON.stringify({ error: message }),
          },
        ],
        isError: true,
      };
    }
  });

  // ─── Start Function ───

  async function start(): Promise<void> {
    const stdioTransport = new StdioServerTransport();
    transport.log("Starting MCP server on stdio...");

    // Connect the MCP server to stdio
    await server.connect(stdioTransport);

    transport.log("MCP server connected and ready");

    // Forward orchestrator events as MCP logging notifications
    // The orchestrator already emits EventBus events; we intercept the
    // RpcTransport's sendNotification to also emit MCP logging messages.
    const originalSendNotification = transport.sendNotification.bind(transport);
    transport.sendNotification = (method: string, params: Record<string, unknown>) => {
      // Still send via the original transport for non-MCP consumers
      // (In MCP mode, stdout is owned by MCP — skip raw JSON-RPC writes)
      // Instead, forward as MCP logging notification
      if (method === "mercury_event" || method === "agent_message" || method === "agent_streaming") {
        server.sendLoggingMessage({
          level: "info",
          logger: "mercury",
          data: { method, ...params },
        }).catch(() => {
          // Non-fatal: logging notification delivery is best-effort
        });
      } else if (method === "ready") {
        server.sendLoggingMessage({
          level: "info",
          logger: "mercury",
          data: { method, ...params },
        }).catch(() => {
          // best-effort
        });
      } else if (method === "log") {
        server.sendLoggingMessage({
          level: "info",
          logger: "mercury",
          data: params,
        }).catch(() => {
          // best-effort
        });
      } else {
        // For unknown notification types, still forward as logging
        server.sendLoggingMessage({
          level: "debug",
          logger: "mercury",
          data: { method, ...params },
        }).catch(() => {
          // best-effort
        });
      }
    };

    // Send ready notification via MCP logging
    await server.sendLoggingMessage({
      level: "info",
      logger: "mercury",
      data: { method: "ready", timestamp: Date.now() },
    });
  }

  return { start, server };
}
