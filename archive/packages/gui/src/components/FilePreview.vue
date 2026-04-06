<script setup lang="ts">
/**
 * FilePreview — file viewer/editor with syntax highlighting, minimap, edit mode.
 *
 * Uses:
 * - @tauri-apps/plugin-fs readTextFile / writeTextFile (v2: https://v2.tauri.app/reference/javascript/fs/)
 * - @tauri-apps/api/core convertFileSrc (asset protocol: https://v2.tauri.app/reference/javascript/api/namespacecore/)
 * - shiki v4 codeToHtml (https://shiki.matsu.io/guide/install)
 * - marked for Markdown rendering
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { readTextFile, writeTextFile } from "@tauri-apps/plugin-fs";
import { convertFileSrc } from "@tauri-apps/api/core";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { getGitDiff } from "../lib/tauri-bridge";
import { useAgentStore } from "../stores/agents";

const props = defineProps<{
  filePath: string;
  fileName: string;
}>();

const { defaultWorkDir, getWorkDir } = useAgentStore();

const content = ref("");
const editContent = ref("");
const htmlContent = ref("");
const imageUrl = ref("");
const fileType = ref<"code" | "image" | "markdown" | "binary" | "loading" | "error">("loading");
const errorMsg = ref("");
const fileSize = ref(0);
const isEditing = ref(false);
const isDirty = ref(false);
const isSaving = ref(false);

// ─── Diff mode ───
const showDiff = ref(false);
const diffContent = ref("");
const diffLoading = ref(false);

interface DiffLine {
  type: "add" | "remove" | "context" | "header";
  text: string;
  lineNum?: string;
}

const parsedDiff = computed<DiffLine[]>(() => {
  if (!diffContent.value) return [];
  const lines = diffContent.value.split("\n");
  const result: DiffLine[] = [];
  let oldLine = 0;
  let newLine = 0;
  for (const line of lines) {
    if (line.startsWith("@@")) {
      // Parse hunk header: @@ -oldStart,count +newStart,count @@
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/);
      if (match) {
        oldLine = parseInt(match[1]) - 1;
        newLine = parseInt(match[2]) - 1;
      }
      result.push({ type: "header", text: line });
    } else if (line.startsWith("---") || line.startsWith("+++") || line.startsWith("diff ") || line.startsWith("index ")) {
      result.push({ type: "header", text: line });
    } else if (line.startsWith("+")) {
      newLine++;
      result.push({ type: "add", text: line.slice(1), lineNum: String(newLine) });
    } else if (line.startsWith("-")) {
      oldLine++;
      result.push({ type: "remove", text: line.slice(1), lineNum: String(oldLine) });
    } else {
      oldLine++;
      newLine++;
      result.push({ type: "context", text: line.startsWith(" ") ? line.slice(1) : line, lineNum: String(newLine) });
    }
  }
  return result;
});

async function toggleDiff() {
  if (showDiff.value) {
    showDiff.value = false;
    return;
  }
  diffLoading.value = true;
  try {
    const { mainAgent } = useAgentStore();
    const panelKey = mainAgent.value ? `main:${mainAgent.value.id}` : "";
    const repoPath = (panelKey ? getWorkDir(panelKey) : defaultWorkDir.value) || "";
    if (!repoPath) {
      diffContent.value = "No workspace directory set";
      showDiff.value = true;
      return;
    }
    // Get relative path for git diff
    const normalized = props.filePath.replace(/\\/g, "/");
    const base = repoPath.replace(/\\/g, "/");
    let relPath = normalized.startsWith(base) ? normalized.slice(base.length) : normalized;
    if (relPath.startsWith("/")) relPath = relPath.slice(1);

    // Tauri command using git diff: https://git-scm.com/docs/git-diff
    const diff = await getGitDiff(repoPath, relPath);
    diffContent.value = diff || "No changes (file matches HEAD)";
    showDiff.value = true;
  } catch (err) {
    diffContent.value = `Diff failed: ${err instanceof Error ? err.message : String(err)}`;
    showDiff.value = true;
  } finally {
    diffLoading.value = false;
  }
}

// Refs for scroll sync
const codeContainerEl = ref<HTMLDivElement | null>(null);
const minimapEl = ref<HTMLDivElement | null>(null);
const minimapThumbEl = ref<HTMLDivElement | null>(null);
const editAreaEl = ref<HTMLTextAreaElement | null>(null);

// ─── File type detection ───
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"]);
const MARKDOWN_EXTS = new Set(["md", "mdx", "markdown"]);
const BINARY_EXTS = new Set(["pdf", "zip", "tar", "gz", "7z", "rar", "exe", "dll", "so", "dylib", "wasm", "mp3", "mp4", "avi", "mov", "mkv"]);
const EDITABLE_EXTS = new Set([
  "md", "mdx", "markdown", "txt", "json", "yaml", "yml", "toml",
  "js", "jsx", "ts", "tsx", "vue", "html", "css", "scss", "less",
  "py", "rs", "go", "java", "c", "cpp", "h", "hpp", "sh", "bash",
  "sql", "xml", "svg", "ini", "conf", "env", "dockerfile", "makefile",
  "lua", "rb", "php", "swift", "dart", "r", "ps1", "bat", "cmd",
]);

const MAX_TEXT_SIZE = 512 * 1024;

function getExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

const canEdit = computed(() => {
  const ext = getExtension(props.fileName);
  return EDITABLE_EXTS.has(ext) || MARKDOWN_EXTS.has(ext);
});

// ─── Shiki lazy loader ───
let shikiPromise: Promise<typeof import("shiki")> | null = null;
function getShiki() {
  if (!shikiPromise) shikiPromise = import("shiki");
  return shikiPromise;
}

function extToLang(ext: string): string {
  const map: Record<string, string> = {
    js: "javascript", jsx: "jsx", ts: "typescript", tsx: "tsx",
    vue: "vue", html: "html", css: "css", scss: "scss", less: "less",
    json: "json", yaml: "yaml", yml: "yaml", toml: "toml",
    py: "python", rs: "rust", go: "go", java: "java", kt: "kotlin",
    c: "c", cpp: "cpp", h: "c", hpp: "cpp",
    sh: "bash", bash: "bash", zsh: "bash", fish: "fish",
    sql: "sql", graphql: "graphql", xml: "xml", svg: "xml",
    dockerfile: "dockerfile", makefile: "makefile",
    lua: "lua", ruby: "ruby", rb: "ruby", php: "php",
    swift: "swift", dart: "dart", r: "r",
    ps1: "powershell", bat: "batch", cmd: "batch",
    conf: "ini", ini: "ini", env: "bash", lock: "json",
    md: "markdown", mdx: "markdown", markdown: "markdown",
  };
  return map[ext] || "text";
}

// ─── Load file ───
async function loadFile() {
  fileType.value = "loading";
  content.value = "";
  editContent.value = "";
  htmlContent.value = "";
  imageUrl.value = "";
  errorMsg.value = "";
  isEditing.value = false;
  isDirty.value = false;

  const ext = getExtension(props.fileName);

  try {
    if (IMAGE_EXTS.has(ext)) {
      imageUrl.value = convertFileSrc(props.filePath);
      fileType.value = "image";
      return;
    }

    if (BINARY_EXTS.has(ext)) {
      fileType.value = "binary";
      return;
    }

    // Tauri v2 readTextFile: https://v2.tauri.app/reference/javascript/fs/
    const text = await readTextFile(props.filePath);
    fileSize.value = text.length;

    if (text.length > MAX_TEXT_SIZE) {
      content.value = text.slice(0, MAX_TEXT_SIZE);
      errorMsg.value = `Truncated (${(MAX_TEXT_SIZE / 1024).toFixed(0)}KB of ${(text.length / 1024).toFixed(0)}KB)`;
    } else {
      content.value = text;
    }
    editContent.value = content.value;

    if (MARKDOWN_EXTS.has(ext)) {
      const raw = await marked(content.value);
      htmlContent.value = DOMPurify.sanitize(raw);
      fileType.value = "markdown";
    } else {
      fileType.value = "code";
      try {
        const shiki = await getShiki();
        const lang = extToLang(ext);
        // shiki v4 codeToHtml: https://shiki.matsu.io/guide/install
        const highlighted = await shiki.codeToHtml(content.value, {
          lang,
          theme: "github-dark",
        });
        htmlContent.value = highlighted;
      } catch {
        htmlContent.value = "";
      }
    }

    // Update minimap after render
    nextTick(() => updateMinimapThumb());
  } catch (err: unknown) {
    fileType.value = "error";
    errorMsg.value = err instanceof Error ? err.message : String(err);
  }
}

// ─── Edit mode ───
function toggleEdit() {
  if (isEditing.value) {
    // Switch back to preview
    isEditing.value = false;
    if (isDirty.value) {
      // Re-render preview with edited content
      content.value = editContent.value;
      renderPreview();
    }
  } else {
    editContent.value = content.value;
    isEditing.value = true;
    isDirty.value = false;
    nextTick(() => editAreaEl.value?.focus());
  }
}

function onEditInput() {
  isDirty.value = editContent.value !== content.value;
}

async function saveFile() {
  if (!isDirty.value || isSaving.value) return;
  isSaving.value = true;
  try {
    // Tauri v2 writeTextFile: https://v2.tauri.app/reference/javascript/fs/
    await writeTextFile(props.filePath, editContent.value);
    content.value = editContent.value;
    isDirty.value = false;
    fileSize.value = editContent.value.length;
    await renderPreview();
  } catch (err) {
    errorMsg.value = `Save failed: ${err instanceof Error ? err.message : String(err)}`;
  } finally {
    isSaving.value = false;
  }
}

async function renderPreview() {
  const ext = getExtension(props.fileName);
  if (MARKDOWN_EXTS.has(ext)) {
    const raw = await marked(content.value);
    htmlContent.value = DOMPurify.sanitize(raw);
  } else {
    try {
      const shiki = await getShiki();
      const highlighted = await shiki.codeToHtml(content.value, {
        lang: extToLang(ext),
        theme: "github-dark",
      });
      htmlContent.value = highlighted;
    } catch {
      htmlContent.value = "";
    }
  }
}

// ─── Minimap (canvas-based scroll overview) ───
// Ref: https://www.ben-knight.dev/blog/web/documentscroll/
const minimapVisible = computed(() =>
  (fileType.value === "code" || fileType.value === "markdown") && !isEditing.value && lineCount.value > 30
);

function updateMinimapThumb() {
  const container = codeContainerEl.value;
  const thumb = minimapThumbEl.value;
  const minimap = minimapEl.value;
  if (!container || !thumb || !minimap) return;

  const { scrollTop, scrollHeight, clientHeight } = container;
  const minimapHeight = minimap.clientHeight;
  if (scrollHeight <= 0) return;

  const thumbH = Math.max(20, (clientHeight / scrollHeight) * minimapHeight);
  const thumbTop = (scrollTop / scrollHeight) * minimapHeight;

  thumb.style.height = thumbH + "px";
  thumb.style.top = thumbTop + "px";
}

function onCodeScroll() {
  updateMinimapThumb();
}

let minimapDragging = false;

function onMinimapMouseDown(e: MouseEvent) {
  e.preventDefault();
  minimapDragging = true;
  scrollToMinimapY(e.clientY);
  window.addEventListener("mousemove", onMinimapMouseMove);
  window.addEventListener("mouseup", onMinimapMouseUp);
}

function onMinimapMouseMove(e: MouseEvent) {
  if (!minimapDragging) return;
  scrollToMinimapY(e.clientY);
}

function onMinimapMouseUp() {
  minimapDragging = false;
  window.removeEventListener("mousemove", onMinimapMouseMove);
  window.removeEventListener("mouseup", onMinimapMouseUp);
}

function scrollToMinimapY(clientY: number) {
  const container = codeContainerEl.value;
  const minimap = minimapEl.value;
  if (!container || !minimap) return;

  const rect = minimap.getBoundingClientRect();
  const ratio = (clientY - rect.top) / rect.height;
  container.scrollTop = ratio * (container.scrollHeight - container.clientHeight);
}

onBeforeUnmount(() => {
  window.removeEventListener("mousemove", onMinimapMouseMove);
  window.removeEventListener("mouseup", onMinimapMouseUp);
});

// ─── Keyboard shortcut: Ctrl+S to save ───
function onKeydown(e: KeyboardEvent) {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    if (isEditing.value && isDirty.value) void saveFile();
  }
}

// Watch for file changes
watch(() => props.filePath, () => {
  if (props.filePath) loadFile();
}, { immediate: true });

const lineCount = computed(() => {
  if (!content.value) return 0;
  return content.value.split("\n").length;
});

// Minimap lines (scaled-down representation — proportional to container)
const minimapLines = computed(() => {
  if (!content.value) return [];
  return content.value.split("\n").map((line) => {
    const trimmed = line.replace(/\t/g, "  ");
    return Math.min(trimmed.length, 120);
  });
});

// Proportional line height: scale all lines to fit within minimap container
const minimapLineHeight = computed(() => {
  const count = minimapLines.value.length;
  if (count <= 0) return 2;
  // Use actual minimap container height if available, else estimate
  const el = minimapEl.value;
  const availableH = el ? el.clientHeight - 8 : 400; // subtract padding
  // Each line = height + ~30% gap; total per line = height * 1.3
  // availableH = count * lineH * 1.3  →  lineH = availableH / (count * 1.3)
  const raw = availableH / (count * 1.3);
  return Math.max(1, Math.min(3, raw));
});
</script>

<template>
  <div class="file-preview" @keydown="onKeydown">
    <!-- Toolbar -->
    <div v-if="fileType !== 'loading' && fileType !== 'error' && fileType !== 'binary' && fileType !== 'image'" class="fp-toolbar">
      <div class="fp-toolbar-left">
        <button
          v-if="canEdit"
          class="fp-tool-btn"
          :class="{ active: isEditing }"
          @click="toggleEdit"
          :title="isEditing ? 'Switch to Preview' : 'Edit'"
        >
          {{ isEditing ? '👁 Preview' : '✏️ Edit' }}
        </button>
        <button
          v-if="isEditing && isDirty"
          class="fp-tool-btn fp-save-btn"
          :disabled="isSaving"
          @click="saveFile"
          title="Save (Ctrl+S)"
        >
          {{ isSaving ? '💾 Saving...' : '💾 Save' }}
        </button>
        <span v-if="isDirty" class="fp-dirty-dot" title="Unsaved changes" />
        <button
          class="fp-tool-btn"
          :class="{ active: showDiff }"
          :disabled="diffLoading"
          @click="toggleDiff"
          title="Show git diff"
        >
          {{ diffLoading ? '⏳' : '±' }} Diff
        </button>
      </div>
      <div class="fp-toolbar-right">
        <span class="fp-toolbar-info">{{ lineCount }} lines</span>
        <span v-if="fileSize > 0" class="fp-toolbar-info">{{ (fileSize / 1024).toFixed(1) }}KB</span>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="fileType === 'loading'" class="fp-center">
      <span class="fp-spinner" />
      <span class="fp-loading-text">Loading...</span>
    </div>

    <!-- Error -->
    <div v-else-if="fileType === 'error'" class="fp-center fp-error">
      <span class="fp-error-icon">⚠</span>
      <span>{{ errorMsg }}</span>
    </div>

    <!-- Binary -->
    <div v-else-if="fileType === 'binary'" class="fp-center">
      <span class="fp-binary-icon">📦</span>
      <span>Binary file — preview not available</span>
    </div>

    <!-- Image -->
    <div v-else-if="fileType === 'image'" class="fp-image-container">
      <img :src="imageUrl" :alt="fileName" class="fp-image" />
    </div>

    <!-- Edit mode -->
    <div v-else-if="isEditing" class="fp-edit-container">
      <textarea
        id="file-edit-content"
        name="file-edit-content"
        ref="editAreaEl"
        v-model="editContent"
        class="fp-edit-textarea"
        spellcheck="false"
        @input="onEditInput"
      />
    </div>

    <!-- Diff view (overlays normal preview when active) -->
    <div v-else-if="showDiff" class="fp-diff-container">
      <div v-if="parsedDiff.length === 0" class="fp-center">
        <span>{{ diffContent }}</span>
      </div>
      <div v-else class="fp-diff-lines">
        <div
          v-for="(line, i) in parsedDiff"
          :key="i"
          class="fp-diff-line"
          :class="'diff-' + line.type"
        >
          <span v-if="line.lineNum" class="fp-diff-num">{{ line.lineNum }}</span>
          <span v-else class="fp-diff-num">···</span>
          <span class="fp-diff-prefix">{{ line.type === 'add' ? '+' : line.type === 'remove' ? '-' : line.type === 'header' ? '' : ' ' }}</span>
          <span class="fp-diff-text">{{ line.text }}</span>
        </div>
      </div>
    </div>

    <!-- Markdown preview -->
    <div v-else-if="fileType === 'markdown'" class="fp-content-wrapper">
      <div
        ref="codeContainerEl"
        class="fp-markdown-container"
        @scroll="onCodeScroll"
      >
        <div class="fp-markdown" v-html="htmlContent" />
      </div>
      <!-- Minimap -->
      <div
        v-if="minimapVisible"
        ref="minimapEl"
        class="fp-minimap"
        @mousedown="onMinimapMouseDown"
      >
        <div class="fp-minimap-lines">
          <div
            v-for="(len, i) in minimapLines"
            :key="i"
            class="fp-minimap-line"
            :style="{ width: Math.max(2, len * 0.5) + 'px', height: minimapLineHeight + 'px', marginBottom: Math.max(0, minimapLineHeight * 0.3) + 'px' }"
          />
        </div>
        <div ref="minimapThumbEl" class="fp-minimap-thumb" />
      </div>
    </div>

    <!-- Code preview -->
    <div v-else-if="fileType === 'code'" class="fp-content-wrapper">
      <div
        ref="codeContainerEl"
        class="fp-code-container"
        @scroll="onCodeScroll"
      >
        <div v-if="htmlContent" class="fp-code-html" v-html="htmlContent" />
        <pre v-else class="fp-code-plain"><code>{{ content }}</code></pre>
      </div>
      <!-- Minimap -->
      <div
        v-if="minimapVisible"
        ref="minimapEl"
        class="fp-minimap"
        @mousedown="onMinimapMouseDown"
      >
        <div class="fp-minimap-lines">
          <div
            v-for="(len, i) in minimapLines"
            :key="i"
            class="fp-minimap-line"
            :style="{ width: Math.max(2, len * 0.5) + 'px', height: minimapLineHeight + 'px', marginBottom: Math.max(0, minimapLineHeight * 0.3) + 'px' }"
          />
        </div>
        <div ref="minimapThumbEl" class="fp-minimap-thumb" />
      </div>
    </div>

    <!-- Status bar -->
    <div v-if="fileType !== 'loading' && fileType !== 'error'" class="fp-status-bar">
      <span class="fp-status-item">{{ fileName }}</span>
      <span v-if="errorMsg" class="fp-status-warn">{{ errorMsg }}</span>
    </div>
  </div>
</template>

<style scoped>
.file-preview {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--bg-primary);
  overflow: hidden;
}

/* ─── Toolbar ─── */
.fp-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 12px;
  background: var(--bg-secondary);
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
  gap: 8px;
}

