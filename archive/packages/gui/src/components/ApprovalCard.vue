<script setup lang="ts">
import { computed } from "vue";
import { useApprovalStore } from "../stores/approvals";

const props = defineProps<{ requestId: string }>();

const { getRequest, approve, deny } = useApprovalStore();

const request = computed(() => getRequest(props.requestId));
const isPending = computed(() => request.value?.status === "pending");

function formatTime(ts: number): string {
  return new Date(ts).toLocaleString();
}
</script>

<template>
  <div v-if="request" class="approval-card" :class="request.status">
    <div class="card-header">
      <span class="card-title">Approval Request</span>
      <span class="card-status">{{ request.status }}</span>
    </div>
    <div class="card-body">
      <div class="card-summary">{{ request.summary }}</div>
      <div class="card-meta">
        <span>{{ request.role || "sub" }}:{{ request.agentId }}</span>
        <span>{{ request.toolName || request.kind }}</span>
        <span>{{ formatTime(request.createdAt) }}</span>
      </div>
      <div class="card-cwd" v-if="request.cwd">{{ request.cwd }}</div>
      <div class="card-reason" v-if="request.decisionReason">{{ request.decisionReason }}</div>
    </div>
    <div class="card-actions" v-if="isPending">
      <button class="approve-btn" @click="approve(request.id)">Approve</button>
      <button class="deny-btn" @click="deny(request.id)">Deny</button>
    </div>
  </div>
</template>

<style scoped>
.approval-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: rgba(255, 184, 77, 0.08);
  border: 1px solid rgba(255, 184, 77, 0.35);
  border-radius: 6px;
  padding: 10px;
}

.approval-card.approved {
  border-color: rgba(58, 204, 120, 0.35);
  background: rgba(58, 204, 120, 0.08);
}

.approval-card.denied,
.approval-card.timed_out,
.approval-card.cancelled {
  border-color: rgba(255, 82, 82, 0.35);
  background: rgba(255, 82, 82, 0.08);
}

.card-header,
.card-meta,
.card-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.card-title {
  font-weight: 600;
  font-size: 12px;
}

.card-status {
  margin-left: auto;
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-secondary);
}

.card-summary {
  font-size: 12px;
  line-height: 1.5;
}

.card-meta,
.card-cwd,
.card-reason {
  font-size: 10px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  flex-wrap: wrap;
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
