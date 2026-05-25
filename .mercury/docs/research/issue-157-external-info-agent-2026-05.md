---
issue: 157
title: "External Information Update Agent — SDK/库/开源项目变更自动追踪 (Phase 1 设计 ADR)"
date: 2026-05-24
session: S133 (design lane)
status: phase-1-design-deliverable
verdict: "推荐 hybrid 架构 — 确定性依赖检测交 GHA(Renovate/Dependabot)，LLM-判定+自动建 Issue 这层用 GHA scheduled workflow 跑轻量脚本+gh CLI；Routines 列为 Phase 3 候选(research preview，不做硬依赖)"
relation: "complements #157↔#92(已 Closed as not planned); 是 #381 tech-intel-sweep 手工先例的自动化"
research_protocol: "所有外部 SDK/服务能力对照官方文档核实，核实日期 2026-05-24；未核实项标 UNVERIFIED + 来源 URL"
---

# Issue #157 — 外部信息更新 Agent: Phase 1 设计 ADR

> **本 doc 是设计交付物，不实装、不改代码、不 dispatch 其它 agent。** Issue #157 保持 **OPEN**(设计阶段，非实现)。

## TL;DR / 结论

推荐 **hybrid 双层架构**，把 #157 拆成两类性质不同的工作:

1. **确定性依赖版本检测层 (no-LLM)** — 用 **Renovate**(优先)或 **Dependabot** 处理 Mercury 自身 `package.json` / `pyproject.toml` / `Cargo.toml` 里 pin 的依赖 bump。这层不需要 agent，是成熟工具的 commodity 能力，直接产出 PR。
2. **LLM-判定 + 自动建 Issue 层 (需要 agent)** — 这才是 #157 的真正新增价值: 监控 **未被 package manifest 捕获的外部信号**(Claude Code SDK / Anthropic API 文档变更、Tauri/Codex CLI release notes 的语义、以及 Mercury *参考过但未作为依赖 pin* 的 OSS 项目如 autoresearch / everything-claude-code / superpowers / openclaw 的新版本/架构变化)，由 LLM 判定"对 Mercury 是否重要"，再自动 categorize 成 GitHub Issue 带 priority/impact 标签。

