<script setup lang="ts">
/**
 * ExplorerPanel — workspace file tree with context menu and git status.
 *
 * Uses @tauri-apps/plugin-fs (v2): readDir, writeTextFile, mkdir
 * Ref: https://v2.tauri.app/reference/javascript/fs/
 * Git status via Tauri command: git status --porcelain
 * Ref: https://git-scm.com/docs/git-status
 */
import { computed, ref, watch } from "vue";
import { readDir, writeTextFile, mkdir } from "@tauri-apps/plugin-fs";
// Tauri v2 dialog: open({directory:true}) for folder picker
// Ref: https://v2.tauri.app/plugin/dialog/
import { open } from "@tauri-apps/plugin-dialog";
import { useAgentStore } from "../stores/agents";
// Tauri invoke bridge: https://v2.tauri.app/develop/calling-rust/
import { getGitFileStatus, setAgentCwd, getGitInfo } from "../lib/tauri-bridge";

const emit = defineEmits<{
  "open-file": [path: string, name: string];
}>();

const { defaultWorkDir, getWorkDir, getGitBranch, setWorkDir, setGitBranch, mainAgent } = useAgentStore();

const mainPanelKey = computed(() => {
  return mainAgent.value ? `main:${mainAgent.value.id}` : "";
});

const workDir = computed(() => {
  if (mainPanelKey.value) return getWorkDir(mainPanelKey.value);
  return defaultWorkDir.value;
});

const gitBranch = computed(() => {
  if (mainPanelKey.value) return getGitBranch(mainPanelKey.value);
  return null;
});

const shortWorkDir = computed(() => {
  const dir = workDir.value;
  if (!dir) return "";
  const parts = dir.replace(/\\/g, "/").split("/");
  return parts[parts.length - 1] || parts[parts.length - 2] || dir;
});

// ─── Change workspace directory ───
async function changeWorkDir() {
  // Tauri v2 dialog open({directory:true}): https://v2.tauri.app/reference/javascript/dialog/
  const selected = await open({
    directory: true,
    multiple: false,
    title: "Select workspace directory",
  });
  if (!selected || typeof selected !== "string") return;
  const pk = mainPanelKey.value;
  if (!pk) {
    console.warn("[ExplorerPanel] No main agent panel key — cannot switch workspace");
    return;
  }
  // Sync backend first; only update frontend state on success
  const agent = mainAgent.value;
  if (agent) {
    try {
      await setAgentCwd(agent.id, selected);
    } catch (err) {
      console.error("[ExplorerPanel] Backend setAgentCwd failed:", err);
      return; // Don't update frontend if backend rejected
    }
  }
  setWorkDir(pk, selected);
  // Refresh git branch for new directory
  try {
    const info = await getGitInfo(selected);
    setGitBranch(pk, info.gitBranch);
  } catch {
    setGitBranch(pk, null);
  }
}

// ─── File tree ───
interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  children: TreeNode[];
  expanded: boolean;
  loading: boolean;
  depth: number;
}

