---
issue: 462
title: "Phase 2 PoC — 2-department lane-as-process 三层 dogfood 实证结果 (Director-Manager-Employee depth-3)"
date: 2026-05-27
session: S144 (main lane)
verdict: "GO (三层 lane-as-process 结构在 Mercury 真实环境实跑可行) — GO 条件 1/2/4 PASS;GO 条件 3 CONDITIONAL PASS + 1 个 ADR-altering finding:Employee(sub-agent)file-edit 默认 NOT 触发 worktree 隔离(官方设计:subagent 默认在 parent cwd,需显式 isolation: worktree frontmatter),推翻 ADR #155 §5.3 N3 的『Employee file-edit 经 auto-worktree HYBRID』假设。Department 隔离边界 = Manager 的独立 worktree,非 Employee auto-worktree。"
relation: "executes ADR #155 (issue-155-multi-team-architecture-2026-05.md, CONDITIONAL_GO) §5.4 Phase 2 承诺;矫正 §5.3 N3 + §11.3;引用 #386/#391 (agent-view + file-edit 隔离基线)"
research_protocol: "外部 CLI/SDK 行为对照官方文档核实 2026-05-27(MANDATORY RESEARCH PROTOCOL):agent-view docs + sub-agents docs(fetched 2026-05-27);实证 = 本 session 真实 bg session dogfood(CLI v2.1.150)。无法 web-verify 或受环境限制项标 UNVERIFIED。"
---

# Issue #462 — Phase 2 PoC: 2-department lane-as-process 三层 dogfood 实证结果

> 本 doc 是 ADR #155 §5.4 承诺的 Phase 2 PoC 实证记录。**不改代码、不改 DIRECTION.md/EXECUTION-PLAN.md。** #155 + #462 保持 OPEN(实证完成后由用户决定 Phase 3 去留)。本 doc 可 *建议* ADR #155 的 N3 修正,措辞为建议,实际 ADR 编辑另开动作。

---

## Executive Summary / 结论

**Verdict: GO(三层 lane-as-process 结构在 Mercury 真实环境实跑可行)+ 1 个 ADR-altering finding。**

ADR #155 用 web-verify 证明了"平台能力可行"(bg session 能 spawn subagent / mem0 filter 可用 / shared_memory scope 已知)。本 PoC 把它推进到"Mercury 场景实跑可行",4 个 GO 条件结果:

| GO 条件 | 结果 | 一句话 |
|---|---|---|
| **(1)** 2 Department 并行 ≤ 5-lane cap | ✅ **PASS** | main(Director)+ 2 bg session(Manager A/B)= 3 并行 process,Employee sub-agent 不占 lane 名额 |
| **(2)** Manager(side lane)spawn Employee sub-agent(depth-3) | ✅ **PASS** | 3 次独立 spawn 全成功(probe Explore + Manager A/B 各 1 general-purpose Employee) |
| **(3)** Employee file-edit 产出可 merge diff(经 N3 worktree 流程) | ⚠️ **CONDITIONAL PASS** | 可 merge diff 产出 ✅;但 **Employee file-edit 默认 NOT 触发 worktree 隔离** → 见 §3 finding |
| **(4)** mem0 跨 department 记忆全局共享 | ✅ **PASS**(结构性 + 物理;非 live-recall 端到端) | 磁盘单一 `collection/mercury` + 代码固定 `user_id="mercury"`;无 per-lane/dept collection。live recall 受 shell 无 key 限制(标 UNVERIFIED,不计入 PASS 依据) |

**核心 finding(§3)**:对照 probe 决定性证明 —— bg session **本体**(Manager 层)file-edit **自动进** `.claude/worktrees/`(隔离 YES);但 Employee(Agent tool spawn 的 sub-agent)file-edit **不触发**隔离,直接落 Manager 的 working checkout(隔离 NO)。这是**官方设计行为**(sub-agents docs:subagent 默认在 parent cwd,需显式 `isolation: worktree` frontmatter 才隔离),**非 bug**,但**推翻 ADR #155 §5.3 N3** 的"Employee file-edit 触发 #391 auto-worktree HYBRID"假设。

