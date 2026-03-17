import { computed, ref } from "vue";
import type { ApprovalMode, ApprovalRequest, MercuryEvent } from "../lib/tauri-bridge";
import {
  approveRequest as bridgeApproveRequest,
  denyRequest as bridgeDenyRequest,
  getApprovalMode as bridgeGetApprovalMode,
  listApprovalRequests as bridgeListApprovalRequests,
  onMercuryEvent,
  setApprovalMode as bridgeSetApprovalMode,
} from "../lib/tauri-bridge";

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

let approvalsInitialized = false;

async function initApprovalStore(): Promise<void> {
  if (approvalsInitialized) return;
  approvalsInitialized = true;

  const [modeResult, requests] = await Promise.all([
    bridgeGetApprovalMode(),
    bridgeListApprovalRequests(),
  ]);
  approvalMode.value = modeResult.mode;
  const next = new Map<string, ApprovalRequest>();
  for (const request of requests) next.set(request.id, request);
  approvalRequests.value = next;

  await onMercuryEvent((event: MercuryEvent) => {
    if (event.type !== "agent.approval.requested" && event.type !== "agent.approval.resolved") {
      return;
    }

    const payload = event.payload as unknown as ApprovalRequest;
    if (payload?.id) {
      upsertRequest(payload);
    }
  });
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
  };
}
