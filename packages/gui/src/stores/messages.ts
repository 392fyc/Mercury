/**
 * Per-panel message history store.
 *
 * Messages are keyed by panelKey (roleSlotKey = "{role}:{agentId}"),
 * enabling independent message history per role panel.
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

/** Reverse map: sessionId → panelKey, populated when we get sessionIds back */
const sessionToPanelKey = new Map<string, string>();

function getMessages(panelKey: string): DisplayMessage[] {
  return messages.value.get(panelKey) ?? [];
}

function appendMessage(panelKey: string, msg: DisplayMessage) {
  const current = messages.value.get(panelKey) ?? [];
  messages.value = new Map(messages.value).set(panelKey, [...current, msg]);
}

function clearMessages(panelKey: string) {
  messages.value = new Map(messages.value).set(panelKey, []);
}

/**
 * Send a prompt from a specific role panel.
 * @param panelKey - roleSlotKey "{role}:{agentId}"
 */
async function sendPrompt(panelKey: string, prompt: string, images?: ImageAttachment[]) {
  const { setStatus, setSession, clearSession } = useAgentStore();

  // Parse panelKey to get role and agentId
  const colonIdx = panelKey.indexOf(":");
  const role = panelKey.slice(0, colonIdx);
  const agentId = panelKey.slice(colonIdx + 1);

  // Handle /clear and /new — clear frontend messages and end backend session
  const trimmed = prompt.trim().toLowerCase();
  if (trimmed === "/clear" || trimmed === "/new") {
    const sid = useAgentStore().getSession(panelKey);
    if (sid) {
      try { await bridgeStopSession(agentId, sid); } catch { /* best-effort */ }
      sessionToPanelKey.delete(sid);
    }
    clearMessages(panelKey);
    clearSession(panelKey);
    setStatus(panelKey, "idle");
    return;
  }

  // Optimistic: add user message immediately
  appendMessage(panelKey, {
    role: "user",
    content: prompt,
    timestamp: Date.now(),
    images,
  });

  setStatus(panelKey, "active");

  try {
    const result = await bridgeSendPrompt(agentId, prompt, images, role);
    if (result?.sessionId) {
      setSession(panelKey, result.sessionId);
      sessionToPanelKey.set(result.sessionId, panelKey);
    }
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    appendMessage(panelKey, {
      role: "system",
      content: `Error: ${error}`,
      timestamp: Date.now(),
    });
    setStatus(panelKey, "error");
  }
}

/**
 * Resolve a sessionId to a panelKey. Falls back to first matching panelKey
 * for the agentId if session mapping is unknown.
 */
function resolvePanelKey(agentId: string, sessionId: string): string {
  const known = sessionToPanelKey.get(sessionId);
  if (known) return known;
  // Fallback: find any panel for this agentId (best guess)
  const store = useAgentStore();
  for (const [key, sid] of store.sessions.value) {
    if (sid === sessionId) return key;
  }
  // Last resort: use first role of this agent
  const agent = store.agents.value.find((a) => a.id === agentId);
  const firstRole = agent?.roles[0] ?? "dev";
  return `${firstRole}:${agentId}`;
}

let messageListenersInitialized = false;

async function initMessageListeners() {
  if (messageListenersInitialized) return;
  messageListenersInitialized = true;

  const { setStatus } = useAgentStore();

  await onAgentMessage((data) => {
    const panelKey = resolvePanelKey(data.agentId, data.sessionId);
    appendMessage(panelKey, {
      role: data.message.role as "user" | "assistant" | "system",
      content: data.message.content,
      timestamp: data.message.timestamp,
      images: data.message.images,
      metadata: data.message.metadata,
    });
  });

  await onAgentStreamEnd((data) => {
    const panelKey = resolvePanelKey(data.agentId, data.sessionId);
    setStatus(panelKey, "idle");
  });

  await onAgentError((data) => {
    const panelKey = resolvePanelKey(data.agentId, data.sessionId);
    appendMessage(panelKey, {
      role: "system",
      content: `Error: ${data.error}`,
      timestamp: Date.now(),
    });
    setStatus(panelKey, "error");
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
