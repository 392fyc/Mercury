<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useAgentStore } from "../stores/agents";
import { getProjectInfo } from "../lib/tauri-bridge";

const props = defineProps<{ activeView: "agents" | "tasks" }>();
const emit = defineEmits<{ "open-settings": []; "switch-view": [view: "agents" | "tasks"] }>();

const { sidecarReady, anyActive, anyError } = useAgentStore();

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

const statusClass = computed(() => {
  if (anyError.value) return "error";
  if (anyActive.value) return "active";
  if (sidecarReady.value) return "ready";
  return "";
});

const statusText = computed(() => {
  if (anyError.value) return "Error";
  if (anyActive.value) return "Running";
  if (sidecarReady.value) return "Ready";
  return "Connecting...";
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
    <div class="titlebar-center" data-tauri-drag-region>
      <span class="status-dot" :class="statusClass"></span>
      <span class="status-text">{{ statusText }}</span>
    </div>
    <div class="titlebar-right">
      <button class="titlebar-btn" title="Event Log">⚡</button>
      <button class="titlebar-btn" title="Settings" @click="emit('open-settings')">⚙</button>
    </div>
  </header>
</template>

<style scoped>
.titlebar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 12px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  user-select: none;
  -webkit-app-region: drag;
}

.titlebar-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.logo {
  font-size: 18px;
}

.title {
  font-weight: 600;
  font-size: 14px;
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
  gap: 2px;
  margin-left: 8px;
}

.tab-btn {
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-muted);
  font-size: 11px;
  padding: 2px 8px 4px;
  cursor: pointer;
  -webkit-app-region: no-drag;
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

.project-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11px;
  padding-right: 8px;
  border-right: 1px solid var(--border);
  margin-right: 2px;
}

.project-name {
  color: var(--text-secondary);
  font-weight: 500;
}

.git-branch {
  display: flex;
  align-items: center;
  gap: 2px;
  color: var(--accent-main);
  font-family: var(--font-mono);
  font-size: 10px;
}

.branch-icon {
  font-size: 12px;
  line-height: 1;
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-muted);
}

.status-dot.ready {
  background: var(--accent-success);
  box-shadow: 0 0 4px var(--accent-success);
}

.status-dot.active {
  background: var(--accent-main);
  box-shadow: 0 0 4px var(--accent-main);
}

.status-dot.error {
  background: var(--accent-error);
  box-shadow: 0 0 4px var(--accent-error);
}

.status-text {
  font-size: 11px;
  color: var(--text-secondary);
}

.titlebar-right {
  display: flex;
  gap: 4px;
}

.titlebar-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 14px;
  width: 28px;
  height: 28px;
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
</style>
