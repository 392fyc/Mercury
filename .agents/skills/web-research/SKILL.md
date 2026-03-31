---
name: web-research
description: |
  Mercury's mandatory web research protocol for verifying external SDK/API/CLI behavior before writing code. Use this skill whenever the task involves importing external packages, referencing API signatures, claiming package versions, using CLI flags, or integrating with third-party tools. Also use when the user says "研究", "验证", "审查", "查阅", "核实", "调查", "research", "verify", "validate", "check docs", "look up". This skill should be consulted proactively, even if the user does not explicitly ask for research. Any code touching external dependencies needs verification first.
---

# Web Research Protocol

Mercury enforces a strict rule: never guess SDK, API, or CLI behavior from training data. This skill provides the structured research workflow to follow before writing code that depends on external tools or libraries.

This protocol exists because stale signatures, deprecated methods, and incorrect version numbers cause avoidable debugging churn. A short official-doc search is cheaper than recovering from wrong assumptions.

## When This Protocol Applies

Research is required before writing code that:
- imports an external SDK
- references an API method signature or constructor
- claims a specific package version or compatibility
- uses CLI flags or command syntax
- references environment variables or configuration keys from external tools

## Research Workflow

### 1. Identify Claims to Verify

Before writing, list every external dependency claim in your planned code:
- package name and version
- import path
- method signatures
- configuration keys or values
- CLI command syntax

### 2. Search Official Sources

For each claim, verify against the vendor's official documentation in this priority order:

1. official docs site
2. npm or PyPI registry
3. official blog posts or changelogs
4. official GitHub README as a supplement

Not sufficient on their own:
- source code without docs
- Stack Overflow
- third-party blogs
- memory snippets

### 3. Record Evidence

When you find the authoritative answer, record:
- the exact URL
- the version or date when available
- the exact API signature or behavior confirmed

### 4. Mark Unverified Claims

If web search is unavailable or the official docs do not cover the question:
- mark the claim as `UNVERIFIED`
- note what you searched for
- escalate if the missing fact is critical

## Example

Before writing:

```typescript
import { query } from "@anthropic-ai/claude-agent-sdk";
```

Search for:
- package existence on npm
- current published version
- `query()` function signature
- import path correctness

Then proceed only with verified signatures.

## Integration with Enforcement

Claude uses hooks as a safety net. Codex on Windows cannot rely on that path, so enforcement lives in:
- `.codex/config.toml`
- `.codex/rules/`
- repo skills such as `web-research` and `codex-git-guard`
- repo scripts under `scripts/codex/`

These mechanisms are the backup. This skill remains the proactive workflow.

## Research Scope Routing

This skill handles light research (1-2 questions, single-source verification, SDK/API checks). For larger investigations, route to `deep-research`.

### When to Escalate to Deep Research

- research questions >= 3
- cross-verification across >= 3 independent sources
- architectural decision analysis
- TaskBundle `researchScope == "deep"`

### Light Gate Thresholds

Applied automatically within this skill's workflow:

| Rule | Threshold |
|------|-----------|
| Web search executed | Must be true |
| Source URL present | All claims must have URLs |
| `UNVERIFIED` marked | Unverifiable claims tagged |
| Max searches per question | 5 |
| Total search budget per task | 15 |
| SDK/API verification budget | 20 |

### Quality Checklist

Before declaring research done, verify:
- every SDK import path confirmed against official docs
- package version verified on npm or PyPI
- API method signatures match vendor documentation
- source URLs recorded for each verified claim
- unverifiable claims explicitly marked `UNVERIFIED`
