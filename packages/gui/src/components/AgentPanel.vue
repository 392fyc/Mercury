<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { open } from "@tauri-apps/plugin-dialog";
import { useAgentStore } from "../stores/agents";
import { useMessageStore } from "../stores/messages";
import { getSlashCommands, getGitInfo, setAgentCwd, stopSession, listModels, setModel } from "../lib/tauri-bridge";
import type { SlashCommand, ImageAttachment, ImageMediaType } from "../lib/tauri-bridge";
import SlashCommandPalette from "./SlashCommandPalette.vue";
import ApprovalCard from "./ApprovalCard.vue";

const props = defineProps<{
  agentId: string;
  agentName: string;
  role: "main" | "dev" | "acceptance" | "research" | "design";
  panelKey: string;
}>();

const { agents, getStatus, getSession, getSessionInfo, getWorkDir, setWorkDir, getGitBranch, setGitBranch, clearSession, defaultWorkDir } = useAgentStore();
const { getMessages, sendPrompt, clearMessages, openSessionPicker, openHistory } = useMessageStore();

const inputText = ref("");
const messagesEl = ref<HTMLDivElement>();
const textareaEl = ref<HTMLTextAreaElement>();
const paletteRef = ref<InstanceType<typeof SlashCommandPalette>>();

const status = computed(() => getStatus(props.panelKey));
const messages = computed(() => getMessages(props.panelKey));
const sessionId = computed(() => getSession(props.panelKey));
const sessionInfo = computed(() => getSessionInfo(props.panelKey));
const sessionTitle = computed(() => sessionInfo.value?.sessionName ?? null);
const hasLegacyRoleConfig = computed(() => sessionInfo.value?.legacyRoleConfig === true);
const sessionShortId = computed(() => {
  const id = sessionId.value;
  return id ? id.slice(0, 8) : null;
});

// ─── Workspace ───

// Explicitly reference defaultWorkDir.value so Vue tracks it as a dependency
const workDir = computed(() => getWorkDir(props.panelKey) || defaultWorkDir.value);
const gitBranch = computed(() => getGitBranch(props.panelKey));
const shortWorkDir = computed(() => {
  const dir = workDir.value;
  if (!dir) return "";
  const parts = dir.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || parts[parts.length - 2] || dir;
});

const agentModel = computed(() => {
  const agent = agents.value.find((a) => a.id === props.agentId);
  return agent?.model || null;
});

// ─── Model Picker ───
const showModelPicker = ref(false);
const availableModels = ref<{ id: string; name: string }[]>([]);
const modelPickerLoading = ref(false);

async function toggleModelPicker() {
  if (showModelPicker.value) {
    showModelPicker.value = false;
    return;
  }
  modelPickerLoading.value = true;
  showModelPicker.value = true;
  try {
    availableModels.value = await listModels(props.agentId);
  } catch {
    availableModels.value = [];
  } finally {
    modelPickerLoading.value = false;
  }
}

async function selectModel(modelId: string) {
  showModelPicker.value = false;
  if (modelId === agentModel.value) return;
  try {
    await setModel(props.agentId, modelId);
    // Update local agents store to reflect change immediately
    const agent = agents.value.find((a) => a.id === props.agentId);
    if (agent) agent.model = modelId;
  } catch (err) {
    console.error("Failed to set model:", err);
  }
}

async function refreshGitBranch(path: string) {
  try {
    const info = await getGitInfo(path);
    setGitBranch(props.panelKey, info.gitBranch);
  } catch {
    setGitBranch(props.panelKey, null);
  }
}

// Refresh git branch when workDir becomes available (may resolve async)
watch(workDir, (dir) => {
  if (dir) refreshGitBranch(dir);
}, { immediate: true });