**对 Department 设计的含义(§4)**:三层结构本身跑通,但**隔离边界是 Manager 的独立 worktree(`Mercury-deptX`),不是 Employee 的 auto-worktree**。本 PoC 用户选的"2 bg session 都起在同一 main worktree"方案下,2 个 Department 的 Employee 都写**同一个 main checkout**(靠不同文件名才没冲突)—— 实证了 ADR §4.1"每 Manager 独立 worktree"设计的**必要性**:不给每个 Manager 独立 worktree,Department 间就无 Employee-level 写隔离。

---

## 1. PoC 方法(增量去风险,先小后大)

**路径约定(沿用 ADR #155 §Path conventions)**:`${MERCURY_ROOT}` = Mercury main checkout(本机按 CLAUDE.md "Install to D drive" policy 落 D 盘,不在仓库内容固化具体机器路径);`~/.claude` ≡ `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`,出现处均为用户级 evidence pointer。下文实证报告里 Manager/Employee 转述的绝对路径已统一占位符化(原始 dogfood 落点 = `${MERCURY_ROOT}` 解析后的真实路径)。

**实证环境**:Claude Code CLI **v2.1.150**(≥ v2.1.143,支持 agent-view + `worktree.bgIsolation`);模型 Opus 4.7(1M context);user-level `~/.claude/settings.json` `permissions.defaultMode = "bypassPermissions"`(全局,bg session 继承);**无** `worktree.bgIsolation` 设置(默认隔离行为);2 个 Department bg session 起在 main worktree `${MERCURY_ROOT}`(用户决策:纯 synthetic scratch + 2 个并行 bg session,不正式注册 LANES.md lane)。

**Step 0 — GO 条件 2 最小 probe(keystone,先做)**:在 main lane 起 1 个 throwaway bg session(`poc462-probe`,`claude --bg`),让它 dispatch 1 个 read-only `Explore` sub-agent 读 `CLAUDE.md` 首个 heading 并报告。用最小代价证掉最大未知(bg session 内能否 spawn subagent)。结果:`DEPTH3-PROBE-RESULT subagent=Explore spawned=YES heading=# Mercury — Claude Code`,`Worked for 15s`。**PASS** → depth-3 第二跳跑通。throwaway,完成即 stop+rm。

**Step 1 — 2-department scale**:起 2 个并行 bg session(`deptA-mgr` / `deptB-mgr` = 2 Manager),各 dispatch 1 个 `general-purpose` Employee sub-agent 做 synthetic file-edit(deptA 写 tactical-RPG turn-order 需求 / deptB 写 save-load 需求,各 3 bullet),Employee 报告 worktree 隔离字段(`entered_worktree` / `worktree_branch` / `file_abs_path` / `git_status`),Manager 转述。

**Step 2 — 对照 probe(根因定位)**:观察到 Employee file-edit 未隔离后,起 1 个对照 bg session(`poc462-selfedit`),让 **bg session 本体**(不 dispatch subagent)直接 file-edit,区分根因是「sub-agent 路径」还是「bypassPermissions/settings 全局」。

**监控约束(实证记录)**:agent-view docs 明确 "Subagents … a session spawns aren't listed as separate rows" —— Employee sub-agent 不在 `claude agents` 显示为独立 row。故 depth-3 第二跳的证据靠 `claude logs <bg-id>` 读 bg session transcript(Manager 转述 Employee 报告),不能靠 agent view 看 sub-agent row。

---

## 2. GO 条件逐项结果

### GO 1 — 2 Department 并行 ≤ 5-lane cap ✅ PASS

峰值并行:main lane(Director,本 session)+ `deptA-mgr` + `deptB-mgr` = **3 个并行 Claude Code process**。Employee sub-agent 在 Manager 的单 session 内运行(sub-agents docs:"Subagents work within a single session"),**不占 lane 名额、不在 `claude agents` roster 计数**。3 ≤ 5-lane hard cap。`claude agents --json` 实测同时列出 2 个 dept Manager(各 `kind` 隐含 bg)+ main。**结论**:2-department bootstrap 落在 cap 内,与 ADR §4.3 推算一致(5-lane cap 下最多 1 Director + 4 Manager = 4 department 并行)。

### GO 2 — Manager spawn Employee sub-agent(depth-3)✅ PASS

3 次独立 spawn 全成功:
- probe:`Explore` sub-agent,`spawned=YES`,返回正确 heading。
- `deptA-mgr`:`general-purpose` Employee,`spawned=YES`,产出有效 markdown(见 GO 3)。
- `deptB-mgr`:`general-purpose` Employee,`spawned=YES`,产出有效 markdown。

**结论**:bg session(平台视角 main session = Manager)成功 dispatch 自己的 sub-agent(Employee)—— ADR §3.3 lane-as-process 三层的核心能力在 Mercury 真实环境**实跑确认**(此前 ADR §11.3 仅 doc-confirmed 平台能力)。GO 条件 2 的"实跑确认"达成。

### GO 3 — Employee file-edit 产出可 merge diff ⚠️ CONDITIONAL PASS

**可 merge diff 产出 ✅**:两个 Employee 各写出实质有效内容(非占位):
- `poc462-scratch/deptA-employee-output.md`(608 B):tactical-RPG turn-order 3 需求(initiative/speed ordering、tie resolution 确定性、action-economy turn-queue 显示)。
- `poc462-scratch/deptB-employee-output.md`(626 B):save-load 3 需求(named multi-slot、load/restore + 损坏处理、durable storage + version/checksum 完整性)。
- 两文件落 main checkout,`git status --short` 显示 `?? poc462-scratch/`(untracked,可 `git add` + commit + merge)。

**但 worktree 隔离 NOT 触发**(见 §3 finding)：两个 Employee 均报 `entered_worktree=NO` / `worktree_branch=NONE`,文件直接写 Manager 的 main checkout 而非 `.claude/worktrees/`。因此 GO 3 是 **CONDITIONAL PASS**:diff 可 merge 这一目标达成,但"经 N3 worktree 流程"的路径假设被实证推翻。

### GO 4 — mem0 跨 department 记忆全局共享 ✅ PASS(结构性 + 物理;**非 live-recall 端到端**)

> **验收边界(严谨性声明)**:本条 PASS 的依据是**结构性 + 物理**两类证据(单 collection + 固定 user_id + 磁盘单一 collection 目录),**不含** live-recall 运行时查询验证(后者 UNVERIFIED,见本节末)。读者不应将 GO 4 PASS 理解为"端到端记忆检索已完整验证";它验的是"全局共享是结构/物理现状"这一 #462 GO 4 明确的范围,live cross-session recall 是受环境限制未跑的加强项。

验证目标(ADR + #462):仅验"现状全局共享",per-dept namespace 留 Phase 4。两类证据:

- **代码层(结构)**:`~/.claude/scripts/mem0_hooks.py` `_DEFAULT_USER = "mercury"`(L36)、`collection_name: "mercury"`(L88)、`_DEDUP_THRESHOLD = 0.92`(L37)。所有 session/lane/department 的 `add`/`search` 都以固定 `user_id="mercury"` 打到单一 `"mercury"` collection → 结构上无 per-lane/dept 隔离 = 全局共享。(注:行号为 user-level 实测;ADR #155 §6.1 引用的是 repo 副本 `scripts/mem0_hooks.py` 的 L30/L31/L82,两份文件行号有差异,事实一致。)
- **物理层(磁盘)**:本地 Qdrant store `~/.claude/scripts/mem0-state/qdrant/` 实测**只有一个** `collection/mercury/storage.sqlite`(文件 12288 B;含 `meta.json`(508 B) + `.lock`(13 B) 该目录共 12809 B),**无** per-lane/per-dept collection 目录 → 物理确证全局单一向量空间。

**live recall — UNVERIFIED(环境限制)**:`mem0_bridge.recall()` 需 `OPENAI_API_KEY` 做 embedding;当前 bash/PowerShell shell 均未注入该 key(`~/.claude/.env` 不存在;key 经 hook-only 机制注入运行时),故本 session 未能跑通一次 live cross-session recall query。**这不影响 GO 4 结论** —— 全局共享是"单 collection + 固定 user_id"的磁盘+代码事实,recall 必然命中同一空间;live query 仅是加强项,标 UNVERIFIED 以诚实记录。

> **生产/多 department caveat(勿照搬 PoC 配置)**:固定 `user_id="mercury"` + 单 `collection_name="mercury"` 是当前**单租户 PoC 现状**,其"全局共享"对当前单用户场景是 feature(跨 lane 记忆共享)。但若未来常态化多 department 并对记忆做隔离要求,**不应直接照搬此固定标识到需隔离的场景** —— 否则多 department 共享同一向量空间会有检索串读(ADR #155 R4)。per-department 隔离方案见 ADR §6.2 O1(metadata `department_id` filter,已 web-verify 支持)/ Phase 4 条件触发。本 PoC 范围仅验现状全局共享(#462 GO 4 明确 per-dept namespace 留 Phase 4),不实施隔离。

---

## 3. ADR-altering finding — Employee(sub-agent)file-edit 默认 NOT 触发 worktree 隔离

### 3.1 决定性对照证据

| file-edit 主体 | `entered_worktree` | 落点 | worktree branch |
|---|---|---|---|
| **bg session 本体**(Manager 层,`poc462-selfedit`,不经 subagent) | **YES** | `.claude/worktrees/poc462-self-edit/poc462-scratch/probe-self-edit.md` | `worktree-poc462-self-edit`(git worktree list 实测新建,logs `● Creating worktree(poc462-self-edit)` → `Switched to worktree on branch worktree-poc462-self-edit`) |
| **Employee sub-agent**(`deptA-mgr`/`deptB-mgr` dispatch 的 general-purpose) | **NO** | Manager 的 main checkout `${MERCURY_ROOT}/poc462-scratch/` | NONE |

同一环境(CLI v2.1.150 + bypassPermissions + main worktree)下,唯一变量是 file-edit 主体是 **bg session 本体** 还是 **它 spawn 的 subagent**。本体隔离、subagent 不隔离 → **根因是 sub-agent 路径,不是 bypassPermissions**(本体在 bypassPermissions 下仍隔离)。

### 3.2 官方文档印证 — 这是设计行为,非 bug

- **sub-agents docs**(fetched 2026-05-27,<https://code.claude.com/docs/en/sub-agents>):
  - "A subagent starts in the **main conversation's current working directory**. … To give the subagent an isolated copy of the repository instead, set `isolation: worktree`."
  - `isolation` frontmatter 字段:"No(required）… Set to `worktree` to run the subagent in a temporary git worktree … The worktree is automatically cleaned up if the subagent makes no changes" —— **默认未设 = 无 worktree,subagent 在 parent cwd**。
  - "When Claude spawns a fork through the Agent tool, it **can pass** `isolation: "worktree"` so the fork's file edits are written to a separate git worktree instead of your checkout" —— 隔离是 opt-in。
- **agent-view docs**(fetched 2026-05-27,<https://code.claude.com/docs/en/agent-view>):bg **session** 本体 "Before editing files, Claude moves the session into an isolated git worktree under `.claude/worktrees/`"(本体强制隔离);并 "To make a subagent always run in its own worktree regardless of how it was started, set `isolation: worktree` in its frontmatter"(subagent 需显式 frontmatter)。
- **社区佐证(GitHub issue,标注非官方文档)**:Issue #33045 "Agent tool isolation: `worktree` has no effect … agent runs in main repo";Issue #58433 "forced-worktree enforcement on background-session Edit/Write";Issue #29110/#38859 bypassPermissions × spawned agents 交互。这些印证"subagent file-edit 落 main repo"是被广泛观察到的真实行为(UNVERIFIED 作为官方行为定义,仅作社区现象佐证)。

### 3.3 对 ADR #155 §5.3 N3 的修正建议

ADR §5.3 N3 原假设(引 #391):"Employee(sub-agent)在 Manager 的 side lane worktree 内 file-edit 时,触发 #391 记录的 auto-worktree HYBRID 流程(平台 `tool_use_error` → `EnterWorktree` → branch `worktree-<name>`)"。

**实证 + 官方文档修正**:Employee(默认无 `isolation: worktree` frontmatter 的 sub-agent,如 Mercury 的 `dev`/`general-purpose`/`research`)file-edit **默认不触发 worktree 隔离**,直接写 Manager 的 working cwd。#391 记录的 auto-worktree HYBRID 是 **bg session 本体**(Manager 层)的行为,不是 subagent(Employee 层)的行为 —— ADR N3 把这两层的隔离机制混为一谈。

**场景适用范围(防读者困惑)**:本 PoC 的 2 个 Manager 起在 **main worktree** 而非 ADR N3 设定的"独立 side lane worktree";但 Employee 不隔离的根因是 **subagent 定义层缺 `isolation: worktree` frontmatter**(官方:subagent 默认在 parent cwd),**与 Manager 本身在哪个 worktree 无关**。官方文档已确证此为 subagent 默认行为,故结论对"Manager 在 main worktree"与"Manager 在独立 side lane worktree"两种场景同等适用 —— 后者只是把 Employee 的落点从 main checkout 换成 Manager 的 side lane checkout,仍无 Employee-level auto-worktree。

**建议 N3 改为**(措辞为建议,实际 ADR 编辑另开动作):
> Department 内 file-edit 隔离分两级,机制不同:
> - **Manager 本体**(bg session)file-edit → 自动进 `.claude/worktrees/`(agent-view 强制隔离,`worktree.bgIsolation:"none"` 可关)。
> - **Employee**(sub-agent)file-edit → **默认不隔离**,落 Manager cwd。要让 Employee 各自隔离,需在其 agent 定义加 `isolation: worktree` frontmatter(从 default branch 分支,非 parent HEAD;无改动自动清理)。
> 故 **Department 隔离边界 = Manager 的独立 worktree**;Department 内 Employee 默认共享 Manager checkout(同 dept 可接受)。

---

## 4. 对 Department 设计的含义

1. **三层结构跑通**,但隔离语义与 ADR N3 预期不同:**Department 间隔离靠 Manager 的独立 worktree**(`Mercury-deptA`/`Mercury-deptB`,ADR §4.1 原设计),**不靠** Employee 的 auto-worktree。
2. 本 PoC 用户选"2 bg session 都起在 main worktree" → 2 Department 的 Employee 都写**同一 main checkout**,无 Employee-level 隔离(本 PoC 靠 deptA/deptB 不同文件名避开冲突;若两 Employee 写同一文件会直接冲突)。**实证了 ADR §4.1"每 Manager 独立 worktree"的必要性**:常态化多 department 时,必须 `git worktree add Mercury-deptX` 给每个 Manager 独立 checkout,Employee 在其内写才能保证 Department 间不互踩。
3. **Phase 3 若推进**,N1/N2/N3 标准化中 N3 应明确两级隔离机制 + Department=独立 Manager worktree 边界;并评估是否给 file-edit Employee agent(`dev` 等)加 `isolation: worktree` frontmatter(注意 Issue #33045 反映该字段在某些场景可能失效 —— 社区报告,UNVERIFIED,需 Phase 3 单独 empirical 验证)。

---

## 5. Recommendation / 待用户决策

**总体 GO**:三层 lane-as-process 在 Mercury 真实环境实跑可行(GO 1/2/4 PASS,GO 3 conditional pass + N3 修正)。ADR #155 §9 Verdict(CONDITIONAL_GO)**不变** —— finding 是隔离机制的澄清,不构成新 blocker,反而强化了 ADR §4.1"独立 worktree per Manager"的设计。

**待用户决策点**(本 doc 不擅自推进):
- (a) 是否把 §3.3 的 N3 修正 + §4 含义回写进 ADR #155(主 lane 独占编辑 `.mercury/docs/research/`,可在后续 session 做)。
- (b) 是否推进 **Phase 3**(协调自动化:#319 Dim1.3 5 module + N1/N2/N3 标准化)—— 仅当用户确认要常态化多 department。ADR 定位 P2 优先级不高。
- (c) #155 / #462 去留:默认保持 OPEN(Phase 1 设计 + Phase 2 PoC 完成,跟踪 Phase 3+)。

---

## 6. Source Index

**实证(第一手,本 session dogfood,CLI v2.1.150)**:
- bg session probe `poc462-probe`(Explore spawn)/ `deptA-mgr` + `deptB-mgr`(general-purpose Employee file-edit)/ `poc462-selfedit`(本体 file-edit 对照)— 全部 throwaway,session+worktree+scratch 已 teardown(§7)。
- `claude agents --json`(roster)/ `claude logs <id>`(transcript 提取报告字段)。

**官方文档(对照核实 2026-05-27,MANDATORY RESEARCH PROTOCOL)**:
- [agent-view] Claude Code Agent view — bg session 本体强制 worktree 隔离 + "subagents … aren't listed as separate rows" + subagent isolation 需 frontmatter:<https://code.claude.com/docs/en/agent-view>
- [sub-agents] Claude Code Subagents — "A subagent starts in the main conversation's current working directory" + `isolation: worktree` 字段(默认无 worktree)+ fork `isolation:"worktree"` opt-in:<https://code.claude.com/docs/en/sub-agents>

**社区佐证(GitHub issue,UNVERIFIED 作官方行为定义,仅佐证现象)**:
- Issue #33045(Agent tool isolation worktree no effect)/ #58433(forced-worktree enforcement opt-out)/ #29110 / #38859(bypassPermissions × spawned agents):<https://github.com/anthropics/claude-code/issues/33045> 等。

**Mercury 内部**:
- ADR #155 `.mercury/docs/research/issue-155-multi-team-architecture-2026-05.md`(§5.3 N3 / §5.4 Phase 2 / §9 GO 条件 / §11.3)。
- #386 `agent-view-multi-lane-adaptation-2026-05.md` / #391 `agent-view-phase6-empirical-2026-05.md`(file-edit 隔离基线 — 本 PoC 矫正其对 subagent 层的适用范围)。
- mem0:`~/.claude/scripts/mem0_hooks.py`(user-level 实测 L36/L37/L88;ADR #155 §6.1/§10 引用的是 repo 副本 `scripts/mem0_hooks.py` L30/L31/L82,两套行号均正确,差异源于两份文件)+ Qdrant store `~/.claude/scripts/mem0-state/qdrant/collection/mercury/`。

---

## 7. Teardown 记录

- bg session(short ID 已脱敏 — 均为 ephemeral 本地 supervisor job ID,session 全部 stop+rm 后无再用价值):`poc462-probe`(probe 后即 stop+rm)/ `deptA-mgr`(rm)/ `deptB-mgr`(rm)/ `poc462-selfedit`(stop+rm;1 个 job state 残留 cosmetic,已不在 live roster)。
- worktree:`.claude/worktrees/poc462-self-edit`(`git worktree remove --force` + `prune`)→ `.claude/worktrees/` 空,`git worktree list` 仅 main + side-bug。
- scratch:`poc462-scratch/`(throwaway,内容已记录于 §2 GO 3,`rm -rf` 删除)。
- main checkout:`git status` 回 clean(仅 `.codex/prompts/` benign untracked,与 session 起点一致)。
- LANES.md:未改(用户决策"不正式注册 lane",PoC 用临时 bg session 形态)。
