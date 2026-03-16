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
  model?: string; // e.g. "claude-opus-4-6", "o3"
  role: "main" | "dev" | "acceptance" | "research";
  integration: string;
  capabilities: string[];
  restrictions: string[];
  maxConcurrentSessions: number;
}

export interface AgentMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
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

// Commands (frontend → Rust → sidecar)

export async function getAgents(): Promise<AgentConfig[]> {
  return invoke<AgentConfig[]>("get_agents");
}

export async function sendPrompt(
  agentId: string,
  prompt: string,
): Promise<{ sessionId: string }> {
  return invoke("send_prompt", { agentId, prompt });
}

export async function startSession(
  agentId: string,
): Promise<{ sessionId: string }> {
  return invoke("start_session", { agentId });
}

export async function stopSession(
  agentId: string,
  sessionId: string,
): Promise<void> {
  return invoke("stop_session", { agentId, sessionId });
}

export async function configureAgent(config: AgentConfig): Promise<void> {
  return invoke("configure_agent", { config });
}

export async function dispatchTask(
  fromAgentId: string,
  toAgentId: string,
  prompt: string,
): Promise<{ sessionId: string; taskId: string }> {
  return invoke("dispatch_task", { params: { fromAgentId, toAgentId, prompt } });
}

// ─── Config Operations ───

export interface ObsidianConfig {
  enabled: boolean;
  vaultName: string;
  autoInjectContext: boolean;
  contextFiles: string[];
}

export interface MercuryProjectConfig {
  agents: AgentConfig[];
  workDir?: string;
  obsidian?: ObsidianConfig;
}

export async function getConfig(): Promise<MercuryProjectConfig> {
  return invoke<MercuryProjectConfig>("get_config");
}

export async function updateConfig(
  config: MercuryProjectConfig,
): Promise<{ ok: true }> {
  return invoke("update_config", { config });
}

// ─── Task Orchestration Operations ───

export type TaskPriority = "sev-0" | "sev-1" | "sev-2" | "sev-3";
export type TaskStatus =
  | "drafted" | "dispatched" | "in_progress" | "implementation_done"
  | "acceptance" | "verified" | "closed" | "failed" | "blocked";
export type AcceptanceVerdict = "pass" | "partial" | "fail" | "blocked";

export interface TaskAssignee {
  agentId: string;
  model?: string;
  sessionId?: string;
}

export interface TaskBundle {
  taskId: string;
  title: string;
  phaseId?: string;
  priority: TaskPriority;
  status: TaskStatus;
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
  reworkCount: number;
  maxReworks: number;
  linkedIssueIds: string[];
}

export interface CreateTaskParams {
  title: string;
  phaseId?: string;
  priority: TaskPriority;
  assignedTo: string;
  branch?: string;
  codeScope: { include: string[]; exclude: string[] };
  readScope: { requiredDocs: string[]; optionalDocs: string[] };
  allowedWriteScope: { codePaths: string[]; kbPaths: string[] };
  docsMustUpdate?: string[];
  docsMustNotTouch?: string[];
  definitionOfDone: string[];
  requiredEvidence?: string[];
  context: string;
  maxReworks?: number;
}

export async function createTask(params: CreateTaskParams): Promise<TaskBundle> {
  return invoke<TaskBundle>("create_task", { params });
}

export async function getTask(taskId: string): Promise<TaskBundle | null> {
  return invoke<TaskBundle | null>("get_task", { taskId });
}

export async function listTasks(
  status?: TaskStatus,
  assignedTo?: string,
): Promise<TaskBundle[]> {
  return invoke<TaskBundle[]>("list_tasks", { status: status ?? null, assignedTo: assignedTo ?? null });
}

export async function dispatchBundleTask(
  taskId: string,
): Promise<{ sessionId: string; taskId: string }> {
  return invoke("dispatch_task", { params: { taskId } });
}

export async function recordReceipt(
  taskId: string,
  receipt: Record<string, unknown>,
): Promise<TaskBundle> {
  return invoke<TaskBundle>("record_receipt", { taskId, receipt });
}

export async function createAcceptance(
  taskId: string,
  acceptorId: string,
): Promise<{ acceptanceId: string; sessionId: string }> {
  return invoke("create_acceptance", { taskId, acceptorId });
}

export async function recordAcceptanceResult(
  acceptanceId: string,
  results: { verdict: AcceptanceVerdict; findings: string[]; recommendations: string[] },
): Promise<{ verdict: AcceptanceVerdict; reworkTriggered: boolean; newSession: boolean }> {
  return invoke("record_acceptance_result", { acceptanceId, results });
}

export async function createIssue(
  params: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return invoke("create_issue", { params });
}

export async function resolveIssue(
  issueId: string,
  resolution: { resolvedBy: string; summary: string; resolvedAt: number },
): Promise<Record<string, unknown>> {
  return invoke("resolve_issue", { issueId, resolution });
}

export async function summarizeSession(
  agentId: string,
  summary: string,
): Promise<{ newSessionId: string }> {
  return invoke("summarize_session", { agentId, summary });
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

export async function getSlashCommands(agentId: string): Promise<SlashCommand[]> {
  return invoke<SlashCommand[]>("get_slash_commands", { agentId });
}

// ─── Knowledge Base Operations (optional, requires obsidian enabled) ───

export async function kbRead(file: string): Promise<{ content: string }> {
  return invoke("kb_read", { file });
}

export async function kbSearch(
  query: string,
): Promise<Array<{ file: string; matches: string[] }>> {
  return invoke("kb_search", { query });
}

export async function kbList(
  folder?: string,
): Promise<Array<{ path: string; name: string; folder: string }>> {
  return invoke("kb_list", { folder: folder ?? null });
}

export async function kbWrite(
  name: string,
  content: string,
): Promise<{ ok: true }> {
  return invoke("kb_write", { name, content });
}

export async function kbAppend(
  file: string,
  content: string,
): Promise<{ ok: true }> {
  return invoke("kb_append", { file, content });
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

export interface AgentErrorEvent {
  agentId: string;
  sessionId: string;
  error: string;
}

export interface SidecarReadyEvent {
  agents: string[];
  timestamp: number;
}

export function onAgentMessage(
  handler: (data: AgentMessageEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentMessageEvent>("agent-message", (event) =>
    handler(event.payload),
  );
}

export function onAgentStreamEnd(
  handler: (data: AgentStreamEndEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentStreamEndEvent>("agent-stream-end", (event) =>
    handler(event.payload),
  );
}

export function onAgentError(
  handler: (data: AgentErrorEvent) => void,
): Promise<UnlistenFn> {
  return listen<AgentErrorEvent>("agent-error", (event) =>
    handler(event.payload),
  );
}

export function onMercuryEvent(
  handler: (data: MercuryEvent) => void,
): Promise<UnlistenFn> {
  return listen<MercuryEvent>("mercury-event", (event) =>
    handler(event.payload),
  );
}

export function onSidecarReady(
  handler: (data: SidecarReadyEvent) => void,
): Promise<UnlistenFn> {
  return listen<SidecarReadyEvent>("ready", (event) =>
    handler(event.payload),
  );
}

export function onSidecarError(
  handler: (data: { error: string }) => void,
): Promise<UnlistenFn> {
  return listen<{ error: string }>("sidecar-error", (event) =>
    handler(event.payload),
  );
}
