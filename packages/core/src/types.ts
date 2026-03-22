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

export type AgentRole = "main" | "dev" | "acceptance" | "critic" | "research" | "design";

export type IntegrationType = "sdk" | "mcp" | "http" | "pty" | "rpc";

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

/**
 * @deprecated ROLE_CARDS has been removed. Role definitions are now loaded at runtime
 * from .mercury/roles/{role}.yaml via role-loader.ts. This empty map is kept for
 * backward compatibility — migrate to loadRoleCard() from @mercury/orchestrator.
 */
export const ROLE_CARDS: Record<string, never> = {};

export interface RoleCard {
  role: AgentRole;
  description: string;
  canExecuteCode: boolean;
  canDelegateToRoles: AgentRole[];
  inputBoundary: string[];
  outputBoundary: string[];
}

// ─── Project Configuration ───

export interface ObsidianConfig {
  enabled: boolean;
  vaultName: string;
  vaultPath?: string; // filesystem path to vault root (for git sync)
  obsidianBin?: string; // explicit path to Obsidian binary (auto-detected if omitted)
  kbPaths?: {
    tasks?: string;
    acceptances?: string;
    issues?: string;
  };
  autoInjectContext: boolean;
  contextFiles: string[]; // files to inject as system prompt context
  roleContextFiles?: {
    main?: string[];
    dev?: string[];
    acceptance?: string[];
  };
}

export interface RTKConfig {
  enabled: boolean;
  binaryPath?: string; // explicit path to RTK binary (defaults to "rtk" when omitted)
  commands: string[]; // command basenames to wrap, e.g. ["codex", "gemini"]
}

export interface MercuryConfig {
  agents: AgentConfig[];
  workDir?: string;
  rpcPort?: number;
  obsidian?: ObsidianConfig;
  rtk?: RTKConfig;
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

export type KBEntryKind = "file" | "folder";

export interface KBFileInfo {
  path: string;
  name: string;
  folder: string;
  kind: KBEntryKind;
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
  | "orchestrator.critic.result"
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

export type TaskPriority = "P0" | "P1" | "P2" | "P3";

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

/** Normalize legacy priority formats (sev-0..sev-3) to canonical P0..P3. */
export function normalizePriority(raw: string): TaskPriority {
  const map: Record<string, TaskPriority> = {
    "sev-0": "P0", "sev-1": "P1", "sev-2": "P2", "sev-3": "P3",
    "P0": "P0", "P1": "P1", "P2": "P2", "P3": "P3",
    "p0": "P0", "p1": "P1", "p2": "P2", "p3": "P3",
  };
  return map[raw] ?? "P3";
}

export interface PreCheckConfig {
  name: string;
  command: string;
  args?: string[];
  cwd?: string;
  timeoutMs?: number;
  shell?: boolean;
}

export interface PreCheckResult {
  name: string;
  command: string;
  cwd: string;
  success: boolean;
  exitCode: number | null;
  timedOut: boolean;
  durationMs: number;
  stdout: string;
  stderr: string;
}

export interface ReviewFinding {
  severity: "critical" | "major" | "minor" | "info";
  title: string;
  detail: string;
  file?: string;
  line?: number;
}

export interface StructuredReviewResult {
  decision: "APPROVE_FOR_ACCEPTANCE" | "SEND_BACK";
  summary: string;
  reason?: string;
  findings: ReviewFinding[];
}

export interface ReviewConfig {
  preChecks?: PreCheckConfig[];
  diffBaseRef?: string;
  diffMaxChars?: number;
}

/** SoT task bundle: tracks a unit of work through its full lifecycle. */
export interface TaskBundle {
  taskId: string;
  title: string;
  phaseId?: string;
  priority: TaskPriority;
  status: TaskStatus;
  createdAt?: string; // ISO 8601, set by TaskManager when task is created (optional for legacy compat)
  closedAt: string | null; // ISO 8601, set by TaskManager when status → closed/verified
  failedAt: string | null; // ISO 8601, set by TaskManager when status → failed
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
  reviewConfig?: ReviewConfig;

  // Acceptance handoff
  handoffToAcceptance?: {
    acceptanceBundleId: string;
    blindInputPolicy: { allowed: string[]; forbidden: string[] };
    acceptanceFocus: string[];
  };

