/**
 * Minimal Codex app-server protocol bindings used by Mercury.
 *
 * These types are derived from `codex app-server generate-ts` and trimmed down
 * to the request/response/event surface the adapter actually uses.
 */

export type JsonValue =
  | number
  | string
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue | undefined };

export type RequestId = string | number;

export interface JsonRpcRequest<TMethod extends string = string, TParams = unknown> {
  jsonrpc?: "2.0";
  method: TMethod;
  id: RequestId;
  params?: TParams;
}

export interface JsonRpcNotification<TMethod extends string = string, TParams = unknown> {
  jsonrpc?: "2.0";
  method: TMethod;
  params?: TParams;
}

export interface JsonRpcSuccess<TResult = unknown> {
  jsonrpc?: "2.0";
  id: RequestId;
  result: TResult;
}

export interface JsonRpcFailure {
  jsonrpc?: "2.0";
  id: RequestId | null;
  error: {
    code: number;
    message: string;
    data?: unknown;
  };
}

export type JsonRpcMessage =
  | JsonRpcRequest
  | JsonRpcNotification
  | JsonRpcSuccess
  | JsonRpcFailure;

export type AskForApproval =
  | "untrusted"
  | "on-failure"
  | "on-request"
  | "never"
  | {
      reject: {
        sandbox_approval: boolean;
        rules: boolean;
        request_permissions: boolean;
        mcp_elicitations: boolean;
      };
    };

export type SandboxMode = "read-only" | "workspace-write" | "danger-full-access";

export type CommandExecutionApprovalDecision =
  | "accept"
  | "acceptForSession"
  | "decline"
  | "cancel"
  | { acceptWithExecpolicyAmendment: { execpolicy_amendment: unknown } }
  | { applyNetworkPolicyAmendment: { network_policy_amendment: unknown } };

export type FileChangeApprovalDecision =
  | "accept"
  | "acceptForSession"
  | "decline"
  | "cancel";

export type CommandExecutionStatus = "inProgress" | "completed" | "failed" | "declined";
export type PatchApplyStatus = "inProgress" | "completed" | "failed" | "declined";
export type TurnStatus = "completed" | "interrupted" | "failed" | "inProgress";
export type MessagePhase = "thinking" | "final" | "commentary" | "summary" | null;

export interface ClientInfo {
  name: string;
  title: string | null;
  version: string;
}

export interface InitializeCapabilities {
  experimentalApi: boolean;
  optOutNotificationMethods?: string[] | null;
}

export interface InitializeParams {
  clientInfo: ClientInfo;
  capabilities: InitializeCapabilities | null;
}

export interface InitializeResponse {
  userAgent: string;
}

export type UserInput =
  | {
      type: "text";
      text: string;
      text_elements: Array<unknown>;
    }
  | {
      type: "localImage";
      path: string;
    }
  | {
      type: "image";
      url: string;
    }
  | {
      type: "skill";
      name: string;
      path: string;
    }
  | {
      type: "mention";
      name: string;
      path: string;
    };

export interface ThreadStartParams {
  model?: string | null;
  modelProvider?: string | null;
  cwd?: string | null;
  approvalPolicy?: AskForApproval | null;
  sandbox?: SandboxMode | null;
  baseInstructions?: string | null;
  developerInstructions?: string | null;
  ephemeral?: boolean | null;
  experimentalRawEvents: boolean;
  persistExtendedHistory: boolean;
}

export interface ThreadResumeParams {
  threadId: string;
  history?: Array<unknown> | null;
  path?: string | null;
  model?: string | null;
  modelProvider?: string | null;
  cwd?: string | null;
  approvalPolicy?: AskForApproval | null;
  sandbox?: SandboxMode | null;
  baseInstructions?: string | null;
  developerInstructions?: string | null;
  persistExtendedHistory: boolean;
}

export interface ThreadReadParams {
  threadId: string;
  includeTurns: boolean;
}

export interface ThreadListParams {
  cursor?: string | null;
  limit?: number | null;
  sourceKinds?: string[] | null;
  cwd?: string | null;
  archived?: boolean | null;
  searchTerm?: string | null;
}

export interface ThreadSetNameParams {
  threadId: string;
  name: string;
}

export interface TurnStartParams {
  threadId: string;
  input: UserInput[];
  cwd?: string | null;
  approvalPolicy?: AskForApproval | null;
  model?: string | null;
}