const tree = ref<TreeNode[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

// ─── Git file status ───
const gitStatus = ref<Record<string, string>>({});

async function loadGitStatus() {
  const dir = workDir.value;
  if (!dir) return;
  try {
    gitStatus.value = await getGitFileStatus(dir);
  } catch {
    gitStatus.value = {};
  }
}

// Build a map that includes parent directory status bubbling (like VS Code).
// If a child has M, all ancestor dirs get M; if only U, ancestors get U.
// Priority: M > D > U (M overrides U at parent level).
const gitStatusMap = computed<Record<string, string>>(() => {
  const raw = gitStatus.value;
  const map: Record<string, string> = {};
  const STATUS_PRIORITY: Record<string, number> = { M: 3, D: 2, U: 1, A: 1, R: 2 };

  for (const [relPath, status] of Object.entries(raw)) {
    // Set file itself
    map[relPath] = status;
    // Bubble up to all parent directories
    const parts = relPath.split("/");
    for (let i = 1; i < parts.length; i++) {
      const dirKey = parts.slice(0, i).join("/");
      const existing = map[dirKey];
      const newPri = STATUS_PRIORITY[status] ?? 0;
      const existPri = existing ? (STATUS_PRIORITY[existing] ?? 0) : 0;
      if (newPri > existPri) {
        map[dirKey] = status;
      }
    }
  }
  return map;
});

function getFileGitStatus(filePath: string): string | null {
  const dir = workDir.value;
  if (!dir) return null;
  const normalized = filePath.replace(/\\/g, "/");
  const base = dir.replace(/\\/g, "/");
  let rel = normalized.startsWith(base) ? normalized.slice(base.length) : normalized;
  if (rel.startsWith("/")) rel = rel.slice(1);
  return gitStatusMap.value[rel] ?? null;
}

// ─── Directory loading ───
async function loadDir(dirPath: string, depth: number): Promise<TreeNode[]> {
  try {
    // Tauri v2 readDir: https://v2.tauri.app/reference/javascript/fs/
    const entries = await readDir(dirPath);
    const nodes: TreeNode[] = [];
    for (const entry of entries) {
      nodes.push({
        name: entry.name,
        path: `${dirPath}/${entry.name}`.replace(/\\/g, "/"),
        isDir: entry.isDirectory,
        children: [],
        expanded: false,
        loading: false,
        depth,
      });
    }
    nodes.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });
    return nodes;
  } catch (e) {
    console.error("[ExplorerPanel] readDir failed:", e);
    return [];
  }
}

async function loadRoot() {
  const dir = workDir.value;
  if (!dir) return;
  loading.value = true;
  error.value = null;
  try {
    tree.value = await loadDir(dir, 0);
    loadGitStatus();
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}

async function toggleExpand(node: TreeNode) {
  if (!node.isDir) {
    emit("open-file", node.path, node.name);
    return;
  }
  if (node.expanded) {
    node.expanded = false;
    return;
  }
  node.loading = true;
  node.children = await loadDir(node.path, node.depth + 1);
  node.expanded = true;
  node.loading = false;
}

// ─── Flatten tree (references original nodes, no spread) ───
interface FlatEntry {
  node: TreeNode;
  indent: number;
}

const flatTree = computed<FlatEntry[]>(() => {
  const result: FlatEntry[] = [];
  function walk(nodes: TreeNode[], indent: number) {
    for (const n of nodes) {
      result.push({ node: n, indent });
      if (n.isDir && n.expanded && n.children.length > 0) {
        walk(n.children, indent + 1);
      }
    }
  }
  walk(tree.value, 0);
  return result;
});

function refreshTree() {
  loadRoot();
}

const HEAVY_DIRS = new Set(["node_modules", "target", "dist", ".git", "__pycache__", ".venv", ".next", ".nuxt"]);

function fileIcon(name: string, isDir: boolean): string {
  if (isDir) return HEAVY_DIRS.has(name) ? "📦" : "📁";
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    vue: "🟢", ts: "🔷", tsx: "🔷", js: "🟡", jsx: "🟡",
    rs: "🦀", css: "🎨", scss: "🎨", json: "📋", md: "📝",
    toml: "⚙️", yaml: "⚙️", yml: "⚙️",
    png: "🖼️", jpg: "🖼️", jpeg: "🖼️", gif: "🖼️", svg: "🖼️", webp: "🖼️",
    lock: "🔒", html: "🌐", py: "🐍", sh: "⚡", bash: "⚡",
  };
  return map[ext] || "📄";
}

// ─── Context menu ───
const ctxMenu = ref<{ x: number; y: number; node: TreeNode | null } | null>(null);
const newItemName = ref("");
const newItemType = ref<"file" | "folder" | null>(null);
const newItemParentPath = ref("");

function showContextMenu(e: MouseEvent, node: TreeNode | null) {
  e.preventDefault();
  e.stopPropagation();
  const x = Math.min(e.clientX, window.innerWidth - 180);
  const y = Math.min(e.clientY, window.innerHeight - 200);
  ctxMenu.value = { x, y, node };
}

function hideContextMenu() { ctxMenu.value = null; }

