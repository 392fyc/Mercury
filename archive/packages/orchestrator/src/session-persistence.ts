/**
 * Session persistence — saves/restores orchestrator session state to disk.
 * Uses atomic write (tmp + rename) to avoid corruption on crash.
 */

import { writeFileSync, readFileSync, mkdirSync, renameSync } from "node:fs";
import { join } from "node:path";
import type { ApprovalMode, ApprovalRequest, SessionInfo } from "@mercury/core";

export interface PersistedSessionState {
  /** Map of roleSlotKey → sessionId. Optional for backward compatibility with older state files. */
  roleSessions?: Record<string, string>;
  sessions: Record<string, SessionInfo>;
  agentCwds: Record<string, string>;
  approvalMode?: ApprovalMode;
  approvalRequests?: Record<string, ApprovalRequest>;
  savedAt: number;
}

export class SessionPersistence {
  private filePath: string;

  constructor(basePath: string) {
    const dir = join(basePath, ".mercury");
    mkdirSync(dir, { recursive: true });
    this.filePath = join(dir, "sessions.json");
  }

  save(state: PersistedSessionState): void {
    const tmp = this.filePath + ".tmp";
    writeFileSync(tmp, JSON.stringify(state, null, 2), "utf-8");
    renameSync(tmp, this.filePath);
  }

  load(): PersistedSessionState | null {
    try {
      const raw = readFileSync(this.filePath, "utf-8");
      return JSON.parse(raw) as PersistedSessionState;
    } catch {
      return null;
    }
  }
}
