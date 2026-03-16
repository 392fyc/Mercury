<script setup lang="ts">
import { onMounted, ref } from "vue";
import TitleBar from "./components/TitleBar.vue";
import AgentPanel from "./components/AgentPanel.vue";
import EventLog from "./components/EventLog.vue";
import SettingsPanel from "./components/SettingsPanel.vue";
import TaskDashboard from "./components/TaskDashboard.vue";
import { useAgentStore } from "./stores/agents";
import { useMessageStore } from "./stores/messages";
import { useEventStore } from "./stores/events";
import { useTaskStore } from "./stores/tasks";

const { agents, mainAgent, subAgents, sidecarReady, sidecarError, initAgents } =
  useAgentStore();
const { initMessageListeners } = useMessageStore();
const { initEventListeners } = useEventStore();
const { loadTasks, initTaskListeners } = useTaskStore();

const showSettings = ref(false);
const activeView = ref<"agents" | "tasks">("agents");

onMounted(async () => {
  await initAgents();
  await initMessageListeners();
  await initEventListeners();
  await initTaskListeners();
  await loadTasks();
});
</script>

<template>
  <div class="app-shell">
    <TitleBar
      :activeView="activeView"
      @open-settings="showSettings = true"
      @switch-view="(v) => activeView = v"
    />
    <SettingsPanel v-if="showSettings" @close="showSettings = false" />

    <div v-if="sidecarError" class="error-banner">
      Orchestrator error: {{ sidecarError }}
    </div>

    <div class="workspace">
      <!-- Agents View -->
      <template v-if="activeView === 'agents'">
        <div class="agents-area" v-if="agents.length > 0">
          <AgentPanel
            v-if="mainAgent"
            :agentId="mainAgent.id"
            :agentName="mainAgent.displayName"
            :role="mainAgent.role"
          />
          <div class="sub-agents" v-if="subAgents.length > 0">
            <AgentPanel
              v-for="agent in subAgents"
              :key="agent.id"
              :agentId="agent.id"
              :agentName="agent.displayName"
              :role="agent.role"
            />
          </div>
        </div>
        <div v-else class="loading-state">
          <p v-if="!sidecarReady">Connecting to orchestrator...</p>
          <p v-else>No agents configured</p>
        </div>
      </template>

      <!-- Tasks View -->
      <TaskDashboard v-if="activeView === 'tasks'" />

      <EventLog />
    </div>
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
  grid-template-rows: 1fr 180px;
  gap: var(--panel-gap);
  padding: var(--panel-gap);
  min-height: 0;
}

.agents-area {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--panel-gap);
  min-height: 0;
}

.sub-agents {
  display: grid;
  grid-template-rows: repeat(auto-fit, minmax(0, 1fr));
  gap: var(--panel-gap);
  min-height: 0;
}

.loading-state {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  font-size: 14px;
}
</style>
