<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { open } from "@tauri-apps/plugin-dialog";
import { useConfigStore } from "../stores/config";
import type { AgentConfig, ObsidianConfig } from "../lib/tauri-bridge";

const emit = defineEmits<{ close: [] }>();

const { config, loading, error, loadConfig, saveConfig } = useConfigStore();

const activeTab = ref<"agents" | "project" | "display">("agents");
const saving = ref(false);
const saveMsg = ref("");

// Local editable copies
const editAgents = ref<AgentConfig[]>([]);
const editObsidian = ref<ObsidianConfig>({
  enabled: false,
  vaultName: "",
  autoInjectContext: false,
  contextFiles: [],
});
const editWorkDir = ref(".");

onMounted(async () => {
  await loadConfig();
  syncFromConfig();
});

function syncFromConfig() {
  if (!config.value) return;
  editAgents.value = JSON.parse(JSON.stringify(config.value.agents));
  editObsidian.value = config.value.obsidian
    ? JSON.parse(JSON.stringify(config.value.obsidian))
    : { enabled: false, vaultName: "", autoInjectContext: false, contextFiles: [] };
  editWorkDir.value = config.value.workDir ?? ".";
}

watch(config, syncFromConfig);

function addAgent() {
  editAgents.value.push({
    id: `agent-${Date.now()}`,
    displayName: "New Agent",
    cli: "",
    role: "dev",
    integration: "sdk",
    capabilities: [],
    restrictions: [],
    maxConcurrentSessions: 2,
  });
}

function removeAgent(index: number) {
  editAgents.value.splice(index, 1);
}

function addCapability(agent: AgentConfig) {
  const cap = prompt("Capability name:");
  if (cap) agent.capabilities.push(cap);
}

function removeCapability(agent: AgentConfig, index: number) {
  agent.capabilities.splice(index, 1);
}

async function browseWorkDir() {
  const selected = await open({ directory: true, title: "Select Working Directory" });
  if (selected && typeof selected === "string") {
    editWorkDir.value = selected;
  }
}

function addContextFile() {
  const file = prompt("Context file path (relative to vault):");
  if (file) editObsidian.value.contextFiles.push(file);
}

function removeContextFile(index: number) {
  editObsidian.value.contextFiles.splice(index, 1);
}

async function handleSave() {
  saving.value = true;
  saveMsg.value = "";
  try {
    await saveConfig({
      agents: editAgents.value,
      workDir: editWorkDir.value,
      obsidian: editObsidian.value,
    });
    saveMsg.value = "Saved";
    setTimeout(() => (saveMsg.value = ""), 2000);
  } catch (e) {
    saveMsg.value = `Error: ${e instanceof Error ? e.message : e}`;
  } finally {
    saving.value = false;
  }
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
}
</script>

