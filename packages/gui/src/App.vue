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
const explorerSize = ref(15);
const isExplorerResizing = ref(false);

const EXPLORER_MIN_SIZE = 8;
const EXPLORER_MAX_SIZE = 25;

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
                  @open-file="(_path, _name) => { /* TODO: open file in center area */ }"
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
                <div class="main-agent-area">
                  <AgentPanel
                    v-if="mainAgent"
                    :agentId="mainAgent.id"
                    :agentName="mainAgent.displayName"
                    :role="'main'"
                    :panelKey="`main:${mainAgent.id}`"
                  />
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
  flex: 0 0 300px;
  width: 300px;
  min-width: 300px;
  max-width: 300px;
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

:global(body.explorer-resizing) {
  cursor: col-resize;
  user-select: none;
}
</style>