**执行基座推荐: GitHub Actions scheduled workflow**(每日/每周 cron)跑一个轻量采集脚本 + 一次 LLM 判定 + `gh issue create`。理由见 §3 trade-off 表 — GHA 是**最契合**"确定性可调度 + 能访问 GitHub Issue 写 + 成本可控 + 不依赖 research-preview + 可剥离性最佳"的基座(NAS cron 同样确定性且能写 Issue,但绑定本机环境、可剥离性差,见 §3)。Routines 列为 Phase 3 增强候选(其 B3 nightly-triage PoC 模式与本 Issue 的自动建-Issue 输出直接邻近，但受 research-preview / quota / 访问不到本机路径三重约束，见 #289 doc)。

**推荐的 Phase 2 PoC**: 双源、不改业务代码、人工审 Issue —— 监控 **Claude Code SDK + Anthropic API 文档**两个源的 release/changelog，每周一次，LLM 判定显著性后建一条 *draft 性质*的 GitHub Issue(标 `intel/needs-triage`)，人工 review 后决定保留或关。"不改业务代码"指: 只写 last-seen 状态 + 建 `needs-triage` Issue,不修改任何 Mercury 功能代码、不推受保护分支(develop/master)、不依赖 custom agents。状态持久化机制见 §5.2(必须让下次 scheduled run 可读)。

---

## 1. 背景

### 1.1 #157 scope (摘自 Issue body，已核实)

来源: <https://github.com/392fyc/Mercury/issues/157> (P2 / Enhancement / OPEN)

一个自治 agent，周期性追踪两类外部信息:

- **(a) 依赖的 SDK/库** — 重点: Claude Code SDK、Anthropic API、Tauri、Codex CLI。判断更新是否解决已知痛点 / 带来新能力 / 与现有功能冲突。
- **(b) 参考过的 OSS 项目** — autoresearch、everything-claude-code、superpowers、openclaw 等的新版本/功能/架构变化，并记录新发现的有价值项目。

**输出**: 自动 categorize 成 GitHub Issue，标 priority + impact scope。

### 1.2 与 #92 的关系 — 前提需要修正

#157 body 说"与 #92(Internal Cron Agent)共享 cron-scheduling 基础设施，职责独立"。但核实发现 **#92 已 Closed as not planned**(来源: <https://github.com/392fyc/Mercury/issues/92>)。

#92 设想的执行模型(独立 role slot + 用 RPC `list_sessions`/`list_tasks` 自检 + Haiku 跑 ~5min)绑定的是 **Phase 4 之前的 session/task/RPC 架构**，那套已随 S75 orchestration reframe(main=部长 / side=开发小组 / Telegram=总裁窗口,见 **user-level memory** 的 `project_orchestration_reframe.md` —— 该文件在 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/`,**不在本 repo**)和 mem0 取代 RPC-KB 而过时。

**结论**: #157 **不应**把"复用 #92 的 cron 基础设施"当作设计前提。#92 已死，其内网 RPC 自检模型对 #157(纯外部信息 + 纯 GitHub 输出)也不适用。#157 的自然基座是 **GitHub-native 调度(GHA)**，不是 #92 设想的 NAS-internal RPC cron。本 ADR 据此独立选型。

### 1.3 #381 tech-intel-sweep — 手工先例

早稿曾以为该 doc 在 `.mercury/docs/research/tech-intel-sweep-2026-05-12.md`。**核实: 该文件不在 Mercury repo 内**(`Glob **/tech-intel-sweep*` 无结果)。MEMORY.md 的索引条目指向的是 **user-level memory 路径**,按 CLAUDE.md 约定为 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/research/tech-intel-sweep-2026-05-12.md`(注意含 `/memory/` 层),属用户私有记忆层,**不在本 repo**,本 design lane 无法读取。

可确认的事实(来自 MEMORY.md 索引行): #381 是一次 **手工的 3-周 Anthropic + OpenAI + GitHub 情报扫描**，产出 4 个 follow-up Issue(#382/#383/#384/#385)+ 2 个 defer note。**这正是 #157 要自动化的东西的人工先例** —— 即"周期性扫外部源 → 判定显著性 → 落成可执行 Issue / defer note"。

**对设计的启示**(从可确认的先例结构推导，未读到原文细节标 UNVERIFIED):
- 输出形态已被先例验证为可行: 显著发现 → 开 follow-up Issue；不值得跟进 → defer note(带 re-eval 条件)。#157 自动版应复刻这个二分输出(Issue vs defer note)，而非只会建 Issue。
- 扫描源涵盖 Anthropic + OpenAI + GitHub 三大类，与 #157 的 SDK + OSS 两类高度重合。
- **UNVERIFIED**: #381 用的具体 source 清单、categorization taxonomy、Issue body 模板 —— 后续从 user-level memory 或 #381 Issue 本体提取,可**丰富** taxonomy/模板。

**repo 内可追溯的最小基线(不依赖 user-level memory,团队/CI 均可访问)**: Phase 2 **不被** user-level memory 阻塞 —— 启动所需的最小源清单已自包含在本 repo:
- **B 类源清单**: 见 §4.2 表(Claude Code SDK / Anthropic API 文档 / Tauri / Codex CLI / 参考 OSS: autoresearch、everything-claude-code、superpowers、openclaw)—— 直接取自 #157 Issue body(repo-tracked GitHub Issue)。
- **categorization 基线**: 见 §5.1 的固定 label 白名单(priority P1/P2/P3 + `impact/*` + `intel/*`)。
- **输出形态基线**: 见 §5.3 的 Issue-vs-digest 二分。

user-level #381 的 taxonomy/模板仅作 **enrichment(锦上添花)**,**非 Phase 2 前置阻塞项**。若该 memory 在实装时不可访问,以上 repo-内基线即足以启动 PoC。

---

## 2. 把问题正确切开: 确定性 vs LLM-判定

#157 最重要的设计判断是 **不要把所有东西都丢给 LLM agent**。两类信号性质不同:

| 信号类型 | 例子 | 检测机制 | 需要 LLM? |
|---|---|---|---|
| **A. 已 pin 依赖的版本 bump** | `@anthropic-ai/sdk` npm 版本、Tauri crate 版本、`pyproject.toml` 里的库 | Renovate/Dependabot 比对 manifest vs registry | **否** — commodity，直接产 PR |
| **B. 未 pin 的"软依赖"信号** | Anthropic API 文档新增端点、Claude Code SDK changelog 语义、Codex CLI release notes、参考过的 OSS 项目(autoresearch 等)的架构变化 | 抓 release/changelog/feed → LLM 判"对 Mercury 是否重要" | **是** — 这是 #157 真正的新增价值 |

**A 类已有成熟解，#157 不该重造**(违反 CLAUDE.md "No self-research" + DIRECTION.md "替代成本"评估)。**B 类才是 agent 的用武之地**: 一个 release note 本身是确定性可抓的，但"这个变化对 Mercury 重要吗、是什么 priority/impact、该不该开 Issue"是判断问题，需要 LLM + Mercury 上下文。

这个切分直接决定架构: **A 类 → 现成工具(Renovate);B 类 → 轻量采集脚本 + 一次 LLM 调用 + gh CLI 建 Issue**。

---

## 3. 执行基座 trade-off

核实日期 2026-05-24。各项能力对照官方文档。

| 维度 | GitHub Actions (scheduled) | Claude Code Routines | NAS cron + `schedule` skill | Hybrid (推荐) |
|---|---|---|---|---|
| **调度确定性** | 高 — POSIX cron，最短 5min 粒度，**仅按 UTC 解释**(GHA schedule 无 timezone 配置,需其它时区须自行换算)[1] | 中 — daily-run quota 限制(具体数值见 routines docs，#289 未独立复核) [#289] | 高 — 本机 cron 完全可控 | 高(GHA 主调度) |
| **访问 GitHub Issue 写** | 原生 — `GITHUB_TOKEN` / `gh` CLI 内置 | 经 GitHub MCP connector(需配置 + 授权) [#289] | 经 `gh` CLI(需本机已认证) | 原生(GHA) |
| **访问本机/NAS/`~/.claude` 路径** | 否 — runner 是 GitHub 云 | **否** — Anthropic 云，访问不到本机路径(#289 §3 已确认) | 是 — 本机执行 | #157 不需要本机路径，故非约束 |
| **LLM 判定能力** | 需在 workflow 内调 Anthropic API(自己付 token) | 原生(就是 Claude Code session) | 需本机调 API 或起 Claude Code | GHA 内调 Anthropic API(B 类只需 1 次/源/周，量极小) |
| **成本** | GHA 免费额度(公开 repo 免费;私有有分钟额度) + Anthropic API token | 计入 Routines quota + token | 本机电费(已有 NAS) + token | 低 — GHA 调度免费 + 极少 token |
| **research-preview 风险** | 无(GHA GA) | **有** — 仍 research preview，API 可能变(有 2-版本兼容窗口) [#289] | 无 | 无(不依赖 Routines) |
| **custom agents 依赖** | 不涉及 | 加载 `.claude/agents/*.md` **UNVERIFIED** [#289 Q2] | 本机可用 | 不依赖(B 类用单次 API 调用，非 subagent) |
| **可剥离性 (DIRECTION.md)** | 高 — 一个 `.github/workflows/*.yml` + 一个独立脚本，删了即 no-op | 中 — Routine 定义在 Anthropic 云侧 | 中 — 耦合 NAS 环境 | 高 |

### 推荐: GitHub Actions scheduled workflow 作主基座

理由:
1. **#157 的所有输入输出都是 GitHub-native 或公网 HTTP** —— 抓 npm/PyPI/crates.io/GitHub release feed(公网)、调 Anthropic API(公网)、建 GitHub Issue(GHA 原生)。**没有任何一步需要访问本机/NAS/`~/.claude`**，所以 Routines 和 NAS cron 各自的核心优势(Routines=云端 Claude session / NAS=本机访问)对 #157 都不是必需，反而引入约束(Routines 的 research-preview + quota;NAS 的环境耦合 + 可剥离性差)。
2. **确定性 + 无 research-preview 风险**: GHA 是 GA 产品，cron 语义稳定[1]。
3. **可剥离性最佳**: 整个 #157 = 一个 workflow yml + 一个独立采集/判定脚本。符合 DIRECTION.md 模块化 + "独立可拆卸"要求，删除即彻底 no-op。
4. **A 类直接交 Renovate/Dependabot**(也跑在 GitHub 侧)，与主 workflow 同生态，运维面统一。

**Routines 的定位**: 列为 **Phase 3 增强候选**而非 Phase 2 基座。#289 doc 推荐的 B3 PoC(nightly Issue triage，只读 Issue + post comment)与 #157 的"自动建 Issue"输出语义高度邻近 —— 一旦 Routines 摘掉 research-preview 标签(#289 re-check trigger 之一)，可评估把 B 类的 LLM 判定从"GHA 内调 API"迁到 Routine(获得完整 Claude Code 上下文 + skill 访问)。但当前不做硬依赖。

**NAS cron 的定位**: 不用于 #157。#157 无本机访问需求，用 NAS cron 反而降低可剥离性、绑定单机环境。NAS cron 继续服务那些**确实需要本机访问**的任务(如 argus-selfcheck)。

---

## 4. 源监控设计

核实日期 2026-05-24。每个源的检测机制对照官方文档。**关键原则: 优先用结构化 API/feed，避免抓 HTML。**

### 4.1 A 类源 — 已 pin 依赖(交 Renovate/Dependabot，no-LLM)

| 源 | manifest | 检测机制 | 来源 |
|---|---|---|---|
| npm 包(如 `@anthropic-ai/sdk`、Tauri JS) | `package.json` | Renovate/Dependabot 比对 vs npm registry | [2] |
| PyPI 包 | `pyproject.toml` | Renovate/Dependabot 比对 vs PyPI | [2] |
| Rust crate(Tauri 后端) | `Cargo.toml` | Renovate/Dependabot 比对 vs crates.io | [2] |
| GitHub Actions 自身版本 | workflow yml | Dependabot `github-actions` ecosystem | [2] |

Dependabot 支持 30+ ecosystem(npm/pip/Cargo/github-actions/uv 等)，Renovate 支持 60+ 且 grouping 更灵活、可自定义 regex manager 抓非标准文件[2]。**推荐 Renovate**(grouping 减少 PR 噪音 + regex manager 能力为将来抓非标准 manifest 留余地)，但若想零外部服务 + GitHub-only 最省事，Dependabot 也够用。**二者皆为 commodity，#157 只需配置不需开发。**

### 4.2 B 类源 — 软依赖信号(采集脚本 + LLM 判定)

| 源 | 检测机制 | 端点/feed | 来源 |
|---|---|---|---|
| **Claude Code SDK / CLI** | npm 包元数据(若 npm 分发)+ 官方 changelog 页 | `GET /{package}` packument 解析 `dist-tags.latest`(或 abbreviated header)[3] | [3] |
| **Anthropic API 文档** | 文档/changelog 页变更检测(无结构化 feed → 内容 diff) | docs 页面快照对比(UNVERIFIED 是否有官方 changelog feed) | — |
| **Tauri** | GitHub Releases API + crates.io | `GET /repos/tauri-apps/tauri/releases`(免认证 60 req/h)或 crates.io sparse index | [4][5][6] |
| **Codex CLI** | GitHub Releases API + npm(若 npm 分发) | `GET /repos/<owner>/codex/releases`(REST, 已核实)| [4] |
| **参考过的 OSS**(autoresearch/everything-claude-code/superpowers/openclaw) | GitHub Releases/Tags API(REST 优先)+ atom feed(可选, UNVERIFIED) | `GET /repos/{o}/{r}/releases` + `GET /repos/{o}/{r}/tags`;atom feed 见检测要点 | [4] |

**检测机制要点**(核实状态逐项标注):
- **npm**(已核实端点,响应大小未核实): 官方 registry API 文档化的是 `GET /{package}` 返回 full packument(含 `dist-tags.latest`),以及经 `Accept: application/vnd.npm.install-v1+json` header 取**精简元数据**[3]。⚠️ 早稿写的 `?fields=dist-tags` query 参数 + "~100 bytes" 大小在所引官方 docs **未找到支持,标 UNVERIFIED** —— Phase 2 实测用哪种取法(full packument 解析 vs abbreviated header)。
- **PyPI**: `https://pypi.org/pypi/<pkg>/json` 返回 latest + 全 release 列表，PEP 440 版本规范/排序[8]。
- **crates.io**: **必须带 User-Agent header**(否则被拒);官方建议用 sparse index `index.crates.io` 做单包/少量包高效查询，或 `https://crates.io/api/openapi.json`(experimental OpenAPI)[5][6]。
- **GitHub Releases**: `GET /repos/{owner}/{repo}/releases` 免认证可读公开 repo，但**免认证仅 60 req/h**[4][9]。**强烈建议带 token**(GHA 里用 `GITHUB_TOKEN` 即可，额度大幅提升)。注意: 该端点**不含未关联 release 的普通 git tag**[4] —— 若目标 repo 只打 tag 不发 release，改用 `GET /repos/{o}/{r}/tags`(REST,已核实[4])。
- **Atom feed**(`releases.atom` / `tags.atom` / `commits.atom`): 这些 repo-specific atom URL 在社区广泛使用,但 ⚠️ **所引官方 GitHub feeds API 文档[7][10] 记录的是认证态 `GET /feeds` 端点 + timeline 资源(可经 `Accept` header 返回 Atom),并未文档化 repo-specific `*.atom` 路径** —— 故这些 URL 标 **UNVERIFIED against official docs**(很可能存在但官方未列),Phase 2 须实测目标 repo 的实际 feed 内容。已知社区报告: release atom feed 可能把 tag 当 release 混入、pre-release 过滤行为不一致(亦 UNVERIFIED,PoC 实测)[7]。**保守默认: 优先用已核实的 REST `/releases` + `/tags`,atom feed 仅作 PoC 验证后的可选优化。**

**确定性采集 vs LLM 判定的边界**(B 类内部再切一刀):
- **确定性部分**(脚本做): 抓 feed/API、与上次记录的 last-seen 版本/SHA 比对、识别"有新东西"。这部分**不用 LLM**，纯字符串/版本比对。
- **LLM 判定部分**(只在"有新东西"时触发 1 次调用): 喂 release notes / changelog diff + Mercury 上下文(我们在用它的哪些能力、已知痛点)，输出 `{ significant: bool, priority, impact, summary, suggested_action }`。**LLM 只在有 delta 时被调用，token 量极小。**

---

## 5. 自动建 Issue 输出设计

#157 最大的运维风险是 **Issue spam**。设计必须把"避免刷屏"作为一等约束。

### 5.1 categorization + 标签

LLM 判定输出落成 Issue，带结构化标签:

- **priority**: 复用现有 P1/P2/P3 体系(与 #157 自身的 P2 标签同源)。
- **impact**: 新建标签维度，建议 `impact/blocking`(冲突/破坏现有功能) / `impact/capability`(新能力可采纳) / `impact/maintenance`(常规 bump) / `impact/fyi`(知悉即可)。
- **来源标签**: `intel/sdk`(A/B 类 SDK) / `intel/oss`(参考项目) / `intel/api-docs`。
- **triage 标签**: `intel/needs-triage` —— 所有自动建的 Issue 默认带此标签，**人工 review 前不进正常 backlog**，避免污染开发队列。
- **⚠️ 标签来源安全约束**: 上述 priority / impact / 来源标签全部是**固定枚举白名单**,实现时 label 值只能从该白名单查表取得,**禁止**用 LLM 输出或 release-note 文本动态拼接 label —— 否则有命令注入 + label 污染风险(详见 §7 step 5 的转义约束)。

### 5.2 dedup —— 防 Issue spam 的核心

三层去重:
1. **last-seen 状态(确定性，脚本层)**: 记录每个源 last-seen 版本/SHA/feed-entry-id。只有 delta 才进入 LLM 判定。**这是第一道也是最强的去重闸** —— 没有版本变化根本不调 LLM、不建 Issue。

   ⚠️ **状态持久化机制是 dedup 正确性的前提,必须显式设计**: GitHub Actions scheduled workflow 默认从 **default branch 的 latest commit** checkout —— 若把状态文件只提交到一个侧分支(如 `claude/intel-state`),下次 scheduled run **看不到**该分支的状态,第一道去重闸失效、会重复报同一发现。可行机制(Phase 2 实测选一):
   - **(a) commit 回 default branch**: workflow 用 `GITHUB_TOKEN` 直接 commit `.mercury/state/intel-watch.json` 到 develop —— 但 develop 有 branch protection (require PR),GITHUB_TOKEN 直推会被挡,需评估是否给 workflow 例外或走 PR(笨重)。
   - **(b) 侧分支 + 显式 fetch**: 状态存 `claude/intel-state` 分支,但 workflow **起始步骤必须显式 `git fetch origin claude/intel-state` 并读取该分支的文件**(不依赖 checkout 的 default-branch 树),结束时 commit 回该分支。这是侧分支方案能 work 的唯一前提。
   - **(c) git-外状态存储**: 用 GitHub repo/environment **variable**(`gh variable set`)或一个 pinned tracking Issue 的 body 作状态存储,经 `gh api` 读写 —— 完全绕开分支可见性问题,推荐作为 PoC 最简路径。
   - GHA `actions/cache` **不可靠**(best-effort,可被驱逐),不作 dedup 状态用。

   **PoC 须固定单一 state 源 + 原子更新**: 上述 (a)/(b)/(c) 只选一个作权威 state(PoC 用 (c)),不并存多源以免分叉。并发约束: scheduled run 可能与手动 `workflow_dispatch` 重叠 —— 用 GHA `concurrency:` group(同 group 串行/取消在途)防两个 run 同时读改 state;state 写入用"读-改-写一次性提交"(repo variable 的 `gh variable set` 是整体覆盖,pinned Issue body 用 `gh issue edit --body` 整体替换),避免部分更新。
2. **开放 Issue 查重**(确定性，建 Issue 前): `gh issue list --label intel/needs-triage --state open --search "<源名> <版本>"` —— **注意 `gh --label` 不支持通配符**(必须传精确 label 名),因所有自动建的 Issue 都带 `intel/needs-triage`,用该精确 label 过滤即可;若已有覆盖同一 source+version 的 open Issue 则跳过(或 append comment 而非新建)。
3. **LLM significance gate**(判定层): LLM 判 `significant: false` 的 delta(如纯 patch bump 无 changelog 实质)**不建 Issue**，只更新 last-seen 状态(§5.2 的持久化机制)+ 可选写一行到 digest。

### 5.3 输出节流: digest 优先于逐条 Issue

复刻 #381 先例的 **Issue vs defer-note 二分**，再加一层节流:
- **显著(significant + priority≥P2)** → 建独立 GitHub Issue(带上述标签 + `needs-triage`)。
- **次要/批量** → **不逐条建 Issue**，而是 append 到一个**单条 rolling digest Issue**(如固定一个 `[intel] Weekly digest` Issue，每周 comment 一段),或写 `.mercury/state/intel-digest.md`。这是 #381 "defer note(带 re-eval 条件)"的自动化对应物。
- **数量上限熔断**: 单次运行建 Issue 数设硬上限(如 ≤3)。超过则全部降级进 digest + 在 digest 里标"本轮 delta 过多，需人工集中 triage"，防止某天上游集中发版导致刷屏。

### 5.4 与现有 review/自动化生态的关系(避免重复)

参照 #86 audit 的教训(`issue-86-pr-monitor-audit-2026-05.md`): 提案能力常被现有机制 subsume。核查 #157 不与现有重复:
- A 类(依赖 bump)→ Renovate/Dependabot，**Mercury 当前无此能力**(确认非重复，是新增)。
- B 类(外部情报 → Issue)→ 当前是 **#381 式手工扫描**，无自动化(确认非重复，是自动化先例)。
- 建 Issue 后的开发流程 → 复用现有 issue-workflow + pr-flow + Argus，**#157 不碰这层**(只产出 `needs-triage` Issue，交回主流程)。

---

## 6. 模块化 / DIRECTION.md 对齐

| DIRECTION.md / CLAUDE.md 约束 | #157 设计如何满足 |
|---|---|
| **独立可拆卸 (modular design)** | 整个 #157 = `.github/workflows/external-intel.yml` + `scripts/intel-watch.*`(采集+判定脚本) + `.mercury/state/intel-watch.json`(状态)。删 workflow 即彻底停;删脚本无残留。Renovate/Dependabot 各自独立配置文件,可单独启停。 |
| **adapter ≤200 LOC** | 仅当挂载**外部项目**作 adapter 时才适用。#157 的采集/判定脚本是 **Mercury-internal tooling under `scripts/`**,按 CLAUDE.md 明确 carve-out **不受 200-LOC cap**(它实现的是本 repo 内的监控协议,非挂载外部项目)。但 Renovate/Dependabot 是**配置**而非 adapter,不涉及 LOC cap。 |
| **No self-research / 优先挂载** | A 类显式交 Renovate/Dependabot(commodity),不自研依赖检测,符合"外部项目能解决就挂载/用现成"。 |
| **MANDATORY RESEARCH PROTOCOL** | 本 ADR 所有外部能力(npm/PyPI/crates.io/GitHub API/feed/GHA cron/Dependabot ecosystem)均对照官方文档核实并附 URL;未核实项标 UNVERIFIED(见 §9)。Phase 2 实装前,任何新引入的 SDK 调用(如 Anthropic API 的 message 端点签名)需再次 web-verify。 |
| **Issue-first / PR to develop** | #157 自动建的 Issue 本身就是 issue-first 的产物。workflow 代码经正常 PR 入 develop。 |
| **upstream-manifest / cherry-pick 协议** | Renovate/Dependabot 是**通过其官方机制配置启用**,非 cherry-pick 文件,不触发 manifest 登记。若将来引入第三方 GHA action(如某个 release-watch action),按 cherry-pick 协议登记。 |

---

## 7. 分阶段 rollout

### Phase 1 — 设计(本 doc) ✅
本 ADR。Issue #157 保持 OPEN。

### Phase 2 — 最小 PoC(推荐,低风险)

**范围: 双源(2 sources)+ 不改业务代码 + 人工审 Issue。**

具体建议:
1. **基座**: 一个 GitHub Actions scheduled workflow(`cron: '0 9 * * 1'` 每周一 09:00 UTC[1])。
2. **源**: 接 B 类最相关源(实装见 #453,门槛已 verify 见 §9 回填块)—— `@anthropic-ai/claude-code` + `@anthropic-ai/claude-agent-sdk` 的 npm `dist-tags.latest`(经 `GET /{package}` + `Accept: application/vnd.npm.install-v1+json` abbreviated packument[3])+ Claude Code raw `CHANGELOG.md` hash-diff(`raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`,比 HTML API-docs 页干净)。Anthropic API HTML docs 快照为可选 stretch(脆弱,可延 Phase 3)。**不接 A 类**(A 类另起一个独立的 Renovate 配置 PR,与 PoC 解耦并行)。
3. **状态**: 记 last-seen 版本/CHANGELOG hash。**PoC 选定 §5.2 机制 (c) 的 pinned Issue body 变体**(§9 回填: GITHUB_TOKEN `issues: write` 可靠无需 PAT;Variables 写权限 UNVERIFIED 故弃用),经 `gh api` 读写 + `gh issue edit --body-file` 整体替换,绕开 scheduled-run 的分支可见性问题(机制 a/b 留 Phase 3 视 branch-protection 实测)。这是 dedup 第一闸能跨 run 工作的前提。
4. **LLM 判定**: workflow 内调 Anthropic API 一次(仅当有 delta),输出 significance + priority + impact + summary。**调 API 前 web-verify message 端点签名**(MANDATORY RESEARCH PROTOCOL)。**外部边界错误处理(必须显式设计)**: API 超时/限流/5xx → 有限次重试(指数退避)后**降级**(把本轮 delta 写入 digest + 仍持久化 last-seen 状态,**绝不**因 API 失败而丢弃 delta 或重复整批);workflow step 设 timeout;`gh issue create` 失败时同样回退到 digest 并保留 state,避免告警风暴或 delta 漏报。
5. **输出**: significant 时 `gh issue create --label <source-label>,intel/needs-triage`,其中 `<source-label>` **必须从固定白名单映射取值**(`intel/sdk` = Claude Code SDK / `intel/api-docs` = Anthropic API 文档 / `intel/oss` = 参考项目,见 §5.1)—— **禁止把 LLM 输出或外部 release-note 文本直接拼进 `gh` 命令参数**(命令注入 + label 污染风险);所有传给 `gh` 的参数(title/body/label)须严格引用/转义,body 经 `--body-file` 传入而非内联拼接。非 significant 只更新 last-seen 状态(§5.2 机制 c)。
6. **护栏**: 单次 ≤1 Issue(PoC 阶段更严);全部带 `needs-triage`,人工 review;不改任何业务代码、不推非 `claude/*`/状态分支;label 仅取固定白名单;`gh` 参数严格转义。
7. **跑 4 周**,评估: 误报率(建了不该建的 Issue)/ 漏报率 / token 成本 / Issue 噪音感受。判定 prompt 以 §1.3 的 repo-内基线启动(源清单 §4.2 + label §5.1 + 输出形态 §5.3);user-level #381 的 taxonomy/模板作可选 enrichment(可访问时补,不可访问不阻塞)。

PoC 规避的风险: 不依赖 Routines(无 research-preview 风险) / 不依赖 custom agents / 不推受保护分支 / 双源 + 单次 ≤1 Issue 上限把 spam 风险降到最低。

### Phase 3 — 全量

PoC 验证后扩展:
1. **扩源**: 加 Tauri(GitHub Releases API[4] + crates.io sparse index[5][6])、Codex CLI(GitHub Releases API[4] + npm,**不涉 crates.io**)、全部参考 OSS(REST `GET /releases` + `GET /tags`[4] 为默认;atom feed 仅作可选优化, UNVERIFIED 见 §4.2/§9)。
2. **A 类正式上线**: Renovate(推荐)配置,grouping + automerge patch-level。
3. **digest + 熔断**: §5.3 的 rolling digest Issue + ≤3 Issue/run 上限。
4. **Routines 评估(条件触发)**: **仅当** Routines 摘 research-preview 标签(#289 re-check trigger)时,评估把 B 类 LLM 判定迁到 Routine(B3-style),获得完整 Claude Code 上下文 + skill 访问。在此之前 GHA 内调 API 完全够用。
5. **defer-note 自动化**: 复刻 #381 的 defer note(带 re-eval 条件)输出形态。

---

## 8. 推荐架构图(文字版)

两条**独立并行**轨道(A 类与 B 类不共用同一调度器):

```
  ── A 类轨道(独立机制,no-LLM)──         ── B 类轨道(本 Issue 新增价值)──
  Renovate / Dependabot                    GitHub Actions scheduled workflow
  (GitHub-side 自有调度,独立配置文件)        (weekly cron) [GA, 无 research-preview 风险]
        │                                          │
        ▼                              ┌────────────┴────────────┐
   比对 manifest vs registry            ▼                        ▼
        │                       [B 类采集脚本: 确定性]   [last-seen 状态: §5.2 机制(c)]
        ▼                        抓 npm 包元数据 /        pinned Issue body / repo var
   直接产 PR                      PyPI json / crates       (经 gh api 读写, 第一道去重闸)
  (走正常 pr-flow)                sparse index / GH              ▲
                                 releases(REST/atom)            │ 更新
                                       │ 仅当有 delta            │
                                       ▼                        │
                              [LLM 判定: 1 次 Anthropic API 调用]─┘
                               significant? priority? impact?
                                       │
                           ┌───────────┴───────────┐
                           ▼                       ▼
                  significant + ≥P2         非 significant / 批量
                           │                       │
                   gh issue create          append to rolling
                   label: intel/{sdk|         digest Issue / 状态
                   api-docs|oss},             (defer-note 等价物)
                   intel/needs-triage
                   (≤3/run 熔断)
                           │
                           ▼
                   人工 review → 进正常 issue-workflow
```

---

## 9. Open questions / UNVERIFIED

> ### 验证结果回填 — Phase 2 入口门槛 (核实日期 2026-05-25, S137, per MANDATORY RESEARCH PROTOCOL)
>
> Phase 2 PoC 实装 Issue: **#453**。下列 PoC 相关门槛项已对照官方文档逐项核实，结论回填如下；residual UNVERIFIED 见各项末尾(均非 PoC 阻塞)。
>
> - **Q-PKG 包名 (新增核实)** — VERIFIED:"Claude Code SDK" 已改名 **Claude Agent SDK**。当前 npm 包: CLI=`@anthropic-ai/claude-code`(v2.1.150)、SDK=`@anthropic-ai/claude-agent-sdk`(v0.3.150)、核心 API SDK=`@anthropic-ai/sdk`(v0.98.0);`@anthropic-ai/claude-code-sdk` **不存在**(registry 404)。来源: `https://registry.npmjs.org/@anthropic-ai/claude-code/latest` 等。PoC "Claude Code SDK" 源 → 应监控 `@anthropic-ai/claude-code` + `@anthropic-ai/claude-agent-sdk`。residual UNVERIFIED: 改名确切日期 + claude-agent-sdk 官方 docs 页 URL(非 PoC 阻塞)。
> - **Q2 Anthropic changelog feed** — VERIFIED:API/platform changelog (`https://platform.claude.com/docs/en/release-notes/overview`, `docs.anthropic.com`/`docs.claude.com` 均 301 至此) 为 **HTML-only,无 RSS/Atom/JSON feed** → 脆弱快照 diff。**但 Claude Code 有官方 `CHANGELOG.md`**(`https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`,raw 纯文本,适合 hash-diff)。**决策: 首选 raw CHANGELOG.md 作干净监控目标;HTML API-docs 页作脆弱 fallback。**
> - **Q3b npm 取法** — VERIFIED:用 `GET https://registry.npmjs.org/{package}` + `Accept: application/vnd.npm.install-v1+json`(abbreviated packument) 读 `dist-tags.latest`(官方文档化,响应小)。`?fields=dist-tags` query 与 `/-/package/{pkg}/dist-tags` 端点 **均不在官方文档** → 不用。来源: `https://github.com/npm/registry/blob/main/docs/responses/package-metadata.md`。
> - **Q5 状态持久化** — VERIFIED(选型敲定):repo Variables 单条 48 KB / 500 条 / run 256 KB,但 **GITHUB_TOKEN 写 Variables API 权限 UNVERIFIED(可能需 PAT)**;而 GITHUB_TOKEN 的 `issues: write` 官方文档化且可靠 → **PoC 用 pinned tracking Issue body 作 state 存储(机制 c 变体),无需 PAT**。来源: `https://docs.github.com/en/rest/issues/issues`、`https://docs.github.com/en/actions/reference/workflows-and-actions/variables`。residual UNVERIFIED: Issue body ~64 KB 上限为社区经验值(官方 REST docs 未标注;PoC 存的是极短版本串,远低于上限)。
> - **Q6 GHA 分钟成本** — VERIFIED:Mercury repo = **PUBLIC**(`gh repo view`) → GHA 免费,无分钟额度顾虑。
>
> **未纳入 PoC 的门槛项(Phase 3)**: Q3 atom feed 行为、Q4 Codex CLI 分发渠道 —— PoC 双源(npm + CHANGELOG.md)不依赖二者,留 Phase 3 扩源前核实。Q1(#381 taxonomy enrichment)仍非阻塞。

1. **#381 的具体 source 清单 / taxonomy / Issue 模板** — UNVERIFIED。原文在 user-level memory,按 CLAUDE.md 约定为 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/research/tech-intel-sweep-2026-05-12.md`(含 `/memory/` 层,不在本 repo),本 design lane 读不到。**非 Phase 2 阻塞项** —— §1.3 已给出 repo-内自包含最小基线(源清单 §4.2 / label §5.1 / 输出形态 §5.3),足以启动 PoC;从该路径或 #381 Issue 提取仅作 taxonomy/模板的 enrichment。
2. **Anthropic API / Claude Code SDK 是否有官方结构化 changelog feed** — UNVERIFIED。若无,B 类对"API 文档变更"只能做页面内容快照 diff(脆弱、易误报)。需核实 platform.claude.com 是否提供 changelog RSS/JSON。
3. **GitHub repo-specific atom feed (`releases.atom`/`tags.atom`)** — 双重 UNVERIFIED: (a) 这些 URL 在社区广泛使用但**未被官方 GitHub feeds API docs 记录**[7](官方只文档化认证态 `GET /feeds` + timeline 资源);(b) pre-release/tag 混入行为社区报告不一致。**保守默认用已核实的 REST `/releases` + `/tags`**;atom feed 仅作 Phase 2 实测后的可选优化。
3b. **npm `?fields=dist-tags` query + 响应大小** — UNVERIFIED:官方 npm registry docs 未记录该 query 参数;改用文档化的 `GET /{package}` packument 或 abbreviated `Accept` header,Phase 2 实测取法。
4. **Codex CLI 的分发渠道** — UNVERIFIED 是 npm 还是仅 GitHub release。决定用 npm dist-tags 还是 GitHub Releases API。
5. **状态持久化机制的最终选型** — §5.2 列了 (a) default-branch commit / (b) 侧分支 + 显式 fetch / (c) git-外存储(pinned Issue/repo var)三方案,PoC 默认 (c)。Phase 2 实测确认 (c) 的 `gh api` 读写延迟/配额可接受,并验证 (a) 在 branch protection 下是否真被挡。
6. **私有 repo 的 GHA 分钟成本** — Mercury repo 若为私有,GHA 有月度分钟额度;weekly 轻量 workflow 消耗极小,但需确认未超额(UNVERIFIED Mercury repo 当前是否 public)。

### Phase 2 入口验收门槛(UNVERIFIED 项固化)

上述 UNVERIFIED 项直接影响采集稳定性与误报率,**进入 Phase 2 实装前必须逐项 web-verify 并作为验收门槛清单**(per MANDATORY RESEARCH PROTOCOL)。核实于 2026-05-25 (S137) 完成,结论见上方"验证结果回填"块:
- ✅ Q2 Anthropic API/SDK changelog feed → 无 feed;首选 Claude Code raw `CHANGELOG.md` hash-diff,HTML API-docs 页作 fallback。
- ✅ Q3b npm 取法 → `GET /{package}` + `Accept: application/vnd.npm.install-v1+json` 读 `dist-tags.latest`;`?fields` / `/-/package/.../dist-tags` 非官方,不用。
- ✅ Q5 状态持久化 → 选定 pinned Issue body(机制 c 变体),GITHUB_TOKEN `issues: write` 可靠无需 PAT;Variables 写权限 UNVERIFIED 故弃用。
- ✅ Q6 GHA 分钟额度 → repo PUBLIC,免费无额度顾虑。
- 🔜 Q3 atom feed 行为 / Q4 Codex CLI 分发渠道 → **Phase 3 扩源前**核实(PoC 双源不依赖,见回填块"未纳入 PoC 的门槛项")。

PoC 门槛已 discharge(Q2/Q3b/Q5/Q6 ✅);Q3/Q4 为 Phase 3 扩源门槛,任一未核实不得进入对应源的全量 Phase 3。

---

## 10. 给主 agent 的交接要点

- **#157 保持 OPEN**(设计交付物,非实现)。
- **不要把"复用 #92 cron"当前提** —— #92 已 Closed as not planned,其 RPC/session 模型对 #157 不适用(§1.2)。
- Phase 2 PoC **不被 user-level memory 阻塞**: §1.3 已给 repo-内自包含最小基线(源清单/label/输出形态);从 user-level memory 提取 #381 taxonomy/模板仅作可选 enrichment(§9 Q1)。
- A 类(Renovate/Dependabot)与 B 类(intel agent)**应作为两个独立 PR / Issue 推进**,解耦。

---

## 参考来源

核实日期均为 2026-05-24。

**执行基座**:
- [1] GitHub Actions 调度 cron 语法(POSIX, 5min 最短, **仅按 UTC 解释(无 timezone 配置)**, 跑 default branch latest commit): <https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions>
- [#289] Claude Code Routines 约束(research preview / daily quota / custom-agent 加载 UNVERIFIED / 分支推送限制 / 访问不到本机路径): `.mercury/docs/research/issue-289-design-routines-2026-05.md` + <https://code.claude.com/docs/en/routines>

**依赖检测(A 类)**:
- [2] Dependabot 支持的 ecosystem(30+: npm/pip/Cargo/github-actions/uv 等)+ Renovate(60+ package manager, grouping, regex manager): <https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories> + <https://docs.renovatebot.com/bot-comparison/>

**源监控(B 类)**:
- [3] npm registry API(文档化: `GET /{package}` packument 含 `dist-tags.latest`,abbreviated 元数据经 `Accept: application/vnd.npm.install-v1+json`;`?fields=dist-tags` query + 响应大小为 UNVERIFIED): <https://github.com/npm/registry/blob/main/docs/REGISTRY-API.md> + <https://api-docs.npmjs.com/>
- [4] GitHub REST API list releases(`GET /repos/{owner}/{repo}/releases`, 不含未关联 release 的 tag, 免认证可读公开 repo): <https://docs.github.com/en/rest/releases/releases>
- [5] crates.io 数据访问(必须带 User-Agent header, sparse index `index.crates.io` 高效查单包, experimental OpenAPI `crates.io/api/openapi.json`): <https://crates.io/data-access>
- [6] crates.io API 客户端参考: <https://docs.rs/crates_io_api/latest/crates_io_api/>
- [7] GitHub feeds(官方 docs 记录认证态 `GET /feeds` + timeline 资源,**未文档化 repo-specific `releases.atom`/`tags.atom`/`commits.atom` 路径** → 这些 URL UNVERIFIED against official docs;pre-release/tag 混入行为社区报告不一致): <https://docs.github.com/en/rest/activity/feeds> + <https://github.com/orgs/community/discussions/17052>
- [8] PyPI JSON API(`https://pypi.org/pypi/<pkg>/json` latest + 全 release, PEP 440 版本规范/排序): <https://docs.pypi.org/api/json/>
- [9] GitHub REST API rate limit(免认证 60 req/h, 带 token 大幅提升): <https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api>
- [10] GitHub feeds REST endpoint(timeline 资源默认 JSON, Accept: application/atom+xml 返回 Atom): <https://docs.github.com/en/rest/activity/feeds>

**Mercury-内部引用**(非外部 vendor 源):
- Issue #157 body: <https://github.com/392fyc/Mercury/issues/157>
- Issue #92 body(已 Closed as not planned): <https://github.com/392fyc/Mercury/issues/92>
- #381 tech-intel-sweep 手工先例: MEMORY.md 索引行(原文在 user-level memory, 本 repo 不可读, 见 §1.3 + §9 Q1)
- #86 PR Monitor audit(subsumption 核查方法论): `.mercury/docs/research/issue-86-pr-monitor-audit-2026-05.md`
- DIRECTION.md 适配层规范(adapter ≤200 LOC scope) + 候选挂载评估标准: `.mercury/docs/DIRECTION.md` §"适配层规范"(L231-251)
- CLAUDE.md: MANDATORY RESEARCH PROTOCOL / modular design / scripts-exempt-from-LOC-cap carve-out
