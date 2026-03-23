<script setup lang="ts">
import { computed, ref } from "vue";
import { useAgentStore } from "../stores/agents";
import { deleteSession as rpcDeleteSession } from "../lib/tauri-bridge";
import type { BookmarkInfo } from "../stores/agents";

const emit = defineEmits<{
  "open-session": [panelKey: string];
  "create-session": [];
}>();

const {
  bookmarkList, openFloatingTabs, removeBookmark, parsePanelKey, getSession,
} = useAgentStore();

// ─── Group collapse state ───
type RoleGroup = "dev" | "research" | "acceptance" | "design";
const ROLE_ORDER: RoleGroup[] = ["dev", "research", "acceptance", "design"];

const collapsedGroups = ref<Set<string>>(new Set());

function toggleGroup(role: string) {
  const next = new Set(collapsedGroups.value);
  if (next.has(role)) next.delete(role);
  else next.add(role);
  collapsedGroups.value = next;
}

// ─── Grouped bookmarks ───
interface GroupedSessions {
  role: RoleGroup;
  items: BookmarkInfo[];
}

const groupedSessions = computed<GroupedSessions[]>(() => {
  const map = new Map<string, BookmarkInfo[]>();
  for (const bm of bookmarkList.value) {
    const list = map.get(bm.role) ?? [];
    list.push(bm);
    map.set(bm.role, list);
  }
  const result: GroupedSessions[] = [];
  for (const role of ROLE_ORDER) {
    const items = map.get(role);
    if (items && items.length > 0) {
      result.push({ role, items });
    }
  }
  return result;
});

const totalCount = computed(() => bookmarkList.value.length);

// ─── Session actions ───
function isTabOpen(panelKey: string): boolean {
  return openFloatingTabs.value.includes(panelKey);
}

