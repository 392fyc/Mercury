<script setup lang="ts">
import { computed, ref } from "vue";
import { useAgentStore } from "../stores/agents";

const emit = defineEmits<{
  "open-session": [panelKey: string];
  "create-session": [];
  "open-archived": [];
}>();

const { bookmarkList, openFloatingTabs } = useAgentStore();

/** Scroll offset: which bookmark index is at the center of the visible area. */
const scrollOffset = ref(0);
const MAX_VISIBLE = 7;

const visibleBookmarks = computed(() => {
  const list = bookmarkList.value;
  if (list.length <= MAX_VISIBLE) return list;
  const half = Math.floor(MAX_VISIBLE / 2);
  const start = Math.max(0, Math.min(scrollOffset.value - half, list.length - MAX_VISIBLE));
  return list.slice(start, start + MAX_VISIBLE);
});

const hasOverflowTop = computed(() => {
  if (bookmarkList.value.length <= MAX_VISIBLE) return false;
  const half = Math.floor(MAX_VISIBLE / 2);
  const start = Math.max(0, Math.min(scrollOffset.value - half, bookmarkList.value.length - MAX_VISIBLE));
  return start > 0;
});

const hasOverflowBottom = computed(() => {
  if (bookmarkList.value.length <= MAX_VISIBLE) return false;
  const half = Math.floor(MAX_VISIBLE / 2);
  const start = Math.max(0, Math.min(scrollOffset.value - half, bookmarkList.value.length - MAX_VISIBLE));
  return start + MAX_VISIBLE < bookmarkList.value.length;
});

function handleWheel(event: WheelEvent) {
  event.preventDefault();
  const delta = event.deltaY > 0 ? 1 : -1;
  scrollOffset.value = Math.max(0, Math.min(scrollOffset.value + delta, bookmarkList.value.length - 1));
}

/** Fisheye scale: center bookmark is largest, edges are smaller. */
function fisheyeScale(index: number): number {
  const center = Math.floor(visibleBookmarks.value.length / 2);
  const dist = Math.abs(index - center);
  return Math.max(0.75, 1 - dist * 0.07);
}

function isTabOpen(panelKey: string): boolean {
  return openFloatingTabs.value.includes(panelKey);
}

function formatTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}
</script>

<template>
  <div class="bookmark-rail" @wheel="handleWheel">
    <!-- Archived sessions entry -->
    <button class="bookmark-archived" title="Archived sub-agent sessions" @click="emit('open-archived')">
      <span class="archived-icon">📋</span>
      <span class="archived-label">Archived</span>
    </button>

    <!-- Overflow top indicator -->
    <div v-if="hasOverflowTop" class="overflow-indicator top">⋯</div>

    <!-- Bookmarks -->
    <div class="bookmark-list">
      <button
        v-for="(bm, idx) in visibleBookmarks"
        :key="bm.panelKey"
        class="bookmark-tab"
        :class="{ active: bm.status === 'active', open: isTabOpen(bm.panelKey) }"
        :style="{ transform: `scale(${fisheyeScale(idx)})` }"
        @click="emit('open-session', bm.panelKey)"
      >
        <div class="bm-top-row">
          <span class="bm-role">{{ bm.role }}</span>
          <span class="bm-status-dot" :class="bm.status"></span>
        </div>
        <span class="bm-title">{{ bm.sessionName || bm.sessionId.slice(0, 10) }}</span>
        <div class="bm-bottom-row">
          <span class="bm-agent">{{ bm.displayName }}</span>
          <span class="bm-time">{{ formatTime(bm.lastActiveAt) }}</span>
        </div>
      </button>
    </div>

    <!-- Overflow bottom indicator -->
    <div v-if="hasOverflowBottom" class="overflow-indicator bottom">⋯</div>

    <!-- Create new session button -->
    <button class="bookmark-add" title="New sub-agent session" @click="emit('create-session')">
      <span class="add-icon">+</span>
    </button>
  </div>
</template>

<style scoped>
.bookmark-rail {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  width: 136px;
  padding: 8px 6px;
  gap: 5px;
  background: linear-gradient(to right, var(--bg-secondary), rgba(22, 33, 62, 0.95));
  border-left: 1px solid var(--border);
  user-select: none;
  overflow: hidden;
  flex-shrink: 0;
}

.bookmark-list {
  display: flex;
  flex-direction: column;
  gap: 3px;
  flex: 1;
  justify-content: center;
}

.bookmark-tab {
  display: flex;
  flex-direction: column;
  gap: 3px;
  width: 100%;
  padding: 8px 10px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-right: none;
  border-radius: 6px 0 0 6px;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 11px;
  text-align: left;
  transition: transform 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
  position: relative;
  transform-origin: right center;
}

.bookmark-tab:hover {
  transform: scale(1.05) translateX(-4px) !important;
  background: var(--bg-panel);
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.3);
  z-index: 2;
}

.bookmark-tab.open {
  background: var(--bg-panel);
  border-color: var(--accent-main);
  color: var(--text-primary);
}

.bm-top-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.bm-role {
  font-weight: 600;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
  color: var(--accent-sub);
}

.bm-status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--text-muted);
  flex-shrink: 0;
}

.bm-status-dot.active {
  background: var(--accent-main);
  box-shadow: 0 0 4px var(--accent-main);
}

.bm-status-dot.error {
  background: var(--accent-error);
}

.bm-title {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.bm-bottom-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}

.bm-agent {
  font-size: 9px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.bm-time {
  font-size: 9px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  white-space: nowrap;
  flex-shrink: 0;
}

.bookmark-archived {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-muted);
  font-size: 10px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  flex-shrink: 0;
}

.bookmark-archived:hover {
  background: var(--bg-panel);
  color: var(--text-secondary);
}

.archived-icon {
  font-size: 12px;
}

.archived-label {
  font-weight: 500;
}

.overflow-indicator {
  font-size: 10px;
  color: var(--text-muted);
  height: 14px;
  line-height: 14px;
  text-align: center;
}

.bookmark-add {
  width: 100%;
  height: 36px;
  border-radius: 6px;
  border: 1.5px dashed var(--text-muted);
  background: rgba(0, 212, 255, 0.03);
  color: var(--text-secondary);
  font-size: 18px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: auto;
  transition: border-color 0.15s, color 0.15s, background 0.15s;
}

.bookmark-add:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
  background: rgba(0, 212, 255, 0.06);
}

.add-icon {
  line-height: 1;
}
</style>
