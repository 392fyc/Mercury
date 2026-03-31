import { computed, ref } from "vue";
import type { UnlistenFn } from "@tauri-apps/api/event";
import type { ApprovalMode, ApprovalRequest, MercuryEvent } from "../lib/tauri-bridge";
import {
  approveRequest as bridgeApproveRequest,
  denyRequest as bridgeDenyRequest,
  getApprovalMode as bridgeGetApprovalMode,
  listApprovalRequests as bridgeListApprovalRequests,
  onMercuryEvent,
  onAgentError,
  setApprovalMode as bridgeSetApprovalMode,
} from "../lib/tauri-bridge";
import { useAgentStore } from "./agents";

const { waitForSidecarReady } = useAgentStore();

const approvalMode = ref<ApprovalMode>("main_agent_review");
const approvalRequests = ref<Map<string, ApprovalRequest>>(new Map());
const queueOpen = ref(false);

const sortedRequests = computed(() =>
  [...approvalRequests.value.values()].sort((a, b) => b.createdAt - a.createdAt),
);

const pendingRequests = computed(() =>
  sortedRequests.value.filter((request) => request.status === "pending"),
);

const pendingCount = computed(() => pendingRequests.value.length);

function applyApprovalSnapshot(mode: ApprovalMode, requests: ApprovalRequest[]): void {
  approvalMode.value = mode;
  const next = new Map<string, ApprovalRequest>();
  for (const request of requests) next.set(request.id, request);
  approvalRequests.value = next;
}

function upsertRequest(request: ApprovalRequest): void {
  approvalRequests.value = new Map(approvalRequests.value).set(request.id, request);
}

function getRequest(requestId: string): ApprovalRequest | undefined {
  return approvalRequests.value.get(requestId);
}

function openQueue(): void {
  queueOpen.value = true;
}

function closeQueue(): void {
  queueOpen.value = false;
}

async function refreshApprovals(): Promise<void> {
  const requests = await bridgeListApprovalRequests();
  const next = new Map<string, ApprovalRequest>();
  for (const request of requests) next.set(request.id, request);
  approvalRequests.value = next;
}

async function setMode(mode: ApprovalMode): Promise<void> {
  const result = await bridgeSetApprovalMode(mode);
  approvalMode.value = result.mode;
}

async function approve(requestId: string, reason?: string): Promise<void> {
  await bridgeApproveRequest(requestId, reason);
  const current = approvalRequests.value.get(requestId);
  if (current) {
    upsertRequest({
      ...current,
      status: "approved",
      resolvedAt: Date.now(),
      decisionBy: "main_agent",
      decisionReason: reason,
    });
  }
}

async function deny(requestId: string, reason?: string): Promise<void> {
  await bridgeDenyRequest(requestId, reason);
  const current = approvalRequests.value.get(requestId);
  if (current) {
    upsertRequest({
      ...current,
      status: "denied",
      resolvedAt: Date.now(),
      decisionBy: "main_agent",
      decisionReason: reason,
    });
  }
}

/**
 * Immediately cancel all pending approvals for a given session.
 * Called when a transport crash makes approval buttons unresponsive.
 */
function cancelPendingApprovalsForSession(sessionId: string, reason: string): void {
  const now = Date.now();
  let changed = false;
  const next = new Map(approvalRequests.value);
  for (const [, request] of approvalRequests.value) {
    if (request.sessionId === sessionId && request.status === "pending") {
      next.set(request.id, {
        ...request,
        status: "cancelled",
        resolvedAt: now,
        decisionBy: "system",
        decisionReason: reason,
      });
      changed = true;
    }
  }
  if (changed) {
    approvalRequests.value = next;
    // Refresh from backend to ensure consistency
    void refreshApprovals();
  }
}

let approvalsInitialized = false;
let approvalsInitPromise: Promise<void> | null = null;
let approvalsInitGeneration = 0;
let approvalsDisposed = false;
const approvalUnlisteners: UnlistenFn[] = [];
const approvalPendingListenerBatches = new Set<UnlistenFn[]>();

function cleanupApprovalListeners(listeners: UnlistenFn[]): void {
  for (const unlisten of listeners) unlisten();
  listeners.length = 0;
}

function isActiveApprovalInit(generation: number): boolean {
  return !approvalsDisposed && approvalsInitGeneration === generation;
}

async function initApprovalStore(): Promise<void> {
  if (approvalsInitialized) return;
  if (approvalsInitPromise) return approvalsInitPromise;

  approvalsDisposed = false;
  const generation = ++approvalsInitGeneration;
  let initPromise!: Promise<void>;
  initPromise = (async () => {
    const pending: UnlistenFn[] = [];
    approvalPendingListenerBatches.add(pending);
    try {
      await waitForSidecarReady();
      if (!isActiveApprovalInit(generation)) return;

      pending.push(await onMercuryEvent((event: MercuryEvent) => {
        if (event.type !== "agent.approval.requested" && event.type !== "agent.approval.resolved") {
          return;
        }

        const payload = event.payload as unknown as ApprovalRequest;
        if (payload?.id) {
          upsertRequest(payload);
        }
      }));
      if (!isActiveApprovalInit(generation)) return;

      // When a transport crash occurs, immediately cancel pending approvals for
      // that session so the GUI doesn't show stale, unresponsive approval buttons.
      pending.push(await onAgentError((data) => {
        if (data.isTransportCrash) {
          cancelPendingApprovalsForSession(
            data.sessionId,
            "Transport disconnected — approval cancelled",
          );
        }
      }));
      if (!isActiveApprovalInit(generation)) return;

      const [modeResult, requests] = await Promise.all([
        bridgeGetApprovalMode(),
        bridgeListApprovalRequests(),
      ]);
      if (!isActiveApprovalInit(generation)) return;
      applyApprovalSnapshot(modeResult.mode, requests);

      // Close the snapshot/subscribe window by reconciling once after listeners
      // are active so events that raced with the initial fetch are not lost.
      const [reconciledMode, reconciledRequests] = await Promise.all([
        bridgeGetApprovalMode(),
        bridgeListApprovalRequests(),
      ]);
      if (!isActiveApprovalInit(generation)) return;
      applyApprovalSnapshot(reconciledMode.mode, reconciledRequests);

      approvalUnlisteners.push(...pending);
      pending.length = 0;
      approvalsInitialized = true;
    } catch (e) {
      cleanupApprovalListeners(pending);
      approvalsInitialized = false;
      throw e;
    } finally {
      approvalPendingListenerBatches.delete(pending);
      if (!isActiveApprovalInit(generation)) {
        cleanupApprovalListeners(pending);
      }
      if (approvalsInitPromise === initPromise) {
        approvalsInitPromise = null;
      }
    }
  })();

  approvalsInitPromise = initPromise;
  return approvalsInitPromise;
}

function disposeApprovalStoreListeners(): void {
  approvalsDisposed = true;
  approvalsInitGeneration += 1;
  cleanupApprovalListeners(approvalUnlisteners);
  for (const batch of approvalPendingListenerBatches) cleanupApprovalListeners(batch);
  approvalPendingListenerBatches.clear();
  approvalsInitialized = false;
  approvalsInitPromise = null;
}

export function useApprovalStore() {
  return {
    approvalMode,
    approvalRequests: sortedRequests,
    pendingRequests,
    pendingCount,
    queueOpen,
    getRequest,
    openQueue,
    closeQueue,
    refreshApprovals,
    setMode,
    approve,
    deny,
    initApprovalStore,
    disposeApprovalStoreListeners,
  };
}
