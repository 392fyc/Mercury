/**
 * Knowledge Service — optional Obsidian CLI wrapper.
 *
 * This is a project-level knowledge backend, NOT an agent adapter.
 * When enabled, provides read/write/search/list over an Obsidian vault via CLI.
 * When disabled, Mercury operates normally — agents can still use their own
 * MCP servers, mem0, or other knowledge tools independently.
 */

import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { promisify } from "node:util";
import type { KBSearchResult, KBFileInfo, ObsidianConfig } from "@mercury/core";

const execFileAsync = promisify(execFile);
type KBEntryKind = KBFileInfo["kind"];
const WINDOWS_OBSIDIAN_BIN_CANDIDATES = [
  "D:/Programs/Obsidian/Obsidian.exe",
  process.env.LOCALAPPDATA
    ? path.join(process.env.LOCALAPPDATA, "Obsidian", "Obsidian.exe")
    : undefined,
].filter((candidate): candidate is string => Boolean(candidate));

/** Obsidian CLI wrapper providing read/write/search/list over a vault. */
export class KnowledgeService {
  private vaultName: string;
  private vaultPath: string | undefined;
  private enabled: boolean;
  private obsidianBin: string;

  constructor(config: ObsidianConfig) {
    this.vaultName = config.vaultName;
    this.vaultPath = config.vaultPath;
    this.enabled = config.enabled;
    this.obsidianBin = this.resolveObsidianBin(config.obsidianBin);
  }

  /**
   * Attempt to find the Obsidian binary on PATH.
   * On Windows, `execFile` does not perform PATHEXT resolution, so we
   * manually search PATH directories for Obsidian.exe / obsidian.exe.
   * Falls back to bare "obsidian" (works on Linux/macOS where shell-level
   * resolution handles it).
   */
  private resolveObsidianBin(configuredBin?: string): string {
    const explicitBin = configuredBin?.trim();
    if (explicitBin) {
      // If the configured path is a directory (e.g. "D:\Programs\Obsidian"),
      // try appending the executable name before returning as-is.
      if (process.platform === "win32" && !explicitBin.toLowerCase().endsWith(".exe")) {
        const withExe = path.join(explicitBin, "Obsidian.exe");
        if (existsSync(withExe)) {
          return withExe;
        }
      }
      return explicitBin;
    }

    if (process.platform === "win32") {
      const pathDirs = (process.env.PATH ?? "").split(path.delimiter);
      const candidates = ["Obsidian.exe", "obsidian.exe"];
      for (const dir of pathDirs) {
        for (const name of candidates) {
          const full = path.join(dir, name);
          if (existsSync(full)) {
            return full;
          }
        }
      }

      for (const candidate of WINDOWS_OBSIDIAN_BIN_CANDIDATES) {
        if (existsSync(candidate)) {
          return candidate;
        }
      }
    }
    return "obsidian";
  }

  /** Resolve a vault-relative path to an absolute filesystem path, or null if vaultPath is unset. */
  private resolveVaultFilePath(relativePath: string): string | null {
    const vaultPath = this.vaultPath?.trim();
    if (!vaultPath) {
      return null;
    }

    // Strip leading vault-name prefix (e.g. "Mercury_KB/04-research/foo.md" → "04-research/foo.md")
    // to prevent double-path like D:\Mercury\Mercury_KB\Mercury_KB\...
    const vaultBaseName = path.basename(vaultPath);
    const prefixSlash = `${vaultBaseName}/`;
    const prefixBackslash = `${vaultBaseName}\\`;
    let normalizedRelative = relativePath;
    if (normalizedRelative.startsWith(prefixSlash) || normalizedRelative.startsWith(prefixBackslash)) {
      normalizedRelative = normalizedRelative.slice(prefixSlash.length);
    }

    const pathSegments = normalizedRelative.split(/[\\/]+/).filter(Boolean);
    const resolved = path.join(vaultPath, ...pathSegments);
    const normalizedVault = path.resolve(vaultPath);
    const normalizedResolved = path.resolve(resolved);
    if (!normalizedResolved.startsWith(normalizedVault + path.sep) && normalizedResolved !== normalizedVault) {
      console.error(`[knowledge] Path traversal attempt blocked: ${relativePath}`);
      return null;
    }
    return resolved;
  }

  /** Split raw CLI output into trimmed, non-empty lines. */
  private parsePlainTextList(raw: string): string[] {
    return raw
      .split(/\r?\n/)
      .map((line) => line.trim())
      .filter((line) => line.length > 0);
  }

