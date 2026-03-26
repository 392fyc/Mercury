<script setup lang="ts">
import { ref, onMounted, watch } from "vue";
import { open } from "@tauri-apps/plugin-dialog";
import { useConfigStore } from "../stores/config";
import { refreshContext, getContextStatus, kbList, getRoleInstructions } from "../lib/tauri-bridge";
import type { AgentConfig, ContextStatus, ObsidianConfig } from "../lib/tauri-bridge";

const emit = defineEmits<{ close: [] }>();

const { config, loading, error, loadConfig, saveConfig } = useConfigStore();

const activeTab = ref<"agents" | "project" | "display">("agents");
const saving = ref(false);
const saveMsg = ref("");
const roleContextExpanded = ref(true);

type RoleContextKey = "main" | "dev" | "acceptance" | "research" | "design";
type ContextTarget = "global" | RoleContextKey;

interface CliPreset {
  cli: string;
  label: string;
  id: string;
  integration: string;
  capabilities: string[];
  restrictions: string[];
  maxSessions: number;
  disabled?: boolean;
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
    id: "gemini-cli", integration: "sdk",
    capabilities: ["code", "multimodal", "research"],
    restrictions: ["no_kb_write", "isolated_branch_only"], maxSessions: 3,
  },
];

function getPreset(cli: string): CliPreset | undefined {
  return CLI_PRESETS.find((preset) => preset.cli === cli);
}

function getInstructionPersistenceLabel(agent: AgentConfig): string | null {
  if (agent.cli === "claude") {
    return "System-level prompt (every request, survives compaction)";
  }
  if (agent.cli === "codex") {
    return "Turn-level prepend (every turn, early turns may be summarized)";
  }
  return null;
}

interface RoleDef {
  value: string;
  label: string;
  hint: string;
}

const ROLE_DEFS: RoleDef[] = [
  { value: "main", label: "Main", hint: "Orchestrator — user talks to this agent directly, delegates tasks to others" },
  { value: "dev", label: "Dev", hint: "Worker — receives task bundles, writes code, returns implementation receipts" },
  { value: "acceptance", label: "Acceptance", hint: "Reviewer — performs blind acceptance testing on completed tasks" },
  { value: "research", label: "Research", hint: "Analyst — gathers information, reads docs, answers questions without writing code" },
  { value: "design", label: "Design", hint: "Designer — generates UI/UX mockups, design specs, and visual assets" },
];

const ROLE_CONTEXT_DEFS: Array<{ value: RoleContextKey; label: string; hint: string }> = [
  { value: "main", label: "Main", hint: "Orchestrator — user talks to this agent directly." },
  { value: "dev", label: "Dev", hint: "Worker — receives tasks, writes code." },
  { value: "acceptance", label: "Acceptance", hint: "Reviewer — blind acceptance testing." },
  { value: "research", label: "Research", hint: "Analyst — gathers information, reads docs." },
  { value: "design", label: "Design", hint: "Designer — UI/UX mockups and design specs." },
];

function createEmptyRoleContextFiles(): NonNullable<ObsidianConfig["roleContextFiles"]> {
  return {
    main: [],
    dev: [],
    acceptance: [],
    research: [],
    design: [],
  };
}

/** Return a default ObsidianConfig with all fields initialized to safe empty values. */
function createEmptyObsidianConfig(): ObsidianConfig {
  return {
    enabled: false,
    vaultName: "",
    vaultPath: "",
    obsidianBin: "",
    autoInjectContext: false,
    contextFiles: [],
    roleContextFiles: createEmptyRoleContextFiles(),
    roleInstructionOverrides: {},
  };
}

