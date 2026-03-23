<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import TitleBar from "./components/TitleBar.vue";
import AgentPanel from "./components/AgentPanel.vue";
import EventLog from "./components/EventLog.vue";
import SettingsPanel from "./components/SettingsPanel.vue";
import TaskDashboard from "./components/TaskDashboard.vue";
import SessionPicker from "./components/SessionPicker.vue";
import HistoryPanel from "./components/HistoryPanel.vue";
import ApprovalQueue from "./components/ApprovalQueue.vue";
import SessionsPanel from "./components/SessionsPanel.vue";
import ExplorerPanel from "./components/ExplorerPanel.vue";
import FloatingPanel from "./components/FloatingPanel.vue";
// Local SFC import — https://vuejs.org/guide/components/registration
import FilePreview from "./components/FilePreview.vue";
import AgentRoleSelector from "./components/AgentRoleSelector.vue";
import RemoteControlPanel from "./components/RemoteControlPanel.vue";
import PRMonitorPanel from "./components/PRMonitorPanel.vue";
import { useAgentStore } from "./stores/agents";
import { useApprovalStore } from "./stores/approvals";
import { useMessageStore } from "./stores/messages";
import { useEventStore } from "./stores/events";
import { useTaskStore } from "./stores/tasks";

const {
  agents, mainAgent, sidecarReady, sidecarError, initAgents,
  openFloatingTab,
} = useAgentStore();
const { initMessageListeners } = useMessageStore();
const { initApprovalStore } = useApprovalStore();
const { initEventListeners } = useEventStore();
const { loadTasks, initTaskListeners } = useTaskStore();

const showSettings = ref(false);
const showRemoteControl = ref(false);
const showPrMonitor = ref(false);
const activeView = ref<"agents" | "tasks">("agents");
const showEventLog = ref(false);
const showAgentRoleSelector = ref(false);
const splitShellEl = ref<HTMLDivElement | null>(null);
const isExplorerResizing = ref(false);

// ─── Center area tab switching (Agent ↔ File Preview) ───
const centerTab = ref<"agent" | "file">("agent");
const openFilePath = ref("");
const openFileName = ref("");

function handleOpenFile(path: string, name: string) {
  openFilePath.value = path;
  openFileName.value = name;
  centerTab.value = "file";
}

function switchToAgent() {
  centerTab.value = "agent";
}

// ─── Explorer resize with localStorage persistence ───
// Uses Window.localStorage (MDN Web API) for cross-session persistence
const EXPLORER_STORAGE_KEY = "mercury-explorer-size";
const EXPLORER_MIN_SIZE = 8;
const EXPLORER_MAX_SIZE = 25;

function loadExplorerSize(): number {
  try {
    const saved = localStorage.getItem(EXPLORER_STORAGE_KEY);
    if (saved) {
      const val = parseFloat(saved);
      if (!isNaN(val)) return clampExplorerSize(val);
    }
  } catch { /* ignore */ }
  return 15;
}

const explorerSize = ref(loadExplorerSize());

function clampExplorerSize(size: number) {
  return Math.min(EXPLORER_MAX_SIZE, Math.max(EXPLORER_MIN_SIZE, size));
}

function updateExplorerSize(clientX: number) {
  const shell = splitShellEl.value;
  if (!shell) return;

  const rect = shell.getBoundingClientRect();
  if (rect.width <= 0) return;

  const nextSize = ((clientX - rect.left) / rect.width) * 100;
  explorerSize.value = clampExplorerSize(nextSize);
}

function handleExplorerPointerMove(event: PointerEvent) {
  if (!isExplorerResizing.value) return;
  event.preventDefault();
  updateExplorerSize(event.clientX);
}

function stopExplorerResize() {
  if (!isExplorerResizing.value) return;
  isExplorerResizing.value = false;
  document.body.classList.remove("explorer-resizing");
  window.removeEventListener("pointermove", handleExplorerPointerMove);
  window.removeEventListener("pointerup", stopExplorerResize);
  window.removeEventListener("pointercancel", stopExplorerResize);
  // Persist size to localStorage
  try { localStorage.setItem(EXPLORER_STORAGE_KEY, String(explorerSize.value)); } catch { /* ignore */ }
}

