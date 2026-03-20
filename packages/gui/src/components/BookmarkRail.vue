<script setup lang="ts">
import { computed, ref } from "vue";
import { useAgentStore } from "../stores/agents";

const emit = defineEmits<{
  "open-session": [panelKey: string];
  "create-session": [];
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
  return Math.max(0.65, 1 - dist * 0.1);
}

function isTabOpen(panelKey: string): boolean {
  return openFloatingTabs.value.includes(panelKey);
}

function shortName(name?: string, panelKey?: string): string {
  if (name) return name.length > 18 ? name.slice(0, 16) + "…" : name;
  return panelKey?.split(":")[0]?.toUpperCase() ?? "?";
}
</script>

<template>
  <div class="bookmark-rail" @wheel="handleWheel">
    <!-- Overflow top indicator -->
    <div v-if="hasOverflowTop" class="overflow-indicator top">⋮</div>

    <!-- Bookmarks -->
    <div class="bookmark-list">
      <button
        v-for="(bm, idx) in visibleBookmarks"
        :key="bm.panelKey"
        class="bookmark-tab"
        :class="{ active: bm.status === 'active', open: isTabOpen(bm.panelKey) }"
        :style="{ transform: `scale(${fisheyeScale(idx)})` }"
        :title="`${bm.displayName} — ${bm.sessionName ?? bm.sessionId.slice(0, 8)}\n${bm.status.toUpperCase()}`"
        @click="emit('open-session', bm.panelKey)"
      >
        <span class="bm-role">{{ bm.role.slice(0, 3).toUpperCase() }}</span>
        <span class="bm-name">{{ shortName(bm.sessionName, bm.panelKey) }}</span>
        <span class="bm-dot" :class="bm.status"></span>
      </button>
    </div>

    <!-- Overflow bottom indicator -->
    <div v-if="hasOverflowBottom" class="overflow-indicator bottom">⋮</div>

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
  align-items: center;
  width: 44px;
  padding: 8px 2px;
  gap: 4px;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  user-select: none;
  overflow: hidden;
}

.bookmark-list {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  flex: 1;
  justify-content: center;
}

.bookmark-tab {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 38px;
  min-height: 44px;
  padding: 4px 2px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-right: none;
  border-radius: 6px 0 0 6px;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 9px;
  transition: transform 0.15s ease, background 0.15s ease, box-shadow 0.15s ease;
  position: relative;
}

.bookmark-tab:hover {
  transform: scale(1.12) translateX(-4px) !important;
  background: var(--bg-panel);
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.3);
  z-index: 2;
}

.bookmark-tab.open {
  background: var(--bg-panel);
  border-color: var(--accent-main);
  color: var(--text-primary);
}

.bookmark-tab.active .bm-dot {
  background: var(--accent-main);
  box-shadow: 0 0 4px var(--accent-main);
}

.bm-role {
  font-weight: 600;
  font-size: 8px;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  color: var(--accent-sub);
}

.bm-name {
  font-size: 7px;
  color: var(--text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 34px;
  text-align: center;
}

.bm-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--text-muted);
  margin-top: 2px;
}

.bm-dot.active {
  background: var(--accent-main);
  box-shadow: 0 0 4px var(--accent-main);
}

.bm-dot.error {
  background: var(--accent-error);
}

.overflow-indicator {
  font-size: 10px;
  color: var(--text-muted);
  height: 12px;
  line-height: 12px;
}

.bookmark-add {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1px dashed var(--border);
  background: none;
  color: var(--text-muted);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: auto;
  transition: border-color 0.15s, color 0.15s;
}

.bookmark-add:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
}

.add-icon {
  line-height: 1;
}
</style>
