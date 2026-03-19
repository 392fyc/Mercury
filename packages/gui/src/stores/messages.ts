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
  listSessions as bridgeListSessions,
  resumeSession as bridgeResumeSession,
  getSessionMessages as bridgeGetSessionMessages,
  onAgentMessage,
  onAgentWorking,
  onAgentStreamEnd,
  onAgentError,
} from "../lib/tauri-bridge";
import type { SessionListItem, TranscriptMessage } from "../lib/tauri-bridge";
import { useAgentStore } from "./agents";

export interface DisplayMessage {
  role: "user" | "assistant" | "system";
  content: string;
  timestamp: number;
  images?: ImageAttachment[];
  metadata?: Record<string, unknown>;
}

const STORAGE_KEY = "mercury:messages";
const MAX_MESSAGES_PER_PANEL = 200;

const messages = ref<Map<string, DisplayMessage[]>>(new Map());

/** Reverse map: sessionId → panelKey, populated when we get sessionIds back */
const sessionToPanelKey = new Map<string, string>();

/** Pending session picker state (set by /resume, consumed by SessionPicker component). */
const pendingSessionPick = ref<{
  panelKey: string;
  agentId: string;
  role: string;
  sessions: SessionListItem[];
} | null>(null);

const pendingHistoryView = ref<{
  panelKey: string;
  agentId: string;
  sessions: SessionListItem[];
  selectedSessionId: string | null;
  messages: TranscriptMessage[];
} | null>(null);

let saveDebounceTimer: ReturnType<typeof setTimeout> | null = null;

function saveToStorage(): void {
  if (saveDebounceTimer) clearTimeout(saveDebounceTimer);
  saveDebounceTimer = setTimeout(() => {
    try {
      const obj: Record<string, DisplayMessage[]> = {};
      for (const [key, msgs] of messages.value) {
        // Keep only the last N messages per panel (strip images to save space)
        obj[key] = msgs.slice(-MAX_MESSAGES_PER_PANEL).map((m) => ({
          role: m.role,
          content: m.content,
          timestamp: m.timestamp,
          metadata: m.metadata,
          // Omit images from persistence — too large for localStorage
        }));
      }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(obj));
    } catch {
      // localStorage full or unavailable — ignore
    }
  }, 500);
}

function loadFromStorage(): void {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const obj = JSON.parse(raw) as Record<string, DisplayMessage[]>;
    const map = new Map<string, DisplayMessage[]>();
    for (const [key, msgs] of Object.entries(obj)) {
      if (Array.isArray(msgs)) map.set(key, msgs);
    }
    messages.value = map;
  } catch {
    // Corrupted data — start fresh
  }
}

/** Retrieve messages for a specific panel. */
function getMessages(panelKey: string): DisplayMessage[] {
  return messages.value.get(panelKey) ?? [];
}

/** Append a message to a panel's history and persist to localStorage. */
function appendMessage(panelKey: string, msg: DisplayMessage) {
  const current = messages.value.get(panelKey) ?? [];
  messages.value = new Map(messages.value).set(panelKey, [...current, msg]);
  saveToStorage();
}

/** Clear all messages for a panel and persist to localStorage. */
function clearMessages(panelKey: string) {
  messages.value = new Map(messages.value).set(panelKey, []);
  saveToStorage();
}

/**
 * Send a prompt from a specific role panel.
 * @param panelKey - roleSlotKey "{role}:{agentId}"
 */
