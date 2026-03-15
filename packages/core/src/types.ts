/**
 * Mercury Core Types
 *
 * SoT Role Model:
 * - Main Agent = Orchestrator (user-configurable) — user talks to this only
 * - Sub Agents (Codex, opencode, Gemini) = Workers — receive tasks, return results
 *
 * Mercury wraps existing CLIs in a GUI, no API key management needed.
 */

// ─── Agent Identity ───

export type AgentRole = "main" | "dev" | "acceptance" | "research";

export type IntegrationType = "sdk" | "mcp" | "http" | "pty";

export interface AgentConfig {
  id: string;
  displayName: string;
  cli: string; // e.g. "claude", "codex", "opencode"
  role: AgentRole;
  integration: IntegrationType;
  capabilities: string[];
  restrictions: string[];
  maxConcurrentSessions: number;
}

// ─── Event Bus ───

export type EventType =
  | "agent.session.start"
  | "agent.session.end"
  | "agent.message.send"
  | "agent.message.receive"
  | "agent.tool.use"
  | "agent.tool.result"
  | "agent.error"
  | "orchestrator.task.dispatch"
  | "orchestrator.task.complete"
  | "orchestrator.task.fail"
  | "orchestrator.context.compact"
  | "orchestrator.session.handoff"
  | "human.intervention";

export interface MercuryEvent<T = unknown> {
  id: string;
  type: EventType;
  timestamp: number;
  agentId: string;
  sessionId: string;
  payload: T;
  parentEventId?: string;
}

// ─── Task Bundle (from SoT) ───

export type TaskStatus =
  | "drafted"
  | "dispatched"
  | "in_progress"
  | "implementation_done"
  | "acceptance"
  | "verified"
  | "closed"
  | "failed";

export interface TaskBundle {
  taskId: string;
  title: string;
  priority: "sev-0" | "sev-1" | "sev-2" | "sev-3";
  assignedTo: string; // agent id
  branch?: string;
  input: {
    readScope: string[];
    designRefs: string[];
    context: string;
  };
  constraints: {
    allowedWriteScope: string[];
    docsMustNotTouch: string[];
  };
  definitionOfDone: string[];
  status: TaskStatus;
  receipt?: {
    commits: string[];
    notes: string;
  };
}

// ─── Session Management ───

export interface SessionInfo {
  sessionId: string;
  agentId: string;
  startedAt: number;
  lastActiveAt: number;
  tokenUsage?: number;
  tokenLimit?: number;
  status: "active" | "paused" | "completed" | "overflow";
  parentSessionId?: string; // for session continuity on overflow
}

// ─── Agent Adapter Interface ───

export interface AgentMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  metadata?: Record<string, unknown>;
}

export interface AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;

  startSession(cwd: string): Promise<SessionInfo>;
  sendPrompt(sessionId: string, prompt: string): AsyncGenerator<AgentMessage>;
  resumeSession(sessionId: string): Promise<SessionInfo>;
  endSession(sessionId: string): Promise<void>;

  // Session continuity: when context overflows, create new session inheriting context
  handoffSession(
    oldSessionId: string,
    summary: string,
  ): Promise<SessionInfo>;
}
