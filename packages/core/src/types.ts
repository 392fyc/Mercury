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

export type AgentRole = "main" | "dev" | "acceptance" | "research" | "design";

export type IntegrationType = "sdk" | "mcp" | "http" | "pty";

export interface AgentConfig {
  id: string;
  displayName: string;
  cli: string; // e.g. "claude", "codex", "opencode"
  model?: string; // e.g. "claude-opus-4-6", "o3", "gemini-2.5-pro"
  roles: AgentRole[];
  integration: IntegrationType;
  capabilities: string[];
  restrictions: string[];
  maxConcurrentSessions: number;
}

// ─── Role Slot ───

/** Composite key for role-scoped sessions: "{role}:{agentId}" */
export type RoleSlotKey = `${AgentRole}:${string}`;

export function makeRoleSlotKey(role: AgentRole, agentId: string): RoleSlotKey {
  return `${role}:${agentId}`;
}

export function parseRoleSlotKey(key: RoleSlotKey): { role: AgentRole; agentId: string } {
  const idx = key.indexOf(":");
  return { role: key.slice(0, idx) as AgentRole, agentId: key.slice(idx + 1) };
}

// ─── Role Cards ───

export interface RoleCard {
  role: AgentRole;
  description: string;
  canExecuteCode: boolean;
  canDelegateToRoles: AgentRole[];
  inputBoundary: string[];
  outputBoundary: string[];
}

export const ROLE_CARDS: Record<AgentRole, RoleCard> = {
  main: {
    role: "main",
    description: "Orchestrator: reviews, verifies, delegates. Does NOT execute code directly.",
    canExecuteCode: false,
    canDelegateToRoles: ["dev", "acceptance", "research", "design"],
    inputBoundary: ["user prompts", "acceptance results", "research findings"],
    outputBoundary: ["task bundles", "delegation directives", "final summaries"],
  },
  dev: {
    role: "dev",
    description: "Worker: receives task bundles, writes code, returns implementation receipts.",
    canExecuteCode: true,
    canDelegateToRoles: [],
    inputBoundary: ["task bundles", "rework directives"],
    outputBoundary: ["implementation receipts", "code changes"],
  },
  acceptance: {
    role: "acceptance",
    description: "Reviewer: blind acceptance testing on completed tasks.",
    canExecuteCode: true,
    canDelegateToRoles: [],
    inputBoundary: ["acceptance bundles", "code to review"],
    outputBoundary: ["acceptance verdicts"],
  },
  research: {
    role: "research",
    description: "Analyst: reads docs, answers questions, no code writing.",
    canExecuteCode: false,
    canDelegateToRoles: [],
    inputBoundary: ["research questions", "document references"],
    outputBoundary: ["research findings", "summaries"],
  },
  design: {
    role: "design",
    description: "Designer: generates specs, mockups, design decisions.",
    canExecuteCode: false,
    canDelegateToRoles: [],
    inputBoundary: ["design requirements"],
    outputBoundary: ["design specs", "architecture decisions"],
  },
};

// ─── Project Configuration ───

export interface ObsidianConfig {
  enabled: boolean;
  vaultName: string;
  vaultPath?: string; // filesystem path to vault root (for git sync)
  autoInjectContext: boolean;
  contextFiles: string[]; // files to inject as system prompt context
}

export interface MercuryConfig {
  agents: AgentConfig[];
  workDir?: string;
  obsidian?: ObsidianConfig;
}

// ─── Approval Control Plane ───

export type ApprovalMode = "main_agent_review" | "auto_accept";

export type ApprovalRequestStatus =
  | "pending"
  | "approved"
  | "denied"
  | "timed_out"
  | "cancelled";

export type ApprovalRequestKind =
  | "permission"
  | "tool_use"
  | "command_execution"
  | "file_change"
  | "user_input";

export type ApprovalDecisionSource = "main_agent" | "system";

export interface ApprovalRequest {
  id: string;
  agentId: string;
  sessionId: string;
  role?: AgentRole;
  adapter: string;
  kind: ApprovalRequestKind;
  toolName?: string;
  summary: string;
  rawRequest?: Record<string, unknown>;
  cwd?: string;
  createdAt: number;
  resolvedAt?: number;
  status: ApprovalRequestStatus;
  decisionBy?: ApprovalDecisionSource;
  decisionReason?: string;
}

export interface ApprovalDecision {
  action: "approve" | "deny";
  reason?: string;
  updatedInput?: Record<string, unknown>;
}

export interface AgentApprovalRequest {
  kind: ApprovalRequestKind;
  toolName?: string;
  summary: string;
  rawRequest?: Record<string, unknown>;
}

