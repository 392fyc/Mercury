<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { useAgentStore } from "../stores/agents";
import { getProjectInfo } from "../lib/tauri-bridge";

const props = defineProps<{
  activeView: "agents" | "tasks";
  eventLogOpen?: boolean;
}>();
const emit = defineEmits<{
  "open-settings": [];
  "switch-view": [view: "agents" | "tasks"];
  "toggle-event-log": [];
}>();

const { sidecarReady } = useAgentStore();

const projectRoot = ref("");
const gitBranch = ref<string | null>(null);


async function loadProjectInfo() {
  try {
    const info = await getProjectInfo();
    projectRoot.value = info.projectRoot;
    gitBranch.value = info.gitBranch;
  } catch {
    // Project info unavailable — not critical
  }
}

function handleVisibilityChange() {
  if (document.visibilityState === "visible") loadProjectInfo();
}

onMounted(() => {
  loadProjectInfo();
  // Refresh git branch when user returns to the window (not on every element focus)
  document.addEventListener("visibilitychange", handleVisibilityChange);
});

onUnmounted(() => {
  document.removeEventListener("visibilitychange", handleVisibilityChange);
});

</script>

<template>
  <header class="titlebar" data-tauri-drag-region>
    <div class="titlebar-left">
      <span class="logo">☿</span>
      <span class="title">Mercury</span>
      <span class="badge">v0.1.0</span>
      <div class="view-tabs">
        <button
          class="tab-btn"
          :class="{ active: props.activeView === 'agents' }"
          @click="emit('switch-view', 'agents')"
        >Agents</button>
        <button
          class="tab-btn"
          :class="{ active: props.activeView === 'tasks' }"
          @click="emit('switch-view', 'tasks')"
        >Tasks</button>
      </div>
    </div>
    <div class="titlebar-center" data-tauri-drag-region></div>
    <div class="titlebar-right">
      <button
        class="titlebar-btn"
        :class="{ 'btn-active': props.eventLogOpen }"
        title="Toggle Event Log"
        @click="emit('toggle-event-log')"
      >⚡</button>
      <button class="titlebar-btn" title="Settings" @click="emit('open-settings')">⚙</button>
    </div>
  </header>
</template>

<style scoped>
.titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 44px;
  padding: 0 14px;
  background: var(--bg-secondary);
  border-bottom: 1px solid rgba(0, 212, 255, 0.1);
  box-shadow: 0 1px 6px rgba(0, 212, 255, 0.03);
  user-select: none;
  -webkit-app-region: drag;
}

.titlebar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logo {
  font-size: 20px;
}

.title {
  font-weight: 600;
  font-size: 15px;
  color: var(--text-primary);
}

.badge {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--bg-panel);
  color: var(--text-muted);
}

.view-tabs {
  display: flex;
  gap: 0;
  margin-left: 12px;
  align-self: stretch; /* stretch to full titlebar height */
}

.tab-btn {
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 12px;
  padding: 0 12px;
  cursor: pointer;
  -webkit-app-region: no-drag;
  display: flex;
  align-items: center;
  margin-bottom: -1px; /* overlap the titlebar border-bottom */
}

.tab-btn:hover {
  color: var(--text-secondary);
}

.tab-btn.active {
  color: var(--text-primary);
  border-bottom-color: var(--accent-main);
}

.titlebar-center {
  display: flex;
  align-items: center;
  gap: 8px;
}

.titlebar-right {
  display: flex;
  gap: 4px;
}

.titlebar-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 16px;
  width: 32px;
  height: 32px;
  border-radius: var(--radius);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  -webkit-app-region: no-drag;
}

.titlebar-btn:hover {
  background: var(--bg-panel);
  color: var(--text-primary);
}

.titlebar-btn.btn-active {
  background: rgba(0, 212, 255, 0.15);
  color: var(--accent-main);
}
</style>
