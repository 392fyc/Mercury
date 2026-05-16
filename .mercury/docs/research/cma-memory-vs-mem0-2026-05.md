# CMA Memory + `/responses/compact` vs Mercury mem0 — ADR

> 状态: **生效中** | 制定日期: 2026-05-16 | 决策者: 392fyc (main lane S102) | Closes: [Issue #384](https://github.com/392fyc/Mercury/issues/384)
> Parent context: [Issue #381 tech intel sweep](https://github.com/392fyc/Mercury/issues/381) + `~/.claude/projects/D--Mercury-Mercury/memory/research/tech-intel-sweep-2026-05-12.md` (user-level memory file, 不在 Mercury repo)
> Predecessor: PR #258 (`scripts/mem0_hooks.py` 引入, Mercury #252 **Phase A** 2026-04-17 — adapter prototype; Phase B hook 接线后续 user-level 落地 per CLAUDE.md #259 governance pattern)
>
> **路径约定**: 本 ADR 涉及的用户级路径形式 `~/.claude/...` 等价于 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/...`, 二者可任选一种书写, env 形式在多账户 / CI 下更可移植 — 沿用 CLAUDE.md §"Related Repositories" 约定。

---

## TL;DR

**Verdict: (a) status quo + monitor**. mem0 + Qdrant 保持 Mercury 的 canonical memory layer。Anthropic CMA Memory 与 OpenAI `/responses/compact` 在当前形态下**均不可替代** mem0 的 cross-session semantic recall 能力，主要因为：

1. **CMA Memory 仅在 Anthropic Managed Agents 运行时容器内可用** — 不在 Claude Code、不在 Codex CLI、不在 raw Messages API 调用上下文中可访问。Mercury 主体跑在 Claude Code 上，这是 architectural-killer constraint。
2. **CMA Memory 无 semantic search** — 只是 `/mnt/memory/` 路径下的文本文件挂载，需要 agent 自己 `ls` + `cat` 路径导航。mem0 的 Qdrant + 0.92 cosine dedup_guard 提供的多信号召回与之不重叠。
3. **`/responses/compact` 是 intra-conversation token 压缩** — 输出是 opaque encrypted blob，OpenAI Responses API 链内 chained-via-`previous_response_id`；不解决 cross-session、cross-model、human-inspectable 长期记忆问题。
4. 三者切换成本均不与现有 mem0 capability 形成正向 ROI — 切换会引入运行时锁死或丢失能力。

**Monitor 条件 (任一触发即 re-eval)**：
- CMA Memory 公开 non-CMA-runtime access (raw API / Claude Code 集成)
- CMA "dreaming" research preview 从 restricted access GA → 自动化 KB consolidation 与 Mercury Karpathy 模式正面对撞
- mem0 维护成本飙升 (mem0ai upstream P1 bug 数量 / Qdrant 长期 lock-in 风险信号)
- Mercury 决定将主体运行时迁移至 CMA (架构 pivot, 远超 memory 单维度)

**DIRECTION.md amendment**: **不需要**。但研究过程发现 §模块 2 「Memory Layer (长期记忆层)」描述 (Obsidian vault + NAS SSH + Karpathy compile) 已被 mem0 swap (PR #258) 取代，文档严重过时。单独 follow-up Issue 处理 — **不在本 ADR 提交范围**。

---

## 1. 背景

### 1.1 Issue #384 触发链

[Issue #381 (side-bug S6 tech intel sweep, 2026-04-21 → 2026-05-12)](https://github.com/392fyc/Mercury/issues/381) 在 3-week 跨 vendor 调研 (Anthropic + OpenAI + GitHub hot projects) 中 surface 2 个 mem0 替代候选：

- **Anthropic Claude Managed Agents (CMA) Memory** — public beta 2026-04-23
- **OpenAI Responses API `/responses/compact`** — window release (per Issue #384 body)

两者都晚于 Mercury mem0 swap (PR #258 `599d313bb29f56e2aeb96c678c8198c78c5f2b86` merged 2026-04-17, Mercury Issue #252 **Phase A** 的 mem0 adapter prototype — PR head branch `feature/252-mem0-phase-a` + PR title `feat(memory/phase-a)` 确认; Phase B hook 接线为后续 user-level 落地)。AgentKB-fork 已归档 (in-repo: [`./agentkb-fork-salvage-audit-2026-04-17.md`](./agentkb-fork-salvage-audit-2026-04-17.md)), Mercury_KB 早于 AgentKB 已废。

#384 提出 4 个 verdict 选项：
- (a) Status quo — 保持 mem0 canonical
- (b) Layer mem0 on top of CMA Memory
- (c) Pivot to CMA + `/responses/compact`, retire mem0
- (d) Defer pending CMA Memory GA

### 1.2 当前 Mercury mem0 stack 现状 (post-#258)

| 组件 | 路径 | 功能 |
|------|------|------|
| Adapter | `~/.claude/scripts/mem0_hooks.py` | `mem0ai.Memory` 包装 + 4 个 P1 bug guard (`#4099 empty-payload` / `#4799 list-content` / `#4453 threshold` / `#4536 contradiction`) + `dedup_guard` 0.92 cosine 阈值 + telemetry-off forced |
| Bridge | `~/.claude/scripts/mem0_bridge.py` | Thin no-op-safe `ingest_session()` + `recall()` 入口；fail-safe (mem0 缺失 / `OPENAI_API_KEY` 缺失 / store 错误均 falsy return) |
| Hook 触发 | `~/.claude/hooks/{session-start.py,session-end.py,pre-compact.py}` | SessionStart 注入历史 / SessionEnd flush 总结 / PreCompact 压缩前 snapshot |
| Vector store | `~/.claude/scripts/mem0-state/qdrant/` + `history.db` | Qdrant on-disk + SQLite history |
| Disable kill-switch | `AGENTKB_MEM0_DISABLED=1` / `MERCURY_MEM0_DISABLED=1` | Case-insensitive env; `0/false/no/off` 视为 not-disabled |
| 单测 | `~/.claude/scripts/mem0_bridge_test.py` | 7-test smoke, CLAUDE.md §"用户级变更治理" 验证清单中引用 |

特征：
- **跨 session、跨 model、跨 vendor** (mem0 持久层是 user-controlled Qdrant，不依赖任何 Anthropic / OpenAI runtime)
- **Semantic search** via `mem0.search()` (Qdrant cosine + BM25 + entity graph triple)
- **Single-user runtime** (`user_id="mercury"` 单租户)
- **Cross-repo**: 完全在用户 home 下，不在任何 git repo 内，per CLAUDE.md "Related Repositories" #259 governance pattern

---

## 2. 2026-05 Vendor-native 候选 — 调研发现

### 2.1 Anthropic CMA Memory (public beta 2026-04-23)

**Source verification** (Mercury 强制 web-research protocol)：
- 官方文档: <https://platform.claude.com/docs/en/managed-agents/memory>
- 官方 cookbook: <https://platform.claude.com/cookbook/managed-agents-cma-remember-user-preferences>
- 官方事件流文档: <https://platform.claude.com/docs/en/managed-agents/events-and-streaming>
- 9to5Mac 2026-05-07 报道: <https://9to5mac.com/2026/05/07/anthropic-updates-claude-managed-agents-with-three-new-features/>
- WaveSpeed 2026 pricing review: <https://wavespeed.ai/blog/posts/claude-managed-agents-pricing-2026/>

**Capability**：
- `/mnt/memory/` filesystem 挂载在 CMA session container 内, agent 用标准 file tools (bash `cat` / `write`) 访问
- 单 session 最多 attach 8 stores; 单 memory file ≤ 100 kB (~25k tokens) — [memory doc](https://platform.claude.com/docs/en/managed-agents/memory)
- Memory 单位是 `/path/like/this.md`-style 文本文件 (Markdown OK); store 是 container
- REST 资源: `/v1/memory_stores/{id}/memories` (workspace-scoped, 不绑定 agent ID / session ID)

**Versioning**：
- 每次 mutation 创建 immutable `memver_...` 版本
- 版本属于 store, 不属于单个 memory; memory 删除后版本仍保留
- Retention: 30 天最少 (历史短的 memory 可能更长)
- **No restore endpoint** — rollback 需读 version content 然后 `memories.update`/`create`
- Optimistic concurrency: `content_sha256` precondition on update
- `memory_versions.redact` 端点用于 PII/合规 scrub (保留 audit trail)

**SDK** (`anthropic` Python ≥ 0.91.0, TypeScript camelCase parity)：
```python
client.beta.memory_stores.create(name, description)
client.beta.memory_stores.memories.create(store_id, path, content)
client.beta.memory_stores.memories.update(memory_id, memory_store_id, content, precondition)
client.beta.memory_stores.memory_versions.list(store_id, memory_id)
```

**Audit trail**：不是专用 `memory.*` 事件，而是 `agent.tool_use` / `agent.tool_result` 事件携带 `/mnt/memory/...` 路径 input。需要 inference from path prefix。Primary source: [memory doc §"How the agent accesses memory"](https://platform.claude.com/docs/en/managed-agents/memory) — 原文 "The agent's reads and writes appear in the event stream as ordinary `agent.tool_use` and `agent.tool_result` events for whichever tool touched the mount."; 通用事件类型 schema 见 [event stream doc](https://platform.claude.com/docs/en/managed-agents/events-and-streaming)。

**Beta SLA**：
- 仍 public beta 截至 2026-05-16; `managed-agents-2026-04-01` beta header 必须
- Memory-store-specific 默认 capacity & rate limit 未公开数字 — [memory doc "## Limits"](https://platform.claude.com/docs/en/managed-agents/memory) 原文 "Default capacity and rate limits apply to memory stores while this feature is in beta. Contact support if you need higher limits." 通用 Managed Agents API rate limit 在其它 docs 页面公开 (与 memory 单独限额是两件事)
- Memory stores **只能在 session 创建时 attach** — 不能动态加减
- Archive 单向 (no unarchive)
- 30-day version retention floor (无 GA 承诺) — [memory doc](https://platform.claude.com/docs/en/managed-agents/memory)
- 无 GA 时间表

**关键运行时约束**：CMA Memory 的 `/mnt/memory/` 挂载**只在 Managed Agents container 内可见**。REST `/v1/memory_stores` 端点可以从任何调用方读写 (seeding / 修正 / 监控)，但**实际作为 agent 的"内存"使用必须在 CMA 运行时**。这是与 Mercury 当前架构最大的 mismatch — Mercury 跑在 Claude Code (本地 CLI runtime) 和 Codex CLI 上，**不在 CMA 容器内**。

**Comparison anchors**：
| 维度 | CMA Memory | Mercury mem0 |
|------|------------|--------------|
| Semantic search | ❌ 无 | ✅ Qdrant 向量 + BM25 + entity graph (mem0 multi-signal) |
| Cross-session persistence | ✅ workspace-scoped | ✅ user-controlled Qdrant |
| 跨 model | ❌ 仅 Claude family | ✅ model-agnostic (任何 Claude/Codex/Gemini sub-agent 都能 hook) |
| 跨 vendor | ❌ 仅 Anthropic | ✅ vendor-neutral (OpenAI embeddings + 任意 LLM 调用方) |
| 跨 runtime | ❌ CMA-only (非 Claude Code / 非 Codex CLI / 非 raw API agent) | ✅ Claude Code + Codex + 任何 hook-aware runtime |
| Vendor lock-in | ✅ Anthropic | ⚠️ mem0ai upstream + Qdrant (但 self-hosted, portable) |
| Audit trail | ✅ memory_versions + redact API | ⚠️ history.db (本地 SQLite, 无 redact API) |
| Cost | $0.08/session-hour CMA runtime; storage 暂无单独 charge | OPENAI_API_KEY embedding 费用 + 本地存储 |

### 2.2 OpenAI `/responses/compact`

**Source verification**：
- API Reference: <https://developers.openai.com/api/reference/resources/responses/methods/compact>
- Compaction guide: <https://developers.openai.com/api/docs/guides/compaction>
- Community Q&A: <https://community.openai.com/t/compact-a-response-with-previous-response-id/1372502>
- liteLLM proxy 文档 (OpenAI 格式镜像): <https://docs.litellm.ai/docs/response_api_compact>

**Endpoint contract**：`POST /v1/responses/compact`

| Body 字段 | 说明 |
|-----------|------|
| `model` | 必填, e.g. `gpt-5.5` / `gpt-4o` |
| `input` | 必填, 全 window message array / 单 response 对象 |
| `instructions` | 可选, system 级别向前携带 |
| `previous_response_id` | 可选, chain from prior stored response |

**Output schema**：
```json
{
  "id": "resp_abc123",
  "object": "response.compaction",
  "output": [
    {"type": "message", "role": "assistant", ...},
    {"type": "compaction", "encrypted_content": "..."}
  ],
  "usage": {...}
}
```

**Semantics**：
- **Lossy + encrypted + opaque** — `encrypted_content` blob "not intended to be human-interpretable"
- 所有 user turns 保留 verbatim + 单个 `compaction` item 替代所有 prior assistant turns + tool calls + tool results
- 不是 summarization, 是 model-driven 压缩 — Mercury PreCompact hook 无法 inspect / augment

**Trigger 模式**：
- Client-driven: 显式调用 `POST /v1/responses/compact`
- Server-side: `context_management: [{ compact_threshold: <N> }]` 字段附在 `POST /responses` 上, server inline 触发

**Cost**：同 token 计费 (input + output), 无 flat fee。Net saving 取决于后续 turn 是否变便宜。

**Compatible models** (web-verified): `gpt-5.3-codex`, `gpt-5.5`。`o`-series / `gpt-4o` 是否 fully supported **UNVERIFIED** — API reference 列了 92 model identifier 但未明确 compaction 行为 matrix。

**Persistence**：
- **NOT durable artifact** — endpoint 描述 "stateless: you send the full window, it returns a compacted window"
- `previous_response_id` chaining 可以隔 session 但无 TTL 承诺、无 named store、无 semantic search
- ZDR (`store: false`) 下 `previous_response_id` chain 不可用 — by design

**Mercury fit**：
| 维度 | `/responses/compact` | Mercury PreCompact hook + mem0 |
|------|----------------------|--------------------------------|
| Scope | 单 model call chain (OpenAI Responses API 限定) | 整个 Claude Code session context |
| Output | Encrypted opaque blob | Human-readable markdown handoff + mem0 semantic store |
| Cross-session | Implicit via `previous_response_id` (TTL 未承诺, ZDR 下失效) | Explicit file + Qdrant 持久索引 |
| 跨 model | ❌ OpenAI only | ✅ model-agnostic |
| 跨 vendor | ❌ 仅 OpenAI | ✅ vendor-neutral |
| Hook integration | ❌ model-side, not hook-side | ✅ PreCompact hook 可注入 |
| Long-term memory | ❌ 无 semantic search | ✅ mem0 multi-signal retrieval |

`/responses/compact` 与 Mercury mem0 解决**正交问题**。重叠很窄 (仅在 "压缩进模型的 token 数" 这一维度, 笔者粗估 < 20% — 此为作者主观推断, 非引用统计数据), 且机制不兼容 (Mercury 跑 Claude / Codex 非 OpenAI Responses)。

---

## 3. 决策 — Verdict (a) status quo + monitor

### 3.1 排除其它选项的理由

| 选项 | 排除理由 |
|------|----------|
| **(b) Layer mem0 on top of CMA Memory** | CMA Memory 的 `/mnt/memory/` 挂载只在 Managed Agents container 内可见。Mercury 跑在 Claude Code 本地 CLI runtime 上，**不在 CMA 容器内**。"Layer on top" 在物理层就不通 — 没有 mem0 进程能看到 `/mnt/memory/`。即使通过 REST `/v1/memory_stores` 端点 mirror 到 mem0, 也是 dual-write 复杂度而非"layering"。Anthropic 官方亦无任何 documented pattern 推荐 layer external semantic search on top of CMA Memory。 |
| **(c) Pivot to CMA + `/responses/compact`** | 两层 architectural killer：①CMA pivot 等于放弃 Claude Code 作为主 runtime — 远超 memory 单维度的架构 pivot, 不在 #384 scope 内决策。②`/responses/compact` 是 OpenAI-only intra-conversation 压缩 — 与 mem0 的 cross-session semantic recall 完全不重叠, 不能"retire mem0"。 |
| **(d) Defer pending CMA Memory GA** | 不 actionable — defer 假设 GA 之后选项 (b)/(c) 会变得可行, 但 (b)/(c) 的核心 blocker 不是 beta 状态，是 CMA-only runtime constraint。GA 即使发生也不解决 runtime mismatch。如果 Anthropic 未来公开 non-CMA-runtime memory access (例如 raw API memory mount), 那时才有 defer→re-eval 价值。 |

### 3.2 (a) 决策的关键正面理由

1. **mem0 当前形态满足 Mercury 实际用例** — semantic recall + cross-session + cross-model + cross-repo (per #259 governance pattern) + 4 P1 bug guards 已封装。No-op-safe fail behavior 经 #258 / #259 / #361 多次 dogfood 验证。
2. **mem0 的 ROI 已 sunk-cost-acceptable** — PR #258 + Phase B 已交付 (`599d313`); 维护成本 = 偶发 mem0ai upstream bug guard + Qdrant on-disk store 监控。切换至 CMA 的迁移成本 > 维持 mem0 的运行成本。
3. **保持 portability** — Mercury 哲学 "外部挂载 + 模块可拆卸" (DIRECTION.md §P3) 要求 memory layer 可独立于任何 vendor runtime。CMA pivot 违反 P3。
4. **Issue #384 P1 priority rationale** 自身承认 "mem0 currently works and CMA Memory is public beta — no urgent breakage"。验证结果支持 status-quo 路径。

### 3.3 Monitor 触发条件

设置 4 个 re-eval triggers (任一发生重启 #384 评估)：

1. **CMA Memory non-CMA-runtime access**: Anthropic 公开 raw API / Claude Code / Codex 调用 memory mount 的途径 → (b) Layer 选项变 architecturally 可行 → 重新评估 layered hybrid
2. **CMA "dreaming" GA**: dreaming research preview 当前 restricted access (per 9to5Mac 2026-05-07), 是自动化 KB consolidation 能力 — 与 Mercury Karpathy 模式 (raw → compile → wiki → Q&A) 正面对撞 — 若 GA 且开放 non-CMA access, 强 candidate for (b)/(c) re-eval
3. **mem0 维护成本飙升信号**: mem0ai upstream P1 bug 数量 / Qdrant lock-in 风险 / `OPENAI_API_KEY` embedding 费用结构变化 — 任一显著恶化 → 评估 vendor-native 替代
4. **Mercury 架构 pivot 至 CMA**: 若 Mercury 整体决定迁移至 Anthropic Managed Agents 作为主 runtime (远超 memory 单维度), 那时 memory layer 自然跟随 — 但这是远期可能, 不在 S102 范围内

### 3.4 DIRECTION.md amendment 决策

**结论: 不需要本 ADR 引发的 DIRECTION.md 编辑**。Verdict (a) 是 status quo, 不改变 memory 战略方向。

**但研究过程发现独立 DIRECTION.md 过时问题** — §模块 2「Memory Layer (长期记忆层)」 line 104-122 仍描述：
- "NAS 上的 Obsidian vault 作为中心化知识库"
- "MCP server 提供读写接口（需评估当前可用的 Obsidian MCP server 方案）"
- "NAS SSH 直接访问底层文件"

这是 Phase 3 时代设计 (S32-S57 frame), 在 Mercury #252 Phase B (2026-04-17 PR #258 mem0 swap) 后已被 mem0 + Qdrant 取代。Mercury_KB / AgentKB-fork 均已归档 (per CLAUDE.md "Related Repositories" 表格)。

**Action**: 本 ADR **不修改** DIRECTION.md (S102 scope 严格限定 research-only per handoff doc Rule 4 红线)。但 file 单独 follow-up Issue (P2 docs) 记录 DIRECTION.md §模块 2 过时, 待后续 session 独立处理 — 不污染本 ADR PR scope。

---

## 4. Acceptance 检查 — 对照 #384 Issue body

| #384 Acceptance | 本 ADR 落点 |
|-----------------|-------------|
| ☑ Research session reading CMA Memory docs end-to-end (capabilities, limits, beta SLA) | §2.1 全节 + 5 个官方 sources |
| ☑ Research session reading `/responses/compact` docs | §2.2 全节 + 4 个官方/社区 sources |
| ☑ Compare against current Mercury mem0 capability surface | §1.2 mem0 stack 现状表 + §2.1/§2.2 末尾对比表 |
| ☑ Decision recorded in `.mercury/docs/research/cma-memory-vs-mem0-2026-05.md` | 本文 |
| ☑ If (b) or (c): DIRECTION.md amendment PR | Verdict (a), 不需要 amendment; 但发现 §模块 2 独立过时 → 单独 follow-up Issue (P2 docs, 不在 #384 close 范围) |

---

## 5. 参考资料 — 完整 source list

### Anthropic CMA Memory — 本 ADR 引用
- [Using agent memory — Claude API Docs](https://platform.claude.com/docs/en/managed-agents/memory) — §2.1 capability / limits / SLA 主源
- [CMA overview — Claude API Docs](https://platform.claude.com/docs/en/managed-agents/overview) — §2.1 beta header 与 runtime 约束源
- [Build agents that remember your users — Claude Cookbook](https://platform.claude.com/cookbook/managed-agents-cma-remember-user-preferences) — §2.1 SDK 代码与"Karpathy 模式" 比较源
- [Session event stream — Claude API Docs](https://platform.claude.com/docs/en/managed-agents/events-and-streaming) — §2.1 audit trail 源
- [9to5Mac: Anthropic updates Claude Managed Agents with three new features (2026-05-07)](https://9to5mac.com/2026/05/07/anthropic-updates-claude-managed-agents-with-three-new-features/) — §3.3 dreaming research-preview restricted-access 信号源
- [WaveSpeed: CMA Pricing and Beta Limits 2026](https://wavespeed.ai/blog/posts/claude-managed-agents-pricing-2026/) — §2.1 表内 "$0.08/session-hour" cost 源

### Anthropic CMA Memory — Further reading (background, 非 load-bearing)
- [opentools.ai: Anthropic Managed Agents Add Memory](https://opentools.ai/news/anthropic-managed-agents-add-memory-persistent-state-for-ai-that-actually-ships) — launch context
- [SD Times: Anthropic adds memory to CMA](https://sdtimes.com/anthropic/anthropic-adds-memory-to-claude-managed-agents/) — launch context
- [VentureBeat: Anthropic wants to own your agent's memory](https://venturebeat.com/orchestration/anthropic-wants-to-own-your-agents-memory-evals-and-orchestration-and-that-should-make-enterprises-nervous) — enterprise lock-in 视角
- [Claude Managed Agents in 2026 — blog.laozhang.ai](https://blog.laozhang.ai/en/posts/claude-managed-agents) — third-party 综述
- [claude-agent-sdk-python CHANGELOG](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md) — SDK 版本变更追踪

### OpenAI `/responses/compact`
- [OpenAI API Reference — Compact a response](https://developers.openai.com/api/reference/resources/responses/methods/compact)
- [OpenAI Compaction guide](https://developers.openai.com/api/docs/guides/compaction)
- [OpenAI Developer Community thread](https://community.openai.com/t/compact-a-response-with-previous-response-id/1372502)
- [liteLLM /responses/compact docs](https://docs.litellm.ai/docs/response_api_compact)

### Mercury internal
- `~/.claude/scripts/mem0_hooks.py` (user-level cross-repo, 不在 Mercury repo 内 — 路径等价 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/mem0_hooks.py`) — mem0 adapter (4 P1 bug guards)
- `~/.claude/scripts/mem0_bridge.py` (user-level cross-repo, 不在 Mercury repo 内) — fail-safe `ingest_session()` + `recall()`
- `~/.claude/projects/D--Mercury-Mercury/memory/research/tech-intel-sweep-2026-05-12.md` (user-level memory file, 不在 Mercury repo) — parent intel sweep
- [`./agentkb-fork-salvage-audit-2026-04-17.md`](./agentkb-fork-salvage-audit-2026-04-17.md) — in-repo predecessor archive audit
- [Mercury Issue #252](https://github.com/392fyc/Mercury/issues/252) (3-phase plan: A 适配器 / B hook 接线 / C 后续) + [PR #258 `feat(memory/phase-a)`](https://github.com/392fyc/Mercury/pull/258) — Phase A 落地的 mem0 adapter prototype (`599d313`)
- [mem0.ai: State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- `.mercury/docs/DIRECTION.md` §模块 2 (line 104-122) — 过时, 需单独 follow-up Issue

### UNVERIFIED claims
- 是否 `o1`/`o3`/`gpt-4o` 在 `/responses/compact` 上 produce full compaction items 还是 fallback truncation (model support matrix 在官方 doc 之外未确认)
- `previous_response_id` chain 在 OpenAI 服务器上的 TTL 时长 (无公开文档)
- CMA Memory 区域可用性 parity (Anthropic 未公开 regional restriction list)
- `gpt-5.5-instant` / `chat-latest` alias 对 compaction 端点的 support (carry-forward from S6 intel sweep)
