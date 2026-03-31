/**
 * Task orchestration store — manages TaskBundle state for the dashboard.
 */

import { ref, computed } from "vue";
import type {
  TaskBundle,
  TaskStatus,
  MercuryEvent,
} from "../lib/tauri-bridge";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { listTasks, getTask, onMercuryEvent, onSidecarReady } from "../lib/tauri-bridge";
import { useAgentStore } from "./agents";

const tasks = ref<TaskBundle[]>([]);
const selectedTaskId = ref<string | null>(null);
const statusFilter = ref<TaskStatus | null>(null);

const filteredTasks = computed(() => {
  if (!statusFilter.value) return tasks.value;
  return tasks.value.filter((t) => t.status === statusFilter.value);
});

const selectedTask = computed(() =>
  tasks.value.find((t) => t.taskId === selectedTaskId.value) ?? null,
);

const statusCounts = computed(() => {
  const counts: Partial<Record<TaskStatus, number>> = {};
  for (const t of tasks.value) {
    counts[t.status] = (counts[t.status] ?? 0) + 1;
  }
  return counts;
});

function selectTask(taskId: string | null) {
  selectedTaskId.value = taskId;
}

function setFilter(status: TaskStatus | null) {
  statusFilter.value = status;
}

let loadTasksInflight: Promise<void> | null = null;

async function loadTasks() {
  if (loadTasksInflight) return loadTasksInflight;
  loadTasksInflight = (async () => {
    try {
      tasks.value = await listTasks();
    } catch (e) {
      console.error("Failed to load tasks:", e);
      throw e; // re-throw so callers (e.g. handleRefresh) can catch
    } finally {
      loadTasksInflight = null;
    }
  })();
  return loadTasksInflight;
}

/** Refresh a single task by ID (e.g. after event notification). */
async function refreshTask(taskId: string) {
  try {
    const updated = await getTask(taskId);
    if (!updated) return;
    const idx = tasks.value.findIndex((t) => t.taskId === taskId);
    if (idx >= 0) {
      tasks.value = [...tasks.value.slice(0, idx), updated, ...tasks.value.slice(idx + 1)];
    } else {
      tasks.value = [...tasks.value, updated];
    }
  } catch {
    // Fallback: full reload
    await loadTasks();
  }
}

let taskListenersInitialized = false;
const taskUnlisteners: UnlistenFn[] = [];
let taskListenersInitPromise: Promise<void> | null = null;

async function initTaskListeners() {
  if (taskListenersInitialized) return;
  if (taskListenersInitPromise) return taskListenersInitPromise;

  taskListenersInitPromise = (async () => {
    const { waitForSidecarReady } = useAgentStore();
    const pending: UnlistenFn[] = [];
    // Accumulators for events that arrive while the initial snapshot is in-flight.
    // Without these, refreshTask() writes could be clobbered by the subsequent
    // tasks.value = await listTasks() assignment.
    const pendingRefreshIds = new Set<string>();
    let pendingFullReload = false;
    let snapshotInflight = false;
    try {
      // Register all listeners before taking the initial snapshot so events
      // arriving during snapshotting are not lost.

      // Reload tasks whenever sidecar becomes ready (handles F5 page refresh where
      // sidecar may not be available yet when onMounted fires).
      pending.push(await onSidecarReady(() => loadTasks()));

      pending.push(await onMercuryEvent((event: MercuryEvent) => {
        if (event.type.startsWith("orchestrator.task.") || event.type.startsWith("orchestrator.acceptance.")) {
          const taskId =
            (event.payload as Record<string, unknown>).taskId as string | undefined;
          if (snapshotInflight) {
            // Queue for replay after the snapshot completes to avoid clobbering.
            if (taskId) { pendingRefreshIds.add(taskId); } else { pendingFullReload = true; }
          } else {
            if (taskId) { refreshTask(taskId); } else { loadTasks(); }
          }
        }
      }));

      // Listeners are active — now take the initial snapshot.
      // If the ready event was emitted before we registered the listener above,
      // wait on the shared sidecar-ready state that agents.ts keeps in sync.
      snapshotInflight = true;
      await waitForSidecarReady();
      await loadTasks();
      snapshotInflight = false;

      // Replay any events that arrived during the snapshot to avoid lost updates.
      if (pendingFullReload) {
        await loadTasks();
      } else {
        for (const id of pendingRefreshIds) refreshTask(id);
      }
      pendingRefreshIds.clear();
      pendingFullReload = false;

      // All listeners registered and snapshot complete — commit
      taskListenersInitialized = true;
      taskUnlisteners.push(...pending);
    } catch (e) {
      // Rollback: unregister any listeners that were successfully created
      for (const unlisten of pending) unlisten();
      taskListenersInitPromise = null;
      console.error("Failed to init task listeners:", e);
      throw e;
    }
  })();

  return taskListenersInitPromise;
}

/** Teardown all task event listeners — useful for tests and HMR cleanup. */
function disposeTaskListeners() {
  for (const unlisten of taskUnlisteners) unlisten();
  taskUnlisteners.length = 0;
  taskListenersInitialized = false;
  taskListenersInitPromise = null;
}

export function useTaskStore() {
  return {
    tasks,
    filteredTasks,
    selectedTaskId,
    selectedTask,
    statusFilter,
    statusCounts,
    selectTask,
    setFilter,
    loadTasks,
    refreshTask,
    initTaskListeners,
    disposeTaskListeners,
  };
}