  private toFileInfo(path: string, kind: KBEntryKind): KBFileInfo {
    const normalizedPath = path.replace(/\\/g, "/").replace(/\/+$/, "");
    const parts = normalizedPath.split("/").filter((part) => part.length > 0);
    const name = parts.at(-1) ?? normalizedPath;
    const folder = parts.slice(0, -1).join("/");

    return {
      path: normalizedPath,
      name,
      folder,
      kind,
    };
  }

  private parseListOutput(raw: string, kind: KBEntryKind): KBFileInfo[] {
    return this.parsePlainTextList(raw).map((entryPath) => this.toFileInfo(entryPath, kind));
  }

  private normalizeFolderPath(folder?: string): string | undefined {
    const normalized = folder?.trim().replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
    return normalized && normalized.length > 0 ? normalized : undefined;
  }

  private filterImmediateChildren(entries: KBFileInfo[], folder?: string): KBFileInfo[] {
    const normalizedFolder = this.normalizeFolderPath(folder);

    return entries
      .filter((entry) => {
        if (normalizedFolder) {
          return entry.folder === normalizedFolder;
        }

        if (entry.kind === "folder") {
          return entry.folder === "" && entry.path.length > 0;
        }

        return entry.folder === "";
      })
      .map((entry) => {
        if (entry.kind !== "folder") {
          return entry;
        }

        return {
          ...entry,
          name: entry.path.split("/").filter((part) => part.length > 0).at(-1) ?? entry.name,
        };
      });
  }

  private isRecord(value: unknown): value is Record<string, unknown> {
    return value !== null && typeof value === "object";
  }

  private asString(value: unknown): string | undefined {
    return typeof value === "string" && value.trim().length > 0 ? value : undefined;
  }

  private asStringArray(value: unknown): string[] {
    if (!Array.isArray(value)) {
      return [];
    }

    return value
      .filter((entry): entry is string => typeof entry === "string")
      .map((entry) => entry.trim())
      .filter((entry) => entry.length > 0);
  }

  private extractSearchItems(payload: unknown): unknown[] {
    if (Array.isArray(payload)) {
      return payload;
    }

    if (!this.isRecord(payload)) {
      return [];
    }

    if ("file" in payload || "path" in payload || "matches" in payload || "snippet" in payload) {
      return [payload];
    }

    for (const key of ["results", "items", "matches"]) {
      const candidate = payload[key];
      if (Array.isArray(candidate)) {
        return candidate;
      }
    }

    return [];
  }

  private normalizeSearchResult(item: unknown): KBSearchResult | null {
    if (typeof item === "string") {
      const match = item.trim();
      return match ? { file: "search", matches: [match] } : null;
    }

    if (!this.isRecord(item)) {
      return null;
    }

    const file =
      this.asString(item.file) ??
      this.asString(item.path) ??
      this.asString(item.note) ??
      this.asString(item.title);
    const matches = [
      ...this.asStringArray(item.matches),
      ...this.asStringArray(item.snippets),
      ...this.asStringArray(item.lines),
    ];
    const singleMatch =
      this.asString(item.snippet) ?? this.asString(item.match) ?? this.asString(item.content);

    if (singleMatch && !matches.includes(singleMatch)) {
      matches.push(singleMatch);
    }

    if (!file && matches.length === 0) {
      return null;
    }

    return {
      file: file ?? "search",
      matches,
      score: typeof item.score === "number" ? item.score : undefined,
    };
  }

  private parseSearchResults(raw: string): KBSearchResult[] {
    const parsed = JSON.parse(raw) as unknown;
    const results = this.extractSearchItems(parsed)
      .map((item) => this.normalizeSearchResult(item))
      .filter((item): item is KBSearchResult => item !== null);

    return results;
  }

  isEnabled(): boolean {
    return this.enabled;
  }

