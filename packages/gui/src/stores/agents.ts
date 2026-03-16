/**
 * Agent configuration and session state store.
 */

import { ref, computed } from "vue";
import type { AgentConfig } from "../lib/tauri-bridge";
import {
  getAgents as fetchAgents,
  getProjectInfo,
  onSidecarReady,
  onSidecarError,
} from "../lib/tauri-bridge";

const agents = ref<AgentConfig[]>([]);
const statuses = ref<Map<string, "idle" | "active" | "error">>(new Map());
const sessions = ref<Map<string, string>>(new Map()); // agentId → sessionId
const workDirs = ref<Map<string, string>>(new Map()); // agentId → cwd
const gitBranches = ref<Map<string, string | null>>(new Map()); // agentId → branch
const defaultWorkDir = ref("");
const sidecarReady = ref(false);
const sidecarError = ref<string | null>(null);

const mainAgent = computed(() => agents.value.find((a) => a.role === "main"));

const subAgents = computed(() =>
  agents.value.filter((a) => a.role !== "main"),
);

function setStatus(agentId: string, status: "idle" | "active" | "error") {
  statuses.value = new Map(statuses.value).set(agentId, status);
}

function getStatus(agentId: string): "idle" | "active" | "error" {
  return statuses.value.get(agentId) ?? "idle";
}

function setSession(agentId: string, sessionId: string) {
  sessions.value = new Map(sessions.value).set(agentId, sessionId);
}

function getSession(agentId: string): string | undefined {
  return sessions.value.get(agentId);
}

function clearSession(agentId: string) {
  const next = new Map(sessions.value);
  next.delete(agentId);
  sessions.value = next;
}

function setWorkDir(agentId: string, cwd: string) {
  workDirs.value = new Map(workDirs.value).set(agentId, cwd);
}

function getWorkDir(agentId: string): string {
  return workDirs.value.get(agentId) ?? defaultWorkDir.value;
}

function setGitBranch(agentId: string, branch: string | null) {
  gitBranches.value = new Map(gitBranches.value).set(agentId, branch);
}

function getGitBranch(agentId: string): string | null {
  return gitBranches.value.get(agentId) ?? null;
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
    for (const agent of agents.value) {
      statuses.value.set(agent.id, "idle");
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
