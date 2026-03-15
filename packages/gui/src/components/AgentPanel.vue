<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { useAgentStore } from "../stores/agents";
import { useMessageStore } from "../stores/messages";

const props = defineProps<{
  agentId: string;
  agentName: string;
  role: "main" | "dev" | "acceptance" | "research";
}>();

const { getStatus } = useAgentStore();
const { getMessages, sendPrompt } = useMessageStore();

const inputText = ref("");
const messagesEl = ref<HTMLDivElement>();

const status = computed(() => getStatus(props.agentId));
const messages = computed(() => getMessages(props.agentId));

const roleColor = computed(() => {
  switch (props.role) {
    case "main": return "var(--accent-main)";
    case "dev": return "var(--accent-sub)";
    case "acceptance": return "var(--accent-warn)";
    case "research": return "var(--accent-success)";
  }
});

async function handleSend() {
  const prompt = inputText.value.trim();
  if (!prompt) return;

  inputText.value = "";
  await sendPrompt(props.agentId, prompt);
}

// Auto-scroll on new messages
watch(
  () => messages.value.length,
  async () => {
    await nextTick();
    if (messagesEl.value) {
      messagesEl.value.scrollTop = messagesEl.value.scrollHeight;
    }
  },
);
</script>

<template>
  <div class="agent-panel" :class="{ active: status === 'active' }">
    <div class="panel-header">
      <div class="agent-info">
        <span class="agent-dot" :style="{ background: roleColor }"></span>
        <span class="agent-name">{{ agentName }}</span>
        <span class="agent-role">{{ role }}</span>
      </div>
      <div class="panel-status">
        <span class="status-badge" :class="status">{{ status }}</span>
      </div>
    </div>

    <div class="panel-messages" ref="messagesEl">
      <div v-if="messages.length === 0" class="empty-state">
        <p>No messages yet</p>
        <p class="hint">Type below to send a prompt</p>
      </div>
      <div
        v-for="(msg, i) in messages"
        :key="i"
        class="message"
        :class="msg.role"
      >
        <div class="message-content">{{ msg.content }}</div>
      </div>
    </div>

    <div class="panel-input">
      <input
        v-model="inputText"
        type="text"
        :placeholder="`Send to ${agentName}...`"
        :disabled="status === 'active'"
        @keydown.enter="handleSend"
      />
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
}

.agent-panel.active {
  border-color: var(--border-active);
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border);
}

.agent-info {
  display: flex;
  align-items: center;
  gap: 8px;
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

.status-badge {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 3px;
  text-transform: uppercase;
}

.status-badge.idle {
  background: var(--bg-panel);
  color: var(--text-muted);
}

.status-badge.active {
  background: rgba(0, 212, 255, 0.15);
  color: var(--accent-main);
}

.status-badge.error {
  background: rgba(255, 82, 82, 0.15);
  color: var(--accent-error);
}

.panel-messages {
  flex: 1;
  overflow-y: auto;
  padding: 12px;
  min-height: 0;
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
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}

.message.user {
  background: rgba(0, 212, 255, 0.08);
  border-left: 2px solid var(--accent-main);
}

.message.assistant {
  background: rgba(123, 104, 238, 0.08);
  border-left: 2px solid var(--accent-sub);
}

.message.system {
  background: rgba(255, 82, 82, 0.08);
  border-left: 2px solid var(--accent-error);
  color: var(--accent-error);
}

.panel-input {
  padding: 8px;
  border-top: 1px solid var(--border);
}

.panel-input input {
  width: 100%;
  padding: 8px 12px;
  background: var(--bg-input);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text-primary);
  font-family: var(--font-mono);
  font-size: 12px;
  outline: none;
}

.panel-input input:focus {
  border-color: var(--accent-main);
}

.panel-input input::placeholder {
  color: var(--text-muted);
}

.panel-input input:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
