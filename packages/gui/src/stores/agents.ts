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

// ─── Bookmark Rail ───

export interface BookmarkInfo {
  panelKey: string;
  sessionId: string;
  agentId: string;
  role: "main" | "dev" | "acceptance" | "research" | "design";
  displayName: string;
  sessionName?: string;
  status: "idle" | "active" | "error";
  lastActiveAt: number;
}

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

// ─── Bookmark Rail State ───
/** Manually created sub-agent bookmarks (panelKey → true). Auto-created when session starts. */
const bookmarks = ref<Map<string, boolean>>(new Map());
/** Which panelKeys are currently open as floating tabs */
const openFloatingTabs = ref<string[]>([]);
/** Model cache: agentId → last fetched model list */
const modelCache = ref<Map<string, { id: string; name: string }[]>>(new Map());

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

/**
 * Restore sessions from localStorage and rebuild bookmarks for non-main sessions.
 */
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
    // Rebuild bookmarks from persisted sessions (non-main only)
    for (const panelKey of map.keys()) {
      const { role } = parsePanelKey(panelKey);
      if (role !== "main") {
        bookmarks.value = new Map(bookmarks.value).set(panelKey, true);
      }
    }
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

/**
 * Parse a panelKey into role and agentId.
 * Supports both legacy "{role}:{agentId}" and session-unique "{role}:{agentId}:{shortSid}" formats.
 * Returns empty strings for malformed keys to prevent downstream crashes.
 */
type AgentRole = "main" | "dev" | "acceptance" | "research" | "design";
const VALID_ROLES: ReadonlySet<string> = new Set<AgentRole>(["main", "dev", "acceptance", "research", "design"]);

/**
 * Parse a composite panelKey into role + agentId.
 * Format: "{role}:{agentId}" (main) or "{role}:{agentId}:{sessionId}" (sub-agents).
 * Falls back to role="dev" for malformed keys — this is intentional: malformed
 * keys are filtered out by downstream bookmarkList (skips "main"), so "dev" is
 * the safest default that keeps the entry visible for debugging.
 */
function parsePanelKey(panelKey: string): { role: AgentRole; agentId: string } {
  const parts = panelKey.split(":");
  if (parts.length < 2) {
    console.warn(`[agents] malformed panelKey: "${panelKey}"`);
    return { role: "dev", agentId: "" };
  }
  const rawRole = parts[0];
  if (!VALID_ROLES.has(rawRole)) {
    console.warn(`[agents] unknown role "${rawRole}" in panelKey "${panelKey}", defaulting to "dev"`);
    return { role: "dev", agentId: parts[1] };
  }
  return { role: rawRole as AgentRole, agentId: parts[1] };
}

/** All sub-agent bookmarks, sorted by lastActiveAt descending. */
const bookmarkList = computed<BookmarkInfo[]>(() => {
  const items: BookmarkInfo[] = [];
  for (const panelKey of bookmarks.value.keys()) {
    const { role, agentId } = parsePanelKey(panelKey);
    if (role === "main") continue;
    const agent = agents.value.find((a) => a.id === agentId);
    const meta = sessionMeta.value.get(panelKey);
    const sid = sessions.value.get(panelKey);
    items.push({
      panelKey,
      sessionId: sid ?? "",
      agentId,
      role,
      displayName: agent?.displayName ?? agentId,
      sessionName: meta?.sessionName,
      status: statuses.value.get(panelKey) ?? "idle",
      lastActiveAt: meta?.lastActiveAt ?? 0,
    });
  }
  return items.sort((a, b) => b.lastActiveAt - a.lastActiveAt);
});

/** Check if a sessionId is already open in any floating tab. */
function isSessionOpen(sessionId: string): boolean {
  for (const pk of openFloatingTabs.value) {
    if (sessions.value.get(pk) === sessionId) return true;
  }
  return false;
}

function addBookmark(panelKey: string) {
  bookmarks.value = new Map(bookmarks.value).set(panelKey, true);
}