function startExplorerResize(event: PointerEvent) {
  if (event.button !== 0) return;
  event.preventDefault();
  isExplorerResizing.value = true;
  document.body.classList.add("explorer-resizing");
  updateExplorerSize(event.clientX);
  window.addEventListener("pointermove", handleExplorerPointerMove, { passive: false });
  window.addEventListener("pointerup", stopExplorerResize);
  window.addEventListener("pointercancel", stopExplorerResize);
}

function handleOpenSession(panelKey: string) {
  openFloatingTab(panelKey);
}

function handleCreateSession() {
  showAgentRoleSelector.value = true;
}

onMounted(async () => {
  await initAgents();
  await initMessageListeners();
  await initApprovalStore();
  await initEventListeners();
  await initTaskListeners();
  await loadTasks();
});

onBeforeUnmount(() => {
  stopExplorerResize();
});
</script>

<template>
  <div class="app-shell">
    <TitleBar
      :activeView="activeView"
      :eventLogOpen="showEventLog"
      @open-settings="showSettings = true"
      @open-remote-control="showRemoteControl = true"
      @open-pr-monitor="showPrMonitor = true"
      @switch-view="(v) => activeView = v"
      @toggle-event-log="showEventLog = !showEventLog"
    />
    <SettingsPanel v-if="showSettings" @close="showSettings = false" />

    <div v-if="sidecarError" class="error-banner">
      Orchestrator error: {{ sidecarError }}
    </div>

    <div class="workspace" :class="{ 'event-log-visible': showEventLog }">
      <div class="workspace-main">
        <!-- Agents View -->
        <div v-show="activeView === 'agents'" class="workspace-view">
          <div v-if="agents.length > 0" class="agents-area">
            <div ref="splitShellEl" class="agents-split-shell">
              <div class="explorer-pane" :style="{ flexBasis: `${explorerSize}%` }">
                <ExplorerPanel
                  @open-file="handleOpenFile"
                />
              </div>
              <div
                class="explorer-resizer"
                :class="{ active: isExplorerResizing }"
                role="separator"
                aria-label="Resize explorer"
                aria-orientation="vertical"
                :aria-valuemin="EXPLORER_MIN_SIZE"
                :aria-valuemax="EXPLORER_MAX_SIZE"
                :aria-valuenow="Math.round(explorerSize)"
                @pointerdown="startExplorerResize"
              />
              <div class="center-pane">
                <!-- Tab bar for Agent ↔ File switching -->
                <div v-if="centerTab === 'file'" class="center-tab-bar">
                  <button class="center-tab" @click="switchToAgent">Agent</button>
                  <button class="center-tab active">
                    <span class="tab-file-icon">📄</span>
                    {{ openFileName }}
                  </button>
                  <button class="center-tab-close" @click="switchToAgent" title="Close file">&times;</button>
                </div>

                <!-- Agent Panel (always mounted, visibility toggled) -->
                <div v-show="centerTab === 'agent'" class="main-agent-area">
                  <AgentPanel
                    v-if="mainAgent"
                    :agentId="mainAgent.id"
                    :agentName="mainAgent.displayName"
                    :role="'main'"
                    :panelKey="`main:${mainAgent.id}`"
                  />
                </div>

                <!-- File Preview (shown when a file is open) -->
                <div v-if="centerTab === 'file'" class="file-preview-area">
                  <FilePreview :filePath="openFilePath" :fileName="openFileName" />
                </div>

                <!-- Floating sub-agent panel (overlays right side) -->
                <FloatingPanel />
              </div>
            </div>
            <div class="sessions-rail">
              <!-- Sessions panel: fixed width, not resizable -->
              <SessionsPanel
                @open-session="handleOpenSession"
                @create-session="handleCreateSession"
              />
            </div>
          </div>
          <div v-else class="loading-state">
            <p v-if="!sidecarReady">Connecting to orchestrator...</p>
            <p v-else>No agents configured</p>
          </div>
        </div>

        <!-- Tasks View -->
        <TaskDashboard v-show="activeView === 'tasks'" class="workspace-view" />
      </div>

      <EventLog v-if="showEventLog" />
    </div>

    <RemoteControlPanel
      v-if="showRemoteControl"
      @close="showRemoteControl = false"
    />
    <PRMonitorPanel
      v-if="showPrMonitor"
      @close="showPrMonitor = false"
    />
    <SessionPicker />
    <HistoryPanel />
    <ApprovalQueue />
    <AgentRoleSelector
      v-if="showAgentRoleSelector"
      @close="showAgentRoleSelector = false"
      @created="showAgentRoleSelector = false"
    />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.error-banner {
  padding: 8px 16px;
  background: rgba(255, 82, 82, 0.15);
  color: var(--accent-error);
  font-size: 12px;
  border-bottom: 1px solid var(--accent-error);
}