async function handleChangeDir() {
  if (status.value === "active") return;
  const selected = await open({ directory: true, title: "Select workspace directory" });
  if (!selected || typeof selected !== "string") return;

  // Stop current session if active
  const sid = sessionId.value;
  if (sid) {
    try { await stopSession(props.agentId, sid); } catch { /* best-effort */ }
    clearSession(props.panelKey);
  }

  // Clear message history for fresh start
  clearMessages(props.panelKey);

  // Update cwd in store and orchestrator
  setWorkDir(props.panelKey, selected);
  await setAgentCwd(props.agentId, selected);
  await refreshGitBranch(selected);
}

async function handleChangeBranch() {
  if (status.value === "active") return;
  const dir = workDir.value;
  if (!dir) return;
  // Refresh branch info (re-detect from filesystem)
  await refreshGitBranch(dir);
}

// ─── Image Attachments ───

const pendingImages = ref<ImageAttachment[]>([]);
const isDragOver = ref(false);

const ACCEPTED_TYPES: Record<string, ImageMediaType> = {
  "image/png": "image/png",
  "image/jpeg": "image/jpeg",
  "image/gif": "image/gif",
  "image/webp": "image/webp",
};
const MAX_IMAGE_SIZE = 10 * 1024 * 1024; // 10 MB

function fileToAttachment(file: File): Promise<ImageAttachment | null> {
  const mediaType = ACCEPTED_TYPES[file.type];
  if (!mediaType) return Promise.resolve(null);
  if (file.size > MAX_IMAGE_SIZE) return Promise.resolve(null);

  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result as string;
      // Strip "data:image/xxx;base64," prefix
      const base64 = dataUrl.split(",")[1];
      if (!base64) { resolve(null); return; }

      // Get dimensions via Image
      const img = new Image();
      img.onload = () => {
        resolve({
          data: base64,
          mediaType,
          filename: file.name,
          width: img.naturalWidth,
          height: img.naturalHeight,
        });
      };
      img.onerror = () => {
        resolve({ data: base64, mediaType, filename: file.name });
      };
      img.src = dataUrl;
    };
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  });
}

async function addImagesFromFiles(files: FileList | File[]) {
  for (const file of files) {
    const att = await fileToAttachment(file);
    if (att) pendingImages.value = [...pendingImages.value, att];
  }
}

function removeImage(index: number) {
  pendingImages.value = pendingImages.value.filter((_, i) => i !== index);
}

function handlePaste(e: ClipboardEvent) {
  const items = e.clipboardData?.items;
  if (!items) return;

  const imageFiles: File[] = [];
  for (const item of items) {
    if (item.type.startsWith("image/")) {
      const file = item.getAsFile();
      if (file) imageFiles.push(file);
    }
  }
  if (imageFiles.length > 0) {
    e.preventDefault();
    addImagesFromFiles(imageFiles);
  }
}

function handleDrop(e: DragEvent) {
  e.preventDefault();
  isDragOver.value = false;
  const files = e.dataTransfer?.files;
  if (files) {
    const imageFiles = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (imageFiles.length > 0) addImagesFromFiles(imageFiles);
  }
}

function handleDragOver(e: DragEvent) {
  e.preventDefault();
  isDragOver.value = true;
}

function handleDragLeave() {
  isDragOver.value = false;
}

/** Build a data URI from an ImageAttachment for display */
function imageDataUri(img: ImageAttachment): string {
  return `data:${img.mediaType};base64,${img.data}`;
}

function getApprovalRequestId(metadata?: Record<string, unknown>): string | null {
  if (metadata?.messageType !== "approval_request") return null;
  return typeof metadata.approvalRequestId === "string" ? metadata.approvalRequestId : null;
}

// ─── Slash Commands ───

const slashCommands = ref<SlashCommand[]>([]);
const slashCommandSelected = ref(false);
const showSlashPalette = computed(() =>
  inputText.value.startsWith("/") && !slashCommandSelected.value,
);
const slashQuery = computed(() => {
  if (!showSlashPalette.value) return "";
  const text = inputText.value.slice(1);
  const spaceIdx = text.indexOf(" ");
  return spaceIdx === -1 ? text : text.slice(0, spaceIdx);
});

watch(inputText, (val) => {
  if (!val.startsWith("/")) {
    slashCommandSelected.value = false;
  }
});

