/**
 * Agent configuration and session state store.
 *
 * All per-panel state (status, sessions, workDir, gitBranch) is keyed by
 * panelKey (roleSlotKey = "{role}:{agentId}"), NOT by raw agentId.
 */

import { ref, computed, shallowRef, triggerRef } from "vue";
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
  role: "main" | "dev" | "acceptance" | "critic" | "research" | "design";
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
  role: "main" | "dev" | "acceptance" | "critic" | "research" | "design";
  displayName: string;
  sessionName?: string;
  status: "idle" | "active" | "error";
  lastActiveAt: number;
}

const SESSIONS_STORAGE_KEY = "mercury:sessions";

const agents = ref<AgentConfig[]>([]);
const statuses = shallowRef<Map<string, "idle" | "active" | "error">>(new Map()); // panelKey → status
const sessions = shallowRef<Map<string, string>>(new Map()); // panelKey → sessionId
const sessionMeta = shallowRef<Map<string, SessionMeta>>(new Map()); // panelKey → session metadata
const sessionPromptState = shallowRef<Map<string, SessionPromptState>>(new Map()); // sessionId → prompt metadata
const workDirs = shallowRef<Map<string, string>>(new Map()); // panelKey → cwd
const gitBranches = shallowRef<Map<string, string | null>>(new Map()); // panelKey → branch
const defaultWorkDir = ref("");
const sidecarReady = ref(false);
const sidecarError = ref<string | null>(null);

// ─── Bookmark Rail State ───
/** Manually created sub-agent bookmarks (panelKey → true). Auto-created when session starts. */
const bookmarks = shallowRef<Map<string, boolean>>(new Map());
/** Which panelKeys are currently open as floating tabs */
const openFloatingTabs = ref<string[]>([]);
/** Model cache: agentId → last fetched model list */
const modelCache = shallowRef<Map<string, { id: string; name: string }[]>>(new Map());

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
  statuses.value.set(panelKey, status);
  triggerRef(statuses);
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
        bookmarks.value.set(panelKey, true);
      }
    }
    triggerRef(bookmarks);
  } catch {
    // Corrupted — start fresh
  }
}

function setSession(panelKey: string, sessionId: string) {
  sessions.value.set(panelKey, sessionId);
  triggerRef(sessions);
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
  // Only patch explicitly-returned fields — omit undefined to preserve cache
  const defined = Object.fromEntries(
    Object.entries(nextState).filter(([, v]) => v !== undefined),
  );
  sessionPromptState.value.set(sessionId, {
    ...existing,
    ...defined,
  });
  triggerRef(sessionPromptState);
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
  sessionMeta.value.set(panelKey, merged);
  triggerRef(sessionMeta);
}

function getSession(panelKey: string): string | undefined {
  return sessions.value.get(panelKey);
}

function getSessionInfo(panelKey: string): SessionMeta | undefined {
  return sessionMeta.value.get(panelKey);
}

function clearSession(panelKey: string, skipPersist = false) {
  sessions.value.delete(panelKey);
  triggerRef(sessions);
  sessionMeta.value.delete(panelKey);
  triggerRef(sessionMeta);
  if (!skipPersist) saveSessions();
}

function setWorkDir(panelKey: string, cwd: string) {
  workDirs.value.set(panelKey, cwd);
  triggerRef(workDirs);
}

function getWorkDir(panelKey: string): string {
  return workDirs.value.get(panelKey) ?? defaultWorkDir.value;
}

function setGitBranch(panelKey: string, branch: string | null) {
  gitBranches.value.set(panelKey, branch);
  triggerRef(gitBranches);
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
type AgentRole = "main" | "dev" | "acceptance" | "critic" | "research" | "design";
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
  bookmarks.value.set(panelKey, true);
  triggerRef(bookmarks);
}

function removeBookmark(panelKey: string) {
  bookmarks.value.delete(panelKey);
  triggerRef(bookmarks);
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
  modelCache.value.set(agentId, models);
  triggerRef(modelCache);
}

