<script setup lang="ts">
import { computed, ref } from "vue";
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
  loadTasks,
} = useTaskStore();

/** Role filter — null means "all roles". */
const roleFilter = ref<string | null>(null);

const ROLE_LABELS: { role: string | null; label: string; color: string }[] = [
  { role: null, label: "All Roles", color: "var(--text-secondary)" },
  { role: "dev", label: "Dev", color: "var(--accent-sub)" },
  { role: "research", label: "Research", color: "var(--accent-success)" },
  { role: "design", label: "Design", color: "var(--accent-info, #a78bfa)" },
];

const isRefreshing = ref(false);
const refreshError = ref(false);

async function handleRefresh() {
  if (isRefreshing.value) return;
  isRefreshing.value = true;
  refreshError.value = false;
  try {
    await loadTasks();
  } catch (e) {
    console.error("Task refresh failed:", e);
    refreshError.value = true;
    setTimeout(() => (refreshError.value = false), 3000);
  } finally {
    isRefreshing.value = false;
  }
}

const { agents } = useAgentStore();

/** Status filter buttons with display label and theme color. */
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

/** Sort priority for task statuses (lower = earlier in list). */
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

const KNOWN_PRIORITIES = new Set(["P0", "P1", "P2", "P3"]);

/** Convert a priority slug (e.g. "P1") to an uppercase display label. */
function priorityLabel(p: string | undefined | null): string {
  if (!p) return "-";
  const normalized = p.toUpperCase();
  return KNOWN_PRIORITIES.has(normalized) ? normalized : "-";
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

/** Tasks filtered by role, then sorted by status priority and timestamp. */
const roleFilteredTasks = computed(() => {
  if (!roleFilter.value) return filteredTasks.value;
  return filteredTasks.value.filter(
    (t) => (t.role ?? "dev") === roleFilter.value,
  );
});

const sortedTasks = computed(() =>
  [...roleFilteredTasks.value].sort((a, b) => {
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

/** Return the color for a task role. */
function roleColor(role: string | undefined): string {
  return ROLE_LABELS.find((r) => r.role === (role ?? "dev"))?.color ?? "var(--text-muted)";
}

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

      <button
        class="refresh-btn"
        :class="{ spinning: isRefreshing, 'refresh-error': refreshError }"
        :disabled="isRefreshing"
        :title="refreshError ? 'Refresh failed — click to retry' : 'Reload tasks'"
        @click="handleRefresh"
      >
        {{ refreshError ? '✕' : '↻' }}
      </button>
    </div>

    <!-- Role Filter -->
    <div class="role-bar">
      <button
        v-for="r in ROLE_LABELS"
        :key="r.label"
        class="role-badge"
        :class="{ active: roleFilter === r.role }"
        @click="roleFilter = r.role"
      >
        <span class="badge-dot" :style="{ background: r.color }"></span>
        <span class="badge-label">{{ r.label }}</span>
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
          <span class="task-role" :style="{ color: roleColor(task.role) }">{{ task.role ?? 'dev' }}</span>
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
            <span class="meta-label">Role</span>
            <span class="meta-value" :style="{ color: roleColor(selectedTask.role) }">
              {{ selectedTask.role ?? 'dev' }}
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
          <div v-if="selectedTask.createdAt" class="meta-row">
            <span class="meta-label">Created</span>
            <span class="meta-value">{{ formatDate(selectedTask.createdAt) }}</span>
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
  gap: 0;
  min-height: 0;
  height: 100%;
}

.summary-bar {
  display: flex;
  gap: 4px;
  padding: 6px 8px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius) var(--radius) 0 0;
  flex-wrap: wrap;
  align-items: center;
}

.refresh-btn {
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-secondary);
  font-size: 16px;
  cursor: pointer;
  transition: transform 0.2s, color 0.2s;
  flex-shrink: 0;
}

.refresh-btn:hover {
  color: var(--text-primary);
  background: var(--bg-panel);
}

.refresh-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.refresh-btn.spinning {
  animation: spin 0.8s linear infinite;
}

.refresh-btn.refresh-error {
  color: #ef4444;
  border-color: #ef4444;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
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

.role-bar {
  display: flex;
  gap: 4px;
  padding: 4px 8px;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  border-right: 1px solid var(--border);
}

.role-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 1px 7px;
  border-radius: 10px;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-secondary);
  font-size: 10px;
  cursor: pointer;
  font-family: var(--font-mono);
}

.role-badge:hover {
  background: var(--bg-panel);
}

.role-badge.active {
  background: var(--bg-panel);
  border-color: var(--accent-main);
  color: var(--text-primary);
}

.dashboard-body {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 0;
  min-height: 0;
  flex: 1;
}

.task-list {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-top: none;
  border-radius: 0 0 0 var(--radius);
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

.task-role {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
}

.task-priority {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 4px;
  border-radius: 3px;
}

.task-priority.P0 { background: rgba(255, 82, 82, 0.2); color: var(--accent-error); }
.task-priority.P1 { background: rgba(251, 146, 60, 0.2); color: #fb923c; }
.task-priority.P2 { background: rgba(250, 204, 21, 0.2); color: #facc15; }
.task-priority.P3 { background: rgba(148, 163, 184, 0.2); color: var(--text-muted); }

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
  border-top: none;
  border-left: none;
  border-radius: 0 0 var(--radius) 0;
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
