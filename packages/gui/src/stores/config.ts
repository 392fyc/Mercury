/**
 * Project configuration store.
 * Loads/saves mercury.config.json via sidecar RPC.
 */

import { ref, computed } from "vue";
import type { MercuryProjectConfig, ObsidianConfig, AgentConfig } from "../lib/tauri-bridge";
import { getConfig, updateConfig } from "../lib/tauri-bridge";

const config = ref<MercuryProjectConfig | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);

const agents = computed(() => config.value?.agents ?? []);
const obsidian = computed(() => config.value?.obsidian ?? null);
const kbEnabled = computed(() => config.value?.obsidian?.enabled ?? false);

async function loadConfig() {
  loading.value = true;
  error.value = null;
  try {
    config.value = await getConfig();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function saveConfig(updated: MercuryProjectConfig) {
  error.value = null;
  try {
    await updateConfig(updated);
    config.value = updated;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    throw e;
  }
}

async function updateAgents(newAgents: AgentConfig[]) {
  if (!config.value) return;
  await saveConfig({ ...config.value, agents: newAgents });
}

async function updateObsidian(obs: ObsidianConfig) {
  if (!config.value) return;
  await saveConfig({ ...config.value, obsidian: obs });
}

export function useConfigStore() {
  return {
    config,
    loading,
    error,
    agents,
    obsidian,
    kbEnabled,
    loadConfig,
    saveConfig,
    updateAgents,
    updateObsidian,
  };
}
