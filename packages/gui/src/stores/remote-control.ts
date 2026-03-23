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

/** Start a remote control session, spawning the `claude remote-control` subprocess. */
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

/** Stop the running remote control subprocess and reset state. */
async function stop() {
  try {
    await invokeStop();
    status.value = "stopped";
    sessionUrl.value = null;
  } catch (e) {
    error.value = String(e);
    status.value = "error";
  }
}

/** Fetch the current remote control status from the Tauri backend. */
async function refreshStatus() {
  try {
    const state = await invokeGetStatus();
    /** Sync reactive store with fetched backend state. */
    applyState(state);
  } catch {
    // Ignore — sidecar may not be ready yet
  }
}

/** Apply a backend RemoteControlState snapshot to the reactive store values. */
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

let listenersInitialized = false;

/** Register Tauri event listeners for remote control status, URL, and log updates.
 *  Returns an array of unlisten functions for cleanup on component unmount. */
async function initRemoteControlListeners(): Promise<Array<() => void>> {
  if (listenersInitialized) {
    console.warn("[remote-control] Listeners already initialized");
    return [];
  }
  listenersInitialized = true;

  const unlistenStatus = await onRemoteControlStatus((state) => {
    /** Update store from status event. */
    applyState(state);
  });

  const unlistenUrl = await onRemoteControlUrl((data) => {
    sessionUrl.value = data.url;
  });

  const unlistenLog = await onRemoteControlLog((data) => {
    logs.value = [...logs.value.slice(-99), `[${data.level}] ${data.message}`];
    if (data.level === "error") {
      error.value = data.message;
    }
  });

  // Fetch initial status
  await refreshStatus();

  return [
    () => { unlistenStatus(); listenersInitialized = false; },
    unlistenUrl,
    unlistenLog,
  ];
}

/** Composable that exposes remote control reactive state and actions. */
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
