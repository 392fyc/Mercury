<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useAgentStore } from "../stores/agents";
import { startSession as bridgeStartSession } from "../lib/tauri-bridge";

const emit = defineEmits<{
  close: [];
  created: [panelKey: string];
}>();

const { agents, addBookmark, openFloatingTab, setSession, setSessionInfo, setStatus } = useAgentStore();

const errorMsg = ref<string | null>(null);
const creating = ref(false);

function handleEsc(e: KeyboardEvent) { if (e.key === "Escape") emit("close"); }
onMounted(() => document.addEventListener("keydown", handleEsc));
onUnmounted(() => document.removeEventListener("keydown", handleEsc));

/** All configured agent+role combos (excluding main role). */
const combos = computed(() => {
  const items: { agentId: string; displayName: string; cli: string; role: string; panelKey: string }[] = [];
  for (const agent of agents.value) {
    for (const role of agent.roles) {
      if (role === "main") continue;
      items.push({
        agentId: agent.id,
        displayName: agent.displayName,
        cli: agent.cli ?? "unknown",
        role,
        panelKey: `${role}:${agent.id}`,
      });
    }
  }
  return items;
});

function cliIcon(cli: string): string {
  switch (cli.toLowerCase()) {
    case "claude": return "⚡";
    case "codex": return "🔮";
    case "opencode": return "🌐";
    default: return "🤖";
  }
}

/**
 * Create a new sub-agent session using startSession (no prompt sent).
 * Uses session-unique panelKey to allow multiple concurrent sessions of same role+agent.
 */
async function selectCombo(combo: typeof combos.value[number]) {
  if (creating.value) return; // Prevent double-click re-entry
  creating.value = true;
  errorMsg.value = null;
  try {
    const result = await bridgeStartSession(combo.agentId, combo.role);
    if (result.sessionId) {
      const shortSid = result.sessionId.slice(0, 8);
      const panelKey = `${combo.role}:${combo.agentId}:${shortSid}`;
      setSession(panelKey, result.sessionId);
      setSessionInfo(panelKey, {
        sessionId: result.sessionId,
        sessionName: result.sessionName,
        status: "active",
        lastActiveAt: Date.now(),
      });
      setStatus(panelKey, "idle");
      addBookmark(panelKey);
      openFloatingTab(panelKey);
      emit("created", panelKey);
    }
  } catch (err) {
    console.error("Failed to create session:", err);
    errorMsg.value = `Failed to create session: ${err}`;
    creating.value = false;
    return;
  }
  creating.value = false;
  emit("close");
}
</script>

<template>
  <Teleport to="body">
    <div class="selector-overlay" @click.self="emit('close')">
      <div class="selector-panel">
        <div class="selector-header">
          <span class="selector-title">New Sub-Agent Session</span>
          <button class="selector-close" @click="emit('close')">&times;</button>
        </div>
        <div v-if="errorMsg" class="selector-error">{{ errorMsg }}</div>
        <div class="selector-body">
          <div v-if="combos.length === 0" class="selector-empty">
            No sub-agent roles configured. Add agents with non-main roles in Settings.
          </div>
          <button
            v-for="combo in combos"
            :key="combo.panelKey"
            class="combo-card"
            :disabled="creating"
            @click="selectCombo(combo)"
          >
            <span class="combo-icon">{{ cliIcon(combo.cli) }}</span>
            <div class="combo-info">
              <span class="combo-name">{{ combo.displayName }}</span>
              <span class="combo-role">{{ combo.role.toUpperCase() }}</span>
            </div>
            <span class="combo-cli">{{ combo.cli }}</span>
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.selector-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1100;
}

.selector-panel {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  width: 400px;
  max-height: 400px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.selector-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.selector-title {
  font-weight: 600;
  font-size: 14px;
}

.selector-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 18px;
  cursor: pointer;
  padding: 0 4px;
  line-height: 1;
}

.selector-close:hover {
  color: var(--text-primary);
}

.selector-body {
  overflow-y: auto;
  padding: 8px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 6px;
}

.selector-error {
  padding: 8px 16px;
  background: rgba(255, 82, 82, 0.1);
  color: var(--accent-error);
  font-size: 11px;
  border-bottom: 1px solid rgba(255, 82, 82, 0.2);
}

.selector-empty {
  grid-column: 1 / -1;
  padding: 24px;
  text-align: center;
  color: var(--text-muted);
  font-size: 12px;
}

.combo-card {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
  cursor: pointer;
  text-align: left;
  color: var(--text-primary);
  transition: border-color 0.15s, background 0.15s;
}

.combo-card:hover {
  border-color: var(--accent-main);
  background: rgba(0, 212, 255, 0.05);
}

.combo-icon {
  font-size: 20px;
  flex-shrink: 0;
}

.combo-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.combo-name {
  font-weight: 500;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.combo-role {
  font-size: 9px;
  font-weight: 600;
  color: var(--accent-sub);
  letter-spacing: 0.5px;
}

.combo-cli {
  font-size: 9px;
  color: var(--text-muted);
  font-family: var(--font-mono);
}
</style>