async function sendPrompt(panelKey: string, prompt: string, images?: ImageAttachment[]) {
  const { setStatus, setSessionInfo } = useAgentStore();

  // Parse panelKey to get role and agentId
  const colonIdx = panelKey.indexOf(":");
  const role = panelKey.slice(0, colonIdx);
  const agentId = panelKey.slice(colonIdx + 1);

  // Handle built-in commands — intercepted before reaching the backend
  const trimmed = prompt.trim().toLowerCase();

  // /history — open history panel
  if (trimmed === "/history") {
    await openHistory(panelKey);
    return;
  }

  // /clear and /new — delegate to newSession
  if (trimmed === "/clear" || trimmed === "/new") {
    await newSession(panelKey);
    return;
  }

  // Handle /resume — list or directly resume a session
  if (trimmed === "/resume" || trimmed.startsWith("/resume ")) {
    const resumeArg = prompt.trim().slice("/resume".length).trim();

    if (resumeArg) {
      // Direct resume: /resume <sessionId>
      try {
        const result = await bridgeResumeSession(agentId, resumeArg, role);
        setSessionInfo(panelKey, {
          sessionId: result.sessionId,
          sessionName: result.sessionName,
          status: (result.status as "active" | "paused" | "completed" | "overflow" | undefined) ?? "active",
          lastActiveAt: Date.now(),
        });
        sessionToPanelKey.set(result.sessionId, panelKey);
        // Backfill history from the resumed session
        await loadSessionHistory(panelKey, result.sessionId);
        appendMessage(panelKey, {
          role: "system",
          content: `Resumed session ${result.sessionId.slice(0, 8)}`,
          timestamp: Date.now(),
        });
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        appendMessage(panelKey, {
          role: "system",
          content: `Resume failed: ${error}`,
          timestamp: Date.now(),
        });
      }
    } else {
      // Bare /resume — list sessions and show picker
      try {
        const sessions = await bridgeListSessions(agentId, role, true);
        if (sessions.length === 0) {
          appendMessage(panelKey, {
            role: "system",
            content: "No saved sessions",
            timestamp: Date.now(),
          });
        } else {
          pendingSessionPick.value = { panelKey, agentId, role, sessions };
        }
      } catch (e) {
        const error = e instanceof Error ? e.message : String(e);
        appendMessage(panelKey, {
          role: "system",
          content: `Failed to list sessions: ${error}`,
          timestamp: Date.now(),
        });
      }
    }
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
      setSessionInfo(panelKey, {
        sessionId: result.sessionId,
        sessionName: result.sessionName,
        status: (result.status as "active" | "paused" | "completed" | "overflow" | undefined) ?? "active",
        lastActiveAt: Date.now(),
      });
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
 * Resolve a sessionId to a panelKey. Unknown mappings are not guessed to avoid
 * cross-role message routing corruption.
 */
function resolvePanelKey(_agentId: string, sessionId: string): string | null {
  const known = sessionToPanelKey.get(sessionId);
  if (known) return known;
  const store = useAgentStore();
  for (const [key, sid] of store.sessions.value) {
    if (sid === sessionId) return key;
  }
  return null;
}

let messageListenersInitialized = false;

/** Register Tauri event listeners for agent messages, status, and errors. */
async function initMessageListeners() {
  if (messageListenersInitialized) return;
  messageListenersInitialized = true;

  loadFromStorage();

  const { setStatus } = useAgentStore();

  await onAgentMessage((data) => {
    const panelKey = resolvePanelKey(data.agentId, data.sessionId);
    if (!panelKey) return;
    appendMessage(panelKey, {
      role: data.message.role as "user" | "assistant" | "system",
      content: data.message.content,
      timestamp: data.message.timestamp,
      images: data.message.images,
      metadata: data.message.metadata,
    });
  });

  await onAgentWorking((data) => {
    const panelKey = resolvePanelKey(data.agentId, data.sessionId);
    if (!panelKey) return;
    setStatus(panelKey, "active");
  });

  await onAgentStreamEnd((data) => {
    const panelKey = resolvePanelKey(data.agentId, data.sessionId);
    if (!panelKey) return;
    setStatus(panelKey, "idle");
  });

  await onAgentError((data) => {
    const panelKey = resolvePanelKey(data.agentId, data.sessionId);
    if (!panelKey) return;
    appendMessage(panelKey, {
      role: "system",
      content: `Error: ${data.error}`,
      timestamp: Date.now(),
    });
    setStatus(panelKey, "error");
  });
}

/** Resume a session selected from the SessionPicker modal. */
async function pickSession(sessionId: string): Promise<void> {
  const pick = pendingSessionPick.value;
  if (!pick) return;

  const { panelKey, agentId } = pick;
  const { setSessionInfo } = useAgentStore();
  const role = pick.role;

  pendingSessionPick.value = null;

  try {
    const result = await bridgeResumeSession(agentId, sessionId, role);
    setSessionInfo(panelKey, {
      sessionId: result.sessionId,
      sessionName: result.sessionName,
      status: (result.status as "active" | "paused" | "completed" | "overflow" | undefined) ?? "active",
      lastActiveAt: Date.now(),
    });
    sessionToPanelKey.set(result.sessionId, panelKey);
    // Backfill history from the resumed session
    await loadSessionHistory(panelKey, result.sessionId);
    appendMessage(panelKey, {
      role: "system",
      content: `Resumed session ${result.sessionId.slice(0, 8)}`,
      timestamp: Date.now(),
    });
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    appendMessage(panelKey, {
      role: "system",
      content: `Resume failed: ${error}`,
      timestamp: Date.now(),
    });
  }
}

/**
 * Load historical messages from a session into the panel's message store.
 * Clears existing messages and backfills from the backend transcript.
 */
async function loadSessionHistory(panelKey: string, sessionId: string): Promise<void> {
  try {
    const result = await bridgeGetSessionMessages(sessionId);
    if (!result.messages || result.messages.length === 0) return;

    // Clear current messages and backfill with historical ones
    clearMessages(panelKey);
    for (const msg of result.messages) {
      appendMessage(panelKey, {
        role: msg.role,
        content: msg.content,
        timestamp: msg.timestamp,
        images: msg.images,
        metadata: msg.metadata,
      });
    }
  } catch (e) {
    console.debug("loadSessionHistory: failed to load history", e);
  }
}

/** Close the session picker modal without selecting a session. */
function dismissSessionPick(): void {
  pendingSessionPick.value = null;
}

/** Open the session picker modal listing resumable sessions for this panel. */
async function openSessionPicker(panelKey: string): Promise<void> {
  const colonIdx = panelKey.indexOf(":");
  const role = panelKey.slice(0, colonIdx);
  const agentId = panelKey.slice(colonIdx + 1);

  try {
    const sessions = await bridgeListSessions(agentId, role, true);
    if (sessions.length === 0) {
      appendMessage(panelKey, {
        role: "system",
        content: "No saved sessions",
        timestamp: Date.now(),
      });
      return;
    }
    pendingSessionPick.value = { panelKey, agentId, role, sessions };
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    appendMessage(panelKey, {
      role: "system",
      content: `Failed to list sessions: ${error}`,
      timestamp: Date.now(),
    });
  }
}

/** Open the history panel showing all sessions for this panel's agent. */
async function openHistory(panelKey: string): Promise<void> {
  const colonIdx = panelKey.indexOf(":");
  const agentId = panelKey.slice(colonIdx + 1);

  try {
    const sessions = await bridgeListSessions(agentId, undefined, true);
    pendingHistoryView.value = {
      panelKey,
      agentId,
      sessions,
      selectedSessionId: null,
      messages: [],
    };
    if (sessions.length > 0) {
      await selectHistorySession(sessions[0].sessionId);
    }
  } catch (e) {
    const error = e instanceof Error ? e.message : String(e);
    appendMessage(panelKey, {
      role: "system",
      content: `Failed to open history: ${error}`,
      timestamp: Date.now(),
    });
  }
}

/** Load transcript messages for a session into the history viewer. */
async function selectHistorySession(sessionId: string): Promise<void> {
  const current = pendingHistoryView.value;
  if (!current) return;

  // Mark as selected immediately for UI responsiveness
  pendingHistoryView.value = { ...current, selectedSessionId: sessionId, messages: [] };

  try {
    const result = await bridgeGetSessionMessages(sessionId);
    // Guard against stale response: only apply if this session is still selected
    if (pendingHistoryView.value?.selectedSessionId !== sessionId) return;
    pendingHistoryView.value = {
      ...pendingHistoryView.value,
      messages: result.messages,
    };
  } catch (e) {
    console.debug("selectHistorySession: failed to load transcript", e);
    if (pendingHistoryView.value?.selectedSessionId !== sessionId) return;
    pendingHistoryView.value = {
      ...pendingHistoryView.value,
      messages: [],
    };
  }
}

/** Close the history viewer modal. */
function dismissHistoryView(): void {
  pendingHistoryView.value = null;
}

/**
 * Archive the current session — stops the backend session via bridgeStopSession,
 * clears the panel, and shows a "Session archived" confirmation message.
 *
 * NOTE: Both archiveSession and newSession call the same bridgeStopSession backend
 * API. There is no distinct backend "archive" endpoint yet. The difference is
 * purely frontend UX: Archive shows a confirmation message, New Session does not.
 * A dedicated archive_session backend API can be added in a future iteration to
 * persist archive metadata (e.g., completion status, tags).
 */
async function archiveSession(panelKey: string): Promise<void> {
  const { setStatus, clearSession, getSession } = useAgentStore();
  const colonIdx = panelKey.indexOf(":");
  const agentId = panelKey.slice(colonIdx + 1);

  const sid = getSession(panelKey);
  if (sid) {
    try { await bridgeStopSession(agentId, sid); } catch (e) { console.debug("archiveSession: stop failed (best-effort)", e); }
    sessionToPanelKey.delete(sid);
  }

  // Clear first, then show confirmation so user sees it in the fresh panel
  clearMessages(panelKey);
  clearSession(panelKey);
  setStatus(panelKey, "idle");

  appendMessage(panelKey, {
    role: "system",
    content: "Session archived",
    timestamp: Date.now(),
  });
}

/**
 * Start a new session — stops the current backend session and clears the panel.
 * Uses the same bridgeStopSession call as archiveSession (see note above).
 */
async function newSession(panelKey: string): Promise<void> {
  const { setStatus, clearSession, getSession } = useAgentStore();
  const colonIdx = panelKey.indexOf(":");
  const agentId = panelKey.slice(colonIdx + 1);

  const sid = getSession(panelKey);
  if (sid) {
    try { await bridgeStopSession(agentId, sid); } catch (e) { console.debug("newSession: stop failed (best-effort)", e); }
    sessionToPanelKey.delete(sid);
  }

  clearMessages(panelKey);
  clearSession(panelKey);
  setStatus(panelKey, "idle");
}

/**
 * Get user message history for a panel (for ↑↓ navigation).
 * Filters on each call — acceptable for MAX_MESSAGES_PER_PANEL (200).
 */
function getUserMessageHistory(panelKey: string): string[] {
  const msgs = messages.value.get(panelKey) ?? [];
  return msgs
    .filter((m) => m.role === "user" && m.content.trim() !== "")
    .map((m) => m.content);
}

export function useMessageStore() {
  return {
    messages,
    getMessages,
    appendMessage,
    clearMessages,
    sendPrompt,
    initMessageListeners,
    pendingSessionPick,
    pendingHistoryView,
    pickSession,
    dismissSessionPick,
    openSessionPicker,
    openHistory,
    selectHistorySession,
    dismissHistoryView,
    archiveSession,
    newSession,
    getUserMessageHistory,
  };
}
