# everything-claude-code(ECC)新实践复审 + Mercury 影响分析 — 2026-06

> 调研日期: 2026-06-20 · 方法: ultracode Workflow `mercury-ecc-practice-scan`(45 agents / 1.26M tokens / 488 tool 调用,recon → adversarial cross-check → map-to-Mercury 三相)· Parent: [#478](https://github.com/392fyc/Mercury/issues/478) harness 现代化 **P3** · 对齐 [#233](https://github.com/392fyc/Mercury/issues/233)(ECC / mem0 / MemPalace 审计 tracker,**勿重复立项**)
> ECC 仓库: [`affaan-m/everything-claude-code`](https://github.com/affaan-m/everything-claude-code)(MIT,monolithic harness)· 旧裁定: **整体挂载 DEFER,模块化 cherry-pick OK**(记忆 `project_ecc_defer`,survey §第四梯队)
>
> **强制研究协议**: 凡 `status=unverified` 项标 `[UNVERIFIED]`,落地前须二次核验。worth-absorbing 候选(可落地项)逐条附来源 URL;already-covered/not-applicable/contradicted/unverified 表附 ECC 版本号(`vX.Y.Z`)+ 文末「来源」表的 release URL(版本→URL 可追溯),per-practice 一手核查见 Workflow run `wf_6e0faab3-a56`。本文是 Workflow 输出的人类可读固化,非新事实来源。

---

## TL;DR

复审 ECC **v1.7 → v2.0**(2026-02 ~ 2026-06)新增实践。Workflow recon 阶段 surface **105 个 distinct practices**,按 #385 fan-out 护栏 cap 到 24 个进入 adversarial 核查(**81 个被 log 丢弃**,非静默截断)。逐条独立交叉核查结果:**12 verified · 8 contradicted · 4 UNVERIFIED**。Mercury 影响 map:**2 worth-absorbing · 5 already-covered · 9 not-applicable**。

**关键发现**:adversarial cross-check 抓出 **8 条 recon 幻觉**(路径前缀错 `.claude/skills/` vs 实际 `skills/`、版本号/日期错配、`orch-*` 目录 CHANGELOG 宣称但 repo 实际不存在)。这印证了对抗式多源核查对「社区聚合源」的必要性 —— 单源 recon 会把营销文案/aspirational changelog 当事实吸收。**两个吸收候选均为 small effort,需用户决策是否走 cherry-pick PR;不在本文内立项**(对齐 #233)。

---

## 方法 + 护栏

`mercury-ecc-practice-scan.js`(三相 pipeline,内嵌 #385 护栏):
1. **Recon**(5 angle 并行 `research` agent):各自 WebSearch/WebFetch ECC repo + CHANGELOG + releases + 社区写作,先核实 repo 规范身份再抽取实践;只传任务不 pre-inject;PRACTICE_CAP=24 + 丢弃 log。
2. **Verify**(per-practice 独立 cross-check):对每条实践用 fresh 源核验(不信 recon 原引用),verified/contradicted/unverified 三分。
3. **MapToMercury**(per-**non-contradicted**-practice = verified + unverified,仅跳过 contradicted):agent 自行 Read/Grep Mercury repo 路径(CLAUDE.md / workflows / agents / DIRECTION 等),裁 already-covered / worth-absorbing / not-applicable,引 Mercury 文件证据,遵守 adapters≤200LOC + ECC wholesale-DEFER。

护栏遵守:无 pre-inject(传路径)· fan-out cap + 丢弃 log(81 dropped 已记录)· 继承会话 model(无 haiku 越界)· UNVERIFIED 显式标注。

---

## worth-absorbing(2)— 真实缺口,需用户决策

### 1. Hook Runtime Profile Control(`ECC_HOOK_PROFILE`)— `[UNVERIFIED]`

- **实践**(ECC v1.8.0,2026-03):env var `ECC_HOOK_PROFILE`(`minimal`/`standard`/`strict`)分档 gate 哪些 hook 激活;配套 `ECC_DISABLED_HOOKS`(逗号分隔 hook ID 跳过)、`ECC_SESSION_START_MAX_CHARS`(默认 8000,封顶 SessionStart 注入)等。来源: <https://github.com/affaan-m/everything-claude-code/blob/main/hooks/README.md>
- **核验**: `[UNVERIFIED]` —— 核心 `ECC_HOOK_PROFILE` 三档 + `ECC_DISABLED_HOOKS` + `ECC_SESSION_START_MAX_CHARS` 经 hooks/README.md 确认;但所列清单中 `ECC_SESSION_RETENTION_DAYS`/`ECC_AGENT_DATA_HOME` 在原始源找不到,完整描述无法核实 → 落地前须逐 env var 二次核验。
- **Mercury 影响 = 真实缺口**: Mercury 在 `.claude/settings.json` 跨 5 事件类型(PreToolUse/PostToolUse/UserPromptSubmit/Stop/SubagentStop)注册 **14 个 shell hook + 2 个 `.cjs` adapter**(loop-detector/test-gate);`.claude/hooks/` 里另有未注册脚本(如 `voice-stop-notify.sh`)。现有 gating 是 **tool/event matcher 级**(`Edit|Write`、`Bash`、`Skill|Agent` 等)—— 一个 hook 只要 matcher 命中就触发,**无 runtime profile/lane 级条件 gate**:不能按「subagent lane / session 类型 / profile 档位」整组抑制。唯一 per-hook kill switch 是 `auto-handoff-stop.sh` 的 `MERCURY_AUTO_HANDOFF_STOP_DISABLED=1`。ECC profile control 填的正是这一层(profile/lane 级条件抑制,正交于 Mercury 已有的 matcher gating):随 hook 数增长(SubagentStop/Stop 已做非平凡工作),「subagent lane 抑制 session-init/handoff/context-monitor 而保留安全门 scope-guard/pre-commit-guard/push-guard」是真实运营缺口。
- **cherry-pick 形态**(absorbCost=small): 给非安全 hook 加 `[ "${MERCURY_HOOK_PROFILE:-standard}" = "minimal" ] && exit 0` 守卫 + CLAUDE.md 文档化 `MERCURY_HOOK_PROFILE`/`MERCURY_DISABLED_HOOKS`(沿用现有 `MERCURY_AUTO_HANDOFF_STOP_DISABLED` 模式)。无新文件,Mercury-internal hook 豁免 adapter LOC cap,无需挂载 ECC。**与 #484(SubagentStop 强化)/ #486(hook 处理器试点)同域**,建议并入该批次评估而非独立立项。

### 2. 语言专用 reviewer / build-resolver agents — verified

- **实践**(ECC v1.9.0): 新增 9 个语言专用 subagent(`typescript-reviewer`/`rust-reviewer`/`rust-build-resolver`/`java-*`/`kotlin-*`/`pytorch-build-resolver`/`docs-lookup`),各为单个 `.claude/agents/<name>.md`。来源(verified): <https://github.com/affaan-m/everything-claude-code/releases/tag/v1.9.0>
- **Mercury 影响 = 部分缺口**: Mercury 9 个 `.claude/agents/*.md` 全是通用角色(main/dev/acceptance/critic/research/design/game-*),零语言专用;语言适配检查(`tsc --noEmit`)仅作为注记藏在 `dual-verify/SKILL.md`。**9 个里仅 4 个匹配 Mercury 栈**:`typescript-reviewer`(GUI 前端)、`rust-reviewer` + `rust-build-resolver`(Tauri 后端)、`docs-lookup`(栈无关);Java/Kotlin/PyTorch 不适用本 repo。
- **cherry-pick 形态**(absorbCost=small): 4 个 `.md` 文件,各 <200 行、独立可拆卸。**须走完整 cherry-pick 协议**(manifest entry + `gh api` SHA 核验 + 5 行文件头 + MIT license gate),非 Category A/B CLI 脚手架。建议作为独立小 cherry-pick PR(待用户决策),或并入 Phase 6 GUI(TS/Rust 栈)工具链批次。

---

## already-covered(5)— Mercury 已覆盖且实现更深

| ECC 实践(版本) | Mercury 等价 | 核验 |
|---|---|---|
| Harness Commands `/harness-audit` `/loop-start` `/loop-status` `/model-route` `/quality-gate`(v1.8.0) | `mercury-codebase-audit` Workflow + `mercury-large-migration`(loop-until-done)+ main.md §选型矩阵(model 路由)+ `/dual-verify`(更强的并行双审门) | verified |
| Orchestrator skill family `orch-*` + `/multi-*`(v2.0.0) | Dynamic Workflow DSL(`.claude/workflows/`,六大模式,JS 持计划)+ 四原语选型矩阵;触发 `ultracode`/`/effort ultracode`/`/<name>` 语义等价且有 #385 护栏 | `[UNVERIFIED]`(且 `orch-*` 目录经核查 repo 实际不存在,见 contradicted) |
| Cross-Harness Plugin Surface(`.claude-plugin`/`.cursor`/`.codex`,v2.0.0) | 双 harness:`.codex/config.toml`+`hooks.json`(Codex)+ `.claude/settings.json`(Claude Code),共享 `.claude/hooks/`;Cursor/OpenCode/Gemini/Zed 明确 out-of-scope(DIRECTION §Mercury 不是什么) | verified |
| Worktree-lifecycle service(v2.0.0) | dev-pipeline 中央 worktree 分配 + `cleanup-worktree-branch.sh` + `worktree-reaper.sh`(orphan GC)+ lane 物理隔离(#342) | verified |
| Session adapters for Codex-worktree / OpenCode(v2.0.0) | `.codex/hooks.json` 用 `git rev-parse --show-toplevel` 锚定接入全 Mercury hook 集到 Codex sandbox;OpenCode 不在 active toolchain(按需挂载非预置) | `[UNVERIFIED]` |

> 共性: ECC 把这些做成「外部 control-plane 服务 / slash 命令层」,Mercury 用 Claude Code 原生原语 + dev-pipeline 深度集成实现同能力域,更可审计,且符合 DIRECTION §P1「能挂载绝不自研、本体只做外部做不到的事」。逐个吸收只增表面积无增益。

---

## not-applicable(9)— 不匹配 Mercury 架构/scope

| ECC 实践 | 不适用理由(摘) |
|---|---|
| Selective Install Architecture + SQLite state(`install-plan/apply.js`,v1.9.0)`[UNVERIFIED]` | 解决 10-12 语言工具链安装管理,Mercury 无此问题域;「挂了什么」由 `git submodule` + `upstream-manifest.json` 回答,非安装状态机 |
| Operator Workflow Skills(brand-voice/billing-ops/workspace-ops 等,v1.10.0) | 业务/营销域,与 Mercury 四自研理由(session 续接/记忆/通知/质量门)无关;扩 scope 无益 |
| Media Generation Skills(manim-video/remotion/frontend-slides) | 编程式视频/PPT 生成,域productivity 非 harness;manim/remotion 需 Python/Node 渲染管线远超 200LOC adapter cap;`animate-frames` 已明确排除 video |
| Instinct/Self-Improving Skills(`/learn` `/evolve` `/instinct-*`) | 技能自进化已由 DIRECTION §六 #141 指定 OpenSpace 为上游,吸收 ECC 版会与既定挂载重复 + 竞争实现 |
| MCP inventory(`ecc.mcp.v1`)+ connector 审计 | Mercury `.mcp.json` 仅 4 个本地 server(1 个 localhost HTTP `mercury-orchestrator` + 3 个 stdio:codex/mercury-telegram/playwright),无远程 SaaS MCP;survey §不建议吸收 item 3 已排除;provenance 由 upstream-manifest 治理 |
| `kubernetes-patterns` skill(v2.0.0) | Mercury 栈无 k8s/Helm/容器编排,零 call site |
| `nestjs-patterns` skill(v1.10.0) | Mercury 栈无 NestJS(Python hooks + Rust/Tauri GUI + shell);零重叠 |
| `ecc-tui` 控制面板二进制(Rust,v1.10.0 alpha) | 控制 ECC 自有 daemon/session 模型,Mercury 无对应 daemon;「可视化控制面板」由 `mercury-gui`(Tauri Phase 6)覆盖且 DIRECTION 禁其作 agent 控制面板;非独立可拆卸 |
| Session save/resume 命令(`/save-session` `/resume-session`,v1.9.0) | 原始 context 序列化模型与 Mercury `/handoff`(结构化文档 + `session_chain` SQLite + armed Stop-hook + lane assertion)架构冲突;opaque blob 无 task state/acceptance/Issue ref |

---

## contradicted(8)— recon 幻觉,记录以防复发

> adversarial-verify 核查推翻的 claim(recon angle 把 CHANGELOG 营销文案/aspirational 条目/错误路径当事实)。**记录价值**:这些是「看似对实则错」的典型,未来复审 ECC 须警惕同类。

| 被推翻 claim | 实际(一手源核查) |
|---|---|
| AgentShield Security Scanner(v1.6.0,1282 tests,`/security-scan`,`security-reviewer.md`) | 实体存在(102 规则),但 v1.6.0 不在 CHANGELOG、1282 tests 是首页营销文案独立 repo 未复现、slash 命令名/agent 文件名/Marketplace 条目名均无法证实 |
| Control-Pane Substrate + MCP inventory | v2.0.0 核心属实,但「control-pane」拼写应为 control-plane、Rust 二进制是 alpha、「fragmentation detection / secret redaction」是杜撰特性名 |
| Observer Loop Prevention(5-layer,v1.9.0) | 5-layer guard 属实但 `loop-operator`/`/loop-start` 实为 v1.8.0(claim 错并版本);skill 正名 `continuous-agent-loop` 非 `autonomous-loop` |
| Orchestrator skill family `orch-*`(具体目录) | **GitHub API tree 显示 `.claude/skills/` 下无任何 `orch-*` 目录**;CHANGELOG 提及但 repo 实际未提交该目录 → 宣称 ≠ 落地 |
| Cross-harness architecture guide(v2.0.0-rc.1,2026-04-28) | 文件存在但版本日期错(rc.1 实为 2026-05-25);「Hermes import skill surface」是误读 |
| Operator workflow skills(`.claude/skills/<name>`) | 路径错(实为 `.agents/skills/`),且 7 个里仅 `brand-voice` 实际存在,其余 6 个 repo tree 中缺失 |
| Media-generation skills 路径 | skill 实存但路径前缀错(实为顶层 `skills/` 非 `.claude/skills/`) |
| `/docs` `/prompt-optimize` 命令路径 | skill 名 + v1.9.0 属实,但路径错(实为顶层 `commands/` 非 `.claude/commands/`),且当前 main 分支无 `commands/docs.md` |

---

## unverified(4)— 部分可证、整体描述无法核实 `[UNVERIFIED]`

| claim | 无法核实点 |
|---|---|
| Hook Runtime Profile Control | 核心三档证实,但 `ECC_SESSION_RETENTION_DAYS`/`ECC_AGENT_DATA_HOME` 原始源找不到(详见 worth-absorbing #1) |
| Selective Install Architecture | install-plan/apply.js + SQLite state CHANGELOG 证实,但 `~/.ecc/state.db` 具体路径未证(一源说 `install-state.json`) |
| Orchestrator Skill Family(orch-* + loop-operator) | orch-* family + loop-operator 属实,但 `/multi-plan` 等 5 个具体命令在 changelog/release/源均未现 |
| Session adapters(codex-worktree/opencode-session) | v2.0.0 有「harness-neutral session adapters(ecc.session.v1)」提及,但具名 JS 模块 `codex-worktree`/`opencode-session` 无一手源证实,可能是 mischaracterization |

---

## 建议 / next actions(对齐 #233,不重复立项)

1. **两个 worth-absorbing 候选 → 待用户决策**,不在本文内立项:
   - **Hook Profile Control**(`[UNVERIFIED]`,需逐 env var 二次核验)→ 建议**并入 #484/#486 hook 批次**评估(同域:hook 分档 gate 与 SubagentStop 强化 / settings permissions 试点天然协同),而非独立 Issue。
   - **4 个语言专用 agent**(verified)→ 建议作**独立小 cherry-pick PR**(走完整协议:manifest+SHA+文件头),或并入 Phase 6 GUI(TS/Rust 栈)工具链;由用户定优先级。
2. **#233 tracker 更新**: 本审计结论作为 comment 挂 #233(ECC v2.0 selective cherry-pick audit 已执行),不新开 ECC-audit Issue。
3. **survey 对齐**: `harness-modernization-survey-2026-06.md` §第四梯队「ECC v2.0 modular → selective cherry-pick 审计」即本文,缺口已从「未审计」收敛为「2 候选待决策 + 余皆 covered/n-a」。
4. **整体挂载 DEFER 维持不变**: 本次未发现推翻 `project_ecc_defer` wholesale-mount DEFER 的理由;ECC v2.0 仍是 monolithic harness,只做模块化 cherry-pick。

---

## 来源(web-verified,Workflow 一手核查)

- ECC repo: <https://github.com/affaan-m/everything-claude-code>
- v1.8.0 release(Harness Commands / ECC_HOOK_PROFILE): <https://github.com/affaan-m/everything-claude-code/releases/tag/v1.8.0>
- v1.9.0 release(语言专用 agents / selective install): <https://github.com/affaan-m/everything-claude-code/releases/tag/v1.9.0>
- v1.10.0 release(operator/media skills): <https://github.com/affaan-m/everything-claude-code/releases/tag/v1.10.0>
- hooks/README.md(profile control): <https://github.com/affaan-m/everything-claude-code/blob/main/hooks/README.md>
- Mercury 内部对照: `harness-modernization-survey-2026-06.md` §第四梯队 · `CLAUDE.md`(ECC DEFER + adapter cap + cherry-pick 协议)· 记忆 `project_ecc_defer`
