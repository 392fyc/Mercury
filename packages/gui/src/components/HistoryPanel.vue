<script setup lang="ts">
import { computed } from "vue";
import { useMessageStore } from "../stores/messages";

const { pendingHistoryView, selectHistorySession, dismissHistoryView } = useMessageStore();

const visible = computed(() => pendingHistoryView.value !== null);
const sessions = computed(() => pendingHistoryView.value?.sessions ?? []);
const selectedSessionId = computed(() => pendingHistoryView.value?.selectedSessionId ?? null);
const messages = computed(() => pendingHistoryView.value?.messages ?? []);

function formatTime(ts: number): string {
  return new Date(ts).toLocaleString();
}

function shortId(id: string): string {
  return id.slice(0, 12);
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="history-overlay" @click.self="dismissHistoryView">
      <div class="history-panel">
        <div class="history-header">
          <span class="history-title">Session history (read-only)</span>
          <button class="history-close" @click="dismissHistoryView">&times;</button>
        </div>
        <div class="history-body">
          <div class="history-list">
            <button
              v-for="session in sessions"
              :key="session.sessionId"
              class="history-item"
              :class="{ selected: session.sessionId === selectedSessionId }"
              @click="selectHistorySession(session.sessionId)"
            >
              <div class="history-item-main">
                <span class="history-name">{{ session.sessionName || shortId(session.sessionId) }}</span>
                <span class="history-role">{{ session.role || session.frozenRole || "legacy" }}</span>
                <span class="history-status">{{ session.status }}</span>
              </div>
              <div class="history-item-meta">
                <span>{{ shortId(session.sessionId) }}</span>
                <span>{{ formatTime(session.lastActiveAt) }}</span>
              </div>
            </button>
          </div>
          <div class="history-messages">
            <div v-if="messages.length === 0" class="history-empty">No transcript available</div>
            <div
              v-for="(message, index) in messages"
              :key="`${message.timestamp}-${index}`"
              class="history-message"
              :class="message.role"
            >
              <div class="history-message-meta">
                <span class="history-message-role">{{ message.role }}</span>
                <span>{{ formatTime(message.timestamp) }}</span>
              </div>
              <div class="history-message-content">{{ message.content }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.history-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.history-panel {
  width: min(980px, 90vw);
  height: min(680px, 80vh);
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  display: grid;
  grid-template-rows: auto 1fr;
}

.history-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}

.history-title {
  font-weight: 600;
  font-size: 13px;
}

.history-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
}

.history-body {
  display: grid;
  grid-template-columns: 320px 1fr;
  min-height: 0;
}

.history-list {
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding: 8px;
}

.history-item {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 9px 10px;
  margin-bottom: 6px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg-primary);
  color: var(--text-primary);
  text-align: left;
  cursor: pointer;
}

.history-item.selected {
  border-color: var(--accent-main);
  background: rgba(0, 212, 255, 0.06);
}

.history-item-main,
.history-item-meta,
.history-message-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.history-name {
  font-size: 12px;
  font-weight: 500;
}

.history-role,
.history-status,
.history-item-meta,
.history-message-meta {
  font-size: 10px;
  color: var(--text-muted);
}

.history-status {
  margin-left: auto;
  text-transform: uppercase;
}

.history-messages {
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.history-empty {
  color: var(--text-muted);
  font-size: 12px;
}

.history-message {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  background: var(--bg-primary);
}

.history-message.user {
  border-left: 3px solid var(--accent-main);
}

.history-message.assistant {
  border-left: 3px solid var(--accent-sub);
}

.history-message.system {
  border-left: 3px solid var(--accent-warn);
}

.history-message-role {
  text-transform: uppercase;
}

.history-message-content {
  white-space: pre-wrap;
  font-size: 12px;
  line-height: 1.5;
  margin-top: 6px;
}
</style>
