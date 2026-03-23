<script setup lang="ts">
/**
 * FilePreview — displays file content with syntax highlighting, image preview, or markdown rendering.
 *
 * Uses:
 * - @tauri-apps/plugin-fs readTextFile / readFile (v2 API: https://v2.tauri.app/reference/javascript/fs/)
 * - @tauri-apps/api/core convertFileSrc for image asset protocol
 * - shiki v4 codeToHtml (https://shiki.matsu.io/guide/install)
 * - marked for Markdown rendering (already installed)
 */
import { computed, ref, watch } from "vue";
import { readTextFile, readFile } from "@tauri-apps/plugin-fs";
import { convertFileSrc } from "@tauri-apps/api/core";
import { marked } from "marked";
import DOMPurify from "dompurify";

const props = defineProps<{
  filePath: string;
  fileName: string;
}>();

const content = ref("");
const htmlContent = ref("");
const imageUrl = ref("");
const fileType = ref<"code" | "image" | "markdown" | "binary" | "loading" | "error">("loading");
const errorMsg = ref("");
const fileSize = ref(0);

// ─── File type detection ───
const IMAGE_EXTS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "ico"]);
const MARKDOWN_EXTS = new Set(["md", "mdx", "markdown"]);
const BINARY_EXTS = new Set(["pdf", "zip", "tar", "gz", "7z", "rar", "exe", "dll", "so", "dylib", "wasm", "mp3", "mp4", "avi", "mov", "mkv"]);

const MAX_TEXT_SIZE = 512 * 1024; // 512KB max for text preview

function getExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : "";
}

// ─── Shiki lazy loader (loads only once) ───
let shikiPromise: Promise<typeof import("shiki")> | null = null;

function getShiki() {
  if (!shikiPromise) {
    shikiPromise = import("shiki");
  }
  return shikiPromise;
}

// Map file extensions to shiki language identifiers
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
    conf: "ini", ini: "ini", env: "bash",
    lock: "json", // package-lock.json etc.
  };
  return map[ext] || "text";
}

// ─── Load file ───
async function loadFile() {
  fileType.value = "loading";
  content.value = "";
  htmlContent.value = "";
  imageUrl.value = "";
  errorMsg.value = "";

  const ext = getExtension(props.fileName);

  try {
    if (IMAGE_EXTS.has(ext)) {
      // Image: use convertFileSrc for asset protocol
      // Ref: https://v2.tauri.app/reference/javascript/api/namespacecore/
      imageUrl.value = convertFileSrc(props.filePath);
      fileType.value = "image";
      return;
    }

    if (BINARY_EXTS.has(ext)) {
      fileType.value = "binary";
      return;
    }

    // Text file: read content
    const text = await readTextFile(props.filePath);
    fileSize.value = text.length;

    if (text.length > MAX_TEXT_SIZE) {
      content.value = text.slice(0, MAX_TEXT_SIZE);
      errorMsg.value = `File truncated (showing first ${(MAX_TEXT_SIZE / 1024).toFixed(0)}KB of ${(text.length / 1024).toFixed(0)}KB)`;
    } else {
      content.value = text;
    }

    if (MARKDOWN_EXTS.has(ext)) {
      // Markdown: render to HTML
      const raw = await marked(content.value);
      htmlContent.value = DOMPurify.sanitize(raw);
      fileType.value = "markdown";
    } else {
      // Code: syntax highlight with shiki
      fileType.value = "code";
      try {
        const shiki = await getShiki();
        const lang = extToLang(ext);
        // shiki v4 codeToHtml API: https://shiki.matsu.io/guide/install
        const highlighted = await shiki.codeToHtml(content.value, {
          lang,
          theme: "github-dark",
        });
        htmlContent.value = highlighted;
      } catch {
        // Fallback: plain text (shiki might not support the lang)
        htmlContent.value = "";
      }
    }
  } catch (err: unknown) {
    fileType.value = "error";
    errorMsg.value = err instanceof Error ? err.message : String(err);
  }
}

// Watch for file path changes
watch(() => props.filePath, () => {
  if (props.filePath) loadFile();
}, { immediate: true });

const lineCount = computed(() => {
  if (!content.value) return 0;
  return content.value.split("\n").length;
});
</script>

<template>
  <div class="file-preview">
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

    <!-- Markdown -->
    <div v-else-if="fileType === 'markdown'" class="fp-markdown-container">
      <div class="fp-markdown" v-html="htmlContent" />
    </div>

    <!-- Code -->
    <div v-else-if="fileType === 'code'" class="fp-code-container">
      <!-- Highlighted code -->
      <div v-if="htmlContent" class="fp-code-html" v-html="htmlContent" />
      <!-- Fallback: plain text -->
      <pre v-else class="fp-code-plain"><code>{{ content }}</code></pre>
    </div>

    <!-- Status bar -->
    <div v-if="fileType !== 'loading' && fileType !== 'error'" class="fp-status-bar">
      <span class="fp-status-item">{{ fileName }}</span>
      <span v-if="lineCount > 0" class="fp-status-item">{{ lineCount }} lines</span>
      <span v-if="fileSize > 0" class="fp-status-item">{{ (fileSize / 1024).toFixed(1) }}KB</span>
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
  width: 18px;
  height: 18px;
  border: 2px solid var(--border);
  border-top-color: var(--accent-main);
  border-radius: 50%;
  animation: fp-spin 0.6s linear infinite;
}

@keyframes fp-spin {
  to { transform: rotate(360deg); }
}

.fp-loading-text {
  color: var(--text-muted);
  font-size: 11px;
}

/* ─── Image preview ─── */
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

/* ─── Code preview ─── */
.fp-code-container {
  flex: 1;
  min-height: 0;
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
}

.fp-code-html :deep(code) {
  font-family: inherit;
}

.fp-code-plain {
  margin: 0;
  padding: 12px 16px;
  font-size: 12px;
  line-height: 1.6;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  color: var(--text-primary);
  white-space: pre;
  overflow-x: auto;
}

/* ─── Markdown preview ─── */
.fp-markdown-container {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 16px 20px;
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
}

.fp-markdown :deep(pre code) {
  background: none;
  padding: 0;
}

.fp-markdown :deep(a) {
  color: var(--accent-main);
}

.fp-markdown :deep(table) {
  border-collapse: collapse;
  width: 100%;
  font-size: 12px;
}

.fp-markdown :deep(th),
.fp-markdown :deep(td) {
  border: 1px solid var(--border);
  padding: 6px 10px;
  text-align: left;
}

.fp-markdown :deep(th) {
  background: var(--bg-secondary);
  font-weight: 600;
}

.fp-markdown :deep(blockquote) {
  border-left: 3px solid var(--accent-main);
  margin: 8px 0;
  padding: 4px 12px;
  color: var(--text-muted);
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
</style>
