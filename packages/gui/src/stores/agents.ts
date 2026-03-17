/**
 * Agent configuration and session state store.
 *
 * All per-panel state (status, sessions, workDir, gitBranch) is keyed by
 * panelKey (roleSlotKey = "{role}:{agentId}"), NOT by raw agentId.
 */

import { ref, computed } from "vue";
import type { AgentConfig, MercuryEvent } from "../lib/tauri-bridge";
import {
  getAgents as fetchAgents,
  getConfig,
  getProjectInfo,
  listSessions as fetchSessions,
  onMercuryEvent,
  onSidecarReady,
  onSidecarError,
} from "../lib/tauri-bridge";

// ─── Role Panel ───

export interface RolePanel {
  agentId: string;
  role: "main" | "dev" | "acceptance" | "research" | "design";
  displayName: string;
  panelKey: string; // "{role}:{agentId}"
}

export interface SessionMeta {
  sessionId: string;
  sessionName?: string;
  status?: "active" | "paused" | "completed" | "overflow";
  lastActiveAt?: number;
  promptHash?: string;
  currentPromptHash?: string;
  legacyRoleConfig?: boolean;
}

type SessionPromptState = Pick<SessionMeta, "promptHash" | "currentPromptHash" | "legacyRoleConfig">;

const SESSIONS_STORAGE_KEY = "mercury:sessions";

const agents = ref<AgentConfig[]>([]);
const statuses = ref<Map<string, "idle" | "active" | "error">>(new Map()); // panelKey → status
const sessions = ref<Map<string, string>>(new Map()); // panelKey → sessionId
const sessionMeta = ref<Map<string, SessionMeta>>(new Map()); // panelKey → session metadata
const sessionPromptState = ref<Map<string, SessionPromptState>>(new Map()); // sessionId → prompt metadata
const workDirs = ref<Map<string, string>>(new Map()); // panelKey → cwd
const gitBranches = ref<Map<string, string | null>>(new Map()); // panelKey → branch
const defaultWorkDir = ref("");
const sidecarReady = ref(false);
const sidecarError = ref<string | null>(null);

const mainAgent = computed(() => agents.value.find((a) => a.roles.includes("main")));

/** Expand each agent's non-main roles into individual panels. */
const rolePanels = computed<RolePanel[]>(() => {
  const panels: RolePanel[] = [];
  for (const agent of agents.value) {
    for (const role of agent.roles) {
      if (role === "main") continue;
      panels.push({
        agentId: agent.id,
        role,
        displayName: `${agent.displayName} (${role})`,
        panelKey: `${role}:${agent.id}`,
      });
    }
  }
  return panels;
});

// Legacy compat: subAgents = unique agents with non-main roles
const subAgents = computed(() =>
  agents.value.filter((a) => !a.roles.includes("main") || a.roles.length > 1),
);

function setStatus(panelKey: string, status: "idle" | "active" | "error") {
  statuses.value = new Map(statuses.value).set(panelKey, status);
}

function getStatus(panelKey: string): "idle" | "active" | "error" {
  return statuses.value.get(panelKey) ?? "idle";
}

function saveSessions(): void {
  try {
    const obj: Record<string, string> = {};
    for (const [key, sid] of sessions.value) obj[key] = sid;
    localStorage.setItem(SESSIONS_STORAGE_KEY, JSON.stringify(obj));
  } catch {
    // localStorage unavailable — ignore
  }
}

function loadSessions(): void {
  try {
    const raw = localStorage.getItem(SESSIONS_STORAGE_KEY);
    if (!raw) return;
    const obj = JSON.parse(raw) as Record<string, string>;
    const map = new Map<string, string>();
    for (const [key, sid] of Object.entries(obj)) {
      if (typeof sid === "string") map.set(key, sid);
    }
    sessions.value = map;
  } catch {
    // Corrupted — start fresh
  }
}

function setSession(panelKey: string, sessionId: string) {
  sessions.value = new Map(sessions.value).set(panelKey, sessionId);
  saveSessions();
}

function extractPromptState(info: Partial<SessionMeta>): SessionPromptState {
  return {
    promptHash: info.promptHash,
    currentPromptHash: info.currentPromptHash,
    legacyRoleConfig: info.legacyRoleConfig,
  };
}

function updateSessionPromptState(sessionId: string, info: Partial<SessionMeta>) {
  const nextState = extractPromptState(info);
  if (
    nextState.promptHash === undefined &&
    nextState.currentPromptHash === undefined &&
    nextState.legacyRoleConfig === undefined
  ) {
    return;
  }
  const existing = sessionPromptState.value.get(sessionId);
  sessionPromptState.value = new Map(sessionPromptState.value).set(sessionId, {
    ...existing,
    ...nextState,
  });
}

function setSessionInfo(panelKey: string, info: SessionMeta) {
  setSession(panelKey, info.sessionId);
  updateSessionPromptState(info.sessionId, info);
  const existing = sessionMeta.value.get(panelKey);
  const promptState = sessionPromptState.value.get(info.sessionId);
  const merged =
    existing?.sessionId === info.sessionId
      ? { ...existing, ...info, ...promptState }
      : { ...info, ...promptState };
  sessionMeta.value = new Map(sessionMeta.value).set(panelKey, merged);
}

function getSession(panelKey: string): string | undefined {
  return sessions.value.get(panelKey);
}

function getSessionInfo(panelKey: string): SessionMeta | undefined {
  return sessionMeta.value.get(panelKey);
}

function clearSession(panelKey: string) {
  const next = new Map(sessions.value);
  next.delete(panelKey);
  sessions.value = next;
  const nextMeta = new Map(sessionMeta.value);
  nextMeta.delete(panelKey);
  sessionMeta.value = nextMeta;
  saveSessions();
}

