# Research: Token Management & Model Tiering (Issue #198)

**Issue**: #198 — Token management & model tiering: route tasks to the cheapest adequate model/agent  
**Branch**: research/issue-198-token-tiering  
**Started**: 2026-04-08  
**Status**: COMPLETE (all 5 questions answered, gate passed)

---

## Research Questions

- [x] Q1: Does Claude Code agent frontmatter support a `model:` field to override the model at sub-agent dispatch time?
- [x] Q2: Does the Anthropic Agent SDK support per-invocation model override for sub-agents?
- [x] Q3: Is there documented cost/latency profile for the codex plugin in Claude Code?
- [x] Q4: Are there existing skills/tools for model routing / cost-tiering in public registries?
- [x] Q5: What fields does `.claude/agents/*.md` frontmatter currently support?

---

## Q1 + Q5: Claude Code Agent Frontmatter — `model:` Field Support

**Verdict: FULLY SUPPORTED AND OFFICIALLY DOCUMENTED**

Source: [Create custom subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents) (verified 2026-04-08)

### All Supported Frontmatter Fields

| Field | Required | Description |
|---|---|---|
| `name` | Yes | Unique identifier, lowercase letters and hyphens |
| `description` | Yes | When Claude should delegate to this subagent |
| `tools` | No | Allowlist of tools; inherits all if omitted |
| `disallowedTools` | No | Denylist of tools |
| `model` | No | Model to use — accepts aliases or full IDs; defaults to `inherit` |
| `permissionMode` | No | `default`, `acceptEdits`, `auto`, `dontAsk`, `bypassPermissions`, `plan` |
| `maxTurns` | No | Max agentic turns before stopping |
| `skills` | No | Skills to inject at startup |
| `mcpServers` | No | MCP servers available to this subagent |
| `hooks` | No | Lifecycle hooks scoped to this subagent |
| `memory` | No | `user`, `project`, or `local` |
| `background` | No | `true` to always run as background task |
| `effort` | No | `low`, `medium`, `high` (Sonnet + Opus 4.6), or `max` (**Opus 4.6 only**) |
| `isolation` | No | `worktree` for temporary git worktree isolation |
| `color` | No | Display color for the agent |
| `initialPrompt` | No | Auto-submitted first user turn (for main session agents) |

There is **no `minModel:` field** in the spec. The only model-related field is `model:`.

### `model:` Field Accepted Values

- Short aliases: `sonnet`, `opus`, `haiku`
- Full model IDs: e.g. `claude-opus-4-6`, `claude-sonnet-4-6`
- `inherit` — use the same model as the main conversation (default when omitted)

### Exact Syntax Example

```yaml
---
name: code-reviewer
description: Reviews code for quality and best practices
tools: Read, Glob, Grep
model: sonnet
---
```

### Model Resolution Priority Order (highest first)

1. `CLAUDE_CODE_SUBAGENT_MODEL` environment variable (global override for all subagents)
2. Per-invocation `model` parameter passed by the orchestrator (LLM) at Agent tool call time
3. `model` field in the subagent's `.claude/agents/*.md` frontmatter
4. Main conversation's model (fallback / inherit)

