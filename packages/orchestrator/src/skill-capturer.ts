/**
 * Mercury Skill Capturer — extracts reusable skills from completed tasks.
 *
 * Triggered after Acceptance PASS. Receives the task, receipt, and git diff,
 * makes an LLM call to identify 0-2 reusable patterns, then writes draft SKILL.md
 * files to .mercury/skills/pending/ for Main Agent review.
 *
 * Packages (verified via npm/GitHub 2026-04-04):
 * - write-file-atomic v7.0.1: atomic temp-rename writes, Promise-based (no callback needed)
 */

import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { randomUUID } from "node:crypto";
import type { TaskBundle, ImplementationReceipt } from "@mercury/core";

// write-file-atomic: CommonJS module — ESM interop via createRequire
const _require = createRequire(import.meta.url);
// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
const writeFileAtomic = _require("write-file-atomic") as (path: string, data: string) => Promise<void>;

// ─── Types ───

export interface CaptureInput {
  task: TaskBundle;
  receipt: ImplementationReceipt;
  gitDiff?: string;
}

export interface DraftSkill {
  name: string;
  filePath: string;
}

export type LLMCaller = (prompt: string) => Promise<string>;

// ─── Skill Capturer ───

export class SkillCapturer {
  private readonly pendingDir: string;
  private llmCaller: LLMCaller | null = null;

  constructor(skillDir: string) {
    this.pendingDir = join(skillDir, "pending");
  }

  /** Inject LLM caller for pattern extraction. If not set, capture is a no-op. */
  setLLMCaller(caller: LLMCaller): void {
    this.llmCaller = caller;
  }

  /**
   * Attempt to extract 0-2 reusable skill patterns from the completed task.
   * Writes drafts to .mercury/skills/pending/ — does NOT activate them.
   * Non-blocking: caller should fire-and-forget (void).
   */
  async captureFromAcceptancePass(input: CaptureInput): Promise<DraftSkill[]> {
    if (!this.llmCaller) return [];

    // No internal catch — errors propagate to the caller's .catch() for transport logging.
    // Best-effort is enforced at the call site (fire-and-forget void + .catch()).
    await this.ensurePendingDir();
    const patterns = await this.extractPatterns(input);
    const drafts: DraftSkill[] = [];

    for (const pattern of patterns.slice(0, 2)) {
      const draft = await this.writeDraftSkill(pattern, input.task);
      if (draft) drafts.push(draft);
    }

    return drafts;
  }

  // ─── Private ───

  private async ensurePendingDir(): Promise<void> {
    await mkdir(this.pendingDir, { recursive: true });
  }

  private async extractPatterns(input: CaptureInput): Promise<ExtractedPattern[]> {
    const prompt = buildCapturePrompt(input);
    const response = await this.llmCaller!(prompt);
    return parsePatterns(response);
  }

  private async writeDraftSkill(
    pattern: ExtractedPattern,
    task: TaskBundle,
  ): Promise<DraftSkill | null> {
    const name = sanitizeSlug(pattern.name);
    if (!name) return null;

    // Append UUID suffix to avoid silent overwrite when the same slug is captured again
    const uuid8 = randomUUID().slice(0, 8);
    const skillDir = join(this.pendingDir, `${name}_${uuid8}`);
    await mkdir(skillDir, { recursive: true });

    const filePath = join(skillDir, "SKILL.md");
    // OpenSpace convention: evolved/captured skills use __v{gen}_{uuid8} format
    const skillId = `${name}__v0_${randomUUID().slice(0, 8)}`;
    const now = new Date().toISOString();

    const content = [
      "---",
      `name: ${name}`,
      `description: ${yamlEscape(pattern.description)}`,
      `category: ${yamlEscape(pattern.category)}`,
      `roles:`,
      ...pattern.roles.map((r) => `  - ${yamlEscape(r)}`),
      `origin: CAPTURED`,
      `source_task_id: ${task.taskId}`,
      `source_role: ${yamlEscape(task.role ?? "dev")}`,
      `captured_at: ${now}`,
      `captured_by: acceptance-agent`,
      `tags: []`,
      `generation: 0`,
      `parent_skill_ids: []`,
      `total_selections: 0`,
      `total_applied: 0`,
      `total_completions: 0`,
      `total_fallbacks: 0`,
      "---",
      "",
      `# ${pattern.title}`,
      "",
      pattern.body,
      "",
      `<!-- Draft skill — pending Main Agent review. Source: ${task.taskId} -->`,
    ].join("\n");

    await writeFileAtomic(filePath, content);
    await writeFileAtomic(join(skillDir, ".skill_id"), skillId);

    return { name, filePath };
  }
}

