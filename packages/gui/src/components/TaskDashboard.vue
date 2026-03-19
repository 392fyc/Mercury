<script setup lang="ts">
import { computed } from "vue";
import { useTaskStore } from "../stores/tasks";
import type { TaskStatus } from "../lib/tauri-bridge";
import { dispatchBundleTask, createAcceptance } from "../lib/tauri-bridge";
import { useAgentStore } from "../stores/agents";

const {
  filteredTasks,
  selectedTask,
  statusCounts,
  statusFilter,
  selectTask,
  setFilter,
  refreshTask,
} = useTaskStore();

const { agents } = useAgentStore();

const STATUS_LABELS: { status: TaskStatus | null; label: string; color: string }[] = [
  { status: null, label: "All", color: "var(--text-secondary)" },
  { status: "drafted", label: "Drafted", color: "var(--text-muted)" },
  { status: "dispatched", label: "Dispatched", color: "var(--accent-main)" },
  { status: "in_progress", label: "In Progress", color: "#22d3ee" },
  { status: "implementation_done", label: "Impl Done", color: "#a78bfa" },
  { status: "acceptance", label: "Acceptance", color: "#c084fc" },
  { status: "verified", label: "Verified", color: "var(--accent-success)" },
  { status: "closed", label: "Closed", color: "var(--accent-success)" },
  { status: "failed", label: "Failed", color: "var(--accent-error)" },
  { status: "blocked", label: "Blocked", color: "#fb923c" },
];

const STATUS_ORDER: Record<string, number> = {
  drafted: 0,
  dispatched: 1,
  blocked: 2,
  in_progress: 3,
  implementation_done: 4,
  main_review: 5,
  acceptance: 6,
  verified: 7,
  closed: 8,
  failed: 9,
};

/** Return the theme color associated with a task status. */
function statusColor(status: TaskStatus): string {
  return STATUS_LABELS.find((s) => s.status === status)?.color ?? "var(--text-muted)";
}

/** Convert a priority slug (e.g. "sev-1") to an uppercase display label. */
function priorityLabel(p: string): string {
  return p.toUpperCase();
}

/** Format an ISO date string for display; returns "-" for invalid dates. */
function formatDate(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString();
}

/** Total number of tasks across all statuses. */
const totalCount = computed(() =>
  Object.values(statusCounts.value).reduce((a, b) => a + (b ?? 0), 0),
);

/** Tasks sorted by status priority, then by most recent timestamp descending. */
const sortedTasks = computed(() =>
  [...filteredTasks.value].sort((a, b) => {
    const sa = STATUS_ORDER[a.status] ?? 99;
    const sb = STATUS_ORDER[b.status] ?? 99;
    if (sa !== sb) {
      return sa - sb;
    }

    const timeA = a.closedAt || a.failedAt || a.createdAt || "";
    const timeB = b.closedAt || b.failedAt || b.createdAt || "";
    return timeB.localeCompare(timeA);
  }),
);

/** Dispatch a drafted task and refresh its local state. */
async function handleDispatch(taskId: string) {
  try {
    await dispatchBundleTask(taskId);
    await refreshTask(taskId);
  } catch (e) {
    console.error("Dispatch failed:", e);
  }
}

/** Create an acceptance flow for a completed task, picking an acceptance-role agent. */
async function handleCreateAcceptance(taskId: string) {
  // Find an acceptance-role agent, or fall back to first available
  const acceptor = agents.value.find((a) => a.roles.includes("acceptance")) ?? agents.value[0];
  if (!acceptor) return;
  try {
    await createAcceptance(taskId, acceptor.id);
    await refreshTask(taskId);
  } catch (e) {
    console.error("Create acceptance failed:", e);
  }
}
</script>