function normalizeObsidianConfig(source?: ObsidianConfig | null): ObsidianConfig {
  const roleContextFiles = createEmptyRoleContextFiles();
  if (source?.roleContextFiles) {
    roleContextFiles.main = [...(source.roleContextFiles.main ?? [])];
    roleContextFiles.dev = [...(source.roleContextFiles.dev ?? [])];
    roleContextFiles.acceptance = [...(source.roleContextFiles.acceptance ?? [])];
    roleContextFiles.research = [...(source.roleContextFiles.research ?? [])];
    roleContextFiles.design = [...(source.roleContextFiles.design ?? [])];
  }

  return {
    ...createEmptyObsidianConfig(),
    ...source,
    kbPaths: source?.kbPaths ? { ...source.kbPaths } : undefined,
    contextFiles: [...(source?.contextFiles ?? [])],
    roleContextFiles,
    roleInstructionOverrides: source?.roleInstructionOverrides
      ? { ...source.roleInstructionOverrides }
      : {},
  };
}

const editAgents = ref<AgentConfig[]>([]);
const editObsidian = ref<ObsidianConfig>(createEmptyObsidianConfig());
const editWorkDir = ref(".");

onMounted(async () => {
  await loadConfig();
  syncFromConfig();
  await loadContextStatus();
});

function syncFromConfig() {
  if (!config.value) {
    return;
  }

  editAgents.value = JSON.parse(JSON.stringify(config.value.agents));
  editObsidian.value = normalizeObsidianConfig(config.value.obsidian);
  editWorkDir.value = config.value.workDir ?? ".";
}

watch(config, syncFromConfig);

function addAgent() {
  const preset = CLI_PRESETS[0];
  editAgents.value.push({
    id: preset.id,
    displayName: preset.label,
    cli: preset.cli,
    roles: ["dev"],
    integration: preset.integration,
    capabilities: [...preset.capabilities],
    restrictions: [...preset.restrictions],
    maxConcurrentSessions: preset.maxSessions,
  });
}

function removeAgent(index: number) {
  editAgents.value.splice(index, 1);
}

function onCliChange(agent: AgentConfig) {
  const preset = getPreset(agent.cli);
  if (!preset) {
    return;
  }

  agent.id = preset.id;
  agent.displayName = preset.label;
  agent.integration = preset.integration;
  agent.capabilities = [...preset.capabilities];
  agent.restrictions = [...preset.restrictions];
  agent.maxConcurrentSessions = preset.maxSessions;
  // Reset model — old value belongs to previous provider
  agent.model = "default";
}

async function browseWorkDir() {
  const selected = await open({ directory: true, title: "Select Working Directory" });
  if (selected && typeof selected === "string") {
    editWorkDir.value = selected;
  }
}

/** Open a native directory picker for the KB vault path. */
async function browseVaultPath() {
  const selected = await open({ directory: true, title: "Select KB vault directory" });
  if (selected && typeof selected === "string") {
    editObsidian.value.vaultPath = selected;
  }
}

type VaultEntry = {
  path: string;
  name: string;
  folder: string;
  kind: "file" | "folder";
};

const showVaultBrowser = ref(false);
const vaultFiles = ref<VaultEntry[]>([]);
const vaultCurrentFolder = ref("");
const vaultLoading = ref(false);
const vaultError = ref("");
const vaultTarget = ref<ContextTarget>("global");

function ensureRoleContextFiles(): NonNullable<ObsidianConfig["roleContextFiles"]> {
  if (!editObsidian.value.roleContextFiles) {
    editObsidian.value.roleContextFiles = createEmptyRoleContextFiles();
  }
  return editObsidian.value.roleContextFiles;
}

function getContextFilesForTarget(target: ContextTarget): string[] {
  if (target === "global") {
    return editObsidian.value.contextFiles;
  }

  const roleContextFiles = ensureRoleContextFiles();
  if (!roleContextFiles[target]) {
    roleContextFiles[target] = [];
  }
  return roleContextFiles[target] ?? [];
}

function getContextTargetLabel(target: ContextTarget = vaultTarget.value): string {
  if (target === "global") {
    return "Global Context";
  }

  return ROLE_CONTEXT_DEFS.find((role) => role.value === target)?.label ?? target;
}

