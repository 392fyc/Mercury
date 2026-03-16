<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { open } from "@tauri-apps/plugin-dialog";
import { useConfigStore } from "../stores/config";
import { refreshContext, getContextStatus } from "../lib/tauri-bridge";
import type { AgentConfig, ObsidianConfig } from "../lib/tauri-bridge";

const emit = defineEmits<{ close: [] }>();

const { config, loading, error, loadConfig, saveConfig } = useConfigStore();

const activeTab = ref<"agents" | "project" | "display">("agents");
const saving = ref(false);
const saveMsg = ref("");

// ─── Supported CLI Presets ───
// Only CLIs we have adapters for. Selecting a CLI auto-fills derived fields.
interface CliPreset {
  cli: string;
  label: string;
  id: string;
  integration: string;
  capabilities: string[];
  restrictions: string[];
  maxSessions: number;
}

const CLI_PRESETS: CliPreset[] = [
  {
    cli: "claude", label: "Claude Code",
    id: "claude-code", integration: "sdk",
    capabilities: ["code", "analysis", "mcp", "multimodal"],
    restrictions: [], maxSessions: 2,
  },
  {
    cli: "codex", label: "Codex CLI",
    id: "codex-cli", integration: "sdk",
    capabilities: ["code", "batch_json", "test"],
    restrictions: ["no_kb_write", "isolated_branch_only"], maxSessions: 3,
  },
  {
    cli: "opencode", label: "opencode",
    id: "opencode", integration: "http",
    capabilities: ["code", "parallel", "design_to_code"],
    restrictions: ["no_kb_write", "isolated_branch_only"], maxSessions: 3,
  },
  {
    cli: "gemini", label: "Gemini CLI",
    id: "gemini-cli", integration: "pty",
    capabilities: ["code", "analysis", "multimodal"],
    restrictions: [], maxSessions: 2,
  },
];

function getPreset(cli: string): CliPreset | undefined {
  return CLI_PRESETS.find(p => p.cli === cli);
}

// ─── Role Definitions ───
interface RoleDef {
  value: string;
  label: string;
  hint: string;
}

const ROLE_DEFS: RoleDef[] = [
  { value: "main",       label: "Main",       hint: "Orchestrator — user talks to this agent directly, delegates tasks to others" },
  { value: "dev",        label: "Dev",        hint: "Worker — receives task bundles, writes code, returns implementation receipts" },
  { value: "acceptance", label: "Acceptance", hint: "Reviewer — performs blind acceptance testing on completed tasks" },
  { value: "research",   label: "Research",   hint: "Analyst — gathers information, reads docs, answers questions without writing code" },
  { value: "design",     label: "Design",     hint: "Designer — generates UI/UX mockups, design specs, and visual assets" },
];

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
  loadContextStatus();
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
  const preset = CLI_PRESETS[0]; // default to Claude Code
  editAgents.value.push({
    id: preset.id,
    displayName: preset.label,
    cli: preset.cli,
    role: "dev",
    integration: preset.integration,
    capabilities: [...preset.capabilities],
    restrictions: [...preset.restrictions],
    maxConcurrentSessions: preset.maxSessions,
  });
}

function removeAgent(index: number) {
  editAgents.value.splice(index, 1);
}