function removeBookmark(panelKey: string) {
  const next = new Map(bookmarks.value);
  next.delete(panelKey);
  bookmarks.value = next;
  // Also close floating tab if open
  openFloatingTabs.value = openFloatingTabs.value.filter((k) => k !== panelKey);
}

function openFloatingTab(panelKey: string) {
  if (!openFloatingTabs.value.includes(panelKey)) {
    openFloatingTabs.value = [...openFloatingTabs.value, panelKey];
  }
}

function closeFloatingTab(panelKey: string) {
  openFloatingTabs.value = openFloatingTabs.value.filter((k) => k !== panelKey);
}

function getModelCache(agentId: string): { id: string; name: string }[] | undefined {
  return modelCache.value.get(agentId);
}

function setModelCache(agentId: string, models: { id: string; name: string }[]) {
  modelCache.value = new Map(modelCache.value).set(agentId, models);
}

async function hydrateSessionMeta(): Promise<void> {
  // Group by {role}:{agentId} (ignoring optional session suffix)
  const byAgent = new Map<string, string[]>();
  for (const [panelKey, sessionId] of sessions.value) {
    const { role, agentId } = parsePanelKey(panelKey);
    const groupKey = `${role}:${agentId}`;
    const list = byAgent.get(groupKey) ?? [];
    list.push(sessionId);
    byAgent.set(groupKey, list);
  }

  for (const groupKey of byAgent.keys()) {
    const { role, agentId } = parsePanelKey(groupKey);
    try {
      const knownSessions = await fetchSessions(agentId, role, false);
      for (const [panelKey, sessionId] of sessions.value) {
        const pk = parsePanelKey(panelKey);
        if (pk.role !== role || pk.agentId !== agentId) continue;
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

/** Shared cleanup for session end/delete: removes prompt state and session mapping. */
function cleanupPanelState(panelKey: string, sessionId: string): void {
  const nextPromptState = new Map(sessionPromptState.value);
  nextPromptState.delete(sessionId);
  sessionPromptState.value = nextPromptState;
  clearSession(panelKey);
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

      // Main Agent uses stable panelKey (single instance); sub-agents use
      // session-unique keys so multiple dispatches to the same role+agent
      // create independent bookmarks and message streams.
      const panelKey = payload.role === "main"
        ? `${payload.role}:${event.agentId}`
        : `${payload.role}:${event.agentId}:${event.sessionId}`;

      // setSessionInfo internally calls setSession, which registers the
      // panelKey→sessionId mapping needed by resolvePanelKey in messages.ts.
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
      // Auto-create bookmark for sub-agent sessions
      if (payload.role !== "main") {
        addBookmark(panelKey);
      }
      return;
    }

    if (event.type === "agent.session.end" || event.type === "agent.session.delete") {
      for (const [panelKey, sessionId] of sessions.value) {
        if (sessionId !== event.sessionId) continue;

        cleanupPanelState(panelKey, sessionId);

        // For session.end on main roles, preserve the panel but reset to idle
        if (event.type === "agent.session.end") {
          const { role } = parsePanelKey(panelKey);
          if (role === "main") {
            setStatus(panelKey, "idle");
            break;
          }
        }

        // Full removal for delete events and non-main end events
        const nextStatuses = new Map(statuses.value);
        nextStatuses.delete(panelKey);
        statuses.value = nextStatuses;

        const nextWorkDirs = new Map(workDirs.value);
        nextWorkDirs.delete(panelKey);
        workDirs.value = nextWorkDirs;

        const nextBranches = new Map(gitBranches.value);
        nextBranches.delete(panelKey);
        gitBranches.value = nextBranches;

        removeBookmark(panelKey);
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
    // Bookmark Rail
    bookmarks,
    bookmarkList,
    addBookmark,
    removeBookmark,
    openFloatingTabs,
    openFloatingTab,
    closeFloatingTab,
    isSessionOpen,
    parsePanelKey,
    // Model cache
    modelCache,
    getModelCache,
    setModelCache,
  };
}
