/**
 * Per-agent message history store.
 */

import { ref } from "vue";
import type { ImageAttachment } from "../lib/tauri-bridge";
import {
  sendPrompt as bridgeSendPrompt,
  stopSession as bridgeStopSession,
  onAgentMessage,
  onAgentStreamEnd,
  onAgentError,
} from "../lib/tauri-bridge";
import { useAgentStore } from "./agents";

export interface DisplayMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  images?: ImageAttachment[];
  metadata?: Record<string, unknown>;
}

const messages = ref<Map<string, DisplayMessage[]>>(new Map());

function getMessages(agentId: string): DisplayMessage[] {
  return messages.value.get(agentId) ?? [];
}

function appendMessage(agentId: string, msg: DisplayMessage) {
  const current = messages.value.get(agentId) ?? [];
  messages.value = new Map(messages.value).set(agentId, [...current, msg]);
}

function clearMessages(agentId: string) {
  messages.value = new Map(messages.value).set(agentId, []);
}

async function sendPrompt(agentId: string, prompt: string, images?: ImageAttachment[]) {
  const { setStatus, setSession, clearSession } = useAgentStore();

  // Handle /clear and /new — clear frontend messages and end backend session
  const trimmed = prompt.trim().toLowerCase();
  if (trimmed === "/clear" || trimmed === "/new") {
    const sid = useAgentStore().getSession(agentId);
    if (sid) {
      try { await bridgeStopSession(agentId, sid); } catch { /* best-effort */ }
    }
    clearMessages(agentId);
    clearSession(agentId);
    setStatus(agentId, "idle");
    return;
  }

  // Optimistic: add user message immediately
  appendMessage(agentId, {
    role: "user",
    content: prompt,
    timestamp: Date.now(),
    images,
  });

  setStatus(agentId, "active");

  try {
    const result = await bridgeSendPrompt(agentId, prompt, images);
    if (result?.sessionId) {
      setSession(agentId, result.sessionId);
    }
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    appendMessage(agentId, {
      role: "system",
      content: `Error: ${error}`,
      timestamp: Date.now(),
    });
    setStatus(agentId, "error");
  }
}

let messageListenersInitialized = false;

async function initMessageListeners() {
  if (messageListenersInitialized) return;
  messageListenersInitialized = true;

  const { setStatus } = useAgentStore();

  await onAgentMessage((data) => {
    appendMessage(data.agentId, {
      role: data.message.role as "user" | "assistant" | "system",
      content: data.message.content,
      timestamp: data.message.timestamp,
      images: data.message.images,
      metadata: data.message.metadata,
    });
  });

  await onAgentStreamEnd((data) => {
    setStatus(data.agentId, "idle");
  });

  await onAgentError((data) => {
    appendMessage(data.agentId, {
      role: "system",
      content: `Error: ${data.error}`,
      timestamp: Date.now(),
    });
    setStatus(data.agentId, "error");
  });
}

export function useMessageStore() {
  return {
    messages,
    getMessages,
    appendMessage,
    clearMessages,
    sendPrompt,
    initMessageListeners,
  };
}
