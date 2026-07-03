# Issue #535 — 高效使用 Fable 5 周额度:调度策略 ADR + statusline 额度显示

- **状态**:ACCEPTED(2026-07-04)
- **Lane**:main(harness / orchestration 域)
- **关联**:#478 harness 现代化(umbrella)· #480 四原语选型 · #482 cost-tracker Fable 条目 · #280 advisor 调研(CLOSED)· #506 advisor pilot(OPEN)· #259 用户级变更治理 · #361 cost-tracker · #333 statusline
- **grounding**:两个 research agent(官方一手核验)+ 内部盘点,结论写入 #535 正文与本文档 §3.1 / §2.1;不重复推导。

---

## 0. 决策摘要(TL;DR)

用户「一直直接用 Fable 5 推进任务」并撞到 Fable 5 周额度上限。本 ADR 给两件事定案:

**Part A — statusline 显示 Fable 额度**:官方 statusline schema **没有** per-model / Fable 专属字段,Claude Code 侧**没有任何官方途径**能精确读到「Fable 5 已用掉多少周额度」。因此:
- **(a) 精确显示 = 上游功能请求**,记为监控项(等 Anthropic 加字段,唯一能精确的正路)。
- **(b) 本地近似告警 = 可落地的过渡方案**:用 cost-tracker 累计「本周 Fable 估算花费」,在 statusline 显示 + 可选软预算颜色告警。**诚实标注这是客户端估算,不等于服务端真实额度百分比**。默认 env-gated(不打扰),沿用 #361 ceiling 的既有模式。

**Part B — 省 Fable 的调度策略**:Fable 消耗的**真正根源是会话主模型选了 Fable 5**(`main=inherit` → 主循环全程烧 Fable,inherit 的 subagent 也烧),不是 agent 定义(盘点确认:没有一个 agent 硬编码 `model: fable`)。据 Anthropic 官方 Routing + Orchestrator-Workers 范式,落一套**六层分层策略**:主循环默认 Opus 4.8,Fable 只留给最难环节;Workflow per-stage 只最硬 stage 用 fable;advisor 模式指向 #506 pilot(不越界接线)。

---

## 1. 问题与背景

### 1.1 Fable 5 额度经济学(已核实)

