/**
 * Agent configuration and session state store.
 *
 * All per-panel state (status, sessions, workDir, gitBranch) is keyed by
 * panelKey (roleSlotKey = "{role}:{agentId}"), NOT by raw agentId.
 */

import { ref, computed } from "vue";
import type { AgentConfig } from "../lib/tauri-bridge";
import {
  getAgents as fetchAgents,
  getProjectInfo,
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

const agents = ref<AgentConfig[]>([]);
const statuses = ref<Map<string, "idle" | "active" | "error">>(new Map()); // panelKey → status
const sessions = ref<Map<string, string>>(new Map()); // panelKey → sessionId
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

function setSession(panelKey: string, sessionId: string) {
  sessions.value = new Map(sessions.value).set(panelKey, sessionId);
}

function getSession(panelKey: string): string | undefined {
  return sessions.value.get(panelKey);
}

function clearSession(panelKey: string) {
  const next = new Map(sessions.value);
  next.delete(panelKey);
  sessions.value = next;
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

async function loadAgents() {
  try {
    agents.value = await fetchAgents();
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
  } catch (e) {
    console.error("Failed to fetch agents:", e);
  }
}

let agentListenersInitialized = false;

async function initAgents() {
  if (agentListenersInitialized) return;
  agentListenersInitialized = true;

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
    getSession,
    clearSession,
    sessions,
    setWorkDir,
    getWorkDir,
    setGitBranch,
    getGitBranch,
    defaultWorkDir,
    initAgents,
  };
}