async function browseVaultFiles(target: ContextTarget = "global") {
  vaultTarget.value = target;

  if (!editObsidian.value.enabled || !editObsidian.value.vaultName) {
    vaultError.value = "Please enable and configure Knowledge Base first";
    showVaultBrowser.value = true;
    return;
  }

  showVaultBrowser.value = true;
  vaultCurrentFolder.value = "";
  await fetchVaultListing();
}

async function fetchVaultListing() {
  vaultLoading.value = true;
  vaultError.value = "";

  try {
    const results = await kbList(vaultCurrentFolder.value || undefined) as VaultEntry[];
    vaultFiles.value = results.sort((a, b) => {
      const aIsFolder = a.kind === "folder";
      const bIsFolder = b.kind === "folder";
      if (aIsFolder && !bIsFolder) return -1;
      if (!aIsFolder && bIsFolder) return 1;
      return a.name.localeCompare(b.name);
    });
  } catch (e) {
    vaultError.value = e instanceof Error ? e.message : String(e);
    vaultFiles.value = [];
  } finally {
    vaultLoading.value = false;
  }
}

function isFolder(entry: VaultEntry): boolean {
  return entry.kind === "folder";
}

async function navigateVaultFolder(folderPath: string) {
  vaultCurrentFolder.value = folderPath;
  await fetchVaultListing();
}

async function navigateVaultUp() {
  const parts = vaultCurrentFolder.value.split("/").filter(Boolean);
  parts.pop();
  vaultCurrentFolder.value = parts.join("/");
  await fetchVaultListing();
}

function selectVaultFile(filePath: string) {
  const targetFiles = getContextFilesForTarget(vaultTarget.value);
  if (!targetFiles.includes(filePath)) {
    targetFiles.push(filePath);
  }
  showVaultBrowser.value = false;
}

function closeVaultBrowser() {
  showVaultBrowser.value = false;
}

function removeContextFile(index: number, target: ContextTarget = "global") {
  const targetFiles = getContextFilesForTarget(target);
  targetFiles.splice(index, 1);
}

// ─── Role Instructions Editor ───

const editingRole = ref<RoleContextKey | null>(null);
const roleEditorContent = ref("");
const roleDefaultContent = ref("");
const roleEditorLoading = ref(false);
const roleEditorError = ref("");

function hasRoleOverride(role: RoleContextKey): boolean {
  const overrides = editObsidian.value.roleInstructionOverrides;
  return overrides != null && role in overrides && overrides[role] !== undefined;
}

let roleEditorRequestId = 0;

async function openRoleEditor(role: RoleContextKey) {
  // Save current edits before switching
  if (editingRole.value) {
    closeRoleEditor();
  }

  const requestId = ++roleEditorRequestId;
  editingRole.value = role;
  roleEditorLoading.value = true;
  roleEditorError.value = "";

  try {
    const result = await getRoleInstructions(role);
    if (requestId !== roleEditorRequestId) return; // stale response
    roleDefaultContent.value = result.defaultInstructions;
    // Show override if it exists in local edit state, else from backend, else default
    const localOverride = editObsidian.value.roleInstructionOverrides?.[role];
    roleEditorContent.value = localOverride ?? result.override ?? result.defaultInstructions;
  } catch (e) {
    if (requestId !== roleEditorRequestId) return;
    roleEditorError.value = e instanceof Error ? e.message : String(e);
    roleDefaultContent.value = "";
    roleEditorContent.value = "";
  } finally {
    if (requestId === roleEditorRequestId) {
      roleEditorLoading.value = false;
    }
  }
}

function closeRoleEditor() {
  if (roleEditorLoading.value || roleEditorError.value) {
    // Don't persist changes when loading failed — just close without modifying overrides
    editingRole.value = null;
    roleEditorContent.value = "";
    roleDefaultContent.value = "";
    roleEditorError.value = "";
    return;
  }
  if (editingRole.value) {
    const role = editingRole.value;
    const content = roleEditorContent.value;
    const isDefault = content === roleDefaultContent.value;
    if (!editObsidian.value.roleInstructionOverrides) {
      editObsidian.value.roleInstructionOverrides = {};
    }
    if (isDefault) {
      delete editObsidian.value.roleInstructionOverrides[role];
    } else {
      editObsidian.value.roleInstructionOverrides[role] = content;
    }
  }
  editingRole.value = null;
  roleEditorContent.value = "";
  roleDefaultContent.value = "";
  roleEditorError.value = "";
}

