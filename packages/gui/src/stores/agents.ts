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

const anyActive = computed(() =>
  [...statuses.value.values()].some((s) => s === "active"),
);

const anyError = computed(() =>
  [...statuses.value.values()].some((s) => s === "error"),
);

async function initAgents() {
  // Listen for sidecar ready
  await onSidecarReady(async (data) => {
    sidecarReady.value = true;
    sidecarError.value = null;
    try {
      agents.value = await fetchAgents();
      for (const agent of agents.value) {
        statuses.value.set(agent.id, "idle");
      }
    } catch (e) {
      console.error("Failed to fetch agents:", e);
    }
  });

  await onSidecarError((data) => {
    sidecarError.value = data.error;
  });
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
    initAgents,
  };
}
