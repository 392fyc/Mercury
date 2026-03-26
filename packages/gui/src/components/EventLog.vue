<script setup lang="ts">
import { useEventStore } from "../stores/events";

const { events, eventCount, clearEvents } = useEventStore();

function typeColor(type: string): string {
  if (type.startsWith("agent.error")) return "var(--accent-error)";
  if (type.startsWith("orchestrator.")) return "var(--accent-sub)";
  if (type.startsWith("agent.")) return "var(--accent-main)";
  return "var(--text-secondary)";
}

function payloadPreview(payload: Record<string, unknown>): string {
  return JSON.stringify(payload).slice(0, 80);
}
</script>

<template>
  <div class="event-log">
    <div class="log-header">
      <span class="log-title">Event Log</span>
      <span class="log-count">{{ eventCount }} events</span>
      <button class="log-clear-btn" @click="clearEvents" title="Clear event log">Clear</button>
    </div>
    <div class="log-body">
      <div v-if="events.length === 0" class="empty-state">
        <p>Event stream will appear here</p>
      </div>
      <div
        v-for="event in events"
        :key="event.id"
        class="log-entry"
      >
        <span class="log-time">{{ new Date(event.timestamp).toLocaleTimeString() }}</span>
        <span class="log-type" :style="{ color: typeColor(event.type) }">{{ event.type }}</span>
        <span class="log-agent">{{ event.agentId }}</span>
        <span class="log-preview">{{ payloadPreview(event.payload) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.event-log {
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border: none;
  border-top: 1px solid rgba(0, 212, 255, 0.1);
  box-shadow: 0 -1px 6px rgba(0, 212, 255, 0.03);
  border-radius: 0;
  overflow: hidden;
  min-height: 0;
}

.log-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
}

.log-title {
  font-weight: 600;
  font-size: 13px;
}

.log-count {
  font-size: 10px;
  color: var(--text-muted);
}

.log-clear-btn {
  margin-left: 8px;
  padding: 2px 8px;
  font-size: 10px;
  background: none;
  border: 1px solid var(--border);
  border-radius: 3px;
  color: var(--text-muted);
  cursor: pointer;
  line-height: 1.4;
  transition: color 0.15s, border-color 0.15s;
}

.log-clear-btn:hover {
  color: var(--accent-error);
  border-color: var(--accent-error);
}

.log-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px;
  font-family: var(--font-mono);
  font-size: 11px;
  min-height: 0;
}

.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  font-size: 12px;
}

.log-entry {
  display: flex;
  gap: 8px;
  padding: 3px 8px;
  border-radius: 3px;
}

.log-entry:hover {
  background: var(--bg-panel);
}

.log-time {
  color: var(--text-muted);
  flex-shrink: 0;
}

.log-type {
  flex-shrink: 0;
}

.log-agent {
  color: var(--accent-sub);
  flex-shrink: 0;
}

.log-preview {
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
