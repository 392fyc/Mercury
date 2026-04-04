---
name: web-research
description: |
  Mercury's mandatory web research protocol for verifying external SDK/API/CLI behavior before writing code. Use this skill whenever the task involves importing external packages, referencing API signatures, claiming package versions, using CLI flags, or integrating with third-party tools. Also use when the user says "研究", "验证", "审查", "查阅", "核实", "调查", "research", "verify", "validate", "check docs", "look up". This skill should be consulted proactively — even if the user doesn't explicitly ask for research, any code touching external dependencies needs verification first. Training data is frequently wrong about API signatures and versions; a 2-minute search prevents hours of debugging.
user-invocable: true
allowed-tools: WebSearch, WebFetch, Read, Grep
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

### 4. Mark Unverified Claims

If web search is unavailable or the official docs don't cover your specific question:
- Mark the claim as `UNVERIFIED` in a code comment
- Note what you searched for and what you found (or didn't find)
- Escalate to the user if the unverified claim is critical to the task

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
- `web-research-gate.sh` blocks Edit/Write operations containing SDK imports, version claims, or API signatures unless a web research flag was set within the configured TTL (currently 3 minutes; adjustable in the gate script's `THRESHOLD` variable)
- `post-web-research-flag.sh` automatically sets this flag after WebSearch/WebFetch completes
- `user-prompt-submit.sh` injects the research protocol reminder when research-intent keywords are detected

These hooks are a safety net — this skill provides the proactive workflow to follow so you rarely hit the gate.

> **Single source of truth**: Research-intent keywords are defined in `user-prompt-submit.sh`. This skill's description mirrors those keywords for trigger alignment. When updating keywords, change both locations in the same commit.

## Research Scope Routing

This skill handles **light research** (1-2 questions, single-source verification, SDK/API checks). For larger investigations, route to the `autoresearch` skill.

### When to Escalate to Deep Research

- Research questions ≥ 3
- Cross-verification across ≥ 3 independent sources needed
- Architectural decision analysis (comparing multiple alternatives)
- TaskBundle `researchScope` is `"deep"`

### Light Gate Thresholds

Applied automatically within this skill's workflow (see `.mercury/gates/research-quality.yaml`):

| Rule | Threshold |
|------|-----------|
| Web search executed | Must be true |
| Source URL present | All claims must have URLs |
| UNVERIFIED marked | Unverifiable claims tagged |
| Max searches per question | 5 |
| Total search budget per task | 15 |
| SDK/API verification budget | 20 (extended) |

### Quality Checklist (self-check before declaring done)

Before completing a web-research task, verify:
- [ ] Every SDK import path confirmed against official docs
- [ ] Package version verified on npm/PyPI registry
- [ ] API method signatures match vendor documentation
- [ ] Source URLs recorded for each verified claim
- [ ] Unverifiable claims explicitly marked UNVERIFIED
