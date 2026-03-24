<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from "vue";
import AgentPanel from "./AgentPanel.vue";
import { useAgentStore } from "../stores/agents";

const { openFloatingTabs, closeFloatingTab, agents, bookmarkList, parsePanelKey } = useAgentStore();

/** Currently active tab index within the floating panel. */
const activeTabIndex = ref(0);

const hasTabs = computed(() => openFloatingTabs.value.length > 0);

/** Safe tab index clamped to valid range — avoids out-of-bounds after tab close. */
const safeActiveIndex = computed(() => Math.min(activeTabIndex.value, Math.max(0, openFloatingTabs.value.length - 1)));

const tabs = computed(() => {
  return openFloatingTabs.value.map((panelKey) => {
    const { role, agentId } = parsePanelKey(panelKey);
    const agent = agents.value.find((a) => a.id === agentId);
    const bm = bookmarkList.value.find((b) => b.panelKey === panelKey);
    return {
      panelKey,
      agentId,
      role,
      displayName: agent?.displayName ?? agentId,
      sessionName: bm?.sessionName,
      label: bm?.sessionName
        ? `${bm.sessionName.slice(0, 20)}${bm.sessionName.length > 20 ? "…" : ""}`
        : `${role}:${agentId.slice(0, 8)}`,
    };
  });
});

const currentTab = computed(() => tabs.value[safeActiveIndex.value] ?? null);

function selectTab(index: number) {
  activeTabIndex.value = index;
}

function closeTab(panelKey: string) {
  const idx = openFloatingTabs.value.indexOf(panelKey);
  if (idx < 0) return;
  closeFloatingTab(panelKey);
  if (idx <= activeTabIndex.value && activeTabIndex.value > 0) {
    activeTabIndex.value--;
  }
}

function minimizeAll() {
  for (const pk of [...openFloatingTabs.value]) {
    closeFloatingTab(pk);
  }
  activeTabIndex.value = 0;
}

// ─── Left-side resize (drag left edge to widen/narrow) ───
// Uses localStorage (MDN Web Storage API) for persistence
const FP_WIDTH_KEY = "mercury-floating-width";
const FP_MIN_WIDTH = 320;
const FP_MAX_WIDTH_RATIO = 0.65; // max 65% of center-pane

function loadWidth(): number {
  try {
    const saved = localStorage.getItem(FP_WIDTH_KEY);
    if (saved) {
      const val = parseFloat(saved);
      if (!isNaN(val) && val >= FP_MIN_WIDTH) return val;
    }
  } catch { /* ignore */ }
  return 480;
}

const panelWidth = ref(loadWidth());
const isResizing = ref(false);

function onResizePointerDown(e: PointerEvent) {
  if (e.button !== 0) return;
  e.preventDefault();
  isResizing.value = true;
  document.body.classList.add("fp-resizing");
  window.addEventListener("pointermove", onResizeMove, { passive: false });
  window.addEventListener("pointerup", onResizeUp);
  window.addEventListener("pointercancel", onResizeUp);
}

function onResizeMove(e: PointerEvent) {
  if (!isResizing.value) return;
  e.preventDefault();
  // FloatingPanel is right-aligned; its right edge aligns with right side of center-pane.
  // Width = container right edge - mouse X
  const parent = document.querySelector(".center-pane") as HTMLElement | null;
  if (!parent) return;
  const parentRect = parent.getBoundingClientRect();
  const maxW = parentRect.width * FP_MAX_WIDTH_RATIO;
  const raw = parentRect.right - e.clientX;
  panelWidth.value = Math.min(maxW, Math.max(FP_MIN_WIDTH, raw));
}

function onResizeUp() {
  if (!isResizing.value) return;
  isResizing.value = false;
  document.body.classList.remove("fp-resizing");
  window.removeEventListener("pointermove", onResizeMove);
  window.removeEventListener("pointerup", onResizeUp);
  window.removeEventListener("pointercancel", onResizeUp);
  try { localStorage.setItem(FP_WIDTH_KEY, String(Math.round(panelWidth.value))); } catch { /* ignore */ }
}

onBeforeUnmount(() => { onResizeUp(); });
</script>