async function loadSlashCommands() {
  if (slashCommands.value.length > 0) return;
  try {
    slashCommands.value = await getSlashCommands(props.agentId);
  } catch {
    slashCommands.value = [];
  }
}

async function scrollMessagesToBottom() {
  await nextTick();
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
  }
}

onMounted(async () => {
  await loadSlashCommands();
  await scrollMessagesToBottom();
});

function handleSlashSelect(cmd: SlashCommand) {
  slashCommandSelected.value = true;
  const hasArgs = cmd.args && cmd.args.length > 0;
  if (hasArgs) {
    inputText.value = cmd.name + " ";
    nextTick(() => textareaEl.value?.focus());
  } else {
    inputText.value = cmd.name;
    handleSend();
  }
}

// ─── UI helpers ───

const roleColor = computed(() => {
  switch (props.role) {
    case "main": return "var(--accent-main)";
    case "dev": return "var(--accent-sub)";
    case "acceptance": return "var(--accent-warn)";
    case "research": return "var(--accent-success)";
    case "design": return "var(--accent-info, #a78bfa)";
    default: return "var(--text-muted)";
  }
});

marked.setOptions({ breaks: true, gfm: true });

function renderMarkdown(content: string): string {
  const html = marked.parse(content) as string;
  return DOMPurify.sanitize(html);
}

async function handleSend() {
  const prompt = inputText.value.trim();
  if (!prompt && pendingImages.value.length === 0) return;

  const images = pendingImages.value.length > 0 ? [...pendingImages.value] : undefined;
  inputText.value = "";
  pendingImages.value = [];
  resizeTextarea();
  // Use empty prompt for image-only sends — adapter handles content block construction
  await sendPrompt(props.panelKey, prompt, images);
}

function handleKeydown(e: KeyboardEvent) {
  if (showSlashPalette.value && ["ArrowUp", "ArrowDown", "Tab", "Escape"].includes(e.key)) {
    paletteRef.value?.handleKeydown(e);
    return;
  }
  if (showSlashPalette.value && e.key === "Enter" && !e.shiftKey) {
    if (slashCommands.value.length > 0) {
      paletteRef.value?.handleKeydown(e);
      return;
    }
  }
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
}

function resizeTextarea() {
  const el = textareaEl.value;
  if (!el) return;
  el.style.height = "auto";
  const maxH = 120;
  const rawScrollH = el.scrollHeight; // measure before clamping
  el.style.height = Math.min(rawScrollH, maxH) + "px";
  // Show scrollbar when content exceeds max height
  el.style.overflowY = rawScrollH > maxH ? "auto" : "hidden";
}

watch(
  () => messages.value.length,
  scrollMessagesToBottom,
);
</script>

