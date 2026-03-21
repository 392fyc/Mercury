---
name: web-research
description: |
  Use this skill before writing code that depends on an external SDK, API, package, CLI, config key, environment variable, or version claim. Trigger on English and Chinese requests such as "research", "verify", "validate", "check docs", "SDK", "API", "CLI", "npm version", "PyPI", "研究", "验证", "查文档", "查官方文档", "核对版本", "审查集成". This skill enforces Mercury's rule to verify against official vendor docs first, cross-check npm or PyPI for publish status, record source URLs, and mark unresolved claims as `UNVERIFIED`. Do not use this skill for purely internal project APIs unless external behavior is involved.
---

# Web Research Protocol

Mercury enforces a strict rule: **never guess SDK/API/CLI behavior from training data**. This skill provides the structured research workflow to follow before writing any code that depends on external tools or libraries.

This protocol exists because LLM training data frequently contains outdated API signatures, deprecated methods, and incorrect version numbers. A single unverified claim can cascade into hours of debugging. The cost of a 2-minute web search is always lower than the cost of fixing code built on wrong assumptions.

## When This Protocol Applies

Research is required before writing code that:
- Imports an external SDK (`@anthropic-ai/sdk`, `@openai/codex`, `@tauri-apps/api`, etc.)
- References an API method signature or constructor
- Claims a specific package version or compatibility
- Uses CLI flags or command syntax
- References environment variables or configuration keys from external tools

This protocol usually does **not** apply when:
- You are only using Mercury's own internal code or local project types
- The task is pure refactoring with no external API surface
- The user only wants prose, translation, or summarization without implementation risk

## Research Workflow

### 1. Identify Claims to Verify

Before writing, list every external dependency claim in your planned code:
- Package name and version
- Import paths
- Method signatures (parameters, return types)
- Configuration keys/values
- CLI command syntax

### 2. Search Official Sources

For each claim, verify against the **vendor's official documentation** in this priority order:

1. **Official docs site** (e.g., `docs.anthropic.com`, `developers.openai.com`) — most authoritative
2. **npm/PyPI registry** — for published version and install command
3. **Official blog posts or changelogs** — for recent changes
4. **GitHub README** (official repo only) — acceptable as supplement

**Not sufficient on their own**: GitHub source code (may show unreleased dev version), Stack Overflow answers (may be outdated), blog posts from third parties.

### 3. Record Evidence

When you find the authoritative answer, note:
- The exact URL you verified against
- The version/date of the documentation
- The specific API signature or behavior confirmed

When you report findings, include source URLs directly in your notes or handoff so the next agent can re-check quickly.

### 4. Mark Unverified Claims

If web search is unavailable or the official docs don't cover your specific question:
- Mark the claim as `UNVERIFIED` in a code comment
- Note what you searched for and what you found (or didn't find)
- Escalate to the user if the unverified claim is critical to the task

## Codex-Specific Guidance

- Use the browser/search tool before writing code that imports external packages or claims current versions.
- Prefer official sources over mirrors, summaries, or repo source trees.
- Cross-check package existence on npm or PyPI before naming an install command or version.
- If the environment cannot browse or the source is inaccessible, explicitly say the claim is `UNVERIFIED`; do not silently proceed as if it were confirmed.
- When multiple packages have similar names, confirm the exact published package name before writing imports.

## Example

Before writing:
```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";
```

Search: `WebSearch("anthropic claude agent sdk npm query function 2026")`

Verify:
- Package exists on npm: `@anthropic-ai/claude-agent-sdk`
- Current published version
- `query()` function signature and parameters
- Import path is correct

Then proceed with implementation using verified signatures.

## Integration with Hooks

Mercury has automated enforcement via hooks:
- `web-research-gate.sh` blocks Edit/Write operations containing SDK imports, version claims, or API signatures unless a web research flag was set within the last 3 minutes
- `post-web-research-flag.sh` automatically sets this flag after WebSearch/WebFetch completes
- `user-prompt-submit.sh` injects the research protocol reminder when research-intent keywords are detected

These hooks are a safety net — this skill provides the proactive workflow to follow so you rarely hit the gate.
