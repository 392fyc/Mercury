<script setup lang="ts">
import { computed, ref } from "vue";
import AgentPanel from "./AgentPanel.vue";
import { useAgentStore } from "../stores/agents";

const { openFloatingTabs, closeFloatingTab, agents, bookmarkList, parsePanelKey } = useAgentStore();

/** Currently active tab index within the floating panel. */
const activeTabIndex = ref(0);

const hasTabs = computed(() => openFloatingTabs.value.length > 0);

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

const currentTab = computed(() => {
  const idx = Math.min(activeTabIndex.value, tabs.value.length - 1);
  return tabs.value[idx] ?? null;
});

function selectTab(index: number) {
  activeTabIndex.value = index;
}

function closeTab(panelKey: string) {
  const idx = openFloatingTabs.value.indexOf(panelKey);
  closeFloatingTab(panelKey);
  if (idx <= activeTabIndex.value && activeTabIndex.value > 0) {
    activeTabIndex.value--;
  }
}

function minimizeAll() {
  // Close all floating tabs (bookmarks remain)
  for (const pk of [...openFloatingTabs.value]) {
    closeFloatingTab(pk);
  }
  activeTabIndex.value = 0;
}
</script>

<template>
  <Transition name="slide">
    <div v-if="hasTabs" class="floating-panel">
      <!-- Tab bar -->
      <div class="fp-tab-bar">
        <button
          v-for="(tab, idx) in tabs"
          :key="tab.panelKey"
          class="fp-tab"
          :class="{ active: idx === Math.min(activeTabIndex, tabs.length - 1) }"
          @click="selectTab(idx)"
        >
          <span class="fp-tab-role">{{ tab.role.slice(0, 3) }}</span>
          <span class="fp-tab-label">{{ tab.label }}</span>
          <button class="fp-tab-close" @click.stop="closeTab(tab.panelKey)" title="Close tab">&times;</button>
        </button>
        <button class="fp-minimize-all" title="Minimize all to bookmarks" @click="minimizeAll">
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
  top: 16px;
  bottom: 16px;
  right: 140px; /* bookmark rail width (136px) + gap */
  width: 33%;
  min-width: 360px;
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: -6px 0 24px rgba(0, 0, 0, 0.35), 0 4px 16px rgba(0, 0, 0, 0.15);
  z-index: 10;
  overflow: hidden;
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

/* Slide-in animation */
.slide-enter-active {
  transition: transform 0.2s ease-out, opacity 0.2s ease-out;
}
.slide-leave-active {
  transition: transform 0.15s ease-in, opacity 0.15s ease-in;
}
.slide-enter-from {
  transform: translateX(20px);
  opacity: 0;
}
.slide-leave-to {
  transform: translateX(20px);
  opacity: 0;
}
</style>