export interface GitInfo {
  sha: string | null;
  branch: string | null;
  originUrl: string | null;
}

export type ThreadStatus =
  | { type: "notLoaded" }
  | { type: "idle" }
  | { type: "systemError" }
  | { type: "active"; activeFlags: Array<{ type: string } | string> };

export type SessionSource =
  | "cli"
  | "vscode"
  | "exec"
  | "appServer"
  | "unknown"
  | { subAgent: unknown };

export interface FileUpdateChange {
  path: string;
  kind:
    | { type: "add" }
    | { type: "delete" }
    | { type: "update"; move_path: string | null };
  diff: string;
}

export type CommandAction =
  | { type: "read"; command: string; name: string; path: string }
  | { type: "listFiles"; command: string; path: string | null }
  | { type: "search"; command: string; query: string | null; path: string | null }
  | { type: "unknown"; command: string };

export type ThreadItem =
  | { type: "userMessage"; id: string; content: UserInput[] }
  | { type: "agentMessage"; id: string; text: string; phase: MessagePhase }
  | { type: "plan"; id: string; text: string }
  | { type: "reasoning"; id: string; summary: string[]; content: string[] }
  | {
      type: "commandExecution";
      id: string;
      command: string;
      cwd: string;
      processId: string | null;
      status: CommandExecutionStatus;
      commandActions: CommandAction[];
      aggregatedOutput: string | null;
      exitCode: number | null;
      durationMs: number | null;
    }
  | {
      type: "fileChange";
      id: string;
      changes: FileUpdateChange[];
      status: PatchApplyStatus;
    }
  | {
      type: "mcpToolCall";
      id: string;
      server: string;
      tool: string;
      status: string;
      arguments: JsonValue;
      result: unknown;
      error: unknown;
      durationMs: number | null;
    }
  | {
      type: "dynamicToolCall";
      id: string;
      tool: string;
      arguments: JsonValue;
      status: string;
      contentItems: unknown[] | null;
      success: boolean | null;
      durationMs: number | null;
    }
  | {
      type: "webSearch";
      id: string;
      query: string;
      action: unknown;
    }
  | { type: "imageView"; id: string; path: string }
  | { type: "imageGeneration"; id: string; status: string; revisedPrompt: string | null; result: string }
  | { type: "enteredReviewMode"; id: string; review: string }
  | { type: "exitedReviewMode"; id: string; review: string }
  | { type: "contextCompaction"; id: string };

export interface TurnError {
  message: string;
  codexErrorInfo: unknown | null;
  additionalDetails: string | null;
}

export interface Turn {
  id: string;
  items: ThreadItem[];
  status: TurnStatus;
  error: TurnError | null;
}

export interface Thread {
  id: string;
  preview: string;
  ephemeral: boolean;
  modelProvider: string;
  createdAt: number;
  updatedAt: number;
  status: ThreadStatus;
  path: string | null;
  cwd: string;
  cliVersion: string;
  source: SessionSource;
  agentNickname: string | null;
  agentRole: string | null;
  gitInfo: GitInfo | null;
  name: string | null;
  turns: Turn[];
}

export interface ThreadStartResponse {
  thread: Thread;
  model: string;
  modelProvider: string;
  cwd: string;
  approvalPolicy: AskForApproval;
  sandbox: unknown;
  reasoningEffort: string | null;
  serviceTier: string | null;
}

export type ThreadResumeResponse = ThreadStartResponse;

export interface ThreadReadResponse {
  thread: Thread;
}

export interface ThreadListResponse {
  data: Thread[];
  nextCursor: string | null;
}

export interface TurnStartResponse {
  turn: Turn;
}

export interface CommandExecutionRequestApprovalParams {
  threadId: string;
  turnId: string;
  itemId: string;
  approvalId?: string | null;
  reason?: string | null;
  networkApprovalContext?: unknown | null;
  command?: string | null;
  cwd?: string | null;
  commandActions?: CommandAction[] | null;
  additionalPermissions?: unknown | null;
  skillMetadata?: unknown | null;
  proposedExecpolicyAmendment?: unknown | null;
  proposedNetworkPolicyAmendments?: unknown[] | null;
  availableDecisions?: CommandExecutionApprovalDecision[] | null;
}

