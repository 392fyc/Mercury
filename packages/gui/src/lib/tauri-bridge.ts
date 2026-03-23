/**
 * Typed wrappers for Tauri invoke() and listen() calls.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

// Agent config matching @mercury/core AgentConfig
export interface AgentConfig {
  id: string;
  displayName: string;
  cli: string;
  model?: string; // e.g. "claude-opus-4-6", "gpt-5.4"
  roles: ("main" | "dev" | "acceptance" | "research" | "design")[];
  integration: string;
  capabilities: string[];
  restrictions: string[];
  maxConcurrentSessions: number;
}

export type ImageMediaType = "image/png" | "image/jpeg" | "image/gif" | "image/webp";

export interface ImageAttachment {
  data: string; // base64 encoded
  mediaType: ImageMediaType;
  filename?: string;
  width?: number;
  height?: number;
}

export interface AgentMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  images?: ImageAttachment[];
  metadata?: Record<string, unknown>;
}

export interface MercuryEvent {
  id: string;
  type: string;
  timestamp: number;
  agentId: string;
  modelId?: string;
  sessionId: string;
  payload: Record<string, unknown>;
  parentEventId?: string;
}

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

export interface ApprovalRequest {
  id: string;
  agentId: string;
  sessionId: string;
  role?: string;
  adapter: string;
  kind: ApprovalRequestKind;
  toolName?: string;
  summary: string;
  rawRequest?: Record<string, unknown>;
  cwd?: string;
  createdAt: number;
  resolvedAt?: number;
  status: ApprovalRequestStatus;
  decisionBy?: "main_agent" | "system";
  decisionReason?: string;
}

// Project info (frontend → Rust directly, no sidecar)

export interface ProjectInfo {
  projectRoot: string;
  gitBranch: string | null;
}

/** Retrieve the current project root path and git branch via Rust. */
export async function getProjectInfo(): Promise<ProjectInfo> {
  return invoke<ProjectInfo>("get_project_info");
}

// Git info for arbitrary directory (frontend → Rust directly, no sidecar)

export interface GitInfo {
  path: string;
  gitBranch: string | null;
}

/** Get git information (branch name) for an arbitrary directory path. */
export async function getGitInfo(path: string): Promise<GitInfo> {
  return invoke<GitInfo>("get_git_info", { path });
}

export interface GitBranchList {
  current: string;
  local: string[];
  remote: string[];
}

/** List local and remote git branches for the repository at the given path. */
export async function listGitBranches(path: string): Promise<GitBranchList> {
  return invoke<GitBranchList>("list_git_branches", { path });
}

/** Switch the repository at `path` to the specified git branch. */
export async function checkoutBranch(path: string, branch: string): Promise<{ ok: boolean; branch: string }> {
  return invoke<{ ok: boolean; branch: string }>("checkout_branch", { path, branch });
}

// Agent workspace (frontend → Rust → sidecar)

/** Set the working directory for an agent via the sidecar. */
export async function setAgentCwd(
  agentId: string,
  cwd: string,
): Promise<{ ok: true }> {
  return invoke("set_agent_cwd", { agentId, cwd });
}

// Commands (frontend → Rust → sidecar)

/** Retrieve the list of configured agents from the sidecar. */
export async function getAgents(): Promise<AgentConfig[]> {
  return invoke<AgentConfig[]>("get_agents");
}

/** Send a prompt (with optional images and role) to an agent session. */
export async function sendPrompt(
  agentId: string,
  prompt: string,
  images?: ImageAttachment[],
  role?: string,
): Promise<{ sessionId: string; role?: string; sessionName?: string; status?: string }> {
  return invoke("send_prompt", { agentId, prompt, images: images ?? null, role: role ?? null });
}

/**
 * Start a new session for an agent, optionally with a specific role.
 * Does NOT send any prompt — just creates the session.
 */
export async function startSession(
  agentId: string,
  role?: string,
): Promise<{ sessionId: string; role?: string; sessionName?: string; status?: string }> {
  return invoke("start_session", { agentId, role: role ?? null });
}

/** Stop an active agent session by ID. */
export async function stopSession(
  agentId: string,
  sessionId: string,
): Promise<void> {
  return invoke("stop_session", { agentId, sessionId });
}

/** Delete a terminated session and its associated resources. */
export async function deleteSession(
  agentId: string,
  sessionId: string,
): Promise<void> {
  return invoke("delete_session", { agentId, sessionId });
}