function ctxCopyPath() {
  if (ctxMenu.value?.node) navigator.clipboard.writeText(ctxMenu.value.node.path);
  hideContextMenu();
}
function ctxCopyName() {
  if (ctxMenu.value?.node) navigator.clipboard.writeText(ctxMenu.value.node.name);
  hideContextMenu();
}

function ctxNewFile() {
  const parent = ctxMenu.value?.node?.isDir ? ctxMenu.value.node.path : workDir.value;
  if (!parent) return;
  newItemParentPath.value = parent;
  newItemType.value = "file";
  newItemName.value = "";
  hideContextMenu();
}

function ctxNewFolder() {
  const parent = ctxMenu.value?.node?.isDir ? ctxMenu.value.node.path : workDir.value;
  if (!parent) return;
  newItemParentPath.value = parent;
  newItemType.value = "folder";
  newItemName.value = "";
  hideContextMenu();
}

async function confirmNewItem() {
  const name = newItemName.value.trim();
  if (!name || !newItemType.value) { newItemType.value = null; return; }
  // Sanitize: reject path traversal and embedded separators
  if (/[/\\]/.test(name) || name === ".." || name === "." || name.includes("..")) {
    console.warn("[ExplorerPanel] Invalid filename rejected:", name);
    newItemType.value = null;
    newItemName.value = "";
    return;
  }
  const fullPath = `${newItemParentPath.value}/${name}`;
  try {
    if (newItemType.value === "folder") {
      await mkdir(fullPath, { recursive: true });
    } else {
      await writeTextFile(fullPath, "");
    }
    refreshTree();
  } catch (err) {
    console.error("[ExplorerPanel] create failed:", err);
  }
  newItemType.value = null;
  newItemName.value = "";
}

function cancelNewItem() { newItemType.value = null; newItemName.value = ""; }

watch(workDir, () => loadRoot(), { immediate: true });
</script>