.fp-toolbar-left, .fp-toolbar-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.fp-tool-btn {
  background: none;
  border: 1px solid var(--border);
  border-radius: 4px;
  color: var(--text-secondary);
  font-size: 11px;
  padding: 3px 8px;
  cursor: pointer;
  transition: all 0.12s;
}
.fp-tool-btn:hover { background: rgba(255, 255, 255, 0.06); color: var(--text-primary); }
.fp-tool-btn.active { border-color: var(--accent-main); color: var(--accent-main); }

.fp-save-btn { border-color: var(--accent-success); color: var(--accent-success); }
.fp-save-btn:hover { background: rgba(0, 230, 118, 0.08); }

.fp-dirty-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent-warn);
}

.fp-toolbar-info {
  font-size: 10px;
  color: var(--text-muted);
}

/* ─── Centered states ─── */
.fp-center {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  flex: 1;
  gap: 8px;
  color: var(--text-muted);
  font-size: 12px;
}
.fp-error { color: var(--accent-error); }
.fp-error-icon { font-size: 24px; }
.fp-binary-icon { font-size: 24px; opacity: 0.5; }

.fp-spinner {
  width: 18px; height: 18px;
  border: 2px solid var(--border);
  border-top-color: var(--accent-main);
  border-radius: 50%;
  animation: fp-spin 0.6s linear infinite;
}
@keyframes fp-spin { to { transform: rotate(360deg); } }
.fp-loading-text { font-size: 11px; }

