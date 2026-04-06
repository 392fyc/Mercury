<script setup lang="ts">
import { computed, ref } from "vue";
import { useAgentStore } from "../stores/agents";
import { deleteSession as rpcDeleteSession } from "../lib/tauri-bridge";

const emit = defineEmits<{
  "open-session": [panelKey: string];
  "create-session": [];
  "open-archived": [];
}>();

const { bookmarkList, openFloatingTabs, removeBookmark, parsePanelKey, getSession } = useAgentStore();

/** Scroll offset: which bookmark index is at the center of the visible area. */
const scrollOffset = ref(0);
const MAX_VISIBLE = 7;

/** Computed start index for the visible window of bookmarks. */
const visibleStart = computed(() => {
  const len = bookmarkList.value.length;
  if (len <= MAX_VISIBLE) return 0;
  const half = Math.floor(MAX_VISIBLE / 2);
  return Math.max(0, Math.min(scrollOffset.value - half, len - MAX_VISIBLE));
});

const visibleBookmarks = computed(() => {
  const list = bookmarkList.value;
  if (list.length <= MAX_VISIBLE) return list;
  return list.slice(visibleStart.value, visibleStart.value + MAX_VISIBLE);
});

const hasOverflowTop = computed(() => bookmarkList.value.length > MAX_VISIBLE && visibleStart.value > 0);

const hasOverflowBottom = computed(() => {
  return bookmarkList.value.length > MAX_VISIBLE && visibleStart.value + MAX_VISIBLE < bookmarkList.value.length;
});

/** Handle scroll wheel on bookmark rail. Only intercepts when bookmarks overflow. */
function handleWheel(event: WheelEvent) {
  if (bookmarkList.value.length <= MAX_VISIBLE) return; // Don't block scroll when no overflow
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

/** Hide bookmark without deleting the session. */
function closeBookmark(panelKey: string, event: Event) {
  event.stopPropagation();
  hideContextMenu();
  removeBookmark(panelKey);
}

/**
 * Delete the session entirely (stops + removes from orchestrator + hides bookmark).
 * Uses optimistic UI: the bookmark is always removed regardless of RPC outcome.
 * If RPC fails, the orchestrator-side session will be cleaned up on next restart
 * via session persistence reconciliation.
 */
async function handleDeleteSession(panelKey: string, event: Event) {
  event.stopPropagation();
  hideContextMenu();
  const { agentId } = parsePanelKey(panelKey);
  const sessionId = getSession(panelKey);
  if (sessionId && agentId) {
    try {
      await rpcDeleteSession(agentId, sessionId);
    } catch (err) {
      console.warn("[BookmarkRail] deleteSession RPC failed:", err);
    }
  }
  removeBookmark(panelKey);
}

const contextMenu = ref<{ panelKey: string; x: number; y: number } | null>(null);

const MENU_WIDTH_ESTIMATE = 160;
const MENU_HEIGHT_ESTIMATE = 80;

function showContextMenu(panelKey: string, event: MouseEvent) {
  event.preventDefault();
  event.stopPropagation();
  const x = Math.min(event.clientX, window.innerWidth - MENU_WIDTH_ESTIMATE);
  const y = Math.min(event.clientY, window.innerHeight - MENU_HEIGHT_ESTIMATE);
  contextMenu.value = { panelKey, x, y };
}

function hideContextMenu() {
  contextMenu.value = null;
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape" && contextMenu.value) {
    hideContextMenu();
  }
}
</script>

<template>
  <div class="bookmark-rail" @wheel="handleWheel" @keydown="handleKeydown">
    <!-- Archived sessions entry -->
    <button class="bookmark-archived" title="Archived sub-agent sessions" @click="emit('open-archived')">
      <span class="archived-icon">📋</span>
      <span class="archived-label">Archived</span>
    </button>

    <!-- Overflow top indicator -->
    <div v-if="hasOverflowTop" class="overflow-indicator top">⋯</div>

    <!-- Bookmarks -->
    <div class="bookmark-list">
      <div
        v-for="(bm, idx) in visibleBookmarks"
        :key="bm.panelKey"
        class="bookmark-item"
        :style="{ transform: `scale(${fisheyeScale(idx)})` }"
        @contextmenu="showContextMenu(bm.panelKey, $event)"
      >
        <button
          type="button"
          class="bookmark-tab"
          :class="{ active: bm.status === 'active', open: isTabOpen(bm.panelKey) }"
          @click="emit('open-session', bm.panelKey)"
        >
          <div class="bm-top-row">
            <span class="bm-role">{{ bm.role }}</span>
            <span class="bm-status-dot" :class="bm.status"></span>
          </div>
          <span class="bm-title">{{ bm.sessionName || (bm.sessionId ? bm.sessionId.slice(0, 10) : bm.role) }}</span>
          <div class="bm-bottom-row">
            <span class="bm-agent">{{ bm.displayName }}</span>
            <span class="bm-time">{{ formatTime(bm.lastActiveAt) }}</span>
          </div>
        </button>
        <button
          type="button"
          class="bm-close"
          aria-label="Hide bookmark"
          @click="closeBookmark(bm.panelKey, $event)"
        >&times;</button>
      </div>
    </div>

    <!-- Overflow bottom indicator -->
    <div v-if="hasOverflowBottom" class="overflow-indicator bottom">⋯</div>

    <!-- Create new session button -->
    <button class="bookmark-add" title="New sub-agent session" @click="emit('create-session')">
      <span class="add-icon">+</span>
    </button>

    <!-- Context menu -->
    <Teleport to="body">
      <div
        v-if="contextMenu"
        class="bm-context-menu"
        :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
        @click="hideContextMenu"
      >
        <button class="ctx-item" @click="closeBookmark(contextMenu.panelKey, $event)">
          Hide Bookmark
        </button>
        <button class="ctx-item ctx-danger" @click="handleDeleteSession(contextMenu.panelKey, $event)">
          Delete Session
        </button>
      </div>
      <div v-if="contextMenu" class="ctx-backdrop" @click="hideContextMenu" />
    </Teleport>
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
  background: var(--bg-secondary);
  border-left: 1px solid rgba(0, 212, 255, 0.15);
  box-shadow: inset 1px 0 8px rgba(0, 212, 255, 0.04);
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

.bookmark-item {
  position: relative;
  transform-origin: right center;
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
  transition: background 0.15s ease, box-shadow 0.15s ease;
  position: relative;
}

.bm-close {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  border: none;
  background: none;
  color: var(--text-muted);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
  opacity: 0;
  pointer-events: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 3px;
  z-index: 3;
}

.bm-close:hover {
  background: rgba(255, 100, 100, 0.2);
  color: var(--accent-error, #ff6464);
}

.bookmark-item:hover .bm-close,
.bookmark-item:focus-within .bm-close {
  opacity: 1;
  pointer-events: auto;
}

.bookmark-item:hover {
  z-index: 2;
}

.bookmark-item:hover .bookmark-tab {
  background: var(--bg-panel);
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.3);
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

/* ─── Context Menu ─── */
.bm-context-menu {
  position: fixed;
  z-index: 9999;
  background: var(--bg-panel, #1e1e2e);
  border: 1px solid var(--border, #333);
  border-radius: 6px;
  padding: 4px 0;
  min-width: 140px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.ctx-item {
  display: block;
  width: 100%;
  padding: 6px 14px;
  background: none;
  border: none;
  color: var(--text-secondary, #ccc);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.ctx-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-primary, #fff);
}

.ctx-danger:hover {
  background: rgba(255, 100, 100, 0.15);
  color: var(--accent-error, #ff6464);
}

.ctx-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9998;
}
</style>
