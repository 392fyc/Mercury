import { ref, computed } from "vue";
import {
  getOpenPrs,
  getPrMonitorState,
  startPrPolling,
  stopPrPolling,
  triggerCoderabbitReview,
  mergePr,
  onPrListUpdated,
  type PullRequest,
} from "../lib/tauri-bridge";

const prs = ref<PullRequest[]>([]);
const polling = ref(false);
const intervalSecs = ref(60);
const lastError = ref<string | null>(null);
const lastFetchedAt = ref<string | null>(null);
const loading = ref(false);

let listenersInitialized = false;

const alertCount = computed(() => prs.value.filter((pr) => pr.timeout_alert).length);
const hasAlerts = computed(() => alertCount.value > 0);

/** Convert Unix-ms string from backend to ISO display string. */
function msToIso(ms: string): string {
  const num = Number(ms);
  return Number.isFinite(num) ? new Date(num).toISOString() : ms;
}

async function fetchPrs() {
  loading.value = true;
  lastError.value = null;
  try {
    const result = await getOpenPrs();
    prs.value = result.prs;
    lastFetchedAt.value = new Date().toISOString();
  } catch (e) {
    lastError.value = String(e);
  } finally {
    loading.value = false;
  }
}

async function startPolling(interval?: number) {
  lastError.value = null;
  try {
    await startPrPolling(interval);
    polling.value = true;
    if (interval) intervalSecs.value = interval;
  } catch (e) {
    const msg = String(e);
    if (msg.includes("Polling already active")) {
      polling.value = true;
    } else {
      lastError.value = msg;
    }
  }
}

async function stopPollingAction() {
  try {
    await stopPrPolling();
    polling.value = false;
  } catch (e) {
    lastError.value = String(e);
  }
}

async function requestCoderabbitReview(prNumber: number) {
  try {
    await triggerCoderabbitReview(prNumber);
  } catch (e) {
    lastError.value = String(e);
    throw e;
  }
}

async function requestMerge(prNumber: number) {
  try {
    await mergePr(prNumber);
    // Refresh after merge
    await fetchPrs();
  } catch (e) {
    lastError.value = String(e);
    throw e;
  }
}

async function syncState() {
  try {
    const state = await getPrMonitorState();
    prs.value = state.prs;
    polling.value = state.polling;
    intervalSecs.value = state.interval_secs;
    lastError.value = state.last_error;
    lastFetchedAt.value = state.last_fetched_at ? msToIso(state.last_fetched_at) : null;
  } catch (e) {
    lastError.value = String(e);
  }
}

async function initPrMonitorListeners(): Promise<Array<() => void>> {
  if (listenersInitialized) return [];

  const collected: Array<() => void> = [];
  try {
    const unlisten = await onPrListUpdated((data) => {
      prs.value = data.prs;
      lastFetchedAt.value = msToIso(data.timestamp);
      lastError.value = null;
    });
    collected.push(unlisten);
  } catch (e) {
    collected.forEach((fn) => fn());
    return [];
  }
  listenersInitialized = true;
  return collected;
}

/** Reset listener flag so listeners can be re-registered on next mount. */
function resetListeners() {
  listenersInitialized = false;
}

export function usePrMonitorStore() {
  return {
    prs,
    polling,
    intervalSecs,
    lastError,
    lastFetchedAt,
    loading,
    alertCount,
    hasAlerts,
    fetchPrs,
    startPolling,
    stopPolling: stopPollingAction,
    requestCoderabbitReview,
    requestMerge,
    syncState,
    initPrMonitorListeners,
    resetListeners,
  };
}