<template>
  <div
    class="agent-panel"
    :class="{ active: status === 'active', 'drag-over': isDragOver }"
    @drop="handleDrop"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
  >
    <div class="panel-header">
      <div class="agent-info">
        <span class="agent-dot" :style="{ background: roleColor }"></span>
        <span class="agent-name">{{ agentName }}</span>
        <span class="agent-role">{{ role }}</span>
        <span class="session-title" v-if="sessionTitle" :title="sessionTitle">
          {{ sessionTitle }}
        </span>
        <span class="session-id" v-if="sessionShortId" :title="sessionId">
          {{ sessionShortId }}
        </span>
        <span
          v-if="hasLegacyRoleConfig"
          class="session-flag"
          title="Role prompt configuration changed after this session started. Mercury still resumes this session with its original frozen prompt."
        >
          Legacy Role Config
        </span>
      </div>
      <div class="panel-status">
        <button
          class="history-button"
          title="Resumable sessions (same role, same agent)"
          @click="openSessionPicker(panelKey)"
        >
          Resume
        </button>
        <button class="history-button" @click="openHistory(panelKey)">
          History
        </button>
        <span class="status-badge" :class="status">
          <span v-if="status === 'active'" class="status-indicator" aria-hidden="true">
            <span class="status-pulse"></span>
            <span class="status-spinner"></span>
          </span>
          <span>{{ status }}</span>
        </span>
      </div>
    </div>

    <div class="panel-messages" ref="messagesEl">
      <div v-if="messages.length === 0" class="empty-state">
        <p>No messages yet</p>
        <p class="hint">Type below to send a prompt (paste or drag images)</p>
      </div>
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="message"
        :class="msg.role"
      >
        <ApprovalCard
          v-if="getApprovalRequestId(msg.metadata)"
          :requestId="getApprovalRequestId(msg.metadata)!"
        />
        <!-- Inline images -->
        <div v-else-if="msg.images && msg.images.length > 0" class="message-images">
          <img
            v-for="(img, j) in msg.images"
            :key="j"
            :src="imageDataUri(img)"
            :alt="img.filename || 'attached image'"
            class="inline-image"
          />
        </div>
        <div
          v-else-if="msg.role === 'assistant'"
          class="message-content markdown-body"
          v-html="renderMarkdown(msg.content)"
        ></div>
        <div v-else class="message-content">{{ msg.content }}</div>
      </div>
    </div>

    <!-- Pending image previews -->
    <div v-if="pendingImages.length > 0" class="pending-images">
      <div v-for="(img, i) in pendingImages" :key="i" class="pending-thumb">
        <img :src="imageDataUri(img)" :alt="img.filename || 'pending'" />
        <button class="remove-btn" @click="removeImage(i)" title="Remove">&times;</button>
      </div>
    </div>

    <!-- Workspace bar (above input, like Claude Desktop) -->
    <div class="workspace-bar" v-if="workDir || agentModel">
      <div class="model-picker-wrapper">
        <button class="workspace-branch" @click="toggleModelPicker" :title="'Model: ' + (agentModel || 'not set')">
          &#9670; {{ agentModel || 'select model' }}
        </button>
        <div v-if="showModelPicker" class="model-picker-dropdown">
          <div v-if="modelPickerLoading" class="model-picker-item disabled">Loading…</div>
          <template v-else-if="availableModels.length">
            <button
              v-for="m in availableModels"
              :key="m.id"
              class="model-picker-item"
              :class="{ active: m.id === agentModel }"
              @click="selectModel(m.id)"
            >{{ m.name || m.id }}</button>
          </template>
          <div v-else class="model-picker-item disabled">No models available</div>
        </div>
      </div>
      <button v-if="workDir" class="workspace-dir" @click="handleChangeDir" :title="workDir" :disabled="status === 'active'">
        {{ shortWorkDir }}
      </button>
      <button v-if="workDir" class="workspace-branch" @click="handleChangeBranch" :disabled="status === 'active'" :title="gitBranch || 'No branch detected'">
        <span class="branch-icon">&#9095;</span>{{ gitBranch || '—' }}
      </button>
    </div>

    <div class="panel-input" style="position: relative;">
      <SlashCommandPalette
        ref="paletteRef"
        :commands="slashCommands"
        :query="slashQuery"
        :visible="showSlashPalette"
        @select="handleSlashSelect"
        @close="inputText = ''"
      />
      <textarea
        ref="textareaEl"
        v-model="inputText"
        :placeholder="`Send to ${agentName}...`"
        :disabled="status === 'active'"
        rows="1"
        @keydown="handleKeydown"
        @input="resizeTextarea"
        @paste="handlePaste"
        @focus="loadSlashCommands"
      ></textarea>
    </div>

    <!-- Drag overlay -->
    <div v-if="isDragOver" class="drag-overlay">
      <span>Drop image here</span>
    </div>
  </div>
</template>

<style scoped>
.agent-panel {
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  min-height: 0;
  min-width: 0;
  height: 100%;
  position: relative;
}

.agent-panel.active {
  border-color: var(--border-active);
}

.agent-panel.drag-over {
  border-color: var(--accent-main);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
  min-width: 0;
}

