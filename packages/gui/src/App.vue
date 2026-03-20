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
import { useAgentStore } from "./stores/agents";
import { useApprovalStore } from "./stores/approvals";
import { useMessageStore } from "./stores/messages";
import { useEventStore } from "./stores/events";
import { useTaskStore } from "./stores/tasks";

const { agents, mainAgent, rolePanels, sidecarReady, sidecarError, initAgents } =
  useAgentStore();
const { initMessageListeners } = useMessageStore();
const { initApprovalStore } = useApprovalStore();
const { initEventListeners } = useEventStore();
const { loadTasks, initTaskListeners } = useTaskStore();

const showSettings = ref(false);
const activeView = ref<"agents" | "tasks">("agents");
const showEventLog = ref(false);
/** Whether a main-role agent is configured and available. */
const hasMainAgent = computed(() => Boolean(mainAgent.value));
/** Number of non-main (sub) agent panels currently rendered. */
const subAgentCount = computed(() => rolePanels.value.length);

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
          <div
            v-if="agents.length > 0"
            class="agents-area"
            :class="{
              'main-only': hasMainAgent && subAgentCount === 0,
              'single-sub-agent': subAgentCount === 1,
              'multi-sub-agents': subAgentCount > 1,
              'sub-agents-only': !hasMainAgent && subAgentCount > 0,
            }"
          >
            <AgentPanel
              v-if="mainAgent"
              :agentId="mainAgent.id"
              :agentName="mainAgent.displayName"
              :role="'main'"
              :panelKey="`main:${mainAgent.id}`"
            />
            <div
              v-if="subAgentCount > 0"
              class="sub-agents"
              :class="{
                'single-panel': subAgentCount === 1,
                'multi-panel': subAgentCount > 1,
              }"
            >
              <AgentPanel
                v-for="panel in rolePanels"
                :key="panel.panelKey"
                :agentId="panel.agentId"
                :agentName="panel.displayName"
                :role="panel.role"
                :panelKey="panel.panelKey"
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
  --agent-panel-min-height: 280px;
  display: grid;
  grid-template-columns: minmax(0, 1.08fr) minmax(0, 0.92fr);
  gap: var(--panel-gap);
  min-height: 0;
  height: 100%;
  align-items: stretch;
  flex: 1;
}

.agents-area :deep(.agent-panel) {
  min-height: var(--agent-panel-min-height);
  height: 100%;
}

.agents-area.single-sub-agent {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.agents-area.main-only,
.agents-area.sub-agents-only {
  grid-template-columns: minmax(0, 1fr);
}

.sub-agents {
  display: grid;
  gap: var(--panel-gap);
  min-height: 0;
  min-width: 0;
  align-content: stretch;
}

.sub-agents.single-panel {
  grid-template-rows: minmax(0, 1fr);
  overflow: hidden;
}

.sub-agents.multi-panel {
  grid-auto-rows: minmax(var(--agent-panel-min-height), 1fr);
  overflow-y: auto;
  padding-right: 2px;
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

  .agents-area,
  .agents-area.single-sub-agent {
    grid-template-columns: 1fr;
    grid-auto-rows: minmax(var(--agent-panel-min-height), auto);
    overflow-y: auto;
    align-content: start;
  }

  .sub-agents.single-panel,
  .sub-agents.multi-panel {
    grid-template-rows: none;
    grid-auto-rows: minmax(var(--agent-panel-min-height), auto);
    overflow: visible;
    padding-right: 0;
  }
}
</style>
