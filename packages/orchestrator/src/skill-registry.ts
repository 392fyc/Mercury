/**
 * Mercury Skill Registry — BM25-based retrieval of accumulated agent skills.
 *
 * Skills are stored as SKILL.md files in .mercury/skills/{name}/SKILL.md.
 * The registry scans the directory on init(), builds an in-memory BM25 index,
 * and provides searchSkills() for role-filtered retrieval at dispatch time.
 *
 * Packages (verified via npm/web 2026-04-04):
 * - fast-bm25 v0.0.5: BM25 class, search() returns {index,score}[], fieldBoosts supported
 * - write-file-atomic v7.0.1: CommonJS default export, Promise<void>, atomic temp-rename writes
 */

import { createRequire } from "node:module";
import { readdir, readFile, stat } from "node:fs/promises";
import { existsSync, mkdirSync } from "node:fs";
import { join, basename } from "node:path";
import { BM25 } from "fast-bm25";

// write-file-atomic is CommonJS — ESM interop via createRequire
const _require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const writeFileAtomic = _require("write-file-atomic") as (path: string, data: string) => Promise<void>;

// ─── Public Types ───

/** Skill category mirroring OpenSpace SkillCategory enum */
export type SkillCategory = "TOOL_GUIDE" | "WORKFLOW" | "REFERENCE";

/** Skill origin mirroring OpenSpace SkillOrigin enum */
export type SkillOrigin = "IMPORTED" | "CAPTURED" | "DERIVED" | "FIXED";

/** Agent roles that can receive skill injection */
export type SkillRole = "dev" | "research" | "main" | "design" | "acceptance" | "critic";

/**
 * SkillMeta — lightweight in-memory representation used for BM25 indexing and retrieval.
 * Loaded from YAML frontmatter of each SKILL.md.
 */
export interface SkillMeta {
  /** Hyphenated slug, e.g. "git-commit-heredoc-pattern" */
  name: string;
  /** One-sentence description used for BM25 retrieval */
  description: string;
  category: SkillCategory;
  roles: SkillRole[];
  origin: SkillOrigin;
  tags: string[];
  generation: number;
  /** Absolute path to the SKILL.md file */
  filePath: string;
  /** ISO8601 timestamp — used for staleness detection (last_validated_at in frontmatter) */
  lastValidatedAt?: string;
  /** Quality metrics */
  totalSelections: number;
  totalApplied: number;
  totalCompletions: number;
  totalFallbacks: number;
  /** Lineage */
  sourceTaskId?: string;
  sourceRole?: string;
  capturedAt?: string;
  capturedBy?: string;
  parentSkillIds: string[];
  /** Staleness flag set during init() */
  staleness?: "STALE" | "UNDERPERFORMING";
}

// ─── Frontmatter helpers ───

