<script setup lang="ts">
import { onMounted, onUnmounted, ref, reactive } from "vue";
import { usePrMonitorStore } from "../stores/pr-monitor";
import type { PullRequest, CodeRabbitStatus } from "../lib/tauri-bridge";

const emit = defineEmits<{ close: [] }>();
const {
  prs,
  polling,
  lastError,
  loading,
  hasAlerts,
  fetchPrs,
  startPolling,
  stopPolling,
  requestCoderabbitReview,
  requestMerge,
  syncState,
  initPrMonitorListeners,
  resetListeners,
} = usePrMonitorStore();

const disposed = ref(false);
const unlistenFns: Array<() => void> = [];
const confirmMerge = ref<number | null>(null);
const pending = reactive<Record<number, boolean>>({});

onMounted(async () => {
  // Reset listener flag so re-mount can re-register
  resetListeners();
  const fns = await initPrMonitorListeners();
  if (disposed.value) {
    fns.forEach((fn) => fn());
    return;
  }
  unlistenFns.push(...fns);
  // Sync backend state first, then fetch + start polling if not active
  await syncState();
  if (disposed.value) return;
  await fetchPrs();
  if (disposed.value) return;
  if (!polling.value) {
    await startPolling(60);
  }
});

onUnmounted(() => {
  disposed.value = true;
  unlistenFns.forEach((fn) => fn());
  resetListeners();
  stopPolling();
});

function codeRabbitLabel(status: CodeRabbitStatus): string {
  const labels: Record<CodeRabbitStatus, string> = {
    pending: "Pending",
    commented: "Reviewing",
    approved: "Approved",
    changes_requested: "Changes Requested",
  };
  return labels[status] ?? "Unknown";
}

function codeRabbitColor(status: CodeRabbitStatus): string {
  const colors: Record<CodeRabbitStatus, string> = {
    pending: "var(--text-muted)",
    commented: "var(--accent-main)",
    approved: "var(--accent-success)",
    changes_requested: "var(--accent-error)",
  };
  return colors[status] ?? "var(--text-muted)";
}

function reviewDecisionLabel(decision: string | null): string {
  if (!decision) return "No reviews";
  const map: Record<string, string> = {
    APPROVED: "Approved",
    CHANGES_REQUESTED: "Changes Requested",
    REVIEW_REQUIRED: "Review Required",
  };
  return map[decision] ?? decision;
}

function timeAgo(isoString: string): string {
  const created = new Date(isoString).getTime();
  const now = Date.now();
  const diffMin = Math.floor((now - created) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  return `${Math.floor(diffH / 24)}d ago`;
}

async function handleTriggerReview(pr: PullRequest) {
  if (pending[pr.number]) return;
  pending[pr.number] = true;
  try {
    await requestCoderabbitReview(pr.number);
    await fetchPrs();
  } catch {
    // Error already stored in lastError by the store
  } finally {
    pending[pr.number] = false;
  }
}

async function handleMerge(prNumber: number) {
  if (pending[prNumber]) return;
  pending[prNumber] = true;
  try {
    await requestMerge(prNumber);
    confirmMerge.value = null;
  } catch {
    // Error already stored in lastError by the store; keep confirm dialog visible
  } finally {
    pending[prNumber] = false;
  }
}
</script>

<template>
  <div class="pr-overlay" @click.self="emit('close')">
    <div class="pr-panel">
      <div class="pr-header">
        <div class="pr-title-row">
          <span class="pr-icon">🔀</span>
          <h3>PR Monitor</h3>
          <span v-if="hasAlerts" class="alert-badge">!</span>
        </div>
        <div class="pr-controls">
          <button
            class="ctrl-btn"
            :class="{ active: polling }"
            :title="polling ? 'Stop auto-refresh' : 'Start auto-refresh (60s)'"
            @click="polling ? stopPolling() : startPolling(60)"
          >
            {{ polling ? "⏸" : "▶" }}
          </button>
          <button
            class="ctrl-btn"
            title="Refresh now"
            :disabled="loading"
            @click="fetchPrs()"
          >🔄</button>
          <button class="ctrl-btn close-btn" title="Close" @click="emit('close')">✕</button>
        </div>
      </div>

      <div v-if="lastError" class="pr-error">{{ lastError }}</div>

      <div v-if="loading && prs.length === 0" class="pr-loading">
        Loading PRs...
      </div>

      <div v-else-if="prs.length === 0" class="pr-empty">
        No open pull requests
      </div>

      <div v-else class="pr-list">
        <div
          v-for="pr in prs"
          :key="pr.number"
          class="pr-card"
          :class="{ 'pr-alert': pr.timeout_alert }"
        >
          <div class="pr-card-header">
            <a
              class="pr-number"
              :href="pr.url"
              target="_blank"
              rel="noopener noreferrer"
              title="Open in browser"
            >#{{ pr.number }}</a>
            <span class="pr-card-title">{{ pr.title }}</span>
          </div>
          <div class="pr-card-meta">
            <span class="pr-branch">{{ pr.head_ref_name }}</span>
            <span class="pr-author">{{ pr.author }}</span>
            <span class="pr-time">{{ timeAgo(pr.created_at) }}</span>
          </div>
          <div class="pr-card-status">
            <div class="status-row">
              <span class="status-label">CodeRabbit:</span>
              <span
                class="status-value"
                :style="{ color: codeRabbitColor(pr.coderabbit_status) }"
              >
                <span class="status-dot" :style="{ background: codeRabbitColor(pr.coderabbit_status) }"></span>
                {{ codeRabbitLabel(pr.coderabbit_status) }}
              </span>
              <span v-if="pr.timeout_alert" class="timeout-badge">TIMEOUT</span>
            </div>
            <div class="status-row">
              <span class="status-label">Decision:</span>
              <span class="status-value">{{ reviewDecisionLabel(pr.review_decision) }}</span>
            </div>
          </div>
          <div class="pr-card-actions">
            <button
              class="action-btn review-btn"
              :disabled="pending[pr.number]"
              title="@coderabbitai review"
              @click="handleTriggerReview(pr)"
            >🐰 Request Review</button>
            <button
              v-if="confirmMerge !== pr.number"
              class="action-btn merge-btn"
              :disabled="pr.review_decision !== 'APPROVED' || pending[pr.number]"
              :title="pr.review_decision === 'APPROVED' ? 'Squash merge' : 'Not yet approved'"
              @click="confirmMerge = pr.number"
            >Merge</button>
            <div v-else class="merge-confirm">
              <span>Confirm merge?</span>
              <button class="action-btn confirm-yes" @click="handleMerge(pr.number)">Yes</button>
              <button class="action-btn confirm-no" @click="confirmMerge = null">No</button>
            </div>
          </div>
        </div>
      </div>

      <div class="pr-footer">
        <span v-if="polling" class="poll-indicator">
          <span class="poll-dot"></span> Auto-refresh active
        </span>
        <span v-else class="poll-indicator poll-off">Auto-refresh off</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pr-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
}