function restoreRoleDefault() {
  roleEditorContent.value = roleDefaultContent.value;
}

const contextStatus = ref<ContextStatus | null>(null);
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
    refreshMsg.value = result.injected
      ? `Rebuilt ${(result.contextLength / 1024).toFixed(1)}KB of prompt context`
      : "No context to inject (check config)";
    await loadContextStatus();
    setTimeout(() => {
      refreshMsg.value = "";
    }, 4000);
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
      obsidian: normalizeObsidianConfig(editObsidian.value),
    });
    saveMsg.value = "Saved";
    setTimeout(() => {
      saveMsg.value = "";
    }, 2000);
  } catch (e) {
    saveMsg.value = `Error: ${e instanceof Error ? e.message : e}`;
  } finally {
    saving.value = false;
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === "Escape") {
    emit("close");
  }
}
</script>

<template>
  <div class="settings-overlay" @keydown="handleKeydown" tabindex="0">
    <div class="settings-panel">
      <div class="settings-header">
        <div>
          <h2>Settings</h2>
          <p class="header-subtitle">Agent routing, KB injection, and project runtime defaults.</p>
        </div>
        <button class="close-btn" @click="emit('close')" title="Close">X</button>
      </div>

      <div class="settings-tabs">
        <button :class="{ active: activeTab === 'agents' }" @click="activeTab = 'agents'">Agents</button>
        <button :class="{ active: activeTab === 'project' }" @click="activeTab = 'project'">Project</button>
        <button :class="{ active: activeTab === 'display' }" @click="activeTab = 'display'">Display</button>
      </div>

      <div v-if="loading" class="settings-body">
        <p class="loading-text">Loading configuration...</p>
      </div>

      <div v-else-if="error && !config" class="settings-body">
        <p class="error-text">{{ error }}</p>
      </div>

      <div class="settings-body" v-else>
        <div v-if="activeTab === 'agents'" class="tab-content">
          <p class="hint">
            Configure which AI agents Mercury can orchestrate. Each agent maps to a supported CLI tool.
          </p>

          <div v-for="(agent, i) in editAgents" :key="i" class="agent-card">
            <div class="card-header">
              <span class="agent-title">{{ agent.displayName || "New Agent" }}</span>
              <span class="agent-meta">{{ agent.id }} / {{ agent.integration }}</span>
              <button class="remove-btn" @click="removeAgent(i)" title="Remove">X</button>
            </div>
            <div class="card-fields">
              <label>
                <span>CLI Tool</span>
                <select v-model="agent.cli" class="field-input" @change="onCliChange(agent)">
                  <option v-for="p in CLI_PRESETS" :key="p.cli" :value="p.cli" :disabled="p.disabled">
                    {{ p.label }}
                  </option>
                </select>
              </label>
              <label>
                <span>Roles</span>
                <div class="role-checkboxes">
                  <label v-for="r in ROLE_DEFS" :key="r.value" class="role-checkbox">
                    <input type="checkbox" :value="r.value" v-model="agent.roles" />
                    <span>{{ r.label }}</span>
                  </label>
                </div>
              </label>
              <label>
                <span>Max Sessions</span>
                <input v-model.number="agent.maxConcurrentSessions" type="number" min="1" max="10" class="field-input" />
              </label>
              <label>
                <span>Display Name</span>
                <input v-model="agent.displayName" class="field-input" />
              </label>
              <label>
                <span>Model</span>
                <input v-model="agent.model" class="field-input" placeholder="e.g. claude-opus-4-6, gpt-5.4, gpt-5.3-codex" />
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
            <p v-if="getInstructionPersistenceLabel(agent)" class="role-hint">
              Prompt persistence: {{ getInstructionPersistenceLabel(agent) }}
            </p>
            <p class="role-hint">
              {{ agent.roles?.map((r) => ROLE_DEFS.find((d) => d.value === r)?.hint).filter(Boolean).join(" | ") ?? "" }}
            </p>
          </div>
          <button class="add-agent-btn" @click="addAgent">+ Add Agent</button>
        </div>

        <div v-if="activeTab === 'project'" class="tab-content">
          <div class="section">
            <h3>Working Directory</h3>
            <p class="hint compact">Default workspace for agent sessions and sidecar operations.</p>
            <div class="dir-input-row">
              <input v-model="editWorkDir" class="field-input dir-field" />
              <button class="browse-btn" @click="browseWorkDir" title="Browse working directory">Browse</button>
            </div>
          </div>

          <div class="section">
            <h3>Knowledge Base (Obsidian CLI)</h3>
            <p class="hint compact">
              Optional. When disabled, agents can still use their own MCP servers, mem0, or other knowledge tools.
            </p>
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
              <label>
                <span>KB Path (vault file system path)</span>
                <div class="dir-input-row">
                  <input
                    v-model="editObsidian.vaultPath"
                    class="field-input dir-field"
                    placeholder="D:/Mercury/Mercury_KB"
                  />
                  <button type="button" class="browse-btn" @click="browseVaultPath">Browse</button>
                </div>
              </label>
              <label>
                <span>Obsidian Binary Path</span>
                <input v-model="editObsidian.obsidianBin" class="field-input full-width" placeholder="auto-detect" />
              </label>
              <label class="toggle-row">
                <span class="toggle-switch">
                  <input type="checkbox" v-model="editObsidian.autoInjectContext" />
                  <span class="toggle-track"><span class="toggle-thumb"></span></span>
                </span>
                <span>Auto-inject KB context into agent prompts</span>
              </label>

              <template v-if="editObsidian.autoInjectContext">
                <div class="context-group">
                  <div class="context-group-header">
                    <div>
                      <span class="cap-label">Context Files</span>
                      <p class="hint compact">These files are injected into every role.</p>
                    </div>
                    <button class="cap-add" @click="browseVaultFiles('global')">+ Add File</button>
                  </div>

                  <div v-if="editObsidian.contextFiles.length > 0" class="context-file-list">
                    <div v-for="(f, fi) in editObsidian.contextFiles" :key="`global-${fi}`" class="context-file-row">
                      <span class="context-file">{{ f }}</span>
                      <button class="remove-btn small" @click="removeContextFile(fi, 'global')">X</button>
                    </div>
                  </div>
                  <p v-else class="empty-state">No global context files selected.</p>
                </div>

                <div class="role-context-shell">
                  <button class="section-toggle" @click="roleContextExpanded = !roleContextExpanded">
                    <span>Role Instructions</span>
                    <span class="section-toggle-state">{{ roleContextExpanded ? "Hide" : "Show" }}</span>
                  </button>
                  <p class="hint compact">
                    Edit the system-prompt instructions injected for each role. Overrides are saved in config; defaults come from .mercury/roles/.
                  </p>

                  <div v-if="roleContextExpanded" class="role-context-grid">
                    <!-- Role instruction cards (collapsed view) -->
                    <template v-for="role in ROLE_CONTEXT_DEFS" :key="role.value">
                      <div v-if="editingRole !== role.value" class="role-context-card">
                        <div class="role-context-card-header">
                          <div>
                            <h4>{{ role.label }}</h4>
                            <p class="hint compact">{{ role.hint }}</p>
                          </div>
                          <button class="cap-add" @click="openRoleEditor(role.value)">Edit</button>
                        </div>
                        <span
                          class="role-override-badge"
                          :class="{ overridden: hasRoleOverride(role.value) }"
                        >
                          {{ hasRoleOverride(role.value) ? "Custom override" : "Using default" }}
                        </span>
                      </div>

                      <!-- Role instruction editor (expanded view) -->
                      <div v-else class="role-editor-card">
                        <div class="role-editor-header">
                          <h4>Editing: {{ role.label }} Role Instructions</h4>
                          <div class="role-editor-actions">
                            <button
                              class="role-editor-restore"
                              @click="restoreRoleDefault"
                              :disabled="roleEditorContent === roleDefaultContent"
                              title="Revert to default instructions from .mercury/roles/"
                            >Restore Default</button>
                            <button class="role-editor-close" @click="closeRoleEditor">Done</button>
                          </div>
                        </div>

                        <div v-if="roleEditorLoading" class="role-editor-loading">Loading instructions...</div>
                        <div v-else-if="roleEditorError" class="role-editor-error">{{ roleEditorError }}</div>
                        <textarea
                          v-else
                          v-model="roleEditorContent"
                          class="role-editor-textarea"
                          spellcheck="false"
                          placeholder="Role instructions (Markdown)"
                        ></textarea>

                        <span
                          class="role-override-badge"
                          :class="{ overridden: roleEditorContent !== roleDefaultContent }"
                        >
                          {{ roleEditorContent !== roleDefaultContent ? "Modified — will be saved as override" : "Matches default — no override" }}
                        </span>
                      </div>
                    </template>
                  </div>
                </div>

                <div class="context-status-section">
                  <div class="context-status-row">
                    <div class="context-status-info">
                      <span class="cap-label">Prompt Context Cache</span>
                      <span v-if="contextStatus?.hasContext" class="status-badge active">
                        Active ({{ (contextStatus.contextLength / 1024).toFixed(1) }}KB)
                      </span>
                      <span v-else class="status-badge inactive">Not built</span>
                    </div>
                    <button
                      class="refresh-btn"
                      @click="handleRefreshContext"
                      :disabled="refreshing"
                      title="Rebuild global and role-specific KB prompt context"
                    >
                      {{ refreshing ? "Refreshing..." : "Refresh Context" }}
                    </button>
                  </div>
                  <p
                    v-if="refreshMsg"
                    class="refresh-msg"
                    :class="refreshMsg.startsWith('Error') ? 'error-text' : 'success-text'"
                  >
                    {{ refreshMsg }}
                  </p>
                  <p class="hint compact">
                    Rebuilds the prompt cache for global and role-specific files. Acceptance sessions only receive
                    acceptance-scoped additions.
                  </p>
                </div>
              </template>
            </div>
          </div>
        </div>

        <div v-if="activeTab === 'display'" class="tab-content">
          <div class="section">
            <h3>Theme</h3>
            <p class="hint compact">Dark mode only (more themes coming soon).</p>
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

    <div v-if="showVaultBrowser" class="vault-browser-overlay" @click.self="closeVaultBrowser">
      <div class="vault-browser">
        <div class="vault-browser-header">
          <div>
            <h3>Select File</h3>
            <p class="hint compact">Target: {{ getContextTargetLabel() }}</p>
          </div>
          <button class="close-btn" @click="closeVaultBrowser" title="Close">X</button>
        </div>
        <div class="vault-browser-breadcrumb">
          <button class="breadcrumb-segment" @click="navigateVaultFolder('')">vault</button>
          <template v-for="(seg, si) in vaultCurrentFolder.split('/').filter(Boolean)" :key="si">
            <span class="breadcrumb-sep">/</span>
            <button
              class="breadcrumb-segment"
              @click="navigateVaultFolder(vaultCurrentFolder.split('/').filter(Boolean).slice(0, si + 1).join('/'))"
            >
              {{ seg }}
            </button>
          </template>
        </div>
        <div class="vault-browser-body">
          <p v-if="vaultLoading" class="loading-text">Loading...</p>
          <p v-else-if="vaultError" class="error-text">{{ vaultError }}</p>
          <template v-else>
            <button v-if="vaultCurrentFolder" class="vault-entry vault-folder" @click="navigateVaultUp">
              <span class="vault-icon">..</span>
              <span class="vault-name">(parent folder)</span>
            </button>
            <button
              v-for="entry in vaultFiles"
              :key="entry.path"
              class="vault-entry"
              :class="isFolder(entry) ? 'vault-folder' : 'vault-file'"
              @click="isFolder(entry) ? navigateVaultFolder(entry.path) : selectVaultFile(entry.path)"
            >
              <span class="vault-icon">{{ isFolder(entry) ? "\uD83D\uDCC1" : "\uD83D\uDCC4" }}</span>
              <span class="vault-name">{{ entry.name }}</span>
            </button>
            <p v-if="vaultFiles.length === 0" class="loading-text">No files found in this folder.</p>
          </template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.64);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  outline: none;
}