function parseFrontmatter(content: string): Record<string, unknown> {
  const fmMatch = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!fmMatch) return {};

  const out: Record<string, unknown> = {};
  const lines = fmMatch[1].split(/\r?\n/);
  let currentKey: string | null = null;
  let listItems: string[] = [];

  const flushList = () => {
    if (currentKey && listItems.length > 0) {
      out[currentKey] = [...listItems];
      listItems = [];
    }
  };

  for (const line of lines) {
    const listMatch = line.match(/^  ?- (.+)$/);
    if (listMatch && currentKey) {
      listItems.push(listMatch[1].trim());
      continue;
    }
    if (listItems.length > 0) flushList();

    const kvMatch = line.match(/^([a-zA-Z_][a-zA-Z0-9_-]*):\s*(.*)$/);
    if (!kvMatch) continue;
    currentKey = kvMatch[1];
    const rawVal = kvMatch[2].trim();

    if (rawVal === "" || rawVal === "[]") {
      if (rawVal === "[]") out[currentKey] = [];
      continue;
    }
    const inlineList = rawVal.match(/^\[(.+)\]$/);
    if (inlineList) {
      out[currentKey] = inlineList[1].split(",").map((s) => s.trim().replace(/^['"]|['"]$/g, ""));
      continue;
    }
    const num = Number(rawVal);
    if (!Number.isNaN(num)) { out[currentKey] = num; continue; }
    out[currentKey] = rawVal.replace(/^['"]|['"]$/g, "");
  }
  if (listItems.length > 0) flushList();

  return out;
}

function serializeFrontmatterField(key: string, val: unknown): string {
  if (Array.isArray(val)) {
    if (val.length === 0) return `${key}: []`;
    return `${key}:\n${(val as unknown[]).map((v) => `  - ${String(v)}`).join("\n")}`;
  }
  return `${key}: ${String(val)}`;
}

function updateFrontmatterFields(content: string, updates: Record<string, unknown>): string {
  const fmMatch = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!fmMatch) return content;

  const fmLines = fmMatch[1].split(/\r?\n/);
  const updatedLines: string[] = [];
  const handled = new Set<string>();

  let i = 0;
  while (i < fmLines.length) {
    const line = fmLines[i];
    const kvMatch = line.match(/^([a-zA-Z_][a-zA-Z0-9_-]*):\s*/);
    if (kvMatch && kvMatch[1] in updates) {
      const key = kvMatch[1];
      handled.add(key);
      updatedLines.push(serializeFrontmatterField(key, updates[key]));
      i++;
      while (i < fmLines.length && /^\s+-/.test(fmLines[i])) i++;
      continue;
    }
    updatedLines.push(line);
    i++;
  }

  for (const [key, val] of Object.entries(updates)) {
    if (!handled.has(key)) {
      updatedLines.push(serializeFrontmatterField(key, val));
    }
  }

  return content.replace(fmMatch[0], `---\n${updatedLines.join("\n")}\n---`);
}

// ─── Constants ───

const STALE_DAYS = 90;
const LOW_COMPLETION_RATE = 0.3;
const MIN_SELECTIONS_FOR_RATE = 5;

// ─── SkillRegistry ───

export class SkillRegistry {
  private skills = new Map<string, SkillMeta>();
  private bm25: BM25 | null = null;
  private indexedNames: string[] = [];
  private skillDir = "";

  /**
   * Scan skillDir for SKILL.md files, build BM25 index, run staleness detection.
   * Safe to call multiple times (re-initializes state).
   */
  async init(skillDir: string): Promise<void> {
    this.skillDir = skillDir;
    this.skills.clear();
    this.indexedNames = [];

    if (!existsSync(skillDir)) {
      mkdirSync(skillDir, { recursive: true });
    }

    const metas = await this.scanSkillDir(skillDir);
    const docs: Array<Record<string, string>> = [];

    for (const meta of metas) {
      this.skills.set(meta.name, meta);
      docs.push({
        description: meta.description,
        tags: meta.tags.join(" "),
        category: meta.category.toLowerCase().replace(/_/g, " "),
      });
      this.indexedNames.push(meta.name);

      // Staleness detection
      if (meta.lastValidatedAt) {
        const ageMs = Date.now() - new Date(meta.lastValidatedAt).getTime();
        if (ageMs > STALE_DAYS * 24 * 60 * 60 * 1000) {
          meta.staleness = "STALE";
        }
      }
      if (
        meta.totalSelections > MIN_SELECTIONS_FOR_RATE &&
        meta.totalApplied > 0 &&
        meta.totalCompletions / meta.totalApplied < LOW_COMPLETION_RATE
      ) {
        meta.staleness = meta.staleness === "STALE" ? "STALE" : "UNDERPERFORMING";
      }
    }

    if (docs.length > 0) {
      this.bm25 = new BM25(docs, {
        fieldBoosts: { description: 2.0, tags: 1.5, category: 0.5 },
      });
    }
  }

  /**
   * Search skills by query, filtered to the given roles.
   * Returns up to `limit` results ordered by BM25 relevance.
   */
  searchSkills(query: string, roles: string[], limit = 3): SkillMeta[] {
    if (!this.bm25 || this.skills.size === 0) return [];

    const topK = Math.min(this.skills.size, limit * 6);
    const results = this.bm25.search(query, topK) as Array<{ index: number; score: number }>;

    const filtered: SkillMeta[] = [];
    for (const result of results) {
      const name = this.indexedNames[result.index];
      if (!name) continue;
      const meta = this.skills.get(name);
      if (!meta) continue;
      if (roles.length > 0 && !roles.some((r) => meta.roles.includes(r as SkillRole))) continue;
      filtered.push(meta);
      if (filtered.length >= limit) break;
    }

    return filtered;
  }

  /** Look up a skill by exact name. */
  getSkill(name: string): SkillMeta | undefined {
    return this.skills.get(name);
  }

  /** Read the full SKILL.md body (text after the frontmatter block). */
  async loadSkillBody(skill: SkillMeta): Promise<string> {
    const content = await readFile(skill.filePath, "utf-8");
    return content.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "").trimStart();
  }

  /** Return all loaded skills. */
  listSkills(): SkillMeta[] {
    return Array.from(this.skills.values());
  }

  /** Whether any skills are loaded and searchable. */
  isReady(): boolean {
    return this.skills.size > 0;
  }

  /** Returns the configured skill directory path. */
  getSkillDir(): string {
    return this.skillDir;
  }

  // ─── Metric recording (non-blocking, best-effort) ───

  /** Increment total_selections for a skill. */
  recordSelection(skillName: string): void {
    const meta = this.skills.get(skillName);
    if (!meta) return;
    meta.totalSelections++;
    void this.persistStats(meta);
  }

  /**
   * Increment total_completions for a skill and refresh last_validated_at.
   * Called after Acceptance PASS for each skill that was injected into that dispatch.
   */
  recordCompletion(skillName: string): void {
    const meta = this.skills.get(skillName);
    if (!meta) return;
    meta.totalCompletions++;
    meta.lastValidatedAt = new Date().toISOString();
    meta.staleness = undefined;
    void this.persistStats(meta);
  }

  /** Increment total_fallbacks for a skill. */
  recordFallback(skillName: string): void {
    const meta = this.skills.get(skillName);
    if (!meta) return;
    meta.totalFallbacks++;
    void this.persistStats(meta);
  }

  // ─── Private ───

  private async scanSkillDir(dir: string): Promise<SkillMeta[]> {
    let entries: string[];
    try {
      entries = await readdir(dir) as string[];
    } catch {
      return [];
    }

    const metas: SkillMeta[] = [];
    for (const entry of entries) {
      if (entry === "pending" || entry.startsWith(".") || entry.endsWith(".md")) continue;
      const skillMdPath = join(dir, entry, "SKILL.md");
      try {
        await stat(skillMdPath);
      } catch {
        continue;
      }
      const meta = await this.loadSkillMeta(skillMdPath);
      if (meta) metas.push(meta);
    }
    return metas;
  }

  private async loadSkillMeta(filePath: string): Promise<SkillMeta | null> {
    let content: string;
    try {
      content = await readFile(filePath, "utf-8");
    } catch {
      return null;
    }

    const fm = parseFrontmatter(content);
    const name = String(fm["name"] ?? basename(filePath));
    if (!name) return null;

    const safeArr = (v: unknown): string[] => {
      if (Array.isArray(v)) return v.map(String);
      if (typeof v === "string" && v) return [v];
      return [];
    };
    const safeNum = (v: unknown): number => (typeof v === "number" ? v : 0);
    const safeStr = (v: unknown): string | undefined =>
      typeof v === "string" && v ? v : undefined;

    return {
      name,
      description: String(fm["description"] ?? ""),
      category: (String(fm["category"] ?? "TOOL_GUIDE").toUpperCase()) as SkillCategory,
      roles: safeArr(fm["roles"]) as SkillRole[],
      origin: (String(fm["origin"] ?? "IMPORTED").toUpperCase()) as SkillOrigin,
      tags: safeArr(fm["tags"]),
      generation: safeNum(fm["generation"]),
      filePath,
      lastValidatedAt: safeStr(fm["last_validated_at"]),
      totalSelections: safeNum(fm["total_selections"]),
      totalApplied: safeNum(fm["total_applied"]),
      totalCompletions: safeNum(fm["total_completions"]),
      totalFallbacks: safeNum(fm["total_fallbacks"]),
      sourceTaskId: safeStr(fm["source_task_id"]),
      sourceRole: safeStr(fm["source_role"]),
      capturedAt: safeStr(fm["captured_at"]),
      capturedBy: safeStr(fm["captured_by"]),
      parentSkillIds: safeArr(fm["parent_skill_ids"]),
    };
  }

  private async persistStats(meta: SkillMeta): Promise<void> {
    try {
      const content = await readFile(meta.filePath, "utf-8");
      const updated = updateFrontmatterFields(content, {
        total_selections: meta.totalSelections,
        total_applied: meta.totalApplied,
        total_completions: meta.totalCompletions,
        total_fallbacks: meta.totalFallbacks,
        last_validated_at: meta.lastValidatedAt ?? "",
      });
      await writeFileAtomic(meta.filePath, updated);
    } catch {
      // Best-effort — a failed stat update does not break the skill engine
    }
  }
}