<template>
  <div class="settings-overlay" @keydown="handleKeydown" tabindex="0">
    <div class="settings-panel">
      <div class="settings-header">
        <h2>Settings</h2>
        <button class="close-btn" @click="emit('close')" title="Close">X</button>
      </div>

      <div class="settings-tabs">
        <button
          :class="{ active: activeTab === 'agents' }"
          @click="activeTab = 'agents'"
        >Agents</button>
        <button
          :class="{ active: activeTab === 'project' }"
          @click="activeTab = 'project'"
        >Project</button>
        <button
          :class="{ active: activeTab === 'display' }"
          @click="activeTab = 'display'"
        >Display</button>
      </div>

      <div class="settings-body" v-if="loading">
        <p class="loading-text">Loading configuration...</p>
      </div>

      <div class="settings-body" v-else-if="error && !config">
        <p class="error-text">{{ error }}</p>
      </div>

      <div class="settings-body" v-else>
        <!-- Agents Tab -->
        <div v-if="activeTab === 'agents'" class="tab-content">
          <div
            v-for="(agent, i) in editAgents"
            :key="i"
            class="agent-card"
          >
            <div class="card-header">
              <input v-model="agent.displayName" class="field-input name-input" placeholder="Display Name" />
              <button class="remove-btn" @click="removeAgent(i)" title="Remove">X</button>
            </div>
            <div class="card-fields">
              <label>
                <span>ID</span>
                <input v-model="agent.id" class="field-input" />
              </label>
              <label>
                <span>CLI</span>
                <input v-model="agent.cli" class="field-input" placeholder="claude, codex, opencode..." />
              </label>
              <label>
                <span>Role</span>
                <select v-model="agent.role" class="field-input">
                  <option value="main">Main</option>
                  <option value="dev">Dev</option>
                  <option value="acceptance">Acceptance</option>
                  <option value="research">Research</option>
                </select>
              </label>
              <label>
                <span>Integration</span>
                <select v-model="agent.integration" class="field-input">
                  <option value="sdk">SDK</option>
                  <option value="mcp">MCP</option>
                  <option value="http">HTTP</option>
                  <option value="pty">PTY</option>
                </select>
              </label>
              <label>
                <span>Max Sessions</span>
                <input v-model.number="agent.maxConcurrentSessions" type="number" min="1" class="field-input" />
              </label>
            </div>
            <div class="capabilities">
              <span class="cap-label">Capabilities:</span>
              <span
                v-for="(cap, ci) in agent.capabilities"
                :key="ci"
                class="cap-tag"
                @click="removeCapability(agent, ci)"
                title="Click to remove"
              >{{ cap }}</span>
              <button class="cap-add" @click="addCapability(agent)">+</button>
            </div>
          </div>
          <button class="add-agent-btn" @click="addAgent">+ Add Agent</button>
        </div>

        <!-- Project Tab -->
        <div v-if="activeTab === 'project'" class="tab-content">
          <div class="section">
            <h3>Working Directory</h3>
            <div class="dir-input-row">
              <input v-model="editWorkDir" class="field-input dir-field" />
              <button class="browse-btn" @click="browseWorkDir" title="Browse...">Browse</button>
            </div>
          </div>

          <div class="section">
            <h3>Knowledge Base (Obsidian CLI)</h3>
            <p class="hint">Optional. When disabled, agents can still use their own MCP servers, mem0, or other knowledge tools.</p>
            <label class="toggle-row">
              <span class="toggle-switch">
                <input type="checkbox" v-model="editObsidian.enabled" />
                <span class="toggle-track"><span class="toggle-thumb"></span></span>
              </span>
              <span>Enable Obsidian CLI integration</span>
            </label>

            <div v-if="editObsidian.enabled" class="kb-config">
              <label>
                <span>Vault Name</span>
                <input v-model="editObsidian.vaultName" class="field-input full-width" placeholder="Mercury-KB" />
              </label>
              <label class="toggle-row">
                <span class="toggle-switch">
                  <input type="checkbox" v-model="editObsidian.autoInjectContext" />
                  <span class="toggle-track"><span class="toggle-thumb"></span></span>
                </span>
                <span>Auto-inject KB context into agent prompts</span>
              </label>
              <div class="context-files" v-if="editObsidian.autoInjectContext">
                <span class="cap-label">Context Files:</span>
                <div v-for="(f, fi) in editObsidian.contextFiles" :key="fi" class="context-file-row">
                  <span class="context-file">{{ f }}</span>
                  <button class="remove-btn small" @click="removeContextFile(fi)">X</button>
                </div>
                <button class="cap-add" @click="addContextFile">+ Add File</button>
              </div>
            </div>
          </div>
        </div>

        <!-- Display Tab -->
        <div v-if="activeTab === 'display'" class="tab-content">
          <div class="section">
            <h3>Theme</h3>
            <p class="hint">Dark mode only (more themes coming soon)</p>
          </div>
        </div>
      </div>

      <div class="settings-footer">
        <span v-if="saveMsg" :class="saveMsg.startsWith('Error') ? 'error-text' : 'success-text'">
          {{ saveMsg }}
        </span>
        <button class="save-btn" @click="handleSave" :disabled="saving">
          {{ saving ? "Saving..." : "Save" }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  outline: none;
}

.settings-panel {
  width: 640px;
  height: 520px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.settings-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
}

.settings-header h2 {
  font-size: 16px;
  font-weight: 600;
  margin: 0;
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 14px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: var(--radius);
}