  // Implementation receipt (filled by dev agent)
  implementationReceipt?: ImplementationReceipt;
  mainReview?: {
    preChecks: PreCheckResult[];
    gitDiff: string;
    result?: StructuredReviewResult;
    reviewedAt?: number;
  };

  // Critic review (spec-driven verification, parallel to main_review)
  criticReview?: {
    result?: CriticResult;
    reviewedAt?: number;
    criticAgent?: string; // agentId of the critic
  };

  // Dispatch retry tracking
  dispatchAttempts: number;
  maxDispatchAttempts: number;
  lastDispatchError?: string;
  lastDispatchAt?: string; // ISO 8601

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

// ─── Critic Result ───

export type CriticVerdict = "pass" | "partial" | "fail";
export type CriticItemVerdict = "pass" | "fail" | "partial" | "skip";

/** Per-item verification result from the Critic Agent. */
export interface CriticItemResult {
  dodItem: string;
  verdict: CriticItemVerdict;
  evidence: string;
  detail: string;
}

/** Structured output from the Critic Agent's spec-driven verification. */
export interface CriticResult {
  overallVerdict: CriticVerdict;
  completeness: number; // 0.0 – 1.0
  items: CriticItemResult[];
  blockers: string[];
  suggestions: string[];
}

// ─── Issue Bundle ───

export type IssueType = "bug" | "scope_creep" | "blocker" | "question";

export interface IssueBundle {
  issueId: string;
  title: string;
  status: "open" | "resolved" | "deferred";
  type: IssueType;
  priority: TaskPriority;
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
  baseRolePromptHash?: string;
  promptHash?: string;
  /** ISO 8601 timestamp when a context checkpoint was taken at ~70% token usage. */
  tokenCheckpointAt?: string;
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

// ─── Streaming Events ───

export type AgentStreamingEventKind =
  | "text_delta"
  | "tool_start"
  | "tool_delta"
  | "tool_end";

/**
 * Incremental streaming event yielded by adapters when streaming is enabled.
 * Unlike AgentMessage (complete turns), streaming events represent partial content
 * arriving token-by-token from the LLM.
 *
 * Claude SDK: includePartialMessages → content_block_delta (text_delta / input_json_delta)
 * Codex app-server: item/agentMessage/delta, item/commandExecution/outputDelta
 */
export interface AgentStreamingEvent {
  type: "streaming";
  eventKind: AgentStreamingEventKind;
  /** Incremental text content (for text_delta, tool_delta) */
  content?: string;
  /** Tool name (for tool_start) */
  toolName?: string;
  /** Partial JSON input for tool call (for tool_delta) */
  toolInput?: string;
  /** Cumulative token count reported by the adapter during streaming. */
  tokenCount?: number;
  timestamp: number;
}

/** Union type yielded by adapter sendPrompt generators. */
export type AdapterYield = AgentMessage | AgentStreamingEvent;

/** Type guard: is the yielded value a streaming event? */
export function isStreamingEvent(value: AdapterYield): value is AgentStreamingEvent {
  return "type" in value && (value as AgentStreamingEvent).type === "streaming";
}

export interface AgentOneShotOptions {
  model?: string;
  sandbox?: string;
  approvalPolicy?: unknown;
}

export interface AgentOneShotResult {
  messages: AgentMessage[];
  finalMessage: string;
  threadId: string;
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
  ): AsyncGenerator<AdapterYield>;
  resumeSession(sessionId: string, persistedInfo?: SessionInfo, cwd?: string): Promise<SessionInfo>;
  endSession(sessionId: string): Promise<void>;
  executeOneShot?(
    prompt: string,
    cwd: string,
    options?: AgentOneShotOptions,
  ): Promise<AgentOneShotResult>;

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

  // Runtime model listing and mid-session switching
  listModels(): Promise<{ id: string; name: string }[]>;
  setModel(model: string): void;

  // Slash commands supported by this agent's CLI
  getSlashCommands(): SlashCommand[];

  // Session continuity: when context overflows, create new session inheriting context
  handoffSession(
    oldSessionId: string,
    summary: string,
  ): Promise<SessionInfo>;
}