/** When user changes CLI dropdown, auto-fill derived fields from preset. */
function onCliChange(agent: AgentConfig) {
  const preset = getPreset(agent.cli);
  if (!preset) return;
  agent.id = preset.id;
  agent.displayName = preset.label;
  agent.integration = preset.integration;
  agent.capabilities = [...preset.capabilities];
  agent.restrictions = [...preset.restrictions];
  agent.maxConcurrentSessions = preset.maxSessions;
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

// ─── Context Injection Status ───
const contextStatus = ref<{ hasContext: boolean; contextLength: number; autoInject: boolean; contextFiles: string[] } | null>(null);
const refreshing = ref(false);
const refreshMsg = ref("");

async function loadContextStatus() {
  try {
    contextStatus.value = await getContextStatus();
  } catch {
    contextStatus.value = null;
  }
}

async function handleRefreshContext() {
  refreshing.value = true;
  refreshMsg.value = "";
  try {
    const result = await refreshContext();
    if (result.injected) {
      refreshMsg.value = `Injected ${(result.contextLength / 1024).toFixed(1)}KB into ${result.agentCount} agent(s)`;
    } else {
      refreshMsg.value = "No context to inject (check config)";
    }
    await loadContextStatus();
    setTimeout(() => (refreshMsg.value = ""), 4000);
  } catch (e) {
    refreshMsg.value = `Error: ${e instanceof Error ? e.message : e}`;
  } finally {
    refreshing.value = false;
  }
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
          <p class="hint">Configure which AI agents Mercury can orchestrate. Each agent maps to a supported CLI tool.</p>
          <div
            v-for="(agent, i) in editAgents"
            :key="i"
            class="agent-card"
          >
            <div class="card-header">
              <span class="agent-title">{{ agent.displayName || 'New Agent' }}</span>
              <span class="agent-meta">{{ agent.id }} / {{ agent.integration }}</span>
              <button class="remove-btn" @click="removeAgent(i)" title="Remove">X</button>
            </div>
            <div class="card-fields">
              <label>
                <span>CLI Tool</span>
                <select v-model="agent.cli" class="field-input" @change="onCliChange(agent)">
                  <option v-for="p in CLI_PRESETS" :key="p.cli" :value="p.cli">{{ p.label }}</option>
                </select>
              </label>
              <label>
                <span>Role</span>
                <select v-model="agent.role" class="field-input">
                  <option v-for="r in ROLE_DEFS" :key="r.value" :value="r.value">{{ r.label }}</option>
                </select>
              </label>
              <label>
                <span>Max Sessions</span>
                <input v-model.number="agent.maxConcurrentSessions" type="number" min="1" max="10" class="field-input" />
              </label>
              <label>
                <span>Display Name</span>
                <input v-model="agent.displayName" class="field-input" />
              </label>
            </div>
            <div class="capabilities">
              <span class="cap-label">Capabilities:</span>
              <span
                v-for="(cap, ci) in agent.capabilities"
                :key="ci"
                class="cap-tag"
              >{{ cap }}</span>
            </div>
            <p class="role-hint">{{ ROLE_DEFS.find(r => r.value === agent.role)?.hint ?? '' }}</p>
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

              <!-- Shared Context Status & Refresh -->
              <div class="context-status-section" v-if="editObsidian.autoInjectContext">
                <div class="context-status-row">
                  <div class="context-status-info">
                    <span class="cap-label">Shared Context:</span>
                    <span v-if="contextStatus?.hasContext" class="status-badge active">
                      Active ({{ (contextStatus.contextLength / 1024).toFixed(1) }}KB)
                    </span>
                    <span v-else class="status-badge inactive">Not injected</span>
                  </div>
                  <button
                    class="refresh-btn"
                    @click="handleRefreshContext"
                    :disabled="refreshing"
                    title="Rebuild and inject KB context into all agents"
                  >
                    {{ refreshing ? "Refreshing..." : "Refresh Context" }}
                  </button>
                </div>
                <p v-if="refreshMsg" class="refresh-msg" :class="refreshMsg.startsWith('Error') ? 'error-text' : 'success-text'">
                  {{ refreshMsg }}
                </p>
                <p class="hint">Injects KB files as system-level context. Does not consume agent conversation window.</p>
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
  gap: 8px;
  margin-bottom: 8px;
}

.agent-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text-primary);
}

.agent-meta {
  font-size: 10px;
  font-family: var(--font-mono);
  color: var(--text-muted);
  flex: 1;
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
}

.role-hint {
  margin: 6px 0 0 0;
  font-size: 10px;
  color: var(--text-muted);
  font-style: italic;
  line-height: 1.4;
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

/* Context Status */
.context-status-section {
  margin-top: 8px;
  padding: 10px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.context-status-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.context-status-info {
  display: flex;
  align-items: center;
  gap: 6px;
}

.status-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 3px;
  font-family: var(--font-mono);
}

.status-badge.active {
  background: rgba(0, 212, 255, 0.12);
  color: var(--accent-main);
}

.status-badge.inactive {
  background: rgba(255, 255, 255, 0.05);
  color: var(--text-muted);
}

.refresh-btn {
  padding: 4px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--accent-main);
  border-radius: var(--radius);
  color: var(--accent-main);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}

.refresh-btn:hover {
  background: rgba(0, 212, 255, 0.1);
}

.refresh-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.refresh-msg {
  font-size: 11px;
  margin: 6px 0 0 0;
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
