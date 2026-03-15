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
  return invoke("dispatch_task", { fromAgentId, toAgentId, prompt });
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
