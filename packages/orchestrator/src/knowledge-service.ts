/**
 * Knowledge Service — optional Obsidian CLI wrapper.
 *
 * This is a project-level knowledge backend, NOT an agent adapter.
 * When enabled, provides read/write/search/list over an Obsidian vault via CLI.
 * When disabled, Mercury operates normally — agents can still use their own
 * MCP servers, mem0, or other knowledge tools independently.
 */

import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { KBSearchResult, KBFileInfo, ObsidianConfig } from "@mercury/core";

const execFileAsync = promisify(execFile);

export class KnowledgeService {
  private vaultName: string;
  private vaultPath: string | undefined;
  private enabled: boolean;
  private obsidianBin: string;

  constructor(config: ObsidianConfig, obsidianBin = "obsidian") {
    this.vaultName = config.vaultName;
    this.vaultPath = config.vaultPath;
    this.enabled = config.enabled;
    this.obsidianBin = obsidianBin;
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  private async exec(args: string[]): Promise<string> {
    if (!this.enabled) {
      throw new Error("Knowledge service is disabled. Enable obsidian in mercury.config.json.");
    }

    const fullArgs = [`vault="${this.vaultName}"`, ...args];

    try {
      const { stdout } = await execFileAsync(this.obsidianBin, fullArgs, {
        timeout: 15_000,
      });
      return stdout;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("ENOENT")) {
        throw new Error(
          `Obsidian CLI not found. Ensure Obsidian desktop is running and CLI is enabled in Settings → General.`,
        );
      }
      throw new Error(`Obsidian CLI error: ${msg}`);
    }
  }

  async read(file: string): Promise<string> {
    return this.exec(["read", `file="${file}"`]);
  }

  async write(name: string, content: string): Promise<void> {
    await this.exec(["create", `name="${name}"`, `content="${content}"`]);
  }

  async append(file: string, content: string): Promise<void> {
    await this.exec(["append", `file="${file}"`, `content="${content}"`]);
  }

  async search(query: string): Promise<KBSearchResult[]> {
    const raw = await this.exec(["search", `query="${query}"`, "format=json"]);
    try {
      return JSON.parse(raw) as KBSearchResult[];
    } catch {
      // Non-JSON output — wrap as single result
      return [{ file: "search", matches: [raw] }];
    }
  }

  async list(folder?: string): Promise<KBFileInfo[]> {
    const args = ["files", "format=json"];
    if (folder) args.push(`folder="${folder}"`);
    const raw = await this.exec(args);
    try {
      return JSON.parse(raw) as KBFileInfo[];
    } catch {
      return [];
    }
  }

  async properties(file: string): Promise<Record<string, unknown>> {
    const raw = await this.exec(["properties", `file="${file}"`, "format=json"]);
    try {
      return JSON.parse(raw) as Record<string, unknown>;
    } catch {
      return {};
    }
  }

  /**
   * Git sync the KB vault — fire-and-forget commit.
   * Failures are logged but never block the caller.
   */
  async gitSync(message: string): Promise<void> {
    if (!this.enabled) return;
    const vaultPath = this.vaultPath?.trim();
    if (!vaultPath) {
      console.warn("[knowledge] gitSync skipped: vaultPath is not configured.");
      return;
    }

    try {
      await execFileAsync("git", ["add", "-A"], {
        cwd: vaultPath,
        timeout: 10_000,
      });
      await execFileAsync("git", ["commit", "-m", message, "--allow-empty"], {
        cwd: vaultPath,
        timeout: 10_000,
      });
    } catch {
      // Fire-and-forget: log but never throw
    }
  }

  /**
   * Build a system prompt context string from configured context files.
   * Returns empty string if no context files or KB is disabled.
   */
  async buildContext(contextFiles: string[]): Promise<string> {
    if (!this.enabled || contextFiles.length === 0) return "";

    const parts: string[] = [];
    for (const file of contextFiles) {
      try {
        const content = await this.read(file);
        parts.push(`--- ${file} ---\n${content}`);
      } catch {
        // Skip files that can't be read
      }
    }
    return parts.join("\n\n");
  }
}