/* ─── Image ─── */
.fp-image-container {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
  overflow: auto;
}
.fp-image {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

/* ─── Content wrapper (code/md with overlay minimap) ─── */
.fp-content-wrapper {
  position: relative;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}

/* ─── Code preview — always left-aligned ─── */
.fp-code-container {
  width: 100%;
  height: 100%;
  min-height: 0;
  min-width: 0;
  overflow: auto;
}

.fp-code-html :deep(pre) {
  margin: 0;
  padding: 12px 16px;
  font-size: 12px;
  line-height: 1.6;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  background: transparent !important;
  overflow-x: auto;
  text-align: left;
}
.fp-code-html :deep(code) { font-family: inherit; }

.fp-code-plain {
  margin: 0;
  padding: 12px 16px;
  font-size: 12px;
  line-height: 1.6;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  color: var(--text-primary);
  white-space: pre;
  overflow-x: auto;
  text-align: left;
}

/* ─── Markdown preview — left-aligned ─── */
.fp-markdown-container {
  width: 100%;
  height: 100%;
  min-height: 0;
  min-width: 0;
  overflow: auto;
  padding: 16px 20px;
  text-align: left;
}

.fp-markdown :deep(h1),
.fp-markdown :deep(h2),
.fp-markdown :deep(h3) {
  color: var(--text-primary);
  margin: 16px 0 8px;
}
.fp-markdown :deep(h1) { font-size: 20px; border-bottom: 1px solid var(--border); padding-bottom: 6px; }
.fp-markdown :deep(h2) { font-size: 16px; }
.fp-markdown :deep(h3) { font-size: 14px; }

.fp-markdown :deep(p) {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.6;
  margin: 6px 0;
}

.fp-markdown :deep(code) {
  background: rgba(0, 212, 255, 0.08);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 12px;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
}

.fp-markdown :deep(pre) {
  background: var(--bg-secondary);
  padding: 10px 14px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 12px;
  text-align: left;
}
.fp-markdown :deep(pre code) { background: none; padding: 0; }
.fp-markdown :deep(a) { color: var(--accent-main); }

.fp-markdown :deep(table) { border-collapse: collapse; width: 100%; font-size: 12px; }
.fp-markdown :deep(th), .fp-markdown :deep(td) { border: 1px solid var(--border); padding: 6px 10px; text-align: left; }
.fp-markdown :deep(th) { background: var(--bg-secondary); font-weight: 600; }

.fp-markdown :deep(blockquote) {
  border-left: 3px solid var(--accent-main);
  margin: 8px 0;
  padding: 4px 12px;
  color: var(--text-muted);
}

/* ─── Edit mode ─── */
.fp-edit-container {
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  display: flex;
}

.fp-edit-textarea {
  flex: 1;
  min-width: 0;
  width: 100%;
  height: 100%;
  background: var(--bg-primary);
  color: var(--text-primary);
  border: none;
  outline: none;
  resize: none;
  padding: 12px 16px;
  font-size: 12px;
  line-height: 1.6;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  tab-size: 2;
  white-space: pre;
  overflow: auto;
  box-sizing: border-box;
}

.fp-edit-textarea:focus {
  box-shadow: inset 0 0 0 1px rgba(0, 212, 255, 0.15);
}

/* ─── Minimap (VS Code-style overlay, right-top with margin) ─── */
.fp-minimap {
  position: absolute;
  top: 8px;
  right: 12px;
  width: 64px;
  max-height: calc(100% - 16px);
  background: rgba(30, 30, 50, 0.5);
  border-radius: 3px;
  overflow: hidden;
  cursor: pointer;
  z-index: 5;
  opacity: 0.4;
  transition: opacity 0.2s;
}

.fp-content-wrapper:hover .fp-minimap {
  opacity: 0.9;
}

.fp-minimap-lines {
  padding: 4px 4px;
}

.fp-minimap-line {
  /* height and margin-bottom set via inline style for proportional scaling */
  background: rgba(200, 200, 220, 0.2);
  border-radius: 1px;
  min-height: 1px;
}

.fp-minimap-thumb {
  position: absolute;
  left: 0;
  right: 0;
  background: rgba(0, 212, 255, 0.15);
  border-left: 2px solid rgba(0, 212, 255, 0.5);
  min-height: 20px;
  transition: top 0.05s;
}

.fp-minimap:hover .fp-minimap-thumb {
  background: rgba(0, 212, 255, 0.25);
}

/* ─── Status bar ─── */
.fp-status-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 12px;
  background: var(--bg-secondary);
  border-top: 1px solid var(--border);
  font-size: 10px;
  color: var(--text-muted);
  flex-shrink: 0;
}