<template>
  <div class="task-dashboard">
    <!-- Summary Bar -->
    <div class="summary-bar">
      <button
        v-for="s in STATUS_LABELS"
        :key="s.label"
        class="status-badge"
        :class="{ active: statusFilter === s.status }"
        @click="setFilter(s.status)"
      >
        <span class="badge-dot" :style="{ background: s.color }"></span>
        <span class="badge-label">{{ s.label }}</span>
        <span class="badge-count">{{
          s.status === null ? totalCount : (statusCounts[s.status] ?? 0)
        }}</span>
      </button>
    </div>

    <div class="dashboard-body">
      <!-- Task List -->
      <div class="task-list">
        <div
          v-for="task in sortedTasks"
          :key="task.taskId"
          class="task-row"
          :class="{ selected: selectedTask?.taskId === task.taskId }"
          @click="selectTask(task.taskId)"
        >
          <span class="task-status-dot" :style="{ background: statusColor(task.status) }"></span>
          <span class="task-id">{{ task.taskId }}</span>
          <span class="task-title">{{ task.title }}</span>
          <span class="task-assignee">{{ task.assignee?.agentId ?? task.assignedTo }}</span>
          <span class="task-priority" :class="task.priority">{{ priorityLabel(task.priority) }}</span>
        </div>

        <div v-if="sortedTasks.length === 0" class="empty-state">
          No tasks{{ statusFilter ? ` with status "${statusFilter}"` : "" }}
        </div>
      </div>

      <!-- Task Detail -->
      <div class="task-detail" v-if="selectedTask">
        <div class="detail-header">
          <h3>{{ selectedTask.title }}</h3>
          <span class="detail-id">{{ selectedTask.taskId }}</span>
        </div>

        <div class="detail-meta">
          <div class="meta-row">
            <span class="meta-label">Status</span>
            <span class="meta-value" :style="{ color: statusColor(selectedTask.status) }">
              {{ selectedTask.status }}
            </span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Priority</span>
            <span class="meta-value" :class="selectedTask.priority">
              {{ priorityLabel(selectedTask.priority) }}
            </span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Assignee</span>
            <span class="meta-value">
              {{ selectedTask.assignee?.agentId ?? selectedTask.assignedTo }}
              <span v-if="selectedTask.assignee?.model" class="model-tag">
                {{ selectedTask.assignee.model }}
              </span>
            </span>
          </div>
          <div v-if="selectedTask.closedAt" class="meta-row">
            <span class="meta-label">Closed</span>
            <span class="meta-value">{{ formatDate(selectedTask.closedAt) }}</span>
          </div>
          <div v-else-if="selectedTask.failedAt" class="meta-row">
            <span class="meta-label">Failed</span>
            <span class="meta-value">{{ formatDate(selectedTask.failedAt) }}</span>
          </div>
          <div class="meta-row" v-if="selectedTask.branch">
            <span class="meta-label">Branch</span>
            <span class="meta-value mono">{{ selectedTask.branch }}</span>
          </div>
          <div class="meta-row">
            <span class="meta-label">Rework</span>
            <span class="meta-value">{{ selectedTask.reworkCount }} / {{ selectedTask.maxReworks }}</span>
          </div>
        </div>

        <!-- Context -->
        <div class="detail-section" v-if="selectedTask.context">
          <div class="section-label">Context</div>
          <div class="section-body mono">{{ selectedTask.context }}</div>
        </div>

        <!-- Definition of Done -->
        <div class="detail-section" v-if="selectedTask.definitionOfDone.length">
          <div class="section-label">Definition of Done</div>
          <ul class="dod-list">
            <li v-for="(item, i) in selectedTask.definitionOfDone" :key="i">{{ item }}</li>
          </ul>
        </div>

        <!-- Implementation Receipt -->
        <div class="detail-section" v-if="selectedTask.implementationReceipt">
          <div class="section-label">Implementation Receipt</div>
          <pre class="json-block">{{ JSON.stringify(selectedTask.implementationReceipt, null, 2) }}</pre>
        </div>

        <!-- Actions -->
        <div class="detail-actions">
          <button
            v-if="selectedTask.status === 'drafted'"
            class="action-btn dispatch"
            @click="handleDispatch(selectedTask.taskId)"
          >
            Dispatch
          </button>
          <button
            v-if="selectedTask.status === 'implementation_done'"
            class="action-btn acceptance"
            @click="handleCreateAcceptance(selectedTask.taskId)"
          >
            Create Acceptance
          </button>
        </div>
      </div>

      <!-- No selection -->
      <div class="task-detail empty-detail" v-else>
        <p>Select a task to view details</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.task-dashboard {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  height: 100%;
}