<template>
  <Transition name="slide">
    <div v-if="hasTabs" class="floating-panel" :style="{ width: panelWidth + 'px' }">
      <!-- Left resize handle -->
      <div class="fp-resize-handle" @pointerdown="onResizePointerDown" />
      <!-- Tab bar -->
      <div class="fp-tab-bar" role="tablist" aria-label="Open sub-agent sessions">
        <div
          v-for="(tab, idx) in tabs"
          :key="tab.panelKey"
          class="fp-tab"
          :class="{ active: idx === safeActiveIndex }"
          role="tab"
          :aria-selected="idx === safeActiveIndex"
          tabindex="0"
          @click="selectTab(idx)"
          @keydown.enter="selectTab(idx)"
          @keydown.space.prevent="selectTab(idx)"
        >
          <span class="fp-tab-role">{{ tab.role.slice(0, 3) }}</span>
          <span class="fp-tab-label">{{ tab.label }}</span>
          <button type="button" class="fp-tab-close" @click.stop="closeTab(tab.panelKey)" title="Close tab">&times;</button>
        </div>
        <button type="button" class="fp-minimize-all" title="Minimize all to bookmarks" @click="minimizeAll">
          <span>⎽</span>
        </button>
      </div>

      <!-- Panel content: AgentPanel for the active tab -->
      <div v-if="currentTab" class="fp-body">
        <AgentPanel
          :key="currentTab.panelKey"
          :agentId="currentTab.agentId"
          :agentName="currentTab.displayName"
          :role="currentTab.role"
          :panelKey="currentTab.panelKey"
          :isFloating="true"
        />
      </div>
    </div>
  </Transition>
</template>

<style scoped>
.floating-panel {
  position: absolute;
  top: 0;
  bottom: 0;
  right: 0;
  min-width: 320px;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border-left: 1px solid rgba(0, 212, 255, 0.15);
  box-shadow: -4px 0 20px rgba(0, 0, 0, 0.35);
  z-index: 10;
  overflow: hidden;
}

.fp-resize-handle {
  position: absolute;
  top: 0;
  bottom: 0;
  left: -2px;
  width: 6px;
  cursor: col-resize;
  z-index: 11;
  transition: background 0.15s;
}

.fp-resize-handle:hover,
:global(body.fp-resizing) .fp-resize-handle {
  background: var(--accent-main);
}

.fp-tab-bar {
  display: flex;
  gap: 1px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
  flex-shrink: 0;
}

.fp-tab-bar::-webkit-scrollbar {
  height: 2px;
}

.fp-tab {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 8px;
  background: var(--bg-secondary);
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  min-width: 0;
  flex-shrink: 0;
}

.fp-tab:hover {
  color: var(--text-secondary);
  background: var(--bg-panel);
}

.fp-tab.active {
  color: var(--text-primary);
  border-bottom-color: var(--accent-main);
  background: var(--bg-secondary);
}

.fp-tab-role {
  font-size: 9px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--accent-sub);
}

.fp-tab-label {
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 120px;
}

.fp-tab-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
  border-radius: 2px;
}

.fp-tab-close:hover {
  color: var(--accent-error);
  background: rgba(255, 82, 82, 0.1);
}

.fp-minimize-all {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 3px;
  flex-shrink: 0;
}

.fp-minimize-all:hover {
  color: var(--text-primary);
  background: var(--bg-panel);
}

.fp-body {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

.fp-body :deep(.agent-panel) {
  height: 100%;
  border: none;
  border-radius: 0;
}

/* Slide-in animation from right — Vue 3 Transition classes */
/* Ref: https://vuejs.org/guide/built-ins/transition */
.slide-enter-active {
  transition: transform 0.2s ease-out, opacity 0.2s ease-out;
}
.slide-leave-active {
  transition: transform 0.15s ease-in, opacity 0.15s ease-in;
}
.slide-enter-from {
  transform: translateX(100%);
  opacity: 0;
}
.slide-leave-to {
  transform: translateX(100%);
  opacity: 0;
}

:global(body.fp-resizing) {
  cursor: col-resize;
  user-select: none;
}
</style>