- Fable 5 = `claude-fable-5`,$10/M 输入、$50/M 输出(≈ Opus 4.8 的 2 倍),1M context。
- **当前条款**(官方 <https://www.anthropic.com/news/redeploying-fable-5>):Pro/Max/Team 及部分 Enterprise「included for up to 50% of weekly usage limits **through July 7**」,7/8 起转 **usage credits**(按量计费)。
- 含义:存在一个 Fable 专属的周用量子上限(共享周池的 50% 份额)。你可以在别的模型还有周额度时,先把 Fable 这 50% 用光——与用户体验吻合。Anthropic **不公布**各套餐的绝对周额度数值。

### 1.2 消耗根源(本 ADR 的关键定位)

盘点 `.claude/agents/*.md` 的 `model:` 字段:

| agent | model | agent | model |
|---|---|---|---|
| design | opus | critic | opus |
| dev | sonnet | acceptance | sonnet |
| research | sonnet | game-analyst / game-critic | sonnet |
| game-researcher | haiku | **main** | **inherit** |

**没有一个 agent 硬编码 `model: fable`。** 所以 Fable 的消耗不来自 agent 定义,而来自**会话主模型**:用户在 Claude Code 里把 `/model` 选成 Fable 5,于是:
1. 主循环(Main 编排推理)全程烧 Fable —— 这是 token 量最大的一块;
2. `main=inherit` 让 Main 继承会话模型 = Fable;
3. 任何 `model: inherit` 的 subagent 也继承 Fable。

**结论:省 Fable 的最大杠杆是「会话主模型默认别用 Fable」,不是改 agent 定义。** agent 分层现状已经是好的(便宜档为主),问题在主循环模型选择 + 缺少「何时才升级到 Fable」的约定。

### 1.3 Mythos 5 非杠杆(grounding 结论)

Mythos 5 = Fable 5 的去安全分类器孪生版,Project Glasswing 邀请制、同价、不在 Claude Code。**不是节约杠杆**。真正的便宜档是 Opus 4.8(`claude-opus-4-8`,$5/$25)、Sonnet 5(`claude-sonnet-5`,$3/$15)、Haiku 4.5(`claude-haiku-4-5`,$1/$5)。

---

## 2. Part A — statusline 显示 Fable 额度

### 2.1 硬约束:精确显示不可行(官方一手核实)

statusline 命令收到的 usage JSON(<https://code.claude.com/docs/en/statusline>)里与额度相关的字段**全部**是账户级聚合,没有任何 per-model 拆分:

- `rate_limits.five_hour.{used_percentage,resets_at}` — 5 小时窗口,聚合。
- `rate_limits.seven_day.{used_percentage,resets_at}` — 7 天(周)窗口,聚合。
- `cost.total_cost_usd` — session 聚合花费(混合计价)。
- `model.{id,display_name}` — 当前模型标识(不带该模型的额度数字)。

`rate_limits` 只有 `five_hour` / `seven_day` 两档,各自只有 `used_percentage` + `resets_at`,**不区分**是 Opus、Sonnet 还是 Fable 消耗的。核查 support.claude.com 的 usage-limits 文档 + `/cost` / `/model` 命令,均无 per-model 用量查询能力。

**定性结论:目前(2026-07)Claude Code 侧没有任何官方数据源(statusline 字段 / 环境变量 / CLI 命令 / 本地状态文件 / API endpoint)能精确读到「Fable 5 剩余周额度」。** 用户看到的「5h + 周额度」是 Claude Code 原生用量指示器的聚合值,无法反推 Fable 单独用量。

### 2.2 决策

- **(a) 精确显示 → 上游功能请求 + 监控**:在 statusline schema / CLI 里加 per-model 或 Fable 专属额度字段,是唯一能精确显示的正路,但依赖 Anthropic。记为监控项(periodically 查 <https://code.claude.com/docs/en/changelog> 的 statusline schema 演进);本 ADR 不实现精确显示,避免建立在不存在的字段上。
- **(b) 本地近似告警 → 落地(可选,env-gated)**:用 cost-tracker 已持久化的 per-model 花费数据,累计「本周 Fable 估算花费」,在 statusline 显示 + 可选软预算告警。

### 2.3 本地近似方案设计

**实证基础**(已验证):cost-tracker 的每个 session jsonl summary 里 `models` 字段按 model ID 拆分 `turn_count` + `total_usd`(实测 97 个历史 jsonl 结构一致)。所以「本周 Fable 花费」= 遍历 7 天窗口内各 session jsonl,累加 `models["claude-fable-5"].total_usd`(按 fable 前缀匹配,兼容 date-stamped 变体)。

改动(用户级 `~/.claude/`,走 #259 治理,**不进 Mercury PR**):

1. **`cost_tracker.py`**:
   - 新增 `fable_window_total_usd(window_seconds=7*24*3600)`:复用 `window_total_usd` 的 7 天窗口遍历逻辑,只累加 fable 家族的 per-model `total_usd`。
   - 更新 L40-43 的 **stale 注释**:现注释说 Fable「TEMPORARILY DISABLED as of 2026-06-20 ... DORMANT」——已过时(Fable 于 2026-07-01 官方 redeploy 重新可用,用户 7/2 起选用)。改为反映「active,$10/$50,50%-of-weekly through 7/7 后转 usage-credits」的现状 + 7/7 条款提醒。
2. **`statusline-context.sh`**:
   - 在 cost 段后追加可选 Fable 段:当 `MERCURY_FABLE_WEEKLY_BUDGET_USD` 设置时,显示 `Fable ¤X.XX/wk`(本周 Fable 估算花费),并按占预算比例走颜色阶梯(绿 <70% / 黄 70-89% / 红 ≥90%),沿用 #361 ceiling 的既有 pattern。
   - **默认不显示**(env 未设 = no-op),避免打扰;精度限制在 ADR + 注释里写明。
3. **诚实标注**:段落是「本地估算」,不是服务端真实额度百分比;7/7 后条款切换(usage-credits)会改变「50% 周额度」这个隐含分母,届时软预算数值需用户按新计费重设。

**为什么不做进度条对齐「50% 周额度」**:Anthropic 不公布各套餐周额度绝对值(分母未知),任何「已用 X% of 50%」都是猜测。软预算($ 阈值,用户自设)比伪精确的百分比进度条更诚实、更实用。

---

## 3. Part B — 省 Fable 的调度策略

### 3.1 外部实践综合(全部 web 验证;引用见 §6)

| 实践 | 来源权威性 | 对本任务的映射 | 采纳 |
|---|---|---|---|
| **Anthropic Routing 范式** | 官方一手 | 便宜模型接常规、贵模型(Fable)接难点 | **采纳(核心依据)** |
| **Anthropic Orchestrator-Workers** | 官方一手 | 贵模型管规划/裁决/汇总,便宜模型跑量;与 Mercury main/side 同构 | **采纳(核心依据)** |
| **Claude Code 原生 `model:` frontmatter** | 官方文档 | `haiku/sonnet/opus/fable/inherit`,官方把「用 Haiku 省成本」列为设计目的;优先级 env > per-invocation > frontmatter > 主会话 | **采纳(落地手段)** |
| **`/effort`(low/medium/high/max/xhigh)** | 官方文档 | 同模型内 thinking token 调节,正交于模型选型;用 Fable 时默认低/中 effort | **采纳(第二层杠杆)** |
| **advisor 工具(`advisor_20260301`)** | 官方 + #280/#506 | cheap-main + strong-advisor 原生能力。**#506 既定 pilot = Opus-advisor**;省 Fable 的扩展设想 = Opus-main + Fable-advisor(Fable-as-advisor CLI 支持性 UNVERIFIED) | **pilot → #506;Fable-advisor 为可选扩展验证点** |
| **advisor-strategy-skill(MIT)** | GitHub | 触发条件清单 + 1000-3000 token 上下文预算硬控 | **结构借鉴 / 可 cherry-pick(归 #506)** |
| FrugalGPT cascade | 论文 | 逐档升级、够用就停;需自定义「够用」信号 | 需改造(记为思路) |
| aider Architect/Editor | 项目文档 | 贵出方案、便宜落地;目的是分工非省钱 | 需改造(记为思路) |
| RouteLLM(Apache-2.0) | 论文/repo | 分类器+阈值路由;依赖训练管线 | 不引入(只借「阈值路由」概念) |
| OpenRouter Auto Router | 产品文档 | 多供应商网关能力 | 不适用(走原生分层) |
| **Task Budgets API(`task-budgets-2026-03-13`)** | 官方文档 | **官方明确「not supported on Claude Code」** | **排除(此路不通)** |
| Karpathy 模型分层论述 | — | 未找到确切一手出处;autoresearch 是**算力**分层非模型分层 | 不署名归因 |

> **cherry-pick 合规提示**:表中标「可 cherry-pick」的外部实现(advisor-strategy-skill MIT、RouteLLM Apache-2.0 等),真正引入代码时**必须走 Mercury cherry-pick 协议**(`.mercury/state/upstream-manifest.json` 条目 + SKILL.md frontmatter 归属 + SHA 核实 + license 门槛,见 CLAUDE.md §Cherry-pick protocol)。本 ADR 仅为**策略研究,未引入任何外部代码**,故无 manifest / 归属改动;advisor 相关落地一律归 #506 pilot,届时按协议补齐合规。

### 3.2 Mercury Fable 节约策略(六层)

按「把 Fable 留给最难环节,其余用便宜档」的原则,从大杠杆到补充杠杆:

- **L0 — 会话主模型默认 Opus 4.8,Fable 靠显式升级(最大杠杆)**
  主循环 token 量最大。日常编排 / dev-pipeline / 研究综合用 Opus 4.8 做 driver;只在遇到「最难环节」(见 L2 清单)时会话内 `/model fable` 临时切,用完切回。这是**给用户的工作习惯建议 + Mercury 用 statusline 预警(Part A)+ 分层约定(本节)支撑**,不是强制改配置(Mercury 不能替用户选 `/model`)。

- **L1 — subagent `model:` 分层(现状已达标,固化为有意设计)**
  design/critic=opus、dev/acceptance/research=sonnet、game-researcher=haiku、main=inherit。没有 agent 硬编码 fable = 有意的省 Fable 设计。**约定:新增 agent 默认不写 `model: fable`;需要 Fable 级能力时优先 opus,确有必要再显式 fable 并在 PR 说明理由。**

- **L2 — Fable 只用于「最难环节」(明确清单)**
  架构综合 / 长程多步裁决 / Argus 反复卡不过的复杂 review 的深度分析 / 跨仓库不可逆决策的方案推演 / 需要 1M context 一次性容纳的超大上下文综合。**其余一律 Opus 4.8 及以下。**

- **L3 — Workflow per-stage 只最硬 stage 用 fable**
  当前 Workflow 模板默认继承会话 model。**风险:若会话主模型是 Fable,整个 Workflow 数十个 agent 全烧 Fable**(finder/机械 stage 本该 sonnet/haiku)。约定:模板里 finder / 机械 / 分类 stage 用 `opts.model:'sonnet'` 或 `'haiku'`,只有最硬的 judge / synthesis / adversarial 裁决 stage 才 `opts.model:'fable'`(或不指定 fable、用 opus)。把 Fable 周额度花在刀刃上。

- **L4 — advisor 模式(pilot-gated,归 #506,本 ADR 不接线)**
  原生 advisor 工具支持 cheap-main + strong-advisor。**#506 pilot 的既定配置 = Sonnet/Opus-main + Opus-advisor**(降本,避开 Haiku-main 选择器 bug),这是 #506 当前范围。**省 Fable 的扩展形态** = Opus-main + **Fable**-advisor(主循环 Opus,只在关键决策点 consult Fable),把 Fable 消耗从「全程」压到「仅 consult」——**但**:advisor 按 advisor 模型费率独立计费(Fable-advisor 每次 consult 烧 $10/$50),是否净省取决于 consult 频率;且 **Fable 作为 advisor model 是否被 CLI 支持 = UNVERIFIED,需 pilot 验证**。故 Fable-as-advisor 仅作 #506 的**可选扩展验证点**,不是其既定 pilot 范围。**本 ADR 只记录 advisor 为候选杠杆并指向 #506,不越界接线**(#506 约束:不写任何 Messages-API wrapper / MCP / skill 包装,advisor 是服务端单请求工具)。

- **L5 — `/effort` 第二层杠杆**
  即便某任务确定用 Fable,也默认给低/中 effort,只有真正需要深推理的裁决点升 high/max。模型选型(L0-L3)之外再省一层 thinking token。

### 3.3 接线边界(repo 内 vs 用户级)

| 改动 | 位置 | 提交路径 |
|---|---|---|
| 本 ADR 文档 | `.mercury/docs/research/issue-535-*.md` | worktree → PR → develop |
| CLAUDE.md「Fable 调度策略」小节 | `CLAUDE.md` | worktree → PR → develop |
| main.md §编排升级 加「模型分层 / Fable 节约」 | `.claude/agents/main.md` | worktree → PR → develop |
| workflows README 加「per-stage 模型分层」约定 | `.claude/workflows/README.md` | worktree → PR → develop |
| cost_tracker.py `fable_window_total_usd` + stale 注释 | `~/.claude/scripts/cost_tracker.py` | **用户级,#259 治理,不进 PR** |
| statusline-context.sh Fable 段 | `~/.claude/hooks/statusline-context.sh` | **用户级,#259 治理,不进 PR** |

---

## 4. 落地清单 + 验证

**repo 内(dual-verify + PR to develop)**:ADR + CLAUDE.md + main.md + workflows README。

**用户级(#259 治理,记录在 #535)**:
- 前置备份已建:`statusline-context.sh.backup-pre-535` / `cost_tracker.py.backup-pre-535`。
- 验证清单(#259 必过):① `settings.json` 未动(本次不改);② cost_tracker 在合成输入下 import + 函数 OK;③ statusline 在合成 stdin 下 exit 0(env 未设 = 无 Fable 段;env 设 = 有段且不崩);④ 一次真实 statusline 触发无回归。
- 回滚:`mv *.backup-pre-535` 回去即可。

---

## 5. 未决 / 后续

- **精确 Fable 额度显示**:上游功能请求 + 监控 changelog(§2.2a)。
- **advisor pilot**:#506 既定范围 = Sonnet/Opus-main + Opus-advisor(降本)。**Opus-main + Fable-advisor 的省 Fable 形态**(Fable-as-advisor CLI 支持性 UNVERIFIED)可作为 #506 的**可选扩展验证点**;cost-tracker 是否需拆 `advisor_message` 子推断 token 见 #506。
- **7/7 usage-credits 切换**:7/8 后 Fable 计费从「周额度 50% 份额」变按量,软预算分母语义变化,需用户按新计费重设 `MERCURY_FABLE_WEEKLY_BUDGET_USD`。
- **$10/$50 价格**:platform.claude.com/pricing 已核实(cost_tracker 现值);redeploying-fable-5 公告页本身未列价格数字(不影响,pricing 页为准)。

---

## 6. Sources(已核验)

- <https://www.anthropic.com/research/building-effective-agents>(Routing + Orchestrator-Workers 官方范式)
- <https://code.claude.com/docs/en/sub-agents>(`model:` frontmatter + 优先级)
- <https://platform.claude.com/docs/en/build-with-claude/effort>(/effort 力度分层)
- <https://platform.claude.com/docs/en/build-with-claude/task-budgets>(Task Budgets — 明确不支持 Claude Code)
- <https://code.claude.com/docs/en/statusline>(statusline usage schema — 仅聚合 five_hour/seven_day)
- <https://www.anthropic.com/news/redeploying-fable-5>(Fable 5 50%-of-weekly through 7/7 条款)
- <https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5>(Fable/Mythos 5 身份 + 定价)
- <https://arxiv.org/abs/2305.05176>(FrugalGPT cascade)· <https://arxiv.org/abs/2406.18665> + <https://github.com/lm-sys/RouteLLM>(RouteLLM,Apache-2.0)
- <https://aider.chat/2024/09/26/architect.html>(aider Architect/Editor)
- <https://github.com/aivsomkar/advisor-strategy-skill>(advisor-strategy-skill,MIT)
- 内部:#280(advisor 调研 CLOSED)· #506(advisor pilot OPEN)· `.claude/workflows/README.md` · `.claude/agents/main.md` §编排升级 · `~/.claude/scripts/cost_tracker.py`