.settings-panel {
  width: min(1200px, 90vw);
  height: min(85vh, 960px);
  max-height: 85vh;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
}

.settings-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--border);
}

.settings-header h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}

.header-subtitle {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 12px;
}

.close-btn {
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 14px;
  cursor: pointer;
  padding: 6px 10px;
  border-radius: var(--radius);
}

.close-btn:hover {
  background: var(--bg-panel);
  color: var(--text-primary);
}

.settings-tabs {
  display: flex;
  border-bottom: 1px solid var(--border);
  padding: 0 12px;
}

.settings-tabs button {
  flex: 1;
  padding: 12px 18px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  text-transform: uppercase;
  letter-spacing: 0.6px;
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
  padding: 24px;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 20px;
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

.hint {
  margin: 0;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.5;
}

.hint.compact {
  margin-top: 0;
}

.section {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 18px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.section h3 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.agent-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px;
}

.card-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
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
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px 12px;
}

.card-fields label,
.kb-config label {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.card-fields label span,
.kb-config label > span {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}

.field-input {
  padding: 7px 10px;
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
  margin-top: 10px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
}

.cap-label {
  margin-right: 4px;
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}

.cap-tag {
  font-size: 10px;
  padding: 2px 6px;
  background: rgba(0, 212, 255, 0.1);
  color: var(--accent-main);
  border-radius: 3px;
}

.role-checkboxes {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 6px;
  margin-top: 2px;
}

.role-checkbox {
  display: flex;
  align-items: center;
  gap: 4px;
  color: var(--text-secondary);
  font-size: 11px;
  cursor: pointer;
}

.role-checkbox input[type="checkbox"] {
  margin: 0;
  cursor: pointer;
}

.role-hint {
  margin: 8px 0 0;
  font-size: 10px;
  color: var(--text-muted);
  font-style: italic;
  line-height: 1.45;
}

.cap-add,
.browse-btn,
.refresh-btn {
  padding: 6px 12px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
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
  padding: 10px 12px;
  background: var(--bg-secondary);
  border: 1px dashed var(--border);
  border-radius: 12px;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.add-agent-btn:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
}

.dir-input-row,
.context-status-row,
.context-group-header,
.role-context-card-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.dir-field {
  flex: 1;
  min-width: 0;
}

.browse-btn:hover {
  border-color: var(--accent-main);
  color: var(--accent-main);
}

.toggle-row {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
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
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 16px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--border);
  border-radius: 12px;
}

