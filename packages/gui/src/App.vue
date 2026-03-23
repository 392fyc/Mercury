<script setup lang="ts">
import { nextTick, onMounted, ref } from "vue";
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
import { Splitpanes, Pane } from "splitpanes";
import "splitpanes/dist/splitpanes.css";
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

// Splitpanes initial size fix: force recalculation after mount
// See: https://github.com/antoniandre/splitpanes/issues/108
const splitpanesKey = ref(0);

function forceSplitpanesRecalc() {
  nextTick(() => {
    splitpanesKey.value++;
  });
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
  // Force splitpanes recalculation after everything is mounted
  forceSplitpanesRecalc();
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
            <Splitpanes :key="splitpanesKey" class="default-theme mercury-splitpanes">
              <!-- Explorer pane: resizable -->
              <Pane :size="15" :min-size="8" :max-size="25">
                <ExplorerPanel
                  @open-file="(_path, _name) => { /* TODO: open file in center area */ }"
                />
              </Pane>
              <!-- Center pane: Agent chat area -->
              <Pane :min-size="50">
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
              </Pane>
            </Splitpanes>
            <!-- Sessions panel: fixed width, not resizable -->
            <SessionsPanel
              @open-session="handleOpenSession"
              @create-session="handleCreateSession"
            />
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
  height: 100%;
  flex: 1;
}

.center-pane {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
}

.main-agent-area {
  flex: 1;
  min-height: 0;
  min-width: 0;
  position: relative;
}

.main-agent-area :deep(.agent-panel) {
  height: 100%;
  border: none;
  border-radius: 0;
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

/* ─── Splitpanes dark theme overrides ─── */
.agents-area :deep(.mercury-splitpanes) {
  flex: 1;
  min-width: 0;
  height: 100%;
}

.agents-area :deep(.splitpanes__splitter) {
  background: var(--border);
  width: 3px !important;
  min-width: 3px !important;
  border: none;
  position: relative;
  transition: background 0.15s;
}

.agents-area :deep(.splitpanes__splitter:hover),
.agents-area :deep(.splitpanes__splitter.splitpanes__splitter__active) {
  background: var(--accent-main);
}

.agents-area :deep(.splitpanes__splitter::before),
.agents-area :deep(.splitpanes__splitter::after) {
  display: none;
}

.agents-area :deep(.splitpanes__pane) {
  overflow: hidden;
  height: 100%;
}
</style>
