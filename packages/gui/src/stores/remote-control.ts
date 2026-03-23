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

/** Apply a backend RemoteControlState snapshot to the reactive store values.
 *  All status variants are now unit variants (plain strings), so no object
 *  parsing is needed. Error details arrive in the separate `error_message` field. */
function applyState(state: RemoteControlState) {
  const validStatuses: RemoteControlStatus[] = [
    "stopped", "starting", "waiting_for_connection", "connected", "error",
  ];
  status.value = validStatuses.includes(state.status as RemoteControlStatus)
    ? (state.status as RemoteControlStatus)
    : "stopped";
  if (state.status === "error" && state.error_message) {
    error.value = state.error_message;
  }
  sessionUrl.value = state.session_url;
  if (state.session_name) {
    sessionName.value = state.session_name;
  }
}

let listenersInitialized = false;

/** Register Tauri event listeners for remote control status, URL, and log updates.
 *  Returns an array of unlisten functions for cleanup on component unmount.
 *  The `listenersInitialized` flag is only set after ALL listeners succeed;
 *  on partial failure the already-registered listeners are cleaned up. */
async function initRemoteControlListeners(): Promise<Array<() => void>> {
  if (listenersInitialized) {
    console.warn("[remote-control] Listeners already initialized");
    return [];
  }

  const collected: Array<() => void> = [];
  try {
    const unlistenStatus = await onRemoteControlStatus((state) => {
      applyState(state);
    });
    collected.push(unlistenStatus);

    const unlistenUrl = await onRemoteControlUrl((data) => {
      sessionUrl.value = data.url;
    });
    collected.push(unlistenUrl);

    const unlistenLog = await onRemoteControlLog((data) => {
      logs.value = [...logs.value.slice(-99), `[${data.level}] ${data.message}`];
      if (data.level === "error") {
        error.value = data.message;
      }
    });
    collected.push(unlistenLog);
  } catch (e) {
    // Partial failure — clean up any listeners that were registered.
    collected.forEach((fn) => fn());
    console.error("[remote-control] Failed to init listeners:", e);
    return [];
  }

  // All listeners registered successfully — mark as initialised.
  listenersInitialized = true;

  // Fetch initial status
  await refreshStatus();

  return [
    () => { collected[0](); listenersInitialized = false; },
    ...collected.slice(1),
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