  /** Execute an Obsidian CLI command with the configured vault. */
  private async exec(args: string[]): Promise<string> {
    if (!this.enabled) {
      throw new Error("Knowledge service is disabled. Enable obsidian in mercury.config.json.");
    }

    const fullArgs = [`vault=${this.vaultName}`, ...args];

    try {
      const { stdout } = await execFileAsync(this.obsidianBin, fullArgs, {
        timeout: 15_000,
        windowsHide: true,
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

  /** Read a file from the vault via CLI, falling back to direct fs read if CLI fails. */
  async read(file: string): Promise<string> {
    try {
      return await this.exec(["read", `file=${file}`]);
    } catch (error) {
      const filePath = this.resolveVaultFilePath(file);
      if (!filePath) {
        console.error("[knowledge] CLI read failed and no vaultPath configured for fallback");
        throw error;
      }

      try {
        const content = await fs.readFile(filePath, "utf-8");
        console.warn(`[knowledge] CLI read failed, fell back to direct fs read: ${filePath}`);
        return content;
      } catch (fsError) {
        console.error(`[knowledge] Fallback fs read also failed: ${fsError instanceof Error ? fsError.message : fsError}`);
        throw error; // Still throw original CLI error
      }
    }
  }

  /** Content size above which we skip the CLI and write directly to the vault filesystem. */
  private static readonly CLI_WRITE_SIZE_THRESHOLD = 8192;

  /** Write a file to the vault via CLI, falling back to direct fs write if CLI fails. */
  async write(name: string, content: string): Promise<void> {
    // For large content, bypass CLI entirely — passing multi-KB content as a single
    // argv entry exceeds Windows CreateProcess limits and will always fail.
    const byteCount = Buffer.byteLength(content, "utf8");
    if (byteCount > KnowledgeService.CLI_WRITE_SIZE_THRESHOLD) {
      const filePath = this.resolveVaultFilePath(name);
      if (filePath) {
        await fs.mkdir(path.dirname(filePath), { recursive: true });
        await fs.writeFile(filePath, content, "utf-8");
        console.warn(`[knowledge] Large content (${byteCount} bytes) — wrote directly to fs: ${filePath}`);
        return;
      }
      // No vaultPath — fall through to CLI attempt which will surface a clear error
    }
    try {
      await this.exec(["create", `name=${name}`, `content=${content}`]);
    } catch (error) {
      const filePath = this.resolveVaultFilePath(name);
      if (!filePath) {
        console.error("[knowledge] CLI write failed and no vaultPath configured for fallback");
        throw error;
      }

      try {
        await fs.mkdir(path.dirname(filePath), { recursive: true });
        await fs.writeFile(filePath, content, "utf-8");
        console.warn(`[knowledge] CLI write failed, fell back to direct fs write: ${filePath}`);
      } catch (fsError) {
        console.error(`[knowledge] Fallback fs write also failed: ${fsError instanceof Error ? fsError.message : fsError}`);
        throw error; // Still throw original CLI error
      }
    }
  }

  /** Append content to an existing vault file, falling back to direct fs append if CLI fails. */
  async append(file: string, content: string): Promise<void> {
    try {
      await this.exec(["append", `file=${file}`, `content=${content}`]);
    } catch (error) {
      const filePath = this.resolveVaultFilePath(file);
      if (!filePath) {
        console.error("[knowledge] CLI append failed and no vaultPath configured for fallback");
        throw error;
      }

      try {
        await fs.appendFile(filePath, content, "utf-8");
        console.warn(`[knowledge] CLI append failed, fell back to direct fs append: ${filePath}`);
      } catch (fsError) {
        console.error(`[knowledge] Fallback fs append also failed: ${fsError instanceof Error ? fsError.message : fsError}`);
        throw error;
      }
    }
  }

  /** Search the vault for notes matching a query, returning parsed results. */
  async search(query: string): Promise<KBSearchResult[]> {
    const raw = await this.exec(["search", `query=${query}`, "format=json"]);
    try {
      const parsed = this.parseSearchResults(raw);
      return parsed.length > 0 ? parsed : [{ file: "search", matches: [raw] }];
    } catch {
      // Non-JSON output — wrap as single result
      return [{ file: "search", matches: [raw] }];
    }
  }

  /** List immediate children (files and folders) of a vault directory. */
  async list(folder?: string): Promise<KBFileInfo[]> {
    const scopedArg = folder ? [`folder=${folder}`] : [];
    const [filesRaw, foldersRaw] = await Promise.all([
      this.exec(["files", ...scopedArg]),
      this.exec(["folders", ...scopedArg]),
    ]);

    const entries = [
      ...this.parseListOutput(foldersRaw, "folder"),
      ...this.parseListOutput(filesRaw, "file"),
    ];

    const deduped = new Map<string, KBFileInfo>();
    for (const entry of entries) {
      deduped.set(`${entry.kind}:${entry.path}`, entry);
    }

    return this.filterImmediateChildren(Array.from(deduped.values()), folder);
  }

  async properties(file: string): Promise<Record<string, unknown>> {
    const raw = await this.exec(["properties", `file=${file}`, "format=json"]);
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
        windowsHide: true,
      });
      await execFileAsync("git", ["commit", "-m", message, "--allow-empty"], {
        cwd: vaultPath,
        timeout: 10_000,
        windowsHide: true,
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
