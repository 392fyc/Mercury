---
name: web-research
description: |
  Mercury's mandatory web research protocol for verifying external SDK/API/CLI behavior before writing code. **Use proactively whenever the task involves importing external packages, referencing API signatures, claiming versions, using CLI flags, or integrating third-party tools — even if the user doesn't explicitly ask.** For ≥3-question deep investigations, use autoresearch instead. Triggers: '研究', '验证', '审查', '查阅', 'research', 'verify', 'validate', 'check docs', 'look up'. Training data is frequently wrong about API signatures and versions; a 2-minute search prevents hours of debugging.
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

## 强制是怎么落地的（Codex 上与 Claude Code 上不同）

> ⚠️ **在 Codex 上没有任何 hook 在守这条规则。** 下面写清楚哪些还在、哪些已经失效，
> 免得你以为有个网兜着而放松自觉。

**Claude Code 上**（历史形态，仍然有效）：
- `web-research-gate.sh` 在 Edit/Write 含 SDK import、版本声明或 API 签名时阻断，除非在 TTL 内设过研究标记；
- `post-web-research-flag.sh` 在 WebSearch/WebFetch 完成后自动设那个标记。

**Codex 上这两条都不生效，而且不是配置疏漏、是没法修的**：官方明文 hosted tool
（`web_search` 这类）**不走本地 hook 路径**，所以 PreToolUse/PostToolUse 根本收不到它的事件，
`post-web-research-flag.sh` 永远不会被调用，`web-research-gate.sh` 依赖的时间戳也就永远不会被刷新。
两个脚本在 `.codex/hooks.json` 里**从未注册**，这是有意的 —— 注册了也只会制造「有人在守」的假象。

**所以 Codex 上唯一的强制层是指令层**：`.codex/config.toml` 的 `developer_instructions`
第 1–5 条（写 SDK/API 代码前必须先查官方文档、交叉核对 npm/PyPI、GitHub 源码不算数、
附来源 URL、查不到就标 UNVERIFIED）与第 11 条。指令层没有阻断能力，**它只能提醒，不能拦你**。

结论很直白：**这条规则在 Codex 上完全靠自觉**。本 skill 描述的主动流程不再是「少撞门」的便利，
而是唯一的执行路径。

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
