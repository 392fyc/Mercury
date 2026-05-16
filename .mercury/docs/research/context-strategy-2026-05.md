# Context Strategy Re-baseline vs 1M-ctx Norm — ADR

> 状态: **生效中** | 制定日期: 2026-05-17 | 决策者: 392fyc (main lane S104) | Closes: [Issue #385](https://github.com/392fyc/Mercury/issues/385)
> Parent context: [Issue #381 tech intel sweep](https://github.com/392fyc/Mercury/issues/381) + `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/research/tech-intel-sweep-2026-05-12.md` (user-level memory, 不在 Mercury repo; `<encoded_cwd>` 约定见 `.mercury/docs/research/multi-lane-protocol-2026-04-25.md` §Path conventions)
> Related: `feedback_context_protection.md` at `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/feedback_context_protection.md` (user-memory, 60 天前 2026-03 起作; this ADR 评估其在 1M ctx norm 下是否仍 valid)
>
> **路径约定**: `~/.claude/...` 等价于 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/...`, 沿用 CLAUDE.md §"Related Repositories"。

---

## TL;DR

**Verdict: (a) keep core rule + relax single-axis threshold for Sonnet/Opus parent**。`feedback_context_protection.md` 的"不 pre-inject 全量文档"基础规则 **仍然 valid**，但触发阈值与适用范围需按 model tier 与 vendor 重新分层：

1. **Haiku-tier subagent 仍 200K ctx 硬上限** — 1M norm 不适用于 Haiku 4.5。Mercury 子 agent (autoresearch/dev-pipeline/Explore) 通常 sonnet，但凡有 haiku fallback 必须坚守 protection rule。
2. **GPT-5.5 在 >272K input 触发整 session 2x input + 1.5x output surcharge** — bulk injection 越过此线一次, 整 session 计费翻倍 (Codex CLI 路径下尤其危险)。
3. **Opus 4.7 新 tokenizer 同文本 token 膨胀** — Anthropic 官方描述为 "may use up to 35% more tokens for the same fixed text" (1.0x-1.35x range, 取决于内容), 最坏情况下 1M ctx 等效 ~750K 4.6-时代 token, **worst-case 而非 baseline**。
4. **Mercury 实测 token use** 远未触及任何上限 — 单 session input_tokens (delta) P50=1.6K / P90=4.3K / P99=132K / max=132K (= 13% of 1M)，3 路 vendor 均富余。但 **cumulative cache** 才是 cost driver, bulk injection 会污染 cache 拓扑。

**核心规则保留** (do NOT pre-inject full KB docs into sub-agent context) — 因为 (1) Haiku fallback、(2) GPT-5.5 272K cliff、(3) Opus 4.7 tokenizer 膨胀、(4) cache 经济学 — 任一单独都足够推翻 "1M GA = 随便塞" 的乐观假设。

**Monitor 条件 (任一触发即 re-eval — full list 见 §7)**:
- Haiku 提升到 ≥1M context window (目前 200K 硬上限)
- OpenAI 取消 GPT-5.5 >272K surcharge (变 flat-rate 全段)
- Mercury 实测 P99 **session cumulative input** (input + cache_1h + cache_5m + 0.1×cache_read tokens summed across turns) 持续超 500K (3 session+ rolling) — **不是** §3.1 last-turn delta `input_tokens` 字段单看
- Anthropic 公开 Opus 5.x 改回与 4.6 等效 tokenizer (撤销 up to 1.35x 膨胀)
- Anthropic / OpenAI 公开 prompt cache 经济模型重大变更 (e.g. write 1h premium 取消)

**DIRECTION.md amendment**: **不需要**。Direction §模块 2 (memory layer) 已在 S103 [PR #397](https://github.com/392fyc/Mercury/pull/397) 内 rewrite，与本 ADR scope 正交。

---

## 1. 背景

### 1.1 Issue #385 触发链

[Issue #381 (side-bug S6 tech intel sweep, 2026-04-21 → 2026-05-12)](https://github.com/392fyc/Mercury/issues/381) 在 3-week 跨 vendor 调研中 surface 两个上下文窗口里程碑:

- Anthropic Claude Opus 4.7 — 1M GA 2026-04-16 ([release notes](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7))
- OpenAI GPT-5.5 / GPT-5.5-pro — 1M+ 2026-04-24 ([model page](https://developers.openai.com/api/docs/models/gpt-5.5))

两家旗舰同时落地 1M ctx (Sonnet 4.6 早在 2026-02-17 已先到位)，Mercury 既有 `feedback_context_protection.md` (撰写于 2026-03，原 200K ctx 时代) 描述的"不 pre-inject 全量 KB 文档"规则是否依然成立、阈值如何调整，是本 ADR 要回答的问题。

### 1.2 现行规则 (under review)

`feedback_context_protection.md` body 全文 (12 LOC):

> Do not pre-inject full documents into dev agent context. Codex compacted within minutes of receiving large context files, losing precise content it had just read.
>
> **Why:** TASK-SDK-001 dispatch included full context files. Codex hit compaction almost immediately, making the injected docs useless and degrading task quality.
>
> **How to apply:** Context injection is temporarily disabled (`autoInjectContext: true` but `contextFiles: []`). Future optimization: role-specific document subsets + fine-grained snippet injection instead of whole files.

规则 driver: TASK-SDK-001 时代 Codex 早期压缩触发, 与今日 1M ctx 容量 + 改进的 compaction 策略不能简单类比。

### 1.3 关联开放工程: #361 / #362 / #316

- **#361 cost-tracker (S93 MERGED)** — per-session cost log + tier-misuse advisory + statusline ceiling。本 ADR 评估其 ceiling 默认值在 1M norm 下是否仍合适。
- **#362 sub-agent return-size scoping (S92 MERGED)** — autoresearch / dev-pipeline subagent return slim。本 ADR 评估其阈值 (<2K main ctx delta) 是否需要调整。
- **#316 notify-hub (S94 MERGED)** — 与本 ADR 正交, 仅 reference link。

---

## 2. Vendor Landscape (verified 2026-05-17)

### 2.1 Claude family

| 模型 | Ctx Window | Max Output | Input / Output (USD/MTok) | Notes |
|---|---|---|---|---|
| Opus 4.7 | **1M GA** | 128K | $5 / $25 | 新 tokenizer **up to 1.35x token** for same fixed text — Anthropic 描述 1.0x-1.35x range varying by content ([whats-new](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7)); 1M flat-rate, **no long-ctx premium** |
| Opus 4.6 | 1M GA | 128K | $5 / $25 | 与 4.7 同价, 旧 tokenizer |
| Sonnet 4.6 | **1M GA** | 64K | $3 / $15 | "900k 与 9k 同 per-token rate" 官方表述 ([anthropic.com/claude/sonnet](https://www.anthropic.com/claude/sonnet); cross-checked [models overview](https://platform.claude.com/docs/en/about-claude/models/overview)) |
| Haiku 4.5 | **200K ONLY** | 64K | $1 / $5 | **1M ctx 不适用于 Haiku tier** ([models overview](https://platform.claude.com/docs/en/about-claude/models/overview)) |

Cache 折扣 (across family): cache write 5m = 1.25x, write 1h = 2.0x, read = 0.10x ([Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching), mirrored in Mercury local `~/.claude/scripts/cost_tracker.py:52-54`)。

### 2.2 OpenAI GPT-5.5 family

| 模型 | Ctx Window | Max Output | Std I/O (USD/MTok) | >272K Trigger |
|---|---|---|---|---|
| GPT-5.5 | 1.05M | 128K | $5 / $30 | **2x input ($10) + 1.5x output ($45) for full session** ([model page](https://developers.openai.com/api/docs/models/gpt-5.5)) |
| GPT-5.5-pro | 1.05M | 128K | $30 / $180 | Pro 单价已是 6x, surcharge 行为 official 未公开特别条款 ([gpt-5.5-pro model page](https://developers.openai.com/api/docs/models/gpt-5.5-pro)) |

**Critical**: 272K cliff 是 **整 session 计费翻倍**, 不只是 272K 之上 token。300K input prompt → 全 300K 按 long-context rate 计费 ([evolink.ai pricing guide](https://evolink.ai/blog/gpt-5-5-api-pricing-guide-2026))。任何"反正 1M 随便塞"的 bulk-injection 操作只要越线一次, 当 session 后续每 turn 都被 surcharge tax。

### 2.3 跨 vendor 对比

|  | Anthropic | OpenAI |
|---|---|---|
| 1M ctx 是否 flat-rate | ✅ Yes (Opus/Sonnet) | ❌ No (>272K = 2x/1.5x surcharge) |
| Haiku-tier 1M | ❌ 200K cap | n/a |
| Tokenizer 膨胀 (vs prior gen) | up to 1.35x (Opus 4.7, 1.0x-1.35x range) | 未公开数据 |
| Cache 写入 1h 折扣 | 2.0x premium | (Responses API 不同模型, 略) |

---

## 3. Mercury 实测数据 (2026-05 cost-tracker)

### 3.1 数据集

- Source: `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/scripts/cost-tracker/*.jsonl` (19 sessions, 2026-05 月以来)
- Schema: per-session **end-of-session summary** record (`turn_count` / `total_usd` / `input_tokens` / `output_tokens` / `cache_*` / `models{}` / `transcript_path`)
- 注意: `input_tokens` field 是 **最后一 turn 的 input** (delta), 不是 session-cumulative input。Cumulative 视角下需通过 `cache_read_tokens` (复用) + `cache_1h_tokens` (新写入) 看真实 prompt 体量。

### 3.2 单 turn input_tokens 分布

```
P50 = 1,594 tokens   (中位数, 极小)
P90 = 4,316 tokens
P99 = 132,649 tokens (单 outlier session)
max = 132,649 tokens (= 13% of 1M Opus/Sonnet budget)
```

**Implication**: Mercury 实际 single-turn input 远低于 1M, 但 P99 outlier 已经达到 Haiku 4.5 200K cap 的 **66%**。若该 outlier 落到 haiku-tier subagent (autoresearch 中的 search worker、acceptance blind receipt 拆分), 必爆 200K。

### 3.3 单 session cost 拆解 — 实例

最近一个完整 Opus 4.7 session (`4e6eda03-...`, 2026-05-09, 300 turns, $88.93):

| 项目 | Tokens | USD | 占比 |
|---|---|---|---|
| input (last-turn delta) | 4,316 | $0.02 | 0.02% |
| output (cumulative) | 549,948 | $13.75 | 15.5% |
| cache_5m | 0 | $0.00 | 0% |
| cache_1h_tokens (writes) | 4,520,603 | $45.21 | 50.8% |
| cache_read_tokens | 59,908,474 | $29.95 | 33.7% |
| **TOTAL** | — | **$88.93** | 100% |

**Cost driver 是 cache_1h_tokens-writes (50.8%) + cache_read_tokens (33.7%)** — 二者加总占 cost 的 **84.5%**, raw input + output 仅 15.5%。Cumulative cache_read_tokens (59.9M) 相当于 12× cache_1h_tokens-writes 体量 — 命中率极高 ($45 写入换 $30 读取节省的 cache 经济相当于免费 5x 重用)。

### 3.4 Bulk injection 对 cost 的二阶影响

若 pre-inject 一个 50K-token KB 文档到 session 起点:
- 直接成本: 50K × $5/MTok × `cache_1h_tokens` write 2.0x premium = **$0.50** (写入)
- 命中重用: 假设 30 turns 复用 → 50K × 30 × $5/MTok × 0.10 = **$0.75** (读取)
- 总计净增 **~$1.25** per session, **若 KB 内容 90% 未被实际用到 → 净浪费**

50K × 30 turns = 1.5M `cache_read_tokens` 单独占据 session cumulative cache_read 的 **2.5%** — 看似小, 但 **被注入文档侵占的 prompt prefix slot 必然推高 cumulative cache 体量, 增加 `cache_1h_tokens` write 计费基数** ([Anthropic prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching))。注: 关于"是否会主动 evict 其他 cache slot"的 **LRU 替换语义未在 Anthropic 官方文档明确描述** (官方只说 5m / 1h TTL — UNVERIFIED), 但即便不主动 evict, cumulative size 膨胀直接对应 `cache_1h_tokens` write 计费翻倍 (§3.3 显示该项是 cost 主导)。这是 `feedback_context_protection.md` rule 的真实经济动机, 而 1M ctx 上限本身从来不是 binding constraint。

### 3.5 Model 混合分布

- Schema 中实际 model 信息存在 `models{<id>: {turn_count, total_usd}}` 嵌套 dict (而非 top-level `model` 字段) — 抽样 single-session detail (4e6eda03) 显示 `models.claude-opus-4-7 = {turn_count: 300, total_usd: 88.93}`
- 实测: Mercury main lane 当前主体跑 **100% Opus 4.7** (本 ADR sample session 唯一 model 即 Opus 4.7)
- 多 session 综合统计 (P50/P90/P99) 因 schema 嵌套形式合理, **不构成 reporting gap**

---

## 4. Per-axis Re-baseline 分析

### 4.1 Axis A — Main agent (Mercury 自身)

- 跑 Opus 4.7 1M GA, **flat-rate** (no surcharge)
- 实测 P99 = 132K, max = 132K → ctx 富余 **8.7x**
- 受 up to 1.35x tokenizer 膨胀影响, **worst-case** 等效 4.6 时代的 ~750K ctx 可用 (实际取决于内容, 1.0x-1.35x range)
- **Threshold 调整**: 单 turn injection ≤ 100K 完全安全; 不必触发 protection rule
- **Recommendation**: 主 agent 不变, 默认仍走 lean dispatch (TaskBundle 仅含 DoD + scope)

### 4.2 Axis B — Sonnet sub-agent (dev / acceptance / autoresearch search worker)

- Sonnet 4.6 1M GA flat-rate
- Mercury 用法: dev-pipeline / autoresearch / acceptance / critic 全部 sonnet (cf. `.claude/agents/dev.md:5` `model: sonnet`)
- 实际 sub-agent context: 通常 1-5 turn, 单 turn input ≪ 50K → 远未触底 Sonnet 1M cap
- **Threshold 调整**: 单 subagent dispatch 注入 ≤ 50K reference content 安全; 但 protection rule 应 keep — 因 #362 已建立 return-size slim ≤ 2K main-ctx delta, 双向 budget 一致性更重要

### 4.3 Axis C — Haiku sub-agent (potential fallback)

- Haiku 4.5 **200K ONLY** — 与 1M norm 不兼容
- Mercury 当前 active subagent 配置中: **`.claude/agents/game-researcher.md:5` 明确 `model: haiku`** (cherry-picked 2026-04-21 via Mercury #281, S65)。其他 sub-agents (dev.md / acceptance.md / research.md 等) 多为 sonnet 或继承 parent。
- 此外 OMC layer (用户级) 提供 haiku-tier 路径 — writer 等 (cf. CLAUDE.md "claude" agent description "Catch-all" + Haiku model routing 部分)
- **Threshold 调整**: 凡跨 haiku 路径 (in-repo `game-researcher` 或 OMC layer) 的注入必须 ≤ 50K (避免 input + system + cache 总和超 100K → 不能给后续 turns 留 budget)
- **Recommendation**: protection rule 在 haiku 路径下变 **HARD** rule (而非软建议) — 实际有 in-repo agent 受此约束

### 4.4 Axis D — Codex CLI (GPT-5.5)

- Mercury `.codex/hooks.json` 路径下跑 GPT-5.5 / GPT-5.5-codex (per S2-side-bug PR #358)
- **272K cliff = full-session 2x input + 1.5x output**
- 实测 P99 132K < 272K, 当前安全, 但只剩 **2x margin** 而非 1M norm 暗示的 7.5x
- **Threshold 调整**: Codex 路径下 session cumulative input tokens 不得越过 272K cliff (一次越线整 session 计费翻倍)。预算分配建议: 32K system + ~150K conversation 累积 + 50K safety = 232K headroom occupied → 单次 bulk injection 上限 **~40K tokens**, 严于 Sonnet/Opus 路径。该 40K 是逐次注入上限, **不是单 turn input 上限** — 后续 turns cumulative 仍可继续增长直到逼近 272K
- **Recommendation**: protection rule 在 Codex 路径下 HARD + add new sub-rule "若估算 session cumulative input + 当前 turn injection > 250K, refuse dispatch + escalate"

### 4.5 Axis E — Cache 经济 (cross-axis)

详见 §3.4。**与 model tier 无关**: bulk injection 永远抢占 cache slot, 即使 1M ctx 物理上能装下也不应该装。这是 protection rule 的**长效经济动机**, 不会随 ctx 扩张被淘汰。

### 4.6 Axis F — #361 cost ceiling

- 默认 ceiling: env `MERCURY_SESSION_COST_CEILING_USD` 未设时 statusline advisory 不触发
- 实测 single Opus 4.7 session 高位 = $88 (300 turns, 8 hour timespan)
- 1M ctx flat 单 turn 最大 cost: 1M × $5/MTok = $5 (input) + 128K × $25/MTok = $3.2 (output) = **$8.2/turn 上限**
- **Threshold 调整建议**: 默认 ceiling 设为 **$50** (相当于 6 满载 turn 或 1 工作 session) 是合理的初始 advisory line。**本 ADR 不实施, 列入 §8 follow-up Issue 候选**。

### 4.7 Axis G — #362 sub-agent return-size scoping

- 当前 enforcement: autoresearch return slim ≤ 2K main-ctx delta + dev-pipeline blind receipt
- 1M norm 后 main ctx 富余, **理论上**可放宽 return 阈值到 ≤ 10K
- 但 cache 经济 (§4.5) 与 dev-pipeline 双向对称性 (acceptance 仍需 blind receipt) 决定 **return 阈值不应放宽** — main-side 装得下不等于经济上合算, 且 sub-agent dispatch 上下文流要 mirror return 流的 lean style
- **Recommendation**: #362 阈值 **保持 ≤ 2K**, 不调整

---

## 5. Decision Matrix

| 决策项 | 选项 | 评估 |
|---|---|---|
| Keep `feedback_context_protection.md` rule | ✅ Yes | ctx 仅是 4 重 driver 之一; cache / Haiku / GPT-5.5 / Opus tokenizer 单独都足够支撑 rule |
| 软化为 "建议而非强制" | ❌ No | Haiku 路径 + Codex >272K 都是硬经济边界, 不能软化 |
| 添加 model-tier 分层条款 | ✅ Yes | Haiku HARD / Sonnet/Opus SOFT / Codex HARD-with-budget |
| 调整 #362 阈值 | ❌ No | cache 经济 + acceptance 对称性都需要保持 lean |
| 调整 #361 ceiling 默认 | ⏸ Defer | 单独 P3 follow-up issue, 不在本 ADR scope |
| 调整 dual-verify / dev-pipeline token budget | ❌ No | 当前 TaskBundle 已 lean, 无 bulk injection 实际发生 |
| Update `feedback_context_protection.md` body | ✅ Yes | 添加 model-tier 表 + cache 经济 rationale + Codex 272K 警告 |

---

## 6. Verdict + Recommendations

**Verdict**: (a) keep core rule + relax single-axis threshold for Sonnet/Opus parent + add model-tier 分层条款 + add cache 经济动机说明。

### 6.1 立即生效 (本 ADR 落地)

1. `.mercury/docs/research/context-strategy-2026-05.md` (本文件) 作为 canonical reference
2. Issue #385 CLOSED with summary comment 引用本 ADR + 5 re-eval triggers (per §7)

### 6.2 Follow-up (post-ADR, not in scope)

1. `feedback_context_protection.md` body update — 添加本 ADR §4 model-tier 分层表 + §3.4 cache 经济动机段落 (走 user-level governance per #259 pattern, 本 ADR 不修改 user-memory 文件)
2. 单独 P3 follow-up (可选): `MERCURY_SESSION_COST_CEILING_USD` 默认值 advisory 设定 (§4.6)
3. dev/research subagent.md frontmatter 一致性 audit — 是否所有 sub-agents 明确 declare `model:` field (否则继承父级 Opus, 与 dispatch lean-style 假设不符)
4. 跨 session 多 model 抽样 — 当前 sample 仅 1 session 详查; 后续可批量解析 19 sessions 的 `models{}` dict 得到完整 model mix histogram

---

## 7. Re-eval Triggers

任一触发即重开本 ADR:

1. **Haiku tier 提升到 ≥1M ctx** (目前 200K) — 单独消除 Axis C 主要约束
2. **OpenAI 取消 GPT-5.5 >272K surcharge** (变成 flat-rate 全段) — 单独消除 Axis D 主要约束
3. **Mercury 实测 P99 session cumulative input 持续超 500K** — measurement 定义 (canonical, 与 §1 TL;DR Monitor 一致): session-aggregate 跨 turns `input_tokens + cache_1h_tokens + cache_5m_tokens + 0.1 × cache_read_tokens` (cache_read 固定按 0.1x 加权, 反映 Anthropic prompt cache 实际经济计费比例, **非可选**); 3 session+ rolling 窗口持续超过 → 用量真正逼近上限。**不是** §3.1 的 last-turn delta `input_tokens` 单字段 (该字段不可累加)
4. **Anthropic Opus 5.x 撤销 up to 1.35x tokenizer 膨胀** (回归与 4.6 等效) — Axis A 容量边界改善
5. **Anthropic / OpenAI 公开 prompt cache 经济模型重大变更** (e.g., write 1h premium 取消) — Axis E 动机消失

---

## 8. Out of Scope (Follow-up Issues)

- ⏸ `feedback_context_protection.md` user-memory body update (走 #259 governance pattern, 单独 PR / commit-comment)
- ⏸ `MERCURY_SESSION_COST_CEILING_USD` default 设定 (P3 advisory)
- ⏸ Sub-agent frontmatter `model:` declaration consistency audit (P3)
- ⏸ 跨 19 sessions model mix histogram (P3, schema 解析变更)
- ⏸ DIRECTION.md §模块 2 已在 S103 #397 重写, 与本 ADR 正交

---

## 9. References

### 9.1 Official vendor docs (verified 2026-05-17)

- [Claude API Docs — Models overview](https://platform.claude.com/docs/en/about-claude/models/overview) — 全 family ctx + max output + 模型 alias 表
- [Claude API Docs — What's new in Claude Opus 4.7](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-7) — 1M GA + tokenizer up to 1.35x + 128K max output
- [Claude API Docs — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — cache TTL 5m/1h + 折扣 multiplier
- [Claude API Docs — Pricing](https://platform.claude.com/docs/en/about-claude/pricing) — Opus 4.7 $5/$25, Sonnet 4.6 $3/$15, Haiku 4.5 $1/$5
- [Claude API Docs — Context windows](https://platform.claude.com/docs/en/build-with-claude/context-windows) — Haiku 200K cap
- [Anthropic — Claude Sonnet 4.6](https://www.anthropic.com/claude/sonnet) — 900K 与 9K 同 per-token rate
- [OpenAI Developers — GPT-5.5 Model](https://developers.openai.com/api/docs/models/gpt-5.5) — 1.05M ctx, >272K 2x/1.5x surcharge
- [OpenAI Developers — GPT-5.5 Pro Model](https://developers.openai.com/api/docs/models/gpt-5.5-pro) — 1.05M ctx / 128K max output (Pro 单独 spec page)
- [OpenAI — Introducing GPT-5.5](https://openai.com/index/introducing-gpt-5-5/) — release 2026-04-24

### 9.2 Cross-check (third-party 二手)

- [EvoLink — GPT-5.5 API Pricing Guide 2026](https://evolink.ai/blog/gpt-5-5-api-pricing-guide-2026) — 272K surcharge 整 session 适用 confirmation
- [Finout — Claude Opus 4.7 Pricing](https://www.finout.io/blog/claude-opus-4.7-pricing-the-real-cost-story-behind-the-unchanged-price-tag) — tokenizer 膨胀实际开销分析

### 9.3 Mercury 内部参考

- `~/.claude/projects/D--Mercury-Mercury/memory/feedback_context_protection.md` (under review)
- `~/.claude/projects/D--Mercury-Mercury/memory/research/tech-intel-sweep-2026-05-12.md` (parent intel sweep)
- [Issue #381 tech intel sweep](https://github.com/392fyc/Mercury/issues/381) (parent)
- [Issue #361 cost-tracker](https://github.com/392fyc/Mercury/issues/361) + `~/.claude/scripts/cost_tracker.py` PRICING table
- [Issue #362 sub-agent return-size scoping](https://github.com/392fyc/Mercury/issues/362)
- [PR #397 DIRECTION.md §模块 2 rewrite (S103 merged)](https://github.com/392fyc/Mercury/pull/397) — 相关但正交
- [`.mercury/docs/research/cma-memory-vs-mem0-2026-05.md`](./cma-memory-vs-mem0-2026-05.md) (S102 sibling ADR, 同期同模板)