function setWorkDir(panelKey: string, cwd: string) {
  workDirs.value = new Map(workDirs.value).set(panelKey, cwd);
}

function getWorkDir(panelKey: string): string {
  return workDirs.value.get(panelKey) ?? defaultWorkDir.value;
}

function setGitBranch(panelKey: string, branch: string | null) {
  gitBranches.value = new Map(gitBranches.value).set(panelKey, branch);
}

function getGitBranch(panelKey: string): string | null {
  return gitBranches.value.get(panelKey) ?? null;
}

const anyActive = computed(() =>
  [...statuses.value.values()].some((s) => s === "active"),
);

const anyError = computed(() =>
  [...statuses.value.values()].some((s) => s === "error"),
);

async function hydrateSessionMeta(): Promise<void> {
  const byAgent = new Map<string, string[]>();
  for (const [panelKey, sessionId] of sessions.value) {
    const colonIdx = panelKey.indexOf(":");
    const role = panelKey.slice(0, colonIdx);
    const agentId = panelKey.slice(colonIdx + 1);
    const list = byAgent.get(`${role}:${agentId}`) ?? [];
    list.push(sessionId);
    byAgent.set(`${role}:${agentId}`, list);
  }

  for (const panelKey of byAgent.keys()) {
    const colonIdx = panelKey.indexOf(":");
    const role = panelKey.slice(0, colonIdx);
    const agentId = panelKey.slice(colonIdx + 1);
    try {
      const knownSessions = await fetchSessions(agentId, role, false);
      for (const [panelKey, sessionId] of sessions.value) {
        if (panelKey !== `${role}:${agentId}`) continue;
        const match = knownSessions.find((s) => s.sessionId === sessionId);
        if (!match) continue;
        setSessionInfo(panelKey, {
          sessionId: match.sessionId,
          sessionName: match.sessionName,
          status: match.status,
          lastActiveAt: match.lastActiveAt,
          promptHash: match.promptHash,
          currentPromptHash: (match as typeof match & { currentPromptHash?: string }).currentPromptHash,
          legacyRoleConfig: (match as typeof match & { legacyRoleConfig?: boolean }).legacyRoleConfig,
        });
      }
    } catch {
      // Best-effort hydration only
    }
  }
}

async function loadAgents() {
  try {
    const [fetchedAgents, config] = await Promise.all([fetchAgents(), getConfig()]);
    const configuredAgentIds = new Set(config.agents.map((agent) => agent.id));
    agents.value = fetchedAgents.filter((agent) => configuredAgentIds.has(agent.id));
    sidecarReady.value = true;
    sidecarError.value = null;
    // Initialize status for each role panel
    for (const agent of agents.value) {
      for (const role of agent.roles) {
        const key = `${role}:${agent.id}`;
        if (!statuses.value.has(key)) {
          statuses.value.set(key, "idle");
        }
      }
    }
    await hydrateSessionMeta();
  } catch (e) {
    console.error("Failed to fetch agents:", e);
  }
}

let agentListenersInitialized = false;

async function initAgents() {
  if (agentListenersInitialized) return;
  agentListenersInitialized = true;

  loadSessions();

  // Load default project directory
  try {
    const info = await getProjectInfo();
    defaultWorkDir.value = info.projectRoot;
  } catch {
    // non-critical
  }

  // Listen for sidecar ready event
  await onSidecarReady(() => loadAgents());

  await onSidecarError((data) => {
    sidecarError.value = data.error;
  });

  await onMercuryEvent((event: MercuryEvent) => {
    if (event.type === "agent.session.start") {
      const payload = event.payload as {
        role?: string;
        sessionName?: string;
        promptHash?: string;
        currentPromptHash?: string;
        legacyRoleConfig?: boolean;
      };
      if (!payload.role) return;
      const panelKey = `${payload.role}:${event.agentId}`;
      setSessionInfo(panelKey, {
        sessionId: event.sessionId,
        sessionName: payload.sessionName,
        status: "active",
        lastActiveAt: event.timestamp,
        promptHash: payload.promptHash,
        currentPromptHash: payload.currentPromptHash,
        legacyRoleConfig: payload.legacyRoleConfig,
      });
      setStatus(panelKey, "idle");
      return;
    }

    if (event.type === "agent.session.end") {
      for (const [panelKey, sessionId] of sessions.value) {
        if (sessionId !== event.sessionId) continue;
        clearSession(panelKey);
        setStatus(panelKey, "idle");
        break;
      }
      return;
    }

    if (event.type === "agent.message.receive") {
      for (const [panelKey, sessionId] of sessions.value) {
        if (sessionId !== event.sessionId) continue;
        const info = sessionMeta.value.get(panelKey);
        if (!info) break;
        sessionMeta.value = new Map(sessionMeta.value).set(panelKey, {
          ...info,
          lastActiveAt: event.timestamp,
          status: "active",
        });
        break;
      }
    }
  });

  // Fallback: if ready event was missed (race), poll until sidecar responds
  const poll = setInterval(async () => {
    if (sidecarReady.value) {
      clearInterval(poll);
      return;
    }
    try {
      await loadAgents();
      clearInterval(poll);
    } catch {
      // sidecar not ready yet, keep polling
    }
  }, 1000);
}

export function useAgentStore() {
  return {
    agents,
    mainAgent,
    subAgents,
    rolePanels,
    statuses,
    sidecarReady,
    sidecarError,
    anyActive,
    anyError,
    setStatus,
    getStatus,
    setSession,
    setSessionInfo,
    getSession,
    getSessionInfo,
    clearSession,
    sessions,
    sessionMeta,
    setWorkDir,
    getWorkDir,
    setGitBranch,
    getGitBranch,
    defaultWorkDir,
    initAgents,
  };
}
