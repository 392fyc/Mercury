<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useRemoteControlStore } from "../stores/remote-control";

const emit = defineEmits<{ close: [] }>();

const {
  status,
  sessionUrl,
  error,
  logs,
  isRunning,
  start,
  stop,
  initRemoteControlListeners,
} = useRemoteControlStore();

const copyFeedback = ref<string | null>(null);
const isLoading = ref(false);
const unlistenFns: Array<() => void> = [];
/** Guard: if the component unmounts before the async init resolves,
 *  any returned UnlistenFns are executed immediately. */
let disposed = false;

async function handleStart() {
  isLoading.value = true;
  try {
    await start("Mercury");
  } finally {
    isLoading.value = false;
  }
}

async function handleStop() {
  isLoading.value = true;
  try {
    await stop();
  } finally {
    isLoading.value = false;
  }
}

function copyUrl() {
  if (sessionUrl.value) {
    navigator.clipboard.writeText(sessionUrl.value)
      .then(() => {
        copyFeedback.value = "Copied";
        setTimeout(() => (copyFeedback.value = null), 2000);
      })
      .catch(() => {
        copyFeedback.value = "Failed";
        setTimeout(() => (copyFeedback.value = null), 2000);
      });
  }
}

function openInBrowser() {
  if (sessionUrl.value) {
    const win = window.open(sessionUrl.value, "_blank");
    if (!win) {
      // Popup blocked — fall back to clipboard copy
      copyUrl();
    }
  }
}

const statusLabels: Record<string, string> = {
  stopped: "Stopped",
  starting: "Starting...",
  waiting_for_connection: "Waiting for connection",
  connected: "Connected",
  error: "Error",
};

const statusColors: Record<string, string> = {
  stopped: "var(--text-muted)",
  starting: "var(--accent-warn)",
  waiting_for_connection: "var(--accent-main)",
  connected: "var(--accent-success)",
  error: "var(--accent-error)",
};

onMounted(async () => {
  const fns = await initRemoteControlListeners();
  if (disposed) {
    // Component was unmounted while we were awaiting — clean up immediately.
    fns.forEach((fn) => fn());
  } else {
    unlistenFns.push(...fns);
  }
});

onUnmounted(() => {
  disposed = true;
  unlistenFns.forEach((fn) => fn());
});
</script>

<template>
  <div class="rc-overlay" @click.self="emit('close')">
    <div class="rc-panel">
      <!-- Header -->
      <div class="rc-header">
        <div class="rc-title">
          <span class="rc-icon">📡</span>
          Remote Control
        </div>
        <button class="rc-close" @click="emit('close')" title="Close">×</button>
      </div>

      <!-- Status -->
      <div class="rc-status-row">
        <span
          class="rc-status-dot"
          :style="{ background: statusColors[status] ?? 'var(--text-muted)' }"
        ></span>
        <span class="rc-status-label">{{ statusLabels[status] ?? status }}</span>
      </div>

      <!-- Error -->
      <div v-if="error" class="rc-error">{{ error }}</div>

      <!-- Session URL display -->
      <div v-if="sessionUrl" class="rc-url-section">
        <div class="rc-url-label">Session URL</div>
        <div class="rc-url-box">
          <code class="rc-url-text">{{ sessionUrl }}</code>
          <div class="rc-url-actions">
            <button class="rc-btn rc-btn-small" @click="copyUrl" title="Copy URL">
              {{ copyFeedback ?? "Copy" }}
            </button>
            <button class="rc-btn rc-btn-small" @click="openInBrowser" title="Open in browser">
              Open
            </button>
          </div>
        </div>
        <p class="rc-hint">
          Open this URL on your phone or another device to control this session remotely.
          You can also scan the QR code shown in the terminal.
        </p>
      </div>

      <!-- Info when not running -->
      <div v-if="!isRunning && !error" class="rc-info">
        <p>Start a Remote Control session to continue working from your phone, tablet, or another browser.</p>
        <p class="rc-hint">
          Requires Claude Code v2.1.51+ and a Pro/Max subscription with claude.ai OAuth.
          API keys are not supported.
        </p>
      </div>

      <!-- Actions -->
      <div class="rc-actions">
        <button
          v-if="!isRunning"
          class="rc-btn rc-btn-primary"
          :disabled="isLoading"
          @click="handleStart"
        >
          {{ isLoading ? "Starting..." : "Start Remote Control" }}
        </button>
        <button
          v-if="isRunning"
          class="rc-btn rc-btn-danger"
          :disabled="isLoading"
          @click="handleStop"
        >
          {{ isLoading ? "Stopping..." : "Stop" }}
        </button>
      </div>

      <!-- Logs -->
      <div v-if="logs.length > 0" class="rc-logs">
        <div class="rc-logs-label">Logs</div>
        <div class="rc-logs-content">
          <div v-for="(log, i) in logs" :key="i" class="rc-log-line">{{ log }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rc-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
}

.rc-panel {
  width: 480px;
  max-width: 90vw;
  max-height: 80vh;
  overflow-y: auto;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.rc-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.rc-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}

.rc-icon {
  font-size: 18px;
}

.rc-close {
  background: none;
  border: none;
  color: var(--text-muted);
  font-size: 20px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  line-height: 1;
}

.rc-close:hover {
  color: var(--text-primary);
  background: rgba(255, 255, 255, 0.05);
}

.rc-status-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rc-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.rc-status-label {
  font-size: 13px;
  color: var(--text-secondary);
}

.rc-error {
  padding: 8px 12px;
  background: rgba(255, 82, 82, 0.1);
  border: 1px solid rgba(255, 82, 82, 0.3);
  border-radius: var(--radius);
  color: var(--accent-error);
  font-size: 12px;
  font-family: var(--font-mono);
}

.rc-url-section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.rc-url-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
}

.rc-url-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
}

.rc-url-text {
  flex: 1;
  font-size: 12px;
  font-family: var(--font-mono);
  color: var(--accent-main);
  word-break: break-all;
  user-select: all;
}

.rc-url-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}

.rc-info {
  color: var(--text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.rc-hint {
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.4;
  margin-top: 4px;
}

.rc-actions {
  display: flex;
  gap: 8px;
}

.rc-btn {
  padding: 8px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-input);
  color: var(--text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.rc-btn:hover {
  border-color: var(--accent-main);
  background: rgba(0, 212, 255, 0.05);
}

.rc-btn-small {
  padding: 4px 10px;
  font-size: 11px;
}

.rc-btn-primary {
  background: rgba(0, 212, 255, 0.1);
  border-color: var(--accent-main);
  color: var(--accent-main);
}

.rc-btn-primary:hover {
  background: rgba(0, 212, 255, 0.2);
}

.rc-btn-danger {
  background: rgba(255, 82, 82, 0.1);
  border-color: var(--accent-error);
  color: var(--accent-error);
}

.rc-btn-danger:hover {
  background: rgba(255, 82, 82, 0.2);
}

.rc-logs {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.rc-logs-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: var(--text-muted);
}

.rc-logs-content {
  max-height: 120px;
  overflow-y: auto;
  padding: 8px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-muted);
}

.rc-log-line {
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