.context-group,
.role-context-shell,
.context-status-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: 10px;
}

.context-file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.context-file-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.context-file {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-secondary);
  padding: 7px 9px;
  background: rgba(0, 0, 0, 0.16);
  border-radius: 6px;
}

.empty-state {
  margin: 0;
  padding: 10px 12px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  color: var(--text-muted);
  font-size: 11px;
}

.section-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
  padding: 0;
  background: none;
  border: none;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
}

.section-toggle-state {
  color: var(--accent-main);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.role-context-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 12px;
}

.role-context-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid var(--border);
  border-radius: 10px;
}

.role-context-card h4 {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 600;
}

.role-override-badge {
  font-size: 11px;
  color: var(--text-muted);
}

.role-override-badge.overridden {
  color: var(--accent-main);
}

.role-editor-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--accent-main);
  border-radius: 10px;
  grid-column: 1 / -1;
}

.role-editor-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.role-editor-header h4 {
  margin: 0;
  font-size: 13px;
  font-weight: 600;
}

.role-editor-actions {
  display: flex;
  gap: 8px;
}

.role-editor-restore {
  padding: 4px 10px;
  background: none;
  border: 1px solid var(--border);
  border-radius: 6px;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
}

.role-editor-restore:hover:not(:disabled) {
  color: var(--text-secondary);
  border-color: var(--text-muted);
}