function formatTime(ts: number): string {
  if (!ts) return "";
  const d = new Date(ts);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return "now";
  if (diffMin < 60) return `${diffMin}m`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h`;
  const diffD = Math.floor(diffH / 24);
  return `${diffD}d`;
}

async function handleDeleteSession(panelKey: string, event: Event) {
  event.stopPropagation();
  const { agentId } = parsePanelKey(panelKey);
  const sessionId = getSession(panelKey);
  if (sessionId && agentId) {
    try {
      await rpcDeleteSession(agentId, sessionId);
    } catch (err) {
      console.warn("[SessionsPanel] deleteSession RPC failed:", err);
    }
  }
  removeBookmark(panelKey);
}

// ─── Context menu ───
const contextMenu = ref<{ panelKey: string; x: number; y: number } | null>(null);

function showContextMenu(panelKey: string, event: MouseEvent) {
  event.preventDefault();
  event.stopPropagation();
  const x = Math.min(event.clientX, window.innerWidth - 160);
  const y = Math.min(event.clientY, window.innerHeight - 80);
  contextMenu.value = { panelKey, x, y };
}

function hideContextMenu() {
  contextMenu.value = null;
}

// ─── Role colors ───
function roleColor(role: string): string {
  switch (role) {
    case "dev": return "var(--accent-main)";
    case "research": return "var(--accent-success)";
    case "acceptance": return "var(--accent-sub)";
    case "design": return "var(--accent-warn)";
    default: return "var(--text-muted)";
  }
}
</script>

<template>
  <div class="sessions-panel" @keydown.esc="hideContextMenu">
    <!-- Header -->
    <div class="sp-header">
      <span class="sp-title">Sessions</span>
      <span class="sp-count">{{ totalCount }}</span>
    </div>

    <!-- Session list -->
    <div class="sp-list">
      <template v-for="group in groupedSessions" :key="group.role">
        <!-- Group header -->
        <div
          class="sp-group-header"
          :class="{ collapsed: collapsedGroups.has(group.role) }"
          @click="toggleGroup(group.role)"
        >
          <span class="sp-arrow">▼</span>
          <span class="sp-group-label">{{ group.role }}</span>
          <span class="sp-group-count">{{ group.items.length }}</span>
        </div>

        <!-- Group items -->
        <div
          class="sp-group-items"
          :class="{ collapsed: collapsedGroups.has(group.role) }"
        >
          <div
            v-for="bm in group.items"
            :key="bm.panelKey"
            class="sp-item"
            :class="{ active: isTabOpen(bm.panelKey) }"
            @click="emit('open-session', bm.panelKey)"
            @contextmenu="showContextMenu(bm.panelKey, $event)"
          >
            <span
              class="sp-dot"
              :class="{ running: bm.status === 'active' }"
              :style="{ background: roleColor(bm.role) }"
            />
            <div class="sp-info">
              <div class="sp-name">
                {{ bm.sessionName || bm.sessionId?.slice(0, 12) || bm.role }}
              </div>
              <div class="sp-meta">
                {{ bm.status === 'active' ? 'active' : 'idle' }}
                · {{ formatTime(bm.lastActiveAt) }}
              </div>
            </div>
            <button
              type="button"
              class="sp-delete"
              title="Delete Session"
              @click="handleDeleteSession(bm.panelKey, $event)"
            >×</button>
          </div>
        </div>
      </template>

      <!-- Empty state -->
      <div v-if="groupedSessions.length === 0" class="sp-empty">
        No active sessions
      </div>
    </div>

    <!-- New session button -->
    <button class="sp-add" @click="emit('create-session')">
      + New Session
    </button>

    <!-- Context menu -->
    <Teleport to="body">
      <div
        v-if="contextMenu"
        class="sp-ctx-menu"
        :style="{ left: contextMenu.x + 'px', top: contextMenu.y + 'px' }"
        @click="hideContextMenu"
      >
        <button class="sp-ctx-item sp-ctx-danger" @click="handleDeleteSession(contextMenu.panelKey, $event)">
          Delete Session
        </button>
      </div>
      <div v-if="contextMenu" class="sp-ctx-backdrop" @click="hideContextMenu" />
    </Teleport>
  </div>
</template>

<style scoped>
.sessions-panel {
  display: flex;
  flex-direction: column;
  width: 180px;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
  user-select: none;
  flex-shrink: 0;
  overflow: hidden;
}

/* ─── Header ─── */
.sp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px 8px;
  flex-shrink: 0;
}

.sp-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: var(--text-secondary);
}

.sp-count {
  background: rgba(0, 212, 255, 0.12);
  color: var(--accent-main);
  padding: 1px 6px;
  border-radius: 8px;
  font-size: 9px;
  font-weight: 600;
}

/* ─── List ─── */
.sp-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 6px 6px;
}

/* ─── Group header ─── */
.sp-group-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 8px 4px;
  cursor: pointer;
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 1px;
  color: var(--text-muted);
  transition: color 0.15s;
}

.sp-group-header:hover {
  color: var(--text-secondary);
}

.sp-arrow {
  font-size: 8px;
  transition: transform 0.2s;
  display: inline-block;
}

.sp-group-header.collapsed .sp-arrow {
  transform: rotate(-90deg);
}

.sp-group-label {
  flex: 1;
}

.sp-group-count {
  background: rgba(0, 212, 255, 0.1);
  color: var(--accent-main);
  padding: 0 5px;
  border-radius: 8px;
  font-size: 8px;
  line-height: 16px;
}

/* ─── Group items (collapsible) ─── */
.sp-group-items {
  overflow: hidden;
  max-height: 500px;
  transition: max-height 0.25s ease, opacity 0.2s ease;
  opacity: 1;
}

.sp-group-items.collapsed {
  max-height: 0;
  opacity: 0;
}

/* ─── Session item ─── */
.sp-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s;
  margin-bottom: 1px;
}

.sp-item:hover {
  background: rgba(0, 212, 255, 0.06);
}

.sp-item.active {
  background: rgba(0, 212, 255, 0.1);
}

/* ─── Status dot ─── */
.sp-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  position: relative;
}

.sp-dot.running::after {
  content: '';
  position: absolute;
  inset: -3px;
  border-radius: 50%;
  border: 1.5px solid currentColor;
  opacity: 0.4;
  animation: sp-pulse 2s infinite;
}

@keyframes sp-pulse {
  0%, 100% { opacity: 0.4; transform: scale(1); }
  50% { opacity: 0.1; transform: scale(1.3); }
}

/* ─── Info ─── */
.sp-info {
  flex: 1;
  min-width: 0;
}

.sp-name {
  font-size: 12px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.sp-meta {
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 1px;
}

/* ─── Delete button (hover only) ─── */
.sp-delete {
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  min-width: 20px;
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  border: none;
  background: transparent;
  flex-shrink: 0;
  opacity: 0;
  transition: all 0.15s;
  padding: 0;
}

.sp-item:hover .sp-delete {
  opacity: 1;
  color: var(--text-secondary);
}

.sp-delete:hover {
  color: var(--accent-error) !important;
  background: rgba(255, 82, 82, 0.15);
}

/* ─── Empty state ─── */
.sp-empty {
  padding: 24px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 11px;
}

/* ─── Add button ─── */
.sp-add {
  margin: 6px;
  padding: 8px;
  text-align: center;
  color: var(--text-muted);
  font-size: 11px;
  background: transparent;
  border: 1.5px dashed var(--border);
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.15s;
  flex-shrink: 0;
}

.sp-add:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
  background: rgba(0, 212, 255, 0.04);
}

/* ─── Context Menu ─── */
.sp-ctx-menu {
  position: fixed;
  z-index: 9999;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 0;
  min-width: 140px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

.sp-ctx-item {
  display: block;
  width: 100%;
  padding: 6px 14px;
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}

.sp-ctx-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-primary);
}

.sp-ctx-danger:hover {
  background: rgba(255, 100, 100, 0.15);
  color: var(--accent-error);
}

.sp-ctx-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9998;
}
</style>