.close-btn:hover {
  background: var(--bg-panel);
  color: var(--text-primary);
}

.settings-tabs {
  display: flex;
  gap: 0;
  border-bottom: 1px solid var(--border);
}

.settings-tabs button {
  flex: 1;
  padding: 8px 16px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.settings-tabs button.active {
  color: var(--accent-main);
  border-bottom-color: var(--accent-main);
}

.settings-tabs button:hover {
  background: var(--bg-panel);
}

.settings-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.loading-text {
  color: var(--text-muted);
  text-align: center;
  padding: 24px;
}

.error-text {
  color: var(--accent-error);
  font-size: 12px;
}

.success-text {
  color: var(--accent-success);
  font-size: 12px;
}

/* Agent Cards */
.agent-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.name-input {
  font-weight: 600;
  font-size: 14px;
}

.card-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.card-fields label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.card-fields label span {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}

.field-input {
  padding: 4px 8px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 12px;
  outline: none;
}

.field-input:focus {
  border-color: var(--accent-main);
}

.full-width {
  width: 100%;
  box-sizing: border-box;
}

.capabilities {
  margin-top: 8px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}

.cap-label {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.5px;
  margin-right: 4px;
}

.cap-tag {
  font-size: 10px;
  padding: 2px 6px;
  background: rgba(0, 212, 255, 0.1);
  color: var(--accent-main);
  border-radius: 3px;
  cursor: pointer;
}

.cap-tag:hover {
  background: rgba(255, 82, 82, 0.15);
  color: var(--accent-error);
}

.cap-add {
  font-size: 12px;
  padding: 1px 6px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  color: var(--text-secondary);
  cursor: pointer;
}

.cap-add:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
}

.remove-btn {
  background: none;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: var(--radius);
}

.remove-btn:hover {
  background: rgba(255, 82, 82, 0.15);
  color: var(--accent-error);
}

.remove-btn.small {
  font-size: 10px;
  padding: 1px 4px;
}

.add-agent-btn {
  padding: 8px;
  background: var(--bg-secondary);
  border: 1px dashed var(--border);
  border-radius: var(--radius);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.add-agent-btn:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
}

/* Project Tab */
.section {
  margin-bottom: 16px;
}

.section h3 {
  font-size: 13px;
  font-weight: 600;
  margin: 0 0 8px 0;
}

.hint {
  font-size: 11px;
  color: var(--text-muted);
  margin: 0 0 8px 0;
}

/* Directory input with browse button */
.dir-input-row {
  display: flex;
  gap: 6px;
}

.dir-field {
  flex: 1;
  min-width: 0;
}

.browse-btn {
  padding: 4px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}

.browse-btn:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
}

/* Toggle switch */
.toggle-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  margin: 4px 0;
}

.toggle-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  width: 32px;
  height: 18px;
  flex-shrink: 0;
}

.toggle-switch input {
  position: absolute;
  opacity: 0;
  width: 0;
  height: 0;
}

.toggle-track {
  position: absolute;
  inset: 0;
  background: var(--border);
  border-radius: 9px;
  transition: background 0.2s;
}

.toggle-thumb {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 14px;
  height: 14px;
  background: var(--text-muted);
  border-radius: 50%;
  transition: transform 0.2s, background 0.2s;
}

.toggle-switch input:checked + .toggle-track {
  background: var(--accent-main);
}

.toggle-switch input:checked + .toggle-track .toggle-thumb {
  transform: translateX(14px);
  background: var(--bg-primary);
}

.kb-config {
  margin-top: 12px;
  padding: 12px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.kb-config label {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.kb-config label > span {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}

.context-files {
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.context-file-row {
  display: flex;
  align-items: center;
  gap: 4px;
}

.context-file {
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
  padding: 2px 6px;
  background: var(--bg-input);
  border-radius: 3px;
  flex: 1;
}

/* Footer */
.settings-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 12px 16px;
  border-top: 1px solid var(--border);
}

.save-btn {
  padding: 6px 20px;
  background: var(--accent-main);
  color: var(--bg-primary);
  border: none;
  border-radius: var(--radius);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.save-btn:hover {
  opacity: 0.9;
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
