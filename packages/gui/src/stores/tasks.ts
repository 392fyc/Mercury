/**
 * Task orchestration store — manages TaskBundle state for the dashboard.
 */

import { ref, computed } from "vue";
import type {
  TaskBundle,
  TaskStatus,
  MercuryEvent,
} from "../lib/tauri-bridge";
import { listTasks, getTask, onMercuryEvent } from "../lib/tauri-bridge";

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

async function loadTasks() {
  try {
    tasks.value = await listTasks();
  } catch (e) {
    console.error("Failed to load tasks:", e);
  }
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

async function initTaskListeners() {
  if (taskListenersInitialized) return;
  taskListenersInitialized = true;

  await onMercuryEvent((event: MercuryEvent) => {
    if (event.type.startsWith("orchestrator.task.") || event.type.startsWith("orchestrator.acceptance.")) {
      const taskId =
        (event.payload as Record<string, unknown>).taskId as string | undefined;
      if (taskId) {
        refreshTask(taskId);
      } else {
        loadTasks();
      }
    }
  });
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
  };
}