.pr-panel {
  background: var(--bg-primary);
  border: 1px solid rgba(0, 212, 255, 0.15);
  border-radius: 12px;
  width: min(560px, 90vw);
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
}

.pr-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid rgba(0, 212, 255, 0.08);
}

.pr-title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pr-icon {
  font-size: 18px;
}

.pr-title-row h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
}

.alert-badge {
  background: var(--accent-error);
  color: #fff;
  font-size: 10px;
  font-weight: 700;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

.pr-controls {
  display: flex;
  gap: 4px;
}

.ctrl-btn {
  background: none;
  border: 1px solid rgba(0, 212, 255, 0.12);
  color: var(--text-secondary);
  width: 28px;
  height: 28px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.ctrl-btn:hover {
  background: var(--bg-panel);
  color: var(--text-primary);
}

.ctrl-btn.active {
  background: rgba(0, 212, 255, 0.15);
  color: var(--accent-main);
  border-color: var(--accent-main);
}

.ctrl-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.close-btn {
  font-size: 14px;
}

.pr-error {
  padding: 8px 16px;
  background: rgba(255, 82, 82, 0.1);
  color: var(--accent-error);
  font-size: 12px;
  border-bottom: 1px solid rgba(255, 82, 82, 0.15);
}

.pr-loading,
.pr-empty {
  padding: 40px 16px;
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
}

.pr-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.pr-card {
  background: var(--bg-secondary);
  border: 1px solid rgba(0, 212, 255, 0.06);
  border-radius: 8px;
  padding: 12px;
}

.pr-card.pr-alert {
  border-color: rgba(255, 170, 0, 0.4);
  background: rgba(255, 170, 0, 0.04);
}

.pr-card-header {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 6px;
}

.pr-number {
  color: var(--accent-main);
  font-weight: 600;
  font-size: 13px;
  text-decoration: none;
}

.pr-number:hover {
  text-decoration: underline;
}

.pr-card-title {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pr-card-meta {
  display: flex;
  gap: 12px;
  font-size: 11px;
  color: var(--text-muted);
  margin-bottom: 8px;
}

.pr-branch {
  font-family: monospace;
  background: var(--bg-panel);
  padding: 1px 5px;
  border-radius: 3px;
}

.pr-card-status {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 8px;
}

.status-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
}

.status-label {
  color: var(--text-muted);
  min-width: 80px;
}

.status-value {
  display: flex;
  align-items: center;
  gap: 4px;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}

.timeout-badge {
  background: rgba(255, 170, 0, 0.2);
  color: #ffaa00;
  font-size: 10px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  margin-left: 8px;
  animation: pulse 2s infinite;
}

.pr-card-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}

.action-btn {
  background: var(--bg-panel);
  border: 1px solid rgba(0, 212, 255, 0.1);
  color: var(--text-secondary);
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 5px;
  cursor: pointer;
}

.action-btn:hover {
  background: rgba(0, 212, 255, 0.1);
  color: var(--text-primary);
}

.action-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}

.review-btn:hover {
  border-color: var(--accent-main);
}

.merge-btn {
  color: var(--accent-success);
}

.merge-confirm {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  color: var(--text-muted);
}

.confirm-yes {
  color: var(--accent-success) !important;
  border-color: var(--accent-success) !important;
}

.confirm-no {
  color: var(--accent-error) !important;
}

.pr-footer {
  padding: 8px 16px;
  border-top: 1px solid rgba(0, 212, 255, 0.06);
  font-size: 11px;
}

.poll-indicator {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--accent-success);
}

.poll-indicator.poll-off {
  color: var(--text-muted);
}

.poll-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-success);
  animation: pulse 2s infinite;
}
</style>