**Source**: Verbatim from [code.claude.com/docs/en/sub-agents](https://code.claude.com/docs/en/sub-agents) lines 255-260 (verified Round 3).  
**Implication**: Frontmatter `model:` is NOT a hard lock. The main LLM can override it at call time (priority #2). GitHub Issue [anthropics/claude-code#32732](https://github.com/anthropics/claude-code/issues/32732) requesting non-overridable frontmatter was closed NOT_PLANNED (April 7, 2026) — current behavior (LLM-override possible) is permanent.  
**Env var second-source**: Confirmed from [code.claude.com/docs/en/model-config](https://code.claude.com/docs/en/model-config#environment-variables) and GitHub Issue [#10993](https://github.com/anthropics/claude-code/issues/10993) (empirically: env var always wins even when frontmatter is set).

### Notable: Built-in Explore Agent

The built-in **Explore** subagent is already pinned to Haiku by default, explicitly for cost and latency reasons. The docs cite "Control costs by routing tasks to faster, cheaper models like Haiku" as a first-class use case for the subagents system.

Source: [Create custom subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents)  
Source: [Claude Code Frontmatter Schemas — MCP Market](https://mcpmarket.com/tools/skills/claude-code-frontmatter-schemas)

---

## Q2: Agent SDK Per-Invocation Model Override

**Verdict: FULLY SUPPORTED in both Python and TypeScript**

### Package Names (important naming clarification)

- Python: `claude-agent-sdk` (PyPI v0.1.56, April 4 2026) — import: `from claude_agent_sdk import ...`
- TypeScript: `@anthropic-ai/claude-agent-sdk` (npm v0.2.92, April 4 2026)
- **NOT** `@anthropic-ai/sdk` — that is the low-level Client SDK (direct Messages API), a separate package

Source: [claude-agent-sdk on PyPI](https://pypi.org/project/claude-agent-sdk/)  
Source: [@anthropic-ai/claude-agent-sdk on npm](https://www.npmjs.com/package/@anthropic-ai/claude-agent-sdk)

### API: Top-Level Model (`ClaudeAgentOptions`)

Controls the main orchestrator model:

```python
@dataclass
class ClaudeAgentOptions:
    model: str | None = None           # main orchestrator model
    fallback_model: str | None = None  # fallback model (field confirmed; trigger condition UNVERIFIED)
    # ... other fields
```

Source: [claude-agent-sdk-python types.py — GitHub](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py)

**Note (Round 3)**: `fallback_model` field existence is CONFIRMED in the Python source. The specific trigger condition (rate limit vs. any error) is UNVERIFIED — not documented on PyPI or in the quick reference docs.

### API: Per-Sub-Agent Model (`AgentDefinition`)

```python
@dataclass
class AgentDefinition:
    description: str            # required
    prompt: str                 # required
    tools: list[str] | None = None
    model: str | None = None    # "sonnet", "opus", "haiku", "inherit", or full ID
    skills: list[str] | None = None
    memory: Literal["user", "project", "local"] | None = None
    mcpServers: list[str | dict[str, Any]] | None = None
```

### TypeScript `AgentDefinition` Interface

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `description` | `string` | Yes | When to use this agent |
| `prompt` | `string` | Yes | System prompt |
| `tools` | `string[]` | No | Allowed tools |
| `model` | `'sonnet' \| 'opus' \| 'haiku' \| 'inherit'` | No | Model override |
| `skills` | `string[]` | No | Available skills |

### Key Constraint

Sub-agents **cannot spawn their own sub-agents**. The `Agent` tool must not appear in a subagent's `tools` array — this is a hard constraint for nested dispatch design.

Source: [Subagents in the SDK — Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/subagents)  
Source: [Agent SDK overview — Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/overview)  
Source: [Building agents with the Claude Agent SDK — Anthropic Engineering](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)

---

## Q3: Codex Plugin (`codex-plugin-cc`) Cost/Latency Profile

**Verdict: NOT ZERO-COST — incurs OpenAI API costs, but practically negligible (~¥10/month) relative to the Claude subscription in use. Effective cost ratio <1.5%. Adds latency through sequential cross-provider execution.**

**User-verified actual spend**: ~¥10 RMB/month observed in practice. This makes Codex a **viable offload lane for second-opinion tasks**, not just dual-verify.

### What It Is

`codex-plugin-cc` is an official OpenAI plugin for Claude Code at [github.com/openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc). It runs the Codex CLI as a local MCP server. The `mcp__codex__codex` MCP tool and `codex:codex-rescue` skill refer to this same integration.

### Cost Reality

- Requires either a ChatGPT subscription (free plan or paid) OR an OpenAI API key
- Every invocation makes a real OpenAI API call
- **gpt-5.4** ($2.50/$15 per MTok input/output) may be used by default if no `--model` flag is passed, because Codex auto-selects its model
- Available models: `gpt-5.4-mini`, `gpt-5.3-codex-spark`, `gpt-5.4`
- Dual-billing: one Anthropic-side token charge (Claude) + one OpenAI-side charge (Codex) per invocation
- README explicitly: "Usage will contribute to your Codex usage limits"

**The "free-tier" characterization in Issue #198 is technically incorrect** — there is an OpenAI cost per invocation. However, user-observed spend (~¥10/month) confirms the practical impact is negligible (<1.5% of the subscription cost). For tiering policy purposes, Codex is treated as **near-zero-cost** and eligible for broader offload use than originally scoped.

### Latency Reality

- Background execution (`--background`) is recommended for multi-file reviews because sequential cross-provider execution is slow
- Architecture: Claude → Codex CLI (local MCP server) → OpenAI HTTP API → response → Claude continues
- Estimated 2-3x wall-clock time increase vs inline Opus solve for tasks that would otherwise be instant

### Resolved (Round 2)

- **Default model**: `gpt-5.4` — per config-basic example in official Codex developer docs ([developers.openai.com/codex/config-basic](https://developers.openai.com/codex/config-basic)); the CLI models page confirms "defaults to a recommended model" without naming it explicitly. Best available evidence is `gpt-5.4`.
- **gpt-5.4-mini ChatGPT Free**: YES free via ChatGPT consumer product (web/app, March 2026) — NOT free via API key. API pricing UNVERIFIED (OpenAI pricing page returned HTTP 403). For Mercury's API-based usage, `gpt-5.4-mini` incurs OpenAI API costs.

Source: [OpenAI Codex Config Basics](https://developers.openai.com/codex/config-basic)  
Source: [9to5Google: GPT-5.4 mini free for ChatGPT users (March 2026)](https://9to5google.com/2026/03/17/openai-gpt-5-4-mini-nano-models/)

Source: [GitHub — openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc)  
Source: [Introducing Codex Plugin for Claude Code — OpenAI Community](https://community.openai.com/t/introducing-codex-plugin-for-claude-code/1378186)  
Source: [OpenAI Releases Official Claude Code Plugin — SmartScope](https://smartscope.blog/en/blog/codex-plugin-cc-openai-claude-code-2026/)

---

## Q4: Existing Model-Routing / Cost-Tiering Skills

**Verdict: No specialized third-party skill needed. Native Claude Code support is the right path.**

### First-Party (Native Claude Code)

The primary building blocks are already present:

1. **`model:` frontmatter field** in `.claude/agents/*.md` — per-agent model assignment, zero dependencies
2. **`CLAUDE_CODE_SUBAGENT_MODEL` env var** — global override; enables "all subagents use Haiku" infrastructure-level pattern without touching agent files
3. **`effort:` frontmatter field** — `low`/`medium`/`high`/`max` (2D cost/quality surface with model selection)
4. **Built-in Explore agent** — already pinned to Haiku as a native example

Source: [Model configuration — Claude Code Docs](https://code.claude.com/docs/en/model-config)

### Third-Party Skills (all have significant caveats)

| Skill | Where | Mechanism | Applicable to Mercury? |
|-------|-------|-----------|----------------------|
| [Claude Model Selection](https://mcpmarket.com/tools/skills/claude-model-selection) | mcpmarket.com | Decision tree (instructional, not automated) | Partial — useful as reference taxonomy |
| [ClawRouter](https://mcpmarket.com/tools/skills/clawrouter) | mcpmarket.com | Complexity classifier → model recommendation; claims 67% cost savings | No — requires `openclaw` plugin system, not native CC |
| [LLM Gateway Routing](https://mcpmarket.com/tools/skills/llm-gateway-routing) | mcpmarket.com | OpenRouter/LiteLLM proxy setup guidance | No — requires external proxy infrastructure |
| [Ultrawork](https://mcpmarket.com/tools/skills/ultrawork-multi-agent-orchestrator) | mcpmarket.com | Parallel orchestration with model downgrade for simple tasks | UNVERIFIED implementation depth |
| [awesome-claude-code-toolkit](https://github.com/rohitg00/awesome-claude-code-toolkit) | GitHub | `myclaude` multi-agent routing + `claude-cost-optimizer` CLAUDE.md templates | Partial — CLAUDE.md templates worth reviewing |

### Conclusion for Q4

No third-party skill provides automated model routing that is both native to Claude Code and operational without additional infrastructure. The native approach (`model:` frontmatter + env var) is superior for Mercury's use case.

---

## Summary for ADR

### Mercury current state (Round 4 finding)

All 6 agents in `.claude/agents/` currently use `model: inherit`, no `effort:` fields set. **Clean headroom** — tiering implementation has zero existing config conflicts.

Agents: `acceptance.md`, `critic.md`, `design.md`, `dev.md`, `main.md`, `research.md` — all `model: inherit`.

### What works natively (zero new dependencies)

| Mechanism | Scope | Priority |
|-----------|-------|----------|
| `model: haiku/sonnet/opus` in `.claude/agents/*.md` | Per-agent | Medium (frontmatter) |
| `CLAUDE_CODE_SUBAGENT_MODEL` env var | All subagents globally | Highest |
| `effort: low/medium/high` in frontmatter | Per-agent; Sonnet + Opus (`max` Opus-only); Haiku UNVERIFIED | Low |
| `CLAUDE_CODE_EFFORT_LEVEL` env var | Global effort override (beats everything including frontmatter) | Highest |
| `AgentDefinition.model` in Agent SDK | Per-invocation (SDK apps only) | Medium |

Source: [code.claude.com/docs/en/model-config](https://code.claude.com/docs/en/model-config)

### Codex plugin reassessment (Final — user decision)

**Final verdict**: Codex replaces Haiku tier. gpt-5.4 has strong code performance; ~¥10/month spend is negligible. **Haiku tier is dropped from Mercury's tiering policy.**

Revised Codex scope:
- **Mechanical code tasks** (boilerplate edits, path rewrites, Argus-finding fixes, PR body templating, skim reads) — replaces Haiku
- **Second-opinion code audit** — `dual-verify` primary use case
- **Rescue / unstuck scenarios** — `codex:rescue`

**Architectural constraint (important)**:  
Haiku was controlled via `model: haiku` in agent frontmatter (sub-agent invocation path).  
Codex is invoked via `mcp__codex__codex` tool call — a **different invocation path**.  
These cannot be directly substituted at the frontmatter level. Task taxonomy must specify:
- Which tasks use Codex via explicit tool invocation
- Which tasks remain as Sonnet sub-agents (tool-heavy tasks needing CC-native tool access: git ops, status polling, metrics writes)

**Latency trade-off acknowledged**: Codex adds 2-3x wall-clock time vs Haiku. For non-blocking code tasks this is acceptable; for tight polling loops (`cron`-based PR status checks) Sonnet remains preferred.

**Final tier table**:

| Task type | Model/Runtime | Invocation path |
|-----------|--------------|-----------------|
| Architectural decisions, deep research synthesis | Opus 4.6 | Main session |
| Dev implementation, PR fix iterations, acceptance review | Sonnet 4.6 | `model: sonnet` frontmatter |
| Mechanical code edits, boilerplate, path rewrites | Codex (gpt-5.4) | `mcp__codex__codex` tool call |
| Second-opinion code audit | Codex (gpt-5.4) | `dual-verify` skill |
| Rescue / unstuck | Codex (gpt-5.4) | `codex:rescue` skill |
| Git ops, cron polling, metrics writes (latency-sensitive) | Sonnet 4.6 | `model: sonnet` frontmatter |

### No `minModel:` field exists

The concept from Issue #198's proposed approach (item 3) must be redesigned. The field name is `model:`, not `minModel:`. The recommended approach:
- Use `model: sonnet` or `model: haiku` in agent frontmatter (hard assignment, not "minimum")
- Document the taxonomy and default assignments in `.mercury/config/model-tiering.yaml` as a policy doc
- The main agent reads this policy as a CLAUDE.md or skill-level instruction — there is no programmatic enforcement in the harness

---

## Sources Index

1. [Create custom subagents — Claude Code Docs](https://code.claude.com/docs/en/sub-agents)
2. [Model configuration — Claude Code Docs](https://code.claude.com/docs/en/model-config)
3. [Agent SDK overview — Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/overview)
4. [Agent SDK reference — Python](https://platform.claude.com/docs/en/agent-sdk/python)
5. [Agent SDK reference — TypeScript](https://platform.claude.com/docs/en/agent-sdk/typescript)
6. [Subagents in the SDK — Claude API Docs](https://platform.claude.com/docs/en/agent-sdk/subagents)
7. [claude-agent-sdk-python types.py — GitHub](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/types.py)
8. [claude-agent-sdk on PyPI](https://pypi.org/project/claude-agent-sdk/)
9. [@anthropic-ai/claude-agent-sdk on npm](https://www.npmjs.com/package/@anthropic-ai/claude-agent-sdk)
10. [Building agents with Claude Agent SDK — Anthropic Engineering](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)
11. [GitHub — openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc)
12. [Introducing Codex Plugin — OpenAI Community](https://community.openai.com/t/introducing-codex-plugin-for-claude-code/1378186)
13. [OpenAI Releases Claude Code Plugin — SmartScope](https://smartscope.blog/en/blog/codex-plugin-cc-openai-claude-code-2026/)
14. [Claude Code Frontmatter Schemas — MCP Market](https://mcpmarket.com/tools/skills/claude-code-frontmatter-schemas)
15. [awesome-claude-code-toolkit — GitHub](https://github.com/rohitg00/awesome-claude-code-toolkit)

---

## Contradictions / Uncertainties

1. **"Free-tier codex"** claim in Issue #198 → CORRECTED: not free via API, incurs OpenAI costs. `gpt-5.4-mini` is free only via ChatGPT consumer product, not API.
2. **`minModel:` field** proposed in Issue #198 → CORRECTED: field is `model:`, no minimum-model concept exists.
3. **codex default model** → RESOLVED (Round 2): best available evidence is `gpt-5.4` per Codex config-basic example.
4. **Explore subagent Haiku pinning** → CONFIRMED from 2 independent sources: official docs + GitHub issue [anthropics/claude-code#29768](https://github.com/anthropics/claude-code/issues/29768).
