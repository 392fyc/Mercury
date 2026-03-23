<script setup lang="ts">
/**
 * ExplorerPanel — workspace file tree with context menu.
 *
 * Uses @tauri-apps/plugin-fs (v2): readDir, writeTextFile, mkdir
 * Ref: https://v2.tauri.app/reference/javascript/fs/
 */
import { computed, onMounted, ref, watch } from "vue";
import { readDir, writeTextFile, mkdir } from "@tauri-apps/plugin-fs";
import { useAgentStore } from "../stores/agents";

const emit = defineEmits<{
  "open-file": [path: string, name: string];
}>();

const { defaultWorkDir, getWorkDir, getGitBranch } = useAgentStore();

const mainPanelKey = computed(() => {
  const { mainAgent } = useAgentStore();
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

// ─── Show hidden files toggle ───
const showHidden = ref(true); // Default: show dotfiles

// ─── File tree ───
interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  children?: TreeNode[];
  expanded?: boolean;
  loading?: boolean;
  depth: number;
}

const tree = ref<TreeNode[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

// Heavy dirs: collapsed by default, shown in tree but not auto-expanded
const HEAVY_DIRS = new Set(["node_modules", "target", "dist", ".git", "__pycache__", ".venv"]);

async function loadDir(dirPath: string, depth: number): Promise<TreeNode[]> {
  try {
    const entries = await readDir(dirPath);
    const nodes: TreeNode[] = [];
    for (const entry of entries) {
      // Filter hidden files when toggle is off
      if (!showHidden.value && entry.name.startsWith(".")) continue;
      nodes.push({
        name: entry.name,
        path: `${dirPath}/${entry.name}`.replace(/\\/g, "/"),
        isDir: entry.isDirectory,
        children: undefined,
        expanded: false,
        depth,
      });
    }
    // Sort: dirs first, then alphabetical (case-insensitive)
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

// ─── Flatten tree for virtual rendering ───
interface FlatNode extends TreeNode {
  indent: number;
}

const flatTree = computed<FlatNode[]>(() => {
  const result: FlatNode[] = [];
  function walk(nodes: TreeNode[], indent: number) {
    for (const n of nodes) {
      result.push({ ...n, indent });
      if (n.isDir && n.expanded && n.children) {
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

function fileIcon(name: string, isDir: boolean): string {
  if (isDir) {
    if (HEAVY_DIRS.has(name)) return "📦";
    if (name.startsWith(".")) return "📁";
    return "📁";
  }
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
const ctxMenu = ref<{ x: number; y: number; node: TreeNode | null; isBackground: boolean } | null>(null);
const newItemName = ref("");
const newItemType = ref<"file" | "folder" | null>(null);
const newItemParentPath = ref("");

function showContextMenu(e: MouseEvent, node: TreeNode | null) {
  e.preventDefault();
  e.stopPropagation();
  const x = Math.min(e.clientX, window.innerWidth - 180);
  const y = Math.min(e.clientY, window.innerHeight - 200);
  ctxMenu.value = { x, y, node, isBackground: node === null };
}

function hideContextMenu() {
  ctxMenu.value = null;
}

function ctxCopyPath() {
  if (ctxMenu.value?.node) {
    navigator.clipboard.writeText(ctxMenu.value.node.path);
  }
  hideContextMenu();
}

function ctxCopyName() {
  if (ctxMenu.value?.node) {
    navigator.clipboard.writeText(ctxMenu.value.node.name);
  }
  hideContextMenu();
}

function ctxNewFile() {
  const parent = ctxMenu.value?.node?.isDir
    ? ctxMenu.value.node.path
    : workDir.value;
  if (!parent) return;
  newItemParentPath.value = parent;
  newItemType.value = "file";
  newItemName.value = "";
  hideContextMenu();
}

function ctxNewFolder() {
  const parent = ctxMenu.value?.node?.isDir
    ? ctxMenu.value.node.path
    : workDir.value;
  if (!parent) return;
  newItemParentPath.value = parent;
  newItemType.value = "folder";
  newItemName.value = "";
  hideContextMenu();
}

async function confirmNewItem() {
  const name = newItemName.value.trim();
  if (!name || !newItemType.value) {
    newItemType.value = null;
    return;
  }
  const fullPath = `${newItemParentPath.value}/${name}`;
  try {
    if (newItemType.value === "folder") {
      // Tauri v2 mkdir: https://v2.tauri.app/reference/javascript/fs/
      await mkdir(fullPath, { recursive: true });
    } else {
      // Tauri v2 writeTextFile: https://v2.tauri.app/reference/javascript/fs/
      await writeTextFile(fullPath, "");
    }
    refreshTree();
  } catch (err) {
    console.error("[ExplorerPanel] create failed:", err);
  }
  newItemType.value = null;
  newItemName.value = "";
}

function cancelNewItem() {
  newItemType.value = null;
  newItemName.value = "";
}

watch(workDir, () => loadRoot(), { immediate: false });
watch(showHidden, () => loadRoot());

onMounted(() => {
  if (workDir.value) loadRoot();
});
</script>

<template>
  <div class="explorer-panel" @contextmenu="showContextMenu($event, null)">
    <!-- Header -->
    <div class="ep-header">
      <span class="ep-title">Explorer</span>
      <button
        class="ep-toggle-hidden"
        :class="{ active: showHidden }"
        :title="showHidden ? 'Hide dotfiles' : 'Show dotfiles'"
        @click="showHidden = !showHidden"
      >.*</button>
    </div>

    <!-- Workspace directory -->
    <button v-if="workDir" class="ep-workspace-dir" :title="workDir">
      <span class="ep-ws-icon">📂</span>
      <span class="ep-ws-name">{{ shortWorkDir }}</span>
    </button>

    <!-- New item inline input -->
    <div v-if="newItemType" class="ep-new-item">
      <span class="ep-new-icon">{{ newItemType === 'folder' ? '📁' : '📄' }}</span>
      <input
        v-model="newItemName"
        class="ep-new-input"
        :placeholder="newItemType === 'folder' ? 'New folder name...' : 'New file name...'"
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
        <div
          v-for="node in flatTree"
          :key="node.path"
          class="ep-node"
          :class="{ dir: node.isDir }"
          :style="{ paddingLeft: (12 + node.indent * 16) + 'px' }"
          @click="toggleExpand(node)"
          @contextmenu="showContextMenu($event, node)"
        >
          <span v-if="node.isDir" class="ep-arrow" :class="{ expanded: node.expanded }">▶</span>
          <span class="ep-file-icon">{{ fileIcon(node.name, node.isDir) }}</span>
          <span class="ep-name" :class="{ 'is-dir': node.isDir }">{{ node.name }}</span>
          <span v-if="node.loading" class="ep-node-spinner" />
        </div>
      </template>
    </div>

    <!-- Footer: branch + refresh -->
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
      <div
        v-if="ctxMenu"
        class="ep-ctx-menu"
        :style="{ left: ctxMenu.x + 'px', top: ctxMenu.y + 'px' }"
      >
        <button class="ep-ctx-item" @click="ctxNewFile">📄 New File</button>
        <button class="ep-ctx-item" @click="ctxNewFolder">📁 New Folder</button>
        <div class="ep-ctx-sep" />
        <button v-if="ctxMenu.node" class="ep-ctx-item" @click="ctxCopyPath">📋 Copy Path</button>
        <button v-if="ctxMenu.node" class="ep-ctx-item" @click="ctxCopyName">📋 Copy Name</button>
        <div v-if="ctxMenu.node && !ctxMenu.node.isDir" class="ep-ctx-sep" />
        <button
          v-if="ctxMenu.node && !ctxMenu.node.isDir"
          class="ep-ctx-item"
          @click="emit('open-file', ctxMenu.node!.path, ctxMenu.node!.name); hideContextMenu()"
        >
          👁 Open Preview
        </button>
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
  justify-content: space-between;
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

.ep-toggle-hidden {
  background: none;
  border: 1px solid transparent;
  border-radius: 3px;
  color: var(--text-muted);
  font-size: 10px;
  font-family: var(--font-mono);
  cursor: pointer;
  padding: 1px 4px;
  transition: all 0.12s;
}

.ep-toggle-hidden.active {
  color: var(--accent-main);
  border-color: rgba(0, 212, 255, 0.3);
  background: rgba(0, 212, 255, 0.06);
}

/* ─── Workspace dir ─── */
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
.ep-ws-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

/* ─── New item inline ─── */
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

/* ─── Tree ─── */
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
  padding: 3px 8px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 12px;
  color: var(--text-secondary);
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

.ep-file-icon {
  font-size: 10px;
  width: 14px;
  text-align: center;
  flex-shrink: 0;
}

.ep-name {
  overflow: hidden;
  text-overflow: ellipsis;
}
.ep-name.is-dir {
  color: var(--text-primary);
  font-weight: 500;
}

.ep-node-spinner {
  width: 10px;
  height: 10px;
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

/* ─── Footer ─── */
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
  width: 22px;
  height: 22px;
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
.ep-ctx-backdrop {
  position: fixed;
  inset: 0;
  z-index: 9998;
}

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
.ep-ctx-item:hover {
  background: rgba(0, 212, 255, 0.08);
  color: var(--text-primary);
}

.ep-ctx-sep {
  height: 1px;
  background: var(--border);
  margin: 3px 8px;
}
</style>
