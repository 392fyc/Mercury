# Agent Team Orchestration Feasibility Study — "Main = Director / Side Lanes = Dev Teams" Architecture

**Mission**: Mercury Issue #319 (research dimension of #318 architecture reframe)
**Lane**: `main`
**Date**: 2026-04-26
**Status**: Research complete — recommendation **CONDITIONAL_GO**
**Researcher**: Research subagent (S76 session)

---

## Path conventions (read this first)

This document follows the same path convention established in `multi-lane-protocol-2026-04-25.md`. `<encoded_cwd>` is the path-encoded form of the project working directory; computed by Claude Code at session start; do not hardcode (discover via `ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/" | grep -i mercury`).

| Shorthand | Resolves to | Status |
|-----------|-------------|--------|
| `memory/<file>` | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/<file>` (Claude Code per-project user-memory) | NOT in repo — gitignored by design; user-memory artifact, not a repo file |
| `adapters/<name>/` | `${REPO_ROOT}/adapters/<name>/` | In repo |
| `archive/packages/<name>/src/` | `${REPO_ROOT}/archive/packages/<name>/src/` | In repo (archived) |
| `.mercury/docs/research/` | `${REPO_ROOT}/.mercury/docs/research/` | In repo |
| `.mercury/state/` | `${REPO_ROOT}/.mercury/state/` | In repo |
| `.tmp/...` | repo tmp dir (gitignored but local repo) | repo-local, not committed |
| `scripts/...` | actual repo scripts | in repo |

All LOC counts in this document are obtained via `wc -l` on the repository checkout at commit `c4172c8` (origin/develop tip on 2026-04-26).

## Mercury terminology (read before non-Mercury reviewers)

This document uses several Mercury-internal terms defined here for external readers:

- **Lane** — a parallel work stream pinned to a git branch prefix and a lane-tag (e.g. `lane:main`, `lane:side-multi-lane`). One lane = one autonomous Claude Code session line. Up to 5 active lanes per the v0.1 hard cap.
- **Lane Protocol Rules 1–8** — the 8 numbered rules in `memory/feedback_lane_protocol.md` v0.2 (Issue claim, branch prefix, tmp isolation, main-lane spec authority, per-lane state, LANES.md registry, append-only shared index, autonomous chain). See `.mercury/docs/research/multi-lane-protocol-2026-04-25.md` for full text.
- **Probe-after-write (Rule 1.1)** — re-query GitHub Issue labels immediately after `gh issue edit --add-label` to detect concurrent claim races.
- **Autonomous chain (Rule 8 v0.2)** — side lane self-driven Issue progression without per-step user approval.
- **SoT 10-state task machine** — pre-pivot Source-of-Truth task lifecycle: `drafted → dispatched → in_progress → implementation_done → main_review → acceptance → verified → closed` plus `blocked`, `cancelled`. Lives in `archive/packages/orchestrator/src/task-manager.ts`.
- **AD-001 / AD-002 / etc.** — Architecture Decision IDs from `archive/docs/codex-main-agent-roadmap.md` (e.g. AD-001 "don't hardcode Main Agent", AD-002 "MCP-first transport").
- **Director / Dev Team** — the architecture metaphor proposed in Issue #318: main lane = Director (横断管理 + 用户接口), side lanes = Dev Teams (autonomous task lines).

---

## Executive Summary

**Verdict: CONDITIONAL_GO**

The "main = Director / side lanes = Dev Teams" architecture reframe is feasible and directionally correct. The lane protocol v0.1 + Mercury's existing adapter set already deliver the bulk of the metaphor; the remaining gap is 5 small/medium coordination modules totaling ~6–11 person-days of work (see Dim 1.3 gap table). Anthropic Agent Teams is **not** required and should not block Mercury's path forward — Mercury's lane-based model is intentionally Agent Teams-independent (see R1 in Risk Register), with Agent Teams reserved as a future replacement once it reaches GA with session resumption.

The 4 GO-promotion conditions for v1 (statusline 5h observability deployed; cross-lane status aggregator producing valid JSON; ≥1 lane spawned via `lane-spawn.sh` end-to-end; `mercury-channel-router` MAX_SESS bumped to 5) are all small-effort prerequisites achievable inside Phase A+B of the phased PR plan (Recommendation §).

Archive packages contain significant reusable design patterns — particularly the `NotificationBroadcaster`, `SessionPersistence`, role YAML definitions, and the SoT task state machine — but the full pre-pivot orchestrator (4414 LOC `orchestrator.ts` + 1817 LOC `task-manager.ts`, TypeScript) is not directly portable to the current shell/Node.js harness model and should be treated as **reference design only**. The strongest external framework candidates are **Anthropic Agent Teams** (monitor for GA + session-resumption fix; do not adopt now) and **LangGraph** (DEFER — heavy Python dependency vs Mercury's modular-harness philosophy). All other frameworks (CrewAI, Microsoft Agent Framework, OpenAI Swarm/Agents SDK, MetaGPT) are REJECTED for the same modular-harness reason.

---

## Dim 1 — Feasibility + Implementation Effort

### 1.1 Metaphor → Module Mapping

The "main = Director, side = Dev Teams" metaphor maps as follows:

| Metaphor Role | Mercury Implementation | Status |
|---------------|------------------------|--------|
| Director (main lane) | Current main Claude Code session; reads `memory/LANES.md`; issues spec/direction | Exists (convention only, no tooling) |
| Dev Team (side lane) | Autonomous Claude Code session on `lane/<short>/...` branch; Rule 8 v0.2 autonomous chain | Exists (protocol v0.1) |
| President's global window | `adapters/mercury-channel-router` + `mercury-channel-client` Telegram bidirectional | Shipped (PR #295) |
| Cross-lane status board | Aggregator that polls or receives status from all lanes and surfaces to Director | **MISSING** |
| Director → Lane dispatch | Protocol for main lane to spawn/task a side lane programmatically | **MISSING** |
| Lane → Director auto-report | Side lane proactively pushes progress/blockers to main without user prompt | **MISSING** |
| Lane lifecycle coordinator | Spawn, monitor, tear-down side lanes (incl. stale sweep from Rule 3.1) | **MISSING** (scripts/lane-sweep.sh planned, not built) |
| Director command surface | Main lane receives Director-level commands from Telegram and routes to correct lane | Partial (router has `/cancel`, `/list`, routing by label; but no lane-aware dispatch) |

### 1.2 Existing Reusable Modules

| Module | Location | LOC (verified) | Reuse Assessment |
|--------|----------|----------------|-----------------|
| `mercury-channel-router` | `adapters/mercury-channel-router/router.cjs` | 220 | Direct reuse — IPC auth, session registry, Telegram bot, SSE inbox, `/notify`, `/permission-request` all production-quality |
| `mercury-channel-client` | `adapters/mercury-channel-client/channel.cjs` | 231 | Direct reuse — MCP server per-session, SSE inbox consumer, permission relay |
| `mercury-notify` | `adapters/mercury-notify/notify.cjs` | 38 | Direct reuse — thin HTTP client for outbound notifications |
| `mercury-loop-detector` | `adapters/mercury-loop-detector/hook.cjs` | 174 | Direct reuse — sliding window stall detection, configurable thresholds |
| Lane protocol v0.1 | `memory/feedback_lane_protocol.md` (user-memory) | N/A (doc) | Direct reuse — Rule 1–8 + probe-after-write + autonomous chain |
| mem0 layer | `~/.claude/scripts/mem0_bridge.py` | External | Direct reuse — cross-session memory already operational post-#252 |
| `SessionPersistence` (archive) | `archive/packages/orchestrator/src/session-persistence.ts` | 43 | Pattern reuse — atomic write pattern portable to shell/JSON |
| `NotificationBroadcaster` (archive) | `archive/packages/orchestrator/src/notification-broadcaster.ts` | 94 | Pattern reuse — multi-channel fan-out already abstracted |
| Role YAMLs (archive) | `archive/roles/{main,dev,acceptance,critic,research,design}.yaml` | ~60 each | Direct reuse — role boundaries precisely defined, map cleanly to `.claude/agents/*.md` |

### 1.3 Missing Module Gap Table

| Missing Module | Description | Effort | Priority |
|----------------|-------------|--------|----------|
| **Cross-lane status aggregator** | Polls all active lanes' GitHub Issues + last-commit timestamps; produces unified `status.json` surfaced to Director via Telegram or `.mercury/state/lane-status.json` | Medium (1–3d) | P1 |
| **Main → side lane dispatch** | Script or skill: main lane creates new side lane (files Issue, creates branch, writes handoff, updates `LANES.md`) in one command; currently fully manual | Medium (1–3d) | P1 |
| **Side lane auto-report** | Side lane periodically (or on milestone) pushes a progress summary to the router's `/notify` endpoint; Director receives without polling | Small (<1d) | P2 |
| **Lane lifecycle coordinator** | `scripts/lane-spawn.sh`, `scripts/lane-close.sh`, `scripts/lane-sweep.sh` — spawn/teardown/stale-prune; Rule 3.1+3.2 scripts planned but not built | Small (<1d each, ~2d total) | P1 |
| **Director command surface (lane-aware)** | Router extension: inbound Telegram commands can target a specific lane (`@lane-name <cmd>`); currently routes only to `activeId` session | Small (<1d) | P2 |

**Total effort estimate**: 6–11 person-days across 5 modules, all Small–Medium. New scripts target the ≤200 LOC adapter cap; existing adapters cluster around the cap (router 220, channel-client 231) — both edge cases predate the cap and are grandfathered per CLAUDE.md "DO NOT — adapters exceeding 200 lines"; new modules must respect 200. No Anthropic official agent teams feature is required for this architecture — it augments but does not replace the lane protocol.

### 1.4 Does This Require Anthropic Agent Teams?

**No, but Agent Teams could replace the side-lane spawning mechanism if it reaches GA with session resumption.** The current architecture uses separate Claude Code processes (one per terminal/tmux pane), which is the multi-lane protocol v0.1 model. Agent Teams is an alternative coordination layer — with different trade-offs (see Dim 3). Mercury's lane model is cross-session-persistent; Agent Teams is per-session (no `/resume` for in-process teammates as of 2026-04-26). For the Director/DevTeam metaphor at Mercury's scale (1 user, ≤5 lanes), the lane protocol plus the 5 missing modules above is the lower-risk path.

---

## Dim 2 — Pre-pivot Mercury Orchestrator Legacy Salvage

### 2.1 Archive Package Inventory (verified by direct file inspection)

#### `archive/packages/orchestrator/src/` — TypeScript Multi-Session Orchestrator

19 source files. Key modules inspected:

- **`orchestrator.ts`** (4414 LOC, the largest single file in the archive): Full orchestrator class. Imports: `EventBus`, `AgentRegistry`, `TaskManager`, `SkillRegistry`, `SkillCapturer`, `SessionPersistence`, `TranscriptPersistence`, `NotificationBroadcaster`, `CallbackQueue`. Contains: approval control plane, session token checkpoint (70% threshold → handoff), exponential backoff retry, role-slot-key session mapping, the SoT 10-state task machine (`drafted/dispatched/in_progress/implementation_done/main_review/acceptance/verified/closed/blocked/cancelled`), KB context injection, orphan recovery queue. **Verdict: Reference design only** — the full TypeScript orchestrator cannot be mounted into the current shell/Node.js harness model without reimplementing the entire GUI+RPC transport layer it depends on.

- **`task-manager.ts`** (1817 LOC): State machine (10 states as enumerated above for `orchestrator.ts`), prompt builders per role (`buildDevPrompt`, `buildResearchPrompt`, `buildAcceptancePrompt`, etc.), task/issue persistence. **Verdict: High reference value** — the state machine transitions and prompt builder pattern are directly usable as design spec for `.mercury/templates/` dispatch templates.

- **`session-persistence.ts`** (43 LOC): Atomic write (tmp + rename) for `sessions.json`. **Verdict: Directly portable** — the pattern is language-agnostic and maps to any shell script or Node.js script writing `.mercury/state/*.json`.

- **`notification-broadcaster.ts`** (94 LOC): Multi-channel fan-out with named channels, `broadcast()`, `wrapTransport()`. **Verdict: Pattern directly implemented** — `mercury-channel-router`'s session registry + `/notify` endpoint already achieves the same fan-out. No code salvage needed; design validated.

- **`agent-registry.ts`** (66 LOC): Maps `claude` → `ClaudeAdapter`, `codex` → `CodexMCPAdapter`, `opencode` → `OpencodeAdapter`, `gemini` → `GeminiAdapter`. **Verdict: Reference only** — Mercury no longer builds a multi-adapter registry; each lane is a single Claude Code session.

- **`callback-queue.ts`, `rpc-transport.ts`, `rtk-wrapper.ts`**: GUI/IPC transport layer. **Verdict: Fully abandoned** — depends on Tauri IPC which is archived.

#### `archive/packages/sdk-adapters/src/` — Multi-SDK Adapters

6 files. Key files inspected:

- **`claude-adapter.ts`** (856 LOC, largest file in `sdk-adapters/`; smaller than `orchestrator/orchestrator.ts` 4414 and `orchestrator/task-manager.ts` 1817): Full `@anthropic-ai/claude-agent-sdk` integration. Session start/resume/handoff, streaming events (`message_start`, `message_delta`, `content_block_*`), slash command interception (50+ commands), approval mutex, model switching. **Verdict: High reference value** — the streaming event parsing and `handoffSession()` pattern are reusable as reference for any future Session Continuity module. The slash command list (lines 159–278) is a comprehensive catalog of Claude Code native commands.

- **`codex-mcp-adapter.ts`**: Codex MCP adapter via `codex mcp-server`. **Verdict: Reference only** — Mercury no longer targets Codex.

- **`gemini-adapter.ts`, `opencode-adapter.ts`**: Multi-model adapters. **Verdict: Abandoned** — out of scope.

#### `archive/packages/poc/src/` — Proof of Concept Tests

6 files. `session-continuity-test.ts` inspected: demonstrates `EventBus`, `ClaudeAdapter.handoffSession()`, parent session linkage. **Verdict: Reference only** — validates the session handoff design pattern, no code portability.

#### `archive/packages/gui/src/` + `src-tauri/`

Tauri+Vue desktop GUI. Binary build artifacts present (WebView2 DLLs, Rust target/). **Verdict: Fully abandoned** — DIRECTION.md §5 explicitly archives this. GUI may be restarted in Phase 6 from archive, but no salvage for current work.

#### `archive/docs/architecture-evolution-plan.md`

Gap analysis (G1–G13). G1 (context exhaustion), G2 (crash recovery), G3 (retry), G9 (auto-triage) are still open and relevant to Director/DevTeam architecture. G11/G12 (MCP server exposure) remain valid direction. **Verdict: Active reference** — gap numbering is useful for Issue tracking.

#### `archive/docs/codex-main-agent-roadmap.md`

Multi-agent adapter migration plan (App-Server → MCP). AD-001 through AD-004 still architecturally sound. Codex capability table (MCP client/server, subagents, hooks) verified 2026-03. **Verdict: Reference** — AD-001 (don't hardcode Main Agent) and AD-002 (MCP-first) remain valid principles for the Director architecture.

#### `archive/roles/*.yaml`

6 role definitions: `main`, `dev`, `acceptance`, `critic`, `research`, `design`. All already migrated to `.claude/agents/*.md` per DIRECTION.md §5. Role boundaries are production-proven:
- `main.yaml`: no code execution, delegates to dev/acceptance/critic/research/design
- `dev.yaml`: no task creation/dispatch, git scope-restricted, escalation protocol defined

**Verdict: Already salvaged** — Phase 0 migration complete. No additional salvage needed.

### 2.2 Salvage Summary Table

| Package | LOC Salvage % | Salvage Category | Selected Components |
|---------|---------------|------------------|---------------------|
| `orchestrator/` | 5% (pattern only) | Reference design | `session-persistence.ts` pattern, task state machine design, prompt builder templates |
| `sdk-adapters/claude-adapter.ts` | 10% (pattern only) | Reference design | `handoffSession()` pattern, streaming event parsing, slash command catalog |
| `sdk-adapters/codex-*` | 0% | Abandoned | — |
| `poc/` | 0% | Reference only | Session handoff test validates design |
| `gui/` | 0% | Abandoned | Phase 6 candidate |
| `archive/roles/*.yaml` | 100% | Already salvaged | All 6 roles → `.claude/agents/*.md` (Phase 0 done) |
| `archive/docs/architecture-evolution-plan.md` | Reference | Active reference | G1-G3, G9 gap IDs; AD-001/002 principles |

**Key finding**: The archive's most valuable contribution is **design validation**, not code transplant. The orchestrator's notification broadcaster, session persistence, and task state machine all proved correct — and Mercury's current adapters re-implement the equivalent patterns at near-cap sizes (router 220, channel-client 231 — both grandfathered against the strict 200 LOC cap; notify 38, loop-detector 174 well below). The archive's 4414-line `orchestrator.ts` proves the same patterns require ~20× more code when bundled with GUI/RPC transport plumbing.

---

## Dim 3 — Multi-Agent Framework Evaluation

### 3.1 Anthropic Native: Claude Code Agent Teams

**Source**: [Official docs](https://code.claude.com/docs/en/agent-teams) (fetched 2026-04-26), [TechCrunch release](https://techcrunch.com/2026/02/05/anthropic-releases-opus-4-6-with-new-agent-teams/), [MindStudio analysis](https://www.mindstudio.ai/blog/what-is-claude-code-agent-teams), [VentureBeat](https://venturebeat.com/technology/anthropics-claude-opus-4-6-brings-1m-token-context-and-agent-teams/)

**Status**: Experimental as of 2026-04-26. Requires `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`. Shipped with Claude Opus 4.6 on 2026-02-05. Requires Claude Code v2.1.32+. No GA date announced.

**Architecture**:
- Team lead = 1 Claude Code session; coordinates via shared task list + mailbox
- Teammates = separate Claude Code instances, each own context window
- Direct teammate-to-teammate messaging (unlike subagents which only report back)
- Shared task list with file-locking for atomic claim
- Hooks: `TeammateIdle`, `TaskCreated`, `TaskCompleted` (exit code 2 to block/give feedback)
- Storage: `~/.claude/teams/{team-name}/config.json`, `~/.claude/tasks/{team-name}/`

**Known Limitations** (from official docs):
- No session resumption: `/resume` and `/rewind` do NOT restore in-process teammates
- Task status can lag (teammates fail to mark complete)
- Shutdown slow (finishes current tool call first)
- One team per session — no nested teams
- Lead is fixed for team lifetime
- Split-pane mode requires tmux or iTerm2 (not supported in Windows Terminal natively)

**Stress-test evidence**: 16-agent Rust C compiler test — 100,000 LOC output, ~2,000 Claude Code sessions, ~$20,000 API cost. Functional at scale; expensive.

**Mercury fit assessment**:
- Director/DevTeam hierarchy: YES — team lead = Director, teammates = Dev Teams
- Cross-session-persistent lanes: NO — Agent Teams are per-session, no resume
- Single-user 5h quota: CONCERN — each teammate consumes quota independently
- Windows Terminal (Mercury's platform): PARTIAL — in-process mode works; split panes require tmux
- Replaces lane protocol: PARTIAL — would replace Rules 1-7 but not the mem0/handoff/autonomy chain

**Score**: Feature fit 3/5 | Mount cost 1/5 (zero LOC, native) | License N/A (platform feature) | Community 5/5 | Mercury pain relief 3/5

**Recommendation**: Monitor for GA + session-resumption fix. Do NOT block current work on it.

### 3.2 LangGraph

**Source**: [Official docs](https://langchain.com/langgraph), [GitHub](https://github.com/langchain-ai/langgraph), [LangChain docs](https://docs.langchain.com/oss/python/langgraph/overview), [Latenode guide](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langgraph-multi-agent-orchestration/)

**Status**: Production. MIT license. Python primary (JS available). ~29.8k GitHub stars (2026-04-26). Used by Klarna, Replit, Elastic. LangGraph Platform (hosted) available separately.

**Architecture**: Graph-based state machine. Nodes = agents/functions. Edges = conditional routing. Supports supervisor pattern (Director = supervisor node, workers = child nodes), parallel execution, human-in-the-loop checkpoints, persistence layer (Postgres/SQLite backends).

**Mercury fit**:
- Director/DevTeam hierarchy: YES — supervisor node pattern is exact match
- Mount cost: HIGH — requires Python runtime + LangChain dependency chain; Mercury is shell/Node.js
- Cross-session persistence: YES — built-in checkpoint/resume
- Single-user 5h quota: Neutral (LangGraph doesn't consume quota; each Claude call does)
- Modular detachability: PARTIAL — LangGraph is a framework dependency, not a thin adapter

**Score**: Feature fit 4/5 | Mount cost 2/5 (heavy Python dep) | License 5/5 (MIT) | Community 4/5 | Mercury pain relief 3/5

**Recommendation**: DEFER. Excellent framework but adds Python runtime dependency to a shell/Node.js harness. Reconsider if Mercury ever needs a proper workflow engine.

### 3.3 CrewAI

**Source**: [Official docs](https://docs.crewai.com/en/introduction), [GitHub](https://github.com/crewAIInc/crewAI), [PyPI](https://pypi.org/project/crewai/), [IBM overview](https://www.ibm.com/think/topics/crew-ai)

**Status**: Production. MIT license. Python 3.10–3.13. Fastest-growing multi-agent framework in 2026; 100,000+ certified developers. Standalone (no LangChain dependency).

**Architecture**: Crews (agent groups) + Flows (workflow control). Agents have roles, goals, backstory. Tasks have descriptions, expected output, assignee. Sequential or parallel execution modes.

**Mercury fit**:
- Director/DevTeam hierarchy: YES — crew with manager agent pattern
- Mount cost: HIGH — Python runtime required; role/task definition in Python classes (not shell-friendly)
- Single-user: Good — designed for autonomous crews
- Modular detachability: LOW — framework-level coupling

**Score**: Feature fit 3/5 | Mount cost 2/5 | License 5/5 (MIT) | Community 5/5 | Mercury pain relief 2/5

**Recommendation**: REJECT for Mercury. Python runtime + framework coupling conflicts with modular harness philosophy. CrewAI's role/task model is well-designed but Mercury already has equivalent in YAML roles + dispatch templates.

### 3.4 Microsoft Agent Framework (formerly AutoGen)

**Source**: [Microsoft Learn](https://learn.microsoft.com/en-us/agent-framework/overview/), [Foundry blog](https://devblogs.microsoft.com/foundry/introducing-microsoft-agent-framework/), [Agent Framework 1.0](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/), [Visual Studio Magazine](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx)

**Status**: Microsoft Agent Framework 1.0 shipped 2026-04-06 (production-ready). Merger of AutoGen + Semantic Kernel. AutoGen itself now in maintenance mode (no new features, critical bug fixes only). Supports .NET and Python. A2A and MCP interoperability built-in.

**Architecture**: Multi-agent group chat pattern. Agents exchange messages via a shared conversation. Supports human-in-the-loop, tool use, nested conversations. Enterprise-grade SLA commitment.

**Mercury fit**:
- Director/DevTeam hierarchy: YES — group chat with manager agent
- Mount cost: HIGH — .NET or Python runtime; Microsoft-ecosystem coupling
- MCP support: GOOD — native A2A + MCP built-in
- Modular detachability: LOW — framework-level

**Score**: Feature fit 3/5 | Mount cost 1/5 | License 4/5 (MIT) | Community 3/5 | Mercury pain relief 2/5

**Recommendation**: REJECT. Microsoft stack not appropriate for Mercury's lightweight harness model.

### 3.5 OpenAI Swarm / Agents SDK

**Source**: [GitHub openai/swarm](https://github.com/openai/swarm), [Morph guide](https://www.morphllm.com/openai-swarm), [mem0 review](https://mem0.ai/blog/openai-agents-sdk-review), [QubitTool comparison](https://qubittool.com/blog/ai-agent-framework-comparison-2026)

**Status**: Swarm deprecated March 2025, superseded by OpenAI Agents SDK (production). Swarm kept as educational reference (21k+ stars). Agents SDK adds guardrails, tracing, TypeScript support.

**The handoff pattern**: Agent returns another Agent object → framework switches active agent while preserving conversation history. Lightweight and elegant.

**Mercury fit**:
- Director/DevTeam hierarchy: YES (handoff pattern) — but OpenAI-model-centric
- Claude compatibility: POOR — designed for OpenAI models; using with Claude requires wrapper
- Mount cost: MEDIUM (if used with Claude wrappers)
- Modular detachability: GOOD (Swarm is ~700 LOC)

**Score**: Feature fit 2/5 | Mount cost 3/5 | License 4/5 (MIT) | Community 4/5 | Mercury pain relief 1/5

**Recommendation**: REJECT. OpenAI-centric; handoff pattern is useful conceptually but Mercury's lane protocol already implements equivalent.

### 3.6 MetaGPT

**Source**: [GitHub FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT), [docs](https://docs.deepwisdom.ai/main/en/guide/get_started/introduction.html), [xpay overview](https://www.xpay.sh/resources/agentic-frameworks/metagpt/)

**Status**: Production. MIT license. 64.1k GitHub stars (January 2026). MGX (MetaGPT X) launched 2025-02-19 as "world's first AI agent development team." Python.

**Architecture**: SOP-based — encodes software development standard operating procedures as agent roles (PM, Architect, Project Manager, Engineer). `Code = SOP(Team)` philosophy. Produces PRD, design, tasks, code from single-line input.

**Mercury fit**:
- Director/DevTeam hierarchy: YES — PM/Architect = Director, Engineer = Dev Team
- Mount cost: HIGH — Python runtime + full SOP framework
- Mercury overlap: HIGH — MetaGPT reimplements what Mercury already does (role dispatch, task decomposition, acceptance)
- Modular detachability: LOW

**Score**: Feature fit 3/5 | Mount cost 1/5 | License 5/5 (MIT) | Community 5/5 | Mercury pain relief 1/5

**Recommendation**: REJECT. Mercury and MetaGPT solve the same problem with different approaches. Mounting MetaGPT would replace Mercury's core value, not augment it.

### 3.7 Anthropic Multi-Agent Research System

**Source**: [Anthropic Engineering blog](https://www.anthropic.com/engineering/multi-agent-research-system) (2025-06-13), [ByteByteGo summary](https://blog.bytebytego.com/p/how-anthropic-built-a-multi-agent), [ZenML database](https://www.zenml.io/llmops-database/building-a-multi-agent-research-system-for-complex-information-tasks)

**Key findings**:
- Lead agent (Opus 4) decomposes query → spawns subagents (Sonnet 4) in parallel
- Subagents = "intelligent filters" — iteratively search, then return results to lead
- Early failure modes: spawning 50 agents for simple queries; agents distracting each other; endless web scouring
- Primary control lever: prompt engineering (not code changes)
- Claude Opus 4 lead + Claude Sonnet 4 subagents outperformed single-agent by >90%
- Improvement strongly correlated with token usage + spreading reasoning across independent context windows

**Mercury relevance**: Validates the Director/DevTeam split (Opus as Director, Sonnet as Dev Teams). Confirms that prompt engineering is the primary quality lever. Warns against over-spawning (match agent count to actual task parallelism). The "spreading reasoning across independent context windows" benefit directly applies to Mercury's multi-lane model.

### 3.8 Framework Comparison Table

| Framework | Feature Fit | Mount Cost | License | Community | Mercury Pain Relief | Total | Verdict |
|-----------|-------------|------------|---------|-----------|---------------------|-------|---------|
| Anthropic Agent Teams | 3/5 | 1/5 (native) | N/A | 5/5 | 3/5 | 12/20 | WAIT for GA |
| LangGraph | 4/5 | 2/5 | MIT 5/5 | 4/5 | 3/5 | 14/20 | DEFER |
| CrewAI | 3/5 | 2/5 | MIT 5/5 | 5/5 | 2/5 | 12/20 | REJECT |
| MS Agent Framework | 3/5 | 1/5 | MIT 4/5 | 3/5 | 2/5 | 9/20 | REJECT |
| OpenAI Swarm/SDK | 2/5 | 3/5 | MIT 4/5 | 4/5 | 1/5 | 10/20 | REJECT |
| MetaGPT | 3/5 | 1/5 | MIT 5/5 | 5/5 | 1/5 | 10/20 | REJECT |

**Top picks**:
1. **Wait for Anthropic Agent Teams GA** — zero mount cost, native integration, best feature fit if session-resumption limitation is resolved. Watch for GA announcement and `/resume` support for in-process teammates.
2. **LangGraph as fallback** — if Agent Teams GA timeline > 6 months or session-resumption never ships, LangGraph's supervisor pattern + checkpoint persistence is the best external option. Accept Python runtime dependency.

**Verdict**: Neither framework is recommended for immediate adoption. Mercury's lane protocol + 5 missing modules (Dim 1) is the lighter-weight path. Revisit Agent Teams when experimental flag is removed.

---

## Dim 4 — 5h Usage Observability Tooling

### 4.1 Claude Code CLI Native Capability

**Verified sources**: [Official statusline docs](https://code.claude.com/docs/en/statusline) (fetched 2026-04-26), [codelynx.dev guide](https://codelynx.dev/posts/claude-code-usage-limits-statusline), [truefoundry explanation](https://www.truefoundry.com/blog/claude-code-limits-explained)

**Finding**: Claude Code's statusline API (available in v2.1.80+) exposes `rate_limits` as a first-class JSON field passed to any statusline script via stdin:

```json
{
  "rate_limits": {
    "five_hour": {
      "used_percentage": 42.5,
      "resets_at": 1714176000
    },
    "seven_day": {
      "used_percentage": 18.3,
      "resets_at": 1714435200
    }
  }
}
```

- `used_percentage`: 0–100, float
- `resets_at`: Unix epoch seconds
- Available only for Claude.ai Pro/Max subscribers
- Present only after the first API response in a session
- Each window may be independently absent (handle null)
- Data source: undocumented OAuth endpoint `GET https://api.anthropic.com/api/oauth/usage`

**`/usage` command**: Claude Code also has a `/usage` command (aliases: `/cost`, `/stats`) that displays session cost, plan usage limits, and activity stats in-session. Not machine-readable; for human inspection only.

### 4.2 Community Tooling

| Tool | Source | Method | Notes |
|------|--------|--------|-------|
| **ccusage** | [github.com/ryoppippi/ccusage](https://github.com/ryoppippi/ccusage) | Reads local JSONL transcript files | 13.2k stars; daily/monthly/session/5h-window reports; does NOT require API calls — reads `~/.claude/projects/*/conversations/*.jsonl` |
| **Claude-Code-Usage-Monitor** | [github.com/Maciek-roboblog/Claude-Code-Usage-Monitor](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) | Real-time from JSONL | Real-time charts, cost estimates, limit predictions |
| **claude-usage** | [github.com/phuryn/claude-usage](https://github.com/phuryn/claude-usage) | Local dashboard from JSONL | Progress bars for Pro/Max |
| **ccusage-web** | [dev.to/hamzaahmedkhan](https://dev.to/hamzaahmedkhan/ccusage-web-web-dashboard-to-track-claude-code-token-costs-3l17) | Web UI on top of ccusage | Interactive charts |
| **claude-code-statusline** | [github.com/ohugonnot/claude-code-statusline](https://github.com/ohugonnot/claude-code-statusline) | Statusline script consuming `rate_limits` JSON | Tracks session %, weekly %, reset countdown |

### 4.3 Anthropic API Rate Limit Headers

**Source**: [platform.claude.com/docs/en/api/rate-limits](https://platform.claude.com/docs/en/api/rate-limits), [openclaw issue #8791](https://github.com/openclaw/openclaw/issues/8791)

The Messages API response includes:
- `anthropic-ratelimit-requests-remaining` / `anthropic-ratelimit-tokens-remaining`
- `anthropic-ratelimit-requests-limit` / `anthropic-ratelimit-tokens-reset` (RFC 3339 timestamp)
- `anthropic-ratelimit-unified-5h-utilization` / `anthropic-ratelimit-unified-7d-utilization` (advanced)
- `anthropic-ratelimit-unified-representative-claim` (which window is authoritative)

These headers appear on both successful and 429 responses, enabling proactive monitoring. However, they are only accessible when making direct API calls (not via Claude Code CLI sessions which use the OAuth/subscription path).

### 4.4 Recommended Implementation Path

**Chosen path: Path A — Statusline `rate_limits` → marker file → auto-resume**

Rationale: The statusline `rate_limits.five_hour.used_percentage` field is the only officially documented, machine-readable source of 5h quota data for Claude Code CLI Pro/Max sessions. It requires zero new tooling beyond a shell script. ccusage provides complementary post-hoc analytics.

**Implementation outline**:

Two related thresholds (clarification per Issue #320 alignment):
- `PAUSE_THRESHOLD` (default **95**, per Issue #320 acceptance criteria) — hard pause point; writes marker, blocks autonomous chain
- `WARN_THRESHOLD` (optional, default **85**) — early-warning indicator only (color flips red); does NOT write marker

The 95 default ensures Issue #320 acceptance compliance; operators can lower via `MERCURY_PAUSE_THRESHOLD` env var if they want earlier pause for safety margin (research-tunable, not hardcoded).

```bash
# ~/.claude/statusline-mercury.sh
# Reads rate_limits from Claude Code statusline stdin JSON.
# Writes .mercury/state/auto-run-paused when 5h usage >= PAUSE_THRESHOLD (default 95, per #320).
# Deletes marker when usage resets (resets_at passes or usage drops).

#!/bin/bash
set -euo pipefail

PAUSE_THRESHOLD=${MERCURY_PAUSE_THRESHOLD:-95}  # per Issue #320 acceptance >95%
WARN_THRESHOLD=${MERCURY_WARN_THRESHOLD:-85}    # early-warning color only
REPO_ROOT="$(git -C "$(pwd)" rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$REPO_ROOT" ]; then
  echo "[statusline-mercury] Not inside a git repository; skip marker logic." >&2
  # Continue to display path so user still sees usage; just no marker writes.
  STATE_DIR=""
  MARKER=""
else
  STATE_DIR="$REPO_ROOT/.mercury/state"
  MARKER="$STATE_DIR/auto-run-paused"
fi

input=$(cat)

# Extract rate_limits fields (null-safe with // 0 / // "")
five_hour_pct=$(echo "$input" | jq -r '.rate_limits.five_hour.used_percentage // 0')
resets_at=$(echo "$input" | jq -r '.rate_limits.five_hour.resets_at // 0')
now=$(date +%s)

# Pause logic: write marker if threshold exceeded (only when in a git repo)
pct_int=$(printf '%.0f' "$five_hour_pct")
if [ -n "$MARKER" ]; then
  if [ "$pct_int" -ge "$PAUSE_THRESHOLD" ]; then
    mkdir -p "$STATE_DIR"
    echo "$resets_at" > "$MARKER"
  # Resume logic: delete marker if window has reset
  elif [ -f "$MARKER" ]; then
    stored_reset=$(cat "$MARKER" 2>/dev/null || echo 0)
    # Validate stored value before integer comparison; corrupted marker → drop it.
    if ! [[ "$stored_reset" =~ ^[0-9]+$ ]]; then
      echo "[statusline-mercury] Invalid pause marker; removing corrupted file." >&2
      rm -f "$MARKER"
    elif [ "$now" -ge "$stored_reset" ]; then
      rm -f "$MARKER"
    fi
  fi
fi

# Display output
pct_bar=$(printf '%.0f' "$five_hour_pct")
seven_pct=$(echo "$input" | jq -r '.rate_limits.seven_day.used_percentage // "?"')
model=$(echo "$input" | jq -r '.model.display_name // "?"')
ctx=$(echo "$input" | jq -r '.context_window.used_percentage // 0' | cut -d. -f1)

# Color code: green < 70%, yellow at WARN_THRESHOLD (default 85), red at PAUSE_THRESHOLD (default 95)
if [ "$pct_int" -ge "$PAUSE_THRESHOLD" ]; then color='\033[31m'; # red — pause point
elif [ "$pct_int" -ge "$WARN_THRESHOLD" ]; then color='\033[33m'; # yellow — early warn
elif [ "$pct_int" -ge 70 ]; then color='\033[33m'; # yellow — soft warn
else color='\033[32m'; fi # green
reset='\033[0m'

echo -e "${color}5h: ${pct_bar}%${reset} | 7d: ${seven_pct}% | ctx: ${ctx}% | ${model}"
```

**Auto-run detection** (side lane loop, e.g. in `scripts/lane-autonomous-chain.sh`):

```bash
# Check for pause marker before each autonomous iteration
MARKER=".mercury/state/auto-run-paused"
if [ -f "$MARKER" ]; then
  resets_at=$(cat "$MARKER" 2>/dev/null || echo "")
  now=$(date +%s)

  # Guard against corrupted marker files (non-numeric content) — defense-in-depth at file-system boundary
  if ! [[ "$resets_at" =~ ^[0-9]+$ ]]; then
    echo "[lane-autonomous] Invalid pause marker; removing corrupted marker." >&2
    rm -f "$MARKER"
  elif [ "$now" -lt "$resets_at" ]; then
    remaining=$(( resets_at - now ))
    echo "[lane-autonomous] 5h quota pause active; $(( remaining / 60 ))m until reset. Sleeping."
    sleep 300  # re-check every 5 min
    continue
  else
    rm -f "$MARKER"
    echo "[lane-autonomous] Quota reset detected; resuming autonomous chain."
  fi
fi
```

**Settings wiring** (`~/.claude/settings.json` addition):
```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline-mercury.sh",
    "refreshInterval": 60
  }
}
```

**Durable cron registration** (per Issue #320 acceptance: "Cron must be `durable: true` to survive session restarts"):

The statusline command itself runs in-band on every Claude Code refresh and does not need cron — it is invoked by the platform whenever the statusline updates (every `refreshInterval` seconds, default 60). The pause marker therefore self-maintains across all sessions that refresh the statusline.

For sessions that do NOT use a statusline (e.g. background agents, CI runs, headless dispatch), Phase A implementation MUST add a durable cron via Mercury's session-resilient cron infrastructure (see `feedback_auto_run_mode.md`):

```text
CronCreate:
  cron: "*/2 * * * *"
  durable: true   # MANDATORY — survives session restart
  prompt: |
    Re-run statusline-mercury.sh with synthetic stdin from the latest /usage probe
    (or skip if last write_check_at < 60s old).