// ─── Prompt builder ───

function buildCapturePrompt(input: CaptureInput): string {
  const { task, receipt, gitDiff } = input;

  const diffSection = gitDiff
    ? `\n## Git Diff (first 3000 chars)\n\`\`\`\n${gitDiff.slice(0, 3000).replace(/```/g, "` ` `")}\n\`\`\``
    : "";

  return `You are a skill extraction agent for the Mercury multi-agent system.

Analyze the completed task below and identify 0 to 2 reusable agent skills.

A skill is reusable if:
1. It describes a HOW-TO pattern, not a project-specific solution
2. It remains meaningful after removing task IDs, file paths, and domain-specific names
3. It would help future agents avoid repeated exploration or mistakes
4. It fits: TOOL_GUIDE (how to use a tool), WORKFLOW (multi-step process), or REFERENCE (key facts)

## Task
- ID: ${task.taskId}
- Title: ${task.title}
- Role: ${task.role ?? "dev"}
- Context: ${task.context.slice(0, 800)}

## Receipt Summary
${receipt.summary}

## Changed Files
${(receipt.changedFiles ?? []).slice(0, 10).join(", ")}
${diffSection}

## Output Format

Return a JSON array of 0-2 skills. If no reusable pattern exists, return [].

\`\`\`json
[
  {
    "name": "hyphenated-skill-slug",
    "title": "Human-readable title",
    "description": "One sentence for retrieval (max 120 chars)",
    "category": "TOOL_GUIDE|WORKFLOW|REFERENCE",
    "roles": ["dev", "research", "main", "design"],
    "body": "## Instructions\\n\\nMarkdown body with the reusable pattern..."
  }
]
\`\`\`

Return ONLY the JSON array. No other text.`;
}

// ─── Pattern parser ───

interface ExtractedPattern {
  name: string;
  title: string;
  description: string;
  category: string;
  roles: string[];
  body: string;
}

function parsePatterns(response: string): ExtractedPattern[] {
  try {
    const jsonMatch = response.match(/```(?:json)?\s*([\s\S]*?)\s*```/);
    const jsonStr = jsonMatch ? jsonMatch[1] : response.trim();
    const parsed = JSON.parse(jsonStr) as unknown;
    if (!Array.isArray(parsed)) return [];

    return parsed.filter((item): item is ExtractedPattern => {
      if (typeof item !== "object" || item === null) return false;
      const p = item as Record<string, unknown>;
      return (
        typeof p["name"] === "string" &&
        typeof p["title"] === "string" &&
        typeof p["description"] === "string" &&
        typeof p["category"] === "string" &&
        Array.isArray(p["roles"]) &&
        typeof p["body"] === "string"
      );
    });
  } catch {
    return [];
  }
}

/** Escape a string for safe YAML scalar value (inline, unquoted style). */
function yamlEscape(str: string): string {
  // Wrap in double quotes if the value contains characters that break YAML scalars
  if (/[:\n\r"'#\[\]{}|>&*!?,]/.test(str) || str.startsWith(" ") || str.startsWith("-")) {
    return `"${str.replace(/\\/g, "\\\\").replace(/"/g, '\\"').replace(/\n/g, "\\n").replace(/\r/g, "\\r")}"`;
  }
  return str;
}

function sanitizeSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 60);
}
