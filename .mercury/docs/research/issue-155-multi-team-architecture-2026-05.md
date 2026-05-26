---
issue: 155
title: "Director-Manager-Employee 多团队并行架构 — Phase 1 设计 ADR (三层 nested orchestration + 跨 department 记忆共享)"
date: 2026-05-24
session: S135 (design lane)
status: phase-1-design-proposal
verdict: "CONDITIONAL_GO (phased, P2 实现优先级低) — 推荐 lane-as-process 路径实现三层 (Director=main lane / Manager=side lane / Employee=Manager dispatch 的 sub-agents)，绕开 Agent Teams 的 no-nested-teams 与原生 sub-agent depth=1 两个硬限制；不引入 Agent Teams、不引入 Managed Agents API。三层在现有 multi-lane v1 + 5-lane cap 内技术可行，2-department bootstrap = main + 2 side lanes = 3 lanes。复用 #319 的 Dim1.3 5 个 missing module，三层新增 3 项需求。跨 department 记忆 mem0 层已全局共享，缺 per-department 检索隔离 (schema 问题非可行性问题)。"
relation: "build-on #319 (agent-team-orchestration-feasibility-2026-04-26.md, CONDITIONAL_GO, 两层 Director/DevTeam)；引用 #386 (agent-view-multi-lane-adaptation-2026-05.md, multi-lane v1 现状)；引用 #391 (agent-view-phase6-empirical, file-editing bg worktree HYBRID)"
research_protocol: "所有外部能力对照官方文档核实 2026-05-24；未核实项标 UNVERIFIED + 来源 URL。in-repo LOC/path 引用基于 develop @ fa3e171。"
web_verify: "S143 (2026-05-26, develop @ af7953c) web-verify pass — 起草时 3 个 UNVERIFIED 项全部 resolved (详见 §11): ① mem0ai metadata filter VERIFIED-YES (装机版 mem0ai 1.0.11); ② shared_memory MCP scope VERIFIED (OMC v4.13.2 源码: 文件锁跨进程并发协调 + 默认 per-worktree + TTL≤7d); ③ bg-spawn-subagent VERIFIED-YES (agent-view docs 平台能力层, empirical PoC 仍属 Phase 2)。Verdict (§9 CONDITIONAL_GO) 不变。"
---

# Issue #155 — Director-Manager-Employee 多团队并行架构: Phase 1 设计 ADR

> **本 doc 是设计提案 (design proposal)，不实装、不改代码、不 dispatch 其它 agent，不修改 `DIRECTION.md` / `EXECUTION-PLAN.md`。** Issue #155 保持 **OPEN**(设计阶段，非实现)。本 ADR 可以 *建议* 未来对 DIRECTION 的增补，但措辞为建议，不直接改那两份文件。#155 是 P2 strategic research，实现优先级不高但调研可优先。

---

## Path conventions (read this first)

沿用 #319 (`agent-team-orchestration-feasibility-2026-04-26.md`) 与 #386 (`agent-view-multi-lane-adaptation-2026-05.md`) 已确立的 path convention。

- `<encoded_cwd>` 是 project working directory 的 path-encoded 形式，由 Claude Code 在 session start 计算，**不要 hardcode**。发现时须**唯一命中**校验，避免多项目目录误选(把状态写错项目空间会破坏多 lane 数据隔离):

  ```bash
  matches=$(ls "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/" | grep -iE '(^|-)mercury(-|$)')
  [ "$(printf '%s\n' "$matches" | sed '/^$/d' | wc -l)" -eq 1 ] || { echo "encoded_cwd 匹配不唯一或不存在" >&2; exit 1; }
  ```

- `${MERCURY_ROOT}` 是**文档占位符**(非运行时已注入的 env var),指 Mercury 安装父目录(具体值由各 install 决定,Windows 团队按 CLAUDE.md "Install to D drive" policy 落在 D 盘)。main checkout = `${MERCURY_ROOT}/Mercury`;per-lane worktree = `${MERCURY_ROOT}/Mercury-<short>`。本 doc 一律用占位符表达,**不在仓库内容中保留具体机器路径**。

| Shorthand | Resolves to | Status |
|-----------|-------------|--------|
| `memory/<file>` | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects/<encoded_cwd>/memory/<file>` (Claude Code per-project user-memory) | NOT in repo — gitignored by design |
| `.claude/agents/<name>.md` | `${REPO_ROOT}/.claude/agents/<name>.md` (Mercury 角色定义) | In repo |
| `.claude/agent-memory/<name>/` | `${REPO_ROOT}/.claude/agent-memory/<name>/` (原生 subagent `memory: project` scope) | **默认不在 repo**;启用 `memory: project` 后在此生成,可纳入版本控制(当前仓库 `.claude/` 下只有 `agents/`/`hooks/`/`skills/` 等,**Mercury 未启用**) |
| `.mercury/docs/research/` | `${REPO_ROOT}/.mercury/docs/research/` | In repo |
| `.mercury/state/` | `${REPO_ROOT}/.mercury/state/` | In repo |
| `~/.claude/scripts/mem0_bridge.py` 等 | user-level memory layer 运行时 canonical | 运行时 NOT in repo(用户级,见 CLAUDE.md Related Repositories);但 `mem0_hooks.py`/`mem0_migrate.py` 等有 repo 内可审阅副本 `scripts/mem0_*.py`(version-tracked) |
| `${MERCURY_ROOT}/Mercury-<short>` | per-lane git worktree (Rule 5.1) | repo-local worktree，非 commit 进 main checkout |

in-repo LOC / 文件引用基于 develop tip `fa3e171`(本 ADR 起草时的 git status 记录的 develop HEAD)。

## Mercury terminology (read before non-Mercury reviewers)

本 doc 沿用 #319 定义的 Mercury 术语；新增/复用如下:

- **Lane** — 一条绑定到 git branch prefix + lane-tag 的并行工作流。1 lane = 1 条自治 Claude Code session 线。multi-lane v1 hard cap = 5 active lanes。
- **lane-as-process** — 本 ADR 核心概念:每条 side lane 是一个独立的 Claude Code 进程(独立 terminal / `claude --bg`),在 Anthropic 平台视角是一个 "main session"(非 subagent),因此它**自己可以 dispatch sub-agents**。这是绕开 "subagent 不能 spawn subagent" 与 Agent Teams "no nested teams" 两个硬限制的关键。
- **Director / Manager / Employee** — #155 提出的三层 metaphor(详见 §1.1)。区别于 #319 的两层 Director/Dev-Team metaphor(#319 的 Dev-Team 等于本 ADR 的 Manager + 其 Employees,即一个 Department)。
- **Department** — 1 Manager + N Employees,消化一个目标任务,各 department 原子结构一致(同构镜像)。在 lane-as-process 路径下,1 Department = 1 side lane(Manager)+ 该 lane dispatch 的 N sub-agents(Employees)+ 独立 worktree(Rule 5.1)。
- **#319 Dim1.3 missing modules** — #319 识别的 5 个未实现协调模块(cross-lane status aggregator / Director→side lane dispatch / side lane auto-report / lane lifecycle coordinator / Director command surface)。本 ADR 复用该清单,不重复其 effort 评分。
- **Agent Teams** — Anthropic Claude Code 的 experimental 多 session 协作原语(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`),区别于平台层的 Managed Agents API。
- **Managed Agents API** — Anthropic 平台层(`platform.claude.com`)的 coordinator+worker API,走平台 API 凭证(`x-api-key` 或 `Authorization`)直调,非 Claude Code CLI subscription 场景。

---

## Executive Summary / 结论

**Verdict: CONDITIONAL_GO(phased adoption，lane-as-process over Agent Teams，P2 实现优先级低但调研可优先)**

#155 提出的 Director-Manager-Employee **三层** nested orchestration 在 Mercury 当前 multi-lane v1 protocol 下**技术可行，且不需要 Anthropic Agent Teams、不需要突破任何嵌套禁令**。关键路径是 **lane-as-process**:

- **Director** = main lane interactive session(总领进程、周期目标、里程碑、Issue 状态检查、分配项目给各 department;不涉任务执行内核)
- **Manager** = 独立 side lane(`= 现 Main Agent` 的角色:接收项目 → 调研 → 定义需求/影响范围 → 判断复杂度 → 决定工作流)。**因为 side lane 自身是一个独立 Claude Code 进程(平台视角的 "main session"),它可以 dispatch 自己的 sub-agents** —— 这是三层能成立的核心。
- **Employee** = Manager 这个 side lane dispatch 的 sub-agents(dev / research / acceptance 执行角色)
- **Department** = 1 side lane + N sub-agents + 独立 worktree(Rule 5.1),各 department 原子结构一致

