<script setup lang="ts">
import { computed } from "vue";
import { useApprovalStore } from "../stores/approvals";

const {
  pendingRequests,
  queueOpen,
  approve,
  deny,
  closeQueue,
} = useApprovalStore();

const visible = computed(() => queueOpen.value);
const requests = computed(() => pendingRequests.value);

function formatTime(ts: number): string {
  return new Date(ts).toLocaleString();
}

function handleApprove(requestId: string) {
  void approve(requestId);
}

function handleDeny(requestId: string) {
  void deny(requestId);
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="approval-overlay" @click.self="closeQueue">
      <div class="approval-queue">
        <div class="queue-header">
          <span class="queue-title">Pending Approvals</span>
          <button class="queue-close" @click="closeQueue">&times;</button>
        </div>

        <div class="queue-list">
          <div v-if="requests.length === 0" class="queue-empty">
            No pending approvals
          </div>

          <div v-for="request in requests" :key="request.id" class="approval-item">
            <div class="approval-topline">
              <span class="approval-agent">{{ request.role || "sub" }}:{{ request.agentId }}</span>
              <span class="approval-kind">{{ request.toolName || request.kind }}</span>
            </div>
            <div class="approval-summary">{{ request.summary }}</div>
            <div class="approval-meta">
              <span class="approval-session">{{ request.sessionId.slice(0, 12) }}</span>
              <span class="approval-time">{{ formatTime(request.createdAt) }}</span>
              <span class="approval-cwd" v-if="request.cwd">{{ request.cwd }}</span>
            </div>
            <div class="approval-actions">
              <button class="approve-btn" @click="handleApprove(request.id)">Approve</button>
              <button class="deny-btn" @click="handleDeny(request.id)">Deny</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.approval-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.approval-queue {
  width: 560px;
  max-height: 440px;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}

.queue-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}

.queue-title {
  font-weight: 600;
  font-size: 13px;
}

.queue-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
}

.queue-list {
  overflow-y: auto;
  padding: 8px;
}

.queue-empty {
  padding: 20px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}

.approval-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px;
  margin-bottom: 8px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
}

.approval-topline {
  display: flex;
  align-items: center;
  gap: 8px;
}

.approval-agent {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
}

.approval-kind {
  margin-left: auto;
  font-size: 10px;
  color: var(--accent-main);
  font-family: var(--font-mono);
}

.approval-summary {
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-primary);
}

.approval-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 10px;
  color: var(--text-muted);
  font-family: var(--font-mono);
}

.approval-actions {
  display: flex;
  gap: 8px;
}

.approve-btn,
.deny-btn {
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--bg-panel);
  color: var(--text-primary);
  font-size: 11px;
  padding: 4px 10px;
  cursor: pointer;
}

.approve-btn:hover {
  border-color: var(--accent-success);
  color: var(--accent-success);
}

.deny-btn:hover {
  border-color: var(--accent-error);
  color: var(--accent-error);
}
</style>
