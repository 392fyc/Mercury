/**
 * Per-agent message history store.
 */

import { ref } from "vue";
import type { AgentMessage } from "../lib/tauri-bridge";
import {
  sendPrompt as bridgeSendPrompt,
  onAgentMessage,
  onAgentStreamEnd,
  onAgentError,
} from "../lib/tauri-bridge";
import { useAgentStore } from "./agents";

export interface DisplayMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
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

async function sendPrompt(agentId: string, prompt: string) {
  const { setStatus, setSession } = useAgentStore();

  // Optimistic: add user message immediately
  appendMessage(agentId, {
    role: "user",
    content: prompt,
    timestamp: Date.now(),
  });

  setStatus(agentId, "active");

  try {
    const result = await bridgeSendPrompt(agentId, prompt);
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

async function initMessageListeners() {
  const { setStatus } = useAgentStore();

  await onAgentMessage((data) => {
    appendMessage(data.agentId, {
      role: data.message.role as "user" | "assistant" | "system",
      content: data.message.content,
      timestamp: data.message.timestamp,
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
    sendPrompt,
    initMessageListeners,
  };
}