.fp-status-warn {
  color: var(--accent-warn);
  margin-left: auto;
}

/* ─── Diff view ─── */
.fp-diff-container {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--bg-primary);
}

.fp-diff-lines {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  font-size: 12px;
  line-height: 1.6;
}

.fp-diff-line {
  display: flex;
  padding: 0 12px 0 0;
  white-space: pre;
  min-height: 19px;
}

.fp-diff-num {
  display: inline-block;
  width: 40px;
  min-width: 40px;
  text-align: right;
  padding-right: 8px;
  color: var(--text-muted);
  opacity: 0.5;
  flex-shrink: 0;
  user-select: none;
}

.fp-diff-prefix {
  display: inline-block;
  width: 16px;
  min-width: 16px;
  text-align: center;
  flex-shrink: 0;
  font-weight: 600;
}

.fp-diff-text {
  flex: 1;
  min-width: 0;
}

.fp-diff-line.diff-add {
  background: rgba(46, 160, 67, 0.12);
  color: #7ee787;
}
.fp-diff-line.diff-add .fp-diff-prefix { color: #3fb950; }

.fp-diff-line.diff-remove {
  background: rgba(248, 81, 73, 0.12);
  color: #ffa198;
}
.fp-diff-line.diff-remove .fp-diff-prefix { color: #f85149; }

.fp-diff-line.diff-context {
  color: var(--text-secondary);
}

.fp-diff-line.diff-header {
  color: var(--accent-sub);
  font-weight: 500;
  background: rgba(139, 148, 158, 0.06);
  padding-left: 56px;
}
</style>