export interface CommandExecutionRequestApprovalResponse {
  decision: CommandExecutionApprovalDecision;
}

export interface FileChangeRequestApprovalParams {
  threadId: string;
  turnId: string;
  itemId: string;
  reason?: string | null;
  grantRoot?: string | null;
}

export interface FileChangeRequestApprovalResponse {
  decision: FileChangeApprovalDecision;
}

export interface ErrorNotification {
  error: TurnError;
  willRetry: boolean;
  threadId: string;
  turnId: string;
}

export interface ThreadStartedNotification {
  thread: Thread;
}

export interface ThreadStatusChangedNotification {
  threadId: string;
  status: ThreadStatus;
}

export interface ThreadClosedNotification {
  threadId: string;
}

export interface ThreadTokenUsage {
  total: {
    totalTokens: number;
    inputTokens: number;
    cachedInputTokens: number;
    outputTokens: number;
    reasoningOutputTokens: number;
  };
  last: {
    totalTokens: number;
    inputTokens: number;
    cachedInputTokens: number;
    outputTokens: number;
    reasoningOutputTokens: number;
  };
  modelContextWindow: number | null;
}

export interface ThreadTokenUsageUpdatedNotification {
  threadId: string;
  turnId: string;
  tokenUsage: ThreadTokenUsage;
}

export interface TurnStartedNotification {
  threadId: string;
  turn: Turn;
}

export interface TurnCompletedNotification {
  threadId: string;
  turn: Turn;
}

export interface TurnPlanUpdatedNotification {
  threadId: string;
  turnId: string;
  explanation: string | null;
  plan: Array<{
    step: string;
    status: "pending" | "inProgress" | "completed";
  }>;
}

export interface TurnDiffUpdatedNotification {
  threadId: string;
  turnId: string;
  diff: string;
}

export interface ItemCompletedNotification {
  item: ThreadItem;
  threadId: string;
  turnId: string;
}

export interface ItemStartedNotification {
  item: ThreadItem;
  threadId: string;
  turnId: string;
}

export interface ContextCompactedNotification {
  threadId: string;
  turnId: string;
}

export interface ServerRequestResolvedNotification {
  threadId: string;
  requestId: RequestId;
}

export interface CodexAppServerRequestMap {
  initialize: { params: InitializeParams; result: InitializeResponse };
  "thread/start": { params: ThreadStartParams; result: ThreadStartResponse };
  "thread/resume": { params: ThreadResumeParams; result: ThreadResumeResponse };
  "thread/read": { params: ThreadReadParams; result: ThreadReadResponse };
  "thread/list": { params: ThreadListParams; result: ThreadListResponse };
  "thread/name/set": { params: ThreadSetNameParams; result: Record<string, never> };
  "turn/start": { params: TurnStartParams; result: TurnStartResponse };
  "thread/unsubscribe": { params: { threadId: string }; result: { status: string } };
}

export interface CodexAppServerServerRequestMap {
  "item/commandExecution/requestApproval": {
    params: CommandExecutionRequestApprovalParams;
    result: CommandExecutionRequestApprovalResponse;
  };
  "item/fileChange/requestApproval": {
    params: FileChangeRequestApprovalParams;
    result: FileChangeRequestApprovalResponse;
  };
}

export interface CodexAppServerNotificationMap {
  error: ErrorNotification;
  "thread/started": ThreadStartedNotification;
  "thread/status/changed": ThreadStatusChangedNotification;
  "thread/closed": ThreadClosedNotification;
  "thread/tokenUsage/updated": ThreadTokenUsageUpdatedNotification;
  "turn/started": TurnStartedNotification;
  "turn/completed": TurnCompletedNotification;
  "turn/plan/updated": TurnPlanUpdatedNotification;
  "turn/diff/updated": TurnDiffUpdatedNotification;
  "item/started": ItemStartedNotification;
  "item/completed": ItemCompletedNotification;
  "thread/compacted": ContextCompactedNotification;
  "serverRequest/resolved": ServerRequestResolvedNotification;
}

export type CodexAppServerRequestMethod = keyof CodexAppServerRequestMap;
export type CodexAppServerServerRequestMethod = keyof CodexAppServerServerRequestMap;
export type CodexAppServerNotificationMethod = keyof CodexAppServerNotificationMap;