.workspace {
  flex: 1;
  display: grid;
  grid-template-rows: minmax(0, 1fr);
  gap: 0;
  padding: 0;
  min-height: 0;
}

.workspace.event-log-visible {
  grid-template-rows: minmax(0, 1fr) clamp(120px, 18vh, 152px);
}

.workspace-main {
  position: relative;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.workspace-view {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.agents-area {
  display: flex;
  min-height: 0;
  min-width: 0;
  height: 100%;
  flex: 1;
  overflow: hidden;
}

.agents-split-shell {
  display: flex;
  flex: 1 1 0%;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

.explorer-pane {
  display: flex;
  flex: 0 0 auto;
  min-width: 0;
  min-height: 0;
  height: 100%;
  overflow: hidden;
}

.explorer-resizer {
  position: relative;
  flex: 0 0 3px;
  width: 3px;
  min-width: 3px;
  background: var(--border);
  cursor: col-resize;
  touch-action: none;
  transition: background 0.15s;
}

.explorer-resizer:hover,
.explorer-resizer.active {
  background: var(--accent-main);
}

.explorer-resizer::before {
  content: "";
  position: absolute;
  top: 0;
  bottom: 0;
  left: -4px;
  right: -4px;
}

.sessions-rail {
  display: flex;
  flex: 0 0 390px;
  width: 390px;
  min-width: 390px;
  max-width: 390px;
  min-height: 0;
  overflow: hidden;
  background: var(--bg-secondary);
  border-left: 1px solid var(--border);
}

.center-pane {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  min-width: 0;
  overflow: hidden;
  background: var(--bg-primary);
}

.main-agent-area {
  display: flex;
  flex: 1;
  min-height: 0;
  min-width: 0;
  position: relative;
}

.main-agent-area :deep(.agent-panel) {
  flex: 1 1 auto;
  height: 100%;
  border: none;
  border-radius: 0;
  min-width: 0;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
  color: var(--text-muted);
  font-size: 14px;
}

@media (max-width: 1180px) {
  .workspace.event-log-visible {
    grid-template-rows: minmax(0, 1fr) clamp(112px, 16vh, 136px);
  }
}

/* ─── Center tab bar (Agent ↔ File) ─── */
.center-tab-bar {
  display: flex;
  align-items: center;
  gap: 1px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  height: 34px;
}

.center-tab {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
  white-space: nowrap;
  transition: color 0.15s;
}

.center-tab:hover { color: var(--text-secondary); }
.center-tab.active {
  color: var(--text-primary);
  border-bottom-color: var(--accent-main);
}

.tab-file-icon { font-size: 12px; }

.center-tab-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 14px;
  cursor: pointer;
  padding: 2px 6px;
  margin-left: auto;
  border-radius: 3px;
}
.center-tab-close:hover {
  color: var(--accent-error);
  background: rgba(255, 82, 82, 0.1);
}

/* ─── File preview placeholder ─── */
.file-preview-area {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-primary);
}


:global(body.explorer-resizing) {
  cursor: col-resize;
  user-select: none;
}
</style>
