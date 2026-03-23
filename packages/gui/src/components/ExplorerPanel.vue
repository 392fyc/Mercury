<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { readDir } from "@tauri-apps/plugin-fs";
import { useAgentStore } from "../stores/agents";

const emit = defineEmits<{
  "open-file": [path: string, name: string];
}>();

const { defaultWorkDir, getWorkDir, getGitBranch } = useAgentStore();

// Use main agent panel key for workspace info
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


// ─── File tree ───
interface TreeNode {
  name: string;
  path: string;
  isDir: boolean;
  children?: TreeNode[];
  expanded?: boolean;
  loading?: boolean;
}

const tree = ref<TreeNode[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

// Dirs to exclude from display
const HIDDEN_DIRS = new Set([
  "node_modules", ".git", "target", "dist", ".nuxt", ".next",
  "__pycache__", ".venv", ".claude",
]);

async function loadDir(dirPath: string): Promise<TreeNode[]> {
  try {
    const entries = await readDir(dirPath);
    const nodes: TreeNode[] = [];
    for (const entry of entries) {
      if (HIDDEN_DIRS.has(entry.name)) continue;
      if (entry.name.startsWith(".") && entry.isDirectory) continue;
      nodes.push({
        name: entry.name,
        path: `${dirPath}/${entry.name}`.replace(/\\/g, "/"),
        isDir: entry.isDirectory,
        children: entry.isDirectory ? undefined : undefined,
        expanded: false,
      });
    }
    // Sort: dirs first, then alphabetical
    nodes.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
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
    tree.value = await loadDir(dir);
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
  node.children = await loadDir(node.path);
  node.expanded = true;
  node.loading = false;
}

function refreshTree() {
  loadRoot();
}

function fileIcon(name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() ?? "";
  switch (ext) {
    case "vue": return "🟢";
    case "ts": case "tsx": return "🔷";
    case "js": case "jsx": return "🟡";
    case "rs": return "🦀";
    case "css": case "scss": return "🎨";
    case "json": return "📋";
    case "md": return "📝";
    case "toml": case "yaml": case "yml": return "⚙️";
    case "png": case "jpg": case "jpeg": case "gif": case "svg": case "webp": return "🖼️";
    default: return "📄";
  }
}

watch(workDir, () => loadRoot(), { immediate: false });

onMounted(() => {
  if (workDir.value) loadRoot();
});
</script>

<template>
  <div class="explorer-panel">
    <!-- Header -->
    <div class="ep-header">
      <span class="ep-title">Explorer</span>
    </div>

    <!-- Workspace directory (under header, above tree) -->
    <button v-if="workDir" class="ep-workspace-dir" :title="workDir">
      <span class="ep-ws-icon">📂</span>
      <span class="ep-ws-name">{{ shortWorkDir }}</span>
    </button>

    <!-- File tree (indented under workspace) -->
    <div class="ep-tree">
      <div v-if="loading && tree.length === 0" class="ep-loading">Loading...</div>
      <div v-else-if="error" class="ep-error">{{ error }}</div>
      <div v-else-if="tree.length === 0" class="ep-empty">No workspace</div>
      <template v-else>
        <template v-for="node in tree" :key="node.path">
          <div
            class="ep-node"
            :class="{ dir: node.isDir }"
            @click="toggleExpand(node)"
          >
            <span v-if="node.isDir" class="ep-arrow" :class="{ expanded: node.expanded }">▶</span>
            <span v-else class="ep-file-icon">{{ fileIcon(node.name) }}</span>
            <span class="ep-name" :class="{ 'is-dir': node.isDir }">{{ node.name }}</span>
          </div>
          <!-- Children (level 1) -->
          <template v-if="node.isDir && node.expanded && node.children">
            <template v-for="child in node.children" :key="child.path">
              <div
                class="ep-node depth-1"
                :class="{ dir: child.isDir }"
                @click="toggleExpand(child)"
              >
                <span v-if="child.isDir" class="ep-arrow" :class="{ expanded: child.expanded }">▶</span>
                <span v-else class="ep-file-icon">{{ fileIcon(child.name) }}</span>
                <span class="ep-name" :class="{ 'is-dir': child.isDir }">{{ child.name }}</span>
              </div>
              <!-- Children (level 2) -->
              <template v-if="child.isDir && child.expanded && child.children">
                <div
                  v-for="grandchild in child.children"
                  :key="grandchild.path"
                  class="ep-node depth-2"
                  :class="{ dir: grandchild.isDir }"
                  @click="toggleExpand(grandchild)"
                >
                  <span v-if="grandchild.isDir" class="ep-arrow" :class="{ expanded: grandchild.expanded }">▶</span>
                  <span v-else class="ep-file-icon">{{ fileIcon(grandchild.name) }}</span>
                  <span class="ep-name" :class="{ 'is-dir': grandchild.isDir }">{{ grandchild.name }}</span>
                </div>
              </template>
            </template>
          </template>
        </template>
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

/* ─── Header ─── */
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


/* ─── Tree ─── */
.ep-tree {
  flex: 1;
  overflow-y: auto;
  padding: 0 4px 0 8px;
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

.ep-node:hover {
  background: rgba(0, 212, 255, 0.06);
}

.ep-node.depth-1 { padding-left: 20px; }
.ep-node.depth-2 { padding-left: 36px; }

.ep-arrow {
  font-size: 8px;
  color: var(--text-muted);
  transition: transform 0.15s;
  width: 12px;
  text-align: center;
  flex-shrink: 0;
}

.ep-arrow.expanded {
  transform: rotate(90deg);
}

.ep-file-icon {
  font-size: 10px;
  width: 12px;
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

.ep-loading,
.ep-error,
.ep-empty {
  padding: 16px 12px;
  text-align: center;
  color: var(--text-muted);
  font-size: 11px;
}

.ep-error {
  color: var(--accent-error);
}

/* ─── Workspace directory (under header) ─── */
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

.ep-workspace-dir:hover {
  background: rgba(255, 255, 255, 0.04);
}

.ep-ws-icon {
  font-size: 12px;
  flex-shrink: 0;
}

.ep-ws-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ─── Footer: branch + refresh ─── */
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

.ep-branch-btn:hover {
  background: rgba(0, 230, 118, 0.08);
}

.ep-branch-icon {
  font-size: 11px;
  flex-shrink: 0;
}

.ep-branch-text {
  overflow: hidden;
  text-overflow: ellipsis;
}

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

.ep-refresh-btn:hover {
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-secondary);
}
</style>
