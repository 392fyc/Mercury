# 大规模 fan-out 的 context 经济学护栏 + skill 迁移决策基线

> 立项 [#485](https://github.com/392fyc/Mercury/issues/485)(umbrella [#478](https://github.com/392fyc/Mercury/issues/478) harness 现代化 **P2**)
> Canonical 母 ADR: [`context-strategy-2026-05.md`](../research/context-strategy-2026-05.md)([#385](https://github.com/392fyc/Mercury/issues/385),生效中)
> 模板库: [`.claude/workflows/README.md`](../../../.claude/workflows/README.md) · 选型矩阵: [`.claude/agents/main.md`](../../../.claude/agents/main.md) §编排升级([#480](https://github.com/392fyc/Mercury/issues/480))
>
> **本文定位**:把 #385 ADR 的 context 经济学结论**操作化下沉**成「大规模 fan-out 前必过的检查清单 + 规模上限规则」,并新增 #385 未覆盖的 **skill 迁移决策框架 + 回归基线**(防 P0 模板库无序侵蚀稳定 skill)。#385 ADR 仍是 canonical 数据/推导源,本文**不复制其推导**,只给可执行规则 + 回链。S1 模板库 README §385 给的是「**每个模板**内嵌哪 5 条」;本文给的是「**升级到任何大规模 fan-out 前**先过哪些 + 该 fan 多大 + skill 要不要迁」。

---

## 1. 适用范围与三文档分工

大规模 fan-out 的护栏分散在三处,职责不同,**勿混用**:

| 文档 | 职责 | 何时看 |
|---|---|---|
| **#385 ADR** `context-strategy-2026-05.md` | canonical 数据源 + per-axis 推导(model tier × vendor × cache) | 要追溯某条护栏「为什么」、或某阈值变更 re-eval 时 |
| **`.claude/workflows/README.md` §385 护栏** | 每个 **Workflow 模板**内嵌的 5 条硬约束(改模板时核对) | 写/改一个 `.claude/workflows/*.js` 时 |
| **本文** | 升级到任何大规模 fan-out **之前**的操作检查清单 + fan-out 规模上限规则 + skill 迁移决策/基线 | 决定「要不要 fan-out / fan 多大 / 某 skill 要不要迁 Workflow」时 |

**触发本文的场景**:准备跑 Workflow / Agent Teams / 手工多 subagent 并行,且规模超过「几个 agent」;或考虑把某个稳定 skill 改写成 Workflow 模板。单文件改、1-2 源查证、机械单步 → 不适用本文,走 subagent/skill。

---

## 2. 大规模 fan-out 前的 context 经济学检查清单

fan-out 前**逐条**过,任一不满足即调整方案(降规模 / 换 model 路由 / 减注入):

### 2.1 不 pre-inject 全量文档(永久有效,与 ctx 容量无关)

- agent 拿到的是**路径 + 任务**,自己去 Read/Grep/WebFetch,**绝不**把整文件内容塞进 prompt。
- 理由是 **cache 经济**而非 ctx 容量:bulk injection 永久抢占 cache slot,即使 1M ctx 物理装得下也浪费(#385 §3.4 / §4.5)。这是 `feedback_context_protection.md` 规则在 1M norm 下**仍 valid** 的根因。

### 2.2 per-model context cliff 表(web-verified 2026-05~06)

| 路径 / model | 硬边界 | 越界后果 | 注入切片建议上限 |
|---|---|---|---|
| **Haiku 4.5**(in-repo `game-researcher` / OMC writer 等 haiku-tier) | **200K ctx 硬 cliff**(无 1M) | 直接爆 context | **≤50K token**(留 system + cache + 后续 turn 余量) |
| **Sonnet 4.6**(dev/acceptance/critic/research/autoresearch worker) | 1M GA,**flat-rate** | 无 surcharge | ≤50K reference / 次 dispatch |
| **Opus 4.7/4.8**(main lane) | 1M GA,**flat-rate**,无 long-ctx premium | 无 surcharge | ≤100K / turn 完全安全 |
| **Codex GPT-5.5**(`.codex/` 路径) | **>272K input = 整 session 2x input + 1.5x output surcharge** | **一次越线整 session 计费翻倍** | **≤40K / 次注入**(逐次上限,非单 turn 上限);估算 session cumulative + 当前注入 >250K 则 refuse + escalate |

**tokenizer 膨胀**(影响所有 Claude 路径的 token 预算):Opus **4.7→4.8 共用 tokenizer**,官方表述 4.8 token 计数与 4.7「roughly unchanged」、Fable 5 与 4.8 亦同 tokenizer([whats-new-claude-4-8](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8))。故 #385 记录的「同文本最坏 ~1.35x 膨胀(1.0x–1.35x range,4.7 引入)」**沿用至 4.8**:估算注入预算时按最坏 1.35x 留余量,1M ctx worst-case 等效 ~750K 4.6-时代 token(worst-case 非 baseline)。

> **数字失效条件(防过期)**:本表的供应商阈值(Haiku 200K / GPT-5.5 272K / tokenizer 1.35x / cache multiplier)是 vendor-policy-sensitive 的快照(web-verified 2026-05~06)。**canonical 数据源是 [#385 ADR](../research/context-strategy-2026-05.md) §2 vendor 表 + §9 来源**,本表是其操作化镜像 —— 二者出现分歧以 #385 为准。失效/复核触发:**[#385 §7 re-eval triggers](../research/context-strategy-2026-05.md#7-re-eval-triggers) 任一命中即重核本表**(Haiku 升 1M / OpenAI 取消 272K surcharge / Opus 5.x 撤销 tokenizer 膨胀 / cache 经济模型变更);无 trigger 命中时,随 #385 ADR 的周期性 re-eval 一并复核(勿在本文独立追踪阈值,避免双源漂移)。

### 2.3 cache 拓扑污染(cross-axis,cost 主导项)

- cache 折扣:write 5m = 1.25x / write 1h = 2.0x / read = 0.10x(#385 §2.1)。
- **cumulative cache 才是 cost driver**:#385 §3.3 实测单 Opus session,`cache_1h` writes(50.8%)+ `cache_read`(33.7%)合占 cost 的 **84.5%**,raw input+output 仅 15.5%。
- bulk injection 推高 cumulative cache 体量 → 直接抬高 `cache_1h` write 计费基数。**fan-out 越大、注入越肥,cache 税越重**——这是「1000 subagent 各塞 50K 文档」最隐蔽的成本。

### 2.4 stage-model 路由核对

每个 stage 落到哪个 model,注入上限按 §2.2 该行取**最严**值。Workflow 默认继承会话 model 且只传路径,远低于各上限;但若脚本 `opts.model` 显式路由到 haiku 或改造去调 Codex 路径,**必须**重新按该路径上限核对。

---

## 3. fan-out 规模上限规则(1000 ≠ 应该用满)

### 3.1 runtime 兜底 ≠ 目标

- Workflow runtime 硬上限:**≤16 并发 / 1000 agent per run**。这是 **runaway 兜底**(防脚本死循环失控),**不是**「应该 fan 到 1000」的指标。
- 撞到 1000 会**中途截断**——超出的 agent 不跑,工作量静默丢失。所以规模必须由**任务**定,而非由上限定。

### 3.2 按任务复杂度定规模(budget-scaling)

沿用 `main.md` §编排升级 budget-scaling 表:

| 任务类型 | 规模 | 不要 |
|---|---|---|
| 事实核查 / 单点查证 | 1 agent × 3-10 calls | 不 fan-out |
| 直接比较 / 选型 | 2-4 agent(每视角一个) | 不上 Workflow |
| 复杂研究 / repo 级审计 / 大迁移 | 10+ agent(Workflow) | 不盲目逼近 1000 |

预算可由 budget directive 动态伸缩:`const FLEET = budget.total ? Math.floor(budget.total / 100_000) : 5`。

### 3.3 模板必做总量自限 + 不静默截断

- 每个模板设**显式 cap**(`ANGLE_CAP`/`PRACTICE_CAP`/`BATCH_CAP`/`MAX_ROUNDS`),并按 cap 组合算**最坏 agent 数**钳到 `AGENT_BUDGET`(现 `codebase-audit`/`large-migration` 用 800,留余量不撞 1000)。
- **被丢弃的工作量一律 `log()`**:静默截断(top-N / no-retry / sampling)会被误读成「全覆盖」。cap 命中、dedup 丢弃、cap 之下 lower-confidence 丢弃——全部 `log()` 出来。

### 3.4 先小切片试估开销

大 run 前先在**单目录 / 窄问题**上跑一次估 token 开销,`/workflows` 实时看 token / agent / 耗时;再决定全量规模。一次 run 可能比对话方式多用数倍 token(#385 cache 税在 fan-out 下放大)。

---

## 4. skill 迁移决策框架(NL-skill ↔ Workflow)

> #385 未覆盖的新领域。P0 模板库上线后,存在「把稳定 NL-skill 无序改写成 Workflow」的侵蚀风险(调查文档 §不建议吸收 #7 已警示「勿全量重写」)。本节给判定标准,§5 给回归基线。

### 4.1 默认 = 不迁

**skill 稳定性优先**。一个已固化、被依赖的 NL-skill(dual-verify/pr-flow/dev-pipeline…)默认**保持自然语言形态**。迁移是 opt-in,必须过 §4.3 decision gate。「能并行」不是迁移理由。

### 4.2 判定矩阵

| 维度 | 保持 NL-skill | 迁 Workflow |
|---|---|---|
| 规模 | 几个 agent / 单 context 装得下 | 几十到几百 agent / context 装不下 |
| 编排形态 | **自适应**(Claude 按中间结果逐轮决定下一步) | **确定性**(fixed fan-out/pipeline/judge shape) |
| 中间结果 | 留 Main context 即可 | 需留脚本变量(量大/要复用) |
| 复用价值 | 流程靠提示词即可复述 | 编排本身值得沉淀成可重跑脚本 |
| 现有痛点 | 无串行瓶颈 | 现状是串行单 context,fan-out 能实质改善(如 autoresearch 串行 loop) |

**典型保持 NL**:`dev-pipeline`(Main→Dev→Acceptance,几个 agent + 自适应)、`dual-verify`(2 并行 reviewer)、`systematic-debugging`/`handoff`(自适应)、`caveman-toggle`/`kb-lint`/`pr-flow`(状态切换 / 单命令轮询)。
**候选可迁**:需 ≥3 源交叉核查的研究、repo 级审计、大规模迁移——但这些**已有**对应 Workflow 模板(`mercury-*`),无需再迁既有 skill。

### 4.3 decision gate(迁移任一 skill 前,PR body 必须逐条作答)

1. **装不下证据**:任务是否真的超一个 context?给出实证(真实 run 触发 compaction / 串行瓶颈),不是「理论上能并行」。
2. **确定性编排**:编排是否 fixed-shape(非 Claude 逐轮决定)?自适应流程迁 Workflow 会丧失灵活性。
3. **#385 护栏**:Workflow 版是否遵守 §2 全部检查 + §3 规模上限?
4. **行为等价回归**:Workflow 版在一个基准 case 上产出质量是否 **≥** 原 NL-skill?附对比。
5. **触发不冲突 + 可回退**:新 `/name` 命令是否不遮蔽原 skill 触发词?**保留原 NL-skill ≥1 个 release 周期**作回退,迁移走独立 PR + dual-verify。

任一答「否」→ 不迁。

---

## 5. skill 迁移回归基线(2026-06-20 快照)

> 防侵蚀的可核对基线:下列稳定 skill **不得**被删除/掏空改投模板,除非对应 PR 通过 §4.3 decision gate。

### 5.1 当前稳定 Mercury 自有 skill(`.claude/skills/`,NL 形态,**13 个**)

`animate-frames` · `autoresearch` · `caveman-toggle` · `dev-pipeline` · `dual-verify` · `gh-project-flow` · `handoff` · `kb-lint` · `pr-flow` · `subagent-driven-development` · `systematic-debugging` · `verification-before-completion` · `web-research`

### 5.2 当前 Workflow 模板(`.claude/workflows/`,**4 个**,P0 #479 新建,非从 skill 迁来)

`mercury-adversarial-plan-review` · `mercury-codebase-audit` · `mercury-large-migration` · `mercury-multi-source-research`

### 5.3 侵蚀检测规则(回归基线 invariant)

1. **NL-skill 计数下限 = 13**:`.claude/skills/` 的**子目录数**(只数目录,不数散落文件)**不得低于 13**,除非某次下降由一个通过 §4.3 decision gate 的 PR 显式解释。核对命令(只数目录 → 误放的非目录文件不会掩盖被删 skill;给两种环境形态):
   - PowerShell(Windows 主环境):`(Get-ChildItem .claude/skills -Directory).Count` ≥ 13
   - bash / git-bash:`ls -d .claude/skills/*/ | wc -l` ≥ 13(`*/` glob 只匹配目录)
2. **迁移留痕**:任何把 `.claude/skills/<name>/` 改写为 `.claude/workflows/<name>.js`(或为模板掏空一个 NL-skill)的 PR,必须:(a) PR body 答完 §4.3 五问;(b) 保留原 NL-skill ≥1 release 周期;(c) 走 dual-verify;(d) 在本表 §5.1/§5.2 记录迁移(基线随之更新)。
3. **模板不夺触发词**:新增 Workflow 模板的 `meta.name` / 触发词不得遮蔽既有 skill 触发词(`dual-verify`/`pr-flow`/`autoresearch` 等),避免用户调用被静默改路由。
4. **基线更新**:每次新增/迁移/删除 skill 或模板,**同一 PR** 内更新本节快照 + 日期,使本基线始终反映 ground truth。

> **可执行校验 vs 人工约束**:§5.3-1 的两条命令**就是**该 invariant 的可执行检查 —— 复制即可跑(operator 手动 / pre-migration / CI step 均可),返回 <13 即触发审查。当前为**人工/按需执行**(docs 基线 deliverable 的设计边界):规则 #2/#3/#4 是 PR review 人工 gate,#1 是机械可核命令。把 #1 接进 CI / `.claude/hooks/`(如 PR 时自动 assert ≥13 并比对 §5.1 清单)是**已识别的未来增强**,留待 #486(settings.json/hook 试点)批次评估 —— 本文不在 docs PR 内引入 CI 代码以免 scope 蔓延。届时 invariant 数字与命令已 ready,接线零返工。

---

## 6. 回链

- canonical 数据/推导:[`context-strategy-2026-05.md`](../research/context-strategy-2026-05.md)(#385,re-eval triggers 见其 §7)
- 每模板 5 条护栏:[`.claude/workflows/README.md`](../../../.claude/workflows/README.md) §385 护栏
- 选型矩阵 + budget-scaling:[`.claude/agents/main.md`](../../../.claude/agents/main.md) §编排升级
- 触发方式 + 顶层约束:[`CLAUDE.md`](../../../CLAUDE.md) §Ultracode 与 Dynamic Workflows
- 调查母文档:[`harness-modernization-survey-2026-06.md`](../research/harness-modernization-survey-2026-06.md)(§不建议吸收 #7「勿全量重写 skill」)

### 来源(web-verified)
- [Claude API Docs — What's new in Claude Opus 4.8](https://platform.claude.com/docs/en/about-claude/models/whats-new-claude-4-8)(同 tokenizer 4.7→4.8、1M flat-rate、$5/$25、effort 默认 high)
- [Claude API Docs — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)(cache TTL + 折扣 multiplier)
- [OpenAI Developers — GPT-5.5 Model](https://developers.openai.com/api/docs/models/gpt-5.5)(>272K 2x/1.5x surcharge)
- #385 ADR §9 完整 vendor 核验来源表
