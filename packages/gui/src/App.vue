<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import TitleBar from "./components/TitleBar.vue";
import AgentPanel from "./components/AgentPanel.vue";
import EventLog from "./components/EventLog.vue";
import SettingsPanel from "./components/SettingsPanel.vue";
import TaskDashboard from "./components/TaskDashboard.vue";
import SessionPicker from "./components/SessionPicker.vue";
import HistoryPanel from "./components/HistoryPanel.vue";
import ApprovalQueue from "./components/ApprovalQueue.vue";
import BookmarkRail from "./components/BookmarkRail.vue";
import FloatingPanel from "./components/FloatingPanel.vue";
import { useAgentStore } from "./stores/agents";
import { useApprovalStore } from "./stores/approvals";
import { useMessageStore } from "./stores/messages";
import { useEventStore } from "./stores/events";
import { useTaskStore } from "./stores/tasks";

const {
  agents, mainAgent, rolePanels, sidecarReady, sidecarError, initAgents,
  openFloatingTab, bookmarkList,
} = useAgentStore();
const { initMessageListeners } = useMessageStore();
const { initApprovalStore } = useApprovalStore();
const { initEventListeners } = useEventStore();
const { loadTasks, initTaskListeners } = useTaskStore();

const showSettings = ref(false);
const activeView = ref<"agents" | "tasks">("agents");
const showEventLog = ref(false);
const showAgentRoleSelector = ref(false);
/** Whether a main-role agent is configured and available. */
const hasMainAgent = computed(() => Boolean(mainAgent.value));

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
</script>

<template>
  <div class="app-shell">
    <TitleBar
      :activeView="activeView"
      :eventLogOpen="showEventLog"
      @open-settings="showSettings = true"
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
            <!-- Main Agent: full width -->
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
            <!-- Bookmark rail (right edge) -->
            <BookmarkRail
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

    <SessionPicker />
    <HistoryPanel />
    <ApprovalQueue />
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
  gap: var(--panel-gap);
  padding: var(--panel-gap);
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
  position: relative;
  min-height: 0;
  height: 100%;
  flex: 1;
  gap: 0;
}

.main-agent-area {
  flex: 1;
  min-height: 0;
  min-width: 0;
}

.main-agent-area :deep(.agent-panel) {
  height: 100%;
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
</style>
