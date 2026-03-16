<script setup lang="ts">
import { computed } from "vue";
import { useMessageStore } from "../stores/messages";
import type { SessionListItem } from "../lib/tauri-bridge";

const { pendingSessionPick, pickSession, dismissSessionPick } = useMessageStore();

const visible = computed(() => pendingSessionPick.value !== null);
const sessions = computed(() => pendingSessionPick.value?.sessions ?? []);

function formatTime(ts: number): string {
  return new Date(ts).toLocaleString();
}

function shortId(id: string): string {
  return id.slice(0, 12);
}

function handleSelect(session: SessionListItem) {
  pickSession(session.sessionId);
}

function handleBackdrop() {
  dismissSessionPick();
}
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="session-picker-overlay" @click.self="handleBackdrop">
      <div class="session-picker">
        <div class="picker-header">
          <span class="picker-title">Resume Session</span>
          <button class="picker-close" @click="dismissSessionPick">&times;</button>
        </div>
        <div class="picker-list">
          <div v-if="sessions.length === 0" class="picker-empty">
            No saved sessions
          </div>
          <button
            v-for="s in sessions"
            :key="s.sessionId"
            class="session-item"
            :class="{ active: s.active }"
            @click="handleSelect(s)"
          >
            <div class="session-main">
              <span class="session-name">{{ s.sessionName || shortId(s.sessionId) }}</span>
              <span class="session-role" v-if="s.role">{{ s.role }}</span>
              <span class="session-status" :class="s.active ? 'live' : 'saved'">
                {{ s.active ? 'active' : s.status }}
              </span>
            </div>
            <div class="session-meta">
              <span class="session-id">{{ shortId(s.sessionId) }}</span>
              <span class="session-time">{{ formatTime(s.lastActiveAt) }}</span>
            </div>
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.session-picker-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.session-picker {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  width: 460px;
  max-height: 400px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.picker-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}

.picker-title {
  font-weight: 600;
  font-size: 13px;
}

.picker-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.picker-close:hover {
  color: var(--text-primary);
}

.picker-list {
  overflow-y: auto;
  padding: 6px;
}

.picker-empty {
  padding: 20px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}

.session-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  width: 100%;
  padding: 8px 10px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  cursor: pointer;
  text-align: left;
  margin-bottom: 4px;
  color: var(--text-primary);
}

.session-item:hover {
  border-color: var(--accent-main);
  background: rgba(0, 212, 255, 0.05);
}

.session-item.active {
  border-left: 2px solid var(--accent-main);
}

.session-main {
  display: flex;
  align-items: center;
  gap: 8px;
}

.session-name {
  font-weight: 500;
  font-size: 12px;
}

.session-role {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}

.session-status {
  font-size: 9px;
  padding: 1px 5px;
  border-radius: 3px;
  text-transform: uppercase;
  margin-left: auto;
}

.session-status.live {
  background: rgba(0, 212, 255, 0.15);
  color: var(--accent-main);
}

.session-status.saved {
  background: var(--bg-panel);
  color: var(--text-muted);
}

.session-meta {
  display: flex;
  gap: 12px;
  font-size: 10px;
  color: var(--text-muted);
  font-family: var(--font-mono);
}
</style>