.panel-status {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.agent-info {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  flex-wrap: wrap;
  flex: 1 1 auto;
}

.agent-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.agent-name {
  font-weight: 600;
  font-size: 13px;
}

.agent-role {
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.5px;
}

.session-title {
  min-width: 0;
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  color: var(--text-secondary);
}

.session-id {
  font-size: 9px;
  font-family: var(--font-mono);
  color: var(--text-muted);
  background: var(--bg-panel);
  padding: 1px 4px;
  border-radius: 3px;
  cursor: default;
  opacity: 0.7;
}

.session-flag {
  font-size: 9px;
  padding: 2px 6px;
  border-radius: 999px;
  border: 1px solid rgba(255, 184, 77, 0.22);
  background: rgba(255, 184, 77, 0.12);
  color: var(--accent-warn);
  white-space: nowrap;
}

.status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  text-transform: uppercase;
  line-height: 1;
}

.status-indicator {
  position: relative;
  width: 11px;
  height: 11px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.status-spinner {
  position: relative;
  z-index: 1;
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 1.5px solid currentColor;
  border-right-color: transparent;
  animation: status-spin 0.85s linear infinite;
}

.status-pulse {
  position: absolute;
  inset: -3px;
  border-radius: 999px;
  background: currentColor;
  opacity: 0.16;
  animation: status-pulse 1.5s ease-out infinite;
}

.history-button {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  color: var(--text-secondary);
  font-size: 10px;
  padding: 2px 6px;
  cursor: pointer;
  -webkit-app-region: no-drag;
}

.history-button:hover:not(:disabled) {
  border-color: var(--accent-main);
  color: var(--text-primary);
}

.history-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.status-badge.idle {
  background: var(--bg-panel);
  color: var(--text-muted);
}

.status-badge.active {
  background: rgba(0, 212, 255, 0.15);
  color: var(--accent-main);
  box-shadow: inset 0 0 0 1px rgba(0, 212, 255, 0.08);
}

.status-badge.error {
  background: rgba(255, 82, 82, 0.15);
  color: var(--accent-error);
}

@keyframes status-spin {
  to {
    transform: rotate(360deg);
  }
}

@keyframes status-pulse {
  0% {
    transform: scale(0.72);
    opacity: 0.2;
  }

  70% {
    transform: scale(1.3);
    opacity: 0;
  }

  100% {
    transform: scale(1.3);
    opacity: 0;
  }
}

.panel-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  min-height: 0;
  overscroll-behavior: contain;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--text-muted);
  font-size: 12px;
  gap: 4px;
}

.hint {
  font-size: 11px;
  color: var(--text-muted);
  opacity: 0.6;
}

.message {
  margin-bottom: 8px;
  padding: 8px 10px;
  border-radius: var(--radius);
  font-size: 12px;
  line-height: 1.5;
  word-break: break-word;
}

.message.user {
  background: rgba(0, 212, 255, 0.08);
  border-left: 2px solid var(--accent-main);
  font-family: var(--font-mono);
  white-space: pre-wrap;
}

.message.assistant {
  background: rgba(123, 104, 238, 0.08);
  border-left: 2px solid var(--accent-sub);
}

.message.system {
  background: rgba(255, 82, 82, 0.08);
  border-left: 2px solid var(--accent-error);
  color: var(--accent-error);
  font-family: var(--font-mono);
  white-space: pre-wrap;
}

/* Inline images in messages */
.message-images {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 6px;
}

.inline-image {
  max-width: 240px;
  max-height: 180px;
  border-radius: 4px;
  border: 1px solid var(--border);
  object-fit: contain;
  background: var(--bg-primary);
  cursor: pointer;
}

.inline-image:hover {
  border-color: var(--accent-main);
}

/* Pending image previews above input */
.pending-images {
  display: flex;
  gap: 6px;
  padding: 6px 8px 0;
  flex-wrap: wrap;
}

.pending-thumb {
  position: relative;
  width: 56px;
  height: 56px;
  border-radius: 4px;
  border: 1px solid var(--border);
  overflow: hidden;
  background: var(--bg-primary);
}