<template>
  <div class="explorer-panel" @contextmenu="showContextMenu($event, null)">
    <div class="ep-header">
      <span class="ep-title">Explorer</span>
    </div>

    <button v-if="workDir" class="ep-workspace-dir" :title="workDir + ' — click to change'" @click="changeWorkDir">
      <span class="ep-ws-icon">📂</span>
      <span class="ep-ws-name">{{ shortWorkDir }}</span>
      <span class="ep-ws-change">⋯</span>
    </button>

    <!-- New item inline -->
    <div v-if="newItemType" class="ep-new-item">
      <span class="ep-new-icon">{{ newItemType === 'folder' ? '📁' : '📄' }}</span>
      <input
        v-model="newItemName"
        class="ep-new-input"
        :placeholder="newItemType === 'folder' ? 'folder name...' : 'file name...'"
        autofocus
        @keydown.enter="confirmNewItem"
        @keydown.esc="cancelNewItem"
        @blur="confirmNewItem"
      />
    </div>

    <!-- File tree -->
    <div class="ep-tree">
      <div v-if="loading && tree.length === 0" class="ep-loading">Loading...</div>
      <div v-else-if="error" class="ep-error">{{ error }}</div>
      <div v-else-if="tree.length === 0" class="ep-empty">No workspace</div>
      <template v-else>
        <button
          v-for="entry in flatTree"
          :key="entry.node.path"
          type="button"
          class="ep-node"
          :class="{ dir: entry.node.isDir }"
          :style="{ paddingLeft: (20 + entry.indent * 16) + 'px' }"
          role="treeitem"
          :aria-expanded="entry.node.isDir ? entry.node.expanded : undefined"
          @click="toggleExpand(entry.node)"
          @keydown.enter="toggleExpand(entry.node)"
          @keydown.space.prevent="toggleExpand(entry.node)"
          @contextmenu="showContextMenu($event, entry.node)"
        >
          <span v-if="entry.node.isDir" class="ep-arrow" :class="{ expanded: entry.node.expanded }">▶</span>
          <span class="ep-file-icon">{{ fileIcon(entry.node.name, entry.node.isDir) }}</span>
          <span class="ep-name" :class="{ 'is-dir': entry.node.isDir }">{{ entry.node.name }}</span>
          <span v-if="entry.node.loading" class="ep-node-spinner" />
          <!-- Git status: files=letter badge, dirs=colored dot (bubbled from children) -->
          <!-- Vue 3 class binding array syntax: https://vuejs.org/guide/essentials/class-and-style.html -->
          <span
            v-if="getFileGitStatus(entry.node.path)"
            class="ep-git-badge"
            :class="[
              'git-' + (getFileGitStatus(entry.node.path) ?? '').toLowerCase(),
              { 'is-dot': entry.node.isDir }
            ]"
          >{{ entry.node.isDir ? '' : getFileGitStatus(entry.node.path) }}</span>
        </button>
      </template>
    </div>

    <!-- Footer -->
    <div class="ep-footer">
      <button v-if="gitBranch" class="ep-branch-btn" :title="'Branch: ' + gitBranch">
        <span class="ep-branch-icon">⎇</span>
        <span class="ep-branch-text">{{ gitBranch }}</span>
      </button>
      <button class="ep-refresh-btn" title="Refresh" @click="refreshTree">↻</button>
    </div>

    <!-- Context menu -->
    <Teleport to="body">
      <div v-if="ctxMenu" class="ep-ctx-backdrop" @click="hideContextMenu" @contextmenu.prevent="hideContextMenu" />
      <div v-if="ctxMenu" class="ep-ctx-menu" :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }">
        <button class="ep-ctx-item" @click="ctxNewFile">📄 New File</button>
        <button class="ep-ctx-item" @click="ctxNewFolder">📁 New Folder</button>
        <div class="ep-ctx-sep" />
        <button v-if="ctxMenu.node" class="ep-ctx-item" @click="ctxCopyPath">📋 Copy Path</button>
        <button v-if="ctxMenu.node" class="ep-ctx-item" @click="ctxCopyName">📋 Copy Name</button>
        <div v-if="ctxMenu.node && !ctxMenu.node.isDir" class="ep-ctx-sep" />
        <button
          v-if="ctxMenu.node && !ctxMenu.node.isDir"
          class="ep-ctx-item"
          @click="emit('open-file', ctxMenu.node?.path ?? '', ctxMenu.node?.name ?? ''); hideContextMenu()"
        >👁 Open Preview</button>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
.explorer-panel {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  background: var(--bg-secondary);
  border-right: 1px solid var(--border);
  user-select: none;
  overflow: hidden;
}

.ep-header {
  display: flex;
  align-items: center;
  padding: 12px 12px 8px;
  flex-shrink: 0;
}

.ep-title {
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.8px;
  color: var(--text-secondary);
}

.ep-workspace-dir {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  margin: 0 0 2px;
  background: none;
  border: none;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  text-align: left;
  width: 100%;
  transition: background 0.12s;
}
.ep-workspace-dir:hover { background: rgba(255, 255, 255, 0.04); }
.ep-ws-icon { font-size: 12px; flex-shrink: 0; }
.ep-ws-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
.ep-ws-change {
  color: var(--text-muted);
  font-size: 14px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.12s;
}
.ep-workspace-dir:hover .ep-ws-change { opacity: 1; }

.ep-new-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 3px 12px;
  flex-shrink: 0;
}
.ep-new-icon { font-size: 10px; }
.ep-new-input {
  flex: 1;
  background: var(--bg-primary);
  border: 1px solid var(--accent-main);
  border-radius: 3px;
  color: var(--text-primary);
  font-size: 11px;
  padding: 3px 6px;
  outline: none;
}

.ep-tree {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 0 4px 0 0;
}

.ep-node {
  display: flex;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: 3px 8px;
  border: none;
  background: none;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
  text-align: left;
  transition: background 0.12s;
  white-space: nowrap;
  overflow: hidden;
}
.ep-node:hover { background: rgba(0, 212, 255, 0.06); }