.summary-bar {
  display: flex;
  gap: 4px;
  padding: 6px 8px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  flex-wrap: wrap;
}

.status-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 12px;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 11px;
  cursor: pointer;
  font-family: var(--font-mono);
}

.status-badge:hover {
  background: var(--bg-panel);
}

.status-badge.active {
  background: var(--bg-panel);
  border-color: var(--accent-main);
  color: var(--text-primary);
}

.badge-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.badge-count {
  color: var(--text-muted);
}

.dashboard-body {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 8px;
  min-height: 0;
  flex: 1;
}

.task-list {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow-y: auto;
  padding: 4px;
}

.task-row {
  display: grid;
  grid-template-columns: 10px 90px 1fr auto auto;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 12px;
}

.task-row:hover {
  background: var(--bg-panel);
}

.task-row.selected {
  background: var(--bg-panel);
  border: 1px solid var(--accent-main);
}

.task-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.task-id {
  font-family: var(--font-mono);
  color: var(--text-muted);
  font-size: 11px;
}

.task-title {
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-assignee {
  color: var(--text-secondary);
  font-size: 11px;
}

.task-priority {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 3px;
}

.task-priority.sev-0 { background: rgba(255, 82, 82, 0.2); color: var(--accent-error); }
.task-priority.sev-1 { background: rgba(251, 146, 60, 0.2); color: #fb923c; }
.task-priority.sev-2 { background: rgba(250, 204, 21, 0.2); color: #facc15; }
.task-priority.sev-3 { background: rgba(148, 163, 184, 0.2); color: var(--text-muted); }

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  font-size: 13px;
}

/* Detail Panel */
.task-detail {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow-y: auto;
  padding: 12px;
}

.empty-detail {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 12px;
}

.detail-header h3 {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.detail-id {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
}

.detail-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 12px;
}

.meta-row {
  display: flex;
  gap: 8px;
  font-size: 12px;
}

.meta-label {
  color: var(--text-muted);
  min-width: 70px;
}

.meta-value {
  color: var(--text-primary);
}

.meta-value.mono,
.model-tag {
  font-family: var(--font-mono);
}

.model-tag {
  font-size: 10px;
  padding: 0 4px;
  border-radius: 3px;
  background: var(--bg-panel);
  color: var(--text-secondary);
  margin-left: 4px;
}

.detail-section {
  margin-bottom: 10px;
}

.section-label {
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 4px;
}

.section-body {
  font-size: 12px;
  color: var(--text-secondary);
  line-height: 1.5;
  white-space: pre-wrap;
}

.section-body.mono {
  font-family: var(--font-mono);
}

.dod-list {
  margin: 0;
  padding-left: 16px;
  font-size: 12px;
  color: var(--text-secondary);
}

.dod-list li {
  margin-bottom: 2px;
}

.json-block {
  font-family: var(--font-mono);
  font-size: 11px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 8px;
  overflow-x: auto;
  color: var(--text-secondary);
  margin: 0;
  white-space: pre;
}

.detail-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

.action-btn {
  padding: 6px 16px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  font-size: 12px;
  cursor: pointer;
  font-family: var(--font-mono);
}

.action-btn.dispatch {
  background: rgba(34, 211, 238, 0.15);
  color: #22d3ee;
  border-color: rgba(34, 211, 238, 0.3);
}

.action-btn.dispatch:hover {
  background: rgba(34, 211, 238, 0.25);
}

.action-btn.acceptance {
  background: rgba(192, 132, 252, 0.15);
  color: #c084fc;
  border-color: rgba(192, 132, 252, 0.3);
}

.action-btn.acceptance:hover {
  background: rgba(192, 132, 252, 0.25);
}
</style>