三层在此路径下落在现有 **5-lane hard cap** 内:起步 2-department 并行 = main lane(Director)+ 2 side lanes(2 Manager)= **3 lanes**,与 multi-lane v1 protocol 完全兼容,**无需改 lane protocol**;只需补 #319 的 Dim1.3 missing modules(尤其 Director→side lane dispatch)+ 三层模型新增的 3 项需求(§5)。

**天花板 = depth 3**:Employee(sub-agent)**不能再向下 spawn**(官方明确 "subagents cannot spawn other subagents",见 §3.3),所以 Employee 不能成为 sub-Manager。这对 Mercury 单用户 ≤5 lane 的尺度足够,不构成约束。

**为什么不用 Agent Teams / Managed Agents API**:两者都对三层模型构成 hard blocker——

- **Agent Teams**:teammates 不能 spawn 自己的 team/teammates(官方 "No nested teams",2026-05-24 复核仍存在),若用 Agent Teams 实现 Manager 层,Manager(teammate)就无法管理自己的 Employee —— 三层退化为两层。加之 experimental / 无 session resumption / Windows Terminal 不支持 split-pane,均与 #319 结论一致,28 天后**全部仍有效未 stale**。
- **Managed Agents API**:coordinator 只能 delegate 一层("depth > 1 is ignored"),且走平台 API 凭证直调,**不适用 Claude Code CLI subscription 场景**。

**跨 department 记忆共享**:mem0 层(单一 `"mercury"` Qdrant collection + 固定 `user_id="mercury"`)**已经跨 lane/department 全局共享** —— 脱离特定任务后的记忆共享是技术现状,非待建。**现存 gap 是 schema 设计问题非可行性问题**:无 per-department namespace/scoping,department 数增多后检索噪音上升。§6 给出 3 个 design 选项(metadata filter / 多 collection / 原生 subagent `memory:` frontmatter)+ `shared_memory` MCP 候选,推荐 metadata filter(`department_id` + 检索 filter),但作为提案不强制实施。

**实现优先级**:P2,不高。Phase 2 PoC(2-department dogfood)另开 follow-up Issue,#155 保持 OPEN。

---

## 1. 背景

### 1.1 #155 三层定义(摘自 Issue body)

来源: <https://github.com/392fyc/Mercury/issues/155> (P2 / strategic research / OPEN)

| 层 | 职责 | 现 Mercury 对应 |
|---|---|---|
| **Director** | 总领进程、周期目标、里程碑、Issue 状态检查、分配项目给各 team;**不涉任务执行内核** | main lane interactive session |
| **Manager** | 接收项目 → 调研 → 定义需求/影响范围 → 判断复杂度 → 决定工作流 | **= 现 Main Agent** |
| **Employee** | dev / research / acceptance 执行角色 | **= 现 sub-agents** |
| **Department** | 1 Manager + N Employees,消化一个目标任务,各 department 原子结构一致 | 1 side lane + N sub-agents |

其它 #155 要点:
- **跨 team 记忆共享**:脱离特定任务后记忆共享(§6)
- **起步**:2 department 并行验证(§4)

### 1.2 与 #319 的关系 — build-on，不重做

#319(`agent-team-orchestration-feasibility-2026-04-26.md`,CONDITIONAL_GO)已经覆盖**两层** "main = Director / side lanes = Dev Teams" 架构,产出:

- 两层 metaphor → module 映射(Dim 1.1)+ 已有可复用模块(Dim 1.2)+ **5 个 missing module 的 gap table(Dim 1.3)**
- legacy salvage(Dim 2:archive orchestrator / sdk-adapters / role YAML)
- 6 框架评估(Dim 3:Agent Teams WAIT / LangGraph DEFER / CrewAI·MS·Swarm·MetaGPT REJECT)
- 5h observability tooling(Dim 4)
- Anthropic multi-agent research 的 over-spawning 警告(Dim 3.7:"match agent count to actual parallelism")

**本 ADR 不重复 #319 的评分细节**(尤其 Dim 3 框架打分表、Dim 4 statusline 脚本)。#155 ADR 聚焦 **三层 delta**:#319 的 "Dev Team" 是一个**扁平**单元(side lane = 一个自治 session);#155 把它拆成 **Manager + Employees** 两层,即一个 Department 内部再有 orchestration。本 ADR 论证这个内部 orchestration 在 lane-as-process 下可行,并复用 #319 的 Dim1.3 清单。

### 1.3 与 #386 / #391 的关系 — multi-lane v1 现状基线

#386(`agent-view-multi-lane-adaptation-2026-05.md`,2026-05-15)记录 multi-lane v1 现状:并行 lane(main + side-bug + side-sot 3 lane active)、worktree 隔离(`${MERCURY_ROOT}/Mercury-<short>` per lane,Rule 5.1)、branch `lane/<short>/<N>-<slug>`(Rule 2.1)、bg session dispatch(`claude --bg` in lane cwd,`isolation:"none"`)、hook 继承(SessionStart empirical confirmed fire)、agent view 监控(`claude agents --cwd <lane-worktree>`)。

#391(`agent-view-phase6-empirical-2026-05.md`,经 #386 引用)矫正了 bg session file-edit 的行为:平台 PreToolUse 拒第一次 Edit on shared checkout 返 `tool_use_error`,agent 读 error 后调 `EnterWorktree` 进 auto-worktree(branch `worktree-<name>`),`ExitWorktree(action:"keep"|"discard")` 无自动 merge,operator 手动决定 keep/discard。**对 Employee sub-agent file-edit 场景的含义见 §5.3**。

---

## 2. Delta vs #319 — #155 三层相对两层的真正新增

#319 两层模型 vs #155 三层模型的差异在**一个 Department 内部是否有 orchestration**:

