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

// ─── Project Configuration ───

export interface ObsidianConfig {
  enabled: boolean;
  vaultName: string;
  autoInjectContext: boolean;
  contextFiles: string[]; // files to inject as system prompt context
}

export interface MercuryConfig {
  agents: AgentConfig[];
  workDir?: string;
  obsidian?: ObsidianConfig;
}

// ─── Knowledge Base Types ───

export interface KBSearchResult {
  file: string;
  matches: string[];
  score?: number;
}

export interface KBFileInfo {
  path: string;
  name: string;
  folder: string;
  modified?: number;
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
  | "orchestrator.task.created"
  | "orchestrator.task.status_change"
  | "orchestrator.task.rework"
  | "orchestrator.acceptance.created"
  | "orchestrator.acceptance.completed"
  | "orchestrator.issue.created"
  | "orchestrator.issue.resolved"
  | "orchestrator.session.summarize"
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

// ─── Task Orchestration (SoT Pattern) ───

export type TaskStatus =
  | "drafted"
  | "dispatched"
  | "in_progress"
  | "implementation_done"
  | "acceptance"
  | "verified"
  | "closed"
  | "failed"
  | "blocked";

export interface TaskBundle {
  taskId: string;
  title: string;
  phaseId?: string;
  priority: "sev-0" | "sev-1" | "sev-2" | "sev-3";
  status: TaskStatus;
  assignedTo: string;
  branch?: string;

  // Scope controls
  codeScope: { include: string[]; exclude: string[] };
  readScope: { requiredDocs: string[]; optionalDocs: string[] };
  allowedWriteScope: { codePaths: string[]; kbPaths: string[] };
  docsMustUpdate: string[];
  docsMustNotTouch: string[];

  // Completion criteria
  definitionOfDone: string[];
  requiredEvidence: string[];
  context: string;

  // Acceptance handoff
  handoffToAcceptance?: {
    acceptanceBundleId: string;
    blindInputPolicy: { allowed: string[]; forbidden: string[] };
    acceptanceFocus: string[];
  };

  // Implementation receipt (filled by dev agent)
  implementationReceipt?: ImplementationReceipt;

  // Rework tracking
  reworkCount: number;
  maxReworks: number;
  linkedIssueIds: string[];
}

export interface ImplementationReceipt {
  implementer: string;
  branch: string;
  summary: string;
  changedFiles: string[];
  evidence: string[];
  docsUpdated: string[];
  residualRisks: string[];
  completedAt: number;
}

// ─── Acceptance Bundle ───

export type AcceptanceVerdict = "pass" | "partial" | "fail" | "blocked";

export interface AcceptanceBundle {
  acceptanceId: string;
  linkedTaskId: string;
  status: "pending" | "in_progress" | "completed";
  acceptor: string;
  scope: {
    filesToReview: string[];
    docsToCheck: string[];
    runtimeChecks: string[];
  };
  blindInputPolicy: {
    allowed: string[];
    forbidden: string[];
  };
  results?: {
    verdict: AcceptanceVerdict;
    findings: string[];
    recommendations: string[];
  };
  completedAt?: number;
}

// ─── Issue Bundle ───

export type IssueType = "bug" | "scope_creep" | "blocker" | "question";

export interface IssueBundle {
  issueId: string;
  title: string;
  status: "open" | "resolved" | "deferred";
  type: IssueType;
  priority: "sev-0" | "sev-1" | "sev-2" | "sev-3";
  source: {
    reporterType: AgentRole;
    reporterId: string;
  };
  description: {
    summary: string;
    details: string;
    evidence: string[];
  };
  linkedTaskIds: string[];
  resolution?: {
    resolvedBy: string;
    summary: string;
    resolvedAt: number;
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