/** Update an agent's configuration in the sidecar runtime. */
export async function configureAgent(config: AgentConfig): Promise<void> {
  return invoke("configure_agent", { config });
}

/** Dispatch a task prompt from one agent to another, creating a new session. */
export async function dispatchTask(
  fromAgentId: string,
  toAgentId: string,
  prompt: string,
  role?: string,
): Promise<{ sessionId: string; taskId: string }> {
  return invoke("dispatch_task", { params: { fromAgentId, toAgentId, prompt, role } });
}

// ─── Config Operations ───

export interface ObsidianConfig {
  enabled: boolean;
  vaultName: string;
  vaultPath?: string;
  obsidianBin?: string;
  kbPaths?: {
    tasks?: string;
    acceptances?: string;
    issues?: string;
  };
  autoInjectContext: boolean;
  contextFiles: string[];
  roleContextFiles?: {
    main?: string[];
    dev?: string[];
    acceptance?: string[];
  };
}

export interface MercuryProjectConfig {
  agents: AgentConfig[];
  workDir?: string;
  obsidian?: ObsidianConfig;
}

/** Retrieve the current Mercury project configuration. */
export async function getConfig(): Promise<MercuryProjectConfig> {
  return invoke<MercuryProjectConfig>("get_config");
}

/** Persist an updated Mercury project configuration. */
export async function updateConfig(
  config: MercuryProjectConfig,
): Promise<{ ok: true }> {
  return invoke("update_config", { config });
}

// ─── Task Orchestration Operations ───

export type TaskPriority = "P0" | "P1" | "P2" | "P3";
/** Task lifecycle status. SYNC: mirrors @mercury/core TaskStatus. */
export type TaskStatus =
  | "drafted" | "dispatched" | "in_progress" | "implementation_done"
  | "main_review" | "acceptance" | "verified" | "closed" | "failed" | "blocked";
export type AcceptanceVerdict = "pass" | "partial" | "fail" | "blocked";

/** Agent assignment metadata. SYNC: mirrors @mercury/core TaskAssignee. */
export interface TaskAssignee {
  agentId: string;
  model?: string;
  sessionId?: string;
}

/**
 * Frontend representation of a task bundle with lifecycle timestamps.
 * SYNC: This interface mirrors @mercury/core TaskBundle. Keep in sync when modifying fields.
 * Separate definition required due to Tauri serialization boundary (Rust <-> JS).
 */
export interface TaskBundle {
  taskId: string;
  title: string;
  phaseId?: string;
  priority: TaskPriority;
  status: TaskStatus;
  createdAt?: string;
  closedAt: string | null;
  failedAt: string | null;
  assignedTo: string;
  assignee?: TaskAssignee;
  branch?: string;
  codeScope: { include: string[]; exclude: string[] };
  readScope: { requiredDocs: string[]; optionalDocs: string[] };
  allowedWriteScope: { codePaths: string[]; kbPaths: string[] };
  docsMustUpdate: string[];
  docsMustNotTouch: string[];
  definitionOfDone: string[];
  requiredEvidence: string[];
  context: string;
  handoffToAcceptance?: {
    acceptanceBundleId: string;
    blindInputPolicy: { allowed: string[]; forbidden: string[] };
    acceptanceFocus: string[];
  };
  implementationReceipt?: {
    implementer: string;
    branch: string;
    summary: string;
    changedFiles: string[];
    evidence: string[];
    docsUpdated: string[];
    residualRisks: string[];
    completedAt: number;
  };
  criticReview?: {
    result?: {
      overallVerdict: "pass" | "partial" | "fail";
      completeness: number;
      items: Array<{
        dodItem: string;
        verdict: "pass" | "fail" | "partial" | "skip";
        evidence: string;
        detail: string;
      }>;
      blockers: string[];
      suggestions: string[];
    };
    reviewedAt?: number;
    criticAgent?: string;
  };
  reworkCount: number;
  maxReworks: number;
  linkedIssueIds: string[];
}

export interface CreateTaskParams {
  title: string;
  phaseId?: string;
  priority: TaskPriority;
  assignedTo?: string; // Optional: auto-assigned via G9 modelRecommendation if omitted
  branch?: string;
  codeScope: { include: string[]; exclude: string[] };
  readScope: { requiredDocs: string[]; optionalDocs: string[] };
  allowedWriteScope: { codePaths: string[]; kbPaths: string[] };
  docsMustUpdate?: string[];
  docsMustNotTouch?: string[];
  definitionOfDone: string[];
  requiredEvidence?: string[];
  context: string;
  modelRecommendation?: {
    complexity: "low" | "medium" | "high"; // Required per core ModelRecommendation
    preferredModel?: string;
    requiredCapabilities?: string[];
  };
  maxReworks?: number;
}