.pending-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.pending-thumb .remove-btn {
  position: absolute;
  top: 0;
  right: 0;
  width: 18px;
  height: 18px;
  background: rgba(0, 0, 0, 0.65);
  color: #fff;
  border: none;
  border-radius: 0 4px 0 4px;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  opacity: 0;
  transition: opacity 0.15s;
}

.pending-thumb:hover .remove-btn {
  opacity: 1;
}

/* Drag overlay */
.drag-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 212, 255, 0.08);
  border: 2px dashed var(--accent-main);
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--accent-main);
  font-size: 14px;
  font-weight: 600;
  pointer-events: none;
  z-index: 10;
}

/* Markdown rendered content */
.markdown-body :deep(p) {
  margin: 0 0 8px 0;
}

.markdown-body :deep(p:last-child) {
  margin-bottom: 0;
}

.markdown-body :deep(pre) {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 10px;
  overflow-x: auto;
  margin: 6px 0;
}

.markdown-body :deep(code) {
  font-family: var(--font-mono);
  font-size: 11px;
}

.markdown-body :deep(:not(pre) > code) {
  background: var(--bg-primary);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  margin: 4px 0;
  padding-left: 20px;
}

.markdown-body :deep(h1),
.markdown-body :deep(h2),
.markdown-body :deep(h3) {
  margin: 8px 0 4px 0;
  font-size: 13px;
  font-weight: 600;
}

.markdown-body :deep(blockquote) {
  border-left: 2px solid var(--border);
  margin: 4px 0;
  padding-left: 10px;
  color: var(--text-secondary);
}

/* Workspace bar above input */
.workspace-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px;
  border-top: 1px solid var(--border);
  font-size: 10px;
}

.workspace-dir {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 1px 6px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 10px;
  cursor: pointer;
  -webkit-app-region: no-drag;
}

.workspace-dir:hover:not(:disabled) {
  border-color: var(--accent-main);
  color: var(--text-primary);
}

.workspace-dir:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.workspace-branch {
  display: flex;
  align-items: center;
  gap: 2px;
  color: var(--accent-main);
  font-family: var(--font-mono);
  font-size: 10px;
  background: none;
  border: 1px solid transparent;
  border-radius: 3px;
  padding: 1px 6px;
  cursor: pointer;
  -webkit-app-region: no-drag;
}

.workspace-branch:hover:not(:disabled) {
  border-color: var(--accent-main);
  background: var(--bg-panel);
}

.workspace-branch:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.workspace-branch .branch-icon {
  font-size: 12px;
  line-height: 1;
}

.workspace-badge {
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 1px 6px;
  color: var(--text-secondary);
  font-family: var(--font-mono);
  font-size: 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 150px;
}

.model-picker-wrapper {
  position: relative;
}

.model-picker-dropdown {
  position: absolute;
  bottom: 100%;
  left: 0;
  margin-bottom: 4px;
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  min-width: 180px;
  max-height: 240px;
  overflow-y: auto;
  z-index: 100;
  padding: 4px 0;
}

.model-picker-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 6px 12px;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-primary);
  background: none;
  border: none;
  cursor: pointer;
  white-space: nowrap;
}

.model-picker-item:hover:not(.disabled) {
  background: var(--bg-panel);
}

.model-picker-item.active {
  color: var(--accent-main);
  font-weight: 600;
}

.model-picker-item.disabled {
  color: var(--text-muted);
  cursor: default;
  font-style: italic;
}

.panel-input {
  padding: 8px;
  flex-shrink: 0;
}

.panel-input textarea {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 12px;
  outline: none;
  resize: none;
  overflow-y: hidden;
  line-height: 1.5;
  box-sizing: border-box;
}

.panel-input textarea:focus {
  border-color: var(--accent-main);
}

.panel-input textarea::placeholder {
  color: var(--text-muted);
}

.panel-input textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@media (max-width: 720px) {
  .panel-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .panel-status {
    width: 100%;
    justify-content: flex-start;
  }

  .session-title {
    max-width: none;
    flex: 1 1 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  .status-spinner,
  .status-pulse {
    animation: none;
  }
}
</style>