.role-editor-restore:disabled {
  opacity: 0.4;
  cursor: default;
}

.role-editor-close {
  padding: 4px 12px;
  background: var(--accent-main);
  border: none;
  border-radius: 6px;
  color: var(--bg-primary);
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
}

.role-editor-close:hover {
  filter: brightness(1.1);
}

.role-editor-textarea {
  width: 100%;
  min-height: 280px;
  max-height: 420px;
  padding: 12px 14px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 8px;
  color: var(--text-primary);
  font-family: "Cascadia Code", "Fira Code", "JetBrains Mono", "SF Mono", monospace;
  font-size: 12px;
  line-height: 1.6;
  tab-size: 2;
  resize: vertical;
  white-space: pre;
  overflow: auto;
}

.role-editor-textarea:focus {
  outline: none;
  border-color: var(--accent-main);
}

.role-editor-loading,
.role-editor-error {
  padding: 20px;
  text-align: center;
  font-size: 12px;
}

.role-editor-loading {
  color: var(--text-muted);
}

.role-editor-error {
  color: var(--accent-error);
}

.context-status-info {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.status-badge {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
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
  border: 1px solid var(--accent-main);
  color: var(--accent-main);
  font-weight: 500;
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
  margin: 0;
}

.settings-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 24px 20px;
  border-top: 1px solid var(--border);
}