/** Create a new task bundle from the provided parameters. */
export async function createTask(params: CreateTaskParams): Promise<TaskBundle> {
  return invoke<TaskBundle>("create_task", { params });
}

/** Fetch a single task bundle by its ID, or null if not found. */
export async function getTask(taskId: string): Promise<TaskBundle | null> {
  return invoke<TaskBundle | null>("get_task", { taskId });
}

/** List task bundles, optionally filtered by status and/or assignee. */
export async function listTasks(
  status?: TaskStatus,
  assignedTo?: string,
): Promise<TaskBundle[]> {
  return invoke<TaskBundle[]>("list_tasks", { status: status ?? null, assignedTo: assignedTo ?? null });
}

/** Dispatch a task bundle to the assigned agent for execution. */
export async function dispatchBundleTask(
  taskId: string,
): Promise<{ sessionId: string; taskId: string }> {
  return invoke("dispatch_task", { params: { taskId } });
}

/** Record an implementation receipt for a completed task. */
export async function recordReceipt(
  taskId: string,
  receipt: Record<string, unknown>,
): Promise<TaskBundle> {
  return invoke<TaskBundle>("record_receipt", { taskId, receipt });
}

/** Create an acceptance test session for a task, assigning the given acceptor. */
export async function createAcceptance(
  taskId: string,
  acceptorId: string,
): Promise<{ acceptanceId: string; sessionId: string }> {
  return invoke("create_acceptance", { taskId, acceptorId });
}

/** Record the acceptance test verdict and findings for a task. */
export async function recordAcceptanceResult(
  acceptanceId: string,
  results: { verdict: AcceptanceVerdict; findings: string[]; recommendations: string[] },
): Promise<{ verdict: AcceptanceVerdict; reworkTriggered: boolean; newSession: boolean }> {
  return invoke("record_acceptance_result", { acceptanceId, results });
}

/** Create a new issue in the Mercury issue tracker. */
export async function createIssue(
  params: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return invoke("create_issue", { params });
}

/** Mark an issue as resolved with a summary and resolution metadata. */
export async function resolveIssue(
  issueId: string,
  resolution: { resolvedBy: string; summary: string; resolvedAt: number },
): Promise<Record<string, unknown>> {
  return invoke("resolve_issue", { issueId, resolution });
}

/** Summarize and close the current session, returning the new session ID. */
export async function summarizeSession(
  agentId: string,
  summary: string,
): Promise<{ newSessionId: string }> {
  return invoke("summarize_session", { agentId, summary });
}

// ─── Model Listing & Switching ───

/** List available models for the specified agent. */
export async function listModels(agentId: string): Promise<{ id: string; name: string }[]> {
  return invoke<{ id: string; name: string }[]>("list_models", { agentId });
}

/** Switch an agent to a different model at runtime. */
export async function setModel(agentId: string, model: string): Promise<{ ok: boolean }> {
  return invoke<{ ok: boolean }>("set_model", { agentId, model });
}

// ─── Slash Commands ───

export interface SlashCommandArg {
  name: string;
  description: string;
  required: boolean;
  type: string;
  defaultValue?: string;
}

export interface SlashCommand {
  name: string;
  description: string;
  args?: SlashCommandArg[];
  category?: string;
}

/** Retrieve the list of slash commands supported by an agent. */
export async function getSlashCommands(agentId: string): Promise<SlashCommand[]> {
  return invoke<SlashCommand[]>("get_slash_commands", { agentId });
}

// ─── Knowledge Base Operations (optional, requires obsidian enabled) ───

/** Read a file from the Obsidian knowledge base. */
export async function kbRead(file: string): Promise<{ content: string }> {
  return invoke("kb_read", { file });
}

/** Search the knowledge base for files matching the given query. */
export async function kbSearch(
  query: string,
): Promise<Array<{ file: string; matches: string[] }>> {
  return invoke("kb_search", { query });
}

/** List files and folders in the knowledge base, optionally within a subfolder. */
export async function kbList(
  folder?: string,
): Promise<Array<{ path: string; name: string; folder: string; kind: "file" | "folder" }>> {
  return invoke("kb_list", { folder: folder ?? null });
}

