# Harness 现代化调查 — 系统吸收 2026 Q1-Q2 Claude Code 编排原语

> 状态: **调查完成 / 待排期** | 调查日期: 2026-06-20 | 方法: ultracode Workflow(7 agents,5 维 web-verified fan-out + adversarial critique) | Parent: [Issue #101 harness roadmap](https://github.com/392fyc/Mercury/issues/101)
> Guardrail ADR: [`context-strategy-2026-05.md`](./context-strategy-2026-05.md)(#385,生效中)
>
> **核验声明**: 凡标 `[UNVERIFIED]` 的版本号/定价/changelog 具体数字未在本调查内完成官方 fetch 核验,落地前每个 sub-issue 必须按 CLAUDE.md「Web search before SDK/API code」实证。已由本会话环境确证者标 `[ENV-CONFIRMED]`。

---

## TL;DR

Mercury 当前 harness 的最大系统性缺口在「**多 agent 编排原语**」层:现状是「单任务线性链(dev-pipeline Main→Dev→Acceptance)+ 手工多 session 并行(LANES.md)+ 自然语言 skill(autoresearch/dual-verify)」,而 Claude Code/Anthropic 在 2026 Q1-Q2 已 GA 三大原语 — **动态 Workflow DSL**、**原生 Agent Teams**、**ultracode 持久模式**。三者 Mercury 均未吸收(`.claude/workflows/` 目录不存在,Glob 已确认)。

本 umbrella 以「**Workflow DSL 系统化**」为 P0 主线(填补最大空白且为其余条目提供宿主),P1 为「模型层/计量校准 + OMC 升级」快赢,P2 为需 PoC 的高价值项(Agent Teams / 主动调度 / advisor),P3 为审计/优化。**关键约束**:所有大规模 fan-out 必须对齐 #385 context 经济学护栏(Haiku 200K cliff / GPT-5.5 272K surcharge / Opus 4.7 tokenizer 最坏 1.35x 膨胀 / cache 拓扑),不可盲目并行 1000 subagent。

## 方法

ultracode Workflow `mercury-harness-upgrade-survey`(run `wf_1934f043-2dd`,7 agents / 532K tokens / 144 web 调用):5 维并行 research(`research` agent + WebSearch,high effort)→ synthesis(gap 分析 + sub-issue 提案)→ adversarial critique(完整性 + 去重 + 未核验声明)。critic 判定 `needs-more`,本文档已据其纠正(见 §纠正)。

---

## 差距分析(四梯队,已纠正)

### 第一梯队 — 多 agent 编排原语(P0 核心)
- **动态 Workflow DSL** `[ENV-CONFIRMED]`:agent/parallel/pipeline/phase 原语,JS 脚本编排,最多 16 并发 / 1000 agent/次,内置 adversarial-verify / judge-panel / loop-until-dry。触发:ultracode 关键词 / `/effort ultracode`。可保存 `.claude/workflows/*.js` 成 `/name` 命令复用。**Mercury 缺口**:目录不存在,autoresearch/dual-verify 仍是 Main context 串行编排,六大模式(fan-out/adversarial-verify/tournament/generate-and-filter/classify-and-act/loop-until-done)仅 autoresearch 内嵌一个弱化 adversarial 子步骤。
- **原生 Agent Teams** `[UNVERIFIED 版本号]`:peer-to-peer + 共享任务列表 + 独立 context,需 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`。**Mercury 缺口**:多 agent 全走 Main 中介 subagent,无 peer-to-peer。**注意**:可行性已由 **#319(CLOSED)** 结论,Agent Teams PoC 应承接其结论而非重做。
- **ultracode 持久模式** `[ENV-CONFIRMED]`:`/effort ultracode` = xhigh + 自动 workflow,可持久化为新会话默认。**Mercury 缺口**:无 workflows 目录故触发后无法保存复用;CLAUDE.md 无 ultracode 使用规范。

### 第二梯队 — 模型层/计量(P1 快赢,已纠正)
- **DEC-3 模型分层**:文档写 Opus 4.6 基准,但 **#385 实测记录 main lane 跑 100% Opus 4.7**,本会话自报 `claude-opus-4-8[1m]`。**真实漂移 = 4.7→4.8**(非 critic 前版本误述的 4.6→4.8)。effort 参数(low/med/high/xhigh/max)未注入任何 subagent 调度层。
- **cost-tracker(#361)**:critic 实读 `~/.claude/scripts/cost_tracker.py` 确认 PRICING **已有** `claude-opus-4-7`($5/$25)+ 家族 substring fallback("opus")。**真实缺口仅 = 缺显式 `claude-opus-4-8` / `claude-fable-5` 条目**(非"基于旧 tokenizer 致 30% 系统性低估"——该说法是误读)。tokenizer 膨胀体现在 API 返回 token 计数里,不是 PRICING 字典 bug;且 #385 明确膨胀为 worst-case 1.35x 非基线。
- **OMC v4.13.2 → v4.14.7** `[ENV-CONFIRMED 可用]`:含 Windows plugin hook manifest 直调 node 修复(v4.14.3/4.14.4)+ SessionEnd `async:true`(防 Windows 关机取消 mem0/cost-tracker flush)+ Ultragoal CLI + skills 一致性修复。Mercury 落后 10 版,Windows 11 环境直接受益。

### 第三梯队 — hook/调度/MCP 新原语(P2,需 PoC)
- **SubagentStop 强化**:`hookSpecificOutput.additionalContext`(不触发 error 注入反馈)+ `decision:block` + payload `agent_type`。Mercury 仅 dev 挂 mercury-test-gate,未用 additionalContext,acceptance/critic/research 无钩子。
- **ScheduleWakeup / Monitor / PushNotification**(deferred tools):主动式 session 续接(token 耗尽前预约唤醒)优于当前被动 Stop-hook(#469)。**与 #289(Routines)同调度原语家族,必须对齐去重**;是 #183 升级方案。
- **新 hook 处理器**(http / mcp_tool / agent)+ **新事件**(PermissionRequest / SubagentStart / Teams 事件)+ **permissions 层** `Tool(param:value)` 通配符:可把部分 push-guard 规则从脚本下沉到 settings.json。

### 第四梯队 — 参考/依赖项目升级(P3,审计)
- PR-Agent `0.34 → [UNVERIFIED 最新]`(Argus NAS Docker 重建,可减 #476 类误报);mem0 `[UNVERIFIED v2.0]`(bridge API 兼容性待验);ECC v2.0 模块化(旧 defer 根因消除,selective cherry-pick 审计);MemPalace **#233** re-eval(条件已满足)。advisor tool = **#280** 的实施。

---

## Sub-issue 提案(已去重 + 标注既有 issue 关系)

| # | 标题 | 优先级 | effort | 既有 issue 关系 |
|---|------|--------|--------|----------------|
| S1 | `.claude/workflows/` 脚手架 + Mercury 编排模式模板库(codebase-audit/multi-source-research/adversarial-plan-review/large-migration) | **P0** | medium | #101 核心补全;为 #289 提供脚本宿主 |
| S2 | 四原语选型矩阵(Subagents vs Skills vs Teams vs Workflows)+ budget-scaling 量化规则写入 main.md/CLAUDE.md | **P0** | small | #101;#155 上层框架。依赖 S1 |
| S3 | DEC-3 模型层 **4.7→4.8** 核验 + effort 参数注入 subagent 调度层(**先 web-verify 4.8 存在/定价**) | **P1** | small | 扩展 DEC-3 ADR;承接 #385 |
| S4 | cost-tracker 补 `claude-opus-4-8`/`claude-fable-5` PRICING 条目(**先 web-verify 定价**;非 tokenizer 修复) | **P1** | small | 扩展 #361 |
| S5 | OMC v4.13.2→v4.14.7 升级 + Windows hook/SessionEnd 回归验证 | **P1** | small | 影响所有 hook(#361/#469/mem0) |
| S6 | SubagentStop `additionalContext` 强化 + 扩展到 acceptance/critic/research | **P2** | small | 扩展 mercury-test-gate;支撑 #101 |
| S7 | **大规模 fan-out 的 context 经济学护栏**(对齐 #385:Haiku cliff/272K surcharge/tokenizer/cache)+ skill 迁移回归基线 | **P2** | small | 承接 #385;护 S1 模板库 |
| S8 | settings.json permissions 层 `Tool(param:value)` + 新 hook 处理器/事件试点 | **P3** | small | 优化 push-guard/pr-merge-guard |

**不另造、改为承接/comment 既有 issue(去重)**:
- 主动调度(ScheduleWakeup/Monitor 续接)→ **承接 #289 + #183**(同调度原语家族,在 #289 下评估,勿新造)。
- advisor tool 落地 → **推进 #280**(从 research → impl 子项,勿平行立项)。
- Agent Teams PoC → **承接 #319(CLOSED 可行性结论)+ 对齐 #155/#462/#292/#386**,勿重做可行性。
- mem0 v2.0 bridge 兼容 / ECC / MemPalace → **#233 保持独立追踪**,mem0-bridge-compat 与 ECC cherry-pick 各自评估,勿三合一稀释。
- Argus PR-Agent 升级 → 并入 **#476 已记录的 NAS 部署批次**。

## 不建议吸收(notRecommended)
1. **OpenViking 挂载** — AGPL-3.0 阻断 + pre-1.0 未变(LoCoMo 升至 82% 不解许可硬约束),继续 defer。
2. **Grok Build 第四 worker** — 无明确场景(已有 main+side + ccg 三模型扇出),除非 #157 Phase 3 明确需要。
3. **MCP Connector 迁移** — Mercury MCP 全 stdio 本地,无远程 SaaS MCP 场景。
4. **替换 claude-handoff** — 自有插件刚完成 #475 迁移 + #469 armed-hook,替换成本远超收益。
5. **Fast Mode / inference_geo** — 无对应需求。
6. **嵌套 subagent 主动使用** — 在 Agent Teams PoC 框架内统一评估,勿单独引入(#155/#462 已暴露隔离复杂性)。
7. **全量重写 autoresearch/dual-verify 为 Workflow** — large effort,先用模板库验证价值,渐进迁移。

---

## 纠正(据 adversarial critique,verdict=needs-more)

**未核验声明(落地前必 web-verify)**:Opus 4.8 / Fable 5 定价、工具系统提示降幅、PR-Agent 最新版默认模型、mem0 v2.0 LoCoMo/token 数、ECC v2.0、OMC changelog 具体数字 — 均标 `[UNVERIFIED]`,各 sub-issue 第一步实证。

**已修正的技术误述**:(1) "新 tokenizer 致 30% 系统性低估" → 实为 #385 记录的 worst-case 1.35x 非基线;(2) "cost-tracker 基于旧 tokenizer/4.6 基准" → cost_tracker.py 已有 opus-4-7 + 家族 fallback,真实缺口仅缺 4-8/fable-5 条目;(3) DEC-3 漂移 4.6→4.8 → 实为 4.7→4.8(#385 记录 main lane 跑 4.7)。

**已补的遗漏领域**:(1) context 经济学护栏 → 新增 S7 对齐 #385;(2) agent view / 多 lane 整合 → S2/S8 引用 #386(CLOSED)+ agent-view-dispatch convention;(3) skill 迁移回归基线 → 并入 S7;(4) 顶层验证约束 → umbrella 声明 dual-verify + PR + 全套 hook 回归。

**umbrella vs #101 从属**:本 umbrella = #101 harness roadmap 的**执行分解子轨**(聚焦 2026 Q1-Q2 编排原语吸收),非并列 roadmap,避免双头。

---

## 落地顺序(供 handoff 排期)
1. **第一波 P0**(本轮核心):S1 脚手架 + 模板库 → S2 选型矩阵(依赖 S1)。
2. **第二波 P1**(快赢防漂移):S5 OMC 升级(含 Windows 数据丢失修复,优先)→ S3 DEC-3 4.7→4.8 → S4 cost-tracker 条目。
3. **第三波 P2**(高价值需 PoC):S7 context 护栏 → S6 SubagentStop → 承接 #289 调度 / #280 advisor / #319+#155 Teams PoC。
4. **第四波 P3**(审计/优化):S8 permissions → #476 批次内 Argus 升级 → #233 + mem0/ECC 审计。

**治理约束**:涉 `~/.claude` 用户级变更(S4/S5/S6/S8)按 CLAUDE.md「用户级变更治理」(开 Issue + backup settings + 三层验证 + 回滚通道);所有代码合并 dual-verify + PR(实践已转 master-trunk for Argus,Mercury 仍 develop)。