```

Acceptance verification: after `claude` restart, `gh api ... | jq '.cron_jobs'` must still show the registered job. If `durable: false` was used, the cron silently disappears and quota tracking gaps open. This is enforced as Phase A exit criterion #5 in the phased PR plan below.

**Why this path over alternatives**:
- **Path B (ccusage cron)**: ccusage reads JSONL files which lag real-time; statusline has tighter coupling to live session state. ccusage best for analytics, not control plane.
- **Path C (API headers)**: Only available for direct API calls, not Claude Code CLI subscription sessions.
- **Path D (Anthropic Console API)**: No public programmatic usage API documented as of 2026-04-26 (UNVERIFIED — no official endpoint found).

---

## Risk Register

| # | Risk | Severity | Likelihood | Mitigation |
|---|------|----------|------------|------------|
| R1 | Anthropic Agent Teams experimental flag never reaches GA or session-resumption stays broken | HIGH | MEDIUM | Mercury lane protocol is Agent Teams-independent; proceed with lane-based Director/DevTeam without waiting |
| R2 | 5h quota exhaustion mid-autonomous-chain causes incomplete side lane work (stale branch, half-committed state) | HIGH | HIGH | Pause marker + pre-task quota check before each autonomous iteration; always commit before pausing |
| R3 | Cross-lane status aggregator misrepresents lane state (stale GitHub API data) | MEDIUM | MEDIUM | Add `last_checked_at` timestamp to `lane-status.json`; treat data >15 min old as stale; alert Director |
| R4 | Director spawns duplicate side lanes for same Issue (no atomic claim at spawn) | MEDIUM | LOW | Apply Rule 1 + probe-after-write to spawn command; check `LANES.md` before creating new lane |
| R5 | `mercury-channel-router` MAX_SESS=3 cap blocks new lane registration when 3 sessions already active | MEDIUM | MEDIUM | Raise MAX_SESS to 5 (matching lane hard-cap); or implement priority-based eviction for idle sessions |
| R6 | mem0 cross-session memory races: two lanes write conflicting session summaries simultaneously | MEDIUM | LOW | mem0 Qdrant is append-by-design; retrieval ranks by recency; conflicts self-resolve within 1 query cycle |
| R7 | Director role drift: main lane starts writing code instead of directing (role boundary erosion) | LOW | MEDIUM | `.claude/agents/main.md` `forbiddenActions` enforcement + TeammateIdle hook (if Agent Teams used) |
| R8 | LangGraph mount decision reversed after significant investment | LOW | LOW | Decision deferred; no investment until Agent Teams GA timeline clarified |

---

## Recommendation — Phased PR Plan

### Verdict: CONDITIONAL_GO

**Conditions for GO promotion**:
1. 5h observability script (`scripts/statusline-mercury.sh`) deployed and validated for ≥3 sessions
2. Cross-lane status aggregator (`scripts/lane-status.sh`) producing valid `lane-status.json`
3. At least 1 lane spawned via `scripts/lane-spawn.sh` (not manual) with Director receiving auto-report
4. `mercury-channel-router` MAX_SESS bumped to 5

### Phased PR Plan

**Phase A — Observability foundation** (est. 1d)
- PR #A1: `scripts/statusline-mercury.sh` — 5h usage → pause marker → auto-resume detection
- PR #A2: `scripts/lane-status.sh` — poll GitHub Issues + last-commit timestamps → `.mercury/state/lane-status.json`
- Acceptance:
  1. statusline shows 5h%, 7d%, ctx%, model
  2. marker file `.mercury/state/auto-run-paused` created when `used_percentage >= PAUSE_THRESHOLD` (default 95, per Issue #320 acceptance), deleted when window resets
  3. corrupted marker (non-numeric content) self-heals (removed without crash)
  4. `lane-status.json` updates on cron with `last_checked_at` ISO timestamp
  5. **durable cron registered** for non-statusline sessions (`durable: true` confirmed via `gh api` post-restart)

**Phase B — Lifecycle scripts** (est. 2d)
- PR #B1: `scripts/lane-spawn.sh` — atomic lane creation (file Issue, create branch, write handoff, update LANES.md, notify Director via Telegram)
- PR #B2: `scripts/lane-close.sh` — lane teardown (close Issue, mark LANES.md closed, prune `.tmp/lane-<name>/`)
- PR #B3: `scripts/lane-sweep.sh` — stale lane detection (14-day rule, Rule 3.1)
- Acceptance: end-to-end spawn → work → close cycle works without manual LANES.md editing

**Phase C — Auto-report + Director command surface** (est. 1-2d)
- PR #C1: Side lane auto-report hook — on milestone commit, call `/notify` with progress summary
- PR #C2: Router enhancement — inbound `@<lane-label> <cmd>` routes to lane-specific inbox, not just `activeId`
- Acceptance: Director receives unprompted progress updates; Telegram `@side-mlane status` returns lane state

**Phase D — Agent Teams integration** (DEFER until GA)
- When `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` reaches GA with session-resumption support, evaluate replacing Phases A-C coordination with native Agent Teams task list + TeammateIdle hook
- If Agent Teams GA timeline > 6 months: evaluate LangGraph supervisor pattern as Phase D alternative

**Total estimated effort**: 6–10 person-days across 4 sub-Issues (filed under #318/#319 parent).

---

## Source Index

(≥30 sources, deduplicated by section)

### Anthropic Official
1. [Orchestrate teams of Claude Code sessions — Claude Code Docs](https://code.claude.com/docs/en/agent-teams) — Dim 3.1, fetched 2026-04-26
2. [Customize your status line — Claude Code Docs](https://code.claude.com/docs/en/statusline) — Dim 4.1, fetched 2026-04-26
3. [Rate limits — Claude API Docs](https://platform.claude.com/docs/en/api/rate-limits) — Dim 4.3
4. [Multiagent sessions — Claude API Docs](https://platform.claude.com/docs/en/managed-agents/multi-agent) — Dim 3.1
5. [How we built our multi-agent research system — Anthropic Engineering](https://www.anthropic.com/engineering/multi-agent-research-system) — Dim 3.7
6. [2026 Agentic Coding Trends Report — Anthropic resources](https://resources.anthropic.com/2026-agentic-coding-trends-report) — Background

### Third-party Anthropic coverage
6a. [Claude Managed Agents — the-ai-corner.com (third-party)](https://www.the-ai-corner.com/p/claude-managed-agents-guide-2026) — Dim 3.1; non-Anthropic blog summarizing managed-agents

### Claude Code Agent Teams Coverage
8. [Anthropic releases Opus 4.6 with new 'agent teams' — TechCrunch](https://techcrunch.com/2026/02/05/anthropic-releases-opus-4-6-with-new-agent-teams/) — Dim 3.1
9. [What Is Anthropic Claude Code Agent Teams? — MindStudio](https://www.mindstudio.ai/blog/what-is-claude-code-agent-teams) — Dim 3.1
10. [Claude Opus 4.6 brings 1M token context and agent teams — VentureBeat](https://venturebeat.com/technology/anthropics-claude-opus-4-6-brings-1m-token-context-and-agent-teams/) — Dim 3.1
11. [Claude Code Agent Teams: Multi-Agent Development Guide — Lushbinary](https://lushbinary.com/blog/claude-code-agent-teams-multi-agent-development-guide/) — Dim 3.1
12. [Collaborating with agents teams in Claude Code — Medium/Heeki Park](https://heeki.medium.com/collaborating-with-agents-teams-in-claude-code-f64a465f3c11) — Dim 3.1

### Claude Code Usage Observability
13. [How to Show Claude Code Usage Limits in Your Statusline — codelynx.dev](https://codelynx.dev/posts/claude-code-usage-limits-statusline) — Dim 4.1
14. [Claude Code Limits: Quotas & Rate Limits Guide — truefoundry](https://www.truefoundry.com/blog/claude-code-limits-explained) — Dim 4.1
15. [Claude Code /usage: Are You About to Hit the Rate Limit? — Vincent's Blog](https://blog.vincentqiao.com/en/posts/claude-code-usage/) — Dim 4.1
16. [ccusage CLI tool — GitHub ryoppippi/ccusage](https://github.com/ryoppippi/ccusage) — Dim 4.2
17. [Claude-Code-Usage-Monitor — GitHub Maciek-roboblog](https://github.com/Maciek-roboblog/Claude-Code-Usage-Monitor) — Dim 4.2
18. [claude-usage local dashboard — GitHub phuryn](https://github.com/phuryn/claude-usage) — Dim 4.2
19. [claude-code-statusline — GitHub ohugonnot](https://github.com/ohugonnot/claude-code-statusline) — Dim 4.1
20. [Feature: Expose Anthropic rate limit utilization — openclaw/openclaw #8791](https://github.com/openclaw/openclaw/issues/8791) — Dim 4.3
21. [Claude Code usage analytics — Anthropic Help Center](https://support.claude.com/en/articles/12157520-claude-code-usage-analytics) — Dim 4.2
22. [BUG: 5h session limits exhausted abnormally fast — anthropics/claude-code #38335](https://github.com/anthropics/claude-code/issues/38335) — Dim 4.1
22a. [ccusage-web web dashboard — dev.to/hamzaahmedkhan](https://dev.to/hamzaahmedkhan/ccusage-web-web-dashboard-to-track-claude-code-token-costs-3l17) — Dim 4.2

### LangGraph
23. [LangGraph: Agent Orchestration Framework — langchain.com](https://www.langchain.com/langgraph) — Dim 3.2
24. [GitHub langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — Dim 3.2 (29.8k stars, MIT)
25. [LangGraph overview — docs.langchain.com](https://docs.langchain.com/oss/python/langgraph/overview) — Dim 3.2
25a. [LangGraph multi-agent orchestration guide — Latenode](https://latenode.com/blog/ai-frameworks-technical-infrastructure/langgraph-multi-agent-orchestration/) — Dim 3.2

### CrewAI
26. [Introduction — CrewAI docs](https://docs.crewai.com/en/introduction) — Dim 3.3
27. [GitHub crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) — Dim 3.3 (MIT)
28. [crewai — PyPI](https://pypi.org/project/crewai/) — Dim 3.3
28a. [What is crewAI? — IBM Think](https://www.ibm.com/think/topics/crew-ai) — Dim 3.3

### Microsoft Agent Framework / AutoGen
29. [Microsoft Agent Framework Overview — Microsoft Learn](https://learn.microsoft.com/en-us/agent-framework/overview/) — Dim 3.4
30. [Introducing Microsoft Agent Framework — Foundry Blog](https://devblogs.microsoft.com/foundry/introducing-microsoft-agent-framework-the-open-source-engine-for-agentic-ai-apps/) — Dim 3.4
31. [Microsoft Agent Framework Version 1.0 — devblogs](https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-version-1-0/) — Dim 3.4
31a. [Microsoft ships production-ready Agent Framework 1.0 — Visual Studio Magazine](https://visualstudiomagazine.com/articles/2026/04/06/microsoft-ships-production-ready-agent-framework-1-0-for-net-and-python.aspx) — Dim 3.4
32. [AutoGen Update discussion #7066 — microsoft/autogen](https://github.com/microsoft/autogen/discussions/7066) — Dim 3.4 (AutoGen → maintenance mode)

### OpenAI Swarm / Agents SDK
33. [GitHub openai/swarm](https://github.com/openai/swarm) — Dim 3.5 (educational, deprecated)
34. [OpenAI Swarm: Multi-Agent Framework — decisioncrafters](https://www.decisioncrafters.com/openai-swarm-multi-agent-orchestration-framework/) — Dim 3.5 (21k+ stars)
35. [2026 AI Agent Framework Showdown — QubitTool](https://qubittool.com/blog/ai-agent-framework-comparison-2026) — Dim 3.5
35a. [OpenAI Swarm guide — Morph](https://www.morphllm.com/openai-swarm) — Dim 3.5
35b. [OpenAI Agents SDK review — mem0 blog](https://mem0.ai/blog/openai-agents-sdk-review) — Dim 3.5

### MetaGPT
36. [GitHub FoundationAgents/MetaGPT](https://github.com/FoundationAgents/MetaGPT) — Dim 3.6 (64.1k stars, MIT)
37. [MetaGPT Introduction — deepwisdom.ai](https://docs.deepwisdom.ai/main/en/guide/get_started/introduction.html) — Dim 3.6
37a. [MetaGPT agentic framework overview — xpay.sh](https://www.xpay.sh/resources/agentic-frameworks/metagpt/) — Dim 3.6

### Multi-Agent Research
38. [How Anthropic Built a Multi-Agent Research System — ByteByteGo](https://blog.bytebytego.com/p/how-anthropic-built-a-multi-agent) — Dim 3.7
39. [Building a Multi-Agent Research System — ZenML LLMOps Database](https://www.zenml.io/llmops-database/building-a-multi-agent-research-system-for-complex-information-tasks) — Dim 3.7
40. [Towards a science of scaling agent systems — Google Research](https://research.google/blog/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/) — Background (cited in multi-lane-protocol-2026-04-25.md)

### Mercury Context
41. [Mercury multi-lane-protocol-2026-04-25.md — in-repo](.mercury/docs/research/multi-lane-protocol-2026-04-25.md) — Background
42. [Mercury DIRECTION.md — in-repo](.mercury/docs/DIRECTION.md) — Background
43. [Mercury archive/docs/architecture-evolution-plan.md — in-repo](archive/docs/architecture-evolution-plan.md) — Dim 2
44. [Mercury archive/roles/*.yaml — in-repo](archive/roles/) — Dim 2