/** Write (create or overwrite) a file in the knowledge base. */
export async function kbWrite(
  name: string,
  content: string,
): Promise<{ ok: true }> {
  return invoke("kb_write", { name, content });
}

/** Append content to an existing knowledge base file. */
export async function kbAppend(
  file: string,
  content: string,
): Promise<{ ok: true }> {
  return invoke("kb_append", { file, content });
}

// ─── Session Resume Operations ───

export interface SessionListItem {
  sessionId: string;
  agentId: string;
  role?: string;
  frozenRole?: string;
  sessionName?: string;
  startedAt: number;
  lastActiveAt: number;
  status: "active" | "paused" | "completed" | "overflow";
  active: boolean;
  parentSessionId?: string;
  promptHash?: string;
}

/** List sessions, optionally filtered by agent, role, or terminal status. */
export async function listSessions(
  agentId?: string,
  role?: string,
  includeTerminal?: boolean,
): Promise<SessionListItem[]> {
  return invoke<SessionListItem[]>("list_sessions", {
    agentId: agentId ?? null,
    role: role ?? null,
    includeTerminal: includeTerminal ?? null,
  });
}

/** Resume a previously paused or overflow session for an agent. */
export async function resumeSession(
  agentId: string,
  sessionId: string,
  expectedRole?: string,
): Promise<{ sessionId: string; role?: string; sessionName?: string; status?: string }> {
  return invoke("resume_session", {
    agentId,
    sessionId,
    expectedRole: expectedRole ?? null,
  });
}

export interface TranscriptMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  images?: ImageAttachment[];
  metadata?: Record<string, unknown>;
}

/** Retrieve paginated transcript messages for a given session. */
export async function getSessionMessages(
  sessionId: string,
  offset?: number,
  limit?: number,
  agentId?: string,
  role?: string,
): Promise<{ messages: TranscriptMessage[]; total: number }> {
  return invoke("get_session_messages", {
    sessionId,
    offset: offset ?? null,
    limit: limit ?? null,
    agentId: agentId ?? null,
    role: role ?? null,
  });
}

/** Read native CLI session history from JSONL files (direct filesystem, no sidecar). */
export async function readSessionHistory(
  cliType: "claude" | "codex",
  sessionId: string,
  cwd?: string,
): Promise<{ messages: TranscriptMessage[]; source: string; total: number }> {
  return invoke("read_session_history", {
    cliType,
    sessionId,
    cwd: cwd ?? null,
  });
}

// ─── Approval Control Plane ───

/** Get the current approval mode (main_agent_review or auto_accept). */
export async function getApprovalMode(): Promise<{ mode: ApprovalMode }> {
  return invoke<{ mode: ApprovalMode }>("get_approval_mode");
}

/** Set the approval mode for incoming agent requests. */
export async function setApprovalMode(mode: ApprovalMode): Promise<{ mode: ApprovalMode }> {
  return invoke<{ mode: ApprovalMode }>("set_approval_mode", { mode });
}

/** List approval requests, optionally filtered by status. */
export async function listApprovalRequests(
  status?: ApprovalRequestStatus,
): Promise<ApprovalRequest[]> {
  return invoke<ApprovalRequest[]>("list_approval_requests", { status: status ?? null });
}

/** Approve a pending approval request with an optional reason. */
export async function approveRequest(
  requestId: string,
  reason?: string,
): Promise<{ action: "approve" | "deny"; reason?: string }> {
  return invoke("approve_request", { requestId, reason: reason ?? null });
}

/** Deny a pending approval request with an optional reason. */
export async function denyRequest(
  requestId: string,
  reason?: string,
): Promise<{ action: "approve" | "deny"; reason?: string }> {
  return invoke("deny_request", { requestId, reason: reason ?? null });
}

// ─── Shared Context Operations ───

export interface ContextStatus {
  hasContext: boolean;
  contextLength: number;
  autoInject: boolean;
  contextFiles: string[];
  roleContextFiles?: ObsidianConfig["roleContextFiles"];
}

/** Re-inject shared context into all active agent sessions. */
export async function refreshContext(): Promise<{ injected: boolean; agentCount: number; contextLength: number }> {
  return invoke("refresh_context");
}

/** Get the current shared context injection status and configuration. */
export async function getContextStatus(): Promise<ContextStatus> {
  return invoke<ContextStatus>("get_context_status");
}

// ─── Remote Control Operations ───

export type RemoteControlStatus =
  | "stopped"
  | "starting"
  | "waiting_for_connection"
  | "connected"
  | "error";