export interface AgentSendHooks {
  onApprovalRequest?: (request: AgentApprovalRequest) => Promise<ApprovalDecision>;
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
  | "agent.approval.requested"
  | "agent.approval.resolved"
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
  | "orchestrator.task.main_review"
  | "orchestrator.task.callback"
  | "orchestrator.scope.violation"
  | "human.intervention";

export interface MercuryEvent<T = unknown> {
  id: string;
  type: EventType;
  timestamp: number;
  agentId: string;
  modelId?: string;
  sessionId: string;
  payload: T;
  parentEventId?: string;
}

// ─── Task Orchestration (SoT Pattern) ───

/** Agents First: structured agent identity for inter-agent communication */
export interface TaskAssignee {
  agentId: string;
  model?: string;
  sessionId?: string; // populated when task is dispatched
}

export type TaskStatus =
  | "drafted"
  | "dispatched"
  | "in_progress"
  | "implementation_done"
  | "main_review"
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
  assignee?: TaskAssignee; // Agents First: structured agent+model+session metadata
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

  // Callback routing: Main Agent session that dispatched this task
  originatorSessionId?: string;

  // Rework history: accumulated across attempts for iterative context
  reworkHistory: ReworkHistoryEntry[];
}

export interface ReworkHistoryEntry {
  attempt: number;
  receipt: ImplementationReceipt;
  acceptanceId: string;
  findings: string[];
  timestamp: number;
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
  scopeViolations?: ScopeViolation[];
}

export interface ScopeViolation {
  file: string;
  reason: string;
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

// ─── Slash Commands ───

export interface SlashCommandArg {
  name: string;
  description: string;
  required: boolean;
  type: "string" | "number" | "boolean";
  defaultValue?: string;
}

export interface SlashCommand {
  name: string;          // e.g. "/compact"
  description: string;   // e.g. "Compact conversation history"
  args?: SlashCommandArg[];
  category?: string;     // e.g. "session", "debug", "config"
}

// ─── Session Management ───

export interface SessionInfo {
  sessionId: string;
  agentId: string;
  role?: AgentRole;
  frozenRole?: AgentRole;
  sessionName?: string; // "{role}-{cli}-{taskName}"
  cwd?: string;
  startedAt: number;
  lastActiveAt: number;
  tokenUsage?: number;
  tokenLimit?: number;
  status: "active" | "paused" | "completed" | "overflow";
  parentSessionId?: string; // for session continuity on overflow
  resumeToken?: string; // adapter-specific token for restoring a persisted session
  frozenSystemPrompt?: string;
  promptHash?: string;
}

// ─── Image Attachments ───

export type ImageMediaType = "image/png" | "image/jpeg" | "image/gif" | "image/webp";

export interface ImageAttachment {
  /** Base64-encoded image data (no data URI prefix) */
  data: string;
  mediaType: ImageMediaType;
  /** Optional filename for display */
  filename?: string;
  /** Width/height if known (for display) */
  width?: number;
  height?: number;
}

// ─── Agent Adapter Interface ───

export interface AgentMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  /** Attached images (user messages: input images; assistant messages: generated images) */
  images?: ImageAttachment[];
  metadata?: Record<string, unknown>;
}

export interface AgentAdapter {
  readonly agentId: string;
  readonly config: AgentConfig;

  startSession(cwd: string): Promise<SessionInfo>;
  sendPrompt(
    sessionId: string,
    prompt: string,
    images?: ImageAttachment[],
    hooks?: AgentSendHooks,
  ): AsyncGenerator<AgentMessage>;
  resumeSession(sessionId: string, persistedInfo?: SessionInfo, cwd?: string): Promise<SessionInfo>;
  endSession(sessionId: string): Promise<void>;

  /**
   * Inject shared context as system prompt.
   * Adapters use SDK-native system instructions where available (Claude SDK
   * `options.systemPrompt`, opencode HTTP `system` field, Gemini `GEMINI_SYSTEM_MD`
   * env var). For adapters without native support (Codex, opencode CLI fallback),
   * the context is prepended to the first user prompt of each session, which
   * does consume conversation context window tokens.
   * Called by Orchestrator when KB context is built/refreshed.
   */
  setSystemPrompt(prompt: string): void;

  // Slash commands supported by this agent's CLI
  getSlashCommands(): SlashCommand[];

  // Session continuity: when context overflows, create new session inheriting context
  handoffSession(
    oldSessionId: string,
    summary: string,
  ): Promise<SessionInfo>;
}