| 维度 | #319 两层(Director / Dev-Team) | #155 三层(Director / Manager / Employee) | 真正新增 |
|---|---|---|---|
| Dev unit | side lane = 扁平自治 session,自己干活 | side lane = **Manager**,自己**不直接干活**,dispatch Employees 干活 | Department 内部 orchestration |
| 干活的主体 | side lane session 本体 | Manager dispatch 的 sub-agents(dev/research/acceptance) | Employee 层(= 现 sub-agent 用法,但归属明确化) |
| 深度 | depth 2(main → side lane) | depth 3(main → side lane → sub-agent) | +1 层,**到天花板**(§3.3) |
| Director→下层 dispatch | Director → side lane(#319 Dim1.3 MISSING) | Director → Manager(side lane);Manager → Employee(标准 sub-agent dispatch,已可用) | Director→Manager 复用 #319;Manager→Employee 是现成能力 |
| 记忆 | 跨 lane mem0 全局(#319 Dim1.3 已确认 mem0 operational) | 跨 department mem0 全局 + **per-department 隔离检索需求**(§6) | per-department namespace(schema delta) |
| 原子结构 | 无明确 "department" 概念 | **Department = 同构镜像原子单元**(各 dept 结构一致) | department 作为可复制的标准单元 |

**核心洞察**:#155 的三层在 lane-as-process 下,**第二层(Manager=side lane)与第三层(Employee=sub-agent)之间的 dispatch 关系,就是 Mercury 现在 main lane 用 sub-agent 的完全相同机制**。换句话说,#155 不是要求一个**新**的 orchestration 原语,而是要求 **side lane 复制 main lane 已经在做的 sub-agent dispatch**。Director→Manager 这一跳才是真正需要补的协调能力(= #319 Dim1.3 的 "Director → side lane dispatch")。

> **建议(非强制改 DIRECTION)**:未来 DIRECTION.md 若新增 "多团队并行" 章节,可把 "Department = 1 side lane + N sub-agents + 独立 worktree,各 department 同构" 作为 multi-lane v1 之上的可选 orchestration pattern 记录。**这是建议,本 ADR 不直接改 DIRECTION.md。**

---

## 3. RQ1 — 是否有更简单的并行多功能开发方式?

三条路径对比(核实日期 2026-05-24,对照官方文档)。

### 3.1 路径 (a) — Agent Teams(NESTED BLOCKER)

来源: <https://code.claude.com/docs/en/agent-teams>(fetched 2026-05-24)。

- **status**:仍 experimental(`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`),最低 v2.1.32+,GA 无时间表。
- **架构**:team lead = 1 session;teammates = 独立 Claude Code instances,各自 context window;共享 task list(file-locking 原子 claim)+ mailbox 直接互发消息;hooks `TeammateIdle`/`TaskCreated`/`TaskCompleted`。
- **对三层的 hard blocker**:官方 Limitations 明确 **"No nested teams: teammates cannot spawn their own teams or teammates. Only the lead can manage the team."** → 若用 Agent Teams 把 lead 当 Director、teammate 当 Manager,则 Manager(teammate)**无法 spawn 自己的 Employees** → 三层退化为两层。这是若用 Agent Teams 实现三层的 **hard blocker**。
- 其它仍存在的限制(2026-05-24 复核,与 #319 28 天前结论一致,**未 stale**):
  - "No session resumption with in-process teammates"(`/resume`/`/rewind` 不恢复 in-process teammates)
  - "One team at a time"(一 lead 只能管一个 team)
  - "Lead is fixed"(创建 team 的 session 终身为 lead,不能转移)
  - "Split panes ... isn't supported in VS Code's integrated terminal, **Windows Terminal**, or Ghostty"(Mercury 平台 = Windows,split-pane 不可用,只能 in-process)

**verdict**:NO-GO for 三层。即便单看两层,#319 已判 WAIT-for-GA。

### 3.2 路径 (b) — Managed Agents API(depth=1 + CLI 不适用)

来源: <https://platform.claude.com/docs/en/managed-agents/multi-agent>(#319 Dim 3.1 已引,2026-05-24 复核)。

- coordinator + worker 两层;**"coordinator can only delegate to one level of agents; depth > 1 is ignored"** → 平台层就把深度限制在 1。
- 最多 20 unique agents / 25 并发 threads。
- 走平台 API 凭证(`x-api-key` 或 `Authorization`/WIF)直调平台 API,**不适用 Claude Code CLI 场景**(Mercury 是 CLI subscription 路径,非平台 API 直调)。注:auth 方式以官方 auth docs 为准;此处仅区分"平台 API 路径 vs CLI subscription 路径",cited multiagent 页本身只支撑 depth=1 + roster/thread caps,不支撑具体 auth 要求。

**verdict**:NO-GO。depth=1 直接排除三层;且场景不匹配(CLI vs API key)。

### 3.3 路径 (c) — lane-as-process(RECOMMENDED)

**关键技术事实(对照 <https://code.claude.com/docs/en/sub-agents>,fetched 2026-05-24)**:

1. 官方明确 **"Subagents cannot spawn other subagents."**(docs §"Choose between subagents and main conversation" Note;另在 built-in Plan agent 说明 "prevents infinite nesting (subagents cannot spawn other subagents)")。嵌套深度硬限制 = 1。
2. 关于 `Agent` tool:**"This restriction only applies to agents running as the main thread with `claude --agent`. Subagents cannot spawn other subagents, so `Agent(agent_type)` has no effect in subagent definitions."**(docs §"Restrict which subagents can be spawned")。即便 subagent frontmatter 写 `tools: Agent`,subagent 运行期 `Agent` tool 无效。
3. **但**:一个 agent **as the main thread**(`claude --agent`,或就是一个普通 main session)**可以** spawn subagents via `Agent` tool。

**lane-as-process 如何绕开限制**:每条 side lane 是一个独立 Claude Code 进程。在 Anthropic 平台视角,side lane session **是一个 "main session"(不是某个 main session 的 subagent)** —— #386 Phase 2 empirical 已证实:bare `claude --bg` 启的 bg session `isolation:"none"`、`linkScanPath` 指向其 cwd 的 project memory、SessionStart hook 完整 fire,它就是一个独立 session。因此:

- side lane(Manager)作为 main session,**可以 dispatch 自己的 sub-agents**(Employees:dev/research/acceptance)—— 与 main lane(Director)用 sub-agent 的机制完全同构。
- Employee(sub-agent)受 depth=1 限制,**不能再向下 spawn** → Employee 不能成为 sub-Manager → 三层是天花板。

**三层 vs 两层取舍(本节给框架,详细在 §7)**:
- 两层(#319)够用:简单任务线,side lane 自己干活即可,无需内部再分工。
- 三层(#155)值得:复杂 department 需要自身 orchestration(如一个 department 要并行跑 research + dev + acceptance,Manager 协调 3 个 Employee 比单 session 串行更快,且 spread reasoning across independent context windows —— #319 Dim3.7 验证的收益)。

**verdict**:**RECOMMENDED**。技术可行、不需 Agent Teams、不突破嵌套禁令、落在 5-lane cap 内、与 multi-lane v1 完全兼容。

### 3.4 三路径对比表

| 维度 | (a) Agent Teams | (b) Managed Agents API | (c) lane-as-process |
|---|---|---|---|
| 三层 nested 支持 | ❌ no nested teams | ❌ depth>1 ignored | ✅ Manager=main session 可 dispatch Employees |
| 适用 Claude Code CLI | ✅ | ❌ 平台 API 路径(非 CLI subscription) | ✅ |
| status | experimental,无 GA | 平台层(非 experimental) | 现有 multi-lane v1(production) |
| Windows 支持 | ⚠️ 仅 in-process(split-pane 不支持) | N/A(API) | ✅(已是 Mercury 现状) |
| 与 multi-lane v1 兼容 | 替换 Rules 1-7 部分 | N/A | ✅ 完全兼容,无需改 protocol |
| session resumption | ❌ in-process teammates 不恢复 | N/A | ✅ Mercury lane 跨周/跨月持久 |
| 改动面 | 大(引入新原语) | 大(API 路径) | 小(补 Dim1.3 + 3 项新增) |
| verdict | NO-GO(三层) | NO-GO | **RECOMMENDED** |

---

## 4. RQ2 — 现有框架/配置/worktree 能否近似三层?

### 4.1 lane-as-process 三层映射表

| #155 层 | lane-as-process 实现 | 隔离 | 标识 | 现状 |
|---|---|---|---|---|
| **Director** | main lane interactive session @ `${MERCURY_ROOT}/Mercury`(canonical default) | main worktree | lane `main` | 已有(convention) |
| **Manager** | 独立 side lane(独立 Claude Code 进程,平台视角 main session) | per-lane worktree `${MERCURY_ROOT}/Mercury-<short>`(Rule 5.1) | lane short name + branch `lane/<short>/<N>-<slug>`(Rule 2.1) | 已有(multi-lane v1) |
| **Employee** | Manager 这个 side lane dispatch 的 sub-agent(dev/research/acceptance) | sub-agent 默认共享 Manager 的 cwd;file-edit 触发 auto-worktree(§5.3) | `.claude/agents/<name>.md` agent_type | 已有(sub-agent 机制) |
| **Department** | 1 side lane(Manager)+ N sub-agents(Employees)+ 独立 worktree | 上述组合 | 1 lane section in LANES.md + claimed Issue | 部分(LANES.md 已有 lane registry) |

### 4.2 现有 multi-lane v1 已做到哪一步

复用 #386 现状表 + #319 Dim1.2 可复用模块。已实现:

- **并行 lane**:main + side-bug + side-sot 3 lane active(#386 记录)→ Department 并行的载体已在。
- **worktree 隔离**:`git worktree add ${MERCURY_ROOT}/Mercury-<short>` per lane(Rule 5.1)→ Department 隔离已在。
- **branch 隔离**:`lane/<short>/<N>-<slug>`(Rule 2.1)→ Department 工作分支已在。
- **bg session dispatch**:`claude --bg` in lane cwd(#386 Phase 2 verified `isolation:"none"` + SessionStart hook fire)→ Manager 启动机制已在。
- **sub-agent dispatch**:main lane 已常态用 `.claude/agents/*.md`(develop @ `fa3e171` 的 in-repo 角色:`dev` / `research` / `acceptance` / `design` / `critic` / `main` + game-dev 三件套 `game-analyst`/`game-critic`/`game-researcher`)→ Manager→Employee dispatch 是现成能力(Manager 复制 main lane 的 sub-agent 用法即可)。注:`designer`/`executor`/`explore`/`planner`/`architect` 等是用户级 OMC(oh-my-claudecode)全局 agent(`~/.claude`),非 Mercury in-repo 角色,不要混淆。
- **LANES.md registry + Issue-first + Rule 6 cross-lane**:Department 间协调骨架已在。
- **mem0 全局记忆**:`~/.claude/scripts/mem0_bridge.py` + `mem0_hooks.py`,跨 session/lane 共享(§6)→ 跨 department 记忆载体已在。

### 4.3 2-department bootstrap 在 5-lane cap 内

起步 2-department 并行:

```
Director  = main lane              @ ${MERCURY_ROOT}/Mercury        (lane 1)
Manager A = side lane (dept A)     @ ${MERCURY_ROOT}/Mercury-deptA  (lane 2)
  └─ Employees A = dept A 的 sub-agents (dev/research/acceptance)  ← 不占 lane 名额(sub-agent 不是 lane)
Manager B = side lane (dept B)     @ ${MERCURY_ROOT}/Mercury-deptB  (lane 3)
  └─ Employees B = dept B 的 sub-agents                            ← 不占 lane 名额
```

= **3 lanes**(main + 2 side),**落在 multi-lane v1 的 5-lane hard cap 内**。Employees 是 sub-agents 不占 lane 名额(sub-agent 不在 LANES.md registry,不计入 5-lane cap)。

**容量上限推算**:若每 department 1 side lane,5-lane cap 下最多 = 1 Director + 4 Manager = **4 department 并行**。这对单用户 5h quota 是合理上限(#319 Dim3.7 over-spawning 警告 + 每 Manager + 其 Employees 独立消耗 quota,见 §7/§8)。

**verdict(RQ2)**:现有 multi-lane v1 + worktree + sub-agent 机制**已能近似三层**,2-department bootstrap 无需扩 lane cap、无需改 lane protocol;缺口是协调自动化(§5),非结构可行性。

---

## 5. RQ3 — 架构改动范围与准备工作

### 5.1 复用 #319 Dim1.3 的 5 个 missing module

#319 已识别(本 ADR 不重复 effort 评分,只标三层相关性):

| #319 Dim1.3 missing module | 三层模型相关性 | 是否三层新增 |
|---|---|---|
| **Director → side lane dispatch** | **核心** — Director→Manager 这一跳就是它;三层最关键的待补协调 | 否(#319 已识别,三层强依赖) |
| **Cross-lane status aggregator** | Director 看各 Department 状态需要它(`.mercury/state/lane-status.json`) | 否(#319 已识别) |
| **Side lane auto-report** | Manager → Director 主动汇报进度/blocker | 否(#319 已识别) |
| **Lane lifecycle coordinator** | spawn/teardown/stale-prune Department(`lane-spawn.sh`/`lane-close.sh`/`lane-sweep.sh`) | 否(#319 已识别) |
| **Director command surface(lane-aware)** | Director 经 Telegram 把命令 route 到特定 Department(`@<lane> <cmd>`) | 否(#319 已识别) |

### 5.2 三层模型新增的 3 项需求(#319 两层未覆盖)

| 三层新增需求 | 描述 | 为何 #319 两层未覆盖 |
|---|---|---|
| **(N1) Manager 接收 Director 分配的标准机制** | Director 把一个 "项目/目标" 交给 Manager(side lane)时的 handoff 协议:项目描述 + 验收标准 + scope 边界 + 已分配 Issue,落成 Manager lane 的 bootstrap prompt + handoff 文件。区别于 #319 的 "Director→side lane dispatch"(那是**启动 lane**),N1 是 **Manager 启动后如何把一个 high-level 目标拆成 Employee 任务**的输入契约 | #319 的 Dev-Team 是扁平 session,Director 直接给任务即可;三层下 Director 给的是 "项目",Manager 还要再拆 → 需要标准化的 "项目 → Manager" 输入契约 |
| **(N2) Employee dispatch 标准化模板** | Manager(side lane)dispatch dev/research/acceptance sub-agent 时的标准 prompt 模板(复用 `.mercury/templates/` + #319 Dim2 提到的 archive `task-manager.ts` 的 `buildDevPrompt`/`buildResearchPrompt`/`buildAcceptancePrompt` 模式作 reference design)。让各 Department 的 Employee dispatch 同构 | #319 两层下没有 "Manager dispatch Employee" 这一层,模板需求不存在 |
| **(N3) Employee file-edit 的 worktree 自动隔离处理** | Employee(sub-agent)在 Manager 的 side lane worktree 内 file-edit 时,触发 #391 记录的 auto-worktree HYBRID 流程(平台 `tool_use_error` → `EnterWorktree` → branch `worktree-<name>`)。Manager 需手动处理该 worktree branch 的 keep/discard(#386 新约束:无自动 merge)。三层下这成为 Department 内部的常规流程 | #319 两层下 side lane session 本体 file-edit,不经 sub-agent → 不触发 sub-agent 的 auto-worktree;三层下 Employee 是 sub-agent,file-edit 必经此流程 |

### 5.3 N3 细节 — Employee file-edit worktree 处理(对照 #391)

#391 empirical:bg/sub-agent 在 shared checkout 第一次 Edit → 平台 PreToolUse 拒并返 `tool_use_error` → agent 调 `EnterWorktree` 进 `<lane-cwd>/.claude/worktrees/<name>/`(branch `worktree-<name>`)→ `ExitWorktree(action:"keep")` 保留 branch+dir on disk,**无自动 merge**,operator(此处 = Manager)手动决定 merge/discard。

**三层含义**:Department 内 Employee 的 file-edit 产物落在 Manager worktree 下的 auto-worktree branch。Manager 需在 Employee 完成后:(a) review auto-worktree branch 的 diff,(b) merge 回 Manager 的 lane branch 或 discard。这是 N3 要标准化的 Department 内部 reconciliation 步骤。**注**:read-only Employee(research/acceptance 只读)不触发此流程(#386 verified bare bg read-only 安全)。

### 5.4 Phased plan(proposal，P2 实现优先级低)

> 措辞为提案;实现优先级 P2,不高;Phase 2 PoC 另开 follow-up Issue,#155 保持 OPEN。

- **Phase 1 — 设计(本 doc)** ✅。#155 保持 OPEN。
- **Phase 2 — 2-department PoC(low-risk dogfood)**:
  - 复用现有 multi-lane v1,手动开 2 side lanes 作 2 Department(无需任何新脚本)。
  - 每 Manager 手动 dispatch 1-2 个 Employee sub-agent(dev + acceptance)消化一个真实小 Issue。
  - 验证点:(1) Manager(side lane)能成功 dispatch sub-agent(三层 depth-3 跑通);(2) Employee file-edit 经 N3 worktree 流程产出可 merge 的 diff;(3) 2 Department 并行不互相干扰(worktree + branch 隔离);(4) mem0 跨 department 记忆可检索(§6);(5) 落在 3-lane(≤5 cap)。
  - **不需要先建任何协调脚本** —— Phase 2 纯手动跑,验证三层结构本身可行。
- **Phase 3 — 协调自动化(条件触发,P2)**:仅当 Phase 2 验证三层值得 + 用户确认要常态化多 department,才补 #319 Dim1.3 的 5 module(尤其 Director→Manager dispatch)+ §5.2 的 N1/N2/N3。这些 module 的 effort 见 #319 Dim1.3(6-11 person-days,本 ADR 不重复)。
- **Phase 4 — per-department 记忆隔离(条件触发,P3)**:仅当 department 数增多导致 mem0 检索噪音可观测时,实施 §6 推荐的 metadata filter 方案。

---

## 6. RQ4 — 跨 team / department 记忆共享技术方案

### 6.1 现状(技术事实)

| 机制 | 现状 | scope | gap |
|---|---|---|---|
| **mem0 层** | 运行时 canonical = `~/.claude/scripts/mem0_bridge.py` + `mem0_hooks.py`;**repo 内有可审阅副本 `scripts/mem0_hooks.py`**(version-tracked,引用其稳定行号):Qdrant 单一 `"mercury"` collection(`collection_name` `scripts/mem0_hooks.py` L82),`user_id` 固定为常量 `_DEFAULT_USER = "mercury"`(L30,后续 `add`/`search` 以 `user_id=_DEFAULT_USER` 引用);所有 session/lane 写同一向量空间;dedup `_DEDUP_THRESHOLD = 0.92`(L31,cosine≥0.92);metadata 含 session_id/trigger/project_dir 但 **search 不按 metadata 过滤** | **已经跨 lane/department 全局共享** | 无 namespace/scoping → department 数增多后检索噪音 |
| **OMC `shared_memory` MCP** | `mcp__plugin_oh-my-claudecode_t__shared_memory_{write,read,list,delete,cleanup}`。**scope 已源码核实(§11.2,OMC v4.13.2)**:文件型 KV(`<root>/.omc/state/shared-memory/`),文件锁跨进程并发协调,namespace + TTL ≤ 7 天,config gate 默认 enabled | 候选机制(临时显式通道) | **默认 per-worktree → 不自动跨 lane**;跨 dept 需传同一 `workingDirectory` 或设 `OMC_STATE_DIR`。TTL ≤ 7 天 = 临时协调非持久记忆(持久层仍是 mem0) |
| **OMC `project_memory_*` / `state_*`** | per-project / OMC state 工具,session 列表存在 | per-project / OMC-internal | 非跨 department 通用记忆 |
| **原生 subagent `memory:` frontmatter** | 对照 sub-agents docs(2026-05-24):`memory: project` = `.claude/agent-memory/<name-of-agent>/`;`user` = `~/.claude/agent-memory/<name>/`;`local` = `.claude/agent-memory-local/<name>/`。enable 后系统注入 `MEMORY.md` 前 200 行 / 25KB,自动开 Read/Write/Edit。**Mercury 未启用** | per-agent-name(跨 department 同名 Employee 共享同一目录) | 既是优势(跨 dept 学习)也是问题(无法区分 dept A vs B 的同角色经验) |

**结论**:跨 department 记忆共享**技术上已实现**(mem0 全局);**未实现** per-department 隔离检索 —— 这是 **schema 设计问题,非可行性问题**。

### 6.2 per-department namespace 设计选项(proposal)

| 选项 | 机制 | Pro | Con |
|---|---|---|---|
| **(O1) metadata filter(推荐)** | mem0 写入时 metadata 加 `department_id`;检索时按 `department_id` filter(或 OR 多个 dept 做跨 dept 检索) | 单 collection 不变;向后兼容(现有 entry 无 `department_id` 视为 global);检索可选 scope-to-dept 或 cross-dept;最小改动 | 需 `mem0_hooks.py` 改 search filter(用户级变更,走 #259 式 Issue 治理);**mem0ai filter API 已 web-verify 支持(§11.1,装机版 1.0.11)** —— `search(filters={"AND":[{"user_id":"mercury"},{"metadata":{"department_id":...}}]})`;metadata 算子限 `eq`/`contains`/`ne`(多值用 `OR` 替 `in`) |
| **(O2) 多 collection** | per-department 一个 Qdrant collection(`mercury-deptA` 等)+ 一个 global collection | 物理隔离最干净 | collection 生命周期管理复杂;跨 dept 检索需多 collection query 合并;department 是动态的(spawn/teardown)→ collection 也要动态建删,运维面大 |
| **(O3) 原生 subagent `memory:`** | Employee sub-agent 用 `memory: project`(`.claude/agent-memory/<name>/`)持久化 per-role 经验 | 原生支持,零自建;version control 可共享(`project` scope) | 按 **agent-name** 隔离不是按 **department** 隔离 → dept A 的 dev Employee 与 dept B 的 dev Employee 共享同一 `.claude/agent-memory/dev/`,无法区分 → 不满足 #155 "跨 team 记忆共享但脱离特定任务" 的 per-team 语义;且与 mem0 层并存形成双记忆栈 |

**推荐**:**O1(metadata filter)**。理由:单 collection 不动、向后兼容、检索可选 scope、最小改动面。**取舍**:O1 依赖的 mem0ai metadata filter API **已 web-verify 支持**(§11.1,装机版 `mem0ai 1.0.11`;算子限 `eq`/`contains`/`ne`)—— R5 风险消解。若未来解除 `<2.0.0` pin 升 2.x,需把现行顶层 `user_id=` kwarg 迁进 `filters=`(§11.1 注)。若某装机版本不支持 filter,fallback 到 O2(多 collection)或检索后 client-side filter。**作为提案不强制实施** —— 仅当 §5.4 Phase 4 触发条件满足(检索噪音可观测)才落地。

> **建议(非强制)**:`shared_memory` MCP 的 scope 在 design 阶段实测;若证实跨 lane/process 共享且持久,可作为 mem0 之外的轻量跨 department 显式记忆通道(agent 主动 write/read,区别于 mem0 的被动 hook 注入)。本 ADR 标为候选,不下结论。

### 6.3 记忆共享 vs 任务隔离的张力

#155 要 "脱离特定任务后记忆共享" —— 即任务进行中各 department 隔离(worktree/branch),任务完成后经验进全局池。mem0 现状天然契合:mem0 hook 在 session lifecycle(SessionStart/End)注入/抽取,**记忆是任务后沉淀的**,不是任务中实时共享。O1 的 `department_id` metadata 让 "任务中按 dept scope 检索 + 任务后 cross-dept 共享" 两种模式可切换 —— 检索时不带 filter = 全局共享,带 filter = scope 到本 dept。这正好覆盖 #155 的语义。

---

## 7. 三层 vs 两层取舍分析

### 7.1 何时三层值得

- **复杂 department 需自身 orchestration**:一个目标任务内部有可并行的子工作(如 research + dev + acceptance 同时推进),Manager 协调多个 Employee 比单 session 串行快,且各 Employee 独立 context window → spread reasoning(#319 Dim3.7:"improvement strongly correlated with ... spreading reasoning across independent context windows")。
- **department 数 ≥ 2 且各自任务足够大**:每 department 的工作量足以喂饱 Manager + 2-3 Employee。

### 7.2 何时两层够用(不要过度三层)

- **简单任务线**:side lane 自己干活即可,无内部可并行子工作 → 强行加 Employee 层只增协调 overhead(#319 Dim3.7 over-spawning 警告:"spawning 50 agents for simple queries";"match agent count to actual task parallelism";"Three focused teammates often outperform five scattered ones")。
- **任务太小**:Agent Teams docs 同源警告 "Too small: coordination overhead exceeds the benefit" —— 同理适用 Employee 层。

### 7.3 over-spawning 风险量化(对照 #319 Dim3.7 + Agent Teams docs)

- **token 成本线性放大**:每 Employee 是独立 context window,token 随 Employee 数线性增长(Agent Teams docs §Token usage 同源结论)。三层下 = Manager token + Σ Employee token,再 × department 数。
- **单用户 5h quota 约束**:每 Manager + 其 Employees 独立消耗 quota。5-lane cap 下 4 department 并行已是 quota 合理上限(§4.3)。建议沿用 #319 Dim4 的 statusline `rate_limits.five_hour` pause marker 机制做 quota 护栏(本 ADR 不重复脚本)。
- **协调 overhead**:三层比两层多一层 dispatch + reconciliation(N3 worktree merge)。Manager 协调 Employee 的 overhead 只在 department 内部工作真正可并行时才回本。

**决策规则(proposal)**:**默认两层**(side lane 自己干活);**仅当 Manager 判断目标任务有 ≥2 个可并行子工作时,才在该 department 内开 Employee 层(三层)**。这把三层作为 per-department 的**可选升级**,而非全局强制结构 —— 与 #155 "各 department 原子结构一致" 不冲突(结构一致 = 都可以是 Manager+Employees,但 Employee 数按实际并行度定,可为 0 即退化两层)。

---

## 8. Risk Register

| # | Risk | Severity | Likelihood | Mitigation |
|---|------|----------|------------|------------|
| R1 | 误以为必须用 Agent Teams 实现三层 → 撞 no-nested-teams blocker | HIGH | MEDIUM | 本 ADR 明确 lane-as-process 路径,Agent Teams NO-GO for 三层(§3.1);Manager = independent main session 而非 teammate |
| R2 | over-spawning:简单任务也开三层 → token/quota 浪费 | HIGH | MEDIUM | §7.3 决策规则:默认两层,仅可并行子工作 ≥2 才开 Employee 层;沿用 #319 quota pause marker |
| R3 | Employee file-edit 的 auto-worktree branch 失管(无自动 merge)→ 产物丢失或污染 | MEDIUM | MEDIUM | N3(§5.3)标准化 Manager 的 reconciliation 步骤;read-only Employee 不触发;commit before exit |
| R4 | department 数增多 → mem0 检索噪音上升(无 per-dept scope) | MEDIUM | MEDIUM(随 dept 数) | §6.2 O1 metadata filter(Phase 4 条件触发);department 少时(2-4)噪音可接受,不急 |
| R5 | mem0ai search 不支持 metadata filter → O1 不可行 | MEDIUM→**消解** | LOW | **已 web-verify(§11.1):装机版 `mem0ai 1.0.11` 的 `search()`/`get_all()` 支持 `filters=` metadata 过滤,O1 前提成立。** 残留风险仅"未来解除 `<2.0.0` pin 升 2.x 时 `user_id=` kwarg 需迁移进 `filters=`";fallback O2 多 collection / client-side filter 仍备选 |
| R6 | 5-lane cap 限制 department 并行数(>4 dept 无法并行) | LOW | LOW | 单用户尺度 4 dept 已足;若需更多,先评估是否真有 4+ 可并行目标(over-spawning 反模式) |
| R7 | `shared_memory` MCP scope 与假设不符 | LOW→**澄清** | MEDIUM | **已源码核实(§11.2,OMC v4.13.2)**:文件锁**跨进程并发协调**(原"非跨 process"假设系误判),但默认 **per-worktree** → 不自动跨 lane;跨 department 共享需各 lane 传同一 `workingDirectory` 或设 `OMC_STATE_DIR`。TTL ≤ 7 天 = 临时协调非持久记忆。不作硬依赖结论不变,mem0 仍是主记忆栈 |
| R8 | 三层 depth=1 sub-agent 限制误读 → 试图让 Employee 再 spawn | LOW | LOW | §3.3 官方原文 "subagents cannot spawn other subagents";Employee 是天花板,不能成 sub-Manager |
| R9 | Manager(side lane)被误降格为 subagent(失去 dispatch 能力) | MEDIUM | LOW | Manager 必须是 independent Claude Code 进程(`claude` / `claude --bg` 启的 main session),不能用 `Agent` tool 从 main lane spawn 成 subagent |

---

## 9. Recommendation / Phased plan

### Verdict: CONDITIONAL_GO（phased，lane-as-process over Agent Teams，P2 实现优先级低）

**推荐路径**:lane-as-process 实现三层 —— Director=main lane / Manager=独立 side lane(平台视角 main session,可 dispatch sub-agents)/ Employee=Manager dispatch 的 sub-agents / Department = 1 side lane + N sub-agents + 独立 worktree。**不引入 Agent Teams、不引入 Managed Agents API。**

**GO-promotion 条件(若推进 Phase 2 PoC)**:
1. 2 side lane 作 2 Department 手动开通,落在 3-lane(≤5 cap)。
2. ≥1 个 Manager(side lane)成功 dispatch ≥1 个 Employee sub-agent(三层 depth-3 跑通)。**注:平台能力已 doc-confirmed(§11.3 —— agent-view docs "the session can start new subagents",bg session = 完整 main session),本条降级为"在真实 Mercury side lane 实跑确认",非"能力存疑"。**
3. Employee file-edit 经 N3 worktree 流程产出可 merge 的 diff(或 read-only Employee 验证无 worktree 触发)。
4. mem0 跨 department 记忆可检索(O1 metadata 设计前先验证全局共享现状)。

**Phased plan(proposal，P2 优先级低)**:见 §5.4(Phase 1 设计 ✅ → Phase 2 2-dept 手动 PoC → Phase 3 协调自动化条件触发 → Phase 4 per-dept 记忆隔离条件触发)。

**给主 agent 的交接要点**:
- **#155 保持 OPEN**(设计提案,非实现);P2 实现优先级不高,调研可优先。
- **不改 DIRECTION.md / EXECUTION-PLAN.md** —— 本 ADR 的 DIRECTION 增补均为 *建议*(§2 / §6 标注),需用户独立决策。
- Phase 2 PoC **不需先建任何协调脚本** —— 纯手动跑 2 department 验证三层结构本身;协调自动化(Dim1.3 + N1/N2/N3)留 Phase 3 条件触发。
- ~~design 阶段必做 web-verify~~ **已于 S143 完成(2026-05-26,§11)**:(a) mem0ai metadata filter VERIFIED-YES(装机版 1.0.11,§11.1);(b) `shared_memory` MCP scope 已源码核实(§11.2)。Phase 2 起不再有"待 web-verify"门槛,仅余 Mercury 场景 empirical 实跑(GO 条件 2-4,§11.3 降级说明)。
- 三层是 **per-department 可选升级**,默认两层;仅可并行子工作 ≥2 才开 Employee 层(§7.3 决策规则,防 over-spawning)。

> **建议未来 DIRECTION 增补(非强制)**:若用户决定常态化多 department 并行,DIRECTION.md 可考虑新增 "多团队并行(Department)" 章节,记录 lane-as-process 三层映射 + per-department 同构原子单元 + over-spawning 决策规则。**这是建议,不在本 ADR 范围内实施。**

---

## 10. Source Index

**起草时**(S135)的核实日期为 2026-05-24,沿用 #319 / #386 path-convention 节;**S143 research 收尾新增的来源 [5](agent-view)/ [6](mem0 docs)fetch 日期为 2026-05-26**(详见 §11)。每个外部事实带 URL + fetch 日期;无法 web-verify 项标 UNVERIFIED。in-repo LOC/path 引用基于 develop @ `fa3e171`(起草基线);S143 web-verify pass 在 develop @ `af7953c`。

### Anthropic 官方(2026-05-24 复核)

- [1] Agent Teams — 限制 "No nested teams: teammates cannot spawn their own teams or teammates. Only the lead can manage the team" / "One team at a time" / "Lead is fixed" / "No session resumption with in-process teammates" / "Split panes ... isn't supported in ... Windows Terminal";experimental `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`,最低 v2.1.32+: <https://code.claude.com/docs/en/agent-teams>(fetched 2026-05-24,全部声明 28 天后仍有效未 stale)
- [2] Subagents — "Subagents cannot spawn other subagents"(嵌套深度硬限制=1)/ "This restriction only applies to agents running as the main thread with `claude --agent`. ... `Agent(agent_type)` has no effect in subagent definitions" / `memory:` frontmatter(`project`=`.claude/agent-memory/<name>/`,`user`=`~/.claude/agent-memory/<name>/`,`local`=`.claude/agent-memory-local/<name>/`,注入 MEMORY.md 前 200 行/25KB): <https://code.claude.com/docs/en/sub-agents>(fetched 2026-05-24)
- [3] Managed Agents API(平台层)— coordinator+worker,"coordinator can only delegate to one level of agents; depth > 1 is ignored",最多 20 unique agents / 25 并发 threads(cited 页支撑 depth 限制 + roster/thread caps;**该页本身不规定 auth 方式**,auth 以官方 auth docs 为准 —— 走平台 API 路径而非 Claude Code CLI subscription 路径): <https://platform.claude.com/docs/en/managed-agents/multi-agent>(#319 Dim3.1 引用,fetched 2026-05-24)
- [4] Git worktrees(`isolation: worktree` 行为参考): <https://code.claude.com/docs/en/worktrees>(fetched 2026-05-24)
- [5] Agent view / background agents(§11.3 web-verify 新增)— "Each background session is a full Claude Code conversation" / "the session can start new subagents" / bg session 自动进 `.claude/worktrees/` 隔离 + `worktree.bgIsolation:"none"`(CLI v2.1.143+): <https://code.claude.com/docs/en/agent-view>(fetched 2026-05-26)
- [6] mem0 metadata filter(§11.1 web-verify 新增)— OSS `Memory` `search()`/`get_all()` `filters=` 支持 metadata 等值过滤;算子限 `eq`/`contains`/`ne`: <https://docs.mem0.ai/core-concepts/memory-operations/search> + <https://docs.mem0.ai/platform/features/v2-memory-filters> + <https://docs.mem0.ai/migration/api-changes>(fetched 2026-05-26);装机版 `mem0ai 1.0.11`(pin `~/.claude/pyproject.toml` `>=1.0.11,<2.0.0`,PyPI <https://pypi.org/project/mem0ai/>)

### Mercury-内部引用(非外部 vendor 源)

- [#155] Issue #155 body(三层定义 + 起步 2 department): <https://github.com/392fyc/Mercury/issues/155>
- [#319] `agent-team-orchestration-feasibility-2026-04-26.md` — 两层 Director/DevTeam baseline,CONDITIONAL_GO;Dim1.3 5 missing module(本 ADR 复用,不重复 effort 评分);Dim3.7 over-spawning 警告;Dim2 archive `task-manager.ts` prompt builder 模式(N2 reference): `.mercury/docs/research/agent-team-orchestration-feasibility-2026-04-26.md`
- [#386] `agent-view-multi-lane-adaptation-2026-05.md` — multi-lane v1 现状(并行 lane / worktree / branch / bg dispatch `isolation:"none"` / SessionStart hook fire / agent view 监控): `.mercury/docs/research/agent-view-multi-lane-adaptation-2026-05.md`
- [#391] `agent-view-phase6-empirical-2026-05.md` — file-editing bg/sub-agent auto-worktree HYBRID(`tool_use_error`→`EnterWorktree`→branch `worktree-<name>`,`ExitWorktree` 无自动 merge): `.mercury/docs/research/agent-view-phase6-empirical-2026-05.md`(经 #386 引用)
- [mem0] 运行时 canonical = `~/.claude/scripts/mem0_bridge.py` + `mem0_hooks.py`(用户级,见 CLAUDE.md Related Repositories);**repo 内可审阅副本 = `scripts/mem0_hooks.py`**(version-tracked)— `_DEFAULT_USER = "mercury"`(L30)、`_DEDUP_THRESHOLD = 0.92`(L31,cosine≥0.92 dedup)、`collection_name "mercury"`(L82);引用 repo 副本稳定行号,不依赖会漂移的 user-level 行号。跨 department 记忆共享现状依据
- [shared_memory MCP] `mcp__plugin_oh-my-claudecode_t__shared_memory_{write,read,list,delete,cleanup}` — **scope 已源码核实(§11.2,OMC v4.13.2)**:文件型 KV `<root>/.omc/state/shared-memory/`,文件锁跨进程并发协调,默认 per-worktree(不自动跨 lane),TTL ≤ 7 天,config gate 默认 enabled。源码 `~/.claude/plugins/marketplaces/omc/src/lib/shared-memory.ts` 等
- DIRECTION.md(最高准则;本 ADR 仅 *建议* 增补,不直接改): `.mercury/docs/DIRECTION.md`
- multi-lane v1 protocol(authority): `memory/feedback_lane_protocol.md` v1(user-memory,不在 repo)+ `.mercury/docs/guides/lane-naming.md`(in-repo worktree/branch convention)
- CLAUDE.md: MANDATORY RESEARCH PROTOCOL / modular design / Issue-first / 5-lane cap context

### 已核实项(2026-05-26 S143 web-verify pass — 原 UNVERIFIED 三项全部 resolved,详见 §11)

起草时(2026-05-24 S135)标的 **3 个核心 UNVERIFIED 门槛项**已于 2026-05-26(S143,develop @ `af7953c`)全部 web-verify / 源码核实。结论摘要如下,完整证据 + 来源 URL 见 **§11**。

> **关于"全部 resolved"的范围澄清**:本句指**原 3 个核心门槛项**(mem0ai filter / shared_memory scope / bg-spawn-subagent)全部 resolved。§11.2 来源行另有一条 **非门槛的附带 UNVERIFIED 链接**(OMC 源码注释里转录的 GitHub issue-URL `#1119`,未单独 web-fetch)—— 它不是 3 个核心门槛之一,不影响任何 scope 事实或 verdict,仅为 provenance footnote 保留 UNVERIFIED 标注以示诚实。读者不应将其误解为"仍有核心门槛未关闭"。

1. **mem0ai search 是否支持 metadata filter**(O1 前置)— **VERIFIED-YES**(对 Mercury 实际安装版本 `mem0ai 1.0.11`):OSS `Memory` 类 `search()`/`get_all()` 接受 `filters=` 参数,支持按自定义 metadata(如 `department_id`)等值过滤;算子受限(`eq`/`contains`/`ne`,不支持 `in`/`gt`/`lt`)。O1 方案前提成立。详见 §11.1。
2. **`shared_memory` MCP 真实 scope**(R7)— **VERIFIED(源码核实 OMC v4.13.2)**:文件型 KV store(`<root>/.omc/state/shared-memory/{ns}/{key}.json`,`root` 默认按 worktree/cwd),文件锁跨进程并发协调,TTL ≤ 7 天(临时协调,非持久记忆),config gate `agents.sharedMemory.enabled` 默认 enabled。**默认 per-worktree → 不自动跨 lane**;跨 department 共享需各 lane 传同一 `workingDirectory` 或设 `OMC_STATE_DIR`。详见 §11.2。
3. **bg session 作 main session dispatch sub-agent**(GO 条件 2)— **VERIFIED-YES(平台能力层,官方文档)**:agent-view docs 明确 "the session can start new subagents";bg session = 完整独立 main session(非 subagent)。平台能力已 doc-confirmed;Mercury 场景的 empirical 跑通仍属 Phase 2 PoC(GO 条件 2 现降级为"实跑确认",非"能力存疑")。详见 §11.3。

---

## 11. Web-verify 结论(2026-05-26,S143)— 3 个 UNVERIFIED 项核实记录

> 本节是 §10 末尾原 3 个 UNVERIFIED 项的权威核实记录。核实在 develop @ `af7953c`(S143 web-verify pass,Issue #155 research 收尾)进行。外部 SDK/API 对照官方文档(MANDATORY RESEARCH PROTOCOL);OMC 内部机制对照 plugin 源码(OMC v4.13.2)。本节不改 verdict(§9 CONDITIONAL_GO 不变),仅把"待验证门槛"转为"已验证事实",并据此微调 R5/R7 与 GO 条件 2。
>
> **路径约定**:本节出现的 `~/.claude/...` 一律是**用户级安装的源码 / 配置引用(evidence pointer:核实时实际读取的文件位置)**,**非可执行配置**,沿用本 doc 顶部 §"Path conventions" 的约定 —— `~/.claude` ≡ `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`(具体值因 install / 多账户而异)。与既有 §6.1 / §10 来源行的 `~/.claude/scripts/mem0_*.py` 引用风格一致;读者不应将这些证据路径当作 Mercury 仓库内的可运行配置。

### 11.1 mem0ai metadata filter —— VERIFIED-YES(对 `mem0ai 1.0.11`)

**问题**:O1 方案(per-department namespace = 写入 `metadata={"department_id": ...}` + 检索按其过滤)是否被 mem0ai 检索 API 支持。

**核实版本前提(重要)**:Mercury mem0 层实际安装 **`mem0ai 1.0.11`**(`~/.claude/pyproject.toml` pin `mem0ai>=1.0.11,<2.0.0`;`~/.claude/uv.lock` 锁 1.0.11,sdist upload 2026-04-06)。**不是** PyPI 当前最新的 2.0.2。下述结论针对 1.0.x 线核实。

**结论**:**VERIFIED-YES**。OSS 自托管 `Memory` 类(Mercury 经 `scripts/mem0_hooks.py` 用的就是它,配本地 Qdrant)的 `search()` / `get_all()` 接受 `filters=` 参数,可按自定义 metadata 字段(如 `department_id`)做等值过滤:

```python
# 写入(add 时附 metadata)
m.add(messages, user_id="mercury", metadata={"department_id": "deptA"})

# 检索(filters 组合 user_id + metadata)
m.search(query, filters={"AND": [
    {"user_id": "mercury"},
    {"metadata": {"department_id": "deptA"}},
]})
```

**算子限制(落地注意)**:metadata 字段算子比实体 ID(`user_id`/`agent_id` 等)窄 —— 支持等值 / `contains` / `ne`,**不支持** `in` / `gt` / `lt` 等(否则触发 `FilterValidationError`);多值匹配须用 `OR` 包多个等值条件替代 `in`。`filters=` 在 `search`/`get_all`/导出/删除通用。

**版本-增量含义**:1.0.0 Beta migration 文档记录 `search()` 从"基础键值过滤"升级到"算子 + 逻辑组合过滤",1.0.11 在此线内 → metadata filter 可用。**O1 是对现有 `mem0_hooks.py` 的增量改动**(add 加一个 metadata key + search 加 `filters`),现行 `user_id=_DEFAULT_USER` 调用不受影响(1.0.x 仍接受顶层 `user_id` kwarg;2.x/3.x 才要求全部进 `filters={}` —— Mercury pin `<2.0.0` 故不受该 break 影响,但**未来若解除 pin 升 2.x,`mem0_hooks.py` 的 `user_id=` 调用需迁移进 `filters=`**,此为 O1 落地前的版本依赖注意点)。

**对 ADR 的影响**:R5("mem0ai 不支持 filter → O1 不可行")**风险消解**(filter 在装机版本可用);O1 维持为 §6.2 推荐方案,落地仅需 Phase 4 触发条件满足 + 按装机版本 pin filter 语法。

**来源**(fetch 2026-05-26):
- mem0 v2 Memory Filters:<https://docs.mem0.ai/platform/features/v2-memory-filters>
- mem0 Search Memory:<https://docs.mem0.ai/core-concepts/memory-operations/search>
- mem0 API changes (v0.x → v1.0.0):<https://docs.mem0.ai/migration/api-changes>
- mem0ai PyPI(当前 2.0.2;Mercury pin 1.0.11):<https://pypi.org/project/mem0ai/>
- 装机版本实测:`~/.claude/.venv` `importlib.metadata.version("mem0ai")` → `1.0.11`;pin 见 `~/.claude/pyproject.toml` + `~/.claude/uv.lock`

### 11.2 `shared_memory` MCP scope —— VERIFIED(源码核实 OMC v4.13.2)

**问题**:OMC `shared_memory` MCP(`mcp__plugin_oh-my-claudecode_t__shared_memory_*`)的真实 scope —— 跨 process/lane 是否共享、持久化位置;能否作 mem0 之外的显式跨 department 记忆通道(R7)。

**核实方法**:读 OMC plugin 源码 `~/.claude/plugins/marketplaces/omc/src/lib/shared-memory.ts` + `.../tools/shared-memory-tools.ts` + `.../lib/worktree-paths.ts`(`getOmcRoot`),交叉 `docs/TOOLS.md`。

**结论**(以下 scope 事实全部源码可证;唯下方来源行的 OMC issue-URL 系源码注释转录、未 web-fetch,单独标 UNVERIFIED):

| 维度 | 事实 | 来源(OMC v4.13.2 源码) |
|---|---|---|
| **存储** | 文件型 JSON KV:`<root>/.omc/state/shared-memory/{namespace}/{key}.json` | `shared-memory.ts` `SHARED_MEMORY_DIR='state/shared-memory'` + `getNamespaceDir` |
| **root 解析** | `getOmcRoot(worktreeRoot)`:无 `OMC_STATE_DIR` 时 = `join(worktreeRoot ?? getWorktreeRoot() ?? cwd, '.omc')`;设 `OMC_STATE_DIR` 时 = `join(OMC_STATE_DIR, getProjectIdentifier(root))`(集中化,projectId 同项目稳定) | `worktree-paths.ts` `getOmcRoot` L193-215 |
| **跨进程并发协调** | 写走 `withFileLockSync(timeoutMs:500, retryDelayMs:25)`+ tmp-write→`renameSync` 原子替换,锁失败 best-effort 回退无锁写;有 concurrency + lock-timeout 单测 | `shared-memory.ts` `writeEntry` L199-203 |
| **namespace + TTL** | namespace/key 校验(alphanumeric + `.-_`,禁 `..` 路径穿越,≤128 char);TTL 可选 ≤ 604800s(7 天),过期 entry 读时自动删 | `validateNamespace`/`validateKey`/`isExpired` |
| **config gate** | `agents.sharedMemory.enabled` ∈ `~/.claude/.omc-config.json`;**key/文件缺失默认 enabled**(opt-out)。当前 Mercury 环境 `.omc-config.json` 无该 key → 默认启用 | `isSharedMemoryEnabled` L63-74;实测 `.omc-config.json` 无 sharedMemory key |
| **设计用途** | 源码 docstring:"cross-session memory sync between agents in /team and /pipeline workflows" | `shared-memory.ts` 头注释 + `shared-memory-tools.ts` 头注释 |

**对 #155 的 scope 判定**:
- **用文件锁做**跨进程 / 跨 session 的**并发协调**通道,专为 `/team`、`/pipeline` 多 agent 协调设计 —— **修正 R7 原假设**("非跨 process 共享"是误判:它确有跨进程文件锁协调)。**注:协调是 best-effort —— 锁获取失败(timeout 500ms)会降级为无锁写(L199-203),故是"跨进程并发协调"而非"强一致保证"**;对临时 handoff 场景足够,但不应当作事务存储。
- **但**默认 scope = **per-worktree-root**(`getWorktreeRoot()`)。Mercury 每 lane 独立 worktree(`${MERCURY_ROOT}/Mercury-<short>`)→ 各 lane 的 `.omc/state/shared-memory/` **互不相通**,**不自动跨 lane/department 共享**。
- **跨 department 显式共享的两条落地路径**:(a) 各 lane 调 `shared_memory_*` 时传同一 `workingDirectory`(指向共享 root,如 main checkout);(b) 设 `OMC_STATE_DIR` env → 集中化 store 按 `getProjectIdentifier` 聚合(同项目各 worktree 映射同一 store)。再配一个 `department-shared` namespace 即成显式跨 dept 通道。
- **关键取舍 vs mem0**:shared_memory **TTL ≤ 7 天 = 临时协调**(handoff / 进行中状态),**非持久记忆**;mem0 仍是 §6 的**持久**跨 department 记忆层。两者互补:mem0 = 任务后沉淀的长期经验池;shared_memory = 任务中的短时显式 handoff 通道(若启用 + 配共享 root)。

**对 ADR 的影响**:R7 由"scope 与假设不符(可能非跨 process)"更新为"scope 已核实:跨进程并发协调但默认 per-worktree,跨 lane 需共享 root/`OMC_STATE_DIR`";§6.1 表 `shared_memory` 行去 UNVERIFIED。**不作硬依赖**结论不变(mem0 仍是主记忆栈;shared_memory 是可选显式通道)。

**来源**:OMC plugin 源码 `~/.claude/plugins/marketplaces/omc/src/lib/shared-memory.ts` / `tools/shared-memory-tools.ts` / `lib/worktree-paths.ts` / `docs/TOOLS.md`(OMC v4.13.2,cache 路径 `~/.claude/plugins/cache/omc/oh-my-claudecode/4.13.2/`);OMC issue ref 见源码头注释 <https://github.com/anthropics/oh-my-claudecode/issues/1119>(注:该 GitHub repo path 为源码注释原文转录,**UNVERIFIED — 未 web-fetch 该 URL**)。

### 11.3 bg session dispatch sub-agent —— VERIFIED-YES(平台能力层,官方文档)

**问题**:side lane(`claude --bg` 启的 bg session,平台视角 main session)能否 dispatch 自己的 sub-agent(Employee)—— 三层 lane-as-process 的 GO 条件 2 / §3.3 的关键能力假设。

**结论**:**VERIFIED-YES(平台能力层)**。官方 agent-view 文档明确(逐字):

- "Each background session is a full Claude Code conversation that keeps running without a terminal attached" —— bg session = 完整 main session。
- "Once in the background, **the session can start new subagents**, monitors, and background commands, and those keep running across later detach and reattach." —— bg session **可** spawn subagent。
- Troubleshooting "Cannot open agents because background tasks are running":列举 bg session 的 in-flight work 含 "a subagent" —— 进一步佐证 bg session 内有 subagent 运行。
- sub-agents 文档 Note:"Subagents work within a single session. To run many independent sessions in parallel ... see background agents" —— bg session 是**独立 session**(非某 main 的 subagent),故不受 "subagents cannot spawn other subagents" 的 depth=1 限制。
- 启动方式佐证三层:`claude --bg "<prompt>"` 起 bg main session;`claude --agent <name> --bg "..."` 可让一个 named subagent 定义作为 bg session 的 **main agent** 运行(此时它是 main thread,能 spawn subagent),区别于把它作为 subagent spawn。

**降级说明(诚实标注)**:平台**能力**已 doc-confirmed(不再是"理论上")。但"Mercury 真实 side lane 场景下端到端跑通 bg-session→spawn-Employee→产出可 merge diff" 仍是 **empirical PoC**,属 §5.4 Phase 2 范畴。故 GO 条件 2 的措辞由"能力存疑待验证"降级为"实跑确认"(能力已确证,只差一次真实 dogfood)。

**附带更新(N3 / Employee file-edit worktree)**:agent-view 文档给出比 #391 更新的隔离细节,可用于 §5.3 N3 落地:bg session "**Before editing files, Claude moves the session into an isolated git worktree under `.claude/worktrees/`**";若已在 linked worktree 内 / 非 git repo / 写在 cwd 外则跳过;`worktree.bgIsolation: "none"`(settings,需 CLI v2.1.143+)可整体关闭隔离。这与 #391 的 sub-agent `EnterWorktree` HYBRID 一致,且明确了 Manager 这一层(bg session 本体)也会自动进 worktree —— N3 reconciliation 同时覆盖 Manager 本体 + Employee sub-agent 两级 file-edit 产物。

**来源**(fetch 2026-05-26):
- Claude Code Agent view(background agents):<https://code.claude.com/docs/en/agent-view>
- Claude Code Subagents("Subagents cannot spawn other subagents" + main-thread spawn + bg session Note):<https://code.claude.com/docs/en/sub-agents>

### 11.4 核实对 verdict / risk / GO 条件的净影响(汇总)

| 项 | 起草时(S135) | 核实后(S143) |
|---|---|---|
| **§9 Verdict** | CONDITIONAL_GO | **不变**(三项均朝有利方向,无新 blocker) |
| **R5**(mem0ai 不支持 filter) | MEDIUM / LOW likelihood | **消解** —— 1.0.11 支持 filter;残留风险仅"未来升 2.x 需迁移 `user_id` 进 `filters=`" |
| **R7**(shared_memory scope 不符) | LOW / MEDIUM likelihood | **澄清** —— 跨进程并发协调但默认 per-worktree;跨 lane 需共享 root/`OMC_STATE_DIR`;非硬依赖结论不变 |
| **GO 条件 2**(bg spawn subagent) | 能力待 empirical 验证 | **能力 doc-confirmed**;仅余 Phase 2 一次真实 dogfood(措辞降级为"实跑确认") |
| **O1 可行性**(§6.2) | 依赖 UNVERIFIED filter API | **前提成立**;Phase 4 触发条件满足即可落地(按装机版本 pin 语法) |

**仍属 Phase 2/3 的 empirical(非本次能 web-verify)**:三层 depth-3 在真实 Mercury side lane 的端到端跑通(GO 条件 2-4)依然需要 Phase 2 PoC 手动 dogfood —— 本次只清掉了"平台能力 / API 是否支持"层面的不确定性,把 Phase 2 的风险从"结构是否可行"收窄到"Mercury 场景实跑是否顺畅"。