export interface RemoteControlState {
  status: RemoteControlStatus;
  session_url: string | null;
  session_name: string | null;
  /** Present only when `status === "error"`; carries the error description. */
  error_message?: string | null;
}

/** Start a `claude remote-control` subprocess with an optional session name. */
export async function startRemoteControl(
  sessionName?: string,
): Promise<{ ok: true }> {
  return invoke("start_remote_control", { sessionName: sessionName ?? null });
}

/** Stop the running `claude remote-control` subprocess. */
export async function stopRemoteControl(): Promise<{ ok: true }> {
  return invoke("stop_remote_control");
}

/** Query the current remote control subprocess state from the backend. */
export async function getRemoteControlStatus(): Promise<RemoteControlState> {
  return invoke<RemoteControlState>("get_remote_control_status");
}

/** Listen for remote control status change events from the backend. */
export function onRemoteControlStatus(
  handler: (data: RemoteControlState) => void,
): Promise<UnlistenFn> {
  return listen<RemoteControlState>("remote-control-status", (event) =>
    handler(event.payload),
  );
}

/** Listen for remote control session URL events. */
export function onRemoteControlUrl(
  handler: (data: { url: string }) => void,
): Promise<UnlistenFn> {
  return listen<{ url: string }>("remote-control-url", (event) =>
    handler(event.payload),
  );
}

/** Listen for remote control log messages (stdout/stderr). */
export function onRemoteControlLog(
  handler: (data: { level: string; message: string }) => void,
): Promise<UnlistenFn> {
  return listen<{ level: string; message: string }>("remote-control-log", (event) =>
    handler(event.payload),
  );
}

// Events (sidecar → Rust → frontend)

export interface AgentMessageEvent {
  agentId: string;
  sessionId: string;
  message: AgentMessage;
}

export interface AgentStreamEndEvent {
  agentId: string;
  sessionId: string;
}

export type AgentStreamingEventKind =
  | "text_delta"
  | "tool_start"
  | "tool_delta"
  | "tool_end";

/** Incremental streaming event for real-time token display. */
export interface AgentStreamingEvent {
  agentId: string;
  sessionId: string;
  event: {
    eventKind: AgentStreamingEventKind;
    content?: string;
    toolName?: string;
    toolInput?: string;
    timestamp: number;
  };
}

export interface AgentWorkingEvent {
  agentId: string;
  sessionId: string;
  role?: string;
  startedAt: number;
}

export interface AgentErrorEvent {
  agentId: string;
  sessionId: string;
  error: string;
}

export interface SidecarReadyEvent {
  agents: string[];
  timestamp: number;
}

/** Listen for complete agent response messages. */
export function onAgentMessage(
  handler: (data: AgentMessageEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentMessageEvent>("agent-message", (event) =>
    handler(event.payload),
  );
}

/** Listen for the end-of-stream signal from an agent session. */
export function onAgentStreamEnd(
  handler: (data: AgentStreamEndEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentStreamEndEvent>("agent-stream-end", (event) =>
    handler(event.payload),
  );
}

/** Listen for incremental streaming token events from an agent. */
export function onAgentStreaming(
  handler: (data: AgentStreamingEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentStreamingEvent>("agent-streaming", (event) =>
    handler(event.payload),
  );
}

/** Listen for agent working-state notifications (session started processing). */
export function onAgentWorking(
  handler: (data: AgentWorkingEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentWorkingEvent>("agent-working", (event) =>
    handler(event.payload),
  );
}

/** Listen for agent error events. */
export function onAgentError(
  handler: (data: AgentErrorEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentErrorEvent>("agent-error", (event) =>
    handler(event.payload),
  );
}

/** Listen for generic Mercury platform events. */
export function onMercuryEvent(
  handler: (data: MercuryEvent) => void,
): Promise<UnlistenFn> {
  return listen<MercuryEvent>("mercury-event", (event) =>
    handler(event.payload),
  );
}

/** Listen for the sidecar ready event after initial bootstrap. */
export function onSidecarReady(
  handler: (data: SidecarReadyEvent) => void,
): Promise<UnlistenFn> {
  return listen<SidecarReadyEvent>("ready", (event) =>
    handler(event.payload),
  );
}

/** Listen for fatal sidecar error events. */
export function onSidecarError(
  handler: (data: { error: string }) => void,
): Promise<UnlistenFn> {
  return listen<{ error: string }>("sidecar-error", (event) =>
    handler(event.payload),
  );
}