.save-btn {
  padding: 8px 22px;
  background: var(--accent-main);
  color: var(--bg-primary);
  border: none;
  border-radius: var(--radius);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.save-btn:hover {
  opacity: 0.92;
}

.save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.vault-browser-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}

.vault-browser {
  width: min(720px, 84vw);
  max-height: min(72vh, 640px);
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 12px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.vault-browser-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
}

.vault-browser-header h3 {
  margin: 0 0 6px;
  font-size: 13px;
  font-weight: 600;
}

.vault-browser-breadcrumb {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 8px 16px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  font-size: 11px;
  font-family: var(--font-mono);
  overflow-x: auto;
}

.breadcrumb-segment {
  background: none;
  border: none;
  color: var(--accent-main);
  font-size: 11px;
  font-family: var(--font-mono);
  cursor: pointer;
  padding: 1px 2px;
  border-radius: 2px;
}

.breadcrumb-segment:hover {
  background: rgba(0, 212, 255, 0.1);
}

.breadcrumb-sep {
  color: var(--text-muted);
  margin: 0 1px;
}

.vault-browser-body {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
  display: flex;
  flex-direction: column;
}

.vault-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: none;
  border: none;
  text-align: left;
  font-size: 12px;
  color: var(--text-primary);
  cursor: pointer;
  width: 100%;
}

.vault-entry:hover {
  background: var(--bg-panel);
}

.vault-icon {
  flex-shrink: 0;
  width: 16px;
  text-align: center;
  font-size: 13px;
}

.vault-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.vault-folder .vault-name {
  color: var(--accent-main);
  font-weight: 500;
}

.vault-file .vault-name {
  color: var(--text-secondary);
  font-family: var(--font-mono);
}

@media (max-width: 900px) {
  .settings-panel {
    width: 94vw;
    height: 90vh;
    max-height: 90vh;
  }

  .card-fields {
    grid-template-columns: 1fr;
  }

  .role-checkboxes {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .dir-input-row,
  .context-status-row,
  .context-group-header,
  .role-context-card-header {
    flex-direction: column;
  }
}

@media (max-width: 640px) {
  .settings-body,
  .settings-header,
  .settings-footer {
    padding-left: 16px;
    padding-right: 16px;
  }

  .settings-tabs {
    padding: 0;
  }

  .settings-tabs button {
    padding: 10px 12px;
  }
}
</style>
