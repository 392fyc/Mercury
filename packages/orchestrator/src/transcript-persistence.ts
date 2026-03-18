import { appendFileSync, mkdirSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import type { AgentMessage, ImageAttachment } from "@mercury/core";

export interface TranscriptMessage {
  role: AgentMessage["role"];
  content: string;
  timestamp: number;
  images?: ImageAttachment[];
  metadata?: Record<string, unknown>;
}

export class TranscriptPersistence {
  private dirPath: string;

  constructor(basePath: string) {
    this.dirPath = join(basePath, ".mercury", "transcripts");
    mkdirSync(this.dirPath, { recursive: true });
  }

  append(sessionId: string, message: TranscriptMessage): void {
    const filePath = this.getFilePath(sessionId);
    appendFileSync(filePath, `${JSON.stringify(message)}\n`, "utf-8");
  }

  read(
    sessionId: string,
    offset = 0,
    limit?: number,
  ): { messages: TranscriptMessage[]; total: number } {
    try {
      const raw = readFileSync(this.getFilePath(sessionId), "utf-8");
      const messages = raw
        .split(/\r?\n/)
        .filter(Boolean)
        .map((line) => JSON.parse(line) as TranscriptMessage);
      const total = messages.length;
      const safeOffset = Math.max(0, offset);
      const paged = typeof limit === "number"
        ? messages.slice(safeOffset, safeOffset + Math.max(0, limit))
        : messages.slice(safeOffset);
      return { messages: paged, total };
    } catch {
      return { messages: [], total: 0 };
    }
  }

  /** List all session IDs that have transcript files on disk. */
  listSessionIds(): string[] {
    try {
      return readdirSync(this.dirPath)
        .filter((f) => f.endsWith(".jsonl"))
        .map((f) => f.slice(0, -".jsonl".length));
    } catch {
      return [];
    }
  }

  private getFilePath(sessionId: string): string {
    return join(this.dirPath, `${sessionId}.jsonl`);
  }
}
