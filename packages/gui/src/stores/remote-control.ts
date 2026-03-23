/**
 * Remote Control state store.
 * Manages the lifecycle of a `claude remote-control` subprocess
 * and exposes reactive state for the GUI.
 */

import { ref, computed } from "vue";
import type { RemoteControlState, RemoteControlStatus } from "../lib/tauri-bridge";
import {
  startRemoteControl as invokeStart,
  stopRemoteControl as invokeStop,
  getRemoteControlStatus as invokeGetStatus,
  onRemoteControlStatus,
  onRemoteControlUrl,
  onRemoteControlLog,
} from "../lib/tauri-bridge";

const status = ref<RemoteControlStatus>("stopped");
const sessionUrl = ref<string | null>(null);
const sessionName = ref<string | null>(null);
const error = ref<string | null>(null);
const logs = ref<string[]>([]);

const isRunning = computed(() =>
  status.value !== "stopped" && status.value !== "error",
);

const isConnected = computed(() => status.value === "connected");

async function start(name?: string) {
  error.value = null;
  logs.value = [];
  sessionName.value = name ?? "Mercury";
  try {
    await invokeStart(sessionName.value);
  } catch (e) {
    error.value = String(e);
    status.value = "error";
  }
}

async function stop() {
  try {
    await invokeStop();
  } catch (e) {
    error.value = String(e);
  }
  status.value = "stopped";
  sessionUrl.value = null;
}

async function refreshStatus() {
  try {
    const state = await invokeGetStatus();
    applyState(state);
  } catch {
    // Ignore — sidecar may not be ready yet
  }
}

function applyState(state: RemoteControlState) {
  // Map snake_case enum variants to our TS type
  const statusMap: Record<string, RemoteControlStatus> = {
    stopped: "stopped",
    starting: "starting",
    waiting_for_connection: "waiting_for_connection",
    connected: "connected",
  };
  if (typeof state.status === "string") {
    status.value = statusMap[state.status] ?? "stopped";
  } else if (typeof state.status === "object" && state.status !== null) {
    // Rust enum serialization: { "error": "message" }
    const errObj = state.status as Record<string, string>;
    if ("error" in errObj) {
      status.value = "error";
      error.value = errObj.error;
    }
  }
  sessionUrl.value = state.session_url;
  if (state.session_name) {
    sessionName.value = state.session_name;
  }
}

async function initRemoteControlListeners() {
  await onRemoteControlStatus((state) => {
    applyState(state);
  });

  await onRemoteControlUrl((data) => {
    sessionUrl.value = data.url;
  });

  await onRemoteControlLog((data) => {
    logs.value = [...logs.value.slice(-99), `[${data.level}] ${data.message}`];
    if (data.level === "error") {
      error.value = data.message;
    }
  });

  // Fetch initial status
  await refreshStatus();
}

export function useRemoteControlStore() {
  return {
    status,
    sessionUrl,
    sessionName,
    error,
    logs,
    isRunning,
    isConnected,
    start,
    stop,
    refreshStatus,
    initRemoteControlListeners,
  };
}
