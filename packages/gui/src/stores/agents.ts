/**
 * Agent configuration and session state store.
 */

import { ref, computed } from "vue";
import type { AgentConfig } from "../lib/tauri-bridge";
import {
  getAgents as fetchAgents,
  onSidecarReady,
  onSidecarError,
} from "../lib/tauri-bridge";

const agents = ref<AgentConfig[]>([]);
const statuses = ref<Map<string, "idle" | "active" | "error">>(new Map());
const sessions = ref<Map<string, string>>(new Map()); // agentId → sessionId
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

async function initAgents() {
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
    sessions,
    initAgents,
  };
}