.ep-arrow {
  font-size: 8px;
  color: var(--text-muted);
  transition: transform 0.15s;
  width: 12px;
  text-align: center;
  flex-shrink: 0;
}
.ep-arrow.expanded { transform: rotate(90deg); }

.ep-file-icon { font-size: 10px; width: 14px; text-align: center; flex-shrink: 0; }
.ep-name { overflow: hidden; text-overflow: ellipsis; flex: 1; }
.ep-name.is-dir { color: var(--text-primary); font-weight: 500; }

/* ─── Git status badges ─── */
.ep-git-badge {
  font-size: 9px;
  font-weight: 700;
  padding: 0 3px;
  border-radius: 2px;
  flex-shrink: 0;
  line-height: 14px;
  min-width: 14px;
  text-align: center;
}
.ep-git-badge.git-u { color: #73c991; background: rgba(115, 201, 145, 0.12); } /* Untracked = green */
.ep-git-badge.git-m { color: #e2c08d; background: rgba(226, 192, 141, 0.12); } /* Modified = yellow */
.ep-git-badge.git-a { color: #73c991; background: rgba(115, 201, 145, 0.12); } /* Added = green */
.ep-git-badge.git-d { color: #f14c4c; background: rgba(241, 76, 76, 0.12); }  /* Deleted = red */
.ep-git-badge.git-r { color: #6ec1e4; background: rgba(110, 193, 228, 0.12); } /* Renamed = blue */

/* Directory dot style (VS Code-like colored dot instead of letter) */
.ep-git-badge.is-dot {
  width: 7px;
  height: 7px;
  min-width: 7px;
  padding: 0;
  border-radius: 50%;
  font-size: 0;
  line-height: 0;
}
.ep-git-badge.is-dot.git-u { background: #73c991; }
.ep-git-badge.is-dot.git-m { background: #e2c08d; }
.ep-git-badge.is-dot.git-a { background: #73c991; }
.ep-git-badge.is-dot.git-d { background: #f14c4c; }
.ep-git-badge.is-dot.git-r { background: #6ec1e4; }

.ep-node-spinner {
  width: 10px; height: 10px;
  border: 1.5px solid var(--border);
  border-top-color: var(--accent-main);
  border-radius: 50%;
  animation: ep-spin 0.5s linear infinite;
  flex-shrink: 0;
  margin-left: 4px;
}
@keyframes ep-spin { to { transform: rotate(360deg); } }

.ep-loading, .ep-error, .ep-empty {
  padding: 16px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 11px;
}
.ep-error { color: var(--accent-error); }

.ep-footer {
  padding: 4px 8px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  background: var(--bg-secondary);
}
.ep-branch-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 6px;
  background: none;
  border: none;
  border-radius: 3px;
  color: var(--accent-success);
  font-size: 11px;
  font-family: var(--font-mono);
  cursor: pointer;
  white-space: nowrap;
  overflow: hidden;
  min-width: 0;
  flex: 1;
  transition: background 0.12s;
}
.ep-branch-btn:hover { background: rgba(0, 230, 118, 0.08); }
.ep-branch-icon { font-size: 11px; flex-shrink: 0; }
.ep-branch-text { overflow: hidden; text-overflow: ellipsis; }
.ep-refresh-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px; height: 22px;
  padding: 0;
  background: none;
  border: none;
  border-radius: 3px;
  color: var(--text-muted);
  font-size: 13px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.12s, color 0.12s;
}
.ep-refresh-btn:hover { background: rgba(255, 255, 255, 0.06); color: var(--text-secondary); }

/* ─── Context Menu ─── */
.ep-ctx-backdrop { position: fixed; inset: 0; z-index: 9998; }
.ep-ctx-menu {
  position: fixed;
  z-index: 9999;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 4px 0;
  min-width: 160px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
}
.ep-ctx-item {
  display: block;
  width: 100%;
  padding: 6px 14px;
  background: none;
  border: none;
  color: var(--text-secondary);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
}
.ep-ctx-item:hover { background: rgba(0, 212, 255, 0.08); color: var(--text-primary); }
.ep-ctx-sep { height: 1px; background: var(--border); margin: 3px 8px; }
</style>