async function hydrateSessionMeta(): Promise<void> {
  // Group by {role}:{agentId} — snapshot panelKey→sessionId BEFORE any await
  // to prevent TOCTOU: sessions arriving during fetchSessions won't be
  // mistakenly pruned as stale.
  const byAgent = new Map<string, [string, string][]>();
  for (const [panelKey, sessionId] of sessions.value) {
    const { role, agentId } = parsePanelKey(panelKey);
    const groupKey = `${role}:${agentId}`;
    const list = byAgent.get(groupKey) ?? [];
    list.push([panelKey, sessionId]);
    byAgent.set(groupKey, list);
  }

  for (const [groupKey, groupEntries] of byAgent) {
    const { role, agentId } = parsePanelKey(groupKey);
    try {
      const knownSessions = await fetchSessions(agentId, role, false);
      // O(1) lookup instead of O(n) find per entry
      const knownById = new Map(
        knownSessions.map((s) => [s.sessionId, s] as const),
      );

      let pruned = false;
      // Track which sessionIds have been assigned a canonical panelKey.
      // Prefer session-unique keys (3 segments) over legacy keys (2 segments).
      const seenSessionIds = new Map<string, string>();

      for (const [panelKey, sessionId] of groupEntries) {
        // Guard: verify snapshot is still current — a concurrent session.start
        // during the await may have re-bound this panelKey to a different session.
        if (sessions.value.get(panelKey) !== sessionId) continue;

        const match = knownById.get(sessionId);
        if (!match) {
          // Stale session in localStorage that backend no longer knows — prune it.
          // Skip per-item persist; we batch-save after the loop.
          cleanupPanelState(panelKey, sessionId, true);
          statuses.value.delete(panelKey);
          workDirs.value.delete(panelKey);
          gitBranches.value.delete(panelKey);
          removeBookmark(panelKey);
          pruned = true;
          continue;
        }

        // Deduplicate: if another panelKey already claimed this sessionId,
        // keep the one with more segments (new format {role}:{agentId}:{sid}).
        const existingKey = seenSessionIds.get(sessionId);
        if (existingKey) {
          const existingSegments = existingKey.split(":").length;
          const currentSegments = panelKey.split(":").length;
          const keyToRemove = currentSegments > existingSegments ? existingKey : panelKey;
          const keyToKeep = currentSegments > existingSegments ? panelKey : existingKey;
          // Re-check that the key-to-remove hasn't been re-bound during await
          if (sessions.value.get(keyToRemove) === sessionId) {
            clearSession(keyToRemove, true);
            statuses.value.delete(keyToRemove);
            workDirs.value.delete(keyToRemove);
            gitBranches.value.delete(keyToRemove);
            removeBookmark(keyToRemove);
          }
          seenSessionIds.set(sessionId, keyToKeep);
          pruned = true;
          if (keyToRemove === panelKey) continue;
        } else {
          seenSessionIds.set(sessionId, panelKey);
        }

        setSessionInfo(panelKey, {
          sessionId: match.sessionId,
          sessionName: match.sessionName,
          status: match.status,
          lastActiveAt: match.lastActiveAt,
          promptHash: match.promptHash,
          currentPromptHash: match.currentPromptHash,
          legacyRoleConfig: match.legacyRoleConfig,
        });
      }
      if (pruned) {
        triggerRef(statuses);
        triggerRef(workDirs);
        triggerRef(gitBranches);
        saveSessions();
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
    triggerRef(statuses);
    await hydrateSessionMeta();
  } catch (e) {
    console.error("Failed to fetch agents:", e);
  }
}

/** Shared cleanup for session end/delete: removes prompt state and session mapping. */
function cleanupPanelState(panelKey: string, sessionId: string, skipPersist = false): void {
  sessionPromptState.value.delete(sessionId);
  triggerRef(sessionPromptState);
  clearSession(panelKey, skipPersist);
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

      // Deduplicate: collect ALL existing panelKeys that map to this sessionId
      // (e.g. legacy keys restored from localStorage). Snapshot first to avoid
      // mutating sessions.value while iterating.
      // Preserve sessionName from old panel before clearing, in case the event
      // doesn't carry one — prevents sessions losing their human-readable name.
      let preservedSessionName: string | undefined;
      if (payload.role !== "main") {
        const duplicateKeys: string[] = [];
        for (const [existingKey, existingSid] of sessions.value) {
          if (existingSid === event.sessionId && existingKey !== panelKey) {
            duplicateKeys.push(existingKey);
          }
        }
        // Read sessionName BEFORE clearing metadata
        for (const dupKey of duplicateKeys) {
          if (!preservedSessionName) {
            preservedSessionName = sessionMeta.value.get(dupKey)?.sessionName;
          }
          clearSession(dupKey);
          statuses.value.delete(dupKey);
          workDirs.value.delete(dupKey);
          gitBranches.value.delete(dupKey);
          removeBookmark(dupKey);
        }
        if (duplicateKeys.length) {
          triggerRef(statuses);
          triggerRef(workDirs);
          triggerRef(gitBranches);
        }
      }

      // setSessionInfo internally calls setSession, which registers the
      // panelKey→sessionId mapping needed by resolvePanelKey in messages.ts.
      setSessionInfo(panelKey, {
        sessionId: event.sessionId,
        sessionName: payload.sessionName ?? preservedSessionName,
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
        statuses.value.delete(panelKey);
        triggerRef(statuses);

        workDirs.value.delete(panelKey);
        triggerRef(workDirs);

        gitBranches.value.delete(panelKey);
        triggerRef(gitBranches);

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
        sessionMeta.value.set(panelKey, {
          ...info,
          lastActiveAt: event.timestamp,
          status: "active",
        });
        triggerRef(sessionMeta);
        break;
      }
    }
  });

  // Fallback: if ready event was missed (race), poll until sidecar responds
  // Uses exponential backoff to avoid hammering a slow sidecar
  let pollDelay = 500;
  const MAX_POLL_DELAY = 5000;
  const pollFn = async () => {
    if (sidecarReady.value) return;
    try {
      await loadAgents();
    } catch {
      pollDelay = Math.min(pollDelay * 2, MAX_POLL_DELAY);
      setTimeout(pollFn, pollDelay);
    }
  };
  setTimeout(pollFn, pollDelay);
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
